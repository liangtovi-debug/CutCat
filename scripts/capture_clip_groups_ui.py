"""Synthetic persistent-group offscreen matrix; screenshots need visual review."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--scale', type=float)
args = parser.parse_args()
if args.scale is None:
    for scale in (1, 1.5, 2):
        subprocess.run([sys.executable, __file__, '--output', str(args.output), '--scale', str(scale)], check=True)
    raise SystemExit(0)
os.environ['QT_SCALE_FACTOR'] = str(args.scale)
# Offscreen's default 800px screen truncates popup menus at high DPR.
# Provision a synthetic 3840px screen, not a native-display claim.
_screen_directory = tempfile.TemporaryDirectory(prefix='cutcat-group-screen-')
_screen_config = Path(_screen_directory.name) / 'screen.json'
_screen_config.write_text(json.dumps({'screens': [{'name': 'Validation', 'width': 3840, 'height': 2160, 'logicalDpi': 96}]}))
os.environ['QT_QPA_PLATFORM'] = f'offscreen:configfile={_screen_config}'
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from lumen.model import Clip, Media, Project
from lumen.ui import MainWindow

args.output.mkdir(parents=True, exist_ok=True)
app = QApplication.instance() or QApplication([])
assert app.primaryScreen().geometry().width() >= 1366
records = []
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    for locale in ('en', 'zh-CN'):
        for mode in ('simple', 'professional'):
            for font_size in (10, 15):
                app.setFont(QFont('Noto Sans CJK SC', font_size))
                settings = QSettings(str(root / f'{locale}-{mode}-{font_size}.ini'), QSettings.Format.IniFormat)
                settings.setValue('mode', mode)
                window = MainWindow(workspace_settings=settings, ui_language=locale, recovery_root=root / 'recovery')
                window.editor.project = Project(media=[Media('m', str(root/'coast.mov'), '海岸 Coast · synthetic fixture', 20, 1920, 1080, True, True)], clips=[
                    Clip('a','m',0,10,0,80), Clip('b','m',1,30,20,70),
                    Clip('loose','m',0,120,0,40), Clip('audio','m',2,15,0,80)])
                group = window.editor.group_clips(['a','b','audio'])
                window.refresh(); window.show(); app.processEvents()
                for width in (1000, 1366):
                    window.resize(width, 768); app.processEvents()
                    window.main_split.setSizes([190, width - 550, 360]); app.processEvents()
                    window.select_clip('a'); window.timeline.setFocus(); app.processEvents()
                    assert set(window.selected_clip_ids) == {'a','b','audio'}
                    assert len(window.project.clip_groups) == 1 and window.project.clip_groups[0].id == group.id
                    stem=f'clip-groups-{locale}-{mode}-font{font_size}-width{width}-scale{args.scale:g}'
                    files=[]
                    for label,widget in [('window',window),('timeline',window.timeline)]:
                        filename=f'{stem}-{label}.png'; assert widget.grab().save(str(args.output/filename));files.append(filename)
                    # Open the actual existing Edit menu, not a reconstructed list.
                    menu=window.menuBar().actions()[1].menu()
                    menu.popup(window.menuBar().mapToGlobal(window.menuBar().actionGeometry(window.menuBar().actions()[1]).bottomLeft()))
                    app.processEvents()
                    assert menu.width() >= menu.sizeHint().width()
                    filename=f'{stem}-edit-menu.png'; assert menu.grab().save(str(args.output/filename));files.append(filename)
                    menu.hide(); app.processEvents()
                    assert window.width()==width
                    assert window.media_scroll.horizontalScrollBar().maximum()==0
                    records.append(dict(locale=locale,mode=mode,font_size=font_size,requested_width=width,actual_width=window.width(),dpr=window.devicePixelRatioF(),group_id=group.id,selected_ids=list(window.selected_clip_ids),horizontal_scroll_max=window.media_scroll.horizontalScrollBar().maximum(),files=files,evidence='Synthetic metadata; Qt offscreen. No native desktop or decoding claim.'))
                window.dirty=False;window.closing=True;window.close();app.processEvents()
(args.output/f'manifest-scale{args.scale:g}.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))

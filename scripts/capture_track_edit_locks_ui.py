"""Synthetic lock-header offscreen matrix; screenshots require visual review."""
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
_screen_directory = tempfile.TemporaryDirectory(prefix='cutcat-lock-screen-')
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
                window.refresh(); window.show(); app.processEvents()
                for width in (1000, 1366):
                    window.resize(width, 768); app.processEvents()
                    window.main_split.setSizes([190, width - 550, 360]); app.processEvents()
                    window.select_clip('a')
                    buttons = window.timeline.track_lock_buttons
                    for i in range(4):
                        window.toggle_track_edit_lock(i, False)
                    QTest.mouseClick(buttons[0], Qt.MouseButton.LeftButton)
                    buttons[2].setFocus(); QTest.keyClick(buttons[2], Qt.Key.Key_Space)
                    app.processEvents()
                    assert window.project.track_edit_locks == [True, False, True, False]
                    assert not window.split_clip_button.isEnabled() and not window.delete_clips_button.isEnabled()
                    assert window.export_button.isEnabled() and window.preview_button.isEnabled()
                    assert all(b.accessibleName() and b.accessibleDescription() for b in buttons)
                    for i, button in enumerate(buttons):
                        assert button.parentWidget() is window.timeline
                        assert button.geometry().left() >= 0 and button.geometry().right() < 88
                        assert 34 + 53*i <= button.y() and button.geometry().bottom() < 34+53*i+45
                        assert button.fontMetrics().horizontalAdvance(button.text()) <= button.width()-4
                        assert button.fontMetrics().height() <= button.height()-2
                    stem=f'track-edit-locks-{locale}-{mode}-font{font_size}-width{width}-scale{args.scale:g}'
                    files=[]
                    for label,widget in [('window',window),('timeline',window.timeline)]:
                        filename=f'{stem}-{label}.png'; assert widget.grab().save(str(args.output/filename));files.append(filename)
                    assert window.width()==width
                    records.append(dict(locale=locale,mode=mode,font_size=font_size,requested_width=width,actual_width=window.width(),dpr=window.devicePixelRatioF(),locks=window.project.track_edit_locks,button_rectangles=[[b.x(),b.y(),b.width(),b.height()] for b in buttons],button_names=[b.accessibleName() for b in buttons],mouse_keyboard_verified=True,files=files,evidence='Synthetic metadata; Qt offscreen. No native desktop or decoding claim.'))
                window.dirty=False;window.closing=True;window.close();app.processEvents()
(args.output/f'manifest-scale{args.scale:g}.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))

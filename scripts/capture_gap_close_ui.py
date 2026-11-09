"""Synthetic offscreen gap-preview matrix; inspect pixels before claiming QA.

No source decode, native-desktop or actual-DPI hardware claim. Each subprocess
uses a real Qt scale factor and verifies scroll/footer geometry before saving.
"""
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
_screen_directory = tempfile.TemporaryDirectory(prefix='cutcat-gap-screen-')
_screen_config = Path(_screen_directory.name) / 'screen.json'
_screen_config.write_text(json.dumps({'screens':[{'name':'Validation','width':3840,'height':2160,'logicalDpi':96}]}))
os.environ['QT_QPA_PLATFORM'] = f'offscreen:configfile={_screen_config}'
from PySide6.QtCore import QSettings, QRect, Qt
from PySide6.QtGui import QFont, QFontInfo, QTextLayout
from PySide6.QtWidgets import QApplication
from lumen.model import Caption, Clip, Media, Overlay, Project
from lumen.ui import MainWindow
from lumen.ui_gap_close import open_gap_close
from scripts.qt_test_cleanup import cleanup_test_windows

args.output.mkdir(parents=True, exist_ok=True)
app=QApplication.instance() or QApplication([])
app.setFont(QFont('Noto Sans CJK SC',15))
font_test=QTextLayout('CutCat ABC 012 关闭单个空隙', app.font())
font_test.beginLayout();font_test.createLine().setLineWidth(1000);font_test.endLayout()
assert all(index for run in font_test.glyphRuns() for index in run.glyphIndexes())
records=[]
with tempfile.TemporaryDirectory(prefix='cutcat-gap-ui-capture-') as directory:
    root=Path(directory)
    for locale in ('en','zh-CN'):
        for mode in ('simple','professional'):
            settings=QSettings(str(root/f'{locale}-{mode}.ini'),QSettings.Format.IniFormat)
            settings.setValue('mode',mode)
            window=MainWindow(workspace_settings=settings,ui_language=locale,recovery_root=root/'recovery')
            window.editor.project=Project(media=[Media('m',str(root/'source.mov'),'Synthetic source',20,64,48,True,True)],clips=[
                Clip('Left · 保留','m',0,2,0,8),Clip('Right · 前移','m',0,15,30,8),
                Clip('Later · 保留间隔','m',0,28,40,8),Clip('Upper · 画面','m',1,15,40,12),
                Clip('Music · 音乐','m',2,25,50,8),Clip('Fixed anchor · 固定','m',3,10,0,70)],
                captions=[Caption('Caption · 字幕',15,8,'Move')],
                overlays=[Overlay('Sticker · 贴纸','/missing.png','a'*64,20,8)])
            window.refresh();window.seek_frame(12);window.show();app.processEvents()
            assert window.workspace_mode==mode
            d=open_gap_close(window);assert d.track is None
            d.target_track.setCurrentIndex(1);d.capture_button.click()
            d.track_checks[1].setChecked(True);d.track_checks[2].setChecked(True)
            d.follow_captions_check.setChecked(True);d.follow_stickers_check.setChecked(True)
            d.preview_button.click();assert d.plan is not None,d.impact.text()
            plan=d.plan
            d.fit_available_screen(QRect(0,0,360,600))
            for _ in range(5):app.processEvents()
            assert d.scroll.horizontalScrollBar().maximum()==0
            controls=(d.target_track,d.capture_button,*d.track_checks,d.follow_captions_check,d.follow_stickers_check)
            for control in controls:
                d.scroll.ensureWidgetVisible(control);app.processEvents()
                assert control.accessibleName() and control.font().pointSize()==15
                assert d.scroll.viewport().rect().contains(control.mapTo(d.scroll.viewport(),control.rect().center()))
            for control in (d.preview_button,d.apply_button,d.cancel_button):
                assert control.isVisible() and d.rect().contains(control.geometry())
                assert control.fontMetrics().horizontalAdvance(control.text())<=control.width()-8
            stem=f'gap-close-{locale}-{mode}-narrow-font15-scale{args.scale:g}'
            files=[]
            for section in ('target','participants','summary','impact','tail'):
                if section=='target':d.scroll.ensureWidgetVisible(d.target_track,0,0)
                elif section=='participants':d.scroll.verticalScrollBar().setValue(d.track_checks[0].y())
                elif section=='summary':d.scroll.verticalScrollBar().setValue(d.impact.y())
                elif section=='tail':d.scroll.verticalScrollBar().setValue(d.scroll.verticalScrollBar().maximum())
                else:d.scroll.ensureWidgetVisible(d.impact,0,0)
                app.processEvents()
                filename=f'{stem}-{section}.png';assert d.grab().save(str(args.output/filename));files.append(filename)
            assert d.plan is plan and not window.editor.can_undo
            records.append(dict(locale=locale,mode=mode,font_family=QFontInfo(app.font()).family(),font_size=15,
                logical_size=[d.width(),d.height()],dpr=d.devicePixelRatioF(),capture=[d.track,d.playhead],
                gap=[plan.start,plan.end],deleted=0,impact_count=len(plan.item_impacts),
                footer=[[b.x(),b.y(),b.width(),b.height()] for b in (d.preview_button,d.apply_button,d.cancel_button)],
                horizontal_scroll=d.scroll.horizontalScrollBar().maximum(),files=files,
                evidence='Synthetic metadata, Qt offscreen; no native desktop or media render claim.'))
            d.reject();window.dirty=False;window.closing=True;window.close();app.processEvents()
            cleanup_test_windows(app,[])
(args.output/f'manifest-scale{args.scale:g}.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))

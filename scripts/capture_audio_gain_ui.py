"""Actual sample-key dialog QA at one Qt scale; tiny identity-only source."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
parser=argparse.ArgumentParser()
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--scale',type=float,default=1)
args=parser.parse_args()
os.environ['QT_SCALE_FACTOR']=str(args.scale)
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QSettings,QRect,Qt
from PySide6.QtGui import QFont,QFontInfo
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from lumen.ui import MainWindow
from lumen.ui_audio_gain import open_audio_gain
from lumen.audio_gain import AudioGainKeyframe as Key
from lumen.model import Clip,Editor,Media,Project

out=args.output;out.mkdir(parents=True,exist_ok=True)
app=QApplication([])
font=QFont('Noto Sans CJK SC',10)
root=Path(__file__).resolve().parents[1]
files=('lumen/ui.py','lumen/ui_audio_gain.py','lumen/ui_audio_gain_timeline.py',
       'lumen/audio_gain.py','lumen/i18n_catalog_audio_gain.py','scripts/capture_audio_gain_ui.py')
identity=lambda:{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files}
source_before=identity();records=[]

def snapshot(w):return (w.project.to_dict(),len(w.editor._undo),len(w.editor._redo),w.editor.revision)
def events():
    for _ in range(5):app.processEvents()
def shot(widget,name):
    assert widget.grab().save(str(out/name));return name

def keyboard(d,button):
    QApplication.setActiveWindow(d);d.activateWindow();button.setFocus(Qt.FocusReason.TabFocusReason);events()
    assert button.hasFocus();QTest.keyClick(button,Qt.Key.Key_Space);events()

with tempfile.TemporaryDirectory(prefix='cutcat-gain-ui-capture-') as raw:
    temp=Path(raw);source=temp/'identity.wav';source.write_bytes(b'Identity-only source; never decoded')
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    for locale in ('en','zh-CN'):
        for geometry,mode,size in (('normal','simple',(1024,850)),('narrow','professional',(420,600))):
            tag=f'{locale}-{geometry}-{args.scale:g}'
            w=MainWindow(workspace_settings=QSettings(str(temp/f'{tag}.ini'),QSettings.Format.IniFormat),
                         ui_language=locale,recovery_root=temp/tag)
            w.set_workspace_mode(mode,persist=False);w.setFont(font)
            w.editor=Editor(Project(media=[Media('m',str(source),'Synthetic audio',3,has_video=False,has_audio=True)],
                clips=[Clip('a','m',2,0,0,60,gain_db=6,audio_gain_keyframes=[
                    Key(-24000,-12),Key(24000,0),Key(72000,-18,'hold'),Key(2**33+17,-3)])]))
            w.refresh();w.select_clip('a');w.show();events()
            assert w.audio_gain_button.isEnabled()
            d=open_audio_gain(w);d.setFont(font);events();d.select_key(24000)
            d.setMaximumSize(16777215,16777215);d.resize(620,720)
            d.fit_available_screen(QRect(0,0,*size));events()
            assert d.scroll.horizontalScrollBar().maximum()==0
            for label in (d.scope,d.summary,d.timeline_help,d.hidden,d.hidden_help,d.workflow,d.status):
                assert label.wordWrap() and label.width()<=d.scroll.viewport().width()
                assert label.height()>=label.heightForWidth(label.width()),(label.text(),label.height(),label.heightForWidth(label.width()))
            assert d.rect().contains(d.close_button.mapTo(d,d.close_button.rect().bottomRight()))
            for control in (d.sample_edit,d.offset_edit,d.interpolation,d.key_selector,d.timeline,d.add_button,d.close_button):assert control.accessibleName()
            d.scroll.verticalScrollBar().setValue(0);events()
            paths=[shot(d,f'{tag}-top.png')]
            before=snapshot(w)
            d.sample_edit.setText(str(2**33+19));d.offset_edit.setText('1.25')
            d.interpolation.setCurrentIndex(d.interpolation.findData('hold'))
            assert snapshot(w)==before
            keyboard(d,d.add_button)
            assert w.selected_clip().audio_gain_keyframes[-1]==Key(2**33+19,1.25,'hold')
            assert len(w.editor._undo)==before[1]+1
            after=snapshot(w);w.undo();assert w.project.to_dict()==before[0]
            w.redo();assert w.project.to_dict()==after[0]
            d.select_key(24000);d.timeline.setFocus();events()
            before_move=snapshot(w)
            QTest.keyPress(d.timeline,Qt.Key.Key_Right);assert d.timeline.gesture_active
            assert snapshot(w)==before_move
            QTest.keyClick(d.timeline,Qt.Key.Key_Escape)
            QTest.keyRelease(d.timeline,Qt.Key.Key_Right);assert snapshot(w)==before_move
            d.scroll.verticalScrollBar().setValue(max(0,d.offset_edit.y()-d.offset_edit.height()));events()
            paths.append(shot(d,f'{tag}-controls.png'))
            assert d.close_button.isVisible()
            current=snapshot(w);d.sample_edit.setText('12345');keyboard(d,d.close_button)
            assert snapshot(w)==current and d._closed_flag
            assert not w.jobs
            records.append({'locale':locale,'geometry':geometry,'mode':mode,'logical_size':[d.width(),d.height()],
                'device_pixel_ratio':d.devicePixelRatioF(),'horizontal_scroll':d.scroll.horizontalScrollBar().maximum(),
                'exact_large_sample_input':True,'keyboard_add_undo_redo':True,'ghost_escape_no_commit':True,
                'draft_close_no_commit':True,'footer_visible':True,'accessible_controls':True,
                'source_unchanged':hashlib.sha256(source.read_bytes()).hexdigest()==digest,'paths':paths})
            w.dirty=False;w.closing=True;w.close();events();w.deleteLater();events()
assert identity()==source_before
report={'status':'passed','identity_only_fixture':True,'native_windows_macos_validation':False,
        'font_family':QFontInfo(font).family(),'source_identity':source_before,'records':records,
        'screenshot_bytes':sum(path.stat().st_size for path in out.glob('*.png'))}
(out/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
print(json.dumps({'status':'passed','records':len(records),'bytes':report['screenshot_bytes']}))

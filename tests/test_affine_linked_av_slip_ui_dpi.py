"""Retained EN/ZH affine-slip top/details at fresh real Qt DPRs, not native QA."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

DRIVER = r'''
from copy import deepcopy
from fractions import Fraction
import hashlib, json, os, sys, time
from pathlib import Path
root,out,scale=Path(sys.argv[1]),Path(sys.argv[2]),float(sys.argv[3])
sys.path.insert(0,str(root));os.environ['QT_SCALE_FACTOR']=str(scale)
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QPoint,QRect,QSettings,Qt
from PySide6.QtGui import QFont,QFontDatabase,QFontInfo,QTextLayout
from PySide6.QtWidgets import QApplication,QMessageBox
from lumen.i18n import set_locale,tr
from lumen.model import AVLink,AV_LINK_AFFINE_ALIGNMENT,AudioMixAnchor,Clip,Editor,Media,Project
from lumen.timing import ClipTimeMap
from lumen.ui import MainWindow
out.mkdir(parents=True,exist_ok=True);app=QApplication([])
if os.environ.get('LUMEN_TEST_CJK_FONT_DIR'):
    from scripts.provision_test_font import verify_fixture
    fixture=verify_fixture(os.environ['LUMEN_TEST_CJK_FONT_DIR'])
    assert QFontDatabase.addApplicationFont(fixture['font_file'])>=0
font=QFont('Noto Sans CJK SC',10);app.setFont(font)
for text in ('Source delta Preview Apply Cancel','源入点基准角色共同位移联合源滑移取消'):
    layout=QTextLayout(text,font);layout.beginLayout();layout.createLine().setLineWidth(2000);layout.endLayout()
    runs=layout.glyphRuns();assert runs and all(run.rawFont().isValid() for run in runs)
    assert all(index!=0 for run in runs for index in run.glyphIndexes())
source=out/'identity.mov';source.write_bytes(b'Affine slip identity only; never decoded'*100)
source_before=(source.read_bytes(),source.stat().st_mtime_ns)
warnings=[];QMessageBox.warning=lambda *args:warnings.append(str(args[2]));QMessageBox.information=lambda *_:None
w=MainWindow(workspace_settings=QSettings(str(out/'settings.ini'),QSettings.Format.IniFormat),ui_language='en',recovery_root=out/'recovery')
w.timeline.waveforms.request=lambda *_:None

def pump():
    for _ in range(5):app.processEvents()
def settle():
    deadline=time.monotonic()+10
    while w.jobs and time.monotonic()<deadline:pump();time.sleep(.005)
    pump();assert not w.jobs

def make_project():
    project=Project(media=[Media('m',str(source),'Picture <unsafe> & sound',100,64,48,True,True)],clips=[
        Clip('video-id','m',0,5,12,30,source_audio_enabled=False,time_map=ClipTimeMap(12,Fraction(167,4))),
        Clip('audio-id','m',2,3,10,34,time_map=ClipTimeMap(10,Fraction(87,2)),fade_in=2,fade_out=3,
            audio_mix_anchor=AudioMixAnchor(0,5,'historical-video'))],
        av_links=[AVLink('pair','video-id','audio-id','m',AV_LINK_AFFINE_ALIGNMENT)])
    w.editor=Editor(project);w.refresh();w.active_selection_type='clip'
    w.select_clip('video-id');w.show();w.activateWindow();QApplication.setActiveWindow(w);w.timeline.setFocus();pump()
    return project

records=[]
try:
    for locale in ('en','zh-CN'):
        set_locale(locale)
        for geometry,size in (('normal',(780,720)),('narrow',(420,600))):
            make_project();role='video' if locale=='en' else 'audio';w.select_linked_role(role)
            before=deepcopy((w.project,w.editor._undo,w.editor._redo,w.editor.revision,w.revision))
            d=w.open_linked_av_slip();assert d is not None and not d.isModal()
            d.origin.setText('20');plan=d.plan;d.preview();settle()
            assert d.preview_receipt is not None and d.apply_button.isEnabled()
            d.fit_available_screen(QRect(0,0,*size));pump();d.scroll.verticalScrollBar().setValue(0);pump()
            assert d.devicePixelRatioF()==scale
            assert d.width()<=size[0]-32 and d.height()<=size[1]-64
            input_rect=QRect(d.origin.mapTo(d.scroll.viewport(),QPoint(0,0)),d.origin.size())
            assert d.scroll.viewport().rect().contains(input_rect),'The entire source input must be above the fold'
            summary_rect=QRect(d.summary.mapTo(d.scroll.viewport(),QPoint(0,0)),d.summary.size())
            assert d.scroll.viewport().rect().contains(summary_rect)
            assert d.summary.y()<d.origin.y()<d.impact.y()<d.scope.y()<d.source.y()
            for button,title in ((d.preview_button,'Preview joint source slip'),(d.apply_button,'Apply joint source slip'),(d.cancel_button,'Cancel')):
                assert button.text()==tr(title) and button.accessibleName()==button.text()
                assert d.rect().contains(QRect(button.mapTo(d,QPoint(0,0)),button.size()))
                assert button.width()>=button.fontMetrics().horizontalAdvance(button.text())+8
            for label in (d.summary,d.origin_label,d.impact,d.scope,d.source,d.limits,d.check_note,d.status):
                assert label.textFormat()==Qt.TextFormat.PlainText and label.wordWrap()
                assert label.width()<=d.scroll.viewport().width()
                assert label.height()>=max(0,label.heightForWidth(label.width()))
            assert d.scroll.horizontalScrollBar().maximum()==0
            delta=8 if role=='video' else 10
            assert str(delta) in d.summary.text() and '[5, 35)' in d.source.text() and '[3, 37)' in d.source.text()
            assert f'[{12+delta}, {Fraction(167,4)+delta})' in d.impact.text()
            assert f'[{10+delta}, {Fraction(87,2)+delta})' in d.impact.text()
            paths=[]
            for position in ('top','details'):
                if position=='details':d.scroll.verticalScrollBar().setValue(d.scroll.verticalScrollBar().maximum());pump()
                name=f'{locale}-{geometry}-{scale:g}-{position}.png';assert d.grab().save(str(out/name));paths.append(name)
            assert d.plan is plan and (w.project,w.editor._undo,w.editor._redo,w.editor.revision,w.revision)==before
            d.cancel_button.click();assert d._closed_flag and w.linked_av_slip_dialog is None
            assert (w.project,w.editor._undo,w.editor._redo,w.editor.revision,w.revision)==before
            records.append(dict(locale=locale,geometry=geometry,role=role,device_pixel_ratio=scale,
                logical_size=[d.width(),d.height()],initial_input_fully_visible=True,compact_summary_visible=True,
                separate_role_source_and_fixed_timeline_intervals=True,real_cjk_glyphs=True,
                localized_accessible_actions=True,horizontal_scroll=0,preview_cancel_no_commit=True,paths=paths))
    assert not warnings
finally:
    if w.linked_av_slip_dialog is not None:w.linked_av_slip_dialog.reject()
    w.cancel_jobs();settle();w.dirty=False;w.closing=True;w.close();pump()
assert (source.read_bytes(),source.stat().st_mtime_ns)==source_before
origins={}
for name,module in tuple(sys.modules.items()):
    if name=='lumen' or name.startswith(('lumen.','scripts.')):
        path=getattr(module,'__file__',None)
        if path is None:continue
        path=Path(path).resolve();assert path.is_relative_to(root) and path.suffix=='.py'
        origins[name]=dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
size=sum(path.stat().st_size for path in out.glob('*.png'));assert size<2*1024*1024
report=dict(status='passed',identity_only_fixture=True,actual_media_proof_claimed=False,native_validation=False,
    font_family=QFontInfo(font).family(),import_origins=origins,source_unchanged=True,records=records,screenshot_bytes=size)
(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(status='passed',screens=8,bytes=size)))
'''


@pytest.mark.parametrize('scale', [1, 1.5, 2])
def test_affine_slip_bilingual_initial_input_and_per_role_details_at_real_dpr(tmp_path, scale):
    root=Path(__file__).resolve().parents[1];out=tmp_path/'affine-slip-screens'
    result=subprocess.run([sys.executable,'-c',DRIVER,str(root),str(out),str(scale)],cwd=root,
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads((out/'report.json').read_text())
    assert report['status']=='passed' and report['identity_only_fixture'] and report['source_unchanged']
    assert not report['actual_media_proof_claimed'] and not report['native_validation']
    assert len(report['records'])==4 and report['screenshot_bytes']<2*1024*1024
    import hashlib
    assert 'lumen.ui_linked_av' in report['import_origins']
    for row in report['import_origins'].values():
        path=Path(row['path']);assert path.is_relative_to(root)
        assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']
    for row in report['records']:
        assert row['device_pixel_ratio']==scale and row['horizontal_scroll']==0
        for flag in ('initial_input_fully_visible','compact_summary_visible','separate_role_source_and_fixed_timeline_intervals',
                     'real_cjk_glyphs','localized_accessible_actions','preview_cancel_no_commit'):assert row[flag]
        assert len(row['paths'])==2
        for name in row['paths']:assert (out/name).read_bytes().startswith(b'\x89PNG')

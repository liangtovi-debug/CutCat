"""Bounded fresh-process EN/ZH linked-roll inspector QA at actual Qt DPR.

Reuses the existing offscreen capture pattern. Identity-only sources are never
rendered. This checks layout and transaction boundaries, not native platforms.
"""
from pathlib import Path
from copy import deepcopy
from fractions import Fraction
import argparse
import hashlib
import json
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--scale', type=float, default=1)
args = parser.parse_args()
os.environ['QT_SCALE_FACTOR'] = str(args.scale)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFont, QFontInfo, QTextLayout
from PySide6.QtWidgets import QApplication, QMessageBox
from lumen.i18n import set_locale, tr
from lumen.model import AudioMixAnchor, Clip, Editor, Media, Project
from lumen.timing import ClipTimeMap
from lumen.ui import MainWindow
from lumen.ui_linked_av_roll import ROLL_STALE

args.output.mkdir(parents=True, exist_ok=True)
app = QApplication([]); font = QFont('Noto Sans CJK SC', 10); app.setFont(font)
for text in ('Apply joint roll edit Cancel roll draft', '音画联合滚动剪辑取消滚动草稿左侧右侧视频音频'):
    layout = QTextLayout(text, font); layout.beginLayout(); layout.createLine().setLineWidth(2000); layout.endLayout()
    assert all(index != 0 for run in layout.glyphRuns() for index in run.glyphIndexes())
files = ('lumen/ui.py','lumen/ui_linked_av_roll.py','lumen/ui_track_edit_locks.py','lumen/linked_av_roll.py',
         'lumen/model.py','lumen/i18n_catalog_roll.py','scripts/capture_linked_av_roll_ui.py')
identity = lambda: {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
source_identity = identity(); records = []; warnings = []
QMessageBox.warning = lambda *items: warnings.append(str(items[2]))
QMessageBox.information = lambda *_: None


def events():
    for _ in range(5): app.processEvents()


def snapshot(w):
    return deepcopy((w.project,w.editor._undo,w.editor._redo,w.editor.revision,w.revision,
                     tuple(w.selected_clip_ids),w.selection))


def capture(w, tag, state):
    events()
    assert w.devicePixelRatioF() == args.scale
    assert w.roll_range_note.wordWrap() and w.roll_range_note.textFormat() == Qt.TextFormat.PlainText
    assert w.roll_range_note.text() == w.roll_range_note.toolTip() == w.roll_range_note.accessibleDescription()
    assert w.roll_range_note.width() <= w.roll_group.width()
    assert w.roll_range_note.height() >= w.roll_range_note.heightForWidth(w.roll_range_note.width())
    for control in (w.roll_cut_spin,w.apply_roll_button,w.cancel_roll_button):
        assert control.width() > 60 and control.width() <= w.roll_group.width()
    assert w.inspector.horizontalScrollBar().maximum() == 0
    paths = []
    for position, target in (('preview',w.roll_cut_spin),('actions',w.apply_roll_button)):
        w.inspector.ensureWidgetVisible(target,0,20); events()
        filename = f'{tag}-{state}-{position}.png'
        assert w.inspector.grab().save(str(args.output/filename)); paths.append(filename)
    return paths


with tempfile.TemporaryDirectory(prefix='linked-roll-ui-') as raw:
    temp = Path(raw); source = temp/'identity.mov'
    source.write_bytes(b'Identity-only roll inspector fixture; no decoding')
    source_stat = source.stat(); digest = hashlib.sha256(source.read_bytes()).hexdigest()
    w = MainWindow(workspace_settings=QSettings(str(temp/'settings.ini'),QSettings.Format.IniFormat),
                   ui_language='en',recovery_root=temp/'recovery')
    w.timeline.waveforms.request = lambda *_: None
    try:
        for locale in ('en','zh-CN'):
            set_locale(locale)
            for mode in ('simple','professional'):
                w.set_workspace_mode(mode,persist=False)
                for geometry, width, panel in (('normal',1366,440),('narrow',1000,285)):
                    tag=f'{locale}-{mode}-{geometry}-{args.scale:g}'
                    left=ClipTimeMap(Fraction(1,2),31,Fraction(3,2))
                    right=ClipTimeMap(20,Fraction(101,2),Fraction(2,3))
                    w.editor=Editor(Project(media=[Media('m',str(source),'Synthetic AV <unescaped name>',20,64,48,True,True)],clips=[
                        Clip('left-video','m',0,10,0,21,time_map=left,source_audio_enabled=False),
                        Clip('left-audio','m',2,10,0,21,time_map=left,fade_in=2,fade_out=3,
                             audio_mix_anchor=AudioMixAnchor(0,10,'left-video')),
                        Clip('right-video','m',0,31,0,46,time_map=right,source_audio_enabled=False),
                        Clip('right-audio','m',2,31,0,46,time_map=right,fade_in=3,fade_out=2,
                             audio_mix_anchor=AudioMixAnchor(0,31,'right-video'))]))
                    w.editor.link_av_clips('left-video','left-audio');w.editor.link_av_clips('right-video','right-audio')
                    w.refresh(); w.active_selection_type='clip'
                    w.set_selected_clip_ids(tuple(c.id for c in w.project.clips),anchor='right-audio')
                    w.refresh_inspector(); w.resize(width,768); w.show();w.activateWindow();QApplication.setActiveWindow(w)
                    w.main_split.setSizes([180,width-panel-180,panel]);w.timeline.setFocus();events()
                    assert w.roll_cut_spin.value()==31 and w.selection=='right-audio'
                    before=snapshot(w);w.roll_cut_spin.setValue(33);events()
                    assert w.apply_roll_button.isEnabled() and snapshot(w)==before
                    for heading in ('Left V','Left A','Right V','Right A'): assert str(tr(heading)) in w.roll_range_note.text()
                    paths=capture(w,tag,'ready')
                    w.cancel_roll_button.click();assert snapshot(w)==before and w.roll_cut_spin.value()==31
                    w.roll_cut_spin.setValue(10);assert not w.apply_roll_button.isEnabled()
                    paths+=capture(w,tag,'rejected')
                    w.cancel_roll_button.click();w.roll_cut_spin.setValue(33)
                    # Editor Undo ABA returns equal payload but must expire the
                    # captured intent even if no MainWindow refresh occurred.
                    w.editor.update_clip('left-audio',gain_db=-3);w.editor.undo()
                    expected=snapshot(w);w.check_linked_roll_current();w.apply_roll()
                    assert snapshot(w)==expected and w.roll_range_note.text()==str(tr(ROLL_STALE))
                    paths+=capture(w,tag,'stale')
                    w.cancel_roll_button.click();w.roll_cut_spin.setValue(33)
                    old=deepcopy(w.project);depth=len(w.editor._undo);w.apply_roll_button.click();events()
                    assert len(w.editor._undo)==depth+1 and w.selection=='right-audio'
                    after=deepcopy(w.project);w.undo();assert w.project==old
                    w.redo();assert w.project==after
                    assert all(not panel.isEnabled() for panel in w._roll_other_panels)
                    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest and source.stat().st_mtime_ns==source_stat.st_mtime_ns
                    assert not w.jobs
                    records.append({'locale':locale,'workspace':mode,'geometry':geometry,
                        'window_logical_size':[w.width(),w.height()],'inspector_logical_width':w.inspector.width(),
                        'device_pixel_ratio':w.devicePixelRatioF(),'four_roles_wrapped':True,
                        'no_horizontal_scroll':True,'rejection_and_stale_accessible':True,
                        'cancel_no_change':True,'undo_aba_stale':True,'single_apply_undo_redo':True,
                        'active_right_audio_preserved':True,'source_unchanged':True,'paths':paths})
    finally:
        w.cancel_jobs()
        for job in list(w.jobs): assert job.wait(5000)
        w.dirty=False;w.closing=True;w.close();events()
assert not warnings,warnings
assert identity()==source_identity
report={'status':'passed','identity_only_fixture':True,'native_windows_macos_validation':False,
        'font_family':QFontInfo(font).family(),'source_identity':source_identity,'records':records,
        'screenshot_bytes':sum(path.stat().st_size for path in args.output.glob('*.png'))}
(args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':'passed','records':len(records),'bytes':report['screenshot_bytes']}))

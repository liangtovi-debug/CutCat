"""Fresh DPR bilingual joint-split dialog QA; media qualification is mocked.

This script verifies Qt layout, lifecycle and model edit boundaries, not CFR,
actual RGB/PCM continuity or native Windows/macOS behavior.
"""
from pathlib import Path
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import argparse
import hashlib
import json
import os
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--scale', type=float, default=1)
parser.add_argument('--affine', action='store_true', help='Capture unequal-edge affine linked pairs')
args = parser.parse_args()
os.environ['QT_SCALE_FACTOR'] = str(args.scale)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPoint, QRect, QSettings, Qt
from PySide6.QtGui import QFont, QFontInfo, QTextLayout
from PySide6.QtWidgets import QApplication, QMessageBox
from lumen.i18n import set_locale, tr
from lumen.model import AV_LINK_AFFINE_ALIGNMENT, AudioMixAnchor, Clip, Editor, Media, Project
from lumen.timing import ClipTimeMap
from lumen.source_viewer import SourceIdentity, SourcePreviewBackend
from lumen.clip_copy_paste import ClipDependency
import lumen.clip_copy_paste as copy_module
import lumen.ui_linked_av_split as ui
from lumen.ui import MainWindow

args.output.mkdir(parents=True, exist_ok=True)
app = QApplication([]); font = QFont('Noto Sans CJK SC', 10); app.setFont(font)
for text in ('Preview joint split Apply joint split Cancel', '预览联合分割应用联合分割取消'):
    layout = QTextLayout(text, font); layout.beginLayout(); layout.createLine().setLineWidth(2000); layout.endLayout()
    assert all(index != 0 for run in layout.glyphRuns() for index in run.glyphIndexes())
files = ('lumen/ui.py','lumen/ui_linked_av.py','lumen/ui_linked_av_split.py',
    'lumen/ui_color_reference.py','lumen/shortcuts.py','lumen/linked_av_split.py',
    'lumen/i18n_catalog_linked_av.py','scripts/capture_linked_av_split_ui.py')
identity = lambda: {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
source_identity = identity(); records = []; warnings = []; capture_geometry = {}
QMessageBox.warning = lambda *items: warnings.append(str(items[2]))
QMessageBox.information = lambda *_: None
proof_identity = SourceIdentity('/identity-only.mov', (), 'mock-source-sha', 'proof-signature')
copy_module._dependencies = lambda *a, **k: (ClipDependency('media','media',proof_identity),)
SourcePreviewBackend.verify_linked_av_split_source = lambda *a, **k: proof_identity
real_verify = ui.verify_linked_av_split


def settle(window):
    deadline = time.monotonic() + 8
    while window.jobs and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.005)
    app.processEvents(); assert not window.jobs


def focus(window):
    window.show(); window.activateWindow(); QApplication.setActiveWindow(window)
    window.timeline.setFocus(); app.processEvents()


def fit_and_capture(dialog, width, name, feedback=None):
    dialog.fit_available_screen(QRect(0,0,width,720)); app.processEvents()
    assert dialog.devicePixelRatioF() == args.scale
    assert dialog.width() <= width-32 and dialog.height() <= 656
    for button in (dialog.preview_button, dialog.apply_button, dialog.cancel_button):
        assert button.accessibleName() == button.text() and button.toolTip() == button.text()
        assert dialog.rect().contains(button.geometry()) and button.width() > 80
    assert dialog.preview_button.text() == tr('Preview joint split')
    assert dialog.apply_button.text() == tr('Apply joint split')
    for label in (dialog.scope,dialog.source,dialog.limits,dialog.check_note,dialog.status,dialog.impact):
        assert label.textFormat() == Qt.TextFormat.PlainText and label.wordWrap()
        if label.text():
            assert label.width() <= dialog.scroll.viewport().width()
            assert label.height() >= max(0,label.heightForWidth(label.width()))
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
    if feedback is not None:
        target = dialog.impact if feedback == 'failed' else dialog.status
        assert dialog._feedback_state[0] == feedback and target.text()
        top = target.mapTo(dialog.scroll.viewport(), QPoint(0, 0))
        height = target.height() if target.height() <= dialog.scroll.viewport().height()-16 else min(target.height(),target.fontMetrics().lineSpacing())
        required = QRect(top.x(),top.y(),target.width(),height)
        assert dialog.scroll.viewport().rect().contains(required), (feedback, required, dialog.scroll.viewport().size())
        assert dialog._feedback_reveal_pending is None
        if feedback == 'cancelled': assert dialog.status.text() == tr('Joint split check cancelled. No changes were applied; preview again to continue.')
        # Repeated state checks must respect the user's later scroll choice.
        bar = dialog.scroll.verticalScrollBar(); previous = bar.value(); user_scroll = bar.maximum(); bar.setValue(user_scroll)
        for _ in range(3): dialog.check_current(); app.processEvents()
        assert bar.value() == user_scroll
        bar.setValue(previous); app.processEvents()
        top = target.mapTo(dialog.scroll.viewport(), QPoint(0,0))
        assert dialog.scroll.viewport().rect().contains(QRect(top.x(),top.y(),target.width(),height))
    pixels = dialog.grab()
    assert pixels.save(str(args.output/name))
    capture_geometry[name] = {'available_width': width,
        'logical_width': dialog.width(), 'logical_height': dialog.height(),
        'physical_width': pixels.width(), 'physical_height': pixels.height(),
        'device_pixel_ratio': dialog.devicePixelRatioF()}



with tempfile.TemporaryDirectory(prefix='linked-split-ui-') as raw:
    source = Path(raw)/'identity.mov'; source.write_bytes(b'Identity-only fixture, never decoded')
    stat = source.stat(); digest = hashlib.sha256(source.read_bytes()).hexdigest()
    w = MainWindow(workspace_settings=QSettings(str(Path(raw)/'settings.ini'), QSettings.Format.IniFormat),
        ui_language='en', recovery_root=Path(raw)/'recovery')
    w.timeline.waveforms.request = lambda *_: None
    try:
        for locale in ('en','zh-CN'):
            set_locale(locale)
            for geometry,width in (('normal',780),('narrow',390)):
                tag=f'{locale}-{geometry}-{args.scale:g}'; paths=[]
                w.editor = Editor(Project(media=[Media('media',str(source),'Synthetic AV',4,64,48,True,True)], clips=[
                    Clip('video','media',0,5,12,30,source_audio_enabled=False),
                    Clip('audio','media',2,5,12,30,fade_in=2,fade_out=3,
                         audio_mix_anchor=AudioMixAnchor(0,5,'video'))]))
                w.editor.link_av_clips('video','audio')
                if args.affine:
                    video, audio = w.editor._clip('video'), w.editor._clip('audio')
                    video.time_map = ClipTimeMap(12, Fraction(167, 4))
                    audio.start, audio.source_in, audio.duration = 3, Fraction(10), 34
                    audio.time_map = ClipTimeMap(10, Fraction(87, 2))
                    w.project.av_links[0] = replace(w.project.av_links[0], alignment=AV_LINK_AFFINE_ALIGNMENT)
                    w.project.validate()
                w.refresh(); w.select_clip('video'); w.seek_frame(20)
                focus(w); before=deepcopy((w.project,w.editor._undo,w.editor._redo,w.editor.revision))
                assert w.split_clip_button.isEnabled() and w.shortcuts.actions['edit.split'].isEnabled()
                w.split_clip_button.click(); app.processEvents(); d=w.linked_av_split_dialog
                assert d is not None and d.preview_button.isEnabled() and not d.apply_button.isEnabled()
                plan=d.plan; ready=f'{tag}-ready.png'; fit_and_capture(d,width,ready); paths.append(ready)
                d.preview(); settle(w); assert d.plan is plan and d.apply_button.isEnabled()
                verified=f'{tag}-verified.png'; fit_and_capture(d,width,verified,'verified'); paths.append(verified)
                w.seek_frame(21); w.seek_frame(20); d.check_current()
                assert d._stale and not d.apply_button.isEnabled()
                stale=f'{tag}-stale.png'; fit_and_capture(d,width,stale,'stale'); paths.append(stale); d.reject()
                focus(w); d=w.open_linked_av_split(); assert d is not None
                d.fit_available_screen(QRect(0,0,width,720)); app.processEvents()
                def fail(*a,**k): raise ValueError('Linked A/V split source range is incomplete or variable-rate')
                ui.verify_linked_av_split=fail
                d.preview(); settle(w)
                assert d._last_error and not d.apply_button.isEnabled()
                failed=f'{tag}-failed.png'; fit_and_capture(d,width,failed,'failed'); paths.append(failed); d.reject()
                ui.verify_linked_av_split=real_verify
                focus(w); d=w.open_linked_av_split(); assert d is not None
                d.fit_available_screen(QRect(0,0,width,720)); app.processEvents()
                entered,release=threading.Event(),threading.Event()
                def blocked(*a,**k):
                    entered.set(); assert release.wait(5)
                    return real_verify(*a,**k)
                ui.verify_linked_av_split=blocked
                try:
                    d.preview(); assert entered.wait(2)
                    w.cancel_jobs(); d.preview(); assert len(w.jobs)==1
                finally: release.set()
                settle(w); assert d.preview_receipt is None and not d.apply_button.isEnabled()
                cancelled=f'{tag}-cancelled.png'; fit_and_capture(d,width,cancelled,'cancelled'); paths.append(cancelled)
                d.reject(); ui.verify_linked_av_split=real_verify
                assert (w.project,w.editor._undo,w.editor._redo,w.editor.revision)==before
                assert w.linked_av_split_dialog is None and not d.timer.isActive()
                assert hashlib.sha256(source.read_bytes()).hexdigest()==digest and source.stat().st_mtime_ns==stat.st_mtime_ns
                records.append({'affine':args.affine,'locale':locale,'geometry':geometry,'available_width':width,
                    'device_pixel_ratio':args.scale,'ready_verified_stale_failed_cancelled':True,
                    'same_plan_during_preview':True,'one_shared_entry_qualification':True,
                    'localized_accessible_buttons':True,'wrapped_full_text_scrollable':True,
                    'terminal_feedback_full_when_fitting_else_full_first_line':True,'unchanged_state_preserves_user_scroll':True,
                    'late_cancel_result_ignored':True,'no_project_or_source_changes':True,'paths':paths})
        assert len(warnings)==4
    finally:
        w.cancel_jobs(); settle(w); w.dirty=False; w.closing=True; w.close(); app.processEvents()
assert source_identity==identity()
report={'status':'passed','identity_only_fixture':True,'mocked_media_worker':True,
    'native_windows_macos_validation':False,'actual_media_proof_claimed':False,
    'font_family':QFontInfo(font).family(),'source_identity':source_identity,'records':records,
    'capture_geometry':capture_geometry,
    'screenshot_bytes':sum(path.stat().st_size for path in args.output.glob('*.png'))}
(args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':'passed','records':len(records),'bytes':report['screenshot_bytes']}))

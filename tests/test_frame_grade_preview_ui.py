"""Real Qt jobs, cancellation, stale guards and isolated grade-preview display."""
from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QImage, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QInputDialog

from lumen.frame_grade_preview import capture_grade_preview_snapshot
from lumen.i18n import set_locale
from lumen.model import Clip, Editor, Project
from lumen.ui import MainWindow
from lumen.ui_component_setup import ComponentSetupDialog
from lumen.ui_frame_grade_preview import FrameGradePreviewDialog, GradePreviewCanvas, _display_pixels
from test_selective_color_render import source_project


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: pytest.fail(str(args[2])))
    w = MainWindow(workspace_settings=QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat),
                   recovery_root=tmp_path / 'recovery', ui_language='en')
    project = source_project(tmp_path)
    project.clips.append(Clip('other', project.media[0].id, 0, 2, 0, 1))
    w.editor = Editor(project); w.refresh(); w.select_clip('picture')
    w.inspector_tabs.setCurrentIndex(1)
    w.resize(1100, 760); w.show(); app.processEvents()
    yield w
    for dialog in w.findChildren(FrameGradePreviewDialog): dialog.reject()
    w.cancel_jobs()
    for job in list(w.jobs): assert job.wait(5000)
    app.processEvents()
    w.dirty = False; w.closing = True; w.close(); w.deleteLater(); app.processEvents()


def drain(app, condition, seconds=10):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.005)
    app.processEvents()
    assert condition()


def result(snapshot):
    return snapshot, _display_pixels(snapshot.frame, threading.Event()), None


def loaded(window):
    dialog = FrameGradePreviewDialog(window); dialog.show()
    dialog.context = dialog._context()
    snapshot = capture_grade_preview_snapshot(window.project, window.selection, window.timeline.playhead)
    dialog._accept_result(dialog._generation, dialog.context, result(snapshot))
    assert dialog.current()
    return dialog


def test_explicit_modeless_grade_stage_and_full_precision_display_only(window, app):
    w = window
    before = deepcopy(w.project); revision = w.revision
    d = w.open_frame_grade_preview()
    assert not d.isModal() and w.open_frame_grade_preview() is d
    drain(app, lambda: d.current() and not w.jobs)
    assert d.snapshot.frame.dtype == np.float32
    assert d.snapshot.frame.shape == (w.project.height, w.project.width, 3)
    assert d.canvas.image.format() == QImage.Format.Format_RGB888
    assert d.snapshot.stage == 'grade_preview'
    assert 'full grade + LUT' in d.stage.text()
    for excluded in ('effects', 'transforms', 'fades', 'captions', 'overlays', 'compositing'):
        assert excluded in d.stage.text().split('Before ')[1]
    assert 'not the final timeline' in d.status.text()
    assert 'clipped 8-bit SDR' in d.precision.text()
    assert w.project == before and not w.editor.can_undo and revision == w.revision
    assert not list(w.cache.iterdir()) and not w.preview_paths
    assert w.video.image.isNull() and w.video.frame_provenance is None
    assert w.scopes.data is None and w.video.reference is None
    assert w.preview_revision == -1 and w.loaded_preview_mode is None
    QTest.mouseClick(d.close_button, Qt.MouseButton.LeftButton)
    assert d.snapshot is None and d.canvas.image.isNull()


@pytest.mark.parametrize('change', ['selection', 'revision', 'frame', 'grade', 'source', 'project', 'restoration', 'active_type'])
def test_context_changes_clear_pixels_without_implicit_rerender(window, monkeypatch, change):
    w = window; d = loaded(w)
    monkeypatch.setattr(w, 'start_job', lambda *args: pytest.fail('No implicit rerender is allowed'))
    if change == 'selection': w.select_clip('other')
    elif change == 'revision': w.revision += 1
    elif change == 'frame': w.timeline.playhead += 1
    elif change == 'grade': w.selected_clip().grade.saturation = .6
    elif change == 'source': os.utime(w.project.media[0].path, ns=(1, 1))
    elif change == 'project': w.editor = Editor(deepcopy(w.project))
    elif change == 'restoration': w.selected_clip().restoration.denoise = .6
    else: w.active_selection_type = 'overlay'
    d.check_current()
    assert d.state == 'stale' and not d.current()
    assert d.snapshot is None and d.canvas.image.isNull()
    assert 'stale' in d.status.text()


def test_adjustment_requires_explicit_refresh_then_latest_grade_is_shown(window, app):
    w = window; d = loaded(w); before = d.snapshot.frame.copy()
    w.grade_spins['linear_exposure'].setValue(-1)
    d.check_current()
    assert not w.jobs and d.state == 'stale'
    d.refresh_frame()
    drain(app, lambda: d.current() and not w.jobs)
    assert np.mean(d.snapshot.frame) < np.mean(before)
    assert len(w.editor._undo) == 1


def test_repeated_refresh_coalesces_one_active_and_latest_pending_request(window, app, monkeypatch):
    import lumen.ui_frame_grade_preview as module
    w = window
    initial = capture_grade_preview_snapshot(w.project, 'picture', 0)
    final = capture_grade_preview_snapshot(w.project, 'other', 2)
    entered = threading.Event(); release = threading.Event(); calls = []
    def capture(project, clip_id, frame, **kwargs):
        calls.append((clip_id, frame))
        if len(calls) == 1:
            entered.set(); assert release.wait(8)
            return initial
        return final
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    d = w.open_frame_grade_preview()
    try:
        assert entered.wait(2)
        old_generation, old_context = d._generation, d.context
        for _ in range(5): d.refresh_frame()
        w.select_clip('other'); w.timeline.playhead = 2
        d.refresh_frame()
        assert len(w.jobs) == 1 and len(calls) == 1 and d.state == 'waiting'
        # A callback already queued before cancellation must also be rejected.
        d._accept_result(old_generation, old_context, result(initial))
        assert d.snapshot is None
        release.set(); drain(app, lambda: d.current() and not w.jobs)
        assert calls == [('picture', 0), ('other', 2)]
        assert d.snapshot is final and d.snapshot.identity.clip_id == 'other'
        d._accept_result(old_generation, old_context, (None, None, 'late failure'))
        assert d.snapshot is final and d.state == 'current'
    finally:
        release.set()


def test_close_reopen_waits_for_canceled_worker_without_resurrecting_old_dialog(window, app, monkeypatch):
    import lumen.ui_frame_grade_preview as module
    w = window; snapshot = capture_grade_preview_snapshot(w.project, 'picture', 0)
    entered = threading.Event(); release = threading.Event(); calls = []
    def capture(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            entered.set(); assert release.wait(8)
        return snapshot
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    first = w.open_frame_grade_preview()
    try:
        assert entered.wait(2)
        generation, context = first._generation, first.context
        first.reject(); second = w.open_frame_grade_preview()
        assert second is not first and second.state == 'waiting' and len(w.jobs) == 1
        first._accept_result(generation, context, result(snapshot))
        assert first.snapshot is None and first.canvas.image.isNull()
        release.set(); drain(app, lambda: second.current() and not w.jobs)
        assert first.snapshot is None and second.snapshot is snapshot and len(calls) == 2
    finally:
        release.set()


@pytest.mark.parametrize('global_cancel', [False, True])
def test_cancel_rejects_late_callbacks_and_releases_job_registry(window, app, monkeypatch, global_cancel):
    import lumen.ui_frame_grade_preview as module
    w = window; snapshot = capture_grade_preview_snapshot(w.project, 'picture', 0)
    entered = threading.Event(); release = threading.Event()
    def capture(*args, **kwargs):
        entered.set(); assert release.wait(8)
        return snapshot
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    d = w.open_frame_grade_preview()
    try:
        assert entered.wait(2)
        if global_cancel: w.cancel_jobs()
        else: QTest.mouseClick(d.cancel_button, Qt.MouseButton.LeftButton)
        release.set(); drain(app, lambda: not w.jobs)
        assert d.snapshot is None and d.canvas.image.isNull() and d.state == 'canceled'
        assert not d.cancel_button.isEnabled()
    finally:
        release.set()


@pytest.mark.parametrize('failure', ['active_stabilization', 'frame_outside', 'audio', 'selection'])
def test_unsupported_preflight_does_not_start_worker(window, monkeypatch, failure):
    w = window
    monkeypatch.setattr(w, 'start_job', lambda *args: pytest.fail('Unsupported modes must not start a worker'))
    if failure == 'active_stabilization': w.selected_clip().stabilization.enabled = True
    elif failure == 'frame_outside': w.timeline.playhead = 1
    elif failure == 'audio': w.project.media[0].has_video = False
    else: w.active_selection_type = 'caption'
    d = w.open_frame_grade_preview()
    assert d.state == 'unsupported' and 'Unavailable' in d.status.text()
    assert d.snapshot is None and d.canvas.image.isNull() and not w.jobs


def test_backend_failure_stays_local_and_retry_succeeds(window, app, monkeypatch):
    import lumen.ui_frame_grade_preview as module
    w = window; capture = module.capture_grade_preview_snapshot
    def failed(*args, **kwargs): raise ValueError('Transparent source formats are unsupported.')
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', failed)
    d = w.open_frame_grade_preview(); drain(app, lambda: not w.jobs)
    assert d.state == 'unsupported' and 'Transparent' in d.status.text()
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    d.refresh_frame(); drain(app, lambda: d.current() and not w.jobs)


def test_active_frame_job_blocks_new_project_open_and_settings_apply(window, app, tmp_path, monkeypatch):
    import lumen.ui_frame_grade_preview as module
    w = window; snapshot = capture_grade_preview_snapshot(w.project, 'picture', 0)
    entered = threading.Event(); release = threading.Event(); notices = []
    def capture(*args, **kwargs):
        entered.set(); assert release.wait(8)
        return snapshot
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: notices.append(args[2]))
    d = w.open_frame_grade_preview()
    try:
        assert entered.wait(2)
        original = w.project
        w.new_project(); w.open_project()
        assert w.project is original and len(notices) == 2
        setup = ComponentSetupDialog(w, path=tmp_path / 'components.json')
        setup.apply_paths()
        assert 'current task' in setup.status.text() and not setup.path.exists()
        setup.start_diagnostics()
        assert 'current task' in setup.status.text() and len(w.jobs) == 1
        setup.reject()
        d.cancel_refresh(); release.set(); drain(app, lambda: not w.jobs)
        monkeypatch.setattr(QInputDialog, 'getItem', lambda *args: ('1280 × 720 · 30 fps', True))
        w.new_project(); d.check_current()
        assert w.project is not original and not w.project.clips
        d.refresh_frame(); assert d.state == 'unsupported'
    finally:
        release.set()


def test_existing_job_blocks_preview_refresh_without_interference(window, app, monkeypatch):
    w = window; release = threading.Event()
    job = w.start_job('test diagnostics', lambda cancel, progress: release.wait(8), lambda value: None, 'componentsetup')
    try:
        d = w.open_frame_grade_preview()
        assert len(w.jobs) == 1 and d.job is None
        assert 'current task' in d.status.text()
        assert not d.refresh_button.isEnabled() and not d.timeline_button.isEnabled()
        d.reject(); assert not job.cancel.is_set()
    finally:
        release.set(); drain(app, lambda: not w.jobs)


def test_handoff_cancels_and_drains_before_full_timeline_preview(window, app, monkeypatch):
    import lumen.ui_frame_grade_preview as module
    w = window; snapshot = capture_grade_preview_snapshot(w.project, 'picture', 0)
    entered = threading.Event(); release = threading.Event(); full_preview = []
    def capture(*args, **kwargs):
        entered.set(); assert release.wait(8)
        return snapshot
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    monkeypatch.setattr(w, 'build_preview', lambda: full_preview.append((len(w.jobs), w.preview_mode, w.project)))
    w.preview_mode = 'bypass'
    d = w.open_frame_grade_preview()
    try:
        assert entered.wait(2)
        assert d.timeline_button.isEnabled()
        QTest.mouseClick(d.timeline_button, Qt.MouseButton.LeftButton)
        assert not d.isVisible() and not full_preview
        assert d.job.cancel.is_set()
        release.set(); drain(app, lambda: bool(full_preview))
        assert full_preview == [(0, 'bypass', w.project)]
        assert not w.preview_paths and d.snapshot is None
    finally:
        release.set()


@pytest.mark.parametrize('closing', [False, True])
def test_pending_handoff_never_runs_after_context_change_or_shutdown(window, app, monkeypatch, closing):
    import lumen.ui_frame_grade_preview as module
    w = window; snapshot = capture_grade_preview_snapshot(w.project, 'picture', 0)
    entered = threading.Event(); release = threading.Event(); calls = []
    def capture(*args, **kwargs):
        entered.set(); assert release.wait(8)
        return snapshot
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    monkeypatch.setattr(w, 'build_preview', lambda: calls.append(1))
    d = w.open_frame_grade_preview()
    try:
        assert entered.wait(2)
        d.build_timeline_preview()
        if closing: w.closing = True
        else: w.revision += 1
        release.set(); drain(app, lambda: not w.jobs)
        assert not calls and d.snapshot is None
    finally:
        release.set()


def test_window_close_cancels_owned_preview_and_releases_buffers(window, app, monkeypatch):
    import lumen.ui_frame_grade_preview as module
    w = window; snapshot = capture_grade_preview_snapshot(w.project, 'picture', 0)
    entered = threading.Event(); release = threading.Event()
    def capture(*args, **kwargs):
        entered.set(); assert release.wait(8)
        return snapshot
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    d = w.open_frame_grade_preview()
    try:
        assert entered.wait(2)
        w.close()
        assert w.closing and not d.isVisible() and d.job.cancel.is_set()
        assert not d.timer.isActive() and d.snapshot is None
        release.set(); drain(app, lambda: not w.jobs and not w.isVisible())
    finally:
        release.set()


def test_display_quantization_is_worker_local_bounded_and_does_not_mutate_source():
    frame = np.array([[[-.2, .5001, 1.2], [.1, .2, .3]]], dtype=np.float32)
    original = frame.copy(); cancel = threading.Event()
    width, height, pixels = _display_pixels(frame, cancel)
    assert (width, height) == (2, 1)
    assert list(pixels[:3]) == [0, 128, 255]
    assert np.array_equal(frame, original)
    cancel.set(); assert _display_pixels(frame, cancel) is None


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('width', [360, 760])
def test_narrow_dpi_geometry_locale_and_state_preservation(window, app, locale, width):
    w = window; d = loaded(w); snapshot = d.snapshot; project = deepcopy(w.project)
    set_locale(locale); d.resize(width, 700); app.processEvents()
    assert d.width() == width
    expected = '当前有效' if locale == 'zh-CN' else 'Current'
    assert expected in d.status.text()
    for widget in (d.stage, d.status, d.canvas, d.metadata, d.precision,
                   d.refresh_button, d.cancel_button, d.close_button, d.timeline_button):
        assert widget.visibleRegion().boundingRect() == widget.rect()
        assert widget.geometry().left() >= 0 and widget.geometry().right() < d.width()
    assert d.snapshot is snapshot and d.current() and w.project == project
    assert d.canvas.image.width() == w.project.width
    folder = os.environ.get('FRAME_GRADE_SCREENSHOTS')
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        assert d.grab().save(str(Path(folder) / f'frame-grade-{locale}-{width}-dpr{d.devicePixelRatioF():g}.png'))


@pytest.mark.parametrize('mode', ['graded', 'bypass'])
def test_final_viewer_scopes_reference_cache_and_before_after_mode_are_untouched(window, app, tmp_path, mode):
    from test_color_reference_ui import activate_synthetic
    w = window
    w.preview_mode = mode
    w.selected_clip().grade.linear_exposure = -1
    cached = activate_synthetic(w, tmp_path)
    w.preview_paths[(w.revision, mode)] = cached
    w.capture_reference()
    assert w.video.reference is not None and w.video.frame_provenance is not None
    image_key = w.video.image.cacheKey()
    provenance = w.video.frame_provenance
    scopes = w.scopes.data
    reference = w.video.reference
    paths = dict(w.preview_paths)
    request, revision = w.preview_request, w.preview_revision
    before = deepcopy(w.project)
    d = w.open_frame_grade_preview(); drain(app, lambda: d.current() and not w.jobs)
    assert d.state == 'current' and d.snapshot.stage == 'grade_preview'
    assert 'full grade + LUT' in d.stage.text()
    assert w.preview_mode == w.loaded_preview_mode == mode
    assert w.video.image.cacheKey() == image_key and w.video.frame_provenance == provenance
    assert w.scopes.data is scopes and w.video.reference is reference
    assert w.preview_paths == paths and w.preview_request == request and w.preview_revision == revision
    assert w.project == before and not w.editor.can_undo
    d.reject()
    assert w.video.reference is reference


def test_global_cancel_also_cancels_hidden_timeline_handoff(window, app, monkeypatch):
    import lumen.ui_frame_grade_preview as module
    w = window; snapshot = capture_grade_preview_snapshot(w.project, 'picture', 0)
    entered = threading.Event(); release = threading.Event(); handoffs = []
    def capture(*args, **kwargs):
        entered.set(); assert release.wait(8)
        return snapshot
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    monkeypatch.setattr(w, 'build_preview', lambda: handoffs.append(1))
    d = w.open_frame_grade_preview()
    try:
        assert entered.wait(2)
        d.build_timeline_preview(); assert not d.isVisible() and d._handoff_context is not None
        w.cancel_jobs(); release.set(); drain(app, lambda: not w.jobs)
        assert d._handoff_context is None and not handoffs
    finally:
        release.set()


def test_decoding_and_display_quantization_run_off_gui_thread(window, app, monkeypatch):
    import lumen.ui_frame_grade_preview as module
    from PySide6.QtCore import QTimer
    w = window; snapshot = capture_grade_preview_snapshot(w.project, 'picture', 0)
    main_thread = threading.get_ident(); observed = []; ticks = []
    entered = threading.Event(); release = threading.Event()
    original_display = module._display_pixels
    def capture(*args, **kwargs):
        observed.append(('decode', threading.get_ident()))
        entered.set(); assert release.wait(8)
        return snapshot
    def display(frame, cancel):
        observed.append(('display', threading.get_ident()))
        return original_display(frame, cancel)
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    monkeypatch.setattr(module, '_display_pixels', display)
    timer = QTimer(w); timer.setInterval(5); timer.timeout.connect(lambda: ticks.append(1)); timer.start()
    d = w.open_frame_grade_preview()
    try:
        assert entered.wait(2)
        drain(app, lambda: len(ticks) >= 3)
        assert d.state == 'rendering' and d.snapshot is None
        release.set(); drain(app, lambda: d.current() and not w.jobs)
        assert [name for name, _ in observed] == ['decode', 'display']
        assert all(thread != main_thread for _, thread in observed)
    finally:
        timer.stop(); release.set()


def test_accepted_idle_window_close_discards_already_queued_handoff(window, app, monkeypatch):
    from PySide6.QtCore import QTimer
    w = window; d = loaded(w)
    w.frame_grade_preview_dialog = d
    calls = []
    monkeypatch.setattr(w, 'build_preview', lambda: calls.append(1))
    d._handoff_context = (id(w.project), w.revision, w.preview_mode)
    d.reject()
    QTimer.singleShot(0, d._complete_handoff)
    assert not w.jobs and not w.closing
    w.close(); app.processEvents()
    assert d._handoff_context is None and not calls
    assert d.snapshot is None and d.canvas.image.isNull() and not w.isVisible()


@pytest.mark.parametrize('workspace', ['simple', 'professional'])
def test_color_panel_entry_button_opens_modeless_preview_in_both_workspaces(window, app, workspace):
    w = window
    w.set_workspace_mode(workspace, persist=False)
    w.side_tabs.setCurrentWidget(w.inspector)
    w.inspector_tabs.setCurrentIndex(1)
    w.inspector.ensureWidgetVisible(w.frame_grade_preview_button)
    app.processEvents()
    assert w.frame_grade_preview_button.isVisible() and w.frame_grade_preview_button.isEnabled()
    QTest.mouseClick(w.frame_grade_preview_button, Qt.MouseButton.LeftButton)
    d = w.frame_grade_preview_dialog
    assert isinstance(d, FrameGradePreviewDialog) and d.isVisible() and not d.isModal()
    drain(app, lambda: d.current() and not w.jobs)
    assert not w.editor.can_undo

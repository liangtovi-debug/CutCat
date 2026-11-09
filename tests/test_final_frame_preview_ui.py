"""Actual Qt lifecycle tests; backend pixels have a separate frozen oracle."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from lumen.final_frame_preview import project_signature
from lumen.model import Editor, Media, Project, Clip
from lumen.ui import MainWindow
from lumen.ui_final_frame_preview import FinalFramePreviewDialog


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def until(app, condition, timeout=8):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.003)
    app.processEvents()
    assert condition()


def pump(app, seconds=.1):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.003)


def dependencies(project):
    paths = [m.path for m in project.media]
    paths += [c.grade.lut_path for c in project.clips if c.grade.lut_path]
    paths += [o.asset_path for o in project.overlays if o.enabled]
    return tuple((p, hashlib.sha256(Path(p).read_bytes()).hexdigest()) for p in paths)


def fake_snapshot(project, frame):
    return SimpleNamespace(width=8, height=6, rgb_bytes=bytes([frame % 256, 160, 90]) * 48,
        stage='final_composite_sdr', identity=(project_signature(project), frame, dependencies(project)))


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    import lumen.ui_final_frame_preview as module
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    source = tmp_path / 'synthetic.mp4'
    source.write_bytes(b'UI identity fixture only; never decoded')
    w = MainWindow(workspace_settings=QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat),
                   recovery_root=tmp_path / 'recovery', ui_language='en')
    w.editor = Editor(Project(width=320, height=180,
        media=[Media('m', str(source), 'UI fixture', 10, 320, 180, True, False)],
        clips=[Clip('c', 'm', 0, 0, 0, 300)]))
    w.refresh(); w.select_clip('c'); w.show(); app.processEvents()
    calls = []; checks = []; main = threading.get_ident()
    def capture(project, frame, **kwargs):
        assert threading.get_ident() != main
        calls.append(frame)
        return fake_snapshot(project, frame)
    def verify(snapshot, project, frame, **kwargs):
        assert threading.get_ident() != main
        checks.append(frame)
        return snapshot.identity == (project_signature(project), frame, dependencies(project))
    monkeypatch.setattr(module, 'capture_final_frame_preview', capture)
    monkeypatch.setattr(module, 'verify_final_frame_preview', verify)
    w.test_captures, w.test_checks, w.test_warnings = calls, checks, warnings
    yield w
    for d in w.findChildren(FinalFramePreviewDialog):
        d.reject()
    w.cancel_jobs()
    for job in list(w.jobs):
        assert job.wait(5000)
    app.processEvents()
    w.dirty = False; w.closing = True; w.close(); w.deleteLater(); app.processEvents()


def opened(w):
    d = w.open_final_frame_preview()
    d.timer.stop()
    return d


def loaded(w, app):
    d = opened(w)
    d.refresh_frame()
    until(app, lambda: d.current() and not w.jobs)
    return d


def test_open_is_explicit_no_render_and_real_qt_worker_checks_before_display(window, app):
    w = window; before = deepcopy(w.project); revision = w.revision
    cache = w.preview_paths.copy()
    d = opened(w)
    assert w.open_final_frame_preview() is d and not d.isModal()
    pump(app, .4)
    assert not w.jobs and not w.test_captures and d.snapshot is None
    assert not d.auto_refresh_checkbox.isChecked()
    assert 'No audio' in d.stage.text() and 'Opening this window does not render' in d.status.text()
    QTest.mouseClick(d.refresh_button, Qt.MouseButton.LeftButton)
    until(app, lambda: d.current() and not w.jobs)
    assert w.test_captures == [0] and w.test_checks == [0]
    assert d.canvas.image.format() == QImage.Format.Format_RGB888
    assert d.snapshot.stage == 'final_composite_sdr'
    assert w.project == before and w.revision == revision and not w.editor.can_undo
    assert w.preview_paths == cache and not list(w.cache.iterdir())
    assert w.video.image.isNull() and w.video.frame_provenance is None
    assert w.scopes.data is None and w.video.reference is None
    assert 'SDR' in d.status.text() and 'No audio' in d.status.text()


@pytest.mark.parametrize('change', ['revision', 'frame', 'grade', 'project', 'selection', 'active_type'])
def test_pure_context_changes_clear_pixels_without_implicit_render(window, app, change):
    w = window; d = loaded(w, app)
    if change == 'revision': w.revision += 1
    elif change == 'frame': w.seek_frame(8)
    elif change == 'grade': w.project.clips[0].grade.exposure = .5
    elif change == 'project': w.editor = Editor(deepcopy(w.project))
    elif change == 'selection': w.selection = ''
    else: w.active_selection_type = 'caption'
    d.check_current()
    assert d.state == 'stale' and not d.current() and d.snapshot is None
    assert d.canvas.image.isNull() and w.test_captures == [0]


@pytest.mark.parametrize('kind', ['source', 'lut', 'sticker'])
def test_external_dependency_recheck_is_worker_only_and_clears_still(window, app, tmp_path, kind):
    from lumen.model import Overlay
    w = window
    path = Path(w.project.media[0].path)
    if kind == 'lut':
        path = tmp_path / 'look.cube'; path.write_bytes(b'initial look')
        w.project.clips[0].grade.lut_path = str(path)
    elif kind == 'sticker':
        # Model validation is deliberately not mocked rendering's job here.
        path = tmp_path / 'sticker.png'; path.write_bytes(b'initial sticker')
        w.project.overlays = [Overlay('o', str(path), '0' * 64, 0, 30)]
    d = loaded(w, app)
    before = len(w.test_checks)
    path.write_bytes(b'changed external bytes')
    d._next_verify = 0
    d.check_current()
    until(app, lambda: not w.jobs)
    assert len(w.test_checks) == before + 1
    assert d.snapshot is None and d.canvas.image.isNull() and d.state == 'stale'
    assert 'changed' in d.status.text() and w.test_captures == [0]


def test_one_running_latest_pending_and_late_success_error_are_ignored(window, app, monkeypatch):
    import lumen.ui_final_frame_preview as module
    w = window; entered = threading.Event(); release = threading.Event(); calls = []
    first = fake_snapshot(w.project, 0)
    def capture(project, frame, **kwargs):
        calls.append(frame)
        if len(calls) == 1:
            entered.set(); assert release.wait(8)
            return first
        return fake_snapshot(project, frame)
    monkeypatch.setattr(module, 'capture_final_frame_preview', capture)
    d = opened(w); d.refresh_frame()
    try:
        assert entered.wait(2)
        generation, context = d._generation, d.context
        for frame in range(1, 9):
            w.seek_frame(frame); d.refresh_frame()
        assert len(w.jobs) == 1 and calls == [0] and d.state == 'waiting'
        release.set(); until(app, lambda: d.current() and not w.jobs)
        assert calls == [0, 8] and d.context[2] == 8
        shown = d.snapshot
        d._accept_result('render', generation, context, None, (first, None))
        d._accept_result('render', generation, context, None, (None, 'obsolete failure'))
        assert d.snapshot is shown and d.state == 'current' and not w.test_warnings
    finally:
        release.set()


@pytest.mark.parametrize('change', ['seek_aba', 'undo_aba', 'project_aba'])
def test_aba_cannot_resurrect_a_canceled_capture(window, app, monkeypatch, change):
    import lumen.ui_final_frame_preview as module
    w = window; entered = threading.Event(); release = threading.Event()
    first = fake_snapshot(w.project, 0)
    def capture(*args, **kwargs):
        entered.set(); assert release.wait(8); return first
    monkeypatch.setattr(module, 'capture_final_frame_preview', capture)
    d = opened(w); d.refresh_frame()
    try:
        assert entered.wait(2)
        generation, context = d._generation, d.context
        if change == 'seek_aba': w.seek_frame(1); w.seek_frame(0)
        elif change == 'undo_aba':
            w.edit(lambda: w.editor.update_clip('c', gain_db=-3)); w.undo()
        else:
            original = w.editor
            w.editor = Editor(deepcopy(w.project)); w.refresh()
            w.editor = original; w.refresh()
        release.set(); until(app, lambda: not w.jobs)
        d._accept_result('render', generation, context, None, (first, None))
        d._accept_result('render', generation, context, None, (None, 'late failure'))
        assert d.snapshot is None and d.canvas.image.isNull() and not w.test_warnings
    finally:
        release.set()


def test_dependency_change_after_capture_is_checked_before_display(window, app, monkeypatch):
    import lumen.ui_final_frame_preview as module
    w = window
    def capture(project, frame, **kwargs):
        snapshot = fake_snapshot(project, frame)
        Path(project.media[0].path).write_bytes(b'changed after capture and before callback')
        return snapshot
    monkeypatch.setattr(module, 'capture_final_frame_preview', capture)
    d = opened(w); d.refresh_frame()
    until(app, lambda: not w.jobs and d.state == 'stale')
    assert d.snapshot is None and d.canvas.image.isNull() and w.test_checks == [0]


def test_close_reopen_waits_and_never_resurrects_old_window(window, app, monkeypatch):
    import lumen.ui_final_frame_preview as module
    w = window; entered = threading.Event(); release = threading.Event(); calls = []
    def capture(project, frame, **kwargs):
        calls.append(frame)
        if len(calls) == 1: entered.set(); assert release.wait(8)
        return fake_snapshot(project, frame)
    monkeypatch.setattr(module, 'capture_final_frame_preview', capture)
    first = opened(w); first.refresh_frame()
    try:
        assert entered.wait(2)
        first.reject(); second = opened(w)
        assert second is not first and second.state == 'idle' and len(calls) == 1
        second.refresh_frame()
        assert second.state == 'waiting' and len(w.jobs) == 1
        release.set(); until(app, lambda: second.current() and not w.jobs)
        assert first.snapshot is None and first.canvas.image.isNull() and calls == [0, 0]
    finally:
        release.set()


@pytest.mark.parametrize('global_cancel', [False, True])
def test_cancel_discards_late_result_and_queue(window, app, monkeypatch, global_cancel):
    import lumen.ui_final_frame_preview as module
    w = window; entered = threading.Event(); release = threading.Event()
    def capture(project, frame, **kwargs):
        entered.set(); assert release.wait(8); return fake_snapshot(project, frame)
    monkeypatch.setattr(module, 'capture_final_frame_preview', capture)
    d = opened(w); d.refresh_frame()
    try:
        assert entered.wait(2)
        d.refresh_frame()
        (w.cancel_jobs if global_cancel else d.cancel_refresh)()
        release.set(); until(app, lambda: not w.jobs)
        assert d.state == 'canceled' and d._pending is None and d.snapshot is None
        assert not d.cancel_button.isEnabled() and not w.test_checks
    finally:
        release.set()


def test_auto_debounces_latest_paused_intent_and_cancel_suppresses_retry(window, app):
    w = window; d = opened(w)
    d.auto_refresh_checkbox.setChecked(True)
    for frame in range(1, 5): w.seek_frame(frame)
    until(app, lambda: d.current() and not w.jobs)
    assert w.test_captures == [4]
    d.cancel_refresh(); pump(app, .5)
    assert w.test_captures == [4] and d.state == 'canceled'
    w.seek_frame(5)
    until(app, lambda: d.current() and not w.jobs)
    assert w.test_captures == [4, 5]


def test_playback_start_invalidates_even_after_immediate_pause(window, app, monkeypatch):
    w = window; d = loaded(w, app)
    generation, context = d._generation, d.context
    snapshot = d.snapshot
    # Real state signal: media playback is not required for this lifecycle test.
    w.player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
    w.player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PausedState)
    d._accept_result('verify', generation, context, snapshot, (True, None))
    assert d.snapshot is None and d.state == 'stale'


def test_explicit_full_preview_handoff_waits_for_cleanup_and_keeps_mode(window, app, monkeypatch):
    import lumen.ui_final_frame_preview as module
    w = window; entered = threading.Event(); release = threading.Event(); built = []
    def capture(project, frame, **kwargs):
        entered.set(); assert release.wait(8); return fake_snapshot(project, frame)
    monkeypatch.setattr(module, 'capture_final_frame_preview', capture)
    monkeypatch.setattr(w, 'build_preview', lambda: built.append((w.preview_mode, bool(w.jobs))))
    w.preview_mode = 'bypass'
    d = opened(w); d.refresh_frame()
    try:
        assert entered.wait(2)
        d.build_timeline_preview()
        assert not built and d._closed_flag
        release.set(); until(app, lambda: bool(built))
        assert built == [('bypass', False)]
    finally:
        release.set()


def test_error_is_local_and_explicitly_offers_full_preview(window, app, monkeypatch):
    import lumen.ui_final_frame_preview as module
    def capture(*args, **kwargs): raise ValueError('Active stabilization is unsupported.')
    monkeypatch.setattr(module, 'capture_final_frame_preview', capture)
    d = opened(window); d.refresh_frame()
    until(app, lambda: d.state == 'unsupported' and not window.jobs)
    assert 'Build full timeline preview' in d.status.text() and not window.test_warnings
    assert d.snapshot is None and d.timeline_button.isEnabled()


def test_periodic_check_detects_mutation_without_gui_filesystem_io(window, app):
    w = window; d = loaded(w, app)
    Path(w.project.media[0].path).write_bytes(b'changed while display is current')
    d._next_verify = 0
    d.timer.start()
    until(app, lambda: d.state == 'stale' and not w.jobs)
    assert d.snapshot is None and len(w.test_checks) == 2


@pytest.mark.parametrize('action', ['new', 'open', 'recover'])
def test_actual_project_replacement_discards_old_still_and_queued_callbacks(window, app, tmp_path, monkeypatch, action):
    from PySide6.QtWidgets import QFileDialog, QInputDialog
    from lumen.project_recovery import RecoveryStore
    import uuid
    w = window; d = loaded(w, app)
    generation, context, snapshot = d._generation, d.context, d.snapshot
    monkeypatch.setattr(w, 'confirm_discard', lambda: True)
    if action == 'new':
        monkeypatch.setattr(QInputDialog, 'getItem', lambda *args: ('1280 × 720 · 30 fps', True))
        w.new_project()
    elif action == 'open':
        path = tmp_path / 'replacement.lumen'
        Project(name='Explicit opened project').save(path)
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(path), ''))
        w.open_project()
    else:
        writer = RecoveryStore(tmp_path / 'recovery')
        try: entry = writer.write(Project(name='Recovered copy'), str(uuid.uuid4()))
        finally: writer.close()
        assert w.restore_recovery(entry)
    d.check_current()
    d._accept_result('verify', generation, context, snapshot, (True, None))
    d._accept_result('render', generation, context, None, (None, 'old project failure'))
    assert d.snapshot is None and d.canvas.image.isNull() and d.state == 'stale'
    assert not w.test_warnings


@pytest.mark.parametrize('mode', ['graded', 'bypass'])
def test_existing_valid_playback_pixels_scopes_reference_and_cache_are_untouched(window, app, tmp_path, mode):
    from test_color_reference_ui import activate_synthetic
    w = window; w.preview_mode = mode
    cached = activate_synthetic(w, tmp_path)
    w.preview_paths[(w.revision, mode)] = cached
    w.capture_reference()
    assert w.video.reference is not None and w.video.frame_provenance is not None
    image_key, provenance = w.video.image.cacheKey(), w.video.frame_provenance
    scopes, reference = w.scopes.data, w.video.reference
    paths, revision, request = dict(w.preview_paths), w.preview_revision, w.preview_request
    source = cached.read_bytes()
    d = loaded(w, app)
    assert w.preview_mode == w.loaded_preview_mode == mode
    assert w.video.image.cacheKey() == image_key and w.video.frame_provenance == provenance
    assert w.scopes.data is scopes and w.video.reference is reference
    assert w.preview_paths == paths and w.preview_revision == revision and w.preview_request == request
    assert cached.read_bytes() == source and d.current()


def test_close_window_cancels_registered_work_before_cleanup(window, app, monkeypatch):
    import lumen.ui_final_frame_preview as module
    w = window; entered = threading.Event(); release = threading.Event()
    def capture(project, frame, **kwargs):
        entered.set(); assert release.wait(8); return fake_snapshot(project, frame)
    monkeypatch.setattr(module, 'capture_final_frame_preview', capture)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    d = opened(w); d.refresh_frame()
    try:
        assert entered.wait(2)
        w.close()
        assert w.closing and not d.isVisible() and d.job.cancel.is_set()
        assert not d.timer.isActive() and d.snapshot is None
        release.set(); until(app, lambda: not w.jobs and not w.isVisible())
    finally:
        release.set()


def test_global_cancel_also_discards_hidden_full_preview_handoff(window, app, monkeypatch):
    import lumen.ui_final_frame_preview as module
    w = window; entered = threading.Event(); release = threading.Event(); built = []
    def capture(project, frame, **kwargs):
        entered.set(); assert release.wait(8); return fake_snapshot(project, frame)
    monkeypatch.setattr(module, 'capture_final_frame_preview', capture)
    monkeypatch.setattr(w, 'build_preview', lambda: built.append(True))
    d = opened(w); d.refresh_frame()
    try:
        assert entered.wait(2)
        d.build_timeline_preview(); w.cancel_jobs()
        release.set(); until(app, lambda: not w.jobs)
        pump(app)
        assert not built and d._handoff_context is None
    finally:
        release.set()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('width', [360, 760])
def test_narrow_geometry_locale_changes_preserve_current_snapshot(window, app, locale, width):
    from lumen.i18n import set_locale
    w = window; d = loaded(w, app)
    project = deepcopy(w.project); snapshot = d.snapshot
    set_locale(locale); d.setMaximumSize(16777215, 16777215); d.resize(width, 900); app.processEvents()
    assert d.width() == width
    assert ('已验证' if locale == 'zh-CN' else 'Verified') in d.status.text()
    for widget in (d.stage, d.status, d.canvas, d.metadata, d.profile, d.precision, d.freshness,
                   d.auto_refresh_checkbox, d.refresh_button, d.cancel_button,
                   d.close_button, d.timeline_button):
        if widget in (d.stage, d.canvas, d.refresh_button, d.cancel_button, d.close_button, d.timeline_button):
            assert widget.visibleRegion().boundingRect() == widget.rect()
        assert widget.mapTo(d, widget.rect().topLeft()).x() >= 0
        assert widget.mapTo(d, widget.rect().bottomRight()).x() < d.width()
        if hasattr(widget, 'heightForWidth') and widget.heightForWidth(widget.width()) >= 0:
            assert widget.height() >= widget.heightForWidth(widget.width())
    assert d.current() and d.snapshot is snapshot and w.project == project
    set_locale('en')


def test_target_guard_does_not_deep_serialize_unrelated_tail(window, monkeypatch):
    import lumen.ui_final_frame_preview as module
    w = window
    w.project.clips.extend(Clip(f'tail-{i}', 'm', 0, 300 + i*30, 0, 30) for i in range(3000))
    original = module._freeze_visual
    serialized_clips = []
    def guarded(value):
        if isinstance(value, Clip): serialized_clips.append(value.id)
        return original(value)
    monkeypatch.setattr(module, '_freeze_visual', guarded)
    token = module._target_visual_token(w.project, 5)
    assert serialized_clips == ['c']
    w.project.clips[0].grade.exposure = .5
    assert module._target_visual_token(w.project, 5) != token


def test_complete_project_detachment_occurs_only_in_workers(window, app, monkeypatch):
    import lumen.ui_final_frame_preview as module
    main = threading.get_ident(); calls = []
    def detach(value):
        calls.append(threading.get_ident())
        assert calls[-1] != main
        return deepcopy(value)
    monkeypatch.setattr(module, 'copy', SimpleNamespace(deepcopy=detach))
    d = loaded(window, app)
    assert d.current() and len(calls) == 2


def test_edit_during_worker_detachment_is_never_published(window, app, monkeypatch):
    import lumen.ui_final_frame_preview as module
    w = window; entered = threading.Event(); release = threading.Event()
    def detach(value):
        entered.set(); assert release.wait(8)
        return deepcopy(value)
    monkeypatch.setattr(module, 'copy', SimpleNamespace(deepcopy=detach))
    d = opened(w); d.refresh_frame()
    try:
        assert entered.wait(2)
        w.edit(lambda: w.editor.update_clip('c', gain_db=-6))
        release.set(); until(app, lambda: not w.jobs)
        assert d.snapshot is None and d.state == 'stale' and not w.test_checks
    finally:
        release.set()


def test_closed_dialog_disconnects_playback_signal(window, app):
    d = loaded(window, app)
    d.reject(); generation = d._play_generation
    window.player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
    assert d._bound_player is None and d._play_generation == generation


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('reason', ['stabilization', 'font', 'indirect'])
def test_known_unsupported_reason_and_output_profile_are_bilingual(window, app, monkeypatch, locale, reason):
    import lumen.ui_final_frame_preview as module
    from lumen.i18n import set_locale
    set_locale(locale)
    def capture(*args, **kwargs):
        detail = {
            'stabilization': 'Final-frame preview does not support active stabilization.',
            'font': 'Cannot prepare final-frame preview: Final-frame font exceeds 64 MiB; use Build preview.',
            'indirect': 'Final-frame preview requires verified self-contained sources; manifests and external-reference formats are unsupported.',
        }[reason]
        raise ValueError(detail + ' ' + module.FALLBACK)
    monkeypatch.setattr(module, 'capture_final_frame_preview', capture)
    d = opened(window); d.refresh_frame()
    until(app, lambda: d.state == 'unsupported' and not window.jobs)
    expected = {
        'stabilization': ('防抖', 'active stabilization'),
        'font': ('字体超过 64 MiB', 'font exceeds 64 MiB'),
        'indirect': ('自包含源文件', 'verified self-contained sources'),
    }[reason][locale == 'en']
    assert expected in d.status.text()
    assert d.status.text().count(module.FALLBACK) == 0
    assert 'H.264' in d.profile.text() and 'ProRes' in d.profile.text() and 'HDR' in d.profile.text()
    set_locale('en')


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_narrow_main_viewer_preserves_all_five_playback_actions(window, app, locale):
    from lumen.i18n import set_locale
    from PySide6.QtWidgets import QStyle, QStyleOptionButton
    set_locale(locale)
    w = window; w.resize(1000, 700); app.processEvents()
    viewer = w.preview_button.parentWidget()
    def find_row(layout):
        if layout.indexOf(w.preview_button) >= 0: return layout
        for index in range(layout.count()):
            child = layout.itemAt(index).layout()
            if child is not None:
                result = find_row(child)
                if result is not None: return result
    row = find_row(viewer.layout())
    controls = [row.itemAt(i).widget() for i in range(row.count()) if row.itemAt(i).widget()]
    assert len(controls) == 5 and controls[0] is w.play_button
    assert controls[3] is w.preview_button and controls[4] is w.final_frame_preview_button
    for button in controls:
        option = QStyleOptionButton(); option.initFrom(button); option.text = button.text()
        contents = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
        assert button.fontMetrics().horizontalAdvance(button.text()) <= contents.width()
        assert button.visibleRegion().boundingRect() == button.rect()
        assert viewer.rect().contains(button.geometry())
    assert w.final_frame_preview_action.isEnabled()
    set_locale('en')

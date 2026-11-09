"""Qt lifecycle and worker ownership; numerical A/V equivalence is separate."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QObject, QSettings, Qt, Signal, QRect
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from lumen.final_frame_preview import project_signature
from lumen.i18n import set_locale
from lumen.model import Editor, Media, Project, Clip
from lumen.ui import MainWindow
from lumen.ui_range_preview import RangePreviewDialog, open_range_preview


class Player(QObject):
    PlaybackState = QMediaPlayer.PlaybackState
    MediaStatus = QMediaPlayer.MediaStatus
    playbackStateChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal()
    positionChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.plays = 0
        self.pauses = 0
        self.stops = 0
        self.position = 0
        self.source = None
        self.status = self.MediaStatus.LoadedMedia

    def setAudioOutput(self, audio): self.audio = audio
    def setVideoSink(self, sink): self.sink = sink
    def setSource(self, source): self.source = source
    def play(self): self.plays += 1
    def pause(self): self.pauses += 1
    def stop(self): self.stops += 1
    def setPosition(self, position):
        self.position = position
        self.positionChanged.emit(position)
    def mediaStatus(self): return self.status
    def errorString(self): return 'Synthetic codec unavailable'


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def until(app, condition, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(.003)
    assert condition()


def pump(app, seconds=.08):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.003)


def dependencies(project):
    return tuple((media.path, hashlib.sha256(Path(media.path).read_bytes()).hexdigest()) for media in project.media)


def snapshot(project, start, end, owner):
    result = SimpleNamespace(path=Path(owner.test_path) / f'preview-{len(owner.test_snapshots)}.mp4',
        identity=(project_signature(project), start, end, dependencies(project)), closed=False)
    result.path.write_bytes(b'session fixture')
    def close():
        assert threading.get_ident() != owner.test_main_thread
        assert not result.closed, 'Each cache must be released exactly once'
        result.closed = True
        owner.test_closed.append(result)
        result.path.unlink()
    result.close = close
    owner.test_snapshots.append(result)
    return result


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    import lumen.ui_range_preview as module
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    monkeypatch.setattr(module, 'QMediaPlayer', Player)
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'UI identity fixture, not decoded')
    w = MainWindow(workspace_settings=QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat),
                   recovery_root=tmp_path / 'recovery', ui_language='en')
    w.editor = Editor(Project(width=320, height=180,
        media=[Media('m', str(source), 'fixture', 20, 320, 180, True, False)],
        clips=[Clip('c', 'm', 0, 0, 0, 600)]))
    w.refresh(); w.select_clip('c'); w.show(); app.processEvents()
    w.test_captures, w.test_checks, w.test_warnings = [], [], warnings
    w.test_closed, w.test_snapshots = [], []
    w.test_main_thread, w.test_path = threading.get_ident(), tmp_path
    def capture(project, start, end, **kwargs):
        assert threading.get_ident() != w.test_main_thread
        w.test_captures.append((start, end))
        return snapshot(project, start, end, w)
    def verify(candidate, project, **kwargs):
        assert threading.get_ident() != w.test_main_thread
        w.test_checks.append(candidate)
        return candidate.identity == (project_signature(project), candidate.identity[1], candidate.identity[2], dependencies(project))
    monkeypatch.setattr(module, 'capture_range_preview', capture)
    monkeypatch.setattr(module, 'verify_range_preview', verify)
    yield w
    for d in w.findChildren(RangePreviewDialog):
        d.reject()
    until(app, lambda: not w.jobs)
    pump(app)
    until(app, lambda: all(item.closed for item in w.test_snapshots) and not w.jobs)
    w.dirty = False; w.closing = True; w.close(); w.deleteLater(); app.processEvents()


def opened(w):
    d = open_range_preview(w)
    d.timer.stop()
    return d


def loaded(w, app):
    d = opened(w)
    d.build_range()
    until(app, lambda: d.current() and not w.jobs)
    return d


def test_open_explicit_build_verify_not_autoplay_and_no_model_or_cache_mutation(window, app):
    w = window
    before = deepcopy(w.project)
    revision = w.revision
    cache = w.preview_paths.copy()
    original_player = w.player
    d = opened(w)
    assert open_range_preview(w) is d and not d.isModal()
    pump(app)
    assert not w.jobs and not w.test_captures and d.snapshot is None
    assert d.radius.value() == 2 and not d.loop_checkbox.isChecked()
    QTest.mouseClick(d.build_button, Qt.MouseButton.LeftButton)
    until(app, lambda: d.current() and not w.jobs)
    assert w.test_captures == [(0, 60)] and len(w.test_checks) == 1
    assert d.position_label.text() == 'Range position · timeline frame 0'
    assert d.player is None and not d._play_intent and d.state == 'ready'
    assert w.project == before and w.revision == revision and not w.editor.can_undo
    assert w.preview_paths == cache and w.player is original_player
    assert not list(w.cache.iterdir()) and w.video.image.isNull() and w.scopes.data is None


@pytest.mark.parametrize('frame,radius,expected', [(0, 2, (0, 60)), (300, 2, (240, 360)),
    (599, 2, (539, 600)), (300, 5, (150, 450)), (300, .5, (285, 315))])
def test_bounds_clamped_and_configured_without_model_edits(window, frame, radius, expected):
    d = opened(window)
    window.timeline.playhead = frame
    d.radius.setValue(radius)
    assert d._bounds() == expected
    assert not window.editor.can_undo


def test_fractional_fps_range_never_exceeds_ten_seconds(window):
    window.project.fps_num, window.project.fps_den = 30000, 1001
    window.timeline.playhead = 300
    d = opened(window); d.radius.setValue(5)
    start, end = d._bounds()
    assert (end - start) / window.project.fps <= 10
    assert (start, end) == (151, 449)


@pytest.mark.parametrize('change', ['revision', 'frame', 'grade', 'audio_gain', 'project', 'radius'])
def test_changed_context_stales_and_releases_session_only(window, app, change):
    w = window; d = loaded(w, app); item = d.snapshot
    if change == 'revision': w.revision += 1
    elif change == 'frame': w.seek_frame(8)
    elif change == 'grade': w.project.clips[0].grade.exposure = .5
    elif change == 'audio_gain': w.project.clips[0].gain_db = -3
    elif change == 'project': w.editor = Editor(deepcopy(w.project))
    else: d.radius.setValue(3)
    d.check_current()
    assert d.state == 'stale' and not d.current() and d.snapshot is None
    until(app, lambda: item.closed and not w.jobs)
    assert len(w.test_captures) == 1


def test_selection_changes_do_not_invalidate_session(window, app):
    w = window; d = loaded(w, app)
    w.selection = ''; w.active_selection_type = 'caption'
    d.check_current()
    assert d.current() and d.state == 'ready'


def test_play_pause_resume_requires_checks_and_loop_rechecks_without_main_position_change(window, app):
    w = window; w.seek_frame(300); d = loaded(w, app); frame = w.timeline.playhead
    d.toggle_play()
    until(app, lambda: d.player is not None and not w.jobs)
    player = d.player
    assert player.plays == 1 and len(w.test_checks) == 2
    assert w.timeline.playhead == frame and w.player is not player
    d.toggle_play()
    assert player.pauses == 1 and not d._play_intent
    d.toggle_play(); until(app, lambda: not w.jobs)
    assert player.plays == 2 and len(w.test_checks) == 3
    d.loop_checkbox.setChecked(True)
    player.position = 2500
    player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
    until(app, lambda: not w.jobs)
    assert player.position == 0 and player.plays == 3 and len(w.test_checks) == 4
    assert w.timeline.playhead == frame


def test_nonloop_end_requires_new_play_and_codec_failure_does_not_mutate_model(window, app):
    w = window; d = loaded(w, app); before = deepcopy(w.project)
    d.toggle_play(); until(app, lambda: d.player is not None and not w.jobs)
    player = d.player
    player.status = QMediaPlayer.MediaStatus.EndOfMedia
    player.mediaStatusChanged.emit(player.status)
    assert not d._play_intent and d.state == 'ready' and player.plays == 1
    d.toggle_play(); until(app, lambda: not w.jobs)
    assert player.plays == 2 and player.position == 0
    player.errorOccurred.emit()
    assert d.player is None and 'codec unavailable' in d.status.text()
    assert d.current() and w.project == before and not w.test_warnings


def test_external_source_change_checked_off_gui_stops_playback(window, app):
    w = window; d = loaded(w, app)
    d.toggle_play(); until(app, lambda: d.player is not None and not w.jobs)
    old = d.snapshot; player = d.player
    Path(w.project.media[0].path).write_bytes(b'changed source bytes')
    d._next_verify = 0; d.check_current()
    until(app, lambda: d.snapshot is None and old.closed and not w.jobs)
    assert d.player is None and player.stops == 1
    assert 'changed' in d.status.text() and d.state == 'stale'


def test_play_reverification_failure_never_starts_player(window, app):
    w = window; d = loaded(w, app)
    Path(w.project.media[0].path).write_bytes(b'changed before play')
    d.toggle_play(); until(app, lambda: not w.jobs)
    assert d.player is None and not d._play_intent and d.snapshot is None


def test_pause_while_play_check_running_cannot_lateplay(window, app, monkeypatch):
    import lumen.ui_range_preview as module
    w = window; d = loaded(w, app)
    entered, release = threading.Event(), threading.Event()
    def verify(*args, **kwargs):
        entered.set(); assert release.wait(8); return True
    monkeypatch.setattr(module, 'verify_range_preview', verify)
    try:
        d.toggle_play(); assert entered.wait(2)
        d.toggle_play()
        release.set(); until(app, lambda: not w.jobs)
        assert d.player is None and not d._play_intent and d.state == 'paused'
    finally:
        release.set()


def test_one_running_latest_build_and_late_success_error_are_ignored_and_closed(window, app, monkeypatch):
    import lumen.ui_range_preview as module
    w = window; entered, release = threading.Event(), threading.Event(); calls = []
    def capture(project, start, end, **kwargs):
        calls.append((start, end))
        result = snapshot(project, start, end, w)
        if len(calls) == 1:
            entered.set(); assert release.wait(8)
        return result
    monkeypatch.setattr(module, 'capture_range_preview', capture)
    d = opened(w); d.build_range()
    try:
        assert entered.wait(2)
        generation, context = d._generation, d.context
        for frame in range(100, 109):
            w.seek_frame(frame); d.build_range()
        assert len(w.jobs) == 1 and calls == [(0, 60)]
        release.set(); until(app, lambda: d.current() and not w.jobs)
        assert calls == [(0, 60), (48, 168)]
        assert w.test_snapshots[0].closed and not d.snapshot.closed
        shown = d.snapshot
        d._accept_result('render', generation, context, None, None, (w.test_snapshots[0], None))
        d._accept_result('render', generation, context, None, None, (None, 'obsolete failure'))
        assert d.snapshot is shown and d.state == 'ready' and not w.test_warnings
    finally:
        release.set()


@pytest.mark.parametrize('action', ['cancel', 'close', 'project', 'revision'])
def test_cancel_close_or_changed_project_mid_capture_reaps_dropped_result(window, app, monkeypatch, action):
    import lumen.ui_range_preview as module
    w = window; entered, release = threading.Event(), threading.Event()
    def capture(project, start, end, **kwargs):
        entered.set(); assert release.wait(8)
        return snapshot(project, start, end, w)
    monkeypatch.setattr(module, 'capture_range_preview', capture)
    d = opened(w); d.build_range()
    try:
        assert entered.wait(2)
        if action == 'cancel': d.cancel_work()
        elif action == 'close': d.reject()
        elif action == 'project': w.editor = Editor(deepcopy(w.project)); d.check_current()
        else: w.revision += 1; d.check_current()
        release.set()
        until(app, lambda: bool(w.test_snapshots) and all(s.closed for s in w.test_snapshots) and not w.jobs)
        assert d.snapshot is None and d.player is None and not w.test_warnings
    finally:
        release.set()


def test_cancel_then_reopen_while_old_worker_running_keeps_one_worker(window, app, monkeypatch):
    import lumen.ui_range_preview as module
    w = window; entered, release = threading.Event(), threading.Event(); calls = []
    def capture(project, start, end, **kwargs):
        calls.append((start, end))
        if len(calls) == 1:
            entered.set(); assert release.wait(8)
        return snapshot(project, start, end, w)
    monkeypatch.setattr(module, 'capture_range_preview', capture)
    old = opened(w); old.build_range()
    try:
        assert entered.wait(2)
        old.reject()
        newer = opened(w); w.seek_frame(150); newer.build_range()
        assert len(w.jobs) == 1
        release.set()
        try:
            until(app, lambda: newer.current() and not w.jobs)
        except AssertionError:
            pytest.fail(str({'calls': calls, 'state': newer.state, 'pending': newer._pending,
                'current_context': newer.context_current(), 'visible': newer.isVisible(),
                'snapshot': newer.snapshot, 'jobs': [(job.cancel.is_set(), job.isRunning()) for job in w.jobs],
                'new_job': newer.job, 'old_job': old.job, 'status': newer.status.text(),
                'owned': len(newer._owned.items), 'old_owned': len(old._owned.items)}))
        assert calls == [(0, 60), (90, 210)]
        assert w.test_snapshots[0].closed and newer.snapshot is w.test_snapshots[1]
    finally:
        release.set()


def test_full_preview_handoff_waits_cleanup_and_changed_context_blocks_it(window, app, monkeypatch):
    w = window; d = loaded(w, app); called = []
    monkeypatch.setattr(w, 'build_preview', lambda: called.append(True))
    item = d.snapshot
    d.build_timeline_preview()
    until(app, lambda: item.closed and not w.jobs)
    pump(app)
    assert called == [True] and d._closed_flag


def test_full_preview_handoff_changed_project_is_not_started(window, app, monkeypatch):
    w = window; d = loaded(w, app); called = []
    monkeypatch.setattr(w, 'build_preview', lambda: called.append(True))
    item = d.snapshot
    d.build_timeline_preview(); w.revision += 1
    until(app, lambda: item.closed and not w.jobs)
    pump(app)
    assert not called


def test_no_queued_player_callback_from_retired_range_changes_new_state(window, app):
    w = window; d = loaded(w, app)
    d.toggle_play(); until(app, lambda: d.player is not None and not w.jobs)
    old = d.player
    d.build_range(); until(app, lambda: d.current() and not w.jobs)
    status = d.status.text()
    # Qt queued emissions carry an old sender, protected by player identity.
    try:
        old.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        old.errorOccurred.emit()
    except RuntimeError:  # deleteLater already reaped the obsolete QObject.
        pass
    assert d.player is None and d.state == 'ready' and d.status.text() == status


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('size', [(480, 400), (900, 700)])
def test_localized_scroll_layout_keeps_actions_reachable(window, app, locale, size):
    set_locale(locale)
    d = opened(window)
    d.fit_available_screen(QRect(0, 0, *size))
    app.processEvents()
    assert d.width() <= size[0] - 32 and d.height() <= size[1] - 64
    assert not d.scroll.horizontalScrollBar().isVisible()
    for button in (d.build_button, d.play_button, d.cancel_button, d.close_button, d.timeline_button):
        assert d.rect().contains(button.geometry())
        assert button.isVisible() and button.accessibleName()
    if locale == 'zh-CN':
        assert d.windowTitle() == '预览播放头附近'
        assert '构建' in d.build_button.text() and '10 秒' in d.limitations.text()


@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_main_window_entry_in_both_modes_and_cancel_integration(window, app, mode):
    w = window
    w.set_workspace_mode(mode, persist=False)
    app.processEvents()
    assert w.range_preview_button.isVisible() and w.range_preview_action.isEnabled()
    w.range_preview_action.trigger()
    d = w.range_preview_dialog
    d.timer.stop()
    assert d.isVisible() and w.open_range_preview() is d
    d.build_range(); until(app, lambda: d.current() and not w.jobs)
    snapshot = d.snapshot
    w.cancel_jobs(); until(app, lambda: snapshot.closed and not w.jobs)
    assert d.snapshot is None and d.player is None


def test_local_playback_position_maps_to_global_frame_only(window, app):
    w = window; w.seek_frame(300); d = loaded(w, app)
    d.toggle_play(); until(app, lambda: d.player is not None and not w.jobs)
    d.player.positionChanged.emit(1500)
    assert d.position_label.text() == 'Range position · timeline frame 285'
    assert w.timeline.playhead == 300
    d.player.positionChanged.emit(9000)
    assert d.position_label.text() == 'Range position · timeline frame 359'


@pytest.mark.parametrize('kind', ['sound', 'tts', 'main'])
def test_other_playback_stops_range_without_late_restart(window, app, kind):
    w = window; d = loaded(w, app)
    d.toggle_play(); until(app, lambda: d.player is not None and not w.jobs)
    if kind == 'sound': w.sound_audition.changed.emit('new-sound')
    elif kind == 'tts': w.tts_preview_player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
    else: w.player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
    assert d.player is None and not d._play_intent and d.snapshot is None
    until(app, lambda: not w.jobs)


def test_backend_reason_localized_but_arbitrary_path_detail_preserved(window):
    set_locale('zh-CN')
    d = opened(window)
    assert '视频转场' in d._error_text('Range preview does not support an active video transition. Use Build full timeline preview for this project/range.')
    detail = '/tmp/Do not translate this.mp4'
    assert detail in d._error_text(detail)


def test_window_close_registers_and_waits_for_worker_cache_cleanup(window, app):
    w = window; d = loaded(w, app); item = d.snapshot
    entered, release = threading.Event(), threading.Event()
    original_close = item.close
    def close():
        entered.set(); assert release.wait(8)
        original_close()
    item.close = close
    try:
        w.dirty = False
        w.close()
        assert entered.wait(2)
        assert w.closing and w.jobs and w.isVisible()
        release.set()
        until(app, lambda: item.closed and not w.jobs and not w.isVisible())
    finally:
        release.set()


def test_range_cleanup_unregisters_exactly_once_after_cancel_then_close(window, app):
    w = window; d = loaded(w, app); item = d.snapshot
    d.cancel_work(); d.reject()
    until(app, lambda: item.closed and not w.jobs)
    pump(app)
    assert w.test_closed.count(item) == 1


def test_finished_job_receipt_queued_before_new_dialog_connects_cannot_lose_wakeup(window, app):
    w = window
    old = w.start_job('completed fixture', lambda cancel, progress: None, lambda _: None, 'range_preview')
    assert old.wait(2000) and old.isFinished() and old in w.jobs
    d = opened(w); d.build_range()
    until(app, lambda: d.current() and not w.jobs)
    assert w.test_captures == [(0, 60)]


def test_transient_native_handle_release_failure_retried_off_gui(window, app):
    w = window; d = loaded(w, app); item = d.snapshot
    original = item.close; calls = []
    def close():
        assert threading.get_ident() != w.test_main_thread
        calls.append(True)
        if len(calls) == 1:
            raise PermissionError('simulated player handle release lag')
        original()
    item.close = close
    d.cancel_work()
    until(app, lambda: item.closed and not w.jobs)
    assert len(calls) == 2 and not d._owned.cleanup_errors()


def test_permanent_cleanup_failure_keeps_ownership_and_surfaces_without_retry_loop(window, app):
    w = window; d = loaded(w, app); item = d.snapshot
    original = item.close; calls = []
    def close():
        assert threading.get_ident() != w.test_main_thread
        calls.append(True)
        raise ValueError('simulated permanent cache cleanup failure')
    item.close = close
    d.cancel_work()
    until(app, lambda: d.state == 'cleanup_failed' and not w.jobs)
    pump(app)
    assert calls == [True] and item in d._owned.items
    assert str(item.path) in d.status.text() and 'permanent cache cleanup failure' in w.statusBar().currentMessage()
    assert not d.build_button.isEnabled()
    d.reject(); newer = opened(w)
    assert not newer.build_button.isEnabled()
    newer.build_range(); pump(app)
    assert len(w.test_captures) == 1
    # Release the deliberately failing fixture explicitly on a worker; the
    # production failure remains owned rather than silently losing its path.
    item.close = original
    thread = threading.Thread(target=original)
    thread.start(); thread.join(2)
    assert not thread.is_alive() and item.closed


def test_unsupported_build_is_localized_nonmodal_with_explicit_full_preview_fallback(window, app, monkeypatch):
    import lumen.ui_range_preview as module
    w = window; set_locale('zh-CN'); d = opened(w)
    def capture(*args, **kwargs):
        raise ValueError('Range preview does not support an active video transition. Use Build full timeline preview for this project/range.')
    monkeypatch.setattr(module, 'capture_range_preview', capture)
    d.build_range()
    until(app, lambda: d.state == 'unsupported' and not w.jobs)
    assert '视频转场' in d.status.text() and '构建完整时间线预览' in d.status.text()
    assert d.snapshot is None and d.player is None and d.timeline_button.isEnabled()
    assert not w.test_warnings


def test_real_muxed_range_decodes_video_and_reaches_end_in_separate_player(window, app, monkeypatch, tmp_path):
    """Linux/offscreen codec smoke, not audible-device/native-package proof."""
    import shutil
    import subprocess
    import lumen.ui_range_preview as module
    from lumen.media import ffmpeg_binary, probe
    from lumen.range_preview import capture_range_preview, verify_range_preview
    if shutil.which(ffmpeg_binary()) is None:
        pytest.skip('External FFmpeg unavailable for the actual encoded-range fixture')
    availability = QMediaPlayer()
    available = availability.isAvailable()
    availability.deleteLater()
    if not available:
        pytest.skip('Qt media playback backend unavailable in this execution environment')
    path = tmp_path / 'actual-av.mkv'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-threads', '1',
        '-f', 'lavfi', '-i', 'testsrc2=size=64x48:rate=30:duration=2',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2',
        '-c:v', 'ffv1', '-threads', '1', '-c:a', 'pcm_s16le', '-ar', '48000', '-ac', '2',
        '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', str(path)],
        check=True, capture_output=True, timeout=20)
    media = probe(path)
    w = window
    w.editor = Editor(Project(width=64, height=48, media=[media], clips=[Clip('real', media.id, 0, 0, 0, 60)]))
    w.refresh(); w.seek_frame(30)
    monkeypatch.setattr(module, 'QMediaPlayer', QMediaPlayer)
    monkeypatch.setattr(module, 'capture_range_preview', capture_range_preview)
    monkeypatch.setattr(module, 'verify_range_preview', verify_range_preview)
    d = opened(w); d.radius.setValue(.5); d.build_range()
    until(app, lambda: d.current() and not w.jobs, timeout=40)
    assert d.snapshot.work.composite_frames == 30 and d.snapshot.work.mixed_samples == 48000
    main_player = w.player
    d.toggle_play()
    until(app, lambda: d.player is not None and not w.jobs, timeout=10)
    player = d.player
    states = []
    player.mediaStatusChanged.connect(states.append)
    until(app, lambda: not d.canvas.image.isNull(), timeout=10)
    assert d.canvas.image.width() == 64 and d.canvas.image.height() == 48
    until(app, lambda: QMediaPlayer.MediaStatus.EndOfMedia in states, timeout=10)
    assert 950 <= player.duration() <= 1100
    assert d.state == 'ready' and not d._play_intent
    assert w.player is main_player and w.timeline.playhead == 30
    assert w.video.image.isNull() and w.scopes.data is None


def test_paused_range_does_not_repeatedly_hash_dependencies_until_play(window, app):
    w = window; d = loaded(w, app); checks = len(w.test_checks)
    d._next_verify = 0; d.check_current(); pump(app)
    assert len(w.test_checks) == checks and not w.jobs
    d.toggle_play(); until(app, lambda: d.player is not None and not w.jobs)
    assert len(w.test_checks) == checks + 1


def test_live_locale_switch_updates_action_accessible_names_without_session_edits(window, app):
    w = window; d = opened(w)
    d.radius.setValue(3); d.loop_checkbox.setChecked(True)
    before = deepcopy(w.project)
    controls = (d.build_button, d.play_button, d.cancel_button, d.close_button, d.timeline_button)
    for locale in ('zh-CN', 'en'):
        set_locale(locale); app.processEvents()
        assert all(button.accessibleName() == button.text() for button in controls)
        assert d.radius.value() == 3 and d.loop_checkbox.isChecked()
        assert w.project == before and not w.editor.can_undo
    assert not w.jobs and not w.test_captures


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_main_range_header_preserves_scopes_and_comparison_text_at_minimum_window(window, app, tmp_path, locale):
    from PySide6.QtWidgets import QStyle, QStyleOptionComboBox
    from test_color_reference_ui import activate_synthetic, control_geometry
    w = window
    set_locale(locale)
    w.set_workspace_mode('professional', persist=False)
    activate_synthetic(w, tmp_path)
    w.resize(1000, 700)
    w.side_tabs.setFixedWidth(320)
    w.main_split.setSizes([210, 450, 320])
    pump(app, .2)
    assert (w.width(), w.height()) == (1000, 700)
    assert w.range_preview_button.geometry().top() == w.preview_mode_choice.geometry().top()
    assert control_geometry(w.range_preview_button)['text_fits']
    assert w.range_preview_button.visibleRegion().boundingRect() == w.range_preview_button.rect()
    assert w.video.visibleRegion().boundingRect() == w.video.rect()
    assert all(child.visibleRegion().boundingRect() == child.rect()
               for child in (w.scopes.source_label, w.scopes.mode_choice, w.scopes.canvas))
    assert w.scopes.canvas.height() >= 44
    combo = w.preview_mode_choice
    option = QStyleOptionComboBox(); combo.initStyleOption(option)
    contents = combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                          QStyle.SubControl.SC_ComboBoxEditField, combo)
    assert max(combo.fontMetrics().horizontalAdvance(combo.itemText(i))
               for i in range(combo.count())) <= contents.width()
    w.set_workspace_mode('simple', persist=False); pump(app)
    assert w.range_preview_button.isVisible() and control_geometry(w.range_preview_button)['text_fits']

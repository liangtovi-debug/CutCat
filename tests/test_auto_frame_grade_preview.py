"""Independent automatic-paused-preview queue and real RGB16 regressions.

Lifecycle tests use tiny immutable fake snapshots, but real Qt workers/timers.
The final acceptance test decodes actual local FFmpeg media and checks both an
independent scalar reference and the renderer's lossless graded intermediate.
"""
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
import os
import threading
import time

import numpy as np
import pytest
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QCheckBox

from lumen.model import Editor, Grade
from lumen.ui_frame_grade_preview import FrameGradePreviewDialog, _display_pixels
from test_ui import app, window, populate


def pump(app, seconds=.45):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()


def until(app, condition, seconds=5):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert condition()


def file_identity(path):
    stat = Path(path).stat()
    return (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def fake_snapshot(project, clip_id, frame):
    """Freeze request identity independently from the dialog's change token."""
    clip = deepcopy(next(c for c in project.clips if c.id == clip_id))
    media = deepcopy(next(m for m in project.media if m.id == clip.media_id))
    source_identity = file_identity(media.path)
    lut_path = clip.grade.lut_path if clip.grade.enabled and clip.grade.lut_strength > 0 else ''
    lut_identity = file_identity(lut_path) if lut_path else None
    shape = (project.width, project.height, project.fps_num, project.fps_den)
    def current(live_project, live_clip_id, live_frame):
        try:
            live_clip = next(c for c in live_project.clips if c.id == live_clip_id)
            live_media = next(m for m in live_project.media if m.id == live_clip.media_id)
            return (live_clip_id == clip_id and live_frame == frame
                    and live_clip == clip and live_media == media
                    and shape == (live_project.width, live_project.height,
                                  live_project.fps_num, live_project.fps_den)
                    and source_identity == file_identity(live_media.path)
                    and (not lut_path or lut_identity == file_identity(lut_path)))
        except (StopIteration, OSError):
            return False
    pixels = np.full((2, 3, 3), .25 + clip.grade.exposure * .01, np.float32)
    return SimpleNamespace(frame=pixels, source_frame=frame,
                           source_time=Fraction(frame, 30), is_current=current,
                           captured_clip=clip, captured_frame=frame)


def result(snapshot):
    return snapshot, _display_pixels(snapshot.frame, threading.Event()), None


@pytest.fixture
def preview(window, app, tmp_path, monkeypatch):
    import lumen.ui_frame_grade_preview as module
    w = window
    populate(w, tmp_path)
    captures = []
    def capture(project, clip_id, frame, **kwargs):
        snapshot = fake_snapshot(project, clip_id, frame)
        captures.append(snapshot)
        return snapshot
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    dialog = FrameGradePreviewDialog(w)
    w.frame_grade_preview_dialog = dialog
    dialog.show()
    # Poll explicitly except when a test specifically exercises the poll timer.
    # The actual 300-ms single-shot timer and worker/result signals remain live.
    dialog.timer.stop()
    app.processEvents()
    try:
        yield w, dialog, captures
    finally:
        dialog.reject()
        w.cancel_jobs()
        for job in list(w.jobs):
            assert job.wait(5000)
        app.processEvents()


def enable_and_wait(app, w, dialog):
    dialog.auto_refresh_checkbox.setChecked(True)
    until(app, lambda: dialog.current() and not w.jobs)


def test_auto_is_opt_in_and_uses_single_shot_300ms_timer(preview, app):
    w, d, captures = preview
    assert isinstance(d.auto_refresh_checkbox, QCheckBox)
    assert not d.auto_refresh_checkbox.isChecked()
    assert d.auto_timer.isSingleShot() and d.auto_timer.interval() == 300
    assert not d.auto_timer.isActive()
    d.check_current()
    w.selected_clip().grade.exposure = .6
    d.check_current()
    pump(app)
    assert captures == [] and not w.jobs
    enable_and_wait(app, w, d)
    assert len(captures) == 1 and captures[0].captured_clip.grade.exposure == .6


def test_rapid_changes_restart_debounce_and_capture_only_latest(preview, app):
    w, d, captures = preview
    d.auto_refresh_checkbox.setChecked(True)
    for frame in range(1, 6):
        w.selected_clip().grade.exposure = frame / 10
        w.timeline.playhead = frame
        d.check_current()
        pump(app, .045)
        assert captures == [] and not w.jobs
    assert d.cancel_button.isEnabled()
    until(app, lambda: d.current() and not w.jobs)
    assert len(captures) == 1
    assert captures[0].captured_frame == 5
    assert captures[0].captured_clip.grade.exposure == .5
    for _ in range(3):
        d.check_current()
        pump(app, .12)
    assert len(captures) == 1, 'An unchanged successful snapshot must not loop'


def test_poll_timer_detects_grade_edit_without_manual_refresh(preview, app):
    w, d, captures = preview
    enable_and_wait(app, w, d)
    d.timer.start()
    w.selected_clip().grade.exposure = .9
    until(app, lambda: len(captures) == 2 and d.current() and not w.jobs)
    assert captures[-1].captured_clip.grade.exposure == .9
    assert d.snapshot is captures[-1]


def test_automatic_mode_never_interrupts_playback_and_resumes_after_pause(preview, app, monkeypatch):
    w, d, captures = preview
    playback = [QMediaPlayer.PlaybackState.PlayingState]
    pauses = []
    monkeypatch.setattr(w.player, 'playbackState', lambda: playback[0])
    monkeypatch.setattr(w.player, 'pause', lambda: pauses.append(1))
    d.auto_refresh_checkbox.setChecked(True)
    for frame in (1, 2, 3):
        w.timeline.playhead = frame
        d.check_current()
    pump(app)
    assert captures == [] and pauses == [] and not d.auto_timer.isActive()
    playback[0] = QMediaPlayer.PlaybackState.PausedState
    d.check_current()
    until(app, lambda: d.current() and not w.jobs)
    assert len(captures) == 1 and captures[-1].captured_frame == 3


def test_playback_start_cancels_debounce_and_clears_display(preview, app, monkeypatch):
    w, d, captures = preview
    enable_and_wait(app, w, d)
    playback = [QMediaPlayer.PlaybackState.PausedState]
    monkeypatch.setattr(w.player, 'playbackState', lambda: playback[0])
    w.timeline.playhead += 1
    d.check_current()
    assert d.auto_timer.isActive()
    playback[0] = QMediaPlayer.PlaybackState.PlayingState
    d.check_current()
    pump(app)
    assert len(captures) == 1 and not d.auto_timer.isActive()
    assert d.snapshot is None and d.canvas.image.isNull()


def test_running_capture_keeps_only_latest_request_and_rejects_late_callbacks(preview, app, monkeypatch):
    import lumen.ui_frame_grade_preview as module
    w, d, _ = preview
    entered, release = threading.Event(), threading.Event()
    captures = []
    def capture(project, clip_id, frame, **kwargs):
        snapshot = fake_snapshot(project, clip_id, frame)
        captures.append(snapshot)
        if len(captures) == 1:
            entered.set()
            assert release.wait(5)
        return snapshot
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    d.auto_refresh_checkbox.setChecked(True)
    try:
        until(app, entered.is_set)
        old_generation, old_context = d._generation, d.context
        for frame in (1, 2, 3):
            w.timeline.playhead = frame
            w.selected_clip().grade.exposure = frame / 5
            d.check_current()
            # Allow each debounce to expire while the original worker is stuck.
            pump(app, .36)
            assert len(w.jobs) == 1 and len(captures) == 1
        assert d._pending is not None
        d._accept_result(old_generation, old_context, result(captures[0]))
        assert d.snapshot is None and d.canvas.image.isNull()
        release.set()
        until(app, lambda: d.current() and not w.jobs)
        assert [s.captured_frame for s in captures] == [0, 3]
        assert captures[-1].captured_clip.grade.exposure == .6
        accepted = d.snapshot
        d._accept_result(old_generation, old_context, (None, None, 'stale decode failure'))
        assert d.snapshot is accepted and d.state == 'current'
    finally:
        release.set()


@pytest.mark.parametrize('global_cancel', [False, True])
def test_cancel_pending_auto_refresh_stays_canceled_until_new_edit(preview, app, global_cancel):
    w, d, captures = preview
    d.auto_refresh_checkbox.setChecked(True)
    assert d.auto_timer.isActive()
    (w.cancel_jobs if global_cancel else d.cancel_refresh)()
    for _ in range(4):
        d.check_current()
        pump(app, .12)
    assert captures == [] and not d.auto_timer.isActive() and not w.jobs
    assert d.state == 'canceled' and not d.cancel_button.isEnabled()
    w.selected_clip().grade.exposure = .7
    d.check_current()
    until(app, lambda: d.current() and not w.jobs)
    assert len(captures) == 1


@pytest.mark.parametrize('resume', ['manual', 'toggle', 'source', 'lut'])
def test_cancel_suppression_can_be_explicitly_rearmed_or_file_changed(preview, app, tmp_path, resume):
    w, d, captures = preview
    lut = tmp_path / 'active.cube'
    lut.write_text('first fixture identity')
    w.selected_clip().grade.lut_path = str(lut)
    w.selected_clip().grade.lut_strength = .5
    enable_and_wait(app, w, d)
    d.cancel_refresh()
    d.check_current()
    pump(app)
    assert len(captures) == 1 and d.snapshot is None
    if resume == 'manual':
        d.refresh_frame()
    elif resume == 'toggle':
        d.auto_refresh_checkbox.setChecked(False)
        d.auto_refresh_checkbox.setChecked(True)
    elif resume == 'source':
        with Path(w.project.media[0].path).open('ab') as stream:
            stream.write(b'new source identity')
        d.check_current()
    else:
        lut.write_text('different fixture LUT identity')
        d.check_current()
    until(app, lambda: d.current() and not w.jobs)
    assert len(captures) == 2


@pytest.mark.parametrize('global_cancel', [False, True])
def test_cancel_running_and_queued_auto_work_never_restarts(preview, app, monkeypatch, global_cancel):
    import lumen.ui_frame_grade_preview as module
    w, d, _ = preview
    entered, release = threading.Event(), threading.Event()
    calls = []
    def capture(project, clip_id, frame, **kwargs):
        calls.append(frame)
        snapshot = fake_snapshot(project, clip_id, frame)
        entered.set()
        assert release.wait(5)
        return snapshot
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    d.auto_refresh_checkbox.setChecked(True)
    try:
        until(app, entered.is_set)
        w.timeline.playhead = 4
        d.check_current()
        pump(app, .36)
        assert d._pending is not None
        (w.cancel_jobs if global_cancel else d.cancel_refresh)()
        release.set()
        until(app, lambda: not w.jobs)
        for _ in range(4):
            d.check_current()
            pump(app, .12)
        assert calls == [0] and d._pending is None and d.snapshot is None
        assert d.state == 'canceled' and not d.auto_timer.isActive()
    finally:
        release.set()


@pytest.mark.parametrize('change', ['source', 'lut'])
def test_file_replacement_invalidates_current_image_and_rejects_old_result(preview, app, tmp_path, change):
    w, d, captures = preview
    lut = tmp_path / 'active.cube'
    lut.write_text('old LUT bytes')
    w.selected_clip().grade.lut_path = str(lut)
    w.selected_clip().grade.lut_strength = .5
    enable_and_wait(app, w, d)
    old, generation, context = captures[0], d._generation, d.context
    path = Path(w.project.media[0].path) if change == 'source' else lut
    replacement = path.with_suffix('.replacement')
    replacement.write_bytes(path.read_bytes() + b' changed')
    os.replace(replacement, path)
    d.check_current()
    assert d.snapshot is None and d.canvas.image.isNull()
    d._accept_result(generation, context, result(old))
    assert d.snapshot is None
    until(app, lambda: len(captures) == 2 and d.current() and not w.jobs)
    d._accept_result(generation, context, result(old))
    assert d.snapshot is captures[-1]


def test_other_job_defers_auto_request_without_canceling_it(preview, app):
    w, d, captures = preview
    release = threading.Event()
    job = w.start_job('Existing diagnostic', lambda cancel, progress: release.wait(5),
                      lambda value: None, 'componentsetup')
    try:
        d.auto_refresh_checkbox.setChecked(True)
        pump(app)
        assert captures == [] and len(w.jobs) == 1 and not job.cancel.is_set()
        w.timeline.playhead = 6
        d.check_current()
        pump(app)
        assert captures == [] and not job.cancel.is_set()
        release.set()
        until(app, lambda: not w.jobs)
        d.check_current()
        until(app, lambda: d.current() and not w.jobs)
        assert len(captures) == 1 and captures[0].captured_frame == 6
    finally:
        release.set()


def test_toggle_off_and_close_stop_debounce_without_new_jobs(preview, app):
    w, d, captures = preview
    d.auto_refresh_checkbox.setChecked(True)
    d.auto_refresh_checkbox.setChecked(False)
    pump(app)
    assert captures == [] and not w.jobs and not d.auto_timer.isActive()
    d.auto_refresh_checkbox.setChecked(True)
    d.reject()
    pump(app)
    assert captures == [] and not w.jobs
    assert not d.auto_timer.isActive() and not d.timer.isActive()


def test_backend_failure_does_not_retry_until_an_edit(preview, app, monkeypatch):
    import lumen.ui_frame_grade_preview as module
    w, d, _ = preview
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise ValueError('Synthetic unsupported source')
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', fail)
    d.auto_refresh_checkbox.setChecked(True)
    until(app, lambda: bool(calls) and not w.jobs)
    for _ in range(4):
        d.check_current()
        pump(app, .12)
    assert calls == [1] and d.state == 'unsupported'
    w.selected_clip().grade.exposure = .5
    d.check_current()
    until(app, lambda: len(calls) == 2 and not w.jobs)


def test_real_auto_refresh_matches_independent_reference_and_rgb16_render(window, app, tmp_path):
    from lumen.media import ffmpeg_available
    from lumen.qualifier import QualifierSettings
    from lumen.render import RenderEngine
    from lumen.restoration_settings import RestorationSettings
    from test_color_picker_restoration import make_source, ff, picture
    from test_frame_grade_preview_render import complete_reference, lut_reference
    if not ffmpeg_available():
        pytest.skip('FFmpeg/ffprobe required')
    frames = np.stack([picture(12, 16) + offset for offset in (0, 137, 281)])
    project, frames = make_source(tmp_path, frames, transfer='iec61966-2-1')
    project.height = 16
    clip = project.clips[0]
    clip.restoration = RestorationSettings(enabled=True, deblock=.6, denoise=.7, sharpen=.3)
    clip.grade = Grade(input_encoding='srgb', exposure=.08, contrast=1.03,
        white_balance_enabled=True, white_balance_gains=[1.02, .98, 1.03],
        red_curve=[[0., 0.], [.5, .54], [1., 1.]],
        hue_hue_curve=[[0., .02], [1., .02]], hue_sat_curve=[[0., .9], [1., .9]])
    clip.grade.qualifier = QualifierSettings(enabled=True, hue_enabled=False,
        saturation_enabled=False, luma_low=.25, luma_high=.7, luma_softness=.1,
        exposure=.25, saturation=.85, matte_morphology=.5, matte_smoothing=.5,
        window_shape='ellipse', window_width=.7, window_height=.7, window_feather=.2)
    lut = tmp_path / 'cross-channel.cube'
    corners = [lut_reference(np.array([r, g, b]), 1.)
               for b in (0, 1) for g in (0, 1) for r in (0, 1)]
    lut.write_text('LUT_3D_SIZE 2\n' + ''.join(' '.join(map(str, row)) + '\n' for row in corners))
    clip.grade.lut_path, clip.grade.lut_strength = str(lut), .65
    w = window
    w.editor = Editor(project)
    w.refresh()
    w.select_clip(clip.id)
    before = deepcopy(project)
    revision, undo_count = w.revision, len(w.editor._undo)
    cache_before = set(w.cache.iterdir())
    d = FrameGradePreviewDialog(w)
    w.frame_grade_preview_dialog = d
    d.show()
    try:
        enable_and_wait(app, w, d)
        np.testing.assert_allclose(d.snapshot.frame,
            complete_reference(frames[0], clip, project.height), atol=1.2e-6, rtol=0)
        assert w.project == before and w.revision == revision and len(w.editor._undo) == undo_count
        clip.grade.exposure = -.12
        w.timeline.playhead = 1
        # Let the actual repeating poll detect both changes, without refresh_frame.
        until(app, lambda: d.current() and d.context[3] == 1 and not w.jobs, seconds=10)
        expected = complete_reference(frames[1], clip, project.height)
        np.testing.assert_allclose(d.snapshot.frame, expected, atol=1.2e-6, rtol=0)
        assert d.snapshot.frame.dtype == np.float32 and d.snapshot.stage == 'grade_preview'
        assert not w.preview_paths and set(w.cache.iterdir()) == cache_before
        assert w.scopes.data is None and w.video.reference is None and w.video.image.isNull()
        assert w.preview_revision == -1 and w.loaded_preview_mode is None
        engine = RenderEngine(timeout=60)
        try:
            stage = engine._graded_segment(project.media[0], clip, project, None)
            decoded = np.frombuffer(ff('-i', stage, '-pix_fmt', 'rgb48le', '-f', 'rawvideo', 'pipe:1'),
                                    dtype='<u2').reshape(3, 16, 16, 3)
            np.testing.assert_allclose(decoded[1], np.rint(np.clip(expected, 0, 1) * 65535), atol=1, rtol=0)
            np.testing.assert_allclose(decoded[1], np.rint(np.clip(d.snapshot.frame, 0, 1) * 65535), atol=1, rtol=0)
        finally:
            engine.close()
    finally:
        d.reject()


def test_disabling_auto_drops_already_queued_automatic_request(preview, app, monkeypatch):
    import lumen.ui_frame_grade_preview as module
    w, d, _ = preview
    entered, release = threading.Event(), threading.Event()
    calls = []
    def capture(project, clip_id, frame, **kwargs):
        calls.append(frame)
        snapshot = fake_snapshot(project, clip_id, frame)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return snapshot
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    d.auto_refresh_checkbox.setChecked(True)
    try:
        until(app, entered.is_set)
        w.timeline.playhead = 4
        d.check_current()
        pump(app, .36)
        assert d._pending is not None
        d.auto_refresh_checkbox.setChecked(False)
        release.set()
        until(app, lambda: not w.jobs)
        pump(app)
        assert calls == [0], 'Turning off automatic mode must discard its queued refresh'
        assert d._pending is None and not d.auto_timer.isActive()
    finally:
        release.set()


def test_manual_refresh_from_playback_does_not_queue_duplicate_auto_refresh(preview, app, monkeypatch):
    w, d, captures = preview
    playback = [QMediaPlayer.PlaybackState.PlayingState]
    monkeypatch.setattr(w.player, 'playbackState', lambda: playback[0])
    def pause():
        if playback[0] == QMediaPlayer.PlaybackState.PlayingState:
            playback[0] = QMediaPlayer.PlaybackState.PausedState
            w.player.playbackStateChanged.emit(playback[0])
    monkeypatch.setattr(w.player, 'pause', pause)
    d.auto_refresh_checkbox.setChecked(True)
    assert not d.auto_timer.isActive()
    d.refresh_frame()
    until(app, lambda: d.current() and not w.jobs)
    pump(app, .5)
    assert len(captures) == 1, 'The manual pause signal must not schedule a duplicate automatic refresh'
    assert not d.auto_timer.isActive()


@pytest.mark.parametrize('change', ['grade', 'source', 'lut'])
def test_failure_for_changed_input_is_rejected_before_next_poll(preview, app, tmp_path, monkeypatch, change):
    import lumen.ui_frame_grade_preview as module
    w, d, _ = preview
    lut = tmp_path / 'request.cube'
    lut.write_text('old active LUT')
    w.selected_clip().grade.lut_path = str(lut)
    w.selected_clip().grade.lut_strength = .5
    entered, release = threading.Event(), threading.Event()
    calls = []
    def capture(project, clip_id, frame, **kwargs):
        calls.append(1)
        snapshot = fake_snapshot(project, clip_id, frame)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return snapshot
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    d.auto_refresh_checkbox.setChecked(True)
    try:
        until(app, entered.is_set)
        generation, context = d._generation, d.context
        if change == 'grade':
            w.selected_clip().grade.exposure = .4
        else:
            path = Path(w.project.media[0].path) if change == 'source' else lut
            path.write_bytes(path.read_bytes() + b' new identity')
        # The polling timer is intentionally stopped. Reject old errors even
        # in the real 0-200 ms gap before check_current sees an input change.
        d._accept_result(generation, context, (None, None, 'obsolete input failure'))
        assert d.state != 'unsupported'
        assert 'obsolete input failure' not in d.status.text()
        assert d.snapshot is None
        d.check_current()
        release.set()
        until(app, lambda: len(calls) == 2 and d.current() and not w.jobs)
    finally:
        release.set()


def test_immediately_finished_job_cannot_leave_dialog_permanently_busy(preview, monkeypatch):
    from PySide6.QtCore import QObject, Signal
    w, d, captures = preview
    class ImmediateJob(QObject):
        finished = Signal()
        def __init__(self):
            super().__init__()
            self.cancel = threading.Event()
    def start(label, operation, success, kind, *, before_start=None):
        job = ImmediateJob()
        w.jobs.add(job)
        w.frame_grade_preview_job = job
        if before_start is not None:
            before_start(job)
        success(operation(job.cancel, lambda _: None))
        w.jobs.discard(job)
        w.frame_grade_preview_job = None
        job.finished.emit()
        return job
    monkeypatch.setattr(w, 'start_job', start)
    d.refresh_frame()
    assert len(captures) == 1 and d.current()
    assert d.job is None and not w.jobs and not d.cancel_button.isEnabled()

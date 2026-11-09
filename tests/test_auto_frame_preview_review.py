"""Independent race and interrupted-flow checks for opt-in paused grade feedback."""
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtMultimedia import QMediaPlayer

from test_ui import app, window, populate, pump_until


def pump_for(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(.002)
    app.processEvents()


def snapshot():
    return SimpleNamespace(frame=np.zeros((2, 2, 3), np.float32), source_frame=0,
                           source_time=0, is_current=lambda *_: True)


def test_immediate_finished_signal_cannot_leave_dead_job_handoff(window, app, tmp_path, monkeypatch):
    """A worker may finish before start_job returns under ordinary scheduling."""
    import lumen.ui_frame_grade_preview as module
    populate(window, tmp_path)
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', lambda *a, **k: snapshot())
    original = window.start_job
    def finished_before_return(*args, **kwargs):
        job = original(*args, **kwargs)
        assert job.wait(2000)
        return job
    monkeypatch.setattr(window, 'start_job', finished_before_return)
    handoffs = []
    monkeypatch.setattr(window, 'build_preview', lambda: handoffs.append(1))
    dialog = window.open_frame_grade_preview()
    try:
        pump_until(app, lambda: not window.jobs)
        assert dialog.current()
        dialog.build_timeline_preview()
        pump_for(app, .1)
        assert handoffs == [1], 'finished before callback connection leaves self.job non-None forever'
        assert dialog.job is None
    finally:
        dialog.reject()


def test_manual_refresh_while_auto_enabled_playing_is_not_double_scheduled(window, app, tmp_path, monkeypatch):
    import lumen.ui_frame_grade_preview as module
    populate(window, tmp_path)
    calls = []
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', lambda *a, **k: calls.append(1) or snapshot())
    state = [QMediaPlayer.PlaybackState.PlayingState]
    monkeypatch.setattr(window.player, 'playbackState', lambda: state[0])
    def pause():
        if state[0] != QMediaPlayer.PlaybackState.PausedState:
            state[0] = QMediaPlayer.PlaybackState.PausedState
            window.player.playbackStateChanged.emit(state[0])
    monkeypatch.setattr(window.player, 'pause', pause)
    dialog = module.FrameGradePreviewDialog(window)
    dialog.show()
    dialog.auto_refresh_checkbox.setChecked(True)
    try:
        assert not dialog._auto_due
        dialog.refresh_frame()
        pump_for(app, .9)
        assert len(calls) == 1, 'pause signal schedules a duplicate auto refresh of the explicit request'
        assert dialog.current()
    finally:
        dialog.reject()


def test_automatic_changes_cancel_active_only_newest_pending_survives(window, app, tmp_path, monkeypatch):
    import lumen.ui_frame_grade_preview as module
    populate(window, tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []
    max_jobs = []
    def capture(project, clip_id, frame, **kwargs):
        calls.append((project.clips[0].grade.linear_exposure, frame))
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return snapshot()
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    dialog = window.open_frame_grade_preview()
    try:
        assert entered.wait(2)
        dialog.auto_refresh_checkbox.setChecked(True)
        for value in (.5, 1., 1.5):
            window.selected_clip().grade.linear_exposure = value
            dialog.check_current()
            pump_for(app, .35)
            max_jobs.append(len(window.jobs))
        assert calls == [(0., 0)]
        assert dialog.job.cancel.is_set()
        assert dialog._pending is not None
        release.set()
        pump_until(app, lambda: not window.jobs and dialog.current())
        assert calls == [(0., 0), (1.5, 0)]
        assert max(max_jobs) == 1
    finally:
        release.set()
        dialog.reject()


def test_cancel_quiets_auto_until_new_edit_and_close_stops_timer(window, app, tmp_path, monkeypatch):
    import lumen.ui_frame_grade_preview as module
    populate(window, tmp_path)
    calls = []
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', lambda *a, **k: calls.append(1) or snapshot())
    dialog = window.open_frame_grade_preview()
    try:
        pump_until(app, lambda: not window.jobs and dialog.current())
        dialog.auto_refresh_checkbox.setChecked(True)
        dialog.cancel_refresh()
        pump_for(app, .7)
        assert calls == [1] and dialog.state == 'canceled'
        window.selected_clip().grade.linear_exposure = .5
        dialog.check_current()
        pump_until(app, lambda: len(calls) == 2 and not window.jobs and dialog.current())
        window.selected_clip().grade.linear_exposure = 1.
        dialog.check_current()
        dialog.reject()
        pump_for(app, .7)
        assert calls == [1, 1] and not dialog.auto_timer.isActive()
    finally:
        dialog.reject()


def test_auto_playback_start_cancels_work_and_resume_waits_for_pause(window, app, tmp_path, monkeypatch):
    import lumen.ui_frame_grade_preview as module
    populate(window, tmp_path)
    state = [QMediaPlayer.PlaybackState.PausedState]
    monkeypatch.setattr(window.player, 'playbackState', lambda: state[0])
    monkeypatch.setattr(window.player, 'pause', lambda: None)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def capture(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return snapshot()
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    dialog = window.open_frame_grade_preview()
    try:
        assert entered.wait(2)
        dialog.auto_refresh_checkbox.setChecked(True)
        state[0] = QMediaPlayer.PlaybackState.PlayingState
        window.player.playbackStateChanged.emit(state[0])
        assert dialog.job.cancel.is_set() and not dialog._auto_due
        release.set()
        pump_for(app, .6)
        assert calls == [1] and dialog.snapshot is None
        state[0] = QMediaPlayer.PlaybackState.PausedState
        window.player.playbackStateChanged.emit(state[0])
        pump_until(app, lambda: len(calls) == 2 and not window.jobs and dialog.current())
    finally:
        release.set()
        dialog.reject()


def test_unknown_source_auto_fails_closed_without_retries(window, app, tmp_path):
    populate(window, tmp_path)  # File intentionally is not valid video.
    dialog = window.open_frame_grade_preview()
    try:
        dialog.auto_refresh_checkbox.setChecked(True)
        pump_until(app, lambda: not window.jobs and dialog.state == 'unsupported'
                   and not dialog._auto_due and not dialog.auto_timer.isActive())
        generation = dialog._generation
        pump_for(app, .7)
        assert dialog.state == 'unsupported' and dialog._generation == generation
        assert dialog.snapshot is None and dialog.canvas.image.isNull()
        assert not window.video.frame_provenance and window.scopes.data is None
    finally:
        dialog.reject()


def test_controlled_4k_worker_keeps_gui_heartbeat_responsive(window, app, tmp_path, monkeypatch):
    import lumen.ui_frame_grade_preview as module
    populate(window, tmp_path)
    entered, release = threading.Event(), threading.Event()
    measured = []
    result = snapshot()
    result.frame = np.full((2160, 3840, 3), .4, np.float32)
    main_thread = threading.get_ident()
    def capture(*args, **kwargs):
        measured.append(('capture', threading.get_ident()))
        entered.set()
        assert release.wait(5)
        return result
    original = module._display_pixels
    def presentation(*args, **kwargs):
        measured.append(('presentation', threading.get_ident()))
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    monkeypatch.setattr(module, '_display_pixels', presentation)
    ticks = []
    timer = QTimer(window)
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    dialog = window.open_frame_grade_preview()
    try:
        assert entered.wait(2)
        dialog.auto_refresh_checkbox.setChecked(True)
        # Re-arm at a stable context; use a slow controlled decode to measure UI.
        dialog.auto_timer.stop()
        dialog._auto_due = False
        pump_for(app, .3)
        release.set()
        pump_until(app, lambda: not window.jobs and dialog.current())
        gaps = np.diff(ticks) * 1000
        evidence = {'frame': [3840, 2160], 'ticks': len(ticks),
                    'median_gap_ms': float(np.median(gaps)), 'p95_gap_ms': float(np.quantile(gaps, .95)),
                    'max_gap_ms': float(np.max(gaps)),
                    'capture_off_gui': measured[0][1] != main_thread,
                    'presentation_off_gui': measured[1][1] != main_thread}
        folder = Path(os.environ.get('AUTO_FRAME_REVIEW_EVIDENCE', '../../evidence/independent-review'))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'gui-responsiveness.json').write_text(json.dumps(evidence, indent=2))
        assert len(ticks) >= 30 and max(gaps) < 500
        assert all(thread != main_thread for _, thread in measured)
        assert dialog.canvas.image.size().width() == 3840
    finally:
        timer.stop()
        release.set()
        dialog.reject()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('width', [360, 760])
def test_automatic_ui_locales_compact_geometry_and_screenshot(window, app, tmp_path, locale, width):
    from lumen.i18n import set_locale
    from lumen.model import Editor
    from test_selective_color_render import source_project
    window.editor = Editor(source_project(tmp_path))
    window.refresh()
    window.select_clip('picture')
    set_locale(locale)
    dialog = window.open_frame_grade_preview()
    try:
        dialog.auto_refresh_checkbox.setChecked(True)
        dialog.resize(width, 700)
        pump_until(app, lambda: not window.jobs and dialog.current()
                   and not dialog._auto_due and not dialog.auto_timer.isActive())
        pump_for(app, .05)
        assert dialog.width() == width
        checkbox = dialog.auto_refresh_checkbox
        assert checkbox.text() == ('暂停时自动刷新' if locale == 'zh-CN' else 'Automatically refresh while paused')
        assert ('当前有效' if locale == 'zh-CN' else 'Current') in dialog.status.text()
        assert checkbox.isChecked() and checkbox.width() >= checkbox.minimumSizeHint().width()
        widgets = (dialog.stage, dialog.status, dialog.canvas, dialog.metadata,
                   dialog.precision, checkbox, dialog.refresh_button,
                   dialog.cancel_button, dialog.close_button, dialog.timeline_button)
        for widget in widgets:
            assert widget.visibleRegion().boundingRect() == widget.rect()
            assert widget.geometry().left() >= 0 and widget.geometry().right() < dialog.width()
        folder = Path(os.environ.get('AUTO_FRAME_REVIEW_EVIDENCE', '../../evidence/independent-review'))
        folder.mkdir(parents=True, exist_ok=True)
        name = f'auto-grade-{locale}-{width}-dpr{dialog.devicePixelRatioF():g}'
        assert dialog.grab().save(str(folder / (name + '.png')))
        geometry = {type(w).__name__ + str(i): {'text': w.text() if hasattr(w, 'text') else '',
                   'rect': list(w.geometry().getRect()), 'size_hint': [w.sizeHint().width(), w.sizeHint().height()]}
                    for i, w in enumerate(widgets)}
        (folder / (name + '.json')).write_text(json.dumps(geometry, ensure_ascii=False, indent=2))
    finally:
        dialog.reject()
        set_locale('en')


@pytest.mark.parametrize('change', ['source', 'lut', 'grade'])
@pytest.mark.parametrize('outcome', ['success', 'error'])
def test_inflight_same_revision_change_rejects_callback_before_poll(window, app, tmp_path, monkeypatch, change, outcome):
    """Token recheck must precede both image display and old-error display."""
    import lumen.ui_frame_grade_preview as module
    from test_frame_grade_preview_source import write_lut
    populate(window, tmp_path)
    lut_path = tmp_path / 'change.cube'
    window.selected_clip().grade.lut_path = write_lut(lut_path)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def capture(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return snapshot()
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', capture)
    dialog = module.FrameGradePreviewDialog(window)
    window.frame_grade_preview_dialog = dialog
    dialog.show()
    dialog.timer.stop()  # Model result arriving before the next 200-ms poll.
    dialog.auto_refresh_checkbox.setChecked(True)
    try:
        pump_until(app, entered.is_set)
        generation, context, revision = dialog._generation, dialog.context, window.revision
        if change == 'source':
            source_path = Path(window.project.media[0].path)
            source_path.write_bytes(b'replaced source on disk')
        elif change == 'lut':
            write_lut(lut_path, True)
        else:
            window.selected_clip().grade.linear_exposure = .5
        assert window.revision == revision
        old = snapshot()
        result = (None, None, 'obsolete error from previous source') if outcome == 'error' else (
            old, module._display_pixels(old.frame, threading.Event()), None)
        dialog._accept_result(generation, context, result)
        assert dialog.state == 'stale' and dialog.snapshot is None
        assert dialog.canvas.image.isNull() and 'obsolete error' not in dialog.status.text()
        assert dialog.job.cancel.is_set()
        dialog.check_current()
        pump_for(app, .35)
        assert calls == [1] and len(window.jobs) == 1 and dialog._pending is not None
        release.set()
        pump_until(app, lambda: len(calls) == 2 and not window.jobs and dialog.current())
    finally:
        release.set()
        dialog.reject()

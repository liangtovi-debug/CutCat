"""Independent interruption regressions for the grade-preview job queue."""
from types import SimpleNamespace
import threading
import time

import numpy as np

from test_ui import app, window, populate


def settle_jobs(app, window):
    # Empty once is insufficient: finished handlers enqueue zero-delay callbacks.
    deadline, idle_since = time.monotonic() + 5, None
    while time.monotonic() < deadline:
        app.processEvents()
        if window.jobs:
            idle_since = None
        elif idle_since is None:
            idle_since = time.monotonic()
        elif time.monotonic() - idle_since > .1:
            return
        time.sleep(.005)
    assert not window.jobs


def test_global_cancel_drops_pending_latest_refresh(window, app, tmp_path, monkeypatch):
    import lumen.ui_frame_grade_preview as preview
    w = window
    populate(w, tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []
    snapshot = SimpleNamespace(
        frame=np.zeros((2, 2, 3), np.float32), source_frame=0, source_time=0,
        is_current=lambda *_: True)
    def capture(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(10)
        return snapshot
    monkeypatch.setattr(preview, 'capture_grade_preview_snapshot', capture)
    dialog = w.open_frame_grade_preview()
    try:
        assert entered.wait(3)
        dialog.refresh_frame()
        assert dialog._pending is not None
        w.cancel_jobs()
        release.set()
        settle_jobs(app, w)
        assert calls == [1], 'Global Cancel job must not restart the queued grade refresh'
        assert dialog._pending is None and dialog.snapshot is None
    finally:
        release.set()
        dialog.reject()


def test_reopening_frame_preview_supersedes_old_pending_timeline_handoff(window, app, tmp_path, monkeypatch):
    import lumen.ui_frame_grade_preview as preview
    w = window
    populate(w, tmp_path)
    entered, release = threading.Event(), threading.Event()
    captures, handoffs = [], []
    snapshot = SimpleNamespace(
        frame=np.zeros((2, 2, 3), np.float32), source_frame=0, source_time=0,
        is_current=lambda *_: True)
    def capture(*args, **kwargs):
        captures.append(1)
        if len(captures) == 1:
            entered.set()
            assert release.wait(10)
        return snapshot
    monkeypatch.setattr(preview, 'capture_grade_preview_snapshot', capture)
    monkeypatch.setattr(w, 'build_preview', lambda: handoffs.append(1))
    first = w.open_frame_grade_preview()
    second = None
    try:
        assert entered.wait(3)
        first.build_timeline_preview()
        assert first._handoff_context is not None and not first.isVisible()
        second = w.open_frame_grade_preview()
        assert second is not first and second._pending is not None
        release.set()
        settle_jobs(app, w)
        assert handoffs == [], 'Old full-preview handoff must not supersede a newer frame-preview request'
        assert captures == [1, 1] and second.snapshot is snapshot
    finally:
        release.set()
        first.reject()
        if second is not None:
            second.reject()


def test_global_cancel_clears_hidden_dialog_timeline_handoff(window, app, tmp_path, monkeypatch):
    import lumen.ui_frame_grade_preview as preview
    w = window
    populate(w, tmp_path)
    entered, release = threading.Event(), threading.Event()
    handoffs = []
    snapshot = SimpleNamespace(
        frame=np.zeros((2, 2, 3), np.float32), source_frame=0, source_time=0,
        is_current=lambda *_: True)
    def capture(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return snapshot
    monkeypatch.setattr(preview, 'capture_grade_preview_snapshot', capture)
    monkeypatch.setattr(w, 'build_preview', lambda: handoffs.append(1))
    dialog = w.open_frame_grade_preview()
    try:
        assert entered.wait(3)
        dialog.build_timeline_preview()
        assert not dialog.isVisible() and dialog._handoff_context is not None
        w.cancel_jobs()
        release.set()
        settle_jobs(app, w)
        assert handoffs == [] and dialog._handoff_context is None
    finally:
        release.set()
        dialog.reject()


def test_capture_and_display_quantization_both_run_off_gui_thread(window, app, tmp_path, monkeypatch):
    import lumen.ui_frame_grade_preview as preview
    from PySide6.QtCore import QThread
    w = window
    populate(w, tmp_path)
    visited = []
    snapshot = SimpleNamespace(
        frame=np.zeros((2, 2, 3), np.float32), source_frame=0, source_time=0,
        is_current=lambda *_: True)
    original_display = preview._display_pixels
    def capture(*args, **kwargs):
        visited.append(('capture', QThread.currentThread() is app.thread()))
        return snapshot
    def display(frame, cancel):
        visited.append(('presentation', QThread.currentThread() is app.thread()))
        return original_display(frame, cancel)
    monkeypatch.setattr(preview, 'capture_grade_preview_snapshot', capture)
    monkeypatch.setattr(preview, '_display_pixels', display)
    dialog = w.open_frame_grade_preview()
    try:
        settle_jobs(app, w)
        assert visited == [('capture', False), ('presentation', False)]
        assert dialog.snapshot is snapshot and dialog.state == 'current'
    finally:
        dialog.reject()


def test_main_close_cancels_handoff_already_queued_after_worker_drained(window, app, tmp_path, monkeypatch):
    """Model the exact post-finished/pre-singleShot interval with no active jobs."""
    import lumen.ui_frame_grade_preview as preview
    from PySide6.QtCore import QTimer
    w = window
    populate(w, tmp_path)
    handoffs = []
    monkeypatch.setattr(w, 'build_preview', lambda: handoffs.append(1))
    dialog = preview.FrameGradePreviewDialog(w)
    w.frame_grade_preview_dialog = dialog
    dialog.show()
    dialog._handoff_context = (id(w.project), w.revision, w.preview_mode)
    dialog.reject()
    # Worker has been reaped and its finished callback has queued this call.
    assert not w.jobs and dialog.job is None and dialog._waiting_job is None
    QTimer.singleShot(0, dialog._complete_handoff)
    w.dirty = False
    w.close()
    app.processEvents()
    assert handoffs == [], 'An accepted no-jobs main-window close must cancel queued handoffs'

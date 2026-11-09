"""Real Qt auto-preview workers decode the moving window on each output frame.

This is a headless Qt integration test, not native Windows/macOS display QA.
Capture, FFmpeg decode, source selection, grading, worker signals and both
Qt polling/debounce timers are real; no capture implementation is mocked.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest

from lumen.media import ffmpeg_available
from lumen.model import Editor
from lumen.ui_frame_grade_preview import FrameGradePreviewDialog
from test_auto_frame_grade_preview import pump, until
from test_ui import app, window
from test_window_animation_render import (MOVING_KEYS, animated_settings,
                                          grade_reference, make_source)

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def test_automatic_preview_uses_output_time_for_repeated_source_and_key_edits(
        window, app, tmp_path):
    project, source = make_source(tmp_path, animated_settings(),
        fps=Fraction(30000, 1001), speed=Fraction(1, 2),
        origin=Fraction(3, 2), duration=6, start=17)
    clip = project.clips[0]
    w = window
    w.editor = Editor(project)
    w.refresh()
    w.select_clip(clip.id)
    w.seek_frame(18)  # Local output 1 -> floor(3/2 + 1/2) = source 2.
    w.player.pause()
    assert w.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
    original = deepcopy(project)
    revision, undo_count = w.revision, len(w.editor._undo)
    cache_before = set(w.cache.iterdir())
    dialog = FrameGradePreviewDialog(w)
    w.frame_grade_preview_dialog = dialog
    dialog.show()
    try:
        # Merely opening the dialog must not silently opt into background work.
        assert not dialog.auto_refresh_checkbox.isChecked()
        pump(app, .4)
        assert dialog.snapshot is None and not w.jobs
        checkbox = dialog.auto_refresh_checkbox
        QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton,
                         pos=QPoint(8, checkbox.height() // 2))
        assert checkbox.isChecked()
        assert dialog.timer.isActive() and dialog.auto_timer.isSingleShot()
        until(app, lambda: dialog.current() and dialog.context[3] == 18 and not w.jobs,
              seconds=10)
        first = dialog.snapshot
        assert first.identity.timeline_frame == 18 and first.identity.clip_id == clip.id
        assert first.source_frame == 2 and first.source_time == Fraction(2) / project.fps
        np.testing.assert_allclose(first.frame,
            grade_reference(source[2], 1, MOVING_KEYS, clip.grade), atol=3e-7, rtol=0)
        assert first.stage == 'grade_preview' and not first.frame.flags.writeable
        assert not dialog.canvas.image.isNull()

        # Let the real 200-ms poll and 300-ms debounce notice this change.
        # There is deliberately no direct refresh/check_current/capture call.
        w.seek_frame(19)  # Local output 2 -> same exact decoded source 2.
        assert not dialog.current()
        until(app, lambda: dialog.current() and dialog.context[3] == 19 and not w.jobs,
              seconds=10)
        second = dialog.snapshot
        assert second is not first
        assert second.identity.timeline_frame == 19 and second.source_frame == first.source_frame == 2
        assert second.source_time == first.source_time
        np.testing.assert_allclose(second.frame,
            grade_reference(source[2], 2, MOVING_KEYS, clip.grade), atol=3e-7, rtol=0)
        assert np.max(abs(first.frame - second.frame)) > .04
        assert project == original and w.revision == revision and len(w.editor._undo) == undo_count

        # An in-place typed key replacement exercises the signature guard even
        # without a revision or playhead change. The next accepted result must
        # use the new geometry, not just reshow the previous decoded source.
        keys = clip.grade.qualifier.window_keyframes
        keys[1] = replace(keys[1], center_x=.20, width=.20)
        edited_keys = (MOVING_KEYS[0], (8, .20, .48, .20, .62, 'linear'))
        assert not second.is_current(project, clip.id, 19) and not dialog.current()
        until(app, lambda: dialog.snapshot is None and dialog.canvas.image.isNull(), seconds=3)
        until(app, lambda: dialog.current() and dialog.context[3] == 19 and not w.jobs,
              seconds=10)
        edited = dialog.snapshot
        assert edited is not second and edited.identity.timeline_frame == 19
        assert edited.source_frame == 2 and edited.source_time == second.source_time
        np.testing.assert_allclose(edited.frame,
            grade_reference(source[2], 2, edited_keys, clip.grade), atol=3e-7, rtol=0)
        assert np.max(abs(edited.frame - second.frame)) > .10
        assert edited.is_current(project, clip.id, 19)
        assert w.revision == revision and len(w.editor._undo) == undo_count
        assert not w.preview_paths and set(w.cache.iterdir()) == cache_before
        assert w.scopes.data is None and w.video.reference is None and w.video.image.isNull()
        assert w.preview_revision == -1 and w.loaded_preview_mode is None

        # Cancel an actual pending automatic request before its debounce fires.
        # Remaining unchanged must not restart it or resurrect an accepted image.
        w.seek_frame(20)
        until(app, lambda: dialog.auto_timer.isActive() and dialog.cancel_button.isEnabled(), seconds=3)
        QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
        canceled_generation = dialog._generation
        assert dialog.state == 'canceled' and dialog.snapshot is None
        pump(app, .7)
        assert dialog.state == 'canceled' and dialog._generation == canceled_generation
        assert not dialog.auto_timer.isActive() and not w.jobs
        assert dialog._pending is None and dialog.canvas.image.isNull()
    finally:
        dialog.reject()
        w.cancel_jobs()
        for job in list(w.jobs):
            assert job.wait(5000), 'Automatic frame grading did not stop'
        app.processEvents()
    assert not dialog.timer.isActive() and not dialog.auto_timer.isActive()
    assert not w.jobs and dialog.snapshot is None

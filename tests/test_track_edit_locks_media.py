"""Edit locks are metadata, never a render, timing, or source transformation."""
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from lumen.audio_analysis import audio_settings_fingerprint
from lumen.media import ffmpeg_available
from lumen.model import Editor, Project
from test_clip_groups_media import (grouped_sources, project_for_sources,
    expected_signal, group_pairs, render_delivery)
from test_retime_video import engine, frame_sources, assert_delivery_frames, decode
from test_roll_media import exact_pcm

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


@pytest.mark.parametrize('mode', ['preview', 'export'])
def test_lock_all_tracks_and_unlock_are_graph_picture_pcm_and_source_neutral(
        tmp_path, engine, grouped_sources, mode):
    editor = Editor(project_for_sources(grouped_sources))
    group_pairs(editor)
    original = deepcopy(editor.project)
    sources = {Path(m.path): Path(m.path).read_bytes() for m in original.media}
    graph = engine._compile(original)
    fingerprint = audio_settings_fingerprint(original)
    pixels = decode(render_delivery(engine, original, tmp_path, 'unlocked', mode), rgb16=False)
    pcm = exact_pcm(engine, original)
    expected_picture, expected_pcm = expected_signal(grouped_sources)
    # Independent numbered RGB source arrays and synthetic PCM, not a renderer
    # serving as its own oracle. Delivery RGB uses documented codec tolerances.
    assert_delivery_frames(pixels, expected_picture)
    np.testing.assert_allclose(pcm, expected_pcm, rtol=3e-7, atol=3e-7)
    for track in range(4):
        editor.set_track_edit_lock(track, True)
        assert engine._compile(editor.project) == graph
        assert audio_settings_fingerprint(editor.project) == fingerprint
    locked = deepcopy(editor.project)
    assert locked.track_edit_locks == [True] * 4
    expected = deepcopy(original); expected.track_edit_locks = [True] * 4
    assert locked == expected
    destination = tmp_path / 'locked.lumen'
    locked.save(destination)
    reopened = Project.load(destination)
    assert reopened == locked
    np.testing.assert_array_equal(
        decode(render_delivery(engine, reopened, tmp_path, 'locked', mode), rgb16=False), pixels)
    np.testing.assert_array_equal(exact_pcm(engine, reopened), pcm)
    for track in range(4):
        editor.set_track_edit_lock(track, False)
        assert engine._compile(editor.project) == graph
    assert editor.project == original
    np.testing.assert_array_equal(
        decode(render_delivery(engine, editor.project, tmp_path, 'unlocked-again', mode), rgb16=False), pixels)
    np.testing.assert_array_equal(exact_pcm(engine, editor.project), pcm)
    for _ in range(4):
        assert editor.undo()
    assert editor.project == locked
    for _ in range(4):
        assert editor.redo()
    assert editor.project == original
    assert all(path.read_bytes() == contents for path, contents in sources.items())

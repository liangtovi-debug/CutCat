"""Independent numbered-picture/PCM impulse oracles for atomic group edits."""
from copy import deepcopy
from fractions import Fraction
import wave

import numpy as np
import pytest

from lumen.audio_waveform import extract_peaks, identity, map_peaks
from lumen.media import ffmpeg_available, probe
from lumen.model import Clip, Editor, Project
from test_retime_video import WIDTH, HEIGHT, assert_delivery_frames, decode, engine, frame_sources
from test_roll_media import exact_pcm

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


@pytest.mark.parametrize('delta', [-4, 5])
def test_numbered_video_group_move_delete_and_reopen(tmp_path, engine, frame_sources, delta):
    source = frame_sources[Fraction(24)]
    project = Project(width=WIDTH, height=HEIGHT, fps_num=24, fps_den=1,
        media=[deepcopy(source.media)], clips=[
        Clip('a', source.media.id, 0, 10, 4, 6),
        Clip('b', source.media.id, 1, 22, 30, 5),
        Clip('anchor', source.media.id, 0, 40, 60, 4)])
    editor = Editor(project); before = deepcopy(project)
    old_segments = [engine._graded_segment(source.media, c, project, None) for c in project.clips]
    editor.move_clips(('a', 'b'), delta)
    expected = np.zeros((44, HEIGHT, WIDTH, 3), np.uint16)
    expected[10+delta:16+delta] = source.original[4:10]
    expected[22+delta:27+delta] = source.original[30:35]
    expected[40:44] = source.original[60:64]
    # Body movement changes placement only, so source/grade segment cache keys reuse.
    assert [engine._graded_segment(source.media, c, project, None) for c in project.clips] == old_segments
    assert_delivery_frames(decode(engine.preview(project), rgb16=False), expected)
    saved = tmp_path/'group.lumen'; project.save(saved); loaded = Project.load(saved)
    assert loaded == project
    assert_delivery_frames(decode(engine.render(loaded, tmp_path/'moved.mp4'), rgb16=False), expected)
    moved = deepcopy(project)
    editor.delete_clips(('a', 'b'))
    expected[:40] = 0
    assert_delivery_frames(decode(engine.preview(editor.project), rgb16=False), expected)
    assert editor.project.media == before.media
    assert editor.undo() and editor.project == moved
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == moved


@pytest.mark.parametrize('delta', [-4, 5])
def test_pcm_impulses_waveform_identity_move_delete_and_reopen(tmp_path, engine, delta):
    raw = np.zeros(48000*4, np.int16)
    for frame, amplitude in ((5, 7000), (8, -9000), (31, 11000), (33, -13000), (61, 15000)):
        raw[frame*2000:frame*2000+3] = (amplitude, -amplitude, amplitude)
    path = tmp_path/'impulses.wav'
    with wave.open(str(path), 'wb') as out:
        out.setparams((1, 2, 48000, 0, 'NONE', 'not compressed')); out.writeframes(raw.astype('<i2').tobytes())
    media = probe(path)
    project = Project(width=64, height=48, fps_num=24, fps_den=1, media=[media], clips=[
        Clip('a', media.id, 2, 10, 4, 6), Clip('b', media.id, 3, 22, 30, 5),
        Clip('anchor', media.id, 2, 40, 60, 4)])
    editor = Editor(project); before = deepcopy(project); source_identity = identity(path)
    peaks = extract_peaks(path)
    envelopes = [map_peaks(peaks, c, Fraction(24), local_end=c.duration, pixels=60) for c in project.clips]
    editor.move_clips(('a', 'b'), delta)
    expected = np.zeros(44*2000, np.float32)
    for start, source_in, duration in ((10+delta, 4, 6), (22+delta, 30, 5), (40, 60, 4)):
        expected[start*2000:(start+duration)*2000] += raw[source_in*2000:(source_in+duration)*2000].astype(np.float32)/32768
    np.testing.assert_allclose(exact_pcm(engine, project), expected, rtol=3e-7, atol=3e-7)
    assert identity(path) == source_identity
    for clip, old in zip(project.clips, envelopes):
        np.testing.assert_array_equal(map_peaks(peaks, clip, Fraction(24), local_end=clip.duration, pixels=60), old)
    saved = tmp_path/'group-audio.lumen'; project.save(saved); loaded = Project.load(saved)
    assert loaded == project
    np.testing.assert_allclose(exact_pcm(engine, loaded), expected, rtol=3e-7, atol=3e-7)
    editor.delete_clips(('a', 'b')); expected[:40*2000] = 0
    np.testing.assert_allclose(exact_pcm(engine, editor.project), expected, rtol=3e-7, atol=3e-7)
    assert identity(path) == source_identity and editor.project.media == before.media
    assert editor.undo(); assert editor.undo() and editor.project == before

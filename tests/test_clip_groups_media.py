"""Persistent groups are metadata, not a source-time or signal transform.

Numbered lossless pictures and deterministic PCM are independent oracles. Group
translations are checked against the original source arrays, not another edit,
production timing helper, waveform picture, or rendered reference timeline.
"""
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
import wave

import numpy as np
import pytest

from lumen.audio_waveform import extract_peaks, identity, map_peaks
from lumen.clip_motion import ClipMotionKeyframe
from lumen.media import ffmpeg_available, probe
from lumen.model import Clip, Editor, Project
from lumen.qualifier import WindowKeyframe
from lumen.timing import ClipTimeMap
from test_retime_video import WIDTH, HEIGHT, assert_delivery_frames, decode, engine, frame_sources
from test_roll_media import exact_pcm


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


@pytest.fixture
def grouped_sources(tmp_path, frame_sources):
    source = frame_sources[Fraction(24)]
    # Every sample varies, including the tails. This catches wrong source trims,
    # old placement, gaps, dropped members, and a one-sample audio displacement.
    raw = np.random.default_rng(736).integers(-6000, 6001, 48000 * 4, dtype=np.int16)
    for frame, amplitude in ((5, 18000), (8, -19000), (31, 20000), (33, -21000), (65, 22000)):
        raw[frame * 2000:frame * 2000 + 3] = (amplitude, -amplitude, amplitude)
    path = tmp_path / 'group-source.wav'
    with wave.open(str(path), 'wb') as out:
        out.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        out.writeframes(raw.astype('<i2').tobytes())
    return source, probe(path), raw


def project_for_sources(sources):
    source, audio, _ = sources
    # Two independent groups, each spanning a picture and an audio track. Two
    # loose clips move alongside them; the final picture/audio pair is fixed.
    picture_a = Clip('picture-a', source.media.id, 0, 10, 0, 6,
                     time_map=ClipTimeMap(Fraction(9, 8), Fraction(81, 8), Fraction(3, 2)))
    picture_b = Clip('picture-b', source.media.id, 1, 22, 0, 5,
                     time_map=ClipTimeMap(Fraction(121, 4), Fraction(149, 4), Fraction(3, 2)))
    # Signed and hidden local handles must remain local after body movement.
    # Neutral picture geometry and a bypassed qualifier keep the independently
    # numbered source oracle applicable without a second geometry renderer.
    picture_a.motion_keyframes = [ClipMotionKeyframe(-3), ClipMotionKeyframe(2, interpolation='hold'),
                                  ClipMotionKeyframe(11)]
    picture_a.grade.qualifier.window_keyframes = [
        WindowKeyframe(-4, .2, .3, .4, .5), WindowKeyframe(12, .8, .7, .6, .4)]
    return Project(width=WIDTH, height=HEIGHT, fps_num=24, fps_den=1,
        media=[deepcopy(source.media), audio], clips=[
        picture_a, Clip('audio-a', audio.id, 2, 10, 4, 6),
        picture_b, Clip('audio-b', audio.id, 3, 22, 30, 5),
        Clip('loose-picture', source.media.id, 0, 34, 50, 3),
        Clip('loose-audio', audio.id, 2, 34, 50, 3),
        Clip('fixed-picture', source.media.id, 0, 44, 64, 4),
        Clip('fixed-audio', audio.id, 2, 44, 64, 4)])


def expected_signal(sources, delta=0, *, deleted=False):
    source, _, raw = sources
    picture = np.zeros((48, HEIGHT, WIDTH, 3), np.uint16)
    pcm = np.zeros(48 * 2000, np.float32)
    if not deleted:
        # All constants come from this fixture's input specification. Do not
        # read edited Clip.timing, compiler plans, or waveform envelopes here.
        for start, count, origin, speed in (
            (10 + delta, 6, Fraction(9, 8), Fraction(3, 2)),
            (22 + delta, 5, Fraction(121, 4), Fraction(3, 2)),
            (34 + delta, 3, Fraction(50), Fraction(1))):
            indices = [(origin + local * speed).__floor__() for local in range(count)]
            picture[start:start + count] = source.original[indices]
        for start, origin, count in ((10 + delta, 4, 6), (22 + delta, 30, 5), (34 + delta, 50, 3)):
            pcm[start * 2000:(start + count) * 2000] = raw[origin * 2000:(origin + count) * 2000].astype(np.float32) / 32768
    picture[44:48] = source.original[64:68]
    pcm[44 * 2000:48 * 2000] = raw[64 * 2000:68 * 2000].astype(np.float32) / 32768
    return picture, pcm


def group_pairs(editor):
    first = editor.group_clips(('audio-a', 'picture-a'))
    second = editor.group_clips(('picture-b', 'audio-b'))
    assert first.id != second.id
    return deepcopy(editor.project.clip_groups)


def render_delivery(engine, project, tmp_path, name, mode):
    if mode == 'preview':
        return engine.preview(project)
    return engine.render(project, tmp_path / f'{name}.mp4')


@pytest.mark.parametrize('mode', ['preview', 'export'])
def test_group_and_ungroup_are_exact_graph_picture_and_pcm_neutral(
        tmp_path, engine, grouped_sources, mode):
    editor = Editor(project_for_sources(grouped_sources))
    original = deepcopy(editor.project)
    sources = {Path(media.path): Path(media.path).read_bytes() for media in editor.project.media}
    graph = engine._compile(editor.project)
    pixels = decode(render_delivery(engine, editor.project, tmp_path, 'original', mode), rgb16=False)
    pcm = exact_pcm(engine, editor.project)
    expected_picture, expected_pcm = expected_signal(grouped_sources)
    assert_delivery_frames(pixels, expected_picture)
    np.testing.assert_allclose(pcm, expected_pcm, rtol=3e-7, atol=3e-7)

    groups = group_pairs(editor)
    assert editor.project.media == original.media and editor.project.clips == original.clips
    assert engine._compile(editor.project) == graph
    np.testing.assert_array_equal(
        decode(render_delivery(engine, editor.project, tmp_path, 'grouped', mode), rgb16=False), pixels)
    np.testing.assert_array_equal(exact_pcm(engine, editor.project), pcm)
    grouped = deepcopy(editor.project)

    # Naming just one member dissolves that group, not its other group or clips.
    editor.ungroup_clips(('audio-a',))
    assert editor.project.clip_groups == [groups[1]]
    assert engine._compile(editor.project) == graph
    editor.ungroup_clips(('picture-b',))
    assert editor.project == original
    assert engine._compile(editor.project) == graph
    np.testing.assert_array_equal(
        decode(render_delivery(engine, editor.project, tmp_path, 'ungrouped', mode), rgb16=False), pixels)
    np.testing.assert_array_equal(exact_pcm(engine, editor.project), pcm)
    assert editor.undo() and editor.undo() and editor.project == grouped
    assert editor.redo() and editor.redo() and editor.project == original
    assert all(path.read_bytes() == contents for path, contents in sources.items())


@pytest.mark.parametrize('delta', [-4, 5])
def test_multiple_persistent_groups_and_loose_clips_move_against_source_oracles(
        tmp_path, engine, grouped_sources, delta):
    editor = Editor(project_for_sources(grouped_sources))
    groups = group_pairs(editor)
    before = deepcopy(editor.project)
    selected = ('loose-audio', 'audio-b', 'picture-a', 'loose-picture', 'picture-b', 'audio-a')
    audio_path = Path(grouped_sources[1].path)
    audio_identity = identity(audio_path)
    peaks = extract_peaks(audio_path)
    envelopes = {c.id: map_peaks(peaks, c, Fraction(24), local_end=c.duration, pixels=60)
                 for c in before.clips if c.media_id == grouped_sources[1].id}
    video_before = {c.id: engine._graded_segment(grouped_sources[0].media, c, before, None)
                    for c in before.clips if c.media_id == grouped_sources[0].media.id}

    moved = editor.move_clips(selected, delta)
    assert [c.id for c in moved] == [c.id for c in before.clips if c.id in selected]
    assert editor.project.clip_groups == groups
    for old, actual in zip(before.clips, editor.project.clips):
        expected = deepcopy(old)
        if old.id in selected:
            expected.start += delta
        assert actual == expected  # Exact source map, all local keys and payload.
        if actual.id in video_before:
            assert engine._graded_segment(grouped_sources[0].media, actual, editor.project, None) == video_before[actual.id]
        if actual.id in envelopes:
            np.testing.assert_array_equal(
                map_peaks(peaks, actual, Fraction(24), local_end=actual.duration, pixels=60), envelopes[actual.id])
    expected_picture, expected_pcm = expected_signal(grouped_sources, delta)
    assert_delivery_frames(decode(engine.preview(editor.project), rgb16=False), expected_picture)
    np.testing.assert_allclose(exact_pcm(engine, editor.project), expected_pcm, rtol=3e-7, atol=3e-7)
    saved = tmp_path / 'moved-groups.lumen'
    editor.project.save(saved)
    reopened = Project.load(saved)
    assert reopened == editor.project
    assert_delivery_frames(decode(engine.render(reopened, tmp_path / 'moved.mp4'), rgb16=False), expected_picture)
    np.testing.assert_allclose(exact_pcm(engine, reopened), expected_pcm, rtol=3e-7, atol=3e-7)

    moved_project = deepcopy(editor.project)
    editor.delete_clips(selected)
    assert editor.project.clip_groups == []
    assert [c.id for c in editor.project.clips] == ['fixed-picture', 'fixed-audio']
    picture, pcm = expected_signal(grouped_sources, deleted=True)
    assert_delivery_frames(decode(engine.preview(editor.project), rgb16=False), picture)
    np.testing.assert_allclose(exact_pcm(engine, editor.project), pcm, rtol=3e-7, atol=3e-7)
    assert editor.project.media == before.media and identity(audio_path) == audio_identity
    assert editor.undo() and editor.project == moved_project
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == moved_project


def test_invalid_group_translations_and_partial_delete_are_signal_atomic(engine, grouped_sources):
    editor = Editor(project_for_sources(grouped_sources))
    group_pairs(editor)
    ids = tuple(c.id for c in editor.project.clips[:6])
    # Keep a genuine redo branch: failed edits must not consume it.
    editor.move_clips(ids, 1)
    assert editor.undo()
    before, undo, redo = deepcopy(editor.project), deepcopy(editor._undo), deepcopy(editor._redo)
    compiled = engine._compile(editor.project)
    picture = decode(engine.preview(editor.project), rgb16=False)
    pcm = exact_pcm(engine, editor.project)
    rejected = (
        lambda: editor.move_clips(ids, -11),  # Earliest member before zero.
        lambda: editor.move_clips(ids, 10),   # Loose members collide with anchors.
        lambda: editor.move_clips(('picture-a',), 1),
        lambda: editor.delete_clips(('audio-b',)),
    )
    for operation in rejected:
        with pytest.raises(ValueError):
            operation()
        assert editor.project == before and editor._undo == undo and editor._redo == redo
        assert engine._compile(editor.project) == compiled
    np.testing.assert_array_equal(decode(engine.preview(editor.project), rgb16=False), picture)
    np.testing.assert_array_equal(exact_pcm(engine, editor.project), pcm)
    assert editor.redo()
    assert [c.start for c in editor.project.clips[:6]] == [11, 11, 23, 23, 35, 35]

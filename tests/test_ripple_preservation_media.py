"""Actual PCM regressions for whole-clip negative timeline translations.

Synthetic one-second audio is enough to demonstrate the formerly silent sample
loss and altered fades. The oracle decodes production audio filters, rather than
assuming unchanged Clip metadata preserves the audible contribution.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import hashlib
from pathlib import Path
import wave

import numpy as np
import pytest

from lumen.audio_mix import compile_audio_mix
from lumen.audio_timing import compile_audio_timing
from lumen.media import ffmpeg_available, probe
from lumen.model import Clip, Editor, Project
from lumen.timing import ClipTimeMap
from test_retime_video import ff

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def boundary(frame, fps):
    """Independent half-up rounding of an absolute 48 kHz sample boundary."""
    value = Fraction(frame * 48000) / fps
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def source(root, offset=0, seed=731):
    path = root / f'noise-{seed}.wav'
    samples = np.random.default_rng(seed).integers(-15000, 15001, 48000 * 2,
                                                 dtype=np.int16)
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        stream.writeframes(samples.astype('<i2').tobytes())
    media = probe(path)
    # Explicit offsets are part of the project audio contract. Embedded offset
    # metadata is independently exercised by the participation full-mix tests.
    media.audio_offset = offset
    return media


def pcm(project):
    mix = compile_audio_mix(project, {m.id: m for m in project.media})
    return np.frombuffer(ff(*mix.inputs, '-filter_complex_threads', '1',
        '-filter_complex', mix.graph, '-map', '[aout]', '-c:a', 'pcm_f32le',
        '-f', 'f32le', 'pipe:1'), dtype='<f4').reshape(-1, 2).copy()


def solo(project, clip):
    result = deepcopy(project)
    result.clips = [deepcopy(clip)]
    result.captions = []
    result.overlays = []
    result.clip_groups = []
    result.video_transitions = []
    return result


@pytest.mark.parametrize('duration,fade', [(1, 0), (5, 1)], ids=['sample-loss', 'fade-phase'])
def test_incompatible_negative_phase_has_real_pcm_damage_and_rejects_atomically(tmp_path, duration, fade):
    media = source(tmp_path)
    editor = Editor(Project(width=16, height=16, fps_num=30000, fps_den=1001,
        media=[media], clips=[Clip('delete', media.id, 2, 0, 0, 1, muted=True),
            Clip('survivor', media.id, 2, 2, 0, duration, fade_in=fade)]))
    original = deepcopy(editor._clip('survivor'))
    naive = replace(original, start=1)
    old = pcm(solo(editor.project, original))[boundary(2, editor.project.fps):]
    altered = pcm(solo(editor.project, naive))[boundary(1, editor.project.fps):]
    if duration == 1:
        assert (len(old), len(altered)) == (1602, 1601)
        np.testing.assert_array_equal(old[:-1], altered)
        assert np.max(np.abs(old[-1])) > 0  # Lost real content, not zero padding.
    else:
        assert len(old) == len(altered) == 8008
        assert not np.array_equal(old, altered)
        assert np.count_nonzero(old[:1602] != altered[:1602]) > 300
        np.testing.assert_array_equal(old[1602:], altered[1602:])
    # Preserve redo and revision as well as every project record on rejection.
    editor.update_clip('survivor', gain_db=-1)
    assert editor.undo() and editor.can_redo
    before = deepcopy((editor.project, editor._undo, editor._redo, editor.revision))
    with pytest.raises(ValueError, match='sample|audio|phase|equivalen'):
        editor.ripple_delete_clip('delete')
    assert (editor.project, editor._undo, editor._redo, editor.revision) == before
    np.testing.assert_array_equal(pcm(solo(editor.project, editor._clip('survivor')))[boundary(2, editor.project.fps):], old)


@pytest.mark.parametrize('offset', [0, .137, -.113], ids=['zero', 'positive', 'negative'])
@pytest.mark.parametrize('speed', [Fraction(1), Fraction(3, 2)], ids=['1x', 'retimed'])
def test_equivalent_negative_phase_preserves_real_local_pcm_fades_offsets_retime_and_history(tmp_path, offset, speed):
    media = source(tmp_path, offset)
    mapping = ClipTimeMap(Fraction(0), 10 * speed, speed)
    original = Clip('survivor', media.id, 2, 7, 0, 10, time_map=mapping,
                    gain_db=-3, fade_in=1, fade_out=3)
    editor = Editor(Project(width=16, height=16, fps_num=30000, fps_den=1001,
        media=[media], clips=[Clip('delete', media.id, 2, 1, 0, 5, muted=True), original]))
    before = deepcopy(editor.project)
    digest = hashlib.sha256(Path(media.path).read_bytes()).hexdigest()
    old = pcm(editor.project)
    editor.ripple_delete_clip('delete')
    actual = pcm(editor.project)
    lo, hi = boundary(1, before.fps), boundary(6, before.fps)
    expected = np.concatenate((old[:lo], old[hi:]))
    np.testing.assert_array_equal(actual, expected)
    assert np.max(np.abs(actual)) > .001
    assert editor._clip('survivor') == replace(original, start=2)
    assert len(editor._undo) == 1 and editor.revision == 1
    saved = tmp_path / 'preserved.lumen'
    editor.project.save(saved)
    assert Project.load(saved) == editor.project
    assert editor.undo() and editor.project == before
    np.testing.assert_array_equal(pcm(editor.project), old)
    assert editor.redo()
    np.testing.assert_array_equal(pcm(editor.project), actual)
    assert hashlib.sha256(Path(media.path).read_bytes()).hexdigest() == digest


def test_negative_common_phase_counterexample_has_equal_local_pcm_but_incompatible_relative_shift(tmp_path):
    media = source(tmp_path)
    clips = [Clip('delete', media.id, 2, 1, 0, 1, muted=True),
             Clip('follower-two', media.id, 2, 2, 0, 5),
             Clip('follower-three', media.id, 3, 3, 10, 5)]
    editor = Editor(Project(width=16, height=16, fps_num=30000, fps_den=1001,
                            media=[media], clips=clips))
    shifts = []
    for original in clips[1:]:
        moved = replace(original, start=original.start - 1)
        old_timing = compile_audio_timing(original, editor.project.fps)
        new_timing = compile_audio_timing(moved, editor.project.fps)
        assert replace(old_timing, start_sample=0) == replace(new_timing, start_sample=0)
        assert old_timing.sample_count == new_timing.sample_count == 8008
        old_local = pcm(solo(editor.project, original))[boundary(original.start, editor.project.fps):]
        new_local = pcm(solo(editor.project, moved))[boundary(moved.start, editor.project.fps):]
        np.testing.assert_array_equal(new_local, old_local)
        shifts.append(boundary(moved.start, editor.project.fps) - boundary(original.start, editor.project.fps))
    assert shifts == [-1601, -1602]
    expected_shift = boundary(1, editor.project.fps) - boundary(2, editor.project.fps)
    assert expected_shift == -1601 and expected_shift != -boundary(1, editor.project.fps)
    before = deepcopy((editor.project, editor._undo, editor._redo, editor.revision))
    with pytest.raises(ValueError, match='sample|audio|phase|shift|synchron'):
        editor.plan_ripple_delete('delete', participating_tracks=(2, 3))
    assert (editor.project, editor._undo, editor._redo, editor.revision) == before
    # Legacy local-only contract is valid: the independently fixed track is not
    # falsely promised a common displacement.
    editor.ripple_delete_clip('delete')
    assert editor._clip('follower-three') == next(c for c in before[0].clips if c.id == 'follower-three')
    assert editor._clip('follower-two').start == 1


def test_incompatible_leading_silence_phase_rejects_even_with_equal_budget(tmp_path):
    media = source(tmp_path, .13701)
    original = Clip('survivor', media.id, 2, 2, 0, 5)
    editor = Editor(Project(width=16, height=16, fps_num=30000, fps_den=1001,
        media=[media], clips=[Clip('delete', media.id, 2, 0, 0, 1, muted=True), original]))
    moved = replace(original, start=1)
    old_timing = compile_audio_timing(original, editor.project.fps, media.audio_offset)
    new_timing = compile_audio_timing(moved, editor.project.fps, media.audio_offset)
    assert old_timing.sample_count == new_timing.sample_count == 8008
    assert (old_timing.leading_samples, new_timing.leading_samples) == (6577, 6576)
    old = pcm(solo(editor.project, original))[boundary(2, editor.project.fps):]
    altered = pcm(solo(editor.project, moved))[boundary(1, editor.project.fps):]
    assert not np.array_equal(old, altered)
    assert np.count_nonzero(old[:6577]) == np.count_nonzero(altered[:6576]) == 0
    assert np.max(np.abs(old[6577:])) > .01
    np.testing.assert_array_equal(old[6577:], altered[6576:-1])
    before = deepcopy((editor.project, editor._undo, editor._redo, editor.revision))
    with pytest.raises(ValueError, match='sample|audio|phase|equivalen'):
        editor.ripple_delete_clip('delete')
    assert (editor.project, editor._undo, editor._redo, editor.revision) == before

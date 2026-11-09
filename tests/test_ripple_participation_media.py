"""Real negative-shift picture and complete stereo-mix participation oracles.

Full renderer outputs consume both picture and sound. Expected mixes splice the
OLD full mix, or independently captured OLD track contributions in stable track
order; they are not reconstructed from the operation's resulting clip records.
"""
from copy import deepcopy
from fractions import Fraction
import hashlib
from pathlib import Path
import subprocess
import wave

import numpy as np
import pytest

from lumen.media import ffmpeg_available, probe
from lumen.model import Clip, Editor, Project
from test_exact_audio_cache_backend import production_pcm
from test_retime_video import (WIDTH, HEIGHT, assert_delivery_frames, decode,
                               engine, ff, make_source)
from test_ripple_preservation_media import boundary

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def av_input(root, fps, offset, seed):
    path = root / f'source-{seed}.mkv'
    audio = root / f'source-{seed}.wav'
    raw = np.random.default_rng(seed).integers(-16000, 16001, 48000 * 2, dtype=np.int16)
    with wave.open(str(audio), 'wb') as stream:
        stream.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        stream.writeframes(raw.astype('<i2').tobytes())
    ff('-f', 'lavfi', '-i', f'testsrc2=size=16x16:rate={fps}:duration=2',
       '-itsoffset', str(offset), '-i', audio, '-map', '0:v', '-map', '1:a',
       '-c:v', 'ffv1', '-c:a', 'pcm_s16le', path)
    media = probe(path)
    assert media.has_audio and media.has_video
    assert media.audio_offset == pytest.approx(offset, abs=1e-9)
    return media


def one_track(project, track):
    result = deepcopy(project)
    result.clips = [c for c in result.clips if c.track == track]
    result.captions = []
    result.overlays = []
    result.clip_groups = []
    result.video_transitions = []
    return result


def make_mix_project(root, fps, offset):
    a = av_input(root, fps, offset, 1931)
    b = av_input(root, fps, -.113 if offset > 0 else .137, 7589)
    return Project(width=16, height=16, fps_num=fps.numerator, fps_den=fps.denominator,
        media=[a, b], clips=[
            Clip('target-before', a.id, 0, 0, 20, 5, gain_db=-6),
            Clip('delete', b.id, 0, 5, 25, 5, gain_db=-3),
            Clip('target-later', a.id, 0, 10, 0, 10, gain_db=-6, fade_in=1, fade_out=2),
            Clip('v1-before', b.id, 1, 0, 30, 3, gain_db=-9),
            Clip('v1-later', b.id, 1, 12, 4, 10, gain_db=-9, fade_in=2, fade_out=3),
            Clip('a0-before', b.id, 2, 1, 35, 3, gain_db=-7),
            Clip('a0-later', a.id, 2, 15, 5, 10, gain_db=-7, fade_in=3, fade_out=1),
            Clip('a1-before', a.id, 3, 2, 40, 3, gain_db=-11),
            Clip('a1-later', b.id, 3, 10, 15, 10, gain_db=-11, fade_in=1, fade_out=3)])


def unchanged_payloads(before, after, participants):
    current = {c.id: c for c in after.clips}
    assert set(current) == {c.id for c in before.clips} - {'delete'}
    for original in before.clips:
        if original.id == 'delete':
            continue
        expected = deepcopy(original)
        if original.track in participants and original.start >= 10:
            expected.start -= 5
        assert current[original.id] == expected
    for name in vars(before):
        if name != 'clips':
            assert getattr(after, name) == getattr(before, name)


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30000, 1001)], ids=['24', '29.97'])
@pytest.mark.parametrize('offset', [.137, -.113], ids=['positive-offset', 'negative-offset'])
def test_entire_four_track_rendered_pcm_is_exact_old_mix_splice_with_master_gain(tmp_path, engine, fps, offset):
    project = make_mix_project(tmp_path, fps, offset)
    project.master_gain_db = -1
    editor = Editor(project)
    before = deepcopy(project)
    digests = {m.path: hashlib.sha256(Path(m.path).read_bytes()).hexdigest() for m in project.media}
    old = production_pcm(engine, project, tmp_path, 'old-whole')
    lo, hi = boundary(5, fps) * 8, boundary(10, fps) * 8
    expected = old[:lo] + old[hi:]
    assert len(old) == boundary(25, fps) * 8
    assert np.max(np.abs(np.frombuffer(old[hi:], dtype='<f4'))) > .02
    editor.apply_ripple_delete(editor.plan_ripple_delete('delete', participating_tracks=(0, 1, 2, 3)))
    actual = production_pcm(engine, project, tmp_path, 'new-whole')
    assert actual == expected  # Every stereo f32 byte, without downmix/tolerance.
    assert len(actual) == boundary(20, fps) * 8
    unchanged_payloads(before, project, (0, 1, 2, 3))
    path = tmp_path / 'whole-mix.lumen'
    project.save(path)
    assert Project.load(path) == project
    assert len(editor._undo) == 1 and editor.undo() and editor.project == before
    assert production_pcm(engine, editor.project, tmp_path, 'undo-whole') == old
    assert editor.redo()
    assert production_pcm(engine, editor.project, tmp_path, 'redo-whole') == actual
    for path, digest in digests.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30000, 1001)], ids=['24', '29.97'])
@pytest.mark.parametrize('offset', [.137, -.113], ids=['positive-offset', 'negative-offset'])
def test_partial_complete_mix_equals_shifted_and_fixed_original_tracks_and_retains_fixed_extent(tmp_path, engine, fps, offset):
    project = make_mix_project(tmp_path, fps, offset)
    editor = Editor(project)
    before = deepcopy(project)
    old = {track: np.frombuffer(production_pcm(engine, one_track(project, track), tmp_path,
            f'old-{track}'), dtype='<f4').reshape(-1, 2).copy() for track in range(4)}
    lo, hi, end = boundary(5, fps), boundary(10, fps), boundary(22, fps)
    expected = np.zeros((end, 2), dtype=np.float32)
    for track in range(4):
        contribution = old[track]
        if track in (0, 2):
            contribution = np.concatenate((contribution[:lo], contribution[hi:]))
        # At most one clip per track contributes to a sample. Stable track
        # order is the original renderer summation order, not a reassociation.
        expected[:len(contribution)] += contribution
    editor.apply_ripple_delete(editor.plan_ripple_delete('delete', participating_tracks=(0, 2)))
    actual = production_pcm(engine, project, tmp_path, 'partial-whole')
    assert actual == expected.astype('<f4').tobytes()
    assert project.duration_frames == 22 != before.duration_frames - 5
    unchanged_payloads(before, project, (0, 2))
    for track in (1, 3):
        assert production_pcm(engine, one_track(project, track), tmp_path, f'fixed-{track}') == old[track].astype('<f4').tobytes()


@pytest.fixture(scope='module')
def numbered_sources(tmp_path_factory):
    root = tmp_path_factory.mktemp('ripple-numbered-source')
    return {fps: make_source(root / f'{fps.numerator}-{fps.denominator}.mkv', fps)
            for fps in (Fraction(24), Fraction(30000, 1001))}


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30000, 1001)], ids=['24', '29.97'])
@pytest.mark.parametrize('follow', [False, True], ids=['explicit-fixed', 'participating'])
def test_actual_picture_ids_delete_only_target_and_translate_only_checked_tracks(tmp_path, numbered_sources, engine, fps, follow):
    source = numbered_sources[fps]
    project = Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator, fps_den=fps.denominator,
        media=[deepcopy(source.media)], clips=[
            Clip('target-before', source.media.id, 0, 0, 0, 5),
            Clip('delete', source.media.id, 0, 5, 10, 5),
            Clip('target-later', source.media.id, 0, 20, 20, 10),
            Clip('follower-before', source.media.id, 1, 0, 40, 5),
            Clip('follower-later', source.media.id, 1, 10, 50, 10)])
    old_target = np.zeros((30, HEIGHT, WIDTH, 3), np.uint16)
    old_target[0:5] = source.original[0:5]
    old_target[5:10] = source.original[10:15]
    old_target[20:30] = source.original[20:30]
    old_follower = np.zeros((20, HEIGHT, WIDTH, 3), np.uint16)
    old_follower[0:5] = source.original[40:45]
    old_follower[10:20] = source.original[50:60]
    for track, expected in ((0, old_target), (1, old_follower)):
        assert_delivery_frames(decode(engine.preview(one_track(project, track)), rgb16=False), expected)
    editor = Editor(project)
    editor.apply_ripple_delete(editor.plan_ripple_delete('delete', participating_tracks=(0, 1) if follow else (0,)))
    new_target = np.concatenate((old_target[:5], old_target[10:]))
    new_follower = np.concatenate((old_follower[:5], old_follower[10:])) if follow else old_follower
    for track, expected in ((0, new_target), (1, new_follower)):
        assert_delivery_frames(decode(engine.preview(one_track(project, track)), rgb16=False), expected)


def test_planning_whole_participants_never_invokes_process_or_downstream_decode(tmp_path, monkeypatch):
    # The source may be long; no proof budget should force a full decode merely
    # to compare the already-committed local compiler contract of whole clips.
    project = make_mix_project(tmp_path, Fraction(24), .137)
    editor = Editor(project)
    def forbidden(*args, **kwargs):
        raise AssertionError('Whole-clip ripple planning must not start any subprocess/decode')
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'run', forbidden)
        patch.setattr(subprocess, 'Popen', forbidden)
        plan = editor.plan_ripple_delete('delete', participating_tracks=(0, 1, 2, 3))
        editor.apply_ripple_delete(plan)
    assert editor.project.duration_frames == 20

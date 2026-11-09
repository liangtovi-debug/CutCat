"""Real single-gap closure: independent old-scene/old-PCM splice oracles.

These tests never derive expected source selections or sample timing from the
new project's clip records. Source pictures encode frame IDs; complete stereo
f32 mixes are captured before editing and spliced at independently rounded
absolute sample boundaries. All media is synthetic and local.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import hashlib
from pathlib import Path
import subprocess
import wave

import numpy as np
import pytest

from lumen.audio_timing import compile_audio_timing
from lumen.clip_audio_translation import guard_local_audio_translation
from lumen.media import ffmpeg_available, probe
from lumen.model import Caption, CaptionStyle, Clip, Editor, OverlayKeyframe, Project
from lumen.timing import ClipTimeMap
from test_caption_render import latin_font
from test_exact_audio_cache_backend import production_pcm
from test_overlay_render import sticker
from test_retime_video import (HEIGHT, WIDTH, assert_delivery_frames, decode,
                               engine, ff, make_source)
from test_ripple_participation_media import one_track
from test_ripple_preservation_media import boundary, pcm, solo, source
from test_source_insert_backend import pcm_file

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
RATES = [Fraction(24), Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001)]
RATE_IDS = ['24', '23.976', '29.97', '59.94']


def gap_av_input(root, fps, offset, seed):
    """Distinct stereo noise, real embedded signed offset, lossless picture/audio."""
    path = root / f'stereo-{seed}.mkv'
    audio = root / f'stereo-{seed}.wav'
    raw = np.random.default_rng(seed).integers(-16000, 16001, (48000 * 2, 2), dtype=np.int16)
    raw[::421, 0] = 21000
    raw[::683, 1] = -22000
    with wave.open(str(audio), 'wb') as stream:
        stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        stream.writeframes(raw.astype('<i2').tobytes())
    ff('-f', 'lavfi', '-i', f'testsrc2=size=16x16:rate={fps}:duration=2',
       '-itsoffset', str(offset), '-i', audio, '-map', '0:v', '-map', '1:a',
       '-c:v', 'ffv1', '-c:a', 'pcm_s16le', path)
    media = probe(path)
    assert media.has_audio and media.has_video
    assert media.audio_offset == pytest.approx(offset, abs=1e-9)
    return media


def mixture(root, fps, offset, speed=Fraction(1)):
    a = gap_av_input(root, fps, offset, 8101)
    b = gap_av_input(root, fps, -.113 if offset > 0 else .137, 8102)
    mapping = ClipTimeMap(Fraction(0), 10 * speed, speed)
    return Project(width=16, height=16, fps_num=fps.numerator, fps_den=fps.denominator,
        media=[a, b], clips=[
            Clip('target-left', a.id, 0, 0, 20, 5, gain_db=-6),
            Clip('target-right', a.id, 0, 10, 0, 10, time_map=mapping,
                 gain_db=-6, fade_in=1, fade_out=2),
            Clip('target-later', a.id, 0, 25, 35, 5, gain_db=-6),
            Clip('v1-before', b.id, 1, 0, 30, 3, gain_db=-9),
            Clip('v1-later', b.id, 1, 12, 4, 10, gain_db=-9, fade_in=2, fade_out=3),
            Clip('a0-before', b.id, 2, 1, 35, 3, gain_db=-7),
            Clip('a0-later', a.id, 2, 15, 5, 10, gain_db=-7, fade_in=3, fade_out=1),
            Clip('a1-before', a.id, 3, 2, 40, 3, gain_db=-11),
            Clip('a1-later', b.id, 3, 10, 15, 10, gain_db=-11, fade_in=1, fade_out=3)])


def complete_records(before, after, tracks, captions=False, stickers=False):
    """Zero deletions and no source/identity/hidden-field changes."""
    for name, participates in (
            ('clips', lambda item: item.track in tracks),
            ('captions', lambda item: captions), ('overlays', lambda item: stickers)):
        old = getattr(before, name)
        actual = {item.id: item for item in getattr(after, name)}
        assert len(actual) == len(old) and set(actual) == {item.id for item in old}
        for original in old:
            expected = deepcopy(original)
            if participates(original) and original.start >= 10:
                expected.start -= 5
            assert actual[original.id] == expected
    for name in vars(before):
        if name not in {'clips', 'captions', 'overlays'}:
            assert getattr(before, name) == getattr(after, name)


@pytest.mark.parametrize('fps', RATES, ids=RATE_IDS)
@pytest.mark.parametrize('offset', [.137, -.113], ids=['positive-offset', 'negative-offset'])
@pytest.mark.parametrize('speed', [Fraction(1), Fraction(3, 2)], ids=['1x', 'retimed'])
def test_entire_four_track_pcm_equals_old_mix_splice_fades_offsets_retime_and_history(
        tmp_path, engine, fps, offset, speed):
    project = mixture(tmp_path, fps, offset, speed)
    project.master_gain_db = -1
    editor = Editor(project)
    before = deepcopy(project)
    digests = {m.path: hashlib.sha256(Path(m.path).read_bytes()).hexdigest() for m in project.media}
    old = production_pcm(engine, project, tmp_path, 'old-whole')
    channels = np.frombuffer(old, dtype='<f4').reshape(-1, 2)
    assert np.any(channels[:, 0] != channels[:, 1]), 'The whole-mix oracle must exercise distinct stereo channels'
    lo, hi = boundary(5, fps) * 8, boundary(10, fps) * 8
    expected = old[:lo] + old[hi:]
    assert len(old) == boundary(30, fps) * 8
    assert not any(old[lo:hi]), 'The independently captured original gap must actually be silent'
    assert np.max(np.abs(np.frombuffer(old[hi:], dtype='<f4'))) > .02
    plan = editor.plan_gap_close(0, 7, participating_tracks=(0, 1, 2, 3))
    assert (plan.left_clip_id, plan.right_clip_id, plan.start, plan.end) == ('target-left', 'target-right', 5, 10)
    editor.apply_gap_close(plan)
    actual = production_pcm(engine, project, tmp_path, 'new-whole')
    assert actual == expected  # Every stereo f32 byte, no tolerance/downmix.
    assert len(actual) == boundary(25, fps) * 8
    complete_records(before, project, (0, 1, 2, 3))
    path = tmp_path / 'closed.lumen'
    project.save(path)
    assert Project.load(path) == project
    assert len(editor._undo) == 1 and editor.undo() and editor.project == before
    assert production_pcm(engine, editor.project, tmp_path, 'undo-whole') == old
    assert editor.redo()
    assert production_pcm(engine, editor.project, tmp_path, 'redo-whole') == actual
    for path, digest in digests.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize('fps', RATES, ids=RATE_IDS)
def test_partial_pcm_is_sum_of_spliced_and_fixed_old_tracks_and_uses_actual_extent(tmp_path, engine, fps):
    project = mixture(tmp_path, fps, .137)
    next(c for c in project.clips if c.id == 'v1-later').start = 18  # fixed extent 28
    before = deepcopy(project)
    old = {track: np.frombuffer(production_pcm(engine, one_track(project, track), tmp_path,
        f'old-track-{track}'), dtype='<f4').reshape(-1, 2).copy() for track in range(4)}
    lo, hi, end = boundary(5, fps), boundary(10, fps), boundary(28, fps)
    expected = np.zeros((end, 2), dtype=np.float32)
    for track in range(4):
        contribution = old[track]
        if track in (0, 2):
            contribution = np.concatenate((contribution[:lo], contribution[hi:]))
        expected[:len(contribution)] += contribution  # Original stable track summation order.
    editor = Editor(project)
    editor.apply_gap_close(editor.plan_gap_close(0, 5, participating_tracks=(0, 2)))
    assert production_pcm(engine, project, tmp_path, 'new-mix') == expected.astype('<f4').tobytes()
    assert project.duration_frames == 28 != before.duration_frames - 5
    complete_records(before, project, (0, 2))
    for track in (1, 3):
        assert production_pcm(engine, one_track(project, track), tmp_path,
                              f'fixed-track-{track}') == old[track].astype('<f4').tobytes()


@pytest.fixture(scope='module')
def gap_numbered_sources(tmp_path_factory):
    root = tmp_path_factory.mktemp('gap-numbered-source')
    return {fps: make_source(root / f'{fps.numerator}-{fps.denominator}.mkv', fps) for fps in RATES}


@pytest.mark.parametrize('fps', RATES, ids=RATE_IDS)
@pytest.mark.parametrize('follow', [False, True], ids=['upper-fixed', 'upper-follows'])
def test_numbered_preview_and_export_remove_only_one_gap_keep_clips_and_later_gap(
        tmp_path, gap_numbered_sources, engine, fps, follow):
    footage = gap_numbered_sources[fps]
    source_path = Path(footage.media.path)
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    project = Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator, fps_den=fps.denominator,
        media=[deepcopy(footage.media)], clips=[
            Clip('left', footage.media.id, 0, 0, 0, 5),
            Clip('right', footage.media.id, 0, 10, 20, 5),
            Clip('later', footage.media.id, 0, 20, 30, 5),
            Clip('upper-early', footage.media.id, 1, 0, 40, 3),
            Clip('upper-later', footage.media.id, 1, 17, 50, 3)])
    before = deepcopy(project)
    old_target = np.zeros((25, HEIGHT, WIDTH, 3), np.uint16)
    old_target[:5] = footage.original[:5]
    old_target[10:15] = footage.original[20:25]
    old_target[20:25] = footage.original[30:35]
    old_combined = old_target.copy()
    old_combined[:3] = footage.original[40:43]
    old_combined[17:20] = footage.original[50:53]
    assert_delivery_frames(decode(engine.preview(project), rgb16=False), old_combined)
    editor = Editor(project)
    editor.apply_gap_close(editor.plan_gap_close(0, 9, participating_tracks=(0, 1) if follow else (0,)))
    new_target = np.concatenate((old_target[:5], old_target[10:]))
    expected = new_target.copy()
    expected[:3] = footage.original[40:43]
    if follow:
        expected[12:15] = footage.original[50:53]
    else:
        expected[17:20] = footage.original[50:53]
    for path in (engine.preview(project), engine.render(project, tmp_path / 'delivery.mp4')):
        assert_delivery_frames(decode(path, rgb16=False), expected)
    # The target's second, unchosen [15,20) gap survives as [10,15).
    assert_delivery_frames(decode(engine.preview(one_track(project, 0)), rgb16=False), new_target)
    assert new_target[10:15].max() == 0
    complete_records(before, project, (0, 1) if follow else (0,))
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == digest


def lossless_scene(engine, project, path):
    inputs, graph, _ = engine._compile(project)
    ff(*inputs, '-filter_complex_threads', '1', '-filter_complex', graph,
       '-map', '[vout]', '-map', '[aout]', '-c:v', 'ffv1', '-c:a', 'pcm_f32le', path)
    frames = decode(path, rgb16=False)
    path.unlink()
    return frames


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30000, 1001)], ids=['24', '29.97'])
@pytest.mark.parametrize('follow_captions,follow_stickers', [(False, False), (True, False),
    (False, True), (True, True)], ids=['both-fixed', 'captions-follow', 'stickers-follow', 'both-follow'])
def test_actual_caption_and_animated_sticker_toggles_preserve_old_pixels_and_assets(
        tmp_path, engine, latin_font, fps, follow_captions, follow_stickers):
    media = pcm_file(tmp_path / 'silent-target.wav', 2)
    early, _ = sticker(tmp_path, name='early.png', start=0, duration=5)
    later, _ = sticker(tmp_path, name='later.png', start=20, duration=5,
        keyframes=[OverlayKeyframe(0, 0, 0, 1, 1), OverlayKeyframe(4, 20, 5, 1, .6)])
    project = Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator, fps_den=fps.denominator,
        media=[media], clips=[Clip('left', media.id, 2, 0, 0, 5, muted=True),
                             Clip('right', media.id, 2, 10, 5, 5, muted=True),
                             Clip('tail', media.id, 2, 25, 10, 5, muted=True)],
        captions=[Caption('early', 0, 5, 'EARLY'), Caption('later', 20, 5, 'LATER')],
        overlays=[early, later], caption_style=CaptionStyle(font_family=latin_font,
            font_size=10, margin_bottom=2, outline_width=0))
    before = deepcopy(project)
    assets = {Path(o.asset_path): hashlib.sha256(Path(o.asset_path).read_bytes()).hexdigest() for o in project.overlays}
    def category_pixels(state, category, name):
        isolated = deepcopy(state)
        isolated.clips = []
        if category == 'caption':
            isolated.overlays = []
        else:
            isolated.captions = []
        return lossless_scene(engine, isolated, tmp_path / f'{name}-{category}.mkv')
    old = {category: category_pixels(project, category, 'old') for category in ('caption', 'sticker')}
    old_combined = lossless_scene(engine, project, tmp_path / 'old-combined.mkv')
    for value in old.values():
        assert value[:5].max() > 200 and value[20:25].max() > 100
        assert value[5:20].max() == 0
    editor = Editor(project)
    editor.apply_gap_close(editor.plan_gap_close(2, 7, follow_captions=follow_captions,
                                                follow_stickers=follow_stickers))
    for category, follows in (('caption', follow_captions), ('sticker', follow_stickers)):
        expected = np.concatenate((old[category][:5], old[category][10:])) if follows else old[category]
        np.testing.assert_array_equal(category_pixels(project, category, 'new'), expected)
    if follow_captions == follow_stickers:
        expected_combined = (np.concatenate((old_combined[:5], old_combined[10:]))
                             if follow_captions else old_combined[:25])
    else:
        # One late layer moves and one remains fixed: their new gates do not
        # overlap. Combine independently rendered OLD layer pixels, not new
        # project metadata or new layer output.
        expected_combined = np.zeros((25, HEIGHT, WIDTH, 3), dtype=np.uint8)
        expected_combined[:5] = old_combined[:5]
        caption_start = 15 if follow_captions else 20
        sticker_start = 15 if follow_stickers else 20
        expected_combined[caption_start:caption_start + 5] = old['caption'][20:25]
        expected_combined[sticker_start:sticker_start + 5] = old['sticker'][20:25]
    np.testing.assert_array_equal(lossless_scene(engine, project, tmp_path / 'new-combined.mkv'),
                                  expected_combined)
    complete_records(before, project, (2,), follow_captions, follow_stickers)
    saved = tmp_path / 'categories.lumen'
    project.save(saved)
    assert Project.load(saved) == project
    assert len(editor._undo) == 1 and editor.undo() and editor.project == before
    assert editor.redo()
    for path, digest in assets.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_single_track_equal_local_pcm_but_unequal_common_shift_rejects_before_mutation(tmp_path):
    media = source(tmp_path)
    clips = [Clip('left', media.id, 2, 0, 0, 1, muted=True),
             Clip('right', media.id, 2, 2, 0, 5), Clip('later', media.id, 2, 8, 10, 5)]
    editor = Editor(Project(width=16, height=16, fps_num=30000, fps_den=1001, media=[media], clips=clips))
    shifts = []
    for original in clips[1:]:
        moved = replace(original, start=original.start - 1)
        guard_local_audio_translation(original, moved, editor.project, operation='test')
        old_timing = compile_audio_timing(original, editor.project.fps)
        new_timing = compile_audio_timing(moved, editor.project.fps)
        assert old_timing.sample_count == new_timing.sample_count == 8008
        old_local = pcm(solo(editor.project, original))[boundary(original.start, editor.project.fps):]
        moved_local = pcm(solo(editor.project, moved))[boundary(moved.start, editor.project.fps):]
        np.testing.assert_array_equal(old_local, moved_local)
        shifts.append(boundary(moved.start, editor.project.fps) - boundary(original.start, editor.project.fps))
    assert shifts == [-1601, -1602]
    assert boundary(1, editor.project.fps) - boundary(2, editor.project.fps) == -1601
    old = pcm(editor.project)
    naive = deepcopy(editor.project)
    for clip in naive.clips[1:]:
        clip.start -= 1
    expected = np.concatenate((old[:boundary(1, editor.project.fps)], old[boundary(2, editor.project.fps):]))
    altered = pcm(naive)
    assert not np.array_equal(altered, expected), 'Local PCM equality alone must not justify whole-mix equality'
    before = deepcopy((editor.project, editor._undo, editor._redo, editor.revision))
    with pytest.raises(ValueError, match='sample|audio|phase|shift|synchron'):
        editor.plan_gap_close(2, 1)
    assert (editor.project, editor._undo, editor._redo, editor.revision) == before


@pytest.mark.parametrize('duration,fade,offset', [(1, 0, 0), (5, 1, 0), (5, 0, .13701)],
                         ids=['sample-budget', 'fade-phase', 'leading-silence'])
def test_real_local_pcm_damage_rejects_single_gap_atomically(tmp_path, duration, fade, offset):
    media = source(tmp_path, offset)
    right = Clip('right', media.id, 2, 2, 0, duration, fade_in=fade)
    editor = Editor(Project(width=16, height=16, fps_num=30000, fps_den=1001,
        media=[media], clips=[Clip('left', media.id, 2, 0, 0, 1, muted=True), right]))
    old = pcm(solo(editor.project, right))[boundary(2, editor.project.fps):]
    altered = pcm(solo(editor.project, replace(right, start=1)))[boundary(1, editor.project.fps):]
    assert not np.array_equal(old, altered)
    if offset:
        assert np.count_nonzero(old[:6577]) == np.count_nonzero(altered[:6576]) == 0
        assert np.max(np.abs(old[6577:])) > .01
    editor.update_clip('right', gain_db=-1)
    assert editor.undo() and editor.can_redo
    before = deepcopy((editor.project, editor._undo, editor._redo, editor.revision))
    with pytest.raises(ValueError, match='sample|audio|phase|equivalen'):
        editor.plan_gap_close(2, 1)
    assert (editor.project, editor._undo, editor._redo, editor.revision) == before


def test_whole_long_clip_gap_plan_apply_never_start_process_or_source_decode(tmp_path, monkeypatch):
    media = pcm_file(tmp_path / 'long-source.wav', 66)
    project = Project(width=16, height=16, fps_num=30, media=[media], clips=[
        Clip('left', media.id, 2, 0, 0, 5),
        Clip('long-right', media.id, 2, 10, 10, 1080, gain_db=-2, fade_in=3, fade_out=4)])
    editor = Editor(project)
    digest = hashlib.sha256(Path(media.path).read_bytes()).hexdigest()
    def forbidden(*args, **kwargs):
        raise AssertionError('Whole-gap translation must not start any subprocess or source decode')
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'run', forbidden)
        patch.setattr(subprocess, 'Popen', forbidden)
        editor.apply_gap_close(editor.plan_gap_close(2, 5))
    assert editor.project.duration_frames == 1085
    assert len(editor.project.clips) == 2 and editor._clip('long-right').start == 5
    assert hashlib.sha256(Path(media.path).read_bytes()).hexdigest() == digest

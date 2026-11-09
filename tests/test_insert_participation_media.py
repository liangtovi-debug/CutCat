"""Cross-track Insert: independent pictures and exact per-contribution PCM.

All inputs are tiny synthetic local files. Expected displacement is constructed
from original source arrays and independently rounded absolute sample positions,
never from Insert's changed records or its production timing plan. Full renderer
outputs consume both picture and sound, retaining actual export scheduling.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import hashlib
import math
from pathlib import Path
import time

import numpy as np
import pytest

from lumen.audio_mix import AudioMixBuilder
from lumen.audio_timing import compile_audio_timing
from lumen.media import ffmpeg_available, probe
from lumen.model import Clip, Editor, Project
from lumen.source_viewer import SourcePreviewBackend, SourceViewerSession, _probe
from test_retime_video import (WIDTH, HEIGHT, assert_delivery_frames, decode,
                               engine, make_source)
from test_roll_media import exact_pcm
from test_source_overwrite_media import pcm_source, put_pcm, sample
from test_exact_audio_cache_backend import (av_source, copied_short_aac,
    origin_oracle, production_pcm, selected_oracle)

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


@pytest.fixture(scope='module')
def numbered_sources(tmp_path_factory):
    root = tmp_path_factory.mktemp('participation-original-frame-ids')
    return {fps: make_source(root / f'{fps.numerator}-{fps.denominator}.mkv', fps)
            for fps in (Fraction(24), Fraction(30000, 1001))}


def one_track(project, track):
    """Observe one original contribution without other tracks hiding its pixels."""
    isolated = deepcopy(project)
    isolated.clips = [c for c in isolated.clips if c.track == track]
    isolated.captions = []
    isolated.overlays = []
    isolated.clip_groups = []
    isolated.video_transitions = []
    return isolated


def splice_pcm(old, fps, cut, delta):
    lo, hi = sample(cut, fps), sample(cut + delta, fps)
    padded = np.pad(old, (0, max(0, lo - len(old))))
    return np.concatenate((padded[:lo], np.zeros(hi - lo, np.float32), padded[lo:]))


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30000, 1001)], ids=['24', '29.97'])
@pytest.mark.parametrize('follow', [False, True], ids=['explicit-fixed', 'participating'])
def test_each_picture_track_actual_frames_follow_only_explicit_choice(
        tmp_path, numbered_sources, engine, fps, follow):
    source = numbered_sources[fps]
    project = Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
        fps_den=fps.denominator, media=[deepcopy(source.media)], clips=[
            Clip('destination', source.media.id, 0, 10, 5, 20),
            Clip('destination-later', source.media.id, 0, 40, 25, 10),
            Clip('follower-before', source.media.id, 1, 0, 50, 5),
            Clip('follower-after', source.media.id, 1, 40, 60, 5)])
    editor = Editor(project)
    before = deepcopy(project)
    digest = hashlib.sha256(Path(source.media.path).read_bytes()).hexdigest()
    old_target = np.zeros((50, HEIGHT, WIDTH, 3), np.uint16)
    old_target[10:30] = source.original[5:25]
    old_target[40:50] = source.original[25:35]
    old_follower = np.zeros((45, HEIGHT, WIDTH, 3), np.uint16)
    old_follower[0:5] = source.original[50:55]
    old_follower[40:45] = source.original[60:65]
    for track, expected in ((0, old_target), (1, old_follower)):
        assert_delivery_frames(decode(engine.preview(one_track(project, track)), rgb16=False), expected)
    plan = editor.plan_source_insert(source.media.id, 0, 15, 10, 20,
                                    participating_tracks=(0, 1) if follow else (0,))
    editor.apply_source_insert(plan)
    new_target = np.concatenate((old_target[:15], source.original[10:20], old_target[15:]))
    new_follower = old_follower
    if follow:
        new_follower = np.concatenate((old_follower[:15],
            np.zeros((10, HEIGHT, WIDTH, 3), np.uint16), old_follower[15:]))
    for track, expected in ((0, new_target), (1, new_follower)):
        assert_delivery_frames(decode(engine.preview(one_track(project, track)), rgb16=False), expected)
    if not follow:
        assert [c for c in project.clips if c.track == 1] == [c for c in before.clips if c.track == 1]
    path = tmp_path / 'cross-track.lumen'
    project.save(path)
    assert Project.load(path) == project
    assert len(editor._undo) == 1 and editor.undo() and editor.project == before
    assert editor.redo()
    assert hashlib.sha256(Path(source.media.path).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize('fps', [Fraction(25), Fraction(30000, 1001)], ids=['25', '29.97'])
@pytest.mark.parametrize('offset', [0, .137, -.113], ids=['aligned', 'positive-offset', 'negative-offset'])
@pytest.mark.parametrize('follow', [False, True], ids=['fixed-audio', 'following-audio'])
def test_each_audio_track_pcm_has_one_common_delta_with_fades_and_offsets(
        tmp_path, engine, fps, offset, follow):
    media, raw = pcm_source(tmp_path, offset)
    project = Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
        fps_den=fps.denominator, media=[media], clips=[
            Clip('target', media.id, 2, 10, 5, 20),
            Clip('target-later', media.id, 2, 40, 25, 10),
            Clip('follower-before', media.id, 3, 0, 50, 5),
            Clip('follower-fade', media.id, 3, 35, 20, 10,
                 gain_db=-3, fade_in=1, fade_out=2)])
    editor = Editor(project)
    before = deepcopy(project)
    digest = hashlib.sha256(Path(media.path).read_bytes()).hexdigest()
    old = {track: exact_pcm(engine, one_track(project, track)) for track in (2, 3)}
    target_expected = np.zeros(sample(50, fps), np.float32)
    for start, origin, count in ((10, 5, 20), (40, 25, 10)):
        put_pcm(target_expected, raw, fps, start, origin, count, offset)
    np.testing.assert_allclose(old[2], target_expected, rtol=3e-7, atol=3e-7)
    # Validate that this is a non-silent real fade, not just a metadata test.
    lo, hi = sample(35, fps), sample(45, fps)
    assert old[3][lo] == 0 and np.max(np.abs(old[3][lo + 100:hi - 100])) > .01
    plan = editor.plan_source_insert(media.id, 2, 15, 10, 20,
                                    participating_tracks=(2, 3) if follow else (2,))
    editor.apply_source_insert(plan)
    target = splice_pcm(old[2], fps, 15, 10)
    put_pcm(target, raw, fps, 15, 10, 10, offset)
    follower = splice_pcm(old[3], fps, 15, 10) if follow else old[3]
    actual = {track: exact_pcm(engine, one_track(project, track)) for track in (2, 3)}
    np.testing.assert_allclose(actual[2], target, rtol=3e-7, atol=3e-7)
    np.testing.assert_array_equal(actual[3], follower)
    lo, hi = sample(15, fps), sample(25, fps)
    for track in ((2, 3) if follow else (2,)):
        np.testing.assert_array_equal(actual[track][:lo], old[track][:lo])
        np.testing.assert_array_equal(actual[track][hi:], old[track][lo:])
    if not follow:
        assert [c for c in project.clips if c.track == 3] == [c for c in before.clips if c.track == 3]
    moved = next(c for c in project.clips if c.id == 'follower-fade')
    assert (moved.gain_db, moved.fade_in, moved.fade_out) == (-3, 1, 2)
    assert len(editor._undo) == 1 and editor.undo() and editor.project == before
    assert editor.redo()
    assert hashlib.sha256(Path(media.path).read_bytes()).hexdigest() == digest


def test_real_pcm_exposes_one_sample_cross_track_phase_counterexample(tmp_path, engine):
    fps = Fraction(30000, 1001)
    media, _ = pcm_source(tmp_path, 0)
    originals = [Clip('start-one', media.id, 2, 1, 0, 5),
                 Clip('start-two', media.id, 3, 2, 10, 5)]
    project = Project(width=WIDTH, height=HEIGHT, fps_num=30000, fps_den=1001,
                      media=[media], clips=originals)
    old_audio, moved_audio = {}, {}
    displacements = []
    for clip in originals:
        moved = deepcopy(clip); moved.start += 1
        local = []
        for snapshot in (clip, moved):
            single = deepcopy(project); single.clips = [snapshot]
            timing = compile_audio_timing(snapshot, fps)
            fragment = AudioMixBuilder(single).clip_fragment(0, media).fragment
            local.append((replace(timing, start_sample=0), fragment.rsplit(',adelay=', 1)[0]))
        assert local[0] == local[1]  # Existing local guard alone cannot detect it.
        assert local[0][0].sample_count == 8008
        before = one_track(project, clip.track)
        after = deepcopy(before); after.clips[0] = moved
        old_audio[clip.id] = exact_pcm(engine, before)
        moved_audio[clip.id] = exact_pcm(engine, after)
        shift = sample(clip.start + 1, fps) - sample(clip.start, fps)
        displacements.append(shift)
        np.testing.assert_array_equal(moved_audio[clip.id][shift:], old_audio[clip.id])
    assert displacements == [1601, 1602]
    # The intended insertion's common shift is 1601; the second contribution's
    # old samples do not land at that common position in a naive frame move.
    second_start = sample(2, fps)
    assert not np.array_equal(moved_audio['start-two'][second_start + 1601:-1],
                              old_audio['start-two'][second_start:])
    editor = Editor(deepcopy(project))
    before = deepcopy((editor.project, editor._undo, editor._redo, editor.revision))
    with pytest.raises(ValueError, match='sample|audio|phase|shift|synchron'):
        editor.plan_source_insert(media.id, 2, 1, 30, 31, participating_tracks=(2, 3))
    assert (editor.project, editor._undo, editor._redo, editor.revision) == before
    # Historical this-track-only behavior remains accepted (the fixed other
    # track is not being promised synchronized displacement).
    default = editor.plan_source_insert(media.id, 2, 1, 30, 31)
    editor.apply_source_insert(default)
    assert next(c for c in editor.project.clips if c.id == 'start-two') == originals[1]


@pytest.mark.parametrize('offset', [.137, -.113], ids=['positive-offset', 'negative-offset'])
def test_late_embedded_aac_and_independent_audio_follow_same_exact_pcm_shift(tmp_path, engine, offset):
    path = av_source(tmp_path, offset=offset, origin=5)
    media = probe(path)
    project = Project(width=16, height=16, fps_num=25, media=[media], clips=[
        Clip('picture-split', media.id, 0, 10, 800, 30),
        Clip('audio-follower', media.id, 2, 45, 900, 10)])
    editor = Editor(project)
    session = SourceViewerSession(project, media.id)
    before = deepcopy(project)
    plan = editor.plan_source_insert(media.id, 0, 25, 850, 855, participating_tracks=(0, 2))
    backend = SourcePreviewBackend()
    try:
        old = {t: production_pcm(engine, one_track(project, t), tmp_path, f'late-old-{t}') for t in (0, 2)}
        identity = backend.inspect_frame(session).identity
        assert backend.verify_insert_sources(session, identity, project, plan)
        assert project == before
        editor.apply_source_insert(plan)
        new = {t: production_pcm(engine, one_track(project, t), tmp_path, f'late-new-{t}') for t in (0, 2)}
        lo, shift = 25 * 1920 * 8, 5 * 1920 * 8
        full = origin_oracle(path)
        assert new[0] == old[0][:lo] + selected_oracle(full, session, 850, 855) + old[0][lo:]
        assert new[2] == old[2][:lo] + bytes(shift) + old[2][lo:]
        assert new[0][lo + shift:] == old[0][lo:]
        assert new[2][lo + shift:] == old[2][lo:]
    finally:
        backend.close()


@pytest.mark.parametrize('rate,offset,origin', [(22050, .137, 5), (96000, -.113, 5)])
def test_copied_aac_real_eof_tail_follows_without_full_follower_decode(
        tmp_path, engine, rate, offset, origin, monkeypatch):
    path = copied_short_aac(tmp_path, rate, offset, origin)
    media = probe(path)
    project = Project(width=16, height=16, fps_num=25, media=[media], clips=[
        Clip('tail-split', media.id, 0, 0, 20, 15),
        Clip('tail-follower', media.id, 1, 20, 20, 15)])
    editor = Editor(project)
    session = SourceViewerSession(project, media.id)
    plan = editor.plan_source_insert(media.id, 0, 5, 24, 29, participating_tracks=(0, 1))
    backend = SourcePreviewBackend()
    try:
        full = origin_oracle(path)
        identity = backend.inspect_frame(session).identity
        info = _probe(session, identity, None, time.monotonic() + 60)
        declared = math.ceil(info['audio_duration'] * 48000)
        assert len(full) // 8 > declared
        assert np.max(np.abs(np.frombuffer(full[declared * 8:], dtype='<f4')), initial=0) > .001
        cfr_intervals = []
        prove = backend._prove_cfr
        def record_proof(request, identity, info, intervals, trim_ranges, cancel, deadline):
            cfr_intervals.extend(intervals)
            return prove(request, identity, info, intervals, trim_ranges, cancel, deadline)
        monkeypatch.setattr(backend, '_prove_cfr', record_proof)
        old = {t: production_pcm(engine, one_track(project, t), tmp_path, f'tail-old-{t}') for t in (0, 1)}
        assert backend.verify_insert_sources(session, identity, project, plan)
        assert cfr_intervals == [(20, 35)]  # Only selected+split source union.
        assert backend._audio_eof and next(iter(backend._audio_eof.values())) == len(full) // 8
        editor.apply_source_insert(plan)
        new = {t: production_pcm(engine, one_track(project, t), tmp_path, f'tail-new-{t}') for t in (0, 1)}
        lo, shift = 5 * 1920 * 8, 5 * 1920 * 8
        assert new[0] == old[0][:lo] + selected_oracle(full, session, 24, 29) + old[0][lo:]
        assert new[1] == old[1][:lo] + bytes(shift) + old[1][lo:]
    finally:
        backend.close()


@pytest.mark.parametrize('follow_captions,follow_stickers', [(False, False), (True, False),
    (False, True), (True, True)], ids=['both-fixed', 'captions-only', 'stickers-only', 'both-follow'])
def test_actual_caption_and_sticker_frames_follow_independent_toggles_atomically(
        tmp_path, engine, latin_font, follow_captions, follow_stickers):
    from lumen.model import Caption, CaptionStyle, OverlayKeyframe
    from test_overlay_render import sticker
    from test_retime_video import ff
    from test_source_insert_backend import pcm_file
    media = pcm_file(tmp_path / 'selection.wav', 1)
    early, _ = sticker(tmp_path, name='early.png', start=0, duration=5)
    later, _ = sticker(tmp_path, name='later.png', start=20, duration=5,
        keyframes=[OverlayKeyframe(0, 0, 0, 1, 1), OverlayKeyframe(4, 20, 5, 1, .6)])
    project = Project(width=WIDTH, height=HEIGHT, fps_num=30, media=[media],
        captions=[Caption('early', 0, 5, 'EARLY'), Caption('later', 20, 5, 'LATER')],
        overlays=[early, later], caption_style=CaptionStyle(font_family=latin_font,
            font_size=10, margin_bottom=2, outline_width=0))
    editor = Editor(project)
    before = deepcopy(project)
    assets = {Path(o.asset_path): hashlib.sha256(Path(o.asset_path).read_bytes()).hexdigest()
              for o in project.overlays}
    def pixels(state, category, name):
        observed = deepcopy(state)
        observed.clips = []
        if category == 'caption': observed.overlays = []
        else: observed.captions = []
        inputs, graph, _ = engine._compile(observed)
        path = tmp_path / f'{name}-{category}.mkv'
        ff(*inputs, '-filter_complex_threads', '1', '-filter_complex', graph,
           '-map', '[vout]', '-map', '[aout]', '-c:v', 'ffv1', '-c:a', 'pcm_f32le', path)
        result = decode(path, rgb16=False)
        path.unlink()
        return result
    old = {category: pixels(project, category, 'before') for category in ('caption', 'sticker')}
    for value in old.values():
        assert value[:5].max() > 200 and value[20:25].max() > 100
        assert value[5:20].max() == 0
    plan = editor.plan_source_insert(media.id, 2, 10, 0, 5,
        participating_tracks=(2,), follow_captions=follow_captions, follow_stickers=follow_stickers)
    editor.apply_source_insert(plan)
    for category, follows in (('caption', follow_captions), ('sticker', follow_stickers)):
        expected = old[category]
        if follows:
            expected = np.concatenate((expected[:10], np.zeros((5, HEIGHT, WIDTH, 3), np.uint8), expected[10:]))
        np.testing.assert_array_equal(pixels(project, category, 'after'), expected)
    assert project.captions[0] == before.captions[0]
    assert project.overlays[0] == before.overlays[0]
    assert project.overlays[1].keyframes == before.overlays[1].keyframes
    saved = tmp_path / 'categories.lumen'
    project.save(saved)
    assert Project.load(saved) == project
    assert len(editor._undo) == 1 and editor.undo() and editor.project == before
    assert editor.redo()
    for path, digest in assets.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


# Reuse the existing explicit, portable local-font fixture; it never downloads.
from test_caption_render import latin_font


def test_common_shift_preserves_target_split_and_follower_leading_silence(tmp_path, engine):
    fps = Fraction(25)
    media, raw = pcm_source(tmp_path, .137)
    project = Project(width=WIDTH, height=HEIGHT, fps_num=25, media=[media], clips=[
        Clip('split-before-onset', media.id, 2, 10, 0, 20),
        Clip('follower-before-onset', media.id, 3, 35, 0, 10)])
    editor = Editor(project)
    old = {track: exact_pcm(engine, one_track(project, track)) for track in (2, 3)}
    plan = editor.plan_source_insert(media.id, 2, 12, 10, 15, participating_tracks=(2, 3))
    editor.apply_source_insert(plan)
    expected = {track: splice_pcm(old[track], fps, 12, 5) for track in (2, 3)}
    put_pcm(expected[2], raw, fps, 12, 10, 5, .137)
    for track in (2, 3):
        actual = exact_pcm(engine, one_track(project, track))
        np.testing.assert_allclose(actual, expected[track], rtol=3e-7, atol=3e-7)
        np.testing.assert_array_equal(actual[sample(17, fps):], old[track][sample(12, fps):])
    # Right target remnant and whole follower retain silence to the exact
    # delayed source onset; it is not filled using the next source samples.
    shifted_follower_start = sample(40, fps)
    moved = exact_pcm(engine, one_track(project, 3))
    assert np.count_nonzero(moved[shifted_follower_start:shifted_follower_start + 6576]) == 0
    assert np.max(np.abs(moved[shifted_follower_start + 6576:])) > .01

"""Old-project-derived actual-media oracles for joint A/V edge trimming.

A trim intentionally selects a different source interval and edge-anchored fade
window. No retained-waveform, retime phase-continuity or lossless AAC claim is
made. 1x expectations slice original marked stereo samples and evaluate gains,
fades and sums independently. Retime alone retains the accepted fixed Rubber
Band pipeline through the schema40 mixer fixture, followed by scalar envelope
and mix math. Expected edit maps never come from the returned candidate.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import math
import os

import numpy as np
import pytest

from lumen.audio_gain import AudioGainKeyframe as Key
from lumen.clip_motion import ClipMotionKeyframe
from lumen.media import ffmpeg_available
from lumen.model import AudioMixAnchor, Clip, Editor, Project
from lumen.qualifier import WindowKeyframe
from lumen.timing import ClipTimeMap
from test_audio_gain_pcm import independent_pcm, scalar_offsets
from test_audio_mix import _pcm
from test_gap_close_render import lossless_scene
from test_linked_av_pcm import (av_root, assert_sources_untouched, marked_source,
                                source_fingerprints, unlinked)
from test_retime_video import (HEIGHT, WIDTH, assert_delivery_frames, decode,
                              engine, ff, normalized_source, segment_pixels)
from test_source_audio_separation_pcm import constant_av, pcm, samples

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='Local FFmpeg/ffprobe required')


def rounded(value):
    value = Fraction(value)
    return math.floor(value + Fraction(1, 2))


def sample(frame, fps):
    return rounded(Fraction(frame) * 48000 / fps)


def pair_project(source, fps, *, speed=Fraction(1), gain=True):
    origin = Fraction(9, 8)
    endpoint = origin + Fraction(19, 2) * speed
    mapping = ClipTimeMap(origin, endpoint, speed)
    v = Clip('video', source.media.id, 0, 3, origin, 10, time_map=mapping,
             source_audio_enabled=False, gain_db=18, fade_in=2, fade_out=4,
             audio_gain_keyframes=[Key(-71, 12), Key(99999, -96)])
    a = Clip('audio', source.media.id, 2, 3, origin, 10, time_map=mapping,
             audio_mix_anchor=AudioMixAnchor(0, 3, 'video'))
    if gain:
        a.gain_db = -4.125
        a.fade_in = 2
        a.fade_out = 3
        a.audio_gain_keyframes = [Key(-67, -11), Key(1023, 3, 'hold'),
                                  Key(1024, -6), Key(22000, 7)]
    # Hidden V motion and both roles' stored disabled qualifier keys survive.
    # Existing schema validation correctly prohibits A-track motion records.
    v.motion_keyframes = [ClipMotionKeyframe(-13), ClipMotionKeyframe(41)]
    for clip in (v, a):
        clip.grade.qualifier.window_keyframes = [
            WindowKeyframe(-17, .2, .3, .4, .5), WindowKeyframe(37, .8, .7, .6, .5)]
    project = Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
        fps_den=fps.denominator, media=[source.media], clips=[v, a,
            Clip('fixed-picture', source.media.id, 1, 16, 25, 1, source_audio_enabled=False),
            Clip('fixed-audio', source.media.id, 3, 15, 21, 2, gain_db=-18,
                 fade_in=1, audio_gain_keyframes=[Key(-17, -4), Key(4000, 2)])])
    editor = Editor(project)
    editor.link_av_clips('video', 'audio')
    return editor


def requested_project(before, edge, delta):
    """Expected model from the old document and request, without edit helpers.

    ClipTimeMap is merely the existing rational record constructor here. We do
    not use a duration setter, production rebasing, map evaluator or candidate.
    """
    wanted = deepcopy(before)
    ids = set(before.av_links[0].clip_ids)
    originals = {clip.id: clip for clip in before.clips}
    for clip in wanted.clips:
        if clip.id not in ids:
            continue
        old = originals[clip.id]
        mapping = old.timing
        clip.start = old.start + (delta if edge == 'left' else 0)
        clip.duration = old.duration + (-delta if edge == 'left' else delta)
        origin = mapping.source_in + (delta * mapping.speed if edge == 'left' else 0)
        endpoint = mapping.source_out + (delta * mapping.speed if edge == 'right' else 0)
        clip.source_in = origin
        clip.time_map = ClipTimeMap(origin, endpoint, mapping.speed)
        if mapping.speed == 1 and origin.denominator == 1 and endpoint - origin == clip.duration:
            clip.source_in = int(origin)
            clip.time_map = None
        if edge == 'left':
            clip.motion_keyframes = [replace(key, frame=key.frame - delta)
                                     for key in old.motion_keyframes]
            clip.grade.qualifier.window_keyframes = [replace(key, frame=key.frame - delta)
                for key in old.grade.qualifier.window_keyframes]
            offset = sample(old.start + delta, before.fps) - sample(old.start, before.fps)
            clip.audio_gain_keyframes = [replace(key, sample=key.sample - offset)
                                        for key in old.audio_gain_keyframes]
    wanted.validate()
    return wanted


def raw_unit_pcm(expected, raw):
    """Independent source slices, global half-up boundaries, envelopes and sum.

    This oracle imports no production compiler, evaluator or timing helper.
    The only input signal is the original synthetic stereo sample array.
    """
    fps = Fraction(expected.fps_num, expected.fps_den)
    result = np.zeros((sample(expected.duration_frames, fps), 2), np.float32)
    by_id = {media.id: media for media in expected.media}
    def order(clip):
        anchor = clip.audio_mix_anchor
        return ((anchor.track, anchor.start, anchor.clip_id) if anchor else
                (clip.track, clip.start, clip.id)), clip.start, clip.id
    for clip in sorted(expected.clips, key=order):
        media = by_id[clip.media_id]
        if clip.muted or not clip.source_audio_enabled or not media.has_audio:
            continue
        assert clip.speed == 1
        begin, end = sample(clip.start, fps), sample(clip.end, fps)
        count = end - begin
        origin, endpoint = Fraction(clip.source_in) / fps, clip.source_out / fps
        offset = Fraction(str(media.audio_offset))
        lo = rounded(max(Fraction(0), origin - offset) * 48000)
        hi = rounded(max(Fraction(0), endpoint - offset) * 48000)
        late = max(Fraction(0), offset - origin)
        leading = rounded((Fraction(clip.start) / fps + late) * 48000) - begin
        local = np.zeros((count, 2), np.float32)
        length = min(max(0, count - leading), max(0, hi - lo), max(0, len(raw) - lo))
        if length:
            local[leading:leading + length] = raw[lo:lo + length]
        # Match only the documented float32 storage boundaries, not production
        # coefficient calculation or expression generation.
        base = np.float32(math.pow(10, float(format(clip.gain_db, '.12g')) / 20))
        local *= base
        if clip.audio_gain_keyframes:
            local = (local.astype(np.float64) * scalar_offsets(
                clip.audio_gain_keyframes, count)[:, None]).astype(np.float32)
        if clip.fade_in:
            length = sample(clip.start + clip.fade_in, fps) - begin
            gain = np.minimum(np.arange(count, dtype=np.float64) / length, 1)
            local = (local.astype(np.float64) * gain[:, None]).astype(np.float32)
        if clip.fade_out:
            length = end - sample(clip.end - clip.fade_out, fps)
            gain = np.clip((count - np.arange(count, dtype=np.float64)) / length, 0, 1)
            local = (local.astype(np.float64) * gain[:, None]).astype(np.float32)
        result[begin:end] += local
    if expected.master_gain_db:
        result *= np.float32(math.pow(10, float(format(expected.master_gain_db, '.12g')) / 20))
    return result.astype('<f4').tobytes()


def expected_frames(before, expected, pictures):
    """Independent floor(O+k*r) source lookup and full-scene placement."""
    clip = next(clip for clip in expected.clips if clip.id == 'video')
    selected = pictures[[math.floor(clip.source_in + k * clip.speed)
                         for k in range(clip.duration)]]
    full = np.zeros((before.duration_frames, HEIGHT, WIDTH, 3), np.uint16)
    full[clip.start:clip.end] = selected
    full[16:17] = pictures[25:26]
    return selected, full


RATES_OFFSETS = [(Fraction(24), 0), (Fraction(24000, 1001), -.113),
                 (Fraction(30000, 1001), .137), (Fraction(60000, 1001), .137)]


@pytest.mark.parametrize('fps,offset', RATES_OFFSETS,
                         ids=['24', '23.976-early', '29.97-late', '59.94-late'])
@pytest.mark.parametrize('edge,delta', [('left', -1), ('left', 1), ('right', -1), ('right', 1)],
                         ids=['left-extend', 'left-shorten', 'right-shorten', 'right-extend'])
def test_joint_edges_numbered_rgb_and_raw_stereo_envelope_oracles(av_root, engine, fps, offset, edge, delta):
    source = marked_source(av_root, fps, offset)
    editor = pair_project(source, fps)
    editor.project.master_gain_db = -2.125
    before = deepcopy(editor.project)
    expected = requested_project(before, edge, delta)
    wanted = raw_unit_pcm(expected, source.audio)
    old_wanted = raw_unit_pcm(before, source.audio)
    if edge == 'left':
        shift = sample(before.clips[1].start + delta, fps) - sample(before.clips[1].start, fps)
        if shift != sample(delta, fps):
            wrong_phase = deepcopy(expected)
            wrong_phase.clips[1].audio_gain_keyframes = [
                replace(key, sample=key.sample - sample(delta, fps))
                for key in before.clips[1].audio_gain_keyframes]
            assert raw_unit_pcm(wrong_phase, source.audio) != wanted, (
                'This fixture must expose the one-sample S(delta) rebase error')
    paths = tuple(av_root.iterdir()); fingerprints = source_fingerprints(paths)
    old_audio = pcm(before)
    np.testing.assert_array_max_ulp(samples(old_audio), samples(old_wanted), maxulp=2)
    old_pixels, old_scene = expected_frames(before, before, source.pictures)
    np.testing.assert_array_equal(segment_pixels(engine, before, before.clips[0]), old_pixels)
    assert_delivery_frames(lossless_scene(engine, before, av_root / 'old.mkv'), old_scene)
    selected, scene = expected_frames(before, expected, source.pictures)
    history = len(editor._undo); revision = editor.revision
    preview = editor.preview_trim_linked_av('audio', edge, delta)
    assert editor.project == before and len(editor._undo) == history and editor.revision == revision
    assert [c.id for c in preview] == ['video', 'audio']
    assert list(preview) == expected.clips[:2]
    editor.trim_linked_av('audio', edge, delta)
    assert editor.project == expected and editor.revision == revision + 1
    assert len(editor._undo) == history + 1
    assert editor.project.av_links == before.av_links
    assert editor.project.clips[2:] == before.clips[2:]
    for old, new in zip(before.clips[:2], editor.project.clips[:2]):
        assert new.duration * new.speed - (new.source_out - new.source_in) == (
            old.duration * old.speed - (old.source_out - old.source_in))
        assert (new.fade_in, new.fade_out, new.audio_mix_anchor, new.source_audio_enabled) == (
            old.fade_in, old.fade_out, old.audio_mix_anchor, old.source_audio_enabled)
    actual = pcm(editor.project)
    np.testing.assert_array_max_ulp(samples(actual), samples(wanted), maxulp=2)
    # Trimming already-silent leading handles can happen to conserve PCM.
    # The independently derived request decides; never demand either outcome.
    assert (actual != old_audio) == (wanted != old_wanted)
    assert len(actual) == sample(before.duration_frames, fps) * 8
    np.testing.assert_array_equal(segment_pixels(engine, editor.project, editor._clip('video')), selected)
    assert_delivery_frames(lossless_scene(engine, editor.project, av_root / 'new.mkv'), scene)
    inputs, graph, _ = engine._compile(editor.project)
    assert _pcm(inputs, graph, video_output=os.devnull) == actual
    # Exact tail padding survives envelopes/fades; it is never a repeated last
    # source sample. Choose the central quarter-frame of the partial tail.
    a = expected.clips[1]
    tail_end = sample(a.end, fps)
    assert not np.count_nonzero(samples(actual)[tail_end - max(1, sample(1, fps) // 4):tail_end])
    saved = av_root / 'trimmed.lumen'; editor.project.save(saved)
    reopened = Project.load(saved)
    assert reopened == expected and pcm(reopened) == actual
    saved.unlink()
    assert editor.undo() and editor.project == before and pcm(editor.project) == old_audio
    assert editor.redo() and editor.project == expected and pcm(editor.project) == actual
    assert set(av_root.iterdir()) == set(paths)
    assert_sources_untouched(paths, fingerprints)


@pytest.mark.parametrize('native,fps,edge,delta', [
    (Fraction(60), Fraction(30000, 1001), 'left', 1),
    (Fraction(24), Fraction(60000, 1001), 'right', 1),
], ids=['60-native-29.97-project-left', '24-native-59.94-project-right'])
def test_mixed_native_project_rates_do_not_turn_trim_delta_into_native_frames(av_root, engine, native, fps, edge, delta):
    source = marked_source(av_root, native, -.113)
    editor = pair_project(source, fps)
    before = deepcopy(editor.project)
    expected = requested_project(before, edge, delta)
    # The existing source-grid normalization is explicitly established by a
    # separate FFmpeg fps call, not RenderEngine's selected segment.
    grid = normalized_source(source, fps)
    old_selected, old_full = expected_frames(before, before, grid)
    np.testing.assert_array_equal(segment_pixels(engine, before, before.clips[0]), old_selected)
    assert_delivery_frames(lossless_scene(engine, before, av_root / 'mixed-before.mkv'), old_full)
    selected, full = expected_frames(before, expected, grid)
    wanted = raw_unit_pcm(expected, source.audio)
    paths = tuple(av_root.iterdir()); fingerprints = source_fingerprints(paths)
    editor.trim_linked_av('video', edge, delta)
    assert editor.project == expected
    np.testing.assert_array_equal(segment_pixels(engine, editor.project, editor._clip('video')), selected)
    assert_delivery_frames(lossless_scene(engine, editor.project, av_root / 'mixed.mkv'), full)
    np.testing.assert_array_max_ulp(samples(pcm(editor.project)), samples(wanted), maxulp=2)
    assert_sources_untouched(paths, fingerprints)


@pytest.mark.parametrize('fps,speed,edge,delta', [
    (Fraction(30000, 1001), Fraction(3, 2), 'left', -1),
    (Fraction(30000, 1001), Fraction(3, 2), 'left', 1),
    (Fraction(60000, 1001), Fraction(2, 3), 'right', -1),
    (Fraction(60000, 1001), Fraction(2, 3), 'right', 1),
], ids=['retime-left-extend', 'retime-left-shorten', 'retime-right-shorten', 'retime-right-extend'])
def test_retime_expected_new_map_fixed_pipeline_and_independent_envelope_mix(av_root, engine, fps, speed, edge, delta):
    source = marked_source(av_root, fps, -.113)
    # Left extension needs at least one speed interval of source handle.
    editor = pair_project(source, fps, speed=speed)
    if edge == 'left' and delta < 0:
        editor.slip_linked_av('video', Fraction(25, 8))
    before = deepcopy(editor.project)
    expected = requested_project(before, edge, delta)
    paths = tuple(av_root.iterdir()); fingerprints = source_fingerprints(paths)
    # Only the unchanged, accepted Rubber Band local waveform is captured by
    # the frozen schema40 baseline; gain/fade/global placement/mix are scalar.
    old_wanted = independent_pcm(unlinked(before))
    wanted = independent_pcm(unlinked(expected))
    old_audio = pcm(before)
    np.testing.assert_array_max_ulp(samples(old_audio), samples(old_wanted), maxulp=1)
    assert wanted != old_audio
    old_selected, old_full = expected_frames(before, before, source.pictures)
    np.testing.assert_array_equal(segment_pixels(engine, before, before.clips[0]), old_selected)
    assert_delivery_frames(lossless_scene(engine, before, av_root / 'retimed-before.mkv'), old_full)
    editor.trim_linked_av('video', edge, delta)
    assert editor.project == expected
    actual = pcm(editor.project)
    np.testing.assert_array_max_ulp(samples(actual), samples(wanted), maxulp=1)
    assert len(actual) == sample(before.duration_frames, fps) * 8
    selected, full = expected_frames(before, expected, source.pictures)
    np.testing.assert_array_equal(segment_pixels(engine, editor.project, editor._clip('video')), selected)
    assert_delivery_frames(lossless_scene(engine, editor.project, av_root / 'retimed.mkv'), full)
    inputs, graph, _ = engine._compile(editor.project)
    assert _pcm(inputs, graph, video_output=os.devnull) == actual
    saved = av_root / 'retimed.lumen'; editor.project.save(saved)
    reopened = Project.load(saved)
    assert reopened == expected and pcm(reopened) == actual
    np.testing.assert_array_equal(segment_pixels(engine, reopened, reopened.clips[0]), selected)
    saved.unlink()
    assert editor.undo() and editor.project == before and pcm(editor.project) == old_audio
    np.testing.assert_array_equal(segment_pixels(engine, editor.project, editor._clip('video')), old_selected)
    assert editor.redo() and editor.project == expected and pcm(editor.project) == actual
    np.testing.assert_array_equal(segment_pixels(engine, editor.project, editor._clip('video')), selected)
    assert set(av_root.iterdir()) == set(paths)
    assert_sources_untouched(paths, fingerprints)


@pytest.mark.parametrize('muted', [False, True], ids=['audible-pair', 'muted-pair'])
def test_historical_tie_order_crossing_rejects_real_float32_cancellation(av_root, muted):
    tiny = 2 ** -25
    sources = [constant_av(av_root, 'tiny', tiny, -tiny),
               constant_av(av_root, 'positive', 1, -1),
               constant_av(av_root, 'negative', -1, 1)]
    anchor = AudioMixAnchor(0, 0, 'shared-historical-descendant')
    editor = Editor(Project(width=16, height=16, fps_num=24, media=sources, clips=[
        Clip('video', sources[0].id, 0, 3, 3, 6, source_audio_enabled=False,
             audio_mix_anchor=anchor),
        Clip('positive', sources[1].id, 1, 1, 0, 10, audio_mix_anchor=anchor),
        Clip('negative', sources[2].id, 2, 2, 0, 9, audio_mix_anchor=anchor),
        Clip('audio', sources[0].id, 3, 3, 3, 6, muted=muted, audio_mix_anchor=anchor)]))
    editor.link_av_clips('video', 'audio')
    before = deepcopy(editor.project)
    paths = tuple(av_root.iterdir()); fingerprints = source_fingerprints(paths)
    original = pcm(before)
    # Show why anchor metadata equality alone is insufficient. An independently
    # built, otherwise-valid candidate loses every tiny overlapping sample.
    damaged = requested_project(before, 'left', -2)
    old_potential, new_potential = deepcopy(before), deepcopy(damaged)
    next(c for c in old_potential.clips if c.id == 'audio').muted = False
    next(c for c in new_potential.clips if c.id == 'audio').muted = False
    lo, hi = sample(4, Fraction(24)), sample(8, Fraction(24))
    np.testing.assert_array_equal(samples(pcm(old_potential))[lo:hi],
                                  np.tile([tiny, -tiny], (hi - lo, 1)))
    assert not np.count_nonzero(samples(pcm(new_potential))[lo:hi])
    state = deepcopy((editor.project, editor._undo, editor._redo, editor.revision))
    for operation in (editor.preview_trim_linked_av, editor.trim_linked_av):
        with pytest.raises(ValueError, match='(?i)(mix|order)'):
            operation('audio', 'left', -2)
        assert (editor.project, editor._undo, editor._redo, editor.revision) == state
        assert pcm(editor.project) == original
    # Accepted left and right trims keep the original order; only the intended
    # selected interval changes, never the stored IDs or historical anchors.
    for edge, delta in [('left', 1), ('right', 1)]:
        expected = requested_project(before, edge, delta)
        wanted = independent_pcm(unlinked(expected))
        editor.trim_linked_av('video', edge, delta)
        assert editor.project == expected
        assert pcm(editor.project) == wanted
        assert editor.undo() and editor.project == before
    assert set(av_root.iterdir()) == set(paths)
    assert_sources_untouched(paths, fingerprints)


def test_joint_trim_real_preview_export_cache_and_reopen_agree(av_root, engine):
    fps = Fraction(30000, 1001)
    source = marked_source(av_root, fps, -.113)
    editor = pair_project(source, fps)
    before = deepcopy(editor.project)
    expected = requested_project(before, 'left', 1)
    _, before_scene = expected_frames(before, before, source.pictures)
    _, after_scene = expected_frames(before, expected, source.pictures)
    paths = tuple(av_root.iterdir()); fingerprints = source_fingerprints(paths)
    previous = engine.preview(before)
    assert_delivery_frames(decode(previous, rgb16=False), before_scene)
    editor.trim_linked_av('video', 'left', 1)
    current = engine.preview(editor.project)
    assert current != previous and engine.preview(editor.project) == current
    exported = engine.render(editor.project, av_root / 'trimmed-export.mp4')
    audio = []
    for path in (current, exported):
        assert_delivery_frames(decode(path, rgb16=False), after_scene)
        decoded = ff('-i', path, '-map', '0:a:0', '-vn', '-ar', '48000',
                     '-ac', '2', '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1')
        assert sample(expected.duration_frames, fps) <= len(samples(decoded)) < (
            sample(expected.duration_frames, fps) + 1024)
        audio.append(decoded)
    # Both public paths use the same AAC configuration. Equality here is
    # between those delivery paths, never between lossy AAC and exact PCM.
    assert audio[0] == audio[1]
    exported.unlink()
    saved = av_root / 'delivery.lumen'; editor.project.save(saved)
    assert engine.preview(Project.load(saved)) == current
    saved.unlink()
    assert editor.undo() and engine.preview(editor.project) == previous
    assert editor.redo() and engine.preview(editor.project) == current
    assert set(av_root.iterdir()) == set(paths)
    assert_sources_untouched(paths, fingerprints)


def test_muted_pair_trim_then_unmute_uses_rebased_audio_without_suppressed_video(av_root):
    fps = Fraction(30000, 1001)
    source = marked_source(av_root, fps, -.113)
    editor = pair_project(source, fps)
    editor.update_clip('audio', muted=True)
    before = deepcopy(editor.project)
    expected = requested_project(before, 'left', 1)
    paths = tuple(av_root.iterdir()); fingerprints = source_fingerprints(paths)
    old_audio = pcm(before)
    editor.trim_linked_av('audio', 'left', 1)
    assert editor.project == expected
    assert pcm(editor.project) == old_audio  # Only the fixed other A is audible.
    assert not editor._clip('video').source_audio_enabled
    editor.update_clip('audio', muted=False)
    expected.clips[1].muted = False
    assert editor.project == expected
    np.testing.assert_array_max_ulp(samples(pcm(editor.project)),
                                  samples(raw_unit_pcm(expected, source.audio)), maxulp=2)
    assert pcm(editor.project) != old_audio
    assert not editor._clip('video').source_audio_enabled
    assert_sources_untouched(paths, fingerprints)

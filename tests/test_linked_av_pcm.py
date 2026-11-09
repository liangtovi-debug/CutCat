"""Real numbered-picture and marked stereo PCM oracles for linked A/V editing.

Link/Unlink is checked against captured unlinked output, byte for byte. Slip is
NOT a conservation edit: its expected new map is built from the pre-edit map
and explicit requested source-in, never from the returned edited clips. 1x
PCM also has a raw-sample slicing oracle independent of the timing/compiler.
Retime uses the accepted fixed Rubber Band pipeline (not a lossless/phase
continuity claim) with the frozen pre-feature mixer and scalar gain oracle.
"""
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import math
import os
from pathlib import Path
import shutil

import numpy as np
import pytest

from lumen.audio_gain import AudioGainKeyframe as GainKey
from lumen.audio_mix import compile_audio_mix
from lumen.clip_motion import ClipMotionKeyframe
from lumen.media import ffmpeg_available, probe
from lumen.model import AudioMixAnchor, Clip, Editor, Project
from lumen.qualifier import WindowKeyframe
from lumen.timing import ClipTimeMap
from test_audio_gain_pcm import independent_pcm
from test_audio_mix import _pcm
from test_gap_close_render import lossless_scene
from test_retime_video import (HEIGHT, WIDTH, assert_delivery_frames, decode,
                              engine, ff, make_source, segment_pixels)
from test_source_audio_separation_pcm import (boundary, constant_av, digest,
                                             pcm, samples)

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='Local FFmpeg/ffprobe required')


@pytest.fixture
def av_root(tmp_path):
    # Synthetic media and temporary render outputs only; no lasting generated
    # fixtures in the source copy or resource-constrained acceptance workspace.
    root = tmp_path / 'linked-av-oracle'
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root)


@dataclass
class MarkedSource:
    media: object
    pictures: np.ndarray
    audio: np.ndarray


def marked_source(root, fps, offset=0):
    """Lossless RGB16 frame IDs plus distinct source-frame-coded stereo PCM."""
    numbered = make_source(root / 'numbered-video.mkv', fps)
    count = 48000 * 4
    n = np.arange(count, dtype=np.int64)
    source_frame = n * fps.numerator // (48000 * fps.denominator)
    # Exactly representable binary values: source-frame identity and sub-frame
    # variation make a wrong in-point, swap, duplicate or tail repeat visible.
    raw = np.column_stack((
        ((source_frame * 17 + n % 127) % 1024 - 512) / 1024,
        ((source_frame * 31 + n % 251) % 1024 - 512) / 2048,
    )).astype('<f4')
    raw[::997, 0] = .75
    raw[::1423, 1] = -.625
    path = root / 'numbered-and-marked.mkv'
    # Set timestamps after input decoding: FFmpeg 9 rebases raw-input
    # -itsoffset during encoding. Keep original PCM samples unchanged.
    ff('-i', numbered.media.path,
       '-f', 'f32le', '-ar', '48000', '-ac', '2', '-i', 'pipe:0',
       '-map', '0:v', '-map', '1:a', '-af', f'asetpts=PTS+({offset})/TB',
       '-c:v', 'copy', '-c:a', 'pcm_f32le', '-avoid_negative_ts', 'make_non_negative',
       path, data=raw.tobytes())
    media = probe(path)
    assert media.has_audio and media.has_video
    assert media.audio_offset == pytest.approx(offset, abs=1e-9)
    np.testing.assert_array_equal(decode(path), numbered.original)
    decoded = ff('-i', path, '-map', '0:a:0', '-vn', '-ar', '48000',
                 '-ac', '2', '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1')
    np.testing.assert_array_equal(samples(decoded), raw)
    return MarkedSource(media, numbered.original, raw)


def unlinked(project):
    result = deepcopy(project)
    result.av_links = []
    return result


def assert_sources_untouched(paths, before):
    assert {str(path): (digest(path), path.stat().st_mtime_ns, path.stat().st_size)
            for path in paths} == before


def source_fingerprints(paths):
    return {str(path): (digest(path), path.stat().st_mtime_ns, path.stat().st_size)
            for path in paths}


def linked_project(source, fps, *, speed=Fraction(1), gain=False):
    origin = Fraction(9, 8)
    span = Fraction(19, 2) * speed
    mapping = ClipTimeMap(origin, origin + span, speed)
    v = Clip('video', source.media.id, 0, 2, origin, 10,
             time_map=mapping, source_audio_enabled=False, gain_db=18,
             fade_in=1, fade_out=4,
             audio_gain_keyframes=[GainKey(-71, 12), GainKey(99999, -96)])
    # V's disabled but nontrivial audio parameters must never become a second
    # contribution merely because a link is added/removed or A is unmuted.
    a = Clip('audio', source.media.id, 2, 2, origin, 10,
             time_map=mapping, audio_mix_anchor=AudioMixAnchor(0, 2, 'video'))
    if gain:
        a.gain_db = -4.125; a.fade_in = 2; a.fade_out = 3
        a.audio_gain_keyframes = [GainKey(-67, -11), GainKey(1023, 3, 'hold'),
                                  GainKey(1024, -6), GainKey(22000, 7)]
    return Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
                   fps_den=fps.denominator, media=[source.media], clips=[v, a])


def expected_slip(before, requested):
    """Construct the expected content selection solely from original intent."""
    expected = unlinked(before)
    for clip in expected.clips:
        if clip.id not in ('video', 'audio'):
            continue
        old = next(item for item in before.clips if item.id == clip.id).timing
        clip.source_in = Fraction(requested)
        clip.time_map = ClipTimeMap(Fraction(requested),
            Fraction(requested) + old.source_out - old.source_in, old.speed)
        # Explicit expected representation rather than reading candidate state.
        if (clip.time_map.speed == 1 and clip.source_in.denominator == 1
                and clip.time_map.source_out - clip.source_in == clip.duration):
            clip.source_in = int(clip.source_in)
            clip.time_map = None
    expected.validate()
    return expected


def round_half_up(value):
    value = Fraction(value)
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def unit_speed_expected(project, raw):
    """No production timing, map-evaluation, envelope or render helpers."""
    clip = next(c for c in project.clips if c.id == 'audio')
    assert clip.speed == 1 and not clip.audio_gain_keyframes
    assert not clip.muted and clip.source_audio_enabled
    assert clip.gain_db == clip.fade_in == clip.fade_out == 0
    assert project.master_gain_db == 0
    fps = Fraction(project.fps_num, project.fps_den)
    origin = Fraction(clip.source_in) / fps
    endpoint = Fraction(clip.source_out) / fps
    offset = Fraction(str(project.media[0].audio_offset))
    start = round_half_up(Fraction(clip.start * 48000) / fps)
    end = round_half_up(Fraction(clip.end * 48000) / fps)
    lo = round_half_up(max(Fraction(0), origin - offset) * 48000)
    hi = round_half_up(max(Fraction(0), endpoint - offset) * 48000)
    late = max(Fraction(0), offset - origin)
    leading = round_half_up((Fraction(clip.start) / fps + late) * 48000) - start
    result = np.zeros((round_half_up(Fraction(project.duration_frames * 48000) / fps), 2), '<f4')
    length = min(max(0, end - start - leading), max(0, hi - lo), max(0, len(raw) - lo))
    if length:
        result[start + leading:start + leading + length] = raw[lo:lo + length]
    return result.tobytes()


@pytest.mark.parametrize('muted', [False, True], ids=['audible-A', 'muted-A'])
def test_link_unlink_is_zero_impact_real_rgb_and_entire_float32_mix(av_root, engine, muted):
    fps = Fraction(30000, 1001)
    source = marked_source(av_root, fps, .137)
    project = linked_project(source, fps, gain=True)
    project.master_gain_db = -1.75
    project.clips[1].muted = muted
    project.clips += [
        Clip('upper', source.media.id, 1, 4, 31, 3, gain_db=-14, fade_in=1),
        Clip('other-audio', source.media.id, 3, 1, 17, 12, gain_db=-17,
             audio_gain_keyframes=[GainKey(-13, -2), GainKey(13000, 4)])]
    # Both negative and future visual handles remain unchanged; disabled
    # qualifier geometry is metadata and cannot be silently rebased by Link.
    project.clips[0].motion_keyframes = [ClipMotionKeyframe(-11), ClipMotionKeyframe(43)]
    project.clips[0].grade.qualifier.window_keyframes = [
        WindowKeyframe(-9, .2, .3, .4, .5), WindowKeyframe(47, .8, .7, .6, .5)]
    editor = Editor(project)
    before = deepcopy(project)
    paths = tuple(av_root.iterdir()); fingerprints = source_fingerprints(paths)
    original_pixels = lossless_scene(engine, before, av_root / 'before.mkv')
    original_pcm = pcm(before)
    reference = independent_pcm(unlinked(before))
    np.testing.assert_array_max_ulp(samples(original_pcm), samples(reference), maxulp=1)
    assert np.count_nonzero(samples(original_pcm)) > 0
    revision = editor.revision
    editor.link_av_clips('video', 'audio')
    assert editor.revision == revision + 1 and len(editor._undo) == 1
    assert unlinked(editor.project) == before
    assert pcm(editor.project) == original_pcm
    np.testing.assert_array_equal(lossless_scene(engine, editor.project,
        av_root / 'linked.mkv'), original_pixels)
    linked = deepcopy(editor.project)
    saved = av_root / 'linked.lumen'; editor.project.save(saved)
    assert Project.load(saved) == linked
    assert pcm(Project.load(saved)) == original_pcm
    saved.unlink()
    editor.unlink_av_clips(['audio'])
    assert editor.project == before and len(editor._undo) == 2
    assert not editor._clip('video').source_audio_enabled
    assert pcm(editor.project) == original_pcm
    np.testing.assert_array_equal(lossless_scene(engine, editor.project,
        av_root / 'unlinked.mkv'), original_pixels)
    assert editor.undo() and editor.project == linked
    assert editor.redo() and editor.project == before
    if muted:
        editor.update_clip('audio', muted=False)
        expected = deepcopy(before); expected.clips[1].muted = False
        assert pcm(editor.project) == pcm(expected)
        assert pcm(editor.project) != original_pcm
        assert not editor._clip('video').source_audio_enabled
    assert set(av_root.iterdir()) == set(paths)
    assert_sources_untouched(paths, fingerprints)


@pytest.mark.parametrize('fps,offset', [
    (Fraction(24), 0), (Fraction(30000, 1001), .137),
    (Fraction(24000, 1001), -.113), (Fraction(60000, 1001), .137),
], ids=['24-zero-offset', '29.97-late-audio', '23.976-early-audio', '59.94-late-audio'])
def test_joint_slip_new_map_matches_numbered_frames_and_independent_raw_pcm(av_root, engine, fps, offset):
    source = marked_source(av_root, fps, offset)
    editor = Editor(linked_project(source, fps))
    editor.link_av_clips('video', 'audio')
    before = deepcopy(editor.project)
    requested = Fraction(25, 4)
    expected = expected_slip(before, requested)
    paths = tuple(av_root.iterdir()); fingerprints = source_fingerprints(paths)
    old_audio = pcm(editor.project)
    np.testing.assert_array_equal(samples(old_audio), samples(unit_speed_expected(unlinked(before), source.audio)))
    wanted_audio = unit_speed_expected(expected, source.audio)
    assert wanted_audio != old_audio, 'Slip must genuinely select different marked source audio'
    wanted_frames = source.pictures[[math.floor(requested + k) for k in range(10)]]
    editor.slip_linked_av('audio', requested)
    assert unlinked(editor.project) == expected
    assert editor.project.av_links == before.av_links
    assert editor._clip('video').timing == editor._clip('audio').timing
    assert editor._clip('audio').source_out == requested + Fraction(19, 2)
    np.testing.assert_array_equal(segment_pixels(engine, editor.project, editor._clip('video')), wanted_frames)
    assert pcm(editor.project) == wanted_audio
    inputs, graph, _ = engine._compile(editor.project)
    assert _pcm(inputs, graph, video_output=os.devnull) == wanted_audio
    scene = lossless_scene(engine, editor.project, av_root / 'slipped.mkv')
    expected_scene = np.zeros((12, HEIGHT, WIDTH, 3), np.uint16)
    expected_scene[2:] = wanted_frames
    assert_delivery_frames(scene, expected_scene)
    # The last picture is held for the partial final frame, but the audio tail
    # is real zero padding after the exact selected source endpoint.
    tail = samples(wanted_audio)[-max(1, boundary(1, fps) // 4):]
    assert not np.count_nonzero(tail)
    assert len(wanted_audio) == boundary(12, fps) * 8
    after = deepcopy(editor.project)
    assert editor.undo() and editor.project == before and pcm(editor.project) == old_audio
    assert editor.redo() and editor.project == after and pcm(editor.project) == wanted_audio
    saved = av_root / 'slipped.lumen'; editor.project.save(saved)
    assert Project.load(saved) == after and pcm(Project.load(saved)) == wanted_audio
    saved.unlink()
    assert set(av_root.iterdir()) == set(paths)
    assert_sources_untouched(paths, fingerprints)


@pytest.mark.parametrize('fps,offset,speed', [
    (Fraction(24), .137, Fraction(3, 2)),
    (Fraction(30000, 1001), -.113, Fraction(2, 3)),
    (Fraction(60000, 1001), .137, Fraction(3, 2)),
], ids=['24-speed-1.5-late', '29.97-speed-two-thirds-early', '59.94-speed-1.5-late'])
def test_joint_retimed_partial_slip_uses_expected_new_map_not_old_pcm_equality(av_root, engine, fps, offset, speed):
    source = marked_source(av_root, fps, offset)
    project = linked_project(source, fps, speed=speed, gain=True)
    project.master_gain_db = -2.125
    project.clips[0].motion_keyframes = [ClipMotionKeyframe(-13), ClipMotionKeyframe(41)]
    project.clips[0].grade.qualifier.window_keyframes = [
        WindowKeyframe(-17, .2, .3, .4, .5), WindowKeyframe(37, .8, .7, .6, .5)]
    project.clips += [
        Clip('upper', source.media.id, 1, 5, 37, 3, gain_db=-19,
             audio_mix_anchor=AudioMixAnchor(1, 0, 'old-upper')),
        Clip('other', source.media.id, 3, 1, 19, 12, gain_db=-13,
             fade_in=1, fade_out=2)]
    editor = Editor(project); editor.link_av_clips('video', 'audio')
    before = deepcopy(project); requested = Fraction(53, 8)
    expected = expected_slip(before, requested)
    paths = tuple(av_root.iterdir()); fingerprints = source_fingerprints(paths)
    # This references the accepted source/retime path and independent gain
    # interpolation/mixing. Rubber Band is deliberately not reimplemented.
    wanted = independent_pcm(expected)
    old = pcm(before)
    assert wanted != old
    editor.slip_linked_av('video', requested)
    assert unlinked(editor.project) == expected
    assert editor._clip('audio').timing.source_out == requested + Fraction(19, 2) * speed
    actual = pcm(editor.project)
    np.testing.assert_array_max_ulp(samples(actual), samples(wanted), maxulp=1)
    assert len(actual) == boundary(13, fps) * 8
    wanted_frames = source.pictures[[math.floor(requested + k * speed) for k in range(10)]]
    np.testing.assert_array_equal(segment_pixels(engine, editor.project, editor._clip('video')), wanted_frames)
    full_expected = np.zeros((13, HEIGHT, WIDTH, 3), np.uint16)
    full_expected[2:12] = wanted_frames
    full_expected[5:8] = source.pictures[37:40]
    assert_delivery_frames(lossless_scene(engine, editor.project,
        av_root / 'retimed.mkv'), full_expected)
    inputs, graph, _ = engine._compile(editor.project)
    assert _pcm(inputs, graph, video_output=os.devnull) == actual
    # Exercise the exact retimed/partial selection through history and disk,
    # rather than proving those paths only for the simpler 1x raw-sample case.
    after = deepcopy(editor.project)
    old_frames = source.pictures[[
        math.floor(before.clips[0].source_in + k * speed) for k in range(10)]]
    assert not np.array_equal(old_frames, wanted_frames)
    assert len(editor._undo) == 2
    assert editor.undo() and editor.project == before and pcm(editor.project) == old
    np.testing.assert_array_equal(segment_pixels(engine, editor.project,
        editor._clip('video')), old_frames)
    assert editor.redo() and editor.project == after and pcm(editor.project) == actual
    np.testing.assert_array_equal(segment_pixels(engine, editor.project,
        editor._clip('video')), wanted_frames)
    saved = av_root / 'retimed-slipped.lumen'; editor.project.save(saved)
    reopened = Project.load(saved)
    assert reopened == after and pcm(reopened) == actual
    np.testing.assert_array_equal(segment_pixels(engine, reopened,
        next(c for c in reopened.clips if c.id == 'video')), wanted_frames)
    saved.unlink()
    assert set(av_root.iterdir()) == set(paths)
    assert_sources_untouched(paths, fingerprints)


def test_link_unlink_preserves_historical_full_float_mix_order_and_structural_suppression(av_root):
    tiny = 2 ** -25
    sources = [constant_av(av_root, 'tiny', tiny, -tiny),
               constant_av(av_root, 'positive', 1, -1),
               constant_av(av_root, 'negative', -1, 1)]
    editor = Editor(Project(width=16, height=16, fps_num=24, media=sources, clips=[
        Clip('video', sources[0].id, 0, 0, 0, 6, source_audio_enabled=False),
        Clip('positive', sources[1].id, 1, 0, 0, 6),
        Clip('negative', sources[2].id, 2, 0, 0, 6),
        Clip('audio', sources[0].id, 3, 0, 0, 6,
             audio_mix_anchor=AudioMixAnchor(3, 0, 'historical-audio'))]))
    paths = tuple(av_root.iterdir()); fingerprints = source_fingerprints(paths)
    before = deepcopy(editor.project)
    wanted = pcm(before)
    np.testing.assert_array_equal(samples(wanted), np.tile([tiny, -tiny], (12000, 1)))
    # Wrongly moving the A contribution into the V slot erases every marker;
    # the test detects subtle float32 order changes, not just source selection.
    damaged = deepcopy(before)
    damaged.clips[-1].audio_mix_anchor = AudioMixAnchor(0, 0, 'video')
    assert not np.count_nonzero(samples(pcm(damaged)))
    editor.link_av_clips('video', 'audio')
    assert pcm(editor.project) == wanted and unlinked(editor.project) == before
    editor.unlink_av_clips(['video'])
    assert pcm(editor.project) == wanted and editor.project == before
    assert not editor._clip('video').source_audio_enabled
    assert set(av_root.iterdir()) == set(paths)
    assert_sources_untouched(paths, fingerprints)

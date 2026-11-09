"""Independent actual-media oracles for one joint cut between two strict pairs.

All edited maps and key positions come from the OLD project and requested cut,
never from a returned candidate or production timing/rebasing helpers. Unit-speed
PCM is raw source slicing plus scalar envelopes/mixing. Retimed waveform tests
explicitly retain the separately established schema40/Rubber Band semantics;
they do not claim waveform conservation or continuity across the edited cut.
Every actual full-scene render consumes BOTH video and audio into a lossless mux,
not the audio-only/nullsink scheduling trap documented in docs/ROLL_EDIT.md.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import hashlib
import json
import math
import shutil

import numpy as np
import pytest

from lumen.audio_gain import AudioGainKeyframe as Key
from lumen.clip_motion import ClipMotionKeyframe
from lumen.media import ffmpeg_available, probe
from lumen.model import AudioMixAnchor, Clip, Editor, Project
from lumen.qualifier import WindowKeyframe
from lumen.timing import ClipTimeMap
from test_audio_gain_pcm import independent_pcm, scalar_offsets
from test_linked_av_pcm import (MarkedSource, assert_sources_untouched,
                                marked_source, source_fingerprints, unlinked)
from test_retime_video import (HEIGHT, WIDTH, assert_delivery_frames, decode,
                              engine, ff, segment_pixels)
from test_source_audio_separation_pcm import constant_av, samples

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='Local FFmpeg/ffprobe required')
ROLL_IDS = ('left-video', 'left-audio', 'right-video', 'right-audio')
RATES = [Fraction(24), Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001)]


@pytest.fixture
def av_root(tmp_path):
    # Own only these two new-test directories. A missing final success marker
    # preserves raw synthetic inputs, lossless muxes and compact diagnostic
    # metadata after any setup/test failure. Accepted shared fixtures are not
    # modified; RenderEngine's separate cache teardown remains unchanged.
    root = tmp_path / 'linked-av-roll-oracle'
    root.mkdir()
    proof_path(root, 'verification.json').parent.mkdir()
    try:
        yield root
    finally:
        if proof_path(root, 'VERIFIED').is_file():
            shutil.rmtree(root)
            shutil.rmtree(proof_path(root, 'VERIFIED').parent)


def proof_path(root, name):
    return root.with_name(root.name + '-proof') / name


def record_result(root, label, project, *, frames=None, audio=None, basis):
    """Diagnostic hashes/counts only, never uploaded source or signal arrays.

    Expected RGB16 frame-ID hashes and actual RGB8 delivery hashes deliberately
    have different pixel domains; the strict existing visual assertions decide
    equivalence. PCM hashes aid diagnosis; unchanged ULP assertions decide the
    separately documented float-rounding allowance.
    """
    summary = {'basis': basis, 'fps': str(project.fps),
        'timeline_frames': project.duration_frames,
        'clips': [{'id': clip.id, 'media_id': clip.media_id, 'track': clip.track,
                   'start': clip.start, 'duration': clip.duration,
                   'timing': clip.timing.to_dict(),
                   'source_audio_enabled': clip.source_audio_enabled,
                   'muted': clip.muted}
                  for clip in project.clips]}
    if frames is not None:
        summary['frames'] = {'count': len(frames), 'shape': list(frames.shape),
            'dtype': str(frames.dtype), 'sha256': hashlib.sha256(frames.tobytes()).hexdigest()}
    if audio is not None:
        summary['audio'] = {'sample_frames': len(audio) // 8, 'channels': 2,
            'sample_rate': 48000, 'format': 'f32le', 'bytes': len(audio),
            'sha256': hashlib.sha256(audio).hexdigest()}
    proof_path(root, label + '.json').write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def mark_verified(root):
    # Last statement in each test, after every assertion and source invariant.
    proof_path(root, 'VERIFIED').write_text('All assertions completed successfully.\n')


def rounded(value):
    return math.floor(Fraction(value) + Fraction(1, 2))


def sample(frame, fps):
    return rounded(Fraction(frame) * 48000 / fps)


def sources(root, fps, offset):
    """Two distinguishable RGB frame sequences and nonperiodic stereo signals."""
    left_root, right_root = root / 'left', root / 'right'
    left_root.mkdir(); right_root.mkdir()
    left = marked_source(left_root, fps, offset)
    original = marked_source(right_root, fps, -.113)
    pictures = original.pictures[::-1].copy()
    raw = np.column_stack((-original.audio[:, 1], original.audio[:, 0] / 2)).astype('<f4')
    # At 59.94 the real audio stream ends inside the selected right interval,
    # while the video still has enough handles. The expected tail is silence.
    if fps == Fraction(60000, 1001):
        raw = raw[:sample(13, fps)]
    picture = right_root / 'reverse-picture.mkv'
    ff('-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-s', f'{WIDTH}x{HEIGHT}',
       '-r', str(fps), '-i', 'pipe:0', '-an', '-c:v', 'ffv1', '-level', '3',
       '-pix_fmt', 'gbrp16le', '-threads', '1', '-color_range', 'pc',
       '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'rgb',
       picture, data=pictures.astype('<u2').tobytes())
    path = right_root / 'reverse-and-marked.mkv'
    ff('-i', picture, '-f', 'f32le', '-ar', '48000',
       '-ac', '2', '-i', 'pipe:0', '-map', '0:v', '-map', '1:a',
       '-af', 'asetpts=PTS+(-0.113)/TB', '-c:v', 'copy', '-c:a', 'pcm_f32le',
       '-avoid_negative_ts', 'make_non_negative', path, data=raw.tobytes())
    media = probe(path)
    assert media.audio_offset == pytest.approx(-.113, abs=1e-9)
    np.testing.assert_array_equal(decode(path), pictures)
    decoded = ff('-i', path, '-map', '0:a:0', '-vn', '-ar', '48000',
                 '-ac', '2', '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1')
    np.testing.assert_array_equal(samples(decoded), raw)
    return left, MarkedSource(media, pictures, raw)


def pair_project(sources, fps, speeds=(Fraction(1), Fraction(1))):
    clips = []
    for side, source, start, length, origin, speed in (
            ('left', sources[0], 3, 10, Fraction(9, 8), speeds[0]),
            ('right', sources[1], 13, 12, Fraction(25, 8), speeds[1])):
        mapping = ClipTimeMap(origin, origin + (length - Fraction(1, 2)) * speed, speed)
        video = Clip(side + '-video', source.media.id, 0, start, origin, length,
            time_map=mapping, source_audio_enabled=False, gain_db=18,
            fade_in=2, fade_out=4, audio_gain_keyframes=[Key(-71, 12), Key(99999, -96)])
        audio = Clip(side + '-audio', source.media.id, 2, start, origin, length,
            time_map=mapping, audio_mix_anchor=AudioMixAnchor(0, start, video.id),
            gain_db=-4.125, fade_in=2, fade_out=3,
            audio_gain_keyframes=[Key(-67, -11), Key(1023, 3, 'hold'),
                                  Key(1024, -6), Key(22000, 7)])
        video.motion_keyframes = [ClipMotionKeyframe(-13), ClipMotionKeyframe(41)]
        for clip in (video, audio):
            clip.grade.qualifier.window_keyframes = [
                WindowKeyframe(-17, .2, .3, .4, .5), WindowKeyframe(37, .8, .7, .6, .5)]
        clips.extend((video, audio))
    clips += [Clip('fixed-picture', sources[0].media.id, 1, 28, 25, 1,
                   source_audio_enabled=False),
              Clip('fixed-audio', sources[0].media.id, 3, 15, 21, 12,
                   gain_db=-18, fade_in=1, fade_out=2,
                   audio_gain_keyframes=[Key(-17, -4), Key(4000, 2)])]
    editor = Editor(Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
        fps_den=fps.denominator, media=[s.media for s in sources], clips=clips,
        master_gain_db=-2.125))
    editor.link_av_clips(*ROLL_IDS[:2]); editor.link_av_clips(*ROLL_IDS[2:])
    return editor


def requested_project(before, cut):
    """Edit intent from old records only; no candidate/timing/rebase helper."""
    wanted = deepcopy(before)
    originals = {clip.id: clip for clip in before.clips}
    delta = cut - originals['left-video'].end
    for clip in wanted.clips:
        if clip.id not in ROLL_IDS:
            continue
        old = originals[clip.id]
        right = clip.id in ROLL_IDS[2:]
        clip.start = cut if right else old.start
        clip.duration = old.duration + (-delta if right else delta)
        origin = Fraction(old.source_in) + (delta * old.speed if right else 0)
        endpoint = old.source_out + (0 if right else delta * old.speed)
        clip.source_in = origin
        clip.time_map = ClipTimeMap(origin, endpoint, old.speed)
        if old.speed == 1 and origin.denominator == 1 and endpoint - origin == clip.duration:
            clip.source_in = int(origin); clip.time_map = None
        if right:
            clip.motion_keyframes = [replace(key, frame=key.frame - delta)
                                     for key in old.motion_keyframes]
            clip.grade.qualifier.window_keyframes = [replace(key, frame=key.frame - delta)
                for key in old.grade.qualifier.window_keyframes]
            shift = sample(cut, before.fps) - sample(old.start, before.fps)
            clip.audio_gain_keyframes = [replace(key, sample=key.sample - shift)
                                        for key in old.audio_gain_keyframes]
    wanted.validate()
    return wanted


def raw_unit_pcm(expected, raw_by_id):
    """Original sample arrays, rational boundaries, scalar envelopes and sum."""
    fps = Fraction(expected.fps_num, expected.fps_den)
    result = np.zeros((sample(expected.duration_frames, fps), 2), np.float32)
    media = {item.id: item for item in expected.media}
    def order(clip):
        anchor = clip.audio_mix_anchor
        return ((anchor.track, anchor.start, anchor.clip_id) if anchor else
                (clip.track, clip.start, clip.id)), clip.start, clip.id
    for clip in sorted(expected.clips, key=order):
        source = media[clip.media_id]
        if clip.muted or not clip.source_audio_enabled or not source.has_audio:
            continue
        assert clip.speed == 1
        raw = raw_by_id[clip.media_id]
        begin, end = sample(clip.start, fps), sample(clip.end, fps)
        count = end - begin
        origin, endpoint = Fraction(clip.source_in) / fps, clip.source_out / fps
        offset = Fraction(str(source.audio_offset))
        lo = rounded(max(Fraction(0), origin - offset) * 48000)
        hi = rounded(max(Fraction(0), endpoint - offset) * 48000)
        leading = rounded((Fraction(clip.start) / fps + max(Fraction(0), offset - origin)) * 48000) - begin
        local = np.zeros((count, 2), np.float32)
        length = min(max(0, count - leading), max(0, hi - lo), max(0, len(raw) - lo))
        if length:
            local[leading:leading + length] = raw[lo:lo + length]
        local *= np.float32(math.pow(10, float(format(clip.gain_db, '.12g')) / 20))
        if clip.audio_gain_keyframes:
            local = (local.astype(np.float64) * scalar_offsets(
                clip.audio_gain_keyframes, count)[:, None]).astype(np.float32)
        if clip.fade_in:
            length = sample(clip.start + clip.fade_in, fps) - begin
            local = (local.astype(np.float64) * np.minimum(
                np.arange(count, dtype=np.float64) / length, 1)[:, None]).astype(np.float32)
        if clip.fade_out:
            length = end - sample(clip.end - clip.fade_out, fps)
            local = (local.astype(np.float64) * np.clip(
                (count - np.arange(count, dtype=np.float64)) / length, 0, 1)[:, None]).astype(np.float32)
        result[begin:end] += local
    result *= np.float32(math.pow(10, float(format(expected.master_gain_db, '.12g')) / 20))
    return result.astype('<f4').tobytes()


def expected_frames(expected, pictures):
    full = np.zeros((expected.duration_frames, HEIGHT, WIDTH, 3), np.uint16)
    segments = {}
    for clip in sorted(expected.clips, key=lambda c: (c.track, c.start, c.id)):
        if clip.track not in (0, 1):
            continue
        selected = pictures[clip.media_id][[math.floor(clip.source_in + k * clip.speed)
                                          for k in range(clip.duration)]]
        segments[clip.id] = selected
        full[clip.start:clip.end] = selected
    return segments, full


def lossless_av(engine, project, path):
    """Both production outputs must be consumed before inspecting exact PCM."""
    root, name = path.parent, path.name
    path = proof_path(root, name)
    inputs, graph, _ = engine._compile(project)
    ff(*inputs, '-filter_complex_threads', '1', '-filter_complex', graph,
       '-map', '[vout]', '-map', '[aout]', '-c:v', 'ffv1', '-c:a', 'pcm_f32le', path)
    pictures = decode(path, rgb16=False)
    audio = ff('-i', path, '-map', '0:a:0', '-vn', '-ar', '48000',
               '-ac', '2', '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1')
    record_result(root, path.stem + '-actual', project, frames=pictures, audio=audio,
                  basis='Actual production dual-stream FFV1/PCM mux')
    # Keep the mux through all comparisons. Only a final test success marker
    # authorizes this fixture to remove its own inputs and proof directory.
    return pictures, audio


def source_paths(root):
    return tuple(sorted(path for path in root.rglob('*') if path.is_file()))


@pytest.mark.parametrize('fps,offset', list(zip(RATES, [0, -.113, .137, .137])),
                         ids=['24', '23.976-early', '29.97-late', '59.94-late-eof'])
@pytest.mark.parametrize('delta', [-1, 1], ids=['cut-earlier', 'cut-later'])
def test_joint_roll_old_request_rgb_and_independent_stereo_mix(av_root, engine, fps, offset, delta):
    inputs = sources(av_root, fps, offset)
    editor = pair_project(inputs, fps)
    before = deepcopy(editor.project); cut = 13 + delta
    expected = requested_project(before, cut)
    raw = {source.media.id: source.audio for source in inputs}
    pictures = {source.media.id: source.pictures for source in inputs}
    wanted, old_wanted = raw_unit_pcm(expected, raw), raw_unit_pcm(before, raw)
    record_result(av_root, 'before-expected', before, frames=expected_frames(before, pictures)[1],
                  audio=old_wanted, basis='Old project plus original raw samples and scalar envelope/mix')
    record_result(av_root, 'after-expected', expected, frames=expected_frames(expected, pictures)[1],
                  audio=wanted, basis='Old project plus requested cut; independent raw samples and scalar envelope/mix')
    assert wanted != old_wanted, 'The requested roll intentionally changes the edited waveform'
    shift = sample(cut, fps) - sample(13, fps)
    if shift != sample(delta, fps):
        wrong = deepcopy(expected)
        right = next(clip for clip in wrong.clips if clip.id == 'right-audio')
        right.audio_gain_keyframes = [replace(key, sample=key.sample - sample(delta, fps))
                                     for key in before.clips[3].audio_gain_keyframes]
        assert raw_unit_pcm(wrong, raw) != wanted, 'Fixture must expose S(delta), not merely record its difference'
    paths = source_paths(av_root); fingerprints = source_fingerprints(paths)
    old_segments, old_scene = expected_frames(before, pictures)
    actual_scene, old_audio = lossless_av(engine, before, av_root / 'before.mkv')
    assert_delivery_frames(actual_scene, old_scene)
    np.testing.assert_array_max_ulp(samples(old_audio), samples(old_wanted), maxulp=2)
    history, revision = len(editor._undo), editor.revision
    # Deliberately scrambled selection must still return explicit four roles.
    selection = (ROLL_IDS[3], ROLL_IDS[0], ROLL_IDS[2], ROLL_IDS[1])
    preview = editor.preview_roll_linked_av(selection, cut)
    assert [clip.id for clip in preview] == list(ROLL_IDS)
    assert list(preview) == expected.clips[:4]
    assert editor.project == before and len(editor._undo) == history and editor.revision == revision
    editor.roll_linked_av(selection, cut)
    assert editor.project == expected and editor.revision == revision + 1
    assert len(editor._undo) == history + 1
    assert editor.project.av_links == before.av_links and editor.project.clips[4:] == before.clips[4:]
    for old, new in zip(before.clips[:4], editor.project.clips[:4]):
        assert new.duration * new.speed - (new.source_out - new.source_in) == (
            old.duration * old.speed - (old.source_out - old.source_in))
        assert (new.fade_in, new.fade_out, new.audio_mix_anchor, new.source_audio_enabled) == (
            old.fade_in, old.fade_out, old.audio_mix_anchor, old.source_audio_enabled)
    segments, scene = expected_frames(expected, pictures)
    actual_scene, actual = lossless_av(engine, editor.project, av_root / 'after.mkv')
    assert_delivery_frames(actual_scene, scene)
    assert_delivery_frames(actual_scene[cut - 1:cut + 1], scene[cut - 1:cut + 1])
    for clip_id in (ROLL_IDS[0], ROLL_IDS[2]):
        np.testing.assert_array_equal(segment_pixels(engine, editor.project, editor._clip(clip_id)), segments[clip_id])
    np.testing.assert_array_max_ulp(samples(actual), samples(wanted), maxulp=2)
    assert len(actual) == sample(before.duration_frames, fps) * 8
    assert len(actual) == len(old_audio) and actual != old_audio
    # Global budgets meet at the NEW cut, including fractional sample phase.
    assert sample(cut, fps) - sample(3, fps) + sample(25, fps) - sample(cut, fps) == sample(25, fps) - sample(3, fps)
    tail = sample(cut, fps)
    assert not np.count_nonzero(samples(actual)[tail - max(1, sample(1, fps) // 4):tail])
    if offset > 0:
        # The leading silence precedes any unrelated audio contribution.
        assert not np.count_nonzero(samples(actual)[sample(3, fps):sample(4, fps)])
    if fps == Fraction(60000, 1001):
        # Source audio EOF is independently decoded, not a nominal duration;
        # here only the fixed A1 contribution remains after the right audio EOF.
        alone = deepcopy(expected)
        for clip in alone.clips:
            if clip.id != 'fixed-audio': clip.muted = True
        lo, hi = sample(19, fps), sample(24, fps)
        np.testing.assert_array_max_ulp(samples(actual)[lo:hi], samples(raw_unit_pcm(alone, raw))[lo:hi], maxulp=2)
    saved = proof_path(av_root, 'rolled.lumen'); editor.project.save(saved)
    assert Project.load(saved) == expected
    assert editor.undo() and editor.project == before
    undo_scene, undo_audio = lossless_av(engine, editor.project, av_root / 'undo.mkv')
    assert undo_audio == old_audio; assert_delivery_frames(undo_scene, old_scene)
    assert editor.redo() and editor.project == expected
    redo_scene, redo_audio = lossless_av(engine, editor.project, av_root / 'redo.mkv')
    assert redo_audio == actual; assert_delivery_frames(redo_scene, scene)
    assert source_paths(av_root) == paths
    assert_sources_untouched(paths, fingerprints)
    mark_verified(av_root)


@pytest.mark.parametrize('fps,speeds,delta', [
    (Fraction(30000, 1001), (Fraction(3, 2), Fraction(2, 3)), 1),
    (Fraction(24000, 1001), (Fraction(2, 3), Fraction(3, 2)), -1),
], ids=['29.97-fast-left-slow-right', '23.976-slow-left-fast-right'])
def test_joint_roll_mixed_retime_established_waveform_and_independent_envelopes(av_root, engine, fps, speeds, delta):
    inputs = sources(av_root, fps, -.113)
    editor = pair_project(inputs, fps, speeds)
    before = deepcopy(editor.project); expected = requested_project(before, 13 + delta)
    pictures = {source.media.id: source.pictures for source in inputs}
    paths = source_paths(av_root); fingerprints = source_fingerprints(paths)
    # Establish the unchanged old Rubber Band semantics against the complete
    # old dual-stream render, before relying on that waveform for the new map.
    old_reference = independent_pcm(unlinked(before))
    record_result(av_root, 'retime-before-expected', before, frames=expected_frames(before, pictures)[1],
                  audio=old_reference, basis='Old project; established schema40 retime waveform plus scalar envelope/mix')
    old_scene, old_audio = lossless_av(engine, before, av_root / 'retime-before.mkv')
    np.testing.assert_array_max_ulp(samples(old_audio), samples(old_reference), maxulp=1)
    assert_delivery_frames(old_scene, expected_frames(before, pictures)[1])
    wanted = independent_pcm(unlinked(expected))
    record_result(av_root, 'retime-after-expected', expected, frames=expected_frames(expected, pictures)[1],
                  audio=wanted, basis='Old project plus requested cut; established schema40 retime waveform plus scalar envelope/mix')
    assert wanted != old_audio
    editor.roll_linked_av(ROLL_IDS, 13 + delta)
    assert editor.project == expected
    actual_scene, actual = lossless_av(engine, editor.project, av_root / 'retime-after.mkv')
    np.testing.assert_array_max_ulp(samples(actual), samples(wanted), maxulp=1)
    segments, scene = expected_frames(expected, pictures)
    assert_delivery_frames(actual_scene, scene)
    assert_delivery_frames(actual_scene[12 + delta:14 + delta], scene[12 + delta:14 + delta])
    for clip_id in (ROLL_IDS[0], ROLL_IDS[2]):
        np.testing.assert_array_equal(segment_pixels(engine, editor.project, editor._clip(clip_id)), segments[clip_id])
    assert len(actual) == sample(before.duration_frames, fps) * 8
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == expected
    assert source_paths(av_root) == paths
    assert_sources_untouched(paths, fingerprints)
    mark_verified(av_root)


def test_joint_roll_actual_delivery_fingerprints_and_unrelated_cache_undo(av_root, engine):
    fps = Fraction(30000, 1001)
    inputs = sources(av_root, fps, -.113)
    editor = pair_project(inputs, fps)
    before = deepcopy(editor.project); expected = requested_project(before, 14)
    pictures = {source.media.id: source.pictures for source in inputs}
    paths = source_paths(av_root); fingerprints = source_fingerprints(paths)
    def cached_segments(project):
        media = {item.id: item for item in project.media}
        return {clip.id: engine._graded_segment(media[clip.media_id], clip, project, None)
                for clip in project.clips if clip.track in (0, 1)}
    record_result(av_root, 'delivery-before-expected', before, frames=expected_frames(before, pictures)[1],
                  basis='Old project frame-ID oracle')
    record_result(av_root, 'delivery-after-expected', expected, frames=expected_frames(expected, pictures)[1],
                  basis='Old project plus requested cut frame-ID oracle')
    old_segments = cached_segments(before)
    old_preview = engine.preview(before)
    assert_delivery_frames(decode(old_preview, rgb16=False), expected_frames(before, pictures)[1])
    editor.roll_linked_av(ROLL_IDS, 14)
    new_segments = cached_segments(editor.project)
    assert all(old_segments[key] != new_segments[key] for key in (ROLL_IDS[0], ROLL_IDS[2]))
    assert old_segments['fixed-picture'] == new_segments['fixed-picture']
    preview = engine.preview(editor.project)
    assert preview != old_preview and engine.preview(editor.project) == preview
    exported = engine.render(editor.project, proof_path(av_root, 'rolled-export.mp4'))
    audio = []
    scene = expected_frames(expected, pictures)[1]
    for path in (preview, exported):
        frames = decode(path, rgb16=False)
        assert_delivery_frames(frames, scene)
        assert_delivery_frames(frames[13:15], scene[13:15])
        decoded = ff('-i', path, '-map', '0:a:0', '-vn', '-ar', '48000',
                     '-ac', '2', '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1')
        assert sample(expected.duration_frames, fps) <= len(samples(decoded)) < sample(expected.duration_frames, fps) + 1024
        record_result(av_root, 'delivery-preview-actual' if path == preview else 'delivery-export-actual',
                      editor.project, frames=frames, audio=decoded, basis='Actual AAC delivery; not exact raw PCM')
        audio.append(decoded)
    assert audio[0] == audio[1]  # Same AAC delivery path, not exact PCM equality.
    saved = proof_path(av_root, 'delivery.lumen'); editor.project.save(saved)
    reopened = Project.load(saved); assert reopened == expected
    assert engine.preview(reopened) == preview
    assert editor.undo() and engine.preview(editor.project) == old_preview
    assert cached_segments(editor.project) == old_segments
    assert editor.redo() and engine.preview(editor.project) == preview
    assert cached_segments(editor.project) == new_segments
    assert source_paths(av_root) == paths
    assert_sources_untouched(paths, fingerprints)
    mark_verified(av_root)


def test_muted_roles_remain_structural_and_unmute_uses_new_exact_cut(av_root, engine):
    fps = Fraction(30000, 1001)
    inputs = sources(av_root, fps, -.113)
    editor = pair_project(inputs, fps)
    for clip_id in ('left-audio', 'right-audio'):
        editor.update_clip(clip_id, muted=True)
    before = deepcopy(editor.project); expected = requested_project(before, 14)
    paths = source_paths(av_root); fingerprints = source_fingerprints(paths)
    _, old_audio = lossless_av(engine, before, av_root / 'muted-before.mkv')
    editor.roll_linked_av(ROLL_IDS, 14)
    assert editor.project == expected
    _, muted_audio = lossless_av(engine, editor.project, av_root / 'muted-after.mkv')
    assert muted_audio == old_audio  # Only fixed unrelated A1 is audible.
    for clip_id in ('left-audio', 'right-audio'):
        editor.update_clip(clip_id, muted=False)
        next(clip for clip in expected.clips if clip.id == clip_id).muted = False
    assert editor.project == expected
    assert all(not editor._clip(clip_id).source_audio_enabled for clip_id in (ROLL_IDS[0], ROLL_IDS[2]))
    _, actual = lossless_av(engine, editor.project, av_root / 'unmuted.mkv')
    raw = {source.media.id: source.audio for source in inputs}
    np.testing.assert_array_max_ulp(samples(actual), samples(raw_unit_pcm(expected, raw)), maxulp=2)
    assert actual != old_audio
    assert source_paths(av_root) == paths
    assert_sources_untouched(paths, fingerprints)
    mark_verified(av_root)


@pytest.mark.parametrize('muted', [False, True], ids=['audible-right', 'muted-right'])
def test_joint_roll_rejects_historical_tie_crossing_real_float32_cancellation(av_root, engine, muted):
    tiny = 2 ** -25
    media = [constant_av(av_root, 'tiny', tiny, -tiny),
             constant_av(av_root, 'positive', 1, -1),
             constant_av(av_root, 'negative', -1, 1)]
    anchor = AudioMixAnchor(0, 0, 'shared-history')
    project = Project(width=WIDTH, height=HEIGHT, fps_num=24, media=media, clips=[
        Clip('left-video', media[0].id, 0, 3, 0, 4, source_audio_enabled=False),
        Clip('left-audio', media[0].id, 2, 3, 0, 4, muted=True, audio_mix_anchor=anchor),
        Clip('right-video', media[0].id, 0, 7, 4, 4, source_audio_enabled=False),
        Clip('right-audio', media[0].id, 2, 7, 4, 4, muted=muted, audio_mix_anchor=anchor),
        Clip('positive', media[1].id, 1, 5, 0, 6, audio_mix_anchor=anchor),
        Clip('negative', media[2].id, 3, 6, 0, 5, audio_mix_anchor=anchor)])
    editor = Editor(project)
    editor.link_av_clips(*ROLL_IDS[:2]); editor.link_av_clips(*ROLL_IDS[2:])
    before = deepcopy(editor.project); damaged = requested_project(before, 5)
    paths = source_paths(av_root); fingerprints = source_fingerprints(paths)
    # Deliberately construct the otherwise-valid bad order without using the
    # new editor method, proving the reason for rejection even when muted now.
    old_potential, new_potential = deepcopy(before), deepcopy(damaged)
    for candidate in (old_potential, new_potential):
        next(clip for clip in candidate.clips if clip.id == 'right-audio').muted = False
    _, old_audio = lossless_av(engine, old_potential, av_root / 'old-order.mkv')
    _, new_audio = lossless_av(engine, new_potential, av_root / 'bad-order.mkv')
    lo, hi = sample(8, Fraction(24)), sample(10, Fraction(24))
    np.testing.assert_array_equal(samples(old_audio)[lo:hi], np.tile([tiny, -tiny], (hi - lo, 1)))
    assert not np.count_nonzero(samples(new_audio)[lo:hi])
    state = deepcopy((editor.project, editor._undo, editor._redo, editor.revision))
    for operation in (editor.preview_roll_linked_av, editor.roll_linked_av):
        with pytest.raises(ValueError, match='(?i)(mix|order)'):
            operation(ROLL_IDS, 5)
        assert (editor.project, editor._undo, editor._redo, editor.revision) == state
    assert source_paths(av_root) == paths
    assert_sources_untouched(paths, fingerprints)
    mark_verified(av_root)

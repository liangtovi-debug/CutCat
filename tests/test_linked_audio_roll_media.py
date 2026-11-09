"""Full-scene J/L proof with independent OLD-source 1x stereo PCM arithmetic.

Every render consumes production V and A outputs in one FFV1/float32-PCM mux.
The entire decoded video scene must be byte-identical across audio-only edits,
not merely a cut neighborhood or an audio-only/nullsink render. Expected audio
comes from original synthetic sample arrays plus old project/request algebra;
no production timing, rebase, candidate or envelope evaluator supplies it.

The reused raw_unit_pcm test oracle allows at most two float32 ULPs for scalar
libm/gain rounding, never a temporal, block-size, lossy-codec or DSP tolerance.
This module intentionally covers 1x only, not Rubber Band transient continuity.
Test-owned sources/muxes/JSON are retained under tmp_path, including failures.
The existing engine fixture's transient-cache lifecycle is not changed.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import hashlib
import json

import numpy as np
import pytest

from lumen.media import ffmpeg_available, probe
from lumen.model import AV_LINK_ALIGNMENT, AV_LINK_AFFINE_ALIGNMENT, Project
from lumen.timing import ClipTimeMap
from test_linked_av_pcm import (MarkedSource, assert_sources_untouched,
                                source_fingerprints)
from test_linked_av_roll_media import (ROLL_IDS, expected_frames, pair_project,
                                      raw_unit_pcm)
from test_retime_video import (HEIGHT, WIDTH, assert_delivery_frames, decode,
                              engine, ff, frame_id_rgb)
from test_source_audio_separation_pcm import samples

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='Local FFmpeg/ffprobe required')
RATES = (Fraction(24), Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001))


def boundary(frame, fps):
    value = Fraction(frame) * 48000 / fps
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def short_sources(root, fps, offset):
    """Two tiny independently decoded frame-ID sources with nonperiodic stereo.

    Forty 64x48 frames provide handles and one late unchanged picture. The
    right PCM stream really ends after 13 project frames while video continues;
    nominal media duration therefore cannot stand in for actual audio EOF.
    """
    root.mkdir()
    output, paths = [], []
    for side, audio_offset in (('left', offset), ('right', -.113)):
        pictures = frame_id_rgb(40)
        if side == 'right': pictures = pictures[::-1].copy()
        count = boundary(40 if side == 'left' else 13, fps)
        n = np.arange(count, dtype=np.int64)
        source_frame = n * fps.numerator // (48000 * fps.denominator)
        raw = np.column_stack((
            ((source_frame * 17 + n % 127) % 1024 - 512) / 1024,
            ((source_frame * 31 + n % 251) % 1024 - 512) / 2048,
        )).astype('<f4')
        raw[::997, 0] = .75; raw[::1423, 1] = -.625
        if side == 'right': raw = np.column_stack((-raw[:, 1], raw[:, 0]/2)).astype('<f4')
        picture_path, path = root / f'{side}-picture.mkv', root / f'{side}-av.mkv'
        ff('-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-s', f'{WIDTH}x{HEIGHT}',
           '-r', str(fps), '-i', 'pipe:0', '-an', '-c:v', 'ffv1', '-level', '3',
           '-pix_fmt', 'gbrp16le', '-threads', '1', '-color_range', 'pc',
           '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'rgb',
           picture_path, data=pictures.astype('<u2').tobytes())
        ff('-i', picture_path, '-f', 'f32le',
           '-ar', '48000', '-ac', '2', '-i', 'pipe:0', '-map', '0:v', '-map', '1:a',
           '-af', f'asetpts=PTS+({audio_offset})/TB', '-c:v', 'copy', '-c:a', 'pcm_f32le',
           '-avoid_negative_ts', 'make_non_negative', path, data=raw.tobytes())
        media = probe(path)
        assert media.has_video and media.has_audio
        assert media.audio_offset == pytest.approx(audio_offset, abs=1e-9)
        np.testing.assert_array_equal(decode(path), pictures)
        decoded = ff('-i', path, '-map', '0:a:0', '-vn', '-ar', '48000',
                     '-ac', '2', '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1')
        np.testing.assert_array_equal(samples(decoded), raw)
        output.append(MarkedSource(media, pictures, raw)); paths.extend((picture_path, path))
    return tuple(output), tuple(paths)


def requested_audio_project(old, cut, *, reset=False):
    """Expected document from OLD records and cut; V records are not visited."""
    expected = deepcopy(old)
    previous = {clip.id: clip for clip in old.clips}
    if reset: cut = previous['left-video'].end
    delta = cut - previous['left-audio'].end
    if delta == 0 and not reset: return expected
    for clip in expected.clips:
        if clip.id not in ('left-audio', 'right-audio'): continue
        before = previous[clip.id]; right = clip.id == 'right-audio'
        origin = Fraction(before.source_in) + (delta * before.speed if right else 0)
        endpoint = before.source_out + (0 if right else delta * before.speed)
        clip.start = cut if right else before.start
        clip.duration = before.duration + (-delta if right else delta)
        clip.source_in, clip.time_map = origin, ClipTimeMap(origin, endpoint, before.speed)
        if before.speed == 1 and origin.denominator == 1 and endpoint-origin == clip.duration:
            clip.source_in, clip.time_map = int(origin), None
        if right:
            clip.motion_keyframes = [replace(key, frame=key.frame-delta) for key in before.motion_keyframes]
            clip.grade.qualifier.window_keyframes = [replace(key, frame=key.frame-delta)
                for key in before.grade.qualifier.window_keyframes]
            shift = boundary(cut, old.fps) - boundary(before.start, old.fps)
            clip.audio_gain_keyframes = [replace(key, sample=key.sample-shift)
                                        for key in before.audio_gain_keyframes]
    records = {clip.id: clip for clip in expected.clips}
    modes = []
    for link in old.av_links:
        video, audio = (records[key] for key in link.clip_ids)
        equal = (video.start, video.duration, video.timing) == (audio.start, audio.duration, audio.timing)
        modes.append(replace(link, alignment=AV_LINK_ALIGNMENT if reset and equal else AV_LINK_AFFINE_ALIGNMENT))
    expected.av_links = modes
    expected.validate()
    return expected


def render_scene(renderer, project, path):
    """Full production dual-output mux; keep the actual mux for diagnosis."""
    inputs, graph, _ = renderer._compile(project)
    ff(*inputs, '-filter_complex_threads', '1', '-filter_complex', graph,
       '-map', '[vout]', '-map', '[aout]', '-c:v', 'ffv1', '-c:a', 'pcm_f32le', path)
    pictures = decode(path, rgb16=False)
    audio = ff('-i', path, '-map', '0:a:0', '-vn', '-ar', '48000', '-ac', '2',
               '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1')
    return pictures, audio


def verified_scene(renderer, project, expected, raw, path, *, picture_reference=None):
    wanted = raw_unit_pcm(expected, raw)
    frames, audio = render_scene(renderer, project, path)
    # Persist compact actual/independent-expected evidence before assertions.
    path.with_suffix('.json').write_text(json.dumps({
        'basis': 'Old project/request + original source arrays + independent scalar PCM oracle',
        'fps': str(project.fps), 'timeline_frames': project.duration_frames,
        'decoded_frames': len(frames), 'frame_sha256': hashlib.sha256(frames.tobytes()).hexdigest(),
        'actual_sample_frames': len(audio)//8, 'expected_sample_frames': len(wanted)//8,
        'actual_pcm_sha256': hashlib.sha256(audio).hexdigest(),
        'expected_pcm_sha256': hashlib.sha256(wanted).hexdigest(),
        'pcm_max_ulp': 2, 'mux_bytes': path.stat().st_size,
    }, indent=2, sort_keys=True) + '\n')
    assert len(frames) == project.duration_frames == expected.duration_frames
    assert len(audio) == len(wanted) == boundary(expected.duration_frames, expected.fps) * 8
    if picture_reference is not None:
        np.testing.assert_array_equal(frames, picture_reference)
    np.testing.assert_array_max_ulp(samples(audio), samples(wanted), maxulp=2)
    return frames, audio


def make_editor(sources, fps):
    editor = pair_project(sources, fps)
    # A stable tied historical anchor is legal because the third contribution
    # begins at 15, after every tested right-A cut (12 or 14). Order is explicit.
    editor._clip('fixed-audio').audio_mix_anchor = deepcopy(editor._clip('right-audio').audio_mix_anchor)
    editor.project.validate()
    return editor


@pytest.mark.parametrize('fps,offset', list(zip(RATES, [0, -.113, .137, .137])),
    ids=['24', '23.976-early', '29.97-leading', '59.94-leading'])
@pytest.mark.parametrize('delta', [-1, 1], ids=['J-earlier-audio', 'L-later-audio'])
def test_audio_only_roll_preserves_every_video_frame_and_matches_independent_pcm(tmp_path, engine, fps, offset, delta):
    sources, paths = short_sources(tmp_path/'sources', fps, offset)
    fingerprint = source_fingerprints(paths)
    editor = make_editor(sources, fps); before = deepcopy(editor.project)
    raw = {source.media.id: source.audio for source in sources}
    pictures = {source.media.id: source.pictures for source in sources}
    cut = 13+delta; wanted = requested_audio_project(before, cut)
    assert raw_unit_pcm(wanted, raw) != raw_unit_pcm(before, raw)
    old_scene, old_audio = verified_scene(engine, before, before, raw, tmp_path/'before.mkv')
    # Independently identify all pictures, including blanks and the fixed tail.
    assert_delivery_frames(old_scene, expected_frames(before, pictures)[1])
    old_v = tuple(editor._clip(key) for key in ('left-video', 'right-video'))
    editor.roll_linked_audio(tuple(reversed(ROLL_IDS)), cut)
    assert editor.project == wanted
    assert all(editor._clip(clip.id) is clip for clip in old_v)
    assert [editor._clip(clip.id) for clip in old_v] == [before.clips[0], before.clips[2]]
    new_scene, actual = verified_scene(engine, editor.project, wanted, raw,
        tmp_path/'after.mkv', picture_reference=old_scene)
    assert new_scene.tobytes() == old_scene.tobytes() and actual != old_audio
    assert editor.project.duration_frames == before.duration_frames == 29
    assert editor._clip('left-video').end == editor._clip('right-video').start == 13
    assert editor._clip('left-audio').end == editor._clip('right-audio').start == cut
    assert all(link.alignment == AV_LINK_AFFINE_ALIGNMENT for link in editor.project.av_links)
    shift = boundary(cut, fps) - boundary(13, fps)
    if shift != boundary(delta, fps):
        wrong = deepcopy(wanted)
        wrong.clips[3].audio_gain_keyframes = [replace(key, sample=key.sample-boundary(delta, fps))
                                             for key in before.clips[3].audio_gain_keyframes]
        assert raw_unit_pcm(wrong, raw) != raw_unit_pcm(wanted, raw), 'Fixture must expose S(delta) rebasing'
    # Half-frame source tail is silence before the new cut, not repeated PCM.
    tail = boundary(cut, fps)
    assert not np.count_nonzero(samples(actual)[tail-max(1, boundary(1, fps)//4):tail])
    if offset > 0:
        assert not np.count_nonzero(samples(actual)[boundary(3, fps):boundary(4, fps)])
    # The actual short right stream is exhausted by frame 24 at every rate;
    # only the separate, fixed third-track contribution may remain there.
    alone = deepcopy(wanted)
    for clip in alone.clips:
        if clip.id != 'fixed-audio': clip.muted = True
    lo, hi = boundary(24, fps), boundary(25, fps)
    np.testing.assert_array_max_ulp(samples(actual)[lo:hi], samples(raw_unit_pcm(alone, raw))[lo:hi], maxulp=2)
    assert_sources_untouched(paths, fingerprint)


def test_muted_potential_j_l_j_reset_reopen_undo_redo_keep_full_scene(tmp_path, engine):
    fps = Fraction(30000, 1001)
    sources, paths = short_sources(tmp_path/'sources', fps, .137)
    fingerprint = source_fingerprints(paths)
    raw = {source.media.id: source.audio for source in sources}
    editor = make_editor(sources, fps); editor._clip('right-audio').muted = True
    before = deepcopy(editor.project)
    picture, _ = verified_scene(engine, before, before, raw, tmp_path/'before-muted.mkv')
    j = requested_audio_project(before, 12)
    editor.roll_linked_audio(ROLL_IDS, 12); assert editor.project == j
    _, j_muted = verified_scene(engine, editor.project, j, raw, tmp_path/'j-muted.mkv', picture_reference=picture)
    project_path = tmp_path/'split-edit.lumen'; editor.project.save(project_path)
    loaded = Project.load(project_path); assert loaded == j
    _, reopened = verified_scene(engine, loaded, j, raw, tmp_path/'reopened-j.mkv', picture_reference=picture)
    assert reopened == j_muted
    # Unmute must reveal the correctly shifted gain keys, not stale hidden A.
    editor.update_clip('right-audio', muted=False)
    j_audible = deepcopy(j); j_audible.clips[3].muted = False
    assert editor.project == j_audible
    _, j_audio = verified_scene(engine, editor.project, j_audible, raw, tmp_path/'j-unmuted.mkv', picture_reference=picture)
    assert j_audio != j_muted
    later = requested_audio_project(j_audible, 14)
    editor.roll_linked_audio(ROLL_IDS, 14); assert editor.project == later
    _, l_audio = verified_scene(engine, editor.project, later, raw, tmp_path/'l.mkv', picture_reference=picture)
    assert l_audio != j_audio
    returned = requested_audio_project(later, 12)
    editor.roll_linked_audio(ROLL_IDS, 12); assert editor.project == returned == j_audible
    _, repeated = verified_scene(engine, editor.project, returned, raw, tmp_path/'j-again.mkv', picture_reference=picture)
    assert repeated == j_audio
    aligned = requested_audio_project(returned, 13, reset=True)
    editor.align_linked_audio(ROLL_IDS); assert editor.project == aligned
    assert all(link.alignment == AV_LINK_ALIGNMENT for link in editor.project.av_links)
    _, reset_audio = verified_scene(engine, editor.project, aligned, raw, tmp_path/'aligned.mkv', picture_reference=picture)
    initial_audible = deepcopy(before); initial_audible.clips[3].muted = False
    assert aligned == initial_audible
    assert editor.undo() and editor.project == returned
    _, undo_audio = verified_scene(engine, editor.project, returned, raw, tmp_path/'undo.mkv', picture_reference=picture)
    assert undo_audio == j_audio
    assert editor.redo() and editor.project == aligned
    _, redo_audio = verified_scene(engine, editor.project, aligned, raw, tmp_path/'redo.mkv', picture_reference=picture)
    assert redo_audio == reset_audio
    assert_sources_untouched(paths, fingerprint)

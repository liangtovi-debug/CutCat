"""Real local-media acceptance for the exact constant-speed picture mapping.

The temporal oracle is independent of RenderEngine and the timing helpers:
output k selects floor(O + k * R) on the project-normalized source grid.
FFV1 RGB48 intermediates are compared byte-for-byte. H.264 playback/export
is deliberately checked with robust frame IDs and explicit color tolerances;
these delivery encodes are not claimed to be lossless.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import json
import math
import subprocess

import numpy as np
import pytest

from lumen.media import ffmpeg_available, ffmpeg_binary, ffprobe_binary, probe
from lumen.model import Clip, Editor, Media, Project
from lumen.render import RenderEngine
from lumen.timing import ClipTimeMap

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')

WIDTH, HEIGHT = 64, 48
PROJECT_RATES = (Fraction(24000, 1001), Fraction(30000, 1001))
PLAYBACK_RATES = (Fraction(1, 4), Fraction(1, 2), Fraction(3, 2), Fraction(4))


def ff(*args, data=None):
    return subprocess.run(
        [ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-threads', '1',
         *map(str, args)], input=data, capture_output=True, check=True,
        timeout=40,
    ).stdout


def decode(path, *, rgb16=True, filters=None):
    fmt, dtype = ('rgb48le', '<u2') if rgb16 else ('rgb24', 'u1')
    args = ['-protocol_whitelist', 'file,pipe', '-i', path, '-map', '0:V:0', '-an']
    if filters:
        args += ['-vf', filters]
    raw = ff(*args, '-threads', '1', '-fps_mode', 'passthrough',
             '-pix_fmt', fmt, '-f', 'rawvideo', 'pipe:1')
    pixels = np.frombuffer(raw, dtype=dtype)
    assert pixels.size % (HEIGHT * WIDTH * 3) == 0
    return pixels.reshape(-1, HEIGHT, WIDTH, 3)


def frame_id_rgb(count=80):
    """Original RGB16 arrays: eight wide ID bars plus three-channel ramps."""
    frames = np.empty((count, HEIGHT, WIDTH, 3), dtype=np.uint16)
    for index, frame in enumerate(frames):
        frame[:] = np.array([24 + index * 2, 216 - index * 2, 48 + index],
                            dtype=np.uint16) * 257
        for bit in range(8):
            frame[:24, bit * 8:(bit + 1) * 8] = (232 if index & (1 << bit) else 20) * 257
    return frames


@dataclass
class FrameSource:
    media: Media
    original: np.ndarray


def make_source(path, fps, *, timestamp_filter=None):
    original = frame_id_rgb()
    args = ['-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-s', f'{WIDTH}x{HEIGHT}',
            '-r', str(fps), '-protocol_whitelist', 'file,pipe', '-i', 'pipe:0']
    if timestamp_filter:
        args += ['-vf', timestamp_filter]
    ff(*args, '-fps_mode', 'passthrough', '-an', '-c:v', 'ffv1', '-level', '3',
       '-pix_fmt', 'gbrp16le', '-threads', '1', '-color_range', 'pc',
       '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'rgb',
       path, data=original.astype('<u2').tobytes())
    # Prove the source is genuinely lossless before using it as an oracle.
    np.testing.assert_array_equal(decode(path), original)
    return FrameSource(probe(path), original)


@pytest.fixture(scope='module')
def frame_sources(tmp_path_factory):
    directory = tmp_path_factory.mktemp('retime-frame-ids')
    return {fps: make_source(directory / f'{fps.numerator}-{fps.denominator}.mkv', fps)
            for fps in (*PROJECT_RATES, Fraction(24), Fraction(60))}


@pytest.fixture
def engine():
    renderer = RenderEngine(timeout=40)
    try:
        yield renderer
    finally:
        renderer.close()


def expected_indices(origin, endpoint, speed):
    """No production timing helper or compiler is used in the oracle."""
    length = math.ceil((endpoint - origin) / speed)
    positions = [origin + k * speed for k in range(length)]
    assert all(origin <= position < endpoint for position in positions)
    return [position.numerator // position.denominator for position in positions]


def make_project(source, fps, *, origin=Fraction(3), endpoint=Fraction(32),
                 speed=Fraction(1), start=0):
    duration = math.ceil((endpoint - origin) / speed)
    clip = Clip('picture', source.media.id, 0, start, 0, duration,
                time_map=ClipTimeMap(origin, endpoint, speed))
    project = Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
                      fps_den=fps.denominator, media=[deepcopy(source.media)], clips=[clip])
    project.validate()
    return project


def segment_pixels(engine, project, clip=None):
    clip = clip or project.clips[0]
    media = next(media for media in project.media if media.id == clip.media_id)
    path = engine._graded_segment(media, clip, project, None)
    return decode(path)


def normalized_source(source, fps):
    # Establish the documented FFmpeg source-grid normalization independently
    # from the renderer; selection and split phase are then tested separately.
    return decode(source.media.path,
                  filters=f'setpts=PTS-STARTPTS,fps=fps={fps}:start_time=0')


def decoded_ids(frames):
    """Wide bar interiors survive 4:2:0 conversion and both delivery CRFs."""
    ids = np.zeros(len(frames), dtype=np.int64)
    for bit in range(8):
        level = frames[:, 5:19, bit * 8 + 2:bit * 8 + 6].mean(axis=(1, 2, 3))
        ids += (level > 126).astype(np.int64) << bit
    return ids.tolist()


def assert_delivery_frames(actual, expected_rgb16):
    assert len(actual) == len(expected_rgb16)
    expected = np.rint(expected_rgb16.astype(np.float64) / 257).astype(np.uint8)
    assert decoded_ids(actual) == decoded_ids(expected)
    # Compare only flat RGB ramp interiors, away from chroma subsampling edges.
    # All three color channels must agree to 6/255; H.264 is not lossless.
    actual_patch = np.median(actual[:, 30:44, 8:56], axis=(1, 2))
    expected_patch = np.median(expected[:, 30:44, 8:56], axis=(1, 2))
    np.testing.assert_allclose(actual_patch, expected_patch, rtol=0, atol=6)


@pytest.mark.parametrize('fps', PROJECT_RATES, ids=['23.976', '29.97'])
@pytest.mark.parametrize('speed', PLAYBACK_RATES, ids=['quarter', 'half', 'one-and-half', 'four'])
def test_lossless_retime_matches_original_frame_id_oracle(frame_sources, engine, fps, speed):
    source = frame_sources[fps]
    origin, endpoint = Fraction(3), Fraction(32)
    project = make_project(source, fps, origin=origin, endpoint=endpoint, speed=speed)
    expected = source.original[expected_indices(origin, endpoint, speed)]
    actual = segment_pixels(engine, project)
    assert len(actual) == project.clips[0].duration
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize('fps', PROJECT_RATES, ids=['23.976', '29.97'])
@pytest.mark.parametrize('speed', PLAYBACK_RATES, ids=['quarter', 'half', 'one-and-half', 'four'])
def test_fractional_origin_repeated_split_is_pixel_identical(frame_sources, engine, fps, speed):
    source = frame_sources[fps]
    origin, endpoint = Fraction(9, 8), Fraction(249, 8)
    project = make_project(source, fps, origin=origin, endpoint=endpoint, speed=speed, start=5)
    whole = segment_pixels(engine, project)
    expected = source.original[expected_indices(origin, endpoint, speed)]
    np.testing.assert_array_equal(whole, expected)
    editor = Editor(deepcopy(project))
    left, right = editor.split_clip('picture', 8)  # Local frame 3, an odd cut.
    assert left.timing.source_out == right.timing.source_in == origin + 3 * speed
    middle, last = editor.split_clip(right.id, 12)  # Original local frame 7.
    assert middle.timing.source_out == last.timing.source_in == origin + 7 * speed
    assert last.timing.source_out == endpoint
    assert all(clip.timing.source_in.denominator > 1 for clip in editor.project.clips)
    joined = np.concatenate([segment_pixels(engine, editor.project, clip)
                             for clip in editor.project.clips])
    np.testing.assert_array_equal(joined, whole)
    assert len(joined) == project.clips[0].duration


@pytest.mark.parametrize('endpoint,speed,expected_ids', [
    (Fraction(31), Fraction(2), list(range(0, 31, 2))),
    (Fraction(31, 4), Fraction(3, 2), [0, 1, 3, 4, 6, 7]),
    (Fraction(31), Fraction(4), list(range(0, 31, 4))),
])
def test_partial_final_interval_holds_last_picture_without_extra_frame(
        frame_sources, engine, endpoint, speed, expected_ids):
    fps = PROJECT_RATES[0]
    source = frame_sources[fps]
    project = make_project(source, fps, origin=Fraction(0), endpoint=endpoint, speed=speed)
    actual = segment_pixels(engine, project)
    np.testing.assert_array_equal(actual, source.original[expected_ids])
    assert len(actual) == math.ceil(endpoint / speed)


@pytest.mark.parametrize('fps', PROJECT_RATES, ids=['23.976', '29.97'])
@pytest.mark.parametrize('speed', PLAYBACK_RATES, ids=['quarter', 'half', 'one-and-half', 'four'])
def test_actual_preview_export_and_still_share_frame_mapping(
        tmp_path, frame_sources, engine, fps, speed):
    source = frame_sources[fps]
    origin, endpoint, start = Fraction(5, 4), Fraction(93, 4), 3
    project = make_project(source, fps, origin=origin, endpoint=endpoint, speed=speed, start=start)
    expected = source.original[expected_indices(origin, endpoint, speed)]
    # Exercise split segments through the full compiler too. A later marker
    # creates a real gap in which the partial tail must stop holding.
    editor = Editor(project)
    editor.split_clip('picture', start + 3)
    project.clips.append(Clip('later-marker', source.media.id, 0,
                              start + len(expected) + 3, 40, 2))
    preview_path = engine.preview(project)
    exported_path = engine.render(project, tmp_path / 'delivery.mp4')
    for path in (preview_path, exported_path):
        rendered = decode(path, rgb16=False)
        assert len(rendered) == start + len(expected) + 5
        assert rendered[:start].max() <= 3, 'Picture leaked before the timeline clip gate'
        assert_delivery_frames(rendered[start:start + len(expected)], expected)
        assert rendered[start + len(expected):-2].max() <= 3, 'Tail leaked beyond the clip gate'
        assert_delivery_frames(rendered[-2:], source.original[40:42])
        metadata = probe(path)
        assert (metadata.fps_num, metadata.fps_den) == (fps.numerator, fps.denominator)
    # Still viewing uses the cached actual preview path; decode the real PNG.
    local = min(7, len(expected) - 1)
    png = engine.frame(project, start + local, width=WIDTH)
    assert png.startswith(b'\x89PNG\r\n\x1a\n')
    raw = ff('-f', 'image2pipe', '-i', 'pipe:0', '-frames:v', '1', '-threads', '1',
             '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1', data=png)
    still = np.frombuffer(raw, dtype=np.uint8).reshape(1, HEIGHT, WIDTH, 3)
    assert_delivery_frames(still, expected[local:local + 1])
    assert engine.preview(project) == preview_path


@pytest.mark.parametrize('source_rate,fps', [
    (Fraction(24), PROJECT_RATES[1]), (Fraction(60), PROJECT_RATES[0]),
])
def test_mixed_native_rates_keep_project_grid_phase_after_split(
        frame_sources, engine, source_rate, fps):
    source = frame_sources[source_rate]
    origin, endpoint, speed = Fraction(7, 4), Fraction(105, 4), Fraction(3, 2)
    project = make_project(source, fps, origin=origin, endpoint=endpoint, speed=speed)
    normalized = normalized_source(source, fps)
    expected = normalized[expected_indices(origin, endpoint, speed)]
    whole = segment_pixels(engine, project)
    np.testing.assert_array_equal(whole, expected)
    editor = Editor(project)
    editor.split_clip('picture', 7)
    joined = np.concatenate([segment_pixels(engine, editor.project, clip)
                             for clip in editor.project.clips])
    np.testing.assert_array_equal(joined, whole)


@pytest.mark.parametrize('timestamp_filter', [
    'setpts=PTS+2/TB',
    'settb=1/60000,setpts=2500*N+1000*floor(N/2)+120000',
], ids=['nonzero-pts', 'vfr-nonzero-pts'])
def test_timestamp_origin_and_vfr_normalization_remain_split_stable(
        tmp_path, engine, timestamp_filter):
    fps = PROJECT_RATES[1]
    source = make_source(tmp_path / 'timestamped.mkv', Fraction(24),
                         timestamp_filter=timestamp_filter)
    timestamps = subprocess.run(
        [ffprobe_binary(), '-v', 'error', '-protocol_whitelist', 'file,pipe',
         '-select_streams', 'V:0', '-show_frames', '-show_entries',
         'frame=best_effort_timestamp_time', '-of', 'json', source.media.path],
        capture_output=True, text=True, check=True, timeout=20,
    )
    pts = np.array([float(frame['best_effort_timestamp_time'])
                    for frame in json.loads(timestamps.stdout)['frames']])
    assert len(pts) == len(source.original) and pts[0] >= 1.99
    if 'floor' in timestamp_filter:
        assert np.ptp(np.diff(pts)) > .01, 'The VFR fixture became constant-rate'
    else:
        assert np.ptp(np.diff(pts)) < .002
    origin, endpoint, speed = Fraction(5, 4), Fraction(121, 4), Fraction(3, 2)
    project = make_project(source, fps, origin=origin, endpoint=endpoint, speed=speed)
    normalized = normalized_source(source, fps)
    expected = normalized[expected_indices(origin, endpoint, speed)]
    whole = segment_pixels(engine, project)
    np.testing.assert_array_equal(whole, expected)
    editor = Editor(project)
    editor.split_clip('picture', 7)
    np.testing.assert_array_equal(np.concatenate([
        segment_pixels(engine, editor.project, clip) for clip in editor.project.clips]), whole)


def test_speed_and_endpoint_changes_invalidate_both_picture_and_preview_cache(
        frame_sources, engine):
    fps = PROJECT_RATES[1]
    source = frame_sources[fps]
    editor = Editor(make_project(source, fps, origin=Fraction(1),
                                 endpoint=Fraction(15), speed=Fraction(4)))

    def paths():
        project, clip = editor.project, editor.project.clips[0]
        return (engine._graded_segment(source.media, clip, project, None),
                engine.preview(project))

    before = paths()
    assert paths() == before
    editor.set_clip_speed('picture', Fraction(7, 2))
    assert editor.project.clips[0].duration == 4  # Duration is unchanged.
    changed_speed = paths()
    assert all(a != b for a, b in zip(before, changed_speed))
    np.testing.assert_array_equal(decode(changed_speed[0]), source.original[[1, 4, 8, 11]])
    # The endpoint changes neither D nor the selected frame IDs, but remains
    # part of each cache identity (and later audio/partial-tail semantics).
    editor.trim_clip_source('picture', Fraction(1), Fraction(57, 4))
    assert editor.project.clips[0].duration == 4
    changed_endpoint = paths()
    assert all(a != b for a, b in zip(changed_speed, changed_endpoint))
    assert editor.undo()
    assert paths() == changed_speed
    assert editor.undo()
    assert paths() == before


def test_fractional_source_trim_keeps_exact_oracle_and_exclusive_tail(frame_sources, engine):
    fps = PROJECT_RATES[0]
    source = frame_sources[fps]
    editor = Editor(make_project(source, fps, speed=Fraction(3, 2)))
    origin, endpoint = Fraction(7, 4), Fraction(101, 4)
    editor.trim_clip_source('picture', origin, endpoint)
    clip = editor.project.clips[0]
    assert (clip.timing.source_in, clip.timing.source_out) == (origin, endpoint)
    np.testing.assert_array_equal(segment_pixels(engine, editor.project),
                                  source.original[expected_indices(origin, endpoint, Fraction(3, 2))])


@pytest.mark.parametrize('fps', PROJECT_RATES, ids=['23.976', '29.97'])
@pytest.mark.parametrize('speed', [Fraction(1, 2), Fraction(3, 2), Fraction(4)],
                         ids=['half', 'one-and-half', 'four'])
def test_exact_slip_preserves_partial_tail_and_changes_picture_preview_export(
        tmp_path, frame_sources, engine, fps, speed):
    source = frame_sources[fps]
    origin, endpoint, new_origin = Fraction(9, 8), Fraction(251, 8), Fraction(57, 8)
    start = 3
    editor = Editor(make_project(source, fps, origin=origin, endpoint=endpoint,
                                 speed=speed, start=start))
    before = deepcopy(editor.project)
    original_clip = before.clips[0]
    # Each selected speed leaves a real fractional final interval. A slip
    # must not replace it with the larger D * speed source span.
    assert original_clip.duration * speed > endpoint - origin

    def paths():
        project, clip = editor.project, editor.project.clips[0]
        return (engine._graded_segment(source.media, clip, project, None),
                engine.preview(project))

    old_paths = paths()
    old_expected = source.original[expected_indices(origin, endpoint, speed)]
    np.testing.assert_array_equal(decode(old_paths[0]), old_expected)
    editor.slip_clip('picture', new_origin)
    clip = editor.project.clips[0]
    shifted_endpoint = endpoint + new_origin - origin
    assert (clip.source_in, clip.source_out, clip.duration, clip.start, clip.speed) == (
        new_origin, shifted_endpoint, original_clip.duration, start, speed)
    assert clip.source_out - clip.source_in == endpoint - origin
    changed_paths = paths()
    assert all(old != new for old, new in zip(old_paths, changed_paths))
    expected = source.original[expected_indices(new_origin, shifted_endpoint, speed)]
    assert not np.array_equal(expected, old_expected)
    np.testing.assert_array_equal(decode(changed_paths[0]), expected)
    exported = engine.render(editor.project, tmp_path / 'slipped.mp4')
    for path in (changed_paths[1], exported):
        actual = decode(path, rgb16=False)
        assert len(actual) == start + original_clip.duration
        assert actual[:start].max() <= 3, 'Slip moved the clip timeline gate'
        assert_delivery_frames(actual[start:], expected)
    assert editor.undo() and editor.project == before
    assert paths() == old_paths
    assert editor.redo()
    assert paths() == changed_paths


@pytest.mark.parametrize('source_rate,fps', [
    (Fraction(24), PROJECT_RATES[1]), (Fraction(60), PROJECT_RATES[0]),
])
def test_integral_one_times_preserves_pre_retime_mixed_rate_selection(
        frame_sources, engine, source_rate, fps):
    source = frame_sources[source_rate]
    project = make_project(source, fps, origin=Fraction(5), endpoint=Fraction(16))
    # Frozen pre-speed temporal path: an integral 1x source trim resets its
    # source-native phase before fps normalization. Preserve that compatibility
    # explicitly rather than silently replacing it with the new retime rule.
    start = format(float(Fraction(5) / fps), '.12g')
    length = format(float(Fraction(11) / fps), '.12g')
    old_filters = (f'setpts=PTS-STARTPTS,trim=start={start}:duration={length},'
                   f'setpts=PTS-STARTPTS,fps=fps={fps}:start_time=0')
    expected = decode(source.media.path, filters=old_filters)
    np.testing.assert_array_equal(segment_pixels(engine, project), expected)

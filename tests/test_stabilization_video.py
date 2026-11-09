"""Real local-media stabilization through the shared preview/export engine.

Motion comes from actual libvidstab passes. A unique colored marker provides
an independent pixel-domain jitter measurement; lossless FFV1 stages prove
source-grid trim/speed/split equivalence without H.264 tolerance hiding errors.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import math
from pathlib import Path
import subprocess
import threading

import numpy as np
import pytest

from lumen import color
from lumen.media import Media, ffmpeg_available, ffmpeg_binary, probe
from lumen.model import Clip, Editor, Grade, Project
from lumen.render import RenderCancelled, RenderEngine
from lumen.stabilization_settings import StabilizationSettings
from lumen.timing import ClipTimeMap

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
WIDTH, HEIGHT, COUNT = 320, 240, 52
RATES = (Fraction(24000, 1001), Fraction(30000, 1001))


def ff(*args, data=None):
    return subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-threads', '1',
                           *map(str, args)], input=data, capture_output=True, check=True, timeout=45).stdout


def decode(path, *, width=WIDTH, height=HEIGHT, rgb16=True, data=None):
    fmt, dtype = ('rgb48le', '<u2') if rgb16 else ('rgb24', 'u1')
    raw = ff('-protocol_whitelist', 'file,pipe', '-i', path, '-map', '0:V:0', '-an',
             '-threads', '1', '-fps_mode', 'passthrough', '-pix_fmt', fmt, '-f', 'rawvideo', 'pipe:1', data=data)
    pixels = np.frombuffer(raw, dtype=dtype)
    assert pixels.size % (height * width * 3) == 0
    return pixels.reshape(-1, height, width, 3)


@dataclass
class ShakeSource:
    media: Media
    original: np.ndarray
    shifts: np.ndarray
    fps: Fraction


def original_picture():
    # Coarse detail is friendly to block motion estimation. Fine 16-bit ramps
    # ensure the delivered pixels cannot have passed through the 8-bit proxy.
    rng = np.random.default_rng(421)
    gray = np.repeat(np.repeat(rng.integers(3000, 56000, (20, 32), dtype=np.uint16), 12, 0), 10, 1)
    for _ in range(2):
        gray = ((gray.astype(np.uint32) + np.roll(gray, 1, 0) + np.roll(gray, 1, 1)) // 3).astype(np.uint16)
    yy, xx = np.mgrid[:HEIGHT, :WIDTH]
    gray = gray + ((xx * 13 + yy * 7) % 251).astype(np.uint16)
    rgb = np.repeat(gray[..., None], 3, axis=2)
    # No other picture pixel has red-green separation: its weighted centroid
    # tracks shake independently of detector/matrix/trajectory implementation.
    rgb[108:132, 148:172] = (60000, 5000, 5000)
    return rgb


def write_source(path, fps):
    base = original_picture()
    shifts = np.array([(round(5 * math.sin(i * 1.2)), round(4 * math.cos(i * .9))) for i in range(COUNT)])
    frames = np.array([np.roll(base, (int(y), int(x)), (0, 1)) for x, y in shifts])
    ff('-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-s', f'{WIDTH}x{HEIGHT}', '-r', str(fps),
       '-protocol_whitelist', 'file,pipe', '-i', 'pipe:0', '-an', '-c:v', 'ffv1', '-level', '3',
       '-pix_fmt', 'gbrp16le', '-threads', '1', '-color_range', 'pc', '-color_primaries', 'bt709',
       '-color_trc', 'bt709', '-colorspace', 'rgb', path, data=frames.astype('<u2').tobytes())
    np.testing.assert_array_equal(decode(path), frames)
    return ShakeSource(probe(path), frames, shifts, fps)


@pytest.fixture(scope='module')
def sources(tmp_path_factory):
    filters = subprocess.run([ffmpeg_binary(), '-hide_banner', '-filters'], capture_output=True, text=True, timeout=20)
    if 'vidstabdetect' not in filters.stdout or 'vidstabtransform' not in filters.stdout:
        pytest.skip('External FFmpeg lacks libvidstab')
    root = tmp_path_factory.mktemp('stabilization-video')
    return {fps: write_source(root / f'shake {fps.numerator}-{fps.denominator}.mkv', fps) for fps in RATES}


@pytest.fixture
def engine():
    renderer = RenderEngine(timeout=60)
    try:
        yield renderer
    finally:
        renderer.close()


def project_for(source, *, origin=Fraction(0), endpoint=Fraction(COUNT - 1), speed=Fraction(1),
                enabled=True, strength=1., width=WIDTH, height=HEIGHT, grade=None):
    # Keep one tail frame outside the selected clip: Matroska's millisecond
    # duration metadata can round below the last full fractional-FPS interval.
    clip = Clip('picture', source.media.id, 0, 0, 0, math.ceil((endpoint - origin) / speed),
                time_map=ClipTimeMap(origin, endpoint, speed),
                stabilization=StabilizationSettings(enabled=enabled, strength=strength,
                                                     smoothing=5, max_zoom=1.2, border='crop'),
                grade=grade or Grade())
    result = Project(width=width, height=height, fps_num=source.fps.numerator, fps_den=source.fps.denominator,
                     media=[deepcopy(source.media)], clips=[clip])
    result.validate()
    return result


def segment(engine, project, clip=None):
    clip = clip or project.clips[0]
    media = next(media for media in project.media if media.id == clip.media_id)
    return decode(engine._graded_segment(media, clip, project, None), width=project.width, height=project.height)


def marker_centers(frames):
    image = frames.astype(np.float64)
    weight = np.maximum(0., image[..., 0] - image[..., 1] - 10000.)
    weight[:, :60] = weight[:, -60:] = 0
    weight[:, :, :80] = weight[:, :, -80:] = 0
    yy, xx = np.mgrid[:frames.shape[1], :frames.shape[2]]
    total = weight.sum(axis=(1, 2))
    assert (total > 0).all(), 'The independent feature marker disappeared'
    return np.column_stack(((weight * xx).sum(axis=(1, 2)) / total,
                            (weight * yy).sum(axis=(1, 2)) / total))


def test_real_enabled_engine_reduces_pixel_marker_shake_and_preserves_source(sources, engine):
    source = sources[RATES[0]]
    before_hash = hashlib.sha256(Path(source.media.path).read_bytes()).hexdigest()
    disabled = segment(engine, project_for(source, enabled=False))
    enabled = segment(engine, project_for(source))
    np.testing.assert_array_equal(disabled, source.original[:len(disabled)])
    before = marker_centers(disabled)
    after = marker_centers(enabled)
    np.testing.assert_allclose(before - before[0], source.shifts[:len(before)] - source.shifts[0], atol=1e-8)
    # Exclude the documented source-edge taper. The detector and full render
    # must actually reduce shake; merely obtaining nonidentity matrices fails.
    before_jitter = np.std(np.diff(before[8:-8], axis=0), axis=0)
    after_jitter = np.std(np.diff(after[8:-8], axis=0), axis=0)
    assert (before_jitter > 2.).all()
    assert (after_jitter < before_jitter * .3).all(), (before_jitter, after_jitter)
    assert np.unique(enabled[20, 50:180, 40:130, 0]).size > 1000
    assert hashlib.sha256(Path(source.media.path).read_bytes()).hexdigest() == before_hash


@pytest.mark.parametrize('fps', RATES, ids=['23.976', '29.97'])
@pytest.mark.parametrize('speed', (Fraction(1, 2), Fraction(3, 2), Fraction(4)), ids=['half', 'one-and-half', 'four'])
def test_actual_source_grid_fractional_retime_and_repeated_split_are_lossless_identical(sources, engine, fps, speed):
    source = sources[fps]
    whole_source = segment(engine, project_for(source))
    origin, endpoint = Fraction(9, 8), Fraction(193, 8)
    project = project_for(source, origin=origin, endpoint=endpoint, speed=speed)
    duration = math.ceil((endpoint - origin) / speed)
    # Independent floor oracle; no production time-map sampler or matrix lookup.
    positions = [origin + k * speed for k in range(duration)]
    indices = [p.numerator // p.denominator for p in positions]
    actual = segment(engine, project)
    np.testing.assert_array_equal(actual, whole_source[indices])
    editor = Editor(deepcopy(project))
    _, right = editor.split_clip('picture', 2)
    editor.split_clip(right.id, 4)
    joined = np.concatenate([segment(engine, editor.project, clip) for clip in editor.project.clips])
    np.testing.assert_array_equal(joined, actual)
    assert all(clip.timing.source_in.denominator > 1 for clip in editor.project.clips)


@pytest.mark.parametrize('fps', RATES, ids=['23.976', '29.97'])
def test_exact_slip_keeps_source_wide_stabilization_phase_and_reuses_analysis(
        sources, engine, fps, monkeypatch):
    from lumen import stabilization_analysis
    source = sources[fps]
    whole_source = segment(engine, project_for(source))
    plan_keys = set(engine._stabilization_plans)
    assert len(plan_keys) == 1
    # A real whole-source analysis is already cached. Changing only selected
    # source endpoints must reuse it rather than restarting motion estimation.
    monkeypatch.setattr(stabilization_analysis, '_two_passes',
                        lambda *args, **kwargs: pytest.fail('Slip must reuse source-wide analysis'))
    origin, endpoint, speed = Fraction(9, 8), Fraction(195, 8), Fraction(3, 2)
    editor = Editor(project_for(source, origin=origin, endpoint=endpoint, speed=speed))
    before = segment(engine, editor.project)
    settings = deepcopy(editor.project.clips[0].stabilization)
    new_origin = Fraction(81, 8)
    editor.slip_clip('picture', new_origin)
    clip = editor.project.clips[0]
    shifted_endpoint = endpoint + new_origin - origin
    assert clip.source_out == shifted_endpoint
    assert clip.duration == len(before) == math.ceil((endpoint - origin) / speed)
    assert clip.source_out - clip.source_in < clip.duration * speed
    assert clip.stabilization == settings
    positions = [new_origin + k * speed for k in range(clip.duration)]
    indices = [p.numerator // p.denominator for p in positions]
    actual = segment(engine, editor.project)
    np.testing.assert_array_equal(actual, whole_source[indices])
    assert not np.array_equal(actual, before)
    assert set(engine._stabilization_plans) == plan_keys
    editor.split_clip('picture', 3)
    joined = np.concatenate([segment(engine, editor.project, item)
                             for item in editor.project.clips])
    np.testing.assert_array_equal(joined, actual)
    assert set(engine._stabilization_plans) == plan_keys


def test_disabled_and_zero_strength_are_exact_and_never_request_analysis(sources, engine, monkeypatch):
    from lumen import stabilization_render
    source = sources[RATES[0]]
    ordinary = project_for(source, enabled=False, endpoint=Fraction(10))
    baseline = segment(engine, ordinary)
    monkeypatch.setattr(stabilization_render, 'prepare_stabilization',
                        lambda *args, **kwargs: pytest.fail('Bypass must not analyze or create a crop'))
    disabled = project_for(source, enabled=False, strength=.73, endpoint=Fraction(10))
    zero = project_for(source, enabled=True, strength=0., endpoint=Fraction(10))
    np.testing.assert_array_equal(segment(engine, disabled), baseline)
    np.testing.assert_array_equal(segment(engine, zero), baseline)
    np.testing.assert_array_equal(baseline, source.original[:10])


def test_real_float_warp_precedes_grade_and_letterbox_padding(sources, engine, monkeypatch):
    from lumen import stabilization_math
    source = sources[RATES[0]]
    actual_sample, actual_grade = stabilization_math.resample_float_rgb, color.apply_grade
    warps, grades = [], []

    def inspect_warp(rgb, inverse, **kwargs):
        assert rgb.shape == (HEIGHT, WIDTH, 3) and rgb.dtype == np.float32
        assert np.unique(rgb[..., 0]).size > 1000
        stable = actual_sample(rgb, inverse, **kwargs)
        warps.append((rgb.copy(), stable.copy(), inverse.copy()))
        return stable

    def inspect_grade(rgb, grade, **kwargs):
        grades.append(rgb.copy())
        return actual_grade(rgb, grade, **kwargs)

    monkeypatch.setattr(stabilization_math, 'resample_float_rgb', inspect_warp)
    monkeypatch.setattr(color, 'apply_grade', inspect_grade)
    project = project_for(source, origin=Fraction(15), endpoint=Fraction(18), height=320,
                          grade=Grade(lift=[.01, .02, .03], gamma=[1.1, 1., .9]))
    rendered = segment(engine, project)
    assert len(warps) == len(grades) == len(rendered) == 3
    for (original, stable, inverse), before_grade in zip(warps, grades):
        assert not np.array_equal(inverse, np.eye(3))
        np.testing.assert_array_equal(before_grade[40:280], stable)
        np.testing.assert_array_equal(before_grade[:40], 0.)
        np.testing.assert_array_equal(before_grade[280:], 0.)
        # Genuine fractional samples reach grading without RGB16 or RGB8
        # re-encoding between the sampler and the existing grade boundary.
        assert np.max(np.abs(stable * 65535. - np.rint(stable * 65535.))) > .1
        assert not np.array_equal(original, stable)
    for before_grade, result in zip(grades, rendered):
        expected = np.rint(np.clip(actual_grade(before_grade, project.clips[0].grade), 0, 1) * 65535.).astype(np.uint16)
        np.testing.assert_array_equal(result, expected)


def test_actual_preview_export_and_still_share_stabilized_fractional_mapping(sources, engine, tmp_path):
    source = sources[RATES[1]]
    project = project_for(source, origin=Fraction(81, 8), endpoint=Fraction(177, 8), speed=Fraction(3, 2))
    expected = segment(engine, project)
    editor = Editor(project)
    editor.split_clip('picture', 3)
    preview = engine.preview(project)
    export = engine.render(project, tmp_path / 'stabilized.mp4')
    for path in (preview, export):
        actual = decode(path)
        assert len(actual) == len(expected) == 8
        # Delivery H.264 is lossy. The interior picture and unique marker must
        # still track the source-anchored lossless intermediate consistently.
        error = np.abs(actual[:, 25:-25, 25:-25].astype(float) - expected[:, 25:-25, 25:-25]) / 65535.
        assert error.mean() < .012
        np.testing.assert_allclose(marker_centers(actual), marker_centers(expected), atol=.35)
    png = engine.frame(project, 4, width=WIDTH)
    still = decode('pipe:0', rgb16=False, data=png)
    preview_rgb = decode(preview, rgb16=False)
    np.testing.assert_array_equal(still[0], preview_rgb[4])


def test_cancel_during_actual_float_warp_cleans_partial_stage(sources, engine, monkeypatch):
    from lumen import stabilization_math
    source = sources[RATES[0]]
    project = project_for(source, origin=Fraction(12), endpoint=Fraction(17))
    cancel = threading.Event()
    actual_sample = stabilization_math.resample_float_rgb

    def interrupt(*args, **kwargs):
        cancel.set()
        return actual_sample(*args, **kwargs)

    monkeypatch.setattr(stabilization_math, 'resample_float_rgb', interrupt)
    with pytest.raises(RenderCancelled):
        engine._graded_segment(source.media, project.clips[0], project, cancel)
    assert not list(Path(engine._cache_dir.name).glob('*.partial.mkv'))


def test_mixed_fractional_source_and_project_rates_normalize_before_warp_and_retime(sources, engine, monkeypatch):
    from lumen import stabilization_math
    source = sources[RATES[0]]  # 23.976 source, rendered on the 29.97 grid.
    project_rate = RATES[1]
    independent = ff('-protocol_whitelist', 'file,pipe', '-i', source.media.path, '-map', '0:V:0', '-an',
                     '-vf', f'setpts=PTS-STARTPTS,fps=fps={project_rate}:start_time=0',
                     '-threads', '1', '-fps_mode', 'passthrough', '-pix_fmt', 'rgb48le',
                     '-f', 'rawvideo', 'pipe:1')
    normalized = np.frombuffer(independent, '<u2').reshape(-1, HEIGHT, WIDTH, 3)
    assert len(normalized) > len(source.original)
    observed = []
    actual_sample = stabilization_math.resample_float_rgb

    def inspect(rgb, inverse, **kwargs):
        observed.append(hashlib.sha256(rgb.tobytes()).hexdigest())
        return actual_sample(rgb, inverse, **kwargs)

    monkeypatch.setattr(stabilization_math, 'resample_float_rgb', inspect)
    whole = project_for(source)
    whole.fps_num, whole.fps_den = project_rate.numerator, project_rate.denominator
    whole.validate()
    reference = segment(engine, whole)
    expected_inputs = [hashlib.sha256((frame.astype(np.float32) / np.float32(65535)).tobytes()).hexdigest()
                       for frame in normalized[:len(reference)]]
    assert observed == expected_inputs, 'Warp received source-native or trim-rephased frames'
    origin, endpoint, speed = Fraction(5, 4), Fraction(113, 4), Fraction(3, 2)
    trimmed = project_for(source, origin=origin, endpoint=endpoint, speed=speed)
    trimmed.fps_num, trimmed.fps_den = project_rate.numerator, project_rate.denominator
    trimmed.validate()
    positions = [origin + k * speed for k in range(math.ceil((endpoint - origin) / speed))]
    indices = [p.numerator // p.denominator for p in positions]
    actual = segment(engine, trimmed)
    np.testing.assert_array_equal(actual, reference[indices])
    editor = Editor(trimmed)
    editor.split_clip('picture', 3)
    np.testing.assert_array_equal(np.concatenate([segment(engine, trimmed, clip) for clip in trimmed.clips]), actual)


def test_real_combined_rotation_translation_reduces_two_independent_marker_paths(sources, engine, tmp_path):
    # The two widely separated, uniquely colored markers reveal rotation sign
    # and rotation-center errors that a center-only translation marker cannot.
    base = original_picture()
    base[108:132, 148:172] = 25000
    base[70:90, 70:90] = (60000, 4000, 4000)
    base[150:170, 230:250] = (4000, 4000, 60000)
    base = base.astype(np.float64)
    yy, xx = np.mgrid[:HEIGHT, :WIDTH].astype(float)
    count = 44
    angles = .018 * np.sin(np.arange(count) * 1.1)
    shifts = np.array([(4 * math.sin(i * 1.2), 3 * math.cos(i * .9)) for i in range(count)])
    frames = []
    for angle, (dx, dy) in zip(angles, shifts):
        # Independent inverse render of known camera pose: rotate source about
        # c, then translate it. No production stabilization helper is used.
        x = math.cos(angle) * (xx - 160 - dx) + math.sin(angle) * (yy - 120 - dy) + 160
        y = -math.sin(angle) * (xx - 160 - dx) + math.cos(angle) * (yy - 120 - dy) + 120
        x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
        fx, fy = x - x0, y - y0
        frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float64)
        for ox, oy, weight in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)),
                               (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
            sx, sy = x0 + ox, y0 + oy
            valid = (sx >= 0) & (sx < WIDTH) & (sy >= 0) & (sy < HEIGHT)
            frame += base[np.clip(sy, 0, HEIGHT - 1), np.clip(sx, 0, WIDTH - 1)] * (weight * valid)[..., None]
        frames.append(np.rint(frame).astype(np.uint16))
    frames = np.array(frames)
    path, fps = tmp_path / 'combined shake.mkv', RATES[1]
    ff('-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-s', f'{WIDTH}x{HEIGHT}', '-r', str(fps),
       '-protocol_whitelist', 'file,pipe', '-i', 'pipe:0', '-an', '-c:v', 'ffv1', '-level', '3',
       '-pix_fmt', 'gbrp16le', '-threads', '1', '-color_range', 'pc', '-color_primaries', 'bt709',
       '-color_trc', 'bt709', '-colorspace', 'rgb', path, data=frames.astype('<u2').tobytes())
    source = ShakeSource(probe(path), frames, shifts, fps)
    before = segment(engine, project_for(source, endpoint=Fraction(count - 1), enabled=False))
    after = segment(engine, project_for(source, endpoint=Fraction(count - 1)))

    def paths(pictures):
        pictures = pictures.astype(float)
        centers = []
        for channel, other in ((0, 2), (2, 0)):
            weight = np.maximum(0., pictures[..., channel] - np.maximum(pictures[..., 1], pictures[..., other]))
            total = weight.sum(axis=(1, 2))
            assert (total > 0).all()
            centers.append(np.column_stack(((weight * xx).sum(axis=(1, 2)) / total,
                                           (weight * yy).sum(axis=(1, 2)) / total)))
        red, blue = centers
        delta = blue - red
        return (red + blue) / 2., np.unwrap(np.arctan2(delta[:, 1], delta[:, 0]))

    before_center, before_angle = paths(before)
    after_center, after_angle = paths(after)
    # Establish that the independently rendered fixture really has the known
    # combined motion, rather than accepting an accidentally translation-only test.
    np.testing.assert_allclose(before_angle - before_angle[0], angles[:len(before)], atol=3e-4)
    interior = slice(8, -8)
    translation_before = np.std(np.diff(before_center[interior], axis=0), axis=0)
    translation_after = np.std(np.diff(after_center[interior], axis=0), axis=0)
    rotation_before = np.std(np.diff(before_angle[interior]))
    rotation_after = np.std(np.diff(after_angle[interior]))
    assert (translation_before > 1.5).all() and rotation_before > .01
    assert (translation_after < translation_before * .45).all(), (translation_before, translation_after)
    assert rotation_after < rotation_before * .45, (rotation_before, rotation_after)
    print(f'Combined actual-engine marker jitter: translation {translation_before.tolist()} -> '
          f'{translation_after.tolist()} pixels/frame; rotation {rotation_before:.6f} -> {rotation_after:.6f} radians/frame')

"""Independent, real RGB16 acceptance for the picker's restoration input stage.

The reference uses deliberately scalar float64 equations, not apply_grade,
normalize_input, apply_restoration, or any renderer/color helper as its oracle.
Every media fixture is synthetic, local, and decoded back before testing.
"""
from __future__ import annotations

from copy import deepcopy
from bisect import bisect_right
from fractions import Fraction
import math
import subprocess
import threading

import numpy as np
import pytest

from lumen.color_picker_source import (PickerSourceCancelled, PickerSourceError,
                                       capture_picker_snapshot)
from lumen.media import ffmpeg_available, ffmpeg_binary, probe
from lumen.model import Clip, Grade, Project
from lumen.restoration_settings import RestorationSettings
from lumen.timing import ClipTimeMap

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def ff(*args, data=None):
    return subprocess.run([ffmpeg_binary(), '-hide_banner', '-v', 'error',
                           '-nostdin', '-y', '-threads', '1', *map(str, args)],
                          input=data, capture_output=True, check=True, timeout=45).stdout


def make_source(tmp_path, frames, *, fps=6, name='picker-restoration', transfer='bt709'):
    frames = np.asarray(frames, dtype='<u2')
    if frames.ndim == 3:
        frames = np.repeat(frames[None], 3, axis=0)
    count, height, width, _ = frames.shape
    planar = np.moveaxis(frames[..., [1, 2, 0]], -1, 1).astype('<u2')
    path = tmp_path / f'{name}.mkv'
    tags = ['-colorspace', 'rgb', '-color_range', 'pc', '-color_trc', transfer,
            '-color_primaries', 'bt709']
    ff('-f', 'rawvideo', '-pix_fmt', 'gbrp16le', '-s', f'{width}x{height}',
       '-r', fps, *tags, '-i', 'pipe:0', '-frames:v', count,
       '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'gbrp16le', '-threads', '1',
       *tags, path, data=planar.tobytes())
    assert ff('-i', path, '-pix_fmt', 'gbrp16le', '-f', 'rawvideo', 'pipe:1') == planar.tobytes()
    media = probe(path)
    return Project(width=width, height=height, fps_num=fps, media=[media],
                   clips=[Clip('picture', media.id, 0, 0, 0, count)]), frames


def picture(height=20, width=40):
    yy, xx = np.mgrid[:height, :width]
    # Repeated weak edges activate deblock; small colored deviations activate
    # denoise and sharpen, and actual low 16-bit codes must survive ingress.
    step = .026 * ((xx // 8) % 2)
    noise = (((xx * 11 + yy * 7) % 13) - 6) * .00065
    return np.rint(65535 * np.stack([
        .27 + step + noise, .43 + .7 * step - .8 * noise,
        .61 + .4 * step + .6 * noise], axis=-1)).astype('<u2')


def normalize_reference(rgb, encoding):
    def convert(x):
        if encoding == 'rec709':
            return float(x)
        linear = x if encoding == 'linear_rec709' else (
            x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4)
        return 4.5 * linear if linear < .018 else 1.099 * linear ** .45 - .099
    return np.array([convert(float(x)) for x in np.asarray(rgb).flat]).reshape(np.shape(rgb))


def denoise_reference(rgb, amount):
    image = np.asarray(rgb, dtype=float)
    result = np.empty_like(image)
    height, width = image.shape[:2]
    radius, blend = .03 + .06 * amount, .9 * amount
    for y in range(height):
        for x in range(width):
            center = image[y, x]
            total = 1.
            shift = np.zeros(3)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == dy == 0:
                        continue
                    delta = image[min(max(y + dy, 0), height - 1),
                                  min(max(x + dx, 0), width - 1)] - center
                    weight = (1 - min(max(abs(v) for v in delta), radius) ** 2 / radius ** 2) ** 2
                    weight *= .75 if dx == 0 or dy == 0 else .375
                    shift += np.clip(delta, -radius, radius) * weight
                    total += weight
            result[y, x] = center + shift * blend / total
    return result


def sharpen_reference(rgb, amount):
    image = np.asarray(rgb, dtype=float)
    result = np.empty_like(image)
    height, width = image.shape[:2]
    for y in range(height):
        for x in range(width):
            center = image[y, x]
            residual, lower, upper = np.zeros(3), center.copy(), center.copy()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == dy == 0:
                        continue
                    neighbor = image[min(max(y + dy, 0), height - 1),
                                     min(max(x + dx, 0), width - 1)]
                    residual += np.clip(center - neighbor, -1, 1) * (2 if dx == 0 or dy == 0 else 1)
                    lower, upper = np.minimum(lower, neighbor), np.maximum(upper, neighbor)
            residual /= 16
            delta = np.sign(residual) * np.maximum(abs(residual) - .001, 0) * .65 * amount
            result[y, x] = np.clip(center + np.clip(delta, -.015 * amount, .015 * amount), lower, upper)
    return result


def deblock_axis_reference(rgb, amount):
    image = np.asarray(rgb, dtype=float)
    height, width = image.shape[:2]
    if height < 3 or width < 14:
        return image.copy()
    candidate = np.zeros((height, width), bool)
    residual = np.zeros_like(image)
    for y in range(height):
        for right in range(3, width - 2):
            p2, p1, p0, q0, q1, q2 = image[y, right - 3:right + 3]
            left_slope, right_slope = p0 - p1, q1 - q0
            jump = q0 - p0 - (left_slope + right_slope) / 2
            magnitude = max(abs(jump))
            variation = max(max(abs(v)) for v in (left_slope, right_slope, p1 - p2, q2 - q1))
            candidate[y, right] = .004 <= magnitude <= .06 and variation <= min(.012, magnitude * .3)
            residual[y, right] = jump
    supported = candidate.copy()
    for y in range(height):
        for right in range(3, width - 2):
            supported[y, right] &= ((y > 0 and candidate[y - 1, right])
                                    or (y + 1 < height and candidate[y + 1, right]))
    boundaries, previous = [], -1
    for right in range(3, width - 2):
        if np.count_nonzero(supported[:, right]) >= max(3, math.ceil(height / 4)) and right - previous >= 4:
            boundaries.append(right)
            previous = right
    if len(boundaries) < 3:
        return image.copy()
    result = image.copy()
    for right in boundaries:
        for y in range(height):
            if not supported[y, right]:
                continue
            delta = np.clip(residual[y, right] * .22 * amount, -.008 * amount, .008 * amount)
            for offset, weight in ((-2, .35), (-1, 1), (0, -1), (1, -.35)):
                result[y, right + offset] += delta * weight
    return result


def restoration_reference(rgb, settings):
    result = np.asarray(rgb, dtype=float).copy()
    if not settings.enabled:
        return result
    if settings.deblock:
        result = deblock_axis_reference(result, settings.deblock)
        result = deblock_axis_reference(result.transpose(1, 0, 2), settings.deblock).transpose(1, 0, 2)
    if settings.denoise:
        result = denoise_reference(result, settings.denoise)
    if settings.sharpen:
        result = sharpen_reference(result, settings.sharpen)
    return result


def primary_reference(rgb, grade):
    result = np.asarray(rgb, dtype=float).copy()
    if not grade.enabled:
        return result
    result *= 2 ** grade.exposure
    result = (result - .5) * grade.contrast + .5
    result += [grade.temperature * .25 + grade.tint * .125,
               -grade.tint * .25, -grade.temperature * .25 + grade.tint * .125]
    for channel, points in enumerate((grade.red_curve, grade.green_curve, grade.blue_curve)):
        def curve(value):
            left = min(max(bisect_right([p[0] for p in points], value) - 1, 0), len(points) - 2)
            (x0, y0), (x1, y1) = points[left:left + 2]
            return y0 + (value - x0) * (y1 - y0) / (x1 - x0)
        values = result[..., channel]
        result[..., channel] = np.array([curve(x) for x in values.flat]).reshape(values.shape)
    return result


@pytest.fixture
def source(tmp_path):
    frames = np.stack([picture().astype(np.int32) + index * 731 for index in range(10)]).astype('<u2')
    return make_source(tmp_path, frames)


@pytest.mark.parametrize('settings', [
    RestorationSettings(enabled=True, deblock=.8),
    RestorationSettings(enabled=True, denoise=.75),
    RestorationSettings(enabled=True, sharpen=.7),
    RestorationSettings(enabled=True, deblock=.8, denoise=.75, sharpen=.7),
], ids=['deblock', 'denoise', 'sharpen', 'all-passes'])
def test_rgb16_restoration_matches_independent_scalar_reference(source, settings):
    project, frames = source
    project.clips[0].restoration = settings
    raw = frames[2].astype(float) / 65535
    expected = restoration_reference(raw, settings)
    snapshot = capture_picker_snapshot(project, 'picture', 2)
    np.testing.assert_allclose(snapshot.frame, expected, rtol=0, atol=2.5e-7)
    assert np.max(abs(expected - raw)) > .0002, 'Fixture must activate the selected pass'
    coarse = restoration_reference(np.rint(raw * 255) / 255, settings)
    assert np.max(abs(snapshot.frame - coarse)) > .0005, 'Do not silently round source to RGB8'
    assert snapshot.frame.dtype == np.float32 and snapshot.valid_mask.all()
    with pytest.raises(ValueError):
        snapshot.frame.setflags(write=True)


@pytest.mark.parametrize('enabled', [True, False], ids=['creative-on', 'creative-off'])
def test_normalize_once_restore_then_primary_before_selective_lut(tmp_path, enabled):
    project, frames = make_source(tmp_path, picture(), transfer='iec61966-2-1')
    settings = RestorationSettings(enabled=True, deblock=.8, denoise=.8, sharpen=.65)
    grade = Grade(enabled=enabled, input_encoding='srgb', exposure=.3, contrast=1.15,
                  temperature=.15, tint=.1, red_curve=[[0., 0.], [.5, .65], [1., 1.]])
    grade.hue_hue_curve = [[0., .2], [1., .2]]
    grade.hue_sat_curve = [[0., .3], [1., .3]]
    lut = tmp_path / 'swap-red-blue.cube'
    lut.write_text('LUT_3D_SIZE 2\n' + ''.join(
        f'{b} {g} {r}\n' for b in (0, 1) for g in (0, 1) for r in (0, 1)))
    grade.lut_path, grade.lut_strength = str(lut), 1.
    project.clips[0].grade, project.clips[0].restoration = grade, settings
    saved_grade, saved_settings = deepcopy(grade), deepcopy(settings)
    raw = frames[0].astype(float) / 65535
    normalized = normalize_reference(raw, 'srgb')
    expected = primary_reference(restoration_reference(normalized, settings), grade)
    snapshot = capture_picker_snapshot(project, 'picture', 0)
    np.testing.assert_allclose(snapshot.frame, expected, rtol=0, atol=6e-7)
    wrong_order = primary_reference(normalize_reference(restoration_reference(raw, settings), 'srgb'), grade)
    assert np.max(abs(expected - wrong_order)) > 2e-5
    double_normalized = primary_reference(normalize_reference(restoration_reference(normalized, settings), 'srgb'), grade)
    assert np.max(abs(expected - double_normalized)) > .01
    assert grade == saved_grade and settings == saved_settings
    if not enabled:
        assert np.max(abs(snapshot.frame - normalized)) > .0003


def test_padding_never_enters_restoration_but_true_black_picture_does(tmp_path):
    image = picture()
    image[:3] = [2201, 2701, 3301]  # Low-valued edge makes pad leakage measurable.
    image[8, 12] = 0                # Real black picture pixel must remain valid.
    project, frames = make_source(tmp_path, image)
    project.height = 40
    project.clips[0].restoration = RestorationSettings(enabled=True, denoise=1., sharpen=.5)
    project.clips[0].grade.temperature = .15
    expected_picture = restoration_reference(image.astype(float) / 65535, project.clips[0].restoration)
    canvas = np.zeros((40, 40, 3), float)
    canvas[10:30] = expected_picture
    expected = primary_reference(canvas, project.clips[0].grade)
    snapshot = capture_picker_snapshot(project, 'picture', 0)
    np.testing.assert_allclose(snapshot.frame, expected, rtol=0, atol=3e-7)
    assert snapshot.active_rect == (0, 10, 40, 20)
    assert not snapshot.valid_mask[:10].any() and not snapshot.valid_mask[30:].any()
    assert snapshot.valid_mask[10:30].all() and snapshot.valid_mask[18, 12]
    padded = np.zeros_like(canvas)
    padded[10:30] = frames[0].astype(float) / 65535
    bad = primary_reference(restoration_reference(padded, project.clips[0].restoration), project.clips[0].grade)
    assert np.max(abs(expected[10] - bad[10])) > .001


@pytest.mark.parametrize('settings', [RestorationSettings(enabled=False, deblock=1, denoise=1, sharpen=1),
                                     RestorationSettings(enabled=True)], ids=['disabled', 'zero'])
def test_disabled_or_zero_restoration_is_exact_and_skips_kernels(source, settings, monkeypatch):
    import lumen.restoration as restoration
    project, frames = source
    project.clips[0].restoration = settings
    def unexpected(*args, **kwargs):
        pytest.fail('Bypassed restoration must not enter the spatial evaluator')
    monkeypatch.setattr(restoration, 'apply_restoration', unexpected)
    snapshot = capture_picker_snapshot(project, 'picture', 0)
    np.testing.assert_array_equal(snapshot.frame, frames[0].astype(np.float32) / np.float32(65535))


@pytest.mark.parametrize('origin,speed,local,index', [
    (Fraction(2), Fraction(1), 2, 4),
    (Fraction(1, 2), Fraction(3, 2), 3, 5),
    (Fraction(3, 2), Fraction(1, 2), 3, 3),
])
def test_restoration_uses_exact_requested_time_map_frame(source, origin, speed, local, index):
    project, frames = source
    clip = project.clips[0]
    clip.start, clip.source_in, clip.duration = 5, origin, 4
    clip.time_map = ClipTimeMap(origin, origin + 4 * speed, speed)
    clip.restoration = RestorationSettings(enabled=True, denoise=.9, sharpen=.3)
    expected = restoration_reference(frames[index].astype(float) / 65535, clip.restoration)
    snapshot = capture_picker_snapshot(project, 'picture', 5 + local)
    assert snapshot.source_frame == index and snapshot.source_time == Fraction(index, 6)
    np.testing.assert_allclose(snapshot.frame, expected, rtol=0, atol=2.5e-7)


@pytest.mark.parametrize('stop', ['cancel', 'deadline'])
def test_mid_restoration_processing_honors_cancel_and_total_deadline(tmp_path, monkeypatch, stop):
    import lumen.color_picker_source as sampler
    import lumen.restoration as restoration
    project, _ = make_source(tmp_path, picture(height=193))
    project.clips[0].restoration = RestorationSettings(enabled=True, denoise=1., sharpen=1.)
    event, visits, advanced = threading.Event(), [], [0.]
    old_neighborhood, real_clock = restoration._neighborhood, sampler.time.monotonic
    def advance_clock():
        return real_clock() + advanced[0]
    monkeypatch.setattr(sampler.time, 'monotonic', advance_clock)
    def stop_after_first_processing_tile(*args):
        visits.append(args[1:])
        result = old_neighborhood(*args)
        if stop == 'cancel':
            event.set()
        else:
            advanced[0] = 120.
        return result
    monkeypatch.setattr(restoration, '_neighborhood', stop_after_first_processing_tile)
    error = PickerSourceCancelled if stop == 'cancel' else PickerSourceError
    with pytest.raises(error, match='cancelled|timed out'):
        capture_picker_snapshot(project, 'picture', 0, cancel=event, timeout=30)
    assert len(visits) == 1, 'Stop must interrupt processing, not wait for all restoration passes'


def test_restoration_settings_mutation_during_processing_rejects_result(source, monkeypatch):
    import lumen.restoration as restoration
    project, _ = source
    settings = project.clips[0].restoration = RestorationSettings(enabled=True, denoise=.9)
    original = restoration._neighborhood
    def mutate(*args):
        settings.denoise = .2
        return original(*args)
    monkeypatch.setattr(restoration, '_neighborhood', mutate)
    with pytest.raises(PickerSourceError, match='changed during'):
        capture_picker_snapshot(project, 'picture', 0)


def test_restoration_in_place_setting_changes_invalidate_snapshot(source):
    project, _ = source
    settings = project.clips[0].restoration = RestorationSettings(enabled=True, denoise=.9)
    snapshot = capture_picker_snapshot(project, 'picture', 0)
    assert snapshot.is_current(project)
    settings.denoise = .8
    assert not snapshot.is_current(project)
    settings.denoise = .9
    assert snapshot.is_current(project)
    settings.enabled = False
    assert not snapshot.is_current(project)


def test_full_picture_restoration_crosses_bounded_grading_chunk_boundary(tmp_path, monkeypatch):
    import lumen.color_picker_source as sampler
    height, width = 192, 384  # 65536-pixel grade boundary is inside row 170.
    image = np.broadcast_to(np.array([18001, 28003, 40009], '<u2'), (height, width, 3)).copy()
    yy, xx = np.mgrid[:12, :12]
    noise = ((((xx + 2 * yy) % 5) - 2) * 411)[..., None]
    image[164:176, 250:262] = image[164:176, 250:262].astype(int) + noise
    project, _ = make_source(tmp_path, image)
    settings = project.clips[0].restoration = RestorationSettings(enabled=True, denoise=.9, sharpen=.4)
    # A compact independent neighborhood has enough context for both 3x3
    # passes. Its center spans the production grading boundary, not a pad edge.
    expected = restoration_reference(image[162:178, 248:264].astype(float) / 65535, settings)
    original, calls = sampler.apply_grade, []
    def record(rgb, *args, **kwargs):
        calls.append(rgb.shape)
        assert rgb.shape[0] <= 65536
        return original(rgb, *args, **kwargs)
    monkeypatch.setattr(sampler, 'apply_grade', record)
    snapshot = capture_picker_snapshot(project, 'picture', 0)
    np.testing.assert_allclose(snapshot.frame[166:174, 252:260], expected[4:12, 4:12], rtol=0, atol=2.5e-7)
    assert calls == [(65536, 3), (8192, 3)]
    assert np.max(abs(snapshot.frame[169:172, 254:258] - image[169:172, 254:258] / 65535)) > .001


def test_changing_source_geometry_fails_closed_before_picture_decode(source, monkeypatch):
    import lumen.color_picker_source as sampler
    project, _ = source
    project.clips[0].restoration = RestorationSettings(enabled=True, denoise=.9)
    probes, decodes = [], []
    original = sampler._run
    def changing_geometry(source_path, filters, width, height, *args):
        probes.append(list(filters))
        return (width, height) if len(probes) == 1 else (width, height - 2)
    def record(*args, **kwargs):
        decodes.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(sampler, 'active_picture_size', changing_geometry)
    monkeypatch.setattr(sampler, '_run', record)
    with pytest.raises(PickerSourceError, match='changing source geometry'):
        capture_picker_snapshot(project, 'picture', 4)
    assert len(probes) == 2
    assert 'trim=start_frame=4:end_frame=5' in probes[0]
    assert not any('trim=' in item or 'fps=' in item for item in probes[1])
    assert len(decodes) == 1 and '-show_entries' in decodes[0], \
        'Unsafe restoration geometry must be rejected before collecting picture bytes'

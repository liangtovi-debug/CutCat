"""Independent RGB16/picker/decoded-delivery acceptance for contrast pivot.

The oracle uses scalar transfer equations, scalar terminal-slope curves and
BT.709 Y'CbCr coefficients. No production grade, transfer, curve, qualifier or
output helper is used to calculate expected pixels. Fixtures are local and
must survive an actual FFV1 RGB16 decode byte-for-byte before being tested.
"""
from bisect import bisect_right
import colorsys
from copy import deepcopy
import math
import subprocess

import numpy as np
import pytest

from lumen.color import apply_grade
from lumen.color_picker_source import capture_picker_snapshot
from lumen.media import ffmpeg_available, ffmpeg_binary, probe
from lumen.model import Clip, Grade, Project
from lumen.qualifier import QualifierSettings
from lumen.render import RenderEngine


MEDIA = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
PATCH, HEIGHT = 32, 32
LUMA = (.2126, .7152, .0722)


def ff(*args, data=None):
    return subprocess.run([ffmpeg_binary(), '-hide_banner', '-v', 'error',
                           '-nostdin', '-y', '-threads', '1', *map(str, args)],
                          input=data, capture_output=True, check=True, timeout=60).stdout


def source_codes():
    # Adjacent, non-8-bit codes around 18% and midgray expose any RGB8 ingress.
    gray = (0, 1, 8191, 11796, 11797, 22937, 32767, 32768, 54067, 65535)
    return np.asarray([(x, x, x) for x in gray] + [
        (42111, 17347, 9043), (14011, 38437, 52691),
        (31019, 12851, 40217), (5671, 25721, 14089)], dtype='<u2')


def source_project(tmp_path, grade, *, name='pivot', transfer='bt709'):
    values = source_codes()
    image = np.repeat(np.repeat(values[None], HEIGHT, axis=0), PATCH, axis=1)
    width = image.shape[1]
    path = tmp_path / f'{name}-rgb16.mkv'
    tags = ['-colorspace', 'rgb', '-color_range', 'pc', '-color_trc', transfer,
            '-color_primaries', 'bt709']
    planar = np.moveaxis(image[..., [1, 2, 0]], -1, 0).astype('<u2').tobytes()
    ff('-f', 'rawvideo', '-pix_fmt', 'gbrp16le', '-s', f'{width}x{HEIGHT}',
       '-r', '6', *tags, '-i', 'pipe:0', '-frames:v', '3', '-c:v', 'ffv1',
       '-level', '3', '-pix_fmt', 'gbrp16le', *tags, path, data=planar * 3)
    assert ff('-i', path, '-pix_fmt', 'gbrp16le', '-f', 'rawvideo', 'pipe:1') == planar * 3
    media = probe(path)
    return Project(width=width, height=HEIGHT, fps_num=6, media=[media],
                   clips=[Clip('picture', media.id, 0, 0, 0, 3, grade=grade)])


def curve_reference(value, points):
    left = min(max(bisect_right([x for x, _ in points], value) - 1, 0), len(points) - 2)
    (x0, y0), (x1, y1) = points[left:left + 2]
    return y0 + (value - x0) * (y1 - y0) / (x1 - x0)


def encode709(value):
    absolute = abs(value)
    return math.copysign(4.5 * absolute if absolute < .018
                         else 1.099 * absolute ** .45 - .099, value)


def decode709(value):
    absolute = abs(value)
    return math.copysign(absolute / 4.5 if absolute < .081
                         else ((absolute + .099) / 1.099) ** (1 / .45), value)


def primary_reference(rgb, grade, *, premature_clip=False):
    results = []
    for sample in np.asarray(rgb, float).reshape(-1, 3):
        channels = []
        for channel, value in enumerate(sample):
            if grade.input_encoding == 'linear_rec709':
                value = encode709(value)
            elif grade.input_encoding == 'srgb':
                value = encode709(value / 12.92 if value <= .04045
                                  else ((value + .055) / 1.055) ** 2.4)
            if grade.enabled:
                if grade.linear_exposure:
                    value = encode709(decode709(value) * 2 ** grade.linear_exposure)
                value *= 2 ** grade.exposure
                if grade.contrast != 1:
                    value = (value - grade.contrast_pivot) * grade.contrast + grade.contrast_pivot
                if premature_clip:
                    value = min(max(value, 0.), 1.)
                value = curve_reference(value, grade.master_curve)
                value = curve_reference(value, (grade.red_curve, grade.green_curve,
                                                 grade.blue_curve)[channel])
            channels.append(value)
        results.append(channels)
    return np.asarray(results).reshape(np.shape(rgb))


def selective_reference(rgb, grade):
    results = []
    for sample in np.asarray(rgb, float).reshape(-1, 3):
        low, high = min(sample), max(sample)
        if low == high:
            results.append(sample)
            continue
        # Normalize min/max before colorsys so negative and >1 samples work.
        unit = (sample - low) / (high - low)
        hue = colorsys.rgb_to_hsv(*unit)[0]
        rotated = low + (high - low) * np.asarray(colorsys.hsv_to_rgb(
            (hue + curve_reference(hue, grade.hue_hue_curve)) % 1., 1., 1.))
        luma = sum(c * v for c, v in zip(LUMA, rotated))
        results.append(luma + curve_reference(hue, grade.hue_sat_curve) * (rotated - luma))
    return np.asarray(results).reshape(np.shape(rgb))


def luma_qualifier_reference(rgb, settings):
    # This fixture explicitly disables H/S and uses a hard Y' range, making
    # selection independent from the production qualifier implementation.
    assert not settings.hue_enabled and not settings.saturation_enabled
    assert settings.luma_softness == 0 and settings.saturation == 1 and settings.hue_shift == 0
    result, weights = [], []
    for sample in np.asarray(rgb, float).reshape(-1, 3):
        luma = sum(c * v for c, v in zip(LUMA, sample))
        selected = (min(sample) >= 0 and max(sample) <= 1
                    and settings.luma_low <= luma <= settings.luma_high)
        weights.append(selected)
        result.append(sample * 2 ** settings.exposure if selected else sample)
    return np.asarray(result).reshape(np.shape(rgb)), np.asarray(weights)


def patch_means(image):
    return np.asarray([image[8:24, i * PATCH + 8:(i + 1) * PATCH - 8].mean((0, 1), dtype=float)
                       for i in range(len(source_codes()))])


def decode_rgb(path, width):
    raw = ff('-i', path, '-frames:v', '1', '-pix_fmt', 'rgb48le', '-f', 'rawvideo', 'pipe:1')
    return np.frombuffer(raw, '<u2').reshape(HEIGHT, width, 3).astype(float) / 65535


def decoded_yuv_patches(path, width, ten_bit):
    fmt, dtype = ('yuv422p10le', '<u2') if ten_bit else ('yuv420p', 'u1')
    raw = np.frombuffer(ff('-i', path, '-frames:v', '1', '-pix_fmt', fmt,
                           '-f', 'rawvideo', 'pipe:1'), dtype)
    count = HEIGHT * width
    chroma_height = HEIGHT if ten_bit else HEIGHT // 2
    chroma_count = chroma_height * width // 2
    planes = [raw[:count].reshape(HEIGHT, width),
              raw[count:count + chroma_count].reshape(chroma_height, width // 2),
              raw[count + chroma_count:].reshape(chroma_height, width // 2)]
    result = []
    for index in range(len(source_codes())):
        values = []
        for channel, plane in enumerate(planes):
            sx = 1 if channel == 0 else 2
            sy = 2 if channel and not ten_bit else 1
            values.append(plane[8 // sy:24 // sy,
                                (index * PATCH + 8) // sx:((index + 1) * PATCH - 8) // sx].mean())
        result.append(values)
    return np.asarray(result)


def yuv_reference(rgb, ten_bit):
    red, green, blue = np.asarray(rgb, float).T
    luma = .2126 * red + .7152 * green + .0722 * blue
    return (4 if ten_bit else 1) * np.stack([
        16 + 219 * luma, 128 + 224 * (blue - luma) / (2 * (1 - .0722)),
        128 + 224 * (red - luma) / (2 * (1 - .2126))], axis=-1)


@pytest.fixture
def engine():
    renderer = RenderEngine(timeout=90)
    try:
        yield renderer
    finally:
        renderer.close()


def assert_delivery(project, expected, tmp_path, engine, *, name='pivot'):
    bounded = np.clip(expected, 0, 1)
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_allclose(patch_means(decode_rgb(stage, project.width)), bounded,
                               atol=2 / 65535, rtol=0)
    for delivery in ('preview', 'prores_422_hq_10bit'):
        ten_bit = delivery.startswith('prores')
        output = (engine.preview(project) if delivery == 'preview' else engine.render(
            project, tmp_path / f'{name}.mov', export_profile=delivery))
        np.testing.assert_allclose(decoded_yuv_patches(output, project.width, ten_bit),
                                   yuv_reference(bounded, ten_bit), atol=2, rtol=0,
                                   err_msg=delivery)


@pytest.mark.parametrize('contrast', [0., .4, 1., 1.7, 4.])
@pytest.mark.parametrize('exposure', [0., .375])
def test_default_pivot_preserves_legacy_float32_bit_patterns(contrast, exposure):
    codes = np.arange(65536, dtype=np.float32) / np.float32(65535)
    extra = np.asarray([-.0, -.5, -2., 1.5, 4., np.nextafter(np.float32(.5), 0),
                        np.nextafter(np.float32(.5), 1), np.finfo(np.float32).tiny], np.float32)
    ramp = np.concatenate([codes, extra])
    rgb = np.stack([ramp, ramp[::-1], np.roll(ramp, 17)], axis=-1)
    old = rgb.copy()
    if exposure:
        old *= np.float32(2 ** exposure)
    if contrast != 1:
        old = (old - np.float32(.5)) * np.float32(contrast) + np.float32(.5)
    for grade in (Grade(contrast=contrast, exposure=exposure),
                  Grade(contrast=contrast, exposure=exposure, contrast_pivot=.5)):
        np.testing.assert_array_equal(apply_grade(rgb, grade).view(np.uint32), old.view(np.uint32))


@pytest.mark.parametrize('pivot', [0., .18, .83, 1.])
def test_nondefault_pivot_unclipped_math_and_neutral_signed_zero(pivot):
    rgb = np.asarray([[-.25, -.0, 0.], [.00001, .18, .5], [.9, 1., 1.25]], np.float32)
    grade = Grade(contrast=2.25, contrast_pivot=pivot)
    p, contrast = np.float32(pivot), np.float32(grade.contrast)
    expected = (rgb - p) * contrast + p
    actual = apply_grade(rgb, grade)
    np.testing.assert_array_equal(actual.view(np.uint32), expected.view(np.uint32))
    assert actual.min() < 0 and actual.max() > 1
    np.testing.assert_array_equal(apply_grade(rgb, Grade(contrast_pivot=pivot)).view(np.uint32),
                                  rgb.view(np.uint32))
    anchor = np.full((1, 3), p, np.float32)
    np.testing.assert_array_equal(apply_grade(anchor, grade), anchor)
    np.testing.assert_array_equal(apply_grade(rgb, Grade(contrast=0., contrast_pivot=pivot)),
                                  np.full_like(rgb, p))


@MEDIA
@pytest.mark.parametrize('pivot', [0., .18, .75, 1.])
def test_actual_rgb16_preview_and_prores_match_pivot_not_fixed_midgray(tmp_path, engine, pivot):
    grade = Grade(exposure=.15, contrast=1.7, contrast_pivot=pivot)
    project = source_project(tmp_path, grade)
    source = source_codes().astype(float) / 65535
    expected = primary_reference(source, grade)
    np.testing.assert_allclose(apply_grade(source.astype(np.float32), grade), expected, atol=3e-7, rtol=0)
    assert_delivery(project, expected, tmp_path, engine)
    old = deepcopy(grade)
    old.contrast_pivot = .5
    assert np.max(abs(np.clip(expected, 0, 1) - np.clip(primary_reference(source, old), 0, 1))) > .1


@MEDIA
@pytest.mark.parametrize('encoding,transfer', [
    ('rec709', 'bt709'), ('srgb', 'iec61966-2-1'), ('linear_rec709', 'linear')])
def test_pivot_is_after_normalization_linear_exposure_and_legacy_gain(tmp_path, engine, encoding, transfer):
    grade = Grade(input_encoding=encoding, linear_exposure=.4, exposure=-.15,
                  contrast=1.6, contrast_pivot=.23)
    project = source_project(tmp_path, grade, transfer=transfer)
    source = source_codes().astype(float) / 65535
    expected = primary_reference(source, grade)
    np.testing.assert_allclose(apply_grade(source.astype(np.float32), grade), expected, atol=8e-7, rtol=0)
    snapshot = capture_picker_snapshot(project, 'picture', 0)
    np.testing.assert_allclose(patch_means(snapshot.frame), expected, atol=8e-7, rtol=0)
    # Pivot in linear light, before legacy gain, is a materially different look.
    wrong = deepcopy(grade)
    wrong.contrast = 1.
    normalized = primary_reference(source, wrong)
    linear_contrast = np.asarray([[encode709((decode709(v) - grade.contrast_pivot)
                                             * grade.contrast + grade.contrast_pivot)
                                  for v in sample] for sample in normalized])
    assert np.max(abs(expected - linear_contrast)) > .1
    assert_delivery(project, expected, tmp_path, engine)


@MEDIA
def test_extended_range_reaches_curves_before_the_delivery_clip(tmp_path, engine):
    grade = Grade(contrast=2., contrast_pivot=.3,
                  master_curve=[[0., .2], [.5, .5], [1., .8]],
                  red_curve=[[0., .1], [1., .85]])
    project = source_project(tmp_path, grade)
    source = source_codes().astype(float) / 65535
    expected = primary_reference(source, grade)
    prematurely_clipped = primary_reference(source, grade, premature_clip=True)
    assert np.max(abs(np.clip(expected, 0, 1) - prematurely_clipped)) > .15
    assert expected[0, 1] > 0 and expected[0, 1] < .03
    assert expected[9, 1] > 1, 'Highlight extrapolation must remain extended until delivery'
    snapshot = capture_picker_snapshot(project, 'picture', 0)
    np.testing.assert_allclose(patch_means(snapshot.frame), expected, atol=5e-7, rtol=0)
    assert_delivery(project, expected, tmp_path, engine)


@MEDIA
def test_pivot_precedes_both_picker_stages_qualifier_and_lut(tmp_path, engine):
    lut = tmp_path / 'swap-red-blue.cube'
    lut.write_text('LUT_3D_SIZE 2\n' + ''.join(
        f'{b} {g} {r}\n' for b in (0, 1) for g in (0, 1) for r in (0, 1)))
    grade = Grade(contrast=1.6, contrast_pivot=.25,
                  master_curve=[[0., .08], [1., .83]],
                  red_curve=[[0., .04], [1., .94]],
                  hue_hue_curve=[[0., .12], [1., .12]],
                  hue_sat_curve=[[0., .7], [1., .7]],
                  qualifier=QualifierSettings(enabled=True, hue_enabled=False,
                      saturation_enabled=False, luma_low=.25, luma_high=.65,
                      luma_softness=0., exposure=.45),
                  lut_path=str(lut), lut_strength=.6)
    project = source_project(tmp_path, grade)
    source = source_codes().astype(float) / 65535
    primary = primary_reference(source, grade)
    selective = selective_reference(primary, grade)
    qualified, key = luma_qualifier_reference(selective, grade.qualifier)
    assert np.any(key) and np.any(~key)
    wrong_qualified, wrong_key = luma_qualifier_reference(source, grade.qualifier)
    assert np.any(key != wrong_key), 'Raw-source selection must disagree with the real post-pivot key'
    assert np.max(abs(qualified - wrong_qualified)) > .1
    for stage_name, target in [('hue', primary), ('qualifier', selective)]:
        snapshot = capture_picker_snapshot(project, 'picture', 0, stage=stage_name)
        np.testing.assert_allclose(patch_means(snapshot.frame), target, atol=6e-7, rtol=0)
    assert np.max(abs(primary - selective)) > .05
    # The LUT alone clamps its bounded domain, and its partial blend retains
    # 40% of extended pre-LUT samples until final delivery clipping.
    expected = .4 * qualified + .6 * np.clip(qualified, 0, 1)[:, [2, 1, 0]]
    assert_delivery(project, expected, tmp_path, engine)


@MEDIA
def test_pivot_edit_invalidates_preview_grade_cache_and_picker_identity(tmp_path, engine):
    grade = Grade(contrast=1.8, contrast_pivot=.2)
    project = source_project(tmp_path, grade)
    clip, media = project.clips[0], project.media[0]
    original_state = deepcopy(project.to_dict())
    before_stage = engine._graded_segment(media, clip, project, None)
    before_preview = engine.preview(project)
    snapshot = capture_picker_snapshot(project, 'picture', 0)
    grade.contrast_pivot = .7
    assert not snapshot.is_current(project)
    after_stage = engine._graded_segment(media, clip, project, None)
    after_preview = engine.preview(project)
    assert after_stage != before_stage and after_preview != before_preview
    expected = primary_reference(source_codes().astype(float) / 65535, grade)
    assert_delivery(project, expected, tmp_path, engine, name='changed')
    grade.contrast_pivot = .2
    assert project.to_dict() == original_state
    assert engine._graded_segment(media, clip, project, None) == before_stage
    assert engine.preview(project) == before_preview
    assert snapshot.is_current(project)

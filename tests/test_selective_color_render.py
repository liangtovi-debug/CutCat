"""Decoded preview/export acceptance for periodic selective color curves.

Fixtures are generated locally from canonical hues. Expected colors use
Python's independently maintained colorsys hexcone conversion, scalar curve
interpolation, and published BT.709 Y'CbCr coefficients; they never call a
production grade, curve, input, output, or timeline helper.
"""
from bisect import bisect_right
import colorsys
from copy import deepcopy
import subprocess

import numpy as np
import pytest

from lumen.media import ffmpeg_available, ffmpeg_binary, probe
from lumen.model import Clip, Grade, Project
from lumen.render import RenderEngine


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
PATCH = 64
HEIGHT = 64
HUES = (0., .002, 1 / 12, 1 / 6, .25, 1 / 3, .5, 2 / 3, 5 / 6, .998)
HUE_CURVE = [[0., .08], [1 / 12, .08], [1 / 6, 0.], [.5, -.1],
             [2 / 3, 0.], [1., .08]]
SAT_CURVE = [[0., .45], [1 / 6, 1.], [1 / 3, .7], [.5, .5],
             [2 / 3, 1.], [1., .45]]


def ff(*args, data=None):
    return subprocess.run([ffmpeg_binary(), '-hide_banner', '-v', 'error',
                           '-nostdin', '-y', '-threads', '1', *map(str, args)],
                          input=data, capture_output=True, check=True, timeout=60).stdout


def patches():
    values = [colorsys.hsv_to_rgb(h, .7, .75) for h in HUES]
    values += [(.125,) * 3, (.5,) * 3]
    return np.rint(np.array(values) * 65535).astype('<u2')


def source_project(tmp_path, grade=None):
    values = patches()
    rgb = np.repeat(np.repeat(values[None], HEIGHT, axis=0), PATCH, axis=1)
    width = rgb.shape[1]
    source = tmp_path / 'synthetic-selective-rgb16.mkv'
    tags = ['-colorspace', 'rgb', '-color_range', 'pc', '-color_trc', 'bt709',
            '-color_primaries', 'bt709']
    planar = np.moveaxis(rgb[..., [1, 2, 0]], -1, 0).astype('<u2')
    ff('-f', 'rawvideo', '-pix_fmt', 'gbrp16le', '-s', f'{width}x{HEIGHT}',
       '-r', '30', *tags, '-i', 'pipe:0', '-frames:v', '3', '-c:v', 'ffv1',
       '-level', '3', '-pix_fmt', 'gbrp16le', *tags, source,
       data=planar.tobytes() * 3)
    # A compromised precision fixture must fail before testing the feature.
    assert ff('-i', source, '-frames:v', 1, '-pix_fmt', 'gbrp16le',
              '-f', 'rawvideo', 'pipe:1') == planar.tobytes()
    media = probe(source)
    return Project(width=width, height=HEIGHT, media=[media],
                   clips=[Clip('picture', media.id, 0, 0, 0, 1,
                               grade=grade or Grade())])


def sample_curve(points, hue):
    left = min(max(bisect_right([x for x, _ in points], hue) - 1, 0), len(points) - 2)
    (x0, y0), (x1, y1) = points[left:left + 2]
    return y0 + (hue - x0) * (y1 - y0) / (x1 - x0)


def selective_reference(rgb, hue_curve=HUE_CURVE, sat_curve=SAT_CURVE):
    results = []
    for sample in np.asarray(rgb, float).reshape(-1, 3):
        h, saturation, value = colorsys.rgb_to_hsv(*sample)
        if saturation == 0:
            results.append(sample)
            continue
        rotated = np.array(colorsys.hsv_to_rgb(
            (h + sample_curve(hue_curve, h)) % 1., saturation, value))
        luma = np.dot(rotated, [.2126, .7152, .0722])
        # Both lookups use source hue, not the newly rotated hue.
        results.append(luma + sample_curve(sat_curve, h) * (rotated - luma))
    return np.array(results).reshape(np.asarray(rgb).shape)


def expected_patches(grade):
    return selective_reference(patches().astype(float) / 65535,
                               grade.hue_hue_curve, grade.hue_sat_curve)


def decode_rgb(path, width):
    data = ff('-i', path, '-frames:v', 1, '-pix_fmt', 'rgb48le', '-f', 'rawvideo', 'pipe:1')
    return np.frombuffer(data, '<u2').reshape(HEIGHT, width, 3).astype(float) / 65535


def patch_rgb(image):
    return np.array([image[16:48, index * PATCH + 16:(index + 1) * PATCH - 16].mean((0, 1))
                     for index in range(len(patches()))])


def decoded_yuv_patches(path, width, ten_bit):
    fmt, dtype = ('yuv422p10le', '<u2') if ten_bit else ('yuv420p', 'u1')
    data = np.frombuffer(ff('-i', path, '-frames:v', 1, '-pix_fmt', fmt,
                            '-f', 'rawvideo', 'pipe:1'), dtype)
    count = HEIGHT * width
    chroma_height = HEIGHT if ten_bit else HEIGHT // 2
    chroma_count = chroma_height * width // 2
    planes = [data[:count].reshape(HEIGHT, width),
              data[count:count + chroma_count].reshape(chroma_height, width // 2),
              data[count + chroma_count:].reshape(chroma_height, width // 2)]
    result = []
    for index in range(len(patches())):
        values = []
        for channel, plane in enumerate(planes):
            xscale = 1 if channel == 0 else 2
            yscale = 2 if channel and not ten_bit else 1
            values.append(plane[16 // yscale:48 // yscale,
                                (index * PATCH + 16) // xscale:
                                ((index + 1) * PATCH - 16) // xscale].mean())
        result.append(values)
    return np.array(result)


def yuv_reference(rgb, ten_bit):
    red, green, blue = np.asarray(rgb, float).T
    luma = .2126 * red + .7152 * green + .0722 * blue
    factor = 4 if ten_bit else 1
    return factor * np.stack([16 + 219 * luma,
                             128 + 224 * (blue - luma) / (2 * (1 - .0722)),
                             128 + 224 * (red - luma) / (2 * (1 - .2126))], axis=-1)


@pytest.fixture
def engine():
    renderer = RenderEngine(timeout=90)
    try:
        yield renderer
    finally:
        renderer.close()


@pytest.mark.parametrize('delivery', ['preview', 'h264_8bit', 'prores_422_hq_10bit'])
def test_real_preview_and_both_exports_match_independent_hue_patch_reference(
        tmp_path, engine, delivery):
    grade = Grade(hue_hue_curve=deepcopy(HUE_CURVE), hue_sat_curve=deepcopy(SAT_CURVE))
    project = source_project(tmp_path, grade)
    expected = expected_patches(grade)
    assert expected.min() > 0 and expected.max() < 1
    ten_bit = delivery == 'prores_422_hq_10bit'
    if delivery == 'preview':
        output = engine.preview(project)
    else:
        output = engine.render(project, tmp_path / ('graded.mov' if ten_bit else 'graded.mp4'),
                               export_profile=delivery)
    actual = decoded_yuv_patches(output, project.width, ten_bit)
    np.testing.assert_allclose(actual, yuv_reference(expected, ten_bit), atol=2, rtol=0)
    # Independent RGB comparison of the lossless shared grade stage is far
    # tighter than the delivery codec check, catching wrong-hue interpolation.
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_allclose(patch_rgb(decode_rgb(stage, project.width)), expected,
                               atol=3 / 65535, rtol=0)
    # A wrong "sample saturation after hue rotation" implementation differs
    # visibly, so the fixture is sensitive to the stage-order contract.
    rotated_hue = colorsys.rgb_to_hsv(*patches()[0] / 65535)[0] + .08
    assert abs(sample_curve(SAT_CURVE, rotated_hue) - sample_curve(SAT_CURVE, 0)) > .1


@pytest.mark.parametrize('field,curve', [('hue_hue_curve', HUE_CURVE),
                                       ('hue_sat_curve', SAT_CURVE)])
def test_in_place_selective_curve_changes_invalidate_preview_and_lossless_stage(
        tmp_path, engine, field, curve):
    project = source_project(tmp_path)
    clip, media = project.clips[0], project.media[0]
    before_stage = engine._graded_segment(media, clip, project, None)
    before_preview = engine.preview(project)
    before_pixels = patch_rgb(decode_rgb(before_stage, project.width))
    # Deliberately mutate the list without changing its identity or revision.
    getattr(clip.grade, field)[:] = deepcopy(curve)
    after_stage = engine._graded_segment(media, clip, project, None)
    after_preview = engine.preview(project)
    assert before_stage != after_stage and before_preview != after_preview
    after_pixels = patch_rgb(decode_rgb(after_stage, project.width))
    np.testing.assert_allclose(after_pixels, expected_patches(clip.grade), atol=3 / 65535, rtol=0)
    assert np.max(np.abs(before_pixels - after_pixels)) > .1
    np.testing.assert_allclose(decoded_yuv_patches(after_preview, project.width, False),
                               yuv_reference(expected_patches(clip.grade), False), atol=2, rtol=0)
    # Returning to identity safely reuses the actual original pixel cache.
    setattr(clip.grade, field, deepcopy(getattr(Grade(), field)))
    assert engine.preview(project) == before_preview


def test_disabled_selective_grade_is_pixel_exact_in_preview_and_both_exports(tmp_path, engine):
    project = source_project(tmp_path)
    before = {}
    for profile, suffix in [('h264_8bit', '.mp4'), ('prores_422_hq_10bit', '.mov')]:
        path = engine.render(project, tmp_path / ('neutral' + suffix), export_profile=profile)
        before[profile] = decoded_yuv_patches(path, project.width, profile.startswith('prores'))
    before_preview = decoded_yuv_patches(engine.preview(project), project.width, False)
    project.clips[0].grade = Grade(enabled=False, hue_hue_curve=deepcopy(HUE_CURVE),
                                  hue_sat_curve=deepcopy(SAT_CURVE))
    for profile, suffix in [('h264_8bit', '.mp4'), ('prores_422_hq_10bit', '.mov')]:
        path = engine.render(project, tmp_path / ('disabled' + suffix), export_profile=profile)
        np.testing.assert_array_equal(decoded_yuv_patches(path, project.width,
                                                        profile.startswith('prores')), before[profile])
    np.testing.assert_array_equal(decoded_yuv_patches(engine.preview(project), project.width, False),
                                  before_preview)


def test_rgb_curve_then_selective_then_lut_order_survives_real_delivery(tmp_path, engine):
    lut = tmp_path / 'swap-red-blue.cube'
    lut.write_text('LUT_3D_SIZE 2\n' + '\n'.join(
        f'{blue} {green} {red}' for blue in (0, 1) for green in (0, 1)
        for red in (0, 1)) + '\n')
    grade = Grade(red_curve=[[0., .1], [1., .8]], hue_hue_curve=deepcopy(HUE_CURVE),
                  hue_sat_curve=deepcopy(SAT_CURVE), lut_path=str(lut), lut_strength=.6)
    project = source_project(tmp_path, grade)
    rgb = patches().astype(float) / 65535
    rgb[:, 0] = .1 + .7 * rgb[:, 0]
    selective = selective_reference(rgb)
    expected = .4 * selective + .6 * selective[:, [2, 1, 0]]
    output = engine.render(project, tmp_path / 'ordered.mov', export_profile='prores_422_hq_10bit')
    np.testing.assert_allclose(decoded_yuv_patches(output, project.width, True),
                               yuv_reference(expected, True), atol=2, rtol=0)


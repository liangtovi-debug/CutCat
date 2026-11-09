"""Independent decoded acceptance for hue/HSV-saturation/Rec.709 Y' keys.

Synthetic RGB16 sources are decoded byte-for-byte before use. Scalar float64
colorsys and published BT.709 coefficients predict the matte and output; no
production qualifier, grade or color conversion helper supplies expected pixels.
"""
from bisect import bisect_right
import colorsys
from copy import deepcopy
from fractions import Fraction
import subprocess

import numpy as np
import pytest

from lumen.color_picker_source import capture_picker_snapshot, PickerSourceError
from lumen.media import ffmpeg_available, ffmpeg_binary, probe
from lumen.model import Clip, Grade, Overlay, OverlayKeyframe, Project
from lumen.qualifier import QualifierSettings
from lumen.render import RenderEngine
from lumen.restoration_settings import RestorationSettings
from lumen.timing import ClipTimeMap
from test_color_picker_restoration import (normalize_reference, primary_reference,
                                           restoration_reference)


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
PATCH, HEIGHT = 64, 64
LUMA = np.array([.2126, .7152, .0722])
# Saturation and luma are varied independently: hue-only tests miss both bugs.
PATCH_HSY = (
    (.002, .65, .27), (.998, .65, .27), (.06, .65, .27),
    (.11, .65, .27), (.5, .65, .27),
    (0., .18, .27), (0., .37, .27), (0., .82, .27), (0., .92, .20),
    (0., .65, .10), (0., .65, .16), (0., .65, .41), (0., .65, .46),
    (0., 0., .27), (0., 0., 0.),
    (.06, .37, .16),  # All three feather ramps multiply, not min/max/add.
)


def ff(*args, data=None):
    return subprocess.run([ffmpeg_binary(), '-hide_banner', '-v', 'error',
                           '-nostdin', '-y', '-threads', '1', *map(str, args)],
                          input=data, capture_output=True, check=True, timeout=60).stdout


def patch_values():
    values = []
    for hue, saturation, luma in PATCH_HSY:
        unit = np.array(colorsys.hsv_to_rgb(hue, saturation, 1.))
        values.append(unit * luma / np.dot(unit, LUMA))
    result = np.asarray(values)
    assert result.min() >= 0 and result.max() < 1
    return np.rint(result * 65535).astype('<u2')


def source_project(tmp_path, frames=None, grade=None, *, transfer='bt709', name='qualifier'):
    if frames is None:
        values = patch_values()
        image = np.repeat(np.repeat(values[None], HEIGHT, axis=0), PATCH, axis=1)
        frames = np.repeat(image[None], 3, axis=0)
    frames = np.asarray(frames, dtype='<u2')
    count, height, width, _ = frames.shape
    source = tmp_path / f'{name}-rgb16.mkv'
    tags = ['-colorspace', 'rgb', '-color_range', 'pc', '-color_trc', transfer,
            '-color_primaries', 'bt709']
    planar = np.moveaxis(frames[..., [1, 2, 0]], -1, 1).astype('<u2')
    ff('-f', 'rawvideo', '-pix_fmt', 'gbrp16le', '-s', f'{width}x{height}',
       '-r', '6', *tags, '-i', 'pipe:0', '-frames:v', count, '-c:v', 'ffv1',
       '-level', '3', '-pix_fmt', 'gbrp16le', '-threads', '1', *tags, source,
       data=planar.tobytes())
    assert ff('-i', source, '-pix_fmt', 'gbrp16le', '-f', 'rawvideo', 'pipe:1') == planar.tobytes()
    media = probe(source)
    project = Project(width=width, height=height, fps_num=6, media=[media], clips=[
        Clip('picture', media.id, 0, 0, 0, count, grade=grade or Grade())])
    return project, frames


def qualified_settings(**changes):
    values = dict(enabled=True, hue_center=0., hue_width=.04, hue_softness=.04,
                  saturation_low=.45, saturation_high=.75, saturation_softness=.12,
                  luma_low=.20, luma_high=.36, luma_softness=.08,
                  exposure=.2, saturation=.75, hue_shift=.12)
    values.update(changes)
    return QualifierSettings(**values)


def ramp(value, low, high, softness):
    if low <= value <= high:
        return 1.
    if softness == 0:
        return 0.
    distance = low - value if value < low else value - high
    return max(0., 1. - distance / softness)


def mask_reference(rgb, settings):
    result = []
    for sample in np.asarray(rgb, float).reshape(-1, 3):
        if not settings.enabled or min(sample) < 0 or max(sample) > 1:
            result.append(0.)
            continue
        hue, saturation, _ = colorsys.rgb_to_hsv(*sample)
        if settings.hue_width == .5:
            hue_weight = 1.
        elif max(sample) - min(sample) <= 1e-6:
            hue_weight = 0.
        else:
            distance = abs((hue - settings.hue_center + .5) % 1. - .5)
            hue_weight = ramp(distance, 0., settings.hue_width, settings.hue_softness)
        result.append(hue_weight
                      * ramp(saturation, settings.saturation_low, settings.saturation_high,
                             settings.saturation_softness)
                      * ramp(np.dot(sample, LUMA), settings.luma_low, settings.luma_high,
                             settings.luma_softness))
    return np.asarray(result).reshape(np.shape(rgb)[:-1])


def qualifier_reference(rgb, settings):
    rgb = np.asarray(rgb, float)
    if not settings.enabled or (settings.exposure == 0 and settings.saturation == 1
                                and settings.hue_shift == 0):
        return rgb.copy()
    matte = mask_reference(rgb, settings).reshape(-1)
    output = []
    for sample, weight in zip(rgb.reshape(-1, 3), matte):
        if not weight:
            output.append(sample)
            continue
        hue, saturation, value = colorsys.rgb_to_hsv(*sample)
        rotated = np.array(colorsys.hsv_to_rgb((hue + settings.hue_shift) % 1., saturation, value))
        luma = np.dot(rotated, LUMA)
        adjusted = (luma + settings.saturation * (rotated - luma)) * 2 ** settings.exposure
        output.append(sample + weight * (adjusted - sample))
    return np.array(output).reshape(rgb.shape)


def curve_reference(points, hue):
    index = min(max(bisect_right([x for x, _ in points], hue) - 1, 0), len(points) - 2)
    (x0, y0), (x1, y1) = points[index:index + 2]
    return y0 + (hue - x0) * (y1 - y0) / (x1 - x0)


def selective_reference(rgb, grade):
    if not grade.enabled:
        return np.asarray(rgb).copy()
    output = []
    for sample in np.asarray(rgb, float).reshape(-1, 3):
        hue, saturation, value = colorsys.rgb_to_hsv(*sample)
        if saturation == 0:
            output.append(sample)
            continue
        rotated = np.array(colorsys.hsv_to_rgb(
            (hue + curve_reference(grade.hue_hue_curve, hue)) % 1., saturation, value))
        luma = np.dot(rotated, LUMA)
        output.append(luma + curve_reference(grade.hue_sat_curve, hue) * (rotated - luma))
    return np.array(output).reshape(np.shape(rgb))


def decode_rgb(path, width, height=HEIGHT):
    raw = ff('-i', path, '-frames:v', 1, '-pix_fmt', 'rgb48le', '-f', 'rawvideo', 'pipe:1')
    return np.frombuffer(raw, '<u2').reshape(height, width, 3).astype(float) / 65535


def patch_rgb(image):
    return np.array([image[16:48, i * PATCH + 16:(i + 1) * PATCH - 16].mean((0, 1), dtype=float)
                     for i in range(len(PATCH_HSY))])


def decode_yuv_patches(path, width, ten_bit):
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
    for index in range(len(PATCH_HSY)):
        values = []
        for channel, plane in enumerate(planes):
            sx, sy = (1 if channel == 0 else 2), (2 if channel and not ten_bit else 1)
            values.append(plane[16 // sy:48 // sy,
                                (index * PATCH + 16) // sx:((index + 1) * PATCH - 16) // sx].mean())
        result.append(values)
    return np.array(result)


def yuv_reference(rgb, ten_bit):
    red, green, blue = np.asarray(rgb, float).T
    luma = .2126 * red + .7152 * green + .0722 * blue
    scale = 4 if ten_bit else 1
    return scale * np.stack([16 + 219 * luma,
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
def test_rgb16_matte_discrimination_matches_real_preview_and_exports(tmp_path, engine, delivery):
    settings = qualified_settings()
    project, _ = source_project(tmp_path, grade=Grade(qualifier=settings))
    incoming = patch_values().astype(float) / 65535
    matte = mask_reference(incoming, settings)
    expected = qualifier_reference(incoming, settings)
    # Positive controls: both sides of red wrap, soft hue/sat/luma boundaries.
    np.testing.assert_allclose(matte[[0, 1]], 1., atol=0, rtol=0)
    assert all(0.1 < matte[i] < .8 for i in (2, 6, 7, 10, 11))
    assert .07 < matte[15] < .10
    np.testing.assert_array_equal(matte[[3, 4, 5, 8, 9, 12, 13, 14]], 0.)
    assert expected.min() >= 0 and expected.max() < 1
    # Hue moves outside the key. Recomputing selection after adjustment would
    # erase a fully selected change, so these are not self-consistency checks.
    assert mask_reference(expected[[0, 1]], settings).max() == 0
    assert np.max(abs(expected[[0, 1]] - incoming[[0, 1]])) > .1
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_allclose(patch_rgb(decode_rgb(stage, project.width)), expected,
                               atol=3 / 65535, rtol=0)
    ten = delivery == 'prores_422_hq_10bit'
    output = (engine.preview(project) if delivery == 'preview' else engine.render(
        project, tmp_path / ('qualified.mov' if ten else 'qualified.mp4'), export_profile=delivery))
    np.testing.assert_allclose(decode_yuv_patches(output, project.width, ten),
                               yuv_reference(expected, ten), atol=2., rtol=0)


@pytest.mark.parametrize('mode', ['qualifier-disabled', 'neutral-adjustments', 'grade-disabled'])
def test_disabled_or_neutral_qualifier_is_pixel_exact_through_delivery(tmp_path, engine, mode):
    project, _ = source_project(tmp_path)
    before_stage = patch_rgb(decode_rgb(
        engine._graded_segment(project.media[0], project.clips[0], project, None), project.width))
    before_preview = decode_yuv_patches(engine.preview(project), project.width, False)
    before_export = decode_yuv_patches(engine.render(
        project, tmp_path / 'before.mov', export_profile='prores_422_hq_10bit'), project.width, True)
    grade = project.clips[0].grade
    grade.qualifier = qualified_settings()
    if mode == 'qualifier-disabled':
        grade.qualifier.enabled = False
    elif mode == 'neutral-adjustments':
        grade.qualifier.exposure, grade.qualifier.saturation, grade.qualifier.hue_shift = 0., 1., 0.
    else:
        grade.enabled = False
    np.testing.assert_array_equal(patch_rgb(decode_rgb(
        engine._graded_segment(project.media[0], project.clips[0], project, None), project.width)), before_stage)
    np.testing.assert_array_equal(decode_yuv_patches(engine.preview(project), project.width, False), before_preview)
    after_export = engine.render(project, tmp_path / 'after.mov', export_profile='prores_422_hq_10bit')
    np.testing.assert_array_equal(decode_yuv_patches(after_export, project.width, True), before_export)


def test_all_hues_includes_achromatic_samples_but_still_respects_luma(tmp_path, engine):
    settings = qualified_settings(hue_width=.5, saturation_low=0., saturation_high=1.,
                                  exposure=.25, saturation=1., hue_shift=0.)
    project, _ = source_project(tmp_path, grade=Grade(qualifier=settings))
    incoming = patch_values().astype(float) / 65535
    expected = qualifier_reference(incoming, settings)
    assert expected.min() >= 0 and expected.max() < 1
    assert np.all(expected[13] > incoming[13]) and np.array_equal(expected[14], incoming[14])
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_allclose(patch_rgb(decode_rgb(stage, project.width)), expected, atol=3 / 65535, rtol=0)


def test_primary_then_hue_curves_then_qualifier_then_lut_real_delivery(tmp_path, engine):
    lut = tmp_path / 'swap-red-blue.cube'
    lut.write_text('LUT_3D_SIZE 2\n' + ''.join(
        f'{b} {g} {r}\n' for b in (0, 1) for g in (0, 1) for r in (0, 1)))
    grade = Grade(exposure=-.1, red_curve=[[0., .02], [1., .88]],
                  hue_hue_curve=[[0., .1], [1., .1]],
                  hue_sat_curve=[[0., .8], [1., .8]],
                  qualifier=qualified_settings(hue_center=.1, hue_width=.07,
                                               saturation_low=.2, luma_low=.1),
                  lut_path=str(lut), lut_strength=.6)
    project, _ = source_project(tmp_path, grade=grade)
    incoming = patch_values().astype(float) / 65535
    primary = primary_reference(incoming, grade)
    selective = selective_reference(primary, grade)
    qualified = qualifier_reference(selective, grade.qualifier)
    expected = .4 * qualified + .6 * qualified[:, [2, 1, 0]]
    wrong_order = selective_reference(qualifier_reference(primary, grade.qualifier), grade)
    assert np.max(abs(qualified - wrong_order)) > .01
    snapshot = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    np.testing.assert_allclose(patch_rgb(snapshot.frame), selective, rtol=0, atol=6e-7)
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_allclose(patch_rgb(decode_rgb(stage, project.width)), expected, atol=3 / 65535, rtol=0)
    output = engine.render(project, tmp_path / 'ordered.mov', export_profile='prores_422_hq_10bit')
    np.testing.assert_allclose(decode_yuv_patches(output, project.width, True),
                               yuv_reference(expected, True), atol=2., rtol=0)


@pytest.mark.parametrize('creative', [False, True], ids=['creative-bypassed', 'creative-enabled'])
def test_qualifier_picker_uses_exact_restored_timed_pre_adjustment_stage(tmp_path, engine, creative):
    from test_color_picker_restoration_render import (decode_yuv, subsample_reference,
                                                      yuv_reference as frame_yuv_reference)
    height, width = 24, 40
    yy, xx = np.mgrid[:height, :width]
    noise = .018 * (2 * ((xx // 2 + yy // 2) % 2) - 1)
    rgb = np.array([.35, .25, .2])[None, None, :] + noise[..., None]
    frames = np.rint(65535 * np.stack([rgb + i * .017 for i in range(7)])).astype('<u2')
    project, frames = source_project(tmp_path, frames, transfer='iec61966-2-1')
    clip = project.clips[0]
    clip.duration, clip.source_in = 3, Fraction(3, 2)
    clip.time_map = ClipTimeMap(Fraction(3, 2), Fraction(6), Fraction(3, 2))
    clip.restoration = RestorationSettings(enabled=True, denoise=.9, sharpen=.3)
    clip.grade = Grade(enabled=creative, input_encoding='srgb', exposure=.15,
                       temperature=.05, tint=.04, red_curve=[[0., .02], [1., .9]],
                       hue_hue_curve=[[0., .13], [1., .13]],
                       hue_sat_curve=[[0., .72], [1., .72]],
                       qualifier=qualified_settings(hue_width=.5, saturation_low=0.,
                                                    saturation_high=1., luma_low=0., luma_high=1.),
                       lut_path='/intentionally-missing-lut.cube')
    saved = deepcopy(project)
    expected_frames = []
    for local, source_frame in enumerate((1, 3, 4)):
        normalized = normalize_reference(frames[source_frame].astype(float) / 65535, 'srgb')
        restored = restoration_reference(normalized, clip.restoration)
        primary = primary_reference(restored, clip.grade)
        expected = selective_reference(primary, clip.grade)
        expected_frames.append(qualifier_reference(expected, clip.grade.qualifier)
                               if creative else expected)
        snapshot = capture_picker_snapshot(project, 'picture', local, stage='qualifier')
        old_snapshot = capture_picker_snapshot(project, 'picture', local)
        assert snapshot.source_frame == source_frame
        assert snapshot.source_time == Fraction(source_frame, 6)
        assert snapshot.restoration_applied and snapshot.stage == 'qualifier'
        assert snapshot.valid_mask.all() and snapshot.is_current(project)
        np.testing.assert_allclose(snapshot.frame, expected, rtol=0, atol=7e-7)
        np.testing.assert_allclose(old_snapshot.frame, primary, rtol=0, atol=7e-7)
        if creative:
            assert np.max(abs(snapshot.frame - old_snapshot.frame)) > .02
        else:
            np.testing.assert_array_equal(snapshot.frame, old_snapshot.frame)
        with pytest.raises(ValueError):
            snapshot.frame.setflags(write=True)
        with pytest.raises(ValueError):
            snapshot.valid_mask.setflags(write=True)
    assert project == saved
    # The snapshot must also agree with actual retimed, restored delivery,
    # with only its deliberately excluded qualifier applied afterward.
    clip.grade.lut_path = ''
    expected_frames = np.asarray(expected_frames)
    assert expected_frames.min() > 0 and expected_frames.max() < 1
    stage = engine._graded_segment(project.media[0], clip, project, None)
    raw = ff('-i', stage, '-pix_fmt', 'rgb48le', '-f', 'rawvideo', 'pipe:1')
    decoded = np.frombuffer(raw, '<u2').reshape(3, height, width, 3)
    np.testing.assert_allclose(decoded, np.rint(expected_frames * 65535), atol=1, rtol=0)
    for delivery in ('preview', 'prores_422_hq_10bit'):
        ten = delivery.startswith('prores')
        output = engine.preview(project) if not ten else engine.render(
            project, tmp_path / 'restored-qualified.mov', export_profile=delivery)
        reference = frame_yuv_reference(expected_frames, ten)
        for channel, plane in enumerate(decode_yuv(output, width, height, ten)):
            target = subsample_reference(reference, channel, ten)
            error = abs(plane[:, 4:-4, 4:-4] - target[:, 4:-4, 4:-4])
            assert error.mean() < 1.3, (delivery, channel, error.mean())
            assert np.quantile(error, .99) < (3. if ten else 4.), (delivery, channel, error.max())
    before = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    clip.grade.qualifier.hue_shift = -.1
    clip.grade.qualifier.exposure = -.7
    assert not before.is_current(project)
    after = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    np.testing.assert_array_equal(before.frame, after.frame)


def test_in_place_adjustment_changes_real_caches_without_picker_feedback(tmp_path, engine):
    project, _ = source_project(tmp_path, grade=Grade(qualifier=qualified_settings()))
    clip, media = project.clips[0], project.media[0]
    before = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    before_stage = engine._graded_segment(media, clip, project, None)
    before_preview = engine.preview(project)
    clip.grade.qualifier.hue_shift = -.08
    after = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    after_stage = engine._graded_segment(media, clip, project, None)
    after_preview = engine.preview(project)
    assert before_stage != after_stage and before_preview != after_preview
    np.testing.assert_array_equal(before.frame, after.frame)
    expected = qualifier_reference(patch_values().astype(float) / 65535, clip.grade.qualifier)
    np.testing.assert_allclose(patch_rgb(decode_rgb(after_stage, project.width)), expected,
                               atol=3 / 65535, rtol=0)
    np.testing.assert_allclose(decode_yuv_patches(after_preview, project.width, False),
                               yuv_reference(expected, False), atol=2., rtol=0)


def test_lifted_canvas_padding_is_qualified_even_though_not_a_valid_picker_target(tmp_path, engine):
    from test_color_picker_restoration_render import (decode_yuv, subsample_reference,
                                                      yuv_reference as frame_yuv_reference)
    frames = np.empty((3, 24, 40, 3), dtype='<u2')
    frames[:] = [20003, 14009, 9007]
    lifted = [[0., .1], [1., .9]]
    grade = Grade(red_curve=deepcopy(lifted), green_curve=deepcopy(lifted),
                  blue_curve=deepcopy(lifted), qualifier=qualified_settings(
                      hue_width=.5, saturation_low=0., saturation_high=1.,
                      luma_low=0., luma_high=1., exposure=.5, saturation=1., hue_shift=0.))
    project, _ = source_project(tmp_path, frames, grade=grade)
    project.height = 48
    canvas = np.zeros((48, 40, 3), float)
    canvas[12:36] = frames[0].astype(float) / 65535
    pre_qualifier = primary_reference(canvas, grade)
    expected = qualifier_reference(pre_qualifier, grade.qualifier)
    snapshot = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    assert snapshot.active_rect == (0, 12, 40, 24)
    assert not snapshot.valid_mask[:12].any() and not snapshot.valid_mask[36:].any()
    assert snapshot.valid_mask[12:36].all()
    np.testing.assert_allclose(snapshot.frame, pre_qualifier, atol=2e-7, rtol=0)
    # valid_mask is for selection only, never an implicit spatial matte. The
    # renderer grades the full fitted canvas, including these lifted margins.
    np.testing.assert_array_equal(mask_reference(pre_qualifier, grade.qualifier), 1.)
    np.testing.assert_allclose(expected[:12], .1 * 2 ** .5, atol=0, rtol=0)
    assert np.max(abs(expected[:12] - pre_qualifier[:12])) > .04
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_allclose(decode_rgb(stage, project.width, project.height), expected,
                               atol=1 / 65535, rtol=0)
    for delivery in ('preview', 'prores_422_hq_10bit'):
        ten = delivery.startswith('prores')
        output = engine.preview(project) if not ten else engine.render(
            project, tmp_path / 'qualified-padding.mov', export_profile=delivery)
        reference = frame_yuv_reference(np.repeat(expected[None], 3, axis=0), ten)
        for channel, plane in enumerate(decode_yuv(output, project.width, project.height, ten)):
            target = subsample_reference(reference, channel, ten)
            sy = 2 if channel and not ten else 1
            # Check both matte-selected padding and source picture, away from
            # the delivery codec's chroma filtering at the boundary between.
            for low, high in ((4, 8), (18, 30), (40, 44)):
                actual = plane[:, low // sy:high // sy, 4:-4]
                wanted = target[:, low // sy:high // sy, 4:-4]
                np.testing.assert_allclose(actual, wanted, atol=2., rtol=0)


def test_qualifier_picker_explicitly_rejects_transparent_source(tmp_path):
    from PIL import Image
    project, _ = source_project(tmp_path)
    source = tmp_path / 'transparent.png'
    Image.new('RGBA', (project.width, project.height), (180, 40, 20, 128)).save(source)
    project.media[0].path = str(source)
    with pytest.raises(PickerSourceError, match='transparent'):
        capture_picker_snapshot(project, 'picture', 0, stage='qualifier')


def test_qualifier_does_not_regrade_or_destroy_existing_overlay_alpha(tmp_path, engine):
    from PIL import Image
    from lumen.sticker_assets import load_sticker
    project, _ = source_project(tmp_path, grade=Grade(qualifier=qualified_settings()))
    baseline = decode_rgb(engine.preview(project), project.width)
    path = tmp_path / 'alpha-overlay.png'
    Image.new('RGBA', (48, 48), (0, 255, 0, 128)).save(path)
    asset = load_sticker(path)
    project.overlays = [Overlay('green', str(path), asset.digest, 0, 3,
                                keyframes=[OverlayKeyframe(0, 8, 8, 1., .5)])]
    output = decode_rgb(engine.preview(project), project.width)
    expected = baseline[16:40, 16:40] * .75 + np.array([0., 1., 0.]) * .25
    np.testing.assert_allclose(output[16:40, 16:40], expected, atol=7 / 255, rtol=0)
    np.testing.assert_allclose(output[16:48, PATCH + 16:2 * PATCH - 16],
                               baseline[16:48, PATCH + 16:2 * PATCH - 16], atol=3 / 255, rtol=0)
    # The supported alpha path is a separate straight-alpha overlay stage.
    overlay_stage = engine._overlay_segment(project, {project.overlays[0].id: asset}, None, None)
    rgba = np.frombuffer(ff('-i', overlay_stage, '-frames:v', 1, '-pix_fmt', 'rgba',
                            '-f', 'rawvideo', 'pipe:1'), np.uint8).reshape(HEIGHT, project.width, 4)
    assert np.all((rgba[16:40, 16:40, 3] >= 63) & (rgba[16:40, 16:40, 3] <= 64))
    assert not rgba[:, PATCH:, 3].any()

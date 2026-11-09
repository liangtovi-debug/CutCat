"""Independent RGB16, delivery, padding and transform acceptance for spatial keys.

Expected pixels use scalar color reference and analytic normalized coordinates,
never the production spatial-mask helper. Synthetic sources are decoded exactly.
"""
from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest

from lumen.clip_transform import ClipTransform
from lumen.color_picker_source import capture_picker_snapshot
from lumen.media import ffmpeg_available
from lumen.model import Grade
from lumen.qualifier import apply_qualifier, qualifier_mask
from lumen.qualifier_viewport import centered, overview
from test_color_highbit_reference import video_metadata
from test_color_picker_restoration import primary_reference
from test_color_picker_restoration_render import decode_yuv, subsample_reference, yuv_reference
from test_qualifier_render import (decode_rgb, engine, mask_reference, qualified_settings,
                                   qualifier_reference, source_project)

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def window_settings(**changes):
    args = dict(hue_center=0., hue_width=.10, hue_softness=.04,
                saturation_low=.2, saturation_high=.9, saturation_softness=.1,
                luma_low=.1, luma_high=.6, luma_softness=.1,
                exposure=.2, saturation=.8, hue_shift=.12,
                window_shape='ellipse', window_center_x=.32, window_center_y=.5,
                window_width=.30, window_height=.60, window_feather=.35,
                window_invert=False)
    args.update(changes)
    return qualified_settings(**args)


def spatial_reference(width, height, settings):
    if settings.window_shape == 'off':
        return np.ones((height, width))
    y, x = np.mgrid[:height, :width]
    dx = abs(((x + .5) / width - settings.window_center_x) / (settings.window_width / 2))
    dy = abs(((y + .5) / height - settings.window_center_y) / (settings.window_height / 2))
    radius = np.hypot(dx, dy) if settings.window_shape == 'ellipse' else np.maximum(dx, dy)
    if settings.window_feather:
        weight = np.clip(1 - np.maximum(0, radius - 1) / settings.window_feather, 0, 1)
    else:
        weight = (radius <= 1).astype(float)
    return 1 - weight if settings.window_invert else weight


def combined_reference(rgb, settings):
    rgb = np.asarray(rgb, float)
    spatial = spatial_reference(rgb.shape[1], rgb.shape[0], settings)
    # Color reference already blends its H/S/Y weight. A second independent
    # blend gives the PRODUCT of color and spatial weights, not a union.
    adjusted = qualifier_reference(rgb, settings)
    return rgb + spatial[..., None] * (adjusted - rgb)


def uniform_source(tmp_path, settings, width=160, height=96):
    frames = np.empty((3, height, width, 3), '<u2')
    frames[:] = [30007, 12011, 10009]
    return source_project(tmp_path, frames, grade=Grade(qualifier=settings), name='window')


def check_delivery(engine, project, expected, tmp_path, stem):
    for profile in ('preview', 'prores_422_hq_10bit'):
        ten = profile.startswith('prores')
        output = engine.preview(project) if not ten else engine.render(
            project, tmp_path / (stem + '.mov'), export_profile=profile)
        metadata = video_metadata(output)
        assert metadata['codec_name'] == ('prores' if ten else 'h264')
        assert metadata['pix_fmt'] == ('yuv422p10le' if ten else 'yuv420p')
        reference = yuv_reference(np.repeat(expected[None], 3, axis=0), ten)
        for channel, plane in enumerate(decode_yuv(output, project.width, project.height, ten)):
            # ProRes 4:2:2 is left-sited: each chroma sample is aligned
            # with the even luma column, not the center of a 2-pixel box.
            target = (reference[..., channel][..., ::2] if ten and channel
                      else subsample_reference(reference, channel, ten))
            error = abs(plane[:, 4:-4, 4:-4] - target[:, 4:-4, 4:-4])
            assert error.mean() < 1.3, (profile, channel, error.mean())
            assert np.quantile(error, .99) < (3.5 if ten else 4.5), (profile, channel, error.max())


@pytest.mark.parametrize('shape', ['ellipse', 'rectangle'])
@pytest.mark.parametrize('invert', [False, True])
def test_same_color_foreground_and_background_diverge_in_rgb16_preview_prores(
        tmp_path, engine, shape, invert):
    settings = window_settings(window_shape=shape, window_invert=invert)
    project, frames = uniform_source(tmp_path, settings)
    incoming = frames[0].astype(float) / 65535
    spatial = spatial_reference(project.width, project.height, settings)
    expected = combined_reference(incoming, settings)
    assert np.all(mask_reference(incoming, settings) == 1)
    assert np.any((spatial > .1) & (spatial < .9))
    # Identical incoming colors, clearly different regional results.
    np.testing.assert_array_equal(incoming[48, 50], incoming[48, 130])
    assert np.max(abs(expected[48, 50] - expected[48, 130])) > .15
    snapshot = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    np.testing.assert_allclose(snapshot.frame, incoming, atol=3e-8, rtol=0)
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_allclose(decode_rgb(stage, project.width, project.height),
                               expected, atol=3 / 65535, rtol=0)
    check_delivery(engine, project, expected, tmp_path, 'regional')


@pytest.mark.parametrize('shape', ['ellipse', 'rectangle'])
def test_partial_color_and_spatial_feathers_multiply_on_immutable_full_and_crop_source(
        tmp_path, engine, shape):
    settings = window_settings(window_shape=shape, saturation_low=.8,
                               saturation_high=.95, saturation_softness=.3)
    project, frames = uniform_source(tmp_path, settings, 640, 96)
    incoming = frames[0].astype(float) / 65535
    color = mask_reference(incoming, settings)
    spatial = spatial_reference(project.width, project.height, settings)
    expected_mask = color * spatial
    assert .3 < color[48, 200] < .7
    assert np.any((expected_mask > .05) & (expected_mask < .3))
    snapshot = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    saved = snapshot.frame.copy()
    for view in (overview(640, 96), centered(640, 96, (200, 48), 4),
                 centered(640, 96, (330, 50), 8)):
        frame = view.extract(snapshot.frame)
        coordinates = dict(canvas_size=(640, 96), origin=(view.x, view.y), stride=view.stride)
        np.testing.assert_allclose(qualifier_mask(frame, settings, **coordinates),
                                   view.extract(expected_mask), atol=3e-7, rtol=0)
        np.testing.assert_allclose(apply_qualifier(frame, settings, **coordinates),
                                   view.extract(combined_reference(incoming, settings)), atol=3e-7, rtol=0)
    np.testing.assert_array_equal(snapshot.frame, saved)
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_allclose(decode_rgb(stage, 640, 96), combined_reference(incoming, settings),
                               atol=3 / 65535, rtol=0)
    check_delivery(engine, project, combined_reference(incoming, settings), tmp_path, 'joint-feather')
    before_preview = engine.preview(project)
    project.clips[0].grade.qualifier.window_center_x = .7
    changed = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    assert not snapshot.is_current(project)
    np.testing.assert_array_equal(changed.frame, saved)
    assert engine.preview(project) != before_preview


def test_window_uses_fitted_canvas_padding_before_clip_mirror(tmp_path, engine):
    settings = window_settings(hue_width=.5, saturation_low=0., saturation_high=1.,
                               luma_low=0., luma_high=1., exposure=.6, saturation=1., hue_shift=0.,
                               window_shape='rectangle', window_center_x=.25,
                               window_center_y=.5, window_width=.4, window_height=1.,
                               window_feather=.1)
    project, frames = uniform_source(tmp_path, settings, 160, 48)
    project.height = 96
    grade = project.clips[0].grade
    grade.red_curve = grade.green_curve = grade.blue_curve = [[0., .1], [1., .9]]
    project.clips[0].transform = ClipTransform(mirror_x=True)
    canvas = np.zeros((96, 160, 3), float)
    canvas[24:72] = frames[0].astype(float) / 65535
    source = primary_reference(canvas, grade)
    graded = combined_reference(source, settings)
    snapshot = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    assert snapshot.active_rect == (0, 24, 160, 48)
    np.testing.assert_allclose(snapshot.frame, source, atol=2e-7, rtol=0)
    assert not snapshot.valid_mask[8, 40] and graded[8, 40, 0] > .14
    assert graded[8, 120, 0] == .1
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_allclose(decode_rgb(stage, 160, 96), graded, atol=3 / 65535, rtol=0)
    # Exact horizontal mirror happens AFTER grading/window, not before it.
    expected = graded[:, ::-1]
    assert expected[8, 120, 0] > .14 and expected[8, 40, 0] == .1
    check_delivery(engine, project, expected, tmp_path, 'padded-mirrored')

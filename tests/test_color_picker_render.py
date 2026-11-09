"""Picked anchors preserve actual pixels, then selected center changes delivery."""
from copy import deepcopy
import colorsys
import numpy as np
import pytest
from lumen.color_picker import insert_hue_points, sample_hue
from lumen.color_picker_source import capture_picker_snapshot
from lumen.render import RenderEngine
from test_selective_color_render import (source_project, patches, expected_patches,
    decode_rgb, patch_rgb, decoded_yuv_patches, yuv_reference, HUE_CURVE, SAT_CURVE)
from lumen.model import Grade

@pytest.mark.parametrize('key', ['hue_hue_curve', 'hue_sat_curve'])
def test_insert_is_pixel_neutral_then_edit_changes_real_preview_and_exports(tmp_path, key):
    project = source_project(tmp_path, Grade(hue_hue_curve=deepcopy(HUE_CURVE), hue_sat_curve=deepcopy(SAT_CURVE)))
    clip = project.clips[0]
    engine = RenderEngine(timeout=90)
    try:
        snapshot = capture_picker_snapshot(project, clip.id, 0)
        hue = sample_hue(snapshot.frame[32, 4 * 64 + 32])
        assert abs(hue - colorsys.rgb_to_hsv(*(patches()[4].astype(float) / 65535))[0]) < 1e-6
        before = decode_rgb(engine._graded_segment(project.media[0], clip, project, None), project.width)
        selected = insert_hue_points(getattr(clip.grade, key), hue)
        setattr(clip.grade, key, selected.points)
        after = decode_rgb(engine._graded_segment(project.media[0], clip, project, None), project.width)
        np.testing.assert_array_equal(after, before)
        selected.points[selected.selected_index][1] += .08 if key == 'hue_hue_curve' else -.2
        actual = patch_rgb(decode_rgb(engine._graded_segment(project.media[0], clip, project, None), project.width))
        expected = expected_patches(clip.grade)
        np.testing.assert_allclose(actual, expected, atol=3 / 65535, rtol=0)
        assert np.max(np.abs(actual[4] - patch_rgb(before)[4])) > .04
        # Distant blue is untouched by this center-only edit.
        np.testing.assert_array_equal(actual[7], patch_rgb(before)[7])
        for profile in ('preview', 'h264_8bit', 'prores_422_hq_10bit'):
            ten = profile.startswith('prores')
            path = engine.preview(project) if profile == 'preview' else engine.render(
                project, tmp_path / (profile + ('.mov' if ten else '.mp4')), export_profile=profile)
            np.testing.assert_allclose(decoded_yuv_patches(path, project.width, ten),
                                       yuv_reference(expected, ten), atol=2, rtol=0)
    finally:
        engine.close()

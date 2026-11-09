"""Decoded delivery acceptance for source-sampled H/S/Y′ ranges.

Expected ranges use scalar colorsys and BT.709 coefficients, and expected
pixels use the independent analytical references, not production color helpers.
All fixtures are generated locally; no account, network or external API is used.
"""
import colorsys
from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest

from lumen.color_picker_source import capture_picker_snapshot
from lumen.media import ffmpeg_available
from lumen.model import Grade
from lumen.qualifier import QualifierSettings
from lumen.qualifier_picker import initialize_from_sample
from test_color_picker_restoration import primary_reference
from test_qualifier_render import (PATCH, decode_rgb, decode_yuv_patches, engine,
                                   mask_reference, patch_rgb, patch_values,
                                   qualifier_reference, selective_reference,
                                   source_project, yuv_reference)


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def independent_ranges(rgb, base):
    hue, saturation, _ = colorsys.rgb_to_hsv(*map(float, rgb))
    luma = sum(float(value) * coefficient for value, coefficient
               in zip(rgb, (.2126, .7152, .0722)))
    return replace(base, hue_center=hue, hue_width=10 / 360,
                   saturation_low=max(0., saturation - .1),
                   saturation_high=min(1., saturation + .1),
                   luma_low=max(0., luma - .1), luma_high=min(1., luma + .1))


@pytest.mark.parametrize('delivery', ['preview', 'prores_422_hq_10bit'])
@pytest.mark.parametrize('sample_index', [0, 4], ids=['red-wrap', 'cyan'])
def test_sample_initialized_matte_and_decoded_delivery_match_independent_reference(
        tmp_path, engine, sample_index, delivery):
    lut = tmp_path / 'sample-test-swap-red-blue.cube'
    lut.write_text('LUT_3D_SIZE 2\n' + ''.join(
        f'{b} {g} {r}\n' for b in (0, 1) for g in (0, 1) for r in (0, 1)))
    base = QualifierSettings(enabled=True, hue_center=.4, hue_width=.5,
                             hue_softness=.012, saturation_softness=.05,
                             luma_softness=.05, exposure=.15,
                             saturation=.8, hue_shift=.12)
    grade = Grade(exposure=-.1, red_curve=[[0., .02], [1., .88]],
                  hue_hue_curve=[[0., .07], [1., .07]],
                  hue_sat_curve=[[0., .83], [1., .83]],
                  qualifier=deepcopy(base), lut_path=str(lut), lut_strength=.3)
    project, _ = source_project(tmp_path, grade=grade)
    before = deepcopy(project)
    snapshot = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    incoming = patch_values().astype(float) / 65535
    pre_qualifier = selective_reference(primary_reference(incoming, grade), grade)
    np.testing.assert_allclose(patch_rgb(snapshot.frame), pre_qualifier, atol=7e-7, rtol=0)
    rgb = snapshot.frame[32, sample_index * PATCH + 32]
    settings, sample = initialize_from_sample(rgb, base)
    expected_settings = independent_ranges(pre_qualifier[sample_index], base)
    for field in ('hue_center', 'hue_width', 'saturation_low', 'saturation_high',
                  'luma_low', 'luma_high'):
        assert getattr(settings, field) == pytest.approx(getattr(expected_settings, field), abs=5e-7)
    assert settings.enabled and settings.exposure == base.exposure
    assert sample.hue == pytest.approx(expected_settings.hue_center, abs=5e-7)
    assert project == before  # Sample initialization alone has no model side effects.
    matte = mask_reference(pre_qualifier, expected_settings)
    assert matte[sample_index] == 1
    assert matte[4 if sample_index == 0 else 0] == 0
    qualified = qualifier_reference(pre_qualifier, expected_settings)
    assert np.max(abs(qualified[sample_index] - pre_qualifier[sample_index])) > .025
    # Selected color rotates outside its original range; feedback sampling or
    # recomputing the matte from adjusted pixels would erase the correction.
    assert mask_reference(qualified[[sample_index]], expected_settings)[0] == 0
    expected = .7 * qualified + .3 * qualified[:, [2, 1, 0]]
    assert expected.min() >= 0 and expected.max() < 1
    project.clips[0].grade.qualifier = settings
    # The qualifier and LUT do not contaminate the frozen source sampling stage.
    fresh = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    np.testing.assert_array_equal(fresh.frame, snapshot.frame)
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_allclose(patch_rgb(decode_rgb(stage, project.width)), expected,
                               atol=3 / 65535, rtol=0)
    ten = delivery == 'prores_422_hq_10bit'
    output = (engine.preview(project) if not ten else engine.render(
        project, tmp_path / 'sample-initialized.mov', export_profile=delivery))
    np.testing.assert_allclose(decode_yuv_patches(output, project.width, ten),
                               yuv_reference(expected, ten), atol=2., rtol=0)


@pytest.mark.parametrize('delivery', ['preview', 'prores_422_hq_10bit'])
def test_replacing_sample_renders_only_new_range_and_disabled_sampling_is_identity(
        tmp_path, engine, delivery):
    base = QualifierSettings(enabled=False, hue_softness=0,
                             saturation_softness=0, luma_softness=0,
                             exposure=.25, saturation=.65, hue_shift=.1)
    project, _ = source_project(tmp_path, grade=Grade(qualifier=base))
    snapshot = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    red, _ = initialize_from_sample(snapshot.frame[32, 32], base)
    cyan, _ = initialize_from_sample(snapshot.frame[32, 4 * PATCH + 32], red)
    assert not cyan.enabled and not red.enabled
    incoming = patch_values().astype(float) / 65535
    expected_settings = independent_ranges(incoming[4], base)
    project.clips[0].grade.qualifier = cyan
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_allclose(patch_rgb(decode_rgb(stage, project.width)), incoming,
                               atol=1 / 65535, rtol=0)
    # Enabling remains an explicit choice. The second sample replaces all core
    # ranges; it is not a union with the previously clicked red.
    project.clips[0].grade.qualifier = replace(cyan, enabled=True)
    expected_settings.enabled = True
    matte = mask_reference(incoming, expected_settings)
    assert matte[4] == 1 and matte[0] == 0
    expected = qualifier_reference(incoming, expected_settings)
    np.testing.assert_array_equal(expected[0], incoming[0])
    assert np.max(abs(expected[4] - incoming[4])) > .025
    ten = delivery == 'prores_422_hq_10bit'
    output = (engine.preview(project) if not ten else engine.render(
        project, tmp_path / 'sample-replaced.mov', export_profile=delivery))
    np.testing.assert_allclose(decode_yuv_patches(output, project.width, ten),
                               yuv_reference(expected, ten), atol=2., rtol=0)

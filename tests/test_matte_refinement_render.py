"""Matte-level acceptance against an independent scalar color/set/window oracle.

Only synthetic lossless RGB16 sources are used. Every delivery assertion decodes
newly rendered media; expected pixels do not call production qualifier helpers.
"""
import colorsys
from dataclasses import asdict, replace
import json
from uuid import uuid4

import numpy as np
import pytest

from lumen.color_picker_source import capture_picker_snapshot
from lumen.grade_transfer import copy_grade
from lumen.media import ffmpeg_available
from lumen.model import Grade, Project
from lumen.project_recovery import RecoveryStore
from lumen.qualifier import QualifierRange, apply_qualifier, qualifier_mask
from test_color_picker_restoration import primary_reference
from test_qualifier_render import decode_rgb, engine, selective_reference
from test_qualifier_sets_render import (PATCH, PROBES, assert_deliveries,
                                        fixture_project, range_weight, settings_for_sets)
from test_static_window_render import spatial_reference

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def scalar_reference(rgb, settings):
    """Union, subtraction, levels, then window, using Python float arithmetic."""
    spatial = spatial_reference(rgb.shape[1], rgb.shape[0], settings).ravel()
    weights, result, composed = [], [], []
    for pixel, region in zip(np.asarray(rgb, float).reshape(-1, 3), spatial):
        inclusion = max(range_weight(pixel, key) for key in [settings, *settings.inclusion_ranges])
        exclusion = max([0., *(range_weight(pixel, key) for key in settings.exclusion_ranges)])
        color = inclusion * (1. - exclusion)
        refined = min(1., max(0., (color-settings.matte_black)
                             / (settings.matte_white-settings.matte_black)))
        weight = refined * region if settings.enabled else 0.
        hue, saturation, value = colorsys.rgb_to_hsv(*pixel)
        rotated = np.array(colorsys.hsv_to_rgb((hue+settings.hue_shift) % 1., saturation, value))
        luma = sum(channel * coefficient for channel, coefficient in zip(rotated, (.2126, .7152, .0722)))
        adjusted = (luma + settings.saturation*(rotated-luma))*2**settings.exposure
        result.append(pixel + weight*(adjusted-pixel))
        weights.append(weight)
        composed.append(color)
    return (np.array(weights).reshape(rgb.shape[:2]), np.array(result).reshape(rgb.shape),
            np.array(composed).reshape(rgb.shape[:2]))


def assert_stage(engine, project, expected):
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_allclose(decode_rgb(stage, project.width, project.height), expected,
                               atol=3/65535, rtol=0)
    return stage


@pytest.mark.parametrize('black,white,shape,invert', [
    (0., 1., 'off', False), (.25, .8, 'off', False),
    (.25, .8, 'ellipse', False), (.25, .8, 'rectangle', True),
])
def test_levels_composed_before_window_rgb16_preview_prores(tmp_path, engine, black, white, shape, invert):
    settings = settings_for_sets(matte_black=black, matte_white=white,
        window_shape=shape, window_invert=invert, window_width=.65,
        window_height=.5, window_feather=.7)
    project, frames = fixture_project(tmp_path, settings)
    incoming = frames[0].astype(float)/65535
    matte, expected, composed = scalar_reference(incoming, settings)
    np.testing.assert_allclose(qualifier_mask(incoming.astype(np.float32), settings), matte,
                               atol=2e-6, rtol=0)
    if shape == 'off':
        centers = matte[32, np.arange(len(PROBES))*PATCH+32]
        np.testing.assert_array_equal(centers[[0, 1, 6]], 1.)
        np.testing.assert_array_equal(centers[[2, 3, 4]], 0.)
        assert 0. < centers[5] < 1. and 0. < centers[7] < 1.
    else:
        spatial = spatial_reference(project.width, project.height, settings)
        wrong_order = np.clip((composed*spatial-black)/(white-black), 0., 1.)
        assert np.max(abs(matte-wrong_order)) > .1
        assert np.any((matte > .03) & (matte < .8))
    # Inspection crops must retain the full source canvas and sampled pixel
    # centers. Recomputing the window in crop coordinates is observably wrong.
    crop = incoming.astype(np.float32)[7:57:2, 11:499:2]
    assert not crop.flags.c_contiguous
    context = dict(canvas_size=(project.width, project.height), origin=(11, 7), stride=2)
    np.testing.assert_allclose(qualifier_mask(crop, settings, **context),
                               matte[7:57:2, 11:499:2], atol=2e-6, rtol=0)
    np.testing.assert_allclose(apply_qualifier(crop, settings, **context),
                               expected[7:57:2, 11:499:2], atol=2e-6, rtol=0)
    assert_stage(engine, project, expected)
    assert_deliveries(project, expected, tmp_path, 'levels-')


def test_overlapping_add_subtract_refined_once_not_per_range(tmp_path, engine):
    include = QualifierRange(hue_center=2/3, hue_width=.02, hue_softness=.06,
        saturation_low=.8, saturation_high=1., saturation_softness=.25)
    exclude = QualifierRange(hue_center=2/3, hue_width=.02, hue_softness=.06,
        luma_low=0., luma_high=.13, luma_softness=.1)
    settings = settings_for_sets(matte_black=.1, matte_white=.6,
        inclusion_ranges=[include, include], exclusion_ranges=[exclude, exclude],
        window_shape='ellipse', window_width=.5, window_height=.5, window_feather=.8)
    project, frames = fixture_project(tmp_path, settings)
    incoming = frames[0].astype(float)/65535
    matte, expected, composed = scalar_reference(incoming, settings)
    single = replace(settings, inclusion_ranges=[include], exclusion_ranges=[exclude])
    single_matte, single_expected, _ = scalar_reference(incoming, single)
    np.testing.assert_array_equal(matte, single_matte)
    np.testing.assert_array_equal(expected, single_expected)
    sample = incoming[32, PATCH+32]
    iw, ew = range_weight(sample, include), range_weight(sample, exclude)
    region = spatial_reference(project.width, project.height, settings)[32, PATCH+32]
    levels = lambda w: min(1., max(0., (w-.1)/.5))
    correct = levels(iw*(1-ew))*region
    assert matte[32, PATCH+32] == pytest.approx(correct)
    assert abs(correct-levels(iw)*(1-levels(ew))*region) > .01
    np.testing.assert_allclose(qualifier_mask(incoming.astype(np.float32), settings), matte,
                               atol=2e-6, rtol=0)
    assert_stage(engine, project, expected)
    assert_deliveries(project, expected, tmp_path, 'overlap-levels-')


def test_save_copy_recovery_and_level_edits_change_decoded_caches_not_source(tmp_path, engine):
    settings = settings_for_sets()
    grade = Grade(exposure=-.1, hue_hue_curve=[[0., .02], [1., .02]], qualifier=settings)
    project, frames = fixture_project(tmp_path, settings, grade)
    incoming = selective_reference(primary_reference(frames[0].astype(float)/65535, grade), grade)
    snapshot = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    np.testing.assert_allclose(snapshot.frame, incoming, atol=6e-7, rtol=0)
    _, before_expected, _ = scalar_reference(incoming, settings)
    before_stage = assert_stage(engine, project, before_expected)
    before_preview = engine.preview(project)
    settings.matte_black, settings.matte_white = .3, .7
    assert not snapshot.is_current(project)
    changed = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    np.testing.assert_array_equal(snapshot.frame, changed.frame)
    matte, expected, _ = scalar_reference(incoming, settings)
    assert np.max(abs(expected-before_expected)) > .02
    after_stage = assert_stage(engine, project, expected)
    after_preview = engine.preview(project)
    assert before_stage != after_stage and before_preview != after_preview
    assert_deliveries(project, expected, tmp_path, 'edited-levels-')
    saved = tmp_path/'levels.lumen'
    project.save(saved)
    assert json.loads(saved.read_text())['clips'][0]['grade']['qualifier'] == asdict(settings)
    restored = Project.load(saved)
    assert restored == project
    copied = copy_grade(project, 'picture').grade_for_input('auto')
    assert copied.qualifier == settings
    copied.qualifier.matte_black = .2
    copied.qualifier.inclusion_ranges.clear()
    assert settings.matte_black == .3 and len(settings.inclusion_ranges) == 1
    store = RecoveryStore(tmp_path/'recovery')
    try:
        entry = store.write(project, str(uuid4()), saved)
        recovered = store.load(entry)
        assert recovered == project
        assert_stage(engine, recovered, expected)
        assert_deliveries(recovered, expected, tmp_path, 'recovered-levels-')
    finally:
        store.close()
    settings.matte_black, settings.matte_white = 0., 1.
    reset_stage = assert_stage(engine, project, before_expected)
    assert reset_stage == before_stage
    assert engine.preview(project) == before_preview


@pytest.mark.parametrize('disabled', [False, True])
def test_refinement_cannot_break_exact_neutral_or_disabled_bypass(tmp_path, engine, disabled):
    settings = settings_for_sets(matte_black=.35, matte_white=.65,
        window_shape='rectangle', window_invert=True, window_feather=.8)
    if disabled:
        settings.enabled = False
    else:
        settings.exposure, settings.saturation, settings.hue_shift = 0., 1., 0.
    project, frames = fixture_project(tmp_path, settings)
    incoming = frames[0].astype(np.float32)/np.float32(65535)
    untouched = incoming.copy()
    np.testing.assert_array_equal(apply_qualifier(incoming, settings), incoming)
    np.testing.assert_array_equal(incoming, untouched)
    # A cropped, non-contiguous source still bypasses exactly, without mutating
    # its parent image or manufacturing a new window-coordinate interpretation.
    crop = incoming[3:-4, 5:-7:2]
    assert not crop.flags.c_contiguous
    np.testing.assert_array_equal(apply_qualifier(crop, settings), crop)
    expected = frames[0].astype(float)/65535
    assert_stage(engine, project, expected)
    assert_deliveries(project, expected, tmp_path, 'bypass-levels-')

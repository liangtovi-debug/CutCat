"""Independent scalar acceptance of color set masks through real decoded media.

All sources are synthetic lossless RGB16. Expected masks never call production
qualifier math; exports are newly rendered and decoded, not cache-name checks.
"""
import colorsys
from copy import deepcopy
from dataclasses import asdict, replace
import json

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from lumen.color_picker_source import capture_picker_snapshot
from lumen.grade_transfer import copy_grade
from lumen.media import ffmpeg_available
from lumen.model import Editor, Grade, Project
from lumen.qualifier import QualifierRange, QualifierSettings, qualifier_mask
from test_color_picker_restoration import primary_reference
from test_color_picker_restoration_render import decode_yuv, subsample_reference, yuv_reference
from test_qualifier_picker_ui import click_sample, assert_sample_ranges
from test_qualifier_render import decode_rgb, engine, source_project, selective_reference
from test_qualifier_ui import app, loaded, window
from test_static_window_render import spatial_reference
from test_window_handles_render import fresh_delivery

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
PATCH = 64
# Bright red, blue, intervening cyan/green, excluded dark blue, feathered blue,
# red-wrap and feathered-red probes distinguish sets from a broadened interval.
PROBES = [(0., .7, .5), (2/3, .7, .5), (.5, .7, .5),
          (1/3, .7, .5), (2/3, .7, .2), (2/3 + .05, .7, .5),
          (.995, .7, .5), (.055, .7, .5)]


def fixture_project(tmp_path, settings, grade=None):
    colors = np.array([colorsys.hsv_to_rgb(*p) for p in PROBES])
    image = np.repeat(np.repeat(colors[None], 64, axis=0), PATCH, axis=1)
    frames = np.repeat(np.rint(image * 65535).astype('<u2')[None], 3, axis=0)
    return source_project(tmp_path, frames, grade=grade or Grade(qualifier=settings), name='sets')


def settings_for_sets(**changes):
    values = dict(enabled=True, hue_center=0., hue_width=.02, hue_softness=.06,
        saturation_low=.4, saturation_high=.9, saturation_softness=.1,
        luma_low=0., luma_high=1., luma_softness=0.,
        exposure=.3, saturation=.8, hue_shift=.08,
        inclusion_ranges=[QualifierRange(hue_center=2/3, hue_width=.02, hue_softness=.06)],
        exclusion_ranges=[QualifierRange(hue_center=2/3, hue_width=.09, hue_softness=.02,
            luma_low=0., luma_high=.09, luma_softness=.06)])
    values.update(changes)
    return QualifierSettings(**values)


def range_weight(rgb, key):
    hue, sat, _ = colorsys.rgb_to_hsv(*rgb)
    def ramp(value, lo, hi, feather):
        distance = max(lo - value, value - hi, 0.)
        return float(distance == 0) if feather == 0 else max(0., 1. - distance / feather)
    if key.hue_width == .5:
        hw = 1.
    elif max(rgb) - min(rgb) <= 1e-6:
        hw = 0.
    else:
        distance = abs((hue - key.hue_center + .5) % 1. - .5)
        hw = ramp(distance, 0., key.hue_width, key.hue_softness)
    return hw * ramp(sat, key.saturation_low, key.saturation_high, key.saturation_softness) * ramp(
        sum(v * c for v, c in zip(rgb, (.2126, .7152, .0722))),
        key.luma_low, key.luma_high, key.luma_softness)


def reference(rgb, settings):
    weights, output = [], []
    spatial = spatial_reference(rgb.shape[1], rgb.shape[0], settings).reshape(-1)
    for sample, region in zip(np.asarray(rgb, float).reshape(-1, 3), spatial):
        include = max(range_weight(sample, key) for key in [settings, *settings.inclusion_ranges])
        exclude = max([0., *(range_weight(sample, key) for key in settings.exclusion_ranges)])
        weight = include * (1. - exclude) * region if settings.enabled else 0.
        hue, sat, val = colorsys.rgb_to_hsv(*sample)
        rotated = np.array(colorsys.hsv_to_rgb((hue + settings.hue_shift) % 1., sat, val))
        luma = sum(v * c for v, c in zip(rotated, (.2126, .7152, .0722)))
        adjusted = (luma + settings.saturation * (rotated - luma)) * 2 ** settings.exposure
        weights.append(weight)
        output.append(sample + weight * (adjusted - sample))
    return np.array(weights).reshape(rgb.shape[:2]), np.array(output).reshape(rgb.shape)


def assert_deliveries(project, expected, tmp_path, stem):
    for profile in ('preview', 'prores_422_hq_10bit'):
        _, planes = fresh_delivery(project, tmp_path, stem + profile, profile)
        ten = profile.startswith('prores')
        target = yuv_reference(np.repeat(expected[None], 3, axis=0), ten)
        for channel, plane in enumerate(planes):
            wanted = (target[..., channel][..., ::2] if ten and channel
                      else subsample_reference(target, channel, ten))
            sx = 2 if channel else 1
            # Avoid codec chroma filtering across deliberately hard patch boundaries.
            errors = [abs(plane[:, 8:-8, (p * PATCH + 12)//sx:((p+1)*PATCH-12)//sx]
                          - wanted[:, 8:-8, (p * PATCH + 12)//sx:((p+1)*PATCH-12)//sx]).ravel()
                      for p in range(len(PROBES))]
            error = np.concatenate(errors)
            assert error.mean() < 1.3, (profile, channel, error.mean())
            assert np.quantile(error, .99) < (3.5 if ten else 4.5)


@pytest.mark.parametrize('shape,invert', [('off', False), ('ellipse', False), ('rectangle', True)])
def test_union_minus_union_times_one_window_rgb16_preview_prores(tmp_path, engine, shape, invert):
    settings = settings_for_sets(window_shape=shape, window_invert=invert,
        window_width=.65, window_height=.5, window_feather=.7)
    project, frames = fixture_project(tmp_path, settings)
    incoming = frames[0].astype(float) / 65535
    matte, expected = reference(incoming, settings)
    if shape == 'off':
        centers = matte[32, np.arange(len(PROBES)) * PATCH + 32]
        np.testing.assert_allclose(centers[[0, 1, 6]], 1., atol=0)
        np.testing.assert_array_equal(centers[[2, 3, 4]], 0.)
        assert all(.2 < centers[i] < .8 for i in (5, 7))
    else:
        assert np.any((matte > .03) & (matte < .8))
    np.testing.assert_allclose(qualifier_mask(incoming.astype(np.float32), settings), matte, atol=8e-7, rtol=0)
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_allclose(decode_rgb(stage, project.width, project.height), expected,
                               atol=3/65535, rtol=0)
    assert_deliveries(project, expected, tmp_path, 'sets-')
    saved = tmp_path / 'sets.lumen'
    project.save(saved)
    assert json.loads(saved.read_text())['clips'][0]['grade']['qualifier'] == asdict(settings)
    restored = Project.load(saved)
    assert restored == project
    clone = deepcopy(project)
    clone.clips[0].grade.qualifier.inclusion_ranges.clear()
    assert len(project.clips[0].grade.qualifier.inclusion_ranges) == 1
    copied = copy_grade(project, 'picture').grade_for_input('auto')
    assert copied.qualifier == settings
    copied.qualifier.exclusion_ranges.clear()
    assert len(settings.exclusion_ranges) == 1


def test_in_place_range_edits_invalidate_real_caches_not_source(tmp_path, engine):
    project, frames = fixture_project(tmp_path, settings_for_sets())
    clip, media = project.clips[0], project.media[0]
    snapshot = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    before_stage = engine._graded_segment(media, clip, project, None)
    before_preview = engine.preview(project)
    clip.grade.qualifier.inclusion_ranges.append(QualifierRange(hue_center=1/3, hue_width=.02))
    clip.grade.qualifier.exclusion_ranges[:] = [QualifierRange(hue_center=0., hue_width=.02)]
    assert not snapshot.is_current(project)
    changed = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    np.testing.assert_array_equal(snapshot.frame, changed.frame)
    after_stage = engine._graded_segment(media, clip, project, None)
    after_preview = engine.preview(project)
    assert after_stage != before_stage and after_preview != before_preview
    _, expected = reference(frames[0].astype(float)/65535, clip.grade.qualifier)
    np.testing.assert_allclose(decode_rgb(after_stage, project.width, project.height), expected,
                               atol=3/65535, rtol=0)
    assert_deliveries(project, expected, tmp_path, 'edited-')
    # Removing a subtract record must restore the red selection in decoded
    # output; resetting both lists returns to primary-only, not stale sets.
    excluded_red = decode_rgb(after_stage, project.width, project.height)[32, 32].copy()
    clip.grade.qualifier.exclusion_ranges.clear()
    restored_stage = engine._graded_segment(media, clip, project, None)
    restored_rgb = decode_rgb(restored_stage, project.width, project.height)
    assert np.max(abs(restored_rgb[32, 32] - excluded_red)) > .05
    _, restored_expected = reference(frames[0].astype(float)/65535, clip.grade.qualifier)
    np.testing.assert_allclose(restored_rgb, restored_expected, atol=3/65535, rtol=0)
    clip.grade.qualifier.inclusion_ranges.clear()
    reset_stage = engine._graded_segment(media, clip, project, None)
    reset_rgb = decode_rgb(reset_stage, project.width, project.height)
    assert np.max(abs(reset_rgb[32, 3*PATCH+32] - restored_rgb[32, 3*PATCH+32])) > .05
    _, reset_expected = reference(frames[0].astype(float)/65535, clip.grade.qualifier)
    np.testing.assert_allclose(reset_rgb, reset_expected, atol=3/65535, rtol=0)
    assert_deliveries(project, reset_expected, tmp_path, 'reset-')


def test_real_add_subtract_source_clicks_and_views_are_delivery_inert(window, app, tmp_path, monkeypatch):
    settings = settings_for_sets(inclusion_ranges=[], exclusion_ranges=[])
    grade = Grade(exposure=-.1, hue_hue_curve=[[0., .08], [1., .08]], qualifier=settings)
    project, frames = fixture_project(tmp_path, settings, grade)
    window.editor = Editor(project); window.refresh(); window.select_clip('picture')
    baseline = deepcopy(project)
    dialog = loaded(window)
    incoming = selective_reference(primary_reference(frames[0].astype(float)/65535, grade), grade)
    np.testing.assert_allclose(dialog.snapshot.frame, incoming, atol=6e-7, rtol=0)
    source = dialog.snapshot.frame.copy()
    for mode, index, attr in [('add', 1, 'inclusion_ranges'), ('subtract', 0, 'exclusion_ranges')]:
        dialog.sample_mode.setCurrentIndex(dialog.sample_mode.findData(mode))
        QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
        assert dialog.preview_choice.currentData() == 'source'
        # Overview is 1:1 for this 512-pixel source.
        assert dialog.viewport.stride == 1
        click_sample(dialog, index * PATCH + 32, 32)
        assert_sample_ranges(getattr(dialog.settings, attr)[-1], incoming[32, index*PATCH+32])
    pending = deepcopy(dialog.settings)
    expected_matte, _ = reference(incoming, pending)
    for mode in ('source', 'matte', 'adjusted', 'overlay'):
        dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData(mode))
        dialog.render_preview(); app.processEvents()
        np.testing.assert_allclose(dialog.matte, expected_matte, atol=1e-6, rtol=0)
        np.testing.assert_array_equal(dialog.snapshot.frame, source)
        assert dialog.settings == pending and window.project == baseline
    dialog.apply_settings()
    assert len(window.editor._undo) == 1
    _, expected = reference(incoming, pending)
    def forbidden(*args, **kwargs):
        pytest.fail('Display-only inspection leaked into renderer')
    monkeypatch.setattr('lumen.ui_qualifier.QualifierCanvas.paintEvent', forbidden)
    monkeypatch.setattr('lumen.qualifier_overlay.selection_overlay', forbidden)
    assert_deliveries(window.project, expected, tmp_path, 'clicked-')


def test_overlapping_feathers_use_max_not_sum_or_repeated_subtraction(tmp_path, engine):
    include = QualifierRange(hue_center=2/3, hue_width=.02, hue_softness=.06,
        saturation_low=.8, saturation_high=1., saturation_softness=.25)
    exclude = QualifierRange(hue_center=2/3, hue_width=.02, hue_softness=.06,
        luma_low=0., luma_high=.13, luma_softness=.1)
    settings = settings_for_sets(inclusion_ranges=[include, include],
        exclusion_ranges=[exclude, exclude], window_shape='ellipse',
        window_width=.5, window_height=.5, window_feather=.8)
    project, frames = fixture_project(tmp_path, settings)
    incoming = frames[0].astype(float)/65535
    matte, expected = reference(incoming, settings)
    single = replace(settings, inclusion_ranges=[include], exclusion_ranges=[exclude])
    single_matte, single_expected = reference(incoming, single)
    np.testing.assert_array_equal(matte, single_matte)
    np.testing.assert_array_equal(expected, single_expected)
    sample = incoming[32, PATCH + 32]
    iw, ew = range_weight(sample, include), range_weight(sample, exclude)
    spatial = spatial_reference(project.width, project.height, settings)[32, PATCH + 32]
    assert .2 < iw < .8 and .2 < ew < .8 and .2 < spatial < 1.
    correct = iw * (1-ew) * spatial
    assert correct == pytest.approx(matte[32, PATCH + 32])
    assert abs(correct - min(1, iw*2)*(1-ew)*spatial) > .05
    assert abs(correct - iw*(1-ew)**2*spatial) > .05
    assert abs(correct - iw*(1-ew)*spatial**2) > .01
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_allclose(decode_rgb(stage, project.width, project.height), expected,
                               atol=3/65535, rtol=0)
    assert_deliveries(project, expected, tmp_path, 'overlapping-')

"""Softness picking acceptance through synthetic RGB16 and decoded deliveries.

Expected feathers and pixels use scalar colorsys/BT.709 references, never the
production picker or mask as an oracle. All media is generated locally.
"""
import colorsys
from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest

from lumen.color_picker_source import capture_picker_snapshot
from lumen.media import ffmpeg_available
from lumen.model import Grade
from lumen.qualifier import QualifierRange, QualifierSettings
from lumen.qualifier_picker import soften_qualifier_range
from test_qualifier_render import source_project, decode_rgb, engine
from test_qualifier_sets_render import assert_deliveries
from test_qualifier_asymmetric_render import independent_reference as reference, independent_range as range_weight

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
PATCH = 64


def source_colors():
    # Eight patches cover hue wrap, both S and Y edges and achromatic samples.
    hsy = [(.98, .65, .25), (.02, .65, .25), (.4, .2, .25),
           (.4, .85, .25), (.4, .65, .12), (.4, .65, .4),
           (.4, 0., .25), (.7, .65, .25)]
    values = []
    for h, s, y in hsy:
        unit = np.array(colorsys.hsv_to_rgb(h, s, 1.))
        values.append(unit * y / np.dot(unit, [.2126, .7152, .0722]))
    return np.rint(np.array(values) * 65535).astype('<u2')


def sampled_fixture(tmp_path, settings):
    pixels = np.repeat(np.repeat(source_colors()[None], 64, axis=0), PATCH, axis=1)
    return source_project(tmp_path, np.repeat(pixels[None], 3, axis=0),
                          grade=Grade(qualifier=settings), name='softness')


def independently_softened(settings, rgb, target, channel):
    key = settings if target[0] == 'base' else getattr(settings, target[0])[target[1]]
    h, s, _ = colorsys.rgb_to_hsv(*map(float, rgb))
    y = np.dot(rgb, [.2126, .7152, .0722])
    if channel == 'hue':
        outside = max(0., abs((h - key.hue_center + .5) % 1. - .5) - key.hue_width)
    else:
        value = s if channel == 'saturation' else y
        outside = max(0., getattr(key, channel + '_low') - value,
                      value - getattr(key, channel + '_high'))
    result = deepcopy(settings)
    selected = result if target[0] == 'base' else getattr(result, target[0])[target[1]]
    field = channel + '_softness'
    if channel != 'hue':
        side = 'low' if value < getattr(key, channel + '_low') else 'high'
        field += '_' + side
    current = getattr(key, field)
    current = getattr(key, channel + '_softness') if current is None else current
    change = {field: max(current, 2 * outside)}
    if target[0] == 'base':
        result = replace(result, **change)
    else:
        getattr(result, target[0])[target[1]] = replace(selected, **change)
    return result


@pytest.mark.parametrize('channel,index,target,shape', [
    ('hue', 0, ('base', 0), 'off'),
    ('hue', 1, ('inclusion_ranges', 0), 'off'),
    ('saturation', 2, ('inclusion_ranges', 0), 'off'),
    ('saturation', 3, ('base', 0), 'off'),
    ('luma', 4, ('exclusion_ranges', 0), 'off'),
    ('luma', 5, ('base', 0), 'ellipse'),
    ('saturation', 6, ('base', 0), 'off'),
])
def test_selected_feather_rgb16_preview_and_prores(tmp_path, engine, channel, index, target, shape):
    key = QualifierRange(hue_center=(.01 if index == 0 else .99), hue_width=.01,
        hue_softness=0., saturation_low=.4, saturation_high=.7, saturation_softness=0.,
        luma_low=.2, luma_high=.3, luma_softness=0.)
    if channel != 'hue':
        key = replace(key, hue_width=.5)
    if channel == 'saturation':
        key = replace(key, luma_low=0., luma_high=1.)
    if channel == 'luma':
        key = replace(key, saturation_low=0., saturation_high=1.)
    settings = QualifierSettings(enabled=True, exposure=.3, saturation=.8, hue_shift=.08,
        window_shape=shape, window_width=.65, window_height=.5, window_feather=.7)
    if target[0] == 'base':
        for field, value in vars(key).items():
            setattr(settings, field, value)
    elif target[0] == 'inclusion_ranges':
        settings.hue_center, settings.hue_width = .7, .005
        settings.inclusion_ranges = [key]
    else:
        settings.hue_width = .5
        settings.exclusion_ranges = [key]
    project, frames = sampled_fixture(tmp_path, settings)
    original = deepcopy(project)
    snapshot = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    rgb = snapshot.frame[32, index * PATCH + 32]
    updated, sample = soften_qualifier_range(settings, rgb, target=target, channel=channel)
    expected_settings = independently_softened(settings, rgb, target, channel)
    expected_key = (expected_settings if target[0] == 'base' else
                    getattr(expected_settings, target[0])[target[1]])
    actual_key = updated if target[0] == 'base' else getattr(updated, target[0])[target[1]]
    for field in vars(expected_key):
        actual_value, expected_value = getattr(actual_key, field), getattr(expected_key, field)
        if isinstance(expected_value, (int, float)):
            assert actual_value == pytest.approx(expected_value, abs=3e-7)
        else:
            assert actual_value == expected_value
    assert range_weight(rgb.astype(float), expected_key) == pytest.approx(.5, abs=1e-7)
    expected_preserved = deepcopy(settings)
    if target[0] == 'base':
        changes = {field: value for field, value in vars(actual_key).items()
                   if value != getattr(settings, field)}
        assert len(changes) == 1
        expected_preserved = replace(expected_preserved, **changes)
    else:
        getattr(expected_preserved, target[0])[target[1]] = actual_key
    assert updated == expected_preserved  # Only one feather changes, no core or unrelated edit.
    assert project == original
    if index == 6:
        assert sample.hue is None
    incoming = frames[0].astype(float) / 65535
    before_matte, before_rgb = reference(incoming, settings)
    matte, expected = reference(incoming, expected_settings)
    assert np.max(abs(expected - before_rgb)) > .005
    if target[0] == 'exclusion_ranges':
        assert matte[32, index * PATCH + 32] < before_matte[32, index * PATCH + 32]
    project.clips[0].grade.qualifier = updated
    fresh = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    np.testing.assert_array_equal(fresh.frame, snapshot.frame)
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_allclose(decode_rgb(stage, project.width), expected, atol=3/65535, rtol=0)
    assert_deliveries(project, expected, tmp_path, 'softness-')

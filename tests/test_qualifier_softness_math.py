"""Independent scalar oracles for explicit per-range/per-channel soft picking."""
import colorsys
from copy import deepcopy
from dataclasses import asdict, replace

import numpy as np
import pytest

from lumen.qualifier import QualifierRange, QualifierSettings, range_softness
from lumen.qualifier_picker import (add_qualifier_range, replace_qualifier_range,
                                    sample_qualifier, soften_qualifier_range)


def rgb(h, s=1, v=1):
    return np.array(colorsys.hsv_to_rgb(h, s, v), np.float32)


def ramp(distance, feather):
    # Scalar mathematical oracle, independent of any production mask helpers.
    return 1.0 if distance <= 0 else max(0.0, 1.0 - distance / feather)


def fixture_settings():
    return QualifierSettings(enabled=True, exposure=.3, hue_shift=.02,
        window_shape='ellipse', matte_black=.1, matte_white=.9,
        inclusion_ranges=[QualifierRange(hue_center=.9), QualifierRange()],
        exclusion_ranges=[QualifierRange(hue_center=.4), QualifierRange()])


@pytest.mark.parametrize('target', [('base', 0), ('inclusion_ranges', 1),
                                     ('exclusion_ranges', 1)])
@pytest.mark.parametrize('channel', ['hue', 'saturation', 'luma'])
def test_only_target_feather_changes_and_inputs_remain_independent(target, channel):
    original = fixture_settings()
    selected = original if target[0] == 'base' else getattr(original, target[0])[target[1]]
    modifications = dict(hue_width=.02, saturation_low=.7, saturation_high=.8,
                         luma_low=.5, luma_high=.6)
    if target[0] == 'base':
        original = replace(original, **modifications)
    else:
        getattr(original, target[0])[target[1]] = replace(selected, **modifications)
    before = deepcopy(asdict(original))
    source = rgb(.125, .5, 1)
    bits = source.copy().view(np.uint32)
    updated, sample = soften_qualifier_range(original, source, target=target, channel=channel)
    selected = updated if target[0] == 'base' else getattr(updated, target[0])[target[1]]
    expected = deepcopy(before)
    destination = expected if target[0] == 'base' else expected[target[0]][target[1]]
    if channel == 'hue':
        outside = .125 - .02
    else:
        value = getattr(sample, channel)
        outside = max(0, destination[channel + '_low'] - value,
                      value - destination[channel + '_high'])
    edge = 'low' if channel != 'hue' and getattr(sample, channel) < destination[channel + '_low'] else 'high'
    field = channel + '_softness' if channel == 'hue' else channel + '_softness_' + edge
    destination[field] = max(.05, 2 * outside)
    assert asdict(updated) == expected
    effective = selected.hue_softness if channel == 'hue' else range_softness(selected, channel, edge)
    assert ramp(outside, effective) >= .5
    assert asdict(original) == before
    np.testing.assert_array_equal(source.view(np.uint32), bits)
    assert updated is not original
    assert updated.inclusion_ranges is not original.inclusion_ranges
    assert updated.exclusion_ranges is not original.exclusion_ranges


@pytest.mark.parametrize('center,hue,width', [(0.98, .02, .01), (.02, .98, .01),
                                             (1, 0, 0), (0, .5, .25)])
def test_circular_hue_uses_short_arc_and_inclusive_limit(center, hue, width):
    settings = QualifierSettings(hue_center=center, hue_width=width, hue_softness=0)
    changed, sample = soften_qualifier_range(settings, rgb(hue))
    # Compare all three neighboring turns, not production min(distance,1-distance).
    distance = min(abs(sample.hue - center + shift) for shift in (-1, 0, 1))
    expected = 2 * max(0, distance - width)
    assert changed.hue_softness == pytest.approx(expected, abs=1e-15)
    assert ramp(max(0, distance - width), changed.hue_softness) >= .5


@pytest.mark.parametrize('channel', ['saturation', 'luma'])
@pytest.mark.parametrize('value,low,high', [(0, .5, .6), (1, .4, .5),
                                          (.5, .5, .5), (.5, 0, 1)])
def test_interval_below_above_boundary_and_maximum(channel, value, low, high):
    source = rgb(0, value) if channel == 'saturation' else np.full(3, value, np.float32)
    settings = QualifierSettings(**{channel + '_low': low, channel + '_high': high,
                                    channel + '_softness': 0})
    result, sample = soften_qualifier_range(settings, source, channel=channel)
    observed = getattr(sample, channel)
    distance = max(low - observed, observed - high, 0)
    edge = 'low' if observed < low else 'high'
    effective = range_softness(result, channel, edge)
    assert effective == 2 * distance
    assert ramp(distance, effective) >= .5
    assert getattr(result, channel + '_softness') == 0


@pytest.mark.parametrize('channel', ['hue', 'saturation', 'luma'])
def test_sufficient_existing_feather_is_exact_noop(channel):
    settings = QualifierSettings(hue_width=0, hue_softness=.5,
        saturation_low=.6, saturation_high=.6, saturation_softness=.8,
        luma_low=.6, luma_high=.6, luma_softness=.8)
    before = asdict(settings)
    result, _ = soften_qualifier_range(settings, rgb(.125, .5), channel=channel)
    assert asdict(result) == before


@pytest.mark.parametrize('value', [0, .5, 1])
def test_gray_has_no_false_red_hue_and_legacy_pickers_stay_unchanged(value):
    source = np.full(3, value, np.float32)
    settings = QualifierSettings()
    for channel in ('saturation', 'luma'):
        _, sample = soften_qualifier_range(settings, source, channel=channel)
        assert sample.hue is None
        assert sample.saturation == 0
        assert sample.luma == pytest.approx(value)
    with pytest.raises(ValueError, match='ambiguous hue'):
        soften_qualifier_range(settings, source)
    assert sample_qualifier(source) is None
    assert replace_qualifier_range(settings, source) is None
    assert add_qualifier_range(settings, source) is None


@pytest.mark.parametrize('channel,source,kwargs', [
    ('hue', rgb(.5), dict(hue_width=0)),
    ('saturation', rgb(0, 0), dict(saturation_low=.6)),
    ('luma', np.zeros(3, np.float32), dict(luma_low=.6)),
])
def test_impossible_extent_is_rejected_not_clamped(channel, source, kwargs):
    settings = QualifierSettings(**kwargs)
    before = asdict(settings)
    with pytest.raises(ValueError, match='would exceed'):
        soften_qualifier_range(settings, source, channel=channel)
    assert asdict(settings) == before


@pytest.mark.parametrize('target', [None, [], ('base',), ('base', 1), ('base', True),
    ('base', -1), ('inclusion_ranges', 2), ('exclusion_ranges', 2), ('unknown', 0),
    ('base', 0.0), ([], 0)])
def test_invalid_and_stale_targets_rejected(target):
    with pytest.raises(ValueError, match='target|index'):
        soften_qualifier_range(fixture_settings(), rgb(0), target=target)


@pytest.mark.parametrize('channel', [None, '', 'value', [], 1])
def test_invalid_channels_rejected(channel):
    with pytest.raises(ValueError, match='channel'):
        soften_qualifier_range(QualifierSettings(), rgb(0), channel=channel)


@pytest.mark.parametrize('source', [[1, 0, 0], np.array([1, 0, 0], np.float64),
    np.zeros((1, 3), np.float32), np.array([np.nan, 0, 0], np.float32),
    np.array([np.inf, 0, 0], np.float32), np.array([-1, 0, 0], np.float32),
    np.array([1.01, 0, 0], np.float32)])
def test_invalid_source_rejected_even_for_nonhue(source):
    with pytest.raises(ValueError):
        soften_qualifier_range(QualifierSettings(), source, channel='luma')


@pytest.mark.parametrize('valid', [False, None, 0, 1, 'yes'])
def test_transparent_or_invalid_validity_rejected(valid):
    with pytest.raises(ValueError, match='visible source'):
        soften_qualifier_range(QualifierSettings(), rgb(0), valid=valid)


def test_invalid_settings_checked_before_source():
    settings = QualifierSettings()
    settings.hue_softness = float('nan')
    with pytest.raises(ValueError, match='hue_softness'):
        soften_qualifier_range(settings, None, valid=False)
    with pytest.raises(ValueError, match='settings'):
        soften_qualifier_range(None, rgb(0))


def test_near_gray_is_readable_without_inventing_hue():
    source = np.array([.5, .5 + 5e-7, .5], np.float32)
    _, sample = soften_qualifier_range(QualifierSettings(), source, channel='saturation')
    assert sample.hue is None
    expected_s = (float(source.max()) - float(source.min())) / float(source.max())
    assert sample.saturation == pytest.approx(expected_s, rel=1e-7)
    expected_y = sum(float(component) * coefficient
                     for component, coefficient in zip(source, (.2126, .7152, .0722)))
    assert sample.luma == pytest.approx(expected_y, abs=1e-7)
    assert sample_qualifier(source) is None


@pytest.mark.parametrize('width,hue', [(.5, .5), (.125, .125), (.125, .0625)])
def test_hue_core_and_full_circle_are_exact_noops(width, hue):
    settings = QualifierSettings(hue_width=width, hue_softness=0)
    result, _ = soften_qualifier_range(settings, rgb(hue))
    assert asdict(result) == asdict(settings)


def test_exact_half_weight_existing_feather_does_not_grow():
    settings = QualifierSettings(hue_width=.125, hue_softness=.25)
    result, sample = soften_qualifier_range(settings, rgb(.25))
    assert sample.hue == .25
    assert result.hue_softness == .25
    assert ramp(.25 - .125, result.hue_softness) == .5


def test_source_readout_uses_bounded_float32_saturation_and_luma():
    sources = [np.ones(3, np.float32), np.zeros(3, np.float32),
               np.array([np.nextafter(np.float32(0), np.float32(1)), 0, 0], np.float32)]
    sources.extend(np.random.default_rng(5).random((100, 3), dtype=np.float32))
    coefficients = np.array([.2126, .7152, .0722], np.float32)
    for source in sources:
        _, sample = soften_qualifier_range(QualifierSettings(), source, channel='luma')
        maximum, minimum = source.max(), source.min()
        expected_s = float((maximum - minimum) / maximum) if maximum > 0 else 0
        assert sample.saturation == expected_s
        assert sample.luma == float(np.sum(source * coefficients))
        assert 0 <= sample.saturation <= 1
        assert 0 <= sample.luma <= 1


@pytest.mark.parametrize('channel,source,kwargs', [
    ('hue', rgb(.5), dict(hue_width=np.nextafter(np.nextafter(.25, 0), 0))),
    ('saturation', rgb(0, 0), dict(saturation_low=np.nextafter(.5, 1))),
    ('luma', np.zeros(3, np.float32), dict(luma_low=np.nextafter(.5, 1))),
])
def test_first_representable_extent_over_limit_is_not_rounded_or_clamped(channel, source, kwargs):
    with pytest.raises(ValueError, match='would exceed'):
        soften_qualifier_range(QualifierSettings(**kwargs), source, channel=channel)

"""Independent scalar references for the encoded H/S/Y color-range qualifier.

Skin-like swatches below are synthetic mathematical fixtures, not evidence of
real-world skin detection, semantic selection or calibrated color accuracy.
"""
import colorsys
from dataclasses import fields, replace
import math

import numpy as np
import pytest

from lumen.qualifier import (QualifierSettings, apply_qualifier, qualifier_mask,
                             validate_qualifier)


def assert_bits_equal(actual, expected):
    np.testing.assert_array_equal(actual.view(np.uint32), expected.view(np.uint32))


def scalar_interval(value, low, high, softness):
    if low <= value <= high:
        return 1.0
    if softness == 0:
        return 0.0
    distance = low - value if value < low else value - high
    if distance >= softness:
        return 0.0
    return 1 - distance / softness


def scalar_mask(pixel, settings):
    rgb = tuple(float(v) for v in pixel)
    if not settings.enabled or min(rgb) < 0 or max(rgb) > 1:
        return 0.0
    hue, saturation, _ = colorsys.rgb_to_hsv(*rgb)
    if settings.hue_width == .5:
        hue_weight = 1.0
    elif max(rgb) - min(rgb) <= 1e-6:
        return 0.0
    else:
        distance = abs(hue - (settings.hue_center % 1))
        distance = min(distance, 1 - distance)
        hue_weight = scalar_interval(distance, 0, settings.hue_width,
                                     settings.hue_softness)
    y = sum(v * w for v, w in zip(rgb, (.2126, .7152, .0722)))
    return (hue_weight * scalar_interval(saturation, settings.saturation_low,
                                        settings.saturation_high, settings.saturation_softness)
            * scalar_interval(y, settings.luma_low, settings.luma_high,
                              settings.luma_softness))


def scalar_adjust(pixel, settings):
    rgb = tuple(float(v) for v in pixel)
    weight = scalar_mask(pixel, settings)
    if weight == 0:
        return rgb
    adjusted = rgb
    low, high = min(rgb), max(rgb)
    if settings.hue_shift and low != high:
        hue = colorsys.rgb_to_hsv(*rgb)[0]
        unit = colorsys.hsv_to_rgb((hue + settings.hue_shift) % 1, 1, 1)
        adjusted = tuple(low + (high - low) * value for value in unit)
    y = sum(v * w for v, w in zip(adjusted, (.2126, .7152, .0722)))
    adjusted = tuple((y + settings.saturation * (v - y)) * 2**settings.exposure
                     for v in adjusted)
    return tuple(a * (1 - weight) + b * weight for a, b in zip(rgb, adjusted))


def reference(rgb, settings, *, mask=False):
    operation = scalar_mask if mask else scalar_adjust
    shape = rgb.shape[:-1] if mask else rgb.shape
    return np.asarray([operation(p, settings) for p in rgb.reshape(-1, 3)],
                      dtype=np.float64).reshape(shape)


def hsv_pixels(hues, saturation=.8, value=.9):
    return np.array([colorsys.hsv_to_rgb(h, saturation, value) for h in hues], np.float32)


def test_documented_defaults_and_strict_serialization_are_independent():
    settings = QualifierSettings()
    assert settings.to_dict() == {
        'hue_enabled': True, 'saturation_enabled': True, 'luma_enabled': True,
        'enabled': False, 'hue_center': 0, 'hue_width': .1, 'hue_softness': .05,
        'saturation_low': 0, 'saturation_high': 1, 'saturation_softness': .05,
        'luma_low': 0, 'luma_high': 1, 'luma_softness': .05,
        'exposure': 0, 'saturation': 1, 'hue_shift': 0,
        'window_shape': 'off', 'window_center_x': .5, 'window_center_y': .5,
        'window_width': .5, 'window_height': .5, 'window_feather': .1,
        'window_invert': False, 'inclusion_ranges': [], 'exclusion_ranges': [],
        'matte_black': 0., 'matte_white': 1., 'matte_smoothing': 0., 'matte_morphology': 0.,
        'saturation_softness_low': None, 'saturation_softness_high': None,
        'luma_softness_low': None, 'luma_softness_high': None,
        'window_keyframes': [],
    }
    assert QualifierSettings.from_dict(settings.to_dict()) == settings
    data = settings.to_dict()
    data['enabled'] = True
    assert not settings.enabled
    for bad in (None, [], {}, {**data, 'unexpected': 1}):
        with pytest.raises(ValueError):
            QualifierSettings.from_dict(bad)
    for name in data:
        with pytest.raises(ValueError):
            QualifierSettings.from_dict({key: value for key, value in data.items() if key != name})


@pytest.mark.parametrize('enabled', [False, True])
def test_neutral_or_disabled_returns_independent_bit_exact_finite_frame(enabled):
    limit = np.finfo(np.float32).max
    rgb = np.array([[-limit, limit, -0.], [-3, .4, 9], [0, .081, .5]], np.float32)
    settings = QualifierSettings(enabled=enabled)
    if not enabled:
        settings.exposure, settings.saturation, settings.hue_shift = 1, 2, .1
    result = apply_qualifier(rgb, settings)
    assert_bits_equal(result, rgb)
    assert result.dtype == np.float32 and not np.shares_memory(result, rgb)
    if not enabled:
        assert_bits_equal(qualifier_mask(rgb, settings), np.zeros(3, np.float32))


def test_hue_key_trapezoid_wrap_and_center_one_equivalence():
    hues = [0, .02, .1, .125, .15, .2, .85, .875, .9, .98]
    rgb = hsv_pixels(hues)
    settings = QualifierSettings(enabled=True, hue_width=.1, hue_softness=.05)
    mask = qualifier_mask(rgb, settings)
    np.testing.assert_allclose(mask, [1, 1, 1, .5, 0, 0, 0, .5, 1, 1], atol=2e-6)
    assert_bits_equal(mask, qualifier_mask(rgb, replace(settings, hue_center=1)))
    np.testing.assert_allclose(mask, reference(rgb, settings, mask=True), atol=2e-6)


def test_hard_zero_width_and_inclusive_intervals_have_no_division_by_zero():
    rgb = np.array([[1, 0, 0], [1, .01, 0], [1, 0, .01], [0, 1, 0]], np.float32)
    settings = QualifierSettings(enabled=True, hue_width=0, hue_softness=0,
                                 saturation_low=1, saturation_high=1,
                                 saturation_softness=0, luma_softness=0)
    with np.errstate(all='raise'):
        np.testing.assert_array_equal(qualifier_mask(rgb, settings), [1, 0, 0, 0])
    grays = np.repeat(np.array([0, .25, .5, .75, 1], np.float32)[:, None], 3, axis=1)
    exact = replace(settings, hue_width=.5, saturation_low=0, saturation_high=0,
                    luma_low=.25, luma_high=.75)
    np.testing.assert_array_equal(qualifier_mask(grays, exact), [0, 1, 1, 1, 0])


def test_achromatic_epsilon_and_all_hues_bypass_include_gray_and_black():
    rgb = np.array([[0, 0, 0], [.3, .3, .3], [1, 1, 1],
                    [1e-6, 0, 0], [1.1e-6, 0, 0]], np.float32)
    settings = QualifierSettings(enabled=True, hue_width=.49, hue_softness=.5)
    np.testing.assert_array_equal(qualifier_mask(rgb, settings), [0, 0, 0, 0, 1])
    all_hues = replace(settings, hue_width=.5)
    np.testing.assert_array_equal(qualifier_mask(rgb, all_hues), np.ones(5))
    black_key = replace(all_hues, saturation_high=0, saturation_softness=0,
                        luma_low=0, luma_high=0, luma_softness=0)
    np.testing.assert_array_equal(qualifier_mask(rgb, black_key), [1, 0, 0, 0, 0])


def test_saturation_selection_is_hsv_not_hsl_and_y_is_not_lightness():
    # [1,.5,.5] has HSV saturation .5 but HSL saturation 1 and lightness .75.
    rgb = np.array([[1, .5, .5], [1, 0, 0], [0, 1, 0], [0, 0, 1]], np.float32)
    settings = QualifierSettings(enabled=True, hue_width=.5, saturation_low=.49,
                                 saturation_high=.51, saturation_softness=0)
    np.testing.assert_array_equal(qualifier_mask(rgb, settings), [1, 0, 0, 0])
    luma_key = replace(settings, saturation_low=0, saturation_high=1,
                       luma_low=.7, luma_high=.8, luma_softness=0)
    np.testing.assert_array_equal(qualifier_mask(rgb, luma_key), [0, 0, 1, 0])


def test_three_independent_feathers_multiply_not_add_or_take_minimum():
    rgb = hsv_pixels([.125], saturation=.4, value=.8)
    y = sum(float(v) * w for v, w in zip(rgb[0], (.2126, .7152, .0722)))
    settings = QualifierSettings(enabled=True, saturation_low=.5,
                                 saturation_softness=.2, luma_low=y+.1,
                                 luma_softness=.2)
    np.testing.assert_allclose(qualifier_mask(rgb, settings), [.125], atol=4e-7)


@pytest.mark.parametrize('offset', [-1/6, -.08, 0, .04, 1/6])
@pytest.mark.parametrize('factor', [0, .3, 1, 1.7, 2])
def test_selected_controls_against_independent_colorsys_reference(offset, factor):
    rgb = np.random.default_rng(126).uniform(0, 1, (137, 3)).astype(np.float32)
    settings = QualifierSettings(enabled=True, hue_center=.94, hue_width=.25,
                                 hue_softness=.2, saturation_low=.2,
                                 saturation_high=.85, saturation_softness=.15,
                                 luma_low=.1, luma_high=.7, luma_softness=.2,
                                 hue_shift=offset, saturation=factor, exposure=.4)
    np.testing.assert_allclose(qualifier_mask(rgb, settings),
                               reference(rgb, settings, mask=True), atol=1.5e-6)
    np.testing.assert_allclose(apply_qualifier(rgb, settings), reference(rgb, settings),
                               atol=2e-6, rtol=2e-6)


def test_one_incoming_key_and_adjustment_order_have_analytic_expected_value():
    rgb = np.array([[1, 0, 0], [0, 1, 0]], np.float32)
    settings = QualifierSettings(enabled=True, hue_width=.02, hue_softness=0,
                                 hue_shift=1/6, saturation=0, exposure=1)
    # Red -> yellow -> yellow's encoded luma (.9278) -> 2x code-value gain.
    # Re-keying the rotated yellow would wrongly reject the selected red.
    np.testing.assert_allclose(apply_qualifier(rgb, settings),
                               [[1.8556, 1.8556, 1.8556], [0, 1, 0]], atol=5e-7)


def test_half_key_blends_once_against_original_and_preserves_unselected_bits():
    rgb = hsv_pixels([0, .125, .35])
    settings = QualifierSettings(enabled=True, exposure=1)
    actual = apply_qualifier(rgb, settings)
    np.testing.assert_allclose(actual[0], rgb[0] * 2, atol=2e-7)
    np.testing.assert_allclose(actual[1], rgb[1] * 1.5, atol=6e-7)
    assert_bits_equal(actual[2], rgb[2])


def test_saturation_uses_luma_deviation_preserves_y_and_output_is_not_clipped():
    rgb = np.array([[1, 0, 0], [0, 1, 0], [.3, .7, .1]], np.float32)
    settings = QualifierSettings(enabled=True, hue_width=.5, saturation=0)
    gray = apply_qualifier(rgb, settings)
    y = rgb.astype(np.float64) @ np.array([.2126, .7152, .0722])
    np.testing.assert_allclose(gray, np.repeat(y[:, None], 3, axis=1), atol=1e-7)
    saturated = apply_qualifier(rgb, replace(settings, saturation=2))
    np.testing.assert_allclose(saturated @ np.array([.2126, .7152, .0722]), y, atol=1e-7)
    result = apply_qualifier(rgb[:1], replace(settings, saturation=2, exposure=1))
    np.testing.assert_allclose(result, [[3.5748, -.4252, -.4252]], atol=3e-7)
    assert result.min() < 0 and result.max() > 1


def test_synthetic_skinlike_range_is_only_a_color_selection_fixture():
    rgb = np.array([[.72, .46, .35], [.42, .26, .18], [.10, .35, .80],
                    [.12, .75, .20], [.5, .5, .5]], np.float32)
    settings = QualifierSettings(enabled=True, hue_center=.06, hue_width=.035,
                                 hue_softness=.02, saturation_low=.25,
                                 saturation_high=.75, luma_low=.15, luma_high=.75,
                                 saturation=.8, exposure=.2)
    mask = qualifier_mask(rgb, settings)
    np.testing.assert_array_equal(mask, [1, 1, 0, 0, 0])
    actual = apply_qualifier(rgb, settings)
    np.testing.assert_allclose(actual[:2], reference(rgb, settings)[:2], atol=2e-7)
    assert_bits_equal(actual[2:], rgb[2:])


def test_extended_range_exclusion_is_exact_and_avoids_extreme_arithmetic():
    limit = np.finfo(np.float32).max
    rgb = np.array([[-limit, limit, -0.], [-.0001, .5, .5], [1.0001, .2, .1],
                    [-3, -3, -3], [limit, limit, limit], [1, 0, 0]], np.float32)
    original = rgb.copy()
    settings = QualifierSettings(enabled=True, hue_width=.5, exposure=1,
                                 saturation=2, hue_shift=-1/6)
    with np.errstate(over='raise', invalid='raise', divide='raise'):
        mask = qualifier_mask(rgb, settings)
        actual = apply_qualifier(rgb, settings)
    np.testing.assert_array_equal(mask, [0, 0, 0, 0, 0, 1])
    assert_bits_equal(actual[:-1], rgb[:-1])
    assert_bits_equal(rgb, original)


def test_saturation_only_keeps_achromatic_values_bit_exact():
    grays = np.repeat(np.array([-0., 0, .081, .3, .7, 1], np.float32)[:, None], 3, axis=1)
    settings = QualifierSettings(enabled=True, hue_width=.5, saturation=2)
    assert_bits_equal(apply_qualifier(grays, settings), grays)
    ramp = np.repeat(np.linspace(0, 1, 10000, dtype=np.float32)[:, None], 3, axis=1)
    partial = replace(settings, luma_low=.5, luma_softness=.5)
    assert_bits_equal(apply_qualifier(ramp, partial), ramp)


def test_readonly_source_remains_unmodified_and_output_can_be_edited():
    rgb = hsv_pixels([0, .1, .2, .5])
    before = rgb.copy()
    rgb.flags.writeable = False
    settings = QualifierSettings(enabled=True, saturation=.5, exposure=.3)
    result = apply_qualifier(rgb, settings)
    mask = qualifier_mask(rgb, settings)
    assert result.flags.writeable and mask.flags.writeable
    result[:] = 0
    mask[:] = 0
    assert_bits_equal(rgb, before)


def test_arbitrary_shapes_strides_empty_scalar_and_bounded_key_evaluation(monkeypatch):
    from lumen import qualifier
    rgb = np.random.default_rng(291).uniform(0, 1, (11, 9, 3)).astype(np.float32)[::-1, ::2]
    settings = QualifierSettings(enabled=True, hue_center=.7, hue_width=.2,
                                 hue_softness=.3, exposure=.2, saturation=.6, hue_shift=.07)
    expected_mask = qualifier_mask(rgb, settings)
    expected_result = apply_qualifier(rgb, settings)
    monkeypatch.setattr(qualifier, '_BLOCK_PIXELS', 7)
    original_mask = qualifier._mask_block
    calls = []

    def recording_mask(block, config):
        calls.append(len(block))
        assert len(block) <= 7
        return original_mask(block, config)

    monkeypatch.setattr(qualifier, '_mask_block', recording_mask)
    actual = apply_qualifier(rgb, settings)
    assert calls and sum(calls) == rgb.size // 3  # Exactly one key per input block.
    assert_bits_equal(actual, expected_result)
    assert_bits_equal(qualifier_mask(rgb, settings), expected_mask)
    assert actual.shape == rgb.shape and actual.dtype == np.float32
    assert not np.shares_memory(actual, rgb)
    for shape in ((0, 3), (2, 0, 3), (0, 2, 0, 3)):
        empty = np.empty(shape, np.float32)
        assert apply_qualifier(empty, settings).shape == shape
        assert qualifier_mask(empty, settings).shape == shape[:-1]
    pixel = rgb[0, 0]
    assert qualifier_mask(pixel, settings).shape == ()
    np.testing.assert_allclose(apply_qualifier(pixel, settings), reference(pixel, settings), atol=8e-7)
    channels_first = np.random.default_rng(19).random((3, 4, 9)).astype(np.float32)
    unusual = channels_first.transpose(1, 2, 0)[::-1, ::2]
    np.testing.assert_allclose(qualifier_mask(unusual, settings),
                               reference(unusual, settings, mask=True), atol=2e-6)


def test_subnormal_softness_is_valid_and_does_not_create_infinite_slopes():
    tiny = math.nextafter(0, 1)
    settings = QualifierSettings(enabled=True, hue_width=0, hue_softness=tiny,
                                 saturation_low=1, saturation_high=1,
                                 saturation_softness=tiny, luma_softness=tiny,
                                 exposure=1)
    rgb = np.array([[1, 0, 0], [1, .01, 0]], np.float32)
    with np.errstate(over='raise', invalid='raise', divide='raise'):
        np.testing.assert_array_equal(qualifier_mask(rgb, settings), [1, 0])
        expected = np.array([[2, 0, 0], [1, .01, 0]], np.float32)
        assert_bits_equal(apply_qualifier(rgb, settings), expected)


def test_float_precision_ramp_has_no_eight_bit_intermediate():
    values = np.linspace(.2, .8, 8192, dtype=np.float32)
    rgb = np.column_stack((values, values * .3, values * .2))
    settings = QualifierSettings(enabled=True, hue_width=.5, exposure=.3)
    result = apply_qualifier(rgb, settings)
    assert len(np.unique(result[:, 0])) == len(values)
    np.testing.assert_allclose(result, rgb * 2**.3, atol=1e-7)


@pytest.mark.parametrize('rgb', [
    np.zeros((2, 3), np.float64), np.zeros((2, 3), np.uint8),
    np.zeros((2, 4), np.float32), np.zeros((3, 1), np.float32),
    np.array(1, np.float32), [[1, 0, 0]],
    np.array([[0, np.nan, 1]], np.float32),
    np.array([[0, np.inf, 1]], np.float32),
    np.array([[0, -np.inf, 1]], np.float32),
])
@pytest.mark.parametrize('enabled', [False, True])
def test_rejects_invalid_pixels_even_when_bypassed(rgb, enabled):
    settings = QualifierSettings(enabled=enabled)
    for operation in (qualifier_mask, apply_qualifier):
        with pytest.raises(ValueError):
            operation(rgb, settings)


@pytest.mark.parametrize('bad', [True, False, None, '0', (), {}, float('nan'), float('inf'), -float('inf')])
def test_window_keyframes_container_rejects_non_lists_in_all_entry_points(bad):
    with pytest.raises(ValueError):
        QualifierSettings(window_keyframes=bad)
    settings = QualifierSettings()
    settings.window_keyframes = bad
    with pytest.raises(ValueError):
        validate_qualifier(settings)
    for operation in (apply_qualifier, qualifier_mask):
        with pytest.raises(ValueError):
            operation(np.zeros((1, 3), np.float32), settings)
    data = QualifierSettings().to_dict()
    data['window_keyframes'] = bad
    with pytest.raises(ValueError):
        QualifierSettings.from_dict(data)


@pytest.mark.parametrize('name', [f.name for f in fields(QualifierSettings) if f.name not in ('enabled', 'hue_enabled', 'saturation_enabled', 'luma_enabled', 'window_invert', 'window_shape', 'inclusion_ranges', 'exclusion_ranges', 'window_keyframes')])
@pytest.mark.parametrize('bad', [True, False, None, '0', [], float('nan'), float('inf'), -float('inf')])
def test_all_numeric_settings_reject_invalid_types_and_nonfinite(name, bad):
    if bad is None and name.endswith(('_softness_low', '_softness_high')):
        assert getattr(QualifierSettings(**{name: bad}), name) is None
        return
    with pytest.raises(ValueError):
        QualifierSettings(**{name: bad})
    settings = QualifierSettings()
    setattr(settings, name, bad)
    with pytest.raises(ValueError):
        validate_qualifier(settings)
    for operation in (apply_qualifier, qualifier_mask):
        with pytest.raises(ValueError):
            operation(np.zeros((1, 3), np.float32), settings)


@pytest.mark.parametrize('bad', [0, 1, 'true', None, np.bool_(True)])
def test_enabled_is_strict_bool(bad):
    with pytest.raises(ValueError, match='boolean'):
        QualifierSettings(enabled=bad)


@pytest.mark.parametrize('name, low, high', [
    ('hue_center', 0, 1), ('hue_width', 0, .5), ('hue_softness', 0, .5),
    ('saturation_low', 0, 1), ('saturation_high', 0, 1), ('saturation_softness', 0, 1),
    ('luma_low', 0, 1), ('luma_high', 0, 1), ('luma_softness', 0, 1),
    ('exposure', -1, 1), ('saturation', 0, 2), ('hue_shift', -.1666666667, .1666666667),
])
def test_bounds_accept_endpoints_and_reject_adjacent_outside_values(name, low, high):
    for endpoint in (low, high):
        validate_qualifier(QualifierSettings(**{name: endpoint}))
    for bad in (math.nextafter(low, -math.inf), math.nextafter(high, math.inf)):
        with pytest.raises(ValueError):
            QualifierSettings(**{name: bad})


@pytest.mark.parametrize('name', ['saturation', 'luma'])
def test_range_order_is_validated(name):
    with pytest.raises(ValueError, match='must not exceed'):
        QualifierSettings(**{f'{name}_low': .7, f'{name}_high': .3})


@pytest.mark.parametrize('settings', [None, {}, [], False])
def test_settings_object_type_is_validated(settings):
    for operation in (apply_qualifier, qualifier_mask):
        with pytest.raises(ValueError, match='settings'):
            operation(np.zeros((1, 3), np.float32), settings)

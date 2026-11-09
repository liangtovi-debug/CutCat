"""Analytic contracts for replacement-only, full-precision H/S/Y sampling."""
import colorsys
from dataclasses import FrozenInstanceError, fields, replace

import numpy as np
import pytest

from lumen.qualifier import QualifierSettings, apply_qualifier, qualifier_mask
from lumen.qualifier_picker import (
    INITIAL_CHANNEL_HALF_WIDTH, INITIAL_HUE_HALF_WIDTH, QualifierSample,
    initialize_from_sample, replace_qualifier_range, sample_qualifier,
)


def rgb_from_hsv(hue, saturation=.8, value=.8):
    return np.array(colorsys.hsv_to_rgb(hue, saturation, value), dtype=np.float32)


def assert_bits_equal(actual, expected):
    np.testing.assert_array_equal(actual.view(np.uint32), expected.view(np.uint32))


@pytest.mark.parametrize('rgb,hue,luma', [
    ([1, 0, 0], 0, .2126), ([1, 1, 0], 1 / 6, .9278),
    ([0, 1, 0], 1 / 3, .7152), ([0, 1, 1], .5, .7874),
    ([0, 0, 1], 2 / 3, .0722), ([1, 0, 1], 5 / 6, .2848),
])
def test_primary_and_secondary_samples_use_hsv_saturation_and_encoded_y(rgb, hue, luma):
    original = np.array(rgb, np.float32)
    sample = sample_qualifier(original)
    assert isinstance(sample, QualifierSample)
    assert sample.rgb == tuple(rgb)
    assert sample.hue == pytest.approx(hue, abs=5e-8)
    assert sample.saturation == 1
    assert sample.luma == pytest.approx(luma, abs=1e-7)
    result, returned_sample = initialize_from_sample(original, QualifierSettings(enabled=True))
    assert returned_sample == sample
    assert result.hue_center == sample.hue
    assert result.hue_width == 10 / 360 == INITIAL_HUE_HALF_WIDTH
    assert result.saturation_low == .9 and result.saturation_high == 1
    assert result.luma_low == max(0, sample.luma - .1)
    assert result.luma_high == min(1, sample.luma + .1)
    assert qualifier_mask(original, result) == 1


def test_sample_uses_original_float_values_not_display_bytes_or_hsl():
    rgb = np.array([.8123457, .2432109, .4567891], np.float32)
    expected_hue, expected_saturation, _ = colorsys.rgb_to_hsv(*(float(v) for v in rgb))
    expected_y = sum(float(v) * weight for v, weight in zip(rgb, (.2126, .7152, .0722)))
    sample = sample_qualifier(rgb)
    assert sample.rgb == tuple(float(value) for value in rgb)
    assert sample.hue == pytest.approx(expected_hue, abs=1e-7)
    assert sample.saturation == pytest.approx(expected_saturation, abs=1e-7)
    assert sample.luma == pytest.approx(expected_y, abs=1e-7)
    hsl_hue, hsl_lightness, hsl_saturation = colorsys.rgb_to_hls(*sample.rgb)
    assert abs(sample.luma - hsl_lightness) > .05
    assert abs(sample.saturation - hsl_saturation) > .05
    display_rgb = np.rint(rgb * 255).astype(np.uint8).astype(np.float32) / 255
    display_sample = sample_qualifier(display_rgb)
    assert sample != display_sample
    assert sample.hue != display_sample.hue
    assert sample.saturation != display_sample.saturation
    assert sample.luma != display_sample.luma
    with pytest.raises(FrozenInstanceError):
        sample.hue = hsl_hue
    rgb[:] = 0
    assert sample.rgb != (0, 0, 0)


@pytest.mark.parametrize('rgb', [
    [0, 0, 0], [1, 1, 1], [.5, .5, .5], [0, 0, 1e-6],
    [0, np.nan, 1], [np.inf, 0, 1], [0, 1, -np.inf],
    [-1e-8, 0, 1], [1.000001, 0, 0],
    [-np.finfo(np.float32).max, 0, np.finfo(np.float32).max],
])
def test_unpickable_color_is_rejected_without_clamping_or_red_fallback(rgb):
    original = np.array(rgb, np.float32)
    before = original.copy()
    settings = QualifierSettings(enabled=True, hue_center=.3, exposure=.6)
    settings_before = settings.to_dict()
    with np.errstate(all='raise'):
        assert sample_qualifier(original) is None
        assert replace_qualifier_range(settings, original) is None
    assert_bits_equal(original, before)
    assert settings.to_dict() == settings_before


@pytest.mark.parametrize('rgb', [
    None, [1., 0., 0.], (1., 0., 0.),
    np.array([1, 0, 0], np.float64), np.array([255, 0, 0], np.uint8),
    np.array([1, 0, 0], np.int32), np.array([1, 0, 0], np.complex64),
    np.zeros((1, 3), np.float32), np.zeros((3, 1), np.float32),
    np.zeros(4, np.float32), np.array(1, np.float32), np.zeros(0, np.float32),
])
def test_non_float32_triplet_inputs_are_rejected(rgb):
    assert sample_qualifier(rgb) is None
    assert replace_qualifier_range(QualifierSettings(), rgb) is None


@pytest.mark.parametrize('valid', [False, np.bool_(False), None, 1, 'yes', np.array([True])])
def test_invalid_pixel_flag_does_not_sample(valid):
    rgb = np.array([1, 0, 0], np.float32)
    assert sample_qualifier(rgb, valid) is None
    assert replace_qualifier_range(QualifierSettings(), rgb, valid) is None


def test_chroma_boundary_agrees_with_qualifier_and_does_not_use_8bit_gray_threshold():
    epsilon = np.float32(1e-6)
    below = np.nextafter(epsilon, np.float32(0))
    above = np.nextafter(epsilon, np.float32(1))
    for value in (below, epsilon):
        assert sample_qualifier(np.array([0, 0, value], np.float32)) is None
    rgb = np.array([0, 0, above], np.float32)
    settings, sample = replace_qualifier_range(QualifierSettings(enabled=True), rgb)
    assert sample.hue == pytest.approx(2 / 3)
    assert sample.saturation == 1
    assert qualifier_mask(rgb, settings) == 1
    assert sample_qualifier(np.array([.5, .5001, .5], np.float32)) is not None


@pytest.mark.parametrize('hue', [0, 1 / 360, 359 / 360, 1 / 6, 1 / 3, .5, 2 / 3, 5 / 6])
def test_hue_core_wrap_and_immediate_outside_use_circular_distance(hue):
    rgb = rgb_from_hsv(hue)
    settings, sample = replace_qualifier_range(
        QualifierSettings(enabled=True, hue_softness=0), rgb)
    # Bypass S/Y only for this analytic hue-support test. Real picked samples
    # remain joint H/S/Y keys and receive separate full-weight coverage below.
    hue_only = replace(settings, saturation_low=0, saturation_high=1,
                       luma_low=0, luma_high=1)
    margin = 1e-5
    offsets = np.array([0, -10 / 360 + margin, 10 / 360 - margin,
                        -10 / 360 - margin, 10 / 360 + margin, .5])
    pixels = np.array([rgb_from_hsv((sample.hue + offset) % 1) for offset in offsets])
    np.testing.assert_array_equal(qualifier_mask(pixels, hue_only), [1, 1, 1, 0, 0, 0])


def test_tiny_negative_hue_wrap_canonicalizes_red_exactly():
    rgb = np.array([1, 0, 1e-12], np.float32)
    result, sample = replace_qualifier_range(QualifierSettings(enabled=True), rgb)
    assert sample.hue == result.hue_center == 0
    assert qualifier_mask(rgb, result) == 1


@pytest.mark.parametrize('rgb,expected_bounds', [
    ([.01, 0, 0], ('saturation_high', 1., 'luma_low', 0.)),
    ([1., .99, .99], ('saturation_low', 0., 'luma_high', 1.)),
])
def test_channel_ranges_clamp_at_zero_one_without_changing_sample(rgb, expected_bounds):
    original = np.array(rgb, np.float32)
    result, sample = replace_qualifier_range(QualifierSettings(enabled=True), original)
    first, low, second, high = expected_bounds
    assert getattr(result, first) == low and getattr(result, second) == high
    assert sample.rgb == tuple(float(value) for value in original)
    assert result.saturation_low <= sample.saturation <= result.saturation_high
    assert result.luma_low <= sample.luma <= result.luma_high
    assert qualifier_mask(original, result) == 1
    assert INITIAL_CHANNEL_HALF_WIDTH == .1


@pytest.mark.parametrize('enabled', [False, True])
def test_replacement_preserves_all_feathers_adjustments_enablement_and_input(enabled):
    original = QualifierSettings(
        enabled=enabled, hue_center=.123456789, hue_width=.3, hue_softness=.0123456789,
        saturation_low=.125, saturation_high=.75, saturation_softness=.234567891,
        luma_low=.2, luma_high=.7, luma_softness=.345678912,
        exposure=.876543219, saturation=.765432198, hue_shift=-.123456789,
    )
    before = original.to_dict()
    rgb = np.array([.8123457, .2432109, .4567891], np.float32)
    rgb_before = rgb.copy()
    result, sample = replace_qualifier_range(original, rgb)
    replaced = {'hue_center', 'hue_width', 'saturation_low', 'saturation_high',
                'luma_low', 'luma_high'}
    for field in fields(original):
        if field.name not in replaced:
            assert getattr(result, field.name) == getattr(original, field.name)
    assert original.to_dict() == before and result is not original
    assert_bits_equal(rgb, rgb_before)
    assert qualifier_mask(rgb, replace(result, enabled=True)) == 1
    result.hue_center = .2
    assert original.to_dict() == before
    assert sample.rgb == tuple(float(value) for value in rgb)


def test_second_pick_replaces_first_instead_of_creating_broad_union():
    red = np.array([1, 0, 0], np.float32)
    green = np.array([0, 1, 0], np.float32)
    blue = np.array([0, 0, 1], np.float32)
    first, _ = replace_qualifier_range(QualifierSettings(enabled=True), red)
    first_before = first.to_dict()
    second, sample = replace_qualifier_range(first, blue)
    assert first.to_dict() == first_before
    assert second.hue_center == sample.hue == pytest.approx(2 / 3)
    assert second.hue_width == 10 / 360
    np.testing.assert_array_equal(qualifier_mask(np.array([red, green, blue]), second), [0, 0, 1])
    repeated, repeated_sample = replace_qualifier_range(second, blue)
    assert repeated == second and repeated_sample == sample and repeated is not second


@pytest.mark.parametrize('enabled', [False, True])
def test_neutral_adjustments_remain_pixel_exact_after_picking(enabled):
    pixels = np.random.default_rng(8021).random((13, 17, 3), dtype=np.float32)
    pixels[0, 0] = [-0., 0., 1.]
    before = pixels.copy()
    result, _ = replace_qualifier_range(QualifierSettings(enabled=enabled), pixels[4, 7])
    output = apply_qualifier(pixels, result)
    assert_bits_equal(output, before)
    assert_bits_equal(pixels, before)
    assert not np.shares_memory(output, pixels)


def test_read_only_strided_sample_preserves_original_bits_and_signed_zero():
    storage = np.array([.8, 9., -0., 9., .2, 9.], np.float32)
    rgb = storage[::2]
    rgb.setflags(write=False)
    before = storage.copy()
    result, sample = replace_qualifier_range(QualifierSettings(enabled=True), rgb, np.bool_(True))
    assert_bits_equal(np.array(sample.rgb, np.float32), rgb)
    assert_bits_equal(storage, before)
    assert qualifier_mask(rgb, result) == 1


def test_dense_float32_sample_membership_is_exactly_one_and_channels_match_reference():
    rng = np.random.default_rng(401)
    pixels = rng.random((1000, 3), dtype=np.float32)
    for pixel in pixels:
        result, sample = replace_qualifier_range(QualifierSettings(enabled=True), pixel)
        expected_hue, expected_saturation, _ = colorsys.rgb_to_hsv(*sample.rgb)
        distance = abs(sample.hue - expected_hue)
        assert min(distance, 1 - distance) < 1e-7
        assert sample.saturation == pytest.approx(expected_saturation, abs=1e-7)
        assert sample.luma == pytest.approx(
            sum(v * weight for v, weight in zip(sample.rgb, (.2126, .7152, .0722))), abs=1e-7)
        assert qualifier_mask(pixel, result) == np.float32(1)


@pytest.mark.parametrize('bad', [None, {}, 'qualifier'])
def test_invalid_settings_fail_without_mutating_sample(bad):
    rgb = np.array([1, 0, 0], np.float32)
    before = rgb.copy()
    with pytest.raises(ValueError, match='Invalid qualifier settings'):
        replace_qualifier_range(bad, rgb)
    assert_bits_equal(rgb, before)


def test_mutated_invalid_settings_are_rejected_even_with_unpickable_sample():
    settings = QualifierSettings()
    settings.saturation_low = .9
    settings.saturation_high = .1
    with pytest.raises(ValueError, match='saturation_low must not exceed'):
        replace_qualifier_range(settings, np.zeros(3, np.float32))
    assert settings.saturation_low == .9 and settings.saturation_high == .1


@pytest.mark.parametrize('rgb,message', [
    ([1, 0, 0], 'Qualifier sample requires a float32 RGB triplet'),
    (np.array([1, 0, 0], np.float64), 'Qualifier sample requires a float32 RGB triplet'),
    (np.zeros((1, 3), np.float32), 'Qualifier sample requires a float32 RGB triplet'),
    (np.array([0, np.nan, 1], np.float32), 'Qualifier sample color values must be finite'),
    (np.array([0, np.inf, 1], np.float32), 'Qualifier sample color values must be finite'),
    (np.array([-.1, 0, 1], np.float32), 'Qualifier sample RGB must be within 0..1'),
    (np.array([1.1, 0, 0], np.float32), 'Qualifier sample RGB must be within 0..1'),
    (np.array([.5, .5, .5], np.float32), 'Qualifier sample has ambiguous hue (gray or near-gray)'),
    (np.array([0, 0, 1e-6], np.float32), 'Qualifier sample has ambiguous hue (gray or near-gray)'),
])
def test_initializer_reports_explicit_invalid_color_errors_without_changing_settings(rgb, message):
    settings = QualifierSettings(enabled=True, exposure=.3)
    before = settings.to_dict()
    with pytest.raises(ValueError) as caught:
        initialize_from_sample(rgb, settings)
    assert str(caught.value) == message
    assert settings.to_dict() == before


def test_initializer_and_optional_pick_have_identical_preserved_results():
    settings = QualifierSettings(enabled=False, exposure=.3, hue_softness=.0123456789,
                                 saturation_softness=.234567891, luma_softness=.345678912)
    rgb = np.array([.8123457, .2432109, .4567891], np.float32)
    assert initialize_from_sample(rgb, settings) == replace_qualifier_range(settings, rgb)
    settings.hue_width = float('nan')
    with pytest.raises(ValueError, match='hue_width'):
        initialize_from_sample(rgb, settings)

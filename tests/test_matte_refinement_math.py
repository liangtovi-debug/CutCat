"""Independent scalar contract for color-key levels, before spatial intersection.

Expected values never call a production mask, color conversion or level helper.
Float32 stage rounding is explicit; level division alone is double precision.
"""
import colorsys
import math

import numpy as np
import pytest

from lumen.qualifier import QualifierRange, QualifierSettings, apply_qualifier, qualifier_mask


def interval(value, low, high, softness):
    if low <= value <= high:
        return np.float32(1)
    distance = low - value if value < low else value - high
    return np.float32(max(0., 1. - distance / softness)) if softness else np.float32(0)


def scalar_range(pixel, settings):
    r, g, b = map(float, pixel)
    high, low = max(r, g, b), min(r, g, b)
    chroma = np.float32(high - low)
    saturation = float(np.float32(chroma / high)) if high else 0.
    terms = [np.float32(pixel[i] * np.float32(c))
             for i, c in enumerate((.2126, .7152, .0722))]
    luma = float(np.float32(np.float32(terms[0] + terms[1]) + terms[2]))
    weight = np.float32(interval(saturation, settings.saturation_low,
                               settings.saturation_high, settings.saturation_softness)
                        * interval(luma, settings.luma_low, settings.luma_high,
                                   settings.luma_softness))
    if settings.hue_width < .5:
        if chroma <= np.float32(1e-6):
            return np.float32(0)
        hue = colorsys.rgb_to_hsv(r, g, b)[0]
        distance = abs(hue - settings.hue_center % 1)
        weight = np.float32(weight * interval(min(distance, 1 - distance), 0,
                                              settings.hue_width, settings.hue_softness))
    return weight


def levels(weight, black, white):
    # Branching at endpoints is the scalar equivalent of a clamped quotient,
    # and cannot overflow for an accepted subnormal white-black interval.
    if black == 0 and white == 1:
        return weight
    value = float(weight)
    if value <= black:
        return np.float32(0)
    if value >= white:
        return np.float32(1)
    return np.float32((float(weight) - black) / (white - black))


def scalar_mask(rgb, settings, *, canvas_size=None, origin=(0, 0), stride=1):
    result = np.zeros(rgb.shape[:-1], np.float32)
    for index in np.ndindex(result.shape):
        pixel = rgb[index]
        if not settings.enabled or any(v < 0 or v > 1 for v in pixel):
            continue
        include = max(scalar_range(pixel, entry)
                      for entry in [settings, *settings.inclusion_ranges])
        exclude = max([np.float32(0)] + [scalar_range(pixel, entry)
                                      for entry in settings.exclusion_ranges])
        raw = np.float32(include * np.float32(1 - exclude))
        weight = levels(raw, settings.matte_black, settings.matte_white)
        if settings.window_shape != 'off':
            cw, ch = canvas_size or (rgb.shape[1], rgb.shape[0])
            y, x = index
            dx = ((origin[0] + x * stride + .5) / cw - settings.window_center_x) * (2 / settings.window_width)
            dy = ((origin[1] + y * stride + .5) / ch - settings.window_center_y) * (2 / settings.window_height)
            radius = math.hypot(dx, dy) if settings.window_shape == 'ellipse' else max(abs(dx), abs(dy))
            spatial = interval(radius, 0, 1, settings.window_feather)
            if settings.window_invert:
                spatial = np.float32(1 - spatial)
            weight = np.float32(weight * spatial)
        result[index] = weight
    return result


def saturation_ramp(**kwargs):
    return QualifierSettings(enabled=True, hue_width=.5, saturation_low=1.,
                             saturation_high=1., saturation_softness=1., **kwargs)


def test_exact_thresholds_and_neighboring_float32_weights():
    weights = np.array([0, .125, np.nextafter(np.float32(.25), np.float32(0)),
                        .25, np.nextafter(np.float32(.25), np.float32(1)),
                        .5, .75, 1], np.float32)
    # Black channel pins saturation to 1-gray exactly, without luma clipping.
    rgb = np.column_stack((np.ones_like(weights), 1 - weights, 1 - weights))
    s = saturation_ramp(matte_black=.25, matte_white=.75)
    actual = qualifier_mask(rgb, s)
    np.testing.assert_array_equal(actual, scalar_mask(rgb, s))
    np.testing.assert_array_equal(actual[[0, 1, 3, 5, 6, 7]], [0, 0, 0, .5, 1, 1])


@pytest.mark.parametrize('black,white', [
    (0., float(np.nextafter(0., 1.))),
    (float(np.nextafter(0., 1.)), float(np.nextafter(np.nextafter(0., 1.), 1.))),
    (.5, float(np.nextafter(.5, 1.))),
    (float(np.nextafter(.5, 0.)), .5),
    (float(np.nextafter(1., 0.)), 1.),
])
def test_subnormal_and_nextafter_intervals_stay_finite(black, white):
    rgb = np.array([[1, 1, 1], [1, .75, .75], [1, .5, .5], [1, 0, 0]], np.float32)
    s = saturation_ramp(matte_black=black, matte_white=white)
    with np.errstate(all='raise'):
        actual = qualifier_mask(rgb, s)
    assert np.isfinite(actual).all()
    np.testing.assert_array_equal(actual, scalar_mask(rgb, s))


@pytest.mark.parametrize('field', ['matte_black', 'matte_white'])
@pytest.mark.parametrize('value', [True, False, None, '0.5', [], float('nan'),
                                   float('inf'), -float('inf'), -.001, 1.001])
def test_invalid_level_types_and_bounds_rejected_even_disabled(field, value):
    with pytest.raises(ValueError):
        QualifierSettings(**{field: value})


@pytest.mark.parametrize('black,white', [(0., 0.), (1., 1.), (.5, .5), (.8, .2)])
def test_levels_must_be_strictly_ordered(black, white):
    with pytest.raises(ValueError):
        QualifierSettings(matte_black=black, matte_white=white)


@pytest.mark.parametrize('operation', [qualifier_mask, apply_qualifier])
def test_in_place_invalid_levels_validated_before_disabled_bypass(operation):
    s = QualifierSettings()
    s.matte_black = .8
    s.matte_white = .2
    with pytest.raises(ValueError):
        operation(np.zeros((1, 1, 3), np.float32), s)


def random_range(rng):
    saturation = sorted(rng.uniform(0, 1, 2))
    luma = sorted(rng.uniform(0, 1, 2))
    return QualifierRange(hue_center=float(rng.random()), hue_width=.5,
                          saturation_low=float(saturation[0]), saturation_high=float(saturation[1]),
                          saturation_softness=.35, luma_low=float(luma[0]),
                          luma_high=float(luma[1]), luma_softness=.4)


@pytest.mark.parametrize('seed', range(6))
def test_random_range_sets_refine_after_union_and_subtraction(seed):
    rng = np.random.default_rng(seed)
    rgb = rng.uniform(-.1, 1.1, (17, 23, 3)).astype(np.float32)
    s = QualifierSettings(enabled=True, hue_width=.5, saturation_low=.3,
        saturation_high=.6, luma_low=.25, luma_high=.65,
        inclusion_ranges=[random_range(rng) for _ in range(4)],
        exclusion_ranges=[random_range(rng) for _ in range(3)],
        matte_black=.17, matte_white=.83)
    np.testing.assert_array_equal(qualifier_mask(rgb, s), scalar_mask(rgb, s))


@pytest.mark.parametrize('shape', ['ellipse', 'rectangle'])
@pytest.mark.parametrize('invert', [False, True])
@pytest.mark.parametrize('stride', [1, 2, 5])
def test_window_intersection_follows_levels_and_crop_stride(shape, invert, stride):
    rng = np.random.default_rng(151)
    rgb = rng.random((37, 53, 3), dtype=np.float32)
    s = saturation_ramp(matte_black=.23, matte_white=.71, exposure=.5,
        window_shape=shape, window_width=.63, window_height=.41,
        window_feather=.65, window_invert=invert)
    expected = scalar_mask(rgb, s)
    np.testing.assert_allclose(qualifier_mask(rgb, s), expected, rtol=0, atol=6e-8)
    local = rgb[3:32:stride, 7:49:stride]
    before = local.copy()
    local.setflags(write=False)
    grid = dict(canvas_size=(53, 37), origin=(7, 3), stride=stride)
    np.testing.assert_array_equal(qualifier_mask(local, s, **grid),
                                  qualifier_mask(rgb, s)[3:32:stride, 7:49:stride])
    np.testing.assert_allclose(qualifier_mask(local, s, **grid),
                               scalar_mask(local, s, **grid), rtol=0, atol=6e-8)
    np.testing.assert_array_equal(apply_qualifier(local, s, **grid),
                                  apply_qualifier(rgb, s)[3:32:stride, 7:49:stride])
    np.testing.assert_array_equal(local, before)


@pytest.mark.parametrize('black', [0., -0.])
def test_default_levels_are_bit_exact_with_signed_zero(black):
    rng = np.random.default_rng(301)
    rgb = rng.random((9, 13, 3), dtype=np.float32)
    rgb[0, 0] = [-0., 0., -0.]
    s = saturation_ramp(matte_black=black, matte_white=1.)
    np.testing.assert_array_equal(qualifier_mask(rgb, s).view(np.uint32),
                                  scalar_mask(rgb, s).view(np.uint32))
    out = apply_qualifier(rgb, s)
    np.testing.assert_array_equal(out.view(np.uint32), rgb.view(np.uint32))
    assert not np.shares_memory(out, rgb)


@pytest.mark.parametrize('disabled', [False, True])
def test_refinement_preserves_extended_rgb_and_disabled_signed_zeros(disabled):
    maxfloat = np.finfo(np.float32).max
    rgb = np.array([[[-0., -1., maxfloat], [1.01, .5, .2],
                     [-maxfloat, maxfloat, -0.], [-0., 0., -0.]]], np.float32)
    s = saturation_ramp(matte_black=0., matte_white=float(np.nextafter(0., 1.)),
                         exposure=1., saturation=2.)
    s.enabled = not disabled
    with np.errstate(all='raise'):
        out = apply_qualifier(rgb, s)
        mask = qualifier_mask(rgb, s)
    np.testing.assert_array_equal(mask, np.zeros((1, 4), np.float32))
    np.testing.assert_array_equal(out.view(np.uint32), rgb.view(np.uint32))


def test_refined_exposure_blend_matches_scalar_not_reclassified_output():
    rgb = np.array([[[1., .75, .75], [1., .5, .5], [1., .25, .25]]], np.float32)
    s = saturation_ramp(matte_black=.1, matte_white=.9, exposure=1.)
    weights = scalar_mask(rgb, s)
    expected = np.empty_like(rgb)
    for index in np.ndindex(weights.shape):
        w = weights[index]
        expected[index] = rgb[index] * np.float32(1 - w) + (rgb[index] * np.float32(2)) * w
    np.testing.assert_array_equal(apply_qualifier(rgb, s), expected)


def test_levels_apply_once_after_include_max_and_exclude_max():
    rgb = np.array([[[1., .5, .5]]], np.float32)
    # Base is .5, added selection .75, exclusion .5: raw = .375.
    added = QualifierRange(hue_width=.5, saturation_low=.625,
                            saturation_high=1., saturation_softness=.5)
    removed = QualifierRange(hue_width=.5, saturation_low=.75,
                              saturation_high=1., saturation_softness=.5)
    s = saturation_ramp(matte_black=.25, matte_white=.75,
                         inclusion_ranges=[added], exclusion_ranges=[removed])
    np.testing.assert_array_equal(qualifier_mask(rgb, s), [[.25]])
    np.testing.assert_array_equal(scalar_mask(rgb, s), [[.25]])


def test_half_window_multiplies_already_refined_matte():
    rgb = np.tile(np.array([1., .5, .5], np.float32), (1, 9, 1))
    s = saturation_ramp(matte_black=.25, matte_white=.75,
                         window_shape='rectangle', window_width=4/9,
                         window_height=1., window_feather=1.)
    np.testing.assert_allclose(qualifier_mask(rgb, s),
                               [[0., .25, .5, .5, .5, .5, .5, .25, 0.]],
                               rtol=0, atol=6e-8)


def test_random_hue_ranges_and_levels_match_independent_colorsys_reference():
    rng = np.random.default_rng(701)
    rgb = rng.random((19, 29, 3), dtype=np.float32)
    s = QualifierSettings(enabled=True, hue_center=.98, hue_width=.07,
        hue_softness=.21, matte_black=.13, matte_white=.86,
        inclusion_ranges=[QualifierRange(hue_center=.31, hue_width=.12,
                                          hue_softness=.24)],
        exclusion_ranges=[QualifierRange(hue_center=.39, hue_width=.03,
                                          hue_softness=.18)])
    # Production hue rounds at float32 steps; colorsys intentionally computes
    # its independent hue in double precision. The ramp magnifies those ULPs.
    np.testing.assert_allclose(qualifier_mask(rgb, s), scalar_mask(rgb, s),
                               rtol=0, atol=2e-6)

"""Independent scalar square-morphology oracle and project-pixel support contract."""
from dataclasses import replace
import math

import numpy as np
import pytest

import lumen.qualifier as qualifier
from lumen.qualifier import QualifierSettings, QualifierRange, apply_qualifier, qualifier_mask
from test_matte_smoothing_math import _key, _interval, assert_bits_equal, rgb_from_key, settings


def scalar_morphology(raw, amount):
    """No production spatial helper: clamp each square-neighborhood coordinate."""
    height, width = raw.shape
    result = np.empty_like(raw)
    for y, x in np.ndindex(raw.shape):
        values = [raw[min(max(y+dy, 0), height-1), min(max(x+dx, 0), width-1)]
                  for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
        extreme = min(values) if amount < 0 else max(values)
        strength = np.float32(abs(amount))
        result[y, x] = (extreme if strength == 1 else
            np.float32(raw[y, x] + np.float32(strength*np.float32(extreme-raw[y, x]))))
    return result


def scalar_mask(rgb, s):
    height, width = rgb.shape[:2]
    raw = np.array([_key(pixel, s) for pixel in rgb.reshape(-1, 3)], np.float32).reshape(height, width)
    morph = scalar_morphology(raw, s.matte_morphology) if s.matte_morphology else raw
    result = np.empty_like(raw)
    for y, x in np.ndindex(raw.shape):
        weight = morph[y, x]
        if s.matte_smoothing:
            horizontal = []
            for dy in (-1, 0, 1):
                row = min(max(y+dy, 0), height-1)
                a, b, c = [morph[row, min(max(x+dx, 0), width-1)] for dx in (-1, 0, 1)]
                horizontal.append(np.float32(np.float32(np.float32(a+np.float32(2)*b)+c)*np.float32(.25)))
            a, b, c = horizontal
            blur = np.float32(np.float32(np.float32(a+np.float32(2)*b)+c)*np.float32(.25))
            weight = (blur if np.float32(s.matte_smoothing) == 1 else
                np.float32(weight+np.float32(np.float32(s.matte_smoothing)*np.float32(blur-weight))))
        if any(v < 0 or v > 1 for v in rgb[y, x]):
            weight = np.float32(0)
        if s.window_shape != 'off':
            dx = ((x+.5)/width-s.window_center_x)*2/s.window_width
            dy = ((y+.5)/height-s.window_center_y)*2/s.window_height
            radius = math.hypot(dx, dy) if s.window_shape == 'ellipse' else max(abs(dx), abs(dy))
            window = _interval(radius, 0, 1, s.window_feather)
            if s.window_invert:
                window = np.float32(1-window)
            weight = np.float32(weight*window)
        result[y, x] = weight
    return result


@pytest.mark.parametrize('shape', [(1, 1), (1, 9), (11, 1), (9, 13)])
@pytest.mark.parametrize('amount', [-1., -.37, 0., .41, 1.])
@pytest.mark.parametrize('smooth', [0., .63, 1.])
def test_impulses_holes_diagonals_steps_corners_and_combinations(shape, amount, smooth):
    height, width = shape
    keys = []
    for y, x in [(0, 0), (height//2, width//2), (height-1, width-1)]:
        key = np.zeros(shape, np.float32); key[y, x] = 1
        keys.extend([key, 1-key])
    yy, xx = np.indices(shape)
    keys.extend([(xx >= width//2).astype(np.float32), (yy >= xx).astype(np.float32),
                 np.ones(shape, np.float32), np.zeros(shape, np.float32)])
    for key in keys:
        rgb = rgb_from_key(key)
        s = settings(matte_morphology=amount, matte_smoothing=smooth)
        assert_bits_equal(qualifier_mask(rgb, s), scalar_mask(rgb, s))


def test_square_not_cross_and_amount_is_blend_not_radius():
    key = np.zeros((7, 7), np.float32); key[3, 3] = 1
    for amount in (.25, 1.):
        mask = qualifier_mask(rgb_from_key(key), settings(matte_morphology=amount))
        expected = np.zeros_like(key); expected[2:5, 2:5] = amount; expected[3, 3] = 1
        assert_bits_equal(mask, expected)
        shrunk = qualifier_mask(rgb_from_key(1-key), settings(matte_morphology=-amount))
        assert_bits_equal(shrunk, 1-expected)
    both = qualifier_mask(rgb_from_key(key), settings(matte_morphology=1, matte_smoothing=1))
    assert both[1, 1] == np.float32(1/16) and both[0, 0] == 0


@pytest.mark.parametrize('amount', [-1., -.43, .27, 1.])
@pytest.mark.parametrize('window,invert', [('off', False), ('ellipse', True), ('rectangle', False)])
def test_levels_sets_morph_smooth_window_order(amount, window, invert):
    rng = np.random.default_rng(235)
    rgb = rng.uniform(-.05, 1.05, (11, 17, 3)).astype(np.float32)
    s = settings(matte_morphology=amount, matte_smoothing=.7,
        matte_black=.19, matte_white=.79,
        inclusion_ranges=[QualifierRange(hue_width=.5, saturation_low=.3, saturation_high=.5,
                                        saturation_softness=.3, luma_low=.2, luma_high=.7)],
        exclusion_ranges=[QualifierRange(hue_width=.5, luma_low=.4, luma_high=.6,
                                        luma_softness=.2, saturation_low=.2, saturation_high=.4)],
        window_shape=window, window_invert=invert, window_width=.6, window_height=.4,
        window_feather=.3)
    np.testing.assert_allclose(qualifier_mask(rgb, s), scalar_mask(rgb, s), rtol=0, atol=2e-7)
    if window != 'off':
        window_only = qualifier_mask(np.zeros_like(rgb), QualifierSettings(enabled=True, hue_width=.5,
            window_shape=window, window_invert=invert, window_width=.6, window_height=.4,
            window_feather=.3))
        assert not qualifier_mask(rgb, s)[window_only == 0].any()


@pytest.mark.parametrize('stride', [1, 2, 3, 5, 17])
@pytest.mark.parametrize('amount,smooth', [(-1., 0.), (.4, 0.), (-.6, .7), (1., 1.)])
def test_dense_halo_sparse_overview_and_true_boundaries_are_bit_exact(stride, amount, smooth):
    rng = np.random.default_rng(231)
    full = rng.random((31, 43, 3), dtype=np.float32)
    s = settings(matte_morphology=amount, matte_smoothing=smooth, matte_black=.1, matte_white=.8,
        exposure=.4, saturation=1.1, hue_shift=.02, window_shape='ellipse', window_invert=True)
    mask, out = qualifier_mask(full, s), apply_qualifier(full, s)
    radius = 1+int(bool(smooth))
    for ys, xs in [(slice(0, 31, stride), slice(0, 43, stride)),
                   (slice(3, 25, stride), slice(7, 35, stride)),
                   (slice(0, 31, stride), slice(40, 43, stride))]:
        target = full[ys, xs]
        context = dict(canvas_size=(43, 31), origin=(xs.start, ys.start), stride=stride)
        assert_bits_equal(qualifier_mask(target, s, matte_source=full, **context), mask[ys, xs])
        assert_bits_equal(apply_qualifier(target, s, matte_source=full, **context), out[ys, xs])
        left, top = max(xs.start-radius, 0), max(ys.start-radius, 0)
        right = min(xs.start+(target.shape[1]-1)*stride+radius+1, 43)
        bottom = min(ys.start+(target.shape[0]-1)*stride+radius+1, 31)
        kwargs = dict(context, matte_source=full[top:bottom, left:right], matte_source_origin=(left, top))
        assert_bits_equal(qualifier_mask(target, s, **kwargs), mask[ys, xs])
        assert_bits_equal(apply_qualifier(target, s, **kwargs), out[ys, xs])


@pytest.mark.parametrize('op', [qualifier_mask, apply_qualifier])
def test_combined_requires_two_real_pixels_not_one_pixel_or_fake_sparse_neighbors(op):
    full = rgb_from_key(np.eye(13, dtype=np.float32))
    s = settings(matte_morphology=1, matte_smoothing=1, exposure=.5)
    target = full[4:9:2, 4:9:2]
    context = dict(canvas_size=(13, 13), origin=(4, 4), stride=2)
    with pytest.raises(ValueError, match='dense matte_source'):
        op(target, s, **context)
    with pytest.raises(ValueError, match='two-pixel halo'):
        op(target, s, matte_source=full[3:10, 3:10], matte_source_origin=(3, 3), **context)
    changed = full.copy(); changed[4, 4] = [0, 1, 0]
    with pytest.raises(ValueError, match='must match'):
        op(target, s, matte_source=changed, **context)
    full[-1, -1, 0] = np.nan
    with pytest.raises(ValueError, match='finite'):
        op(target, s, matte_source=full, **context)


@pytest.mark.parametrize('budget', [1, 25, 64, 127, 4096])
@pytest.mark.parametrize('stride', [1, 2, 5])
def test_tiles_are_bounded_and_seamless(monkeypatch, budget, stride):
    rng = np.random.default_rng(238)
    full = rng.random((19, 23, 3), dtype=np.float32)
    s = settings(matte_morphology=-.7, matte_smoothing=.8, exposure=.3)
    expected = scalar_mask(full, s)[::stride, ::stride]
    monkeypatch.setattr(qualifier, '_BLOCK_PIXELS', budget)
    real = qualifier._mask_block; seen = []
    def bounded(block, settings):
        seen.append(len(block)); return real(block, settings)
    monkeypatch.setattr(qualifier, '_mask_block', bounded)
    actual = qualifier_mask(full[::stride, ::stride], s, matte_source=full,
        canvas_size=(23, 19), stride=stride)
    assert_bits_equal(actual, expected)
    assert seen and max(seen) <= budget


@pytest.mark.parametrize('value', [True, False, None, '0', [], np.nan, np.inf, -np.inf, -1.01, 1.01])
def test_invalid_morphology_rejected_even_disabled(value):
    with pytest.raises(ValueError, match='matte_morphology'):
        QualifierSettings(matte_morphology=value)


@pytest.mark.parametrize('op', [qualifier_mask, apply_qualifier])
@pytest.mark.parametrize('shape', [(3,), (5, 3), (1, 2, 3, 3), (0, 3, 3)])
def test_zero_exact_pointwise_legacy_bypass_and_nonzero_spatial_validation(op, shape):
    rgb = np.zeros(shape, np.float32)
    s = settings(matte_morphology=0)
    actual = op(rgb, s, matte_source=object(), origin=None, canvas_size='ignored', stride=False)
    assert actual.shape == (rgb.shape[:-1] if op is qualifier_mask else rgb.shape)
    with pytest.raises(ValueError, match='two-dimensional'):
        op(rgb, replace(s, matte_morphology=.1))


@pytest.mark.parametrize('amount', [-1., -1e-40, -float(np.nextafter(0., 1.)),
                                  float(np.nextafter(0., 1.)), 1e-40, 1.])
def test_extreme_strengths_extended_rgb_readonly_and_signed_zero(amount):
    rgb = rgb_from_key(np.eye(5, dtype=np.float32))
    rgb[2, 2] = [-1, np.finfo(np.float32).max, -0.]
    rgb[4, 0] = [-0., 0., -0.]
    rgb = rgb.transpose(1, 0, 2); rgb.setflags(write=False)
    before = rgb.copy()
    s = settings(matte_morphology=amount, matte_smoothing=.7, exposure=.5)
    with np.errstate(all='raise'):
        mask, out = qualifier_mask(rgb, s), apply_qualifier(rgb, s)
    assert np.isfinite(out).all() and np.all((mask >= 0) & (mask <= 1))
    assert_bits_equal(out[2, 2], rgb[2, 2]); assert_bits_equal(out[0, 4], rgb[0, 4])
    assert_bits_equal(rgb, before)
    assert not np.shares_memory(rgb, out)


@pytest.mark.parametrize('op', [qualifier_mask, apply_qualifier])
def test_cancellation_during_spatial_neighborhood_preserves_source(monkeypatch, op):
    rgb = rgb_from_key(np.eye(20, dtype=np.float32)); before = rgb.copy()
    s = settings(matte_morphology=.8, matte_smoothing=.5, exposure=.5)
    marker = TimeoutError('bounded morphology timeout')
    seen = []
    def cancel():
        seen.append(1)
        if len(seen) == 6: raise marker
    with pytest.raises(TimeoutError) as caught:
        op(rgb, s, cancel=cancel)
    assert caught.value is marker and len(seen) == 6
    assert_bits_equal(rgb, before)


@pytest.mark.parametrize('enabled,neutral', [(False, False), (True, True)])
def test_bypassed_adjustment_validates_without_classifying(monkeypatch, enabled, neutral):
    rgb = np.ones((5, 7, 3), np.float32)
    s = settings(matte_morphology=1, matte_smoothing=1, exposure=0 if neutral else .5)
    s.enabled = enabled
    def unexpected(*args): pytest.fail('Bypassed correction classified a key')
    monkeypatch.setattr(qualifier, '_mask_block', unexpected)
    assert_bits_equal(apply_qualifier(rgb, s), rgb)
    rgb[0, 0, 0] = np.inf
    with pytest.raises(ValueError, match='finite'):
        apply_qualifier(rgb, s)


@pytest.mark.parametrize('amount', [-.7, .6])
def test_morphology_precedes_smoothing_and_fractional_levels_are_not_reordered(amount):
    key = np.random.default_rng(239).random((9, 11), dtype=np.float32)
    rgb = rgb_from_key(key)
    s = settings(matte_morphology=amount, matte_smoothing=.8, matte_black=.2, matte_white=.7)
    actual = qualifier_mask(rgb, s)
    expected = scalar_mask(rgb, s)
    assert_bits_equal(actual, expected)
    wrong = scalar_morphology(scalar_mask(rgb, replace(s, matte_morphology=0)), amount)
    assert np.max(abs(expected-wrong)) > .07
    # Fractional morphology and levels do not commute, even though a full
    # minimum/maximum alone would commute with a monotone levels mapping.
    morphed_raw = scalar_morphology(key, amount)
    reordered_levels = np.clip((morphed_raw-.2)/.5, 0, 1)
    proper_levels = qualifier_mask(rgb, replace(s, matte_smoothing=0))
    assert np.max(abs(proper_levels-reordered_levels)) > .08


def test_sparse_support_allocation_is_bounded_not_a_canvas_bounding_box(monkeypatch):
    full = rgb_from_key(np.eye(101, 1001, dtype=np.float32))
    s = settings(matte_morphology=.5, matte_smoothing=.5)
    real = qualifier._raw_image_mask; allocations = []
    def observed(rgb, *args):
        allocations.append(rgb.shape[0]*rgb.shape[1])
        return real(rgb, *args)
    monkeypatch.setattr(qualifier, '_raw_image_mask', observed)
    target = full[::97, ::97]
    qualifier_mask(target, s, matte_source=full, canvas_size=(1001, 101), stride=97)
    # 2 x 11 sparse target centers, each with a 5 x 5 support; no 98 x 972 box.
    assert allocations == [target.shape[0]*target.shape[1]*25]


def test_in_place_morphology_validation_and_round_trip():
    s = settings(matte_morphology=-.375)
    assert QualifierSettings.from_dict(s.to_dict()).matte_morphology == -.375
    s.matte_morphology = np.nan
    for operation in (qualifier_mask, apply_qualifier):
        with pytest.raises(ValueError, match='matte_morphology'):
            operation(np.zeros((5, 7, 3), np.float32), s)

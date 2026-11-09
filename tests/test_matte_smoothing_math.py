"""Independent spatial-matte oracle and bounded-neighborhood API contract.

Expected masks classify each incoming pixel scalarly, compose inclusions and
exclusions, apply levels, convolve [1,2,1]/4 in two scalar stages, blend, reject
extended destinations, and only then evaluate the project-space window.
"""
from dataclasses import replace
import colorsys
import math

import numpy as np
import pytest

import lumen.qualifier as qualifier
from lumen.qualifier import QualifierRange, QualifierSettings, apply_qualifier, qualifier_mask


def _interval(value, lo, hi, feather):
    if lo <= value <= hi:
        return np.float32(1)
    distance = lo - value if value < lo else value - hi
    return np.float32(max(0., 1. - distance / feather)) if feather else np.float32(0)


def _range(pixel, s):
    lo, hi = min(pixel), max(pixel)
    chroma = np.float32(hi - lo)
    sat = np.float32(chroma / hi) if hi > 0 else np.float32(0)
    luma = np.float32(np.float32(pixel[0] * np.float32(.2126)
                                + pixel[1] * np.float32(.7152))
                       + pixel[2] * np.float32(.0722))
    weight = np.float32(_interval(float(sat), s.saturation_low, s.saturation_high,
                                 s.saturation_softness)
                        * _interval(float(luma), s.luma_low, s.luma_high, s.luma_softness))
    if s.hue_width < .5:
        if chroma <= np.float32(1e-6):
            return np.float32(0)
        hue = colorsys.rgb_to_hsv(*map(float, pixel))[0]
        d = abs(hue - s.hue_center % 1)
        weight = np.float32(weight * _interval(min(d, 1-d), 0, s.hue_width, s.hue_softness))
    return weight


def _key(pixel, s):
    if not s.enabled or any(v < 0 or v > 1 for v in pixel):
        return np.float32(0)
    include = max(_range(pixel, q) for q in [s, *s.inclusion_ranges])
    exclude = max([np.float32(0), *[_range(pixel, q) for q in s.exclusion_ranges]])
    weight = np.float32(include * np.float32(1-exclude))
    if s.matte_black == 0 and s.matte_white == 1:
        return weight
    if float(weight) <= s.matte_black:
        return np.float32(0)
    if float(weight) >= s.matte_white:
        return np.float32(1)
    return np.float32((float(weight)-s.matte_black) / (s.matte_white-s.matte_black))


def scalar_mask(rgb, s):
    h, w = rgb.shape[:2]
    raw = np.empty((h, w), np.float32)
    for y, x in np.ndindex(h, w):
        raw[y, x] = _key(rgb[y, x], s)
    out = np.empty_like(raw)
    for y, x in np.ndindex(h, w):
        horizontal = []
        for dy in (-1, 0, 1):
            row = min(max(y+dy, 0), h-1)
            left = raw[row, max(x-1, 0)]
            center = raw[row, x]
            right = raw[row, min(x+1, w-1)]
            horizontal.append(np.float32(np.float32(np.float32(left + np.float32(2)*center) + right)
                                          * np.float32(.25)))
        filtered = np.float32(np.float32(np.float32(horizontal[0] + np.float32(2)*horizontal[1])
                                         + horizontal[2]) * np.float32(.25))
        weight = (filtered if np.float32(s.matte_smoothing) == 1 else
                  np.float32(raw[y, x] + np.float32(s.matte_smoothing)
                             * np.float32(filtered - raw[y, x])))
        if any(v < 0 or v > 1 for v in rgb[y, x]):
            weight = np.float32(0)
        if s.window_shape != 'off':
            dx = ((x+.5)/w-s.window_center_x) * 2/s.window_width
            dy = ((y+.5)/h-s.window_center_y) * 2/s.window_height
            radius = math.hypot(dx, dy) if s.window_shape == 'ellipse' else max(abs(dx), abs(dy))
            window = _interval(radius, 0, 1, s.window_feather)
            if s.window_invert:
                window = np.float32(1-window)
            weight = np.float32(weight*window)
        out[y, x] = weight
    return out


def settings(**kw):
    return QualifierSettings(enabled=True, hue_width=.5, saturation_low=1,
        saturation_high=1, saturation_softness=1, **kw)


def rgb_from_key(key):
    # HSV saturation is exactly 1-gray for these bounded red-axis samples.
    return np.stack((np.ones_like(key), 1-key, 1-key), axis=-1).astype(np.float32)


def assert_bits_equal(actual, expected):
    np.testing.assert_array_equal(actual.view(np.uint32), expected.view(np.uint32))


@pytest.mark.parametrize('shape', [(1, 1), (1, 7), (9, 1), (7, 11), (19, 23)])
@pytest.mark.parametrize('amount', [0., .25, .5, 1.])
def test_impulses_steps_tiny_islands_and_canvas_edges(shape, amount):
    h, w = shape
    keys = []
    for y, x in [(0, 0), (h//2, w//2), (h-1, w-1)]:
        key = np.zeros(shape, np.float32)
        key[y, x] = 1
        keys.append(key)
    step = np.zeros(shape, np.float32)
    step[:, w//2:] = 1
    keys.extend([step, 1-step, np.ones(shape, np.float32), np.zeros(shape, np.float32)])
    for key in keys:
        rgb = rgb_from_key(key)
        s = settings(matte_smoothing=amount)
        assert_bits_equal(qualifier_mask(rgb, s), scalar_mask(rgb, s))


def test_exact_three_by_three_impulse_kernel_and_boundary_replication():
    key = np.zeros((5, 5), np.float32)
    key[2, 2] = 1
    actual = qualifier_mask(rgb_from_key(key), settings(matte_smoothing=1))
    np.testing.assert_array_equal(actual[1:4, 1:4],
                                  np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]])/16)
    assert actual.sum() == 1
    key[:] = 0
    key[0, 0] = 1
    actual = qualifier_mask(rgb_from_key(key), settings(matte_smoothing=1))
    np.testing.assert_array_equal(actual[:2, :2], np.array([[9, 3], [3, 1]])/16)


@pytest.mark.parametrize('window', ['off', 'ellipse', 'rectangle'])
@pytest.mark.parametrize('invert', [False, True])
def test_levels_composition_and_window_order_are_independent(window, invert):
    rng = np.random.default_rng(714)
    rgb = rng.uniform(-.05, 1.05, (13, 17, 3)).astype(np.float32)
    s = settings(matte_smoothing=.73, matte_black=.2, matte_white=.77,
        inclusion_ranges=[QualifierRange(hue_width=.5, saturation_low=.3,
            saturation_high=.5, saturation_softness=.15, luma_low=.2, luma_high=.7)],
        exclusion_ranges=[QualifierRange(hue_width=.5, luma_low=.4, luma_high=.6,
            luma_softness=.25, saturation_low=.2, saturation_high=.4)],
        window_shape=window, window_invert=invert, window_width=.64,
        window_height=.41, window_feather=.6)
    np.testing.assert_allclose(qualifier_mask(rgb, s), scalar_mask(rgb, s), rtol=0, atol=1.2e-7)
    # Actual ordering must differ from smoothing a window-gated mask.
    if window != 'off':
        no_window = qualifier_mask(rgb, replace(s, window_shape='off'))
        assert np.any(no_window != qualifier_mask(rgb, s))


@pytest.mark.parametrize('stride', [1, 2, 3, 5, 19])
@pytest.mark.parametrize('window', ['off', 'ellipse', 'rectangle'])
def test_full_source_and_cropped_halo_match_full_frame_exactly(stride, window):
    rng = np.random.default_rng(919)
    full = rng.random((37, 53, 3), dtype=np.float32)
    full[9, 10] = [-.01, 1.01, .3]
    s = settings(matte_smoothing=.63, matte_black=.13, matte_white=.81,
                 window_shape=window, exposure=.4, saturation=1.2, hue_shift=.07)
    expected_mask, expected_rgb = qualifier_mask(full, s), apply_qualifier(full, s)
    # Interior and all four canvas boundaries exercise cropped halo replication.
    for ys, xs in [(slice(3, 32, stride), slice(7, 49, stride)),
                   (slice(0, 37, stride), slice(0, 53, stride)),
                   (slice(0, 17, stride), slice(36, 53, stride))]:
        target = full[ys, xs]
        grid = dict(canvas_size=(53, 37), origin=(xs.start, ys.start), stride=stride)
        assert_bits_equal(qualifier_mask(target, s, matte_source=full, **grid), expected_mask[ys, xs])
        assert_bits_equal(apply_qualifier(target, s, matte_source=full, **grid), expected_rgb[ys, xs])
        last_y = ys.start+(target.shape[0]-1)*stride
        last_x = xs.start+(target.shape[1]-1)*stride
        left, top = max(0, xs.start-1), max(0, ys.start-1)
        halo = full[top:min(37, last_y+2), left:min(53, last_x+2)]
        kwargs = dict(grid, matte_source=halo, matte_source_origin=(left, top))
        assert_bits_equal(qualifier_mask(target, s, **kwargs), expected_mask[ys, xs])
        assert_bits_equal(apply_qualifier(target, s, **kwargs), expected_rgb[ys, xs])


@pytest.mark.parametrize('block_pixels', [1, 7, 25, 64, 127, 4096])
def test_no_cross_tile_or_processing_block_seams(monkeypatch, block_pixels):
    rng = np.random.default_rng(22)
    rgb = rng.random((31, 43, 3), dtype=np.float32)
    s = settings(matte_smoothing=.8, exposure=.25)
    expected = scalar_mask(rgb, s)
    baseline = apply_qualifier(rgb, s)
    monkeypatch.setattr(qualifier, '_BLOCK_PIXELS', block_pixels)
    observed = []
    real = qualifier._mask_block
    def bounded(block, settings):
        observed.append(len(block))
        return real(block, settings)
    monkeypatch.setattr(qualifier, '_mask_block', bounded)
    assert_bits_equal(qualifier_mask(rgb, s), expected)
    assert_bits_equal(apply_qualifier(rgb, s), baseline)
    assert max(observed) <= block_pixels


@pytest.mark.parametrize('operation', [qualifier_mask, apply_qualifier])
@pytest.mark.parametrize('stride', [1, 2, 4])
def test_missing_dense_context_rejected_instead_of_blurring_sparse_grid(operation, stride):
    rgb = np.ones((11, 13, 3), np.float32)
    target = rgb[2:9:stride, 3:12:stride]
    with pytest.raises(ValueError, match='dense matte_source'):
        operation(target, settings(matte_smoothing=.5),
                  canvas_size=(13, 11), origin=(3, 2), stride=stride)


@pytest.mark.parametrize('operation', [qualifier_mask, apply_qualifier])
def test_halo_and_incoming_pixel_identity_validation(operation):
    rgb = rgb_from_key(np.zeros((7, 9), np.float32))
    s = settings(matte_smoothing=.5)
    target = rgb[2:5, 3:7]
    grid = dict(canvas_size=(9, 7), origin=(3, 2))
    with pytest.raises(ValueError, match='one-pixel halo'):
        operation(target, s, matte_source=target, matte_source_origin=(3, 2), **grid)
    changed = rgb.copy()
    changed[3, 4] = [1, 0, 0]
    with pytest.raises(ValueError, match='must match'):
        operation(target, s, matte_source=changed, **grid)
    changed[:] = rgb
    changed[-1, -1, 1] = np.nan  # Outside sampled area: entire source is validated.
    with pytest.raises(ValueError, match='finite'):
        operation(target, s, matte_source=changed, **grid)


@pytest.mark.parametrize('value', [True, False, None, '0.5', [], np.nan, np.inf, -np.inf, -.01, 1.01])
def test_invalid_smoothing_setting_rejected_even_disabled(value):
    with pytest.raises(ValueError, match='matte_smoothing'):
        QualifierSettings(matte_smoothing=value)


@pytest.mark.parametrize('operation', [qualifier_mask, apply_qualifier])
@pytest.mark.parametrize('shape', [(3,), (5, 3), (1, 2, 3, 3), (0, 3, 3), (3, 0, 3)])
def test_smoothing_requires_nonempty_image_but_zero_retains_legacy_api(operation, shape):
    rgb = np.zeros(shape, np.float32)
    neutral = settings(matte_smoothing=0)
    # Deliberately invalid source/coordinate extras are ignored with no window.
    actual = operation(rgb, neutral, matte_source=object(), matte_source_origin=None,
                       canvas_size='ignored', origin=None, stride=False)
    assert actual.shape == (rgb.shape[:-1] if operation is qualifier_mask else rgb.shape)
    with pytest.raises(ValueError, match='two-dimensional'):
        operation(rgb, replace(neutral, matte_smoothing=.5))


@pytest.mark.parametrize('operation', [qualifier_mask, apply_qualifier])
@pytest.mark.parametrize('kwargs', [dict(stride=0), dict(stride=True), dict(stride=1.5),
    dict(origin=(-1, 0)), dict(origin=(0, False)), dict(canvas_size=(0, 3)),
    dict(canvas_size=(2, 3)), dict(matte_source_origin=(-1, 0)),
    dict(matte_source_origin=(0, False))])
def test_spatial_coordinate_validation(operation, kwargs):
    rgb = np.ones((3, 4, 3), np.float32)
    with pytest.raises(ValueError):
        operation(rgb, settings(matte_smoothing=.5), matte_source=rgb, **kwargs)


def test_extended_rgb_and_signed_zero_survive_smoothing_adjustments():
    rgb = np.ones((7, 7, 3), np.float32)
    rgb[2:5, 2:5] = [1, 0, 0]
    rgb[3, 3] = [-1, np.finfo(np.float32).max, -0.]
    rgb[0, 0] = [-0., 0., -0.]
    s = settings(matte_smoothing=1, exposure=1, saturation=2, hue_shift=.1)
    with np.errstate(over='raise', invalid='raise', divide='raise'):
        mask = qualifier_mask(rgb, s)
        out = apply_qualifier(rgb, s)
    assert mask[3, 3] == 0
    assert_bits_equal(out[3, 3], rgb[3, 3])
    assert_bits_equal(out[0, 0], rgb[0, 0])
    assert np.isfinite(out).all()


def test_noncontiguous_readonly_source_and_input_are_never_mutated():
    rng = np.random.default_rng(1)
    backing = rng.random((30, 40, 3), dtype=np.float32)
    rgb = backing[::2, ::2].transpose(1, 0, 2)
    before = backing.copy()
    rgb.setflags(write=False)
    s = settings(matte_smoothing=.75, exposure=.5)
    actual_mask = qualifier_mask(rgb, s)
    actual_rgb = apply_qualifier(rgb, s)
    assert_bits_equal(actual_mask, scalar_mask(rgb, s))
    assert not np.shares_memory(rgb, actual_mask)
    assert not np.shares_memory(rgb, actual_rgb)
    assert_bits_equal(backing, before)


@pytest.mark.parametrize('operation', [qualifier_mask, apply_qualifier])
@pytest.mark.parametrize('amount', [0., .5])
def test_cancellation_exceptions_propagate_at_bounded_checkpoints(monkeypatch, operation, amount):
    monkeypatch.setattr(qualifier, '_BLOCK_PIXELS', 25)
    rgb = np.ones((31, 41, 3), np.float32)
    calls = []
    marker = RuntimeError('cancel this exact operation')
    def cancel():
        calls.append(1)
        if len(calls) == 4:
            raise marker
    with pytest.raises(RuntimeError) as caught:
        operation(rgb, settings(matte_smoothing=amount), cancel=cancel)
    assert caught.value is marker
    assert len(calls) == 4


def test_settings_round_trip_and_in_place_validation():
    s = settings(matte_smoothing=.37)
    assert QualifierSettings.from_dict(s.to_dict()).matte_smoothing == .37
    s.matte_smoothing = np.nan
    for operation in (qualifier_mask, apply_qualifier):
        with pytest.raises(ValueError, match='matte_smoothing'):
            operation(np.zeros((3, 4, 3), np.float32), s)


@pytest.mark.parametrize('amount', [float(np.nextafter(0., 1.)),
    float(np.nextafter(np.float32(0), np.float32(1))), 1e-40, 1e-20, 1.])
def test_extreme_accepted_smoothing_amounts_stay_finite(amount):
    rgb = rgb_from_key(np.array([[1, 0], [.5, .8]], np.float32))
    s = settings(matte_smoothing=amount, exposure=.5)
    with np.errstate(all='raise'):
        mask = qualifier_mask(rgb, s)
        out = apply_qualifier(rgb, s)
    assert np.isfinite(mask).all() and np.isfinite(out).all()
    assert np.all((mask >= 0) & (mask <= 1))


@pytest.mark.parametrize('enabled,neutral', [(False, False), (True, True)])
def test_disabled_and_neutral_apply_validate_but_skip_classification(monkeypatch, enabled, neutral):
    rgb = np.ones((7, 9, 3), np.float32)
    s = settings(matte_smoothing=.7, exposure=0 if neutral else .5)
    s.enabled = enabled
    def unexpected(*_args):
        pytest.fail('Bypassed adjustment classified a spatial color key')
    monkeypatch.setattr(qualifier, '_mask_block', unexpected)
    assert_bits_equal(apply_qualifier(rgb, s), rgb)
    bad = rgb.copy()
    bad[2, 3] = np.inf
    with pytest.raises(ValueError, match='finite'):
        apply_qualifier(bad, s)


def test_maximum_range_sets_match_independent_hue_saturation_luma_oracle():
    rng = np.random.default_rng(22)
    rgb = rng.random((19, 27, 3), dtype=np.float32)
    include = [QualifierRange(hue_center=i/8, hue_width=.04, hue_softness=.13,
        saturation_low=.3, saturation_high=.8, saturation_softness=.2,
        luma_low=.15, luma_high=.7, luma_softness=.1) for i in range(8)]
    exclude = [QualifierRange(hue_center=(i+.5)/8, hue_width=.02, hue_softness=.07,
        saturation_low=.65, saturation_high=.85, saturation_softness=.1,
        luma_low=.35, luma_high=.55, luma_softness=.08) for i in range(8)]
    s = QualifierSettings(enabled=True, hue_center=.13, hue_width=.11,
        inclusion_ranges=include, exclusion_ranges=exclude, matte_black=.17,
        matte_white=.78, matte_smoothing=.61)
    np.testing.assert_allclose(qualifier_mask(rgb, s), scalar_mask(rgb, s), rtol=0, atol=8e-7)


@pytest.mark.parametrize('amount', [.25, .63, 1.])
def test_exposure_blends_the_smoothed_incoming_key_without_clipping(amount):
    rgb = rgb_from_key(np.array([[0, .25, 1], [.7, 1, .2], [1, .5, 0]], np.float32))
    s = settings(matte_smoothing=amount, exposure=.5)
    weights = scalar_mask(rgb, s)
    expected = rgb.copy()
    factor = np.float32(2**s.exposure)
    for y, x in np.ndindex(weights.shape):
        w = weights[y, x]
        if w == 0:
            continue
        for c in range(3):
            adjusted = np.float32(rgb[y, x, c] * factor)
            expected[y, x, c] = (adjusted if w == 1 or adjusted == rgb[y, x, c]
                else np.float32(np.float32(rgb[y, x, c] * np.float32(1-w))
                                + np.float32(adjusted*w)))
    assert_bits_equal(apply_qualifier(rgb, s), expected)
    assert expected.max() > 1


def test_disabled_smoothed_mask_is_zero_and_independent():
    rgb = np.ones((5, 9, 3), np.float32)
    s = QualifierSettings(enabled=False, matte_smoothing=1)
    actual = qualifier_mask(rgb, s)
    assert_bits_equal(actual, np.zeros((5, 9), np.float32))
    assert not np.shares_memory(actual, rgb)

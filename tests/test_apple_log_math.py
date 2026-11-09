"""Independent scalar/primary references, not snapshots of the implementation."""
import itertools
import math

import numpy as np
import pytest

from lumen.apple_log import (
    BT2020_TO_BT709, BT709_LUMA, apple_log_to_linear,
    apple_log_to_rec709, linear_to_apple_log, _render_sdr_linear,
)


def scalar_decode(p):
    if p >= 47.28711236 * (0.01 + 0.05641088) ** 2:
        return 2 ** ((p - 0.69336945) / 0.08550479) - 0.00964052
    return math.sqrt(p / 47.28711236) - 0.05641088 if p >= 0 else -0.05641088


def scalar_oetf(v):
    return 4.5 * v if v < 0.018 else 1.099 * v ** 0.45 - 0.099


@pytest.mark.parametrize('code', [-3., -0.001, 0., .01, .15, .20855531595464202,
                                  .3, .5, .6945529830551911, 1., 1.25, 2.])
def test_decode_against_independent_scalar(code):
    assert float(apple_log_to_linear(code)) == pytest.approx(scalar_decode(code), rel=2e-14, abs=1e-15)


@pytest.mark.parametrize('linear,code', [(0., .15047645230091253), (.01, .20855531870307897),
    (.18, .4882724585268676), (1., .6945529830551911), (4., .8646759795175297),
    (16., 1.0354629144211909)])
def test_known_scalar_anchors(linear, code):
    assert float(linear_to_apple_log(linear)) == pytest.approx(code, abs=1e-14)
    assert float(apple_log_to_linear(code)) == pytest.approx(linear, abs=1e-14)


def test_roundtrip_and_branch_limits():
    values = np.r_[np.linspace(-.05641088, .01, 1001), np.geomspace(.01, 10000, 1000)]
    np.testing.assert_allclose(apple_log_to_linear(linear_to_apple_log(values)), values, atol=1e-9, rtol=2e-14)
    pt = 47.28711236 * (.01 + .05641088) ** 2
    near = apple_log_to_linear([np.nextafter(pt, -np.inf), pt, np.nextafter(pt, np.inf)])
    np.testing.assert_allclose(near, .01, atol=1e-9, rtol=0)
    assert float(linear_to_apple_log(-1.)) == 0.
    assert float(apple_log_to_linear(-1.)) == -.05641088


def test_matrix_independent_numeric_coefficients():
    # Independently evaluated normalized-primary XYZ equations, rounded to 12 dp.
    expected = np.array([[1.660491002108, -.587641138789, -.072849863320],
                         [-.124550474522, 1.132899897126, -.008349422604],
                         [-.018150763355, -.100578898008, 1.118729661363]])
    np.testing.assert_allclose(BT2020_TO_BT709, expected, atol=5e-13, rtol=0)
    np.testing.assert_allclose(BT2020_TO_BT709 @ np.ones(3), 1., atol=1e-14)
    # Independent XYZ-Y coefficients for BT.2020, not Rec.709 weights reused.
    np.testing.assert_allclose(BT709_LUMA @ BT2020_TO_BT709,
        [.2627002120112671, .6779980715188708, .059301716469862], atol=1e-14)


@pytest.mark.parametrize('linear', [0., .001, .01, .017999, .018001, .1, .18, .180001, .5, 1., 4., 16.])
def test_neutral_expected_shoulder_and_oetf(linear):
    code = linear_to_apple_log([linear] * 3)
    mapped = linear if linear <= .18 else .18 + .82 * (1 - math.exp(-(linear - .18) / .82))
    np.testing.assert_allclose(apple_log_to_rec709(code), scalar_oetf(mapped), atol=5e-7, rtol=0)


def test_shoulder_continuity_midgray_slope_and_highlight_separation():
    values = np.array([.18 - 1e-6, .18, .18 + 1e-6, .5, 1., 2., 4.])
    rendered = _render_sdr_linear(np.repeat(values[:, None], 3, axis=1))[:, 0]
    np.testing.assert_allclose(np.diff(rendered[:3]) / 1e-6, 1., atol=1e-6)
    assert np.all(np.diff(rendered) > 0)
    assert rendered[-1] < 1.
    out = apple_log_to_rec709(np.repeat(np.array([.8, .9, 1., 1.05])[:, None], 3, axis=1))[:, 0]
    assert np.all(np.diff(out) > 0), 'Do not clip Log input or linear highlights at one'


def test_gamut_compression_luminance_and_neutral_axis_not_channel_clip():
    linear2020 = np.array(list(itertools.product([-.05, 0., .02, .2, 1., 4.], repeat=3)))
    linear709 = linear2020 @ BT2020_TO_BT709.T
    output = _render_sdr_linear(linear709)
    luminance = linear709 @ BT709_LUMA
    expected = np.array([0. if y <= 0 else y if y <= .18 else .18 + .82 * (1-math.exp(-(y-.18)/.82)) for y in luminance])
    np.testing.assert_allclose(output @ BT709_LUMA, expected, atol=1e-14, rtol=0)
    assert np.min(output) >= 0 and np.max(output) <= 1
    assert np.all(output[luminance <= 0] == 0)
    positive = luminance > 0
    before_delta = linear709[positive] * (expected[positive] / luminance[positive])[:, None] - expected[positive, None]
    after_delta = output[positive] - expected[positive, None]
    np.testing.assert_allclose(np.cross(before_delta, after_delta), 0., atol=1e-14)
    saturated_red = np.array([[.2, 0., 0.]]) @ BT2020_TO_BT709.T
    assert not np.allclose(_render_sdr_linear(saturated_red), np.clip(saturated_red, 0, 1))


def test_finite_extremes_shape_dtype_immutability_and_empty():
    maximum = np.finfo(np.float64).max
    codes = np.array(list(itertools.product([-maximum, -1., 0., 1., 2., maximum], repeat=3)))
    snapshot = codes.copy()
    with np.errstate(over='raise', invalid='raise', divide='raise'):
        out = apple_log_to_rec709(codes)
        decoded = apple_log_to_linear(codes)
        encoded = linear_to_apple_log(codes)
    assert np.all(np.isfinite(out)) and np.all(np.isfinite(decoded)) and np.all(np.isfinite(encoded))
    assert out.dtype == np.float32 and out.shape == codes.shape
    assert np.min(out) >= 0 and np.max(out) <= 1
    np.testing.assert_array_equal(codes, snapshot)
    assert apple_log_to_rec709(np.empty((0, 2, 3))).shape == (0, 2, 3)
    assert apple_log_to_rec709(np.zeros((2, 3, 4, 3))).shape == (2, 3, 4, 3)


@pytest.mark.parametrize('bad', [np.nan, np.inf, -np.inf, 1j])
@pytest.mark.parametrize('function', [apple_log_to_linear, linear_to_apple_log, apple_log_to_rec709])
def test_invalid_values_rejected(function, bad):
    with pytest.raises(ValueError):
        function([0., bad, 0.])


@pytest.mark.parametrize('bad', [0., [], [0., 0.], np.zeros((2, 4))])
def test_invalid_channel_shape(bad):
    with pytest.raises(ValueError):
        apple_log_to_rec709(bad)


def test_matrix_against_exact_rational_primary_derivation():
    # Independent scalar Cramer's rule with rational arithmetic, no NumPy
    # solver, no implementation's XYZ helper, and no fitted output samples.
    from fractions import Fraction as F

    def cross(a, b):
        return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]

    def dot(a, b):
        return sum(x*y for x, y in zip(a, b))

    def solve_columns(columns, target):
        a, b, c = columns
        determinant = dot(a, cross(b, c))
        return [dot(target, cross(b, c))/determinant,
                dot(a, cross(target, c))/determinant,
                dot(a, cross(b, target))/determinant]

    def xyz(x, y):
        return [x/y, F(1), (1-x-y)/y]

    white = xyz(F(3127, 10000), F(329, 1000))

    def primary_columns(pairs):
        columns = [xyz(F(x, 1000), F(y, 1000)) for x, y in pairs]
        factors = solve_columns(columns, white)
        return [[v*s for v in column] for column, s in zip(columns, factors)]

    source = primary_columns([(708, 292), (170, 797), (131, 46)])
    destination = primary_columns([(640, 330), (300, 600), (150, 60)])
    columns = [solve_columns(destination, primary) for primary in source]
    exact = [[float(columns[col][row]) for col in range(3)] for row in range(3)]
    np.testing.assert_allclose(BT2020_TO_BT709, exact, rtol=0, atol=1e-15)

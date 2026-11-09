"""Bit-pattern parity with the frozen evaluator, not timing-sensitive CI tests."""
from itertools import product
from copy import deepcopy
import math

import numpy as np
import pytest

from lumen import color, selective_color
from lumen.model import Grade
from lumen.qualifier import QualifierSettings
import selective_color_v20_fixture as legacy
from test_selective_color import HUE, SAT, HUE_SHAPE, SAT_SHAPE, constant, reference


SPARSE_HUE = [[0, 0], [.4, 0], [.6, .2], [.8, 0], [1, 0]]
SPARSE_SAT = [[0, 1], [.4, 1], [.6, .2], [.8, 1], [1, 1]]
CURVES = [
    pytest.param(HUE, SAT, id='neutral'),
    pytest.param(HUE_SHAPE, SAT, id='hue-only'),
    pytest.param(HUE, SAT_SHAPE, id='saturation-only'),
    pytest.param(HUE_SHAPE, SAT_SHAPE, id='both-shaped'),
    pytest.param(constant(.173), constant(.7), id='dense-selection'),
    pytest.param(constant(-.5), constant(0), id='all-gray'),
    pytest.param(constant(.5), constant(2), id='maximum-controls'),
    pytest.param(SPARSE_HUE, SPARSE_SAT, id='sparse-selection'),
    pytest.param([[0, 0], [.3, 0], [1, 0]],
                 [[0, 1], [.4, 1], [1, 1]], id='neutral-extra-points'),
]


def assert_bits_equal(actual, expected):
    np.testing.assert_array_equal(actual.view(np.uint32), expected.view(np.uint32))


def assert_parity(rgb, hue, saturation):
    original = rgb.copy()
    expected = legacy.apply_selective_color(rgb, hue, saturation)
    actual = selective_color.apply_selective_color(rgb, hue, saturation)
    assert actual.dtype == np.float32
    assert actual.shape == rgb.shape
    assert actual.flags.c_contiguous
    assert not np.shares_memory(actual, rgb)
    assert_bits_equal(actual, expected)
    assert_bits_equal(rgb, original)
    return actual


@pytest.mark.parametrize('seed', [17, 20261023])
@pytest.mark.parametrize('hue,saturation', CURVES)
def test_random_signed_working_values_are_bit_exact(seed, hue, saturation):
    rgb = np.random.default_rng(seed).uniform(-3, 4, (4097, 3)).astype(np.float32)
    actual = assert_parity(rgb, hue, saturation)
    # A separate double-precision colorsys oracle protects against freezing a
    # numerically consistent but conceptually incorrect implementation.
    np.testing.assert_allclose(actual[:97], reference(rgb[:97], hue, saturation),
                               atol=8e-6, rtol=3e-6)


@pytest.mark.parametrize('hue,saturation', CURVES)
def test_signed_zero_subnormal_and_tiny_products_are_bit_exact(hue, saturation):
    tiny = np.nextafter(np.float32(0), np.float32(1))
    normal = np.finfo(np.float32).tiny
    values = np.array([0., -0., tiny, -tiny, 2 * tiny, -2 * tiny,
                       normal, -normal, .25, -.25], np.float32)
    rgb = np.asarray(list(product(values, repeat=3)), np.float32)
    assert_parity(rgb, hue, saturation)


def test_saturation_zero_preserves_positive_zero_reduction_accumulator():
    tiny = np.nextafter(np.float32(0), np.float32(1))
    # All weighted products underflow to negative zero. np.sum starts at +0,
    # whereas simply adding the three products would retain -0.
    rgb = np.array([[-tiny, -0., -tiny], [-0., -0., -tiny]], np.float32)
    actual = assert_parity(rgb, HUE, constant(0))
    assert_bits_equal(actual, np.zeros_like(rgb))


@pytest.mark.parametrize('layout', [
    'contiguous', 'fortran', 'strided', 'reverse-channels', 'transposed',
    'broadcast', 'readonly', 'one-pixel', 'empty', 'empty-middle',
])
def test_shapes_strides_and_readonly_inputs_are_bit_exact(layout):
    base = np.random.default_rng(51).uniform(-1, 2, (5, 9, 7, 3)).astype(np.float32)
    arrays = {
        'contiguous': base,
        'fortran': np.asfortranarray(base),
        'strided': base[::-2, ::2, ::-2],
        'reverse-channels': base[..., ::-1],
        'transposed': base.transpose(2, 0, 1, 3),
        'broadcast': np.broadcast_to(base[:1, :1, :1], (3, 4, 5, 3)),
        'readonly': base.view(),
        'one-pixel': base[0, 0, 0],
        'empty': np.empty((0, 3), np.float32),
        'empty-middle': np.empty((2, 0, 4, 3), np.float32),
    }
    arrays['readonly'].setflags(write=False)
    assert_parity(arrays[layout], HUE_SHAPE, SAT_SHAPE)


@pytest.mark.parametrize('block_pixels', [1, 2, 7, 64])
def test_forced_block_boundaries_do_not_change_bits(monkeypatch, block_pixels):
    rgb = np.random.default_rng(82).uniform(-1, 2, (257, 3)).astype(np.float32)
    # Leave the frozen reference at its original block size to verify that
    # partitioning itself does not affect a pixel's arithmetic.
    monkeypatch.setattr(selective_color, '_BLOCK_PIXELS', block_pixels)
    assert_parity(rgb, HUE_SHAPE, SAT_SHAPE)


@pytest.mark.parametrize('count', [65535, 65536, 65537, 131089])
def test_production_block_boundaries_are_bit_exact(count):
    rgb = np.random.default_rng(901).uniform(-.25, 1.5, (count, 3)).astype(np.float32)
    assert_parity(rgb, SPARSE_HUE, SPARSE_SAT)


@pytest.mark.parametrize('hue,saturation', [
    (HUE, SAT), (constant(.2), SAT), (HUE, constant(0)),
    (HUE, constant(2)), (constant(-.5), constant(.7)),
])
def test_extreme_finite_pixels_match_bits_or_the_same_error(hue, saturation):
    limit = np.finfo(np.float32).max
    samples = [
        [limit, limit, limit], [-limit, -limit, -limit],
        [-limit, limit, 0], [limit, -limit, -0.],
        [limit * .9, 0, 0], [0, limit * .9, 0], [0, 0, limit * .9],
        [-limit * .9, 0, 0], [0, -limit * .9, 0], [0, 0, -limit * .9],
        [limit * .5, limit * .25, 0], [-limit * .5, -limit * .25, -0.],
        [limit, limit, np.nextafter(limit, np.float32(0))],
    ]
    for sample in samples:
        rgb = np.asarray([sample], np.float32)
        original = rgb.copy()
        try:
            expected = legacy.apply_selective_color(rgb, hue, saturation)
        except ValueError as error:
            with pytest.raises(ValueError) as actual_error:
                selective_color.apply_selective_color(rgb, hue, saturation)
            assert str(actual_error.value) == str(error)
        else:
            actual = assert_parity(rgb, hue, saturation)
            assert_bits_equal(actual, expected)
        assert_bits_equal(rgb, original)


def test_wide_exponent_finite_samples_are_bit_exact():
    bits = np.random.default_rng(667).integers(0, 2**32, (8192, 3), dtype=np.uint32)
    rgb = bits.view(np.float32)
    rgb[~np.isfinite(rgb)] = 0
    # Retain the exponent spread but bound active intermediate arithmetic.
    limit = np.float32(np.finfo(np.float32).max / 8)
    rgb = np.clip(rgb, -limit, limit)
    assert_parity(rgb, HUE_SHAPE, SAT_SHAPE)


def test_late_block_failure_does_not_mutate_the_callers_frame(monkeypatch):
    limit = np.finfo(np.float32).max
    rgb = np.array([[.9, .2, .3], [.2, .8, .4], [-limit, limit, 0]], np.float32)
    original = rgb.copy()
    for evaluator in [legacy, selective_color]:
        monkeypatch.setattr(evaluator, '_BLOCK_PIXELS', 2)
        with pytest.raises(ValueError, match='non-finite'):
            evaluator.apply_selective_color(rgb, HUE_SHAPE, SAT_SHAPE)
        assert_bits_equal(rgb, original)


@pytest.mark.parametrize('kind', ['no-selection', 'one-pixel', 'all-pixels'])
def test_empty_sparse_and_dense_selection_paths_match(kind):
    rgb = np.tile(np.array([[.9, .2, .2]], np.float32), (513, 1))
    if kind == 'one-pixel':
        rgb[256] = [.1, .3, .9]
    elif kind == 'all-pixels':
        rgb[:] = [.1, .3, .9]
    assert_parity(rgb, SPARSE_HUE, SPARSE_SAT)


@pytest.mark.parametrize('points', [
    [[0, 0], [.5, .1], [math.nextafter(.5, 1), -.1], [1, 0]],
    [[0, .1], [math.nextafter(0, 1), -.1], [1, .1]],
    [[0, -.0], [.25, .5], [.75, -.5], [1, -.0]],
])
def test_curve_interpolation_keeps_float64_control_precision(points):
    hues = np.concatenate((np.linspace(0, 1, 8193, dtype=np.float32),
                           np.array([0., -0., .5, np.nextafter(np.float32(.5),
                                    np.float32(1))], np.float32)))
    controls = np.asarray(points, np.float64)
    assert_bits_equal(selective_color._evaluate_curve(hues, controls),
                      legacy._evaluate_curve(hues, controls))


@pytest.mark.parametrize('bad_rgb', [
    np.zeros((2, 3), np.float64), np.zeros((2, 4), np.float32),
    np.asarray(1, np.float32), np.array([[0, np.nan, 1]], np.float32),
    np.array([[0, np.inf, 1]], np.float32), [[1, 0, 0]],
])
def test_invalid_inputs_preserve_exception_contract(bad_rgb):
    with pytest.raises(ValueError) as expected:
        legacy.apply_selective_color(bad_rgb, HUE_SHAPE, SAT_SHAPE)
    with pytest.raises(ValueError) as actual:
        selective_color.apply_selective_color(bad_rgb, HUE_SHAPE, SAT_SHAPE)
    assert str(actual.value) == str(expected.value)


@pytest.mark.parametrize('setting', [
    'neutral', 'disabled', 'bypassed', 'white-balance', 'primaries',
    'rgb-curves', 'qualifier', 'lut-zero', 'lut-blend', 'full-stack',
])
def test_complete_grade_composition_is_bit_exact(monkeypatch, tmp_path, setting):
    cube = tmp_path / 'parity.cube'
    cube.write_text('LUT_3D_SIZE 2\n' + '\n'.join(
        f'{b * .9 + .03} {r * .8 + .1} {g * .9 - .1}'
        for b in (0, 1) for g in (0, 1) for r in (0, 1)))
    grade = Grade(hue_hue_curve=deepcopy(HUE_SHAPE),
                  hue_sat_curve=deepcopy(SAT_SHAPE))
    if setting == 'neutral':
        grade = Grade()
    if setting in ('white-balance', 'full-stack'):
        grade.white_balance_enabled = True
        grade.white_balance_gains = [1.1, .97, .91]
        grade.linear_exposure = .3
        grade.lift = [.01, -.02, .03]
        grade.gamma = [.95, 1.05, 1.1]
        grade.gain = [1.1, .9, 1.02]
    if setting in ('primaries', 'full-stack'):
        grade.exposure = -.2
        grade.contrast = 1.2
        grade.contrast_pivot = .4
        grade.saturation = .8
        grade.temperature = .12
        grade.tint = -.08
        grade.shadows = .1
        grade.highlights = -.15
    if setting in ('rgb-curves', 'full-stack'):
        grade.master_curve = [[0, .03], [.4, .35], [1, .95]]
        grade.red_curve = [[0, -.02], [.5, .6], [1, 1.1]]
        grade.green_curve = [[0, .01], [.3, .25], [1, .9]]
        grade.blue_curve = [[0, .02], [.6, .65], [1, 1.05]]
    if setting in ('qualifier', 'full-stack'):
        grade.qualifier = QualifierSettings(
            enabled=True, hue_center=.6, hue_width=.2, hue_softness=.15,
            saturation_low=.05, saturation_high=.95, saturation_softness=.1,
            luma_low=.1, luma_high=.9, luma_softness=.1,
            exposure=.2, saturation=.75, hue_shift=.03)
    if setting in ('lut-zero', 'lut-blend', 'full-stack'):
        grade.lut_path = str(cube)
        grade.lut_strength = {'lut-zero': 0, 'lut-blend': .35, 'full-stack': 1}[setting]
    if setting in ('disabled', 'full-stack'):
        grade.input_encoding = 'srgb'
    if setting == 'disabled':
        grade.enabled = False
    if setting == 'bypassed':
        grade.input_encoding = 'linear_rec709'
    bypass = setting == 'bypassed'
    rgb = np.random.default_rng(321).uniform(-.2, 1.2, (17, 19, 3)).astype(np.float32)[::-1]
    rgb[0, 0] = [-0., 0., -0.]
    original_rgb, original_grade = rgb.copy(), deepcopy(grade)
    calls = {'legacy': 0, 'optimized': 0}

    def legacy_stage(*args):
        calls['legacy'] += 1
        return legacy.apply_selective_color(*args)

    def optimized_stage(*args):
        calls['optimized'] += 1
        return selective_color.apply_selective_color(*args)

    monkeypatch.setattr(color, 'apply_selective_color', legacy_stage)
    expected = color.apply_grade(rgb, grade, bypass=bypass)
    monkeypatch.setattr(color, 'apply_selective_color', optimized_stage)
    actual = color.apply_grade(rgb, grade, bypass=bypass)
    expected_calls = 0 if bypass or not grade.enabled else 1
    assert calls == {'legacy': expected_calls, 'optimized': expected_calls}
    assert actual.dtype == np.float32 and actual.shape == rgb.shape
    assert not np.shares_memory(actual, rgb)
    assert_bits_equal(actual, expected)
    assert_bits_equal(rgb, original_rgb)
    assert grade == original_grade
    if setting == 'neutral':
        assert_bits_equal(actual, rgb)
    elif setting in ('disabled', 'bypassed'):
        assert_bits_equal(actual, color.normalize_input(rgb, grade.input_encoding))

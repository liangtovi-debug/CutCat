"""Independent display-only references for the bounded selection overlay.

The overlay visualizes the incoming key, not the graded result. Magenta opacity
is half the key weight; display clipping must never change classification.
"""
from dataclasses import replace

import numpy as np
import pytest

from lumen.qualifier import QualifierSettings, apply_qualifier, qualifier_mask
from lumen.qualifier_overlay import selection_overlay


def scalar_overlay(frame, matte):
    """Float64 scalar oracle, independent of the vectorized display helper."""
    result = np.empty(frame.shape, dtype=np.float64)
    for y in range(frame.shape[0]):
        for x in range(frame.shape[1]):
            opacity = float(matte[y, x]) / 2
            for channel, tint in enumerate((1., 0., 1.)):
                source = max(0., min(1., float(frame[y, x, channel])))
                result[y, x, channel] = source * (1 - opacity) + tint * opacity
    return result


def test_analytic_selected_excluded_and_fractional_weights():
    frame = np.array([[[.2, .4, .6], [.2, .4, .6], [.2, .4, .6],
                       [.2, .4, .6], [.2, .4, .6]]], dtype=np.float32)
    matte = np.array([[0, .25, .5, .75, 1]], dtype=np.float32)
    result = selection_overlay(frame, matte)
    expected = [[[.2, .4, .6], [.3, .35, .65], [.4, .3, .7],
                 [.5, .25, .75], [.6, .2, .8]]]
    np.testing.assert_allclose(result, expected, atol=6e-8, rtol=0)
    np.testing.assert_array_equal(result[0, 0], frame[0, 0])
    assert result.shape == frame.shape and result.dtype == np.float32


def test_black_gray_white_and_extended_source_clip_before_blending():
    limit = np.finfo(np.float32).max
    frame = np.array([[[0, 0, 0], [.5, .5, .5], [1, 1, 1]],
                      [[-limit, .25, limit], [-.4, 1.8, .6],
                       [limit, -limit, .5]]], dtype=np.float32)
    matte = np.array([[1, 1, 1], [0, .5, 1]], dtype=np.float32)
    with np.errstate(over='raise', invalid='raise', divide='raise'):
        result = selection_overlay(frame, matte)
    expected = [[[.5, 0, .5], [.75, .25, .75], [1, .5, 1]],
                [[0, .25, 1], [.25, .75, .7], [1, 0, .75]]]
    np.testing.assert_allclose(result, expected, atol=6e-8, rtol=0)
    assert np.isfinite(result).all() and result.min() >= 0 and result.max() <= 1


def test_random_fractional_matte_matches_independent_scalar_reference():
    rng = np.random.default_rng(926)
    frame = rng.uniform(-2, 3, (11, 13, 3)).astype(np.float32)
    matte = rng.uniform(0, 1, frame.shape[:2]).astype(np.float32)
    np.testing.assert_allclose(selection_overlay(frame, matte),
                               scalar_overlay(frame, matte), atol=1e-7, rtol=0)


def test_actual_three_feather_product_controls_opacity_not_binary_selection():
    # H=.125, S=.5. Each H/S/Y exterior feather contributes one half,
    # yielding key=.125 and magenta opacity=.0625, not .5 or .25.
    frame = np.array([[[.5, .4375, .25]]], dtype=np.float32)
    y = sum(float(value) * coefficient for value, coefficient in
            zip(frame[0, 0], (.2126, .7152, .0722)))
    settings = QualifierSettings(enabled=True, hue_center=0, hue_width=.1,
                                 hue_softness=.05, saturation_low=.625,
                                 saturation_softness=.25, luma_low=y + .125,
                                 luma_softness=.25)
    matte = qualifier_mask(frame, settings)
    np.testing.assert_allclose(matte, [[.125]], atol=3e-7, rtol=0)
    np.testing.assert_allclose(selection_overlay(frame, matte),
                               [[[.53125, .41015625, .296875]]],
                               atol=2e-7, rtol=0)


def test_disabled_correction_can_be_inspected_without_enabling_or_adjusting_it():
    frame = np.array([[[.5, 0, 0], [.5, .375, 0], [0, .5, 0],
                       [.4, .4, .4], [1.1, 0, 0]]], dtype=np.float32)
    settings = QualifierSettings(enabled=False, hue_width=.1,
                                 hue_softness=.05, exposure=1,
                                 saturation=1.5, hue_shift=1/6)
    before = settings.to_dict()
    np.testing.assert_array_equal(qualifier_mask(frame, settings), np.zeros((1, 5)))
    np.testing.assert_array_equal(apply_qualifier(frame, settings), frame)
    matte = qualifier_mask(frame, replace(settings, enabled=True))
    np.testing.assert_allclose(matte, [[1, .5, 0, 0, 0]], atol=3e-7, rtol=0)
    overlay = selection_overlay(frame, matte)
    np.testing.assert_allclose(overlay, [[[.75, 0, .5], [.625, .28125, .25],
                                          [0, .5, 0], [.4, .4, .4], [1, 0, 0]]],
                               atol=2e-7, rtol=0)
    # Clipping the last pixel BEFORE keying would incorrectly select it.
    assert qualifier_mask(np.clip(frame, 0, 1), replace(settings, enabled=True))[0, -1] == 1
    assert settings.to_dict() == before and not settings.enabled


def test_source_validity_excludes_padding_without_discarding_fractional_key():
    frame = np.array([[[.5, .375, 0], [.5, .375, 0], [0, 0, 0]]],
                     dtype=np.float32)
    valid = np.array([[True, False, False]])
    settings = QualifierSettings(enabled=True, hue_width=.1, hue_softness=.05)
    incoming_matte = qualifier_mask(frame, settings)
    before = incoming_matte.copy()
    matte = incoming_matte * valid
    np.testing.assert_allclose(matte, [[.5, 0, 0]], atol=3e-7, rtol=0)
    np.testing.assert_allclose(selection_overlay(frame, matte),
                               [[[.625, .28125, .25], [.5, .375, 0], [0, 0, 0]]],
                               atol=2e-7, rtol=0)
    np.testing.assert_array_equal(incoming_matte, before)


@pytest.mark.parametrize('strided', [False, True])
def test_read_only_inputs_are_unchanged_and_result_has_independent_storage(strided):
    rng = np.random.default_rng(37)
    frame = rng.uniform(-.2, 1.2, (7, 10, 3)).astype(np.float32)
    matte = rng.uniform(0, 1, (7, 10)).astype(np.float32)
    if strided:
        frame = frame[::-1, ::2, ::-1]
        matte = matte[::-1, ::2]
        assert not frame.flags.c_contiguous and not matte.flags.c_contiguous
    before_frame, before_matte = frame.copy(), matte.copy()
    frame.setflags(write=False)
    matte.setflags(write=False)
    result = selection_overlay(frame, matte)
    np.testing.assert_array_equal(frame, before_frame)
    np.testing.assert_array_equal(matte, before_matte)
    assert not frame.flags.writeable and not matte.flags.writeable
    assert not np.shares_memory(result, frame) and not np.shares_memory(result, matte)
    np.testing.assert_allclose(result, scalar_overlay(frame, matte), atol=1e-7, rtol=0)


@pytest.mark.parametrize('height,width', [(1, 1), (1, 512), (512, 1), (512, 512)])
def test_viewport_limits_are_inclusive_and_zero_key_is_exact(height, width):
    rng = np.random.default_rng(101)
    frame = rng.uniform(-1, 2, (height, width, 3)).astype(np.float32)
    matte = np.zeros((height, width), dtype=np.float32)
    result = selection_overlay(frame, matte)
    np.testing.assert_array_equal(result, np.clip(frame, 0, 1))
    assert result.dtype == np.float32


@pytest.mark.parametrize('shape', [(3,), (2, 3), (2, 2, 1), (2, 2, 4),
                                    (1, 2, 2, 3), (0, 2, 3), (2, 0, 3),
                                    (513, 1, 3), (1, 513, 3)])
def test_invalid_rgb_shape_or_unbounded_viewport_rejected(shape):
    frame = np.zeros(shape, dtype=np.float32)
    matte = np.zeros(shape[:-1], dtype=np.float32)
    with pytest.raises(ValueError):
        selection_overlay(frame, matte)


@pytest.mark.parametrize('shape', [(), (2,), (3, 2), (2, 3), (1, 2),
                                    (2, 1), (2, 2, 1), (1, 2, 2)])
def test_matte_must_match_both_rgb_dimensions_without_broadcasting(shape):
    frame = np.zeros((2, 2, 3), dtype=np.float32)
    with pytest.raises(ValueError):
        selection_overlay(frame, np.zeros(shape, dtype=np.float32))


@pytest.mark.parametrize('target', ['frame', 'matte'])
@pytest.mark.parametrize('dtype', [np.float64, np.float16, np.uint8, np.int32, np.bool_])
def test_numeric_input_dtypes_produce_float32_without_mutation(target, dtype):
    frame = np.array([[[0, 1, 0], [1, 0, 0]]], dtype=np.float32)
    matte = np.array([[0, 1]], dtype=np.float32)
    if target == 'frame':
        frame = frame.astype(dtype)
    else:
        matte = matte.astype(dtype)
    before_frame, before_matte = frame.copy(), matte.copy()
    result = selection_overlay(frame, matte)
    assert result.dtype == np.float32
    np.testing.assert_array_equal(result, [[[0, 1, 0], [1, 0, .5]]])
    np.testing.assert_array_equal(frame, before_frame)
    np.testing.assert_array_equal(matte, before_matte)


def test_wide_float_source_is_display_clipped_before_float32_conversion():
    limit = np.finfo(np.float64).max
    frame = np.array([[[-limit, limit, .25]]], dtype=np.float64)
    matte = np.array([[.5]], dtype=np.float64)
    with np.errstate(over='raise', invalid='raise'):
        result = selection_overlay(frame, matte)
    assert result.dtype == np.float32
    np.testing.assert_array_equal(result, [[[.25, .75, .4375]]])


@pytest.mark.parametrize('target', ['frame', 'matte'])
@pytest.mark.parametrize('value', [None, [], 'invalid', 0])
def test_non_array_inputs_rejected(target, value):
    frame = np.zeros((1, 1, 3), dtype=np.float32)
    matte = np.zeros((1, 1), dtype=np.float32)
    with pytest.raises(ValueError):
        selection_overlay(value if target == 'frame' else frame,
                          value if target == 'matte' else matte)


@pytest.mark.parametrize('target', ['frame', 'matte'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_values_rejected_even_when_zero_key_would_hide_them(target, value):
    frame = np.zeros((2, 2, 3), dtype=np.float32)
    matte = np.zeros((2, 2), dtype=np.float32)
    if target == 'frame':
        frame[-1, -1, -1] = value
    else:
        matte[-1, -1] = value
    with pytest.raises(ValueError):
        selection_overlay(frame, matte)


@pytest.mark.parametrize('value', [-1, -.001, 1.001, 2,
                                    np.nextafter(np.float32(0), np.float32(-1)),
                                    np.nextafter(np.float32(1), np.float32(2))])
def test_out_of_range_matte_rejected_without_silent_clipping(value):
    frame = np.zeros((1, 1, 3), dtype=np.float32)
    with pytest.raises(ValueError):
        selection_overlay(frame, np.array([[value]], dtype=np.float32))

"""Numerical quality, bounds and frame-local float restoration regressions."""
from __future__ import annotations

import threading
import tracemalloc

import numpy as np
import pytest

import lumen.restoration as restoration
from lumen.restoration import (
    MAX_DEBLOCK_CORRECTION, MAX_SHARPEN_CORRECTION,
    RestorationCancelled, apply_restoration,
)
from lumen.restoration_settings import RestorationSettings


def settings(**changes):
    values = dict(enabled=True, deblock=0., denoise=0., sharpen=0.)
    values.update(changes)
    return RestorationSettings(**values)


def rgb_plane(values, height=64):
    return np.broadcast_to(np.asarray(values, np.float32)[None, :, None],
                           (height, len(values), 3)).copy()


def rms(value):
    return float(np.sqrt(np.mean(np.square(value, dtype=np.float64))))


def test_disabled_and_zero_are_exact_including_signed_hdr_and_negative_zero():
    source = np.random.default_rng(14).uniform(-.7, 2.5, (83, 91, 3)).astype(np.float32)
    source[0, 0] = [-0., 0., 1e-8]
    for configuration in (settings(), settings(enabled=False, deblock=1, denoise=1, sharpen=1)):
        result = apply_restoration(source, configuration)
        assert result is source
        np.testing.assert_array_equal(result.view(np.uint32), source.view(np.uint32))


@pytest.mark.parametrize('shape', [(1, 1, 3), (1, 39, 3), (43, 1, 3), (67, 81, 3)])
def test_constant_color_is_exact_and_never_clipped(shape):
    source = np.broadcast_to(np.array([-.2, .4137, 1.4], np.float32), shape).copy()
    before = source.copy()
    output = apply_restoration(source, settings(deblock=1, denoise=1, sharpen=1))
    assert output.dtype == np.float32 and output.shape == source.shape
    np.testing.assert_array_equal(output, source)
    np.testing.assert_array_equal(source, before)
    assert output.min() < 0 and output.max() > 1


@pytest.mark.parametrize('source', [
    np.zeros((3, 4, 3), np.uint8), np.zeros((3, 4, 3), np.float64),
    np.zeros((0, 4, 3), np.float32), np.zeros((3, 0, 3), np.float32),
    np.zeros((3, 4), np.float32), np.zeros((3, 4, 4), np.float32),
    np.zeros((3, 4, 3, 1), np.float32), [[[0., 0., 0.]]],
])
def test_rejects_invalid_image_layout_and_dtype(source):
    with pytest.raises(ValueError, match='float32 HWC RGB'):
        apply_restoration(source, settings())


@pytest.mark.parametrize('sample', [np.nan, np.inf, -np.inf])
def test_nonfinite_is_rejected_even_when_disabled(sample):
    source = np.zeros((67, 5, 3), np.float32)
    source[-1, -1, -1] = sample
    with pytest.raises(ValueError, match='samples must be finite'):
        apply_restoration(source, settings(enabled=False))


def test_dimension_bound_checked_before_image_scratch_allocation():
    huge_view = np.broadcast_to(np.zeros((1, 1, 3), np.float32), (1, 16385, 3))
    with pytest.raises(ValueError, match='dimensions must not exceed'):
        apply_restoration(huge_view, settings())


def test_settings_revalidated_on_every_call():
    configuration = settings(enabled=False)
    configuration.denoise = float('nan')
    with pytest.raises(ValueError):
        apply_restoration(np.zeros((2, 3, 3), np.float32), configuration)
    with pytest.raises(ValueError):
        apply_restoration(np.zeros((2, 3, 3), np.float32), None)


def test_planar_ramp_interior_stays_linear_and_within_local_range():
    yy, xx = np.mgrid[:131, :137]
    base = (np.float32(-.25) + xx.astype(np.float32) * np.float32(.006)
            + yy.astype(np.float32) * np.float32(.003))
    source = np.stack((base, base + .23, base + .5), axis=-1)
    output = apply_restoration(source, settings(deblock=1, denoise=1, sharpen=1))
    np.testing.assert_allclose(output[2:-2, 2:-2], source[2:-2, 2:-2], atol=2e-7, rtol=0)
    assert output.min() >= source.min() and output.max() <= source.max()
    assert output.min() < 0 and output.max() > 1


def test_seeded_noise_reduced_without_color_bias_or_strong_edge_bleed():
    clean = np.full((128, 160, 3), .25, np.float32)
    clean[:, 80:] = [.75, .65, .55]
    noisy = clean + np.random.default_rng(121).normal(0, .018, clean.shape).astype(np.float32)
    output = apply_restoration(noisy, settings(denoise=.8))
    assert rms(output - clean) < rms(noisy - clean) * .68
    assert np.max(np.abs((output - clean).mean(axis=(0, 1)))) < .0007
    # The max-RGB range gate excludes both sides of this high-contrast edge.
    assert np.max(np.abs(output[:, 79:81].mean(axis=0) - clean[:, 79:81].mean(axis=0))) < .004
    assert rms(output[:, 76:84] - clean[:, 76:84]) < rms(noisy[:, 76:84] - clean[:, 76:84])


def test_denoise_protects_isoluminant_color_detail_and_high_contrast_checker():
    yy, xx = np.mgrid[:65, :93]
    checker = ((xx + yy) % 2).astype(bool)
    source = np.empty((65, 93, 3), np.float32)
    source[checker] = [.5, .2, .3]
    source[~checker] = [.2, .2891762, .3]  # Nearly equal Rec.709 luma, different RGB.
    output = apply_restoration(source, settings(deblock=1, denoise=1, sharpen=1))
    np.testing.assert_array_equal(output, source)


@pytest.mark.parametrize('boundaries', [[32], [24, 56]])
def test_deblock_never_softens_an_isolated_or_two_real_weak_edges(boundaries):
    source = np.full((72, 96, 3), .3, np.float32)
    for index in boundaries:
        source[:, index:] += np.float32(.02)
    np.testing.assert_array_equal(apply_restoration(source, settings(deblock=1)), source)


def test_deblock_protects_real_strong_grid_and_diagonal_edges():
    yy, xx = np.mgrid[:96, :128]
    source = np.repeat((.15 + .7 * ((xx // 8 + yy // 8) % 2))[..., None], 3, axis=2).astype(np.float32)
    np.testing.assert_array_equal(apply_restoration(source, settings(deblock=1)), source)
    diagonal = np.repeat(np.where(xx > yy, .7, .25)[..., None], 3, axis=2).astype(np.float32)
    np.testing.assert_array_equal(apply_restoration(diagonal, settings(deblock=1)), diagonal)


@pytest.mark.parametrize('period,phase,axis', [(8, 0, 1), (11, 3, 1), (13, 5, 0)])
def test_deblock_reduces_block_offset_error_without_grid_phase_assumption(period, phase, axis):
    clean = rgb_plane(np.linspace(.25, .65, 160, dtype=np.float32), height=96)
    artifact = (((np.arange(160) + phase) // period) % 2 * 2 - 1).astype(np.float32) * .012
    blocked = clean + artifact[None, :, None]
    if axis == 0:
        clean, blocked = clean.transpose(1, 0, 2), blocked.transpose(1, 0, 2)
    output = apply_restoration(blocked, settings(deblock=1))
    assert rms(output - clean) < rms(blocked - clean) * .95
    assert np.max(np.abs(output - blocked)) <= MAX_DEBLOCK_CORRECTION + 1e-7
    assert abs(float(np.mean(output, dtype=np.float64) - np.mean(blocked, dtype=np.float64))) < 1e-8
    # It is attenuation at a few boundary samples, not invented reconstruction.
    assert rms(output - clean) > 0


def test_sharpen_improves_soft_edge_slope_without_new_extrema_or_halos():
    x = np.arange(160, dtype=np.float32)
    source = rgb_plane(.2 + .6 * (1 + np.tanh((x - 80) / 2.5)) / 2)
    output = apply_restoration(source, settings(sharpen=1))
    assert np.max(np.diff(output[0, :, 0])) > np.max(np.diff(source[0, :, 0])) * 1.025
    assert np.max(np.abs(output - source)) <= MAX_SHARPEN_CORRECTION + 1e-7
    patch = np.pad(source, ((1, 1), (1, 1), (0, 0)), mode='edge')
    neighbors = [patch[dy:dy + 64, dx:dx + 160] for dy in range(3) for dx in range(3)]
    assert np.all(output >= np.minimum.reduce(neighbors))
    assert np.all(output <= np.maximum.reduce(neighbors))
    # An ideal hard edge is already at its local envelope and is not overshot.
    hard = rgb_plane(np.where(x < 80, -.2, 1.4))
    np.testing.assert_array_equal(apply_restoration(hard, settings(sharpen=1)), hard)


def test_realistic_synthetic_noise_block_gradient_and_detail_improve_conservatively():
    yy, xx = np.mgrid[:192, :256]
    base = .24 + .25 * xx / 255 + .08 * yy / 191
    base += .22 * ((xx > 150) & (yy > 35) & (yy < 145))
    clean = np.stack((base, base * .9 + .025, base * .75 + .08), axis=2).astype(np.float32)
    # Preserve a visible high-frequency texture patch as a counterweight to
    # the easy flat-field noise metric.
    detail = (yy > 145) & (xx < 120)
    clean[detail] += np.broadcast_to((.05 * np.sin(xx * 1.4))[..., None], clean.shape)[detail].astype(np.float32)
    rng = np.random.default_rng(603)
    offsets = rng.normal(0, .006, (12, 16, 1)).astype(np.float32)
    degraded = clean + np.repeat(np.repeat(offsets, 16, axis=0), 16, axis=1)
    degraded += rng.normal(0, .014, clean.shape).astype(np.float32)
    output = apply_restoration(degraded, settings(deblock=.6, denoise=.65, sharpen=.2))
    assert rms(output - clean) < rms(degraded - clean) * .8
    assert rms(output[detail] - clean[detail]) < rms(degraded[detail] - clean[detail])
    # Fine detail is weakened somewhat by a spatial denoiser, but not erased.
    contrast_before = np.std(clean[160:180, 10:110, 0] - base[160:180, 10:110])
    contrast_after = np.std(output[160:180, 10:110, 0] - base[160:180, 10:110])
    assert contrast_after > contrast_before * .75


def test_noncontiguous_readonly_input_is_supported_and_not_mutated():
    backing = np.random.default_rng(11).uniform(-.1, 1.2, (81, 107, 3)).astype(np.float32)
    source = backing[::-1, ::2]
    source.setflags(write=False)
    before = backing.copy()
    output = apply_restoration(source, settings(deblock=.4, denoise=.7, sharpen=.3))
    contiguous = apply_restoration(np.ascontiguousarray(source), settings(deblock=.4, denoise=.7, sharpen=.3))
    np.testing.assert_array_equal(output, contiguous)
    np.testing.assert_array_equal(backing, before)


def test_tiling_has_no_seams_or_tile_size_dependent_result(monkeypatch):
    source = rgb_plane(np.linspace(.1, .7, 173, dtype=np.float32), height=151)
    source += (((np.arange(173) // 9) % 2) * .018).astype(np.float32)[None, :, None]
    configuration = settings(deblock=1, denoise=.6, sharpen=.4)
    expected = apply_restoration(source, configuration)
    for tile_rows in (1, 7, 17, 128):
        monkeypatch.setattr(restoration, 'RESTORATION_TILE_ROWS', tile_rows)
        np.testing.assert_array_equal(apply_restoration(source, configuration), expected)


def test_frame_independent_deterministic_result_and_no_history():
    a = np.random.default_rng(12).normal(.4, .02, (75, 89, 3)).astype(np.float32)
    b = np.random.default_rng(13).normal(.7, .01, a.shape).astype(np.float32)
    config = settings(deblock=.6, denoise=.7, sharpen=.2)
    result = apply_restoration(a, config)
    apply_restoration(b, config)
    np.testing.assert_array_equal(apply_restoration(a, config), result)


def test_cancellation_event_callback_and_callback_exception_preserve_input():
    source = np.full((193, 71, 3), .4, np.float32)
    event = threading.Event()
    event.set()
    with pytest.raises(RestorationCancelled, match='cancelled'):
        apply_restoration(source, settings(enabled=False), cancel=event)
    calls = 0
    def cancel_during_processing():
        nonlocal calls
        calls += 1
        return calls == 7
    with pytest.raises(RestorationCancelled):
        apply_restoration(source, settings(denoise=1), cancel=cancel_during_processing)
    assert calls == 7  # Four validation strips, then three processing strips.
    np.testing.assert_array_equal(source, np.full_like(source, .4))
    def caller_error():
        raise LookupError('caller cancellation')
    with pytest.raises(LookupError, match='caller cancellation'):
        apply_restoration(source, settings(), cancel=caller_error)
    with pytest.raises(ValueError, match='event or callable'):
        apply_restoration(source, settings(), cancel=object())


def test_enormous_finite_samples_do_not_overflow_or_get_clipped():
    source = np.zeros((31, 43, 3), np.float32)
    source[:, :14] = -np.finfo(np.float32).max
    source[:, 14:28] = np.finfo(np.float32).max
    with np.errstate(all='raise'):
        output = apply_restoration(source, settings(deblock=1, denoise=1, sharpen=1))
    np.testing.assert_array_equal(output, source)


def test_scratch_memory_is_bounded_to_two_working_frames_and_row_tiles():
    source = np.random.default_rng(12).normal(.4, .02, (1025, 257, 3)).astype(np.float32)
    tracemalloc.start()
    try:
        apply_restoration(source, settings(deblock=1, denoise=1, sharpen=1))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # Include two working-frame buffers plus a generous fixed allowance for
    # the handful of 64-row arrays, line counts and Python/NumPy bookkeeping.
    tile = 66 * max(source.shape[:2]) * 3 * np.dtype(np.float32).itemsize
    assert peak < 2 * source.nbytes + 20 * tile + 1024 * 1024


def test_ambiguous_weak_real_patterns_are_changed_but_have_a_two_axis_bound():
    # A spatial-only deblock filter cannot know whether repeated weak steps
    # are a graphic or compression. This regression deliberately records the
    # limitation instead of describing the heuristic as perfect recognition.
    yy, xx = np.mgrid[:96, :128]
    stripes = rgb_plane(.4 + .02 * ((np.arange(128) // 8) % 2), height=96)
    stripe_result = apply_restoration(stripes, settings(deblock=1))
    assert np.max(np.abs(stripe_result - stripes)) > .004
    assert np.max(np.abs(stripe_result - stripes)) <= MAX_DEBLOCK_CORRECTION + 1e-7
    checker = np.repeat((.4 + .05 * ((xx // 8 + yy // 8) % 2))[..., None], 3, axis=2).astype(np.float32)
    checker_result = apply_restoration(checker, settings(deblock=1))
    assert np.max(np.abs(checker_result - checker)) > .01
    assert np.max(np.abs(checker_result - checker)) <= 2 * MAX_DEBLOCK_CORRECTION + 1e-7


def test_heavy_noise_and_maximum_sharpen_are_a_documented_counterexample():
    # Strengths are manual controls, not a promise of automatic quality
    # improvement. Range-gated denoise rejects large noise differences, and
    # sharpening can accentuate what remains. Neither implies reconstruction.
    noisy = np.random.default_rng(40).normal(.45, .1, (192, 256, 3)).astype(np.float32)
    output = apply_restoration(noisy, settings(deblock=1, denoise=1, sharpen=1))
    denoised = apply_restoration(noisy, settings(denoise=1))
    assert np.var(output) > np.var(noisy) * 1.05
    assert np.var(denoised) < np.var(noisy)
    sharpened = apply_restoration(noisy, settings(sharpen=1))
    assert np.max(np.abs(sharpened - noisy)) <= MAX_SHARPEN_CORRECTION + 1e-7


def test_strength_controls_change_effect_size_without_changing_constant_offsets():
    noisy = np.random.default_rng(8).normal(.45, .018, (81, 97, 3)).astype(np.float32)
    error = [rms(apply_restoration(noisy, settings(denoise=s)) - .45) for s in (.2, .6, 1.)]
    assert error[0] > error[1] > error[2]
    shifted = apply_restoration(noisy + np.float32(1.25), settings(denoise=.8))
    expected = apply_restoration(noisy, settings(denoise=.8)) + np.float32(1.25)
    np.testing.assert_allclose(shifted, expected, atol=3e-7, rtol=0)
    assert shifted.min() > 1

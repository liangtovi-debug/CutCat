"""Analytical motion convention, geometry, border, and precision regressions."""
import math

import numpy as np
import pytest

from lumen.stabilization_math import (
    MAX_MOTION_ROWS, build_stabilization_plan, parse_vidstab_debug,
    proxy_to_active_matrix, resample_float_rgb,
)
from lumen.stabilization_settings import StabilizationSettings


IDENTITY = np.eye(3)


def settings(**changes):
    values = dict(enabled=True, strength=1., smoothing=3, max_zoom=1.5, border='black')
    values.update(changes)
    return StabilizationSettings(**values)


def pose(angle=0., dx=0., dy=0., size=(101, 81)):
    c = np.array(size, dtype=float) / 2
    r = np.array([[math.cos(angle), -math.sin(angle)],
                  [math.sin(angle), math.cos(angle)]])
    m = np.eye(3)
    m[:2, :2] = r
    m[:2, 2] = c + (dx, dy) - r @ c
    return m


def rows_from_cameras(cameras, size=(101, 81)):
    """Independent derivation: H_i = C_(i-1) inv(C_i)."""
    c = np.array(size, dtype=float) / 2
    rows = np.zeros((len(cameras), 6))
    for i in range(1, len(cameras)):
        h = cameras[i - 1] @ np.linalg.inv(cameras[i])
        rows[i, 1:3] = h[:2, :2] @ c + h[:2, 2] - c
        rows[i, 3] = -math.atan2(h[1, 0], h[0, 0])
    return rows


def make_plan(rows, config=None, size=(101, 81), **kw):
    return build_stabilization_plan(rows, config or settings(), analysis_size=size, active_size=size, **kw)


def transformed_corners(matrix, size):
    w, h = size
    corners = np.array([[0, 0, 1], [w - 1, 0, 1], [0, h - 1, 1], [w - 1, h - 1, 1]])
    return (corners @ matrix.T)[:, :2]


def test_parse_actual_debug_shape_comments_zero_time_and_ignore_zoom():
    raw = parse_vidstab_debug('0 0 0 0 0 1\n# no fields\n0 -5 2 .01 -99.25 0\n# comment\n\n', expected_frames=2)
    np.testing.assert_array_equal(raw, [[0, 0, 0, 0, 0, 1], [0, -5, 2, .01, -99.25, 0]])
    assert raw.dtype == np.float64
    rows = np.zeros((9, 6))
    rows[:, 4] = np.arange(9) * 1000
    np.testing.assert_array_equal(make_plan(rows).inverse_matrices, np.repeat(IDENTITY[None], 9, axis=0))


@pytest.mark.parametrize('text', [
    '', '# comments only', '1 0 0 0 0 0', '0 0 0 0 0', '0 0 0 0 0 0 7',
    '0 0 0 NaN 0 0', '0 0 0 0 inf 0', '0 0 0 0 0 .5',
    '0 0 0 0 0 nope', 'VID.STAB 1\nFrame 1 (List 0 [])',
    '0 0 0 0 0 0 # inline data is not supported',
])
def test_parse_rejects_ambiguous_malformed_and_nonfinite_rows(text):
    with pytest.raises(ValueError):
        parse_vidstab_debug(text)


def test_parse_bounds_count_line_length_comments_and_iterator():
    valid = '0 0 0 0 0 0\n'
    assert len(parse_vidstab_debug(iter([valid] * 3), max_rows=3, expected_frames=3)) == 3
    for arguments in ({'max_rows': 2}, {'expected_frames': 2}, {'max_rows': MAX_MOTION_ROWS + 1}, {'max_rows': True}):
        with pytest.raises(ValueError):
            parse_vidstab_debug(valid * 3, **arguments)
    with pytest.raises(ValueError):
        parse_vidstab_debug('0 ' * 1000)
    with pytest.raises(ValueError):
        parse_vidstab_debug(iter(['#\n'] * 1030), max_rows=1)


def test_disabled_zero_strength_and_neutral_are_exact_no_crop():
    raw = np.zeros((15, 6))
    raw[1::2, 1] = 20
    for config in (settings(enabled=False, border='crop', max_zoom=1.),
                   settings(strength=0., border='crop', max_zoom=1.)):
        plan = make_plan(raw, config)
        np.testing.assert_array_equal(plan.inverse_matrices, np.repeat(IDENTITY[None], len(raw), axis=0))
        np.testing.assert_array_equal(plan.zooms, np.ones(len(raw)))
    plan = make_plan(np.zeros((11, 6)), settings(border='crop', max_zoom=1.))
    np.testing.assert_array_equal(plan.inverse_matrices, np.repeat(IDENTITY[None], 11, axis=0))
    assert np.all(plan.zooms == 1.)


def test_combined_rotation_translation_matches_smoothed_camera_not_wrong_order():
    n, radius = 121, 7
    t = np.arange(n)
    angles = .008 * t + .035 * np.sin(t * 1.7)
    x = .15 * t + 5. * np.sin(t * 1.3)
    y = -.08 * t + 4. * np.sin(t * 1.9)
    poses = np.column_stack((angles, x, y))
    cameras = np.array([pose(*p) for p in poses])
    raw = rows_from_cameras(cameras)
    plan = make_plan(raw, settings(smoothing=radius))
    corrected = np.linalg.inv(plan.inverse_matrices) @ cameras
    for i in range(radius, n - radius):
        mean = poses[i - radius:i + radius + 1].mean(axis=0)
        np.testing.assert_allclose(corrected[i], pose(*mean), atol=2e-11)
    # A tracked world point should lose its high-frequency camera jitter.
    point = np.array([70., 30., 1.])
    before = cameras @ point
    after = corrected @ point
    before_jitter = np.std(np.diff(before[radius:-radius, :2], axis=0), axis=0)
    after_jitter = np.std(np.diff(after[radius:-radius, :2], axis=0), axis=0)
    assert np.all(after_jitter < before_jitter * .1)
    assert before_jitter.min() > 3.


def test_strength_is_rigid_pose_blend_and_tapers_at_segment_edges():
    poses = np.array([[.04 * math.sin(i), 3. * math.sin(1.2 * i), 2. * math.sin(1.8 * i)] for i in range(35)])
    cameras = np.array([pose(*p) for p in poses])
    plan = make_plan(rows_from_cameras(cameras), settings(strength=.35, smoothing=4))
    for i in range(len(poses)):
        amount = .35 * min(1., i / 4., (len(poses) - 1 - i) / 4.)
        averaged = poses[max(0, i - 4):min(len(poses), i + 5)].mean(axis=0)
        expected = pose(*(poses[i] + (averaged - poses[i]) * amount))
        corrected = np.linalg.inv(plan.inverse_matrices[i]) @ cameras[i]
        np.testing.assert_allclose(corrected, expected, atol=1e-11)
        rotation = plan.inverse_matrices[i, :2, :2]
        np.testing.assert_allclose(rotation.T @ rotation, np.eye(2), atol=1e-12)
        np.testing.assert_allclose(np.linalg.det(rotation), 1., atol=1e-12)
    np.testing.assert_array_equal(plan.inverse_matrices[[0, -1]], [IDENTITY, IDENTITY])


def test_angle_unwrap_handles_camera_spinning_through_pi():
    # The path crosses pi twice without a false 2*pi smoothing discontinuity.
    angles = np.linspace(0, 2.4 * math.pi, 121)
    cameras = np.array([pose(angle, .1 * i, -.05 * i) for i, angle in enumerate(angles)])
    plan = make_plan(rows_from_cameras(cameras), settings(smoothing=5))
    for i in range(5, 116):
        np.testing.assert_allclose(plan.inverse_matrices[i], IDENTITY, atol=2e-12)


def test_cut_paths_are_independent_source_anchored_and_stable_for_splits():
    first = rows_from_cameras([pose(.02 * math.sin(i), 3 * math.sin(i), 0) for i in range(31)])
    second = rows_from_cameras([pose(.03 * math.sin(i), 0, 4 * math.sin(i)) for i in range(29)])
    combined = np.vstack((first, second))
    # The estimate crossing the cut is explicitly ignored, even if plausible.
    combined[len(first), 1:4] = (70, -50, .7)
    cuts = np.zeros(len(combined), dtype=bool)
    cuts[len(first)] = True
    plan = make_plan(combined, cut_flags=cuts)
    np.testing.assert_allclose(plan.inverse_matrices[:31], make_plan(first).inverse_matrices, atol=1e-13)
    np.testing.assert_allclose(plan.inverse_matrices[31:], make_plan(second).inverse_matrices, atol=1e-13)
    np.testing.assert_array_equal(plan.shot_ids, [0] * 31 + [1] * 29)
    # Slices of the single source plan are exactly stable across editorial splits.
    np.testing.assert_array_equal(np.concatenate((plan.inverse_matrices[:17], plan.inverse_matrices[17:])), plan.inverse_matrices)


@pytest.mark.parametrize('invalid_kind', ['positive_extra', 'negative_extra', 'nan', 'jump', 'confidence_zero', 'confidence_nan'])
def test_invalid_estimate_is_safe_identity_and_breaks_both_paths(invalid_kind):
    raw = rows_from_cameras([pose(.01 * math.sin(i), 2 * math.sin(i), 0) for i in range(31)])
    confidence = np.ones(len(raw))
    if invalid_kind == 'positive_extra':
        raw[15, 5] = 1
    elif invalid_kind == 'negative_extra':
        raw[15, 5] = -1
    elif invalid_kind == 'nan':
        raw[15, 3] = np.nan
    elif invalid_kind == 'jump':
        raw[15, 1] = 1e300
    elif invalid_kind == 'confidence_zero':
        confidence[15] = 0
    else:
        confidence[15] = np.nan
    plan = make_plan(raw, confidence=confidence)
    assert not plan.valid[15] and plan.shot_ids[15] == -1
    np.testing.assert_array_equal(plan.inverse_matrices[14:17], [IDENTITY] * 3)
    np.testing.assert_allclose(plan.inverse_matrices[:15], make_plan(raw[:15]).inverse_matrices, atol=1e-13)
    np.testing.assert_allclose(plan.inverse_matrices[16:], make_plan(raw[16:]).inverse_matrices, atol=1e-13)
    assert np.all(np.isfinite(plan.inverse_matrices))


def test_all_invalid_never_invents_motion_or_crop_and_plan_is_readonly():
    raw = np.zeros((17, 6))
    raw[:, 5] = 1
    plan = make_plan(raw, settings(border='crop'))
    assert not plan.valid.any()
    np.testing.assert_array_equal(plan.shot_ids, [-1] * 17)
    np.testing.assert_array_equal(plan.inverse_matrices, [IDENTITY] * 17)
    assert np.all(plan.zooms == 1.)
    for array in (plan.inverse_matrices, plan.valid, plan.shot_ids, plan.zooms):
        with pytest.raises(ValueError):
            array.flat[0] = 3


def test_anisotropic_proxy_resize_conjugates_exact_pixel_centers():
    proxy, active = (80, 46), (130, 74)
    geometry = proxy_to_active_matrix(proxy, active)
    np.testing.assert_allclose(geometry @ [-.5, -.5, 1.], [-.5, -.5, 1.])
    np.testing.assert_allclose(geometry @ [79.5, 45.5, 1.], [129.5, 73.5, 1.])
    raw = rows_from_cameras([pose(.025 * math.sin(i), 1.5 * math.sin(i), 1., size=proxy) for i in range(17)], proxy)
    at_proxy = make_plan(raw, size=proxy)
    at_active = build_stabilization_plan(raw, settings(), analysis_size=proxy, active_size=active)
    expected = geometry @ at_proxy.inverse_matrices @ np.linalg.inv(geometry)
    np.testing.assert_allclose(at_active.inverse_matrices, expected, atol=1e-12)
    # Scale-only conjugation is observably different for combined rotation.
    scale_only = geometry.copy()
    scale_only[:2, 2] = 0
    wrong = scale_only @ at_proxy.inverse_matrices @ np.linalg.inv(scale_only)
    assert np.max(np.abs(wrong - at_active.inverse_matrices)) > .001


def test_crop_uses_one_bounded_static_zoom_and_containment_includes_support():
    size = (101, 81)
    cameras = [pose(0, x, 0) for x in (0, 3, -3, 3, -3, 3, 0)]
    raw = rows_from_cameras(cameras)
    black = make_plan(raw, settings(smoothing=1))
    max_shift = np.max(np.abs(black.inverse_matrices[:, 0, 2]))
    exact_cap = 50. / (50. - max_shift)
    plan = make_plan(raw, settings(smoothing=1, border='crop', max_zoom=exact_cap))
    np.testing.assert_allclose(plan.zooms, exact_cap, atol=1e-14)
    for matrix in plan.inverse_matrices:
        bounds = transformed_corners(matrix, size)
        assert bounds.min() >= -1e-12
        assert (bounds <= np.array(size) - 1 + 1e-12).all()
        result = resample_float_rgb(np.ones((81, 101, 3), dtype=np.float32), matrix, border='crop')
        np.testing.assert_array_equal(result, np.ones_like(result))
    with pytest.raises(ValueError, match='exceeding max_zoom'):
        make_plan(raw, settings(smoothing=1, border='crop', max_zoom=exact_cap - 1e-6))


def test_crop_does_not_pump_across_cuts_or_invalid_identity_motion_frames():
    raw = rows_from_cameras([pose(.015 * math.sin(i), 2 * math.sin(i), math.sin(i)) for i in range(71)])
    raw[35, 5] = 1
    cuts = np.zeros(71, bool)
    cuts[50] = True
    plan = make_plan(raw, settings(border='crop'), cut_flags=cuts)
    assert plan.zooms[0] > 1.
    assert np.unique(plan.zooms).size == 1
    # Invalid motion is identity, with only the same disclosed global crop.
    np.testing.assert_array_equal(plan.inverse_matrices[35], plan.inverse_matrices[0])
    assert not plan.valid[35]
    for matrix in plan.inverse_matrices:
        mapped = transformed_corners(matrix, (101, 81))
        assert (mapped >= -1e-9).all()
        assert (mapped <= [100 + 1e-9, 80 + 1e-9]).all()


def test_crop_over_cap_is_explicit_and_black_mode_never_zooms():
    raw = rows_from_cameras([pose(0, x, 0) for x in (0, 35, -35, 35, -35, 35, 0)])
    with pytest.raises(ValueError, match='max_zoom'):
        make_plan(raw, settings(smoothing=1, border='crop'))
    black = make_plan(raw, settings(smoothing=1, border='black'))
    assert np.all(black.zooms == 1.)


def test_float_identity_preserves_all_bits_out_of_range_and_does_not_copy():
    image = np.linspace(-2., 5., 27 * 19 * 3, dtype=np.float32).reshape(27, 19, 3)
    image[0, 0, 0] = -0.
    output = resample_float_rgb(image, IDENTITY, border='crop', tile_rows=7)
    assert output is image
    np.testing.assert_array_equal(output.view(np.uint32), image.view(np.uint32))


def test_subpixel_bilinear_float_ramp_retains_more_than_eight_bit_precision():
    yy, xx = np.mgrid[:37, :53]
    image = np.stack((xx / 65535. - .25, yy / 65535. + 1.2, (xx + yy) / 65535.), axis=-1).astype(np.float32)
    matrix = IDENTITY.copy()
    matrix[:2, 2] = (.375, .625)
    output = resample_float_rgb(image, matrix, tile_rows=5)
    # Compute the same bilinear interpolation independently in double precision.
    expected = (image[:-1, :-1].astype(float) * .625 * .375
                + image[:-1, 1:].astype(float) * .375 * .375
                + image[1:, :-1].astype(float) * .625 * .625
                + image[1:, 1:].astype(float) * .375 * .625).astype(np.float32)
    np.testing.assert_array_equal(output[:-1, :-1], expected)
    assert output.dtype == np.float32
    assert output[1, 1, 0] < 0 and output[1, 1, 1] > 1
    assert len(np.unique(output[3, :-1, 0])) == 52
    np.testing.assert_array_equal(output, resample_float_rgb(image, matrix, tile_rows=1))
    np.testing.assert_array_equal(output, resample_float_rgb(image, matrix, tile_rows=256))


def test_black_border_uses_zero_weighted_taps_and_never_previous_pixels():
    image = np.ones((8, 10, 3), dtype=np.float32)
    matrix = IDENTITY.copy()
    matrix[0, 2] = -.25
    output = resample_float_rgb(image, matrix)
    np.testing.assert_array_equal(output[:, 0], .75)
    np.testing.assert_array_equal(output[:, 1:], 1.)
    matrix[0, 2] = -1e30
    np.testing.assert_array_equal(resample_float_rgb(image, matrix), np.zeros_like(image))
    with pytest.raises(ValueError, match='uncovered'):
        resample_float_rgb(image, matrix, border='crop')


def test_tiled_sampler_matches_independent_scalar_rotation_reference():
    image = np.random.default_rng(72).uniform(-1., 3., (9, 11, 3)).astype(np.float32)
    matrix = pose(.13, .45, -.3, size=(11, 9))
    expected = np.zeros_like(image)
    for y in range(9):
        for x in range(11):
            sx, sy, _ = matrix @ (x, y, 1.)
            x0, y0 = math.floor(sx), math.floor(sy)
            fx, fy = sx - x0, sy - y0
            value = np.zeros(3)
            for dx, dy, weight in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)),
                                   (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
                if 0 <= x0 + dx < 11 and 0 <= y0 + dy < 9:
                    value += image[y0 + dy, x0 + dx].astype(float) * weight
            expected[y, x] = value
    np.testing.assert_allclose(resample_float_rgb(image, matrix, tile_rows=2), expected, atol=2e-7)


@pytest.mark.parametrize('bad_image', [
    np.zeros((3, 4, 4), np.float32), np.zeros((3, 4), np.float32),
    np.zeros((3, 4, 3), np.uint8), np.zeros((3, 4, 3), np.float64),
    np.zeros((0, 4, 3), np.float32), np.full((3, 4, 3), np.nan, np.float32),
])
def test_sampler_rejects_wrong_shape_alpha_dtype_or_nonfinite(bad_image):
    with pytest.raises(ValueError):
        resample_float_rgb(bad_image, IDENTITY)


@pytest.mark.parametrize('bad_matrix', [np.zeros((3, 3)), np.eye(2), np.full((3, 3), np.nan),
    np.array([[1., 0., 0.], [0., 1., 0.], [.01, 0., 1.]])])
def test_sampler_rejects_invalid_matrices(bad_matrix):
    with pytest.raises(ValueError):
        resample_float_rgb(np.zeros((3, 4, 3), np.float32), bad_matrix)


def test_cancellation_propagates_from_plan_and_per_tile_sampler():
    class Cancelled(Exception):
        pass

    calls = []

    def check():
        calls.append(1)
        if len(calls) == 3:
            raise Cancelled

    with pytest.raises(Cancelled):
        resample_float_rgb(np.zeros((33, 23, 3), np.float32), IDENTITY, tile_rows=5, cancel_check=check)
    assert len(calls) == 3
    calls.clear()
    with pytest.raises(Cancelled):
        make_plan(np.zeros((500, 6)), cancel_check=check)
    assert len(calls) == 3


def test_plan_rejects_bad_shape_bounds_and_flag_lengths():
    for rows in (np.zeros((3, 5)), np.zeros((0, 6)), np.zeros((MAX_MOTION_ROWS + 1, 6))):
        with pytest.raises(ValueError):
            make_plan(rows)
    for kwargs in ({'cut_flags': np.zeros(2, bool)}, {'cut_flags': [0, 0, 1]}, {'confidence': [1, 1]},
                   {'proxy_to_active': np.zeros((3, 3))}):
        with pytest.raises(ValueError):
            make_plan(np.zeros((3, 6)), **kwargs)
    with pytest.raises(ValueError):
        make_plan(np.zeros((3, 6)), size=(0, 100))

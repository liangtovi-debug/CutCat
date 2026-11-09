"""Independent scalar/color-coordinate references for periodic hue controls."""
from contrast_legacy_fixture import strip_contrast_pivot_field
from bisect import bisect_right
import colorsys
from copy import deepcopy
import json
import math

import numpy as np
import pytest

from lumen.color import apply_curve, apply_grade, apply_lut, load_cube, normalize_input
from lumen.model import Clip, Editor, Grade, Media, Project, VERSION
from lumen.selective_color import apply_selective_color


HUE = [[0, 0], [1, 0]]
SAT = [[0, 1], [1, 1]]
HUE_SHAPE = [[0, .08], [.12, -.13], [.37, .22], [.69, -.3], [1, .08]]
SAT_SHAPE = [[0, .6], [.14, 1.3], [.42, 0], [.8, 2], [1, .6]]


def constant(value):
    return [[0, value], [1, value]]


def scalar_curve(x, points):
    """Python-double reference, independent of vectorized control evaluation."""
    i = min(bisect_right([p[0] for p in points], x) - 1, len(points) - 2)
    x0, y0 = points[i]
    x1, y1 = points[i + 1]
    return y0 + (y1 - y0) * ((x - x0) / (x1 - x0))


def scalar_reference(pixel, hue_curve, sat_curve):
    """Use stdlib colorsys on normalized RGB, never the production hexcone."""
    rgb = tuple(float(v) for v in pixel)
    low, high = min(rgb), max(rgb)
    if low == high:
        return rgb
    # Normalization puts even signed/overrange RGB into colorsys' normal domain.
    # Chroma is one and value is one, so HSV output is a pure hue reference.
    unit = tuple((v - low) / (high - low) for v in rgb)
    hue = colorsys.rgb_to_hsv(*unit)[0]
    offset, factor = scalar_curve(hue, hue_curve), scalar_curve(hue, sat_curve)
    if offset:
        rgb = tuple(low + (high - low) * v for v in
                    colorsys.hsv_to_rgb((hue + offset) % 1, 1, 1))
    if factor != 1:
        y = sum(v * w for v, w in zip(rgb, (.2126, .7152, .0722)))
        rgb = tuple(y + factor * (v - y) for v in rgb)
    return rgb


def reference(rgb, hue=HUE_SHAPE, sat=SAT_SHAPE):
    return np.asarray([scalar_reference(pixel, hue, sat)
                       for pixel in rgb.reshape(-1, 3)], dtype=np.float64).reshape(rgb.shape)


def sample_project(grade=None):
    return Project(media=[Media('m', '/tmp/source.mp4', 'Source', 10)],
                   clips=[Clip('c', 'm', 0, 0, 0, 30, grade=grade or Grade())])


def assert_bits_equal(actual, expected):
    np.testing.assert_array_equal(actual.view(np.uint32), expected.view(np.uint32))


def test_defaults_are_independent_and_exact_at_entire_finite_working_range():
    limit = np.finfo(np.float32).max
    rgb = np.array([[-limit, limit, -0.], [-3, .4, 9], [0, .081, .5]], np.float32)
    for hue, sat in [(HUE, SAT),
                     ([[0, 0], [.3, 0], [1, 0]], [[0, 1], [.4, 1], [1, 1]])]:
        actual = apply_selective_color(rgb, hue, sat)
        assert_bits_equal(actual, rgb)
        assert actual.dtype == np.float32 and not np.shares_memory(actual, rgb)
        assert_bits_equal(apply_grade(rgb, Grade(hue_hue_curve=hue, hue_sat_curve=sat)), rgb)
    a, b = Grade(), Grade()
    a.hue_hue_curve[0][1] = .2
    a.hue_sat_curve[0][1] = 0
    assert b.hue_hue_curve == HUE and b.hue_sat_curve == SAT


@pytest.mark.parametrize('offset', [-.5, -1/6, -.07, 0, .125, 1/3, .5])
@pytest.mark.parametrize('factor', [0, .25, 1, 1.5, 2])
def test_constant_controls_against_stdlib_colorsys(offset, factor):
    rgb = np.random.default_rng(123).uniform(0, 1, (97, 3)).astype(np.float32)
    hue, sat = constant(offset), constant(factor)
    actual = apply_selective_color(rgb, hue, sat)
    np.testing.assert_allclose(actual, reference(rgb, hue, sat), atol=1.5e-6, rtol=2e-6)
    assert actual.dtype == np.float32


def test_shaped_periodic_controls_against_signed_scalar_reference():
    rgb = np.random.default_rng(42).uniform(-2, 3, (1007, 3)).astype(np.float32)
    original = rgb.copy()
    actual = apply_selective_color(rgb, HUE_SHAPE, SAT_SHAPE)
    np.testing.assert_allclose(actual, reference(rgb), atol=8e-6, rtol=3e-6)
    assert actual.min() < 0 and actual.max() > 1
    assert_bits_equal(rgb, original)


@pytest.mark.parametrize('offset, expected', [
    (1/6, [[1, 1, 0], [0, 1, 1], [1, 0, 1]]),
    (-1/6, [[1, 0, 1], [1, 1, 0], [0, 1, 1]]),
    (1/3, [[0, 1, 0], [0, 0, 1], [1, 0, 0]]),
])
def test_primary_rotations_have_analytic_channel_results(offset, expected):
    result = apply_selective_color(np.eye(3, dtype=np.float32), constant(offset), SAT)
    np.testing.assert_allclose(result, expected, atol=4e-7)


def test_saturation_uses_original_hue_and_rotated_encoded_luma():
    rgb = np.array([[1, 0, 0]], np.float32)
    sat = [[0, .25], [1/3, 1.75], [1, .25]]
    # Red is selected with .25, even though the first curve rotates it to green.
    actual = apply_selective_color(rgb, constant(1/3), sat)
    y = .7152
    expected = [[y * .75, y + .25 * (1 - y), y * .75]]
    np.testing.assert_allclose(actual, expected, atol=2e-7)
    np.testing.assert_allclose(actual @ np.array([.2126, .7152, .0722]), y, atol=2e-7)
    assert not np.allclose(actual, [[.75, 1, .75]])  # Not colorsys HSV saturation.


def test_saturation_luma_invariance_on_extended_values():
    rgb = np.random.default_rng(19).uniform(-3, 4, (1000, 3)).astype(np.float32)
    weights = np.array([.2126, .7152, .0722], np.float64)
    rotated = apply_selective_color(rgb, HUE_SHAPE, SAT)
    actual = apply_selective_color(rgb, HUE_SHAPE, SAT_SHAPE)
    np.testing.assert_allclose(actual @ weights, rotated @ weights, atol=1e-6, rtol=1e-6)


def test_achromatic_and_sampled_neutral_pixels_are_bit_exact():
    limit = np.finfo(np.float32).max
    gray = np.repeat(np.array([-limit, -2, -.001, -0., 0, .3, 2, limit], np.float32)[:, None], 3, axis=1)
    assert_bits_equal(apply_selective_color(gray, HUE_SHAPE, SAT_SHAPE), gray)
    # Curve modification is confined to cyan-blue; red/yellow-green stay neutral.
    hue = [[0, 0], [.4, 0], [.6, .2], [.8, 0], [1, 0]]
    sat = [[0, 1], [.4, 1], [.6, .2], [.8, 1], [1, 1]]
    rgb = np.array([[.9, .2, .2], [3, -.1, -.1], [.1, .8, .2],
                    [.1, .3, .9], [-0., 0., -0.]], np.float32)
    actual = apply_selective_color(rgb, hue, sat)
    assert_bits_equal(actual[[0, 1, 2, 4]], rgb[[0, 1, 2, 4]])
    assert not np.array_equal(actual[3], rgb[3])


def test_red_wrap_is_continuous_and_tiny_negative_angles_do_not_create_sector_six():
    hue = [[0, .19], [.2, 0], [.8, 0], [1, .19]]
    sat = [[0, .7], [.2, 1], [.8, 1], [1, .7]]
    epsilon = 1e-6
    rgb = np.asarray([colorsys.hsv_to_rgb(h, .8, .9)
                      for h in [0, epsilon, 1-epsilon]], np.float32)
    result = apply_selective_color(rgb, hue, sat)
    np.testing.assert_allclose(result, reference(rgb, hue, sat), atol=8e-7)
    assert np.max(np.abs(result[1] - result[2])) < 2e-5
    # np.remainder(float32(-1e-9),1) rounds to 1.0; that must wrap to red.
    tiny = np.array([[1, 0, 1e-9], [1, 1e-9, 0], [1, 0, 0]], np.float32)
    for offset in [-1e-9, 1e-9, .5, -.5]:
        np.testing.assert_allclose(apply_selective_color(tiny, constant(offset), SAT),
                                   reference(tiny, constant(offset), SAT), atol=8e-7)


def test_narrow_valid_control_coordinates_do_not_collapse_to_float32_zero_width():
    next_x = math.nextafter(.5, 1)
    hue = [[0, 0], [.5, .1], [next_x, -.1], [1, 0]]
    rgb = np.array([[0, 1, 1], [1, 0, 0]], np.float32)
    np.testing.assert_allclose(apply_selective_color(rgb, hue, SAT),
                               reference(rgb, hue, SAT), atol=8e-7)
    # Subnormal control intervals also avoid a slope = delta_y/tiny_dx overflow.
    subnormal = [[0, .1], [math.nextafter(0, 1), -.1], [1, .1]]
    actual = apply_selective_color(rgb, subnormal, SAT)
    assert np.isfinite(actual).all()


def test_precision_gradient_has_no_eight_bit_intermediate():
    hues = np.linspace(.04, .14, 8192)
    rgb = np.asarray([colorsys.hsv_to_rgb(float(h), .8, .9) for h in hues], np.float32)
    result = apply_selective_color(rgb, constant(.02), constant(.8))
    assert len(np.unique(result[:, 1])) == len(rgb)
    np.testing.assert_allclose(result, reference(rgb, constant(.02), constant(.8)), atol=8e-7)


def test_arbitrary_shapes_strides_empty_input_and_block_boundaries(monkeypatch):
    from lumen import selective_color
    rgb = np.random.default_rng(51).uniform(-1, 2, (9, 7, 3)).astype(np.float32)[::-1, ::2]
    expected = reference(rgb)
    monkeypatch.setattr(selective_color, '_BLOCK_PIXELS', 7)
    actual = apply_selective_color(rgb, HUE_SHAPE, SAT_SHAPE)
    np.testing.assert_allclose(actual, expected, atol=5e-6, rtol=3e-6)
    assert actual.shape == rgb.shape and actual.dtype == np.float32
    assert not np.shares_memory(actual, rgb)
    assert apply_selective_color(np.empty((0, 3), np.float32), HUE_SHAPE, SAT_SHAPE).shape == (0, 3)
    single = rgb[0, 0]
    np.testing.assert_allclose(apply_selective_color(single, HUE_SHAPE, SAT_SHAPE),
                               reference(single), atol=3e-6)


def test_unrepresentable_active_arithmetic_fails_without_mutating_input():
    limit = np.finfo(np.float32).max
    rgb = np.array([[-limit, limit, 0]], np.float32)
    original = rgb.copy()
    with pytest.raises(ValueError, match='non-finite'):
        apply_selective_color(rgb, constant(.2), SAT)
    assert_bits_equal(rgb, original)
    rgb = np.array([[limit * .9, 0, 0]], np.float32)
    with pytest.raises(ValueError, match='non-finite'):
        apply_selective_color(rgb, HUE, constant(2))


@pytest.mark.parametrize('rgb', [
    np.zeros((2, 3), np.float64), np.zeros((2, 3), np.uint8),
    np.zeros((2, 4), np.float32), np.asarray(1, np.float32),
    np.array([[0, np.nan, 1]], np.float32), np.array([[0, np.inf, 1]], np.float32),
    [[1, 0, 0]],
])
def test_rejects_invalid_pixels(rgb):
    with pytest.raises(ValueError):
        apply_selective_color(rgb, HUE, SAT)


@pytest.mark.parametrize('field, points', [
    (field, points) for field in ('hue_hue_curve', 'hue_sat_curve') for points in
    [None, (), [], [[0, 0]], [[0, 0], [0, 0], [1, 0]], [[0, 0], [.8, 0], [.4, 0], [1, 0]],
     [[.1, 0], [1, 0]], [[0, 0], [.9, 0]], [[0, 0], [1, 1]],
     [[False, 0], [1, 0]], [[0, True], [1, True]], [[0, 0], [float('nan'), 0], [1, 0]],
     [[0, float('inf')], [1, float('inf')]], [[0, '0'], [1, '0']],
     [[0, 0, 0], [1, 0, 0]], [(0, 0), (1, 0)], [[i/32, 0] for i in range(33)]]
] + [('hue_hue_curve', constant(-.5001)), ('hue_hue_curve', constant(.5001)),
     ('hue_sat_curve', constant(-.001)), ('hue_sat_curve', constant(2.001))])
def test_strict_curve_validation_and_atomic_editor_rejection(field, points):
    grade = Grade(**{field: points})
    with pytest.raises(ValueError):
        sample_project(grade).validate()
    with pytest.raises(ValueError):
        apply_selective_color(np.zeros((1, 3), np.float32), grade.hue_hue_curve, grade.hue_sat_curve)
    editor = Editor(sample_project())
    before = editor.project.to_dict()
    with pytest.raises(ValueError):
        editor.update_clip('c', grade=grade)
    assert editor.project.to_dict() == before


@pytest.mark.parametrize('field, low, high', [('hue_hue_curve', -.5, .5), ('hue_sat_curve', 0, 2)])
def test_curve_bounds_and_maximum_point_count_are_accepted(field, low, high):
    points = [[i/31, low if i in (0, 31) else high] for i in range(32)]
    sample_project(Grade(**{field: points})).validate()


def test_schema_17_roundtrip_requires_fields_and_rejects_legacy_smuggling(tmp_path):
    project = sample_project(Grade(hue_hue_curve=HUE_SHAPE, hue_sat_curve=SAT_SHAPE))
    payload = project.to_dict()
    assert payload['version'] == VERSION
    assert Project.from_dict(payload) == project
    path = tmp_path / 'selective.lumen'
    project.save(path)
    assert Project.load(path).clips[0].grade == project.clips[0].grade
    assert json.loads(path.read_text())['clips'][0]['grade']['hue_hue_curve'] == HUE_SHAPE
    for name in ('hue_hue_curve', 'hue_sat_curve'):
        missing = deepcopy(payload)
        del missing['clips'][0]['grade'][name]
        with pytest.raises(ValueError, match='require both'):
            Project.from_dict(missing)
    downgraded = deepcopy(payload)
    downgraded['version'] = 16; strip_contrast_pivot_field(downgraded)
    with pytest.raises(ValueError, match='Legacy projects'):
        Project.from_dict(downgraded)
    for name in ('hue_hue_curve', 'hue_sat_curve'):
        del downgraded['clips'][0]['grade'][name]
    downgraded["clips"][0]["grade"].pop("qualifier")
    restored = Project.from_dict(downgraded)
    assert restored.clips[0].grade.hue_hue_curve == HUE
    assert restored.clips[0].grade.hue_sat_curve == SAT
    rgb = np.random.default_rng(21).uniform(-1, 3, (99, 3)).astype(np.float32)
    assert_bits_equal(apply_grade(rgb, restored.clips[0].grade), rgb)
    malformed = deepcopy(payload)
    malformed['clips'][0]['grade']['hue_lightness_curve'] = HUE
    with pytest.raises(ValueError):
        Project.from_dict(malformed)


def test_selective_edits_undo_redo_split_and_snapshot_are_independent():
    editor = Editor(sample_project())
    grade = Grade(hue_hue_curve=deepcopy(HUE_SHAPE), hue_sat_curve=deepcopy(SAT_SHAPE))
    editor.update_clip('c', grade=grade)
    snapshot = editor.project.to_dict()
    grade.hue_hue_curve[1][1] = .4
    assert editor.project.clips[0].grade.hue_hue_curve == HUE_SHAPE
    assert editor.undo() and editor.project.clips[0].grade == Grade()
    assert editor.redo() and editor.project.to_dict() == snapshot
    left, right = editor.split_clip('c', 15)
    left.grade.hue_sat_curve[1][1] = .01
    assert right.grade.hue_sat_curve == SAT_SHAPE
    snapshot['clips'][0]['grade']['hue_hue_curve'][1][1] = -.4
    assert right.grade.hue_hue_curve == HUE_SHAPE


def test_grade_order_normalization_then_rgb_curves_then_selective_then_lut(tmp_path):
    rgb = np.array([[.9, .2, .1], [.2, .6, .8]], np.float32)
    cube = tmp_path / 'swap.cube'
    cube.write_text('LUT_3D_SIZE 2\n' + '\n'.join(
        f'{b} {r} {g}' for b in (0, 1) for g in (0, 1) for r in (0, 1)))
    grade = Grade(input_encoding='srgb', master_curve=[[0, .02], [1, .95]],
                  red_curve=[[0, 0], [1, .8]], hue_hue_curve=HUE_SHAPE,
                  hue_sat_curve=SAT_SHAPE, lut_path=str(cube), lut_strength=.3)
    intermediate = normalize_input(rgb, 'srgb')
    intermediate = apply_curve(intermediate, grade.master_curve)
    intermediate[:, 0] = apply_curve(intermediate[:, 0], grade.red_curve)
    selective = reference(intermediate).astype(np.float32)
    expected = selective * np.float32(.7) + apply_lut(selective, load_cube(cube)) * np.float32(.3)
    np.testing.assert_allclose(apply_grade(rgb, grade), expected, atol=2e-6)
    bypass = apply_grade(rgb, grade, bypass=True)
    assert_bits_equal(bypass, normalize_input(rgb, 'srgb'))
    grade.enabled = False
    assert_bits_equal(apply_grade(rgb, grade), bypass)


def test_lut_zero_strength_preserves_selective_headroom_without_loading_file():
    rgb = np.array([[-.5, .5, 2]], np.float32)
    grade = Grade(hue_hue_curve=HUE_SHAPE, hue_sat_curve=SAT_SHAPE,
                  lut_path='/nonexistent/selective.cube', lut_strength=0)
    result = apply_grade(rgb, grade)
    np.testing.assert_allclose(result, reference(rgb), atol=3e-6)
    assert result.min() < 0 and result.max() > 1

"""Picker geometry stays neutral on the renderer's existing piecewise line."""
import copy

import numpy as np
import pytest

from lumen.color_picker import (
    MIN_PICK_CHROMA, curve_support_intervals, insert_hue_points,
    sample_hue, selected_support_intervals,
)
from lumen.selective_color import _evaluate_curve, apply_selective_color


@pytest.mark.parametrize('rgb,hue', [([1, 0, 0], 0), ([1, 1, 0], 1/6),
                                    ([0, 1, 0], 1/3), ([0, 1, 1], .5),
                                    ([0, 0, 1], 2/3), ([1, 0, 1], 5/6)])
def test_primary_secondary_sample_hues(rgb, hue):
    assert sample_hue(np.array(rgb, np.float32)) == pytest.approx(hue)


@pytest.mark.parametrize('rgb', [[0, 0, 0], [1, 1, 1], [.5, .5, .5],
                                [0, 0, MIN_PICK_CHROMA], [.5, .5001, .5],
                                [np.nan, 0, 1], [0, np.inf, 1], [-.01, 0, 1],
                                [1.01, 0, 0]])
def test_unpickable_sample_has_no_red_fallback(rgb):
    assert sample_hue(np.array(rgb, np.float32)) is None


def test_sample_shape_precision_and_transparency():
    assert sample_hue(np.array([1, 0, 0], np.float32), valid=False) is None
    assert sample_hue(np.array([1, 0, 0], np.float64)) is None
    assert sample_hue([1., 0., 0.]) is None
    assert sample_hue(np.ones((1, 3), np.float32)) is None
    assert sample_hue(np.ones(4, np.float32)) is None
    assert sample_hue(np.array([0, 0, .01], np.float32)) == pytest.approx(2/3)
    assert sample_hue(np.array([1, 0, 1e-12], np.float32)) == 0


@pytest.mark.parametrize('neutral', [0., 1.])
@pytest.mark.parametrize('hue', [0., 1., .001, .999, .5, 1/12, 11/12])
def test_neutral_insert_wrap_selection_and_repeat(neutral, hue):
    original = [[0., neutral], [1., neutral]]
    before = copy.deepcopy(original)
    picked = insert_hue_points(original, hue)
    assert original == before
    assert picked.points[picked.selected_index] == [hue % 1, neutral]
    assert all(p[1] == neutral for p in picked.points)
    assert picked.points[0] == [0., neutral]
    assert picked.points[-1] == [1., neutral]
    assert insert_hue_points(picked.points, hue) == picked
    assert curve_support_intervals(picked.points, neutral) == []
    if hue in (0, 1, .001, .999):
        assert any(p[0] < .1 for p in picked.points[1:-1])
        assert any(p[0] > .9 for p in picked.points[1:-1])


def test_red_actual_adjacent_support_links_endpoints():
    picked = insert_hue_points([[0., .1], [.04, .2], [.96, 0.], [1., .1]], 0)
    assert picked.selected_index == 0
    assert picked.support_intervals == [(0., .04), (.96, 1.)]
    assert selected_support_intervals(picked.points, len(picked.points)-1) == picked.support_intervals


def test_near_existing_selects_exact_center_without_overwrite():
    points = [[0., 0.], [.3, .1], [.300000000000001, .2], [1., 0.]]
    picked = insert_hue_points(points, .300000000000002)
    assert picked.points[picked.selected_index] == points[2]
    assert all(p in picked.points for p in points)
    assert picked.support_intervals[0][0] == .3
    assert insert_hue_points(points, 1 - 1e-7).selected_index == 0


@pytest.mark.parametrize('hue', [0., .000001, .04, .499999999999999, .5, .72, .999])
def test_insertion_dense_reference_preserves_all_original_values(hue):
    points = [[0., .1], [.04, -.2], [.499999999999998, .4],
              [.5, -.1], [.72, .15], [1., .1]]
    picked = insert_hue_points(points, hue)
    assert all(p in picked.points for p in points)
    samples = np.concatenate((np.linspace(0., 1., 10001),
                              np.array([p[0] for p in points]),
                              np.array([.499999999999999])))
    old = _evaluate_curve(samples, np.array(points, np.float64))
    new = _evaluate_curve(samples, np.array(picked.points, np.float64))
    np.testing.assert_allclose(new, old, atol=3e-8, rtol=1e-7)


def test_capacity_failure_is_atomic_and_noop_at_capacity_succeeds():
    # 32 controls including exact center and both requested anchors.
    xs = sorted(set([0., 1., .25, .5, .75] + [i / 100 for i in range(1, 29)]))
    assert len(xs) == 32
    points = [[x, 0.] for x in xs]
    before = copy.deepcopy(points)
    with pytest.raises(ValueError, match='32-point limit'):
        insert_hue_points(points, .9)
    assert points == before
    picked = insert_hue_points(points, .5, half_width=.25)
    assert picked.points == points
    assert picked.selected_index == xs.index(.5)
    picked.points[0][1] = .1
    assert points == before


def test_non_neutral_support_reports_entire_curve_not_just_selection():
    points = [[0., 0.], [.1, .2], [.2, 0.], [.3, 0.], [.4, -.1],
              [.5, 0.], [.8, 0.], [.9, .1], [1., 0.]]
    assert curve_support_intervals(points, 0) == [(0., .2), (.3, .5), (.8, 1.)]
    assert curve_support_intervals([[0., .1], [.5, -.1], [1., .1]], 0) == [(0., 1.)]
    assert curve_support_intervals([[0., 1.], [.5, 1.], [1., 1.]], 1) == []
    assert curve_support_intervals([[0., 1.], [.5, 1.], [1., 1.]], 0) == [(0., 1.)]


def test_neutral_anchor_insertion_is_pixel_exact_noop():
    rng = np.random.default_rng(812)
    rgb = rng.random((20, 30, 3), dtype=np.float32)
    hue = insert_hue_points([[0., 0.], [1., 0.]], .98).points
    sat = insert_hue_points([[0., 1.], [1., 1.]], .02).points
    np.testing.assert_array_equal(apply_selective_color(rgb, hue, sat), rgb)


@pytest.mark.parametrize('hue', [-.1, 1.1, float('nan'), float('inf'), True])
def test_bad_hue_rejected(hue):
    with pytest.raises(ValueError):
        insert_hue_points([[0., 0.], [1., 0.]], hue)


@pytest.mark.parametrize('width', [0., -.1, .5, float('nan'), float('inf'), True])
def test_bad_width_rejected(width):
    with pytest.raises(ValueError):
        insert_hue_points([[0., 0.], [1., 0.]], .5, width)


def test_invalid_curve_and_selection_rejected():
    with pytest.raises(ValueError):
        insert_hue_points([[0., 0.], [1., .1]], .5)
    with pytest.raises(ValueError):
        selected_support_intervals([[0., 0.], [1., 0.]], -1)
    with pytest.raises(ValueError):
        curve_support_intervals([[0., 0.], [1., 0.]], float('nan'))

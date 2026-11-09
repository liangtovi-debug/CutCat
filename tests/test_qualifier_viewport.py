"""Pure geometry tests: exact pixels and bounded work at every detail level."""
from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from lumen.qualifier_viewport import Viewport, centered, overview


@pytest.mark.parametrize('width,height', [(1, 1), (23, 41), (512, 512),
                                         (513, 512), (1920, 1080), (7680, 4320)])
def test_overview_preserves_existing_sparse_grid(width, height):
    view = overview(width, height)
    step = (max(width, height) + 511) // 512
    assert view == Viewport(0, 0, width, height, step)
    xs, ys = view.source_grid()
    np.testing.assert_array_equal(xs, np.arange(0, width, step))
    np.testing.assert_array_equal(ys, np.arange(0, height, step))
    assert len(xs) <= 512 and len(ys) <= 512


@pytest.mark.parametrize('zoom,side', [(1, 512), (2, 256), (4, 128), (8, 64)])
def test_detail_is_contiguous_original_pixel_view(zoom, side):
    frame = np.arange(900 * 1100 * 3, dtype=np.float32).reshape(900, 1100, 3)
    view = centered(1100, 900, (603, 487), zoom)
    detail = view.extract(frame)
    assert detail.shape == (side, side, 3)
    assert view.stride == 1
    assert np.shares_memory(detail, frame)
    xs, ys = view.source_grid()
    for x, y in [(0, 0), (side // 2, side // 2), (side - 1, side - 1)]:
        sx, sy = view.source_point(x, y)
        assert (sx, sy) == (xs[x], ys[y])
        np.testing.assert_array_equal(detail[y, x], frame[sy, sx])


def test_tiny_feature_missed_by_overview_is_pickable_in_detail():
    frame = np.zeros((1080, 1920, 3), dtype=np.float32)
    frame[501, 1001] = (0.937123, 0.128734, 0.234912)
    assert not overview(1920, 1080).extract(frame).any()
    view = centered(1920, 1080, (1001, 501), zoom=8)
    local_x, local_y = 1001 - view.x, 501 - view.y
    np.testing.assert_array_equal(view.extract(frame)[local_y, local_x], frame[501, 1001])
    assert view.source_point(local_x, local_y) == (1001, 501)


@pytest.mark.parametrize('center,origin', [((-100, -100), (0, 0)),
                                          ((99999, 99999), (488, 288)),
                                          ((1e300, -1e300), (488, 0))])
def test_detail_clamps_at_source_edges(center, origin):
    view = centered(1000, 800, center)
    assert (view.x, view.y) == origin


def test_small_and_narrow_sources_remain_exact():
    assert centered(3, 2) == Viewport(0, 0, 3, 2)
    assert centered(1, 900, (0, 899), zoom=8) == Viewport(0, 836, 1, 64)
    assert centered(1000, 800).center == (499.5, 399.5)


def test_pan_clamps_without_changing_detail_size():
    view = centered(1000, 800)
    assert view.pan(-10000, -10000, 1000, 800) == Viewport(0, 0, 512, 512)
    assert view.pan(10000, 10000, 1000, 800) == Viewport(488, 288, 512, 512)
    assert view.pan(3, -7, 1000, 800) == Viewport(view.x + 3, view.y - 7, 512, 512)
    with pytest.raises(FrozenInstanceError):
        view.x = 0


def test_mask_and_noncontiguous_arrays_use_same_coordinates_without_copy():
    frame = np.arange(100 * 150).reshape(100, 150)[:, ::-1]
    view = centered(150, 100, (120, 80), zoom=8)
    crop = view.extract(frame)
    assert np.shares_memory(crop, frame)
    xs, ys = view.source_grid()
    np.testing.assert_array_equal(crop, frame[np.ix_(ys, xs)])


@pytest.mark.parametrize('bad', [True, np.bool_(False), 1.5, '10', None, 0, -1])
def test_invalid_dimensions_rejected(bad):
    for build in (overview, centered):
        with pytest.raises(ValueError):
            build(bad, 100)
        with pytest.raises(ValueError):
            build(100, bad)


@pytest.mark.parametrize('bad', [True, 1.0, 0, -1, 3, 16, None])
def test_invalid_detail_zoom_rejected(bad):
    with pytest.raises(ValueError):
        centered(1000, 800, zoom=bad)


@pytest.mark.parametrize('bad', [(float('nan'), 0), (0, float('inf')), (True, 1),
                                ('1', 2), (1,), '12'])
def test_invalid_center_rejected(bad):
    with pytest.raises(ValueError):
        centered(1000, 800, bad)


def test_invalid_viewports_and_outside_points_rejected():
    for args in [(-1, 0, 1, 1), (0, 0, 0, 1), (0, 0, 1, 1, 0),
                 (0, 0, 513, 1), (0.5, 0, 1, 1), (False, 0, 1, 1)]:
        with pytest.raises(ValueError):
            Viewport(*args)
    view = Viewport(3, 4, 8, 8)
    for point in [(-1, 0), (8, 0), (0, 8), (True, 0), (0, 1.5)]:
        with pytest.raises(ValueError):
            view.source_point(*point)
    for frame in [None, np.zeros(10), np.zeros((10, 10))]:
        with pytest.raises(ValueError):
            view.extract(frame)
    with pytest.raises(ValueError):
        view.pan(0.5, 0, 100, 100)
    with pytest.raises(ValueError):
        view.pan(0, 0, 7, 100)


@pytest.mark.parametrize('bad', [0, -1, True, 513, 1.5])
def test_view_size_cap_cannot_be_bypassed(bad):
    for build in (overview, centered):
        with pytest.raises(ValueError):
            build(1000, 800, max_size=bad)


def test_numpy_integer_dimensions_and_independent_axes():
    view = overview(np.int64(1025), np.int32(513))
    xs, _ = view.source_grid()
    xs[0] = 99
    assert view.source_point(0, 0) == (0, 0)
    assert type(view.width) is int


def test_nullable_round_trip_mapping_rejects_unrepresented_pixels():
    sparse = overview(1920, 1080)
    assert sparse.source_at(250, 125) == (1000, 500)
    assert sparse.grid_at(1000, 500) == (250, 125)
    assert sparse.grid_at(1001, 501) is None
    for point in [(-1, 0), (1920, 1080), (True, 0), (1.0, 0), (None, 0)]:
        assert sparse.grid_at(*point) is None
    for point in [(-1, 0), (480, 0), (0, 270), (True, 0), (1.0, 0)]:
        assert sparse.source_at(*point) is None
    detail = centered(1920, 1080, (1001, 501), zoom=8)
    assert detail.source_at(*detail.grid_at(1001, 501)) == (1001, 501)
    assert detail.pan(500, 0, 1920, 1080).grid_at(1001, 501) is None

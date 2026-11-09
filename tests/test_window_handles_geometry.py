"""Independent pixel-center geometry and bounded, non-accumulating gestures."""
import numpy as np
import pytest

from lumen.qualifier import QualifierSettings, qualifier_mask
from lumen.qualifier_viewport import centered, overview
from lumen.qualifier_window_handles import WindowGeometry, WindowMapping, WindowDrag


@pytest.mark.parametrize('size', [(1027, 801), (3841, 2161), (511, 137), (1, 1)])
@pytest.mark.parametrize('dpr', [1., 1.25, 1.5, 2.])
def test_mapping_hits_exact_sparse_and_crop_sample_centers(size, dpr):
    cw, ch = size
    for view in [overview(cw, ch)] + [centered(cw, ch, center, zoom)
            for zoom in (1, 2, 4, 8) for center in ((0, 0), (cw - 1, ch - 1), (cw / 3, ch / 3))]:
        # These are physical-aligned logical rectangles, including fractional
        # offsets and extents. No source fraction/viewport.width shortcut.
        left, top, width, height = np.array([13, 7, 397, 181], float) / dpr
        mapping = WindowMapping(view, cw, ch, left, top, width, height)
        for gx in (0, view.sample_width // 2, view.sample_width - 1):
            for gy in (0, view.sample_height // 2, view.sample_height - 1):
                sx, sy = view.source_at(gx, gy)
                expected = (left + (gx + .5) * width / view.sample_width,
                            top + (gy + .5) * height / view.sample_height)
                actual = mapping.display_point((sx + .5) / cw, (sy + .5) / ch)
                np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-10)
                np.testing.assert_allclose(mapping.canvas_point(*expected),
                    ((sx + .5) / cw, (sy + .5) / ch), rtol=0, atol=1e-14)


def test_sparse_phase_is_not_naive_normalized_display_fraction():
    view = overview(1027, 801)
    mapping = WindowMapping(view, 1027, 801, 11.2, 9.6, 343.2, 267.2)
    x, y = mapping.display_point(0, 0)
    assert x != mapping.left and y != mapping.top
    # Source pixel center 0.5 maps to displayed sample center 0.5, although
    # stride is three and the sparse last sample is not source width-0.5.
    assert mapping.display_point(.5 / 1027, .5 / 801) == pytest.approx(
        (11.2 + .5 * 343.2 / 343, 9.6 + .5 * 267.2 / 267))
    assert mapping.canvas_delta(343.2, 267.2) == pytest.approx((1029 / 1027, 801 / 801))


@pytest.mark.parametrize('shape', ['ellipse', 'rectangle'])
def test_outline_and_core_window_membership_share_pretransform_coordinates(shape):
    cw, ch = 1027, 801
    view = centered(cw, ch, (311, 377), 4)
    settings = QualifierSettings(enabled=True, hue_width=.5, window_shape=shape,
        window_center_x=.3123456789, window_center_y=.4689123456,
        window_width=.0678912345, window_height=.0812345678, window_feather=0)
    geometry = WindowGeometry.from_settings(settings)
    mapping = WindowMapping(view, cw, ch, 25.6, 3.2, 211.2, 211.2)
    frame = np.full((view.sample_height, view.sample_width, 3), .5, np.float32)
    mask = qualifier_mask(frame, settings, canvas_size=(cw, ch), origin=(view.x, view.y), stride=view.stride)
    for gy in range(view.sample_height):
        for gx in range(view.sample_width):
            # Use direct raster sample-center screen positions, not the
            # production normalized-to-display helper as the expected value.
            x = mapping.left + (gx + .5) * mapping.width / view.sample_width
            y = mapping.top + (gy + .5) * mapping.height / view.sample_height
            assert mapping.contains(geometry, x, y) == bool(mask[gy, gx])


@pytest.mark.parametrize('handle', ['center', 'left', 'right', 'top', 'bottom'])
def test_noop_precise_values_and_total_displacement_do_not_accumulate(handle):
    geometry = WindowGeometry(.123456789123, .765432198765, .123456789123, .234567891234, 'ellipse')
    mapping = WindowMapping(overview(1027, 801), 1027, 801, 20, 10, 411, 321)
    gesture = WindowDrag(geometry, mapping, handle, 41.25, 31.75)
    assert gesture.updated(41.25, 31.75) == geometry
    for step in range(1000):
        gesture.updated(41.25 + step / 20, 31.75 - step / 30)
    assert gesture.updated(41.25, 31.75) == geometry
    first = gesture.updated(52.5, 40.25)
    assert gesture.updated(52.5, 40.25) == first
    assert gesture.updated(float('nan'), 0) == geometry
    assert geometry.shape == first.shape


@pytest.mark.parametrize('handle,delta,field,expected', [
    ('center', (-1e7, -1e7), 'center_x', 0),
    ('center', (1e7, 1e7), 'center_y', 1),
    ('right', (-1e7, 0), 'width', .001),
    ('left', (1e7, 0), 'width', .001),
    ('right', (1e7, 0), 'width', 2),
    ('left', (-1e7, 0), 'width', 2),
    ('top', (0, 1e7), 'height', .001),
    ('bottom', (0, -1e7), 'height', .001),
    ('top', (0, -1e7), 'height', 2),
    ('bottom', (0, 1e7), 'height', 2),
])
def test_numeric_limits_and_symmetric_resize(handle, delta, field, expected):
    geometry = WindowGeometry(.5, .5, .5, .5, 'rectangle')
    mapping = WindowMapping(overview(1027, 801), 1027, 801, 20, 10, 411, 321)
    result = WindowDrag(geometry, mapping, handle, 0, 0).updated(*delta)
    assert getattr(result, field) == expected
    if handle != 'center':
        assert result.center_x == geometry.center_x and result.center_y == geometry.center_y
    else:
        assert result.width == geometry.width and result.height == geometry.height


def test_tiny_geometry_handles_spaced_without_mutating_real_shape():
    geometry = WindowGeometry(.5, .5, .001, .001, 'ellipse')
    mapping = WindowMapping(overview(3841, 2161), 3841, 2161, 0, 0, 180, 120)
    center = mapping.display_point(.5, .5)
    handles = mapping.handles(geometry)
    assert handles['center'] == center
    assert handles['right'][0] - handles['left'][0] == 32
    assert handles['bottom'][1] - handles['top'][1] == 32
    assert mapping.bounds(geometry)[2] - mapping.bounds(geometry)[0] < 1
    for name, point in handles.items():
        assert WindowDrag(geometry, mapping, name, *point).updated(*point) == geometry
    assert geometry.width == .001

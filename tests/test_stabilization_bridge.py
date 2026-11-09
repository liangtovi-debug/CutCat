"""Bridge guarantees independent of the motion estimator's image decisions."""
from types import SimpleNamespace
import threading
import numpy as np
import pytest
from lumen.render import RenderCancelled, RenderError
from lumen.stabilization_render import PreparedStabilization, float_picture_transform


def prepared(width=4, height=2, count=3):
    return PreparedStabilization(None, SimpleNamespace(active_width=width, active_height=height),
                                 np.repeat(np.eye(3)[None], count, axis=0), 'test', {})


def test_identity_preserves_float_picture_and_out_of_range():
    image = np.linspace(-.25, 1.5, 24, dtype=np.float32).reshape(2, 4, 3)
    result = float_picture_transform(prepared(), 4, 2)(image, 1)
    assert result is image
    np.testing.assert_array_equal(result, image)


def test_padding_added_after_active_picture_warp_not_transformed():
    item = prepared()
    item.inverse_matrices[1, 0, 2] = 1
    image = np.ones((2, 4, 3), np.float32)
    result = float_picture_transform(item, 8, 6)(image, 1)
    assert result.shape == (6, 8, 3) and result.dtype == np.float32
    np.testing.assert_array_equal(result[2:4, 2:5], 1)
    np.testing.assert_array_equal(result[2:4, 5], 0)
    assert not result[:2].any() and not result[4:].any()
    assert not result[:, :2].any() and not result[:, 6:].any()


@pytest.mark.parametrize('index', [-1, 3])
def test_source_grid_index_fails_explicitly(index):
    with pytest.raises(RenderError, match='outside'):
        float_picture_transform(prepared(), 4, 2)(np.ones((2, 4, 3), np.float32), index)


def test_invalid_geometry_does_not_crop_silently():
    with pytest.raises(RenderError, match='exceeds'):
        float_picture_transform(prepared(), 2, 2)


def test_cancel_before_any_float_work():
    cancel = threading.Event(); cancel.set()
    with pytest.raises(RenderCancelled):
        float_picture_transform(prepared(), 4, 2, cancel)(np.ones((2, 4, 3), np.float32), 0)

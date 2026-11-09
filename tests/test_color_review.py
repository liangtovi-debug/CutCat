"""Independent numerical acceptance checks for the owned SDR color stack."""
import numpy as np
from lumen.color import apply_grade, rec709_to_linear, linear_to_rec709
from lumen.model import Grade


def test_highlights_can_be_recovered_after_linear_exposure():
    rgb = np.full((1, 1, 3), .8, np.float32)
    grade = Grade(linear_exposure=2, master_curve=[[0, 0], [1, .5]])
    encoded_expanded = linear_to_rec709(rec709_to_linear(rgb) * np.float32(4))
    assert encoded_expanded.min() > 1
    actual = apply_grade(rgb, grade)
    np.testing.assert_allclose(actual, encoded_expanded * np.float32(.5), atol=2e-7)
    # An 8-bit or clamped intermediate before the curve would instead give .5.
    assert actual.min() > .65
    assert actual.max() < 1


def test_working_linear_exposure_differs_from_encoded_gain():
    rgb = np.full((1, 1, 3), .18, np.float32)
    linear = apply_grade(rgb, Grade(linear_exposure=1))
    encoded = apply_grade(rgb, Grade(exposure=1))
    np.testing.assert_allclose(rec709_to_linear(linear), 2 * rec709_to_linear(rgb), atol=1e-7)
    np.testing.assert_allclose(encoded, rgb * 2, atol=1e-7)
    assert np.max(np.abs(linear - encoded)) > .05


def test_transfer_reference_values_and_neutral_channels():
    # Published Rec.709 OETF anchor values, independently calculated.
    values = np.array([0, .01, .18, 1], np.float32)
    expected = np.array([0, .045, 1.099 * .18 ** .45 - .099, 1], np.float32)
    np.testing.assert_allclose(linear_to_rec709(values), expected, atol=1e-7)
    neutral = np.repeat(values[:, None], 3, axis=1)
    grade = Grade(linear_exposure=.7, lift=[.01]*3, gamma=[1.15]*3, gain=[.95]*3)
    actual = apply_grade(neutral, grade)
    np.testing.assert_array_equal(actual[:,0], actual[:,1])
    np.testing.assert_array_equal(actual[:,1], actual[:,2])

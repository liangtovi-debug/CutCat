"""Integration ownership: disabled/neutral keys do not add a frame pass."""
import numpy as np
import pytest
from lumen.color import apply_grade
from lumen.model import Grade
from lumen.qualifier import QualifierSettings

@pytest.mark.parametrize('enabled', [False, True])
def test_neutral_qualifier_skips_extra_frame_pass_without_skipping_validation(monkeypatch, enabled):
    def unexpected(*args):
        raise AssertionError('Neutral qualifier should not allocate/scan the whole frame')
    monkeypatch.setattr('lumen.color.apply_qualifier', unexpected)
    rgb = np.array([[.12, .23, .34], [-.1, 1.2, -0.]], np.float32)
    grade = Grade(qualifier=QualifierSettings(enabled=enabled, hue_center=.123, hue_width=.001))
    result = apply_grade(rgb, grade)
    np.testing.assert_array_equal(result.view(np.uint32), rgb.view(np.uint32))
    grade.qualifier.hue_width = float('nan')
    with pytest.raises(ValueError):
        apply_grade(rgb, grade)

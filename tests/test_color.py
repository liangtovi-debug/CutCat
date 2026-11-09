import numpy as np
import pytest
from lumen.color import apply_grade, apply_curve, rec709_to_linear, linear_to_rec709, load_cube, apply_lut
from lumen.model import Grade


def test_identity_and_bypass_are_exact_copies():
    x = np.linspace(-.5, 2, 3000, dtype=np.float32).reshape(-1, 3)
    for g, bypass in [(Grade(), False), (Grade(exposure=3), True), (Grade(exposure=3, enabled=False), False)]:
        result = apply_grade(x, g, bypass)
        np.testing.assert_array_equal(result, x)
        assert result.dtype == np.float32 and not np.shares_memory(result, x)


def test_transfer_reference_and_extended_roundtrip():
    x = np.array([-.5, -.01, 0, .01, .18, 1, 2], np.float32)
    encoded = linear_to_rec709(x)
    np.testing.assert_allclose(encoded[3], .045, atol=1e-7)
    np.testing.assert_allclose(encoded[4], 1.099 * .18 ** .45 - .099, atol=1e-7)
    np.testing.assert_allclose(rec709_to_linear(encoded), x, atol=5e-7)
    assert encoded[0] < 0 and encoded[-1] > 1


def test_linear_exposure_doubles_light_not_code_values():
    light = np.array([[.02, .18, .75], [-.1, 0, 1]], np.float32)
    encoded = linear_to_rec709(light)
    graded = apply_grade(encoded, Grade(linear_exposure=1))
    np.testing.assert_allclose(rec709_to_linear(graded), light * 2, atol=2e-6)
    assert not np.allclose(graded, encoded * 2)


def test_legacy_exposure_keeps_code_gain_and_headroom():
    x = np.array([[-.1, .5, .9]], np.float32)
    np.testing.assert_array_equal(apply_grade(x, Grade(exposure=1)), x * 2)


def test_signed_lift_gamma_gain():
    light = np.array([[-.1, .18, 1.2]], np.float32)
    g = Grade(lift=[.02, .1, 0], gamma=[2, 1, .5], gain=[1, 2, 1])
    result = rec709_to_linear(apply_grade(linear_to_rec709(light), g))
    shifted = (light + np.array(g.lift, np.float32)) * np.array(g.gain, np.float32)
    expected = np.sign(shifted) * np.abs(shifted) ** (1 / np.array(g.gamma, np.float32))
    np.testing.assert_allclose(result, expected, atol=2e-6)
    assert result[0, 0] < 0 and result[0, 2] > 1


def test_curves_extrapolate_and_allow_creative_nonmonotone_y():
    x = np.array([-.5, 0, .25, .5, 1, 2], np.float32)
    np.testing.assert_allclose(apply_curve(x, [[0, 0], [.5, .25], [1, 1]]), [-.25, 0, .125, .25, 1, 2.5])
    np.testing.assert_allclose(apply_curve(x, [[0, 1], [1, 0]]), 1 - x)
    for points in [[], [[0, 0]], [[0, 0], [0, 1]], [[1, 0], [0, 1]], [[0, 0], [1, float('nan')]]]:
        with pytest.raises(ValueError):
            apply_curve(x, points)


def test_master_then_channel_curve_order():
    x = np.array([[.25, .25, .25]], np.float32)
    g = Grade(master_curve=[[0, 0], [1, 2]], red_curve=[[0, .1], [1, 1.1]])
    np.testing.assert_allclose(apply_grade(x, g), [[.6, .5, .5]], atol=1e-7)


def write_cube(path, mapping=lambda r, g, b: (r, g, b), domain=''):
    lines = ['TITLE "Unit test"', 'LUT_3D_SIZE 2', domain]
    lines += [' '.join(map(str, mapping(r, g, b))) for b in (0, 1) for g in (0, 1) for r in (0, 1)]
    path.write_text('\n'.join(lines))
    return path


def test_cube_identity_interpolation_and_explicit_clamp(tmp_path):
    lut = load_cube(write_cube(tmp_path / 'identity.cube'))
    x = np.random.default_rng(3).uniform(-.2, 1.2, (10, 9, 3)).astype(np.float32)
    result = apply_lut(x, lut)
    np.testing.assert_allclose(result, np.clip(x, 0, 1), atol=2e-7)
    assert result.dtype == np.float32


def test_cube_axis_order_domain_and_unclipped_output(tmp_path):
    path = write_cube(tmp_path / 'map.cube', lambda r, g, b: (b * 2, r - 1, g), 'DOMAIN_MIN -1 -1 -1\nDOMAIN_MAX 1 1 1')
    x = np.array([[0, .5, -.5]], np.float32)
    np.testing.assert_allclose(apply_grade(x, Grade(lut_path=str(path))), [[.5, -.5, .75]], atol=1e-7)


@pytest.mark.parametrize('content', ['LUT_3D_SIZE 1', 'LUT_1D_SIZE 2\n0 0 0\n1 1 1', 'LUT_3D_SIZE 2\n0 0 0', 'LUT_3D_SIZE 66', 'LUT_3D_SIZE 2\nDOMAIN_MIN 1 1 1\nDOMAIN_MAX 0 0 0', 'LUT_3D_SIZE 2\nno 0 0'])
def test_cube_rejects_invalid_files(tmp_path, content):
    path = tmp_path / 'bad.cube'
    path.write_text(content)
    with pytest.raises(ValueError):
        load_cube(path)


def test_display_controls_reference_math_and_range():
    x = np.array([[-.1, .5, 1.2]], np.float32)
    np.testing.assert_allclose(apply_grade(x, Grade(contrast=2)), (x - .5) * 2 + .5)
    gray = apply_grade(x, Grade(saturation=0))
    np.testing.assert_allclose(gray, np.repeat((x @ np.array([.2126, .7152, .0722], np.float32))[:, None], 3, axis=1))
    np.testing.assert_allclose(apply_grade(x, Grade(temperature=1, tint=1)), x + [[.375, -.25, -.125]])
    neutral = np.array([[0, 0, 0], [1, 1, 1]], np.float32)
    np.testing.assert_allclose(apply_grade(neutral, Grade(shadows=-1, highlights=1)), [[-.5, -.5, -.5], [1.5, 1.5, 1.5]])


def test_gray_ramp_is_neutral_monotone_and_has_no_quantization():
    x = np.repeat(np.linspace(0, 1, 8192, dtype=np.float32)[:, None], 3, axis=1)
    result = apply_grade(x, Grade(linear_exposure=.5, gamma=[1.1]*3))
    np.testing.assert_array_equal(result[:, 0], result[:, 1])
    np.testing.assert_array_equal(result[:, 1], result[:, 2])
    # Rounded BT.709 inverse has one documented toe discontinuity.
    differences = np.diff(result[:, 0])
    toe_crossing = (x[:-1, 0] < .081) & (x[1:, 0] >= .081)
    assert np.all(differences[~toe_crossing] > 0)
    assert np.min(differences) > -.001
    assert len(np.unique(result[:, 0])) == len(x)


def test_input_validation():
    with pytest.raises(ValueError):
        apply_grade(np.ones((3, 3), dtype=np.uint8), Grade())
    with pytest.raises(ValueError):
        apply_grade(np.ones((3, 4), dtype=np.float32), Grade())
    with pytest.raises(ValueError):
        apply_grade(np.array([[np.nan, 0, 0]], np.float32), Grade())


def test_opposing_legacy_exposure_retains_out_of_range_information():
    x = np.array([[-.25, .7, 1.5]], np.float32)
    over = apply_grade(x, Grade(exposure=3))
    recovered = apply_grade(over, Grade(exposure=-3))
    np.testing.assert_array_equal(recovered, x)
    assert over.max() > 1 and over.min() < 0


def test_cube_rejects_nonfinite_and_extra_samples(tmp_path):
    path = write_cube(tmp_path / 'invalid.cube')
    original = path.read_text()
    path.write_text(original.replace('0 0 0', 'nan 0 0', 1))
    with pytest.raises(ValueError):
        load_cube(path)
    path.write_text(original + '\n0 0 0')
    with pytest.raises(ValueError):
        load_cube(path)


def test_lut_cache_refreshes_after_edit(tmp_path):
    path = write_cube(tmp_path / 'edit.cube')
    x = np.array([[.2, .4, .8]], np.float32)
    np.testing.assert_allclose(apply_lut(x, load_cube(path)), x, atol=1e-7)
    write_cube(path, lambda r, g, b: (b, g, r))
    np.testing.assert_allclose(apply_lut(x, load_cube(path)), x[..., ::-1], atol=1e-7)


def test_lut_cache_refreshes_with_preserved_timestamps(tmp_path):
    import os
    path = write_cube(tmp_path / 'same-stat.cube')
    x = np.array([[.2, .4, .8]], np.float32)
    first = path.stat()
    np.testing.assert_allclose(apply_lut(x, load_cube(path)), x, atol=1e-7)
    write_cube(path, lambda r, g, b: (b, g, r))
    os.utime(path, ns=(first.st_atime_ns, first.st_mtime_ns))
    assert path.stat().st_size == first.st_size
    np.testing.assert_allclose(apply_lut(x, load_cube(path)), x[..., ::-1], atol=1e-7)

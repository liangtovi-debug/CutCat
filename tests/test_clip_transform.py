"""Schema and real FFmpeg decoded-pixel checks for bounded clip geometry."""
from dataclasses import asdict
import subprocess

import numpy as np
import pytest

from lumen.clip_transform import ClipTransform, is_identity, transform_filters, validate_transform
from lumen.media import ffmpeg_available, ffmpeg_binary


def test_identity_and_strict_roundtrip():
    settings = ClipTransform()
    assert is_identity(settings)
    assert settings.is_identity()
    assert transform_filters(settings, 1920, 1080) == []
    assert ClipTransform.from_dict(settings.to_dict()) == settings
    assert ClipTransform.from_dict(asdict(ClipTransform(x=16384, y=-16384, scale=.1,
                                                     rotation=-180, mirror_x=True)))


@pytest.mark.parametrize('field,value', [
    ('x', 16385), ('y', -16385), ('scale', 0), ('scale', 4.01),
    ('rotation', 181), ('rotation', -181), ('x', float('inf')),
    ('scale', float('nan')), ('rotation', '90'), ('x', True),
    ('mirror_x', 1), ('mirror_y', 'false'), ('mirror_x', None),
])
def test_rejects_invalid_values(field, value):
    with pytest.raises(ValueError):
        ClipTransform(**{field: value})
    settings = ClipTransform()
    setattr(settings, field, value)
    with pytest.raises(ValueError):
        validate_transform(settings)
    with pytest.raises(ValueError):
        settings.to_dict()
    with pytest.raises(ValueError):
        transform_filters(settings, 64, 48)


@pytest.mark.parametrize('data', [{}, None, [], {'x': 1},
                                 {**asdict(ClipTransform()), 'extra': 0}])
def test_rejects_incomplete_or_unknown_schema(data):
    with pytest.raises(ValueError):
        ClipTransform.from_dict(data)


@pytest.mark.parametrize('width,height', [(0, 32), (32, -1), (1.5, 32),
                                         (True, 32), (16385, 32)])
def test_rejects_bad_canvas(width, height):
    with pytest.raises(ValueError):
        transform_filters(ClipTransform(), width, height)


def run_pixels(rgb, settings):
    if not ffmpeg_available():
        pytest.skip('FFmpeg required for decoded-pixel transform checks')
    height, width, _ = rgb.shape
    filters = transform_filters(settings, width, height)
    command = [ffmpeg_binary(), '-v', 'error', '-filter_threads', '1', '-f', 'rawvideo',
               '-pixel_format', 'rgb24', '-video_size', f'{width}x{height}', '-i', 'pipe:0']
    if filters:
        command += ['-vf', ','.join(filters)]
    command += ['-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']
    result = subprocess.run(command, input=rgb.tobytes(), capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(rgb.shape)


def fixture():
    image = np.zeros((24, 32, 3), dtype=np.uint8)
    image[:12, :16] = [240, 10, 20]
    image[:12, 16:] = [20, 230, 10]
    image[12:, :16] = [10, 20, 220]
    image[12:, 16:] = [220, 210, 20]
    return image


def test_identity_pixels_unchanged():
    image = fixture()
    np.testing.assert_array_equal(run_pixels(image, ClipTransform()), image)


@pytest.mark.parametrize('settings,axis', [(ClipTransform(mirror_x=True), 1),
                                          (ClipTransform(mirror_y=True), 0)])
def test_mirrors_include_last_row_and_column(settings, axis):
    image = fixture()
    # Distinct edge values expose the geq bilinear edge-clamping trap.
    image[-1, :] = [70, 80, 90]
    image[:, -1] = [120, 140, 160]
    np.testing.assert_allclose(run_pixels(image, settings), np.flip(image, axis), atol=1)


def test_translation_is_clipped_black_not_edge_smeared():
    image = fixture()
    expected = np.zeros_like(image)
    expected[3:, 5:] = image[:-3, :-5]
    np.testing.assert_allclose(run_pixels(image, ClipTransform(x=5, y=3)), expected, atol=1)
    assert not run_pixels(image, ClipTransform(x=-16384, y=16384)).any()


def test_rotation_clockwise_and_centered():
    image = fixture()[:, :24]
    np.testing.assert_allclose(run_pixels(image, ClipTransform(rotation=90)),
                               np.rot90(image, -1), atol=1)
    np.testing.assert_allclose(run_pixels(image, ClipTransform(rotation=180)),
                               np.rot90(image, 2), atol=1)


def test_scale_down_black_canvas_and_scale_up_fixed_dimensions():
    image = fixture()
    smaller = run_pixels(image, ClipTransform(scale=.5))
    assert not smaller[:6].any() and not smaller[-6:].any()
    assert not smaller[:, :8].any() and not smaller[:, -8:].any()
    np.testing.assert_allclose(smaller[7, 9], image[2, 2], atol=1)
    larger = run_pixels(image, ClipTransform(scale=4))
    assert larger.shape == image.shape
    np.testing.assert_allclose(larger[0, 0], image[10, 12], atol=1)


def test_combined_transform_inverse_mapping():
    image = fixture()[:, :24]
    expected = np.zeros_like(image)
    rotated = np.rot90(np.flip(image, 1), -1)
    expected[:-2, 3:] = rotated[2:, :-3]
    actual = run_pixels(image, ClipTransform(x=3, y=-2, rotation=90, mirror_x=True))
    np.testing.assert_allclose(actual, expected, atol=1)


def test_non_quarter_rotation_has_opaque_black_corners():
    image = np.full((32, 32, 3), 200, dtype=np.uint8)
    actual = run_pixels(image, ClipTransform(rotation=45))
    assert not actual[0, 0].any()
    assert not actual[-1, -1].any()
    np.testing.assert_allclose(actual[16, 16], image[16, 16], atol=1)


def test_fractional_translation_uses_bilinear_sampling():
    image = np.zeros((16, 16, 3), dtype=np.uint8)
    image[:, 8:] = 200
    actual = run_pixels(image, ClipTransform(x=.5))
    np.testing.assert_allclose(actual[8, 8], [100, 100, 100], atol=1)
    assert not actual[:, 0].any()


def test_scale_limits_use_only_canvas_sized_buffers():
    for scale in (.1, 4):
        filters = transform_filters(ClipTransform(scale=scale), 1920, 1080)
        assert 'pad=1921:1081:0:0:color=black' in filters
        assert filters[-1] == 'crop=1920:1080:0:0'
        assert not any(item.startswith('scale=') for item in filters)
    result = run_pixels(fixture(), ClipTransform(scale=.1, rotation=37, x=.5))
    assert result.shape == fixture().shape
    assert not result[0].any()


def test_native_exact_transform_fast_paths():
    assert transform_filters(ClipTransform(mirror_x=True), 64, 48) == ['format=gbrp16le', 'hflip']
    assert transform_filters(ClipTransform(mirror_y=True), 64, 48) == ['format=gbrp16le', 'vflip']
    assert transform_filters(ClipTransform(rotation=180), 64, 48) == ['format=gbrp16le', 'hflip', 'vflip']
    assert transform_filters(ClipTransform(rotation=90), 48, 48) == ['format=gbrp16le', 'transpose=clock']
    assert transform_filters(ClipTransform(rotation=-90), 48, 48) == ['format=gbrp16le', 'transpose=cclock']
    assert any(f.startswith('geq=') for f in transform_filters(ClipTransform(rotation=90), 64, 48))
    image = fixture()[:, :24]
    np.testing.assert_allclose(run_pixels(image, ClipTransform(rotation=-90, mirror_y=True)),
                               np.rot90(np.flip(image, 0)), atol=1)
    np.testing.assert_allclose(run_pixels(image, ClipTransform(rotation=-180, mirror_x=True)),
                               np.flip(image, 0), atol=1)

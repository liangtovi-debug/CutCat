"""Independent geometric and precision oracles for RGB16 vignette.

The scalar reference comes from FFmpeg's optical cos^4 falloff, not from the
production expression builder or capability probe. Its signed integer offsets
preserve the legacy default center, including odd and one-pixel dimensions.
RGB16 intentionally does not copy legacy angle/mask float32 or 8-bit rounding.
Sources: FFmpeg n7.1 libavfilter/vf_vignette.c and vf_geq.c in the official
https://github.com/FFmpeg/FFmpeg repository. All inputs are synthetic and local.
"""
from __future__ import annotations

import math
import subprocess

import numpy as np
import pytest

from lumen import highbit_effects as high
from lumen.media import ffmpeg_available, ffmpeg_binary
from lumen.model import ClipEffect


needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def scalar_vignette(image, amount):
    """Ideal encoded-RGB multiplier with independently evaluated geometry."""
    height, width, _ = image.shape
    out = np.empty_like(image, dtype=np.uint16)
    denominator = math.sqrt((width / 2) ** 2 + (height / 2) ** 2)
    for y in range(height):
        dy = math.trunc(y - height / 2)
        for x in range(width):
            dx = math.trunc(x - width / 2)
            distance = math.sqrt(dx * dx + dy * dy) / denominator
            cosine = math.cos(amount * math.pi * distance / 2)
            gain = 0 if distance > 1 else cosine ** 4
            for channel in range(3):
                out[y, x, channel] = math.floor(int(image[y, x, channel]) * gain)
    return out


def apply_rgb16(image, effects):
    """Reject implicit format conversion and decode native planar GBR16."""
    image = np.asarray(image, dtype='<u2')
    height, width, _ = image.shape
    planar = np.moveaxis(image[..., [1, 2, 0]], -1, 0).astype('<u2')
    filters = high.highbit_effect_filters(effects)
    command = [ffmpeg_binary(), '-hide_banner', '-v', 'error', '-nostdin',
               '-noauto_conversion_filters', '-filter_threads', '1',
               '-f', 'rawvideo', '-pix_fmt', 'gbrp16le',
               '-s', f'{width}x{height}', '-i', 'pipe:0',
               '-vf', ','.join(filters) or 'null', '-frames:v', '1',
               '-c:v', 'rawvideo', '-threads', '1', '-pix_fmt', 'gbrp16le',
               '-f', 'rawvideo', 'pipe:1']
    result = subprocess.run(command, input=planar.tobytes(), capture_output=True,
                            check=True, timeout=30)
    assert len(result.stdout) == height * width * 6
    planes = np.frombuffer(result.stdout, '<u2').reshape(3, height, width)
    return np.moveaxis(planes[[2, 0, 1]], 0, -1)


def edge_fixture(width, height):
    """Distinct neighboring samples and channels, including every final edge."""
    y, x, channel = np.indices((height, width, 3))
    image = ((x * 1777 + y * 2213 + channel * 13001 + 10001) % 65536).astype('<u2')
    image[-1, -1] = [65535, 32769, 1]
    return image


@needs_ffmpeg
@pytest.mark.parametrize('width,height', [(1, 1), (1, 9), (9, 1), (2, 9),
                                         (9, 2), (15, 11), (16, 12)])
@pytest.mark.parametrize('amount', [.001, .125, .5, 1])
def test_native_rgb16_all_pixels_match_independent_scalar_oracle(width, height, amount):
    image = edge_fixture(width, height)
    result = apply_rgb16(image, [ClipEffect('v', 'vignette', amount)])
    expected = scalar_vignette(image, amount)
    error = np.abs(result.astype(int) - expected.astype(int))
    # One RGB16 code allows different libm implementations and expression
    # literal formatting, not an 8-bit bottleneck or changed center/sampling.
    assert error.max() <= 1
    assert error[-1, :, :].max() <= 1
    assert error[:, -1, :].max() <= 1
    # The historical int offset gives an exact unity center on odd canvases
    # too. Identity samples must not lose a code or borrow a neighboring pixel.
    center = (height // 2, width // 2)
    np.testing.assert_array_equal(result[center], image[center])


@needs_ffmpeg
def test_repeated_vignettes_match_sequential_scalar_rounding():
    image = edge_fixture(37, 23)
    actual = apply_rgb16(image, [ClipEffect('first', 'vignette', .125),
                                 ClipEffect('second', 'vignette', .75)])
    expected = scalar_vignette(scalar_vignette(image, .125), .75)
    assert np.abs(actual.astype(int) - expected.astype(int)).max() <= 2


@needs_ffmpeg
def test_precision_control_rejects_hidden_eight_bit_input_or_output():
    image = np.full((48, 64, 3), 32778, dtype='<u2')
    image[:, 32:, :] = 32958
    coarse = (np.rint(image.astype(float) / 257) * 257).astype('<u2')
    assert np.unique(coarse).tolist() == [32896]
    effects = [ClipEffect('edge', 'vignette', .5)]
    fine_result = apply_rgb16(image, effects)
    coarse_result = apply_rgb16(coarse, effects)
    expected = scalar_vignette(image, .5)
    assert np.abs(fine_result.astype(int) - expected.astype(int)).max() <= 1
    # The two positions have exactly equal radius but distinct source low bits.
    left, right = (24, 31, 0), (24, 33, 0)
    assert int(fine_result[right]) - int(fine_result[left]) >= 175
    assert coarse_result[right] == coarse_result[left]
    for negative in (coarse_result,
                     np.rint(fine_result.astype(float) / 257) * 257):
        assert np.abs(negative.astype(int) - expected.astype(int)).max() > 50


@needs_ffmpeg
def test_dark_rgb16_one_code_channel_steps_survive_at_unity_center():
    image = np.full((8, 10, 3), 1, dtype='<u2')
    image[4, 5] = [10001, 10002, 10003]
    actual = apply_rgb16(image, [ClipEffect('maximum', 'vignette', 1)])
    np.testing.assert_array_equal(actual[4, 5], [10001, 10002, 10003])
    expected = scalar_vignette(image, 1)
    assert np.abs(actual.astype(int) - expected.astype(int)).max() <= 1


@needs_ffmpeg
@pytest.mark.parametrize('effect', [ClipEffect('zero', 'vignette', 0),
                                    ClipEffect('disabled', 'vignette', 1, False)])
def test_bypass_preserves_every_rgb16_code_including_final_edges(effect):
    image = edge_fixture(17, 9)
    assert high.highbit_effect_filters([effect]) == []
    np.testing.assert_array_equal(apply_rgb16(image, [effect]), image)


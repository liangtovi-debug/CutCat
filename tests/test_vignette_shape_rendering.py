"""Actual RGB8/RGB16 pixels against an independent shaped-vignette oracle.

All fixtures are synthetic. The reference never calls the production geometry,
expression builder or capability fixture; correct tags cannot hide an 8-bit path.
"""
from copy import deepcopy
import math
import subprocess

import numpy as np
import pytest

from lumen import highbit_effects as high
from lumen.media import ffmpeg_available, ffmpeg_binary
from lumen.model import ClipEffect
from lumen.overlay_render import OverlayRenderError, effect_filters
from test_highbit_effects import independent_blur, scene


needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def effect(amount=.65, radius=.75, feather=1.5, *, enabled=True, id='shape'):
    return ClipEffect(id, 'vignette', amount, enabled, radius=radius, feather=feather)


def oracle(image, amount, radius, feather):
    """Scalar normalized radial profile with integer sample-boundary truncation."""
    h, w, channels = image.shape
    out = np.empty_like(image)
    diagonal = math.sqrt(w * w + h * h) * .5
    for y in range(h):
        for x in range(w):
            dx, dy = math.trunc(x - w * .5), math.trunc(y - h * .5)
            distance = math.sqrt(dx * dx + dy * dy) / diagonal
            profile = min(1., math.pow(distance / radius, 1. / feather))
            attenuation = math.cos(amount * profile * math.pi * .5) ** 4
            for c in range(channels):
                out[y, x, c] = math.floor(int(image[y, x, c]) * attenuation)
    return out


def apply(image, effects, *, bits=16):
    h, w, _ = image.shape
    dtype, pixel_format = ('<u2', 'gbrp16le') if bits == 16 else ('u1', 'gbrp')
    planar = np.moveaxis(image[..., [1, 2, 0]], -1, 0).astype(dtype)
    chain = high.highbit_effect_filters(effects) if bits == 16 else effect_filters(effects)
    args = [ffmpeg_binary(), '-hide_banner', '-v', 'error', '-nostdin']
    if any('gblur=' in item for item in chain):
        args += ['-cpuflags', '0']
    args += ['-noauto_conversion_filters', '-filter_threads', '1',
             '-f', 'rawvideo', '-pix_fmt', pixel_format, '-s', f'{w}x{h}', '-i', 'pipe:0',
             '-vf', ','.join(chain) or 'null', '-frames:v', '1', '-c:v', 'rawvideo',
             '-threads', '1', '-pix_fmt', pixel_format, '-f', 'rawvideo', 'pipe:1']
    result = subprocess.run(args, input=planar.tobytes(), capture_output=True,
                            check=True, timeout=30)
    planes = np.frombuffer(result.stdout, dtype).reshape(3, h, w)
    return np.moveaxis(planes[[2, 0, 1]], 0, -1)


def fixture(w, h, bits):
    y, x, c = np.indices((h, w, 3))
    image = ((10001 + 1777 * x + 2213 * y + 13001 * c) % 65536).astype('<u2')
    image[-1, -1] = [65535, 32769, 1]
    return image if bits == 16 else (image // 257).astype('u1')


@needs_ffmpeg
@pytest.mark.parametrize('bits', [8, 16])
@pytest.mark.parametrize('w,h', [(1, 1), (1, 9), (9, 1), (3, 7), (15, 11), (16, 12)])
@pytest.mark.parametrize('amount,radius,feather', [
    (.001, .25, .25), (.5, .75, 1.5), (1, .25, 2), (.65, 2, .25), (.8, 1, 2), (.8, 2, 1)])
def test_each_native_sample_matches_independent_oracle(bits, w, h, amount, radius, feather):
    image = fixture(w, h, bits)
    before = image.copy()
    actual = apply(image, [effect(amount, radius, feather)], bits=bits)
    expected = oracle(image, amount, radius, feather)
    error = np.abs(actual.astype(int) - expected.astype(int))
    # One code accommodates platform libm; exact center and edge sampling still
    # reject interpolation and accidental lower bit depth at the RGB16 boundary.
    assert error.max() <= 1
    np.testing.assert_array_equal(actual[h // 2, w // 2], image[h // 2, w // 2])
    np.testing.assert_array_equal(image, before)


def test_exact_legacy_graphs_remain_unchanged_at_default_shape():
    legacy = ClipEffect('v', 'vignette', .5)
    explicit = effect(.5, 1., 1.)
    assert effect_filters([legacy]) == effect_filters([explicit]) == [
        'vignette=angle=0.785398163397:mode=forward:eval=init:dither=0']
    gain = 'pow(cos(0.78539816339744828*hypot(trunc(X-W/2),trunc(Y-H/2))/hypot(W/2,H/2)),4)'
    expected = "geq=" + ':'.join(f"{c}='{c}(X,Y)*{gain}'" for c in 'rgb') + ':interpolation=nearest'
    assert high.highbit_effect_filters([legacy]) == high.highbit_effect_filters([explicit]) == [
        'format=gbrp16le', expected, 'format=gbrp16le']


def test_nondefault_graphs_are_explicit_rgb_and_dimension_dynamic():
    e = effect()
    before = deepcopy(e)
    low = effect_filters([e])
    native = high.highbit_effect_filters([e])
    assert low[0] == low[-1] == 'format=gbrp'
    assert native[0] == native[-1] == 'format=gbrp16le'
    assert low[1] == native[1] and low[1].startswith('geq=')
    assert 'min(1,pow(' in low[1] and 'W/2' in low[1] and 'H/2' in low[1]
    assert 'interpolation=nearest' in low[1]
    assert e == before


@pytest.mark.parametrize('field', ['radius', 'feather'])
@pytest.mark.parametrize('value', [False, True, None, '1', float('nan'), float('inf'),
                                    -float('inf'), 0, .249999, 2.000001, 10**1000],
                         ids=['false', 'true', 'none', 'string', 'nan', 'inf',
                              'negative-inf', 'zero', 'too-small', 'too-large', 'huge-int'])
@pytest.mark.parametrize('enabled,amount', [(True, .5), (False, .5), (True, 0)])
def test_invalid_shape_rejected_before_bypass_in_both_paths(field, value, enabled, amount):
    e = effect(amount, enabled=enabled)
    setattr(e, field, value)
    for builder in (effect_filters, high.highbit_effect_filters):
        with pytest.raises((ValueError, OverlayRenderError), match='Invalid clip effect'):
            builder([e])


@pytest.mark.parametrize('enabled,amount', [(True, 2), (False, 2), (True, 0)])
@pytest.mark.parametrize('field', ['radius', 'feather'])
def test_blur_cannot_silently_accept_nondefault_shape(field, enabled, amount):
    e = ClipEffect('blur', 'blur', amount, enabled)
    setattr(e, field, 1.5)
    for builder in (effect_filters, high.highbit_effect_filters):
        with pytest.raises((ValueError, OverlayRenderError), match='blur radius and feather'):
            builder([e])


@needs_ffmpeg
@pytest.mark.parametrize('bits', [8, 16])
@pytest.mark.parametrize('enabled,amount', [(True, 0), (False, 1)])
def test_valid_nondefault_bypass_has_no_conversion_and_is_bit_exact(bits, enabled, amount):
    image = fixture(17, 9, bits)
    e = effect(amount, .25, 2, enabled=enabled)
    assert effect_filters([e]) == high.highbit_effect_filters([e]) == []
    np.testing.assert_array_equal(apply(image, [e], bits=bits), image)


@needs_ffmpeg
def test_native_low_bits_survive_both_sub_eight_bit_plateaus_and_one_code_ramp():
    image = np.full((48, 64, 3), 32778, dtype='<u2')
    image[:, 32:] = 32958
    e = effect(.5, 1.5, .75)
    fine = apply(image, [e])
    expected = oracle(image, e.amount, e.radius, e.feather)
    np.testing.assert_allclose(fine, expected, atol=1, rtol=0)
    assert int(fine[24, 33, 0]) - int(fine[24, 31, 0]) >= 175
    coarse = (np.rint(image.astype(float) / 257) * 257).astype('<u2')
    control = apply(coarse, [e])
    assert control[24, 33, 0] == control[24, 31, 0]
    assert np.abs(control.astype(int) - expected).max() > 50
    ramp = np.broadcast_to((10001 + np.arange(2048, dtype=np.uint16))[None, :, None],
                           (16, 2048, 3)).copy()
    result = apply(ramp, [effect(.1, 2, .5)])
    assert np.unique(result[8, :, 0]).size > 1900
    np.testing.assert_allclose(result, oracle(ramp, .1, 2, .5), atol=1, rtol=0)


@needs_ffmpeg
def test_controls_are_monotonic_and_clamp_prevents_cosine_wraparound():
    image = np.full((36, 64, 3), 50001, dtype='<u2')
    small = apply(image, [effect(.8, .25, 1)])
    large = apply(image, [effect(.8, 2, 1)])
    assert np.all(large >= small) and np.mean(large.astype(int) - small) > 10000
    soft = apply(image, [effect(.8, 2, .25)])
    broad = apply(image, [effect(.8, 2, 2)])
    assert np.all(soft >= broad) and np.mean(soft.astype(int) - broad) > 10000
    # All locations beyond the radius share the same darkest clamped response;
    # an unclamped cosine would wrap around and re-brighten the outer corners.
    assert small[0, 0, 0] == small[0, 63, 0] == small[0, 32, 0]
    assert small[0, 0, 0] < 500


@needs_ffmpeg
def test_mixed_order_and_repeated_vignettes_use_sequential_integer_boundaries():
    image = fixture(128, 96, 16)
    one, two = effect(.7, .75, 1.5, id='one'), effect(.4, 1.5, .5, id='two')
    blur = ClipEffect('blur', 'blur', 2)
    legacy = ClipEffect('legacy', 'vignette', .2)
    actual = apply(image, [one, blur, legacy, two])
    expected = oracle(image, one.amount, one.radius, one.feather)
    expected = np.rint(independent_blur(expected, 2)).astype('<u2')
    expected = oracle(expected, .2, 1, 1)
    expected = oracle(expected, two.amount, two.radius, two.feather)
    np.testing.assert_allclose(actual[24:-24, 24:-24], expected[24:-24, 24:-24], atol=2, rtol=0)
    reverse = apply(image, [two, legacy, blur, one])
    assert np.max(abs(reverse.astype(int) - actual)) > 100
    low = fixture(15, 11, 8)
    np.testing.assert_allclose(apply(low, [one, two], bits=8),
        oracle(oracle(low, one.amount, one.radius, one.feather),
               two.amount, two.radius, two.feather), atol=2, rtol=0)


@needs_ffmpeg
@pytest.mark.parametrize('w,h', [(16384, 2), (2, 16384)])
def test_maximum_supported_dimension_uses_bounded_narrow_fixture(w, h):
    # Exercise each maximum supported side without allocating a 16K square.
    # The graph's W/H must track each input, not a preview-size cached mask.
    p = scene(); p.width, p.height = w, h
    p.clips[0].effects = [effect(.7, .75, 1.5)]
    p.validate()
    image = fixture(w, h, 16)
    actual = apply(image, p.clips[0].effects)
    np.testing.assert_allclose(actual, oracle(image, .7, .75, 1.5), atol=1, rtol=0)
    p.width = 16385
    with pytest.raises(ValueError, match='width'):
        p.validate()

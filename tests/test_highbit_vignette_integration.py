"""Independent RGB16 and decoded ProRes acceptance for radial vignette.

All footage and artwork are synthetic. The scalar cos^4 oracle never calls
production effect/grade/probe math; deliberately eight-bit sources must fail
precision checks even when delivered with correct ten-bit output tags.
"""
from copy import deepcopy
import math
from pathlib import Path
import threading

import numpy as np
import pytest

from lumen import highbit_effects as high
from lumen.clip_transform import ClipTransform
from lumen.media import ffmpeg_available
from lumen.model import Caption, CaptionStyle, ClipEffect, Grade, OverlayKeyframe
from lumen.render import RenderCancelled, RenderEngine, RenderError
from test_color_highbit_reference import (
    project, rec709_yuv10, rgb_source, video_metadata, yuv422,
)
from test_highbit_caption_reference import latin_font
from test_highbit_effects import compiled_pixels, filtered_rgb, independent_blur
from test_overlay_render import sticker


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
PROFILE = 'prores_422_hq_10bit'


@pytest.fixture
def engine():
    renderer = RenderEngine(timeout=60)
    try:
        yield renderer
    finally:
        renderer.close()


def vignette(amount):
    return ClipEffect('vignette', 'vignette', amount)


def scalar_vignette(image, amount):
    """Legacy radial geometry with RGB16 truncation at the effect boundary.

    The radius is the half diagonal and angle is amount*pi/2. Integer offsets
    deliberately truncate toward zero, including odd and one-pixel canvases.
    """
    image = np.asarray(image)
    height, width, _ = image.shape
    out = np.empty(image.shape, dtype=np.uint16)
    radius = math.sqrt(width * width + height * height) / 2
    for y in range(height):
        for x in range(width):
            dx, dy = int(x - width / 2), int(y - height / 2)
            theta = amount * math.pi * math.sqrt(dx * dx + dy * dy) / (2 * radius)
            gain = math.cos(theta) ** 4
            for channel in range(3):
                out[y, x, channel] = int(float(image[y, x, channel]) * gain)
    return out


def linear_exposure(image, stops):
    """Independent BT.709 decode, linear exposure, and re-encode."""
    encoded = np.asarray(image, dtype=float) / 65535
    linear = np.where(encoded < .081, encoded / 4.5,
                      ((encoded + .099) / 1.099) ** (1 / .45)) * 2 ** stops
    encoded = np.where(linear < .018, linear * 4.5, 1.099 * linear ** .45 - .099)
    return np.rint(np.clip(encoded, 0, 1) * 65535).astype('<u2')


@pytest.mark.parametrize('width,height,amount', [
    (2, 2, 1), (1, 9, .65), (3, 7, .65), (14, 10, .65),
    (128, 64, 1e-12), (128, 64, .25), (128, 64, 1),
])
def test_native_rgb16_full_frame_matches_independent_scalar_geometry(width, height, amount):
    positions = np.arange(width * height, dtype=np.uint16).reshape(height, width)
    image = np.stack([10001 + positions, 29002 + positions, 48003 + positions], -1)
    before = image.copy()
    actual = filtered_rgb(image, high.highbit_effect_filters([vignette(amount)]))
    expected = scalar_vignette(image, amount)
    np.testing.assert_allclose(actual, expected, atol=1, rtol=0)
    np.testing.assert_array_equal(image, before)
    if width > 2 and amount >= .25:
        assert actual[0, 0, 0] < image[0, 0, 0]
        assert actual[height // 2, width // 2, 0] > actual[0, 0, 0]


def test_native_vignette_keeps_one_code_ramp_and_sub_eight_bit_samples():
    width, height = 2048, 32
    image = np.broadcast_to((10001 + np.arange(width, dtype=np.uint16))[None, :, None],
                            (height, width, 3)).copy()
    chain = high.highbit_effect_filters([vignette(.2)])
    actual = filtered_rgb(image, chain)
    expected = scalar_vignette(image, .2)
    np.testing.assert_allclose(actual, expected, atol=1, rtol=0)
    assert np.unique(actual[16, :, 0]).size > 1300
    coarse = (np.rint(image.astype(float) / 257) * 257).astype('<u2')
    control = filtered_rgb(coarse, chain)
    assert np.mean(abs(control.astype(float) - expected)) > 50
    assert np.mean(abs(actual.astype(float) - expected)) < .1


def test_decoded_prores_preserves_vignette_precision_not_only_output_tags(tmp_path, engine):
    width, height = 2048, 64
    image = np.broadcast_to(np.rint(np.linspace(0, 65535, width))[None, :, None],
                            (height, width, 3)).astype('<u2').copy()
    image[32:, :width // 2] = 32778
    image[32:, width // 2:] = 32958
    p = project(rgb_source(tmp_path, image))
    p.clips[0].effects = [vignette(.2)]
    before = deepcopy(p.to_dict())
    output = engine.render(p, tmp_path / 'vignette.mov', export_profile=PROFILE)
    metadata = video_metadata(output)
    assert (metadata['codec_name'], metadata['profile'], metadata['pix_fmt']) == (
        'prores', 'HQ', 'yuv422p10le')
    assert metadata['color_space'] == metadata['color_transfer'] == metadata['color_primaries'] == 'bt709'
    y, cb, cr = yuv422(output, width, height)
    expected = 64 + 876 * scalar_vignette(image, .2)[..., 0].astype(float) / 65535
    error = np.mean(abs(y[0, 8:24] - expected[8:24]))
    assert error < .65
    assert np.unique(y[0, 16]).size > 650
    # Symmetric x and W-x share exactly the same radial attenuation.
    left = np.arange(128, 896)
    right = width - left
    assert np.mean(y[0, 40:56, right] - y[0, 40:56, left]) > 1.75
    np.testing.assert_allclose(cb, 512, atol=1, rtol=0)
    np.testing.assert_allclose(cr, 512, atol=1, rtol=0)
    coarse = (np.rint(image.astype(float) / 257) * 257).astype('<u2')
    q = project(rgb_source(tmp_path, coarse, name='deliberately-eight-bit'))
    q.clips[0].effects = [vignette(.2)]
    control = engine.render(q, tmp_path / 'coarse.mov', export_profile=PROFILE)
    coarse_y, _, _ = yuv422(control, width, height)
    coarse_error = np.mean(abs(coarse_y[0, 8:24] - expected[8:24]))
    assert error < coarse_error * .7
    assert abs(np.mean(coarse_y[0, 40:56, right] - coarse_y[0, 40:56, left])) < .5
    assert p.to_dict() == before


def test_decoded_pixels_apply_grade_then_vignette_then_geometry(tmp_path, engine):
    width, height = 128, 96
    row, col = np.indices((height, width))
    image = np.stack([9001 + col * 100, 14003 + row * 100,
                      18007 + (col + row) * 35], -1).astype('<u2')
    p = project(rgb_source(tmp_path, image), grade=Grade(linear_exposure=1))
    p.clips[0].effects = [vignette(.8)]
    p.clips[0].transform = ClipTransform(x=12)
    before = deepcopy(p.to_dict())
    output = engine.render(p, tmp_path / 'ordered.mov', export_profile=PROFILE)
    y, _, _ = yuv422(output, width, height)
    graded = linear_exposure(image, 1)
    expected = rec709_yuv10(scalar_vignette(graded, .8).astype(float) / 65535)[..., 0]
    np.testing.assert_allclose(y[0, 8:-8, 20:-8], expected[8:-8, 8:-20], atol=2, rtol=0)
    np.testing.assert_allclose(y[0, :, :8], 64, atol=1, rtol=0)
    # Both plausible wrong orders must fail the numeric fixture, not just a
    # string-order assertion in the production graph.
    wrong_grade = rec709_yuv10(linear_exposure(scalar_vignette(image, .8), 1) / 65535)[..., 0]
    assert np.mean(abs(wrong_grade - expected)) > 10
    shifted = np.zeros_like(graded)
    shifted[:, 12:] = graded[:, :-12]
    wrong_geometry = rec709_yuv10(scalar_vignette(shifted, .8) / 65535)[..., 0]
    assert np.mean(abs(wrong_geometry[8:-8, 20:-8] - expected[8:-8, 8:-20])) > 10
    assert p.to_dict() == before


def test_multiple_blurs_and_vignettes_apply_every_operator_in_list_order():
    row, col = np.indices((128, 192))
    image = np.stack([24001 + 15000 * ((col // 8 + row // 8) % 2),
                      19003 + col * 80, 33007 - row * 90], -1).astype('<u2')
    entries = [vignette(.7), ClipEffect('first-blur', 'blur', 3.25),
               ClipEffect('second-vignette', 'vignette', .4),
               ClipEffect('second-blur', 'blur', .75)]
    actual = filtered_rgb(image, high.highbit_effect_filters(entries))
    expected = scalar_vignette(image, .7)
    expected = np.rint(independent_blur(expected, 3.25)).astype('<u2')
    expected = scalar_vignette(expected, .4)
    expected = np.rint(independent_blur(expected, .75))
    np.testing.assert_allclose(actual[24:-24, 24:-24], expected[24:-24, 24:-24], atol=2, rtol=0)
    reversed_pixels = filtered_rgb(image, high.highbit_effect_filters(entries[::-1]))
    assert np.max(abs(reversed_pixels[24:-24, 24:-24].astype(float) - actual[24:-24, 24:-24])) > 30
    missing = filtered_rgb(image, high.highbit_effect_filters(entries[:2]))
    assert np.mean(abs(missing.astype(float) - actual)) > 100


def test_captions_and_alpha_sticker_are_composed_after_vignette(tmp_path, engine, latin_font):
    width, height = 384, 160
    image = np.full((height, width, 3), 28001, dtype='<u2')
    p = project(rgb_source(tmp_path, image), frames=3)
    p.clips[0].effects = [vignette(.8)]
    baseline = compiled_pixels(engine, p)
    art, _ = sticker(tmp_path, start=1, duration=1, color=(255, 0, 0, 128),
                     keyframes=[OverlayKeyframe(0, 16, 24, 2, 1)])
    p.overlays = [art]
    p.caption_style = CaptionStyle(font_family=latin_font, font_size=32, margin_bottom=12,
                                   outline_width=0, safe_margin_percent=5)
    p.captions = [Caption('caption', 1, 1, 'SHARP')]
    before = deepcopy(p.to_dict())
    actual = compiled_pixels(engine, p)
    for plain, decorated in zip(baseline, actual):
        np.testing.assert_array_equal(decorated[[0, 2]], plain[[0, 2]])
        np.testing.assert_array_equal(decorated[1, :20], plain[1, :20])
    expected_red = rec709_yuv10([1, 0, 0])
    for index, (plain, decorated) in enumerate(zip(baseline, actual)):
        left, right = (16, 48) if index == 0 else (8, 24)
        expected = plain[1, 24:56, left:right] * (1 - 128 / 255) + expected_red[index] * 128 / 255
        np.testing.assert_allclose(decorated[1, 24:56, left:right], expected, atol=4, rtol=0)
    np.testing.assert_array_equal(actual[0][1, 24:56, 15], baseline[0][1, 24:56, 15])
    np.testing.assert_array_equal(actual[0][1, 24:56, 48], baseline[0][1, 24:56, 48])
    assert np.count_nonzero(actual[0][1, 96:] != baseline[0][1, 96:]) > 150
    # Independently bake just the picture. Identical later artwork proves
    # vignette never darkens the caption or the half-alpha red sticker itself.
    baked = project(rgb_source(tmp_path, scalar_vignette(image, .8), name='baked'), frames=3)
    baked.overlays, baked.captions = deepcopy(p.overlays), deepcopy(p.captions)
    baked.caption_style = deepcopy(p.caption_style)
    reference = compiled_pixels(engine, baked)
    for decorated, independent in zip(actual, reference):
        np.testing.assert_allclose(decorated, independent, atol=1, rtol=0)
    assert p.to_dict() == before


def test_vignette_edits_reuse_grade_cache_without_caching_effect_results(tmp_path, engine):
    image = np.full((64, 128, 3), (21001, 32003, 43007), dtype='<u2')
    p = project(rgb_source(tmp_path, image))
    original = compiled_pixels(engine, p)
    cache = Path(engine._cache_dir.name)
    files = sorted(cache.glob('*.mkv'))
    assert len(files) == 1
    snapshot = [(path, path.stat().st_mtime_ns, path.read_bytes()) for path in files]
    p.clips[0].effects = [vignette(.3)]
    mild = compiled_pixels(engine, p)
    p.clips[0].effects[0].amount = .9
    strong = compiled_pixels(engine, p)
    assert np.mean(abs(mild[0].astype(float) - original[0])) > 20
    assert np.mean(abs(strong[0].astype(float) - mild[0])) > 100
    p.clips[0].effects[0].enabled = False
    bypass = compiled_pixels(engine, p)
    for source, current in zip(original, bypass):
        np.testing.assert_array_equal(current, source)
    assert sorted(cache.glob('*.mkv')) == files
    for path, modified, contents in snapshot:
        assert path.stat().st_mtime_ns == modified and path.read_bytes() == contents
    assert not list(cache.glob('*.partial*'))


@pytest.mark.parametrize('failure', ['probe-error', 'probe-cancel'])
def test_failed_vignette_capability_precedes_caches_and_publication(tmp_path, engine, monkeypatch, latin_font, failure):
    p = project(rgb_source(tmp_path, np.full((64, 128, 3), 20001, dtype='<u2')))
    p.clips[0].effects = [vignette(.65)]
    p.overlays = [sticker(tmp_path, duration=1)[0]]
    p.captions = [Caption('caption', 0, 1, 'TEXT')]
    p.caption_style.font_family = latin_font
    snapshot = deepcopy(p.to_dict())
    cache = Path(engine._cache_dir.name)
    before = set(cache.rglob('*'))
    calls = []
    def fail(*args, **kwargs):
        calls.append(True)
        raise (RenderCancelled('Render cancelled.') if failure == 'probe-cancel'
               else RenderError(high.VIGNETTE_ERROR))
    def forbidden(*args, **kwargs):
        pytest.fail('Capability must be checked before generating cached picture or artwork')
    monkeypatch.setattr(high, '_run_vignette_probe', fail)
    monkeypatch.setattr(engine, '_ensure_highbit_encoder', lambda: None)
    monkeypatch.setattr(engine, '_graded_segment', forbidden)
    monkeypatch.setattr(engine, '_overlay_segment', forbidden)
    import lumen.highbit_captions as captions
    import lumen.highbit_stickers as stickers
    monkeypatch.setattr(captions, 'highbit_caption_filters', forbidden)
    monkeypatch.setattr(stickers, 'highbit_sticker_filters', forbidden)
    with pytest.raises(RenderCancelled if failure == 'probe-cancel' else RenderError):
        engine.render(p, tmp_path / 'failed.mov', export_profile=PROFILE)
    assert calls == [True]
    assert set(cache.rglob('*')) == before
    assert not (tmp_path / 'failed.mov').exists()
    assert not list(tmp_path.glob('.lumen-render-*'))
    assert p.to_dict() == snapshot


@pytest.mark.parametrize('failure', ['verification', 'cancel-before-publish'])
def test_vignette_export_failure_cleans_temporary_and_safe_retry_succeeds(tmp_path, engine, monkeypatch, failure):
    p = project(rgb_source(tmp_path, np.full((48, 64, 3), 24001, dtype='<u2')))
    p.clips[0].effects = [vignette(.65)]
    before = deepcopy(p.to_dict())
    cancel = threading.Event()
    verify = engine._verify_highbit_output
    verified = []
    def fail_after_encoding(path):
        verify(path)
        verified.append(Path(path))
        assert Path(path).stat().st_size > 0
        if failure == 'verification':
            raise RenderError('Synthetic final output verification failure.')
        cancel.set()
    monkeypatch.setattr(engine, '_verify_highbit_output', fail_after_encoding)
    target = tmp_path / 'final.mov'
    with pytest.raises(RenderCancelled if failure == 'cancel-before-publish' else RenderError):
        engine.render(p, target, cancel=cancel, export_profile=PROFILE)
    assert len(verified) == 1 and not verified[0].exists()
    assert not target.exists() and not list(tmp_path.glob('.lumen-render-*'))
    assert not list(Path(engine._cache_dir.name).glob('*.partial*'))
    assert p.to_dict() == before
    monkeypatch.setattr(engine, '_verify_highbit_output', verify)
    cancel.clear()
    assert engine.render(p, target, cancel=cancel, export_profile=PROFILE) == target
    assert video_metadata(target)['pix_fmt'] == 'yuv422p10le'
    assert not list(tmp_path.glob('.lumen-render-*'))

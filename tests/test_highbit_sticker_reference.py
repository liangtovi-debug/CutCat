"""Independent code-value oracles for high-depth static sticker composition.

The art layer intentionally remains Qt's premultiplied 8-bit RGBA raster.
The picture must not be quantized to eight bits. BT.709 Y'CbCr coefficients
below are an independent oracle, not the production color evaluator. These
tests document legacy gamma-encoded SDR composition, not true sRGB/ICC or
linear-light color management of artwork.
"""
from copy import deepcopy
from fractions import Fraction
import os

import numpy as np
from PySide6.QtGui import QImage
import pytest

from lumen.media import ffmpeg_available, probe
from lumen.model import Caption, CaptionStyle, Overlay, OverlayKeyframe, Project
from lumen.overlay_render import compose_frame
from lumen.render import RenderCancelled, RenderEngine, RenderError
from lumen.sticker_assets import load_sticker
from test_color_highbit_reference import ff, project, rgb_source, video_metadata, yuv422
from test_highbit_caption_reference import latin_font, rec709_codes, split422
from test_overlay_render import png, rgba, sticker


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
PROFILE = 'prores_422_hq_10bit'


@pytest.fixture
def engine():
    value = RenderEngine(timeout=60)
    try:
        yield value
    finally:
        value.close()


def array_sticker(tmp_path, pixels, *, name='art.png', duration=1, **changes):
    """Save exact straight RGBA PNG samples without an image-conversion oracle."""
    pixels = np.ascontiguousarray(pixels, dtype=np.uint8)
    height, width, _ = pixels.shape
    image = QImage(pixels.data, width, height, width * 4,
                   QImage.Format.Format_RGBA8888)
    path = tmp_path / name
    assert image.save(str(path), 'PNG')
    asset = load_sticker(path)
    return Overlay(name, str(path), asset.digest, 0, duration, **changes), asset


def compiled_pixels(engine, p):
    """Run the untouched production graph and inspect native pre-codec YUV.

    Map unused audio to a real null output instead of anullsink: the latter
    triggers a FFmpeg 7 filter-scheduler assertion for source-only fractional
    timelines. No video or audio graph stage is removed or replaced.
    """
    inputs, graph, _ = engine._compile(p, export_profile=PROFILE)
    raw = ff('-filter_complex_threads', '1', *inputs, '-filter_complex', graph,
             '-map', '[vout]', '-frames:v', p.duration_frames,
             '-r', f'{p.fps_num}/{p.fps_den}', '-fps_mode', 'cfr',
             '-c:v', 'rawvideo', '-threads', '1', '-pix_fmt', 'yuv422p10le',
             '-f', 'rawvideo', 'pipe:1', '-map', '[aout]', '-f', 'null', os.devnull)
    return split422(raw, p.width, p.height)


def direct_sticker_pixels(engine, p, source_planes):
    """Exercise the actual composition filters with native arbitrary YUV codes.

    There is deliberately no picture RGB conversion or codec in this oracle.
    Explicitly disallow hidden format conversions at the FFmpeg boundary.
    """
    from lumen.highbit_stickers import highbit_sticker_filters
    assets = engine._verify_overlay_sources(p)
    main_filter, layer_filter, overlay_filter = highbit_sticker_filters(
        p, assets, 60, None, RenderError, RenderCancelled)
    layer = engine._overlay_segment(p, assets)
    graph = (f'[0:v]{main_filter}[main];[1:v]{layer_filter}[art];'
             f'[main][art]{overlay_filter}[vout]')
    raw = b''.join(plane.astype('<u2').tobytes() for plane in source_planes)
    actual = ff('-noauto_conversion_filters', '-filter_complex_threads', '1',
                '-f', 'rawvideo', '-pix_fmt', 'yuv422p10le',
                '-s', f'{p.width}x{p.height}', '-r', f'{p.fps_num}/{p.fps_den}',
                '-i', 'pipe:0', '-i', layer, '-filter_complex', graph,
                '-map', '[vout]', '-frames:v', p.duration_frames,
                '-c:v', 'rawvideo', '-threads', '1', '-pix_fmt', 'yuv422p10le',
                '-f', 'rawvideo', 'pipe:1', data=raw * p.duration_frames)
    return split422(actual, p.width, p.height)


def test_all_ten_bit_yuv_codes_survive_outside_alpha_and_inactive_frames(
        tmp_path, engine):
    width, height = 1024, 64
    y = np.tile(np.arange(width, dtype='<u2'), (height, 1))
    u = np.tile(np.arange(width // 2, dtype='<u2'), (height, 1))
    u += ((np.arange(height, dtype=np.uint16) % 2) * 512)[:, None]
    v = 1023 - u
    overlay, _ = sticker(tmp_path, start=1, duration=1, color=(255, 0, 0, 128),
                         keyframes=[OverlayKeyframe(0, 400, 24, 2, 1)])
    tail = Overlay('extent', str(tmp_path / 'absent.png'), '0' * 64,
                   2, 1, enabled=False)
    p = Project(width=width, height=height, overlays=[overlay, tail])
    before = deepcopy(p.to_dict())
    actual = direct_sticker_pixels(engine, p, (y, u, v))
    for channel, (source, frames) in enumerate(zip((y, u, v), actual)):
        np.testing.assert_array_equal(frames[[0, 2]], np.stack([source, source]))
        np.testing.assert_array_equal(frames[1, :20], source[:20])
        untouched = np.ones(source.shape, dtype=bool)
        div = 1 if channel == 0 else 2
        untouched[24:56, 400 // div:432 // div] = False
        np.testing.assert_array_equal(frames[1][untouched], source[untouched])
        assert np.unique(frames[1, :20]).size == 1024
        assert np.count_nonzero(frames[1, :20] % 4) > 1000
    assert np.count_nonzero(actual[0][1] != y) > 800
    assert p.to_dict() == before
    # Deliberate eight-bit conversion cannot pass the same exact oracle.
    raw = b''.join(plane.astype('<u2').tobytes() for plane in (y, u, v))
    coarse = ff('-filter_threads', '1', '-f', 'rawvideo',
                '-pix_fmt', 'yuv422p10le', '-s', f'{width}x{height}',
                '-i', 'pipe:0', '-vf', 'format=yuv422p,format=yuv422p10le',
                '-frames:v', 1, '-pix_fmt', 'yuv422p10le',
                '-f', 'rawvideo', 'pipe:1', data=raw)
    for source, frames in zip((y, u, v), split422(coarse, width, height)):
        assert np.unique(frames[0, :20]).size == 256
        assert np.count_nonzero(frames[0, :20] != source[:20]) > 1000


@pytest.mark.parametrize('width,height', [(2, 2), (2, 16), (4, 2), (4, 16)])
@pytest.mark.parametrize('alpha', [0, 128, 255])
def test_smallest_canvases_preserve_identity_and_average_both_chroma_samples(
        tmp_path, engine, width, height, alpha):
    """The real cached BGRA chain must handle swscale's narrow-width corner.

    The final visible pair is exercised in both orientations. Before the
    padded area-reduction fix, width two selected only the right pixel's
    chroma, even though unchanged backgrounds still passed identity tests.
    """
    index = np.arange(width * height).reshape(height, width)
    y = np.where(index % 2, 1023 - index, index).astype('<u2')
    row, col = np.indices((height, width // 2))
    u = np.where((row + col) % 2, 1023, 0).astype('<u2')
    v = (1023 - u).astype('<u2')
    sources = (y, u, v)
    paint_y = max(0, height // 2 - 1)
    pixels = np.full((1, 1, 4), (255, 0, 0, alpha), dtype=np.uint8)
    overlay, _ = array_sticker(tmp_path, pixels,
        keyframes=[OverlayKeyframe(0, width - 2, paint_y, 1, 1)])
    overlay.start = 1
    tail = Overlay('extent', str(tmp_path / 'missing.png'), '0' * 64,
                   2, 1, enabled=False)
    p = Project(width=width, height=height, overlays=[overlay, tail])
    red = rec709_codes((1, 0, 0))
    results = []
    for x in (width - 2, width - 1):
        overlay.keyframes[0].x = x
        actual = direct_sticker_pixels(engine, p, sources)
        results.append(actual)
        for channel, (source, frames) in enumerate(zip(sources, actual)):
            assert frames.shape == (3, *source.shape)
            np.testing.assert_array_equal(frames[0], source)
            np.testing.assert_array_equal(frames[2], source)
            if alpha == 0:
                np.testing.assert_array_equal(frames[1], source)
                continue
            target_x = x if channel == 0 else x // 2
            coverage = alpha / 255. / (1 if channel == 0 else 2)
            expected = source.astype(float).copy()
            expected[paint_y, target_x] = (
                expected[paint_y, target_x] * (1 - coverage) + red[channel] * coverage)
            np.testing.assert_allclose(frames[1], expected, atol=4, rtol=0)
            untouched = np.ones(source.shape, dtype=bool)
            untouched[paint_y, target_x] = False
            np.testing.assert_array_equal(frames[1][untouched], source[untouched])
    for channel in (1, 2):
        np.testing.assert_array_equal(results[0][channel], results[1][channel])


@pytest.mark.parametrize('rgb', [(255, 0, 0), (0, 255, 0), (0, 0, 255),
                               (128, 128, 128), (255, 255, 255)])
@pytest.mark.parametrize('alpha', [128, 255])
def test_solid_sticker_interiors_match_limited_709_reference(
        tmp_path, engine, rgb, alpha):
    overlay, _ = sticker(tmp_path, color=(*rgb, alpha), duration=1,
                         keyframes=[OverlayKeyframe(0, 16, 16, 2, 1)])
    p = Project(width=64, height=64, overlays=[overlay])
    planes = compiled_pixels(engine, p)
    actual = np.array([planes[0][0, 24:40, 24:40].mean(),
                       planes[1][0, 24:40, 12:20].mean(),
                       planes[2][0, 24:40, 12:20].mean()])
    black = np.array([64, 512, 512])
    expected = black + (rec709_codes(np.asarray(rgb) / 255.) - black) * alpha / 255.
    # Up to one art code plus integer YUV/alpha rounding, not a 601 mismatch.
    np.testing.assert_allclose(actual, expected, atol=4, rtol=0)
    assert all(plane.shape[0] == 1 for plane in planes)
    if rgb == (255, 0, 0) and alpha == 255:
        assert abs(actual[0] - (64 + 876 * .299)) > 65


def test_alpha_ramp_uses_encoded_domain_and_retains_eight_bit_art_coverage(
        tmp_path, engine):
    pixels = np.empty((24, 256, 4), dtype=np.uint8)
    pixels[..., :3] = 255
    pixels[..., 3] = np.arange(256, dtype=np.uint8)[None, :]
    overlay, _ = array_sticker(tmp_path, pixels,
                               keyframes=[OverlayKeyframe(0, 0, 8, 1, 1)])
    p = Project(width=256, height=40, overlays=[overlay])
    y, u, v = compiled_pixels(engine, p)
    alpha = np.arange(256) / 255.
    expected = 64 + 876 * alpha
    np.testing.assert_allclose(y[0, 16].astype(float), expected, atol=4, rtol=0)
    np.testing.assert_allclose(u[0, 16], 512, atol=2, rtol=0)
    np.testing.assert_allclose(v[0, 16], 512, atol=2, rtol=0)
    linear_light = 64 + 876 * np.where(alpha < .018, 4.5 * alpha,
                                      1.099 * alpha ** .45 - .099)
    assert np.mean(np.abs(y[0, 16, 32:224] - linear_light[32:224])) > 100
    assert 240 < np.unique(y[0, 16]).size <= 256


def test_fractional_resampling_matches_qt_art_luma_without_background_roundtrip(
        tmp_path, engine):
    # Qt's raster is the permitted 8-bit artwork boundary. Isolate its output
    # as coverage/color data, then independently compute YUV code-value math.
    pixels = np.zeros((16, 32, 4), dtype=np.uint8)
    pixels[..., :3] = (240, 80, 20)
    pixels[..., 3] = np.tile(np.arange(0, 256, 8, dtype=np.uint8), (16, 1))
    overlay, asset = array_sticker(tmp_path, pixels,
        keyframes=[OverlayKeyframe(0, 14.25, 8.5, 1.75, .65)])
    p = Project(width=96, height=48, overlays=[overlay])
    art = rgba(compose_frame(p, 0, {overlay.id: asset})).astype(float)
    rgb, alpha = art[..., :3] / 255., art[..., 3] / 255.
    expected_y = 64 + 876 * (rgb @ np.array([.2126, .7152, .0722])) * alpha
    actual_y = compiled_pixels(engine, p)[0][0].astype(float)
    active = alpha > 0
    assert np.count_nonzero((alpha > .05) & (alpha < .6)) > 600
    np.testing.assert_allclose(actual_y[active], expected_y[active], atol=4, rtol=0)
    np.testing.assert_array_equal(actual_y[~active], 64)


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30000, 1001),
                                Fraction(60000, 1001)])
def test_position_scale_opacity_keyframes_follow_exact_fractional_frames(
        tmp_path, engine, fps):
    path = png(tmp_path / 'white.png', (255, 255, 255, 255), 8, 8)
    asset = load_sticker(path)
    overlay = Overlay('animated-transform', str(path), asset.digest, 1, 5,
                      keyframes=[OverlayKeyframe(0, 4, 8, 1, 0),
                                 OverlayKeyframe(4, 28, 16, 2, 1)])
    tail = Overlay('disabled-tail', str(tmp_path / 'unused.json'), '0' * 64,
                   7, 1, enabled=False)
    p = Project(width=96, height=64, fps_num=fps.numerator,
                fps_den=fps.denominator, overlays=[overlay, tail])
    before = deepcopy(p.to_dict())
    raw_y = compiled_pixels(engine, p)[0]
    assert len(raw_y) == 8
    np.testing.assert_array_equal(raw_y[[0, 1, 6, 7]], 64)
    for local in range(1, 5):
        frame = raw_y[local + 1]
        x, y, size = 4 + 6 * local, 8 + 2 * local, 8 + 2 * local
        expected = 64 + 876 * local / 4
        np.testing.assert_allclose(frame[y:y+size, x:x+size], expected, atol=4, rtol=0)
        active = np.argwhere(frame > 70)
        assert tuple(active.min(axis=0)) == (y, x)
        assert tuple(active.max(axis=0)) == (y + size - 1, x + size - 1)
        assert len(active) == size * size
    output = engine.render(p, tmp_path / 'keyframes.mov', export_profile=PROFILE)
    decoded, _, _ = yuv422(output, 96, 64)
    assert len(decoded) == 8
    np.testing.assert_allclose(decoded[[0, 1, 6, 7]], 64, atol=2, rtol=0)
    media = probe(output)
    assert Fraction(media.fps_num, media.fps_den) == fps
    assert p.to_dict() == before


def test_zero_opacity_is_exact_picture_identity_with_active_static_art(
        tmp_path, engine):
    image = np.broadcast_to(np.rint(np.linspace(0, 65535, 1024))[None, :, None],
                            (64, 1024, 3)).astype(np.uint16).copy()
    media = rgb_source(tmp_path, image, frames=2)
    p = project(media, frames=2)
    baseline = compiled_pixels(engine, p)
    overlay, _ = sticker(tmp_path, duration=2,
                         keyframes=[OverlayKeyframe(0, 128, 8, 2, 0)])
    p.overlays = [overlay]
    transparent = compiled_pixels(engine, p)
    for clean, actual in zip(baseline, transparent):
        np.testing.assert_array_equal(actual, clean)
    overlay.enabled = False
    for clean, actual in zip(baseline, compiled_pixels(engine, p)):
        np.testing.assert_array_equal(actual, clean)


def test_real_prores_decoding_retains_sub_eight_bit_tones_with_stickers(
        tmp_path, engine):
    width, height = 2048, 144
    image = np.broadcast_to(np.rint(np.linspace(0, 65535, width))[None, :, None],
                            (height, width, 3)).astype(np.uint16).copy()
    image[48:96, :width // 2] = 32778
    image[48:96, width // 2:] = 32958
    media = rgb_source(tmp_path, image, frames=3)
    p = project(media, frames=3)
    overlay, _ = sticker(tmp_path, start=1, duration=1,
                         keyframes=[OverlayKeyframe(0, 512, 104, 2, .7)])
    p.overlays = [overlay]
    clean = deepcopy(p)
    clean.overlays = []
    pristine, stickered = compiled_pixels(engine, clean), compiled_pixels(engine, p)
    for before, after in zip(pristine, stickered):
        np.testing.assert_array_equal(after[[0, 2]], before[[0, 2]])
        np.testing.assert_array_equal(after[:, :96], before[:, :96])
    assert np.count_nonzero(pristine[0][1] != stickered[0][1]) > 500
    output = engine.render(p, tmp_path / 'stickered.mov', export_profile=PROFILE)
    meta = video_metadata(output)
    assert (meta['codec_name'], meta['profile'], meta['pix_fmt']) == (
        'prores', 'HQ', 'yuv422p10le')
    assert meta['color_space'] == meta['color_transfer'] == meta['color_primaries'] == 'bt709'
    y, _, _ = yuv422(output, width, height)
    assert len(y) == 3 and np.unique(y[1, 16]).size > 700
    reference = 64 + 876 * image[16, :, 0].astype(float) / 65535
    error = np.mean(np.abs(y[1, 8:24].mean(axis=0) - reference))
    assert error < .65
    assert y[1, 64:80, 1152:1920].mean() - y[1, 64:80, 128:896].mean() >= 1.75
    coarse = (np.rint(image.astype(float) / 257) * 257).astype(np.uint16)
    coarse_media = rgb_source(tmp_path, coarse, name='eight-bit-control', frames=3)
    q = project(coarse_media, frames=3)
    q.overlays = deepcopy(p.overlays)
    control = engine.render(q, tmp_path / 'eight-bit-control.mov', export_profile=PROFILE)
    coarse_y, _, _ = yuv422(control, width, height)
    assert error < np.mean(np.abs(coarse_y[1, 8:24].mean(axis=0) - reference)) * .7
    assert abs(coarse_y[1, 64:80, 1152:1920].mean()
               - coarse_y[1, 64:80, 128:896].mean()) <= .5


def test_real_prores_combines_sticker_below_native_highbit_caption(
        tmp_path, engine, latin_font):
    overlay, _ = array_sticker(tmp_path,
        np.full((16, 32, 4), (255, 0, 0, 255), dtype=np.uint8),
        keyframes=[OverlayKeyframe(0, 0, 0, 10, 1)])
    p = Project(width=320, height=128, overlays=[overlay],
                captions=[Caption('topmost', 0, 1, 'HHH')],
                caption_style=CaptionStyle(font_family=latin_font, font_size=64,
                                           margin_bottom=12, outline_width=0))
    before = deepcopy(p.to_dict())
    _, graph, _ = engine._compile(p, export_profile=PROFILE)
    assert graph.index('[stickered]') < graph.index('drawtext=')
    y, u, v = compiled_pixels(engine, p)
    assert np.count_nonzero(y[0] > 930) > 300
    np.testing.assert_allclose(y[0, :20], rec709_codes((1, 0, 0))[0], atol=4, rtol=0)
    # Both solid art and white caption retain the explicit 709 matrix; drawing
    # the sticker after the caption would erase every white glyph pixel.
    output = engine.render(p, tmp_path / 'sticker-and-caption.mov', export_profile=PROFILE)
    delivered, _, _ = yuv422(output, 320, 128)
    assert np.count_nonzero(delivered[0] > 920) > 300
    assert video_metadata(output)['pix_fmt'] == 'yuv422p10le'
    assert p.to_dict() == before

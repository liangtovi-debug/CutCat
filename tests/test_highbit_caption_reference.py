"""Independent pixel references for native high-depth caption composition.

Synthetic fixtures distinguish genuine picture precision from ten-bit tags and
dither. Color and gamma oracles use published coefficients, not the production
color evaluator. Eight-bit glyph coverage is permitted; eight-bit pictures are
not. These tests intentionally allow the native drawtext integer rounding bias.
"""
from copy import deepcopy
from fractions import Fraction
import json
import subprocess

import numpy as np
import pytest

from lumen.caption_render import CaptionRenderError, caption_filters, font_identity
from lumen.highbit_captions import highbit_caption_filters
from lumen.media import ffmpeg_available, ffmpeg_binary, ffprobe_binary
from lumen.model import Caption, CaptionStyle, Project
from lumen.render import RenderCancelled, RenderEngine, RenderError
from test_color_highbit_reference import ff, project, rgb_source, video_metadata, yuv422


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
PROFILE = 'prores_422_hq_10bit'


@pytest.fixture(scope='module')
def latin_font():
    for family in ('DejaVu Sans', 'Arial', 'Helvetica'):
        try:
            font_identity(CaptionStyle(font_family=family))
            return family
        except CaptionRenderError:
            pass
    pytest.skip('A local Latin test face is required')


@pytest.fixture
def engine():
    value = RenderEngine(timeout=60)
    try:
        yield value
    finally:
        value.close()


def style(family, **changes):
    values = dict(font_family=family, font_size=40, margin_bottom=16,
                  outline_width=0, safe_margin_percent=5)
    values.update(changes)
    return CaptionStyle(**values)


def split422(raw, width, height):
    pixels = width * height
    frames = np.frombuffer(raw, '<u2').reshape(-1, pixels * 2)
    return (frames[:, :pixels].reshape(-1, height, width),
            frames[:, pixels:pixels * 3 // 2].reshape(-1, height, width // 2),
            frames[:, pixels * 3 // 2:].reshape(-1, height, width // 2))


def compiled_pixels(engine, p, profile=PROFILE):
    """Run the actual production video graph, before any lossy encoder.

    Only the unused audio output is sent to anullsink. No video stage is replaced
    or mocked, so format/range mistakes in the real compiler remain visible.
    """
    inputs, graph, _ = engine._compile(p, export_profile=profile)
    assert graph.endswith('[aout]')
    graph = graph[:-len('[aout]')] + ',anullsink'
    fmt = 'yuv422p10le' if profile == PROFILE else 'yuv420p'
    raw = ff('-filter_complex_threads', '1', *inputs, '-filter_complex', graph,
             '-map', '[vout]', '-frames:v', p.duration_frames,
             '-pix_fmt', fmt, '-f', 'rawvideo', 'pipe:1')
    if profile == PROFILE:
        return split422(raw, p.width, p.height)
    values = np.frombuffer(raw, np.uint8).reshape(-1, p.width * p.height * 3 // 2)
    return values[:, :p.width * p.height].reshape(-1, p.height, p.width)


def test_native_ten_bit_picture_is_exact_outside_glyphs_and_when_inactive(
        tmp_path, latin_font):
    width, height = 1024, 128
    y = np.tile(np.arange(width, dtype='<u2'), (height, 1))
    u = np.tile(np.arange(width // 2, dtype='<u2'), (height, 1))
    u += ((np.arange(height, dtype=np.uint16) % 2) * 512)[:, None]
    v = 1023 - u
    raw = b''.join(plane.astype('<u2').tobytes() for plane in (y, u, v))
    p = Project(width=width, height=height,
                captions=[Caption('cue', 1, 1, 'TEN BIT')],
                caption_style=style(latin_font, font_size=28, outline_width=2))
    filters = highbit_caption_filters(p, tmp_path, 30, None, RenderError, RenderCancelled)
    prefix = ['-filter_threads', '1', '-f', 'rawvideo', '-pix_fmt', 'yuv422p10le',
              '-s', f'{width}x{height}', '-r', '30', '-i', 'pipe:0']
    data = ff('-noauto_conversion_filters', *prefix, '-vf', ','.join(filters),
              '-frames:v', 3, '-pix_fmt', 'yuv422p10le', '-f', 'rawvideo', 'pipe:1',
              data=raw * 3)
    actual = split422(data, width, height)
    for source, output in zip((y, u, v), actual):
        np.testing.assert_array_equal(output[0], source)
        np.testing.assert_array_equal(output[2], source)
        np.testing.assert_array_equal(output[1, :64], source[:64])
        assert np.unique(output[1, :64]).size == 1024
        assert np.count_nonzero(output[1, :64] % 4) > 0
    assert np.count_nonzero(actual[0][1] != y) > 100
    # The same source forced through eight bits must fail this precision oracle.
    coarse = ff(*prefix, '-vf', 'format=yuv422p,format=yuv422p10le',
                '-frames:v', 1, '-pix_fmt', 'yuv422p10le', '-f', 'rawvideo', 'pipe:1',
                data=raw)
    for source, output in zip((y, u, v), split422(coarse, width, height)):
        assert np.unique(output[0, :64]).size == 256
        assert np.count_nonzero(output[0, :64] != source[:64]) > 1000


def rec709_codes(rgb):
    red, green, blue = np.asarray(rgb, dtype=float)
    luma = .2126 * red + .7152 * green + .0722 * blue
    return np.array([64 + 876 * luma,
                     512 + 896 * (blue - luma) / (2 * (1 - .0722)),
                     512 + 896 * (red - luma) / (2 * (1 - .2126))])


@pytest.mark.parametrize('hex_color,rgb', [('#FF0000', (1, 0, 0)),
                                         ('#00FF00', (0, 1, 0)),
                                         ('#0000FF', (0, 0, 1)),
                                         ('#808080', (128 / 255,) * 3)])
def test_production_colored_glyphs_use_709_before_drawing(
        engine, latin_font, hex_color, rgb):
    p = Project(width=320, height=128,
                captions=[Caption('cue', 0, 1, 'HHH')],
                caption_style=style(latin_font, color=hex_color, font_size=72))
    y, cb, cr = compiled_pixels(engine, p)
    # The solid interior has maximum luma on the black canvas. Require two
    # adjacent full-coverage pixels so 4:2:2 chroma does not sample an edge.
    full = y[0] == y.max()
    paired = full[:, ::2] & full[:, 1::2]
    assert paired.sum() > 30
    actual = np.array([y.max(), np.median(cb[0][paired]), np.median(cr[0][paired])])
    # Native FFmpeg scales 8-bit RGB colors and raster alpha to high-depth
    # integers with small bias. Four 10-bit codes cover that, not a 601 error.
    np.testing.assert_allclose(actual, rec709_codes(rgb), atol=4, rtol=0)
    if hex_color == '#FF0000':
        wrong_601_luma = 64 + 876 * .299
        assert abs(actual[0] - wrong_601_luma) > 65


def test_partial_glyph_coverage_is_encoded_domain_not_linear_light(
        tmp_path, engine, latin_font):
    p = Project(width=320, height=128,
                captions=[Caption('cue', 0, 1, 'HORS')],
                caption_style=style(latin_font, font_size=63))
    rendered = compiled_pixels(engine, p)[0][0].astype(float)
    # Isolate the rasterizer's coverage on full-range gray8. This is a coverage
    # reference only; expected image/color math is independently computed below.
    filters = caption_filters(p, tmp_path)
    mask_raw = ff('-noauto_conversion_filters', '-filter_threads', '1',
                  '-f', 'rawvideo', '-pix_fmt', 'gray', '-s', '320x128', '-i', 'pipe:0',
                  '-vf', 'setparams=range=full:colorspace=bt709,' + ','.join(filters),
                  '-frames:v', 1, '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1',
                  data=bytes(320 * 128))
    alpha = np.frombuffer(mask_raw, np.uint8).reshape(128, 320) / 255.
    partial = (alpha > .15) & (alpha < .85)
    assert partial.sum() > 50
    encoded_reference = 64 + 876 * alpha
    np.testing.assert_allclose(rendered[partial], encoded_reference[partial], atol=5, rtol=0)
    linear_light_reference = 64 + 876 * np.where(
        alpha < .018, 4.5 * alpha, 1.099 * alpha ** .45 - .099)
    assert np.mean(np.abs(rendered[partial] - linear_light_reference[partial])) > 90


def test_production_caption_graph_and_prores_retain_sub_eight_bit_tones(
        tmp_path, engine, latin_font):
    width, height = 2048, 160
    image = np.broadcast_to(np.rint(np.linspace(0, 65535, width))[None, :, None],
                            (height, width, 3)).astype(np.uint16).copy()
    image[48:96, :width // 2] = 32778
    image[48:96, width // 2:] = 32958
    media = rgb_source(tmp_path, image, frames=3)
    p = project(media, frames=3)
    p.caption_style = style(latin_font, font_size=28)
    p.captions = [Caption('cue', 1, 1, 'PRECISION')]
    baseline = deepcopy(p)
    baseline.captions = []
    pristine = compiled_pixels(engine, baseline)
    captioned = compiled_pixels(engine, p)
    for before, after in zip(pristine, captioned):
        np.testing.assert_array_equal(after[[0, 2]], before[[0, 2]])
        np.testing.assert_array_equal(after[:, :96], before[:, :96])
    assert np.count_nonzero(pristine[0][1] != captioned[0][1]) > 100

    output = engine.render(p, tmp_path / 'captioned.mov', export_profile=PROFILE)
    meta = video_metadata(output)
    assert (meta['codec_name'], meta['profile'], meta['pix_fmt']) == ('prores', 'HQ', 'yuv422p10le')
    assert meta['color_space'] == meta['color_transfer'] == meta['color_primaries'] == 'bt709'
    frame_probe = subprocess.run([ffprobe_binary(), '-v', 'error', '-select_streams', 'v:0',
                                  '-read_intervals', '%+#1', '-show_frames', '-show_entries',
                                  'frame=pix_fmt,color_range', '-of', 'json', str(output)],
                                 capture_output=True, text=True, check=True, timeout=30)
    decoded_meta = json.loads(frame_probe.stdout)['frames'][0]
    assert decoded_meta['pix_fmt'] == 'yuv422p10le' and decoded_meta['color_range'] == 'tv'
    y, _, _ = yuv422(output, width, height)
    assert len(y) == 3
    reference = 64 + 876 * image[16, :, 0].astype(float) / 65535
    assert np.unique(y[1, 16]).size > 700
    assert np.mean(np.abs(y[1, 8:24].mean(axis=0) - reference)) < .65
    assert (y[1, 64:80, 1152:1920].mean() - y[1, 64:80, 128:896].mean()) >= 1.75
    # Broad equal-valued 8-bit patches remain equal even after dithering/ringing.
    coarse = (np.rint(image.astype(float) / 257) * 257).astype(np.uint16)
    coarse_media = rgb_source(tmp_path, coarse, name='eight-bit-control', frames=3)
    q = project(coarse_media, frames=3)
    q.caption_style, q.captions = deepcopy(p.caption_style), deepcopy(p.captions)
    control = engine.render(q, tmp_path / 'eight-bit-control.mov', export_profile=PROFILE)
    coarse_y, _, _ = yuv422(control, width, height)
    control_error = np.mean(np.abs(coarse_y[1, 8:24].mean(axis=0) - reference))
    assert np.mean(np.abs(y[1, 8:24].mean(axis=0) - reference)) < control_error * .7
    assert abs(coarse_y[1, 64:80, 1152:1920].mean()
               - coarse_y[1, 64:80, 128:896].mean()) <= .5


@pytest.mark.parametrize('fps', [Fraction(30), Fraction(30000, 1001), Fraction(60000, 1001)])
def test_exact_fractional_caption_gates_and_caption_only_tail(
        tmp_path, engine, latin_font, fps):
    media = rgb_source(tmp_path, np.zeros((128, 320, 3), np.uint16), frames=3, fps=fps)
    p = project(media, frames=3, fps=fps)
    p.caption_style = style(latin_font)
    p.captions = [Caption('middle', 1, 1, 'MIDDLE'), Caption('tail', 4, 2, 'TAIL')]
    assert p.duration_frames == 6
    encoded = engine.render(p, tmp_path / 'timing.mov', export_profile=PROFILE)
    y, _, _ = yuv422(encoded, 320, 128)
    assert len(y) == 6
    for index in (0, 2, 3):
        assert np.max(np.abs(y[index] - 64)) <= 2
    for index in (1, 4, 5):
        assert np.count_nonzero(y[index] > 800) > 100


def test_literal_wrapping_shared_with_legacy_preview_without_project_mutation(
        tmp_path, engine, latin_font):
    text = "Literal %{n}: 'x'; [a] stays literal and wraps safely\nsecond line"
    p = Project(width=320, height=240,
                captions=[Caption('literal', 0, 2, text)],
                caption_style=style(latin_font, font_size=23, outline_width=2))
    before = p.to_dict()
    high = compiled_pixels(engine, p)[0]
    legacy = compiled_pixels(engine, p, 'h264_8bit')
    assert len(high) == len(legacy) == 2
    high_ink, legacy_ink = high[0] > 500, legacy[0] > 125
    assert high_ink.sum() > 200
    assert (high_ink & legacy_ink).sum() / (high_ink | legacy_ink).sum() > .97
    preview = engine.preview(p)
    assert video_metadata(preview)['pix_fmt'] == 'yuv420p'
    path = tmp_path / 'roundtrip.lumen'
    p.save(path)
    assert Project.load(path).captions[0].text == text
    assert p.to_dict() == before

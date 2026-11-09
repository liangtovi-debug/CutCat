"""Colored compiler/codec oracles catch matrix errors hidden by gray fixtures.

New RGB8 shape math must not negotiate a 601 conversion, or apply YUV matrix
scaling to an RGB grade cache. Actual code values, not metadata, are decisive.
"""
from copy import deepcopy
import os

import numpy as np
import pytest

from lumen.clip_transform import ClipTransform
from lumen.media import ffmpeg_available
from lumen.model import ClipEffect
from lumen.render import RenderEngine
from test_color_highbit_reference import ff, project, rgb_source
from test_vignette_shape_rendering import effect


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
COLORS = [(65535, 0, 0), (0, 65535, 0), (0, 0, 65535), (40000, 20000, 5000)]


@pytest.fixture
def engine():
    renderer = RenderEngine(timeout=60)
    try:
        yield renderer
    finally:
        renderer.close()


def compiled_yuv(engine, p):
    inputs, graph, _ = engine._compile(p)
    data = ff('-filter_complex_threads', 1, *inputs, '-filter_complex', graph,
              '-map', '[vout]', '-frames:v', 1, '-c:v', 'rawvideo', '-threads', 1,
              '-pix_fmt', 'yuv420p', '-f', 'rawvideo', 'pipe:1',
              '-map', '[aout]', '-f', 'null', os.devnull)
    values = np.frombuffer(data, np.uint8)
    w, h = p.width, p.height
    n = w * h
    return (values[:n].reshape(h, w), values[n:n * 5 // 4].reshape(h // 2, w // 2),
            values[n * 5 // 4:].reshape(h // 2, w // 2)), graph


def rec709_codes(color):
    # The new effect precision boundary is eight-bit full-range encoded RGB.
    r, g, b = np.rint(np.asarray(color, dtype=float) / 257) / 255
    luma = .2126 * r + .7152 * g + .0722 * b
    return [16 + 219 * luma, 128 + 224 * (b - luma) / (2 * (1 - .0722)),
            128 + 224 * (r - luma) / (2 * (1 - .2126))]


@pytest.mark.parametrize('color', COLORS, ids=['red', 'green', 'blue', 'warm'])
@pytest.mark.parametrize('stack', ['shape', 'legacy-before', 'legacy-after', 'blur-mixed'])
def test_near_zero_shape_preserves_rec709_colors_through_compiler_and_geometry(tmp_path, engine, color, stack):
    image = np.full((64, 64, 3), color, dtype='<u2')
    p = project(rgb_source(tmp_path, image))
    shaped = effect(1e-12, .75, 1.5)
    legacy = ClipEffect('legacy', 'vignette', 1e-12)
    p.clips[0].effects = {
        'shape': [shaped], 'legacy-before': [legacy, shaped],
        'legacy-after': [shaped, legacy],
        'blur-mixed': [ClipEffect('blur', 'blur', 2), legacy, shaped],
    }[stack]
    expected = rec709_codes(color)
    for transform in (ClipTransform(), ClipTransform(mirror_x=True),
                      ClipTransform(rotation=90), ClipTransform(x=5, y=3, scale=.8, rotation=13)):
        p.clips[0].transform = transform
        before = deepcopy(p.to_dict())
        actual, graph = compiled_yuv(engine, p)
        assert 'scale=out_color_matrix=bt709:in_range=full:out_range=tv,format=yuv420p' in graph
        assert 'force_original_aspect_ratio=decrease:in_color_matrix=bt709' not in graph
        for plane, desired in zip(actual, expected):
            cy, cx = (size // 2 for size in plane.shape)
            np.testing.assert_allclose(plane[cy - 3:cy + 3, cx - 3:cx + 3], desired, atol=1.1, rtol=0)
        if transform.scale == .8:
            for plane, black in zip(actual, (16, 128, 128)):
                np.testing.assert_allclose(plane[:2, :2], black, atol=1, rtol=0)
        assert p.to_dict() == before


@pytest.mark.parametrize('color', COLORS, ids=['red', 'green', 'blue', 'warm'])
def test_finite_strength_nondefault_shape_keeps_unity_center_color(tmp_path, engine, color):
    p = project(rgb_source(tmp_path, np.full((64, 128, 3), color, dtype='<u2')))
    p.clips[0].effects = [effect(.65, 1.0000000001, 1)]
    actual, _ = compiled_yuv(engine, p)
    expected = rec709_codes(color)
    for plane, desired in zip(actual, expected):
        # Chroma is subsampled across the nearly-unity central neighborhood.
        np.testing.assert_allclose(plane[plane.shape[0] // 2, plane.shape[1] // 2], desired, atol=1.1, rtol=0)


@pytest.mark.parametrize('color', COLORS, ids=['red', 'green', 'blue', 'warm'])
@pytest.mark.parametrize('preview', [False, True])
def test_real_h264_and_preview_near_zero_shape_do_not_shift_color(tmp_path, engine, color, preview):
    p = project(rgb_source(tmp_path, np.full((64, 128, 3), color, dtype='<u2')))
    p.clips[0].effects = [effect(1e-12, .75, 1.5)]
    output = engine.render(p, tmp_path / 'color.mp4', preview=preview)
    raw = ff('-i', output, '-an', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1')
    decoded = np.frombuffer(raw, np.uint8).reshape(-1, 64, 128, 3)
    expected = np.asarray(color, dtype=float) / 257
    np.testing.assert_allclose(decoded[0, 16:48, 32:96], np.broadcast_to(expected, (32, 64, 3)), atol=3, rtol=0)

"""Real preview/export acceptance for static canvas transforms.

Synthetic local fixtures only. Independent NumPy inverse mapping checks the
picture, and pre-AAC PCM verifies transforms cannot disturb audio timing/data.
"""
from copy import deepcopy
import subprocess
import wave

import numpy as np
import pytest

from lumen.clip_transform import ClipTransform
from lumen.media import ffmpeg_available, ffmpeg_binary, probe
from lumen.model import Clip, ClipEffect, Editor, Project
from lumen.render import RenderEngine
from test_roll_media import exact_pcm

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
WIDTH, HEIGHT, FRAMES, FPS = 64, 48, 6, 24


def ff(*args, data=None):
    return subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-y',
                           '-threads', '1', *map(str, args)], input=data,
                          capture_output=True, check=True, timeout=40).stdout


def decode(path):
    raw = ff('-i', path, '-map', '0:V:0', '-an', '-pix_fmt', 'rgb24',
             '-f', 'rawvideo', 'pipe:1')
    return np.frombuffer(raw, np.uint8).reshape(-1, HEIGHT, WIDTH, 3)


def make_source(tmp_path, name='source', width=WIDTH, height=HEIGHT, value=None):
    image = np.empty((height, width, 3), dtype=np.uint16)
    if value is None:
        image[:height//2, :width//2] = 40 * 257
        image[:height//2, width//2:] = 100 * 257
        image[height//2:, :width//2] = 160 * 257
        image[height//2:, width//2:] = 220 * 257
    else:
        image[:] = value * 257
    picture = tmp_path / f'{name}-picture.mkv'
    ff('-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-s', f'{width}x{height}', '-r', FPS,
       '-i', 'pipe:0', '-an', '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le',
       '-color_range', 'pc', '-color_primaries', 'bt709', '-color_trc', 'bt709',
       '-colorspace', 'rgb', picture, data=image.astype('<u2').tobytes() * FRAMES)
    sound = tmp_path / f'{name}.wav'
    rng = np.random.default_rng(752)
    pcm = rng.integers(-10000, 10000, FRAMES * 48000 // FPS, dtype=np.int16)
    with wave.open(str(sound), 'wb') as stream:
        stream.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        stream.writeframes(pcm.astype('<i2').tobytes())
    output = tmp_path / f'{name}.mkv'
    ff('-i', picture, '-i', sound, '-map', '0:v', '-map', '1:a', '-c', 'copy', output)
    return probe(output)


@pytest.fixture
def engine():
    renderer = RenderEngine(timeout=40)
    try:
        yield renderer
    finally:
        renderer.close()


def project(source):
    return Project(width=WIDTH, height=HEIGHT, fps_num=FPS, fps_den=1,
                   media=[source], clips=[Clip('picture', source.id, 0, 0, 0, FRAMES)])


def oracle(image, settings):
    """Independent homogeneous forward matrix and inverse bilinear resampler."""
    height, width, _ = image.shape
    cx, cy = (width-1)/2, (height-1)/2
    theta = np.deg2rad(settings.rotation)
    rotation = np.array([[np.cos(theta), -np.sin(theta), 0],
                         [np.sin(theta), np.cos(theta), 0], [0, 0, 1]])
    scale = np.diag([settings.scale * (-1 if settings.mirror_x else 1),
                     settings.scale * (-1 if settings.mirror_y else 1), 1])
    center = np.array([[1, 0, -cx], [0, 1, -cy], [0, 0, 1]])
    offset = np.array([[1, 0, cx+settings.x], [0, 1, cy+settings.y], [0, 0, 1]])
    yy, xx = np.indices((height, width))
    coordinates = np.linalg.inv(offset @ rotation @ scale @ center) @ np.stack(
        [xx.ravel(), yy.ravel(), np.ones(width*height)])
    x, y = coordinates[:2]
    x[np.abs(x-np.rint(x)) < 1e-9] = np.rint(x[np.abs(x-np.rint(x)) < 1e-9])
    y[np.abs(y-np.rint(y)) < 1e-9] = np.rint(y[np.abs(y-np.rint(y)) < 1e-9])
    valid = (x >= 0) & (y >= 0) & (x <= width-1) & (y <= height-1)
    x, y = np.clip(x, 0, width-1), np.clip(y, 0, height-1)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0+1, width-1), np.minimum(y0+1, height-1)
    dx, dy = (x-x0)[:, None], (y-y0)[:, None]
    result = ((1-dx)*(1-dy)*image[y0, x0] + dx*(1-dy)*image[y0, x1]
              + (1-dx)*dy*image[y1, x0] + dx*dy*image[y1, x1])
    result[~valid] = 0
    return result.reshape(height, width, 3)


def assert_geometry(actual, expected):
    # H.264 is lossy. Compare every interior pixel of regions with no strong
    # edge, retaining black canvas and all four independently labeled regions.
    low, high = expected.copy(), expected.copy()
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            neighbor = np.roll(np.roll(expected, dy, axis=0), dx, axis=1)
            low = np.minimum(low, neighbor)
            high = np.maximum(high, neighbor)
    mask = (high-low).max(axis=2) < 8
    assert mask.mean() > .5
    for frame in actual:
        error = np.abs(frame[mask].astype(float) - expected[mask])
        # Tiny H.264 ringing outliers survive even a two-pixel edge exclusion.
        # Keep both a strict bulk error bound and an absolute outlier bound.
        assert np.quantile(error, .995) <= 8
        assert error.max() <= 16


def test_identity_matches_legacy_bypass_bytes_and_pixels(tmp_path, engine, monkeypatch):
    p = project(make_source(tmp_path))
    actual = engine.render(p, tmp_path/'identity.mp4')
    import lumen.clip_transform as transforms
    monkeypatch.setattr(transforms, 'transform_filters', lambda *_args: [])
    legacy = engine.render(p, tmp_path/'legacy.mp4')
    assert actual.read_bytes() == legacy.read_bytes()
    np.testing.assert_array_equal(decode(actual), decode(legacy))


@pytest.mark.parametrize('settings', [
    ClipTransform(mirror_x=True), ClipTransform(mirror_y=True),
    ClipTransform(rotation=90), ClipTransform(x=8, y=-6),
    ClipTransform(scale=.5), ClipTransform(scale=2),
    ClipTransform(rotation=31, scale=.8, x=5, y=-3, mirror_x=True),
], ids=['horizontal', 'vertical', 'rotate', 'translate', 'shrink', 'zoom', 'combined'])
def test_preview_export_independent_geometry(tmp_path, engine, settings):
    p = project(make_source(tmp_path))
    baseline = decode(engine.render(p, tmp_path/'baseline.mp4'))[0].astype(float)
    p.clips[0].transform = settings
    expected = oracle(baseline, settings)
    for output in (engine.preview(p), engine.render(p, tmp_path/'transformed.mp4')):
        actual = decode(output)
        assert len(actual) == FRAMES
        assert_geometry(actual, expected)


def test_portrait_fit_and_effects_precede_transform(tmp_path, engine):
    p = project(make_source(tmp_path, width=24, height=48))
    p.clips[0].effects = [ClipEffect('blur', 'blur', 2)]
    baseline = decode(engine.render(p, tmp_path/'fitted-effect.mp4'))[0].astype(float)
    assert baseline[:, :15].max() <= 3 and baseline[:, -15:].max() <= 3
    settings = ClipTransform(rotation=90, x=6, scale=.8)
    p.clips[0].transform = settings
    expected = oracle(baseline, settings)
    assert_geometry(decode(engine.preview(p)), expected)
    assert_geometry(decode(engine.render(p, tmp_path/'portrait.mp4')), expected)


def test_upper_transformed_canvas_is_opaque_black(tmp_path, engine):
    lower = make_source(tmp_path, name='lower', value=220)
    upper = make_source(tmp_path, name='upper', value=100)
    p = project(lower)
    p.media.append(upper)
    p.clips.append(Clip('upper', upper.id, 1, 0, 0, FRAMES,
                        transform=ClipTransform(scale=.5), muted=True))
    for output in (engine.preview(p), engine.render(p, tmp_path/'opaque.mp4')):
        actual = decode(output)
        assert actual[:, :10].max() <= 4
        assert actual[:, -10:].max() <= 4
        np.testing.assert_allclose(actual[:, 20:28, 28:36], 100, atol=5)


def test_audio_unchanged_cache_roundtrip_and_undo(tmp_path, engine):
    p = project(make_source(tmp_path))
    editor = Editor(p)
    before = deepcopy(editor.project)
    source = p.media[0]
    original_segment = engine._graded_segment(source, p.clips[0], p, None)
    original_preview = engine.preview(p)
    original_pcm = exact_pcm(engine, p)
    assert len(original_pcm) == FRAMES * 48000 // FPS
    settings = ClipTransform(rotation=17, x=7, scale=.8)
    editor.update_clip('picture', transform=settings)
    changed = editor.project
    assert engine._graded_segment(source, changed.clips[0], changed, None) == original_segment
    changed_preview = engine.preview(changed)
    assert changed_preview != original_preview
    assert not np.array_equal(decode(changed_preview), decode(original_preview))
    np.testing.assert_array_equal(exact_pcm(engine, changed), original_pcm)
    saved = tmp_path/'transform.lumen'
    changed.save(saved)
    loaded = Project.load(saved)
    assert loaded == changed
    assert engine.preview(loaded) == changed_preview
    assert editor.undo() and editor.project == before
    assert engine.preview(editor.project) == original_preview
    assert editor.redo()
    assert engine.preview(editor.project) == changed_preview


def test_combined_transform_lossless_compiler_pixels(tmp_path, engine):
    p = project(make_source(tmp_path))
    def compiled_picture(name):
        inputs, graph, _ = engine._compile(p)
        path = tmp_path / name
        ff(*inputs, '-filter_complex_threads', '1', '-filter_complex', graph,
           '-map', '[vout]', '-map', '[aout]', '-c:v', 'ffv1', '-c:a', 'pcm_f32le', path)
        return decode(path)
    baseline = compiled_picture('baseline-lossless.mkv')[0].astype(float)
    settings = ClipTransform(rotation=31, scale=.8, x=5, y=-3, mirror_x=True)
    p.clips[0].transform = settings
    expected = oracle(baseline, settings)
    actual = compiled_picture('transformed-lossless.mkv')
    # Unlike delivery's H.264 checks, this includes every edge and black pixel.
    for frame in actual:
        np.testing.assert_allclose(frame, expected, atol=4, rtol=0)

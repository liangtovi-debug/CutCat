"""High-bit art edges, immutable cache inputs, and failed-publication safety."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import threading

import numpy as np
import pytest

from lumen.media import ffmpeg_available
from lumen.model import OverlayKeyframe, Project
from lumen.overlay_render import compose_frame
from lumen.render import RenderCancelled, RenderEngine, RenderError
from test_highbit_sticker_reference import array_sticker, compiled_pixels
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


def independently_flattened_reference(art):
    """Analytic source-over in gamma-coded RGB, then equal-pair 4:2:2.

    The test takes Qt artwork as an allowed coverage/color boundary, not a
    compositing oracle. No production color or FFmpeg compositor is reused.
    Pair averaging is the route's explicit area-downsample chroma policy.
    """
    rgb = art[..., :3].astype(float) / 255.
    alpha = art[..., 3:].astype(float) / 255.
    red, green, blue = np.moveaxis(rgb * alpha, -1, 0)
    luma = .2126 * red + .7152 * green + .0722 * blue
    cb = 512 + 896 * (blue - luma) / (2 * (1 - .0722))
    cr = 512 + 896 * (red - luma) / (2 * (1 - .2126))
    return ((64 + 876 * luma)[None],
            ((cb[:, ::2] + cb[:, 1::2]) / 2)[None],
            ((cr[:, ::2] + cr[:, 1::2]) / 2)[None])


@pytest.mark.parametrize('color', [(255, 0, 0, 255), (0, 0, 255, 255)])
def test_saturated_odd_pixel_edge_does_not_double_attenuate_chroma(
        tmp_path, engine, color):
    overlay, asset = sticker(tmp_path, color=color, duration=1,
                             keyframes=[OverlayKeyframe(0, 17, 16, 1, 1)])
    p = Project(width=64, height=48, overlays=[overlay])
    art = rgba(compose_frame(p, 0, {overlay.id: asset}))
    expected = independently_flattened_reference(art)
    actual = compiled_pixels(engine, p)
    for before, after in zip(expected, actual):
        # No low-alpha unpremultiplication: this fixture is purely 0/255 alpha.
        # Four 10-bit codes cover only conversion/rounding, never a dark fringe.
        np.testing.assert_allclose(after[0, 20:28], before[0, 20:28], atol=4, rtol=0)
    # A chroma sample belongs to a pair. Check exact identity only outside the
    # pair/filter footprint, not the unmodified luma pixel sharing that sample.
    np.testing.assert_array_equal(actual[0][0, :, :16], 64)
    # The synthetic color source uses FFmpeg's existing 10-bit neutral code;
    # check equality to that untouched region, not an assumed 512 spelling.
    np.testing.assert_array_equal(actual[1][0, :, :6], actual[1][0, 0, 0])
    np.testing.assert_array_equal(actual[2][0, :, :6], actual[2][0, 0, 0])


def test_fractional_saturated_alpha_ramp_resamples_without_dark_chroma_fringe(
        tmp_path, engine):
    pixels = np.zeros((12, 24, 4), dtype=np.uint8)
    pixels[..., :3] = (255, 0, 0)
    pixels[..., 3] = np.linspace(0, 255, 24).astype(np.uint8)[None, :]
    overlay, asset = array_sticker(tmp_path, pixels,
        keyframes=[OverlayKeyframe(0, 13.25, 12.5, 1.75, .7)])
    p = Project(width=80, height=48, overlays=[overlay])
    art = rgba(compose_frame(p, 0, {overlay.id: asset}))
    expected = independently_flattened_reference(art)
    actual = compiled_pixels(engine, p)
    for before, after in zip(expected, actual):
        # Qt's actual 8-bit raster is already included in the reference. Allow
        # up to six 10-bit codes for alpha/RGB integer rounding and resampling.
        np.testing.assert_allclose(after[0, 16:28], before[0, 16:28], atol=6, rtol=0)


@pytest.mark.parametrize('pair_x', [16, 62])
def test_half_covered_chroma_pair_is_symmetric_even_at_right_canvas_boundary(
        tmp_path, engine, pair_x):
    pixels = np.full((8, 1, 4), (255, 0, 0, 255), dtype=np.uint8)
    overlay, asset = array_sticker(tmp_path, pixels,
        keyframes=[OverlayKeyframe(0, pair_x, 16, 1, 1)])
    p = Project(width=64, height=48, overlays=[overlay])
    results = []
    for x in (pair_x, pair_x + 1):
        overlay.keyframes[0].x = x
        art = rgba(compose_frame(p, 0, {overlay.id: asset}))
        expected = independently_flattened_reference(art)
        actual = compiled_pixels(engine, p)
        for reference, delivered in zip(expected, actual):
            np.testing.assert_allclose(delivered, reference, atol=4, rtol=0)
        results.append(actual)
    # Native FFmpeg's direct 4:2:2 alpha path incorrectly weights even/odd
    # samples 3:1, and treats the final pair differently. The 4:4:4 composition
    # route must remove both asymmetries before its explicit area reduction.
    for channel in (1, 2):
        np.testing.assert_array_equal(results[0][channel], results[1][channel])


def test_repeated_highbit_compile_uses_owned_cache_but_rehashes_source(
        tmp_path, engine):
    overlay, _ = sticker(tmp_path, duration=2)
    p = Project(width=64, height=48, overlays=[overlay])
    original = compiled_pixels(engine, p)
    cache = list(Path(engine._cache_dir.name).glob('stickers-*.mkv'))
    assert len(cache) == 1
    before_stat = cache[0].stat()
    repeated = compiled_pixels(engine, p)
    assert cache[0].stat().st_mtime_ns == before_stat.st_mtime_ns
    for a, b in zip(original, repeated):
        np.testing.assert_array_equal(a, b)
    source = Path(overlay.asset_path)
    before = source.stat()
    png(source, (0, 0, 255, 255))
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert source.stat().st_size == before.st_size
    with pytest.raises(RenderError, match='changed since import'):
        engine.render(p, tmp_path / 'must-not-publish.mov', export_profile=PROFILE)
    assert not (tmp_path / 'must-not-publish.mov').exists()
    assert not list(Path(engine._cache_dir.name).glob('*.partial.mkv'))
    assert not list(tmp_path.glob('.lumen-render-*'))


def test_source_mutation_during_art_composition_does_not_publish_cache_or_output(
        tmp_path, engine, monkeypatch):
    import lumen.overlay_render as compositor
    overlay, _ = sticker(tmp_path, duration=2)
    p = Project(width=64, height=48, overlays=[overlay])
    original = compositor.render_overlay_segment

    def replace_source(*args, **kwargs):
        original(*args, **kwargs)
        png(Path(overlay.asset_path), (0, 0, 255, 255))

    monkeypatch.setattr(compositor, 'render_overlay_segment', replace_source)
    with pytest.raises(RenderError, match='sticker'):
        engine.render(p, tmp_path / 'mutated.mov', export_profile=PROFILE)
    assert not (tmp_path / 'mutated.mov').exists()
    assert not list(Path(engine._cache_dir.name).glob('stickers-*'))
    assert not list(tmp_path.glob('.lumen-render-*'))


def test_changing_transform_invalidates_owned_art_cache(tmp_path, engine):
    overlay, _ = sticker(tmp_path, duration=2,
                         keyframes=[OverlayKeyframe(0, 4, 8, 1, 1)])
    p = Project(width=64, height=48, overlays=[overlay])
    original = compiled_pixels(engine, p)
    first = set(Path(engine._cache_dir.name).glob('stickers-*.mkv'))
    assert len(first) == 1
    overlay.keyframes[0].x = 28
    overlay.keyframes[0].opacity = .5
    changed = compiled_pixels(engine, p)
    assert len(set(Path(engine._cache_dir.name).glob('stickers-*.mkv')) - first) == 1
    assert original[0][0, 12, 8] > 240
    assert changed[0][0, 12, 8] == 64
    assert 150 <= changed[0][0, 12, 32] <= 160
    assert not list(Path(engine._cache_dir.name).glob('*.partial.mkv'))


def test_cancelling_real_art_encoder_reaps_processes_and_removes_partial_files(
        tmp_path, engine, monkeypatch):
    import lumen.overlay_render as compositor
    overlay, _ = sticker(tmp_path, duration=30)
    p = Project(width=64, height=48, overlays=[overlay])
    cancel = threading.Event()
    compose = compositor.compose_frame
    popen = compositor.subprocess.Popen
    processes = []

    def cancel_frame(*args, **kwargs):
        result = compose(*args, **kwargs)
        cancel.set()
        return result

    def remember(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(compositor, 'compose_frame', cancel_frame)
    monkeypatch.setattr(compositor.subprocess, 'Popen', remember)
    try:
        with pytest.raises(RenderCancelled):
            engine.render(p, tmp_path / 'cancelled.mov', cancel=cancel,
                          export_profile=PROFILE)
        assert processes and all(process.poll() is not None for process in processes)
        assert all(process.wait(timeout=1) is not None for process in processes)
        assert not (tmp_path / 'cancelled.mov').exists()
        assert not list(Path(engine._cache_dir.name).glob('*.partial.mkv'))
        assert not list(tmp_path.glob('.lumen-render-*'))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_cached_art_still_honors_cancel_without_mutating_project(tmp_path, engine):
    overlay, _ = sticker(tmp_path, duration=2)
    p = Project(width=64, height=48, overlays=[overlay])
    before = deepcopy(p.to_dict())
    compiled_pixels(engine, p)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RenderCancelled):
        engine.render(p, tmp_path / 'cancelled-cache.mov', cancel=cancel,
                      export_profile=PROFILE)
    assert p.to_dict() == before
    assert not (tmp_path / 'cancelled-cache.mov').exists()
    assert not list(Path(engine._cache_dir.name).glob('*.partial.mkv'))
    assert not list(tmp_path.glob('.lumen-render-*'))


def test_special_asset_and_cache_paths_are_argv_data_not_filter_expressions(
        tmp_path, engine):
    special = tmp_path / ("cache's [a],;" + (' colon:' if os.name != 'nt' else ''))
    special.mkdir()
    engine._cache_dir.cleanup()
    engine._cache_dir = tempfile.TemporaryDirectory(dir=special)
    # Colon-containing imported asset paths are intentionally rejected by the
    # shared asset validator (portable alternate-data-stream protection).
    overlay, _ = sticker(tmp_path, name="quote's [a],; sticker.png", duration=1)
    p = Project(width=64, height=48, overlays=[overlay])
    output = engine.render(p, tmp_path / 'escaped.mov', export_profile=PROFILE)
    assert output.is_file() and output.stat().st_size > 0
    assert not list(tmp_path.glob('.lumen-render-*'))
    assert not list(Path(engine._cache_dir.name).glob('*.partial.mkv'))

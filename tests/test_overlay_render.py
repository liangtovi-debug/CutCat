"""Frame/pixel/audio verification for the real shared sticker/effect graph."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading

import numpy as np
from PySide6.QtGui import QColor, QImage
import pytest

from lumen.media import ffmpeg_available, ffmpeg_binary, probe
from lumen.model import Caption, CaptionStyle, Clip, ClipEffect, Grade, Overlay, OverlayKeyframe, Project
from lumen.overlay_render import (
    FrameImageCache, OverlayRenderCancelled, OverlayRenderError, animation_frame,
    compose_frame, effect_filters, evaluate_overlay, render_overlay_segment,
)
from lumen.render import RenderCancelled, RenderEngine, RenderError
from lumen.sticker_assets import load_sticker


def png(path, color, width=16, height=16):
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor(*color))
    assert image.save(str(path), 'PNG')
    return path


def sticker(tmp_path, *, color=(255, 0, 0, 255), name='red.png', start=0, duration=8, **kwargs):
    path = png(tmp_path / name, color)
    asset = load_sticker(path)
    overlay = Overlay(name, str(path), asset.digest, start, duration, **kwargs)
    return overlay, asset


def rgba(image):
    return np.frombuffer(image.constBits(), dtype=np.uint8).reshape(image.height(), image.width(), 4).copy()


def ff(*args):
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-y', *map(str, args)], capture_output=True, check=True)


def decoded(path, width=64, height=48, pix_fmt='rgb24'):
    result = subprocess.run([ffmpeg_binary(), '-v', 'error', '-i', str(path), '-an', '-pix_fmt', pix_fmt,
                             '-f', 'rawvideo', 'pipe:1'], capture_output=True, check=True)
    return np.frombuffer(result.stdout, np.uint8).reshape(-1, height, width, 4 if pix_fmt == 'rgba' else 3)


def audio_bytes(path):
    result = subprocess.run([ffmpeg_binary(), '-v', 'error', '-i', str(path), '-vn', '-ac', '2',
                             '-ar', '48000', '-f', 'f32le', 'pipe:1'], capture_output=True, check=True)
    return result.stdout


@pytest.fixture
def engine():
    value = RenderEngine(timeout=30)
    yield value
    value.close()


@pytest.fixture
def footage(tmp_path):
    target = tmp_path / 'checker.mkv'
    ff('-f', 'lavfi', '-i', 'color=gray:s=64x48:r=30:d=1,drawbox=x=32:y=0:w=32:h=48:color=white:t=fill',
       '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1',
       '-c:v', 'ffv1', '-c:a', 'pcm_s16le', target)
    return probe(target)


def test_relative_keyframes_linear_interpolation_and_hold():
    overlay = Overlay('o', '/unused.png', '0' * 64, 5, 11, keyframes=[
        OverlayKeyframe(2, 10, 20, 1, .2), OverlayKeyframe(8, 22, 8, 2.5, .8)])
    assert evaluate_overlay(overlay, 4) is None
    assert evaluate_overlay(overlay, 16) is None
    assert evaluate_overlay(overlay, 5).x == 10
    pose = evaluate_overlay(overlay, 10)
    assert (pose.x, pose.y, pose.scale, pose.local_frame) == (16, 14, 1.75, 5)
    assert pose.opacity == pytest.approx(.5)
    assert evaluate_overlay(overlay, 15).x == 22
    overlay.enabled = False
    assert evaluate_overlay(overlay, 10) is None
    with pytest.raises(OverlayRenderError, match='integer'):
        evaluate_overlay(overlay, 10.0)


def test_animation_loop_and_last_frame_hold(tmp_path):
    a, first = sticker(tmp_path)
    second_path = png(tmp_path / 'blue.png', (0, 0, 255, 255))
    asset = replace(first, frames=(first.frames[0], second_path), frame_duration=2, loop=True,
                    frame_sha256=(first.frame_sha256[0], hashlib.sha256(second_path.read_bytes()).hexdigest()))
    assert [animation_frame(asset, i) for i in range(7)] == [0, 0, 1, 1, 0, 0, 1]
    assert [animation_frame(replace(asset, loop=False), i) for i in range(7)] == [0, 0, 1, 1, 1, 1, 1]
    project = Project(width=64, height=48, overlays=[a])
    assert rgba(compose_frame(project, 2, {a.id: asset}))[4, 4].tolist() == [0, 0, 255, 255]


def test_alpha_z_order_equal_layer_order_and_bypass(tmp_path):
    red, ar = sticker(tmp_path, color=(255, 0, 0, 128))
    blue, ab = sticker(tmp_path, name='blue.png', color=(0, 0, 255, 255), z_order=-1)
    project = Project(width=64, height=48, overlays=[red, blue])
    assets = {red.id: ar, blue.id: ab}
    frame = rgba(compose_frame(project, 0, assets))
    assert frame[4, 4].tolist() == [128, 0, 127, 255]
    assert frame[30, 30].tolist() == [0, 0, 0, 0]
    blue.z_order = red.z_order
    assert rgba(compose_frame(project, 0, assets))[4, 4].tolist() == [0, 0, 255, 255]
    blue.enabled = False
    red.keyframes[0].opacity = .5
    center = rgba(compose_frame(project, 0, assets))[4, 4]
    assert center[0] == 255 and 63 <= center[3] <= 64


def test_top_left_uniform_scale_and_canvas_clipping(tmp_path):
    overlay, asset = sticker(tmp_path, keyframes=[OverlayKeyframe(0, -4, 4, 2, 1)])
    project = Project(width=64, height=48, overlays=[overlay])
    frame = rgba(compose_frame(project, 0, {overlay.id: asset}))
    assert frame[4:36, :28, 3].min() == 255
    assert frame[:4, :, 3].max() == 0
    assert frame[36:, :, 3].max() == 0
    assert frame[:, 28:, 3].max() == 0
    overlay.keyframes[0].x = -16384
    assert rgba(compose_frame(project, 0, {overlay.id: asset})).max() == 0


def test_image_cache_has_bounded_decoded_memory(tmp_path):
    paths = [png(tmp_path / f'{i}.png', (i, 0, 0, 255)) for i in range(5)]
    cache = FrameImageCache(max_bytes=16 * 16 * 4 * 2)
    for path in paths:
        cache.get(path)
    assert cache._bytes == 16 * 16 * 4 * 2
    assert len(cache._images) == 2


def test_effect_whitelist_exact_zero_disabled_and_order():
    values = [ClipEffect('a', 'blur', 0), ClipEffect('b', 'vignette', .5, enabled=False)]
    assert effect_filters(values) == []
    values = [ClipEffect('a', 'blur', 2), ClipEffect('b', 'vignette', .5)]
    filters = effect_filters(values)
    assert filters[0].startswith('gblur=sigma=2:')
    assert filters[1].startswith('vignette=angle=')
    assert effect_filters(list(reversed(values))) == list(reversed(filters))
    for kind, amount in [('movie=bad', 1), ('blur', float('nan')), ('blur', 31), ('vignette', -1), ('blur', True)]:
        with pytest.raises(OverlayRenderError):
            effect_filters([ClipEffect('a', kind, amount)])


requires_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg required')


@requires_ffmpeg
@pytest.mark.parametrize('num,den', [(24, 1), (30, 1), (30000, 1001)])
def test_exact_start_end_gates_and_shared_preview_export(tmp_path, engine, num, den):
    overlay, _ = sticker(tmp_path, start=1, duration=3, name="quote's [a],; image.png")
    tail = deepcopy(overlay)
    tail.id, tail.start, tail.duration, tail.enabled = 'disabled-tail', 7, 1, False
    project = Project(width=64, height=48, fps_num=num, fps_den=den, overlays=[overlay, tail])
    project.save(tmp_path / 'timeline.lumen')
    project = Project.load(tmp_path / 'timeline.lumen')
    exported = engine.render(project, tmp_path / 'out.mp4')
    preview = engine.preview(project)
    export_frames, preview_frames = decoded(exported), decoded(preview)
    for frames in (export_frames, preview_frames):
        assert len(frames) == 8
        for index in (0, 4, 5, 6, 7):
            assert frames[index].max() <= 5
        for index in (1, 2, 3):
            assert frames[index, 8, 8, 0] >= 245
            assert frames[index, 8, 8, 1:].max() <= 8
    assert np.abs(export_frames.astype(float) - preview_frames).mean() < 2
    assert (probe(exported).fps_num, probe(exported).fps_den) == (num, den)
    assert engine.preview(project) == preview
    assert engine.frame(project, 2, width=64).startswith(b'\x89PNG')


@requires_ffmpeg
@pytest.mark.parametrize('loop', [True, False])
def test_frame_pack_animation_and_interpolated_transform_pixels(tmp_path, engine, loop):
    png(tmp_path / 'r.png', (255, 0, 0, 255), 8, 8)
    png(tmp_path / 'b.png', (0, 0, 255, 255), 8, 8)
    manifest = tmp_path / 'pack.json'
    manifest.write_text(json.dumps({'version': 1, 'frames': ['r.png', 'b.png'], 'frame_duration': 2, 'loop': loop}))
    asset = load_sticker(manifest)
    overlay = Overlay('o', str(manifest), asset.digest, 1, 7, keyframes=[
        OverlayKeyframe(0, 0, 4, 1, 1), OverlayKeyframe(6, 24, 4, 1, 1)])
    project = Project(width=64, height=48, overlays=[overlay])
    frames = decoded(engine.render(project, tmp_path / 'animated.mp4'))
    expected = [0, 0, 2, 2, 0, 0, 2] if loop else [0, 0, 2, 2, 2, 2, 2]
    for local, channel in enumerate(expected):
        assert frames[local + 1, 8, local * 4 + 4, channel] > 230
        assert frames[local + 1, 35, 50].max() <= 5
    assert frames[0].max() <= 5


@requires_ffmpeg
def test_lossless_intermediate_preserves_straight_alpha(tmp_path):
    overlay, asset = sticker(tmp_path, color=(100, 200, 50, 128), duration=3,
                             keyframes=[OverlayKeyframe(0, 4, 6, 1.5, .5)])
    project = Project(width=64, height=48, overlays=[overlay])
    target = tmp_path / 'alpha.mkv'
    render_overlay_segment(project, {overlay.id: asset}, target, timeout=10)
    frames = decoded(target, pix_fmt='rgba')
    assert len(frames) == 3
    assert np.array_equal(frames[0], rgba(compose_frame(project, 0, {overlay.id: asset})))
    assert frames[0, 40, 50, 3] == 0
    assert 63 <= frames[0, 10, 10, 3] <= 64


@requires_ffmpeg
def test_real_blur_vignette_order_identity_and_grade(tmp_path, engine, footage):
    clip = Clip('c', footage.id, 0, 0, 0, 5)
    project = Project(width=64, height=48, media=[footage], clips=[clip])
    original = decoded(engine.render(project, tmp_path / 'original.mp4'))
    clip.effects = [ClipEffect('blur', 'blur', 0), ClipEffect('vignette', 'vignette', 1, enabled=False)]
    bypass = decoded(engine.render(project, tmp_path / 'bypass.mp4'))
    assert np.array_equal(original, bypass)
    clip.effects = [ClipEffect('blur', 'blur', 6)]
    blurred = decoded(engine.render(project, tmp_path / 'blur.mp4'))
    # A real Gaussian spreads the bright right side across the vertical edge.
    assert blurred[2, 24, 28, 0] > original[2, 24, 28, 0] + 15
    assert blurred[2, 24, 35, 0] < original[2, 24, 35, 0] - 15
    clip.effects.append(ClipEffect('vignette', 'vignette', .9))
    first = decoded(engine.render(project, tmp_path / 'blur-vignette.mp4'))
    clip.effects.reverse()
    second = decoded(engine.render(project, tmp_path / 'vignette-blur.mp4'))
    assert np.abs(first.astype(float) - second).mean() > 1
    assert first[2, 2, 60, 0] < blurred[2, 2, 60, 0] - 70
    assert project.clips[0].effects[0].kind == 'vignette'  # Reprobe did not reset effects.
    clip.effects = [ClipEffect('v', 'vignette', .4)]
    clip.grade = Grade(exposure=-1)
    graded = decoded(engine.render(project, tmp_path / 'graded.mp4'))
    clip.grade.enabled = False
    ungraded = decoded(engine.render(project, tmp_path / 'ungraded.mp4'))
    assert ungraded[2, 24, 20, 0] > graded[2, 24, 20, 0] + 30


@requires_ffmpeg
def test_stickers_after_effects_before_captions_and_audio_unchanged(tmp_path, engine, footage):
    from lumen.caption_render import CaptionRenderError, font_identity
    clip = Clip('c', footage.id, 0, 0, 0, 8)
    project = Project(width=64, height=48, media=[footage], clips=[clip])
    base = engine.render(project, tmp_path / 'base.mp4')
    overlay, _ = sticker(tmp_path, color=(255, 0, 0, 255), duration=8)
    project.overlays = [overlay]
    clip.grade = Grade(exposure=-5)
    clip.effects = [ClipEffect('b', 'blur', 20), ClipEffect('v', 'vignette', 1)]
    out = engine.render(project, tmp_path / 'effects-and-sticker.mp4')
    assert decoded(out)[4, 8, 8, 0] > 240  # Sticker is not graded/blurred/vignetted.
    assert audio_bytes(base) == audio_bytes(out)
    project.caption_style = CaptionStyle(font_family='DejaVu Sans', font_size=12, margin_bottom=2)
    try:
        font_identity(project.caption_style)
    except CaptionRenderError:
        return  # Caption rendering has its own portable font coverage tests.
    project.captions = [Caption('c', 0, 8, 'A')]
    _, graph, _ = engine._compile(project)
    assert graph.index('gblur=') < graph.index('[stickered]') < graph.index('drawtext=')
    assert decoded(engine.render(project, tmp_path / 'captioned.mp4')).max() > 240


@requires_ffmpeg
def test_cache_hit_rehashes_changed_png_even_with_preserved_size_mtime(tmp_path, engine):
    overlay, _ = sticker(tmp_path, duration=3)
    project = Project(width=64, height=48, overlays=[overlay])
    engine.preview(project)
    source = Path(overlay.asset_path)
    before = source.stat()
    # Both are valid PNGs with equal byte length. Restoring mtime must not
    # bypass the expected content digest even though decoding still succeeds.
    png(source, (0, 0, 255, 255))
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert source.stat().st_size == before.st_size
    with pytest.raises(RenderError, match='changed since import'):
        engine.preview(project)
    assert not list(Path(engine._cache_dir.name).glob('*.partial.mkv'))


@requires_ffmpeg
def test_mid_composition_change_rejected_before_atomic_cache_publication(tmp_path, engine, monkeypatch):
    import lumen.overlay_render as compositor
    overlay, _ = sticker(tmp_path, duration=3)
    project = Project(width=64, height=48, overlays=[overlay])
    original = compositor.render_overlay_segment
    def replacing(*args, **kwargs):
        original(*args, **kwargs)
        png(Path(overlay.asset_path), (0, 0, 255, 255))
    monkeypatch.setattr(compositor, 'render_overlay_segment', replacing)
    with pytest.raises(RenderError, match='sticker'):
        engine.render(project, tmp_path / 'never.mp4')
    assert not (tmp_path / 'never.mp4').exists()
    assert not list(Path(engine._cache_dir.name).glob('stickers-*'))


@requires_ffmpeg
def test_cancel_running_compositor_reaps_and_removes_temporary(tmp_path, engine, monkeypatch):
    import lumen.overlay_render as compositor
    overlay, _ = sticker(tmp_path, duration=60)
    project = Project(width=64, height=48, overlays=[overlay])
    cancel = threading.Event()
    original = compositor.compose_frame
    def cancel_during_frame(*args, **kwargs):
        result = original(*args, **kwargs)
        cancel.set()
        return result
    monkeypatch.setattr(compositor, 'compose_frame', cancel_during_frame)
    processes = []
    popen = compositor.subprocess.Popen
    def remember(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(compositor.subprocess, 'Popen', remember)
    with pytest.raises(RenderCancelled):
        engine.render(project, tmp_path / 'cancel.mp4', cancel=cancel)
    assert processes and all(process.poll() is not None for process in processes)
    assert not (tmp_path / 'cancel.mp4').exists()
    assert not list(Path(engine._cache_dir.name).glob('*.partial.mkv'))
    assert not list(tmp_path.glob('.lumen-render-*'))


@requires_ffmpeg
def test_timeout_and_existing_output_safety(tmp_path, engine):
    overlay, _ = sticker(tmp_path, duration=20)
    project = Project(width=64, height=48, overlays=[overlay])
    target = tmp_path / 'timeout.mkv'
    with pytest.raises(OverlayRenderError, match='timed out'):
        render_overlay_segment(project, {overlay.id: load_sticker(overlay.asset_path)}, target, timeout=0)
    assert not target.exists()
    existing = tmp_path / 'existing.mp4'
    existing.write_bytes(b'keep')
    with pytest.raises(RenderError, match='exists'):
        engine.render(project, existing)
    assert existing.read_bytes() == b'keep'


@requires_ffmpeg
def test_keyframed_opacity_and_scale_reach_real_encoded_pixels(tmp_path, engine):
    overlay, _ = sticker(tmp_path, duration=5, keyframes=[
        OverlayKeyframe(0, 0, 0, 1, 0), OverlayKeyframe(4, 16, 8, 2, 1)])
    project = Project(width=64, height=48, overlays=[overlay])
    for path in (engine.render(project, tmp_path / 'keyframes.mp4'), engine.preview(project)):
        frames = decoded(path)
        assert frames[0].max() <= 5
        assert 120 <= frames[2, 12, 16, 0] <= 135
        assert frames[2, 12, 16, 1:].max() <= 8
        assert frames[4, 24, 40, 0] >= 245  # Scaled to width32, at x16.
        assert frames[4, 44, 60].max() <= 5


@requires_ffmpeg
def test_disabled_missing_asset_skips_decode_but_preserves_extent(tmp_path, engine):
    overlay = Overlay('disabled', str(tmp_path / 'missing.json'), '0' * 64, 3, 2, enabled=False)
    project = Project(width=64, height=48, overlays=[overlay])
    frames = decoded(engine.render(project, tmp_path / 'disabled.mp4'))
    assert len(frames) == 5 and frames.max() <= 5


@requires_ffmpeg
def test_export_alias_cannot_replace_sticker_input(tmp_path, engine):
    overlay, _ = sticker(tmp_path, duration=2)
    project = Project(width=64, height=48, overlays=[overlay])
    before = Path(overlay.asset_path).read_bytes()
    alias = tmp_path / 'alias.mp4'
    try:
        alias.symlink_to(overlay.asset_path)
    except (OSError, NotImplementedError):
        pytest.skip('Symlinks unavailable')
    with pytest.raises(RenderError, match='exists'):
        engine.render(project, alias)
    assert Path(overlay.asset_path).read_bytes() == before


@requires_ffmpeg
def test_successful_preview_cache_still_honors_cancellation(tmp_path, engine):
    overlay, _ = sticker(tmp_path, duration=2)
    project = Project(width=64, height=48, overlays=[overlay])
    engine.preview(project)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RenderCancelled):
        engine.preview(project, cancel=cancel)


@requires_ffmpeg
def test_png_alpha_and_keyframe_opacity_blend_over_video(tmp_path, engine, footage):
    clip = Clip('c', footage.id, 0, 0, 0, 4)
    project = Project(width=64, height=48, media=[footage], clips=[clip])
    baseline = decoded(engine.render(project, tmp_path / 'baseline.mp4'))
    overlay, _ = sticker(tmp_path, duration=4, color=(255, 0, 0, 128),
                         keyframes=[OverlayKeyframe(0, 0, 0, 1, .5)])
    project.overlays = [overlay]
    output = decoded(engine.render(project, tmp_path / 'blend.mp4'))
    expected = baseline[2, 8, 8].astype(float) * .75 + np.array([255, 0, 0]) * .25
    assert np.abs(output[2, 8, 8].astype(float) - expected).max() < 6
    assert np.abs(output[2, 32, 48].astype(float) - baseline[2, 32, 48]).max() < 3


def test_oversized_canvas_rejected_before_qimage_or_encoder(tmp_path, engine, monkeypatch):
    import lumen.overlay_render as compositor
    overlay, asset = sticker(tmp_path, duration=2)
    project = Project(width=16384, height=16384, overlays=[overlay])
    def must_not_allocate(*args, **kwargs):
        pytest.fail('Oversized project reached an image allocation or subprocess')
    monkeypatch.setattr(compositor, 'QImage', must_not_allocate)
    monkeypatch.setattr(compositor.subprocess, 'Popen', must_not_allocate)
    with pytest.raises(OverlayRenderError, match='smaller project resolution'):
        compose_frame(project, 0, {overlay.id: asset})
    with pytest.raises(OverlayRenderError, match='smaller project resolution'):
        render_overlay_segment(project, {overlay.id: asset}, tmp_path / 'large.mkv', timeout=1)
    with pytest.raises(RenderError, match='smaller project resolution'):
        engine._verify_overlay_sources(project)
    assert not (tmp_path / 'large.mkv').exists()
    compositor.validate_canvas(Project(width=3840, height=2160))
    compositor.validate_canvas(Project(width=4096, height=4096))


@requires_ffmpeg
def test_changed_then_restored_frame_buffer_cannot_enter_render_cache(tmp_path, engine, monkeypatch):
    import lumen.sticker_assets as assets
    overlay, _ = sticker(tmp_path, duration=2)
    project = Project(width=64, height=48, overlays=[overlay])
    alternate = png(tmp_path / 'alternate.png', (0, 0, 255, 255)).read_bytes()
    # Simulate a valid changed file being read, then restored before the final
    # whole-asset hash check. Immutable per-frame fingerprints must catch it.
    monkeypatch.setattr(assets, 'read_sticker_frame', lambda *args, **kwargs: alternate)
    with pytest.raises(RenderError, match='changed after verification'):
        engine.render(project, tmp_path / 'race.mp4')
    assert not (tmp_path / 'race.mp4').exists()
    assert not list(Path(engine._cache_dir.name).glob('stickers-*'))

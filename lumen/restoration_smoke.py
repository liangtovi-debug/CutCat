"""Bounded real-media acceptance for local spatial restoration.

The test card and tone are original procedural QA material, not user footage or
a claim of general perceptual quality. No downloaded assets or models are used.
Every reported picture metric measures decoded media from the real renderer.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import subprocess
import time

import numpy as np

WIDTH, HEIGHT, FPS, FRAMES = 320, 240, 24, 24


def _require(condition, message):
    if not condition:
        raise RuntimeError('Frozen restoration acceptance: ' + message)


def synthetic_restoration_frames():
    """Known flat, ramp, hard-edge and softly blurred-edge reference panels."""
    clean = np.full((HEIGHT, WIDTH, 3), 32, dtype=np.uint8)
    clean[16:104, 16:144] = 89
    ramp = np.rint(np.linspace(.32, .60, 128) * 255).astype(np.uint8)
    clean[16:104, 176:304] = ramp[None, :, None]
    clean[112:128, 16:160] = 51
    clean[112:128, 160:304] = 179
    clean[144:224, 16:160] = 51
    clean[144:224, 160:304] = 179
    degraded = np.repeat(clean[None], FRAMES, axis=0).astype(np.float32)
    rng = np.random.default_rng(731)
    degraded[:, 16:104, 16:144] += rng.normal(0, .018 * 255, (FRAMES, 88, 128, 3))
    # Small offsets in 8-pixel plateaus over a known smooth ramp. These weak
    # repeated steps are deliberately distinct from the strong real hard edge.
    block_offsets = (((np.arange(128) // 8) % 2) * 2 - 1) * (.012 * 255)
    degraded[:, 16:104, 176:304] += block_offsets[None, None, :, None]
    profile = clean[180, :, 0].astype(np.float32)
    softened = np.convolve(np.pad(profile, (2, 2), mode='edge'),
                           np.array([1, 4, 6, 4, 1]) / 16, mode='valid')
    degraded[:, 144:224, 16:304] = softened[None, None, 16:304, None]
    return clean, np.rint(np.clip(degraded, 0, 255)).astype(np.uint8)


def _decoded_frames(path):
    from .media import ffmpeg_binary
    decoded = subprocess.run([
        ffmpeg_binary(), '-v', 'error', '-nostdin', '-threads', '1',
        '-protocol_whitelist', 'file,pipe', '-i', str(path), '-map', '0:V:0', '-an',
        '-frames:v', str(FRAMES + 1), '-f', 'rawvideo', '-pix_fmt', 'rgb24',
        '-threads', '1', 'pipe:1',
    ], capture_output=True, check=True, timeout=30)
    _require(len(decoded.stdout) == FRAMES * HEIGHT * WIDTH * 3,
             'the decoded stream does not contain exactly 24 complete frames')
    return np.frombuffer(decoded.stdout, dtype=np.uint8).reshape(FRAMES, HEIGHT, WIDTH, 3)


def _decoded_audio(path):
    from .media import ffmpeg_binary
    decoded = subprocess.run([
        ffmpeg_binary(), '-v', 'error', '-nostdin', '-threads', '1',
        '-protocol_whitelist', 'file,pipe', '-i', str(path), '-map', '0:a:0', '-vn',
        '-t', str(FRAMES / FPS), '-ac', '1', '-ar', '48000', '-f', 'f32le', 'pipe:1',
    ], capture_output=True, check=True, timeout=30)
    audio = np.frombuffer(decoded.stdout, dtype='<f4')
    _require(len(audio) == round(FRAMES / FPS * 48000) and np.isfinite(audio).all(),
             'the rendered soundtrack changed sample count or contains nonfinite samples')
    return audio


def _quality_metrics(frames, clean):
    """Metrics on isolated card regions; values use the 0..255 code scale."""
    pictures = frames.astype(np.float64)
    flat = pictures[:, 24:96, 24:136]
    flat_error = flat - clean[24:96, 24:136]
    # Measure the extra step at each known plateau boundary after averaging
    # rows/channels. Reference slope remains present and is subtracted.
    ramp = pictures[:, 24:96, 184:296].mean(axis=(1, 3))
    reference = clean[24:96, 184:296].astype(np.float64).mean(axis=(0, 2))
    boundaries = np.arange(7, 111, 8)
    seam_error = np.diff(ramp, axis=1)[:, boundaries] - np.diff(reference)[boundaries]
    edge = pictures[:, 160:208, 154:166]
    edge_reference = clean[160:208, 154:166]
    left = float(pictures[:, 116:124, 120:144].mean())
    right = float(pictures[:, 116:124, 176:200].mean())
    return {
        'flat_rmse': float(np.sqrt(np.mean(flat_error ** 2))),
        'flat_temporal_rms': float(np.sqrt(np.mean(np.var(flat, axis=0)))),
        'block_boundary_rmse': float(np.sqrt(np.mean(seam_error ** 2))),
        'soft_edge_rmse': float(np.sqrt(np.mean((edge - edge_reference) ** 2))),
        'hard_edge_contrast': right - left,
        'edge_min': float(pictures[:, 160:208, 144:176].min()),
        'edge_max': float(pictures[:, 160:208, 144:176].max()),
    }


def run_restoration_smoke(engine, folder):
    """Render actual isolated stages and combined restoration, then verify."""
    from .media import ffmpeg_binary, probe
    from .model import Clip, Project
    from .restoration_settings import RestorationSettings

    started = time.monotonic()
    root = Path(folder) / 'restoration'
    root.mkdir(exist_ok=False)
    source = root / 'original-degraded-test-card.mkv'
    clean, degraded = synthetic_restoration_frames()
    subprocess.run([
        ffmpeg_binary(), '-v', 'error', '-nostdin', '-n', '-threads', '1',
        '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{WIDTH}x{HEIGHT}',
        '-r', str(FPS), '-i', 'pipe:0', '-f', 'lavfi', '-i',
        f'sine=frequency=440:sample_rate=48000:duration={FRAMES / FPS}',
        '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'ffv1', '-threads', '1',
        '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'rgb',
        '-color_range', 'pc', '-c:a', 'pcm_s16le', '-t', str(FRAMES / FPS), str(source),
    ], input=degraded.tobytes(), capture_output=True, check=True, timeout=30)
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    media = probe(source)
    _require(media.has_audio, 'the original test tone was not included')
    settings = RestorationSettings(True, 1., .8, .6)
    project = Project(name='Original procedural local-restoration acceptance',
                      width=WIDTH, height=HEIGHT, fps_num=FPS, media=[media],
                      clips=[Clip('restoration', media.id, 0, 0, 0, FRAMES, restoration=settings)])
    project_file = root / 'restored-project.lumen'
    project.save(project_file)
    project = Project.load(project_file)
    _require(project.clips[0].restoration == settings, 'saved restoration settings did not round-trip')

    variants = {'bypass': RestorationSettings(False, 1., .8, .6),
                'deblock': RestorationSettings(True, 1., 0., 0.),
                'denoise': RestorationSettings(True, 0., .8, 0.),
                'sharpen': RestorationSettings(True, 0., 0., .6),
                'restored': settings}
    pictures, outputs, metrics = {}, {}, {}
    for name, variant in variants.items():
        candidate = deepcopy(project)
        candidate.clips[0].restoration = variant
        output = engine.render(candidate, root / f'{name}-output.mp4')
        pictures[name] = _decoded_frames(output)
        outputs[name] = output
        metrics[name] = _quality_metrics(pictures[name], clean)
        rendered_media = probe(output)
        _require((rendered_media.width, rendered_media.height) == (WIDTH, HEIGHT)
                 and abs(rendered_media.duration - FRAMES / FPS) <= 1 / FPS
                 and rendered_media.has_audio,
                 f'{name} changed output geometry, duration or audio presence')

    baseline = metrics['bypass']
    _require(baseline['flat_rmse'] > 1.5 and baseline['block_boundary_rmse'] > 2,
             'the actual encoded fixture lost its intended measurable degradation')
    _require(metrics['denoise']['flat_rmse'] < baseline['flat_rmse'] * .80,
             'denoise did not reduce the known flat-patch error by at least 20%')
    _require(metrics['denoise']['flat_temporal_rms'] < baseline['flat_temporal_rms'] * .85,
             'denoise did not reduce independently generated frame noise')
    _require(metrics['deblock']['block_boundary_rmse'] < baseline['block_boundary_rmse'] * .90,
             'deblock did not reduce the known weak block-edge error by at least 10%')
    _require(metrics['sharpen']['soft_edge_rmse'] < baseline['soft_edge_rmse'] * .99,
             'gentle sharpening did not improve the known softened edge')
    _require(metrics['restored']['flat_rmse'] < baseline['flat_rmse'] * .85
             and metrics['restored']['block_boundary_rmse'] < baseline['block_boundary_rmse'] * .95,
             'the combined stages did not improve both flat noise and weak block edges')
    for name in ('deblock', 'denoise', 'sharpen', 'restored'):
        _require(metrics[name]['hard_edge_contrast'] >= baseline['hard_edge_contrast'] * .97,
                 f'{name} erased the strong reference edge')
        _require(metrics[name]['edge_min'] >= 45 and metrics[name]['edge_max'] <= 185,
                 f'{name} introduced an excessive dark/bright halo in the encoded step')

    preview = engine.preview(project)
    _require(preview == engine.preview(project), 'unchanged restoration did not reuse its preview')
    bypass = deepcopy(project); bypass.clips[0].restoration.enabled = False
    bypass_preview = engine.preview(bypass)
    _require(bypass_preview != preview, 'restoration bypass did not invalidate the preview key')
    _require(preview == engine.preview(project), 'returning to restoration lost its existing preview')
    preview_frames = _decoded_frames(preview)
    preview_delta = float(np.abs(pictures['restored'].astype(np.float32) - preview_frames).mean())
    correction_delta = float(np.abs(pictures['restored'].astype(np.float32) - pictures['bypass']).mean())
    _require(correction_delta > .20, 'the enabled pipeline did not change real rendered pixels')
    _require(preview_delta < 4, 'preview/export pictures disagree beyond codec tolerance')
    baseline_audio = _decoded_audio(outputs['bypass'])
    audio_rms = float(np.sqrt(np.mean(baseline_audio ** 2)))
    _require(audio_rms > .03, 'the original audible tone became silent')
    audio_delta = max(float(np.abs(_decoded_audio(path) - baseline_audio).max())
                      for path in (outputs['restored'], preview))
    _require(audio_delta < 1e-6, 'a picture-only restoration changed decoded soundtrack samples')
    png = engine.frame(project, FRAMES // 2, width=WIDTH)
    _require(png.startswith(b'\x89PNG\r\n\x1a\n'), 'viewer frame extraction did not return a PNG')
    (root / 'restored-preview.png').write_bytes(png)
    _require(hashlib.sha256(source.read_bytes()).hexdigest() == source_sha,
             'restoration modified the original source bytes')
    return project, preview, {
        'fixture': 'original procedural flat noise, weak 8-pixel ramp blocks, hard/soft edges and 440 Hz tone',
        'quality_scope': 'bounded synthetic spatial QA only; not natural-footage perceptual or temporal certification',
        'source_sha256': source_sha, 'source_unchanged': True,
        'settings': settings.to_dict(), 'source_frames': FRAMES,
        'output_frames': len(pictures['restored']), 'preview_frames': len(preview_frames),
        'fps': FPS, 'size': [WIDTH, HEIGHT], 'decoded_picture_metrics': metrics,
        'isolated_deblock_improved': True, 'isolated_denoise_improved': True,
        'isolated_sharpen_improved': True, 'combined_noise_and_blocks_improved': True,
        'strong_edge_and_halo_bounds_preserved': True,
        'correction_mean_pixel_difference': correction_delta,
        'preview_export_mean_pixel_difference': preview_delta,
        'preview_cache_reused': True, 'restoration_bypass_invalidates_preview': True,
        'audio_samples': len(baseline_audio), 'audio_rms': audio_rms,
        'audio_max_sample_difference': audio_delta, 'audio_unchanged': True,
        'rendered_file': str(outputs['restored'].relative_to(folder)),
        'project_file': str(project_file.relative_to(folder)),
        'elapsed_seconds': round(time.monotonic() - started, 3),
    }


def capture_restoration_ui(window, project, preview, folder, screenshots):
    """Apply/undo genuine controls, decode actual restored media and capture."""
    from PySide6.QtCore import QPoint, QUrl
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    from .model import Editor

    app = QApplication.instance()
    window.player.stop(); window.player.setSource(QUrl()); window.video.image = QImage()
    window.editor = Editor(deepcopy(project)); window.revision += 1; window.dirty = False
    window.refresh(); window.select_clip('restoration')
    window.side_tabs.setCurrentIndex(0); window.inspector_tabs.setCurrentIndex(0)
    before, revision = deepcopy(window.project.to_dict()), window.revision
    window.restoration_denoise.setValue(.55)
    _require(window.project.to_dict() == before, 'staging changed the project before Apply')
    window.apply_restoration_button.click()
    _require(window.selected_clip().restoration.denoise == .55 and window.revision == revision + 1,
             'Apply did not perform one actual undoable restoration edit')
    window.apply_restoration_button.click()
    _require(window.revision == revision + 1, 'repeated Apply committed an unchanged edit')
    window.undo()
    _require(window.project.to_dict() == before and window.revision == revision + 2,
             'Undo did not restore the exact restoration project')
    window.restoration_enabled.setChecked(False)
    _require(window.project.to_dict() == before, 'staging bypass changed the project before Apply')
    window.apply_restoration_button.click()
    _require(not window.selected_clip().restoration.enabled
             and window.selected_clip().restoration.denoise == .8,
             'bypass discarded stored restoration strengths')
    window.undo()
    _require(window.project.to_dict() == before, 'restoration bypass could not be undone')
    window.activate_preview(preview, window.revision, 'graded'); window.player.play()
    deadline = time.monotonic() + 10
    while (window.video.image.isNull() or window.player.position() < 400) and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.01)
    window.player.pause()
    _require(not window.video.image.isNull(), 'native viewer did not decode the real restored preview')
    window.scopes.set_image(window.video.image)
    invariant = deepcopy((window.project.to_dict(), window.revision, window.dirty,
                          [item.to_dict() for item in window.editor._undo],
                          [item.to_dict() for item in window.editor._redo]))
    # Reserve enough vertical space to show the complete section, including
    # its limitations and Apply/Reset controls, with actual host font metrics.
    window.vertical_split.setSizes([800, 120])
    for locale in ('zh-CN', 'en'):
        window.set_ui_language(locale, persist=False)
        for mode in ('simple', 'professional'):
            window.set_workspace_mode(mode, persist=False)
            for _ in range(10): app.processEvents()
            _require(invariant == (window.project.to_dict(), window.revision, window.dirty,
                                    [item.to_dict() for item in window.editor._undo],
                                    [item.to_dict() for item in window.editor._redo]),
                     'workspace/language switching changed the restoration editing state')
        group_top = window.restoration_group.mapTo(window.inspector.widget(), QPoint(0, 0)).y()
        window.inspector.verticalScrollBar().setValue(max(0, group_top - 8))
        for _ in range(10): app.processEvents()
        top = window.restoration_group.mapTo(window.inspector.viewport(), QPoint(0, 0)).y()
        _require(top >= 0 and top + window.restoration_group.height() <= window.inspector.viewport().height(),
                 'the restoration screenshot would clip its controls or limitations')
        expected = '应用画质修复' if locale == 'zh-CN' else 'Apply restoration'
        _require(window.apply_restoration_button.text() == expected,
                 'the packaged restoration catalog was not translated')
        filename = f'frozen-ui-{locale}-restoration.png'
        _require(window.grab().save(str(Path(folder) / filename)), 'restoration screenshot failed')
        screenshots.append(filename)
    return {'ui_apply_and_undo_verified': True, 'ui_bypass_preserves_strengths': True,
            'ui_preview_decoded': True, 'ui_locales': ['zh-CN', 'en'],
            'ui_locale_workspace_state_preserved': True, 'ui_complete_group_visible': True}

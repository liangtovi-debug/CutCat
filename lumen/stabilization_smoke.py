"""Bounded real-media acceptance for source and frozen native packages.

All images are generated procedurally here. There are no downloads or external
media fixtures, and no synthetic motion estimates are passed to the renderer.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import subprocess
import time

import numpy as np

WIDTH, HEIGHT, FPS, FRAMES = 320, 240, 30, 30


def _require(condition, message):
    if not condition:
        raise RuntimeError('Frozen stabilization acceptance: ' + message)


def synthetic_shake_frames():
    """Original textured scene with a deterministic, known two-axis shake."""
    rng = np.random.default_rng(421)
    base = np.repeat(np.repeat(rng.integers(0, 256, (20, 32, 3), dtype=np.uint8), 12, 0), 10, 1)
    for _ in range(2):
        base = ((base.astype(np.uint16) + np.roll(base, 1, 0)
                 + np.roll(base, 1, 1)) // 3).astype(np.uint8)
    shifts = [(round(5 * np.sin(i * 1.2)), round(4 * np.cos(i * .9))) for i in range(FRAMES)]
    return np.stack([np.roll(base, (y, x), (0, 1)) for x, y in shifts]), shifts


def _decoded_frames(path):
    from .media import ffmpeg_binary
    result = subprocess.run([
        ffmpeg_binary(), '-v', 'error', '-nostdin', '-threads', '1',
        '-protocol_whitelist', 'file,pipe', '-i', str(path), '-an',
        '-frames:v', str(FRAMES + 1), '-f', 'rawvideo', '-pix_fmt', 'rgb24',
        '-threads', '1', 'pipe:1',
    ], capture_output=True, check=True, timeout=30)
    expected = FRAMES * HEIGHT * WIDTH * 3
    _require(len(result.stdout) == expected, 'the rendered stream does not contain exactly 30 full frames')
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(FRAMES, HEIGHT, WIDTH, 3)


def _translation_jitter(frames):
    """Diagnostic phase-correlation RMS between consecutive central images."""
    gray = frames[:, 32:-32, 40:-40].astype(np.float64).mean(axis=-1)
    window = np.outer(np.hanning(gray.shape[1]), np.hanning(gray.shape[2]))
    spectra = np.fft.fft2((gray - gray.mean(axis=(1, 2))[:, None, None]) * window)
    shifts = []
    for previous, current in zip(spectra, spectra[1:]):
        cross = current * np.conj(previous)
        cross /= np.maximum(np.abs(cross), 1e-12)
        correlation = np.fft.fftshift(np.fft.ifft2(cross).real)
        y, x = np.unravel_index(np.argmax(correlation), correlation.shape)
        shifts.append((x - gray.shape[2] // 2, y - gray.shape[1] // 2))
    return float(np.sqrt(np.mean(np.sum(np.square(shifts), axis=1))))


def run_stabilization_smoke(engine, folder):
    """Verify real analysis, nonidentity correction, export, preview and bytes."""
    from .media import ffmpeg_binary, probe
    from .model import Clip, Project
    from .stabilization_render import prepare_stabilization
    from .stabilization_settings import StabilizationSettings

    started = time.monotonic()
    root = Path(folder) / 'stabilization'
    root.mkdir(exist_ok=False)
    source = root / 'original-known-shake.mkv'
    frames, shifts = synthetic_shake_frames()
    subprocess.run([
        ffmpeg_binary(), '-v', 'error', '-nostdin', '-n', '-threads', '1',
        '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{WIDTH}x{HEIGHT}',
        '-r', str(FPS), '-i', 'pipe:0', '-c:v', 'ffv1', '-threads', '1',
        '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
        str(source),
    ], input=frames.tobytes(), capture_output=True, check=True, timeout=30)
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    media = probe(source)
    settings = StabilizationSettings(True, .8, 8, 1.2, 'crop')
    project = Project(name='Original camera-shake stabilization acceptance', width=WIDTH,
                      height=HEIGHT, fps_num=FPS, media=[media],
                      clips=[Clip('shake', media.id, 0, 0, 0, FRAMES, stabilization=settings)])
    project.save(root / 'stabilized-project.lumen')
    project = Project.load(root / 'stabilized-project.lumen')
    prepared = prepare_stabilization(engine, source, project.clips[0], project)
    matrices = np.asarray(prepared.inverse_matrices)
    _require(matrices.shape == (FRAMES, 3, 3) and np.isfinite(matrices).all(),
             'real analysis did not produce finite matrices for every source frame')
    dynamic_extent = float(np.max(np.std(matrices[:, :2, 2], axis=0)))
    _require(dynamic_extent > .25, 'the known shake produced no dynamic correction')
    _require(prepared.report['analysis']['source_sha256'] == source_sha,
             'analysis provenance does not match the original source bytes')
    output = engine.render(project, root / 'stabilized-output.mp4')
    preview = engine.preview(project)
    preview_again = engine.preview(project)
    _require(preview == preview_again, 'unchanged stabilization did not reuse its preview')
    rendered = _decoded_frames(output)
    preview_frames = _decoded_frames(preview)
    bypassed = deepcopy(project); bypassed.clips[0].stabilization.enabled = False
    before_output = engine.render(bypassed, root / 'unstabilized-output.mp4')
    original = _decoded_frames(before_output)
    correction_delta = float(np.abs(rendered.astype(np.float32) - original).mean())
    preview_delta = float(np.abs(rendered.astype(np.float32) - preview_frames).mean())
    _require(correction_delta > 1, 'enabled stabilization did not change the real rendered picture')
    _require(preview_delta < 8, 'preview and export pictures disagree beyond the codec tolerance')
    for path in (output, preview):
        output_media = probe(path)
        _require((output_media.width, output_media.height) == (WIDTH, HEIGHT)
                 and abs(output_media.duration - FRAMES / FPS) <= 1 / FPS,
                 'stabilization changed output geometry or duration')
    png = engine.frame(project, 15, width=WIDTH)
    _require(png.startswith(b'\x89PNG\r\n\x1a\n'), 'stabilized viewer extraction did not produce PNG')
    (root / 'stabilized-preview.png').write_bytes(png)
    _require(hashlib.sha256(source.read_bytes()).hexdigest() == source_sha,
             'stabilization modified the original source')
    before_jitter, after_jitter = _translation_jitter(original), _translation_jitter(rendered)
    _require(before_jitter > 1 and after_jitter < before_jitter * .85,
             'real output did not sufficiently reduce the known translation shake')
    report = {
        'fixture': 'original procedural texture with deterministic two-axis translation',
        'known_shifts_xy': shifts, 'source_sha256': source_sha,
        'source_unchanged': True, 'settings': settings.to_dict(),
        'source_frames': FRAMES, 'output_frames': len(rendered), 'preview_frames': len(preview_frames),
        'fps': FPS, 'size': [WIDTH, HEIGHT], 'dynamic_correction_std_px': dynamic_extent,
        'real_analysis': prepared.report, 'correction_mean_pixel_difference': correction_delta,
        'preview_export_mean_pixel_difference': preview_delta,
        'diagnostic_translation_jitter_before_px': before_jitter,
        'diagnostic_translation_jitter_after_px': after_jitter,
        'known_translation_jitter_reduced': True,
        'preview_cache_reused': True, 'rendered_file': str(output.relative_to(folder)),
        'project_file': str((root / 'stabilized-project.lumen').relative_to(folder)),
        'elapsed_seconds': round(time.monotonic() - started, 3),
    }
    return project, preview, report


def capture_stabilization_ui(window, project, preview, folder, screenshots):
    """Exercise packaged controls and capture real decoded stabilized playback."""
    from PySide6.QtCore import QPoint, QUrl
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    from .model import Editor

    app = QApplication.instance()
    window.player.stop(); window.player.setSource(QUrl()); window.video.image = QImage()
    window.editor = Editor(deepcopy(project)); window.revision += 1; window.dirty = False
    window.refresh(); window.select_clip('shake')
    window.side_tabs.setCurrentIndex(0); window.inspector_tabs.setCurrentIndex(0)
    before, revision = deepcopy(window.project.to_dict()), window.revision
    window.stabilization_strength.setValue(.7)
    _require(window.project.to_dict() == before, 'staging controls mutated the project before Apply')
    window.apply_stabilization_button.click()
    _require(window.selected_clip().stabilization.strength == .7 and window.revision == revision + 1,
             'Apply did not perform the actual undoable stabilization edit')
    window.undo()
    _require(window.project.to_dict() == before and window.revision == revision + 2,
             'Undo did not restore the rendered stabilization settings')
    window.stabilization_advanced_toggle.setChecked(True)
    window.activate_preview(preview, window.revision, 'graded'); window.player.play()
    deadline = time.monotonic() + 10
    while (window.video.image.isNull() or window.player.position() < 400) and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.01)
    window.player.pause()
    _require(not window.video.image.isNull(), 'the native viewer did not decode the stabilized preview')
    window.scopes.set_image(window.video.image)
    invariant = (window.project.to_dict(), window.revision, window.dirty)
    for locale in ('zh-CN', 'en'):
        window.set_ui_language(locale, persist=False)
        for _ in range(10): app.processEvents()
        # Frame the complete progressive section, not the preceding timing UI.
        group_top = window.stabilization_group.mapTo(window.inspector.widget(), QPoint(0, 0)).y()
        window.inspector.verticalScrollBar().setValue(max(0, group_top - 8))
        for _ in range(10): app.processEvents()
        _require(invariant == (window.project.to_dict(), window.revision, window.dirty),
                 'language switching changed stabilization project state')
        expected = '应用画面稳定' if locale == 'zh-CN' else 'Apply stabilization'
        _require(window.apply_stabilization_button.text() == expected,
                 'the packaged stabilization catalog was not translated')
        filename = f'frozen-ui-{locale}-stabilization.png'
        _require(window.grab().save(str(Path(folder) / filename)), 'stabilization screenshot failed')
        screenshots.append(filename)
    return {'ui_apply_and_undo_verified': True, 'ui_preview_decoded': True,
            'ui_locales': ['zh-CN', 'en'], 'ui_locale_state_preserved': True}

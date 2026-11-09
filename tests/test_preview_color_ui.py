"""Actual MainWindow acceptance of explicit mapped YUV preview conversion.

These tests inject CPU-backed QVideoFrames through the real QVideoSink. The
transport is synthetic; this is not native playback, native-platform, HDR, or
display-calibration evidence. Every saved UI image is an untouched QWidget
capture, and the color bars contain no user material.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QRect, QRectF, QSize
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lumen.i18n import set_locale, tr
from test_color_reference_ui import (activate_synthetic, app, choose, close_window,
    control_geometry, make_window, transaction_state, window)


ERROR_TEXT = ('Preview color unavailable. Unsupported format or color metadata; '
              'no guessed conversion is displayed.')
BAR_RGB = ((255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0),
           (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0))


def mapped_bars(*, variant=False, failure=None, number=0):
    """Encode 8 flat bars independently, then fill actual stride-aware NV12.

    The scalar oracle reconstructs the quantized YUV samples, rather than
    treating the pre-quantization RGB bars as exact. No production converter,
    Qt toImage(), or FFmpeg conversion participates in these expectations.
    """
    pixel_format = (QVideoFrameFormat.PixelFormat.Format_AYUV if failure == 'format'
                    else QVideoFrameFormat.PixelFormat.Format_NV12)
    fmt = QVideoFrameFormat(QSize(640, 360), pixel_format)
    fmt.setColorSpace(QVideoFrameFormat.ColorSpace.ColorSpace_BT709)
    fmt.setColorRange(QVideoFrameFormat.ColorRange.ColorRange_Video)
    fmt.setColorTransfer(QVideoFrameFormat.ColorTransfer.ColorTransfer_BT709)
    if failure == 'matrix':
        fmt.setColorSpace(QVideoFrameFormat.ColorSpace.ColorSpace_Undefined)
    elif failure == 'range':
        fmt.setColorRange(QVideoFrameFormat.ColorRange.ColorRange_Unknown)
    elif failure == 'transfer':
        fmt.setColorTransfer(QVideoFrameFormat.ColorTransfer.ColorTransfer_Unknown)
    elif failure == 'hdr':
        fmt.setColorTransfer(QVideoFrameFormat.ColorTransfer.ColorTransfer_ST2084)
    frame = QVideoFrame(fmt)
    assert frame.isValid()
    frame.setStartTime(round(number / 30 * 1_000_000))
    frame.setEndTime(round((number + 1) / 30 * 1_000_000))
    if failure == 'format':
        # This is a valid allocated Qt YUV format, but not an accepted decoder
        # layout. Its bytes are irrelevant because format rejection is first.
        return frame, None
    encoded, decoded = [], []
    for original in BAR_RGB:
        r, g, b = ((value * .60 / 255 if variant else value / 255) for value in original)
        y = .2126 * r + .7152 * g + .0722 * b
        cb, cr = (b - y) / 1.8556, (r - y) / 1.5748
        yc, uc, vc = round(16 + 219 * y), round(128 + 224 * cb), round(128 + 224 * cr)
        encoded.append((yc, uc, vc))
        yy, uu, vv = (yc - 16) / 219, (uc - 128) / 224, (vc - 128) / 224
        rr, bb = yy + 1.5748 * vv, yy + 1.8556 * uu
        gg = (yy - .2126 * rr - .0722 * bb) / .7152
        decoded.append(tuple(round(min(1., max(0., value)) * 255) for value in (rr, gg, bb)))
    assert frame.map(QVideoFrame.MapMode.WriteOnly)
    try:
        y_plane = np.frombuffer(frame.bits(0), dtype=np.uint8).reshape(360, frame.bytesPerLine(0))
        uv_plane = np.frombuffer(frame.bits(1), dtype=np.uint8).reshape(180, frame.bytesPerLine(1))
        y_plane.fill(239); uv_plane.fill(239)  # Padding must never be sampled.
        for index, (y, u, v) in enumerate(encoded):
            y_plane[:, index * 80:(index + 1) * 80] = y
            uv_plane[:, index * 80:(index + 1) * 80:2] = u
            uv_plane[:, index * 80 + 1:(index + 1) * 80:2] = v
    finally:
        frame.unmap()
    expected = np.repeat(np.asarray(decoded, dtype=np.uint8), 80, axis=0)
    expected = np.tile(expected[None, :, :], (360, 1, 1))
    return frame, expected


def image_rgb(image):
    assert not image.isNull()
    rgb = image.convertToFormat(QImage.Format.Format_RGB888)
    raw = np.frombuffer(rgb.constBits(), dtype=np.uint8).reshape(rgb.height(), rgb.bytesPerLine())
    return raw[:, :rgb.width() * 3].reshape(rgb.height(), rgb.width(), 3).copy()


def inject(window, frame):
    # Force this acceptance through the scope sample cadence, then use the real
    # sink signal, receiver generation, provenance, converter, viewer and scopes.
    window.scope_counter = 0
    window.video.videoSink().setVideoFrame(frame)
    QApplication.processEvents()


def assert_pixels(image, expected):
    np.testing.assert_allclose(image_rgb(image), expected, rtol=0, atol=1)


def assert_current_histogram(window, expected):
    data = window.scopes.data
    assert data is not None and data.source_size == (640, 360)
    # 256 x 144 center samples: exactly 32 columns for each 80-pixel bar.
    samples = np.repeat(expected[0, ::80], 32 * 144, axis=0)
    for channel in range(3):
        np.testing.assert_array_equal(data.histogram[channel],
            np.bincount(samples[:, channel], minlength=256))


def painted_labels(viewer, monkeypatch):
    import lumen.ui_color_reference as reference_ui
    calls = []
    class RecordingPainter(QPainter):
        def drawText(self, *args):
            calls.append((QRectF(args[0]), str(args[-1])))
            return super().drawText(*args)
    with monkeypatch.context() as probe:
        probe.setattr(reference_ui, 'QPainter', RecordingPainter)
        viewer.grab()
    return calls


def test_corrected_frame_feeds_viewer_current_only_scopes_and_immutable_reference(window, app, tmp_path):
    w = window; activate_synthetic(w, tmp_path)
    before = transaction_state(w)
    source, expected_reference = mapped_bars()
    inject(w, source)
    assert_pixels(w.video.image, expected_reference)
    assert_current_histogram(w, expected_reference)
    assert w.capture_reference()
    frozen = w.video.reference; frozen_bytes = frozen.pixels
    assert_pixels(frozen.image, expected_reference)
    # Both returned reference images and producer-owned frame buffers may be
    # mutated without changing the immutable reference snapshot.
    detached = frozen.image; detached.fill(QColor('orange'))
    assert source.map(QVideoFrame.MapMode.WriteOnly)
    try:
        for plane in range(source.planeCount()):
            source.bits(plane)[:] = bytes(source.mappedBytes(plane))
    finally:
        source.unmap()
    current, expected_current = mapped_bars(variant=True)
    inject(w, current)
    for mode in ('side_by_side', 'wipe', 'off'):
        choose(w.comparison_mode_choice, mode); app.processEvents()
        assert_pixels(w.video.image, expected_current)
        assert_current_histogram(w, expected_current)
        assert w.video.reference is frozen and frozen.pixels == frozen_bytes
        assert_pixels(frozen.image, expected_reference)
    assert transaction_state(w) == before


@pytest.mark.parametrize('failure', ['format', 'matrix', 'range', 'transfer', 'hdr'])
def test_unsupported_frame_clears_current_and_scopes_preserves_still_and_recovers(window, app, tmp_path, monkeypatch, failure):
    w = window; activate_synthetic(w, tmp_path)
    frame, expected = mapped_bars(); inject(w, frame); assert w.capture_reference()
    frozen = w.video.reference; before = transaction_state(w)
    invalid, _ = mapped_bars(failure=failure); inject(w, invalid)
    assert w.video.image.isNull() and w.video.frame_provenance is None
    assert w.video.color_error and w.scopes.data is None
    assert not w.capture_reference_button.isEnabled() and not w.capture_reference()
    assert w.video.reference is frozen and w.clear_reference_button.isEnabled()
    # Clearing the temporary status message must not clear the persistent
    # painted viewer error. Live translations must update it and the tooltip.
    w.statusBar().clearMessage()
    for locale in ('zh-CN', 'en-US'):
        set_locale(locale); app.processEvents()
        for mode in ('off', 'side_by_side', 'wipe'):
            choose(w.comparison_mode_choice, mode); app.processEvents()
            labels = painted_labels(w.video, monkeypatch)
            assert labels[-1][1] == str(tr('Preview color unavailable'))
            assert all(QRectF(w.video.rect()).contains(rect) for rect, _ in labels)
            assert str(tr(ERROR_TEXT)) in w.video.toolTip()
            assert w.video.reference is frozen and w.scopes.data is None
    valid, corrected = mapped_bars(variant=True); inject(w, valid)
    assert not w.video.color_error
    assert w.capture_reference_button.isEnabled()
    assert_pixels(w.video.image, corrected); assert_current_histogram(w, corrected)
    assert w.video.reference is frozen; assert_pixels(frozen.image, expected)
    assert str(tr(ERROR_TEXT)) not in w.video.toolTip()
    assert painted_labels(w.video, monkeypatch)[-1][1] == str(tr('Current · f{frame}', frame=0))
    assert transaction_state(w) == before


def test_obsolete_generation_is_rejected_before_conversion_and_cannot_clear_current(window, app, tmp_path, monkeypatch):
    w = window; activate_synthetic(w, tmp_path)
    valid, expected = mapped_bars(); inject(w, valid); assert w.capture_reference()
    frozen, current, data = w.video.reference, w.video.image.copy(), w.scopes.data
    previous_count = w.scope_counter
    def forbidden(_):
        pytest.fail('An obsolete decoder generation reached color conversion')
    monkeypatch.setattr('lumen.ui.preview_frame_image', forbidden)
    rejected, _ = mapped_bars(failure='matrix')
    w.video_frame(rejected, generation=w._preview_generation - 1)
    assert w.video.image == current and w.scopes.data is data
    assert w.video.reference is frozen and w.capture_reference_button.isEnabled()
    assert w.scope_counter == previous_count and not w.video.color_error


@pytest.mark.parametrize('locale', ['en-US', 'zh-CN'])
@pytest.mark.parametrize('scale', [1., 1.5, 2.])
def test_color_failure_and_recovery_at_actual_dpi_and_window_sizes(tmp_path, locale, scale):
    output = tmp_path / f'{locale}-{scale:g}'
    requested = os.environ.get('PREVIEW_COLOR_SCREENSHOTS')
    if requested: output = Path(requested) / output.name
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--layout',
                             str(output), locale, str(scale)], cwd=ROOT,
                            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR=str(scale)),
                            text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / 'geometry.json').read_text())
    assert report['scale'] == scale and report['locale'] == locale
    assert len(report['records']) == 6
    for record in report['records']:
        assert record['dpr'] == pytest.approx(scale)
        assert record['window_size'] in ([1000, 700], [1440, 960])
        assert record['video_fully_visible'] and record['scopes_fully_visible']
        assert record['scope_canvas_height'] >= 44
        assert record['reference_preserved'] and record['project_and_history_unchanged']
        assert record['capture_enabled'] == (record['state'] != 'unsupported')
        assert record['scope_data_present'] == (record['state'] != 'unsupported')
        assert record['color_error_present'] == (record['state'] == 'unsupported')
        assert record['error_tooltip_present'] == (record['state'] == 'unsupported')
        assert all(row['fully_visible'] and row['text_fits'] for row in record['controls'])
        assert not record['controls_overlap']
        assert (output / record['screenshot']).is_file()
        assert (output / record['viewer_screenshot']).is_file()


def capture_layout(output, locale, scale):
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    # A subprocess does not inherit conftest's application-font registration.
    # Use the same hash-verified fixture when CI provides it, not a download or
    # a system install. Otherwise use an existing installed CJK font.
    fixture_directory = os.environ.get('LUMEN_TEST_CJK_FONT_DIR')
    if fixture_directory:
        from scripts.provision_test_font import verify_fixture
        fixture = verify_fixture(fixture_directory)
        assert QFontDatabase.addApplicationFont(fixture['font_file']) >= 0
    families = QFontDatabase.families()
    cjk = next((f for f in ('Noto Sans CJK SC', 'Noto Serif CJK SC', 'WenQuanYi Zen Hei') if f in families), None)
    assert cjk, 'Chinese layout evidence requires an installed CJK font'
    font = QFont(app.font()); font.setFamilies([font.family(), cjk]); app.setFont(font)
    runtime = output / '_runtime'
    w = make_window(runtime, locale); w.set_workspace_mode('professional')
    w.inspector_tabs.setCurrentIndex(1); w.color_tabs.setCurrentIndex(0)
    activate_synthetic(w, runtime)
    reference, expected_reference = mapped_bars(); inject(w, reference); assert w.capture_reference()
    frozen = w.video.reference; choose(w.comparison_mode_choice, 'side_by_side')
    before = transaction_state(w); records = []
    try:
        for width, height in ((1000, 700), (1440, 960)):
            w.resize(width, height); w.side_tabs.setFixedWidth(320 if width == 1000 else 360)
            w.main_split.setSizes([210, 450 if width == 1000 else 800, w.side_tabs.width()])
            if width == 1440: w.vertical_split.setSizes([700, 180])
            for state in ('corrected', 'unsupported', 'recovered'):
                frame, expected = mapped_bars(variant=True, failure='matrix' if state == 'unsupported' else None)
                inject(w, frame); w.statusBar().clearMessage()
                for _ in range(6): app.processEvents(); QTest.qWait(20)
                if state != 'unsupported':
                    assert_pixels(w.video.image, expected); assert_current_histogram(w, expected)
                assert_pixels(frozen.image, expected_reference)
                controls = [w.capture_reference_button, w.clear_reference_button,
                            w.comparison_mode_choice, w.reference_note]
                positions = [QRect(widget.mapTo(w, QPoint()), widget.size()) for widget in controls]
                overlap = any(a.intersects(b) for i, a in enumerate(positions) for b in positions[i+1:])
                name = f'preview-color-{locale}-{width}x{height}-{state}-{scale:g}.png'
                viewer_name = 'viewer-' + name
                assert w.grab().save(str(output / name))
                assert w.video.parentWidget().grab().save(str(output / viewer_name))
                records.append(dict(state=state, window_size=[w.width(), w.height()],
                    dpr=w.devicePixelRatioF(), screenshot=name, viewer_screenshot=viewer_name,
                    video_fully_visible=w.video.visibleRegion().boundingRect() == w.video.rect(),
                    scopes_fully_visible=all(child.visibleRegion().boundingRect() == child.rect()
                        for child in (w.scopes.source_label, w.scopes.mode_choice, w.scopes.canvas)),
                    scope_canvas_height=w.scopes.canvas.height(),
                    controls=[control_geometry(widget) for widget in controls], controls_overlap=overlap,
                    capture_enabled=w.capture_reference_button.isEnabled(),
                    scope_data_present=w.scopes.data is not None,
                    color_error_present=bool(w.video.color_error),
                    error_tooltip_present=str(tr(ERROR_TEXT)) in w.video.toolTip(),
                    reference_preserved=w.video.reference is frozen,
                    project_and_history_unchanged=transaction_state(w) == before))
        (output / 'geometry.json').write_text(json.dumps(dict(locale=locale, scale=scale,
            scope='Linux Qt offscreen CPU-backed NV12 injection through actual MainWindow QVideoSink; '
                  'not native Windows/macOS playback, HDR or calibrated display proof',
            records=records), ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        close_window(w, app)


if __name__ == '__main__':
    assert sys.argv[1] == '--layout'
    capture_layout(Path(sys.argv[2]), sys.argv[3], float(sys.argv[4]))

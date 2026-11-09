"""Click coordinates must identify the source sample Qt actually displays.

The image colors encode unique source indices. Comparing a widget grab with
the emitted index detects raster/hit disagreement without reproducing either
production mapping formula in the expected result.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def _exercise_raster():
    """Run after process-level Qt scale selection, including fractional DPR."""
    import math

    import numpy as np
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from lumen.ui_qualifier import QualifierCanvas

    app = QApplication.instance() or QApplication([])
    canvas = QualifierCanvas()
    canvas.picking = True
    canvas.show()
    received = []
    canvas.sample_clicked.connect(lambda x, y: received.append((x, y)))
    tested = 0

    def click(point):
        received.clear()
        event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(*point),
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(canvas, event)

    try:
        for width, height in ((512, 100), (257, 513), (37, 19), (3, 2)):
            # Every source pixel has a unique exact RGB8 color. The source is
            # still float32, as required by the normal canvas display path.
            indices = np.arange(width * height, dtype=np.uint32).reshape(height, width)
            bytes_rgb = np.stack((indices & 255, (indices >> 8) & 255,
                                  (indices >> 16) & 255), axis=-1).astype(np.uint8)
            canvas.set_frame(bytes_rgb.astype(np.float32) / np.float32(255))
            for size in ((180, 120), (181, 121), (319, 173), (211, 220), (180, 120)):
                canvas.resize(*size)
                app.processEvents()
                rect = canvas.image_rect()
                dpr = canvas.devicePixelRatioF()
                grabbed = canvas.grab().toImage()
                # Select physical pixels wholly inside the fitted picture.
                # Fractions within the same device pixel must select the same
                # sample, including when Qt reports fractional logical input.
                left = math.ceil(rect.left() * dpr - 1e-8)
                top = math.ceil(rect.top() * dpr - 1e-8)
                right = math.floor(rect.right() * dpr + 1e-8) - 1
                bottom = math.floor(rect.bottom() * dpr + 1e-8) - 1
                assert left <= right and top <= bottom
                xs = sorted(set(np.linspace(left, right, 9).astype(int)))
                ys = sorted(set(np.linspace(top, bottom, 5).astype(int)))
                for px in xs:
                    for py in ys:
                        for fraction in (0., .05, .5, .95):
                            point = ((px + fraction) / dpr, (py + fraction) / dpr)
                            click(point)
                            assert len(received) == 1, (size, (width, height), dpr, point, received)
                            sx, sy = received[0]
                            assert 0 <= sx < width and 0 <= sy < height
                            displayed = grabbed.pixelColor(px, py).getRgb()[:3]
                            expected = tuple(map(int, bytes_rgb[sy, sx]))
                            assert displayed == expected, {
                                'widget_size': size, 'source_size': (width, height),
                                'dpr': dpr, 'logical_click': point,
                                'device_pixel': (px, py), 'emitted_source': (sx, sy),
                                'displayed_rgb': displayed, 'emitted_source_rgb': expected,
                            }
                            tested += 1

                # Exact right/bottom edges and all canvas-letterbox padding
                # must be rejected rather than clamped to a source sample.
                cx, cy = rect.center().x(), rect.center().y()
                epsilon = .01 / dpr
                for point in ((rect.left() - epsilon, cy),
                              (rect.right(), cy),
                              (cx, rect.top() - epsilon),
                              (cx, rect.bottom())):
                    click(point)
                    assert not received, (size, (width, height), dpr, point, received)
        print(json.dumps({'dpr': canvas.devicePixelRatioF(), 'verified_clicks': tested}))
    finally:
        canvas.close()
        canvas.deleteLater()
        app.processEvents()


@pytest.mark.parametrize('scale', ['1', '1.25', '1.5', '2'])
def test_source_raster_matches_clicks_after_shrink_odd_resize_and_device_scale(scale):
    # QGuiApplication chooses DPR at startup; isolated processes exercise the
    # real raster path without altering other Qt test fixtures or app state.
    code = "import runpy, sys; runpy.run_path(sys.argv[1])['_exercise_raster']()"
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR=scale)
    result = subprocess.run([sys.executable, '-c', code, str(Path(__file__).resolve())],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            text=True, capture_output=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.strip())
    assert evidence['dpr'] == float(scale)
    assert evidence['verified_clicks'] >= 2000

"""Actual Qt raster pixels must equal the full-source pixels picked after crops."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def _exercise_detail_raster():
    import math
    import numpy as np
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    from lumen.ui_qualifier import QualifierCanvas
    from lumen.qualifier_viewport import centered, overview

    app = QApplication.instance() or QApplication([])
    canvas = QualifierCanvas(); canvas.picking = True; canvas.show()
    received = []
    canvas.sample_clicked.connect(lambda x, y: received.append((x, y)))
    width, height = 1027, 801
    indices = np.arange(width * height, dtype=np.uint32).reshape(height, width)
    rgb8 = np.stack((indices & 255, (indices >> 8) & 255,
                     (indices >> 16) & 255), axis=-1).astype(np.uint8)
    source = rgb8.astype(np.float32) / np.float32(255)
    source.setflags(write=False)
    tested = 0

    def click(point):
        received.clear()
        event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(*point),
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(canvas, event)

    views = [overview(width, height)]
    views += [centered(width, height, origin, zoom)
              for zoom in (1, 2, 4, 8)
              for origin in ((0, 0), (513, 401), (1026, 800), (293, 177))]
    try:
        for view in views:
            canvas.set_frame(view.extract(source))
            for size in ((181, 121), (319, 173), (211, 220)):
                canvas.resize(*size); app.processEvents()
                rect = canvas.image_rect(); dpr = canvas.devicePixelRatioF()
                grabbed = canvas.grab().toImage()
                left, top = math.ceil(rect.left() * dpr), math.ceil(rect.top() * dpr)
                right = math.floor(rect.right() * dpr + 1e-8) - 1
                bottom = math.floor(rect.bottom() * dpr + 1e-8) - 1
                for px in sorted(set(np.linspace(left, right, 7).astype(int))):
                    for py in sorted(set(np.linspace(top, bottom, 5).astype(int))):
                        for fraction in (.05, .5, .95):
                            click(((px + fraction) / dpr, (py + fraction) / dpr))
                            assert len(received) == 1, (view, size, px, py, received)
                            gx, gy = received[0]
                            coordinate = view.source_at(gx, gy)
                            assert coordinate is not None
                            sx, sy = coordinate
                            assert view.grid_at(sx, sy) == (gx, gy)
                            displayed = grabbed.pixelColor(px, py).getRgb()[:3]
                            expected = tuple(map(int, rgb8[sy, sx]))
                            assert displayed == expected, {
                                'view': str(view), 'size': size, 'dpr': dpr,
                                'device_pixel': (px, py), 'grid': (gx, gy),
                                'source': coordinate, 'displayed': displayed,
                                'source_rgb': expected,
                            }
                            tested += 1
                cx, cy = rect.center().x(), rect.center().y()
                for point in ((rect.left() - .01 / dpr, cy), (rect.right(), cy),
                              (cx, rect.top() - .01 / dpr), (cx, rect.bottom())):
                    click(point); assert not received
        print(json.dumps({'dpr': canvas.devicePixelRatioF(), 'verified_clicks': tested,
                          'view_count': len(views)}))
    finally:
        canvas.close(); canvas.deleteLater(); app.processEvents()


@pytest.mark.parametrize('scale', ['1', '1.25', '1.5', '2'])
def test_cropped_source_raster_matches_real_mouse_events_at_fractional_dpr(scale):
    code = "import runpy, sys; runpy.run_path(sys.argv[1])['_exercise_detail_raster']()"
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR=scale)
    result = subprocess.run([sys.executable, '-c', code, str(Path(__file__).resolve())],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.strip())
    assert evidence['dpr'] == float(scale)
    assert evidence['view_count'] == 17
    assert evidence['verified_clicks'] >= 5000

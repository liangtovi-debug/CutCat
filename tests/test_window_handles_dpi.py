"""Real Qt mouse events and paint isolation across fractional screen scales."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def _exercise_handles():
    import numpy as np
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    from lumen.qualifier_viewport import overview, centered
    from lumen.qualifier_window_handles import WindowGeometry
    from lumen.ui_qualifier import QualifierCanvas

    app = QApplication.instance() or QApplication([])
    canvas = QualifierCanvas(); canvas.show()
    cw, ch = 1027, 801
    source = np.empty((ch, cw, 3), np.float32); source[:] = (.1234567, .4567891, .6543219)
    source.setflags(write=False)
    views = [overview(cw, ch)] + [centered(cw, ch, center, zoom)
        for zoom in (1, 2, 4, 8) for center in ((0, 0), (513, 401), (cw - 1, ch - 1), (293, 177))]
    results = []; accepted = []
    canvas.window_geometry_changed.connect(results.append)
    canvas.window_drag_finished.connect(accepted.append)

    def event(kind, position, button, buttons):
        QApplication.sendEvent(canvas, QMouseEvent(kind, position, canvas.mapToGlobal(position.toPoint()),
                                button, buttons, Qt.KeyboardModifier.NoModifier))

    tested = 0
    try:
        for view in views:
            for size in ((181, 121), (319, 173), (211, 220)):
                canvas.set_frame(view.extract(source)); canvas.resize(*size); app.processEvents()
                # Shape center is an actual sampled source pixel. Its screen
                # point must coincide with that sample center, not naive view
                # fractions or a mouse-pixel-quantized cursor position.
                gx, gy = view.sample_width // 2, view.sample_height // 2
                sx, sy = view.source_at(gx, gy)
                geometry = WindowGeometry((sx + .5) / cw, (sy + .5) / ch, .001, .001, 'ellipse')
                canvas.set_window_context(geometry, view, (cw, ch), True)
                original_image = canvas.image.copy(); original_pixels = canvas._pixels.copy()
                rect = canvas.image_rect()
                expected_center = (rect.left() + (gx + .5) * rect.width() / view.sample_width,
                                   rect.top() + (gy + .5) * rect.height() / view.sample_height)
                center = canvas.window_handles()['center']
                assert abs(center.x() - expected_center[0]) < 1e-9
                assert abs(center.y() - expected_center[1]) < 1e-9
                for name in ('center', 'left', 'right', 'top', 'bottom'):
                    canvas.set_window_context(geometry, view, (cw, ch), True)
                    start = canvas.window_handles()[name]
                    results.clear(); accepted.clear()
                    # Actual press/release, including fractional logical points.
                    event(QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
                    event(QEvent.Type.MouseButtonRelease, start, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
                    assert not results and accepted == [True]
                    assert canvas.window_geometry == geometry
                    dx = -7.25 if name == 'left' else 7.25 if name in ('right', 'center') else 0
                    dy = -5.75 if name == 'top' else 5.75 if name in ('bottom', 'center') else 0
                    end = start + QPointF(dx, dy)
                    event(QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
                    event(QEvent.Type.MouseMove, end, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
                    event(QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
                    assert not canvas.window_drag_active
                    expected = dict(center_x=geometry.center_x, center_y=geometry.center_y,
                                    width=geometry.width, height=geometry.height)
                    nx = dx * view.sample_width * view.stride / (rect.width() * cw)
                    ny = dy * view.sample_height * view.stride / (rect.height() * ch)
                    if name == 'center':
                        expected['center_x'] = min(1, max(0, geometry.center_x + nx))
                        expected['center_y'] = min(1, max(0, geometry.center_y + ny))
                    elif name in ('left', 'right'):
                        expected['width'] += 2 * abs(nx)
                    else:
                        expected['height'] += 2 * abs(ny)
                    for key, value in expected.items():
                        assert abs(getattr(canvas.window_geometry, key) - value) < 1e-12, (view, size, name, key)
                    assert canvas.image == original_image
                    np.testing.assert_array_equal(canvas._pixels, original_pixels)
                    tested += 1
        assert not source.flags.writeable
        print(json.dumps({'dpr': canvas.devicePixelRatioF(), 'gestures': tested, 'views': len(views)}))
    finally:
        canvas.close(); canvas.deleteLater(); app.processEvents()


@pytest.mark.parametrize('scale', ['1', '1.25', '1.5', '2'])
def test_real_window_gestures_match_continuous_source_coordinates_at_fractional_dpr(scale):
    code = "import runpy, sys; runpy.run_path(sys.argv[1])['_exercise_handles']()"
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR=scale)
    result = subprocess.run([sys.executable, '-c', code, str(Path(__file__).resolve())],
        cwd=Path(__file__).resolve().parents[1], env=env, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.strip())
    assert evidence == {'dpr': float(scale), 'gestures': 255, 'views': 17}

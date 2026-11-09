"""Qt pointer ownership, interrupted editing and paint-only handle buffers."""
from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lumen.qualifier import QualifierSettings, qualifier_mask
from test_qualifier_ui import app, loaded, window


def prepare(window, app, *, tiny=False, shape='ellipse'):
    window.selected_clip().grade.qualifier = QualifierSettings(
        enabled=True, hue_width=.5, window_shape=shape,
        window_center_x=.5123456789, window_center_y=.4987654321,
        window_width=.001 if tiny else .2345678912,
        window_height=.001 if tiny else .3456789123,
        window_feather=.2345678912, window_invert=True)
    dialog = loaded(window)
    source = np.full((801, 1027, 3), (.31, .51, .27), np.float32)
    source.setflags(write=False)
    dialog.accept_snapshot(replace(dialog.snapshot, frame=source, valid_mask=np.ones((801, 1027), bool)))
    dialog.window_edit_button.setChecked(True)
    app.processEvents()
    return dialog


def send(canvas, kind, point, button, buttons):
    QApplication.sendEvent(canvas, QMouseEvent(kind, point, canvas.mapToGlobal(point.toPoint()),
        button, buttons, Qt.KeyboardModifier.NoModifier))


def press(canvas, point):
    send(canvas, QEvent.Type.MouseButtonPress, point, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)


def move(canvas, point):
    send(canvas, QEvent.Type.MouseMove, point, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)


def release(canvas, point):
    send(canvas, QEvent.Type.MouseButtonRelease, point, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)


@pytest.mark.parametrize('handle', ['center', 'left', 'right', 'top', 'bottom'])
def test_actual_noop_clicks_preserve_all_exact_fields_and_apply_no_undo(window, app, handle):
    dialog = prepare(window, app, tiny=True)
    expected = deepcopy(dialog.settings)
    start = dialog.canvas.window_handles()[handle]
    press(dialog.canvas, start); release(dialog.canvas, start)
    assert dialog.settings == expected
    dialog.apply_settings()
    assert window.selected_clip().grade.qualifier == expected and not window.editor.can_undo


@pytest.mark.parametrize('handle', ['left', 'right', 'top', 'bottom'])
def test_tiny_handles_remain_separate_and_resize_without_center_jump(window, app, handle):
    dialog = prepare(window, app, tiny=True)
    before = deepcopy(dialog.settings)
    canvas = dialog.canvas; start = canvas.window_handles()[handle]
    dx = -13.25 if handle == 'left' else 13.25 if handle == 'right' else 0
    dy = -11.75 if handle == 'top' else 11.75 if handle == 'bottom' else 0
    end = start + QPointF(dx, dy)
    press(canvas, start); assert canvas.window_drag_active
    move(canvas, end); release(canvas, end)
    rect = canvas.image_rect(); view = dialog.viewport
    expected_width = before.window_width + 2 * abs(dx) * view.sample_width * view.stride / (rect.width() * 1027)
    expected_height = before.window_height + 2 * abs(dy) * view.sample_height * view.stride / (rect.height() * 801)
    assert dialog.settings.window_width == pytest.approx(expected_width)
    assert dialog.settings.window_height == pytest.approx(expected_height)
    assert dialog.settings.window_center_x == before.window_center_x
    assert dialog.settings.window_center_y == before.window_center_y
    assert dialog.settings.window_feather == before.window_feather and dialog.settings.window_invert
    assert not window.editor.can_undo


@pytest.mark.parametrize('reason', ['escape_canvas', 'escape_dialog', 'ungrab', 'deactivate', 'dialog_deactivate', 'resize',
                                   'disable', 'hide', 'shape', 'numeric', 'off', 'new_snapshot', 'invalid'])
def test_interrupted_drag_rolls_back_only_geometry_and_never_commits(window, app, reason):
    dialog = prepare(window, app)
    before = deepcopy(dialog.settings); project = deepcopy(window.project)
    canvas = dialog.canvas; start = canvas.window_handles()['center']
    press(canvas, start); move(canvas, start + QPointF(31.75, -13.25))
    assert dialog.settings.window_center_x != before.window_center_x
    if reason.startswith('escape'):
        QTest.keyClick(canvas if reason == 'escape_canvas' else dialog, Qt.Key.Key_Escape)
    elif reason == 'ungrab': QApplication.sendEvent(canvas, QEvent(QEvent.Type.UngrabMouse))
    elif reason == 'deactivate': QApplication.sendEvent(canvas, QEvent(QEvent.Type.WindowDeactivate))
    elif reason == 'dialog_deactivate': QApplication.sendEvent(dialog, QEvent(QEvent.Type.WindowDeactivate))
    elif reason == 'resize': canvas.resize(canvas.width() - 1, canvas.height())
    elif reason == 'disable': canvas.setEnabled(False)
    elif reason == 'hide': canvas.hide()
    elif reason == 'shape': dialog.window_shape.setCurrentIndex(dialog.window_shape.findData('rectangle'))
    elif reason == 'numeric': dialog.controls['window_width'].setValue(42)
    elif reason == 'off': dialog.window_shape.setCurrentIndex(dialog.window_shape.findData('off'))
    elif reason == 'new_snapshot': dialog.accept_snapshot(dialog.snapshot)
    elif reason == 'invalid':
        dialog.controls['saturation_low'].setValue(80); dialog.controls['saturation_high'].setValue(20)
    assert not canvas.window_drag_active
    assert dialog.settings.window_center_x == before.window_center_x
    assert dialog.settings.window_center_y == before.window_center_y
    assert dialog.settings.window_width == (.42 if reason == 'numeric' else before.window_width)
    assert dialog.settings.window_height == before.window_height
    assert window.project == project and not window.editor.can_undo
    assert dialog.isVisible()
    release(canvas, start + QPointF(31.75, -13.25))
    assert dialog.settings.window_center_x == before.window_center_x


@pytest.mark.parametrize('mutation', ['revision', 'source', 'selection', 'frame'])
def test_stale_drag_cancels_immediately_and_cannot_late_commit(window, app, mutation):
    import os
    dialog = prepare(window, app)
    before = deepcopy(dialog.settings); canvas = dialog.canvas
    start = canvas.window_handles()['center']; press(canvas, start)
    move(canvas, start + QPointF(13, 7))
    if mutation == 'revision': window.revision += 1
    elif mutation == 'source':
        path = window.project.media[0].path; info = os.stat(path)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
    elif mutation == 'selection': window.select_clip('other')
    elif mutation == 'frame': window.timeline.playhead += 1
    project = deepcopy(window.project)
    move(canvas, start + QPointF(23, 17)); release(canvas, start + QPointF(23, 17))
    assert dialog.settings == before and not canvas.window_drag_active
    assert not dialog.apply_button.isEnabled() and not dialog.window_edit_button.isEnabled()
    dialog.apply_settings()
    assert window.project == project and not window.editor.can_undo


def test_decorations_change_widget_pixels_only_not_source_image_or_matte(window, app):
    dialog = prepare(window, app)
    canvas = dialog.canvas
    original = canvas.image.copy(); pixels = canvas._pixels.copy()
    source = dialog.snapshot.frame.copy(); matte = dialog.matte.copy()
    with_handles = canvas.grab().toImage()
    dialog.window_edit_button.setChecked(False)
    without_handles = canvas.grab().toImage()
    assert with_handles != without_handles
    assert canvas.image == original
    np.testing.assert_array_equal(canvas._pixels, pixels)
    np.testing.assert_array_equal(dialog.snapshot.frame, source)
    np.testing.assert_array_equal(dialog.matte, matte)
    assert not dialog.snapshot.frame.flags.writeable
    assert not window.editor.can_undo


def test_drag_out_and_back_then_apply_is_noop_with_exact_untouched_floats(window, app):
    dialog = prepare(window, app)
    original = deepcopy(dialog.settings); canvas = dialog.canvas
    for name in canvas.window_handles():
        start = canvas.window_handles()[name]
        press(canvas, start); move(canvas, start + QPointF(15.75, -3.25))
        move(canvas, start); release(canvas, start)
    assert dialog.settings == original
    dialog.apply_settings()
    assert not window.editor.can_undo


def test_live_render_during_long_drag_after_resize_does_not_cancel_current_mapping(window, app):
    dialog = prepare(window, app)
    # Resize after accepting the snapshot, so the cached display mapping is
    # older than the gesture's actual logical-pixel rectangle.
    dialog.resize(551, 641); app.processEvents()
    before = deepcopy(dialog.settings); canvas = dialog.canvas
    start = canvas.window_handles()['center']; end = start + QPointF(19.5, 7.25)
    press(canvas, start); move(canvas, end)
    moved = deepcopy(dialog.settings)
    assert moved != before and canvas.window_drag_active
    QTest.qWait(90); app.processEvents()
    assert canvas.window_drag_active and dialog.settings == moved
    np.testing.assert_array_equal(dialog.matte, qualifier_mask(
        dialog.viewport.extract(dialog.snapshot.frame), replace(moved, enabled=True),
        canvas_size=(1027, 801), origin=(dialog.viewport.x, dialog.viewport.y), stride=dialog.viewport.stride))
    move(canvas, start); QTest.qWait(90)
    assert canvas.window_drag_active and dialog.settings == before
    release(canvas, start); dialog.apply_settings()
    assert not window.editor.can_undo

"""Independent acceptance for on-canvas static-window editing.

Mouse expectations use displayed logical distances and original canvas extent,
not the production handle/gesture math. Frames are local synthetic RGB16 media.
"""
from copy import deepcopy
from dataclasses import asdict, replace
import ast
from string import Formatter
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from lumen import i18n
from lumen.qualifier import apply_qualifier, qualifier_mask
from test_qualifier_ui import app, loaded, window


GEOMETRY = ('window_center_x', 'window_center_y', 'window_width', 'window_height')


def click_editor(dialog, app):
    dialog.window_edit_button.setFocus(); app.processEvents()
    assert dialog.window_edit_button.visibleRegion().boundingRect() == dialog.window_edit_button.rect()
    QTest.mouseClick(dialog.window_edit_button, Qt.MouseButton.LeftButton)
    app.processEvents()


def prepare(window, app, shape='ellipse'):
    # Actual decoder fits the local RGB16 test chart into this project canvas.
    window.project.width = 960; window.project.height = 540
    dialog = loaded(window)
    dialog.resize(760, 800)
    dialog.enabled_check.setChecked(True)
    for key, value in [('hue_width', 180), ('exposure', .5),
                       ('window_center_x', 50), ('window_center_y', 50),
                       ('window_width', 40), ('window_height', 50),
                       ('window_feather', 32)]:
        dialog.controls[key].setValue(value)
    dialog.window_shape.setCurrentIndex(dialog.window_shape.findData(shape))
    dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData('overlay'))
    dialog.render_preview(); app.processEvents()
    assert dialog.window_edit_button.isEnabled()
    click_editor(dialog, app)
    assert dialog.window_edit_button.isChecked() and dialog.canvas.window_editing
    return dialog


def drag(dialog, app, handle, delta, *, release=True):
    """Dispatch real Qt test mouse events, retaining an unfinished gesture."""
    start = dialog.canvas.window_handles()[handle].toPoint()
    end = start + QPoint(*delta)
    QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=start)
    assert dialog.canvas.window_drag_active, (handle, start, dialog.canvas.window_handles())
    QTest.mouseMove(dialog.canvas, end, delay=20)
    app.processEvents()
    if release:
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=end)
        app.processEvents()
        assert not dialog.canvas.window_drag_active
    return start, end


def expected_delta(dialog, delta):
    rect = dialog.canvas.image_rect()
    height, width = dialog.snapshot.frame.shape[:2]
    return (delta[0] * ((dialog.viewport.width + dialog.viewport.stride - 1) // dialog.viewport.stride * dialog.viewport.stride) / rect.width() / width,
            delta[1] * ((dialog.viewport.height + dialog.viewport.stride - 1) // dialog.viewport.stride * dialog.viewport.stride) / rect.height() / height)


def assert_display_matches_full_frame(dialog):
    source = dialog.snapshot.frame
    full_mask = qualifier_mask(source, replace(dialog.settings, enabled=True))
    np.testing.assert_array_equal(dialog.matte, dialog.viewport.extract(full_mask))
    mode = dialog.preview_choice.currentData()
    if mode == 'adjusted':
        expected = dialog.viewport.extract(apply_qualifier(source, dialog.settings))
    elif mode == 'matte':
        expected = np.repeat(dialog.viewport.extract(full_mask)[..., None], 3, axis=-1)
    elif mode == 'source':
        expected = dialog.viewport.extract(source)
    else:
        expected = np.clip(dialog.viewport.extract(source), 0, 1)
        alpha = dialog.viewport.extract(full_mask)[..., None] * np.float32(.5)
        expected = expected * (np.float32(1) - alpha) + np.array([1, 0, 1], np.float32) * alpha
    np.testing.assert_array_equal(dialog.canvas._pixels,
        np.rint(np.clip(expected, 0, 1) * 255).astype(np.uint8))


@pytest.mark.parametrize('shape', ['ellipse', 'rectangle'])
def test_review_real_center_drag_and_numeric_controls_agree_without_early_commit(window, app, shape):
    dialog = prepare(window, app, shape)
    before_project = deepcopy(window.project)
    before = deepcopy(dialog.settings)
    frame = dialog.snapshot.frame.copy()
    delta = (31, -17); dx, dy = expected_delta(dialog, delta)
    _, end = drag(dialog, app, 'center', delta, release=False)
    assert dialog.settings.window_center_x == pytest.approx(before.window_center_x + dx, abs=1e-12)
    assert dialog.settings.window_center_y == pytest.approx(before.window_center_y + dy, abs=1e-12)
    assert dialog.settings.window_width == before.window_width
    assert dialog.settings.window_height == before.window_height
    assert window.project == before_project and not window.editor.can_undo
    QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=end); app.processEvents()
    dialog.render_preview()
    for key in GEOMETRY:
        assert dialog.controls[key].value() == pytest.approx(getattr(dialog.settings, key) * 100, abs=.000501)
    assert_display_matches_full_frame(dialog)
    np.testing.assert_array_equal(dialog.snapshot.frame, frame)
    assert not dialog.snapshot.frame.flags.writeable
    assert window.project == before_project and not window.editor.can_undo


@pytest.mark.parametrize('shape', ['ellipse', 'rectangle'])
@pytest.mark.parametrize('handle,delta,field,sign', [
    ('left', (-19, 0), 'window_width', -1), ('right', (19, 0), 'window_width', 1),
    ('top', (0, -13), 'window_height', -1), ('bottom', (0, 13), 'window_height', 1),
])
def test_review_four_edge_handles_resize_symmetrically(window, app, shape, handle, delta, field, sign):
    dialog = prepare(window, app, shape); before = deepcopy(dialog.settings)
    dx, dy = expected_delta(dialog, delta)
    drag(dialog, app, handle, delta)
    expected = getattr(before, field) + 2 * sign * (dx if field == 'window_width' else dy)
    assert getattr(dialog.settings, field) == pytest.approx(expected, abs=1e-12)
    for key, value in asdict(before).items():
        if key != field:
            assert getattr(dialog.settings, key) == value, key
    dialog.render_preview(); assert_display_matches_full_frame(dialog)
    assert not window.editor.can_undo


@pytest.mark.parametrize('shape', ['ellipse', 'rectangle'])
def test_review_escape_rolls_back_only_active_gesture_then_cancel_discards_all(window, app, shape):
    dialog = prepare(window, app, shape); before_project = deepcopy(window.project)
    drag(dialog, app, 'center', (19, 11))
    earlier_pending = deepcopy(dialog.settings)
    _, end = drag(dialog, app, 'right', (33, 0), release=False)
    assert dialog.settings != earlier_pending
    QTest.keyClick(dialog.canvas, Qt.Key.Key_Escape); app.processEvents()
    assert dialog.isVisible() and not dialog.canvas.window_drag_active
    assert dialog.settings == earlier_pending
    QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=end); app.processEvents()
    assert dialog.settings == earlier_pending
    QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton); app.processEvents()
    assert not dialog.isVisible() and dialog.snapshot is None
    assert window.project == before_project and not window.editor.can_undo


def test_review_many_mouse_drags_apply_as_one_normal_undo_transaction(window, app):
    dialog = prepare(window, app); before = deepcopy(window.project)
    drag(dialog, app, 'center', (13, 7))
    drag(dialog, app, 'right', (21, 0))
    drag(dialog, app, 'top', (0, -11))
    pending = deepcopy(dialog.settings)
    assert window.project == before and not window.editor.can_undo
    QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton); app.processEvents()
    assert window.selected_clip().grade.qualifier == pending
    assert len(window.editor._undo) == 1
    after = deepcopy(window.project)
    window.undo(); assert window.project == before
    window.redo(); assert window.project == after


def test_review_picker_and_handles_are_mutually_exclusive_and_do_not_change_geometry(window, app):
    dialog = prepare(window, app)
    geometry = {key: value for key, value in asdict(dialog.settings).items() if key.startswith('window_')}
    center = dialog.canvas.window_handles()['center'].toPoint()
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton); app.processEvents()
    assert dialog.canvas.picking and not dialog.canvas.window_editing
    assert not dialog.window_edit_button.isChecked()
    QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=center); app.processEvents()
    assert not dialog.canvas.picking
    assert {key: getattr(dialog.settings, key) for key in geometry} == geometry
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
    click_editor(dialog, app); app.processEvents()
    assert dialog.canvas.window_editing and not dialog.canvas.picking
    assert not dialog.pick_button.isChecked()
    assert {key: getattr(dialog.settings, key) for key in geometry} == geometry
    assert not window.editor.can_undo


@pytest.mark.parametrize('shape', ['ellipse', 'rectangle'])
def test_review_crop_drag_stays_in_global_canvas_coordinates_and_matte_matches(window, app, shape):
    dialog = prepare(window, app, shape)
    dialog.zoom_choice.setCurrentIndex(dialog.zoom_choice.findData(2))
    dialog.set_detail_center(480, 270); app.processEvents()
    before = deepcopy(dialog.settings); dx, dy = expected_delta(dialog, (21, -11))
    drag(dialog, app, 'center', (21, -11))
    assert dialog.settings.window_center_x == pytest.approx(before.window_center_x + dx, abs=1e-12)
    assert dialog.settings.window_center_y == pytest.approx(before.window_center_y + dy, abs=1e-12)
    for mode in ['source', 'matte', 'overlay', 'adjusted']:
        dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData(mode))
        dialog.render_preview(); assert_display_matches_full_frame(dialog)
    edited = deepcopy(dialog.settings)
    for zoom, center in [(8, (0, 0)), (8, (959, 539)), (1, (480, 270)), (0, (480, 270))]:
        dialog.zoom_choice.setCurrentIndex(dialog.zoom_choice.findData(zoom))
        dialog.set_detail_center(*center); app.processEvents()
        assert dialog.settings == edited
        assert_display_matches_full_frame(dialog)
    assert not window.editor.can_undo


@pytest.mark.parametrize('action', ['zoom', 'revision', 'cancel'])
def test_review_interrupted_mouse_drag_cannot_leak_or_commit(window, app, action):
    dialog = prepare(window, app); before = deepcopy(dialog.settings); project = deepcopy(window.project)
    _, end = drag(dialog, app, 'center', (25, 13), release=False)
    assert dialog.settings != before
    if action == 'zoom':
        dialog.zoom_choice.setCurrentIndex(dialog.zoom_choice.findData(2))
    elif action == 'revision':
        window.revision += 1; dialog.check_current()
    else:
        dialog.reject()
    app.processEvents()
    assert not dialog.canvas.window_drag_active
    assert dialog.settings == before
    QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=end); app.processEvents()
    assert dialog.settings == before
    assert window.project == project and not window.editor.can_undo


def test_review_handle_keys_and_dynamic_help_have_bilingual_catalog_coverage(window, app):
    import lumen.ui_qualifier as ui
    keys = ['Edit window handles', ui.HANDLE_HELP_TEXT, ui.HANDLE_OFF_TEXT, ui.HANDLE_NAV_TEXT]
    # Audit all literal and constant tr(...) keys in this feature, not just the button.
    tree = ast.parse(Path(ui.__file__).read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'tr' and node.args:
            argument = node.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                keys.append(argument.value)
            elif isinstance(argument, ast.Name) and isinstance(getattr(ui, argument.id, None), str):
                keys.append(getattr(ui, argument.id))
    i18n._load_catalogs()
    for key in keys:
        assert key in i18n._CATALOGS['zh-CN'], key
        if any(literal.strip() for literal, *_ in Formatter().parse(key)):
            assert i18n._CATALOGS['zh-CN'][key] != key, key
        assert i18n._CATALOGS['en'].get(key, key) == key
    dialog = prepare(window, app); before = deepcopy(dialog.settings)
    for locale in ['zh-CN', 'en', 'zh-CN']:
        i18n.set_locale(locale); app.processEvents()
        assert dialog.window_edit_button.text() == str(i18n.tr('Edit window handles'))
        assert dialog.window_edit_button.accessibleName() == str(i18n.tr('Edit window handles'))
        assert dialog.window_edit_button.toolTip() == str(i18n.tr(ui.HANDLE_HELP_TEXT))
        assert dialog.window_edit_button.accessibleDescription() == str(i18n.tr(ui.HANDLE_HELP_TEXT))
        assert dialog.canvas.toolTip() == str(i18n.tr(ui.HANDLE_NAV_TEXT))
        assert dialog.canvas.window_editing and dialog.settings == before


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('width', [320, 360])
def test_review_narrow_detail_after_pick_keeps_mode_and_every_numeric_label_reachable(window, app, locale, width):
    i18n.set_locale(locale)
    dialog = prepare(window, app)
    point = dialog.canvas.window_handles()['center'].toPoint()
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=point)
    app.processEvents()
    assert dialog.sample_source_position is not None
    dialog.resize(width, 600); app.processEvents()
    dialog.zoom_choice.setCurrentIndex(dialog.zoom_choice.findData(2)); app.processEvents()
    click_editor(dialog, app)
    assert (dialog.width(), dialog.height()) == (width, 600)
    for field in [dialog.window_shape, dialog.window_edit_button, dialog.window_invert, *dialog.controls.values()]:
        field.setFocus(); app.processEvents()
        assert field.visibleRegion().boundingRect() == field.rect(), field.objectName()
        label = dialog.control_labels.get(field)
        if label is not None:
            assert label.visibleRegion().boundingRect() == label.rect(), field.objectName()
    for fixed in [dialog.canvas, dialog.preview_choice, dialog.pick_button, dialog.sample_label,
                  dialog.resolution, dialog.status, dialog.apply_button, dialog.close_button]:
        assert fixed.visibleRegion().boundingRect() == fixed.rect()
        if hasattr(fixed, 'wordWrap') and fixed.wordWrap():
            assert fixed.heightForWidth(fixed.width()) <= fixed.height()
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
    assert not window.editor.can_undo

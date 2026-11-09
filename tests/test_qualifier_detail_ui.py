"""Full-resolution qualifier picking: frozen pixels and view-only navigation."""
from copy import deepcopy
from dataclasses import replace
import gc
import weakref

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from lumen.grade_transfer import copy_grade, paste_grade
from lumen.model import Editor
from test_qualifier_ui import app, loaded, window
from test_qualifier_picker_ui import arm, click_sample, assert_sample_ranges


def install_source(dialog, size=(1080, 1920), target=(997, 541)):
    frame = np.empty((*size, 3), np.float32)
    frame[:] = (.7, .1, .15)
    rgb = np.array((.12345678, .56789123, .31098765), np.float32)
    frame[target[1], target[0]] = rgb
    frame.setflags(write=False)
    dialog.accept_snapshot(replace(dialog.snapshot, frame=frame,
                                   valid_mask=np.ones(size, bool)))
    return frame, rgb


def zoom(dialog, value):
    index = dialog.zoom_choice.findData(value)
    assert index >= 0
    dialog.zoom_choice.setCurrentIndex(index)
    dialog.render_preview()


def test_detail_reaches_single_4k_pixel_missing_from_overview_without_redecode(window, app, monkeypatch):
    dialog = loaded(window)
    frame, rgb = install_source(dialog, (2160, 3840), (1997, 1081))
    frozen = dialog.snapshot
    before = deepcopy(window.project)
    assert dialog.viewport.grid_at(1997, 1081) is None
    monkeypatch.setattr('lumen.ui_qualifier.capture_picker_snapshot',
                        lambda *a, **k: pytest.fail('Navigation must not decode again'))
    zoom(dialog, 8)
    dialog.set_detail_center(1997, 1081)
    app.processEvents()
    grid = dialog.viewport.grid_at(1997, 1081)
    assert grid is not None
    assert dialog.viewport.source_at(*grid) == (1997, 1081)
    arm(dialog); click_sample(dialog, *grid)
    assert_sample_ranges(dialog.settings, rgb)
    assert dialog.sample_source_position == (1997, 1081)
    assert dialog.canvas.sample_position == grid
    assert 'Source (1997, 1081)' in dialog.sample_label.text()
    assert dialog.matte[grid[1], grid[0]] == 1
    assert dialog.snapshot is frozen and dialog.snapshot.frame is frame
    assert not frame.flags.writeable
    assert max(dialog.preview_shape) <= 512
    assert window.project == before and not window.editor.can_undo


def test_zoom_pan_keeps_source_pin_and_ranges_without_interpolating_marker(window, app):
    dialog = loaded(window)
    frame, rgb = install_source(dialog)
    zoom(dialog, 8); dialog.set_detail_center(997, 541); app.processEvents()
    arm(dialog); click_sample(dialog, *dialog.viewport.grid_at(997, 541))
    settings = deepcopy(dialog.settings)
    for level in (4, 2, 1, 0, 8):
        zoom(dialog, level)
        assert dialog.settings == settings
        assert dialog.sample_source_position == (997, 541)
        assert dialog.canvas.sample_position == dialog.viewport.grid_at(997, 541)
        assert dialog.snapshot.frame is frame
    dialog.pan_view(200, 200)
    assert dialog.viewport.grid_at(997, 541) is None
    assert dialog.canvas.sample_position is None
    assert dialog.sample_source_position == (997, 541)
    dialog.set_detail_center(997, 541)
    assert dialog.canvas.sample_position == dialog.viewport.grid_at(997, 541)
    assert dialog.settings == settings and not window.editor.can_undo


@pytest.mark.parametrize('target', [(0, 0), (1919, 0), (0, 1079), (1919, 1079)])
def test_edge_centers_remain_reachable_and_invalid_grid_cannot_clamp(window, app, target):
    dialog = loaded(window)
    frame, rgb = install_source(dialog, target=target)
    zoom(dialog, 8); dialog.set_detail_center(*target); app.processEvents()
    arm(dialog)
    before = deepcopy(dialog.settings)
    height, width = dialog.preview_shape
    for point in [(-1, 0), (0, -1), (width, 0), (0, height)]:
        dialog.pick_sample(*point)
    assert dialog.settings == before and dialog.canvas.picking
    grid = dialog.viewport.grid_at(*target)
    assert grid is not None and dialog.viewport.source_at(*grid) == target
    click_sample(dialog, *grid)
    assert_sample_ranges(dialog.settings, rgb)


def test_detail_padding_rejects_float_pixel_then_valid_neighbor_recovers(window, app):
    dialog = loaded(window)
    frame, _ = install_source(dialog)
    valid = np.ones(frame.shape[:2], bool); valid[541, 997] = False
    dialog.accept_snapshot(replace(dialog.snapshot, valid_mask=valid))
    zoom(dialog, 8); dialog.set_detail_center(997, 541); app.processEvents()
    arm(dialog); before = deepcopy(dialog.settings)
    click_sample(dialog, *dialog.viewport.grid_at(997, 541))
    assert dialog.settings == before and dialog.canvas.picking
    assert 'padding' in dialog.sample_label.text().lower()
    click_sample(dialog, *dialog.viewport.grid_at(998, 541))
    assert_sample_ranges(dialog.settings, frame[541, 998])
    assert not window.editor.can_undo


def test_stale_detail_cannot_pick_or_apply(window, app):
    dialog = loaded(window); install_source(dialog)
    zoom(dialog, 8); dialog.set_detail_center(997, 541); app.processEvents()
    grid = dialog.viewport.grid_at(997, 541)
    arm(dialog); before = deepcopy(window.project); settings = deepcopy(dialog.settings)
    window.revision += 1
    dialog.pick_sample(*grid); dialog.pan_view(10, 10); dialog.apply_settings()
    assert dialog.settings == settings and window.project == before
    assert not dialog.apply_button.isEnabled() and not dialog.canvas.picking
    assert not window.editor.can_undo


def test_cancel_detail_releases_4k_array_even_after_view_changes(window, app):
    before = deepcopy(window.project)
    dialog = loaded(window)
    frame, _ = install_source(dialog, (2160, 3840), (1997, 1081))
    reference = weakref.ref(frame)
    zoom(dialog, 8); dialog.set_detail_center(1997, 1081)
    arm(dialog); click_sample(dialog, *dialog.viewport.grid_at(1997, 1081))
    dialog.pan_view(20, 30); zoom(dialog, 1)
    del frame
    QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)
    app.processEvents(); gc.collect()
    assert reference() is None
    assert dialog.snapshot is None and dialog.viewport is None
    assert dialog.sample_source_position is None and dialog.canvas.sample_position is None
    assert dialog.canvas.image.isNull() and dialog.matte is None
    assert window.project == before and not window.editor.can_undo


def test_detail_apply_is_single_undo_and_grade_copy_contains_no_view_state(window, app):
    before = deepcopy(window.project)
    dialog = loaded(window); install_source(dialog)
    zoom(dialog, 8); dialog.set_detail_center(997, 541); app.processEvents()
    arm(dialog); click_sample(dialog, *dialog.viewport.grid_at(997, 541))
    expected = deepcopy(dialog.settings)
    dialog.pan_view(100, 100); zoom(dialog, 2)
    QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
    after = deepcopy(window.project)
    assert window.selected_clip().grade.qualifier == expected
    assert len(window.editor._undo) == 1
    copied = copy_grade(window.project, 'picture')
    editor = Editor(deepcopy(window.project))
    assert paste_grade(editor, 'other', copied)
    assert editor.project.clips[1].grade.qualifier == expected
    window.undo(); assert window.project == before
    window.redo(); assert window.project == after


def test_overview_double_click_enters_detail_without_changing_ranges(window, app):
    dialog = loaded(window); install_source(dialog); app.processEvents()
    before = deepcopy(dialog.settings)
    rect = dialog.canvas.image_rect()
    point = QPoint(int(rect.center().x()), int(rect.center().y()))
    grid = dialog.canvas.grid_at(point)
    source = dialog.viewport.source_at(*grid)
    QTest.mouseDClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=point)
    app.processEvents()
    assert dialog.zoom_choice.currentData() != 0
    assert dialog.viewport.grid_at(*source) is not None
    assert dialog.settings == before and not window.editor.can_undo


def test_pan_delta_is_exact_and_zero_does_not_drift_even_crop(window):
    dialog = loaded(window); install_source(dialog)
    zoom(dialog, 8); dialog.set_detail_center(997, 541)
    origin = (dialog.viewport.x, dialog.viewport.y)
    dialog.pan_view(0, 0)
    assert (dialog.viewport.x, dialog.viewport.y) == origin
    dialog.pan_view(13, -7)
    assert (dialog.viewport.x, dialog.viewport.y) == (origin[0] + 13, origin[1] - 7)
    dialog.pan_view(-13, 7)
    assert (dialog.viewport.x, dialog.viewport.y) == origin


def test_real_middle_drag_and_wheel_navigate_without_mutation(window, app):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent, QWheelEvent
    from PySide6.QtWidgets import QApplication

    dialog = loaded(window); install_source(dialog)
    zoom(dialog, 4); dialog.set_detail_center(997, 541); app.processEvents()
    before = deepcopy(dialog.settings)
    rect = dialog.canvas.image_rect()
    start = rect.center(); end = start + QPointF(20, 10)
    origin = (dialog.viewport.x, dialog.viewport.y)
    deltas = []
    dialog.canvas.pan_requested.connect(lambda x, y: deltas.append((x, y)))
    for kind, pos, button, buttons in [
        (QEvent.Type.MouseButtonPress, start, Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton),
        (QEvent.Type.MouseMove, end, Qt.MouseButton.NoButton, Qt.MouseButton.MiddleButton),
        (QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.MiddleButton, Qt.MouseButton.NoButton),
    ]:
        QApplication.sendEvent(dialog.canvas, QMouseEvent(kind, pos, button, buttons,
                                                        Qt.KeyboardModifier.NoModifier))
    assert len(deltas) == 1 and deltas[0][0] < 0 and deltas[0][1] < 0
    assert (dialog.viewport.x, dialog.viewport.y) == (origin[0] + deltas[0][0],
                                                    origin[1] + deltas[0][1])
    wheel = QWheelEvent(start, start, QPoint(), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(dialog.canvas, wheel)
    assert dialog.zoom_choice.currentData() == 8
    assert dialog.settings == before and not window.editor.can_undo


@pytest.mark.parametrize('target,delta,expected', [
    ((0, 0), (-500, -500), (0, 0)),
    ((1919, 1079), (500, 500), (1856, 1016)),
    ((0, 0), (1, 0), (1, 0)),
    ((0, 0), (0, 1), (0, 1)),
    ((1919, 1079), (-1, 0), (1855, 1016)),
    ((1919, 1079), (0, -1), (1856, 1015)),
])
def test_pan_at_clamped_edges_moves_without_dead_travel(window, target, delta, expected):
    dialog = loaded(window); install_source(dialog)
    zoom(dialog, 8); dialog.set_detail_center(*target)
    dialog.pan_view(*delta)
    assert (dialog.viewport.x, dialog.viewport.y) == expected


def test_successful_armed_double_click_does_not_navigate_after_first_pick(window, app):
    dialog = loaded(window); app.processEvents()
    arm(dialog)
    rect = dialog.canvas.image_rect()
    point = QPoint(int(rect.left() + 16.5 * rect.width() / dialog.canvas.image.width()),
                   int(rect.top() + 16.5 * rect.height() / dialog.canvas.image.height()))
    before_view = dialog.viewport
    navigated = []
    dialog.canvas.detail_requested.connect(lambda x, y: navigated.append((x, y)))
    # Qt's actual double-click sequence is press/release, double-click/release.
    # A successful first press synchronously disarms the one-shot picker.
    QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=point)
    assert not dialog.canvas.picking and dialog.sample_source_position is not None
    picked = deepcopy(dialog.settings)
    QTest.mouseDClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=point)
    assert not navigated
    assert dialog.zoom_choice.currentData() == 0 and dialog.viewport == before_view
    assert dialog.settings == picked and not window.editor.can_undo
    # Suppression must not make subsequent deliberate unarmed navigation inert.
    QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseDClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=point)
    assert len(navigated) == 1 and dialog.zoom_choice.currentData() == 1


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('mode', ['source', 'matte'])
def test_small_dialog_picked_detail_has_no_vertical_overlap_and_localized_actions(
        window, app, locale, mode):
    from lumen.i18n import set_locale, tr

    set_locale(locale)
    dialog = loaded(window)
    install_source(dialog, (2160, 3840), (1997, 1081))
    zoom(dialog, 8); dialog.set_detail_center(1997, 1081)
    dialog.resize(320, 540); app.processEvents()
    arm(dialog); click_sample(dialog, *dialog.viewport.grid_at(1997, 1081))
    dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData(mode))
    app.processEvents()
    assert dialog.height() >= 600
    rows = [dialog.canvas, dialog.preview_choice, dialog.zoom_choice,
            dialog.pick_button, dialog.sample_label, dialog.resolution,
            dialog.scroll, dialog.status, dialog.apply_button]
    for previous, following in zip(rows, rows[1:]):
        assert previous.geometry().bottom() < following.geometry().top(), (
            locale, mode, previous.objectName(), previous.geometry(), following.geometry())
    for widget in rows:
        assert widget.geometry().top() >= 0
        assert widget.geometry().bottom() < dialog.height()
    for label in (dialog.sample_label, dialog.resolution, dialog.status):
        assert label.height() >= label.heightForWidth(label.width()), (locale, mode, label.text())
    assert dialog.zoom_choice.accessibleName() == str(tr('Source detail zoom'))
    assert dialog.center_x.accessibleName() == str(tr('Detail center X'))
    assert dialog.center_y.accessibleName() == str(tr('Detail center Y'))
    assert dialog.pick_button.accessibleName() == str(tr('Replace ranges from source click'))
    texts = [dialog.canvas.toolTip(), dialog.pick_button.toolTip(),
             dialog.zoom_choice.accessibleName(), dialog.center_x.accessibleName(),
             dialog.center_y.accessibleName(), dialog.pick_button.text()]
    texts.extend(dialog.zoom_choice.itemText(index) for index in range(dialog.zoom_choice.count()))
    assert all(texts)
    if locale == 'zh-CN':
        assert all(any('\u4e00' <= char <= '\u9fff' for char in text) for text in texts), texts

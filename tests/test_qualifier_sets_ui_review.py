"""Independent real Qt multi-range transaction and layout review."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from lumen.i18n import set_locale
from test_qualifier_ui import app, loaded, window
from test_qualifier_picker_ui import click_sample


def choose(combo, data):
    index = next((i for i in range(combo.count()) if combo.itemData(i) == data), -1)
    assert index >= 0
    combo.setFocus()
    QTest.keyClick(combo, Qt.Key.Key_Home)
    for _ in range(index): QTest.keyClick(combo, Qt.Key.Key_Down)
    assert combo.currentData() == data


def pick(dialog, mode, x=16, y=16):
    choose(dialog.sample_mode, mode)
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
    assert dialog.canvas.picking
    click_sample(dialog, x, y)


def reveal_click(button):
    button.setFocus(); QTest.qWait(80)
    assert button.visibleRegion().boundingRect() == button.rect()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)


def test_set_selection_edit_remove_reset_and_exact_transaction(window, app):
    before = deepcopy(window.project)
    dialog = loaded(window); app.processEvents()
    source = dialog.snapshot.frame.copy()
    pick(dialog, 'replace'); base = deepcopy(dialog.settings)
    pick(dialog, 'add', 48)
    assert len(dialog.settings.inclusion_ranges) == 1
    assert dialog.range_choice.currentData() == ('inclusion_ranges', 0)
    dialog.controls['hue_width'].setValue(17)
    assert dialog.settings.inclusion_ranges[0].hue_width == 17 / 360
    assert dialog.settings.hue_width == base.hue_width
    pick(dialog, 'subtract')
    assert len(dialog.settings.exclusion_ranges) == 1
    assert dialog.matte[16, 16] == 0
    choose(dialog.range_choice, ('inclusion_ranges', 0))
    reveal_click(dialog.remove_range_button)
    assert not dialog.settings.inclusion_ranges and len(dialog.settings.exclusion_ranges) == 1
    assert not dialog.remove_range_button.isEnabled()
    pick(dialog, 'add', 48)
    pending = deepcopy(dialog.settings)
    np.testing.assert_array_equal(source, dialog.snapshot.frame)
    assert window.project == before and not window.editor.can_undo
    QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
    assert window.selected_clip().grade.qualifier == pending and len(window.editor._undo) == 1
    after = deepcopy(window.project)
    window.undo(); assert window.project == before
    window.redo(); assert window.project == after
    again = loaded(window); app.processEvents()
    QTest.mouseClick(again.apply_button, Qt.MouseButton.LeftButton)
    assert len(window.editor._undo) == 1
    again = loaded(window); app.processEvents()
    reveal_click(again.reset_ranges_button)
    assert not again.settings.inclusion_ranges and not again.settings.exclusion_ranges
    assert again.settings.hue_center == pending.hue_center
    QTest.mouseClick(again.close_button, Qt.MouseButton.LeftButton)
    assert window.project == after


@pytest.mark.parametrize('mode,field', [('add', 'inclusion_ranges'), ('subtract', 'exclusion_ranges')])
def test_capacity_invalid_sample_cancel_and_stale(window, app, mode, field):
    dialog = loaded(window); app.processEvents()
    before = deepcopy(window.project)
    for _ in range(8): pick(dialog, mode)
    assert len(getattr(dialog.settings, field)) == 8
    pending = deepcopy(dialog.settings)
    pick(dialog, mode)
    assert dialog.settings == pending and dialog.canvas.picking
    assert '8' in dialog.sample_label.text()
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
    reveal_click(dialog.reset_ranges_button)
    assert not getattr(dialog.settings, field)
    frame = dialog.snapshot.frame.copy(); frame[:64, :64] = .4; frame.setflags(write=False)
    dialog.snapshot = replace(dialog.snapshot, frame=frame)
    dialog.render_preview(); app.processEvents()
    pick(dialog, mode)
    assert not getattr(dialog.settings, field) and dialog.canvas.picking
    window.revision += 1; dialog.check_current(); app.processEvents()
    assert not dialog.sample_mode.isEnabled() and not dialog.pick_button.isEnabled()
    assert not dialog.range_choice.isEnabled() and not dialog.apply_button.isEnabled()
    QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)
    assert window.project == before and not window.editor.can_undo


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('size', [(320, 600), (360, 600), (760, 800)])
def test_new_controls_focus_visible_no_clipped_captions(window, app, locale, size):
    set_locale(locale)
    dialog = loaded(window); dialog.resize(*size); QTest.qWait(100)
    assert dialog.width() == size[0]
    for mode in ['replace', 'add', 'subtract']:
        choose(dialog.sample_mode, mode); QTest.qWait(100)
        for widget in [dialog.sample_mode, dialog.pick_button]:
            assert widget.visibleRegion().boundingRect() == widget.rect()
        assert dialog.pick_button.fontMetrics().horizontalAdvance(dialog.pick_button.text()) + 16 <= dialog.pick_button.width()
        assert dialog.sample_mode.fontMetrics().horizontalAdvance(dialog.sample_mode.currentText()) + 25 <= dialog.sample_mode.width()
        assert dialog.sample_mode.geometry().intersected(dialog.pick_button.geometry()).isEmpty()
    pick(dialog, 'add')
    for widget in [dialog.range_choice, dialog.remove_range_button, dialog.reset_ranges_button]:
        widget.setFocus(); QTest.qWait(100)
        assert widget.visibleRegion().boundingRect() == widget.rect()
        if hasattr(widget, 'text'):
            assert widget.fontMetrics().horizontalAdvance(widget.text()) + 16 <= widget.width()
    assert dialog.scroll.horizontalScrollBar().maximum() == 0


@pytest.mark.parametrize('message', [
    'Qualifier range requires exactly its documented fields',
    'Qualifier range sets must be bounded lists',
    'Qualifier range sets must be lists of at most 8 entries',
    'Qualifier range sets require typed range records',
    'Qualifier exclusion mode must be a boolean',
    'Qualifier range set is full (maximum 8)',
    'Version 19 requires exactly the legacy qualifier fields',
])
def test_set_errors_have_direct_chinese_translation(message):
    from lumen.i18n import tr_error
    try:
        set_locale('en'); assert tr_error(message) == message
        set_locale('zh-CN'); translated = str(tr_error(message))
        assert translated != message and '技术错误详情' not in translated
        assert any('\u4e00' <= char <= '\u9fff' for char in translated)
    finally:
        set_locale('en')


@pytest.mark.parametrize('mode', ['add', 'subtract'])
@pytest.mark.parametrize('view', ['matte', 'overlay'])
def test_set_sampling_preserves_frozen_source_at_detail_and_replace_clears(window, app, mode, view):
    dialog = loaded(window); app.processEvents()
    frame = dialog.snapshot.frame
    digest = hashlib.sha256(frame.tobytes()).hexdigest()
    choose(dialog.preview_choice, view)
    choose(dialog.zoom_choice, 2)
    dialog.set_detail_center(32, 32); app.processEvents()
    sx, sy = 32, 32
    x, y = dialog.viewport.grid_at(sx, sy)
    pick(dialog, mode, x, y)
    pending = deepcopy(dialog.settings)
    field = 'inclusion_ranges' if mode == 'add' else 'exclusion_ranges'
    assert len(getattr(pending, field)) == 1
    assert hashlib.sha256(frame.tobytes()).hexdigest() == digest and not frame.flags.writeable
    assert dialog.preview_choice.currentData() == view
    pick(dialog, mode, x, y)
    assert len(getattr(dialog.settings, field)) == 2
    assert getattr(dialog.settings, field)[0] == getattr(dialog.settings, field)[1]
    pick(dialog, 'replace', x, y)
    assert not dialog.settings.inclusion_ranges and not dialog.settings.exclusion_ranges
    assert dialog.range_choice.currentData() == ('base', 0)
    assert hashlib.sha256(frame.tobytes()).hexdigest() == digest
    assert not window.editor.can_undo

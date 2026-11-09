"""Independent pending range editing, reset and transaction semantics."""
from copy import deepcopy

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from test_qualifier_ui import app, loaded, window
from test_qualifier_picker_ui import click_sample


def sample(dialog, mode, x):
    dialog.sample_mode.setCurrentIndex(dialog.sample_mode.findData(mode))
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
    click_sample(dialog, x, 16)


def test_include_exclude_edit_remove_reset_and_replace_are_pending(window, app):
    before = deepcopy(window.project)
    dialog = loaded(window); app.processEvents()
    sample(dialog, 'replace', 16)
    base = deepcopy(dialog.settings)
    sample(dialog, 'add', 176)
    assert len(dialog.settings.inclusion_ranges) == 1
    assert dialog.range_choice.currentData() == ('inclusion_ranges', 0)
    assert dialog.matte[16, 16] == dialog.matte[16, 176] == 1
    sample(dialog, 'subtract', 16)
    assert len(dialog.settings.exclusion_ranges) == 1
    assert dialog.matte[16, 16] == 0 and dialog.matte[16, 176] == 1
    old_include = deepcopy(dialog.settings.inclusion_ranges)
    dialog.controls['hue_softness'].setValue(2)
    assert dialog.settings.exclusion_ranges[0].hue_softness == 2 / 360
    assert dialog.settings.inclusion_ranges == old_include
    assert dialog.settings.hue_softness == base.hue_softness
    dialog.remove_range_button.click()
    assert not dialog.settings.exclusion_ranges
    assert not dialog.remove_range_button.isEnabled()  # Base is never removable.
    dialog.reset_ranges_button.click()
    assert dialog.settings == base
    sample(dialog, 'add', 176); sample(dialog, 'subtract', 16)
    sample(dialog, 'replace', 176)
    assert not dialog.settings.inclusion_ranges and not dialog.settings.exclusion_ranges
    assert dialog.range_choice.count() == 1
    assert window.project == before and not window.editor.can_undo
    dialog.reject()
    assert window.project == before and not window.editor.can_undo


def test_invalid_extra_range_can_be_repaired_or_removed_without_corrupting_base(window, app):
    dialog = loaded(window); app.processEvents()
    sample(dialog, 'replace', 16); sample(dialog, 'add', 176)
    base = dialog.settings.saturation_low
    dialog.controls['saturation_low'].setValue(90)
    dialog.controls['saturation_high'].setValue(20)
    assert not dialog.apply_button.isEnabled() and not dialog.pick_button.isEnabled()
    assert 'minimum' in dialog.status.text().lower()
    assert dialog.settings.saturation_low == base
    dialog.controls['saturation_high'].setValue(95)
    dialog.render_preview()
    assert dialog.apply_button.isEnabled()
    dialog.controls['saturation_high'].setValue(20)
    dialog.remove_range_button.click()
    assert dialog.apply_button.isEnabled() and not dialog.settings.inclusion_ranges


def test_repeated_sample_mode_change_disarms_and_apply_is_single_undo(window, app):
    before = deepcopy(window.project)
    dialog = loaded(window); app.processEvents()
    sample(dialog, 'replace', 16)
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
    dialog.sample_mode.setCurrentIndex(dialog.sample_mode.findData('add'))
    assert not dialog.canvas.picking
    untouched = deepcopy(dialog.settings)
    dialog.pick_sample(176, 16)
    assert dialog.settings == untouched
    sample(dialog, 'add', 176)
    first = deepcopy(dialog.settings)
    sample(dialog, 'add', 176)
    assert dialog.settings.inclusion_ranges[-1] == first.inclusion_ranges[-1]
    assert len(dialog.settings.inclusion_ranges) == 2  # Separate records, identical membership.
    sample(dialog, 'subtract', 16)
    pending = deepcopy(dialog.settings)
    source = dialog.snapshot.frame.copy()
    dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData('overlay'))
    dialog.render_preview()
    np.testing.assert_array_equal(source, dialog.snapshot.frame)
    dialog.apply_settings()
    assert window.selected_clip().grade.qualifier == pending and len(window.editor._undo) == 1
    window.undo(); assert window.project == before
    window.redo(); assert window.selected_clip().grade.qualifier == pending


def test_stale_set_operations_never_mutate_pending_or_project(window, app):
    dialog = loaded(window); app.processEvents()
    sample(dialog, 'replace', 16); sample(dialog, 'add', 176)
    pending = deepcopy(dialog.settings); before = deepcopy(window.project)
    window.revision += 1
    dialog.remove_selected_range(); dialog.reset_extra_ranges(); dialog.apply_settings()
    assert dialog.settings == pending and window.project == before
    assert not dialog.sample_mode.isEnabled() and not dialog.range_choice.isEnabled()


def test_compact_coordinates_keep_five_digits_visible_with_layout_governor(window, app):
    from lumen.ui_style import apply_ui_layout_tokens
    dialog = loaded(window); dialog.resize(320, 600)
    apply_ui_layout_tokens(dialog)
    dialog.zoom_choice.setCurrentIndex(1)
    for spin in (dialog.center_x, dialog.center_y):
        spin.blockSignals(True); spin.setRange(0, 99999); spin.setValue(99999); spin.blockSignals(False)
    QTest.qWait(100)
    for spin in (dialog.center_x, dialog.center_y):
        editor = spin.lineEdit()
        assert editor.fontMetrics().horizontalAdvance(editor.text()) + 4 <= editor.contentsRect().width()
        assert spin.visibleRegion().boundingRect() == spin.rect()
    mode = dialog.sample_mode
    button = dialog.pick_button
    from PySide6.QtCore import QRect, QPoint
    a = QRect(mode.mapTo(dialog, QPoint()), mode.size())
    b = QRect(button.mapTo(dialog, QPoint()), button.size())
    assert a.intersected(b).isEmpty()


@pytest.mark.parametrize('width', [320, 480, 680])
@pytest.mark.parametrize('font_points', [10, 11])
def test_sampling_hosts_follow_governed_combo_minimums(window, app, width, font_points):
    from PySide6.QtCore import QRect, QPoint
    from PySide6.QtGui import QFont
    from lumen.ui_style import apply_ui_layout_tokens

    dialog = loaded(window)
    font = QFont(dialog.font())
    font.setPointSizeF(font_points)
    window.setFont(font)
    apply_ui_layout_tokens(window)
    dialog.setFont(font)
    dialog.resize(width, 600)
    apply_ui_layout_tokens(dialog)
    dialog.sample_mode.setCurrentIndex(dialog.sample_mode.findData('soften'))
    QTest.qWait(100)
    assert dialog.width() == width
    for combo, neighbour in ((dialog.sample_mode, dialog.pick_button),
                             (dialog.softness_channel, dialog.softness_target)):
        assert combo.font().pointSizeF() == font_points
        host = combo.parentWidget()
        assert host.rect().contains(combo.geometry())
        a = QRect(combo.mapTo(dialog, QPoint()), combo.size())
        b = QRect(neighbour.mapTo(dialog, QPoint()), neighbour.size())
        assert a.intersected(b).isEmpty()
        assert dialog.rect().contains(a) and dialog.rect().contains(b)
        assert combo.visibleRegion().boundingRect() == combo.rect()
        assert neighbour.visibleRegion().boundingRect() == neighbour.rect()
    for spin in (dialog.center_x, dialog.center_y):
        spin.blockSignals(True)
        spin.setRange(0, 99999)
        spin.setValue(99999)
        spin.blockSignals(False)
        editor = spin.lineEdit()
        assert editor.fontMetrics().horizontalAdvance(editor.text()) + 4 <= editor.contentsRect().width()
        assert spin.visibleRegion().boundingRect() == spin.rect()

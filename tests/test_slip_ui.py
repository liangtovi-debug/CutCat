"""Explicit staged slip control with real headless Qt widgets."""
from copy import deepcopy
from fractions import Fraction
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from lumen.i18n import set_locale
from lumen.timing import ClipTimeMap
from test_speed_ui import app, window  # shared isolated real-window fixture


def partial(w):
    w.editor.trim_clip_source('c', Fraction(1, 2), 31)
    w.editor.set_clip_speed('c', Fraction(3, 2)); w.changed()


def test_click_slip_preserves_partial_tail_and_ignores_staged_other_controls(window):
    w = window; partial(w)
    before = deepcopy(w.project); revision = w.revision
    w.start_spin.setValue(50); w.duration_spin.setValue(19); w.speed_edit.setText('2')
    w.slip_source_edit.setText('2.5')
    assert w.project == before and w.revision == revision
    assert '33' in w.slip_range_note.text()
    assert w.apply_slip_button.isEnabled()
    QTest.mouseClick(w.apply_slip_button, Qt.MouseButton.LeftButton)
    c = w.selected_clip()
    assert c.timing == ClipTimeMap(Fraction(5, 2), 33, Fraction(3, 2))
    assert (c.start, c.duration) == (10, 21)
    assert w.revision == revision + 1
    after = deepcopy(w.project)
    assert not w.apply_slip_button.isEnabled()
    w.apply_slip(); assert w.revision == revision + 1
    w.undo(); assert w.project == before
    w.redo(); assert w.project == after


@pytest.mark.parametrize('value', ['-1', '290', '1/1000001', 'NaN', '1/0'])
def test_invalid_slip_never_changes_project(window, value):
    w = window; partial(w)
    before = deepcopy(w.project); revision = w.revision; undo = deepcopy(w.editor._undo)
    w.slip_source_edit.setText(value)
    assert not w.apply_slip_button.isEnabled()
    w.apply_slip()
    assert w.project == before and w.revision == revision and w.editor._undo == undo
    assert w.test_warnings


def test_language_switch_preserves_staged_slip_and_retranslates(window):
    w = window; partial(w)
    w.slip_source_edit.setText('7/3'); before = deepcopy(w.project); revision = w.revision
    set_locale('zh-CN')
    assert w.slip_source_edit.text() == '7/3'
    assert w.apply_slip_button.text() == '应用素材滑移'
    assert '滑移后' in w.slip_range_note.text()
    assert '字幕' in w.slip_anchor_note.text()
    set_locale('en')
    assert w.slip_source_edit.text() == '7/3' and w.apply_slip_button.text() == 'Apply source slip'
    assert w.project == before and w.revision == revision


def test_disabled_without_selection_and_repeated_selection_refresh(window):
    w = window; partial(w); w.slip_source_edit.setText('8')
    w.selection = ''; w.refresh_inspector(); w.refresh_slip_range()
    assert not w.inspector.isEnabled() and not w.apply_slip_button.isEnabled()
    w.select_clip('c')
    assert w.slip_source_edit.value() == Fraction(1, 2)
    assert not w.apply_slip_button.isEnabled()


def test_out_of_range_slip_preview_is_translated(window):
    w = window; partial(w); set_locale('zh-CN')
    w.slip_source_edit.setText('290')
    assert w.slip_range_note.text() == '素材滑移超出可用的源素材范围。'
    assert not w.apply_slip_button.isEnabled()


def test_workspace_switch_keeps_staged_slip(window):
    w = window; partial(w); w.slip_source_edit.setText('11/4')
    before = deepcopy(w.project); revision = w.revision
    for mode in ('simple', 'professional', 'simple'):
        w.set_workspace_mode(mode)
        assert w.slip_source_edit.text() == '11/4'
        assert w.project == before and w.revision == revision

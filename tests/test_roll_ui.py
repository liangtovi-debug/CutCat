"""Actual Qt controls: staged roll is independent, explicit and reversible."""
from copy import deepcopy
from fractions import Fraction
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from lumen.i18n import set_locale
from lumen.model import Clip
from lumen.timing import ClipTimeMap
from test_speed_ui import app, window


def pair(w):
    w.editor.trim_clip_source('c', Fraction(1, 2), 31)
    w.editor.set_clip_speed('c', Fraction(3, 2))
    w.project.clips.append(Clip('right', 'm', 0, 31, 20, 46, time_map=ClipTimeMap(20, Fraction(101, 2), Fraction(2, 3))))
    w.project.validate(); w.changed()


def test_click_roll_ignores_all_other_staged_controls(window):
    w = window; pair(w); before = deepcopy(w.project); revision = w.revision
    assert w.roll_cut_spin.value() == 31 and not w.apply_roll_button.isEnabled()
    w.start_spin.setValue(50); w.in_spin.setText('4'); w.duration_spin.setValue(7)
    w.speed_edit.setText('2'); w.slip_source_edit.setText('9/4'); w.roll_cut_spin.setValue(33)
    assert w.project == before and w.revision == revision
    assert '33' in w.roll_range_note.text() and '23 / 44' in w.roll_range_note.text()
    assert w.apply_roll_button.isEnabled()
    QTest.mouseClick(w.apply_roll_button, Qt.MouseButton.LeftButton)
    a,b = w.project.clips
    assert a.timing == ClipTimeMap(Fraction(1, 2), 34, Fraction(3, 2))
    assert b.timing == ClipTimeMap(Fraction(64, 3), Fraction(101, 2), Fraction(2, 3))
    assert (a.start,a.end,b.start,b.end) == (10,33,33,77)
    assert w.revision == revision+1 and not w.apply_roll_button.isEnabled()
    after = deepcopy(w.project); w.apply_roll(); assert w.revision == revision+1
    w.undo(); assert w.project == before
    w.redo(); assert w.project == after


@pytest.mark.parametrize('cut', [0,10,77,100])
def test_invalid_roll_preview_and_application_are_atomic(window, cut):
    w=window; pair(w); before=deepcopy(w.project); revision=w.revision
    w.roll_cut_spin.setValue(cut)
    assert not w.apply_roll_button.isEnabled()
    w.apply_roll()
    assert w.project == before and w.revision == revision and w.test_warnings


def test_no_adjacent_clip_gap_and_different_track(window):
    w=window
    assert not w.apply_roll_button.isEnabled() and 'adjacent' in w.roll_range_note.text()
    pair(w); w.project.clips[1].start+=1; w.changed()
    assert not w.apply_roll_button.isEnabled() and 'adjacent' in w.roll_range_note.text()
    w.project.clips[1].start-=1; w.project.clips[1].track=1; w.changed()
    assert not w.apply_roll_button.isEnabled() and 'same track' in w.roll_range_note.text()


def test_locale_and_workspace_changes_keep_staged_cut(window):
    w=window; pair(w); w.roll_cut_spin.setValue(33); before=deepcopy(w.project); revision=w.revision
    set_locale('zh-CN')
    assert w.apply_roll_button.text()=='应用滚动剪辑' and '右侧片段' in w.roll_range_note.text()
    assert '字幕' in w.roll_anchor_note.text()
    for mode in ('simple','professional','simple'):
        w.set_workspace_mode(mode)
        assert w.roll_cut_spin.value()==33
    set_locale('en')
    assert w.apply_roll_button.text()=='Apply roll edit' and 'Right clip:' in w.roll_range_note.text()
    assert w.project==before and w.revision==revision


def test_fade_error_localization_and_selection_reset(window):
    w=window; pair(w); w.project.clips[0].fade_out=21; w.changed(); set_locale('zh-CN')
    w.roll_cut_spin.setValue(30)
    assert w.roll_range_note.text()=='滚动剪辑会缩短已有淡入淡出，请先调整淡入淡出时长。'
    assert not w.apply_roll_button.isEnabled()
    w.selection=''; w.refresh_inspector()
    assert not w.inspector.isEnabled() and not w.apply_roll_button.isEnabled()
    w.select_clip('c')
    assert w.roll_cut_spin.value()==31 and not w.apply_roll_button.isEnabled()
    w.roll_cut_spin.setValue(32); assert w.apply_roll_button.isEnabled()
    w.select_clip('right'); assert w.roll_cut_spin.value()==77 and not w.apply_roll_button.isEnabled()


def test_high_timeline_does_not_stage_silent_clamped_roll(window):
    w=window; pair(w)
    for c in w.project.clips: c.start+=100_000_000
    w.changed()
    assert w.roll_cut_spin.value()==100_000_031 and not w.apply_roll_button.isEnabled()
    for c in w.project.clips: c.start+=2_147_483_647
    before=deepcopy(w.project); w.refresh_roll_range()
    assert not w.roll_cut_spin.isEnabled() and not w.apply_roll_button.isEnabled()
    assert '2147483647' in w.roll_range_note.text()
    w.apply_roll(); assert w.project==before

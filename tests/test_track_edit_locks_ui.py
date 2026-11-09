"""Native Qt lock headers, inspection, input guards and stale edit boundaries."""
from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from lumen.i18n import set_locale, tr
from lumen.model import Clip
from lumen.ui_track_edit_locks import LOCK_REASON, LOCK_NOTE, SOURCE_LOCK_REASON
from test_speed_ui import app, window
from test_clip_multiselection_timeline import press, move, release


def test_four_header_buttons_mouse_keyboard_and_history(window, app):
    w = window; w.show(); app.processEvents()
    buttons = w.timeline.track_lock_buttons
    assert len(buttons) == 4
    assert all(b.parentWidget() is w.timeline and b.isCheckable() for b in buttons)
    before = len(w.editor._undo)
    QTest.mouseClick(buttons[0], Qt.MouseButton.LeftButton)
    assert w.project.track_edit_locks == [True, False, False, False]
    buttons[2].setFocus(); QTest.keyClick(buttons[2], Qt.Key.Key_Space)
    assert w.project.track_edit_locks == [True, False, True, False]
    assert len(w.editor._undo) == before + 2
    w.undo(); assert w.project.track_edit_locks == [True, False, False, False]
    w.redo(); assert w.project.track_edit_locks == [True, False, True, False]
    for i, button in enumerate(buttons):
        assert button.isChecked() == w.project.track_edit_locks[i]
        assert button.accessibleName() and button.accessibleDescription()
        assert button.geometry().right() < 88
        assert 34+i*53 <= button.y() < 34+i*53+45


@pytest.mark.parametrize('edge', [None, 'left', 'right'])
def test_locked_clip_remains_selectable_without_gesture(window, edge):
    w = window; w.toggle_track_edit_lock(0, True)
    w.clear_clip_selection(); w.refresh()
    before = deepcopy(w.project.to_dict())
    p = press(w.timeline, 'c', edge=edge)
    assert w.selection == 'c' and w.selected_clip_ids == ('c',)
    assert w.timeline.drag is None and w.timeline.trim_drag is None
    release(w.timeline, move(w.timeline, p, 10))
    assert w.project.to_dict() == before
    assert w.inspector.isEnabled() and not w.start_spin.isEnabled() and w.track_lock_note.text() == LOCK_NOTE
    assert not w.shortcuts.enabled('edit.delete') and not w.shortcuts.enabled('edit.split')
    assert w.statusBar().currentMessage() == LOCK_REASON


def test_mixed_locked_group_disables_commands_and_drag(window):
    w = window
    w.project.clips.append(Clip('b', 'm', 1, 60, 0, 20))
    w.editor.group_clips(('c', 'b')); w.refresh(); w.select_clip('c')
    w.toggle_track_edit_lock(1, True)
    p = press(w.timeline, 'c'); release(w.timeline, move(w.timeline, p, 10))
    assert set(w.selected_clip_ids) == {'c', 'b'}
    assert w.timeline.drag is None
    for command in ('edit.group', 'edit.ungroup', 'edit.delete', 'edit.split'):
        assert not w.shortcuts.enabled(command)
    assert not w.delete_clips_button.isEnabled()


@pytest.mark.parametrize('edge', [None, 'left'])
def test_lock_during_mouse_gesture_cancels_without_commit(window, edge):
    w = window
    original = deepcopy(w.project.clips)
    p = press(w.timeline, 'c', edge=edge); move(w.timeline, p, 4)
    assert w.timeline.drag is not None or w.timeline.trim_drag is not None
    w.toggle_track_edit_lock(0, True)
    assert w.timeline.drag is None and w.timeline.trim_drag is None
    release(w.timeline, move(w.timeline, p, 8))
    assert w.project.clips == original


def test_cross_track_locked_target_preview_is_invalid(window):
    w = window; w.toggle_track_edit_lock(1, True)
    p = press(w.timeline, 'c')
    from PySide6.QtCore import QPointF
    from test_clip_multiselection_timeline import mouse
    from PySide6.QtCore import QEvent
    target = QPointF(p.x()+40, 34+53+20)
    mouse(w.timeline, QEvent.Type.MouseMove, target, buttons=Qt.MouseButton.LeftButton)
    assert w.timeline.drag is not None and w.timeline.drag.error
    release(w.timeline, target)
    assert w.project.clips[0].track == 0 and w.project.clips[0].start == 10


def test_lock_preserves_preview_and_export_and_does_not_redirect_insertion(window):
    w = window
    w.preview_revision = w.revision; w.loaded_preview_mode = w.preview_mode
    w.update_playback_controls()
    assert w.play_button.isEnabled()
    w.toggle_track_edit_lock(0, True)
    assert w.play_button.isEnabled() and w.preview_button.isEnabled() and w.export_button.isEnabled()
    w.undo(); assert w.play_button.isEnabled()
    w.redo(); assert w.play_button.isEnabled()
    assert w.track_choice.currentIndex() == 0 and not w.add_media_button.isEnabled()
    w.track_choice.setCurrentIndex(1)
    assert w.add_media_button.isEnabled()
    assert w.project.track_edit_locks == [True, False, False, False]


def test_live_language_reason_and_accessible_state(window):
    w = window; w.toggle_track_edit_lock(0, True); w.explain_track_edit_lock()
    set_locale('zh-CN')
    assert '已锁定' in w.timeline.track_lock_buttons[0].text()
    assert '编辑锁定' in w.timeline.track_lock_buttons[0].accessibleName()
    assert '已锁定' in w.track_lock_note.text()
    w.toggle_track_edit_lock(0, False)
    from lumen.ui_video_transition import AUDIO
    assert w.video_transition_button.toolTip() == tr(AUDIO)
    assert w.add_media_button.toolTip() == tr('Add to\nselected track')
    w.toggle_track_edit_lock(0, True)
    assert w.statusBar().currentMessage() == tr(LOCK_REASON)
    set_locale('en')
    assert w.timeline.track_lock_buttons[0].text() == 'Locked'
    assert w.track_lock_note.text() == LOCK_NOTE
    w.toggle_track_edit_lock(0, False)
    assert w.inspector.isEnabled() and not w.track_lock_note.isVisible()


def test_delete_focus_guard_still_respects_text_inputs(window, app):
    w = window; w.show(); app.processEvents()
    w.speed_edit.setFocus(); w.speed_edit.selectAll()
    QTest.keyClick(w.speed_edit, Qt.Key.Key_Delete)
    assert len(w.project.clips) == 1
    w.toggle_track_edit_lock(0, True)
    w.timeline.setFocus(); QTest.keyClick(w.timeline, Qt.Key.Key_Delete)
    assert len(w.project.clips) == 1


@pytest.mark.parametrize('lock_at_probe', [False, True])
def test_relink_checks_all_source_references_before_probe_and_commit(window, monkeypatch, lock_at_probe):
    w = window
    w.project.media[0].path += '.missing'
    w.project.clips.append(Clip('b', 'm', 1, 60, 0, 20))
    w.refresh()
    if not lock_at_probe:
        w.toggle_track_edit_lock(1, True)
    from lumen import ui
    monkeypatch.setattr(ui.QFileDialog, 'getOpenFileName', lambda *_: ('replacement.mp4', ''))
    calls = []
    original = deepcopy(w.project.media[0])
    def probe(*_, **__):
        calls.append(True)
        w.toggle_track_edit_lock(1, True)
        result = deepcopy(original); result.path = 'replacement.mp4'
        return result
    monkeypatch.setattr(ui, 'probe', probe)
    w.relink_dialog()
    assert bool(calls) == lock_at_probe
    assert w.project.media[0] == original
    assert w.project.track_edit_locks[1]
    assert w.test_warnings[-1] == SOURCE_LOCK_REASON


def test_every_lock_error_has_chinese_boundary_translation():
    import ast
    from pathlib import Path
    import lumen.model
    from lumen.i18n import tr_error
    tree = ast.parse(Path(lumen.model.__file__).read_text())
    messages = [n.args[0].value for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == 'ValueError' and n.args
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)
                and 'lock' in n.args[0].value.lower()]
    set_locale('zh-CN')
    assert len(messages) >= 10
    for message in messages:
        assert str(tr_error(message)) != message and '技术错误' not in str(tr_error(message))


def test_tab_reaches_and_exposes_all_compact_header_locks(window, app):
    from PySide6.QtGui import QFont
    from lumen.ui_style import apply_ui_layout_tokens
    w = window
    w.resize(1000, 700)
    w.show(); app.processEvents()
    font = QFont(w.font()); font.setPointSize(15); w.setFont(font)
    apply_ui_layout_tokens(w); app.processEvents()
    w.vertical_split.setSizes([500, 150]); app.processEvents()
    buttons = w.timeline.track_lock_buttons
    buttons[0].setFocus(); app.processEvents()
    for index, button in enumerate(buttons):
        assert app.focusWidget() is button
        assert button.height() <= 22
        assert button.fontMetrics().height() <= button.height()-2
        viewport = w.timeline_scroll.viewport()
        assert viewport.rect().contains(button.mapTo(viewport, button.rect().center()))
        if index < 3:
            QTest.keyClick(button, Qt.Key.Key_Tab); app.processEvents()


def test_locked_inspector_tabs_scroll_and_read_only_controls(window, app):
    w = window; w.show(); app.processEvents()
    w.toggle_track_edit_lock(0, True)
    before = deepcopy(w.project.to_dict())
    bar = w.inspector_tabs.tabBar()
    for index in (0, 1, 2, 0):
        w.inspector.verticalScrollBar().setValue(0); app.processEvents()
        QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(index).center())
        app.processEvents()
        assert w.inspector_tabs.currentIndex() == index
        assert w.inspector.isEnabled() and bar.isEnabled()
    scroll = w.inspector.verticalScrollBar()
    assert scroll.maximum() > 0 and scroll.isEnabled()
    scroll.setFocus(); QTest.keyClick(scroll, Qt.Key.Key_End)
    assert scroll.value() == scroll.maximum()
    for control in (w.start_spin, w.speed_edit, w.gain, w.mute, w.grade_enabled,
                    w.curve_editor, w.selective_curve_control, w.reset_color_button,
                    w.apply_transform_button, *w.wheel_controls.values()):
        assert not control.isEnabled()
    # A late panel update cannot re-enable an editing leaf while locked.
    w.reset_color_button.setEnabled(True)
    assert not w.reset_color_button.isEnabled()
    w.inspector_tabs.setCurrentIndex(1)
    w.color_tabs.setCurrentIndex(2)
    assert w.curve_choice.isEnabled() and w.wheel_choice.isEnabled()
    w.curve_choice.setCurrentIndex(1)
    assert w.project.to_dict() == before
    w.toggle_track_edit_lock(0, False)
    assert w.start_spin.isEnabled() and w.speed_edit.isEnabled()
    assert w.gain.isEnabled() and w.mute.isEnabled() and w.grade_enabled.isEnabled()
    assert w.reset_color_button.isEnabled()
    # Restore underlying dynamic availability, not an unconditional enable-all.
    assert not w.remove_lut_button.isEnabled()
    assert not w.apply_transform_button.isEnabled()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_lock_note_rendered_lines_fit_actual_inspector_viewport(window, app, locale, mode):
    from PySide6.QtGui import QFont
    from PySide6.QtCore import QPoint, QRect
    from lumen.ui_style import apply_ui_layout_tokens
    w = window
    w.set_workspace_mode(mode, persist=False)
    set_locale(locale)
    font = QFont('Noto Sans CJK SC', 15); w.setFont(font)
    apply_ui_layout_tokens(w)
    w.resize(1000, 768); w.show(); app.processEvents()
    w.main_split.setSizes([190, 450, 360]); app.processEvents()
    w.toggle_track_edit_lock(0, True); app.processEvents()
    w.inspector.verticalScrollBar().setValue(0)
    w.inspector.horizontalScrollBar().setValue(0); app.processEvents()
    note = w.track_lock_note; viewport = w.inspector.viewport()
    origin = note.mapTo(viewport, QPoint(0, 0))
    available = viewport.width() - origin.x() - 4
    assert origin.x() >= 0 and available > 0
    assert note.isVisible() and note.text() == tr(LOCK_NOTE)
    for line in note.text().splitlines():
        assert note.fontMetrics().horizontalAdvance(line) <= available
    flags = int(note.alignment()) | int(Qt.TextFlag.TextWordWrap)
    needed = note.fontMetrics().boundingRect(QRect(0, 0, available, 10000), flags, note.text()).height()
    assert note.contentsRect().height() >= needed
    # Verify pixels can be rendered from the actual onscreen viewport geometry.
    assert not viewport.grab().isNull()

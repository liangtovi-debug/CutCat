"""Persistent groups own selection, mouse gestures and guarded menu commands."""
from copy import deepcopy
import json

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QFocusEvent, QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QSpinBox, QComboBox, QVBoxLayout

from lumen.i18n import set_locale, tr_error
from lumen.model import Caption, Clip, ClipGroup, Editor, Project
from lumen.shortcuts import COMMANDS, SETTINGS_KEY, default_bindings
from lumen.ui_clip_groups import GROUP_HELP, GROUP_TRIM_HELP
from test_speed_ui import app, window
from test_clip_multiselection_timeline import mouse, press, move, release, timeline


def snapshot(w):
    return deepcopy((w.project.to_dict(), w.editor.revision, w.revision,
                     w.editor._undo, w.editor._redo, w.dirty, w.preview_revision))


def add_clips(w):
    w.project.clips.extend([Clip('b', 'm', 1, 60, 0, 20),
                            Clip('sound', 'm', 2, 80, 0, 20)])
    w.refresh()
    w.set_selected_clip_ids(('c', 'b'), anchor='c')


def create_group(w):
    add_clips(w)
    w.group_selected_clips()
    assert len(w.project.clip_groups) == 1
    return w.project.clip_groups[0]


@pytest.fixture
def grouped_timeline(timeline):
    t = timeline
    t.editor_for_test.group_clips(('a', 'b'))
    t.set_project(t.editor_for_test.project, 'a', ('a', 'b'))
    return t


@pytest.mark.parametrize('member', ['a', 'b'])
def test_member_click_and_release_preserve_group(grouped_timeline, member):
    t = grouped_timeline
    t.set_project(t.project, 'sound', ('sound',))
    p = press(t, member)
    assert set(t.selected_clip_ids) == {'a', 'b'}
    assert t.drag is not None and not t.drag.collapse_on_release
    release(t, p)
    assert set(t.selected_clip_ids) == {'a', 'b'}
    assert t.select_signals == [member]
    assert not t.group_signals and not t.move_signals
    # Repeated clicks still cannot degrade a persistent group to one clip.
    p = press(t, member); release(t, p)
    assert set(t.selected_clip_ids) == {'a', 'b'} and t.select_signals == [member]


@pytest.mark.parametrize('member', ['c', 'b'])
@pytest.mark.parametrize('edge', [None, 'left', 'right'])
def test_shift_click_toggles_entire_group_without_model_edit(window, member, edge):
    w = window; create_group(w)
    before = snapshot(w)
    p = press(w.timeline, member, edge=edge, shift=True); release(w.timeline, p)
    assert w.selected_clip_ids == () and w.selection == ''
    assert w.timeline.trim_drag is None and w.timeline.drag is None
    p = press(w.timeline, member, edge=edge, shift=True); release(w.timeline, p)
    assert set(w.selected_clip_ids) == {'c', 'b'}
    assert snapshot(w) == before


@pytest.mark.parametrize('member', ['c', 'b'])
@pytest.mark.parametrize('edge', ['left', 'right'])
def test_grouped_edge_selects_group_and_explains_ungroup(window, member, edge):
    w = window; create_group(w); w.select_clip('sound')
    before = snapshot(w)
    p = press(w.timeline, member, edge=edge)
    assert set(w.selected_clip_ids) == {'c', 'b'}
    assert w.timeline.trim_drag is None and w.timeline.drag is None
    assert w.statusBar().currentMessage() == GROUP_TRIM_HELP
    assert w.timeline.toolTip() == GROUP_TRIM_HELP
    release(w.timeline, move(w.timeline, p, 4))
    assert snapshot(w) == before
    assert not w.inspector.isEnabled() and 'Ungroup to edit separately' in w.clip_name.text()
    assert not w.split_clip_button.isEnabled()


def test_group_and_ungroup_are_one_edit_with_explicit_menu_actions(window):
    w = window; add_clips(w)
    before = snapshot(w)
    assert w.shortcuts.enabled('edit.group') and not w.shortcuts.enabled('edit.ungroup')
    w.shortcuts.actions['edit.group'].trigger()
    assert len(w.project.clip_groups) == 1
    assert len(w.editor._undo) == len(before[3]) + 1 and w.revision == before[2] + 1
    assert not w.shortcuts.enabled('edit.group') and w.shortcuts.enabled('edit.ungroup')
    group = deepcopy(w.project.clip_groups)
    w.shortcuts.actions['edit.ungroup'].trigger()
    assert not w.project.clip_groups
    assert set(w.selected_clip_ids) == {'c', 'b'} and w.selected_clip() is None
    w.undo(); assert w.project.clip_groups == group and set(w.selected_clip_ids) == {'c', 'b'}
    w.undo(); assert not w.project.clip_groups and set(w.selected_clip_ids) == {'c', 'b'}
    w.redo(); assert w.project.clip_groups == group
    assert not w.test_warnings


def test_no_nested_implicit_merge_or_nonclip_groups(window):
    w = window; create_group(w)
    w.toggle_clip_selection('sound')
    before = snapshot(w)
    assert not w.can_group_selected_clips()
    w.group_selected_clips(); assert snapshot(w) == before
    w.ungroup_selected_clips()
    assert not w.project.clip_groups and set(w.selected_clip_ids) == {'c', 'b', 'sound'}
    w.group_selected_clips()
    assert len(w.project.clip_groups) == 1 and len(w.project.clip_groups[0].clip_ids) == 3
    for target in ('caption', 'overlay', 'effect', 'transition'):
        w.active_selection_type = target
        before = snapshot(w)
        w.group_selected_clips(); w.ungroup_selected_clips()
        assert not w.can_group_selected_clips() and not w.can_ungroup_selected_clips()
        assert snapshot(w) == before


def test_central_selection_and_undo_reconcile_complete_group(window):
    w = window; create_group(w)
    for select in (lambda: w.select_clip('b'),
                   lambda: w.set_selected_clip_ids(('b',), anchor='b'),
                   lambda: setattr(w, 'selection', 'b')):
        select()
        assert set(w.selected_clip_ids) == {'c', 'b'}
        assert set(w.timeline.selected_clip_ids) == {'c', 'b'} and w.selected_clip() is None
    w.ungroup_selected_clips(); w.select_clip('c')
    assert w.selected_clip_ids == ('c',)
    w.undo(); assert set(w.selected_clip_ids) == {'c', 'b'}
    w.redo(); assert set(w.selected_clip_ids) == {'c', 'b'}
    w.select_clip('c'); assert w.selected_clip_ids == ('c',)


def test_reopened_groups_survive_but_old_selection_does_not(window):
    w = window; create_group(w)
    reopened = Project.from_dict(w.project.to_dict())
    w.editor = Editor(reopened); w.refresh()
    assert w.project.clip_groups and not w.selected_clip_ids
    p = press(w.timeline, 'b'); release(w.timeline, p)
    assert set(w.selected_clip_ids) == {'c', 'b'}


def test_mouse_group_drag_is_horizontal_one_undo_and_delete_is_nonripple(window, monkeypatch):
    w = window; create_group(w)
    w.project.captions.append(Caption('caption', 80, 20, 'Untouched'))
    w.refresh(); w.timeline.snapping_enabled = False
    before = snapshot(w)
    invalidations = []
    monkeypatch.setattr(w, 'invalidate_preview_frame', lambda *a, **k: invalidations.append(True))
    p = press(w.timeline, 'b')
    for delta in (3, 7, 5):
        target = move(w.timeline, p, delta, y=200)
        assert snapshot(w) == before and not invalidations
    release(w.timeline, target)
    assert [(c.start, c.track) for c in w.project.clips] == [(15, 0), (65, 1), (80, 2)]
    assert len(w.editor._undo) == len(before[3]) + 1 and invalidations == [True]
    w.undo(); assert w.project.to_dict() == before[0]
    depth = len(w.editor._undo)
    w.delete_selected()
    assert [(c.id, c.start) for c in w.project.clips] == [('sound', 80)]
    assert not w.project.clip_groups and w.project.captions[0].start == 80
    assert len(w.editor._undo) == depth + 1 and not w.selected_clip_ids
    w.undo(); assert w.project.to_dict() == before[0] and not w.selected_clip_ids


@pytest.mark.parametrize('change', ['membership', 'revision', 'session', 'selection', 'project', 'focus', 'scale'])
def test_stale_group_gesture_never_commits(window, change):
    w = window; create_group(w); w.timeline.snapping_enabled = False
    p = press(w.timeline, 'b'); target = move(w.timeline, p, 4)
    assert w.timeline.drag is not None
    if change == 'membership':
        # Fingerprint and explicit membership checks catch unversioned edits too.
        w.project.clip_groups[0].clip_ids = ['c', 'sound']
    elif change == 'revision':
        w.editor.update_clip('c', gain_db=1)
    elif change == 'session':
        w.editor = Editor(deepcopy(w.project))
    elif change == 'selection':
        w.select_clip('sound'); w.select_clip('c')
    elif change == 'project':
        w.editor.project = deepcopy(w.project)
    elif change == 'focus':
        QApplication.sendEvent(w.timeline, QFocusEvent(QEvent.Type.FocusOut))
    elif change == 'scale':
        w.timeline.scale += 1
    after_change = snapshot(w)
    release(w.timeline, target)
    assert w.timeline.drag is None and snapshot(w) == after_change


def test_grouping_during_existing_trim_cancels_trim(window):
    w = window; add_clips(w); w.select_clip('c')
    p = press(w.timeline, 'c', edge='right'); target = move(w.timeline, p, -3)
    assert w.timeline.trim_drag is not None
    w.editor.group_clips(('c', 'b'))
    before = snapshot(w)
    release(w.timeline, target)
    assert w.timeline.trim_drag is None and snapshot(w) == before
    w.refresh(); assert set(w.selected_clip_ids) == {'c', 'b'}


@pytest.mark.parametrize('kind', ['text', 'number', 'combo', 'media', 'search', 'effects', 'dialog', 'ime'])
@pytest.mark.parametrize('operation', ['group', 'ungroup'])
def test_group_commands_respect_focus_and_input_method(window, app, kind, operation):
    w = window
    if operation == 'ungroup': create_group(w)
    else: add_clips(w)
    w.show(); app.processEvents()
    extra = None
    if kind in ('text', 'number', 'combo'):
        focus = {'text': QLineEdit, 'number': QSpinBox, 'combo': QComboBox}[kind]()
        w.addToolBar('Focus safety').addWidget(focus); app.processEvents()
    elif kind == 'media': focus = w.bin
    elif kind == 'search': focus = w.media_search
    elif kind == 'effects':
        w.side_tabs.setCurrentWidget(w.clip_effects_panel)
        # Normally disabled for a group. Exercise a delayed/stale focus event
        # against the real effects view rather than accidentally testing the
        # previous, unrelated widget that Qt leaves focused.
        w.effects_controls.setEnabled(True)
        focus = w.effect_list
    elif kind == 'ime': focus = w.timeline
    else:
        extra = QDialog(w); layout = QVBoxLayout(extra); focus = QLineEdit(); layout.addWidget(focus); extra.show()
    focus.setFocus(); app.processEvents()
    if kind == 'ime': QApplication.sendEvent(focus, QInputMethodEvent('拼', []))
    before = snapshot(w)
    getattr(w, f'{operation}_selected_clips')()
    assert snapshot(w) == before
    if kind == 'ime': QApplication.sendEvent(focus, QInputMethodEvent('', []))
    if extra: extra.close()


def test_actual_edit_menu_group_and_ungroup(window, app):
    w = window; add_clips(w); w.show(); w.activateWindow(); w.timeline.setFocus(); app.processEvents()
    edit_menu = next(action.menu() for action in w.menuBar().actions() if action.text() == 'Edit')
    for command, count in [('edit.group', 1), ('edit.ungroup', 0)]:
        w.timeline.setFocus(); app.processEvents()
        QTest.mouseClick(w.menuBar(), Qt.MouseButton.LeftButton,
                         pos=w.menuBar().actionGeometry(edit_menu.menuAction()).center())
        app.processEvents()
        action = w.shortcuts.actions[command]
        assert action.isEnabled()
        QTest.mouseClick(edit_menu, Qt.MouseButton.LeftButton,
                         pos=edit_menu.actionGeometry(action).center())
        app.processEvents()
        assert len(w.project.clip_groups) == count


def test_actual_group_keyboard_shortcuts(window, app):
    w = window; add_clips(w); w.show(); w.activateWindow(); w.timeline.setFocus(); app.processEvents()
    from lumen.shortcuts import sequence_from_binding
    for command, count in [('edit.group', 1), ('edit.ungroup', 0)]:
        combination = sequence_from_binding(w.shortcuts.bindings[command][0])[0]
        QTest.keyClick(w.timeline, combination.key(), combination.keyboardModifiers())
        assert len(w.project.clip_groups) == count


@pytest.mark.parametrize('locale', ['zh-CN', 'en'])
def test_live_group_language_accessibility_and_help(window, locale):
    from lumen.ui_help import help_text
    from lumen.i18n_catalog_clip_groups import ZH_CN
    w = window; create_group(w)
    state = snapshot(w)
    set_locale(locale)
    expected = ZH_CN['Group clips'] if locale == 'zh-CN' else 'Group clips'
    assert w.shortcuts.actions['edit.group'].text().startswith(expected)
    assert ('取消编组' if locale == 'zh-CN' else 'Ungroup') in w.clip_name.text()
    assert ('1 个持久编组' if locale == 'zh-CN' else '1 persistent groups') in w.timeline.accessibleDescription()
    assert ('已选 2 个片段' if locale == 'zh-CN' else '2 clips selected') in w.timeline.accessibleDescription()
    assert (ZH_CN[GROUP_HELP] if locale == 'zh-CN' else GROUP_HELP) in help_text()
    assert snapshot(w) == state
    # A visible grouped-edge notice retranslates without losing selection.
    p = press(w.timeline, 'c', edge='right'); release(w.timeline, p)
    set_locale('en' if locale == 'zh-CN' else 'zh-CN')
    assert ('取消片段编组' if locale == 'en' else 'Ungroup clips') in w.statusBar().currentMessage()
    assert set(w.selected_clip_ids) == {'c', 'b'}


def test_all_new_model_group_errors_have_chinese_boundary_translation():
    import ast
    from pathlib import Path
    import lumen.model
    tree = ast.parse(Path(lumen.model.__file__).read_text())
    messages = [node.args[0].value for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == 'ValueError' and node.args
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                and 'group' in node.args[0].value.lower()]
    assert len(messages) >= 18
    set_locale('zh-CN')
    for message in messages:
        assert str(tr_error(message)) != message and '技术错误' not in str(tr_error(message))


def test_previous_shortcut_settings_gain_unbound_group_commands(window):
    from lumen.shortcuts import ShortcutController
    w = window
    profiles = {profile: {key: list(value) for key, value in default_bindings(profile).items()
                          if key not in ('edit.group', 'edit.ungroup')}
                for profile in ('windows', 'macos')}
    # Older custom G must never be stolen or cause settings reset.
    profiles['windows']['edit.split'] = ['Ctrl+G']
    w.workspace_settings.setValue(SETTINGS_KEY, json.dumps(
        {'version': 1, 'active_profile': 'windows', 'profiles': profiles}))
    controller = ShortcutController(w, w.workspace_settings)
    try:
        assert not controller.load_warning
        assert controller.bindings['edit.split'] == ('Ctrl+G',)
        assert controller.bindings['edit.group'] == () and controller.bindings['edit.ungroup'] == ()
        assert set(controller.bindings) == set(COMMANDS)
    finally:
        controller.detach()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_shortcut_catalog_and_bilingual_search_include_groups(window, app, locale):
    from lumen.i18n_catalog_shortcuts import EN, ZH_CN
    from lumen.ui_shortcuts import ShortcutDialog
    for command_id in ('edit.group', 'edit.ungroup'):
        command = COMMANDS[command_id]
        for catalog in (EN, ZH_CN):
            assert command.label in catalog and command.category in catalog
    set_locale(locale)
    dialog = ShortcutDialog(window.shortcuts, window)
    try:
        dialog.show(); app.processEvents()
        # Search must use both direct catalogs, regardless of display language.
        for query, expected in [('片段编组', {'edit.group', 'edit.ungroup'}),
                                ('取消片段编组', {'edit.ungroup'}),
                                ('Ungroup clips', {'edit.ungroup'}),
                                ('edit.group', {'edit.group'})]:
            dialog.search_edit.setText(query); app.processEvents()
            visible = {command_id for command_id, row in dialog._rows.items()
                       if not dialog.commands_table.isRowHidden(row)}
            assert visible == expected
    finally:
        dialog.close(); dialog.deleteLater(); app.processEvents()


@pytest.mark.parametrize('scale', [1, 4])
def test_group_marking_and_hover_are_visible_even_on_narrow_clips(grouped_timeline, scale, monkeypatch):
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QToolTip
    t = grouped_timeline; t.scale = scale
    clip = t.project.clips[0]; rect = t.rect_for(clip)
    image = t.grab().toImage()
    ratio = image.devicePixelRatio()
    assert image.pixelColor(round(rect.center().x() * ratio), round((rect.bottom() - 3) * ratio)).name() == '#d1b4ff'
    shown = []
    monkeypatch.setattr(QToolTip, 'showText', lambda *args: shown.append(args[1]))
    point = rect.center().toPoint()
    QApplication.sendEvent(t, QHelpEvent(QEvent.Type.ToolTip, point, t.mapToGlobal(point)))
    assert shown == ['Group 1 · 2 clips. Ungroup to edit separately.']
    set_locale('zh-CN')
    assert '编组 1' in t.group_tooltip(clip.id) and '2 个片段' in t.group_tooltip(clip.id)
    t.editor_for_test.ungroup_clips(('a',))
    t.set_project(t.editor_for_test.project, 'a', ('a',))
    assert t.group_tooltip(clip.id) == ''
    assert '0 个持久编组' in t.accessibleDescription()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_group_notice_text_fits_actual_narrow_inspector_viewport(window, app, locale, mode):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QFont
    w = window
    original_font = QFont(app.font())
    try:
        app.setFont(QFont('Noto Sans CJK SC', 15))
        set_locale(locale)
        w.set_workspace_mode(mode, persist=False)
        add_clips(w)
        w.editor.group_clips(('c', 'b', 'sound'))
        w.refresh(); w.show(); app.processEvents()
        w.resize(1000, 768); app.processEvents()
        w.main_split.setSizes([190, 450, 360]); app.processEvents()
        w.select_clip('c'); app.processEvents()
        label = w.clip_name
        lines = label.text().split('\n')
        assert len(lines) == 2
        assert ('Ungroup' if locale == 'en' else '取消编组') in lines[1]
        viewport = w.inspector.viewport()
        origin = label.mapTo(viewport, QPoint(0, 0))
        visible_width = viewport.width() - origin.x()
        assert visible_width > 0
        # The old inspector content can exceed its viewport. Check the actual
        # visible text area, not the wider QLabel layout / sizeHint.
        metrics = label.fontMetrics()
        assert all(metrics.horizontalAdvance(line) <= visible_width for line in lines)
        assert label.height() >= 2 * metrics.lineSpacing()
        assert origin.y() + 2 * metrics.lineSpacing() <= viewport.height()
    finally:
        app.setFont(original_font)

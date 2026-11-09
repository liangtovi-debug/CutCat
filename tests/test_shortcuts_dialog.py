"""Real Qt preferences interactions, with isolated QSettings and no project edits.

Offscreen coverage verifies Qt behavior and logical-pixel geometry, not physical
OS shortcut delivery, native screen scaling, or a particular keyboard layout.
"""
from copy import deepcopy
import json
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QWidget

from lumen.i18n import set_locale, tr
from lumen.i18n_catalog_shortcuts import EN, ZH_CN
from lumen.shortcuts import (
    COMMANDS, SETTINGS_KEY, ShortcutController, default_bindings,
    sequence_from_binding,
)
from lumen.ui_shortcuts import ShortcutDialog


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controller(app, tmp_path):
    host = QWidget()
    settings = QSettings(str(tmp_path / 'keyboard.ini'), QSettings.Format.IniFormat)
    controller = ShortcutController(host, settings=settings, platform='windows')
    yield controller
    host.close()
    host.deleteLater()
    app.processEvents()


@pytest.fixture
def dialog(app, controller):
    dialog = ShortcutDialog(controller, controller.window)
    dialog.show()
    app.processEvents()
    yield dialog
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def click(button):
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    QApplication.processEvents()


def select(dialog, command_id):
    dialog.search_edit.clear()
    row = dialog._rows[command_id]
    item = dialog.commands_table.item(row, 0)
    dialog.commands_table.scrollToItem(item)
    QApplication.processEvents()
    rectangle = dialog.commands_table.visualItemRect(item)
    QTest.mouseClick(dialog.commands_table.viewport(), Qt.MouseButton.LeftButton,
                     pos=rectangle.center())
    QApplication.processEvents()
    assert dialog.selected_command_id == command_id


def record(editor, binding):
    editor.setFocus()
    combination = sequence_from_binding(binding)[0]
    QTest.keyClick(editor, combination.key(), combination.keyboardModifiers())
    QApplication.processEvents()


def search(dialog, text):
    # QTest.keyClicks cannot synthesize arbitrary CJK keys. Setting Unicode text
    # exercises the same textChanged/filter path as an IME commit.
    dialog.search_edit.setText(text)
    QApplication.processEvents()
    return {command_id for command_id, row in dialog._rows.items()
            if not dialog.commands_table.isRowHidden(row)}


def test_dialog_has_complete_catalog_and_real_single_chord_editors(dialog):
    assert dialog.isModal()
    assert dialog.commands_table.rowCount() == len(COMMANDS)
    assert not dialog.commands_table.alternatingRowColors()
    assert dialog.primary_edit.maximumSequenceLength() == 1
    assert dialog.alternate_edit.maximumSequenceLength() == 1
    assert not dialog.apply_button.isEnabled()
    assert set(EN) == set(ZH_CN)
    assert all(command.label in ZH_CN and command.category in ZH_CN for command in COMMANDS.values())
    assert 'C/V' in dialog.scope_label.text() and 'J/K/L' in dialog.scope_label.text()
    assert 'I/O' in dialog.scope_label.text() and 'ripple' in dialog.scope_label.text()
    assert 'IME' in dialog.safety_label.text()
    assert 'native key names' in dialog.recording_label.text()
    assert 'Meta means Cmd' in dialog.recording_label.text()
    assert dialog.commands_table.horizontalHeaderItem(0).text() == 'Command'


def test_search_is_bilingual_and_accepts_ids_categories_keys_in_either_locale(dialog):
    for locale in ('en', 'zh-CN'):
        set_locale(locale)
        assert search(dialog, '分割') == {'edit.split'}
        assert search(dialog, 'split') == {'edit.split'}
        assert search(dialog, 'edit.split') == {'edit.split'}
        assert search(dialog, '滚动关联音频接点') == {'edit.audio_roll'}
        assert search(dialog, 'Roll linked audio cut') == {'edit.audio_roll'}
        assert search(dialog, 'edit.audio_roll') == {'edit.audio_roll'}
        assert search(dialog, 'Ctrl+Shift+S') == {'file.save_as'}
        assert search(dialog, 'File') == {command_id for command_id in COMMANDS if command_id.startswith('file.')}
        assert search(dialog, '文件') == {command_id for command_id in COMMANDS if command_id.startswith('file.')}
        assert search(dialog, '不存在的命令') == set()
        assert not dialog.editor_panel.isEnabled() and not dialog.unbind_button.isEnabled()
        assert dialog.selected_command_id is None
        assert search(dialog, '') == set(COMMANDS)
        assert dialog.editor_panel.isEnabled()


def test_qtest_recording_is_staged_cancel_and_reopen_discard(dialog, controller, app):
    original = deepcopy(controller.profiles)
    select(dialog, 'file.save')
    record(dialog.primary_edit, 'Ctrl+Alt+F12')
    assert dialog.staged_profiles['windows']['file.save'] == ('Ctrl+Alt+F12',)
    assert controller.profiles == original
    assert controller.settings.value(SETTINGS_KEY) is None
    click(dialog.cancel_button)
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.staged_profiles == original
    dialog.show()
    app.processEvents()
    assert not dialog.apply_button.isEnabled()
    assert dialog.primary_edit.keySequence() == sequence_from_binding('Ctrl+S')
    dialog.close()
    reopened = ShortcutDialog(controller, controller.window)
    try:
        assert reopened.staged_profiles == original
        assert not reopened.apply_button.isEnabled()
    finally:
        reopened.deleteLater()
        app.processEvents()


def test_apply_persists_both_profiles_and_cancel_only_later_changes(dialog, controller, app):
    changed = QSignalSpy(controller.changed)
    select(dialog, 'file.save')
    record(dialog.primary_edit, 'Ctrl+Alt+F12')
    record(dialog.alternate_edit, 'Ctrl+Alt+F11')
    dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData('macos'))
    record(dialog.primary_edit, 'Cmd+Alt+F12')
    assert controller.profile == 'windows'
    assert controller.profiles['macos'] == default_bindings('macos')
    click(dialog.apply_button)
    assert dialog.isVisible()
    assert changed.count() == 1 and controller.profile == 'macos'
    assert controller.profiles['windows']['file.save'] == ('Ctrl+Alt+F12', 'Ctrl+Alt+F11')
    assert controller.profiles['macos']['file.save'] == ('Cmd+Alt+F12',)
    persisted = controller.settings.value(SETTINGS_KEY)
    assert json.loads(persisted)['active_profile'] == 'macos'
    assert not dialog.apply_button.isEnabled()
    assert dialog.apply()
    assert changed.count() == 1  # Repeated Apply is idempotent.
    record(dialog.primary_edit, 'Cmd+Alt+F10')
    click(dialog.cancel_button)
    assert controller.bindings['file.save'] == ('Cmd+Alt+F12',)
    assert controller.settings.value(SETTINGS_KEY) == persisted
    host = QWidget()
    restored = ShortcutController(host, QSettings(controller.settings.fileName(), QSettings.Format.IniFormat), 'windows')
    assert restored.profile == 'macos' and restored.profiles == controller.profiles
    assert not restored.load_warning
    host.deleteLater()
    app.processEvents()


def test_primary_alternate_clear_and_unbind_are_staged(dialog, controller):
    select(dialog, 'edit.delete')
    assert not dialog.primary_edit.keySequence().isEmpty()
    assert not dialog.alternate_edit.keySequence().isEmpty()
    click(dialog.clear_primary_button)
    assert dialog.primary_edit.keySequence().isEmpty()
    assert dialog.staged_profiles['windows']['edit.delete'] == ('Delete',)
    assert controller.bindings['edit.delete'] == ('Backspace', 'Delete')
    click(dialog.clear_alternate_button)
    assert dialog.staged_profiles['windows']['edit.delete'] == ()
    record(dialog.primary_edit, 'F11')
    record(dialog.alternate_edit, 'F12')
    click(dialog.unbind_button)
    assert dialog.primary_edit.keySequence().isEmpty() and dialog.alternate_edit.keySequence().isEmpty()
    click(dialog.apply_button)
    assert controller.bindings['edit.delete'] == ()


def test_conflict_is_visible_blocks_apply_then_clear_old_assignment(dialog, controller):
    select(dialog, 'file.save')
    record(dialog.primary_edit, 'Ctrl+O')
    assert 'already assigned' in dialog.error_label.text()
    assert 'Open project' in dialog.error_label.text()
    assert not dialog.apply_button.isEnabled()
    assert not dialog.apply()
    assert controller.settings.value(SETTINGS_KEY) is None
    select(dialog, 'file.open')
    click(dialog.unbind_button)
    assert not dialog.error_label.isVisible()
    assert dialog.apply_button.isEnabled()
    click(dialog.apply_button)
    assert controller.bindings['file.save'] == ('Ctrl+O',)
    assert controller.bindings['file.open'] == ()


def test_duplicate_alternate_is_rejected_without_mutating_controller(dialog, controller):
    select(dialog, 'file.save')
    record(dialog.alternate_edit, 'Ctrl+S')
    assert 'different combinations' in dialog.error_label.text()
    assert not dialog.apply_button.isEnabled()
    assert controller.bindings['file.save'] == ('Ctrl+S',)
    click(dialog.clear_alternate_button)
    assert not dialog.error_label.isVisible()
    assert not dialog.apply_button.isEnabled()


def test_reserved_invalid_input_survives_selection_and_language_changes(dialog, controller):
    select(dialog, 'file.save')
    # Direct assignment uses the same real QKeySequenceEdit change signal,
    # avoiding invoking Alt+F4 on an actual host desktop during the test.
    invalid = QKeySequence('Alt+F4', QKeySequence.SequenceFormat.PortableText)
    dialog.primary_edit.setKeySequence(invalid)
    assert 'reserved' in dialog.error_label.text()
    assert not dialog.apply_button.isEnabled()
    assert controller.bindings['file.save'] == ('Ctrl+S',)
    select(dialog, 'edit.split')
    select(dialog, 'file.save')
    assert dialog.primary_edit.keySequence() == invalid
    set_locale('zh-CN')
    assert '操作系统保留' in dialog.error_label.text()
    assert dialog.primary_edit.keySequence() == invalid
    set_locale('en')
    assert 'reserved' in dialog.error_label.text()
    click(dialog.clear_primary_button)
    assert not dialog.error_label.isVisible()
    assert dialog.apply_button.isEnabled()


def test_live_language_switch_retains_staged_slots_profile_search_selection(dialog, controller):
    select(dialog, 'edit.split')
    record(dialog.primary_edit, 'Ctrl+Alt+F12')
    record(dialog.alternate_edit, 'Ctrl+Alt+F11')
    dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData('macos'))
    record(dialog.primary_edit, 'Cmd+Alt+F12')
    assert search(dialog, '分割') == {'edit.split'}
    staged = deepcopy(dialog.staged_profiles)
    primary = QKeySequence(dialog.primary_edit.keySequence())
    for locale, title in [('zh-CN', '键盘快捷键'), ('en', 'Keyboard shortcuts'), ('zh-CN', '键盘快捷键')]:
        set_locale(locale)
        QApplication.processEvents()
        assert dialog.windowTitle() == title
        assert dialog.alternate_edit.findChild(QLineEdit).placeholderText() == (
            '按下快捷键' if locale == 'zh-CN' else 'Press shortcut')
        assert dialog.staged_profiles == staged
        assert dialog.active_profile == 'macos'
        assert dialog.selected_command_id == 'edit.split'
        assert dialog.primary_edit.keySequence() == primary
        assert dialog.search_edit.text() == '分割'
        assert dialog.apply_button.isEnabled()
    assert controller.profiles['windows'] == default_bindings('windows')
    assert controller.profiles['macos'] == default_bindings('macos')
    assert controller.settings.value(SETTINGS_KEY) is None


def test_reset_current_profile_preserves_other_staged_profile_until_apply(dialog, controller):
    select(dialog, 'file.save')
    record(dialog.primary_edit, 'Ctrl+Alt+F12')
    dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData('macos'))
    record(dialog.primary_edit, 'Cmd+Alt+F12')
    click(dialog.reset_button)
    assert dialog.staged_profiles['macos'] == default_bindings('macos')
    assert dialog.staged_profiles['windows']['file.save'] == ('Ctrl+Alt+F12',)
    assert controller.settings.value(SETTINGS_KEY) is None
    click(dialog.apply_button)
    assert controller.profile == 'macos'
    assert controller.profiles['macos'] == default_bindings('macos')
    assert controller.profiles['windows']['file.save'] == ('Ctrl+Alt+F12',)
    dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData('windows'))
    click(dialog.reset_button)
    click(dialog.cancel_button)
    assert controller.profiles['windows']['file.save'] == ('Ctrl+Alt+F12',)


def test_error_in_inactive_profile_blocks_apply_and_reset_can_fix_it(dialog, controller):
    select(dialog, 'file.save')
    record(dialog.primary_edit, 'Ctrl+O')
    dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData('macos'))
    assert 'Windows' in dialog.error_label.text()
    assert not dialog.apply_button.isEnabled()
    dialog.profile_combo.setCurrentIndex(dialog.profile_combo.findData('windows'))
    click(dialog.reset_button)
    assert not dialog.error_label.isVisible()
    assert dialog.staged_profiles == controller.profiles


def test_failed_persistence_keeps_staged_changes_for_retry_and_translates(dialog, controller, monkeypatch):
    original = deepcopy(controller.profiles)
    real_apply = controller.apply_configuration
    calls = []
    def fail(profiles, active_profile):
        calls.append((deepcopy(profiles), active_profile))
        raise OSError(tr('Could not save shortcut preferences. Your active shortcuts are unchanged.'))
    monkeypatch.setattr(controller, 'apply_configuration', fail)
    select(dialog, 'file.save')
    record(dialog.primary_edit, 'Ctrl+Alt+F12')
    click(dialog.apply_button)
    assert len(calls) == 1 and controller.profiles == original
    assert 'Could not save' in dialog.error_label.text()
    assert dialog.staged_profiles['windows']['file.save'] == ('Ctrl+Alt+F12',)
    assert dialog.apply_button.isEnabled()
    set_locale('zh-CN')
    assert '无法保存' in dialog.error_label.text() and 'Could not save' not in dialog.error_label.text()
    monkeypatch.setattr(controller, 'apply_configuration', real_apply)
    click(dialog.apply_button)
    assert controller.bindings['file.save'] == ('Ctrl+Alt+F12',)
    assert not dialog.error_label.isVisible()


def test_recording_multiple_keys_keeps_one_chord_and_enter_does_not_apply(dialog, controller):
    select(dialog, 'file.save')
    record(dialog.primary_edit, 'Ctrl+Alt+F12')
    record(dialog.primary_edit, 'Ctrl+Alt+F11')
    assert dialog.primary_edit.keySequence().count() == 1
    assert dialog.staged_profiles['windows']['file.save'] == ('Ctrl+Alt+F11',)
    QTest.keyClick(dialog.primary_edit, Qt.Key.Key_Return)
    QApplication.processEvents()
    assert dialog.isVisible()
    assert controller.settings.value(SETTINGS_KEY) is None
    assert controller.bindings['file.save'] == ('Ctrl+S',)


def test_escape_and_window_close_never_save(dialog, controller):
    select(dialog, 'file.save')
    record(dialog.primary_edit, 'Ctrl+Alt+F12')
    dialog.search_edit.setFocus()
    QTest.keyClick(dialog.search_edit, Qt.Key.Key_Escape)
    QApplication.processEvents()
    assert not dialog.isVisible()
    assert controller.settings.value(SETTINGS_KEY) is None
    assert controller.bindings['file.save'] == ('Ctrl+S',)


@pytest.mark.parametrize('width,height', [(1000, 700), (667, 467), (500, 350)])
@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_dialog_scrolls_at_small_scaled_viewports(dialog, width, height, locale, app):
    set_locale(locale)
    dialog.resize(width, height)
    app.processEvents()
    # At 1.5×/2× a 1000×700 physical viewport can offer these logical sizes.
    # Keep Apply/Cancel outside the scrollable content and reachable throughout.
    assert dialog.width() <= width and dialog.height() <= height
    for button in (dialog.apply_button, dialog.cancel_button):
        point = button.mapTo(dialog, button.rect().center())
        assert dialog.rect().contains(point)
    scroll = dialog.scroll_area.verticalScrollBar()
    scroll.setValue(scroll.maximum())
    app.processEvents()
    assert dialog.reset_button.isVisible()
    point = dialog.reset_button.mapTo(dialog.scroll_area.viewport(), dialog.reset_button.rect().center())
    assert dialog.scroll_area.viewport().rect().contains(point)
    assert dialog.primary_edit.width() >= 110


def test_preference_edit_and_apply_leave_real_project_undo_selection_unchanged(app, tmp_path):
    from lumen.ui import MainWindow
    from test_ui import populate
    settings = QSettings(str(tmp_path / 'window.ini'), QSettings.Format.IniFormat)
    window = MainWindow(workspace_settings=settings, shortcut_platform='windows', ui_language='en')
    dialog = None
    try:
        clip = populate(window, tmp_path)
        window.select_clip(clip.id)
        window.seek_frame(10)
        before = (deepcopy(window.project.to_dict()), window.revision, window.dirty,
                  window.active_selection_type, window.selected_clip().id,
                  window.timeline.playhead, window.editor.can_undo, window.editor.can_redo)
        dialog = ShortcutDialog(window.shortcuts, window)
        dialog.show()
        app.processEvents()
        select(dialog, 'file.save')
        record(dialog.primary_edit, 'Ctrl+Alt+F12')
        click(dialog.apply_button)
        set_locale('zh-CN')
        click(dialog.cancel_button)
        after = (window.project.to_dict(), window.revision, window.dirty,
                 window.active_selection_type, window.selected_clip().id,
                 window.timeline.playhead, window.editor.can_undo, window.editor.can_redo)
        assert after == before
    finally:
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        window.closing = True
        window.dirty = False
        window.close()
        window.deleteLater()
        app.processEvents()

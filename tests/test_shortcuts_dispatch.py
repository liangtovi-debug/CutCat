"""Actual Qt event-dispatch regression checks for configurable editor shortcuts.

Offscreen tests exercise application event filters, focus, native text editing,
modal/popup ownership and platform modifier translation. They do not claim
physical Windows/macOS keyboard or IME validation.
"""
from __future__ import annotations

import copy
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtGui import QAction, QColor, QImage, QInputMethodEvent, QKeyEvent, QKeySequence
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDoubleSpinBox, QLineEdit, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QSlider, QSpinBox, QTextEdit, QWidget,
)

from lumen.model import Media, Overlay, OverlayKeyframe
from lumen.ui import MainWindow


CTRL = (Qt.KeyboardModifier.MetaModifier if sys.platform == 'darwin'
        else Qt.KeyboardModifier.ControlModifier)
CMD = (Qt.KeyboardModifier.ControlModifier if sys.platform == 'darwin'
       else Qt.KeyboardModifier.MetaModifier)
SHIFT = Qt.KeyboardModifier.ShiftModifier
ALT = Qt.KeyboardModifier.AltModifier
NONE = Qt.KeyboardModifier.NoModifier


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def activate(app, window, widget=None):
    window.show()
    window.activateWindow()
    QApplication.setActiveWindow(window)
    if widget is not None:
        widget.setFocus(Qt.FocusReason.OtherFocusReason)
    app.processEvents()
    assert QApplication.activeWindow() is window
    if widget is not None:
        assert QApplication.focusWidget() is widget


@pytest.fixture
def make_window(app, monkeypatch, tmp_path):
    windows = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: pytest.fail(str(args[2])))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)

    def make(profile='windows', callbacks=None):
        for name, callback in (callbacks or {}).items():
            monkeypatch.setattr(MainWindow, name, callback)
        settings = QSettings(str(tmp_path / f'shortcuts-{len(windows)}.ini'),
                             QSettings.Format.IniFormat)
        settings.setValue('mode', 'professional')
        window = MainWindow(workspace_settings=settings,
                            shortcut_settings=settings,
                            shortcut_platform=profile)
        windows.append(window)
        activate(app, window, window.timeline)
        return window

    yield make
    for window in reversed(windows):
        window.cancel_jobs()
        for job in list(window.jobs):
            assert job.wait(5000), 'Background work failed to stop'
        window.dirty = False
        window.closing = True
        # Await the separately owned, read-only source-status worker before
        # explicit QObject deletion; close() alone may defer asynchronously.
        window.close_media_status()
        status_job = window._media_status_job
        if status_job is not None:
            assert status_job.wait(5000), 'Media status work failed to stop'
        app.processEvents()
        window.close()
        window.deleteLater()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def populate(window, tmp_path):
    source = tmp_path / 'shortcut-source.mp4'
    source.write_bytes(b'mocked preview fixture; no media decoding requested')
    window.editor.add_media(Media('shortcut-media', str(source), 'Shortcut clip',
                                  10, 640, 360, True, True))
    window.refresh()
    window.bin.setCurrentRow(0)
    window.add_selected_media()
    return window.selected_clip()


def press(app, widget, key, modifiers=NONE):
    QTest.keyClick(widget, key, modifiers)
    app.processEvents()


def repeat(app, widget, key, modifiers=NONE):
    # QTest does not expose autorepeat, so deliver a real repeat QKeyEvent
    # through QApplication rather than calling the controller directly.
    event = QKeyEvent(QEvent.Type.KeyPress, key, modifiers, '', True, 1)
    QApplication.sendEvent(widget, event)
    app.processEvents()


def snapshot(window):
    return copy.deepcopy((
        window.project.to_dict(), window.revision, window.dirty,
        [p.to_dict() for p in window.editor._undo],
        [p.to_dict() for p in window.editor._redo],
        window.selection, window.active_selection_type, window.timeline.playhead,
        window.preview_revision, window.preview_paths, window.preview_request,
        window.preview_pending,
    )), set(window.jobs)


def test_split_undo_redo_and_delete_dispatch_once(make_window, app, tmp_path):
    window = make_window()
    populate(window, tmp_path)
    window.seek_frame(90)
    window.timeline.setFocus()
    revision = window.revision
    press(app, window.timeline, Qt.Key.Key_K, CTRL)
    assert [c.duration for c in window.project.clips] == [90, 210]
    assert window.revision == revision + 1
    press(app, window.timeline, Qt.Key.Key_Z, CTRL)
    assert len(window.project.clips) == 1
    assert window.revision == revision + 2
    press(app, window.timeline, Qt.Key.Key_Z, CTRL | SHIFT)
    assert len(window.project.clips) == 2
    assert window.revision == revision + 3
    window.select_clip(window.project.clips[-1].id)
    press(app, window.timeline, Qt.Key.Key_Delete)
    assert len(window.project.clips) == 1
    assert window.revision == revision + 4
    window.select_clip(window.project.clips[0].id)
    press(app, window.timeline, Qt.Key.Key_Backspace)
    assert window.project.clips == []
    assert window.revision == revision + 5


@pytest.mark.parametrize('key,modifiers,delta', [
    (Qt.Key.Key_Left, NONE, -1), (Qt.Key.Key_Right, NONE, 1),
    (Qt.Key.Key_Left, SHIFT, -5), (Qt.Key.Key_Right, SHIFT, 5),
])
def test_frame_step_and_repeat_are_frame_exact(make_window, app, tmp_path,
                                               key, modifiers, delta):
    window = make_window()
    populate(window, tmp_path)
    window.seek_frame(100)
    window.timeline.setFocus()
    revision = window.revision
    press(app, window.timeline, key, modifiers)
    assert window.timeline.playhead == 100 + delta
    repeat(app, window.timeline, key, modifiers)
    assert window.timeline.playhead == 100 + 2 * delta
    assert window.revision == revision


def test_frame_step_clamps_to_project_boundaries(make_window, app, tmp_path):
    window = make_window()
    populate(window, tmp_path)
    window.timeline.setFocus()
    window.seek_frame(0)
    press(app, window.timeline, Qt.Key.Key_Left, SHIFT)
    assert window.timeline.playhead == 0
    window.seek_frame(299)
    press(app, window.timeline, Qt.Key.Key_Right, SHIFT)
    assert window.timeline.playhead == 299


def test_space_toggles_playback_once_and_repeat_is_suppressed(make_window, app,
                                                            monkeypatch, tmp_path):
    window = make_window()
    populate(window, tmp_path)
    window.timeline.setFocus()
    state = {'playing': False, 'play': 0, 'pause': 0}

    def play():
        state['playing'] = True
        state['play'] += 1

    def pause():
        state['playing'] = False
        state['pause'] += 1

    monkeypatch.setattr(window.player, 'play', play)
    monkeypatch.setattr(window.player, 'pause', pause)
    monkeypatch.setattr(window.player, 'playbackState', lambda:
                        QMediaPlayer.PlaybackState.PlayingState if state['playing']
                        else QMediaPlayer.PlaybackState.PausedState)
    window.preview_revision = window.revision
    window.loaded_preview_mode = window.preview_mode
    window.update_playback_controls()
    press(app, window.timeline, Qt.Key.Key_Space)
    assert state == {'playing': True, 'play': 1, 'pause': 0}
    repeat(app, window.timeline, Qt.Key.Key_Space)
    assert state == {'playing': True, 'play': 1, 'pause': 0}
    press(app, window.timeline, Qt.Key.Key_Space)
    assert state == {'playing': False, 'play': 1, 'pause': 1}


@pytest.mark.parametrize('key,modifiers', [
    (Qt.Key.Key_K, CTRL | SHIFT), (Qt.Key.Key_K, CTRL | ALT | SHIFT),
    (Qt.Key.Key_K, CTRL | CMD), (Qt.Key.Key_Delete, SHIFT),
    (Qt.Key.Key_Backspace, CTRL), (Qt.Key.Key_Right, CTRL),
    (Qt.Key.Key_Left, ALT), (Qt.Key.Key_Space, SHIFT),
])
def test_extra_modifiers_do_not_fall_through_to_globals(make_window, app, tmp_path,
                                                       key, modifiers):
    calls = []
    window = make_window(callbacks={'toggle_play': lambda self: calls.append('play')})
    populate(window, tmp_path)
    window.seek_frame(100)
    window.timeline.setFocus()
    before = snapshot(window)
    press(app, window.timeline, key, modifiers)
    assert snapshot(window) == before
    assert calls == []


def test_repeat_cannot_split_delete_undo_redo_or_toggle_snapping(make_window, app,
                                                               tmp_path):
    window = make_window()
    populate(window, tmp_path)
    window.seek_frame(100)
    window.timeline.setFocus()
    before = snapshot(window)
    snapping = window.timeline.snapping_enabled
    for key, modifiers in [(Qt.Key.Key_K, CTRL), (Qt.Key.Key_Delete, NONE),
                           (Qt.Key.Key_Backspace, NONE), (Qt.Key.Key_Z, CTRL),
                           (Qt.Key.Key_Z, CTRL | SHIFT), (Qt.Key.Key_S, NONE)]:
        repeat(app, window.timeline, key, modifiers)
    assert snapshot(window) == before
    assert window.timeline.snapping_enabled is snapping


FILE_KEYS = [
    (Qt.Key.Key_N, CTRL), (Qt.Key.Key_O, CTRL), (Qt.Key.Key_S, CTRL),
    (Qt.Key.Key_S, CTRL | SHIFT), (Qt.Key.Key_I, CTRL), (Qt.Key.Key_M, CTRL),
]


def file_spies(calls):
    return {name: (lambda self, *args, command=name:
                   calls.append((command, args)))
            for name in ('new_project', 'open_project', 'save_project',
                         'import_dialog', 'export_dialog')}


def test_all_file_commands_dispatch_and_old_aliases_are_inert(make_window, app, tmp_path):
    calls = []
    window = make_window(callbacks=file_spies(calls))
    populate(window, tmp_path)
    window.timeline.setFocus()
    for key, modifiers in FILE_KEYS:
        press(app, window.timeline, key, modifiers)
    assert calls == [('new_project', ()), ('open_project', ()),
                     ('save_project', ()), ('save_project', (True,)),
                     ('import_dialog', ()), ('export_dialog', ())]
    calls.clear()
    window.seek_frame(100)
    before = snapshot(window)
    for key, modifiers in [(Qt.Key.Key_E, CTRL), (Qt.Key.Key_B, CTRL),
                           (Qt.Key.Key_C, NONE), (Qt.Key.Key_V, NONE),
                           (Qt.Key.Key_J, NONE), (Qt.Key.Key_K, NONE),
                           (Qt.Key.Key_L, NONE), (Qt.Key.Key_I, NONE),
                           (Qt.Key.Key_O, NONE)]:
        press(app, window.timeline, key, modifiers)
    assert calls == []
    assert snapshot(window) == before


@pytest.mark.parametrize('kind', ['line', 'plain', 'rich', 'integer', 'decimal',
                                 'combo', 'editable_combo', 'caption'])
def test_editable_focus_guards_every_global_including_file_commands(
        make_window, app, tmp_path, monkeypatch, kind):
    calls = []
    from lumen.shortcuts import ShortcutController
    monkeypatch.setattr(ShortcutController, 'open_dialog',
                        lambda self: calls.append(('shortcuts', ())))
    callbacks = file_spies(calls)
    callbacks['toggle_play'] = lambda self: calls.append(('play', ()))
    window = make_window(callbacks=callbacks)
    populate(window, tmp_path)
    window.seek_frame(100)
    if kind == 'caption':
        window.side_tabs.setCurrentIndex(1)
        control = window.caption_text
    else:
        cls = {'line': QLineEdit, 'plain': QPlainTextEdit, 'rich': QTextEdit,
               'integer': QSpinBox, 'decimal': QDoubleSpinBox,
               'combo': QComboBox, 'editable_combo': QComboBox}[kind]
        control = cls(window.centralWidget())
        control.setGeometry(10, 10, 220, 60)
        if isinstance(control, QComboBox):
            control.addItems(['First', 'Second'])
            control.setEditable(kind == 'editable_combo')
        control.show()
    control.setFocus()
    app.processEvents()
    assert control.hasFocus() or control.isAncestorOf(QApplication.focusWidget())
    before = snapshot(window)
    snapping = window.timeline.snapping_enabled
    for key, modifiers in FILE_KEYS + [
        (Qt.Key.Key_K, CTRL), (Qt.Key.Key_Z, CTRL), (Qt.Key.Key_Z, CTRL | SHIFT),
        (Qt.Key.Key_Delete, NONE), (Qt.Key.Key_Backspace, NONE),
        (Qt.Key.Key_Space, NONE), (Qt.Key.Key_Left, NONE),
        (Qt.Key.Key_Right, SHIFT), (Qt.Key.Key_S, NONE),
        (Qt.Key.Key_K, CTRL | ALT),
    ]:
        press(app, QApplication.focusWidget(), key, modifiers)
    assert snapshot(window) == before
    assert window.timeline.snapping_enabled is snapping
    assert calls == []
    assert QApplication.activeModalWidget() is None


@pytest.mark.parametrize('kind', ['line', 'caption'])
def test_native_text_delete_backspace_and_undo_remain_available(make_window, app,
                                                               tmp_path, kind):
    window = make_window()
    populate(window, tmp_path)
    if kind == 'caption':
        window.side_tabs.setCurrentIndex(1)
        editor = window.caption_text
        get_text = editor.toPlainText
    else:
        editor = QLineEdit(window.centralWidget())
        editor.setGeometry(10, 10, 200, 40)
        editor.show()
        get_text = editor.text
    editor.setFocus()
    app.processEvents()
    before = snapshot(window)
    QTest.keyClicks(editor, 'abc')
    press(app, editor, Qt.Key.Key_Backspace)
    assert get_text() == 'ab'
    QTest.keySequence(editor, QKeySequence(QKeySequence.StandardKey.Undo))
    app.processEvents()
    assert get_text() == 'abc'
    editor.selectAll()
    press(app, editor, Qt.Key.Key_Delete)
    assert get_text() == ''
    assert snapshot(window) == before


def test_ime_preedit_suppresses_global_dispatch_until_finished(make_window, app,
                                                             tmp_path):
    window = make_window()
    populate(window, tmp_path)
    window.seek_frame(100)
    target = window.timeline
    target.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
    target.setFocus()
    app.processEvents()
    before = snapshot(window)
    QApplication.sendEvent(target, QInputMethodEvent('拼音', []))
    press(app, target, Qt.Key.Key_K, CTRL)
    assert snapshot(window) == before
    QApplication.sendEvent(target, QInputMethodEvent('', []))
    press(app, target, Qt.Key.Key_K, CTRL)
    assert len(window.project.clips) == 2


@pytest.mark.parametrize('modal', [False, True])
def test_owned_dialog_focus_blocks_editor_and_file_commands(make_window, app,
                                                           tmp_path, modal):
    calls = []
    window = make_window(callbacks=file_spies(calls))
    populate(window, tmp_path)
    window.seek_frame(100)
    before = snapshot(window)
    dialog = QDialog(window)
    dialog.setModal(modal)
    dialog.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    try:
        activate(app, dialog, dialog)
        for key, modifiers in FILE_KEYS + [(Qt.Key.Key_K, CTRL),
                                           (Qt.Key.Key_Delete, NONE)]:
            press(app, dialog, key, modifiers)
        assert snapshot(window) == before
        assert calls == []
    finally:
        dialog.close()
        dialog.deleteLater()
        activate(app, window, window.timeline)
    press(app, window.timeline, Qt.Key.Key_K, CTRL)
    assert len(window.project.clips) == 2


def test_active_popup_blocks_editor_shortcuts_even_if_main_window_is_active(
        make_window, app, tmp_path):
    calls = []
    window = make_window(callbacks=file_spies(calls))
    populate(window, tmp_path)
    window.seek_frame(100)
    window.timeline.setFocus()
    before = snapshot(window)
    popup = QMenu(window)
    popup.addAction('Popup item')
    try:
        popup.popup(window.timeline.mapToGlobal(window.timeline.rect().topLeft()))
        app.processEvents()
        assert QApplication.activePopupWidget() is popup
        for key, modifiers in FILE_KEYS + [(Qt.Key.Key_K, CTRL)]:
            press(app, popup, key, modifiers)
        assert snapshot(window) == before
        assert calls == []
    finally:
        popup.close()
        popup.deleteLater()
        activate(app, window, window.timeline)


def test_inactive_window_ignores_directed_key_events(make_window, app, tmp_path):
    calls = []
    window = make_window(callbacks=file_spies(calls))
    populate(window, tmp_path)
    window.seek_frame(100)
    before = snapshot(window)
    other = QWidget()
    other.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    try:
        activate(app, other, other)
        assert not window.isActiveWindow()
        for key, modifiers in FILE_KEYS + [(Qt.Key.Key_K, CTRL),
                                           (Qt.Key.Key_Delete, NONE)]:
            press(app, window.timeline, key, modifiers)
        assert snapshot(window) == before
        assert calls == []
    finally:
        other.close()
        other.deleteLater()
        activate(app, window, window.timeline)


def test_mac_profile_uses_command_not_physical_control(make_window, app, tmp_path):
    calls = []
    window = make_window('macos', callbacks=file_spies(calls))
    populate(window, tmp_path)
    window.seek_frame(100)
    window.timeline.setFocus()
    assert window.shortcuts.bindings['edit.split'] == ('Cmd+K',)
    before = snapshot(window)
    press(app, window.timeline, Qt.Key.Key_K, CTRL)
    press(app, window.timeline, Qt.Key.Key_S, CTRL)
    assert snapshot(window) == before
    assert calls == []
    press(app, window.timeline, Qt.Key.Key_K, CMD)
    assert len(window.project.clips) == 2
    press(app, window.timeline, Qt.Key.Key_Z, CMD)
    assert len(window.project.clips) == 1
    press(app, window.timeline, Qt.Key.Key_Z, CMD | SHIFT)
    assert len(window.project.clips) == 2
    press(app, window.timeline, Qt.Key.Key_S, CMD)
    assert calls == [('save_project', ())]


def test_rebinding_changes_actual_dispatch_without_project_edits(make_window, app,
                                                              tmp_path):
    window = make_window()
    populate(window, tmp_path)
    window.seek_frame(100)
    window.timeline.setFocus()
    before = snapshot(window)
    window.shortcuts.set_bindings('edit.split', ['Ctrl+R'])
    assert snapshot(window) == before
    press(app, window.timeline, Qt.Key.Key_K, CTRL)
    assert snapshot(window) == before
    press(app, window.timeline, Qt.Key.Key_R, CTRL)
    assert len(window.project.clips) == 2


def test_clicking_timeline_restores_shortcuts_after_text_focus(make_window, app,
                                                             tmp_path):
    window = make_window()
    clip = populate(window, tmp_path)
    window.seek_frame(100)
    editor = QLineEdit(window.centralWidget())
    editor.setGeometry(10, 10, 200, 40)
    editor.show()
    editor.setFocus()
    app.processEvents()
    assert editor.hasFocus()
    assert window.timeline.focusPolicy() == Qt.FocusPolicy.StrongFocus
    point = window.timeline.rect_for(clip).topLeft().toPoint()
    point.setX(point.x() + 15)
    point.setY(point.y() + 15)
    QTest.mouseClick(window.timeline, Qt.MouseButton.LeftButton, NONE, point)
    app.processEvents()
    assert window.timeline.hasFocus()
    press(app, window.timeline, Qt.Key.Key_K, CTRL)
    assert len(window.project.clips) == 2


def test_menu_actions_do_not_register_second_keyboard_dispatch_path(make_window):
    window = make_window()
    assert all(not action.shortcuts() for action in window.findChildren(QAction))


def test_explicitly_disabled_action_never_dispatches(make_window, app, tmp_path):
    window = make_window()
    populate(window, tmp_path)
    window.seek_frame(100)
    window.timeline.setFocus()
    before = snapshot(window)
    action = window.shortcuts.actions['edit.split']
    assert action.isEnabled()
    action.setEnabled(False)
    press(app, window.timeline, Qt.Key.Key_K, CTRL)
    assert snapshot(window) == before
    assert not action.isEnabled()
    action.setEnabled(True)
    press(app, window.timeline, Qt.Key.Key_K, CTRL)
    assert len(window.project.clips) == 2


def test_media_bin_focus_cannot_delete_stale_timeline_selection(make_window, app,
                                                              tmp_path):
    window = make_window()
    clip = populate(window, tmp_path)
    window.bin.setFocus()
    app.processEvents()
    assert window.bin.hasFocus()
    assert window.selection == clip.id
    before = snapshot(window)
    press(app, window.bin, Qt.Key.Key_Delete)
    press(app, window.bin, Qt.Key.Key_Backspace)
    assert snapshot(window) == before


def test_caption_list_deletes_only_its_own_active_selection(make_window, app,
                                                           tmp_path):
    window = make_window()
    clip = populate(window, tmp_path)
    caption = window.editor.add_caption(0, 60, 'Keep or delete deliberately')
    window.changed()
    window.select_clip(clip.id)
    window.side_tabs.setCurrentIndex(1)
    # A stale displayed row must not authorize deleting either selected clip
    # or caption when the active selection still belongs to the timeline.
    window.caption_list.blockSignals(True)
    window.caption_list.setCurrentRow(0)
    window.caption_list.blockSignals(False)
    window.caption_list.setFocus()
    app.processEvents()
    before = snapshot(window)
    press(app, window.caption_list, Qt.Key.Key_Delete)
    assert snapshot(window) == before
    window.caption_list.setCurrentRow(-1)
    window.caption_list.setCurrentRow(0)
    assert window.active_selection_type == 'caption'
    assert window.selected_caption().id == caption.id
    press(app, window.caption_list, Qt.Key.Key_Delete)
    assert window.project.captions == []
    assert [item.id for item in window.project.clips] == [clip.id]


def test_button_space_activates_native_button_without_toggling_playback(
        make_window, app, tmp_path):
    calls = []
    window = make_window(callbacks={'toggle_play': lambda self: calls.append('play')})
    populate(window, tmp_path)
    window.preview_revision = window.revision
    window.loaded_preview_mode = window.preview_mode
    window.update_playback_controls()
    button = QPushButton('Local action', window.centralWidget())
    button.setGeometry(10, 10, 200, 40)
    button.clicked.connect(lambda: calls.append('button'))
    button.show()
    button.setFocus()
    app.processEvents()
    press(app, button, Qt.Key.Key_Space)
    assert calls == ['button']


def test_slider_keys_stay_local_and_file_commands_are_guarded(make_window, app,
                                                            tmp_path):
    calls = []
    window = make_window(callbacks=file_spies(calls))
    populate(window, tmp_path)
    window.seek_frame(100)
    slider = QSlider(Qt.Orientation.Horizontal, window.centralWidget())
    slider.setRange(0, 50)
    slider.setValue(10)
    slider.setGeometry(10, 10, 200, 40)
    slider.show()
    slider.setFocus()
    app.processEvents()
    before = snapshot(window)
    press(app, slider, Qt.Key.Key_Right)
    assert slider.value() == 11
    for key, modifiers in FILE_KEYS:
        press(app, slider, key, modifiers)
    assert snapshot(window) == before
    assert calls == []


def test_snapping_shortcut_controls_real_drag_algorithm_without_project_edit(
        make_window, app, tmp_path):
    window = make_window()
    clip = populate(window, tmp_path)
    window.seek_frame(100)
    window.timeline.setFocus()
    assert window.timeline.snapping_enabled is True
    assert window.snapping_button.isChecked()

    def drag_by_frames(frames):
        current = window.project.clips[0]
        point = window.timeline.rect_for(current).topLeft().toPoint()
        point.setX(point.x() + 20)
        point.setY(point.y() + 20)
        end = point.__class__(point.x() + round(frames * window.timeline.scale), point.y())
        QTest.mousePress(window.timeline, Qt.MouseButton.LeftButton, NONE, point)
        QTest.mouseRelease(window.timeline, Qt.MouseButton.LeftButton, NONE, end)
        app.processEvents()

    drag_by_frames(99)
    assert window.project.clips[0].start == 100
    press(app, window.timeline, Qt.Key.Key_Z, CTRL)
    assert window.project.clips[0].start == 0
    before = snapshot(window)
    press(app, window.timeline, Qt.Key.Key_S)
    assert window.timeline.snapping_enabled is False
    assert not window.snapping_button.isChecked()
    assert snapshot(window) == before
    drag_by_frames(99)
    assert window.project.clips[0].start == 99
    assert window.project.clips[0].id == clip.id


@pytest.mark.parametrize('kind', ['button', 'custom_widget'])
def test_unrelated_inspector_focus_cannot_split_or_delete_stale_clip(
        make_window, app, tmp_path, kind):
    window = make_window()
    populate(window, tmp_path)
    window.seek_frame(100)
    control = (QPushButton('Inspector action', window.inspector)
               if kind == 'button' else QWidget(window.inspector))
    control.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    control.setGeometry(10, 10, 200, 40)
    control.show()
    control.setFocus()
    app.processEvents()
    assert control.hasFocus()
    before = snapshot(window)
    for key, modifiers in [(Qt.Key.Key_K, CTRL), (Qt.Key.Key_Delete, NONE),
                           (Qt.Key.Key_Backspace, NONE)]:
        press(app, control, key, modifiers)
    assert snapshot(window) == before


def test_file_commands_do_not_auto_repeat(make_window, app, tmp_path):
    calls = []
    window = make_window(callbacks=file_spies(calls))
    populate(window, tmp_path)
    window.timeline.setFocus()
    for key, modifiers in FILE_KEYS:
        repeat(app, window.timeline, key, modifiers)
    assert calls == []


def test_ime_focus_change_does_not_leave_global_commands_stuck(make_window, app,
                                                            tmp_path):
    window = make_window()
    populate(window, tmp_path)
    window.seek_frame(100)
    editor = QLineEdit(window.centralWidget())
    editor.setGeometry(10, 10, 200, 40)
    editor.show()
    editor.setFocus()
    app.processEvents()
    QApplication.sendEvent(editor, QInputMethodEvent('拼音', []))
    window.timeline.setFocus()
    app.processEvents()
    press(app, window.timeline, Qt.Key.Key_K, CTRL)
    assert len(window.project.clips) == 2


def test_search_results_focus_cannot_edit_stale_timeline_selection(make_window,
                                                                app, tmp_path):
    window = make_window()
    clip = populate(window, tmp_path)
    window.seek_frame(100)
    window.side_tabs.setCurrentWidget(window.ai_panel)
    window.ai_results.addItem('Actual search-results widget: keyboard-context fixture')
    window.ai_results.setCurrentRow(0)
    window.ai_panel.ensureWidgetVisible(window.ai_results)
    window.ai_results.setFocus()
    app.processEvents()
    assert window.ai_results.hasFocus()
    assert window.selection == clip.id
    before = snapshot(window)
    for key, modifiers in [(Qt.Key.Key_Delete, NONE), (Qt.Key.Key_Backspace, NONE),
                           (Qt.Key.Key_K, CTRL)]:
        press(app, window.ai_results, key, modifiers)
    assert snapshot(window) == before
    assert window.ai_results.count() == 1


def test_voice_catalog_and_actual_popup_cannot_edit_stale_timeline_selection(
        make_window, app, tmp_path):
    window = make_window()
    clip = populate(window, tmp_path)
    window.seek_frame(100)
    window.side_tabs.setCurrentIndex(1)
    # Exercise the real populated catalog control without pretending that a
    # synthesis runtime/model has been installed or verified by this fixture.
    window.tts_voice.setEnabled(True)
    window.side_tabs.widget(1).ensureWidgetVisible(window.tts_voice)
    window.tts_voice.setFocus()
    app.processEvents()
    assert window.tts_voice.hasFocus()
    assert window.tts_voice.count() > 0
    assert window.selection == clip.id
    before = snapshot(window)
    for key, modifiers in [(Qt.Key.Key_Delete, NONE), (Qt.Key.Key_Backspace, NONE),
                           (Qt.Key.Key_K, CTRL)]:
        press(app, window.tts_voice, key, modifiers)
    assert snapshot(window) == before
    try:
        window.tts_voice.showPopup()
        app.processEvents()
        assert QApplication.activePopupWidget() is not None
        view = window.tts_voice.view()
        assert view.hasFocus() or view.isAncestorOf(QApplication.focusWidget())
        for key, modifiers in [(Qt.Key.Key_Delete, NONE),
                               (Qt.Key.Key_Backspace, NONE), (Qt.Key.Key_K, CTRL)]:
            press(app, view, key, modifiers)
        assert snapshot(window) == before
    finally:
        window.tts_voice.hidePopup()
        app.processEvents()


def test_professional_keyframe_delete_targets_selected_key_only(make_window, app,
                                                              tmp_path):
    from lumen.sticker_assets import load_sticker

    window = make_window()
    clip = populate(window, tmp_path)
    asset = tmp_path / 'keyframe-sticker.png'
    image = QImage(16, 16, QImage.Format.Format_RGBA8888)
    image.fill(QColor('#80bbff'))
    assert image.save(str(asset))
    overlay = window.editor.add_overlay(Overlay(
        id='keyboard-keyframe-sticker', asset_path=str(asset),
        asset_sha256=load_sticker(asset).digest, start=0, duration=60,
        keyframes=[OverlayKeyframe(), OverlayKeyframe(25, 50, 0, 1, 1),
                   OverlayKeyframe(50, 90, 10, 1, .8)],
    ))
    window.changed()
    window.select_overlay(overlay.id)
    window.keyframe_toggle.setChecked(True)
    window.overlay_keyframes.selectRow(1)
    window.overlay_keyframes.setFocus()
    app.processEvents()
    assert window.workspace_mode == 'professional'
    assert window.overlay_keyframes.hasFocus()
    assert window.overlay_keyframes.currentRow() == 1
    assert window.active_selection_type == 'overlay'
    assert window.shortcuts.enabled('edit.delete')
    assert window.shortcuts.actions['edit.delete'].isEnabled()
    revision = window.revision
    press(app, window.overlay_keyframes, Qt.Key.Key_Delete)
    assert [item.id for item in window.project.clips] == [clip.id]
    assert [item.id for item in window.project.overlays] == [overlay.id]
    assert [key.frame for key in window.selected_overlay().keyframes] == [0, 50]
    assert window.revision == revision + 1
    press(app, window.overlay_keyframes, Qt.Key.Key_Z, CTRL)
    assert [key.frame for key in window.selected_overlay().keyframes] == [0, 25, 50]
    window.overlay_keyframes.selectRow(0)
    before = snapshot(window)
    press(app, window.overlay_keyframes, Qt.Key.Key_Delete)
    assert snapshot(window) == before
    assert [key.frame for key in window.selected_overlay().keyframes] == [0, 25, 50]


def test_accepted_close_detaches_and_reopen_reattaches_once(make_window, app,
                                                          tmp_path, monkeypatch):
    window = make_window()
    populate(window, tmp_path)
    window.seek_frame(100)
    window.timeline.setFocus()
    assert window.shortcuts._installed is True
    window.dirty = False
    # This test asserts synchronous accepted-close behavior, after the new
    # independent background source-status check has settled. Active-scan
    # deferred close is covered separately in test_media_status_ui.
    from test_ui import pump_until
    pump_until(app, lambda: window._media_status_job is None)
    assert window.close() is True
    app.processEvents()
    assert not window.isVisible()
    assert window.shortcuts._installed is False

    installations = []
    original_install = app.installEventFilter

    def install_event_filter(event_filter):
        if event_filter is window.shortcuts:
            installations.append(event_filter)
        original_install(event_filter)

    monkeypatch.setattr(app, 'installEventFilter', install_event_filter)
    activate(app, window, window.timeline)
    assert window.shortcuts._installed is True
    activate(app, window, window.timeline)
    window.shortcuts.attach()
    window.shortcuts.attach()
    assert installations == [window.shortcuts]
    triggered = []
    window.shortcuts.actions['edit.split'].triggered.connect(
        lambda checked=False: triggered.append('split'))
    revision = window.revision
    press(app, window.timeline, Qt.Key.Key_K, CTRL)
    assert triggered == ['split']
    assert len(window.project.clips) == 2
    assert window.revision == revision + 1


def test_cancelled_dirty_close_keeps_event_filter_and_dispatch(make_window, app,
                                                             tmp_path, monkeypatch):
    window = make_window()
    populate(window, tmp_path)
    window.seek_frame(100)
    window.timeline.setFocus()
    assert window.dirty
    before = snapshot(window)
    questions = []

    def cancel_close(*args):
        questions.append(args[1])
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, 'question', cancel_close)
    assert window.close() is False
    app.processEvents()
    assert questions == ['Save project changes?']
    assert window.isVisible()
    assert not window.closing
    assert window.shortcuts._installed is True
    assert snapshot(window) == before
    press(app, window.timeline, Qt.Key.Key_K, CTRL)
    assert len(window.project.clips) == 2


def test_real_shortcut_opens_modal_capture_without_editor_side_effects(make_window, app, tmp_path):
    from PySide6.QtCore import QTimer
    from lumen.ui_shortcuts import ShortcutDialog
    window = make_window()
    populate(window, tmp_path)
    window.seek_frame(90)
    activate(app, window, window.timeline)
    before = snapshot(window)
    observed = []
    errors = []

    def use_dialog():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, ShortcutDialog)
            dialog.select_command('edit.split')
            dialog.primary_edit.setFocus()
            QTest.keyClick(dialog.primary_edit, Qt.Key.Key_K, CTRL)
            assert snapshot(window) == before
            assert dialog.staged_profiles['windows']['edit.split'] == ('Ctrl+K',)
            observed.append(True)
        except BaseException as exc:
            errors.append(exc)
        finally:
            if dialog is not None:
                dialog.reject()

    QTimer.singleShot(20, use_dialog)
    press(app, window.timeline, Qt.Key.Key_K, CTRL | ALT)
    assert observed == [True] and not errors, errors
    assert snapshot(window) == before
    assert window.shortcuts.bindings['edit.split'] == ('Ctrl+K',)


@pytest.mark.parametrize('host', ['darwin', 'win32', 'linux'])
def test_native_mac_arrow_keypad_annotation_dispatches_only_on_mac(make_window, app, tmp_path, monkeypatch, host):
    import lumen.shortcuts as module
    window = make_window('macos')
    populate(window, tmp_path)
    window.seek_frame(90)
    activate(app, window, window.timeline)
    monkeypatch.setattr(module.sys, 'platform', host)
    keypad = Qt.KeyboardModifier.KeypadModifier
    press(app, window.timeline, Qt.Key.Key_Right, keypad)
    assert window.timeline.playhead == (91 if host == 'darwin' else 90)
    press(app, window.timeline, Qt.Key.Key_Left, keypad | SHIFT)
    assert window.timeline.playhead == (86 if host == 'darwin' else 90)
    before = snapshot(window)
    press(app, window.timeline, Qt.Key.Key_Right, keypad | ALT)
    assert snapshot(window) == before


def test_current_host_arrow_annotation_contract_without_platform_override(make_window, app, tmp_path):
    window = make_window()
    populate(window, tmp_path)
    window.seek_frame(90)
    activate(app, window, window.timeline)
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right,
                      Qt.KeyboardModifier.KeypadModifier)
    QApplication.sendEvent(window.timeline, event)
    app.processEvents()
    assert window.timeline.playhead == (91 if sys.platform == 'darwin' else 90)

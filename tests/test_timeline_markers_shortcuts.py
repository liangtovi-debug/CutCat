"""Real key dispatch and migration for project annotation commands."""
import json

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QMenu, QSpinBox

from lumen.model import Caption, Project, TimelineMarker
from lumen.shortcuts import COMMANDS, PROFILES, SETTINGS_KEY, default_bindings, validate_bindings
from test_shortcuts_dispatch import (app, make_window, activate, populate, press, repeat,
                                     CTRL, SHIFT)
from test_shortcuts_model import controller

MARKER_COMMANDS = ('timeline.markers', 'timeline.add_marker',
                   'timeline.previous_marker', 'timeline.next_marker')


def test_new_defaults_no_conflicts_both_platforms():
    for profile, primary in [('windows', 'Ctrl'), ('macos', 'Cmd')]:
        values = default_bindings(profile)
        assert validate_bindings(values) == values
        assert values['timeline.add_marker'] == ('M',)
        assert values['timeline.previous_marker'] == (primary + '+Shift+Left',)
        assert values['timeline.next_marker'] == (primary + '+Shift+Right',)
        assert not values['timeline.markers']
        assert all(not COMMANDS[c].repeat for c in MARKER_COMMANDS)


def test_older_saved_profiles_gain_unbound_markers_without_reset(controller):
    profiles = {profile: {key: list(value) for key, value in default_bindings(profile).items()
                          if key not in MARKER_COMMANDS} for profile in PROFILES}
    profiles['windows']['timeline.snapping'] = ['M']  # Formerly free key stays owned.
    controller.settings.setValue(SETTINGS_KEY, json.dumps({
        'version': 1, 'active_profile': 'windows', 'profiles': profiles}))
    controller._load()
    assert controller.bindings['timeline.snapping'] == ('M',)
    assert all(controller.bindings[key] == () for key in MARKER_COMMANDS)
    assert not controller.load_warning


def test_add_key_dispatch_once_suppresses_repeat(make_window, app):
    calls = []
    w = make_window(callbacks={'add_timeline_marker_at_playhead': lambda self: calls.append(self.timeline.playhead)})
    press(app, w.timeline, Qt.Key.Key_M)
    repeat(app, w.timeline, Qt.Key.Key_M)
    assert calls == [0] and not w.project.timeline_markers


def test_previous_next_skip_sameframe_outofrange_without_wrap(make_window, app, tmp_path):
    w = make_window(); populate(w, tmp_path)
    w.project.timeline_markers = [TimelineMarker('a', 'A', '', 'red', 10),
        TimelineMarker('b', 'B', '', 'blue', 10), TimelineMarker('c', 'C', '', 'pink', 20),
        TimelineMarker('o', 'Outside', '', 'yellow', 25, 9999)]
    w.refresh(); w.seek_frame(0); activate(app, w, w.timeline)
    before = w.editor.revision
    press(app, w.timeline, Qt.Key.Key_Right, CTRL | SHIFT)
    assert w.timeline.playhead == 10
    press(app, w.timeline, Qt.Key.Key_Right, CTRL | SHIFT)
    assert w.timeline.playhead == 20
    press(app, w.timeline, Qt.Key.Key_Right, CTRL | SHIFT)
    assert w.timeline.playhead == 20 and not w.shortcuts.enabled('timeline.next_marker')
    press(app, w.timeline, Qt.Key.Key_Left, CTRL | SHIFT)
    assert w.timeline.playhead == 10
    press(app, w.timeline, Qt.Key.Key_Left, CTRL | SHIFT)
    assert w.timeline.playhead == 10 and not w.shortcuts.enabled('timeline.previous_marker')
    assert w.editor.revision == before


@pytest.mark.parametrize('focus_kind', ['text', 'number', 'media', 'dialog', 'modal', 'popup', 'ime'])
@pytest.mark.parametrize('key,modifiers,callback', [
    (Qt.Key.Key_M, Qt.KeyboardModifier.NoModifier, 'add_timeline_marker_at_playhead'),
    (Qt.Key.Key_Left, CTRL | SHIFT, 'previous_timeline_marker'),
    (Qt.Key.Key_Right, CTRL | SHIFT, 'next_timeline_marker'),
], ids=['add', 'previous', 'next'])
def test_marker_shortcut_input_focus_and_ime_guards(make_window, app, focus_kind, key, modifiers, callback):
    calls = []
    w = make_window(callbacks={callback: lambda self: calls.append('command')})
    w.editor.project = Project(captions=[Caption('c', 0, 40, 'Extent')], timeline_markers=[
        TimelineMarker('a', 'A', '', 'yellow', 10), TimelineMarker('b', 'B', '', 'blue', 20)])
    w.refresh(); w.seek_frame(15)
    before = w.project.to_dict()
    assert w.shortcuts.enabled('timeline.previous_marker') and w.shortcuts.enabled('timeline.next_marker')
    control = None
    if focus_kind == 'text':
        control = QLineEdit(w.centralWidget()); control.show(); activate(app, w, control)
    elif focus_kind == 'number':
        control = QSpinBox(w.centralWidget()); control.show(); activate(app, w, control)
    elif focus_kind == 'media':
        control = w.bin; activate(app, w, control)
    elif focus_kind in ('dialog', 'modal'):
        control = QDialog(w); control.setModal(focus_kind == 'modal'); control.show()
        QApplication.setActiveWindow(control); control.setFocus(); app.processEvents()
    elif focus_kind == 'popup':
        control = QMenu(w); control.addAction('dummy'); control.popup(w.mapToGlobal(w.rect().center())); app.processEvents()
    else:
        QApplication.sendEvent(w.timeline, QInputMethodEvent('拼', [])); control = w.timeline
    press(app, control, key, modifiers)
    assert not calls and w.project.to_dict() == before and w.timeline.playhead == 15
    if focus_kind == 'ime': QApplication.sendEvent(w.timeline, QInputMethodEvent('', []))
    if focus_kind in ('dialog', 'modal', 'popup'): control.close(); control.deleteLater()

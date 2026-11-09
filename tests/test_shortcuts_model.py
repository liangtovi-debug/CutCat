"""Bounded data-only keymap storage and physical-host conversion contracts."""
import copy
import json
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QWidget

from lumen.shortcuts import (
    COMMANDS, PROFILES, SETTINGS_KEY, default_bindings, normalize_binding,
    validate_bindings, conflict_for, sequence_from_binding, binding_from_sequence,
    display_binding, ShortcutController,
)


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controller(app, tmp_path):
    window = QWidget()
    settings = QSettings(str(tmp_path / 'keymap.ini'), QSettings.Format.IniFormat)
    value = ShortcutController(window, settings, 'windows')
    yield value
    app.removeEventFilter(value)
    window.deleteLater()
    app.processEvents()


def test_premiere_subset_defaults_and_aliases():
    for profile, primary in [('windows', 'Ctrl'), ('macos', 'Cmd')]:
        defaults = default_bindings(profile)
        assert set(defaults) == set(COMMANDS)
        assert validate_bindings(defaults) == defaults
        assert defaults['edit.split'] == (primary + '+K',)
        assert defaults['file.export'] == (primary + '+M',)
        assert defaults['edit.delete'] == ('Backspace', 'Delete')
        assert defaults['timeline.snapping'] == ('S',)
        assert defaults['playback.next_five_frames'] == ('Shift+Right',)
        assert defaults['timeline.preview_in'] == ('I',)
        assert defaults['timeline.preview_out'] == ('O',)
        assert defaults['timeline.preview_selection'] == ('Shift+Space',)
        assigned = {binding for values in defaults.values() for binding in values}
        assert assigned.isdisjoint({'C', 'V', 'J', 'K', 'L', 'Shift+Delete', 'Ctrl+B', 'Ctrl+E'})


@pytest.mark.parametrize('source,expected', [
    ('shift+control+k', 'Ctrl+Shift+K'), (' command + option + k ', 'Cmd+Alt+K'),
    ('Meta+k', 'Cmd+K'), ('Forward Delete', 'Delete'), ('Del', 'Delete'),
    ('page up', 'PgUp'), ('Ctrl+Comma', 'Ctrl+Comma'), ('', ''),
])
def test_canonicalization(source, expected):
    assert normalize_binding(source) == expected
    assert display_binding(source) == expected


@pytest.mark.parametrize('value', [
    None, 12, {}, [], 'Ctrl', 'Ctrl+Ctrl+K', 'Ctrl+', 'Ctrl++', 'Ctrl+K, Ctrl+S',
    'Alt+F4', 'Ctrl+Alt+Delete', 'Cmd+Q', 'Cmd+H', 'Cmd+Space', 'F36', 'F0',
    'Exec+Delete', '__import__("os")', 'A'*81, 'Escape', 'Return', 'Tab',
])
def test_invalid_and_reserved_bindings_rejected(value):
    with pytest.raises(ValueError): normalize_binding(value)


def test_real_qt_sequence_roundtrip_for_every_default_and_punctuation(app):
    for profile in PROFILES:
        for sequences in default_bindings(profile).values():
            for binding in sequences:
                assert binding_from_sequence(sequence_from_binding(binding)) == binding
    for key in ('Plus', 'Minus', 'Equal', 'Comma', 'Period', 'Slash', 'Backslash',
                'Semicolon', 'Apostrophe', 'BracketLeft', 'BracketRight', 'QuoteLeft'):
        binding = 'Ctrl+Shift+' + key
        assert binding_from_sequence(sequence_from_binding(binding)) == binding
    assert binding_from_sequence(QKeySequence()) == ''
    with pytest.raises(ValueError): binding_from_sequence(QKeySequence('Ctrl+K, Ctrl+S'))


@pytest.mark.parametrize('host', ['darwin', 'win32', 'linux'])
def test_mac_qt_modifier_swap_is_host_not_profile(monkeypatch, host):
    import lumen.shortcuts as module
    monkeypatch.setattr(module.sys, 'platform', host)
    command = sequence_from_binding('Cmd+K')[0]
    control = sequence_from_binding('Ctrl+K')[0]
    assert command.keyboardModifiers() == (Qt.ControlModifier if host == 'darwin' else Qt.MetaModifier)
    assert control.keyboardModifiers() == (Qt.MetaModifier if host == 'darwin' else Qt.ControlModifier)
    assert binding_from_sequence(QKeySequence(command)) == 'Cmd+K'
    assert binding_from_sequence(QKeySequence(control)) == 'Ctrl+K'


@pytest.mark.parametrize('alias', ['Delete', 'Backspace', 'Del', 'Forward Delete'])
def test_delete_alias_conflicts_are_not_silently_overwritten(alias):
    defaults = default_bindings('windows')
    assert conflict_for(defaults, 'edit.split', (alias,)) == 'edit.delete'
    defaults['edit.split'] = (alias,)
    with pytest.raises(ValueError, match='conflicts'): validate_bindings(defaults)


@pytest.mark.parametrize('mutation', [
    lambda m: m.update({'python.exec': ('X',)}),
    lambda m: m.pop('edit.delete'),
    lambda m: m.update({'edit.split': 'Ctrl+K'}),
    lambda m: m.update({'edit.split': ('A', 'B', 'C')}),
    lambda m: m.update({'edit.split': ('Ctrl+K', 'control+k')}),
    lambda m: m.update({'edit.split': ({'callback': 'evil'},)}),
])
def test_invalid_command_schema_rejected(mutation):
    defaults = default_bindings('windows')
    mutation(defaults)
    with pytest.raises(ValueError): validate_bindings(defaults)


def test_persist_profiles_reset_and_restart(controller):
    c = controller
    c.set_bindings('edit.split', ['Ctrl+Shift+K'])
    assert c.bindings['edit.split'] == ('Ctrl+Shift+K',)
    c.switch_profile('macos')
    c.set_bindings('edit.split', ['Cmd+Shift+K'])
    saved = QSettings(c.settings.fileName(), QSettings.Format.IniFormat)
    another = ShortcutController(QWidget(), saved, 'windows')
    try:
        assert another.profile == 'macos'
        assert another.profiles['windows']['edit.split'] == ('Ctrl+Shift+K',)
        assert another.bindings['edit.split'] == ('Cmd+Shift+K',)
        another.reset_profile()
        assert another.bindings == default_bindings('macos')
        assert another.profiles['windows']['edit.split'] == ('Ctrl+Shift+K',)
        another.set_bindings('edit.split', [])
        assert another.bindings['edit.split'] == ()
    finally:
        QApplication.instance().removeEventFilter(another)


def test_conflict_rejects_atomically(controller):
    c = controller
    old = copy.deepcopy(c.profiles)
    raw = c.settings.value(SETTINGS_KEY)
    with pytest.raises(ValueError): c.set_bindings('edit.split', ['Ctrl+S'])
    assert c.profiles == old and c.settings.value(SETTINGS_KEY) == raw


@pytest.mark.parametrize('raw', [
    '{not valid json', '[]', 'null', '123', '{}', 12, [],
    json.dumps({'version': 2, 'active_profile': 'windows', 'profiles': {}}),
    json.dumps({'version': True, 'active_profile': 'windows', 'profiles': {}}),
    'x' * 65537,
], ids=['bad-json', 'array', 'null', 'number', 'empty-object', 'integer-value',
        'array-value', 'future-version', 'boolean-version', 'oversize-value'])
def test_corrupt_preferences_fall_back_without_overwriting(controller, raw):
    c = controller
    c.settings.setValue(SETTINGS_KEY, raw)
    other_window = QWidget()
    other = ShortcutController(other_window, c.settings, 'macos')
    try:
        assert other.profile == 'macos'
        assert other.profiles == {name: default_bindings(name) for name in PROFILES}
        assert other.load_warning
        assert c.settings.value(SETTINGS_KEY) == raw
    finally:
        QApplication.instance().removeEventFilter(other)


def test_conflicting_preferences_fall_back(controller):
    profiles = copy.deepcopy(controller.profiles)
    profiles['windows']['edit.split'] = ['Delete']
    controller.settings.setValue(SETTINGS_KEY, json.dumps({'version': 1, 'active_profile': 'windows', 'profiles': profiles}))
    other_window = QWidget()
    other = ShortcutController(other_window, controller.settings, 'windows')
    try:
        assert other.load_warning
        assert other.bindings == default_bindings('windows')
    finally:
        QApplication.instance().removeEventFilter(other)


def test_failed_persistence_keeps_active_session_atomic(controller):
    class FailingSettings:
        value_saved = None
        def value(self, key, default=None): return self.value_saved
        def setValue(self, key, value): self.value_saved = value
        def remove(self, key): self.value_saved = None
        def sync(self): pass
        def status(self): return QSettings.Status.AccessError
    c = controller
    c.settings = FailingSettings()
    old = copy.deepcopy(c.profiles)
    with pytest.raises(OSError): c.set_bindings('edit.split', ['Ctrl+Shift+K'])
    assert c.profiles == old and c.settings.value_saved is None


def test_every_accepted_key_has_nonzero_known_qt_representation():
    import lumen.shortcuts as module
    keys = set(module._KEY_NAMES.values()) | set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') | {f'F{i}' for i in range(1, 36)}
    for key in keys:
        sequence = sequence_from_binding('Ctrl+' + key)
        assert sequence.count() == 1
        assert sequence[0].key() not in (0, Qt.Key.Key_unknown), key
        assert binding_from_sequence(sequence) == 'Ctrl+' + key
    assert binding_from_sequence(QKeySequence(Qt.Key.Key_unknown)) == ''


def test_malformed_qsettings_file_uses_safe_defaults(app, tmp_path):
    path = tmp_path / 'broken.ini'
    path.write_text('[shortcuts\nthis is not a settings section\nv1={bad json', encoding='utf-8')
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    window = QWidget()
    controller = ShortcutController(window, settings, 'windows')
    try:
        assert controller.bindings == default_bindings('windows')
        assert 'this is not a settings section' in path.read_text(encoding='utf-8')
    finally:
        app.removeEventFilter(controller)


@pytest.mark.parametrize('host', ['darwin', 'win32', 'linux'])
@pytest.mark.parametrize('key,name', [(Qt.Key.Key_Left, 'Left'), (Qt.Key.Key_Right, 'Right'),
                                     (Qt.Key.Key_Up, 'Up'), (Qt.Key.Key_Down, 'Down')])
def test_only_mac_arrow_keypad_annotation_is_normalized(monkeypatch, host, key, name):
    import lumen.shortcuts as module
    monkeypatch.setattr(module.sys, 'platform', host)
    sequence = QKeySequence(key | (Qt.KeyboardModifier.KeypadModifier | Qt.KeyboardModifier.ShiftModifier))
    if host == 'darwin':
        assert binding_from_sequence(sequence) == 'Shift+' + name
    else:
        with pytest.raises(ValueError): binding_from_sequence(sequence)
    with pytest.raises(ValueError):
        binding_from_sequence(QKeySequence(Qt.Key.Key_K | Qt.KeyboardModifier.KeypadModifier))

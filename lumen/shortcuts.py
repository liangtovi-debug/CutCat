"""App-local, validated keyboard commands. Settings contain data, never callbacks.

Bindings use *physical* Ctrl/Cmd names. Qt exchanges Control and Meta meanings
on macOS; conversion is based on the actual host, never the selected preset.
Only this event filter dispatches key commands. Menu labels show bindings but
QActions deliberately register no shortcut, so a Qt shortcut cannot bypass the
text, input-method, modal, focus, or auto-repeat guards.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import re
import sys

from PySide6.QtCore import QObject, QEvent, QSettings, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication, QWidget, QDialog, QLineEdit, QPlainTextEdit, QTextEdit,
    QAbstractSpinBox, QComboBox, QAbstractItemView, QAbstractSlider,
    QAbstractButton, QKeySequenceEdit,
)

from .i18n import tr, locale_events


@dataclass(frozen=True)
class Command:
    id: str
    label: str
    category: str
    repeat: bool = False


_COMMAND_LIST = (
    Command('file.new', 'New project…', 'File'),
    Command('file.open', 'Open project…', 'File'),
    Command('file.save', 'Save', 'File'),
    Command('file.save_as', 'Save as…', 'File'),
    Command('file.import', 'Import…', 'File'),
    Command('file.export', 'Export…', 'File'),
    Command('edit.undo', 'Undo', 'Edit'),
    Command('edit.redo', 'Redo', 'Edit'),
    Command('edit.copy_clips', 'Copy clips', 'Edit'),
    Command('edit.paste_clips', 'Paste clips at playhead (original tracks)', 'Edit'),
    Command('edit.separate_audio', 'Separate source audio…', 'Edit'),
    Command('edit.reattach_audio', 'Reattach source audio…', 'Edit'),
    Command('edit.link_av', 'Link same-source A/V', 'Edit'),
    Command('edit.unlink_av', 'Unlink A/V', 'Edit'),
    Command('edit.joint_slip', 'Joint source slip…', 'Edit'),
    Command('edit.audio_roll', 'Roll linked audio cut…', 'Edit'),
    Command('edit.group', 'Group clips', 'Edit'),
    Command('edit.ungroup', 'Ungroup clips', 'Edit'),
    Command('edit.split', 'Split at playhead', 'Edit'),
    Command('edit.delete', 'Delete selected clip / caption / sticker / effect / transition', 'Edit'),
    Command('playback.toggle', 'Play / pause', 'Playback'),
    Command('playback.previous_frame', 'Previous frame', 'Playback', True),
    Command('playback.next_frame', 'Next frame', 'Playback', True),
    Command('playback.previous_five_frames', 'Back five frames', 'Playback', True),
    Command('playback.next_five_frames', 'Forward five frames', 'Playback', True),
    Command('timeline.preview_in', 'Set preview In', 'Timeline'),
    Command('timeline.preview_out', 'Set preview Out (inclusive)', 'Timeline'),
    Command('timeline.preview_selection', 'Build and loop selection', 'Timeline'),
    Command('timeline.preview_clear', 'Clear preview selection', 'Timeline'),
    Command('timeline.markers', 'Timeline markers…', 'Timeline'),
    Command('timeline.add_marker', 'Add point marker at playhead', 'Timeline'),
    Command('timeline.previous_marker', 'Previous marker', 'Timeline'),
    Command('timeline.next_marker', 'Next marker', 'Timeline'),
    Command('timeline.snapping', 'Toggle snapping', 'Timeline'),
    Command('timeline.master_gain', 'Master output gain…', 'Timeline'),
    Command('timeline.analyze_audio', 'Analyze timeline audio…', 'Timeline'),
    Command('app.shortcuts', 'Keyboard shortcuts…', 'Settings'),
)
COMMANDS = {command.id: command for command in _COMMAND_LIST}
PROFILES = ('windows', 'macos')
SETTINGS_KEY = 'shortcuts/v1'
MAX_SETTINGS_BYTES = 65536


def default_bindings(profile: str) -> dict[str, tuple[str, ...]]:
    if profile not in PROFILES:
        raise ValueError(tr('Unknown shortcut preset.'))
    primary = 'Cmd' if profile == 'macos' else 'Ctrl'
    return {
        'file.new': (f'{primary}+N',), 'file.open': (f'{primary}+O',),
        'file.save': (f'{primary}+S',), 'file.save_as': (f'{primary}+Shift+S',),
        'file.import': (f'{primary}+I',), 'file.export': (f'{primary}+M',),
        'edit.undo': (f'{primary}+Z',), 'edit.redo': (f'{primary}+Shift+Z',),
        'edit.copy_clips': (f'{primary}+C',), 'edit.paste_clips': (f'{primary}+V',), 'edit.separate_audio': (), 'edit.reattach_audio': (),
        'edit.link_av': (), 'edit.unlink_av': (), 'edit.joint_slip': (), 'edit.audio_roll': (),
        'edit.group': (f'{primary}+G',), 'edit.ungroup': (f'{primary}+Shift+G',),
        'edit.split': (f'{primary}+K',), 'edit.delete': ('Backspace', 'Delete'),
        'playback.toggle': ('Space',), 'playback.previous_frame': ('Left',),
        'playback.next_frame': ('Right',), 'playback.previous_five_frames': ('Shift+Left',),
        'playback.next_five_frames': ('Shift+Right',),
        'timeline.preview_in': ('I',), 'timeline.preview_out': ('O',),
        'timeline.preview_selection': ('Shift+Space',), 'timeline.preview_clear': (),
        'timeline.markers': (), 'timeline.add_marker': ('M',),
        'timeline.previous_marker': (f'{primary}+Shift+Left',),
        'timeline.next_marker': (f'{primary}+Shift+Right',),
        'timeline.snapping': ('S',), 'timeline.master_gain': (), 'timeline.analyze_audio': (), 'app.shortcuts': (f'{primary}+Alt+K',),
    }


_MODIFIERS = {'ctrl': 'Ctrl', 'control': 'Ctrl', 'cmd': 'Cmd', 'command': 'Cmd',
              'meta': 'Cmd', 'alt': 'Alt', 'option': 'Alt', 'shift': 'Shift'}
_MODIFIER_ORDER = ('Ctrl', 'Cmd', 'Alt', 'Shift')
_KEY_NAMES = {
    name.lower(): name for name in (
        'Space', 'Backspace', 'Delete', 'Insert', 'Home', 'End', 'Left', 'Right',
        'Up', 'Down', 'PgUp', 'PgDown', 'Plus', 'Minus', 'Equal', 'Comma',
        'Period', 'Slash', 'Backslash', 'Semicolon', 'Apostrophe', 'BracketLeft',
        'BracketRight', 'QuoteLeft',
    )
}
_KEY_NAMES.update({'del': 'Delete', 'ins': 'Insert', 'forward delete': 'Delete', 'pageup': 'PgUp',
                   'pagedown': 'PgDown', 'page up': 'PgUp', 'page down': 'PgDown'})
_KEY_QT = {'Plus': '+', 'Minus': '-', 'Equal': '=', 'Comma': ',', 'Period': '.',
           'Slash': '/', 'Backslash': '\\', 'Semicolon': ';', 'Apostrophe': "'",
           'BracketLeft': '[', 'BracketRight': ']', 'QuoteLeft': '`'}
_QT_KEY = {value: key for key, value in _KEY_QT.items()}
_RESERVED = {'Alt+Tab', 'Alt+Shift+Tab', 'Alt+F4', 'Ctrl+Alt+Delete',
             'Ctrl+Escape', 'Cmd+Tab', 'Cmd+Shift+Tab', 'Cmd+Space', 'Cmd+Q', 'Cmd+H'}


def normalize_binding(binding: str) -> str:
    """Canonical, single-stroke, bounded whitelist; empty means unassigned."""
    if not isinstance(binding, str) or len(binding) > 80:
        raise ValueError(tr('Enter one valid key combination.'))
    binding = binding.strip()
    if not binding:
        return ''
    parts = [part.strip() for part in binding.split('+')]
    if not all(parts) or len(parts) > 5:
        raise ValueError(tr('Enter one valid key combination.'))
    modifiers = []
    for part in parts[:-1]:
        modifier = _MODIFIERS.get(part.lower())
        if modifier is None or modifier in modifiers:
            raise ValueError(tr('Enter one valid key combination.'))
        modifiers.append(modifier)
    raw_key = parts[-1]
    if re.fullmatch(r'[A-Za-z0-9]', raw_key):
        key = raw_key.upper()
    elif re.fullmatch(r'F(?:[1-9]|[12][0-9]|3[0-5])', raw_key.upper()):
        key = raw_key.upper()
    else:
        key = _KEY_NAMES.get(raw_key.lower()) or _QT_KEY.get(raw_key)
    if key is None:
        raise ValueError(tr('Enter one valid key combination.'))
    result = '+'.join([m for m in _MODIFIER_ORDER if m in modifiers] + [key])
    if result in _RESERVED:
        raise ValueError(tr('This combination is reserved for the operating system.'))
    return result


def _qt_modifiers(modifiers):
    value = Qt.KeyboardModifier.NoModifier
    for modifier in modifiers:
        if modifier == 'Cmd':
            value |= Qt.KeyboardModifier.ControlModifier if sys.platform == 'darwin' else Qt.KeyboardModifier.MetaModifier
        elif modifier == 'Ctrl':
            value |= Qt.KeyboardModifier.MetaModifier if sys.platform == 'darwin' else Qt.KeyboardModifier.ControlModifier
        elif modifier == 'Alt':
            value |= Qt.KeyboardModifier.AltModifier
        elif modifier == 'Shift':
            value |= Qt.KeyboardModifier.ShiftModifier
    return value


def sequence_from_binding(binding: str) -> QKeySequence:
    binding = normalize_binding(binding)
    if not binding:
        return QKeySequence()
    *modifiers, key = binding.split('+')
    sequence = QKeySequence(_KEY_QT.get(key, key), QKeySequence.SequenceFormat.PortableText)
    return QKeySequence(sequence[0].key() | _qt_modifiers(modifiers))


def binding_from_sequence(sequence: QKeySequence) -> str:
    if sequence.isEmpty():
        return ''
    if sequence.count() != 1:
        raise ValueError(tr('Enter one valid key combination.'))
    combination = sequence[0]
    key = QKeySequence(combination.key()).toString(QKeySequence.SequenceFormat.PortableText)
    modifiers = combination.keyboardModifiers()
    # Qt documents macOS arrow keys as carrying KeypadModifier, including the
    # dedicated arrows. This is a host input annotation, not an extra user key.
    # Never strip it on other hosts or from non-arrow keys.
    if sys.platform == 'darwin' and combination.key() in (
            Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
        modifiers = Qt.KeyboardModifier(modifiers.value & ~Qt.KeyboardModifier.KeypadModifier.value)
    known = (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier |
             Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier)
    # Test integer bits explicitly: QFlags inversion differs across bindings /
    # native builds and must never erase an unsupported modifier silently.
    if modifiers.value & ~known.value:
        raise ValueError(tr('Enter one valid key combination.'))
    physical = []
    if modifiers & Qt.KeyboardModifier.ControlModifier:
        physical.append('Cmd' if sys.platform == 'darwin' else 'Ctrl')
    if modifiers & Qt.KeyboardModifier.MetaModifier:
        physical.append('Ctrl' if sys.platform == 'darwin' else 'Cmd')
    if modifiers & Qt.KeyboardModifier.AltModifier:
        physical.append('Alt')
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        physical.append('Shift')
    return normalize_binding('+'.join(physical + [_QT_KEY.get(key, key)]))


def display_binding(binding: str) -> str:
    # Explicit words remain unambiguous even when previewing the other OS preset.
    return normalize_binding(binding)


def conflict_for(bindings, command_id, sequences) -> str | None:
    proposed = {normalize_binding(value) for value in sequences if value}
    for other, values in bindings.items():
        if other != command_id and proposed.intersection(normalize_binding(value) for value in values):
            return other
    return None


def validate_bindings(bindings) -> dict[str, tuple[str, ...]]:
    if not isinstance(bindings, dict) or set(bindings) != set(COMMANDS):
        raise ValueError(tr('Shortcut settings contain unknown or missing commands.'))
    result = {}
    used = {}
    for command_id, sequences in bindings.items():
        if not isinstance(sequences, (tuple, list)) or len(sequences) > 2:
            raise ValueError(tr('Use at most two shortcuts for each command.'))
        normalized = tuple(value for value in (normalize_binding(s) for s in sequences) if value)
        if len(set(normalized)) != len(normalized):
            raise ValueError(tr('The same shortcut is assigned twice.'))
        for binding in normalized:
            if binding in used:
                raise ValueError(tr('Shortcut {shortcut} conflicts with {command}.',
                                    shortcut=display_binding(binding), command=tr(COMMANDS[used[binding]].label)))
            used[binding] = command_id
        result[command_id] = normalized
    return result


def _ancestor(widget, classes):
    while isinstance(widget, QWidget):
        if isinstance(widget, classes):
            return widget
        widget = widget.parentWidget()
    return None


class ShortcutController(QObject):
    changed = Signal()

    def __init__(self, window, settings=None, platform=None):
        super().__init__(window)
        self.window = window
        self.settings = settings if settings is not None else window.workspace_settings
        self.profile = platform or ('macos' if sys.platform == 'darwin' else 'windows')
        if self.profile not in PROFILES:
            raise ValueError(tr('Unknown shortcut preset.'))
        self.profiles = {name: default_bindings(name) for name in PROFILES}
        self.actions = {}
        self._composing = None
        self.load_warning = ''
        self._load()
        self.changed.connect(self.refresh_labels)
        locale_events.changed.connect(self.refresh_labels)
        self._installed = False
        self.attach()

    def attach(self):
        if not self._installed:
            QApplication.instance().installEventFilter(self)
            self._installed = True

    def detach(self):
        if self._installed:
            QApplication.instance().removeEventFilter(self)
            self._installed = False
        self._composing = None

    @property
    def bindings(self):
        return self.profiles[self.profile]

    def _load(self):
        raw = self.settings.value(SETTINGS_KEY, None)
        if raw is None:
            return
        try:
            if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_SETTINGS_BYTES:
                raise ValueError
            data = json.loads(raw)
            if (not isinstance(data, dict) or set(data) != {'version', 'active_profile', 'profiles'}
                    or type(data['version']) is not int or data['version'] != 1
                    or data['active_profile'] not in PROFILES
                    or not isinstance(data['profiles'], dict) or set(data['profiles']) != set(PROFILES)):
                raise ValueError
            # New commands must not reset or collide with existing bindings.
            # Older saved profiles gain new commands unbound; fresh presets
            # include the standard Group / Ungroup combinations.
            optional = {'timeline.preview_in', 'timeline.preview_out', 'timeline.preview_selection', 'timeline.preview_clear', 'edit.link_av', 'edit.unlink_av', 'edit.joint_slip', 'edit.audio_roll', 'edit.reattach_audio', 'edit.separate_audio', 'timeline.analyze_audio', 'timeline.master_gain', 'edit.copy_clips', 'edit.paste_clips',
                        'edit.group', 'edit.ungroup', 'timeline.markers', 'timeline.add_marker',
                        'timeline.previous_marker', 'timeline.next_marker'}
            for bindings in data['profiles'].values():
                if isinstance(bindings, dict) and set(COMMANDS) - optional <= set(bindings) <= set(COMMANDS):
                    for command_id in optional:
                        bindings.setdefault(command_id, [])
            profiles = {name: validate_bindings(data['profiles'][name]) for name in PROFILES}
            self.profile, self.profiles = data['active_profile'], profiles
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            self.load_warning = tr('Invalid shortcut preferences were ignored. PR-reference defaults are active.')

    def apply_configuration(self, profiles, active_profile):
        if active_profile not in PROFILES or not isinstance(profiles, dict) or set(profiles) != set(PROFILES):
            raise ValueError(tr('Unknown shortcut preset.'))
        validated = {name: validate_bindings(profiles[name]) for name in PROFILES}
        value = json.dumps({'version': 1, 'active_profile': active_profile, 'profiles': validated}, ensure_ascii=False)
        previous = self.settings.value(SETTINGS_KEY, None)
        try:
            self.settings.setValue(SETTINGS_KEY, value)
            self.settings.sync()
            if self.settings.status() != QSettings.Status.NoError:
                raise OSError(tr('Could not save shortcut preferences. Your active shortcuts are unchanged.'))
        except Exception as exc:
            # Keep session bindings atomic on failure. Best-effort restore prevents
            # QSettings from later flushing the rejected value at destruction.
            try:
                if previous is None:
                    self.settings.remove(SETTINGS_KEY)
                else:
                    self.settings.setValue(SETTINGS_KEY, previous)
                self.settings.sync()
            except Exception:
                pass
            raise OSError(tr('Could not save shortcut preferences. Your active shortcuts are unchanged.')) from exc
        self.profiles = validated
        self.profile = active_profile
        self.load_warning = ''
        self.changed.emit()

    def set_bindings(self, command_id, sequences):
        if command_id not in COMMANDS:
            raise ValueError(tr('Shortcut settings contain unknown or missing commands.'))
        profiles = deepcopy(self.profiles)
        profiles[self.profile][command_id] = sequences
        self.apply_configuration(profiles, self.profile)

    def switch_profile(self, profile):
        self.apply_configuration(self.profiles, profile)

    def reset_profile(self):
        profiles = deepcopy(self.profiles)
        profiles[self.profile] = default_bindings(self.profile)
        self.apply_configuration(profiles, self.profile)

    def register_action(self, command_id, action):
        if command_id not in COMMANDS or command_id in self.actions:
            raise ValueError('Invalid command registration')
        self.actions[command_id] = action
        action.setShortcuts([])
        action.setAutoRepeat(False)
        self.refresh_labels()

    def refresh_labels(self, *_):
        for command_id, action in self.actions.items():
            label = str(tr(COMMANDS[command_id].label))
            keys = ' / '.join(display_binding(binding) for binding in self.bindings[command_id])
            action.setText(label + ('\t' + keys if keys else ''))
            action.setToolTip(label + (' (' + keys + ')' if keys else ''))
        if 'timeline.snapping' in self.actions:
            self.actions['timeline.snapping'].setChecked(self.window.timeline.snapping_enabled)

    def enabled(self, command_id):
        w = self.window
        if w.closing or not w.isEnabled() or not w.centralWidget().isEnabled():
            return False
        if command_id in ('edit.split', 'edit.delete', 'edit.group', 'edit.ungroup') and w.selection_edit_locked():
            return False
        if command_id == 'edit.copy_clips': return w.can_copy_selected_clips()
        if command_id == 'edit.paste_clips': return w.can_paste_copied_clips()
        if command_id == 'edit.separate_audio': return w.can_separate_source_audio()
        if command_id == 'edit.reattach_audio': return w.can_reattach_source_audio()
        if command_id == 'edit.link_av': return w.can_link_selected_av()
        if command_id == 'edit.unlink_av': return w.can_unlink_selected_av()
        if command_id == 'edit.joint_slip': return w.can_slip_linked_av()
        if command_id == 'edit.audio_roll': return w.can_roll_linked_audio()
        if command_id == 'edit.undo': return w.editor.can_undo
        if command_id == 'edit.redo': return w.editor.can_redo
        if command_id == 'edit.group': return w.can_group_selected_clips()
        if command_id == 'edit.ungroup': return w.can_ungroup_selected_clips()
        if command_id == 'edit.split': return w.can_split_selected()
        if command_id == 'edit.delete':
            if w.active_selection_type == 'transition': return w.selected_video_transition() is not None
            if w.active_selection_type == 'caption': return w.selected_caption() is not None
            if w.active_selection_type == 'overlay': return w.selected_overlay() is not None
            if w.active_selection_type == 'effect': return w.workspace_mode == 'professional' and w.selected_effect() is not None
            return bool(w.selected_clip_ids)
        if command_id == 'playback.toggle': return w.play_button.isEnabled()
        if command_id.startswith('playback.'): return w.project.duration_frames > 0
        if command_id == 'file.export': return w.export_button.isEnabled()
        if command_id == 'timeline.previous_marker': return w.adjacent_timeline_marker(-1) is not None
        if command_id == 'timeline.next_marker': return w.adjacent_timeline_marker(1) is not None
        if command_id == 'timeline.analyze_audio': return bool(w.project.clips) and not w.jobs
        if command_id in ('file.new', 'file.open'): return not w.jobs
        if command_id == 'file.import': return w.import_job is None
        return True

    def update_enabled(self):
        # One qualification per refresh chain: never cache across mutable project edits.
        split = (self.window.split_selection_qualification()
                 if hasattr(self.window, 'split_selection_qualification') else None)
        if split is not None: self.window.refresh_split_control(split)
        for command_id, action in self.actions.items():
            action.setEnabled(split[0] is not None if command_id == 'edit.split' and split is not None
                              else self.enabled(command_id))
            if command_id in ('edit.link_av', 'edit.unlink_av', 'edit.joint_slip'):
                from .ui_linked_av import UNLINK_NOTE
                _, reason = self.window.linked_av_selection()
                text = tr(reason) if reason else tr(UNLINK_NOTE)
                action.setToolTip(text); action.setStatusTip(text)
            if command_id == 'edit.reattach_audio':
                _, reason = self.window.source_audio_reattach_selection()
                text = tr(reason) if reason else tr('Preview the two selected IDs; Apply deletes only the selected audio clip.')
                action.setToolTip(text); action.setStatusTip(text)
            if command_id == 'edit.split' and split is not None:
                text = self.window.split_clip_button.toolTip()
                action.setToolTip(text); action.setStatusTip(text)
            elif command_id in ('edit.delete', 'edit.group', 'edit.ungroup'):
                from .ui_track_edit_locks import LOCK_REASON
                action.setToolTip(tr(LOCK_REASON) if self.window.selection_edit_locked() else action.text())

    def _focus_allowed(self, command_id, focus):
        if _ancestor(focus, (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox,
                             QComboBox, QKeySequenceEdit, QAbstractSlider)):
            return False
        if _ancestor(focus, QDialog) or self._composing is not None:
            return False
        # All present and future non-history Edit commands inherit the safe
        # timeline focus boundary; adding a command cannot forget a whitelist.
        timeline_edit = (command_id.startswith('edit.') and command_id not in ('edit.undo', 'edit.redo')) or command_id.startswith('timeline.preview_')
        view = _ancestor(focus, QAbstractItemView)
        if view:
            if (command_id.startswith('playback.') or (timeline_edit and command_id != 'edit.delete')
                    or command_id in ('timeline.add_marker', 'timeline.previous_marker', 'timeline.next_marker')):
                return False
            if command_id == 'edit.delete':
                w = self.window
                expected = {w.caption_list: 'caption', w.overlay_list: 'overlay', w.effect_list: 'effect'}
                if view is w.overlay_keyframes:
                    return w.workspace_mode == 'professional' and w.active_selection_type == 'overlay' and view.currentRow() >= 0
                return expected.get(view) == w.active_selection_type and view.currentItem() is not None
        if timeline_edit and view is None:
            # A lingering selection must not make unrelated inspector controls
            # destructive keyboard targets. A deliberate menu/button click is
            # still an explicit edit and does not go through this key guard.
            w = self.window
            if focus is not w and not any(focus is area or area.isAncestorOf(focus)
                                         for area in (w.timeline, w.video)):
                return False
        if command_id == 'playback.toggle' and _ancestor(focus, QAbstractButton):
            return False
        return True

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.InputMethod:
            if isinstance(watched, QWidget) and watched.window() is self.window:
                self._composing = watched if event.preeditString() else None
            return False
        if kind in (QEvent.Type.FocusOut, QEvent.Type.Destroy):
            if watched is self._composing:
                self._composing = None
            return False
        if kind == QEvent.Type.WindowDeactivate and watched is self.window:
            self._composing = None
            return False
        if kind != QEvent.Type.KeyPress or not isinstance(watched, QWidget):
            return False
        app = QApplication.instance()
        if (not self.window.isVisible() or app.activeWindow() is not self.window
                or watched.window() is not self.window or app.activeModalWidget() is not None
                or app.activePopupWidget() is not None):
            return False
        try:
            binding = binding_from_sequence(QKeySequence(event.keyCombination()))
        except ValueError:
            return False
        command_id = next((key for key, sequences in self.bindings.items() if binding and binding in sequences), None)
        if command_id is None:
            return False
        focus = app.focusWidget() or watched
        if not self._focus_allowed(command_id, focus):
            return False
        # A recognized command is consumed even when disabled / repeating; it
        # cannot propagate into a second trigger or the focused button's action.
        action = self.actions.get(command_id)
        if action and action.isEnabled() and self.enabled(command_id):
            if not event.isAutoRepeat() or COMMANDS[command_id].repeat:
                action.trigger()
        event.accept()
        return True

    def open_dialog(self):
        from .ui_shortcuts import ShortcutDialog
        dialog = ShortcutDialog(self, self.window)
        dialog.exec()
        dialog.deleteLater()

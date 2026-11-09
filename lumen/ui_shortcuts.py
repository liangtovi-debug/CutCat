"""Modal, transactional keyboard preferences; no editor or project mutation.

Only a fixed command ID and validated key sequences reach the controller.
Locale changes update presentation in place, never rebuild the staged model.
"""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import QEvent, QSignalBlocker, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox,
    QGridLayout, QHBoxLayout, QHeaderView, QKeySequenceEdit, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .i18n import locale_events, tr
from .i18n_catalog_shortcuts import EN, ZH_CN
from .shortcuts import (
    COMMANDS, binding_from_sequence, conflict_for, default_bindings,
    display_binding, sequence_from_binding, validate_bindings,
)

_PROFILE_LABELS = {
    'windows': 'Windows · Premiere Pro reference',
    'macos': 'macOS · Premiere Pro reference',
}
_SCOPE = ('Reference subset only. C/V tools, J/K/L shuttle and general ripple trim '
          'are not implemented. Source I/O marks and ripple delete exist, but have no shortcut mapping.')
_SAFETY = ('Editor shortcuts pause while typing text or numbers, using an IME, '
           'or editing a dialog. Some known OS-reserved combinations are rejected; '
           'system shortcuts may still take priority.')
_RECORDING = ('Record one key combination in each field. Cmd denotes the '
              'macOS Command key; Ctrl denotes Control. Recording uses native key names; '
              'on Windows/Linux, Meta means Cmd in the list.')
_TRANSACTION = ('Changes take effect only after Apply. Cancel discards changes '
                'made since the last Apply.')


class _ShortcutSequenceEdit(QKeySequenceEdit):
    """Keep the placeholder explicit even after Qt's queued LanguageChange."""

    def event(self, event):
        handled = super().event(event)
        if event.type() in (QEvent.Type.LanguageChange, QEvent.Type.FocusIn,
                            QEvent.Type.FocusOut, QEvent.Type.KeyRelease):
            self.refresh_placeholder()
        return handled

    def refresh_placeholder(self, *_):
        line_edit = self.findChild(QLineEdit)
        if line_edit is not None:
            line_edit.setPlaceholderText(tr('Press shortcut'))

    def setKeySequence(self, sequence):
        super().setKeySequence(sequence)
        self.refresh_placeholder()

    def clear(self):
        super().clear()
        self.refresh_placeholder()


class ShortcutDialog(QDialog):
    """Edit independent Windows/macOS presets, applying them atomically.

    The controller remains the authority for validation and persistence. Invalid
    entries stay visible for correction, but never escape this dialog. Closing
    or cancelling discards only changes made after the latest successful Apply.
    """

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setObjectName('keyboardShortcutsDialog')
        self.setModal(True)
        self.setSizeGripEnabled(True)
        self.staged_profiles = deepcopy(controller.profiles)
        self._slots = self._make_slots(self.staged_profiles)
        self._invalid_sequences: dict[tuple[str, str, int], QKeySequence] = {}
        self._invalid_reasons: dict[tuple[str, str, int], object] = {}
        self._loading = False
        self._applied = False
        self._save_error = ''
        self._command_ids = list(COMMANDS)
        self._rows = {command_id: row for row, command_id in enumerate(self._command_ids)}

        layout = QVBoxLayout(self)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area.setWidget(body)
        layout.addWidget(self.scroll_area, 1)

        profile_row = QHBoxLayout()
        self.profile_label = QLabel()
        self.profile_combo = QComboBox()
        self.profile_combo.setObjectName('shortcutProfile')
        for profile in _PROFILE_LABELS:
            self.profile_combo.addItem('', profile)
        self.profile_combo.setCurrentIndex(self.profile_combo.findData(controller.profile))
        self.profile_label.setBuddy(self.profile_combo)
        profile_row.addWidget(self.profile_label)
        profile_row.addWidget(self.profile_combo, 1)
        body_layout.addLayout(profile_row)

        self.scope_label = self._wrapped_label()
        self.safety_label = self._wrapped_label()
        body_layout.addWidget(self.scope_label)
        body_layout.addWidget(self.safety_label)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName('shortcutSearch')
        self.search_edit.setClearButtonEnabled(True)
        body_layout.addWidget(self.search_edit)

        self.commands_table = QTableWidget(len(self._command_ids), 3)
        self.commands_table.setObjectName('shortcutCommands')
        self.commands_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.commands_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.commands_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.commands_table.setAlternatingRowColors(False)
        self.commands_table.setWordWrap(False)
        self.commands_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.commands_table.verticalHeader().hide()
        self.commands_table.horizontalHeader().setMinimumSectionSize(90)
        self.commands_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            self.commands_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.commands_table.setMinimumHeight(150)
        self.commands_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        for row, command_id in enumerate(self._command_ids):
            for column in range(3):
                item = QTableWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, command_id)
                self.commands_table.setItem(row, column, item)
        body_layout.addWidget(self.commands_table, 1)
        self.results_label = QLabel()
        body_layout.addWidget(self.results_label)

        self.command_label = self._wrapped_label()
        self.command_label.setProperty('sectionHeading', True)
        body_layout.addWidget(self.command_label)
        self.editor_panel = QWidget()
        editor_layout = QGridLayout(self.editor_panel)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        self.primary_label = QLabel()
        self.alternate_label = QLabel()
        self.primary_edit = self._sequence_editor('shortcutPrimary')
        self.alternate_edit = self._sequence_editor('shortcutAlternate')
        self.clear_primary_button = QPushButton()
        self.clear_alternate_button = QPushButton()
        self.primary_label.setBuddy(self.primary_edit)
        self.alternate_label.setBuddy(self.alternate_edit)
        for row, (label, editor, clear) in enumerate((
            (self.primary_label, self.primary_edit, self.clear_primary_button),
            (self.alternate_label, self.alternate_edit, self.clear_alternate_button),
        )):
            editor_layout.addWidget(label, row, 0)
            editor_layout.addWidget(editor, row, 1)
            editor_layout.addWidget(clear, row, 2)
        editor_layout.setColumnStretch(1, 1)
        body_layout.addWidget(self.editor_panel)
        self.recording_label = self._wrapped_label()
        body_layout.addWidget(self.recording_label)

        actions = QHBoxLayout()
        self.unbind_button = QPushButton()
        self.reset_button = QPushButton()
        actions.addWidget(self.unbind_button)
        actions.addStretch(1)
        actions.addWidget(self.reset_button)
        body_layout.addLayout(actions)
        self.error_label = self._wrapped_label()
        self.error_label.setObjectName('shortcutError')
        self.error_label.setStyleSheet('color: #f2a7a0;')
        self.error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body_layout.addWidget(self.error_label)
        self.status_label = self._wrapped_label()
        body_layout.addWidget(self.status_label)
        self.transaction_label = self._wrapped_label()
        body_layout.addWidget(self.transaction_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = self.buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        # Enter while recording or searching must not accidentally persist data.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        layout.addWidget(self.buttons)
        self.apply_button.clicked.connect(self.apply)
        self.buttons.rejected.connect(self.reject)
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        self.search_edit.textChanged.connect(self._filter_commands)
        self.commands_table.itemSelectionChanged.connect(self._load_selection)
        self.primary_edit.keySequenceChanged.connect(lambda sequence: self._edit_sequence(0, sequence))
        self.alternate_edit.keySequenceChanged.connect(lambda sequence: self._edit_sequence(1, sequence))
        self.clear_primary_button.clicked.connect(self.primary_edit.clear)
        self.clear_alternate_button.clicked.connect(self.alternate_edit.clear)
        self.unbind_button.clicked.connect(self._unbind)
        self.reset_button.clicked.connect(self._reset_profile)
        locale_events.changed.connect(self._retranslate)
        self.commands_table.selectRow(0)
        self._retranslate()
        self._load_selection()
        self.resize(900, 680)
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self.resize(min(self.width(), int(available.width() * .94)),
                        min(self.height(), int(available.height() * .92)))

    @staticmethod
    def _wrapped_label():
        label = QLabel()
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return label

    @staticmethod
    def _sequence_editor(name):
        editor = _ShortcutSequenceEdit()
        editor.setObjectName(name)
        editor.setMaximumSequenceLength(1)
        # Tab/Backtab keep their native focus-navigation role; every recorded
        # shortcut consists of a single simultaneous combination.
        editor.setMinimumWidth(110)
        return editor

    @staticmethod
    def _make_slots(profiles):
        return {profile: {command_id: [*(bindings.get(command_id, ())), '', ''][:2]
                          for command_id in COMMANDS}
                for profile, bindings in profiles.items()}

    @property
    def active_profile(self):
        return self.profile_combo.currentData()

    @property
    def selected_command_id(self):
        rows = self.commands_table.selectionModel().selectedRows()
        return self._command_ids[rows[0].row()] if rows else None

    def select_command(self, command_id):
        """Select a fixed command ID; useful to accessibility and UI tests."""
        row = self._rows[command_id]
        self.commands_table.selectRow(row)
        self.commands_table.scrollToItem(self.commands_table.item(row, 0))

    def _profile_changed(self, _index):
        self._save_error = ''
        self._refresh_rows()
        self._filter_commands()
        self._load_selection()
        self._update_status()

    def _refresh_rows(self):
        for command_id, row in self._rows.items():
            command = COMMANDS[command_id]
            item = self.commands_table.item(row, 0)
            item.setText(tr(command.label))
            item.setToolTip(f'{tr(command.category)} · {tr(command.label)}\n{command_id}')
            for slot in (0, 1):
                binding = self._slots[self.active_profile][command_id][slot]
                invalid = self._invalid_sequences.get((self.active_profile, command_id, slot))
                label = (invalid.toString(QKeySequence.SequenceFormat.NativeText) if invalid is not None
                         else display_binding(binding) if binding else tr('Unassigned'))
                item = self.commands_table.item(row, slot + 1)
                item.setText(label)
                item.setToolTip(label)
        self.commands_table.resizeRowsToContents()

    def _filter_commands(self, _text=None):
        terms = self.search_edit.text().casefold().split()
        visible = []
        for command_id, row in self._rows.items():
            command = COMMANDS[command_id]
            fields = [command_id, command.label, command.category,
                      EN.get(command.label, command.label), ZH_CN.get(command.label, ''),
                      ZH_CN.get(command.category, ''), ' '.join(self._slots[self.active_profile][command_id])]
            fields.extend(display_binding(binding) for binding in self._slots[self.active_profile][command_id]
                          if binding)
            haystack = ' '.join(fields).casefold()
            show = all(term in haystack for term in terms)
            self.commands_table.setRowHidden(row, not show)
            if show:
                visible.append(row)
        if not visible:
            self.commands_table.clearSelection()
        elif self.selected_command_id is None or self.commands_table.isRowHidden(
                self._rows[self.selected_command_id]):
            self.commands_table.selectRow(visible[0])
        self.results_label.setText(tr('{count} commands', count=len(visible)) if visible
                                   else tr('No commands match your search.'))
        self._update_command_label()

    def _load_selection(self):
        self._loading = True
        command_id = self.selected_command_id
        self.editor_panel.setEnabled(command_id is not None)
        self.unbind_button.setEnabled(command_id is not None)
        for slot, editor in enumerate((self.primary_edit, self.alternate_edit)):
            with QSignalBlocker(editor):
                if command_id is None:
                    editor.clear()
                else:
                    pending = self._invalid_sequences.get((self.active_profile, command_id, slot))
                    binding = self._slots[self.active_profile][command_id][slot]
                    editor.setKeySequence(pending if pending is not None else
                                          sequence_from_binding(binding) if binding else QKeySequence())
        self._loading = False
        self._update_command_label()
        self._update_status()

    def _update_command_label(self):
        command_id = self.selected_command_id
        if command_id is None:
            self.command_label.setText(tr('Select a command to edit its shortcuts.'))
        else:
            command = COMMANDS[command_id]
            self.command_label.setText(tr('{category} · {command}',
                                           category=tr(command.category), command=tr(command.label)))

    def _edit_sequence(self, slot, sequence):
        command_id = self.selected_command_id
        if self._loading or command_id is None:
            return
        key = (self.active_profile, command_id, slot)
        self._save_error = ''
        try:
            binding = binding_from_sequence(sequence) if not sequence.isEmpty() else ''
        except ValueError as exc:
            self._invalid_sequences[key] = QKeySequence(sequence)
            self._invalid_reasons[key] = exc.args[0] if exc.args else str(exc)
        else:
            self._invalid_sequences.pop(key, None)
            self._invalid_reasons.pop(key, None)
            self._slots[self.active_profile][command_id][slot] = binding
            self.staged_profiles[self.active_profile][command_id] = tuple(
                value for value in self._slots[self.active_profile][command_id] if value)
        self._refresh_rows()
        self._update_status()

    def _unbind(self):
        command_id = self.selected_command_id
        if command_id is None:
            return
        for slot in (0, 1):
            key = (self.active_profile, command_id, slot)
            self._invalid_sequences.pop(key, None)
            self._invalid_reasons.pop(key, None)
        self._slots[self.active_profile][command_id] = ['', '']
        self.staged_profiles[self.active_profile][command_id] = ()
        self._save_error = ''
        self._refresh_rows()
        self._load_selection()
        self._update_status()

    def _reset_profile(self):
        profile = self.active_profile
        self.staged_profiles[profile] = default_bindings(profile)
        self._slots[profile] = self._make_slots({profile: self.staged_profiles[profile]})[profile]
        self._invalid_sequences = {key: value for key, value in self._invalid_sequences.items()
                                   if key[0] != profile}
        self._invalid_reasons = {key: value for key, value in self._invalid_reasons.items()
                                 if key[0] != profile}
        self._save_error = ''
        self._refresh_rows()
        self._filter_commands()
        self._load_selection()
        self._update_status()

    @staticmethod
    def _error_text(error):
        return error.render() if hasattr(error, 'render') else tr(str(error))

    def _validation_error(self):
        for key, reason in self._invalid_reasons.items():
            return tr('{profile}: {error}', profile=tr(_PROFILE_LABELS[key[0]]),
                      error=tr('Invalid shortcut: {error}', error=self._error_text(reason)))
        # Check the selected preset first so errors appear next to the editing
        # context. Validate every preset, including non-active staged edits.
        profiles = [self.active_profile, *(p for p in self.staged_profiles if p != self.active_profile)]
        for profile in profiles:
            bindings = self.staged_profiles[profile]
            command_ids = list(bindings)
            if profile == self.active_profile and self.selected_command_id in command_ids:
                command_ids.remove(self.selected_command_id)
                command_ids.insert(0, self.selected_command_id)
            for command_id in command_ids:
                sequences = bindings[command_id]
                if len(set(sequences)) != len(sequences):
                    reason = tr('Primary and alternate must use different combinations.')
                    return tr('{profile}: {error}', profile=tr(_PROFILE_LABELS[profile]), error=reason)
                conflict = conflict_for(bindings, command_id, sequences)
                if conflict:
                    binding = next((value for value in sequences if value in bindings[conflict]), '')
                    reason = tr('{binding} is already assigned to {command}. Clear that assignment or choose another combination.',
                                binding=display_binding(binding), command=tr(COMMANDS[conflict].label))
                    return tr('{profile}: {error}', profile=tr(_PROFILE_LABELS[profile]), error=reason)
            try:
                validate_bindings(bindings)
            except ValueError as exc:
                return tr('{profile}: {error}', profile=tr(_PROFILE_LABELS[profile]),
                          error=tr('Invalid shortcut: {error}', error=tr(str(exc))))
        return ''

    def _is_dirty(self):
        return bool(self._invalid_sequences or self.staged_profiles != self.controller.profiles
                    or self.active_profile != self.controller.profile)

    def _update_status(self):
        error = self._validation_error()
        if self._save_error:
            error = tr('Could not save keyboard shortcuts. Your changes are still staged. {error}',
                       error=self._error_text(self._save_error))
        self.error_label.setText(error)
        self.error_label.setVisible(bool(error))
        dirty = self._is_dirty()
        self.apply_button.setEnabled(dirty and not self._validation_error())
        self.status_label.setText(tr('Unsaved shortcut changes.') if dirty else
                                  tr('Shortcut changes applied.') if self._applied else '')
        self.status_label.setVisible(bool(self.status_label.text()))

    def apply(self):
        """Persist a complete valid snapshot; keep the dialog open for more edits."""
        if self._validation_error():
            self._update_status()
            return False
        if not self._is_dirty():
            return True
        try:
            self.controller.apply_configuration(deepcopy(self.staged_profiles), self.active_profile)
        except (ValueError, OSError) as exc:
            self._save_error = exc.args[0] if exc.args else str(exc)
            self._update_status()
            return False
        self._save_error = ''
        self._applied = True
        self.staged_profiles = deepcopy(self.controller.profiles)
        self._slots = self._make_slots(self.staged_profiles)
        self._invalid_sequences.clear()
        self._invalid_reasons.clear()
        self._refresh_rows()
        self._load_selection()
        self._update_status()
        return True

    def reject(self):
        # Also discard on Escape/window-close and when the same dialog instance
        # is shown again. Successful Apply is already the new baseline.
        self.staged_profiles = deepcopy(self.controller.profiles)
        self._slots = self._make_slots(self.staged_profiles)
        self._invalid_sequences.clear()
        self._invalid_reasons.clear()
        self._save_error = ''
        with QSignalBlocker(self.profile_combo):
            self.profile_combo.setCurrentIndex(self.profile_combo.findData(self.controller.profile))
        self._refresh_rows()
        self._filter_commands()
        self._load_selection()
        self._update_status()
        super().reject()

    def _retranslate(self, _locale=None):
        self.setWindowTitle(tr('Keyboard shortcuts'))
        self.profile_label.setText(tr('Preset'))
        self.profile_combo.setAccessibleName(tr('Preset'))
        with QSignalBlocker(self.profile_combo):
            for index in range(self.profile_combo.count()):
                self.profile_combo.setItemText(index, tr(_PROFILE_LABELS[self.profile_combo.itemData(index)]))
        self.scope_label.setText(tr(_SCOPE))
        self.safety_label.setText(tr(_SAFETY))
        self.recording_label.setText(tr(_RECORDING))
        self.transaction_label.setText(tr(_TRANSACTION))
        self.search_edit.setPlaceholderText(tr('Search commands or keys (English / 中文)'))
        self.search_edit.setAccessibleName(tr('Search shortcuts'))
        self.commands_table.setAccessibleName(tr('Keyboard shortcuts'))
        self.commands_table.setHorizontalHeaderLabels([tr('Command'), tr('Primary'), tr('Alternate')])
        self.primary_label.setText(tr('Primary'))
        self.alternate_label.setText(tr('Alternate'))
        self.primary_edit.setAccessibleName(tr('Primary'))
        self.alternate_edit.setAccessibleName(tr('Alternate'))
        for editor in (self.primary_edit, self.alternate_edit):
            editor.refresh_placeholder()
        self.primary_edit.setToolTip(tr(_RECORDING))
        self.alternate_edit.setToolTip(tr(_RECORDING))
        self.clear_primary_button.setText(tr('Clear primary'))
        self.clear_alternate_button.setText(tr('Clear alternate'))
        self.unbind_button.setText(tr('Unbind command'))
        self.reset_button.setText(tr('Reset current preset'))
        self.reset_button.setToolTip(tr('Reset only this preset to its reference defaults. Apply to save the reset.'))
        self.apply_button.setText(tr('Apply'))
        self.cancel_button.setText(tr('Cancel'))
        self._refresh_rows()
        self._filter_commands()
        self._update_command_label()
        self._update_status()
        if self.selected_command_id is not None:
            self.commands_table.scrollToItem(self.commands_table.item(
                self._rows[self.selected_command_id], 0))

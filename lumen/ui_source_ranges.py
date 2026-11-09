"""Named source metadata UI; all fresh media checks use the owned source worker.

No remembered status is project data. Recall only changes this viewer's marks;
ordinary Insert/Overwrite must still build and verify its own single-use plan.
"""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSizePolicy
from .i18n import tr
from .i18n_widgets import QComboBox, QLineEdit, QFormLayout
from .source_range_verification import verify_source_range


class SavedSourceRangesMixin:
    def _build_saved_ranges(self, layout):
        self._range_states = {}
        self._range_request = None
        self.saved_range_choice = QComboBox()
        self.saved_range_choice.setMinimumWidth(0)
        self.saved_range_choice.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.saved_range_choice.setAccessibleName(tr('Saved source ranges'))
        self.saved_range_name = QLineEdit()
        self.saved_range_name.setMaxLength(128)
        self.saved_range_name.setAccessibleName(tr('Source range name'))
        self.saved_range_name.setPlaceholderText(tr('Name this source selection'))
        self.saved_range_name.setMinimumWidth(0)
        form = QFormLayout(); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.addRow(tr('Saved source ranges'), self.saved_range_choice)
        form.addRow(tr('Source range name'), self.saved_range_name)
        layout.addLayout(form)
        self.saved_range_note = self._label(''); layout.addWidget(self.saved_range_note)
        self.save_range_button = self._button('Save named range', self.save_named_range, layout)
        self.recall_range_button = self._button('Recall range to marks', self.recall_named_range, layout)
        self.rename_range_button = self._button('Rename saved range', self.rename_named_range, layout)
        self.delete_range_button = self._button('Delete saved range', self.delete_named_range, layout)
        self.saved_range_contract = self._label(tr('Saved ranges are project metadata, not clips or proxies. Recall rechecks the source and requires exact current-frame boundaries; it never rounds, clamps or rebinds. Missing or changed sources keep their saved ranges. Apply still requires its own bounded proof.'))
        layout.addWidget(self.saved_range_contract)
        self.saved_range_choice.currentIndexChanged.connect(self._saved_range_selected)
        self.saved_range_name.textChanged.connect(self._saved_range_name_changed)
        self._refresh_saved_ranges()

    def _selected_saved_range(self):
        if not hasattr(self, 'saved_range_choice'):
            return None
        selected = self.saved_range_choice.currentData()
        return next((entry for entry in self.window.project.source_ranges
                     if entry.id == selected and entry.media_id == self.session.media_id), None)

    def _saved_range_token(self):
        if not hasattr(self, 'saved_range_choice'):
            return None
        return (self._selected_saved_range(), self.saved_range_name.text())

    def _range_metadata_usable(self):
        return (not self._closed_flag and not self.window.closing
                and tuple(self.window.selected_media_ids()) == (self.session.media_id,)
                and any(media.id == self.session.media_id for media in self.window.project.media))

    def _refresh_saved_ranges(self, selected_id=None):
        if not hasattr(self, 'saved_range_choice'):
            return
        selected = self.saved_range_choice.currentData() if selected_id is None else selected_id
        records = [entry for entry in self.window.project.source_ranges
                   if entry.media_id == self.session.media_id]
        self.saved_range_choice.blockSignals(True)
        self.saved_range_choice.clear()
        self.saved_range_choice.addItem(tr('New named range'), None)
        for entry in records:
            # QComboBox uses a plain-text item delegate. Never pass user names
            # through tr() or a QLabel's automatic rich-text detection.
            self.saved_range_choice.addItem(entry.name, entry.id)
        index = self.saved_range_choice.findData(selected)
        self.saved_range_choice.setCurrentIndex(max(0, index))
        self.saved_range_choice.blockSignals(False)
        self._refresh_saved_range_note()

    def _refresh_saved_range_note(self):
        entry = self._selected_saved_range()
        if entry is None:
            self.saved_range_note.setText(tr('Choose a saved range, or name the current In/Out selection.'))
            return
        state = self._range_states.get(entry.id)
        status = (state[1] if state is not None and state[0] == entry else
                  'Unavailable; original bounds preserved' if self.session.total_frames < 1 else
                  'Unverified this session')
        self.saved_range_note.setText(tr('Saved: [{start}, {end}) seconds · captured at {fps} fps · {status}',
            start=f'{entry.in_num}/{entry.in_den}', end=f'{entry.out_num}/{entry.out_den}',
            fps=f'{entry.capture_fps_num}/{entry.capture_fps_den}', status=tr(status)))
        self.saved_range_note.setAccessibleDescription(self.saved_range_note.text())

    def _saved_range_selected(self, *_):
        self.invalidate()
        entry = self._selected_saved_range()
        self.saved_range_name.blockSignals(True)
        self.saved_range_name.setText(entry.name if entry else '')
        self.saved_range_name.blockSignals(False)
        self._refresh_saved_range_note(); self._update_controls()

    def _saved_range_name_changed(self, *_):
        # Captured names are part of request currency: editing a name while a
        # save is pending cannot silently save the previous text.
        self.invalidate(); self._update_controls()

    def _invalidate_saved_ranges(self):
        if hasattr(self, '_range_states'):
            self._range_states.clear()
            self._range_request = None
            self._refresh_saved_range_note()

    def _update_saved_range_controls(self):
        entry = self._selected_saved_range()
        metadata = self._range_metadata_usable() and not self._applying
        usable = self._usable() and not self._applying and self.player is None
        self.saved_range_choice.setEnabled(metadata)
        self.saved_range_name.setEnabled(metadata)
        self.save_range_button.setEnabled(usable and bool(self.saved_range_name.text().strip()))
        self.recall_range_button.setEnabled(usable and entry is not None)
        self.rename_range_button.setEnabled(metadata and entry is not None
                                            and bool(self.saved_range_name.text().strip()))
        self.delete_range_button.setEnabled(metadata and entry is not None)

    def save_named_range(self):
        if not self._usable() or self._applying or self.player is not None:
            return
        name = self.saved_range_name.text()
        if not name.strip():
            self._set_error('Source range name must contain 1..128 plain-text characters'); return
        self.invalidate()
        snapshot = deepcopy(self.session)
        def accept(result, token):
            if not self._current(token) or not result.is_current(self.session, self.window.project):
                return
            # Consume before the central transaction's refresh can reenter.
            self._range_request = None
            saved = self.window.edit(lambda: self.window.editor.save_source_range(
                snapshot.media_id, name, result.in_frame, result.out_frame, result.binding))
            self._observed = self._context()
            if saved is not None:
                self._range_states[saved.id] = (saved, 'Verified now; Apply rechecks')
                self._refresh_saved_ranges(saved.id)
                self._set_status(tr('Named range saved. Undo removes this metadata only; timeline clips are unchanged.'))
            self._update_controls()
        self._submit('Checking source before saving range…',
            lambda cancel, progress: verify_source_range(self.backend, snapshot, cancel=cancel), accept)

    def recall_named_range(self):
        entry = self._selected_saved_range()
        if entry is None or not self._usable() or self._applying or self.player is not None:
            return
        self.invalidate()
        snapshot = deepcopy(self.session)
        token = self._token()
        self._range_request = (entry, token)
        def accept(result, accepted_token):
            if (not self._current(accepted_token) or self._selected_saved_range() != entry
                    or not result.is_current(self.session, self.window.project)):
                return
            self._range_request = None
            self.session.in_frame, self.session.out_frame = result.in_frame, result.out_frame
            self.session.cursor = result.in_frame
            self.session.generation += 1
            self.identity = None; self.canvas.clear(); self._clear_plan()
            self._range_states[entry.id] = (entry, 'Verified now; Apply rechecks')
            self._sync_cursor(); self._refresh_text(); self._update_controls()
            self._set_status(tr('Saved range recalled to marks only. Inspect or preview Insert/Overwrite; no timeline edit was made.'))
        self._submit('Rechecking saved source range…',
            lambda cancel, progress: verify_source_range(self.backend, snapshot, record=entry, cancel=cancel), accept)

    def _saved_range_failed(self, token):
        request = self._range_request
        self._range_request = None
        if request is not None and request[1] == token and self._selected_saved_range() == request[0]:
            entry = request[0]
            self._range_states[entry.id] = (entry, 'Unavailable; original bounds preserved')
            self._refresh_saved_range_note()

    def rename_named_range(self):
        entry = self._selected_saved_range()
        if entry is None or not self._range_metadata_usable() or self._applying:
            return
        name = self.saved_range_name.text()
        self.invalidate()
        changed = self.window.edit(lambda: self.window.editor.rename_source_range(entry.id, name))
        self._observed = self._context()
        self._refresh_saved_ranges(entry.id); self._update_controls()
        if changed is not None:
            self._set_status(tr('Saved range renamed. Timeline clips are unchanged.'))

    def delete_named_range(self):
        entry = self._selected_saved_range()
        if entry is None or not self._range_metadata_usable() or self._applying:
            return
        self.invalidate()
        before = self.window.editor.revision
        self.window.edit(lambda: self.window.editor.delete_source_range(entry.id))
        self._observed = self._context()
        self._refresh_saved_ranges()
        if self.window.editor.revision != before:
            self.saved_range_name.blockSignals(True); self.saved_range_name.clear(); self.saved_range_name.blockSignals(False)
            self._set_status(tr('Saved range deleted. Existing clips are independent and unchanged. Undo restores the range.'))
        self._update_controls()

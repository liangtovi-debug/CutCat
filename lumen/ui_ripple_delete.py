"""Per-operation ripple participation, staged from one committed clip interval.

Planning is in-memory and decodes no media. The old immediate current-track
operation remains separate; this preview path is intentionally conservative
about touched transitions. Choices are never persistent Sync Lock metadata.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from .i18n import tr, tr_error, locale_events
from .i18n_widgets import QDialog, QScrollArea, QWidget, QCheckBox
from .source_overwrite import _signature
from .ui_interval_preview import plain_label, action_button, fit_dialog, impact_text


class RippleDeleteDialog(QDialog):
    def __init__(self, window, clip):
        super().__init__(window)
        self.window = window
        self.selected_clip_id = clip.id
        self.track, self.start, self.end = clip.track, clip.start, clip.end
        self._closed_flag = self._stale = self._applying = False
        self._generation = 0
        self._last_error = None
        self.plan = self.plan_context = None
        self.context = self._context()
        self.setWindowTitle(tr('Ripple delete · participants'))
        self.setModal(False)
        self.resize(660, 620)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); body = QVBoxLayout(content)
        self.scope = self._label(tr('Delete only the selected clip. Participants ending at or before its start stay fixed; participants starting at or after its end move left. Any other intersection blocks the entire edit, including items wholly inside the interval.'))
        self.selection_note = self._label(''); body.addWidget(self.selection_note)
        self.track_checks = []
        for track in range(4):
            check = QCheckBox(tr('Clip track {track}', track=track + 1))
            check.setAccessibleName(tr('Clip track {track}', track=track + 1))
            check.setChecked(track == self.track)
            check.setEnabled(track != self.track)
            check.setToolTip(tr('The selected clip’s track always participates.') if track == self.track else tr('Move only this track’s downstream clips; any interval intersection blocks the edit.'))
            check.toggled.connect(self.choices_changed)
            body.addWidget(check); self.track_checks.append(check)
        self.follow_captions_check = QCheckBox(tr('Follow captions'))
        self.follow_stickers_check = QCheckBox(tr('Follow stickers'))
        for check, label in ((self.follow_captions_check, 'Follow captions'),
                             (self.follow_stickers_check, 'Follow stickers')):
            check.setAccessibleName(tr(label))
            check.toggled.connect(self.choices_changed); body.addWidget(check)
        self.timing_note = self._label(tr('Uses the committed clip interval; staged inspector timing is not used.'))
        body.addWidget(self.timing_note); body.addWidget(self.scope)
        self.warning = self._label(tr('Unchecked tracks, captions and stickers stay fixed and can lose synchronization. Captions do not move speech audio automatically; video does not create linked A/V.'))
        body.addWidget(self.warning)
        self.proof_note = self._label(tr('Moved audible clips must retain the full local audio compilation. Multiple participants also require one common integer sample displacement. Affected groups, locked changed tracks and touched transitions block this preview mode.'))
        body.addWidget(self.proof_note)
        self.transaction = self._label(tr('Per-operation choices only, not persistent Sync Lock. No changes until Apply. One Undo restores all changed collections. Total extent is recomputed from every surviving item.'))
        body.addWidget(self.transaction)
        self.impact = self._label(''); self.impact.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        body.addWidget(self.impact)
        self.status = self._label(''); body.addWidget(self.status); body.addStretch()
        self.scroll.setWidget(content); layout.addWidget(self.scroll, 1)
        row = QHBoxLayout()
        self.preview_button = self._button('Preview', self.preview_delete, row)
        self.apply_button = self._button('Apply', self.apply_delete, row)
        self.cancel_button = self._button('Cancel', self.reject, row)
        layout.addLayout(row)
        self.finished.connect(self._closed)
        locale_events.changed.connect(self.refresh_text)
        self.timer = QTimer(self); self.timer.setInterval(150)
        self.timer.timeout.connect(self.check_current); self.timer.start()
        self.refresh_text(); self.check_current()

    _label = staticmethod(plain_label)

    def _button(self, source, callback, row):
        return action_button(source, callback, row)

    def showEvent(self, event):
        super().showEvent(event); self.fit_available_screen()

    def fit_available_screen(self, available=None):
        fit_dialog(self, available)

    def _context(self):
        w = self.window
        # A generation catches selection-away-and-back even between timer ticks;
        # full project bytes catch metadata changes bypassing normal revisions.
        return (id(w.editor), w.editor.session_id, w.editor.revision, id(w.project),
                w.revision, w.selection_generation, w.selected_clip_ids,
                w.active_selection_type, _signature(w.project))

    def _participation(self):
        return (tuple(track for track, check in enumerate(self.track_checks)
                      if check.isChecked() or track == self.track),
                self.follow_captions_check.isChecked(), self.follow_stickers_check.isChecked())

    def _token(self):
        return self._generation, self._context(), self._participation()

    def choices_changed(self, *_):
        if not self.track_checks[self.track].isChecked():
            check = self.track_checks[self.track]
            check.blockSignals(True); check.setChecked(True); check.blockSignals(False)
        self._generation += 1
        self.plan = self.plan_context = None; self._last_error = None
        self.check_current(); self.refresh_text()

    def check_current(self):
        if self._closed_flag or self._applying:
            return
        if self.window.closing:
            self.reject(); return
        try:
            self._stale = self._stale or self.context != self._context()
        except Exception:
            self._stale = True
        if self._stale:
            self.plan = self.plan_context = None
        busy = bool(self.window.jobs)
        for track, check in enumerate(self.track_checks):
            check.setEnabled(not self._stale and track != self.track)
        for check in (self.follow_captions_check, self.follow_stickers_check):
            check.setEnabled(not self._stale)
        self.preview_button.setEnabled(not self._stale and not busy)
        self.apply_button.setEnabled(not self._stale and not busy and self.plan is not None
                                     and self.plan_context == self._token())
        if self._stale:
            self.status.setText(tr('Selection or project changed. Close and reopen Ripple delete before applying.'))
        elif busy:
            self.status.setText(tr('Wait for the current task to finish or cancel it before previewing or applying ripple deletion.'))
        else:
            self.status.setText(tr('Preview ready. Apply deletes only the selected clip in one undoable edit.') if self.plan is not None
                                else tr('Draft only. Preview the exact fixed, moved and deleted items before Apply.'))

    def preview_delete(self):
        self.check_current()
        if self._closed_flag or not self.preview_button.isEnabled():
            return
        self._generation += 1; self.plan = self.plan_context = None; self._last_error = None
        tracks, captions, stickers = self._participation()
        try:
            self.plan = self.window.editor.plan_ripple_delete(self.selected_clip_id,
                participating_tracks=tracks, follow_captions=captions, follow_stickers=stickers)
            self.plan_context = self._token()
        except Exception as exc:
            self._last_error = str(exc)
        self.refresh_text(); self.check_current()

    def refresh_text(self, *_):
        if self._closed_flag:
            return
        self.selection_note.setText(tr('Selected clip: {clip_id}\nCommitted [{start}, {end}) · {duration} frames · track {track}.',
            clip_id=self.selected_clip_id, start=self.start, end=self.end,
            duration=self.end - self.start, track=self.track + 1))
        if self._last_error is not None:
            self.impact.setText(tr('Ripple delete blocked:\n{error}', error=tr_error(self._last_error)))
        elif self.plan is not None:
            self.impact.setText(impact_text(self.plan.item_impacts))
        else:
            self.impact.setText(tr('No current preview. Changing any participant clears the previous preview.'))
        self.check_current()

    def apply_delete(self):
        self.check_current()
        if self._closed_flag or not self.apply_button.isEnabled():
            return
        plan = self.plan
        # Clear the UI capability before the central commit, including nested
        # refresh/repeated activation. A failed commit requires a new preview.
        self.plan = self.plan_context = None
        self.apply_button.setEnabled(False); self._applying = True
        w = self.window; before = (w.editor, w.editor.revision)
        try:
            w.edit(lambda: w.editor.apply_ripple_delete(plan))
        finally:
            self._applying = False
        if before != (w.editor, w.editor.revision):
            self.accept()
        else:
            self.check_current(); self.refresh_text()

    def _closed(self, *_):
        self._closed_flag = True
        self._generation += 1; self.plan = self.plan_context = None
        self.timer.stop()


def open_ripple_delete(window):
    if window.closing or window.active_selection_type != 'clip':
        return None
    clip = window.selected_clip()
    if clip is None or window.selection_edit_locked():
        return None
    previous = getattr(window, 'ripple_delete_dialog', None)
    if previous is not None:
        previous.reject(); previous.deleteLater()
    dialog = RippleDeleteDialog(window, clip)
    window.ripple_delete_dialog = dialog; dialog.show()
    return dialog


def close_ripple_delete(window):
    dialog = getattr(window, 'ripple_delete_dialog', None)
    if dialog is not None:
        dialog.reject()

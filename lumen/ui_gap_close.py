"""One explicitly targeted interior gap; captured playhead never follows playback.

No selected-track state or importer destination is inferred. All planning is
in-memory and the commit capability is discarded before applying or dismissing.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout
from .i18n import tr, tr_error, locale_events
from .i18n_widgets import QDialog, QScrollArea, QWidget, QCheckBox, QComboBox
from .source_overwrite import _signature
from .ui_interval_preview import plain_label, action_button, fit_dialog, impact_text


class GapCloseDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.track = self.playhead = None
        self.plan = self.plan_context = None
        self._generation = 0
        self._closed_flag = self._stale = self._applying = False
        self._last_error = None
        self.context = self._context()
        self.setWindowTitle(tr('Close one gap'))
        self.setModal(False); self.resize(660, 650)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); body = QVBoxLayout(content)
        self.scope = plain_label(tr('Close one interior gap on an explicit target track. Only the empty interval between two adjacent real clips is removed; no clip is deleted. Leading or trailing empty space is not a gap for this tool.'))
        body.addWidget(self.scope)
        body.addWidget(plain_label(tr('Target gap track')))
        self.target_track = QComboBox()
        self.target_track.setAccessibleName(tr('Target gap track'))
        self.target_track.setMinimumWidth(0)
        self.target_track.addItem(tr('Choose gap track…'), None)
        for track in range(4):
            self.target_track.addItem(tr('Clip track {track}', track=track + 1), track)
        self.target_track.currentIndexChanged.connect(self.target_changed)
        body.addWidget(self.target_track)
        capture_row = QHBoxLayout()
        self.capture_button = action_button('Capture playhead', self.capture_playhead, capture_row)
        body.addLayout(capture_row)
        self.capture_note = plain_label(''); body.addWidget(self.capture_note)
        self.timing_note = plain_label(tr('Capture freezes the playhead for this operation. Moving the live playhead or selecting clips does not retarget it. Changing the target track clears the capture; capturing again clears the preview. Staged inspector timing is not used.'))
        body.addWidget(self.timing_note)
        self.track_checks = []
        for track in range(4):
            check = QCheckBox(tr('Clip track {track}', track=track + 1))
            check.setAccessibleName(tr('Clip track {track}', track=track + 1))
            check.setToolTip(tr('The target gap track always participates; other tracks are optional.'))
            check.toggled.connect(self.choices_changed)
            body.addWidget(check); self.track_checks.append(check)
        self.follow_captions_check = QCheckBox(tr('Follow captions'))
        self.follow_stickers_check = QCheckBox(tr('Follow stickers'))
        for check, label in ((self.follow_captions_check, 'Follow captions'),
                             (self.follow_stickers_check, 'Follow stickers')):
            check.setAccessibleName(tr(label)); check.toggled.connect(self.choices_changed)
            body.addWidget(check)
        self.boundary_note = plain_label(tr('Participants ending at or before the gap start stay fixed. Participants starting at or after the gap end move left by the gap length. Any intersection blocks the edit, including an item wholly inside the gap.'))
        body.addWidget(self.boundary_note)
        self.warning = plain_label(tr('Unchecked tracks, captions and stickers stay fixed and can lose synchronization. Captions do not move speech audio automatically; video does not create linked A/V.'))
        body.addWidget(self.warning)
        self.proof_note = plain_label(tr('Every moved audible clip must retain its full local audio compilation and the same exact integer sample displacement, even on one track. Changed locked tracks, affected groups and moving transition endpoints block the edit.'))
        body.addWidget(self.proof_note)
        self.transaction = plain_label(tr('Per-operation choices only, not persistent Sync Lock. No changes until Apply. One Undo restores all changed collections. Total extent is recomputed from every surviving item.'))
        body.addWidget(self.transaction)
        self.impact = plain_label('')
        self.impact.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        body.addWidget(self.impact)
        self.status = plain_label(''); body.addWidget(self.status); body.addStretch()
        self.scroll.setWidget(content); layout.addWidget(self.scroll, 1)
        row = QHBoxLayout()
        self.preview_button = action_button('Preview', self.preview_close, row)
        self.apply_button = action_button('Apply', self.apply_close, row)
        self.cancel_button = action_button('Cancel', self.reject, row)
        layout.addLayout(row)
        self.finished.connect(self._closed)
        locale_events.changed.connect(self.refresh_text)
        self.timer = QTimer(self); self.timer.setInterval(150)
        self.timer.timeout.connect(self.check_current); self.timer.start()
        self.refresh_text()

    def showEvent(self, event):
        super().showEvent(event); self.fit_available_screen()

    def fit_available_screen(self, available=None):
        fit_dialog(self, available)

    def _context(self):
        w = self.window
        # Selection and live playhead intentionally do not own a captured target.
        return (id(w.editor), w.editor.session_id, w.editor.revision,
                id(w.project), w.revision, _signature(w.project))

    def _participation(self):
        return (tuple(t for t, check in enumerate(self.track_checks)
                      if check.isChecked() or t == self.track),
                self.follow_captions_check.isChecked(), self.follow_stickers_check.isChecked())

    def _token(self):
        return self._generation, self._context(), self.track, self.playhead, self._participation()

    def _clear_preview(self):
        self._generation += 1
        self.plan = self.plan_context = None; self._last_error = None

    def target_changed(self, *_):
        self.track = self.target_track.currentData(); self.playhead = None
        for track, check in enumerate(self.track_checks):
            check.blockSignals(True); check.setChecked(track == self.track); check.blockSignals(False)
        self._clear_preview(); self.refresh_text()

    def capture_playhead(self):
        self.check_current()
        if self._closed_flag or not self.capture_button.isEnabled():
            return
        self.playhead = self.window.timeline.playhead
        self._clear_preview(); self.refresh_text()

    def choices_changed(self, *_):
        if self.track is not None and not self.track_checks[self.track].isChecked():
            check = self.track_checks[self.track]
            check.blockSignals(True); check.setChecked(True); check.blockSignals(False)
        self._clear_preview(); self.refresh_text()

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
            self.impact.setText(tr('No current preview. Changing any participant clears the previous preview.'))
        busy = bool(self.window.jobs)
        self.target_track.setEnabled(not self._stale)
        self.capture_button.setEnabled(not self._stale and not busy and self.track is not None)
        for track, check in enumerate(self.track_checks):
            check.setEnabled(not self._stale and self.track is not None and track != self.track)
        for check in (self.follow_captions_check, self.follow_stickers_check):
            check.setEnabled(not self._stale and self.track is not None)
        self.preview_button.setEnabled(not self._stale and not busy and self.playhead is not None)
        self.apply_button.setEnabled(not self._stale and not busy and self.plan is not None
                                     and self.plan_context == self._token())
        if self._stale:
            text = tr('Project changed. Close and reopen Close one gap before applying.')
        elif busy:
            text = tr('Wait for the current task to finish or cancel it before capturing, previewing or closing a gap.')
        elif self.plan is not None:
            text = tr('Preview ready. Apply closes only this captured gap in one undoable edit. Deleted clips: 0.')
        else:
            text = tr('Choose a target track, capture the playhead inside an interior gap, then Preview before Apply.')
        self.status.setText(text)

    def preview_close(self):
        self.check_current()
        if self._closed_flag or not self.preview_button.isEnabled():
            return
        self._clear_preview()
        tracks, captions, stickers = self._participation()
        try:
            self.plan = self.window.editor.plan_gap_close(self.track, self.playhead,
                participating_tracks=tracks, follow_captions=captions, follow_stickers=stickers)
            self.plan_context = self._token()
        except Exception as exc:
            self._last_error = str(exc)
        self.refresh_text()

    def refresh_text(self, *_):
        if self._closed_flag:
            return
        if self.playhead is None:
            self.capture_note.setText(tr('No captured target. Choose the gap track explicitly; the media import track and selected clips are not used.'))
        else:
            self.capture_note.setText(tr('Captured target · clip track {track} · playhead frame {playhead}.',
                track=self.track + 1, playhead=self.playhead))
        if self._last_error is not None:
            self.impact.setText(tr('Gap close blocked:\n{error}', error=tr_error(self._last_error)))
        elif self.plan is not None:
            plan = self.plan
            summary = tr('Gap [{start}, {end}) · {duration} frames\nLeft clip: {left}\nRight clip: {right}\nDeleted clips: 0',
                start=plan.start, end=plan.end, duration=plan.duration,
                left=plan.left_clip_id, right=plan.right_clip_id)
            self.impact.setText(summary + '\n\n' + impact_text(plan.item_impacts))
        else:
            self.impact.setText(tr('No current preview. Changing any participant clears the previous preview.'))
        self.check_current()

    def apply_close(self):
        self.check_current()
        if self._closed_flag or not self.apply_button.isEnabled():
            return
        plan = self.plan
        self.plan = self.plan_context = None
        self.apply_button.setEnabled(False); self._applying = True
        w = self.window; before = w.editor, w.editor.revision
        try:
            w.edit(lambda: w.editor.apply_gap_close(plan))
        finally:
            self._applying = False
        if before != (w.editor, w.editor.revision):
            self.accept()
        else:
            self.refresh_text()

    def _closed(self, *_):
        self._closed_flag = True; self._clear_preview(); self.timer.stop()


def open_gap_close(window):
    if window.closing:
        return None
    previous = getattr(window, 'gap_close_dialog', None)
    if previous is not None:
        previous.reject(); previous.deleteLater()
    dialog = GapCloseDialog(window)
    window.gap_close_dialog = dialog; dialog.show()
    return dialog


def close_gap_close(window):
    dialog = getattr(window, 'gap_close_dialog', None)
    if dialog is not None:
        dialog.reject()

"""Window-private whole-clip clipboard with cancellable dependency checks.

Only immutable requests cross to workers. Every result is checked against the
current owner/session/revision and user intent before the GUI can accept it.
No native clipboard API is read or written here.
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QBoxLayout

from .clip_copy_paste import (prepare_clip_copy, verify_clip_copy, accept_clip_copy,
    plan_clip_paste, verify_clip_paste, apply_clip_paste)
from .i18n import tr, tr_error, locale_events
from .i18n_widgets import QDialog, QScrollArea, QWidget, QLineEdit
from .source_overwrite import _signature
from .ui_interval_preview import plain_label, action_button, fit_dialog

COPY_HELP = ('Edit → Copy clips keeps committed whole clips privately in this window and project session. '
             'Paste clips at playhead keeps their original tracks and relative gaps, with new clip and group IDs. '
             'Copy does not change the project or system clipboard. Source edits do not change the saved parameter snapshot. '
             'Media and LUT dependencies must remain available and unchanged. New/Open/Recover clears the copy. '
             'Locked targets, collisions, transition endpoints and incompatible audio sample phases are rejected; '
             'nothing is overwritten, rippled or automatically repositioned. Captions, stickers, source ranges and timeline markers stay fixed.')
PASTE_SCOPE = ('Paste complete copied clips on their original tracks, keeping relative gaps. '
               'The destination starts at the captured playhead; you may enter an exact project frame. Existing content stays fixed; '
               'collisions, locked tracks or incompatible audio sample phases block the entire edit.')
PASTE_DEPENDENCIES = ('This is a frozen parameter snapshot, not a media-file copy. '
                      'Media and LUT dependencies are checked again on a cancellable worker. '
                      'Captions, stickers, source ranges and timeline markers are not copied or moved.')


def _editor_context(window):
    return (id(window.editor), window.editor.session_id, window.editor.revision,
            id(window.project), window.revision, _signature(window.project))


def _finish_clipboard_job(window, job, generation, outcome):
    """A consumed/stale result still needs terminal UI, but owns no newer job.

    Connected after the shared job_finished slot has removed this job. Do not
    change that generic lifecycle or infer ownership merely from an empty set.
    """
    if window.closing or generation != window._job_generation or window.jobs:
        return
    if outcome['completed']:
        text = tr('Ready · local files only'); progress = 100
    elif job.cancel.is_set():
        text = tr('Canceled · sources unchanged'); progress = 0
    elif outcome['failed']:
        text = tr('Clip dependency check failed · project unchanged'); progress = 0
    else:
        text = tr('Clip operation expired · project unchanged'); progress = 0
    window.progress_label.setText(text)
    window.progress_bar.setValue(progress)


class ClipClipboardMixin:
    def init_clip_clipboard(self):
        self._clip_clipboard = None
        self._clip_clipboard_session = (id(self.editor), self.editor.session_id)
        self._clip_copy_generation = 0
        self.clip_copy_job = None
        self.clip_paste_job = None
        self.clip_paste_dialog = None

    def refresh_clip_clipboard(self):
        current = (id(self.editor), self.editor.session_id)
        if current != self._clip_clipboard_session:
            self.cancel_clip_clipboard_work(close=True)
            self._clip_clipboard = None
            self._clip_clipboard_session = current
        dialog = self.clip_paste_dialog
        if dialog is not None:
            dialog.check_current()

    def cancel_clip_clipboard_work(self, *, close=False):
        self._clip_copy_generation += 1
        if self.clip_copy_job is not None:
            self.clip_copy_job.cancel.set()
        dialog = self.clip_paste_dialog
        if dialog is not None:
            if close:
                dialog.reject()
            else:
                dialog.cancel_work()

    def can_copy_selected_clips(self):
        return (not self.closing and not self.jobs and self.active_selection_type == 'clip'
                and bool(self.selected_clip_ids)
                and set(self.selected_clip_ids) <= {clip.id for clip in self.project.clips})

    def can_paste_copied_clips(self):
        clipboard = self._clip_clipboard
        return (not self.closing and not self.jobs and clipboard is not None
                and self._clip_clipboard_session == (id(self.editor), self.editor.session_id)
                and clipboard.session_id == self.editor.session_id)

    def _clip_clipboard_focus_blocked(self):
        app = QApplication.instance()
        return (self._clip_group_focus_blocked()
                or (self.isVisible() and app.activeWindow() is not self))

    def copy_selected_clips(self):
        self.refresh_clip_clipboard()
        if self._clip_clipboard_focus_blocked() or not self.can_copy_selected_clips():
            return
        self.timeline.cancel_gestures()
        try:
            request = prepare_clip_copy(self.editor, self.selected_clip_ids)
        except Exception as exc:
            self.statusBar().showMessage(tr('Cannot copy clips: {error}', error=tr_error(exc)), 12000)
            return
        self._clip_copy_generation += 1
        generation = self._clip_copy_generation
        context = (_editor_context(self), self.selection_generation, self.selected_clip_ids)
        outcome = {'completed': False, 'failed': False}

        def current():
            return (not self.closing and generation == self._clip_copy_generation
                    and context == (_editor_context(self), self.selection_generation,
                                    self.selected_clip_ids))

        def accepted(value):
            # Failed or stale copies never replace the last successful snapshot.
            try:
                copied = accept_clip_copy(self.editor, request, value)
            except Exception as exc:
                outcome['failed'] = True
                self.statusBar().showMessage(tr('Cannot copy clips: {error}', error=tr_error(exc)), 12000)
                return
            self._clip_clipboard = copied
            outcome['completed'] = True
            self.statusBar().showMessage(tr('Copied {count} clips privately. Move the playhead, then paste on original tracks.',
                                           count=copied.clip_count), 12000)
            self.shortcuts.update_enabled()

        def setup(job):
            job_generation = self._job_generation
            job.failed.connect(lambda *_: outcome.update(failed=True))
            job.finished.connect(lambda: _finish_clipboard_job(self, job, job_generation, outcome))

        self.statusBar().showMessage(tr('Checking copied clip dependencies… Cancel job keeps the previous copy.'))
        return self.start_job(tr('Copy clips'),
            lambda cancel, progress: verify_clip_copy(request, cancel=cancel),
            accepted, 'clip_copy', before_start=setup, is_current=current)

    def paste_copied_clips(self):
        self.refresh_clip_clipboard()
        if self._clip_clipboard_focus_blocked() or not self.can_paste_copied_clips():
            return
        self.timeline.cancel_gestures()
        if self.clip_paste_dialog is not None:
            self.clip_paste_dialog.show(); self.clip_paste_dialog.raise_()
            self.clip_paste_dialog.activateWindow()
            return self.clip_paste_dialog
        dialog = ClipPasteDialog(self)
        self.clip_paste_dialog = dialog
        dialog.show()
        return dialog


class ClipPasteDialog(QDialog):
    """Captured destination, explicit Paste, no implicit overwrite or placement."""
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.clipboard = window._clip_clipboard
        self.context = _editor_context(window)
        self.destination = self.captured_playhead = window.timeline.playhead
        self.plan = None
        self.job = None
        self._generation = 0
        self._closed_flag = self._stale = self._applying = False
        self._last_error = None
        self.setModal(False); self.resize(620, 470)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); body = QVBoxLayout(content)
        self.scope = plain_label(tr(PASTE_SCOPE)); body.addWidget(self.scope)
        self.target = plain_label(''); body.addWidget(self.target)
        self.destination_label = plain_label(tr('Paste destination (project frame)'))
        body.addWidget(self.destination_label)
        self.destination_edit = QLineEdit(str(self.destination))
        self.destination_edit.setMaxLength(128); self.destination_edit.setMinimumWidth(0)
        self.destination_edit.setAccessibleName(tr('Paste destination (project frame)'))
        self.destination_edit.setToolTip(tr('Exact non-negative project frame. No snapping or automatic repositioning.'))
        self.destination_label.setBuddy(self.destination_edit)
        self.destination_edit.textChanged.connect(self.destination_changed)
        body.addWidget(self.destination_edit)
        self.dependencies = plain_label(tr(PASTE_DEPENDENCIES)); body.addWidget(self.dependencies)
        self.private_note = plain_label(tr('Window-private clipboard · same project session only · system clipboard unchanged.'))
        body.addWidget(self.private_note)
        self.impact = plain_label('')
        self.impact.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        body.addWidget(self.impact)
        self.status = plain_label(''); body.addWidget(self.status); body.addStretch()
        self.scroll.setWidget(content); layout.addWidget(self.scroll, 1)
        capture_row = QHBoxLayout()
        self.capture_button = action_button('Capture playhead', self.capture_playhead, capture_row)
        body.insertLayout(4, capture_row)
        row = self.footer_row = QHBoxLayout()
        self.paste_button = action_button('Paste clips', self.paste, row)
        self.cancel_button = action_button('Cancel', self.reject, row)
        layout.addLayout(row)
        self._footer_pending = False
        for button in (self.paste_button, self.cancel_button):
            button.installEventFilter(self)
        self.finished.connect(self._closed)
        locale_events.changed.connect(self.refresh_text)
        self.timer = QTimer(self); self.timer.setInterval(150)
        self.timer.timeout.connect(self.check_current); self.timer.start()
        self._prepare_plan()
        self.refresh_text()

    def _fit_footer(self):
        self._footer_pending = False
        if self._closed_flag:
            return
        buttons = (self.paste_button, self.cancel_button)
        # Ignored-size action buttons must not be assigned equal widths when
        # either label needs more than half the row. Use native style hints
        # (including padding) and measured text, without changing the font.
        widths = [max(b.sizeHint().width(), b.fontMetrics().horizontalAdvance(b.text()) + 8)
                  for b in buttons]
        available = self.layout().contentsRect().width()
        spacing = max(0, self.footer_row.spacing())
        horizontal = 2 * max(widths) + spacing <= available
        direction = (QBoxLayout.Direction.LeftToRight if horizontal
                     else QBoxLayout.Direction.TopToBottom)
        if self.footer_row.direction() != direction:
            self.footer_row.setDirection(direction)

    def _schedule_footer(self):
        if hasattr(self, '_footer_pending') and not self._footer_pending:
            self._footer_pending = True
            QTimer.singleShot(0, self._fit_footer)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange,
                            QEvent.Type.LayoutRequest):
            self._schedule_footer()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_footer()

    def showEvent(self, event):
        super().showEvent(event); self.fit_available_screen()

    def fit_available_screen(self, available=None):
        fit_dialog(self, available)

    def _current(self):
        w = self.window
        return (not self._closed_flag and not self._stale and not w.closing
                and w._clip_clipboard is self.clipboard
                and self.context == _editor_context(w)
                and self.captured_playhead == w.timeline.playhead)

    def _prepare_plan(self):
        try:
            self.plan = plan_clip_paste(self.window.editor, self.clipboard, self.destination)
        except Exception as exc:
            self.plan = None; self._last_error = str(exc)

    def capture_playhead(self):
        if self._closed_flag or self.window.jobs or self.window._clip_clipboard is not self.clipboard:
            return
        if self.clipboard.session_id != self.window.editor.session_id:
            self.reject(); return
        self._generation += 1
        self.context = _editor_context(self.window)
        self.destination = self.captured_playhead = self.window.timeline.playhead
        self.destination_edit.blockSignals(True); self.destination_edit.setText(str(self.destination)); self.destination_edit.blockSignals(False)
        self._stale = False; self._last_error = None
        self._prepare_plan(); self.refresh_text()

    def destination_changed(self, text):
        # Every edit revokes any pending verification, including an A→B→A edit.
        self._generation += 1
        if self.job is not None:
            self.job.cancel.set()
        self.plan = None; self._last_error = None
        if not re.fullmatch(r'[0-9]{1,16}', text) or int(text) > 9007199254740991:
            self.destination = None
            self._last_error = 'Paste destination must be a non-negative integer up to 9007199254740991'
        else:
            self.destination = int(text)
            if self._current():
                self._prepare_plan()
        self.refresh_text()

    def check_current(self):
        if self._closed_flag or self._applying:
            return
        if self.window.closing or self.clipboard.session_id != self.window.editor.session_id:
            self.reject(); return
        if not self._current():
            self._stale = True; self.plan = None
            if self.job is not None:
                self.job.cancel.set()
        busy = bool(self.window.jobs)
        self.capture_button.setEnabled(not busy and self.window._clip_clipboard is self.clipboard)
        self.paste_button.setEnabled(not busy and not self._stale and self.plan is not None)
        if self._stale:
            text = tr('Project, playhead or copy changed. Capture the playhead again before pasting.')
        elif self.job is not None:
            text = tr('Checking dependencies… Cancel leaves the project and previous copy unchanged.')
        elif busy:
            text = tr('Finish or cancel the current job before pasting clips.')
        elif self.plan is not None:
            text = tr('Ready to verify and paste. One Undo removes all new clips and groups.')
        else:
            text = tr('Paste is blocked. Existing content has not changed.')
        self.status.setText(text)

    def refresh_text(self, *_):
        if self._closed_flag:
            return
        self.setWindowTitle(tr('Paste clips at playhead (original tracks)'))
        self.target.setText(tr('Captured playhead: frame {frame} · {count} copied clips · original tracks.',
                               frame=self.captured_playhead, count=self.clipboard.clip_count))
        if self._last_error:
            self.impact.setText(tr('Cannot paste clips: {error}', error=tr_error(self._last_error)))
        else:
            self.impact.setText(tr('Copy anchor: frame {frame}. Destination: frame {destination}. Each new start keeps its original distance from the copy anchor.',
                                  frame=self.clipboard.anchor, destination=self.destination))
        self.check_current()
        self._schedule_footer()

    def paste(self):
        self.check_current()
        if self._closed_flag or not self.paste_button.isEnabled():
            return
        plan = self.plan
        self._generation += 1
        generation = self._generation
        self._last_error = None
        outcome = {'completed': False, 'failed': False}

        def current():
            return generation == self._generation and self.plan is plan and self._current()

        def accepted(value):
            # Consume once before the normal content-edit path can refresh us.
            self._applying = True
            self.plan = None
            result = self.window.edit(lambda: apply_clip_paste(self.window.editor, self.clipboard, plan, value))
            if result is not None:
                outcome['completed'] = True
                ids = tuple(clip.id for clip in result)
                if ids:
                    self.window.select_clip(ids[0])
                    self.window.set_selected_clip_ids(ids, anchor=ids[0])
                    self.window.refresh_inspector(); self.window.timeline.update()
                    self.window.shortcuts.update_enabled()
                self.accept()
            else:
                outcome['failed'] = True
                self._applying = False; self._stale = True; self.refresh_text()

        def setup(job):
            self.job = job
            job_generation = self.window._job_generation
            job.failed.connect(lambda *_: outcome.update(failed=True))
            job.failed.connect(lambda error: self._failed(error) if current() and not job.cancel.is_set() else None)
            job.finished.connect(lambda: self._finished(job))
            job.finished.connect(lambda: _finish_clipboard_job(self.window, job, job_generation, outcome))

        self.window.start_job(tr('Paste clips'),
            lambda cancel, progress: verify_clip_paste(plan, self.clipboard, cancel=cancel),
            accepted, 'clip_paste', before_start=setup, is_current=current)
        self.check_current()

    def _failed(self, error):
        self._last_error = error
        self.refresh_text()

    def _finished(self, job):
        if self.job is job:
            self.job = None
        if not self._closed_flag:
            self.refresh_text()

    def cancel_work(self):
        self._generation += 1
        if self.job is not None:
            self.job.cancel.set()
        self.plan = None; self._stale = True
        if not self._closed_flag:
            self.refresh_text()

    def _closed(self, *_):
        if self._closed_flag:
            return
        self._closed_flag = True
        self.cancel_work(); self.timer.stop()
        if self.window.clip_paste_dialog is self:
            self.window.clip_paste_dialog = None

"""Explicit staged independent audio separation with cancellable source checks."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from .i18n import tr, tr_error, locale_events
from .i18n_widgets import QComboBox, QDialog, QScrollArea, QWidget
from .source_audio_separation import (plan_source_audio_separation,
    verify_source_audio_separation, apply_source_audio_separation)
from .source_overwrite import _signature
from .ui_interval_preview import plain_label, action_button, fit_dialog

SEPARATION_SCOPE = ('Create an independent audio clip on the audio track you choose, at the same start and duration. '
    'The video keeps its picture and settings, but no longer emits source audio. '
    'The new audio shares the original source file; nothing is extracted or copied.')
SEPARATION_LIMITS = ('These clips are not linked or automatically grouped. Later moves, trims, speed changes, mute and deletion are independent. '
    'Deleting the audio or unmuting the video does not restore video sound. One Undo reverses separation. To reattach later, explicitly select a matching suppressed video and independent audio pair, then use Edit → Reattach source audio… . '
    'Grouped clips, transition endpoints, locked tracks and occupied destinations are rejected. '
    'Splitting separated components and cutting them with Source Insert/Overwrite are not supported yet; independent trim remains available.')
SEPARATION_CHECK = ('Preview checks the original local source on a cancellable worker. Apply checks it again before one atomic edit. '
    'No source file is written. Rebuild preview or export to hear the result.')


def _context(window):
    return (id(window.editor), window.editor.session_id, window.editor.revision,
        id(window.project), window.revision, _signature(window.project),
        window.selection_generation, tuple(window.selected_clip_ids), window.active_selection_type)


def _finish_job(window, job, generation, outcome):
    if window.closing or generation != window._job_generation or window.jobs:
        return
    if outcome['completed']:
        text = tr('Ready · local files only'); progress = 100
    elif job.cancel.is_set():
        text = tr('Canceled · sources unchanged'); progress = 0
    elif outcome['failed']:
        text = tr('Audio separation check failed · project unchanged'); progress = 0
    else:
        text = tr('Audio separation expired · project unchanged'); progress = 0
    window.progress_label.setText(text); window.progress_bar.setValue(progress)


class SourceAudioSeparationMixin:
    def init_source_audio_separation(self):
        self.source_audio_separation_dialog = None
        self._source_audio_selection_session = (id(self.editor), self.editor.session_id)
        self._source_audio_selection_history = {}

    def can_separate_source_audio(self):
        if (self.closing or self.jobs or self.active_selection_type != 'clip'
                or len(self.selected_clip_ids) != 1):
            return False
        clip = self.selected_clip()
        if clip is None or clip.track not in (0, 1) or not clip.source_audio_enabled:
            return False
        media = next((m for m in self.project.media if m.id == clip.media_id), None)
        return bool(media and media.has_video and media.has_audio
                    and not self.project.is_track_edit_locked(clip.track))

    def open_source_audio_separation(self):
        dialog = self.source_audio_separation_dialog
        if dialog is not None:
            dialog.show(); dialog.raise_(); dialog.activateWindow()
            return dialog
        if not self.can_separate_source_audio():
            return
        self.timeline.cancel_gestures()
        dialog = SourceAudioSeparationDialog(self)
        self.source_audio_separation_dialog = dialog
        dialog.show()
        return dialog

    def refresh_source_audio_separation(self):
        current = (id(self.editor), self.editor.session_id)
        if current != self._source_audio_selection_session:
            self._source_audio_selection_session = current
            self._source_audio_selection_history.clear()
        dialog = self.source_audio_separation_dialog
        if dialog is not None:
            dialog.check_current()

    def remember_source_audio_selection(self, plan, audio_id):
        # Session-only metadata for this exact history edge, never an A/V link.
        # Discard redo-branch receipts after a new edit, retaining every edge
        # whose new audio still exists in any reachable project snapshot.
        reachable = {clip.id for project in
            (self.project, *self.editor._undo, *self.editor._redo) for clip in project.clips}
        self._source_audio_selection_history = {
            edge: ids for edge, ids in self._source_audio_selection_history.items()
            if ids[1] in reachable}
        self._source_audio_selection_history[(plan.project_signature,
            _signature(self.project))] = (plan.source_clip_id, audio_id)

    def restore_source_audio_selection(self, before_signature):
        if before_signature is None:
            return
        after_signature = _signature(self.project)
        forward = self._source_audio_selection_history.get((before_signature, after_signature))
        reverse = self._source_audio_selection_history.get((after_signature, before_signature))
        selected = forward[1] if forward is not None else reverse[0] if reverse is not None else None
        if selected is not None and any(c.id == selected for c in self.project.clips):
            self.select_clip(selected)

    def cancel_source_audio_separation_work(self, *, close=False):
        dialog = self.source_audio_separation_dialog
        if dialog is not None:
            if close: dialog.reject()
            else: dialog.cancel_work()

    def refresh_source_audio_status(self):
        clip = self.selected_parameter_clip()
        from .ui_linked_av import av_link
        if clip is not None and av_link(self.project, clip.id):
            text = tr('Linked A/V · only the audio-track member emits source audio. Unlink does not restore embedded video audio.')
        elif clip is not None and not clip.source_audio_enabled:
            text = tr('Source audio separated · this video stays silent even when unmuted. Edit the independent audio clip; Undo reverses separation. To reattach, select this video and one matching audio clip, then use Edit → Reattach source audio… .')
        elif clip is not None and clip.track in (2, 3) and clip.audio_mix_anchor is not None:
            text = tr('Independent source audio · no linked editing. Moving, trimming or deleting this clip does not change its video.')
        elif clip is not None and clip.track in (0, 1) and clip.audio_mix_anchor is not None:
            text = tr('Source audio enabled · historical mixing slot retained. Gain, mute and fades belong to this video. No linked editing.')
        else:
            media = next((m for m in self.project.media if clip and m.id == clip.media_id), None)
            text = tr('Source audio is part of this clip. Separate it to edit an independent audio clip.') if clip is not None and clip.track < 2 and media and media.has_audio else ''
        self.source_audio_status.setText(text)
        self.source_audio_status.setVisible(bool(text))
        self.separate_source_audio_button.setEnabled(self.can_separate_source_audio())
        self.separate_source_audio_button.setVisible(bool(clip and clip.track < 2))


class SourceAudioSeparationDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.clip_id = window.selected_clip_ids[0]
        self.session_id = window.editor.session_id
        self.context = _context(window)
        self.plan = self.preview_receipt = self.job = None
        self._generation = 0
        self._closed_flag = self._stale = self._applying = False
        self._last_error = None
        self.setModal(False); self.resize(630, 530)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); body = QVBoxLayout(content)
        self.scope = plain_label(tr(SEPARATION_SCOPE)); body.addWidget(self.scope)
        self.source = plain_label(''); body.addWidget(self.source)
        self.track_label = plain_label(tr('Destination audio track (choose explicitly)')); body.addWidget(self.track_label)
        self.track = QComboBox(); self.track.setMinimumWidth(0)
        self.track.addItem(tr('Choose audio track…'), None)
        self.track.addItem('A0', 2); self.track.addItem('A1', 3)
        self.track.setAccessibleName(tr('Destination audio track (choose explicitly)'))
        self.track.setToolTip(tr('The full source-clip interval must be free. No automatic repositioning.'))
        self.track_label.setBuddy(self.track); body.addWidget(self.track)
        self.limits = plain_label(tr(SEPARATION_LIMITS)); body.addWidget(self.limits)
        self.check_note = plain_label(tr(SEPARATION_CHECK)); body.addWidget(self.check_note)
        self.impact = plain_label(''); body.addWidget(self.impact)
        self.status = plain_label(''); body.addWidget(self.status); body.addStretch()
        self.scroll.setWidget(content); layout.addWidget(self.scroll, 1)
        row = QVBoxLayout()  # Full-width actions keep narrow/large-font labels intact.
        self.preview_button = action_button('Preview separation', self.preview, row)
        self.apply_button = action_button('Apply separation', self.apply, row)
        self.cancel_button = action_button('Cancel', self.reject, row)
        layout.addLayout(row)
        self.track.currentIndexChanged.connect(self.target_changed)
        self.finished.connect(self._closed)
        locale_events.changed.connect(self.refresh_text)
        self.timer = QTimer(self); self.timer.setInterval(150)
        self.timer.timeout.connect(self.check_current); self.timer.start()
        self.refresh_text()

    def showEvent(self, event):
        super().showEvent(event); self.fit_available_screen()

    def fit_available_screen(self, available=None):
        fit_dialog(self, available)

    def _current(self):
        return (not self._closed_flag and not self._stale and not self.window.closing
                and self.context == _context(self.window))

    def target_changed(self, *_):
        self._generation += 1
        if self.job is not None: self.job.cancel.set()
        self.plan = self.preview_receipt = None; self._last_error = None
        if self._current() and self.track.currentData() is not None:
            try:
                self.plan = plan_source_audio_separation(self.window.editor,
                    self.clip_id, self.track.currentData())
            except Exception as exc:
                self._last_error = str(exc)
        self.refresh_text()

    def check_current(self):
        if self._closed_flag or self._applying:
            return
        if self.window.closing or self.session_id != self.window.editor.session_id:
            self.reject(); return
        if not self._current():
            self._stale = True
            self.plan = self.preview_receipt = None
            if self.job is not None: self.job.cancel.set()
        busy = bool(self.window.jobs)
        ready = not busy and not self._stale and self.plan is not None
        self.track.setEnabled(not busy and not self._stale)
        self.preview_button.setEnabled(ready)
        self.apply_button.setEnabled(ready and self.preview_receipt is not None)
        if self._stale:
            text = tr('Project or selection changed. Close and reopen separation for the current clip.')
        elif self.job is not None:
            text = tr('Checking source… Cancel leaves the project unchanged.')
        elif busy:
            text = tr('Finish or cancel the current job before separating audio.')
        elif self.preview_receipt is not None:
            text = tr('Preview verified. Apply will verify again and create one independent audio clip.')
        elif self.plan is not None:
            text = tr('Ready to preview. No project change until Apply separation.')
        else:
            text = tr('Choose a free audio track, then preview separation.')
        self.status.setText(text)

    def refresh_text(self, *_):
        if self._closed_flag:
            return
        self.setWindowTitle(tr('Separate source audio — independent clips'))
        clip = next((c for c in self.window.project.clips if c.id == self.clip_id), None)
        if clip is not None:
            self.source.setText(tr('Video V{track} · [{start}, {end}) project frames · {duration} frames · {speed}× speed.',
                track=clip.track, start=clip.start, end=clip.end, duration=clip.duration, speed=str(clip.speed)))
        if self._last_error:
            text = tr('Cannot separate source audio: {error}', error=tr_error(self._last_error))
        elif self.plan is not None:
            text = tr('Keep the video picture unchanged. Add one independent clip to A{track}; all other clips, captions, stickers, ranges and markers stay fixed.',
                track=self.plan.track - 2)
        else:
            text = tr('No destination selected. The project and original source remain unchanged.')
        self.impact.setText(text)
        self.check_current()

    def preview(self):
        self.check_current()
        if self._closed_flag or not self.preview_button.isEnabled(): return
        self.preview_receipt = None
        self._run(False)

    def apply(self):
        self.check_current()
        if self._closed_flag or not self.apply_button.isEnabled(): return
        self._run(True)

    def _run(self, applying):
        plan = self.plan; previous = self.preview_receipt if applying else None
        self._generation += 1; generation = self._generation
        self._last_error = None
        outcome = {'completed': False, 'failed': False}
        def current():
            return (generation == self._generation and self.plan is plan
                    and self._current())
        def accepted(value):
            if not applying:
                self.preview_receipt = value; outcome['completed'] = True
                self.refresh_text(); return
            self._applying = True
            self.plan = self.preview_receipt = None
            result = self.window.edit(lambda: apply_source_audio_separation(self.window.editor, plan, value))
            if result is not None:
                outcome['completed'] = True
                self.window.remember_source_audio_selection(plan, result.id)
                self.window.select_clip(result.id)
                self.window.refresh_inspector(); self.window.timeline.update()
                self.window.shortcuts.update_enabled()
                self.accept()
            else:
                outcome['failed'] = True
                self._applying = False; self._stale = True; self.refresh_text()
        def setup(job):
            self.job = job; job_generation = self.window._job_generation
            job.failed.connect(lambda *_: outcome.update(failed=True))
            job.failed.connect(lambda error: self._failed(error) if current() and not job.cancel.is_set() else None)
            job.finished.connect(lambda: self._finished(job))
            job.finished.connect(lambda: _finish_job(self.window, job, job_generation, outcome))
        self.window.start_job(tr('Apply separation') if applying else tr('Preview separation'),
            lambda cancel, progress: verify_source_audio_separation(plan, previous=previous, cancel=cancel),
            accepted, 'source_audio_separation', before_start=setup, is_current=current)
        self.check_current()

    def _failed(self, error):
        self._last_error = error; self.preview_receipt = None
        self.refresh_text()

    def _finished(self, job):
        if self.job is job: self.job = None
        if not self._closed_flag: self.refresh_text()

    def cancel_work(self):
        self._generation += 1
        if self.job is not None: self.job.cancel.set()
        self.preview_receipt = None
        if not self._closed_flag: self.refresh_text()

    def _closed(self, *_):
        if self._closed_flag: return
        self._closed_flag = True
        self.cancel_work(); self.timer.stop()
        if self.window.source_audio_separation_dialog is self:
            self.window.source_audio_separation_dialog = None

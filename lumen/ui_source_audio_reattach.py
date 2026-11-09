"""Explicit two-ID source audio reattachment with cancellable source checks."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QVBoxLayout

from .i18n import tr, tr_error, locale_events
from .i18n_widgets import QDialog, QScrollArea, QWidget
from .source_audio_reattach import (plan_source_audio_reattach,
    verify_source_audio_reattach, apply_source_audio_reattach)
from .source_overwrite import _signature
from .ui_interval_preview import plain_label, action_button, fit_dialog

REATTACH_SCOPE = ('Reattach exactly the two selected clips: keep the suppressed video and delete only the selected independent audio clip. '
    'The video receives that audio clip’s gain, mute, fades and historical mixing slot; all picture and source timing settings stay unchanged.')
REATTACH_LIMITS = ('This is not linked editing. The same Media record, exact complete source timing, speed, timeline start and duration must match. '
    'Grouped clips, transition endpoints, locked tracks and ambiguous historical mixing slots are rejected. '
    'No candidate is guessed, no timing is rebased or clamped, and no other clip or annotation is cleaned up.')
REATTACH_CHECK = ('Preview checks the original local source on a cancellable worker. Apply checks it again before one atomic edit and deletes only the audio ID shown in this preview. '
    'One Undo restores both clips and their settings. No source file is written. Rebuild preview or export to hear the result.')
REATTACH_SELECTION = 'Select exactly two clips: one source-audio-suppressed V0/V1 clip and one source-audio-enabled A0/A1 clip. No automatic pairing.'


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
        text = tr('Audio reattachment check failed · project unchanged'); progress = 0
    else:
        text = tr('Audio reattachment expired · project unchanged'); progress = 0
    window.progress_label.setText(text); window.progress_bar.setValue(progress)


class SourceAudioReattachMixin:
    def init_source_audio_reattach(self):
        self.source_audio_reattach_dialog = None

    def source_audio_reattach_selection(self):
        if self.closing or self.jobs:
            return None, 'Finish or cancel the current job before reattaching audio.'
        if self.active_selection_type != 'clip' or len(self.selected_clip_ids) != 2:
            return None, REATTACH_SELECTION
        ids = set(self.selected_clip_ids)
        clips = [c for c in self.project.clips if c.id in ids]
        videos = [c for c in clips if c.track in (0, 1) and not c.source_audio_enabled]
        audios = [c for c in clips if c.track in (2, 3) and c.source_audio_enabled]
        if len(clips) != 2 or len(videos) != 1 or len(audios) != 1:
            return None, REATTACH_SELECTION
        video, audio = videos[0], audios[0]
        if any({video.id, audio.id} & {link.video_clip_id, link.audio_clip_id} for link in self.project.av_links):
            return None, 'Unlink A/V before reattaching source audio.'
        if any(self.project.is_track_edit_locked(c.track) for c in (video, audio)):
            return None, 'Unlock both selected tracks before reattaching source audio.'
        return (video.id, audio.id), None

    def can_reattach_source_audio(self):
        return self.source_audio_reattach_selection()[0] is not None

    def open_source_audio_reattach(self):
        dialog = self.source_audio_reattach_dialog
        if dialog is not None:
            dialog.show(); dialog.raise_(); dialog.activateWindow()
            return dialog
        pair, _ = self.source_audio_reattach_selection()
        if pair is None:
            return
        self.timeline.cancel_gestures()
        dialog = SourceAudioReattachDialog(self, *pair)
        self.source_audio_reattach_dialog = dialog
        dialog.show()
        return dialog

    def refresh_source_audio_reattach(self):
        dialog = self.source_audio_reattach_dialog
        if dialog is not None:
            dialog.check_current()

    def cancel_source_audio_reattach_work(self, *, close=False):
        dialog = self.source_audio_reattach_dialog
        if dialog is not None:
            if close: dialog.reject()
            else: dialog.cancel_work()


class SourceAudioReattachDialog(QDialog):
    def __init__(self, window, video_clip_id, audio_clip_id):
        super().__init__(window)
        self.window = window
        self.video_clip_id = video_clip_id
        self.audio_clip_id = audio_clip_id
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
        self.scope = plain_label(tr(REATTACH_SCOPE)); body.addWidget(self.scope)
        self.source = plain_label(''); body.addWidget(self.source)
        self.limits = plain_label(tr(REATTACH_LIMITS)); body.addWidget(self.limits)
        self.check_note = plain_label(tr(REATTACH_CHECK)); body.addWidget(self.check_note)
        self.impact = plain_label(''); body.addWidget(self.impact)
        self.status = plain_label(''); body.addWidget(self.status); body.addStretch()
        self.scroll.setWidget(content); layout.addWidget(self.scroll, 1)
        row = QVBoxLayout()  # Full-width actions keep narrow/large-font labels intact.
        self.preview_button = action_button('Preview reattachment', self.preview, row)
        self.apply_button = action_button('Apply reattachment', self.apply, row)
        self.cancel_button = action_button('Cancel', self.reject, row)
        layout.addLayout(row)
        # Selection is explicit and immutable for the lifetime of this preview.
        self.finished.connect(self._closed)
        locale_events.changed.connect(self.refresh_text)
        self.timer = QTimer(self); self.timer.setInterval(150)
        self.timer.timeout.connect(self.check_current); self.timer.start()
        try:
            self.plan = plan_source_audio_reattach(window.editor, video_clip_id, audio_clip_id)
        except Exception as exc:
            self._last_error = str(exc)
        self.refresh_text()

    def showEvent(self, event):
        super().showEvent(event); self.fit_available_screen()

    def fit_available_screen(self, available=None):
        fit_dialog(self, available)

    def _current(self):
        return (not self._closed_flag and not self._stale and not self.window.closing
                and self.context == _context(self.window))

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
        self.preview_button.setEnabled(ready)
        self.apply_button.setEnabled(ready and self.preview_receipt is not None)
        if self._stale:
            self.impact.setText(tr('This preview has expired. No clip will be deleted; close and reopen for the current pair.'))
            text = tr('Project or selection changed. Close and reopen reattachment for the current pair.')
        elif self.job is not None:
            text = tr('Checking source… Cancel leaves the project unchanged.')
        elif busy:
            text = tr('Finish or cancel the current job before reattaching audio.')
        elif self.preview_receipt is not None:
            text = tr('Preview verified. Apply will verify again and reattach audio and delete only the selected audio clip.')
        elif self.plan is not None:
            text = tr('Ready to preview. No project change until Apply reattachment.')
        else:
            text = tr('The selected pair cannot be reattached. Nothing has changed.')
        self.status.setText(text)

    def refresh_text(self, *_):
        if self._closed_flag:
            return
        self.setWindowTitle(tr('Reattach source audio — explicit pair'))
        clips = {c.id: c for c in self.window.project.clips}
        video, audio = clips.get(self.video_clip_id), clips.get(self.audio_clip_id)
        if video is not None and audio is not None:
            from .audio_components import effective_audio_mix_anchor
            slot = effective_audio_mix_anchor(audio)
            self.source.setText(tr('KEEP V{video_track} · video ID {video_id} · Media {video_media}\nTimeline [{video_start}, {video_end}) · {video_duration} frames; source [{video_in}, {video_out}) · {video_speed}×.\nDELETE A{audio_track} · audio ID {audio_id} · Media {audio_media}\nTimeline [{audio_start}, {audio_end}) · {audio_duration} frames; source [{audio_in}, {audio_out}) · {audio_speed}×.\nTransfer {gain} dB · mute {muted} · fades {fade_in}/{fade_out} frames. Historical mix slot: ({slot_track}, {slot_start}, {slot_id}).',
                video_track=video.track, video_id=video.id, video_media=video.media_id,
                video_start=video.start, video_end=video.end, video_duration=video.duration,
                video_in=str(video.source_in), video_out=str(video.source_out), video_speed=str(video.speed),
                audio_track=audio.track-2, audio_id=audio.id, audio_media=audio.media_id,
                audio_start=audio.start, audio_end=audio.end, audio_duration=audio.duration,
                audio_in=str(audio.source_in), audio_out=str(audio.source_out), audio_speed=str(audio.speed),
                gain=audio.gain_db, muted=tr('On') if audio.muted else tr('Off'),
                fade_in=audio.fade_in, fade_out=audio.fade_out,
                slot_track=slot[0], slot_start=slot[1], slot_id=slot[2]))
        if self._last_error:
            text = tr('Cannot reattach source audio: {error}', error=tr_error(self._last_error))
        elif self.plan is not None:
            text = tr('Exact timing and source match. Applying deletes only audio ID {audio_id}, restores video ID {video_id} source emission, and preserves the selected audio’s current mute state. All other clips, media and annotations stay unchanged.',
                audio_id=self.audio_clip_id, video_id=self.video_clip_id)
        else:
            text = tr('The selected pair cannot be reattached. Nothing has changed.')
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
            result = self.window.edit(lambda: apply_source_audio_reattach(self.window.editor, plan, value))
            if result is not None:
                outcome['completed'] = True
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
        self.window.start_job(tr('Apply reattachment') if applying else tr('Preview reattachment'),
            lambda cancel, progress: verify_source_audio_reattach(plan, previous=previous, cancel=cancel),
            accepted, 'source_audio_reattach', before_start=setup, is_current=current)
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
        if self.window.source_audio_reattach_dialog is self:
            self.window.source_audio_reattach_dialog = None

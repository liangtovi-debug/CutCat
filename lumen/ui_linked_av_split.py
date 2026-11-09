"""One captured, worker-qualified joint split of a complete same-source pair."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QVBoxLayout

from .i18n import tr, tr_error, locale_events
from .i18n_widgets import QDialog, QScrollArea, QWidget
from .linked_av_split import (plan_linked_av_split, verify_linked_av_split,
                              apply_linked_av_split)
from .source_overwrite import _signature
from .ui_interval_preview import plain_label, action_button, fit_dialog

SPLIT_SCOPE = ('Pause playback, then split exactly one complete linked A/V pair at the captured playhead. '
    'The cut must be strictly inside both members. Their own outer edges, source endpoints and partial tails stay intact, including existing J/L offsets. '
    'Both left members keep their identities; the right members form a new linked pair. '
    'Only the new internal edges align. Nothing ripples. One Undo restores the original pair.')
SPLIT_LIMITS = ('This version requires 1× source timing at the project frame rate, '
    'with a source span covering both members of at most 30 seconds. Fractional source origins and partial tails are retained exactly. '
    'Retiming, mixed or variable frame rates, active effects, stabilization and restoration are not supported. '
    'Fades crossing the cut and non-equivalent audio sample boundaries reject without clamping.')
SPLIT_CHECK = ('Preview verifies current local sources and LUTs, complete CFR timestamps and '
    'native-resolution RGB16 frame hashes for the original and both halves on a cancellable worker. '
    'Apply checks again before one atomic edit. Sources are never written; no project change occurs before Apply.')
SPLIT_SELECTION = 'Select exactly one complete linked A/V pair and place the playhead strictly inside both members.'


def _context(window):
    return (id(window.editor), window.editor.session_id, window.editor.revision,
        id(window.project), window.revision, _signature(window.project),
        window.selection_generation, tuple(window.selected_clip_ids), window.selection,
        window.active_selection_type, window.timeline.playhead,
        getattr(window, '_final_frame_seek_generation', 0), id(window.player),
        window.player.playbackState())


class LinkedAVSplitDialog(QDialog):
    def __init__(self, window, clip_id, frame):
        super().__init__(window)
        from .ui_linked_av import linked_pair
        # Keep QWidget.window() callable for Qt event filters and IME dispatch.
        self.local_owner_window, self.clip_id, self.frame = window, clip_id, frame
        self.link = linked_pair(window.project, window.selected_clip_ids)
        self.active_video = clip_id == self.link.video_clip_id
        self.captured_roles = tuple((clip.id, clip.start, clip.end, clip.source_in, clip.source_out)
            for clip in (window.editor._clip(self.link.video_clip_id),
                         window.editor._clip(self.link.audio_clip_id)))
        self.session_id = window.editor.session_id
        self.context = _context(window)
        # Capture actual IDs exactly once. Preview retries reuse this plan.
        self.plan = plan_linked_av_split(window.editor, clip_id, frame)
        self.preview_receipt = self.job = None
        self._generation = 0
        self._closed_flag = self._stale = self._applying = False
        self._last_error = None
        self._cancelled = False
        self._feedback_state = None
        self._feedback_scroll_generation = 0
        self._feedback_reveal_pending = self._feedback_reveal_queued = None
        self.setModal(False); self.resize(640, 620)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); body = QVBoxLayout(content)
        # Operational feedback comes first; long explanations cannot push a
        # new failure/cancellation below the narrow viewport.
        self.impact = plain_label(''); body.addWidget(self.impact)
        self.status = plain_label(''); body.addWidget(self.status)
        self.scope = plain_label(''); body.addWidget(self.scope)
        self.source = plain_label(''); body.addWidget(self.source)
        self.limits = plain_label(''); body.addWidget(self.limits)
        self.check_note = plain_label(''); body.addWidget(self.check_note)
        body.addStretch()
        self.scroll.setWidget(content); layout.addWidget(self.scroll, 1)
        row = QVBoxLayout()
        self.preview_button = action_button('Preview joint split', self.preview, row)
        self.apply_button = action_button('Apply joint split', self.apply, row)
        self.cancel_button = action_button('Cancel', self.reject, row)
        layout.addLayout(row)
        for watched in (self.scroll.viewport(), self.scroll.verticalScrollBar()):
            watched.installEventFilter(self)
        self.scroll.verticalScrollBar().sliderPressed.connect(self._cancel_feedback_reveal)
        self.scroll.verticalScrollBar().actionTriggered.connect(self._cancel_feedback_reveal)
        self.finished.connect(self._closed)
        locale_events.changed.connect(self.refresh_text)
        self.timer = QTimer(self); self.timer.setInterval(150)
        self.timer.timeout.connect(self.check_current); self.timer.start()
        self._bound_player = window.player
        self._bound_player.playbackStateChanged.connect(self._playback_changed)
        self.refresh_text()

    def _playback_changed(self, *_):
        # Any transport-state change permanently expires this captured intent,
        # including Play/Pause ABA before the next position callback.
        if self._closed_flag or self._applying: return
        self._stale = True; self.preview_receipt = None
        if self.job is not None: self.job.cancel.set()
        self.check_current()

    def showEvent(self, event):
        super().showEvent(event); self.fit_available_screen()

    def fit_available_screen(self, available=None):
        fit_dialog(self, available)

    def _current(self):
        return (not self._closed_flag and not self._stale and not self.local_owner_window.closing
                and self.context == _context(self.local_owner_window))

    def check_current(self):
        if self._closed_flag or self._applying: return
        if self.local_owner_window.closing or self.session_id != self.local_owner_window.editor.session_id:
            self.reject(); return
        if not self._current():
            self._stale = True; self.preview_receipt = None
            if self.job is not None: self.job.cancel.set()
        busy = bool(self.local_owner_window.jobs)
        ready = not busy and not self._stale
        self.preview_button.setEnabled(ready)
        self.apply_button.setEnabled(ready and self.preview_receipt is not None)
        feedback = None
        if self._stale:
            feedback = ('stale',)
            text = tr('Project, selection, role, playback or playhead changed. Close and reopen; no joint split was applied.')
        elif self._last_error:
            feedback = ('failed', str(self._last_error))
            text = tr('Joint split check failed · project unchanged')
        elif self._cancelled:
            feedback = ('cancelled',)
            text = tr('Joint split check cancelled. No changes were applied; preview again to continue.')
        elif self.job is not None:
            text = tr('Checking source and frame continuity… Cancel leaves the project unchanged.')
        elif busy:
            text = tr('Finish or cancel the current job before editing A/V links.')
        elif self.preview_receipt is not None:
            feedback = ('verified',)
            text = tr('Preview verified. Apply checks again and splits both members atomically.')
        else:
            text = tr('Ready to preview the captured cut. No project change until Apply joint split.')
        self.status.setText(text)
        failed = feedback is not None and feedback[0] == 'failed'
        self.impact.setText(tr('Cannot apply joint split: {error}', error=tr_error(self._last_error))
                            if failed else '')
        self.impact.setVisible(failed)
        if feedback != self._feedback_state:
            self._feedback_state = feedback
            self._feedback_scroll_generation += 1
            self._feedback_reveal_pending = self._feedback_scroll_generation if feedback is not None else None
            self._queue_feedback_reveal()

    def _queue_feedback_reveal(self):
        token = self._feedback_reveal_pending
        if token is not None and token != self._feedback_reveal_queued:
            self._feedback_reveal_queued = token
            QTimer.singleShot(0, lambda: self._reveal_feedback(token))

    def _cancel_feedback_reveal(self, *_):
        self._feedback_reveal_pending = None

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Wheel, QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress):
            self._cancel_feedback_reveal()
        return super().eventFilter(watched, event)

    def _reveal_feedback(self, token):
        if self._feedback_reveal_queued == token:
            self._feedback_reveal_queued = None
        if (self._closed_flag or not self.isVisible()
                or token != self._feedback_reveal_pending or self._feedback_state is None):
            return
        # Feedback is anchored at the beginning of the scroll content. Zero
        # remains correct even while lower explanatory text finishes wrapping.
        # One semantic result gets one reveal; polling never scrolls again.
        self._feedback_reveal_pending = None
        self.scroll.verticalScrollBar().setValue(0)

    def refresh_text(self, *_):
        if self._closed_flag: return
        self.setWindowTitle(tr('Joint split — linked A/V'))
        self.scope.setText(tr(SPLIT_SCOPE)); self.limits.setText(tr(SPLIT_LIMITS))
        self.check_note.setText(tr(SPLIT_CHECK))
        # The captured identities and cut remain visible even after expiry.
        video, audio = self.captured_roles
        self.source.setText(tr('Video {video_id} · timeline [{video_start}, {video_end}) · source [{video_in}, {video_out})\nAudio {audio_id} · timeline [{audio_start}, {audio_end}) · source [{audio_in}, {audio_out})\nCaptured timeline cut: frame {frame} · active role: {role}',
            video_id=video[0], video_start=video[1], video_end=video[2], video_in=video[3], video_out=video[4],
            audio_id=audio[0], audio_start=audio[1], audio_end=audio[2], audio_in=audio[3], audio_out=audio[4],
            frame=self.frame, role='V' if self.active_video else 'A'))
        self.check_current()

    def preview(self):
        self.check_current()
        if self._closed_flag or not self.preview_button.isEnabled(): return
        self.preview_receipt = None; self._run(False)

    def apply(self):
        self.check_current()
        if self._closed_flag or not self.apply_button.isEnabled(): return
        self._run(True)

    def _run(self, applying):
        plan = self.plan; previous = self.preview_receipt if applying else None
        self._generation += 1; generation = self._generation
        self._last_error = None; self._cancelled = False
        outcome = {'completed': False, 'failed': False}
        def current():
            return generation == self._generation and self.plan is plan and self._current()
        def accepted(value):
            if not applying:
                self.preview_receipt = value; outcome['completed'] = True; self.refresh_text(); return
            self._applying = True; self.preview_receipt = None
            result = self.local_owner_window.edit(lambda: apply_linked_av_split(self.local_owner_window.editor, plan, value))
            if result is not None:
                outcome['completed'] = True
                selected = result[0] if self.active_video else result[1]
                self.local_owner_window.select_clip(selected.id)
                self.local_owner_window.refresh_inspector(); self.local_owner_window.timeline.update()
                self.local_owner_window.shortcuts.update_enabled(); self.accept()
            else:
                outcome['failed'] = True; self._applying = False; self._stale = True; self.refresh_text()
        def setup(job):
            self.job = job; job_generation = self.local_owner_window._job_generation
            job.failed.connect(lambda *_: outcome.update(failed=True))
            job.failed.connect(lambda error: self._failed(error) if current() and not job.cancel.is_set() else None)
            job.finished.connect(lambda: self._finished(job))
            job.finished.connect(lambda: self._finish_progress(job, job_generation, outcome))
        self.local_owner_window.start_job(tr('Apply joint split') if applying else tr('Preview joint split'),
            lambda cancel, progress: verify_linked_av_split(plan, previous=previous, cancel=cancel),
            accepted, 'linked_av_split', before_start=setup, is_current=current)
        self.check_current()

    def _finish_progress(self, job, generation, outcome):
        window = self.local_owner_window
        if window.closing or generation != window._job_generation or window.jobs: return
        if outcome['completed']: text, progress = tr('Ready · local files only'), 100
        elif job.cancel.is_set(): text, progress = tr('Canceled · sources unchanged'), 0
        elif outcome['failed']: text, progress = tr('Joint split check failed · project unchanged'), 0
        else: text, progress = tr('Joint split expired · project unchanged'), 0
        window.progress_label.setText(text); window.progress_bar.setValue(progress)

    def _failed(self, error):
        self._last_error = error; self.preview_receipt = None; self.refresh_text()

    def _finished(self, job):
        if self.job is job: self.job = None
        if not self._closed_flag: self.refresh_text()

    def cancel_work(self):
        if not self._closed_flag and (self.job is not None or self.preview_receipt is not None):
            self._cancelled = True; self._last_error = None
        self._generation += 1
        if self.job is not None: self.job.cancel.set()
        self.preview_receipt = None
        if not self._closed_flag: self.check_current()

    def _closed(self, *_):
        if self._closed_flag: return
        self._closed_flag = True; self._cancel_feedback_reveal(); self.cancel_work(); self.timer.stop()
        if self._bound_player is not None:
            try: self._bound_player.playbackStateChanged.disconnect(self._playback_changed)
            except (RuntimeError, TypeError): pass
            self._bound_player = None
        if self.local_owner_window.linked_av_split_dialog is self:
            self.local_owner_window.linked_av_split_dialog = None

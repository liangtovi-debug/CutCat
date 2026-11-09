"""Explicit same-source linked A/V roles and worker-verified joint source slip."""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QVBoxLayout, QMessageBox

from .i18n import tr, tr_error, locale_events
from .i18n_widgets import QDialog, QLineEdit, QScrollArea, QWidget
from .source_overwrite import _signature
from .timing import parse_source_position
from .linked_av import plan_linked_av_slip, verify_linked_av_slip, apply_linked_av_slip
from .ui_interval_preview import plain_label, action_button, fit_dialog

LINK_HELP = ('Edit → Link same-source A/V saves one explicitly selected silent V0/V1 picture clip and one enabled A0/A1 audio clip as a pair. '
    'They must reference the same Media and have identical timeline boundaries and complete source timing. Clicking either member selects both; choose the active V or A role to edit only that member’s parameters. '
    'Move and delete the whole pair, or copy it with new identities. Select exactly one pair and drag either member’s edge to trim both without moving other clips. Fades retain their frame lengths and follow the edited edge; audio output may change. Joint source slip translates each member’s own exact source range by one common source delta in one Undo, including existing J/L pairs. '
    'Unlink changes metadata only: it does not restore embedded video audio. Unlink first for independent timing, regrouping or reattachment. '
    'Split at playhead opens a verified joint-split preview for one eligible 1× pair, including J/L offsets and partial tails, within a 30-second source proof budget. '
    'Select exactly two complete adjacent pairs to preview all four roles at a shared cut in the existing roll inspector, then apply once; outer endpoints and identities stay fixed. '
    'Use the separate Roll linked audio cut action to stage a J/L cut while both picture clips stay fixed, or explicitly align the audio cut back to picture. Split-edge pairs also support joint source slip while keeping their picture and audio boundaries fixed, and paired-edge trim moves the corresponding edges by one common timeline delta while preserving existing J/L offsets. Joint split preserves each member’s outer edges and exact source endpoints when the cut lies inside both members. Joint roll still requires identical full timing. '
    'External audio, multiple audio partners, arbitrary sync offsets and joint speed/ripple are not supported.')
LINK_SELECTION = 'Select exactly one suppressed V0/V1 clip and one enabled A0/A1 clip to link. No automatic pairing.'
UNLINK_NOTE = 'Unlink does not restore embedded video audio. The video stays silent; audio emission, gains and mixing slots are unchanged.'
LINK_TRIM_HELP = 'Select exactly one complete linked A/V pair before dragging either edge. Extra clips or multiple pairs cannot be trimmed together; unlink for independent timing.'
SLIP_SCOPE = 'Joint source slip uses the active V or A role’s requested source in to shift both members by one exact source delta, preserving each source span, speed and partial final frame, including existing J/L boundaries.'
SLIP_LIMITS = ('Timeline start, duration, tracks, local picture and audio keys, fades, gains and historical mixing slots stay unchanged. '
    'Both selected source contents change; audio output may change. Existing J/L timing stays fixed. This does not create a J/L cut, resync or joint trim/split.')
SLIP_CHECK = ('Preview checks the original local source and LUT dependencies on a cancellable worker. Apply checks them again before one atomic edit. '
    'No source file is written. Rebuild preview or export to see and hear the new source range.')


def _playing(window):
    return window.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState


def av_link(project, clip_id):
    return next((link for link in project.av_links if clip_id in (link.video_clip_id, link.audio_clip_id)), None)


def linked_pair(project, ids):
    ids = set(ids)
    return next((link for link in project.av_links if ids == {link.video_clip_id, link.audio_clip_id}), None)


def link_badge(project, clip):
    link = av_link(project, clip.id)
    return tr('AV{number} · {role}', number=project.av_links.index(link) + 1,
              role='V' if link.video_clip_id == clip.id else 'A') if link else ''


def _context(window):
    return (id(window.editor), window.editor.session_id, window.editor.revision,
        id(window.project), window.revision, _signature(window.project),
        window.selection_generation, tuple(window.selected_clip_ids), window.selection, window.active_selection_type)


class LinkedAVMixin:
    def init_linked_av(self):
        self.linked_av_slip_dialog = None
        self.linked_av_split_dialog = None
        self.linked_audio_roll_dialog = None

    def selected_parameter_clip(self):
        """Only a single clip or the explicit active role of one complete pair."""
        if len(self.selected_clip_ids) == 1:
            return self.selected_clip()
        if self.active_selection_type == 'clip' and linked_pair(self.project, self.selected_clip_ids):
            return next((c for c in self.project.clips if c.id == self.selection), None)
        return None

    def linked_av_selection(self):
        if self.closing or self.jobs or self.active_selection_type != 'clip':
            return None, 'Finish or cancel the current job before editing A/V links.'
        clips = [c for c in self.project.clips if c.id in self.selected_clip_ids]
        if len(self.selected_clip_ids) != 2 or len(clips) != 2:
            return None, LINK_SELECTION
        videos = [c for c in clips if c.track in (0, 1) and not c.source_audio_enabled]
        audios = [c for c in clips if c.track in (2, 3) and c.source_audio_enabled]
        if len(videos) != 1 or len(audios) != 1:
            return None, LINK_SELECTION
        if any(self.project.is_track_edit_locked(c.track) for c in clips):
            return None, 'Unlock both selected tracks before editing A/V links.'
        return (videos[0].id, audios[0].id), None

    def can_link_selected_av(self):
        pair, _ = self.linked_av_selection()
        return bool(pair and not any(av_link(self.project, cid) for cid in pair))

    def can_unlink_selected_av(self):
        pair, _ = self.linked_av_selection()
        return bool(pair and linked_pair(self.project, pair))

    def can_slip_linked_av(self):
        from .model import AV_LINK_ALIGNMENT, AV_LINK_AFFINE_ALIGNMENT
        if not self.isEnabled() or not self.centralWidget().isEnabled() or _playing(self):
            return False
        pair, _ = self.linked_av_selection()
        link = linked_pair(self.project, pair) if pair else None
        return bool(link and link.alignment in (AV_LINK_ALIGNMENT, AV_LINK_AFFINE_ALIGNMENT)
                    and self.selection in link.clip_ids)

    def can_roll_linked_audio(self):
        from .ui_linked_audio_roll import audio_roll_qualification
        return audio_roll_qualification(self)[0] is not None

    def open_linked_audio_roll(self):
        from .ui_linked_audio_roll import open_linked_audio_roll
        return open_linked_audio_roll(self)

    def split_selection_qualification(self):
        """One pure eligibility path shared by the menu, button and shortcut."""
        from .linked_av_split import qualify_linked_av_split
        from .ui_linked_av_split import SPLIT_SELECTION
        if (self.closing or self.jobs or not self.isEnabled()
                or not self.centralWidget().isEnabled()
                or self.active_selection_type != 'clip'):
            return None, 'Finish or cancel the current job before editing A/V links.'
        if self.selection_edit_locked():
            return None, 'Unlock both selected tracks before editing A/V links.'
        link = linked_pair(self.project, self.selected_clip_ids)
        if link is not None:
            from PySide6.QtMultimedia import QMediaPlayer
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                return None, 'Pause playback before joint split.'
            if self.selection not in link.clip_ids:
                return None, SPLIT_SELECTION
            try:
                qualify_linked_av_split(self.project, self.selection, self.timeline.playhead)
            except (ValueError, TypeError) as exc:
                return None, str(exc)
            return 'linked', None
        clip = self.selected_clip()
        if (clip is not None and av_link(self.project, clip.id) is None
                and clip.start < self.timeline.playhead < clip.end):
            return 'independent', None
        return None, SPLIT_SELECTION

    def can_split_selected(self):
        return self.split_selection_qualification()[0] is not None

    def refresh_split_control(self, qualification=None):
        if not hasattr(self, 'split_clip_button'): return
        kind, reason = self.split_selection_qualification() if qualification is None else qualification
        self.split_clip_button.setEnabled(kind is not None)
        from .ui_linked_av_split import SPLIT_SELECTION
        ui_reasons = (SPLIT_SELECTION, 'Finish or cancel the current job before editing A/V links.',
                      'Unlock both selected tracks before editing A/V links.', 'Pause playback before joint split.')
        self.split_clip_button.setToolTip((tr(reason) if reason in ui_reasons else tr_error(reason))
                                         if reason else tr('Split at playhead'))

    def open_linked_av_split(self):
        previous = self.linked_av_split_dialog
        if previous is not None:
            previous.check_current()
            if not previous._closed_flag:
                previous.show(); previous.raise_(); previous.activateWindow(); return previous
        if self.split_selection_qualification()[0] != 'linked': return None
        from .ui_linked_av_split import LinkedAVSplitDialog
        self.timeline.cancel_gestures()
        try:
            dialog = LinkedAVSplitDialog(self, self.selection, self.timeline.playhead)
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, tr('Cannot make this edit'), tr_error(exc))
            return None
        self.linked_av_split_dialog = dialog
        dialog.show()
        return dialog

    def _edit_link_metadata(self, function):
        try:
            before = self.editor.revision
            result = function()
            if self.editor.revision != before:
                # Preserve only already activated content; workers still expire
                # against the editor/window revisions and exact signature.
                self.marker_metadata_changed()
            return result
        except Exception as exc:
            QMessageBox.warning(self, tr('Cannot make this edit'), tr_error(exc))
            self.refresh_inspector()
            return None

    def link_selected_av(self):
        if self._clip_group_focus_blocked() or not self.can_link_selected_av(): return
        pair, _ = self.linked_av_selection()
        self.timeline.cancel_gestures()
        self._edit_link_metadata(lambda: self.editor.link_av_clips(*pair))

    def unlink_selected_av(self):
        if self._clip_group_focus_blocked() or not self.can_unlink_selected_av(): return
        self.timeline.cancel_gestures()
        self._edit_link_metadata(lambda: self.editor.unlink_av_clips(self.selected_clip_ids))
        self.statusBar().showMessage(tr(UNLINK_NOTE), 8000)

    def select_linked_role(self, role):
        link = linked_pair(self.project, self.selected_clip_ids)
        if link is None or role not in ('video', 'audio'): return
        self.select_clip(link.video_clip_id if role == 'video' else link.audio_clip_id)

    def refresh_linked_av_controls(self):
        if self.linked_audio_roll_dialog is not None: self.linked_audio_roll_dialog.check_current()
        if not hasattr(self, 'linked_av_note'): return
        link = linked_pair(self.project, self.selected_clip_ids)
        self.linked_av_note.setVisible(link is not None)
        self.linked_video_button.setVisible(link is not None)
        self.linked_audio_button.setVisible(link is not None)
        self.linked_slip_button.setVisible(link is not None)
        if link is not None:
            role = 'V' if self.selection == link.video_clip_id else 'A'
            self.linked_av_note.setText(tr('Linked A/V · active parameter role: {role}\nOnly this role receives parameter edits. Timing remains linked.\n{note}', role=role, note=tr(UNLINK_NOTE)))
            self.linked_slip_button.setEnabled(self.can_slip_linked_av())
            self.apply_timing_button.setEnabled(False)
            for control in (self.start_spin, self.in_spin, self.duration_spin,
                            self.speed_edit, self.apply_speed_button, self.roll_cut_spin,
                            self.apply_roll_button, self.ripple_delete_button, self.ripple_participants_button):
                control.setEnabled(False)
            self.apply_slip_button.setText(tr('Preview joint source slip…'))
            self.slip_anchor_note.setText(tr(SLIP_SCOPE))
        else:
            self.apply_timing_button.setEnabled(True)
            for control in (self.start_spin, self.in_spin, self.duration_spin, self.speed_edit):
                control.setEnabled(True)
            self.apply_slip_button.setText(tr('Apply source slip'))
            self.slip_anchor_note.setText(tr('Slip changes only the selected source content. Timeline start, duration, speed and fades stay unchanged. Captions, stickers and separate audio clips do not follow the source.'))
        dialog = self.linked_av_slip_dialog
        if dialog is not None: dialog.check_current()

    def explain_linked_trim(self):
        self.statusBar().showMessage(tr(LINK_TRIM_HELP), 6000)

    def open_linked_av_slip(self, source_in=None):
        previous = self.linked_av_slip_dialog
        if previous is not None:
            previous.check_current()
            if not previous._closed_flag:
                previous.show(); previous.raise_(); previous.activateWindow(); return previous
        if (not self.can_slip_linked_av() or QApplication.activeModalWidget() is not None
                or QApplication.activePopupWidget() is not None
                or getattr(self.shortcuts, '_composing', None) is not None): return None
        clip = self.selected_parameter_clip()
        self.timeline.cancel_gestures()
        try:
            dialog = LinkedAVSlipDialog(self, clip.id, source_in)
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, tr('Cannot make this edit'), tr_error(exc))
            return None
        self.linked_av_slip_dialog = dialog
        dialog.show()
        return dialog

    def refresh_linked_av(self):
        if self.linked_audio_roll_dialog is not None: self.linked_audio_roll_dialog.check_current()
        if self.linked_av_split_dialog is not None: self.linked_av_split_dialog.check_current()
        dialog = self.linked_av_slip_dialog
        if dialog is not None: dialog.check_current()

    def cancel_linked_av_work(self, *, close=False):
        audio_dialog = self.linked_audio_roll_dialog
        if audio_dialog is not None:
            if close: audio_dialog.reject()
            else: audio_dialog.cancel_work()
        split_dialog = self.linked_av_split_dialog
        if split_dialog is not None:
            if close: split_dialog.reject()
            else: split_dialog.cancel_work()
        dialog = self.linked_av_slip_dialog
        if dialog is not None:
            if close: dialog.reject()
            else: dialog.cancel_work()


class LinkedAVSlipDialog(QDialog):
    def __init__(self, window, clip_id, source_in=None):
        super().__init__(window)
        self.window, self.clip_id = window, clip_id
        self.link = linked_pair(window.project, window.selected_clip_ids)
        self.session_id = window.editor.session_id
        self.context = _context(window)
        self._old_clips = {clip.id: deepcopy(clip) for clip in window.project.clips
                           if clip.id in self.link.clip_ids}
        self._playback_player = window.player
        self._job_generation = window._job_generation
        self._composing = False
        self.plan = self.preview_receipt = self.job = None
        self._generation = 0
        self._closed_flag = self._stale = self._applying = False
        self._last_error = None
        self.setModal(False); self.resize(630, 590)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); body = QVBoxLayout(content)
        self.summary = plain_label(''); body.addWidget(self.summary)
        self.origin_label = plain_label('')
        body.addWidget(self.origin_label)
        self.origin = QLineEdit(str(window.editor._clip(clip_id).source_in if source_in is None else source_in))
        self.origin.setMaxLength(128)
        self.origin.installEventFilter(self)
        self.origin_label.setBuddy(self.origin); body.addWidget(self.origin)
        self.impact = plain_label(''); body.addWidget(self.impact)
        self.scope = plain_label(tr(SLIP_SCOPE)); body.addWidget(self.scope)
        self.source = plain_label(''); body.addWidget(self.source)
        self.limits = plain_label(tr(SLIP_LIMITS)); body.addWidget(self.limits)
        self.check_note = plain_label(tr(SLIP_CHECK)); body.addWidget(self.check_note)
        self.status = plain_label(''); body.addWidget(self.status); body.addStretch()
        self.scroll.setWidget(content); layout.addWidget(self.scroll, 1)
        row = QVBoxLayout()
        self.preview_button = action_button('Preview joint source slip', self.preview, row)
        self.apply_button = action_button('Apply joint source slip', self.apply, row)
        self.cancel_button = action_button('Cancel', self.reject, row)
        layout.addLayout(row)
        self.finished.connect(self._closed)
        self.origin.textChanged.connect(self._draft_changed)
        locale_events.changed.connect(self.refresh_text)
        self.timer = QTimer(self); self.timer.setInterval(150)
        self.timer.timeout.connect(self.check_current); self.timer.start()
        self._playback_player.playbackStateChanged.connect(self.check_current)
        self._draft_changed()

    def eventFilter(self, watched, event):
        if watched is getattr(self, 'origin', None):
            if event.type() == QEvent.Type.InputMethod:
                self._composing = bool(event.preeditString())
                self.check_current()
            elif event.type() == QEvent.Type.FocusOut:
                self._composing = False
                self.check_current()
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event); self.fit_available_screen()

    def fit_available_screen(self, available=None):
        fit_dialog(self, available)

    def _current(self):
        try:
            return (not self._closed_flag and not self._stale and not self.window.closing
                and self._job_generation == self.window._job_generation
                and not any(job is not self.job for job in self.window.jobs)
                and not _playing(self.window) and self.context == _context(self.window))
        except (ValueError, TypeError):
            return False

    def _available(self, *, allow_own_job=False):
        return (self._current() and (allow_own_job or not self.window.jobs)
            and self.window.isEnabled() and self.window.centralWidget().isEnabled()
            and QApplication.activeModalWidget() in (None, self)
            and QApplication.activePopupWidget() is None
            and getattr(self.window.shortcuts, '_composing', None) is None and not self._composing)

    def _draft_changed(self, *_):
        self.cancel_work()
        self.plan = None; self._last_error = None
        if self._current():
            try:
                origin = parse_source_position(self.origin.text().strip())
                self.plan = plan_linked_av_slip(self.window.editor, self.clip_id, origin)
            except Exception as exc:
                self._last_error = str(exc)
        self.refresh_text()

    def check_current(self, *_):
        if self._closed_flag or self._applying: return
        if self.window.closing or self.session_id != self.window.editor.session_id:
            self.reject(); return
        if not self._current():
            self._stale = True; self.plan = self.preview_receipt = None
            if self.job is not None: self.job.cancel.set()
        busy = bool(self.window.jobs)
        clip = next((c for c in self.window.project.clips if c.id == self.clip_id), None)
        try: changed = clip is not None and parse_source_position(self.origin.text().strip()) != clip.source_in
        except ValueError: changed = False
        available = self._available()
        ready = available and self.plan is not None and changed
        # Do not disable this field for its own preedit: FocusOut would clear
        # the composition gate while the old committed numeric text remains.
        self.origin.setEnabled(not busy and not self._stale and self.window.isEnabled()
                               and self.window.centralWidget().isEnabled())
        self.preview_button.setEnabled(ready)
        self.apply_button.setEnabled(ready and self.preview_receipt is not None)
        if self._stale:
            self.impact.setText(tr('This joint-slip preview has expired. Close and reopen for the current linked pair.'))
            text = tr('Project, selection or active role changed. No joint slip was applied.')
        elif self.job is not None:
            text = tr('Checking source… Cancel leaves the project unchanged.')
        elif busy:
            text = tr('Finish or cancel the current job before editing A/V links.')
        elif not available:
            text = tr('Finish the active dialog, popup or text composition before joint source slip.')
        elif self.preview_receipt is not None:
            text = tr('Preview verified. Apply verifies again and changes both source ranges atomically.')
        elif self.plan is not None and changed:
            text = tr('Ready to preview. No project change until Apply joint source slip.')
        elif not changed and self.plan is not None:
            text = tr('Enter a different source in. An unchanged range creates no history entry.')
        else:
            text = tr('This joint source slip is unavailable. Nothing has changed.')
        self.status.setText(text)

    def refresh_text(self, *_):
        if self._closed_flag: return
        self.setWindowTitle(tr('Joint source slip — linked A/V'))
        video, audio = self._old_clips[self.link.video_clip_id], self._old_clips[self.link.audio_clip_id]
        role = 'V' if self.clip_id == video.id else 'A'
        label = tr('New {role} source in · exact project frames', role=role)
        self.origin_label.setText(label); self.origin.setAccessibleName(label)
        self.scope.setText(tr(SLIP_SCOPE)); self.limits.setText(tr(SLIP_LIMITS)); self.check_note.setText(tr(SLIP_CHECK))
        delta = None
        if self.plan is not None:
            delta = parse_source_position(self.plan.source_in)-self._old_clips[self.clip_id].source_in
        self.summary.setText(tr('Active source anchor: {role}\nCommon source delta: {delta} project frames',
            role=role, delta=str(delta) if delta is not None else '—'))
        lines = []
        for member, tag in ((video, 'V'), (audio, 'A')):
            origin = member.source_in + delta if delta is not None else member.source_in
            endpoint = member.source_out + delta if delta is not None else member.source_out
            lines.append(tr('{role}{track} · {clip_id}\nSource [{old_in}, {old_out}) → [{new_in}, {new_out})\nFixed timeline [{start}, {end}) · {duration} frames',
                role=tag, track=member.track if tag == 'V' else member.track-2, clip_id=member.id,
                old_in=str(member.source_in), old_out=str(member.source_out), new_in=str(origin), new_out=str(endpoint),
                start=member.start, end=member.end, duration=member.duration))
        lines.append(tr('Media {media_id} · {speed}×', media_id=video.media_id, speed=str(video.speed)))
        self.source.setText('\n\n'.join(lines))
        if self._last_error:
            self.impact.setText(tr('Cannot apply joint source slip: {error}', error=tr_error(self._last_error)))
        elif delta is not None:
            self.impact.setText(tr('V source [{video_in}, {video_out})\nA source [{audio_in}, {audio_out})\nBoth timeline intervals stay fixed.',
                video_in=str(video.source_in+delta), video_out=str(video.source_out+delta),
                audio_in=str(audio.source_in+delta), audio_out=str(audio.source_out+delta)))
        else: self.impact.setText('')
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
        expected_job_generation = self._job_generation + 1
        self._generation += 1; generation = self._generation
        self._last_error = None
        outcome = {'completed': False, 'failed': False}
        def current():
            return generation == self._generation and self.plan is plan and self._current()
        def accepted(value):
            if applying and not self._available(allow_own_job=True):
                # A modal/popup/preedit can appear while the worker observes
                # files. Its late receipt is not permission to commit now.
                self.preview_receipt = None; self.refresh_text(); return
            if not applying:
                self.preview_receipt = value; outcome['completed'] = True; self.refresh_text(); return
            self._applying = True; self.plan = self.preview_receipt = None
            result = self.window.edit(lambda: apply_linked_av_slip(self.window.editor, plan, value))
            if result is not None:
                outcome['completed'] = True
                self.window.select_clip(result.id); self.window.refresh_inspector()
                self.window.timeline.update(); self.window.shortcuts.update_enabled(); self.accept()
            else:
                outcome['failed'] = True; self._applying = False; self._stale = True; self.refresh_text()
        def setup(job):
            self.job = job; job_generation = self.window._job_generation
            if job_generation != expected_job_generation:
                self._stale = True; job.cancel.set()
            else:
                self._job_generation = job_generation
            job.failed.connect(lambda *_: outcome.update(failed=True))
            job.failed.connect(lambda error: self._failed(error) if current() and not job.cancel.is_set() else None)
            job.finished.connect(lambda: self._finished(job))
            job.finished.connect(lambda: self._finish_progress(job, job_generation, outcome))
        self.window.start_job(tr('Apply joint source slip') if applying else tr('Preview joint source slip'),
            lambda cancel, progress: verify_linked_av_slip(plan, previous=previous, cancel=cancel),
            accepted, 'linked_av_slip', before_start=setup, is_current=current)
        self.check_current()

    def _finish_progress(self, job, generation, outcome):
        window = self.window
        if window.closing or generation != window._job_generation or window.jobs: return
        if outcome['completed']: text, progress = tr('Ready · local files only'), 100
        elif job.cancel.is_set(): text, progress = tr('Canceled · sources unchanged'), 0
        elif outcome['failed']: text, progress = tr('Joint source slip check failed · project unchanged'), 0
        else: text, progress = tr('Joint source slip expired · project unchanged'), 0
        window.progress_label.setText(text); window.progress_bar.setValue(progress)

    def _failed(self, error):
        self._last_error = error; self.preview_receipt = None; self.refresh_text()

    def _finished(self, job):
        if self.job is job: self.job = None
        if not self._closed_flag: self.refresh_text()

    def cancel_work(self):
        self._generation += 1
        if self.job is not None: self.job.cancel.set()
        self.preview_receipt = None
        if not self._closed_flag and hasattr(self, 'origin'): self.check_current()

    def _closed(self, *_):
        if self._closed_flag: return
        self._closed_flag = True; self.cancel_work(); self.timer.stop()
        self.plan = self.preview_receipt = None; self._old_clips.clear()
        try: locale_events.changed.disconnect(self.refresh_text)
        except (RuntimeError, TypeError): pass
        try: self._playback_player.playbackStateChanged.disconnect(self.check_current)
        except (RuntimeError, TypeError): pass
        self._playback_player = None
        if self.window.linked_av_slip_dialog is self:
            self.window.linked_av_slip_dialog = None

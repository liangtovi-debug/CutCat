"""Explicit nonmodal staged audio-only cut edit; never source I/O or audition."""
from __future__ import annotations

from copy import deepcopy
import re

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QVBoxLayout

from .i18n import tr, tr_error, locale_events
from .i18n_widgets import QDialog, QLineEdit, QScrollArea, QWidget
from .linked_av_audio_roll import (apply_linked_audio_roll, linked_audio_align_candidate,
    linked_audio_roll_candidate, plan_linked_audio_align, plan_linked_audio_roll,
    resolve_linked_audio_roll)
from .ui_interval_preview import action_button, fit_dialog, plain_label
from .ui_linked_av import _context

AUDIO_ROLL_SELECTION = 'Select exactly two complete adjacent linked A/V pairs, with an active V or A role.'
AUDIO_ROLL_SCOPE = ('Move only the shared audio cut while both picture clips stay exactly unchanged. '
    'Review all four roles below, then Apply once. No source file is read or written and no preview is rendered.')
AUDIO_ROLL_LIMITS = ('Audio fades keep their lengths and follow their edited edges. Source content and audio output may change. '
    'Align audio cut to picture stages a reset; Apply is still required. Strict linking is restored only when the complete rational maps, including partial source tails, match. '
    'This is not a crossfade, audio overlap, external-recording sync or ripple edit.')
AUDIO_ROLL_STALE = 'This audio-cut preview has expired. Cancel and reopen for the current project, selection and active role.'


def _playing(window):
    return window.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState


def audio_roll_qualification(window):
    if (window.closing or window.jobs or not window.isEnabled()
            or not window.centralWidget().isEnabled()):
        return None, 'Finish or cancel the current job before editing A/V links.'
    if (window.active_selection_type != 'clip' or len(window.selected_clip_ids) != 4
            or window.selection not in window.selected_clip_ids):
        return None, AUDIO_ROLL_SELECTION
    if window.selection_edit_locked():
        return None, 'Unlock both selected tracks before editing A/V links.'
    if _playing(window):
        return None, 'Pause playback before audio-only roll.'
    try:
        return resolve_linked_audio_roll(window.project, window.selected_clip_ids), None
    except (ValueError, TypeError) as exc:
        return None, str(exc)


def open_linked_audio_roll(window):
    previous = window.linked_audio_roll_dialog
    if previous is not None:
        previous.check_current()
        if not previous._closed_flag:
            previous.show(); previous.raise_(); previous.activateWindow(); return previous
    if (audio_roll_qualification(window)[0] is None
            or QApplication.activeModalWidget() is not None
            or getattr(window.shortcuts, '_composing', None) is not None):
        return None
    window.timeline.cancel_gestures()
    dialog = LinkedAudioRollDialog(window)
    window.linked_audio_roll_dialog = dialog
    dialog.show()
    return dialog


class LinkedAudioRollDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        pairs, reason = audio_roll_qualification(window)
        if pairs is None:
            raise ValueError(reason)
        self.window = window
        self.context = _context(window)
        self.session_id = window.editor.session_id
        self.job_generation = window._job_generation
        self.original = deepcopy(window.project)
        self.clip_ids = tuple(key for pair in pairs for key in pair.clip_ids)
        self._old_clips = {clip.id: clip for clip in self.original.clips}
        self.picture_cut = self._old_clips[pairs[0].video_clip_id].end
        self.audio_cut = self._old_clips[pairs[0].audio_clip_id].end
        self.minimum_cut = self._old_clips[pairs[0].audio_clip_id].start + 1
        self.maximum_cut = self._old_clips[pairs[1].audio_clip_id].end - 1
        self.plan = self._preview = None
        self.reset_requested = False
        self._closed_flag = self._stale = self._applying = False
        self._composing = False
        self._last_error = None
        self._requested = self.audio_cut
        self.setModal(False); self.resize(660, 710)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); body = QVBoxLayout(content)
        self.scope = plain_label('')
        self.summary = plain_label(''); body.addWidget(self.summary)
        self.cut_label = plain_label(''); body.addWidget(self.cut_label)
        self.cut_edit = QLineEdit(str(self.audio_cut)); self.cut_edit.setMaxLength(32)
        self.cut_label.setBuddy(self.cut_edit); body.addWidget(self.cut_edit)
        self.cut_edit.installEventFilter(self)
        self.bounds = plain_label(''); body.addWidget(self.bounds)
        body.addWidget(self.scope)
        self.impact = plain_label(''); body.addWidget(self.impact)
        self.limits = plain_label(''); body.addWidget(self.limits)
        self.status = plain_label(''); body.addWidget(self.status); body.addStretch()
        self.scroll.setWidget(content); layout.addWidget(self.scroll, 1)
        row = QVBoxLayout()
        self.align_button = action_button('Align audio cut to picture', self.stage_align, row)
        self.apply_button = action_button('Apply audio-only roll', self.apply, row)
        self.cancel_button = action_button('Cancel', self.reject, row)
        layout.addLayout(row)
        self.finished.connect(self._closed)
        self.cut_edit.textChanged.connect(self._draft_changed)
        locale_events.changed.connect(self.refresh_text)
        self.timer = QTimer(self); self.timer.setInterval(150)
        self.timer.timeout.connect(self.check_current); self.timer.start()
        window.player.playbackStateChanged.connect(self.check_current)
        self._draft_changed()

    def eventFilter(self, watched, event):
        if watched is getattr(self, 'cut_edit', None):
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
                and not self.window.jobs and self.job_generation == self.window._job_generation
                and not _playing(self.window) and self.context == _context(self.window)
                and self.original == self.window.project)
        except (ValueError, TypeError):
            return False

    def _available(self):
        return (self._current() and self.window.isEnabled() and self.window.centralWidget().isEnabled()
            and QApplication.activeModalWidget() in (None, self)
            and QApplication.activePopupWidget() is None
            and getattr(self.window.shortcuts, '_composing', None) is None and not self._composing)

    def _capture_plan(self):
        self.plan = self._preview = None; self._last_error = None; self._requested = None
        if not self._current():
            self._stale = True
            return
        try:
            text = self.cut_edit.text().strip()
            if not re.fullmatch(r'[0-9]{1,16}', text):
                raise ValueError('Audio cut must be a whole project frame within the displayed bounds.')
            self._requested = int(text)
            if self.reset_requested:
                self.plan = plan_linked_audio_align(self.window.editor, self.clip_ids)
                self._preview = linked_audio_align_candidate(self.window.project, self.clip_ids)
            else:
                self.plan = plan_linked_audio_roll(self.window.editor, self.clip_ids, self._requested)
                self._preview = linked_audio_roll_candidate(self.window.project, self.clip_ids, self._requested)
        except (ValueError, TypeError) as exc:
            self.plan = self._preview = None; self._last_error = str(exc)

    def _draft_changed(self, *_):
        if self._closed_flag or self._applying:
            return
        self.reset_requested = False
        self._capture_plan()
        self.refresh_text()

    def stage_align(self):
        self.check_current()
        if self._closed_flag or not self.align_button.isEnabled():
            return
        self.reset_requested = True
        self.cut_edit.blockSignals(True); self.cut_edit.setText(str(self.picture_cut)); self.cut_edit.blockSignals(False)
        self._capture_plan()
        self.refresh_text()

    def check_current(self, *_):
        if self._closed_flag or self._applying:
            return
        if self.window.closing or self.session_id != self.window.editor.session_id:
            self.reject(); return
        if not self._current():
            self._stale = True; self.plan = self._preview = None
        available = self._available()
        changed = self._preview is not None and self._preview != self.original
        # Keep the editor enabled during its own IME preedit so the user can
        # finish/cancel composition; disabling it would emit FocusOut, clear the
        # composition flag and accidentally re-enable Apply.
        self.cut_edit.setEnabled(self._current() and self.window.isEnabled()
                                 and self.window.centralWidget().isEnabled())
        self.align_button.setEnabled(available)
        self.apply_button.setEnabled(available and self.plan is not None and changed)
        if self._stale:
            self.status.setText(tr(AUDIO_ROLL_STALE))
        elif not available:
            self.status.setText(tr('Finish the active dialog, popup or text composition before applying the audio cut.'))
        elif self._last_error:
            self.status.setText(tr('Cannot stage audio-only roll: {error}', error=tr_error(self._last_error)))
        elif not changed:
            self.status.setText(tr('No change is staged. Existing audio edges and link modes will stay unchanged.'))
        elif self.reset_requested:
            self.status.setText(tr('Alignment is staged only. Apply restores strict links only where every source-map field matches.'))
        else:
            self.status.setText(tr('Audio-only roll is staged. Picture stays fixed; Apply changes both audio roles once.'))
        self.status.setAccessibleDescription(self.status.text())

    def refresh_text(self, *_):
        if self._closed_flag:
            return
        self.setWindowTitle(tr('Roll linked audio cut…'))
        self.scope.setText(tr(AUDIO_ROLL_SCOPE)); self.limits.setText(tr(AUDIO_ROLL_LIMITS))
        self.cut_label.setText(tr('Requested audio cut · whole project frame'))
        self.cut_edit.setAccessibleName(tr('Requested audio cut · whole project frame'))
        self.bounds.setText(tr('Timeline bounds: {minimum} to {maximum}; source handles, fades and keys may narrow this range. Picture cut stays at {picture}.',
            minimum=self.minimum_cut, maximum=self.maximum_cut, picture=self.picture_cut))
        kind = (tr('J cut · incoming audio starts before picture') if self._requested is not None and self._requested < self.picture_cut
            else tr('L cut · outgoing audio continues after picture') if self._requested is not None and self._requested > self.picture_cut
            else tr('Aligned audio and picture cuts') if self._requested is not None
            else tr('Enter a valid audio cut to review J/L alignment.'))
        self.summary.setText(tr('Fixed picture cut: {picture}\nCurrent audio cut: {current}\nRequested audio cut: {requested}\n{kind}',
            picture=self.picture_cut, current=self.audio_cut,
            requested=self.cut_edit.text() if self._requested is None else self._requested, kind=kind))
        media = {item.id: item for item in self.original.media}
        candidate = {clip.id: clip for clip in self._preview.clips} if self._preview is not None else self._old_clips
        lines = []
        for index, key in enumerate(self.clip_ids):
            old, new = self._old_clips[key], candidate[key]
            role = tr(('Left V', 'Left A', 'Right V', 'Right A')[index])
            values = dict(role=role, name=media[old.media_id].name, clip_id=key, start=new.start,
                end=new.end, source_in=str(new.source_in), source_out=str(new.source_out))
            if index in (0, 2):
                lines.append(tr('{role} · {name} · {clip_id}\nPicture unchanged: [{start}, {end}) · source [{source_in}, {source_out})', **values))
            else:
                lines.append(tr('{role} · {name} · {clip_id}\nAudio [{old_start}, {old_end}) → [{start}, {end})\nSource [{old_in}, {old_out}) → [{source_in}, {source_out})',
                    **values, old_start=old.start, old_end=old.end, old_in=str(old.source_in), old_out=str(old.source_out)))
        self.impact.setText('\n\n'.join(lines)); self.impact.setAccessibleDescription(self.impact.text())
        self.check_current()

    def apply(self):
        self.check_current()
        if self._closed_flag or self._applying or not self.apply_button.isEnabled():
            return
        # Do not call _capture_plan here: Apply cannot renew expired context.
        plan = self.plan
        self._applying = True; self.apply_button.setEnabled(False)
        result = self.window.edit(lambda: apply_linked_audio_roll(self.window.editor, plan))
        if result is not None:
            self.accept()
        else:
            self._applying = False; self._stale = True; self.plan = self._preview = None
            self.refresh_text()

    def cancel_work(self):
        if not self._closed_flag:
            self._stale = True; self.plan = self._preview = None; self.check_current()

    def _closed(self, *_):
        if self._closed_flag:
            return
        self._closed_flag = True; self.timer.stop(); self.plan = self._preview = None
        try: locale_events.changed.disconnect(self.refresh_text)
        except (RuntimeError, TypeError): pass
        try: self.window.player.playbackStateChanged.disconnect(self.check_current)
        except (RuntimeError, TypeError): pass
        if self.window.linked_audio_roll_dialog is self:
            self.window.linked_audio_roll_dialog = None

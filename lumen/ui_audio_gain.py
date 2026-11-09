"""Manual sample-local gain keys: explicit edits, no decode or live audio work."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import math
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QVBoxLayout, QSizePolicy

from .audio_gain import (AudioGainKeyframe, MAX_AUDIO_GAIN_SAMPLE,
    MAX_AUDIO_GAIN_KEYFRAMES, evaluate_audio_gain_offset)
from .timing import sample_boundary
from .i18n import tr, tr_error, locale_events
from .i18n_widgets import QDialog, QScrollArea, QWidget, QFormLayout, QLineEdit, QComboBox
from .ui_interval_preview import plain_label, action_button, fit_dialog
from .ui_audio_gain_timeline import AudioGainTimeline, AUDIO_GAIN_TIMELINE_HELP

GAIN_SCOPE = ('Manual clip volume keys · exact 48 kHz OUTPUT clip-local samples, not source samples or video frames. '
    'Base gain + envelope offset gives the combined gain before fades. No automatic limiting. '
    'Linear means linear in dB; Hold keeps the outgoing value until the next key. Endpoints hold outside the key range.')
GAIN_WORKFLOW = ('Add, replace, move or delete is one undoable edit. Typing alone changes nothing. '
    'Close discards uncommitted fields, not completed edits. Rebuild preview or export to hear changes. '
    'The source waveform remains pre-gain / pre-fade; this editor does not decode audio.')
GAIN_HIDDEN = ('Hidden keys are retained as interpolation handles. Whole moves, slip and speed changes keep local sample times; '
    'left trims and splits rebase them. Exact signed sample entry can reach hidden keys; dragging uses visible samples only.')
GAIN_SAMPLE_ERROR = 'Enter a signed integer sample within ±1,099,511,627,776.'
GAIN_OFFSET_ERROR = 'Enter a finite envelope offset from -96 to +24 dB.'
GAIN_DUPLICATE_ERROR = 'Another audio gain key already occupies that sample.'
GAIN_LIMIT_ERROR = 'Audio gain supports at most 128 keys per clip.'
GAIN_DURATION_ERROR = 'This clip exceeds the audio gain sample budget; existing keys are unchanged.'


def _budget(window, clip):
    return (sample_boundary(clip.end, window.project.fps, 48000)
            - sample_boundary(clip.start, window.project.fps, 48000))


def can_open_audio_gain(window):
    clip = window.selected_parameter_clip()
    if (window.closing or window.jobs or clip is None or window.active_selection_type != 'clip'
            or window.project.is_track_edit_locked(clip.track)):
        return False
    media = next((m for m in window.project.media if m.id == clip.media_id), None)
    return bool(media and media.has_audio)


def open_audio_gain(window):
    previous = getattr(window, 'audio_gain_dialog', None)
    if previous is not None:
        previous.check_current()
        if not previous._closed_flag:
            previous.show(); previous.raise_(); previous.activateWindow()
            return previous
    if not can_open_audio_gain(window):
        return None
    dialog = AudioGainDialog(window)
    window.audio_gain_dialog = dialog
    window.timeline.cancel_gestures()
    dialog.show()
    return dialog


def close_audio_gain(window):
    dialog = getattr(window, 'audio_gain_dialog', None)
    if dialog is not None:
        dialog.reject()


class AudioGainDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        clip = window.selected_parameter_clip()
        self.clip_id = clip.id
        self._selection_context = (tuple(window.selected_clip_ids), window.selection_generation)
        self.editor = window.editor
        self.session_id = window.editor.session_id
        self._closed_flag = self._applying = self._loading = False
        self._signature = None
        self._selected = None
        self._error = None
        self._reference = 0
        self.duration = _budget(window, clip)
        self._supported = 1 <= self.duration <= MAX_AUDIO_GAIN_SAMPLE
        self.setModal(False); self.resize(620, 720)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); content.setMinimumSize(0, 0); body = QVBoxLayout(content)
        self.scope = plain_label(tr(GAIN_SCOPE))
        self.summary = plain_label(''); body.addWidget(self.summary)
        self.timeline = AudioGainTimeline(self.duration if self._supported else 1)
        body.addWidget(self.timeline)
        self.timeline.sampleRequested.connect(self.set_reference)
        self.timeline.keySelected.connect(self.select_key)
        self.timeline.moveRequested.connect(self.move_key)
        self.timeline.gestureChanged.connect(self._gesture_changed)
        self.timeline_help = plain_label(tr(AUDIO_GAIN_TIMELINE_HELP))
        form = QFormLayout(); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.key_selector = QComboBox()
        self.key_selector.setMinimumContentsLength(8)
        self.key_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.key_selector.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.key_selector.setAccessibleName(tr('Select audio gain key, including hidden keys'))
        self.key_selector.currentIndexChanged.connect(self._select_listed)
        form.addRow(tr('Audio gain key'), self.key_selector)
        self.sample_edit = QLineEdit('0'); self.sample_edit.setMaxLength(16)
        self.sample_edit.setAccessibleName(tr('Exact clip-local sample'))
        self.sample_edit.setToolTip(tr(GAIN_SAMPLE_ERROR))
        form.addRow(tr('Exact clip-local sample'), self.sample_edit)
        self.milliseconds = plain_label(''); form.addRow(self.milliseconds)
        self.offset_edit = QLineEdit('0'); self.offset_edit.setMaxLength(32)
        self.offset_edit.setAccessibleName(tr('Envelope offset · dB'))
        self.offset_edit.setToolTip(tr(GAIN_OFFSET_ERROR))
        form.addRow(tr('Envelope offset · dB'), self.offset_edit)
        self.interpolation = QComboBox()
        self.interpolation.addItem(tr('Linear in dB'), 'linear'); self.interpolation.addItem(tr('Hold'), 'hold')
        self.interpolation.setAccessibleName(tr('Outgoing audio gain interpolation'))
        form.addRow(tr('Outgoing interpolation'), self.interpolation)
        body.addLayout(form)
        self.sample_edit.textChanged.connect(self._update_readout)
        self.offset_edit.textChanged.connect(self._update_readout)
        self.add_button = action_button('Add / replace audio gain key', self.add_key, body)
        self.move_button = action_button('Move selected audio gain key', self.move_selected, body)
        self.delete_button = action_button('Delete selected audio gain key', self.delete_key, body)
        self.clear_button = action_button('Clear audio gain envelope', self.clear_keys, body)
        self.hidden = plain_label(''); body.addWidget(self.hidden)
        body.addWidget(self.scope)
        body.addWidget(self.timeline_help)
        self.hidden_help = plain_label(tr(GAIN_HIDDEN)); body.addWidget(self.hidden_help)
        self.workflow = plain_label(tr(GAIN_WORKFLOW)); body.addWidget(self.workflow)
        self.status = plain_label(''); body.addWidget(self.status)
        body.addStretch()
        self.scroll.setWidget(content); layout.addWidget(self.scroll, 1)
        self.close_button = action_button('Close', self.reject, layout)
        self.finished.connect(self._closed)
        locale_events.changed.connect(self.refresh_text)
        self.timer = QTimer(self); self.timer.setInterval(100)
        self.timer.timeout.connect(self.check_current); self.timer.start()
        self.check_current(); self.refresh_text()

    def showEvent(self, event):
        super().showEvent(event); self.fit_available_screen()

    def fit_available_screen(self, available=None):
        fit_dialog(self, available)

    def _clip(self):
        return next((c for c in self.window.project.clips if c.id == self.clip_id), None)

    def check_current(self):
        if self._closed_flag or self._applying:
            return
        if (self.window.closing or self.window.editor is not self.editor
                or self.window.editor.session_id != self.session_id
                or self.window.active_selection_type != 'clip'
                or (tuple(self.window.selected_clip_ids), self.window.selection_generation) != self._selection_context
                or self.window.selected_parameter_clip() is None
                or self.window.selected_parameter_clip().id != self.clip_id):
            self.reject(); return
        clip = self._clip()
        if clip is None:
            self.reject(); return
        duration = _budget(self.window, clip)
        signature = (self.window.editor.revision, clip.start, duration, clip.gain_db,
                     clip.muted, clip.source_audio_enabled, tuple((k.sample, k.offset_db, k.interpolation)
                     for k in clip.audio_gain_keyframes))
        if signature != self._signature:
            self.timeline.cancel_gesture()
            self._signature = signature
            self.duration = duration; self._supported = 1 <= duration <= MAX_AUDIO_GAIN_SAMPLE
            self.timeline.duration = duration if self._supported else 1
            self._reference = min(max(self._reference, 0), self.timeline.duration - 1)
            if self._selected not in {k.sample for k in clip.audio_gain_keyframes}:
                self._selected = None
            self._refresh_keys()
        ready = can_open_audio_gain(self.window) and self._supported
        for control in (self.timeline, self.sample_edit, self.offset_edit, self.interpolation, self.key_selector):
            control.setEnabled(ready)
        self._buttons(ready)
        self._update_readout()

    def _buttons(self, ready=None):
        if ready is None:
            ready = can_open_audio_gain(self.window) and self._supported and not self._closed_flag
        ready = ready and not self.timeline.gesture_active
        self.add_button.setEnabled(ready)
        self.move_button.setEnabled(ready and self._selected is not None)
        self.delete_button.setEnabled(ready and self._selected is not None)
        clip = self._clip()
        self.clear_button.setEnabled(ready and bool(clip and clip.audio_gain_keyframes))

    def _gesture_changed(self, *_):
        self._buttons()

    def _refresh_keys(self):
        clip = self._clip()
        self.key_selector.blockSignals(True); self.key_selector.clear()
        self.key_selector.addItem(tr('No key selected'), None)
        for key in clip.audio_gain_keyframes:
            visibility = tr('visible') if 0 <= key.sample < self.duration else tr('hidden')
            self.key_selector.addItem(tr('{sample} samples · {offset} dB · {visibility}',
                sample=key.sample, offset=format(key.offset_db, '.17g'), visibility=visibility), key.sample)
        self.key_selector.setCurrentIndex(self.key_selector.findData(self._selected))
        self.key_selector.blockSignals(False)
        if self._selected is not None:
            key = next(k for k in clip.audio_gain_keyframes if k.sample == self._selected)
            self._set_fields(key.sample, key.offset_db, key.interpolation)
        else:
            self._set_fields(self._reference, evaluate_audio_gain_offset(clip.audio_gain_keyframes, self._reference), 'linear')
        self.timeline.set_state([key.sample for key in clip.audio_gain_keyframes], self._reference, self._selected)
        self.refresh_text()

    def _set_fields(self, sample, offset, interpolation):
        self._loading = True
        self.sample_edit.setText(str(sample)); self.offset_edit.setText(format(offset, '.17g'))
        self.interpolation.setCurrentIndex(self.interpolation.findData(interpolation))
        self._loading = False

    def _select_listed(self, *_):
        sample = self.key_selector.currentData()
        if sample is None:
            self._selected = None; self._refresh_keys(); self._buttons()
        else:
            self.select_key(sample)

    def select_key(self, sample):
        if self._closed_flag:
            return
        clip = self._clip()
        if clip is None or sample not in {k.sample for k in clip.audio_gain_keyframes}:
            return
        self._selected = sample
        self._reference = min(max(sample, 0), self.duration - 1)
        self._error = None; self._refresh_keys(); self._buttons()

    def set_reference(self, sample):
        self._selected = None; self._reference = int(sample)
        self._error = None; self._refresh_keys(); self._buttons()

    def _sample(self):
        text = self.sample_edit.text().strip()
        if not re.fullmatch(r'[+-]?[0-9]+', text):
            raise ValueError(GAIN_SAMPLE_ERROR)
        sample = int(text)
        if not -MAX_AUDIO_GAIN_SAMPLE <= sample <= MAX_AUDIO_GAIN_SAMPLE:
            raise ValueError(GAIN_SAMPLE_ERROR)
        return sample

    def _offset(self):
        try:
            offset = float(self.offset_edit.text().strip())
        except ValueError:
            raise ValueError(GAIN_OFFSET_ERROR) from None
        if not math.isfinite(offset) or not -96 <= offset <= 24:
            raise ValueError(GAIN_OFFSET_ERROR)
        return offset

    def _update_readout(self, *_):
        if self._loading or self._closed_flag:
            return
        clip = self._clip()
        if clip is None:
            return
        try:
            sample = self._sample()
            self.milliseconds.setText(tr('{sample} samples = {milliseconds} ms at 48 kHz',
                sample=sample, milliseconds=f'{sample / 48:.6f}'))
            offset = evaluate_audio_gain_offset(clip.audio_gain_keyframes, sample)
            self.summary.setText(tr('Saved curve at sample {sample}: base {base} dB + offset {offset} dB = {total} dB before fades. No limiting.',
                sample=sample, base=f'{clip.gain_db:.6g}', offset=f'{offset:.6g}', total=f'{clip.gain_db + offset:.6g}'))
        except ValueError:
            self.milliseconds.setText(tr(GAIN_SAMPLE_ERROR))
        if not self._supported:
            self.status.setText(tr(GAIN_DURATION_ERROR))
        elif self._error:
            self.status.setText(tr('Audio gain edit not applied: {error}', error=tr_error(self._error)))
        elif not clip.source_audio_enabled or clip.muted:
            self.status.setText(tr('This clip is silent or source audio is disabled. Keys do not enable or unmute it.'))
        elif not can_open_audio_gain(self.window):
            self.status.setText(tr('Audio gain editing is paused while busy or the track is locked.'))
        else:
            self.status.setText(tr('Fields are a draft. Completed edits require a new preview to hear them.'))

    def refresh_text(self, *_):
        if self._closed_flag:
            return
        self.setWindowTitle(tr('Clip volume keyframes · 48 kHz samples'))
        clip = self._clip()
        if clip is not None:
            hidden = sum(not 0 <= key.sample < self.duration for key in clip.audio_gain_keyframes)
            self.hidden.setText(tr('{count}/128 keys · {hidden} hidden handles · visible samples [0, {end})',
                count=len(clip.audio_gain_keyframes), hidden=hidden, end=self.duration))
        self._update_readout()

    def _commit(self, keys, selected):
        self.check_current()
        if self._closed_flag or not can_open_audio_gain(self.window) or not self._supported:
            return
        clip = self._clip()
        if keys == clip.audio_gain_keyframes:
            self._selected = selected; self._refresh_keys(); self._buttons(); return
        self._applying = True
        try:
            result = self.window.edit(lambda: self.window.editor.update_clip(self.clip_id, audio_gain_keyframes=keys))
            if result is not None:
                self._selected = selected; self._error = None
            else:
                # MainWindow.edit reports and swallows a rejected transaction.
                # Reload authoritative keys rather than inventing a selection.
                self._error = 'Audio gain edit was rejected; saved keys are unchanged.'
                self._signature = None
        finally:
            self._applying = False
        self.check_current()

    def _run_edit(self, function):
        self.check_current()
        if self._closed_flag or not can_open_audio_gain(self.window) or not self._supported:
            return
        try:
            function()
        except ValueError as exc:
            self._error = str(exc); self._update_readout()

    def add_key(self):
        def change():
            sample, offset = self._sample(), self._offset()
            keys = deepcopy(self._clip().audio_gain_keyframes)
            keys = [key for key in keys if key.sample != sample]
            if len(keys) >= MAX_AUDIO_GAIN_KEYFRAMES:
                raise ValueError(GAIN_LIMIT_ERROR)
            keys.append(AudioGainKeyframe(sample, offset, self.interpolation.currentData()))
            self._commit(sorted(keys, key=lambda key: key.sample), sample)
        self._run_edit(change)

    def move_selected(self):
        self._run_edit(lambda: self.move_key(self._selected, self._sample()))

    def move_key(self, origin, destination):
        def change():
            if origin is None or origin == destination:
                return
            keys = deepcopy(self._clip().audio_gain_keyframes)
            if destination in {key.sample for key in keys}:
                raise ValueError(GAIN_DUPLICATE_ERROR)
            if origin not in {key.sample for key in keys}:
                return
            keys = [replace(key, sample=destination) if key.sample == origin else key for key in keys]
            self._commit(sorted(keys, key=lambda key: key.sample), destination)
        self._run_edit(change)

    def delete_key(self):
        self._run_edit(lambda: self._commit([key for key in self._clip().audio_gain_keyframes
            if key.sample != self._selected], None))

    def clear_keys(self):
        self._run_edit(lambda: self._commit([], None))

    def reject(self):
        self.timeline.cancel_gesture()
        super().reject()

    def _closed(self, *_):
        if self._closed_flag:
            return
        self._closed_flag = True; self.timer.stop(); self.timeline.cancel_gesture()
        if getattr(self.window, 'audio_gain_dialog', None) is self:
            self.window.audio_gain_dialog = None

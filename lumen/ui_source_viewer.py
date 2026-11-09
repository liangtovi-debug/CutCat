"""Session-only source inspection and explicit, guarded source edits.

Metadata and plans are pure GUI work. All source reads, identity checks, proxy
creation and cleanup run in the main window's owned worker registry. There is
one active request and at most one replaceable pending request per viewer.
"""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt, QTimer, QUrl, QRectF, Signal, Slot
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import QVBoxLayout, QSizePolicy

from .i18n import tr, tr_error, locale_events
from .i18n_widgets import QDialog, QLabel, QPushButton, QWidget, QScrollArea, QSpinBox, QSlider, QComboBox, QFormLayout, QCheckBox
from .source_viewer import SourceViewerSession, SourcePreviewBackend
from .ui_source_ranges import SavedSourceRangesMixin


class SourceCanvas(QWidget):
    """I/O shortcuts exist on this focused surface only, never globally."""
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.image = QImage()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(200, 113)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName(tr('Source viewer image'))
        self.setAccessibleDescription(tr('Focus this image to use I for Mark In and O for Mark Out.'))

    def set_pixels(self, result):
        self.image = QImage(result.pixels, result.width, result.height,
                            result.width * 3, QImage.Format.Format_RGB888).copy()
        self.update()

    def clear(self):
        self.image = QImage()
        self.update()

    def keyPressEvent(self, event):
        if (self.hasFocus() and not event.modifiers() and not event.isAutoRepeat()
                and event.key() in (Qt.Key.Key_I, Qt.Key.Key_O)):
            (self.dialog.mark_in if event.key() == Qt.Key.Key_I else self.dialog.mark_out)()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#0b1016'))
        if self.image.isNull():
            return
        scale = min(self.width() / self.image.width(), self.height() / self.image.height())
        width, height = self.image.width() * scale, self.image.height() * scale
        painter.drawImage(QRectF((self.width() - width) / 2, (self.height() - height) / 2,
                                 width, height), self.image)


class SourceViewerDialog(SavedSourceRangesMixin, QDialog):
    # Job.progress is a numeric application-wide signal. Exact-audio stages
    # carry a localized message instead, delivered only on this GUI receiver.
    # Both job identity and the captured context are required: a queued event
    # from canceled work must not revive the old preparation state.
    _worker_progress = Signal(object, object, str, object)

    def __init__(self, window, media_id):
        super().__init__(window)
        self.window = window
        self.session = SourceViewerSession(window.project, media_id, allow_empty=any(
            entry.media_id == media_id for entry in window.project.source_ranges))
        self.backend = SourcePreviewBackend()
        self.job = None
        self._progress_job = None
        self._pending = None
        self._generation = 0
        self._closed_flag = False
        self._cleanup_started = False
        self._observed = self._context()
        self.plan = None
        self.plan_context = None
        self.plan_identity = None
        self.identity = None
        self.player = None
        self.sink = None
        self.audio = QAudioOutput(self)
        self.audio.setVolume(.8)
        self.captured_start = window.timeline.playhead
        self._applying = False
        self._last_error = None
        self._impact_error = None
        self._insert_follow_tracks = set()
        self.setModal(False)
        self.setWindowTitle(tr('Source viewer · mark and insert / overwrite'))
        self.resize(740, 820)
        self.setMinimumSize(320, 360)
        outer = QVBoxLayout(self)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget(); layout = QVBoxLayout(body)
        self.source_name = self._label('')
        layout.addWidget(self.source_name)
        self.session_note = self._label(tr('Session-only source cursor and working marks. Save a named range to retain its exact bounds in the project; closing still clears unsaved marks. Source files stay untouched.'))
        layout.addWidget(self.session_note)
        self.canvas = SourceCanvas(self); self.canvas.setMinimumHeight(190)
        layout.addWidget(self.canvas)
        self.source_note = self._label(tr('Source only, after input normalization. No timeline grade, effects or mix. Audition is limited to 30 seconds from the source cursor.'))
        self.audition_note = self._label(tr('Audition does not move the exact source cursor. Marks are disabled during playback; stop and inspect a source frame before marking.'))
        layout.addWidget(self.source_note)
        self.codec_note = self._label(tr('Compressed audio (AAC/MP3/FLAC), including audio inside video: windows are no longer limited to 30 seconds after the audio origin. Exact PCM preparation scans from the origin; the first late window takes longer. Only requested windows are retained, and unchanged cached windows can be reused. Video still inspection remains independent.'))
        layout.addWidget(self.codec_note)
        self.cache_note = self._label(tr('Session-only exact audio cache, not project media, a proxy replacement or an export source. Closing clears it. One worker; up to 120 seconds per operation, 30 minutes of cumulative origin scanning, and 48 MiB shared cache and pending storage. Selected and affected proof ranges still total at most 30 seconds.'))
        layout.addWidget(self.cache_note)
        layout.addWidget(self.audition_note)
        form = QFormLayout(); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.cursor_spin = QSpinBox(); self.cursor_spin.setRange(0, max(0, min(2147483647, self.session.total_frames - 1)))
        self.cursor_spin.setAccessibleName(tr('Source cursor in project frames'))
        form.addRow(tr('Source cursor in project frames'), self.cursor_spin)
        self.cursor_slider = QSlider(Qt.Orientation.Horizontal); self.cursor_slider.setRange(0, self.cursor_spin.maximum())
        self.cursor_slider.setAccessibleName(tr('Source cursor in project frames'))
        form.addRow(self.cursor_slider)
        layout.addLayout(form)
        self.inspect_button = self._button('Inspect source frame', self.inspect_frame, layout)
        self.audition_button = self._button('Audition · up to 30s', self.audition, layout)
        self.stop_button = self._button('Stop / cancel preview', self.cancel_work, layout)
        self.stop_button.setToolTip(tr('Cancel source preparation, audition or edit verification. No source edit is applied while preparation is running.'))
        self.preparation_status = self._label('')
        self.preparation_status.setAccessibleName(tr('Exact source audio preparation progress'))
        self.preparation_status.hide()
        layout.addWidget(self.preparation_status)
        self.mark_in_button = self._button('Mark In · I', self.mark_in, layout)
        self.mark_out_button = self._button('Mark Out · O', self.mark_out, layout)
        self.clear_button = self._button('Reset to whole source', self.clear_marks, layout)
        self.range_note = self._label(''); layout.addWidget(self.range_note)
        self.mark_note = self._label(tr('Marks use whole project frames: In = current frame; Out = current frame + 1, so the displayed frame is included. I/O keys work only while the source image has focus.'))
        layout.addWidget(self.mark_note)
        self._build_saved_ranges(layout)
        form = QFormLayout(); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.mode_choice = QComboBox()
        self.mode_choice.addItem(tr('Overwrite'), 'overwrite')
        self.mode_choice.addItem(tr('Insert · this track only'), 'insert')
        self.mode_choice.setAccessibleName(tr('Source edit mode'))
        self.mode_choice.setMinimumWidth(0)
        self.mode_choice.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        form.addRow(tr('Source edit mode'), self.mode_choice)
        self.track_choice = QComboBox()
        for label in ('V1 · base picture', 'V2 · upper picture', 'Music 1', 'Music 2'):
            self.track_choice.addItem(tr(label))
        self.track_choice.setCurrentIndex(window.track_choice.currentIndex())
        self.track_choice.setAccessibleName(tr('Source edit destination track'))
        form.addRow(tr('Source edit destination track'), self.track_choice)
        layout.addLayout(form)
        self.participation_panel = QWidget()
        participation = QVBoxLayout(self.participation_panel)
        participation.setContentsMargins(0, 0, 0, 0)
        self.participation_title = self._label(tr('Move downstream items for this insert only'))
        participation.addWidget(self.participation_title)
        self.insert_track_checks = []
        for track, label in enumerate(('V1 · base picture', 'V2 · upper picture', 'Music 1', 'Music 2')):
            checkbox = QCheckBox(tr(label))
            checkbox.setMinimumWidth(0)
            checkbox.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            checkbox.setToolTip(tr('Participating tracks move only whole downstream clips. A follower crossing the captured start blocks the entire insert.'))
            checkbox.setAccessibleName(tr('Participate: {track}', track=tr(label)))
            participation.addWidget(checkbox)
            self.insert_track_checks.append(checkbox)
            checkbox.toggled.connect(lambda checked, track=track: self._participation_changed(track, checked))
        self.follow_captions_check = QCheckBox(tr('Move captions'))
        self.follow_stickers_check = QCheckBox(tr('Move stickers'))
        for checkbox in (self.follow_captions_check, self.follow_stickers_check):
            checkbox.setMinimumWidth(0)
            checkbox.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            checkbox.setAccessibleName(checkbox.text())
            checkbox.setToolTip(tr('Move whole downstream items only. An item crossing the captured start blocks the entire insert; nothing is split or stretched.'))
            checkbox.toggled.connect(self._participation_changed)
            participation.addWidget(checkbox)
        self.participation_note = self._label(tr('The destination track always participates. Unchecked tracks and categories stay fixed. These choices are per operation, not persistent Sync Lock, linked A/V or independent source patching.'))
        participation.addWidget(self.participation_note)
        layout.addWidget(self.participation_panel)
        self.insert_warning = self._label(tr('INSERT MOVES THIS TRACK ONLY. Other tracks, captions and stickers stay fixed and may lose synchronization. This is not cross-track sync insert or linked A/V patching.'))
        # Weight-only styling survives the application stylesheet without
        # fixing a point size or shrinking the warning at high DPI.
        self.insert_warning.setStyleSheet('font-weight: 700;')
        layout.addWidget(self.insert_warning)
        self.destination_note = self._label(''); layout.addWidget(self.destination_note)
        self.capture_button = self._button('Capture playhead', self.capture_playhead, layout)
        self.capture_button.setAccessibleName(tr('Capture timeline playhead'))
        self.capture_button.setToolTip(tr('Capture timeline playhead'))
        self.preview_button = self._button('Preview overwrite impact', self.preview_source_edit, layout)
        self.impact = self._label(tr('Preview the impact before applying. No timeline edit has been made.'))
        self.impact.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.impact)
        self.overwrite_note = self._label(tr('Overwrite replaces only the chosen track range, without ripple. Other tracks, captions and overlays stay fixed. Video source audio follows existing clip audio rules; no linked A/V patching is created.'))
        layout.addWidget(self.overwrite_note)
        self.proof_note = self._label(tr('Overwrite verifies complete source video/audio timing across selected and affected ranges, limited to 30 seconds combined. Video must match the project frame rate. Ambiguous frame or audio sample boundaries are rejected.'))
        layout.addWidget(self.proof_note)
        self.apply_button = self._button('Apply overwrite once', self.apply_source_edit, layout)
        self.status = self._label(tr('Inspect the source or set marks, then preview the overwrite.'))
        layout.addWidget(self.status)
        self.close_button = self._button('Close', self.reject, layout)
        self.scroll.setWidget(body); outer.addWidget(self.scroll)
        self.cursor_spin.valueChanged.connect(self.seek)
        self.cursor_slider.valueChanged.connect(self.seek)
        self.track_choice.currentIndexChanged.connect(self._target_changed)
        self.mode_choice.currentIndexChanged.connect(self._target_changed)
        self.finished.connect(self._closed)
        self._worker_progress.connect(self._accept_worker_progress, Qt.ConnectionType.QueuedConnection)
        self.timer = QTimer(self); self.timer.setInterval(150)
        self.timer.timeout.connect(self.check_current); self.timer.start()
        locale_events.changed.connect(self._refresh_text)
        self.window.sound_audition.changed.connect(self._other_audition_changed)
        if hasattr(self.window, 'tts_preview_player'):
            self.window.tts_preview_player.playbackStateChanged.connect(self._other_playback_changed)
        self.window.player.playbackStateChanged.connect(self._other_playback_changed)
        self._sync_participation_controls()
        self._refresh_text()
        self._update_controls()

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_available_screen()

    def fit_available_screen(self, available=None):
        # Qt reports logical pixels. Reserve native chrome and keep scrolling
        # usable even on a high-DPI screen with less than 360 logical pixels.
        available = self.screen().availableGeometry() if available is None else available
        width = max(1, available.width() - 32)
        height = max(1, available.height() - 64)
        self.setMinimumSize(min(320, width), min(360, height))
        self.setMaximumSize(width, height)
        self.resize(min(self.width(), width), min(self.height(), height))
        # Publish wrapped-form height changes before callers scroll to actions.
        # Do not process events here: worker callbacks must not reenter fitting.
        self.scroll.widget().layout().activate()
        self.scroll.widget().updateGeometry()
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    def _label(self, text):
        label = QLabel(text); label.setWordWrap(True); label.setMinimumWidth(0)
        label.setTextFormat(Qt.TextFormat.PlainText)
        return label

    def _button(self, source, callback, layout):
        button = QPushButton(tr(source)); button.clicked.connect(callback)
        button.setAutoDefault(False)
        button.setAccessibleName(tr(source))
        button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        button.setToolTip(tr(source)); layout.addWidget(button)
        return button

    def _context(self):
        w = self.window
        return (id(w.editor), w.editor.session_id, w.editor.revision,
                id(w.project), w.revision, tuple(w.selected_media_ids()))

    def _usable(self):
        return (not self._closed_flag and not self.window.closing
                and self.session.total_frames > 0
                and tuple(self.window.selected_media_ids()) == (self.session.media_id,)
                and self.session.matches(self.window.project))

    def _token(self):
        return (self._generation, self._context(), self.session.generation,
                self.track_choice.currentIndex(), self.captured_start, self.mode_choice.currentData(),
                self.session.token, (self.session.cursor, self.session.in_frame, self.session.out_frame),
                self._participation(),
                self._saved_range_token())

    def _current(self, token):
        return self._usable() and token == self._token()

    def _set_status(self, text):
        self._last_error = None
        self.status.setText(text)

    def _set_error(self, error, *, wrapped=False):
        self._last_error = (str(error), wrapped)
        self._render_error()

    def _render_error(self):
        if self._last_error is not None:
            raw, wrapped = self._last_error
            text = tr_error(raw)
            self.status.setText(tr('Source operation failed: {error}', error=text) if wrapped else text)

    def _insert_mode(self):
        return self.mode_choice.currentData() == 'insert'

    def _participation(self):
        return (tuple(sorted(self._insert_follow_tracks | {self.track_choice.currentIndex()})),
                self.follow_captions_check.isChecked(), self.follow_stickers_check.isChecked())

    def _cross_track_insert(self):
        tracks, captions, stickers = self._participation()
        return len(tracks) > 1 or captions or stickers

    def _sync_participation_controls(self):
        destination = self.track_choice.currentIndex()
        for track, checkbox in enumerate(self.insert_track_checks):
            checkbox.blockSignals(True)
            checkbox.setChecked(track == destination or track in self._insert_follow_tracks)
            checkbox.blockSignals(False)
            checkbox.setAccessibleDescription(tr('Destination track: always participates.') if track == destination else
                tr('Optional follower track for this insert only.'))

    def _participation_changed(self, track=None, checked=None):
        if type(track) is int and checked is not None:
            if track != self.track_choice.currentIndex():
                if checked: self._insert_follow_tracks.add(track)
                else: self._insert_follow_tracks.discard(track)
        self._sync_participation_controls()
        self.invalidate(); self._refresh_text()
        self._set_status(tr('Insert participation changed. Preview again before applying.'))

    def _refresh_text(self, *_):
        insert = self._insert_mode()
        self.insert_warning.setVisible(insert)
        self.participation_panel.setVisible(insert)
        cross_track = self._cross_track_insert()
        self.mode_choice.setItemText(self.mode_choice.findData('insert'), tr(
            'Insert · chosen participants' if cross_track else 'Insert · this track only'))
        self.insert_warning.setText(tr(
            'INSERT MOVES ONLY THE CHOSEN PARTICIPANTS. Unchecked tracks, captions or stickers stay fixed and may lose synchronization. Followers crossing the captured start block the entire edit. This is not persistent Sync Lock or linked A/V patching.' if cross_track else
            'INSERT MOVES THIS TRACK ONLY. Other tracks, captions and stickers stay fixed and may lose synchronization. This is not cross-track sync insert or linked A/V patching.'))
        self.stop_button.setToolTip(tr(
            'Cancel source preparation, audition or edit verification. No source edit is applied while preparation is running.' if insert else
            'Cancel source preparation, audition or overwrite verification. No overwrite is applied while preparation is running.'))
        self.cache_note.setText(tr(
            'Session-only exact audio cache, not project media, a proxy replacement or an export source. Closing clears it. One worker; up to 120 seconds per operation, 30 minutes of cumulative origin scanning, and 48 MiB shared cache and pending storage. Selected and split source proof ranges still total at most 30 seconds.' if insert else
            'Session-only exact audio cache, not project media, a proxy replacement or an export source. Closing clears it. One worker; up to 120 seconds per operation, 30 minutes of cumulative origin scanning, and 48 MiB shared cache and pending storage. Selected and affected proof ranges still total at most 30 seconds.'))
        for button, source in (
                (self.preview_button, 'Preview insert impact' if insert else 'Preview overwrite impact'),
                (self.apply_button, 'Apply insert once' if insert else 'Apply overwrite once')):
            button.setText(tr(source)); button.setAccessibleName(tr(source)); button.setToolTip(tr(source))
        self.overwrite_note.setText(tr(
            'Insert preserves the destination content and gaps, splitting an eligible clip if needed. Chosen followers move whole downstream items; followers spanning the start block the entire edit. No settings are removed to force an edit.' if insert else
            'Overwrite replaces only the chosen track range, without ripple. Other tracks, captions and overlays stay fixed. Video source audio follows existing clip audio rules; no linked A/V patching is created.'))
        self.proof_note.setText(tr(
            'Insert verifies selected and split source ranges within 30 seconds combined. Whole downstream moves use exact local audio-compiler equivalence checks, not full-timeline decoding. Additional participants require one common integer audio-sample shift, including target remnants. Non-equivalent sample phases reject the entire edit.' if insert else
            'Overwrite verifies complete source video/audio timing across selected and affected ranges, limited to 30 seconds combined. Video must match the project frame rate. Ambiguous frame or audio sample boundaries are rejected.'))
        media = next((m for m in self.window.project.media if m.id == self.session.media_id), None)
        self.source_name.setText(tr('Source: {name} · {frames} project frames',
                                    name=media.name if media else self.session.media_id,
                                    frames=self.session.total_frames))
        self.range_note.setText(tr('Source selection: [{start}, {end}) · {duration} frames',
                                   start=self.session.in_frame, end=self.session.out_frame,
                                   duration=self.session.out_frame - self.session.in_frame))
        self.destination_note.setText(tr('Captured timeline start: frame {frame}. Moving the timeline playhead does not change this target.', frame=self.captured_start))
        if self.plan is not None:
            self._show_impact(self.plan)
        elif self._impact_error is not None:
            self._show_insert_blocker(self._impact_error)
        self.follow_captions_check.setAccessibleName(tr('Move captions'))
        self.follow_stickers_check.setAccessibleName(tr('Move stickers'))
        self._refresh_saved_ranges()
        self._render_error()

    def _update_controls(self):
        usable = self._usable()
        busy = self.job is not None or self._pending is not None
        for control in (self.cursor_spin, self.cursor_slider, self.mark_in_button,
                        self.mark_out_button, self.clear_button, self.track_choice, self.mode_choice, self.capture_button):
            control.setEnabled(usable and not self._applying)
        for control in (self.mark_in_button, self.mark_out_button, self.clear_button):
            control.setEnabled(usable and not self._applying and self.player is None)
        for track, control in enumerate(self.insert_track_checks):
            control.setEnabled(usable and not self._applying and track != self.track_choice.currentIndex())
        for control in (self.follow_captions_check, self.follow_stickers_check):
            control.setEnabled(usable and not self._applying)
        self.inspect_button.setEnabled(usable and not self._applying)
        self.audition_button.setEnabled(usable and not self._applying)
        self.preview_button.setEnabled(usable and not self._applying)
        self.stop_button.setEnabled(busy or self.player is not None)
        self._update_saved_range_controls()
        self.apply_button.setEnabled(usable and not busy and not self._applying
                                     and self.plan is not None and self.plan_context == self._token())

    def _clear_plan(self):
        self.plan = None; self.plan_context = None; self.plan_identity = None
        self._impact_error = None
        self.impact.setText(tr('Preview the impact before applying. No timeline edit has been made.'))
        self.impact.setToolTip(''); self.impact.setAccessibleDescription('')

    def invalidate(self, message=None):
        self._generation += 1
        self._invalidate_saved_ranges()
        self._pending = None
        if self.job is not None:
            self.job.cancel.set()
        self.stop_audition()
        self._clear_plan(); self.identity = None; self.canvas.clear()
        self._applying = False
        self._progress_job = None
        self.preparation_status.clear(); self.preparation_status.hide()
        if message:
            self._set_status(tr(message))
        self._update_controls()

    def check_current(self):
        if self._closed_flag:
            return
        context = self._context()
        if context != self._observed or not self._usable():
            self._observed = context
            if self.job is not None or self.plan is not None or self.identity is not None or self.player is not None:
                self.invalidate('Source or project changed. Reopen the selected source, or preview again before applying.')
            self._invalidate_saved_ranges()
            self._refresh_saved_ranges()
            self._update_controls()

    def cancel_work(self):
        self.invalidate('Source work canceled. No source edit was applied.')

    def seek(self, frame):
        if not self._usable() or frame == self.session.cursor:
            return
        self.invalidate()
        try:
            self.session.seek(frame)
        except ValueError as exc:
            self._set_error(exc); return
        self._sync_cursor()
        self._set_status(tr('Source cursor moved. Inspect explicitly to display this frame.'))
        self._refresh_text(); self._update_controls()

    def _sync_cursor(self):
        for control in (self.cursor_spin, self.cursor_slider):
            control.blockSignals(True); control.setValue(self.session.cursor); control.blockSignals(False)

    def _mark(self, method):
        if not self._usable() or self._applying or self.player is not None:
            return
        self.invalidate()
        try:
            method()
            self._set_status(tr('Marks updated for this viewer session only.'))
        except ValueError as exc:
            self._set_error(exc)
        self._refresh_text(); self._update_controls()

    def mark_in(self): self._mark(self.session.mark_in)
    def mark_out(self): self._mark(self.session.mark_out)
    def clear_marks(self): self._mark(self.session.clear_marks)

    def _target_changed(self, *_):
        self._sync_participation_controls()
        self.invalidate(); self._refresh_text()
        self._set_status(tr('Source edit mode or destination changed. Preview again before applying.'))

    def capture_playhead(self):
        if not self._usable(): return
        self.invalidate()
        self.captured_start = self.window.timeline.playhead
        self._refresh_text(); self._update_controls()

    def _submit(self, label, operation, accept):
        self.check_current()
        token = self._token()
        request = (label, operation, accept, token)
        if self.job is not None:
            self.job.cancel.set(); self._pending = request
            self._set_status(tr('Canceling the previous source request…'))
            self._update_controls(); return
        self._start_request(request)

    def _start_request(self, request):
        label, operation, accept, token = request
        if not self._current(token): return
        self._set_status(tr(label))
        lifecycle = {}
        def current():
            return self.job is lifecycle.get('job') and self._current(token)
        def success(value):
            if not current(): return
            self._progress_job = None
            accept(value, token)
        def installed(job):
            self.job = job; lifecycle['job'] = job
            self._progress_job = job
            job.failed.connect(lambda error: self._failed(error, token) if current() else None)
            job.finished.connect(lambda: self._finished(job))
        def run(cancel, progress):
            # This property is worker-scoped, not persistent UI state. It also
            # keeps the existing numeric Job progress contract unchanged.
            def report(message, **values):
                self._worker_progress.emit(lifecycle['job'], token, message, dict(values))
            self.backend.progress_callback = report
            try:
                return operation(cancel, progress)
            finally:
                if self.backend.progress_callback is report:
                    self.backend.progress_callback = None
        self.window.start_job(tr(label), run, success, 'source_viewer',
                              before_start=installed, is_current=current)
        self._update_controls()

    @Slot(object, object, str, object)
    def _accept_worker_progress(self, job, token, message, values):
        if (self.job is not job or self._progress_job is not job or not self._current(token)
                or job.cancel.is_set()):
            return
        text = tr(message, **values)
        self.preparation_status.setText(text)
        self.preparation_status.show()
        self._set_status(text)

    def _failed(self, error, token):
        if self._current(token):
            self._progress_job = None
            self._applying = False; self._clear_plan()
            self._saved_range_failed(token)
            self._set_error(error, wrapped=True)

    def _finished(self, job):
        if self.job is not job: return
        self.job = None
        self._progress_job = None
        self.preparation_status.clear(); self.preparation_status.hide()
        pending = self._pending; self._pending = None
        if self._closed_flag:
            self._cleanup()
        elif pending is not None:
            self._start_request(pending)
        self._update_controls()
        if not self.window.jobs and not self.window.closing:
            self.window.progress_label.setText(tr('Ready · local files only'))

    def inspect_frame(self):
        if not self._usable() or self._applying: return
        self.invalidate()
        snapshot = copy.deepcopy(self.session)
        self._submit('Inspecting source frame…',
                     lambda cancel, progress: self.backend.inspect_frame(snapshot, cancel=cancel),
                     self._accept_frame)

    def _accept_frame(self, result, token):
        if not self._current(token) or not result.is_current(self.session, self.window.project): return
        self.identity = result.identity
        if result.pixels:
            self.canvas.set_pixels(result)
        self._set_status(tr('Source frame {frame} · source-only inspection', frame=result.source_frame))

    def audition(self):
        if not self._usable() or self._applying: return
        if hasattr(self.window, 'cancel_range_preview'):
            self.window.cancel_range_preview()
        self.invalidate()
        self.window.player.pause()
        self.window.play_button.setText(tr('Play'))
        self.window.sound_audition.stop()
        if hasattr(self.window, 'close_tts_preview'): self.window.close_tts_preview()
        snapshot = copy.deepcopy(self.session)
        self._submit('Preparing bounded source audition…',
                     lambda cancel, progress: self.backend.audition(snapshot, cancel=cancel),
                     self._accept_audition)

    def _accept_audition(self, result, token):
        if not self._current(token) or not result.is_current(self.session, self.window.project): return
        self.identity = result.identity
        self.stop_audition()
        player = QMediaPlayer(self); self.player = player
        sink = QVideoSink(self); self.sink = sink
        generation = self._generation; context = self._context()
        def current():
            return (self.player is player and self._generation == generation
                    and self._context() == context and self._usable())
        def image(frame):
            if current() and frame.isValid():
                self.canvas.image = frame.toImage(); self.canvas.update()
        def status(value):
            if current() and value == QMediaPlayer.MediaStatus.EndOfMedia:
                self.stop_audition(); self._update_controls()
        def error(*_):
            if current():
                message = player.errorString(); self.stop_audition()
                self._set_error(message, wrapped=True); self._update_controls()
        sink.videoFrameChanged.connect(image)
        player.mediaStatusChanged.connect(status); player.errorOccurred.connect(error)
        player.setAudioOutput(self.audio); player.setVideoSink(sink)
        player.setSource(QUrl.fromLocalFile(str(result.path))); player.play()
        self._set_status(tr('Auditioning source [{start}, {end}) · not part of the timeline', start=result.start_frame, end=result.end_frame))
        self._update_controls()

    def _other_audition_changed(self, source_id):
        if source_id and not self._closed_flag:
            self.cancel_work()

    def _other_playback_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState and not self._closed_flag:
            self.cancel_work()

    def stop_audition(self):
        player, sink = self.player, self.sink
        self.player = None; self.sink = None
        if player is not None:
            player.stop(); player.setVideoSink(None); player.setAudioOutput(None)
            player.setSource(QUrl()); player.deleteLater()
            self.canvas.clear()
            self._set_status(tr('Audition stopped. The exact source cursor is unchanged; inspect a frame before marking.'))
        if sink is not None: sink.deleteLater()

    def preview_overwrite(self):
        # Historical entry point retained; the explicitly selected mode wins.
        self.preview_source_edit()

    def preview_source_edit(self):
        if not self._usable() or self._applying: return
        self.invalidate()
        try:
            source_in, source_out = self.session.selection()
            planner = (self.window.editor.plan_source_insert if self._insert_mode()
                       else self.window.editor.plan_source_overwrite)
            options = {}
            if self._insert_mode():
                tracks, captions, stickers = self._participation()
                options = dict(participating_tracks=tracks, follow_captions=captions, follow_stickers=stickers)
            plan = planner(self.session.media_id,
                self.track_choice.currentIndex(), self.captured_start, source_in, source_out, **options)
        except ValueError as exc:
            self._set_error(exc)
            if self._insert_mode(): self._show_insert_blocker(str(exc))
            return
        snapshot = copy.deepcopy(self.session)
        insert = self._insert_mode()
        def accept(result, token):
            if not self._current(token) or not result.is_current(self.session, self.window.project): return
            self._accept_frame(result, token)
            self.plan = plan; self.plan_context = token; self.plan_identity = result.identity
            self._show_impact(plan)
            self._set_status(tr('Impact preview ready. Apply once to make one undoable insert.' if insert else
                                'Impact preview ready. Apply once to make one undoable overwrite.'))
            self._update_controls()
        self._submit('Checking source and insert impact…' if insert else 'Checking source and overwrite impact…',
                     lambda cancel, progress: self.backend.inspect_frame(snapshot, cancel=cancel), accept)

    def _show_impact(self, plan):
        insert = self._insert_mode()
        lines = [str(tr('Destination: track {track}, [{start}, {end}) · {duration} frames',
                        track=plan.track + 1, start=plan.start, end=plan.end, duration=plan.duration)),
                 str(tr('Affected clips: {count}. Surviving pieces: {remnants}.',
                        count=len(plan.affected_clip_ids), remnants=len(plan.remnants)))]
        if insert:
            if len(plan.participating_tracks) == 1 and not plan.follow_captions and not plan.follow_stickers:
                lines.insert(0, str(tr('THIS TRACK ONLY: other tracks, captions and stickers remain at their current positions.')))
            else:
                lines.insert(0, str(tr('CHOSEN PARTICIPANTS ONLY: unchecked tracks and categories remain fixed.')))
            lines.append(str(tr('Participating clip tracks: {tracks}. Captions: {captions}. Stickers: {stickers}.',
                tracks=', '.join(str(track + 1) for track in plan.participating_tracks),
                captions=tr('move downstream') if plan.follow_captions else tr('fixed'),
                stickers=tr('move downstream') if plan.follow_stickers else tr('fixed'))))
            lines.append(str(tr('Whole downstream clips moved: {count}. Original gaps are preserved.', count=len(plan.translations))))
        clips = {c.id: c for c in self.window.project.clips}
        media_names = {m.id: m.name for m in self.window.project.media}
        def clip_label(cid):
            clip = clips.get(cid)
            name = media_names.get(clip.media_id, cid) if clip is not None else cid
            return f'{name} [{cid[:8]}]'
        identities = tr('Affected clip identifiers: {ids}', ids=', '.join(plan.affected_clip_ids))
        self.impact.setToolTip(identities)
        self.impact.setAccessibleDescription(identities)
        for cid in plan.affected_clip_ids:
            clip = clips.get(cid)
            if clip is not None and not insert:
                lines.append(str(tr('Replace {clip}: [{start}, {end})', clip=clip_label(cid),
                                    start=max(plan.start, clip.start), end=min(plan.end, clip.end))))
        for remnant in plan.remnants:
            lines.append(str(tr('Keep {clip}: [{start}, {end})', clip=clip_label(remnant.original_clip_id),
                                start=remnant.start, end=remnant.start + remnant.duration)))
        if insert:
            categories = [(('clip', track), tr('Clip track {track}', track=track + 1)) for track in range(4)]
            categories.extend([(('caption', None), tr('Captions')), (('sticker', None), tr('Stickers'))])
            for (category, track), title in categories:
                items = [item for item in plan.item_impacts if item.category == category and item.track == track]
                if not items: continue
                lines.append(str(title))
                for item in items:
                    label = clip_label(item.item_id) if category == 'clip' else item.item_id
                    if item.disposition == 'move':
                        lines.append(str(tr('Move {clip}: [{old_start}, {old_end}) → [{start}, {end})',
                            clip=label, old_start=item.previous_start, old_end=item.previous_start + item.duration,
                            start=item.start, end=item.start + item.duration)))
                    elif item.disposition == 'fixed':
                        lines.append(str(tr('Fixed {item}: [{start}, {end}) → unchanged',
                            item=label, start=item.previous_start, end=item.previous_start + item.duration)))
                    else:
                        lines.append(str(tr('Split {item}: [{start}, {end}) → retained pieces above',
                            item=label, start=item.previous_start, end=item.previous_start + item.duration)))
            identities = str(identities) + '\n' + str(tr('Impact item identifiers: {ids}',
                ids=', '.join(item.item_id for item in plan.item_impacts)))
            self.impact.setToolTip(identities); self.impact.setAccessibleDescription(identities)
        self.impact.setText('\n'.join(lines))

    def _show_insert_blocker(self, error):
        self._impact_error = str(error)
        self.impact.setText(tr('Insert blocked: {error}\nNo timeline edit has been made.', error=tr_error(error)))

    def apply_overwrite(self):
        # Historical entry point retained; the explicitly selected mode wins.
        self.apply_source_edit()

    def apply_source_edit(self):
        self.check_current()
        if (self._applying or self.job is not None or self.plan is None
                or not self._usable() or self.plan_context != self._token()): return
        plan = self.plan; expected = self.plan_identity
        insert = self._insert_mode()
        apply = self.window.editor.apply_source_insert if insert else self.window.editor.apply_source_overwrite
        verify = self.backend.verify_insert_sources if insert else self.backend.verify_overwrite_sources
        snapshot = copy.deepcopy(self.session)
        project_snapshot = copy.deepcopy(self.window.project)
        self._applying = True; self._update_controls()
        def accept(valid, token):
            if not valid or not self._current(token) or self.plan is not plan: return
            # Consume the plan before invoking a potentially reentrant refresh.
            self._clear_plan(); self._applying = False
            result = self.window.edit(lambda: apply(plan))
            self._observed = self._context()
            if result is not None:
                self._set_status(tr('Insert applied once. Undo restores the previous timeline.' if insert else
                                    'Overwrite applied once. Undo restores the previous timeline.'))
                self.window.select_clip(result.id)
            self._update_controls()
        self._submit('Rechecking source before insert…' if insert else 'Rechecking source before overwrite…',
                     lambda cancel, progress: verify(
                         snapshot, expected, project_snapshot, plan, cancel=cancel), accept)

    def _closed(self, *_):
        if self._closed_flag: return
        self._closed_flag = True; self.timer.stop()
        self.invalidate(); self._pending = None
        if self.window.source_viewer_dialog is self:
            self.window.source_viewer_dialog = None
        if self.job is None: self._cleanup()

    def _cleanup(self):
        if self._cleanup_started: return
        self._cleanup_started = True
        # Cleanup remains owned while the window is closing; never block Qt or
        # leave a temporary proxy behind when a canceled decoder exits late.
        def installed(job):
            job._source_cleanup = True
            job.finished.connect(lambda: self.window.progress_label.setText(tr('Ready · local files only'))
                                 if not self.window.jobs and not self.window.closing else None)
        self.window.start_job(tr('Cleaning source preview…'),
            lambda cancel, progress: self.backend.close(), lambda _: None,
            'source_cleanup', is_current=lambda: False, before_start=installed)


def open_source_viewer(window):
    ids = window.selected_media_ids()
    if len(ids) != 1:
        window.statusBar().showMessage(tr('Select exactly one media source to open the source viewer.'), 6000)
        return None
    dialog = getattr(window, 'source_viewer_dialog', None)
    if dialog is not None and dialog._usable() and dialog.session.media_id == ids[0]:
        dialog.show(); dialog.raise_(); dialog.activateWindow(); return dialog
    if dialog is not None: dialog.reject()
    try:
        dialog = SourceViewerDialog(window, ids[0])
    except ValueError as exc:
        window.statusBar().showMessage(tr_error(exc), 6000); return None
    window.source_viewer_dialog = dialog
    dialog.show(); dialog.canvas.setFocus(); dialog.inspect_frame()
    return dialog


def invalidate_source_viewer(window, *, close=False):
    dialog = getattr(window, 'source_viewer_dialog', None)
    if dialog is not None:
        if close: dialog.reject()
        else: dialog.cancel_work()


def stop_source_audition(window):
    dialog = getattr(window, 'source_viewer_dialog', None)
    if dialog is not None:
        dialog.cancel_work()

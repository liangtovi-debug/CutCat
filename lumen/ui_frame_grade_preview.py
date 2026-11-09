"""Explicit selected-clip grade stills, isolated from the final timeline viewer.

Manual refresh is the default; opt-in paused refresh debounces context changes.
One active decode and one replaceable request
are retained; a newer request, close, or context change invalidates callbacks.
The shared job registry owns worker lifetime and protects settings/project
changes. No output is written to the preview cache, scopes or reference still.
"""
from __future__ import annotations

import copy

import numpy as np
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout

from .frame_grade_preview import capture_grade_preview_snapshot
from .color_picker_source import _selected, _signature, _source_identity, _grade_lut_stat_identity
from .i18n import tr, tr_error
from .i18n_widgets import QCheckBox, QDialog, QLabel, QPushButton, QWidget


def _display_pixels(frame, cancel):
    """Quantize presentation in bounded worker chunks, not on the GUI thread."""
    height, width = frame.shape[:2]
    source = frame.reshape(-1, 3)
    pixels = np.empty(source.shape, dtype=np.uint8)
    for start in range(0, len(source), 65536):
        if cancel.is_set():
            return None
        pixels[start:start + 65536] = np.rint(
            np.clip(source[start:start + 65536], 0, 1) * 255).astype(np.uint8)
    if cancel.is_set():
        return None
    return width, height, pixels.tobytes()


class GradePreviewCanvas(QWidget):
    """An 8-bit presentation copy only; never a processing/sampling surface."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.setMinimumSize(240, 135)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName(tr('Selected clip grade preview · display only'))

    def set_pixels(self, presentation):
        width, height, pixels = presentation
        self.image = QImage(pixels, width, height, width * 3,
                            QImage.Format.Format_RGB888).copy()
        self.update()

    def clear(self):
        self.image = QImage()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#0b1016'))
        if self.image.isNull():
            return
        scale = min(self.width() / self.image.width(), self.height() / self.image.height())
        width, height = self.image.width() * scale, self.image.height() * scale
        painter.drawImage(QRectF((self.width() - width) / 2, (self.height() - height) / 2,
                                 width, height), self.image)


class FrameGradePreviewDialog(QDialog):
    """Modeless, explicitly refreshed, revision/source-guarded grade snapshot."""
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.snapshot = None
        self.context = None
        self.job = None
        self._pending = None
        self._waiting_job = None
        self._generation = 0
        self._closed_flag = False
        self._handoff_context = None
        self._observed_token = None
        self._request_token = None
        self._auto_due = False
        self.state = 'idle'
        self.setWindowTitle(tr('Current-frame grade preview'))
        self.setModal(False)
        self.resize(760, 630)
        layout = QVBoxLayout(self)
        self.stage = self._label(tr('Selected clip only · after input normalization, spatial restoration and the full grade + LUT. Before effects, transforms, fades, captions, overlays and compositing.'))
        layout.addWidget(self.stage)
        self.status = self._label(tr('Refresh explicitly after adjustments. This is not the final timeline preview.'))
        self.status.setStyleSheet('font-weight: 600; color: #9cddcc;')
        layout.addWidget(self.status)
        self.canvas = GradePreviewCanvas(self)
        layout.addWidget(self.canvas, 1)
        self.metadata = self._label(tr('No current-frame grade snapshot.'))
        layout.addWidget(self.metadata)
        self.precision = self._label(tr('RGB16 decode → float32 full-frame processing; display is clipped 8-bit SDR, not a calibrated or HDR monitor. Scopes and reference stills continue to use only the full timeline preview.'))
        layout.addWidget(self.precision)
        self.auto_refresh_checkbox = QCheckBox(tr('Automatically refresh while paused'))
        self.auto_refresh_checkbox.setToolTip(tr('Debounced selected-clip stills only, not live playback or final compositing. Cancel waits for another edit or an explicit refresh.'))
        layout.addWidget(self.auto_refresh_checkbox)
        self.auto_timer = QTimer(self)
        self.auto_timer.setSingleShot(True)
        self.auto_timer.setInterval(300)
        self.auto_timer.timeout.connect(self._automatic_refresh)
        self.auto_refresh_checkbox.toggled.connect(self._auto_toggled)
        row = QHBoxLayout()
        self.refresh_button = QPushButton(tr('Refresh current frame'))
        self.cancel_button = QPushButton(tr('Cancel refresh'))
        self.close_button = QPushButton(tr('Close'))
        for button in (self.refresh_button, self.cancel_button, self.close_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.timeline_button = QPushButton(tr('Build full timeline preview'))
        self.timeline_button.setToolTip(tr('Close this still and use the existing full-timeline preview, including effects and compositing. The viewer’s Before / After choice is preserved.'))
        layout.addWidget(self.timeline_button)
        self.refresh_button.clicked.connect(self.refresh_frame)
        self.cancel_button.clicked.connect(self.cancel_refresh)
        self.close_button.clicked.connect(self.reject)
        self.timeline_button.clicked.connect(self.build_timeline_preview)
        self.finished.connect(self._closed)
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.check_current)
        self.timer.start()
        self.window.player.playbackStateChanged.connect(self.check_current)
        self._update_buttons()

    def _paused(self):
        return self.window.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState

    def _change_token(self):
        # Only signatures and stat metadata on the GUI thread. LUT bytes, decode,
        # processing and presentation conversion remain worker-only.
        base = (self._context(), self.window.active_selection_type, self._paused())
        if self.window.active_selection_type != 'clip' or self.window.selected_parameter_clip() is None:
            return base + ('no_single_clip',)
        try:
            clip, media = _selected(self.window.project, self.window.selection)
            return base + (_signature(self.window.project, self.window.selection),
                           _source_identity(media.path), _grade_lut_stat_identity(clip.grade))
        except (OSError, ValueError, TypeError, AttributeError) as error:
            return base + (type(error).__name__, str(error))

    def _auto_toggled(self, enabled):
        self.auto_timer.stop()
        self._auto_due = False
        self._observed_token = self._change_token()
        if enabled:
            self._schedule_automatic()
        else:
            self.cancel_refresh()

    def _schedule_automatic(self):
        if (self._closed_flag or self.window.closing or not self.isVisible()
                or not self.auto_refresh_checkbox.isChecked() or not self._paused()):
            return
        self._auto_due = True
        self.state = 'stale'
        self.status.setText(tr('Changes pending · automatic refresh after a short pause.'))
        self.auto_timer.start()
        self._update_buttons()

    def _automatic_refresh(self):
        if not self._auto_due or not self.auto_refresh_checkbox.isChecked() or not self._paused():
            return
        if self._other_jobs():
            self.status.setText(tr('Automatic refresh waiting for the current task to finish.'))
            return
        self._auto_due = False
        self.refresh_frame(automatic=True)

    def _label(self, text):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        return label

    def _context(self):
        window = self.window
        return (id(window.project), window.revision, window.selection, window.timeline.playhead,
                window.selected_clip_ids, window.selection_generation)

    def context_current(self, context=None):
        return (not self._closed_flag and not self.window.closing
                and self.isVisible() and self._paused() and self.window.active_selection_type == 'clip'
                and self.window.selected_parameter_clip() is not None
                and (self.context if context is None else context) == self._context())

    def current(self):
        return (self.context_current() and self.snapshot is not None
                and self.snapshot.is_current(self.window.project, self.context[2], self.context[3]))

    def _unavailable_reason(self):
        window = self.window
        clip = window.selected_parameter_clip()
        if window.active_selection_type != 'clip' or clip is None:
            return tr('Select a video clip to preview its grade.')
        media = next((m for m in window.project.media if m.id == clip.media_id), None)
        if media is None or not media.has_video or clip.track >= 2:
            return tr('Current-frame grade preview requires a video clip.')
        if not clip.start <= window.timeline.playhead < clip.end:
            return tr('Move the playhead inside the selected clip, then refresh.')
        if clip.stabilization.enabled and clip.stabilization.strength > 0:
            return tr('Current-frame grade preview is unavailable with active stabilization. Use the full timeline preview.')
        return None

    def _other_jobs(self):
        frame_job = getattr(self.window, 'frame_grade_preview_job', None)
        return [job for job in self.window.jobs if job is not frame_job]

    def _update_buttons(self):
        active = not self._closed_flag and not self.window.closing
        busy = bool(self._other_jobs())
        self.refresh_button.setEnabled(active and not busy)
        self.cancel_button.setEnabled(active and bool(self.job or self._pending or self._waiting_job or self._auto_due))
        # Handoff is explicit and never races a worker or an existing render.
        self.timeline_button.setEnabled(active and not busy and bool(self.window.project.clips))

    def _clear_pixels(self):
        self.snapshot = None
        self.canvas.clear()
        self.metadata.setText(tr('No current-frame grade snapshot.'))

    def refresh_frame(self, checked=False, *, automatic=False):
        if automatic and not self._paused():
            return
        self.auto_timer.stop()
        self._auto_due = False
        if self._closed_flag or self.window.closing or not self.isVisible():
            return
        if self._other_jobs():
            self.status.setText(tr('Wait for the current task to finish or cancel it before refreshing this frame.'))
            self._update_buttons()
            return
        self._generation += 1
        self._pending = None
        if self.job:
            self.job.cancel.set()
        self._clear_pixels()
        self.context = self._context()
        reason = self._unavailable_reason()
        if reason is not None:
            self.state = 'unsupported'
            self.status.setText(tr('Unavailable · {reason}', reason=reason))
            self._update_buttons()
            return
        self.window.player.pause()
        # A synchronous playbackStateChanged can schedule an automatic request.
        # This explicit capture already serves that newly paused context.
        self.auto_timer.stop()
        self._auto_due = False
        self._observed_token = self._change_token()
        self._request_token = self._observed_token
        # Do not sample a drag's uncommitted curve overlay.
        self.window.selective_curve_control.editor.cancel_gesture()
        self._pending = (self._generation, self.context, copy.deepcopy(self.window.project))
        previous = getattr(self.window, 'frame_grade_preview_job', None)
        if previous is not None and previous in self.window.jobs:
            previous.cancel.set()
            self.state = 'waiting'
            self.status.setText(tr('Waiting for the canceled refresh to stop… Latest request retained.'))
            if previous is not self.job and previous is not self._waiting_job:
                self._waiting_job = previous
                previous.finished.connect(self._previous_finished)
        else:
            self._start_pending()
        self._update_buttons()

    def _previous_finished(self):
        self._waiting_job = None
        QTimer.singleShot(0, self._complete_handoff)
        QTimer.singleShot(0, self._start_pending)

    def _start_pending(self):
        if self._pending is None or self._closed_flag or self.window.closing:
            return
        generation, context, project = self._pending
        self._pending = None
        if generation != self._generation or not self.context_current(context):
            self.invalidate()
            return
        if self.window.jobs:
            self.state = 'idle'
            self.status.setText(tr('Wait for the current task to finish or cancel it before refreshing this frame.'))
            self._update_buttons()
            return
        self.state = 'rendering'
        self.status.setText(tr('Rendering selected clip grade… New changes replace this request.') if self.auto_refresh_checkbox.isChecked() else tr('Rendering selected clip grade… Changes require another refresh.'))
        def operation(cancel, progress):
            try:
                snapshot = capture_grade_preview_snapshot(project, context[2], context[3],
                                                          cancel=cancel, timeout=30)
                return snapshot, _display_pixels(snapshot.frame, cancel), None
            except Exception as error:
                # Keep failures local and generation-guarded. The shared job's
                # modal error callback must not show a superseded failure.
                return None, None, str(error)
        def before_start(job):
            self.job = job
            job.finished.connect(lambda: self._job_finished(job, generation))
        self.window.start_job(tr('Rendering current-frame grade…'), operation,
            lambda result: self._accept_result(generation, context, result),
            'frame_grade_preview', before_start=before_start)
        self._update_buttons()

    def _accept_result(self, generation, context, result):
        if generation != self._generation or self._closed_flag or self.window.closing:
            return
        if (not self.context_current(context) or (self._request_token is not None
                and self._change_token() != self._request_token)):
            self.invalidate()
            return
        snapshot, presentation, error = result
        if error is not None:
            self.state = 'unsupported'
            self._clear_pixels()
            self.status.setText(tr('Frame unavailable · {error}', error=tr_error(error)))
            return
        if presentation is None or not snapshot.is_current(self.window.project, context[2], context[3]):
            self.invalidate()
            return
        self.snapshot = snapshot
        self.canvas.set_pixels(presentation)
        self.state = 'current'
        self.status.setText(tr('Current · selected clip grade only, not the final timeline.'))
        self.metadata.setText(tr('Timeline frame {timeline} · source frame {source_frame} at project rate · source time {seconds}s',
            timeline=context[3], source_frame=snapshot.source_frame,
            seconds=f'{float(snapshot.source_time):.6f}'))

    def _job_finished(self, job, generation):
        if self.job is job:
            self.job = None
        if self._closed_flag or self.window.closing:
            QTimer.singleShot(0, self._complete_handoff)
            return
        if generation == self._generation and self.state == 'rendering' and self.snapshot is None:
            self.state = 'canceled'
            self.status.setText(tr('Refresh canceled. The project and full timeline preview are unchanged.'))
        self._update_buttons()
        if self._pending is not None:
            QTimer.singleShot(0, self._start_pending)

    def invalidate(self):
        self._generation += 1
        self._pending = None
        if self.job:
            self.job.cancel.set()
        self._clear_pixels()
        self.state = 'stale'
        self.status.setText(tr('Frame is stale. Refresh for the current clip, grade and playhead.'))
        self._update_buttons()

    def check_current(self, *_):
        if self._closed_flag:
            return
        if self.window.closing:
            self.reject()
            return
        token = self._change_token()
        changed = self._observed_token is not None and token != self._observed_token
        self._observed_token = token
        if changed and self.auto_refresh_checkbox.isChecked():
            self.auto_timer.stop()
            self._auto_due = False
            self.invalidate()
            self._schedule_automatic()
        if self.state in ('current', 'rendering', 'waiting'):
            if not self.context_current() or (self.snapshot is not None and not self.current()):
                self.invalidate()
        if self._auto_due and not self.auto_timer.isActive() and not self._other_jobs():
            self._automatic_refresh()
        self._update_buttons()

    def cancel_refresh(self):
        self.auto_timer.stop()
        self._auto_due = False
        self._observed_token = self._change_token()
        self._handoff_context = None
        self._generation += 1
        self._pending = None
        if self.job:
            self.job.cancel.set()
        self._clear_pixels()
        self.state = 'canceled'
        self.status.setText(tr('Refresh canceled. The project and full timeline preview are unchanged.'))
        self._update_buttons()

    def build_timeline_preview(self):
        if self._closed_flag or self.window.closing or self._other_jobs():
            return
        self._handoff_context = (id(self.window.project), self.window.revision, self.window.preview_mode)
        self.reject()
        self._complete_handoff()

    def _complete_handoff(self):
        if self._handoff_context is None:
            return
        if self.job is not None or self._waiting_job is not None:
            return
        context = self._handoff_context
        self._handoff_context = None
        window = self.window
        if window.closing:
            return
        if window.jobs or context != (id(window.project), window.revision, window.preview_mode):
            window.statusBar().showMessage(tr('Full timeline preview was not started because the editing context changed.'), 8000)
            return
        window.build_preview()

    def _closed(self, *_):
        self._closed_flag = True
        self._generation += 1
        self._pending = None
        self.timer.stop()
        self.auto_timer.stop()
        self._auto_due = False
        if self.job:
            self.job.cancel.set()
        self._clear_pixels()
        self._update_buttons()


def open_frame_grade_preview(window):
    if window.closing:
        return None
    existing = getattr(window, 'frame_grade_preview_dialog', None)
    if existing is not None and existing.isVisible():
        existing.raise_()
        existing.activateWindow()
        return existing
    if existing is not None:
        # Reopening is a newer user intent than an older pending handoff.
        existing.cancel_refresh()
    dialog = FrameGradePreviewDialog(window)
    window.frame_grade_preview_dialog = dialog
    dialog.show()
    dialog.refresh_frame()
    return dialog


def cancel_frame_grade_previews(window):
    """Cancel queued refresh/handoff intent as well as running shared jobs."""
    for dialog in window.findChildren(FrameGradePreviewDialog):
        dialog.cancel_refresh()

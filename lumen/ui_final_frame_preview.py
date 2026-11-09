"""Isolated final SDR stills; no playback-cache, scope or reference writes.

All source/LUT/sticker reads and validation run in registered workers. A render
is published only after a separate worker revalidates its dependencies. Current
stills are rechecked asynchronously; filesystem freshness is checked, not an
instantaneous operating-system guarantee. One worker and one newest request
are retained. Project/seek/selection/play generations also guard late errors.
"""
from __future__ import annotations

import copy
from dataclasses import fields, is_dataclass
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QGridLayout, QVBoxLayout, QScrollArea, QSizePolicy

from .final_frame_preview import (
    capture_final_frame_preview, verify_final_frame_preview, FALLBACK,
)
from .i18n import tr, tr_error
from .i18n_catalog_final_frame_preview import ERROR_ZH_CN
from .i18n_widgets import QCheckBox, QDialog, QLabel, QPushButton, QWidget
from .ui_interval_preview import fit_dialog
from .ui_frame_grade_preview import GradePreviewCanvas


def _freeze_visual(value):
    """Fast immutable target-record values; no JSON, hash or filesystem calls."""
    if is_dataclass(value):
        return tuple(_freeze_visual(getattr(value, f.name)) for f in fields(value))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_visual(item) for item in value)
    if isinstance(value, dict):
        return tuple((key, _freeze_visual(item)) for key, item in value.items())
    return value


def _target_visual_token(project, frame):
    """Only active visual records are deep-read on the GUI thread.

    Interval scanning is linear metadata work, not per-tail-clip grade/LUT/key
    serialization. Normal edits anywhere are additionally guarded by Editor
    and window revisions. The global scalar-blur flag matters even when its
    triggering clip is outside this frame, because legacy FFmpeg flags do.
    """
    active = []
    cuts = {}
    scalar_blur = False
    for clip in project.clips:
        cuts[clip.id] = clip.end
        if clip.track >= 2:
            continue
        if clip.start <= frame < clip.end:
            active.append(clip)
        if not scalar_blur and clip.effects and (clip.motion_keyframes or not clip.composite.is_legacy()):
            scalar_blur = any(e.enabled and e.kind == 'blur' and e.amount > 0 for e in clip.effects)
    # Invalid direct mutations must not make an idle UI serialize unbounded
    # overlapping records. Rendering still performs complete model validation.
    if len(active) > 2:
        return ('invalid_video_overlap', len(active))
    media_ids = {clip.media_id for clip in active}
    media = tuple(_freeze_visual(m) for m in project.media if m.id in media_ids)
    captions = [c for c in project.captions if c.start <= frame < c.end]
    overlays = [o for o in project.overlays if o.enabled and o.start <= frame < o.end]
    if len(captions) > 1 or len(overlays) > 256:
        return ('invalid_active_layers', len(captions), len(overlays))
    transitions = []
    for transition in project.video_transitions:
        cut = cuts.get(transition.left_clip_id)
        if cut is None or cut - transition.duration_frames // 2 <= frame < cut + (transition.duration_frames + 1) // 2:
            transitions.append(_freeze_visual(transition))
    return (project.width, project.height, project.fps_num, project.fps_den,
            tuple(_freeze_visual(c) for c in active), media,
            tuple(_freeze_visual(c) for c in captions),
            _freeze_visual(project.caption_style) if captions else None,
            tuple(_freeze_visual(o) for o in overlays), tuple(transitions), scalar_blur)


class FinalFramePreviewDialog(QDialog):
    """Modeless final-composite still with worker-only dependency checks."""
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
        self._auto_due = False
        self._handoff_context = None
        self._play_generation = 0
        self._bound_player = None
        self._next_verify = 0.0
        self.state = 'idle'
        self.setWindowTitle(tr('Final composite still preview'))
        self.setModal(False)
        self.resize(760, 650)
        outer = QVBoxLayout(self)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumSize(0, 0)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 4, 4)
        self.stage = self._label(tr('Final composite still · Rec.709 SDR · No audio'))
        self.stage.setStyleSheet('font-weight: 700; color: #9cddcc;')
        outer.addWidget(self.stage)
        self.status = self._label(tr('Refresh explicitly to inspect the paused playhead. Opening this window does not render.'))
        layout.addWidget(self.status)
        self.canvas = GradePreviewCanvas(self)
        self.canvas.setMinimumHeight(64)
        self.canvas.setAccessibleName(tr('Final composite still · display only'))
        outer.addWidget(self.canvas, 4)
        self.metadata = self._label(tr('No verified final composite still.'))
        layout.addWidget(self.metadata)
        self.profile = self._label(tr('Matches H.264/playback 8-bit composition before encoding; not a ProRes 10-bit, HDR or calibrated-monitor reference.'))
        layout.addWidget(self.profile)
        self.precision = self._label(tr('Original SDR canvas, up to 1920 × 1080. Full composition, not realtime playback or a proxy. Active stabilization, cross dissolves and unsupported combinations require Build full timeline preview.'))
        layout.addWidget(self.precision)
        self.freshness = self._label(tr('Source, LUT and sticker checks run in the background. This separate 8-bit display does not replace playback, scopes or reference stills.'))
        layout.addWidget(self.freshness)
        self.auto_refresh_checkbox = QCheckBox(tr('Automatically refresh while paused'))
        self.auto_refresh_checkbox.setToolTip(tr('Waits briefly after changes; retains only one running task and the latest request. Cancel waits for another edit or an explicit refresh.'))
        layout.addWidget(self.auto_refresh_checkbox)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 3)
        buttons = QGridLayout()
        self.refresh_button = QPushButton(tr('Refresh final frame'))
        self.cancel_button = QPushButton(tr('Cancel refresh'))
        self.close_button = QPushButton(tr('Close'))
        self.timeline_button = QPushButton(tr('Build full timeline preview'))
        buttons.addWidget(self.refresh_button, 0, 0, 1, 2)
        buttons.addWidget(self.cancel_button, 1, 0)
        buttons.addWidget(self.close_button, 1, 1)
        buttons.addWidget(self.timeline_button, 2, 0, 1, 2)
        outer.addLayout(buttons)
        for button in (self.refresh_button, self.cancel_button, self.close_button, self.timeline_button):
            button.setAutoDefault(False)
            button.setAccessibleName(button.text())
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.refresh_button.clicked.connect(self.refresh_frame)
        self.cancel_button.clicked.connect(self.cancel_refresh)
        self.close_button.clicked.connect(self.reject)
        self.timeline_button.clicked.connect(self.build_timeline_preview)
        self.finished.connect(self._closed)
        self.auto_timer = QTimer(self)
        self.auto_timer.setSingleShot(True)
        self.auto_timer.setInterval(300)
        self.auto_timer.timeout.connect(self._automatic_refresh)
        self.auto_refresh_checkbox.toggled.connect(self._auto_toggled)
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.check_current)
        self._bind_player()
        self._observed_token = self._context()
        self.timer.start()
        self._update_buttons()

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_available_screen()

    def fit_available_screen(self, available=None):
        fit_dialog(self, available)

    @staticmethod
    def _label(text):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        return label

    def _bind_player(self):
        if self._bound_player is not self.window.player:
            if self._bound_player is not None:
                try:
                    self._bound_player.playbackStateChanged.disconnect(self._playback_changed)
                except (RuntimeError, TypeError):
                    pass
            self._bound_player = self.window.player
            self._bound_player.playbackStateChanged.connect(self._playback_changed)
            self._play_generation += 1

    def _playback_changed(self, *_):
        self._play_generation += 1
        self.check_current()

    def _paused(self):
        return self.window.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState

    def _context(self):
        window = self.window
        # No path resolve/stat/probe/hash reads here: only in-memory model data.
        return (id(window.project), window.revision, window.timeline.playhead,
                getattr(window, '_final_frame_seek_generation', 0),
                window.selection_generation, window.selected_clip_ids,
                window.active_selection_type, self._play_generation,
                self._paused(), id(window.editor), window.editor.session_id, window.editor.revision,
                _target_visual_token(window.project, window.timeline.playhead))

    def context_current(self, context=None):
        return (not self._closed_flag and not self.window.closing and self.isVisible()
                and self._paused() and (self.context if context is None else context) == self._context())

    def current(self):
        # Dependencies are worker-verified, not reread by a GUI property.
        return self.snapshot is not None and self.context_current() and self.state in ('current', 'checking')

    def _other_jobs(self):
        own = getattr(self.window, 'final_frame_preview_job', None)
        return [job for job in self.window.jobs if job is not own]

    def _update_buttons(self):
        active = not self._closed_flag and not self.window.closing
        busy = bool(self._other_jobs())
        self.refresh_button.setEnabled(active and not busy)
        self.cancel_button.setEnabled(active and bool(self.job or self._pending or self._waiting_job or self._auto_due))
        self.timeline_button.setEnabled(active and not busy and bool(self.window.project.clips))

    def _clear_pixels(self):
        self.snapshot = None
        self.canvas.clear()
        self.metadata.setText(tr('No verified final composite still.'))

    def _schedule_automatic(self):
        if (self._closed_flag or self.window.closing or not self.isVisible()
                or not self.auto_refresh_checkbox.isChecked() or not self._paused()):
            return
        self._auto_due = True
        self.status.setText(tr('Changes pending · final still refresh after a short pause.'))
        self.auto_timer.start()
        self._update_buttons()

    def _auto_toggled(self, enabled):
        self.cancel_refresh()
        if enabled:
            self._schedule_automatic()

    def _automatic_refresh(self):
        if not self._auto_due or not self.auto_refresh_checkbox.isChecked() or not self._paused():
            return
        if self._other_jobs():
            self.status.setText(tr('Automatic refresh waiting for the current task to finish.'))
            return
        self._auto_due = False
        self.refresh_frame()

    def refresh_frame(self, *_):
        if self._closed_flag or self.window.closing or not self.isVisible():
            return
        if self._other_jobs():
            self.status.setText(tr('Wait for the current task to finish or cancel it before refreshing this frame.'))
            return
        self.window.player.pause()
        self._bind_player()
        self.auto_timer.stop()
        self._auto_due = False
        self._handoff_context = None
        self._generation += 1
        self._clear_pixels()
        self.context = self._context()
        self._observed_token = self.context
        self.window.selective_curve_control.editor.cancel_gesture()
        self._pending = ('render', self._generation, self.context, self.window.project, None)
        self._start_pending()

    def _start_pending(self):
        if self._pending is None or self._closed_flag or self.window.closing:
            return
        previous = getattr(self.window, 'final_frame_preview_job', None)
        if previous is not None and previous in self.window.jobs:
            previous.cancel.set()
            if previous is not self.job and previous is not self._waiting_job:
                self._waiting_job = previous
                previous.finished.connect(self._previous_finished)
            self.state = 'waiting'
            self.status.setText(tr('Waiting for the canceled refresh to stop… Latest request retained.'))
            self._update_buttons()
            return
        kind, generation, context, project, candidate = self._pending
        self._pending = None
        if generation != self._generation or not self.context_current(context):
            self.invalidate()
            return
        if self._other_jobs():
            self.state = 'stale'
            self.status.setText(tr('Wait for the current task to finish or cancel it before refreshing this frame.'))
            self._update_buttons()
            return
        self.state = 'rendering' if kind == 'render' else 'checking'
        if kind == 'render':
            self.status.setText(tr('Rendering the final target frame… New requests replace this one.'))
        elif self.snapshot is None:
            self.status.setText(tr('Checking source, LUT and sticker dependencies before display…'))

        def operation(cancel, progress):
            try:
                # Detach metadata off the GUI thread. A concurrent committed
                # edit changes context and discards this request before display.
                detached = copy.deepcopy(project)
                if cancel.is_set():
                    return None, None
                if kind == 'render':
                    return capture_final_frame_preview(detached, context[2], cancel=cancel, timeout=120), None
                valid = verify_final_frame_preview(candidate, detached, context[2], cancel=cancel, timeout=30)
                return bool(valid), None
            except Exception as error:
                # Never use the shared modal error path for supersedable work.
                return None, str(error)

        def before_start(job):
            self.job = job
            job.finished.connect(lambda: self._job_finished(job, generation))

        self.window.start_job(tr('Rendering final composite still…') if kind == 'render' else tr('Checking final still dependencies…'),
            operation, lambda result: self._accept_result(kind, generation, context, candidate, result),
            'final_frame_preview', before_start=before_start,
            is_current=lambda: generation == self._generation and self.context_current(context))
        self._update_buttons()

    @staticmethod
    def _error_text(error):
        # Strip only the backend's fixed fallback sentence; preserve arbitrary
        # diagnostic values and paths verbatim beneath the localized heading.
        detail = str(error).removesuffix(FALLBACK).rstrip()
        prefix = 'Cannot prepare final-frame preview: '
        if detail.startswith(prefix) and detail[len(prefix):] in ERROR_ZH_CN:
            return tr('Cannot prepare final-frame preview: {error}', error=tr(detail[len(prefix):]))
        return tr(detail) if detail in ERROR_ZH_CN else tr_error(detail)

    def _accept_result(self, kind, generation, context, candidate, result):
        if generation != self._generation or not self.context_current(context):
            return
        value, error = result
        if error is not None:
            self._clear_pixels()
            self.state = 'unsupported'
            self.status.setText(tr('Final frame unavailable · {error} Use Build full timeline preview.', error=self._error_text(error)))
            self._update_buttons()
            return
        if kind == 'render':
            # A fresh GUI event and independent worker check close the queued
            # result race when an external dependency changed after capture.
            self._pending = ('verify', generation, context, self.window.project, value)
            self.state = 'checking'
            self.status.setText(tr('Checking source, LUT and sticker dependencies before display…'))
            return
        if not value:
            self.invalidate()
            self.status.setText(tr('A source, LUT or sticker changed. Refresh for a newly verified final frame.'))
            self._schedule_automatic()
            return
        if self.snapshot is not candidate:
            self.snapshot = candidate
            self.canvas.set_pixels((candidate.width, candidate.height, candidate.rgb_bytes))
        self.state = 'current'
        self._next_verify = time.monotonic() + 1.0
        self.status.setText(tr('Verified final composite still · SDR · No audio'))
        self.metadata.setText(tr('Timeline frame {frame} · original {width} × {height} · background dependency rechecks',
            frame=context[2], width=candidate.width, height=candidate.height))

    def _job_finished(self, job, generation):
        if self.job is job:
            self.job = None
        if self._closed_flag or self.window.closing:
            QTimer.singleShot(0, self._complete_handoff)
            return
        if generation == self._generation and self.state in ('rendering', 'checking') and self._pending is None and self.snapshot is None:
            self.state = 'canceled'
            self.status.setText(tr('Refresh canceled. The project and full timeline preview are unchanged.'))
        self._update_buttons()
        if self._pending is not None:
            QTimer.singleShot(0, self._start_pending)

    def _previous_finished(self):
        self._waiting_job = None
        QTimer.singleShot(0, self._complete_handoff)
        QTimer.singleShot(0, self._start_pending)

    def invalidate(self):
        self._generation += 1
        self._pending = None
        self.auto_timer.stop()
        self._auto_due = False
        if self.job is not None:
            self.job.cancel.set()
        self._clear_pixels()
        self.state = 'stale'
        self.status.setText(tr('Final still is stale. Refresh for the current project and playhead.'))
        self._update_buttons()

    def check_current(self, *_):
        if self._closed_flag:
            return
        if self.window.closing:
            self.reject()
            return
        self._bind_player()
        token = self._context()
        changed = token != self._observed_token
        self._observed_token = token
        if changed:
            self.invalidate()
            self._schedule_automatic()
        elif (self.state == 'current' and self.snapshot is not None and self.job is None
                and self._pending is None and not self._other_jobs()
                and time.monotonic() >= self._next_verify):
            self._pending = ('verify', self._generation, self.context,
                             self.window.project, self.snapshot)
            self._start_pending()
        if self._auto_due and not self.auto_timer.isActive() and not self._other_jobs():
            self._automatic_refresh()
        self._update_buttons()

    def cancel_refresh(self, *_):
        self._handoff_context = None
        self.invalidate()
        self._observed_token = self._context()
        self.state = 'canceled'
        self.status.setText(tr('Refresh canceled. The project and full timeline preview are unchanged.'))

    def build_timeline_preview(self):
        if self._closed_flag or self.window.closing or self._other_jobs():
            return
        self._handoff_context = (id(self.window.project), self.window.revision, self.window.preview_mode)
        self.reject()
        self._complete_handoff()

    def _complete_handoff(self):
        if self._handoff_context is None or self.job is not None or self._waiting_job is not None:
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
        if self._bound_player is not None:
            try:
                self._bound_player.playbackStateChanged.disconnect(self._playback_changed)
            except (RuntimeError, TypeError):
                pass
            self._bound_player = None
        self._closed_flag = True
        self._generation += 1
        self._pending = None
        self.timer.stop()
        self.auto_timer.stop()
        self._auto_due = False
        if self.job is not None:
            self.job.cancel.set()
        self._clear_pixels()
        self._update_buttons()


def open_final_frame_preview(window):
    if window.closing:
        return None
    existing = getattr(window, 'final_frame_preview_dialog', None)
    if existing is not None and existing.isVisible():
        existing.raise_()
        existing.activateWindow()
        return existing
    if existing is not None:
        existing.cancel_refresh()
    dialog = FinalFramePreviewDialog(window)
    window.final_frame_preview_dialog = dialog
    dialog.show()
    return dialog


def cancel_final_frame_previews(window, *, close=False):
    for dialog in window.findChildren(FinalFramePreviewDialog):
        dialog.cancel_refresh()
        if close:
            dialog.reject()

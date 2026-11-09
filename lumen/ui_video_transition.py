"""Explicit, staged two-source picture transitions, independent of clip effects."""
from copy import copy
from PySide6.QtCore import QRectF, QTimer
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QMessageBox, QApplication, QAbstractItemView, QSizePolicy
from .i18n import tr, tr_error
from .i18n_widgets import QDialog, QLabel, QPushButton, QSpinBox, QFormLayout, QMenu, QScrollArea, QWidget
from .ui_recovery import project_fingerprint
from .video_transition import VideoTransition, plan_video_transitions

STALE = 'Transition context changed. Close and reopen this editor.'
PAIR = 'Select exactly two adjacent video clips on the same track.'
AUDIO = 'Video only: audio still cuts at the original boundary.'
LIMITS = 'First version: 1× speed, whole-frame source ranges, no picture animation, animated color windows, transparent compositing, active clip effects or active stabilization.'


def selected_pair(window):
    if window.active_selection_type != 'clip' or len(window.selected_clip_ids) != 2:
        raise ValueError(PAIR)
    clips = sorted((c for c in window.project.clips if c.id in window.selected_clip_ids), key=lambda c: c.start)
    if len(clips) != 2:
        raise ValueError(PAIR)
    left, right = clips
    if left.track not in (0, 1) or left.track != right.track or left.start + left.duration != right.start:
        raise ValueError(PAIR)
    return left, right


class VideoTransitionDialog(QDialog):
    def __init__(self, window, transition=None):
        super().__init__(window)
        self.window = window
        self.transition_id = transition.id if transition else None
        if transition:
            clips = {c.id: c for c in window.project.clips}
            left, right = clips[transition.left_clip_id], clips[transition.right_clip_id]
        else:
            left, right = selected_pair(window)
        self.left_id, self.right_id, self.cut = left.id, right.id, right.start
        self._closed = False
        self.job = None
        self._committed = False
        self._validation_error = ''
        self.context = self._context()
        self.setWindowTitle(tr('Cross Dissolve · video only'))
        self.resize(520, 360)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        content = QWidget(); body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 8, 0)
        self.scroll.setWidget(content); layout.addWidget(self.scroll, 1)
        media = {m.id: m for m in window.project.media}
        pair_label = QLabel(tr('Left: {left}\nRight: {right}\nTrack V{track} · cut at frame {cut}',
            left=media[left.media_id].name, right=media[right.media_id].name, track=left.track + 1, cut=self.cut))
        pair_label.setWordWrap(True); body.addWidget(pair_label)
        form = QFormLayout(); self.duration = QSpinBox(); self.duration.setRange(2, 100_000_000)
        self.duration.setKeyboardTracking(False); self.duration.setAccessibleName(tr('Transition duration · frames'))
        self.duration.setValue(transition.duration_frames if transition else min(12, max(2, min(left.duration, right.duration))))
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.addRow(tr('Transition duration · frames'), self.duration); body.addLayout(form)
        self.window_note = QLabel(); self.window_note.setWordWrap(True); body.addWidget(self.window_note)
        for text in (AUDIO, LIMITS, 'No project changes until Apply.'):
            label = QLabel(tr(text)); label.setWordWrap(True); body.addWidget(label)
        for label in content.findChildren(QLabel):
            label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        body.addStretch()
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        buttons = QHBoxLayout(); self.apply_button = QPushButton(tr('Apply transition'))
        self.cancel_button = QPushButton(tr('Cancel'))
        self.apply_button.setAutoDefault(False); self.cancel_button.setAutoDefault(False)
        self.apply_button.clicked.connect(self.apply_settings); self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.apply_button); buttons.addWidget(self.cancel_button); layout.addLayout(buttons)
        self.duration.valueChanged.connect(self.refresh_draft)
        self.timer = QTimer(self); self.timer.setInterval(150); self.timer.timeout.connect(self.refresh_draft); self.timer.start()
        self.finished.connect(self.closed)
        self.refresh_draft()

    def _context(self):
        w = self.window
        return (id(w.editor), w.editor.session_id, id(w.project), w.editor.revision, w.revision,
                w.selection_generation, w.active_selection_type, w.selected_clip_ids,
                getattr(w, 'transition_selection', ''), project_fingerprint(w.project), w.closing)

    def refresh_draft(self):
        if self._closed:
            return
        d = self.duration.value(); before = d // 2; after = d - before
        self.window_note.setText(tr('Centered window: [{start}, {end}) · {duration} frames. Required handles: left post-roll {post}, right pre-roll {pre} frames.',
            start=self.cut-before, end=self.cut+after, duration=d, post=after, pre=before))
        error = ''
        try:
            if self._context() != self.context:
                if self.job is not None:
                    self.job.cancel.set()
                raise ValueError(STALE)
            self.window.editor.guard_track_editable(next(c.track for c in self.window.project.clips if c.id == self.left_id))
            candidate = VideoTransition(self.transition_id or 'draft-transition', self.left_id, self.right_id, d)
            project = copy(self.window.project)
            project.video_transitions = [t for t in project.video_transitions if t.id != self.transition_id] + [candidate]
            plan_video_transitions(project)
        except (ValueError, TypeError, KeyError) as exc:
            error = str(exc)
        if not error and self.job is not None:
            self.status.setText(tr('Checking transition source handles…'))
        else:
            self.status.setText(tr_error(error or self._validation_error) if error or self._validation_error else tr('Apply, then Build Preview to review the transition.'))
        self.apply_button.setEnabled(not error and self.job is None)
        self.duration.setEnabled(self.job is None)

    def apply_settings(self):
        if self._closed or self.job is not None:
            return
        self.refresh_draft()
        if not self.apply_button.isEnabled():
            return
        w = self.window
        if self.transition_id:
            current = next((t for t in w.project.video_transitions if t.id == self.transition_id), None)
            if current and current.duration_frames == self.duration.value():
                self.accept(); return
        # Capture the exact candidate on the owning thread. Source copy/probe is
        # deliberately not part of Editor._commit or this Qt event callback.
        try:
            request = w.editor.prepare_video_transition(self.left_id, self.right_id,
                self.duration.value(), transition_id=self.transition_id)
        except Exception as exc:
            self._validation_error = str(exc); self.refresh_draft(); return
        self._validation_error = ''
        def operation(cancel, progress):
            from .video_transition_preflight import validate_video_transition_request
            try:
                return validate_video_transition_request(request, cancel=cancel), None
            except Exception as exc:
                return None, str(exc)
        def before_start(job):
            self.job = job
            job.finished.connect(lambda: self.validation_finished(job))
        w.start_job(tr('Checking transition source handles…'), operation,
                    self.validation_ready, 'video_transition', before_start=before_start)
        self.refresh_draft()

    def validation_ready(self, outcome):
        if self._closed or self.job is None or self.job.cancel.is_set():
            return
        try:
            current = self._context() == self.context
        except (ValueError, TypeError, KeyError):
            current = False
        if not current:
            self.job.cancel.set(); self.refresh_draft(); return
        certificate, error = outcome
        if error is not None:
            self._validation_error = error
            return
        result = self.window.edit(lambda: self.window.editor.commit_video_transition(certificate))
        if result is not None:
            self._committed = True
            self.accept()
            self.window.select_video_transition(result.id)

    def validation_finished(self, job):
        if self.job is job:
            self.job = None
        if not self._closed:
            self.refresh_draft()

    def closed(self, *_):
        self._closed = True
        self.timer.stop()
        if self.job is not None and not self._committed:
            self.job.cancel.set()


class VideoTransitionMixin:
    def build_video_transition_controls(self, layout):
        # Keep the existing one-row timeline header. An extra controls row
        # exceeds the small-window splitter minimum and hides the track area.
        self.video_transition_button = self._button(tr('Video transitions…'), lambda: None)
        self.video_transition_button.setToolTip(tr(AUDIO))
        menu = QMenu(self.video_transition_button)
        for title, callback in [('Add Cross Dissolve…', self.add_video_transition_dialog),
                                ('Edit transition…', self.edit_video_transition_dialog),
                                ('Remove transition', self.remove_selected_video_transition)]:
            menu.addAction(tr(title), callback)
        self.video_transition_button.setMenu(menu)
        layout.addWidget(self.video_transition_button)

    def selected_video_transition(self):
        if self.active_selection_type != 'transition':
            return None
        return next((t for t in self.project.video_transitions if t.id == getattr(self, 'transition_selection', '')), None)

    def select_video_transition(self, transition_id):
        if not any(t.id == transition_id for t in self.project.video_transitions):
            return
        self.clear_clip_selection()
        self.transition_selection = transition_id
        self.selection_generation += 1
        self.active_selection_type = 'transition'
        self.timeline.caption_selection = ''; self.timeline.overlay_selection = ''
        self.timeline.transition_selection = transition_id
        self.timeline.update(); self.refresh_inspector(); self.refresh_clip_effects()
        self.update_playback_controls()

    def _open_video_transition_dialog(self, transition=None):
        try:
            dialog = VideoTransitionDialog(self, transition)
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self, tr('Cannot make this edit'), tr_error(exc)); return
        dialog.exec()

    def add_video_transition_dialog(self):
        self._open_video_transition_dialog()

    def edit_video_transition_dialog(self):
        transition = self.selected_video_transition()
        if transition is not None:
            self._open_video_transition_dialog(transition)
        else:
            self.statusBar().showMessage(tr('Select a transition marker at a cut first.'), 6000)

    def remove_selected_video_transition(self):
        if self._destructive_focus_blocked() or isinstance(QApplication.focusWidget(), QAbstractItemView):
            return
        transition = self.selected_video_transition()
        if transition is not None:
            self.edit(lambda: self.editor.delete_video_transition(transition.id))


def transition_rect(timeline, transition):
    right = next((c for c in timeline.project.clips if c.id == transition.right_clip_id), None)
    if right is None:
        return QRectF()
    return QRectF(88 + right.start * timeline.scale - 8, 34 + right.track * 53 + 13, 16, 19)


def paint_transitions(timeline, painter):
    for transition in timeline.project.video_transitions:
        rect = transition_rect(timeline, transition)
        context = timeline.selection_context() if callable(getattr(timeline, 'selection_context', None)) else None
        selected = transition.id == getattr(timeline, 'transition_selection', '') and (context is None or context[1] == 'transition')
        painter.setBrush(QColor('#80552c'))
        painter.setPen(QPen(QColor('#fff0b0' if selected else '#dfbb79'), 2 if selected else 1))
        painter.drawRoundedRect(rect, 3, 3)
        painter.drawLine(rect.topLeft(), rect.bottomRight())
        painter.drawLine(rect.bottomLeft(), rect.topRight())

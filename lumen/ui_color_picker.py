"""Stage-labelled static picker. Display bytes are never used as sample values."""
from __future__ import annotations
import copy
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout
from .i18n import tr, tr_error
from .i18n_widgets import QLabel, QPushButton, QWidget
from .color_picker import sample_hue, insert_hue_points
from .color_picker_source import capture_picker_snapshot


class PickerCanvas(QWidget):
    sampled = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.marker = None
        self.setMinimumSize(240, 180)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(tr('Static source frame for hue sampling'))
        self.setCursor(Qt.CursorShape.CrossCursor)

    def image_rect(self):
        if self.image.isNull():
            return QRectF()
        scale = min(self.width() / self.image.width(), self.height() / self.image.height())
        w, h = self.image.width() * scale, self.image.height() * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def set_frame(self, frame):
        # Only presentation is quantized/clipped. Sampling uses snapshot.frame.
        pixels = np.ascontiguousarray(np.rint(np.clip(frame, 0, 1) * 255).astype(np.uint8))
        h, w = pixels.shape[:2]
        self.image = QImage(pixels.data, w, h, pixels.strides[0], QImage.Format.Format_RGB888).copy()
        self.marker = None
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#0b1016'))
        r = self.image_rect()
        if not self.image.isNull():
            p.drawImage(r, self.image)
            if self.marker:
                x, y = self.marker
                point = QPointF(r.left() + (x + .5) / self.image.width() * r.width(),
                                r.top() + (y + .5) / self.image.height() * r.height())
                p.setPen(QPen(QColor('#ffffff'), 2)); p.drawEllipse(point, 7, 7)
                p.setPen(QPen(QColor('#101820'), 1)); p.drawEllipse(point, 9, 9)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.image.isNull():
            return
        r = self.image_rect(); pos = event.position()
        # QRectF.contains includes the right/bottom edges; pixel bounds do not.
        if not (r.left() <= pos.x() < r.right() and r.top() <= pos.y() < r.bottom()):
            self.sampled.emit(-1, -1); return
        x = int((pos.x() - r.left()) / r.width() * self.image.width())
        y = int((pos.y() - r.top()) / r.height() * self.image.height())
        self.sampled.emit(x, y)


class ColorPickerDialog(QDialog):
    """A frozen frame plus optimistic context guards, never an implicit live tool."""
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.snapshot = None
        self.hue = None
        self.job = None
        # The epoch also rejects selection changes away and back to this ID
        # before a timer or queued worker result gets a chance to run.
        self.context = (id(window.project), window.revision, window.selection,
                        window.curve_key(), window.timeline.playhead,
                        window.selected_clip_ids, window.selection_generation)
        self.setWindowTitle(tr('Pick source hue · static frame'))
        self.resize(700, 590)
        layout = QVBoxLayout(self)
        self.stage = QLabel(tr('Selected clip only · after input normalization, spatial restoration and primary + RGB curves; before hue curves and LUT.\nRGB16 decode → float32 sample; image display is clipped 8-bit. Not the composited preview.'))
        self.stage.setWordWrap(True); layout.addWidget(self.stage)
        self.canvas = PickerCanvas(); layout.addWidget(self.canvas, 1)
        self.metadata = QLabel(tr('Reading the selected clip frame…')); self.metadata.setWordWrap(True); layout.addWidget(self.metadata)
        self.status = QLabel(tr('Click a colored pixel. Gray, padding and out-of-range samples are rejected.'))
        self.status.setWordWrap(True); layout.addWidget(self.status)
        note = QLabel(tr('Add center + range anchors without changing the current curve. Then adjust the selected point. This is not an HSL mask or tracking tool.'))
        note.setWordWrap(True); layout.addWidget(note); self.note = note
        row = QHBoxLayout(); self.apply_button = QPushButton(tr('Add sampled hue points')); self.apply_button.setEnabled(False)
        self.close_button = QPushButton(tr('Cancel')); row.addWidget(self.apply_button); row.addWidget(self.close_button); layout.addLayout(row)
        self.canvas.sampled.connect(self.sample_pixel); self.close_button.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self.apply_sample)
        self.timer = QTimer(self); self.timer.setInterval(150); self.timer.timeout.connect(self.check_current); self.timer.start()
        self.finished.connect(self._closed)

    def context_current(self):
        w = self.window
        return (not w.closing and self.context == (id(w.project), w.revision, w.selection,
                w.curve_key(), w.timeline.playhead, w.selected_clip_ids, w.selection_generation)
                and w.active_selection_type == 'clip' and w.selected_parameter_clip() is not None)

    def current(self):
        if not self.context_current(): return False
        return self.snapshot is None or self.snapshot.is_current(
            self.window.project, self.context[2], self.context[4])

    def invalidate(self):
        self.hue = None; self.apply_button.setEnabled(False); self.canvas.setEnabled(False)
        self.status.setText(tr('Frame is stale. Close and reopen the picker for the current clip and playhead.'))
        if self.job: self.job.cancel.set()
        self.timer.stop()

    def check_current(self):
        if not self.current(): self.invalidate()

    def accept_snapshot(self, snapshot):
        if not self.isVisible() or not self.context_current(): return
        self.snapshot = snapshot
        if not self.current(): self.invalidate(); return
        self.canvas.set_frame(snapshot.frame)
        self.stage.setText(tr('Selected clip only · after input normalization, spatial restoration and primary + RGB curves; before hue curves and LUT.\nRGB16 decode → float32 sample; image display is clipped 8-bit. Not the composited preview.') + '\n' + tr('Spatial restoration: applied to active picture.' if snapshot.restoration_applied else 'Spatial restoration: bypassed (disabled or zero strength).'))
        self.metadata.setText(tr('Timeline frame {timeline} · source frame {source_frame} at project rate · source time {seconds}s',
            timeline=self.context[4], source_frame=snapshot.source_frame, seconds=f'{float(snapshot.source_time):.6f}'))

    def sample_pixel(self, x, y):
        self.hue = None; self.apply_button.setEnabled(False); self.canvas.marker = None; self.canvas.update()
        if not self.current(): self.invalidate(); return
        if self.snapshot is None: return
        frame = self.snapshot.frame
        valid = 0 <= y < frame.shape[0] and 0 <= x < frame.shape[1]
        if valid:
            self.hue = sample_hue(frame[y, x], valid=bool(self.snapshot.valid_mask[y, x]))
        if self.hue is None:
            self.status.setText(tr('No usable hue here: choose a colored, in-range picture pixel, not gray or padding.')); return
        self.canvas.marker = (x, y); self.canvas.update()
        self.status.setText(tr('Sampled hue {degrees}° · pixel ({x}, {y}). No grade change yet.', degrees=f'{self.hue * 360:.3f}', x=x, y=y))
        self.apply_button.setEnabled(True)

    def apply_sample(self):
        if not self.current(): self.invalidate(); return
        if self.hue is None: return
        w = self.window; control = w.selective_curve_control; editor = control.editor
        editor.cancel_gesture()
        try:
            result = insert_hue_points(editor.points, self.hue)
        except ValueError as exc:
            self.status.setText(tr_error(exc)); return
        points = copy.deepcopy(result.points)
        # Close before committing so the intentional project revision is not
        # presented as a stale/error state. The normal edit path owns one undo.
        self.accept()
        if points != editor.points:
            w.apply_selective_curve(points)
        editor.select_point(result.selected_index)
        editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def _closed(self, *_):
        self.timer.stop()
        if self.job: self.job.cancel.set()
        # QDialog children can outlive close while queued job callbacks drain.
        # Release the large source/display buffers immediately, on every exit.
        self.snapshot = None
        self.hue = None
        self.canvas.image = QImage()
        self.canvas.marker = None
        self.apply_button.setEnabled(False)


def open_color_picker(window):
    clip = window.selected_parameter_clip()
    if clip is None or window.active_selection_type != 'clip': return None
    existing = getattr(window, 'color_picker_dialog', None)
    if existing is not None and existing.isVisible():
        existing.raise_(); existing.activateWindow(); return existing
    window.player.pause()
    window.selective_curve_control.editor.cancel_gesture()
    dialog = ColorPickerDialog(window); window.color_picker_dialog = dialog
    project = copy.deepcopy(window.project); frame = window.timeline.playhead
    dialog.show()
    def operation(cancel, progress):
        return capture_picker_snapshot(project, clip.id, frame, cancel=cancel)
    dialog.job = window.start_job(tr('Reading hue sampling frame…'), operation, dialog.accept_snapshot, 'color_picker')
    def failed(message):
        if dialog.isVisible() and dialog.context_current():
            dialog.status.setText(tr_error(message))
    dialog.job.failed.connect(failed)
    return dialog

"""Clip-local, sample-snapped gain key timing UI without project or decoder ownership.

Only a completed gesture emits a move request. The immutable original markers
stay in place during a drag; a ghost shows the prospective destination. The
owner validates/commits requests and supplies the next authoritative state.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy

from .i18n import locale_changed, tr
from .i18n_widgets import QWidget

AUDIO_GAIN_TIMELINE_HELP = ('Select a key or click to set the reference sample. Drag horizontally to whole 48 kHz samples, not video frames. '
                 'Left/Right moves a selected key by 1 sample; Shift by 10. Up/Down selects keys. '
                 'Escape or leaving focus cancels a move. Only completed moves change the project; rebuild preview to hear them. The project playhead does not move.')


class AudioGainTimeline(QWidget):
    sampleRequested = Signal(object)
    keySelected = Signal(object)
    moveRequested = Signal(object, object)
    gestureChanged = Signal(bool)

    def __init__(self, duration, parent=None):
        super().__init__(parent)
        if not isinstance(duration, int) or not 1 <= duration <= 2**40:
            raise ValueError('Invalid audio gain timeline duration')
        self.duration = duration
        self.samples = ()
        self.reference_sample = 0
        self.selected_sample = None
        self._origin = self._candidate = self._gesture_kind = None
        self._keyboard_key = None
        self._press_x = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setAccessibleName(tr('Audio gain keyframe timeline'))
        self.setToolTip(tr(AUDIO_GAIN_TIMELINE_HELP))
        locale_changed.connect(self._refresh_language)
        self._refresh_language()

    def sizeHint(self):
        return QSize(400, self.fontMetrics().height() * 2 + 42)

    def minimumSizeHint(self):
        return QSize(0, self.sizeHint().height())

    @property
    def gesture_active(self):
        return self._origin is not None

    @property
    def preview_sample(self):
        return self._candidate

    def set_state(self, samples, reference_sample, selected_sample=None):
        samples = tuple(sorted(set(sample for sample in samples if 0 <= sample < self.duration)))
        selected_sample = selected_sample if selected_sample in samples else None
        reference_sample = max(0, min(self.duration - 1, int(reference_sample)))
        if (samples, reference_sample, selected_sample) != (self.samples, self.reference_sample, self.selected_sample):
            self.cancel_gesture()
        self.samples, self.reference_sample, self.selected_sample = samples, reference_sample, selected_sample
        self._refresh_language()

    def _refresh_language(self, *_):
        selected = tr('No key selected') if self.selected_sample is None else tr('Selected key: {sample}', sample=self.selected_sample)
        self.setAccessibleDescription(tr('Reference sample {sample}; {selected}. {help}',
            sample=self.reference_sample, selected=selected, help=tr(AUDIO_GAIN_TIMELINE_HELP)))
        self.updateGeometry(); self.update()

    def sample_x(self, sample):
        """Logical-pixel coordinates; Qt handles fractional device pixel ratios."""
        left, right = 12., max(12., self.width() - 12.)
        return (left + right) / 2 if self.duration == 1 else left + sample * (right - left) / (self.duration - 1)

    def sample_at(self, x):
        if self.duration == 1 or self.width() <= 24:
            return 0
        ratio = (float(x) - 12.) / (self.width() - 24.)
        # Explicit half-up integer snapping, including out-of-widget drags.
        return max(0, min(self.duration - 1, int(ratio * (self.duration - 1) + .5)))

    def track_y(self):
        return self.fontMetrics().height() + 19.

    def _hit_key(self, position):
        if abs(position.y() - self.track_y()) > 13:
            return None
        nearby = [sample for sample in self.samples if abs(self.sample_x(sample) - position.x()) <= 9]
        if not nearby:
            return None
        # Prefer the selected key for crowded markers; all keys remain reachable
        # by Up/Down or the owner's standard accessible key selector.
        nearest = min(nearby, key=lambda sample: (abs(self.sample_x(sample) - position.x()), sample))
        if (self.selected_sample in nearby
                and abs(self.sample_x(self.selected_sample) - position.x()) <= abs(self.sample_x(nearest) - position.x()) + 1):
            return self.selected_sample
        return nearest

    def _begin(self, kind):
        if self.selected_sample not in self.samples:
            return False
        self._origin = self._candidate = self.selected_sample
        self._gesture_kind = kind
        self.gestureChanged.emit(True); self.update()
        return True

    def cancel_gesture(self):
        if not self.gesture_active:
            return False
        self._origin = self._candidate = self._gesture_kind = self._keyboard_key = self._press_x = None
        self.gestureChanged.emit(False); self.update()
        return True

    def _finish(self):
        origin, candidate = self._origin, self._candidate
        self.cancel_gesture()
        if origin is not None and candidate != origin:
            self.moveRequested.emit(origin, candidate)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.isEnabled():
            return super().mousePressEvent(event)
        self.cancel_gesture(); self.setFocus(Qt.FocusReason.MouseFocusReason)
        sample = self._hit_key(event.position())
        if sample is None:
            self.sampleRequested.emit(self.sample_at(event.position().x()))
        else:
            self.keySelected.emit(sample)
            if self.isEnabled() and self.selected_sample == sample:
                self._begin('mouse')
                self._press_x = event.position().x()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._gesture_kind == 'mouse':
            self._update_mouse_candidate(event.position().x()); self.update()
            event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._gesture_kind == 'mouse':
            self._update_mouse_candidate(event.position().x())
            self._finish(); event.accept(); return
        super().mouseReleaseEvent(event)

    def _update_mouse_candidate(self, x):
        # Retain the exact original sample on a click, even when many sample
        # positions share a screen pixel. Dragging keeps the initial hit offset.
        delta = x - self._press_x
        self._candidate = self._origin if abs(delta) < 1 else self.sample_at(self.sample_x(self._origin) + delta)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape and self.cancel_gesture():
            event.accept(); return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            if self._gesture_kind == 'mouse':
                event.accept(); return
            direction = -1 if key == Qt.Key.Key_Left else 1
            step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            if self.selected_sample is None:
                self.sampleRequested.emit(max(0, min(self.duration - 1, self.reference_sample + direction * step)))
            elif self.gesture_active or self._begin('keyboard'):
                self._keyboard_key = key
                self._candidate = max(0, min(self.duration - 1, self._candidate + direction * step)); self.update()
            event.accept(); return
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.samples:
            self.cancel_gesture()
            current = self.selected_sample if self.selected_sample is not None else self.reference_sample
            candidates = [sample for sample in self.samples if (sample < current if key == Qt.Key.Key_Up else sample > current)]
            if candidates:
                self.keySelected.emit(max(candidates) if key == Qt.Key.Key_Up else min(candidates))
            event.accept(); return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if self._gesture_kind == 'keyboard' and event.key() == self._keyboard_key:
            if not event.isAutoRepeat():
                self._finish()
            event.accept(); return
        super().keyReleaseEvent(event)

    def event(self, event):
        if event.type() in (QEvent.Type.FocusOut, QEvent.Type.WindowDeactivate,
                            QEvent.Type.Hide, QEvent.Type.UngrabMouse):
            self.cancel_gesture()
        elif event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self.cancel_gesture()
        elif event.type() == QEvent.Type.FontChange:
            self.updateGeometry()
        return super().event(event)

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.base())
        text_color = palette.text().color() if self.isEnabled() else palette.mid().color()
        painter.setPen(text_color)
        metrics = self.fontMetrics(); height = metrics.height()
        title = tr('Reference: {sample}', sample=self.reference_sample)
        if self.gesture_active:
            title = tr('Move {origin} → {sample}', origin=self._origin, sample=self._candidate)
        painter.drawText(QRectF(8, 2, max(0, self.width() - 16), height), Qt.AlignmentFlag.AlignLeft,
                         metrics.elidedText(str(title), Qt.TextElideMode.ElideRight, max(0, self.width() - 16)))
        y = self.track_y()
        painter.setPen(QPen(palette.mid().color(), 1))
        painter.drawLine(QPointF(self.sample_x(0), y), QPointF(self.sample_x(self.duration - 1), y))
        for sample in {0, self.duration - 1}:
            x = self.sample_x(sample)
            painter.drawLine(QPointF(x, y - 4), QPointF(x, y + 4))
        x = self.sample_x(self.reference_sample)
        painter.setPen(QPen(palette.text().color(), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(x, y - 12), QPointF(x, y + 12))
        def diamond(sample, selected=False, ghost=False):
            x = self.sample_x(sample); radius = 7 if selected or ghost else 5
            collision = ghost and sample in self.samples and sample != self._origin
            color = QColor('#d85a5a') if collision else palette.highlight().color() if selected or ghost else text_color
            painter.setPen(QPen(color, 2 if selected or ghost else 1, Qt.PenStyle.DashLine if ghost else Qt.PenStyle.SolidLine))
            painter.setBrush(palette.base() if ghost else color)
            painter.drawPolygon(QPolygonF([QPointF(x, y-radius), QPointF(x+radius, y), QPointF(x, y+radius), QPointF(x-radius, y)]))
        for sample in self.samples:
            if sample != self.selected_sample:
                diamond(sample)
        if self.selected_sample is not None:
            diamond(self.selected_sample, selected=True)
        if self.gesture_active:
            diamond(self._candidate, ghost=True)
        painter.setPen(text_color)
        bounds = tr('Clip-local samples: 0–{last}', last=self.duration - 1)
        painter.drawText(QRectF(8, y + 13, max(0, self.width() - 16), height), Qt.AlignmentFlag.AlignLeft,
                         metrics.elidedText(str(bounds), Qt.TextElideMode.ElideRight, max(0, self.width() - 16)))
        if self.hasFocus():
            painter.setPen(QPen(palette.highlight().color(), 1, Qt.PenStyle.DotLine)); painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))

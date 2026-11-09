"""Clip-local, frame-snapped key timing UI without project or decoder ownership.

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

TIMELINE_HELP = ('Select a key or click to set the reference frame. Drag keys to whole frames. '
                 'Left/Right moves a selected key by 1 frame; Shift by 10. Up/Down selects keys. '
                 'Escape or leaving focus cancels a move. Refresh is manual; the project playhead does not move.')


class KeyframeTimeline(QWidget):
    frameRequested = Signal(int)
    keySelected = Signal(int)
    moveRequested = Signal(int, int)
    gestureChanged = Signal(bool)

    def __init__(self, duration, parent=None):
        super().__init__(parent)
        if not isinstance(duration, int) or not 1 <= duration <= 2**31:
            raise ValueError('Invalid timeline duration')
        self.duration = duration
        self.frames = ()
        self.reference_frame = 0
        self.selected_frame = None
        self._origin = self._candidate = self._gesture_kind = None
        self._keyboard_key = None
        self._press_x = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setAccessibleName(tr('Window keyframe timeline'))
        self.setToolTip(tr(TIMELINE_HELP))
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
    def preview_frame(self):
        return self._candidate

    def set_state(self, frames, reference_frame, selected_frame=None):
        frames = tuple(sorted(set(frame for frame in frames if 0 <= frame < self.duration)))
        selected_frame = selected_frame if selected_frame in frames else None
        reference_frame = max(0, min(self.duration - 1, int(reference_frame)))
        if (frames, reference_frame, selected_frame) != (self.frames, self.reference_frame, self.selected_frame):
            self.cancel_gesture()
        self.frames, self.reference_frame, self.selected_frame = frames, reference_frame, selected_frame
        self._refresh_language()

    def _refresh_language(self, *_):
        selected = tr('No key selected') if self.selected_frame is None else tr('Selected key: {frame}', frame=self.selected_frame)
        self.setAccessibleDescription(tr('Reference frame {frame}; {selected}. {help}',
            frame=self.reference_frame, selected=selected, help=tr(TIMELINE_HELP)))
        self.updateGeometry(); self.update()

    def frame_x(self, frame):
        """Logical-pixel coordinates; Qt handles fractional device pixel ratios."""
        left, right = 12., max(12., self.width() - 12.)
        return (left + right) / 2 if self.duration == 1 else left + frame * (right - left) / (self.duration - 1)

    def frame_at(self, x):
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
        nearby = [frame for frame in self.frames if abs(self.frame_x(frame) - position.x()) <= 9]
        if not nearby:
            return None
        # Prefer the selected key for crowded markers; all keys remain reachable
        # by Up/Down or the owner's standard accessible key selector.
        nearest = min(nearby, key=lambda frame: (abs(self.frame_x(frame) - position.x()), frame))
        if (self.selected_frame in nearby
                and abs(self.frame_x(self.selected_frame) - position.x()) <= abs(self.frame_x(nearest) - position.x()) + 1):
            return self.selected_frame
        return nearest

    def _begin(self, kind):
        if self.selected_frame not in self.frames:
            return False
        self._origin = self._candidate = self.selected_frame
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
        frame = self._hit_key(event.position())
        if frame is None:
            self.frameRequested.emit(self.frame_at(event.position().x()))
        else:
            self.keySelected.emit(frame)
            if self.isEnabled() and self.selected_frame == frame:
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
        # Retain the exact original frame on a click, even when many frame
        # positions share a screen pixel. Dragging keeps the initial hit offset.
        delta = x - self._press_x
        self._candidate = self._origin if abs(delta) < 1 else self.frame_at(self.frame_x(self._origin) + delta)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape and self.cancel_gesture():
            event.accept(); return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            if self._gesture_kind == 'mouse':
                event.accept(); return
            direction = -1 if key == Qt.Key.Key_Left else 1
            step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            if self.selected_frame is None:
                self.frameRequested.emit(max(0, min(self.duration - 1, self.reference_frame + direction * step)))
            elif self.gesture_active or self._begin('keyboard'):
                self._keyboard_key = key
                self._candidate = max(0, min(self.duration - 1, self._candidate + direction * step)); self.update()
            event.accept(); return
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.frames:
            self.cancel_gesture()
            current = self.selected_frame if self.selected_frame is not None else self.reference_frame
            candidates = [frame for frame in self.frames if (frame < current if key == Qt.Key.Key_Up else frame > current)]
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
        title = tr('Reference: {frame}', frame=self.reference_frame)
        if self.gesture_active:
            title = tr('Move {origin} → {frame}', origin=self._origin, frame=self._candidate)
        painter.drawText(QRectF(8, 2, max(0, self.width() - 16), height), Qt.AlignmentFlag.AlignLeft,
                         metrics.elidedText(str(title), Qt.TextElideMode.ElideRight, max(0, self.width() - 16)))
        y = self.track_y()
        painter.setPen(QPen(palette.mid().color(), 1))
        painter.drawLine(QPointF(self.frame_x(0), y), QPointF(self.frame_x(self.duration - 1), y))
        for frame in {0, self.duration - 1}:
            x = self.frame_x(frame)
            painter.drawLine(QPointF(x, y - 4), QPointF(x, y + 4))
        x = self.frame_x(self.reference_frame)
        painter.setPen(QPen(palette.text().color(), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(x, y - 12), QPointF(x, y + 12))
        def diamond(frame, selected=False, ghost=False):
            x = self.frame_x(frame); radius = 7 if selected or ghost else 5
            collision = ghost and frame in self.frames and frame != self._origin
            color = QColor('#d85a5a') if collision else palette.highlight().color() if selected or ghost else text_color
            painter.setPen(QPen(color, 2 if selected or ghost else 1, Qt.PenStyle.DashLine if ghost else Qt.PenStyle.SolidLine))
            painter.setBrush(palette.base() if ghost else color)
            painter.drawPolygon(QPolygonF([QPointF(x, y-radius), QPointF(x+radius, y), QPointF(x, y+radius), QPointF(x-radius, y)]))
        for frame in self.frames:
            if frame != self.selected_frame:
                diamond(frame)
        if self.selected_frame is not None:
            diamond(self.selected_frame, selected=True)
        if self.gesture_active:
            diamond(self._candidate, ghost=True)
        painter.setPen(text_color)
        bounds = tr('Clip-local frames: 0–{last}', last=self.duration - 1)
        painter.drawText(QRectF(8, y + 13, max(0, self.width() - 16), height), Qt.AlignmentFlag.AlignLeft,
                         metrics.elidedText(str(bounds), Qt.TextElideMode.ElideRight, max(0, self.width() - 16)))
        if self.hasFocus():
            painter.setPen(QPen(palette.highlight().color(), 1, Qt.PenStyle.DotLine)); painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))

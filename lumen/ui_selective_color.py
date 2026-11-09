"""Periodic hue curves with staged native gestures and exact numeric edits."""
from __future__ import annotations

import copy
import math

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout

from .i18n import locale_events, tr
from .i18n_widgets import QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QPushButton, QWidget

SELECTIVE_CURVE_KEYS = ('hue_hue_curve', 'hue_sat_curve')


class SelectiveCurveEditor(QWidget):
    """The displayed line is exactly the renderer's piecewise-linear curve.

    Editing either red endpoint updates both endpoints. Mouse and key repeat
    gestures remain local until release; losing their context cancels them.
    """

    committed = Signal(object)
    selection_changed = Signal()
    ARROWS = (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.key = 'hue_hue_curve'
        self.points = [[0., 0.], [1., 0.]]
        self.selected = 0
        self.dragging = False
        self._key_gesture = False
        self._before = None
        self.setMinimumSize(210, 190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(tr('Selective hue curve'))
        self.setToolTip(tr('Drag points; release commits once. Double-click adds. Delete removes an interior point. Arrows adjust; Shift is finer. Page Up / Down selects a point. Escape cancels.'))
        locale_events.changed.connect(self.update)

    @property
    def is_hue(self):
        return self.key == 'hue_hue_curve'

    @property
    def neutral(self):
        return 0. if self.is_hue else 1.

    @property
    def bounds(self):
        return (-.5, .5) if self.is_hue else (0., 2.)

    def set_points(self, key, points, *, preserve_selection=False):
        self.cancel_gesture()
        previous = self.selected if preserve_selection else 0
        self.key = key
        self.points = copy.deepcopy(points)
        self.selected = min(previous or 0, len(self.points) - 1)
        self.selection_changed.emit()
        self.update()

    def plot_rect(self):
        return QRectF(43, 12, max(1, self.width() - 55), max(1, self.height() - 69))

    def to_screen(self, point):
        r = self.plot_rect()
        low, high = self.bounds
        return QPointF(r.left() + point[0] * r.width(),
                       r.bottom() - (point[1] - low) / (high - low) * r.height())

    def from_screen(self, position):
        r = self.plot_rect()
        low, high = self.bounds
        return [max(0., min(1., (position.x() - r.left()) / r.width())),
                max(low, min(high, low + (r.bottom() - position.y()) / r.height() * (high - low)))]

    def nearest(self, position):
        distances = [math.hypot((self.to_screen(p) - position).x(),
                                (self.to_screen(p) - position).y()) for p in self.points]
        index = min(range(len(distances)), key=distances.__getitem__)
        return index if distances[index] <= 12 else None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.plot_rect()
        painter.fillRect(r, QColor('#111821'))
        low, high = self.bounds
        from .color_picker import curve_support_intervals
        for start, end in curve_support_intervals(self.points, self.neutral):
            painter.fillRect(QRectF(r.left() + start * r.width(), r.top(), (end - start) * r.width(), r.height()), QColor(60, 130, 110, 45))
        for value in (low, (low + self.neutral) / 2, self.neutral,
                      (self.neutral + high) / 2, high):
            y = self.to_screen([0, value]).y()
            painter.setPen(QPen(QColor('#34424e'), 1))
            painter.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            label = f'{value * 360:g}°' if self.is_hue else f'{value:g}×'
            painter.setPen(QColor('#b9c5d0'))
            painter.drawText(QRectF(0, y - 9, 38, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
        for index in range(7):
            x = r.left() + index / 6 * r.width()
            painter.setPen(QPen(QColor('#34424e'), 1))
            painter.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        painter.setPen(QPen(QColor('#9aabba'), 1, Qt.PenStyle.DashLine))
        painter.drawLine(self.to_screen([0, self.neutral]), self.to_screen([1, self.neutral]))
        gradient = QLinearGradient(r.bottomLeft(), r.bottomRight())
        for index in range(13):
            gradient.setColorAt(index / 12, QColor.fromHsvF((index / 12) % 1., .82, .9))
        painter.fillRect(QRectF(r.left(), r.bottom() + 7, r.width(), 8), gradient)
        painter.setPen(QColor('#b9c5d0'))
        for index in range(4):
            x = r.left() + index / 3 * r.width()
            rect = QRectF(x - 20, r.bottom() + 17, 40, 18)
            if index == 0:
                rect.moveLeft(r.left())
            elif index == 3:
                rect.moveRight(r.right())
            alignment = Qt.AlignmentFlag.AlignCenter
            if index == 0:
                alignment = Qt.AlignmentFlag.AlignLeft
            elif index == 3:
                alignment = Qt.AlignmentFlag.AlignRight
            painter.drawText(rect, alignment, f'{index * 120}°')
        painter.drawText(QRectF(r.left(), self.height() - 21, r.width(), 19),
                         Qt.AlignmentFlag.AlignCenter, tr('Source hue · red wraps'))
        path = QPainterPath(self.to_screen(self.points[0]))
        for point in self.points[1:]:
            path.lineTo(self.to_screen(point))
        painter.setPen(QPen(QColor('#7cddc4'), 2))
        painter.drawPath(path)
        for index, point in enumerate(self.points):
            painter.setPen(QPen(QColor('#111821'), 1))
            painter.setBrush(QColor('#f3be73' if index == self.selected else '#e4edf4'))
            painter.drawEllipse(self.to_screen(point), 5, 5)

    def select_point(self, index):
        if not 0 <= index < len(self.points):
            return
        self.cancel_gesture()
        self.selected = index
        self.selection_changed.emit()
        self.update()

    def _stage_point(self, index, x, y):
        low, high = self.bounds
        y = max(low, min(high, float(y)))
        if index in (0, len(self.points) - 1):
            self.points[0] = [0., y]
            self.points[-1] = [1., y]
        else:
            left, right = self.points[index - 1][0], self.points[index + 1][0]
            # Persisted points can be arbitrarily close. A fixed epsilon can
            # cross a neighbor; adjacent representable values keep strict order.
            x = max(math.nextafter(left, right),
                    min(math.nextafter(right, left), float(x)))
            self.points[index] = [x, y]
        self.selection_changed.emit()
        self.update()

    def edit_point(self, index, *, x=None, y=None):
        self.cancel_gesture()
        if not 0 <= index < len(self.points):
            return
        before = copy.deepcopy(self.points)
        point = self.points[index]
        self._stage_point(index, point[0] if x is None else x, point[1] if y is None else y)
        if before != self.points:
            self.committed.emit(copy.deepcopy(self.points))

    def add_point(self, x=None, y=None):
        self.cancel_gesture()
        if len(self.points) >= 32:
            return False
        if x is None:
            # Add to the largest gap without changing the current line.
            index = max(range(len(self.points) - 1),
                        key=lambda i: self.points[i + 1][0] - self.points[i][0])
            left, right = self.points[index:index + 2]
            x, y = (left[0] + right[0]) / 2, (left[1] + right[1]) / 2
        if not 0 < x < 1 or any(abs(x - point[0]) < 1e-5 for point in self.points):
            return False
        low, high = self.bounds
        point = [float(x), max(low, min(high, float(self.neutral if y is None else y)))]
        self.points.append(point)
        self.points.sort(key=lambda p: p[0])
        self.selected = self.points.index(point)
        self.selection_changed.emit()
        self.update()
        self.committed.emit(copy.deepcopy(self.points))
        return True

    def remove_point(self, index):
        self.cancel_gesture()
        if index is None or index <= 0 or index >= len(self.points) - 1:
            return False
        self.points.pop(index)
        self.selected = min(index, len(self.points) - 1)
        self.selection_changed.emit()
        self.update()
        self.committed.emit(copy.deepcopy(self.points))
        return True

    def cancel_gesture(self):
        if self._before is None:
            return
        self.points = self._before
        self._before = None
        self.dragging = self._key_gesture = False
        self.selection_changed.emit()
        self.update()

    def _commit_gesture(self):
        before = self._before
        self._before = None
        self.dragging = self._key_gesture = False
        if before is not None and before != self.points:
            self.committed.emit(copy.deepcopy(self.points))

    def mousePressEvent(self, event):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.cancel_gesture()
        index = self.nearest(event.position())
        if index is None:
            return
        self.select_point(index)
        if event.button() == Qt.MouseButton.RightButton:
            self.remove_point(index)
        elif event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self._before = copy.deepcopy(self.points)
            self._press_position = event.position()
            self._moved = False

    def mouseMoveEvent(self, event):
        if self.dragging:
            if not self._moved and (event.position() - self._press_position).manhattanLength() < 2:
                return
            self._moved = True
            self._stage_point(self.selected, *self.from_screen(event.position()))

    def mouseReleaseEvent(self, event):
        if self.dragging and event.button() == Qt.MouseButton.LeftButton:
            self.mouseMoveEvent(event)
            self._commit_gesture()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.nearest(event.position()) is None:
            self.add_point(*self.from_screen(event.position()))

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride and event.key() in (
                *self.ARROWS, Qt.Key.Key_Delete, Qt.Key.Key_Backspace,
                Qt.Key.Key_Escape, Qt.Key.Key_Insert, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            event.accept()
            return True
        if event.type() in (QEvent.Type.FocusOut, QEvent.Type.WindowDeactivate,
                            QEvent.Type.Hide, QEvent.Type.UngrabMouse):
            self.cancel_gesture()
        return super().event(event)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.cancel_gesture()
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove_point(self.selected)
        elif key == Qt.Key.Key_Insert:
            self.add_point()
        elif key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self.select_point((self.selected + (-1 if key == Qt.Key.Key_PageUp else 1)) % len(self.points))
        elif key in self.ARROWS:
            if not self._key_gesture:
                self.cancel_gesture()
                self._before = copy.deepcopy(self.points)
                self._key_gesture = True
            fine = .1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.
            x, y = self.points[self.selected]
            x += fine / 360 * ((key == Qt.Key.Key_Right) - (key == Qt.Key.Key_Left))
            y += fine * (1 / 360 if self.is_hue else .01) * ((key == Qt.Key.Key_Up) - (key == Qt.Key.Key_Down))
            self._stage_point(self.selected, x, y)
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() in self.ARROWS and self._key_gesture and not event.isAutoRepeat():
            self._commit_gesture()
        else:
            super().keyReleaseEvent(event)


class SelectiveCurveControl(QWidget):
    committed = Signal(object)
    pick_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.context = None
        self._syncing = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.description = QLabel()
        self.description.setWordWrap(True)
        outer.addWidget(self.description)
        self.pick_button = QPushButton(tr('Pick from clip frame…'))
        self.pick_button.clicked.connect(self.pick_requested)
        outer.addWidget(self.pick_button)
        self.editor = SelectiveCurveEditor()
        outer.addWidget(self.editor)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.point_choice = QComboBox()
        self.point_choice.setAccessibleName(tr('Curve point'))
        self.point_choice.currentIndexChanged.connect(self._select)
        form.addRow(tr('Point'), self.point_choice)
        self.hue_spin = QDoubleSpinBox()
        self.hue_spin.setDecimals(4)
        self.hue_spin.setRange(0, 360)
        self.hue_spin.setSuffix('°')
        self.hue_spin.setSingleStep(1.)
        self.hue_spin.setKeyboardTracking(False)
        self.hue_spin.setAccessibleName(tr('Source hue · degrees'))
        self.hue_spin.valueChanged.connect(self._hue_changed)
        form.addRow(tr('Source hue'), self.hue_spin)
        self.value_spin = QDoubleSpinBox()
        self.value_spin.setDecimals(4)
        self.value_spin.setKeyboardTracking(False)
        self.value_spin.valueChanged.connect(self._value_changed)
        self.value_label = QLabel()
        self.value_label.setBuddy(self.value_spin)
        form.addRow(self.value_label, self.value_spin)
        outer.addLayout(form)
        row = QHBoxLayout()
        self.add_button = QPushButton(tr('Add point'))
        self.remove_button = QPushButton(tr('Remove point'))
        self.add_button.clicked.connect(lambda: self.editor.add_point())
        self.remove_button.clicked.connect(lambda: self.editor.remove_point(self.editor.selected))
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        outer.addLayout(row)
        self.hint = QLabel(tr('Piecewise linear · 0° / 360° stay linked.\nDrag or use exact fields. Escape cancels a drag.'))
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)
        self.range_note = QLabel()
        self.range_note.setWordWrap(True)
        outer.addWidget(self.range_note)
        self.editor.selection_changed.connect(self._sync_fields)
        self.editor.committed.connect(self.committed)
        locale_events.changed.connect(self._sync_fields)
        self.set_curve(None, 'hue_hue_curve', [[0., 0.], [1., 0.]])

    def set_curve(self, clip_id, key, points):
        context = (clip_id, key)
        same = self.context == context
        self.context = context
        self.editor.set_points(key, points, preserve_selection=same)
        self._sync_fields()

    def _sync_fields(self, *_):
        self._syncing = True
        try:
            editor = self.editor
            self.point_choice.blockSignals(True)
            self.point_choice.clear()
            for index, point in enumerate(editor.points):
                self.point_choice.addItem(tr('{number} · {degrees}°', number=index + 1, degrees=f'{point[0] * 360:.2f}'.rstrip('0').rstrip('.')))
            self.point_choice.setCurrentIndex(editor.selected)
            self.point_choice.blockSignals(False)
            x, y = editor.points[editor.selected]
            interior = 0 < editor.selected < len(editor.points) - 1
            self.hue_spin.blockSignals(True)
            self.hue_spin.setRange(0, 360)
            self.hue_spin.setValue(x * 360)
            self.hue_spin.setEnabled(interior)
            self.hue_spin.blockSignals(False)
            self.value_spin.blockSignals(True)
            self.value_spin.setRange(-180, 180) if editor.is_hue else self.value_spin.setRange(0, 2)
            self.value_spin.setSuffix('°' if editor.is_hue else '×')
            self.value_spin.setSingleStep(1. if editor.is_hue else .01)
            self.value_spin.setValue(y * 360 if editor.is_hue else y)
            self.value_spin.blockSignals(False)
            self.value_label.setText(tr('Hue shift') if editor.is_hue else tr('Saturation factor'))
            self.value_spin.setAccessibleName(tr('Hue shift · degrees') if editor.is_hue else tr('Saturation multiplier'))
            self.description.setText(tr('Shift only chosen hues. Neutral: 0°. Gray stays gray.') if editor.is_hue else tr('Scale saturation by source hue. Neutral: 1×. Gray stays gray.'))
            self.add_button.setEnabled(len(editor.points) < 32)
            self.remove_button.setEnabled(interior)
            self.pick_button.setEnabled(bool(self.context and self.context[0]))
            from .color_picker import curve_support_intervals
            spans = curve_support_intervals(editor.points, editor.neutral)
            intervals = ', '.join(f'{a * 360:.2f}°–{b * 360:.2f}°' for a, b in spans)
            self.range_note.setText(tr('Curve non-neutral hue spans: {spans}. Not a spatial mask.', spans=intervals) if spans else tr('Curve is neutral: no hue range changed.'))
        finally:
            self._syncing = False

    def _select(self, index):
        if not self._syncing:
            self.editor.select_point(index)

    def _hue_changed(self, degrees):
        if not self._syncing:
            self.editor.edit_point(self.editor.selected, x=degrees / 360)

    def _value_changed(self, value):
        if not self._syncing:
            self.editor.edit_point(self.editor.selected, y=value / 360 if self.editor.is_hue else value)

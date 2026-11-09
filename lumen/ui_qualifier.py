"""One local H/S/Y′ qualifier, edited against an explicitly frozen source frame.

Preview pixels and pending settings belong to this dialog. Only Apply commits
one normal grade transaction; cancellation, failed reads and stale results never
alter the project. The matte is range membership, not an opacity/alpha matte.
"""
from __future__ import annotations

import copy
from dataclasses import replace

import numpy as np
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QSizePolicy, QVBoxLayout, QMessageBox

from .color_picker_source import capture_picker_snapshot
from .i18n import tr, tr_error
from .i18n_widgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                           QGroupBox, QLabel, QPushButton, QScrollArea, QSpinBox, QWidget)
from .qualifier import (QualifierSettings, apply_qualifier, qualifier_mask,
                        validate_qualifier, range_softness, CHANNEL_ENABLE_FIELDS)
from .qualifier_picker import (initialize_from_sample, add_qualifier_range,
                               clear_qualifier_ranges, soften_qualifier_range)
from .qualifier_overlay import selection_overlay
from .qualifier_viewport import overview, centered
from .qualifier_window_handles import WindowGeometry, WindowMapping, WindowDrag
from .window_animation import evaluate_window


STAGE_TEXT = ('Selected clip only · after input normalization, spatial restoration, primary, RGB and hue curves; before qualifier and LUT.\n'
              'Frozen RGB16 decode → float32 processing; display is clipped 8-bit. Not the composited preview.')
RANGE_TEXT = ('Ranges use encoded Rec.709 HSV saturation and Y′ luma, not HSL lightness. '
              'Independent include ranges minus exclude ranges, then matte levels, local shrink/grow, local matte smoothing and one static or keyframed window; no tracking or temporal smoothing. Entirely offline.')
STALE_TEXT = 'Frame is stale. Close and reopen the qualifier for the current clip and playhead.'
HANDLE_HELP_TEXT = ('Move the center or shape interior. Drag edge handles to resize around the center. '
                    'Escape cancels the current drag. Handles are display only; Apply saves all pending changes.')
SOFTNESS_HELP_TEXT = ('Sample increases only the selected channel feather to at least 50% channel weight; not combined matte weight. For S/Y′, only the outside low or high edge changes. Core bounds and the opposite edge stay unchanged. Inside or already sufficient: no change. Gray has no hue; an enabled narrow hue condition still excludes gray when sampling S/Y′.')
HANDLE_OFF_TEXT = 'Choose an ellipse or rectangle to edit window handles.'
HANDLE_NAV_TEXT = ('Window handles edit only the static shape. Double-click detail navigation is paused; '
                   'scroll and middle-drag still navigate.')


class QualifierCanvas(QWidget):
    """Aspect-fitted float sample grid; mouse positions use Qt logical pixels."""
    sample_clicked = Signal(int, int)
    detail_requested = Signal(int, int)
    pan_requested = Signal(int, int)
    zoom_requested = Signal(int)
    window_drag_started = Signal()
    window_geometry_changed = Signal(object)
    window_drag_finished = Signal(bool)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.picking = False
        self.sample_position = None
        self._pixels = None
        self._raster_cache = None
        self._pan_position = None
        self._last_press_was_pick = False
        self.window_editing = False
        self.window_geometry = None
        self.window_mapping = None
        self._window_drag = None
        self.setMouseTracking(True)
        self.setMinimumSize(180, 120)
        self.setMaximumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(tr('Static qualifier frame'))
        self.setAccessibleDescription(tr('Use the preview selector to view range matte, source, adjusted pixels or selection overlay.'))

    def image_rect(self):
        if self.image.isNull():
            return QRectF()
        dpr = self.devicePixelRatioF()
        scale = min(self.width() / self.image.width(), self.height() / self.image.height())
        width = max(1, round(self.image.width() * scale * dpr))
        height = max(1, round(self.image.height() * scale * dpr))
        left = round((self.width() * dpr - width) / 2)
        top = round((self.height() * dpr - height) / 2)
        return QRectF(left / dpr, top / dpr, width / dpr, height / dpr)

    def grid_at(self, point):
        rect = self.image_rect()
        if (rect.isEmpty() or not rect.left() <= point.x() < rect.right()
                or not rect.top() <= point.y() < rect.bottom()):
            return None
        dpr = self.devicePixelRatioF()
        # A mouse position identifies one physical screen pixel. Classify its
        # center, using exactly the same nearest-neighbor lookup as painting.
        px = int(np.floor(point.x() * dpr)) - round(rect.left() * dpr)
        py = int(np.floor(point.y() * dpr)) - round(rect.top() * dpr)
        width, height = round(rect.width() * dpr), round(rect.height() * dpr)
        return (min(self.image.width() - 1, int((px + .5) * self.image.width() / width)),
                min(self.image.height() - 1, int((py + .5) * self.image.height() / height)))

    def set_frame(self, frame):
        # Display conversion only. Range evaluation never reads these bytes.
        pixels = np.ascontiguousarray(np.rint(np.clip(frame, 0, 1) * 255).astype(np.uint8))
        self._pixels = pixels
        self._raster_cache = None
        height, width = pixels.shape[:2]
        self.image = QImage(pixels.data, width, height, pixels.strides[0],
                            QImage.Format.Format_RGB888).copy()
        self.update()

    @property
    def window_drag_active(self):
        return self._window_drag is not None

    def set_window_context(self, geometry, viewport, canvas_size, editing):
        rect = self.image_rect()
        mapping = (WindowMapping(viewport, *canvas_size, rect.left(), rect.top(),
                                 rect.width(), rect.height())
                   if viewport is not None and canvas_size is not None and not rect.isEmpty() else None)
        editing = bool(editing and mapping is not None and geometry.shape != 'off')
        if self.window_drag_active and (not editing or mapping != self._window_drag.mapping):
            self.cancel_window_drag()
            # The cancel signal restored the owner's exact pending geometry.
            geometry = self.window_geometry
        self.window_geometry = geometry
        self.window_mapping = mapping
        self.window_editing = editing
        self.update()

    def _current_window_mapping(self):
        if self.window_mapping is None or self.image.isNull():
            return None
        rect = self.image_rect(); previous = self.window_mapping
        return WindowMapping(previous.viewport, previous.canvas_width, previous.canvas_height,
                             rect.left(), rect.top(), rect.width(), rect.height())

    def window_handles(self):
        mapping = self._current_window_mapping()
        if not self.window_editing or mapping is None or self.window_geometry is None:
            return {}
        return {name: QPointF(*point) for name, point in mapping.handles(self.window_geometry).items()}

    def _window_handle_at(self, point):
        if not self.window_editing or self.window_geometry is None:
            return None
        rect = self.image_rect()
        if not rect.adjusted(-7, -7, 7, 7).contains(point):
            return None
        for name, handle in self.window_handles().items():
            if (rect.adjusted(-7, -7, 7, 7).contains(handle)
                    and abs(point.x() - handle.x()) <= 7 and abs(point.y() - handle.y()) <= 7):
                return name
        mapping = self._current_window_mapping()
        if rect.contains(point) and mapping is not None and mapping.contains(
                self.window_geometry, point.x(), point.y()):
            return 'center'
        return None

    def cancel_window_drag(self):
        if getattr(self, '_window_drag', None) is None:
            return False
        self.window_geometry = self._window_drag.geometry
        self._window_drag = None
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()
        self.window_drag_finished.emit(False)
        self.update()
        return True

    def _update_window_drag(self, point):
        if self._window_drag is None:
            return
        if self._current_window_mapping() != self._window_drag.mapping:
            self.cancel_window_drag(); return
        geometry = self._window_drag.updated(point.x(), point.y())
        if geometry != self.window_geometry:
            self.window_geometry = geometry
            self.window_geometry_changed.emit(geometry)
            self.update()

    def event(self, event):
        # Capture loss, a modal switch, hiding or disabling must never leave a
        # half gesture in pending settings. Normal release clears ownership
        # before releaseMouse(), so its own UngrabMouse cannot roll back.
        if (event.type() in (QEvent.Type.UngrabMouse, QEvent.Type.WindowDeactivate, QEvent.Type.Hide)
                or event.type() == QEvent.Type.EnabledChange and not self.isEnabled()):
            self.cancel_window_drag()
            self._pan_position = None
        return super().event(event)

    def resizeEvent(self, event):
        self.cancel_window_drag()
        super().resizeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.cancel_window_drag():
            event.accept(); return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_press_was_pick = self.picking
        if event.button() == Qt.MouseButton.MiddleButton and not self.image.isNull():
            self.cancel_window_drag()
            self._pan_position = event.position()
            event.accept(); return
        if self.window_editing and event.button() == Qt.MouseButton.LeftButton:
            handle = self._window_handle_at(event.position())
            if handle is not None:
                self._window_drag = WindowDrag(self.window_geometry, self._current_window_mapping(),
                                               handle, event.position().x(), event.position().y())
                self.window_drag_started.emit()
                if self._window_drag is not None:
                    self.setFocus(Qt.FocusReason.MouseFocusReason)
                    self.grabMouse()
            event.accept(); return
        if self.picking and event.button() == Qt.MouseButton.LeftButton:
            coordinate = self.grid_at(event.position())
            if coordinate is not None:
                self.sample_clicked.emit(*coordinate)
                event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._window_drag is not None:
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._update_window_drag(event.position())
            else:
                self.cancel_window_drag()
            event.accept(); return
        if self._pan_position is not None and event.buttons() & Qt.MouseButton.MiddleButton:
            rect = self.image_rect()
            if not rect.isEmpty():
                delta = event.position() - self._pan_position
                dx = round(-delta.x() * self.image.width() / rect.width())
                dy = round(-delta.y() * self.image.height() / rect.height())
                if dx or dy:
                    self._pan_position = event.position()
                    self.pan_requested.emit(dx, dy)
            event.accept(); return
        if self.window_editing:
            handle = self._window_handle_at(event.position())
            cursor = (Qt.CursorShape.SizeHorCursor if handle in ('left', 'right') else
                      Qt.CursorShape.SizeVerCursor if handle in ('top', 'bottom') else
                      Qt.CursorShape.SizeAllCursor if handle == 'center' else Qt.CursorShape.ArrowCursor)
            self.setCursor(cursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._window_drag is not None:
            self._update_window_drag(event.position())
            if self._window_drag is not None:
                self._window_drag = None
                if QWidget.mouseGrabber() is self:
                    self.releaseMouse()
                self.window_drag_finished.emit(True)
            event.accept(); return
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_position = None
            event.accept(); return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        # Navigation never replaces a range. With the one-shot picker armed,
        # the normal first click owns sampling, not this double-click handler.
        if self.window_editing and event.button() == Qt.MouseButton.LeftButton:
            event.accept(); return
        if not self.picking and not self._last_press_was_pick and event.button() == Qt.MouseButton.LeftButton:
            coordinate = self.grid_at(event.position())
            if coordinate is not None:
                self.detail_requested.emit(*coordinate)
                event.accept(); return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        if self.grid_at(event.position()) is not None and event.angleDelta().y():
            self.cancel_window_drag()
            self.zoom_requested.emit(1 if event.angleDelta().y() > 0 else -1)
            event.accept(); return
        super().wheelEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#0b1016'))
        if not self.image.isNull():
            rect = self.image_rect(); dpr = self.devicePixelRatioF()
            width, height = round(rect.width() * dpr), round(rect.height() * dpr)
            key = (self.image.cacheKey(), width, height, dpr)
            if self._raster_cache is None or self._raster_cache[0] != key:
                # Explicit raster lookup avoids QPainter's platform-specific
                # nearest-neighbor phase disagreeing with click coordinates.
                columns = np.floor((np.arange(width) + .5) * self.image.width() / width).astype(int)
                rows = np.floor((np.arange(height) + .5) * self.image.height() / height).astype(int)
                pixels = np.ascontiguousarray(self._pixels[rows[:, None], columns[None, :]])
                raster = QImage(pixels.data, width, height, pixels.strides[0],
                                QImage.Format.Format_RGB888).copy()
                raster.setDevicePixelRatio(dpr)
                self._raster_cache = (key, raster)
            painter.drawImage(rect.topLeft(), self._raster_cache[1])
            if self.sample_position is not None:
                x, y = self.sample_position; rect = self.image_rect()
                px = rect.left() + (x + .5) * rect.width() / self.image.width()
                py = rect.top() + (y + .5) * rect.height() / self.image.height()
                painter.setPen(QColor("#00e5ff"))
                painter.drawEllipse(QRectF(px - 5, py - 5, 10, 10))
            self._paint_window_handles(painter)

    def _paint_window_handles(self, painter):
        mapping = self._current_window_mapping()
        if not self.window_editing or mapping is None or self.window_geometry is None:
            return
        # Decoration belongs only to QPainter. It never enters self.image,
        # the float snapshot, qualifier membership or an export/render path.
        geometry = self.window_geometry
        left, top, right, bottom = mapping.bounds(geometry)
        bounds = QRectF(left, top, right - left, bottom - top)
        painter.save(); painter.setClipRect(self.image_rect())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for color, width in ((QColor(0, 0, 0, 220), 3.), (QColor('#60edff'), 1.)):
            pen = QPen(color, width); pen.setCosmetic(True); painter.setPen(pen)
            if geometry.shape == 'ellipse':
                painter.drawEllipse(bounds)
            else:
                painter.drawRect(bounds)
        painter.restore()
        painter.save(); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        visible = self.image_rect().adjusted(-7, -7, 7, 7)
        painter.setClipRect(visible)
        painter.setPen(QPen(QColor('#60edff'), 1.5))
        painter.setBrush(QColor('#0b1016'))
        for name, point in self.window_handles().items():
            if not visible.contains(point):
                continue
            handle = QRectF(point.x() - 4.5, point.y() - 4.5, 9, 9)
            if name == 'center':
                painter.drawEllipse(handle)
                painter.drawLine(point + QPointF(-2, 0), point + QPointF(2, 0))
                painter.drawLine(point + QPointF(0, -2), point + QPointF(0, 2))
            else:
                painter.drawRect(handle)
        painter.restore()


# UI uses degrees and percentages; stored values keep the float model's units.
# Touching one control never quantizes unrelated values from a saved project.
_RANGE_CONTROLS = (
    ('hue_center', 'Hue center (degrees)', 0., 360., 360., 1., '°'),
    ('hue_width', 'Hue half-width (degrees)', 0., 180., 360., 1., '°'),
    ('hue_softness', 'Hue feather (degrees)', 0., 180., 360., 1., '°'),
    ('saturation_low', 'HSV saturation minimum (%)', 0., 100., 100., 1., '%'),
    ('saturation_high', 'HSV saturation maximum (%)', 0., 100., 100., 1., '%'),
    ('saturation_softness_low', 'HSV saturation low feather (%)', 0., 100., 100., 1., '%'),
    ('saturation_softness_high', 'HSV saturation high feather (%)', 0., 100., 100., 1., '%'),
    ('luma_low', 'Y′ luma minimum (%)', 0., 100., 100., 1., '%'),
    ('luma_high', 'Y′ luma maximum (%)', 0., 100., 100., 1., '%'),
    ('luma_softness_low', 'Y′ luma low feather (%)', 0., 100., 100., 1., '%'),
    ('luma_softness_high', 'Y′ luma high feather (%)', 0., 100., 100., 1., '%'),
)
_WINDOW_CONTROLS = (
    ('window_center_x', 'Window center X (% canvas width)', 0., 100., 100., 1., '%'),
    ('window_center_y', 'Window center Y (% canvas height)', 0., 100., 100., 1., '%'),
    ('window_width', 'Window width (% canvas width)', .1, 200., 100., 1., '%'),
    ('window_height', 'Window height (% canvas height)', .1, 200., 100., 1., '%'),
    ('window_feather', 'Window feather (% shape radius)', 0., 100., 100., 1., '%'),
)
_MATTE_CONTROLS = (
    ('matte_black', 'Matte black point (%)', 0., 100., 100., 1., '%'),
    ('matte_white', 'Matte white point (%)', 0., 100., 100., 1., '%'),
    ('matte_morphology', 'Matte shrink / grow (%)', -100., 100., 100., 1., '%'),
    ('matte_smoothing', 'Matte smoothing (%)', 0., 100., 100., 1., '%'),
)
_ADJUSTMENT_CONTROLS = (
    ('exposure', 'Encoded exposure (stops)', -1., 1., 1., .05, ''),
    ('saturation', 'Luma-relative saturation (%)', 0., 200., 100., 1., '%'),
    ('hue_shift', 'Hue rotation (degrees)', -60., 60., 360., 1., '°'),
)


def _control_value(settings, key):
    # Resolve legacy symmetric values for display without changing stored fields.
    if key.endswith(('_softness_low', '_softness_high')):
        channel, _, edge = key.split('_')
        return range_softness(settings, channel, edge)
    return getattr(settings, key)


class SignedPercentageSpinBox(QDoubleSpinBox):
    """Show both directions explicitly without changing the persisted value."""
    def textFromValue(self, value):
        text = super().textFromValue(value)
        return ('+' + text) if value > 0 else text


class QualifierDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.snapshot = None
        self.matte = None
        self.preview_stride = 1
        self.preview_shape = None
        self.viewport = None
        self.sample_source_position = None
        self.job = None
        self._closed_flag = False
        self._invalidated = False
        self._window_drag_before = None
        clip = window.selected_parameter_clip()
        self.clip_start = clip.start
        self.settings = copy.deepcopy(clip.grade.qualifier)
        self.grade_enabled = clip.grade.enabled
        self.context = (id(window.project), window.revision, window.selection,
                        window.curve_key(), window.timeline.playhead,
                        window.selected_clip_ids, window.selection_generation)
        self.setWindowTitle(tr('H/S/Y′ qualifier · static frame'))
        self.resize(680, 680)
        self.setMinimumSize(320, 600)
        layout = QVBoxLayout(self)
        # Preview and transaction controls stay visible while range controls
        # scroll, so tuning a lower control never hides the real static matte.
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumSize(0, 0)
        content = QWidget(); content.setMinimumSize(0, 0)
        body = QVBoxLayout(content)
        self.stage = QLabel(tr(STAGE_TEXT)); self.stage.setWordWrap(True); body.addWidget(self.stage)
        self.canvas = QualifierCanvas(); layout.addWidget(self.canvas, 2)
        preview_row = QHBoxLayout()
        preview_label = QLabel(tr('Static preview'))
        self.preview_choice = QComboBox()
        self.preview_choice.addItem(tr('Range matte (even when correction disabled)'), 'matte')
        self.preview_choice.addItem(tr('Source before qualifier'), 'source')
        self.preview_choice.addItem(tr('Adjusted before LUT'), 'adjusted')
        self.preview_choice.addItem(tr('Selection overlay · magenta 50%'), 'overlay')
        self.preview_choice.setToolTip(tr('Overlay: magenta at 50% × refined color-matte weight × window weight over clipped source. Excluded pixels retain source color. Display only; sampling always reads underlying float source.'))
        self.preview_choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.preview_choice.setMinimumContentsLength(10)
        self.preview_choice.setMinimumWidth(0)
        self.preview_choice.setAccessibleName(tr('Static preview'))
        preview_label.setBuddy(self.preview_choice)
        preview_row.addWidget(preview_label); preview_row.addWidget(self.preview_choice, 1)
        layout.addLayout(preview_row)
        view_row = QHBoxLayout()
        self.zoom_choice = QComboBox()
        for label, zoom in [('Overview (sparse)', 0), ('Detail · 512 px', 1),
                            ('Detail · 256 px', 2), ('Detail · 128 px', 4), ('Detail · 64 px', 8)]:
            self.zoom_choice.addItem(tr(label), zoom)
        self.zoom_choice.setAccessibleName(tr('Source detail zoom'))
        self.zoom_choice.setFixedWidth(105)
        self.zoom_choice.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.zoom_choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.zoom_choice.setMinimumContentsLength(3)
        view_row.addWidget(self.zoom_choice, 1)
        self.center_x, self.center_y = QSpinBox(), QSpinBox()
        for spin, axis in ((self.center_x, 'X'), (self.center_y, 'Y')):
            spin.setRange(0, 0); spin.setKeyboardTracking(False)
            spin.setPrefix(axis + ': ')
            spin.setAccessibleName(tr('Detail center X' if axis == 'X' else 'Detail center Y'))
            spin.setFixedWidth(90)
            # Coordinates remain keyboard/wheel editable. Omitting arrow
            # buttons reserves enough text width for five-digit canvases.
            spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            spin.setStyleSheet('QSpinBox { padding-right: 7px; }')
            # The application stylesheet also pads nested line edits. Avoid
            # double padding so full canvas coordinates fit inside the compact editor.
            spin.lineEdit().setStyleSheet('padding: 0px; border: none;')
            view_row.addWidget(spin)
            spin.valueChanged.connect(self.render_preview)
        layout.addLayout(view_row)
        self.zoom_choice.currentIndexChanged.connect(self.render_preview)
        self.zoom_choice.setEnabled(False)
        self.canvas.detail_requested.connect(self.open_detail)
        self.canvas.pan_requested.connect(self.pan_view)
        self.canvas.zoom_requested.connect(self.zoom_view)
        self.canvas.setToolTip(tr('Double-click to inspect detail. Scroll to zoom; middle-drag to pan. X/Y are fitted-canvas pixel coordinates.'))
        self.pick_button = QPushButton(tr('Pick source'))
        self.pick_button.setCheckable(True); self.pick_button.setAutoDefault(False)
        self.pick_button.setEnabled(False)
        self.pick_button.setAccessibleName(tr('Replace ranges from source click'))
        self.pick_button.setToolTip(tr('Pick replaces enabled base channels with H ±10°, S/Y′ ±10 percentage points. Disabled channel values, feathers and switches stay unchanged.'))
        pick_row = QHBoxLayout()
        self.sample_mode = QComboBox()
        self.sample_mode.setObjectName('qualifier_sample_mode')
        self.sample_mode.setAccessibleName(tr('Sampling mode'))
        for label, mode in [('Replace', 'replace'), ('Add', 'add'), ('Subtract', 'subtract'), ('Soften', 'soften')]:
            self.sample_mode.addItem(tr(label), mode)
        self.sample_mode.setMinimumWidth(112)
        self.pick_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.sample_mode.currentIndexChanged.connect(self.change_sample_mode)
        # Propagate the combo's governed minimum through its host. A fixed
        # host clips larger native-font minima and lets the combo cover Pick.
        mode_host = QWidget()
        mode_host.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        mode_layout = QHBoxLayout(mode_host); mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.addWidget(self.sample_mode)
        pick_row.addWidget(mode_host)
        pick_row.addWidget(self.pick_button, 1)
        layout.addLayout(pick_row)
        self.softness_row = QWidget()
        softness_layout = QHBoxLayout(self.softness_row)
        softness_layout.setContentsMargins(0, 0, 0, 0)
        self.softness_channel = QComboBox()
        self.softness_channel.setObjectName('qualifier_softness_channel')
        self.softness_channel.setAccessibleName(tr('Feather channel'))
        for label, channel in [('H · hue', 'hue'), ('HSV-S', 'saturation'), ('Y′ · luma', 'luma')]:
            self.softness_channel.addItem(tr(label), channel)
        self.softness_channel.setMinimumWidth(110)
        self.softness_channel.currentIndexChanged.connect(self.change_softness_channel)
        self.softness_target = QLabel()
        self.softness_target.setWordWrap(True)
        self.softness_target.setToolTip(tr(SOFTNESS_HELP_TEXT))
        channel_host = QWidget()
        channel_host.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        channel_layout = QHBoxLayout(channel_host); channel_layout.setContentsMargins(0, 0, 0, 0)
        channel_layout.addWidget(self.softness_channel)
        softness_layout.addWidget(channel_host)
        softness_layout.addWidget(self.softness_target, 1)
        self.softness_row.hide()
        layout.addWidget(self.softness_row)
        self.sample_label = QLabel(tr('Pick replaces enabled base channels with H ±10°, S/Y′ ±10 percentage points. Disabled channel values, feathers and switches stay unchanged.'))
        self.sample_label.setWordWrap(True); layout.addWidget(self.sample_label)
        self.pick_button.toggled.connect(self.arm_picker)
        self.canvas.sample_clicked.connect(self.pick_sample)
        self.window_edit_button = QPushButton(tr('Edit window handles'))
        self.window_edit_button.setObjectName('qualifier_window_edit')
        self.window_edit_button.setAccessibleName(tr('Edit window handles'))
        self.window_edit_button.setAccessibleDescription(tr(HANDLE_HELP_TEXT))
        self.window_edit_button.setCheckable(True); self.window_edit_button.setAutoDefault(False)
        self.window_edit_button.setEnabled(False)
        self.window_edit_button.setToolTip(tr(HANDLE_OFF_TEXT))
        self.window_edit_button.toggled.connect(self.arm_window_editor)
        self.canvas.window_drag_started.connect(self.begin_window_drag)
        self.canvas.window_geometry_changed.connect(self.update_window_drag)
        self.canvas.window_drag_finished.connect(self.finish_window_drag)
        self.metadata = QLabel(tr('Reading the selected clip frame…'))
        self.metadata.setWordWrap(True); body.addWidget(self.metadata)
        self.resolution = QLabel(tr('Reading the selected clip frame…'))
        self.resolution.setWordWrap(True); layout.addWidget(self.resolution)
        legend = QLabel(tr('Matte: refined color-matte weight × window weight. White selects, black excludes, gray feathers. Fitted-canvas padding can qualify.'))
        legend.setWordWrap(True); body.addWidget(legend)
        overlay_note = QLabel(tr('Overlay: magenta at 50% × refined color-matte weight × window weight over clipped source. Excluded pixels retain source color. Display only; sampling always reads underlying float source.'))
        overlay_note.setWordWrap(True); body.addWidget(overlay_note)
        note = QLabel(tr(RANGE_TEXT)); note.setWordWrap(True); body.addWidget(note)
        rules = QLabel(tr('Feather extends outside each range; enabled weights multiply. Disabled hue or a 180° hue half-width includes gray. An enabled narrower hue condition excludes gray; out-of-range RGB is always excluded.'))
        rules.setWordWrap(True); body.addWidget(rules)
        self.enabled_check = QCheckBox(tr('Enable qualifier correction'))
        self.enabled_check.setAccessibleName(tr('Enable qualifier correction'))
        self.enabled_check.setChecked(self.settings.enabled)
        body.addWidget(self.enabled_check)
        self.enabled_check.installEventFilter(self)
        self.channel_checks = {}
        self.controls = {}
        self.control_labels = {}
        for title, definitions in [('Color ranges and feather', _RANGE_CONTROLS),
                                    ('Matte refinement', _MATTE_CONTROLS),
                                    ('Static spatial window', _WINDOW_CONTROLS),
                                    ('Adjust selected color', _ADJUSTMENT_CONTROLS)]:
            group = QGroupBox(tr(title))
            form = QFormLayout(group)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            if definitions is _RANGE_CONTROLS:
                self.range_choice = QComboBox()
                self.range_choice.setObjectName('qualifier_range_choice')
                self.range_choice.setAccessibleName(tr('Edit range'))
                self.range_choice.setMinimumWidth(0)
                self.range_choice.setMinimumContentsLength(8)
                self.range_choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                self.range_choice.currentIndexChanged.connect(self.select_range)
                form.addRow(tr('Edit range'), self.range_choice)
                self.remove_range_button = QPushButton(tr('Remove selected range'))
                self.reset_ranges_button = QPushButton(tr('Clear added/subtracted ranges'))
                for button in (self.remove_range_button, self.reset_ranges_button):
                    button.setAutoDefault(False)
                    form.addRow(button)
                    button.installEventFilter(self)
                self.remove_range_button.clicked.connect(self.remove_selected_range)
                self.reset_ranges_button.clicked.connect(self.reset_extra_ranges)
                help_label = QLabel(tr('Base inclusion is retained. Add keeps separate ranges; Subtract removes matching colors. Up to 8 of each. Select a range to edit its bounds and feather.'))
                help_label.setWordWrap(True); form.addRow(help_label)
                self.range_choice.installEventFilter(self)
                for channel, label in [('hue', 'Use hue condition'), ('saturation', 'Use HSV saturation condition'), ('luma', 'Use Y′ luma condition')]:
                    check = QCheckBox(tr(label))
                    check.setObjectName(f'qualifier_{channel}_enabled')
                    check.setAccessibleName(tr(label))
                    check.setChecked(getattr(self.settings, f'{channel}_enabled'))
                    check.toggled.connect(lambda value, c=channel: self.change_setting(f'{c}_enabled', value))
                    check.installEventFilter(self)
                    self.channel_checks[channel] = check
                    form.addRow(check)
                self.channel_note = QLabel(tr('Disabled conditions keep their values and contribute full weight. All three off selects all valid colors; an exclusion with all off clears the color matte. Replace/Add/Subtract sample only enabled base channels; Soften requires its selected channel enabled.'))
                self.channel_note.setWordWrap(True); form.addRow(self.channel_note)

            if definitions is _WINDOW_CONTROLS:
                self.window_shape = QComboBox()
                self.window_shape.setObjectName('qualifier_window_shape')
                self.window_shape.setAccessibleName(tr('Window shape'))
                self.window_shape.setMinimumWidth(0)
                self.window_shape.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                self.window_shape.setMinimumContentsLength(8)
                for label, value in [('Off (color ranges only)', 'off'), ('Ellipse', 'ellipse'), ('Rectangle', 'rectangle')]:
                    self.window_shape.addItem(tr(label), value)
                self.window_shape.setCurrentIndex(self.window_shape.findData(self.settings.window_shape))
                shape_label = QLabel(tr('Window shape')); shape_label.setWordWrap(True); shape_label.setBuddy(self.window_shape)
                form.addRow(shape_label, self.window_shape)
                self.control_labels[self.window_shape] = shape_label
                self.window_shape.installEventFilter(self)
                self.window_shape.currentIndexChanged.connect(lambda: self.change_setting('window_shape', self.window_shape.currentData()))
                # Keep the explicit geometry mode with its shape controls.
                # Another fixed preview row would crowd out labels in a
                # narrow dialog after localized source/sample text wraps.
                form.addRow(self.window_edit_button)
                self.animation_note = QLabel(tr('Center and size are animated. Apply or cancel here, then use Window animation… in Color to edit keys. Shape, feather and color ranges remain editable here.'))
                self.animation_note.setWordWrap(True)
                self.animation_note.setVisible(bool(self.settings.window_keyframes))
                form.addRow(self.animation_note)
                self.window_edit_button.installEventFilter(self)
                self.window_invert = QCheckBox(tr('Invert window only'))
                self.window_invert.setObjectName('qualifier_window_invert')
                self.window_invert.setAccessibleName(tr('Invert window only'))
                self.window_invert.setChecked(self.settings.window_invert)
                self.window_invert.installEventFilter(self)
                self.window_invert.toggled.connect(lambda value: self.change_setting('window_invert', value))
                form.addRow(self.window_invert)
            for key, label, low, high, scale, step, suffix in definitions:
                spin = SignedPercentageSpinBox() if key == 'matte_morphology' else QDoubleSpinBox()
                spin.setDecimals(3); spin.setRange(low, high); spin.setSingleStep(step)
                spin.setSuffix(suffix); spin.setKeyboardTracking(False)
                spin.setAccessibleName(tr(label)); spin.setObjectName('qualifier_' + key)
                spin.setValue(_control_value(self.settings, key) * scale)
                if key.endswith(('_softness_low', '_softness_high')):
                    hint = tr('Low feather extends below the minimum; high feather extends above the maximum. Each is independent. Core bounds and the opposite edge stay unchanged.')
                    spin.setToolTip(hint); spin.setAccessibleDescription(hint)
                if key == 'matte_morphology':
                    hint = tr('Negative shrinks with a 3×3 minimum; positive grows with a 3×3 maximum. 0% is unchanged; −100% / +100% use the full result. Fixed square radius of 1 project pixel, after levels and before smoothing/window. Uses true adjacent source pixels; not a pixel distance.')
                    spin.setToolTip(hint); spin.setAccessibleDescription(hint)
                if key == 'matte_smoothing':
                    spin.setToolTip(tr('Fixed 1 project-pixel radius, after matte levels and shrink/grow, before the window. Uses true adjacent source pixels in both overview and detail.'))
                    spin.setAccessibleDescription(tr('Fixed 1 project-pixel radius, after matte levels and shrink/grow, before the window. Uses true adjacent source pixels in both overview and detail.'))
                label_widget = QLabel(tr(label)); label_widget.setWordWrap(True); label_widget.setBuddy(spin)
                form.addRow(label_widget, spin)
                self.controls[key] = spin
                self.control_labels[spin] = label_widget
                spin.installEventFilter(self); spin.lineEdit().installEventFilter(self)
                spin.valueChanged.connect(lambda value, field=key, factor=scale: self.change_setting(field, value / factor))
            if definitions is _MATTE_CONTROLS:
                self.reset_matte_button = QPushButton(tr('Reset matte refinement'))
                self.reset_matte_button.setObjectName('qualifier_reset_matte')
                self.reset_matte_button.setAutoDefault(False)
                self.reset_matte_button.installEventFilter(self)
                self.reset_matte_button.clicked.connect(self.reset_matte_levels)
                form.addRow(self.reset_matte_button)
                matte_note = QLabel(tr('Remap black/white points, shrink/grow with a 3×3 minimum/maximum, blend with a weighted 3×3 average, then apply the window. Each spatial step has a fixed 1 project-pixel radius. Amounts are blend strengths, not pixel distances. No tracking or temporal smoothing.'))
                matte_note.setWordWrap(True); form.addRow(matte_note)
            body.addWidget(group)
        window_note = QLabel(tr('Window coordinates use the fitted project canvas before clip position, scale, rotation and mirror. Width/height are full dimensions; feather extends outside the shape by a fraction of its radius. Invert affects only the window, never the color ranges. Use Window animation… for manual center/size keys; no tracking.'))
        window_note.setWordWrap(True); body.addWidget(window_note)
        adjustment_note = QLabel(tr('Exposure scales encoded code values, not scene-linear light. Saturation scales deviation from Rec.709 Y′.'))
        adjustment_note.setWordWrap(True); body.addWidget(adjustment_note)
        # Keep editable ranges first; detailed stage/limits remain available
        # below them without displacing the live preview or initial controls.
        for detail in (legend, overlay_note, self.stage, self.metadata, note, rules, window_note):
            body.removeWidget(detail); body.addWidget(detail)
        self.status = QLabel(tr('No project changes until Apply.'))
        self.status.setWordWrap(True); self.status.setAccessibleName(tr('Qualifier status'))
        body.addStretch(1)
        self.scroll.setWidget(content); layout.addWidget(self.scroll, 3)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.apply_button = QPushButton(tr('Apply qualifier')); self.apply_button.setEnabled(False)
        self.close_button = QPushButton(tr('Cancel'))
        # Enter in a numeric editor must not accidentally commit the dialog.
        self.apply_button.setAutoDefault(False); self.close_button.setAutoDefault(False)
        buttons.addWidget(self.apply_button); buttons.addWidget(self.close_button); layout.addLayout(buttons)
        self.preview_timer = QTimer(self); self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(50); self.preview_timer.timeout.connect(self.render_preview)
        self.enabled_check.toggled.connect(lambda enabled: self.change_setting('enabled', enabled))
        self.preview_choice.currentIndexChanged.connect(self.render_preview)
        self.apply_button.clicked.connect(self.apply_settings); self.close_button.clicked.connect(self.reject)
        self.timer = QTimer(self); self.timer.setInterval(150)
        self.timer.timeout.connect(self.check_current); self.timer.start()
        self.finished.connect(self._closed)
        self.refresh_ranges()
        self.sync_animation_controls()

    def sync_animation_controls(self):
        animated = bool(self.settings.window_keyframes)
        self.animation_note.setVisible(animated)
        for key in ('window_center_x', 'window_center_y', 'window_width', 'window_height'):
            self.controls[key].setEnabled(not animated and not self._invalidated and not self._closed_flag)
        if animated:
            evaluated = evaluate_window(self.settings, self.context[4] - self.clip_start)
            for key in ('window_center_x', 'window_center_y', 'window_width', 'window_height'):
                spin = self.controls[key]
                spin.blockSignals(True); spin.setValue(getattr(evaluated, key) * 100); spin.blockSignals(False)

    def reset_matte_levels(self):
        if not self.current():
            self.invalidate(); return
        self.canvas.cancel_window_drag()
        self.settings.matte_black, self.settings.matte_white = 0.0, 1.0
        self.settings.matte_morphology = self.settings.matte_smoothing = 0.0
        for key, value in (('matte_black', 0.), ('matte_white', 100.),
                           ('matte_morphology', 0.), ('matte_smoothing', 0.)):
            control = self.controls[key]
            control.blockSignals(True); control.setValue(value); control.blockSignals(False)
        self.change_setting('matte_black', 0.0)

    def selected_range(self):
        key = self.range_choice.currentData()
        if key and key[0] != 'base':
            return getattr(self.settings, key[0])[key[1]]
        return self.settings

    def refresh_ranges(self, selected=('base', 0)):
        self.range_choice.blockSignals(True); self.range_choice.clear()
        self.range_choice.addItem(tr('Base inclusion'), ('base', 0))
        for field, label in [('inclusion_ranges', 'Include {number}'), ('exclusion_ranges', 'Exclude {number}')]:
            for index, _ in enumerate(getattr(self.settings, field)):
                self.range_choice.addItem(tr(label, number=index + 1), (field, index))
        index = next((i for i in range(self.range_choice.count())
                      if self.range_choice.itemData(i) == selected), 0)
        self.range_choice.setCurrentIndex(max(0, index)); self.range_choice.blockSignals(False)
        self.select_range()
        self.reset_ranges_button.setEnabled(bool(self.settings.inclusion_ranges or self.settings.exclusion_ranges))

    def select_range(self, *_):
        self.pick_button.setChecked(False)
        selected = self.selected_range()
        for key, _label, _low, _high, scale, _step, _suffix in _RANGE_CONTROLS:
            spin = self.controls[key]; spin.blockSignals(True)
            spin.setValue(_control_value(selected, key) * scale); spin.blockSignals(False)
        self.sync_channel_controls()
        base = self.range_choice.currentData() == ('base', 0)
        self.update_softness_target()
        self.remove_range_button.setEnabled(not base)
        self.remove_range_button.setToolTip(tr('Base inclusion cannot be removed; use Replace to start a new selection.') if base else '')

    def sync_channel_controls(self):
        selected = self.selected_range()
        editable = not self._invalidated and not self._closed_flag
        for channel, check in self.channel_checks.items():
            check.blockSignals(True)
            check.setChecked(getattr(selected, f'{channel}_enabled'))
            check.blockSignals(False)
            check.setEnabled(editable)
            for key, *_ in _RANGE_CONTROLS:
                if key.startswith(channel + '_'):
                    self.controls[key].setEnabled(editable and check.isChecked())

    def remove_selected_range(self):
        if not self.current():
            self.invalidate(); return
        key = self.range_choice.currentData()
        if not key or key[0] == 'base':
            return
        self.canvas.cancel_window_drag()
        del getattr(self.settings, key[0])[key[1]]
        self.refresh_ranges(); self.change_setting('enabled', self.settings.enabled)

    def reset_extra_ranges(self):
        if not self.current():
            self.invalidate(); return
        self.canvas.cancel_window_drag()
        # Clearing also repairs a temporarily invalid extra range.
        self.settings.inclusion_ranges = []; self.settings.exclusion_ranges = []
        self.refresh_ranges(); self.change_setting('enabled', self.settings.enabled)

    def update_softness_target(self):
        self.softness_target.setText(tr('Feather · {range} · {channel}',
            range=self.range_choice.currentText(), channel=self.softness_channel.currentText()))

    def change_softness_channel(self, *_):
        self.pick_button.setChecked(False)
        self.update_softness_target()
        self.sample_label.setText(tr(SOFTNESS_HELP_TEXT))

    def change_sample_mode(self, *_):
        self.pick_button.setChecked(False)
        mode = self.sample_mode.currentData()
        text = {'replace': 'Replace ranges from source click',
                'add': 'Add range from source click',
                'subtract': 'Subtract range from source click',
                'soften': 'Soften selected range channel from source click'}[mode]
        self.softness_row.setVisible(mode == 'soften')
        self.update_softness_target()
        self.pick_button.setText(tr('Pick source')); self.pick_button.setAccessibleName(tr(text))
        self.pick_button.setToolTip(tr('Replace resets the selection. Add/Subtract keep independent ranges. Every click samples frozen float source; adjustments and window stay unchanged.'))
        self.sample_label.setText(tr(SOFTNESS_HELP_TEXT) if mode == 'soften' else tr('Choose a mode, then arm sampling and click once. No project changes until Apply.'))
        if mode == 'soften':
            self.pick_button.setToolTip(tr(SOFTNESS_HELP_TEXT))

    def set_detail_center(self, x, y):
        self.canvas.cancel_window_drag()
        if not self.current() or self.snapshot is None:
            if not self.current():
                self.invalidate()
            return
        for spin, value in ((self.center_x, x), (self.center_y, y)):
            spin.blockSignals(True); spin.setValue(round(value)); spin.blockSignals(False)
        self.render_preview()

    def open_detail(self, x, y):
        self.canvas.cancel_window_drag()
        if self.viewport is None or not self.current():
            if not self.current():
                self.invalidate()
            return
        position = self.viewport.source_at(x, y)
        if position is None:
            return
        self.zoom_choice.blockSignals(True)
        if self.zoom_choice.currentData() == 0:
            self.zoom_choice.setCurrentIndex(1)
        self.zoom_choice.blockSignals(False)
        self.set_detail_center(*position)

    def pan_view(self, dx, dy):
        if self.viewport is None or self.zoom_choice.currentData() == 0:
            return
        # Move from the actual clamped crop center, avoiding dead travel at an
        # image edge when requested numeric coordinates lie beyond that center.
        view = self.viewport
        self.set_detail_center(view.x + (view.width - 1) // 2 + dx,
                               view.y + (view.height - 1) // 2 + dy)

    def zoom_view(self, direction):
        self.canvas.cancel_window_drag()
        if self.snapshot is None or not self.current():
            return
        index = max(0, min(self.zoom_choice.count() - 1,
                           self.zoom_choice.currentIndex() + direction))
        self.zoom_choice.setCurrentIndex(index)

    def arm_picker(self, armed):
        if armed and (not self.current() or self.snapshot is None or not self.valid_settings()):
            self.pick_button.setChecked(False); return
        self.canvas.picking = armed
        if armed:
            self.window_edit_button.setChecked(False)
        self.canvas.setCursor(Qt.CursorShape.CrossCursor if armed else Qt.CursorShape.ArrowCursor)
        if armed:
            if self.sample_mode.currentData() == 'soften':
                if self.preview_choice.currentData() != 'overlay':
                    self.preview_choice.setCurrentIndex(1)
                self.sample_label.setText(tr(SOFTNESS_HELP_TEXT))
            elif self.sample_mode.currentData() != 'replace':
                if self.preview_choice.currentData() != 'overlay':
                    self.preview_choice.setCurrentIndex(1)
                self.sample_label.setText(tr('Click once to add an independent include/exclude range from underlying float source. Overlay tint is never sampled.'))
            elif self.preview_choice.currentData() == 'overlay':
                self.sample_label.setText(tr('Click through the magenta overlay to sample underlying float source, not tint. Replaces enabled base channels and clears extra ranges.'))
            else:
                self.preview_choice.setCurrentIndex(1)
                self.sample_label.setText(tr('Click a displayed source sample. Replaces enabled base channels and clears extra ranges; disabled channel values stay unchanged.'))
        self.preview_choice.setEnabled(not armed)

    def sync_window_tools(self):
        usable = (not self._closed_flag and not self._invalidated and self.snapshot is not None
                  and not self.canvas.image.isNull() and self.settings.window_shape != 'off'
                  and not self.settings.window_keyframes)
        self.window_edit_button.setEnabled(usable)
        if not usable:
            self.window_edit_button.setChecked(False)
        self.window_edit_button.setToolTip(tr(HANDLE_HELP_TEXT if self.settings.window_shape != 'off' else HANDLE_OFF_TEXT))
        canvas_size = ((self.snapshot.frame.shape[1], self.snapshot.frame.shape[0])
                       if self.snapshot is not None else None)
        try:
            evaluated = evaluate_window(self.settings, self.context[4] - self.clip_start)
        except (ValueError, TypeError):
            evaluated = self.settings  # Temporarily invalid ranges have no live canvas.
        self.canvas.set_window_context(WindowGeometry.from_settings(evaluated), self.viewport,
                                       canvas_size, usable and self.window_edit_button.isChecked())
        self.canvas.setToolTip(tr(HANDLE_NAV_TEXT if self.canvas.window_editing else
            'Double-click to inspect detail. Scroll to zoom; middle-drag to pan. X/Y are fitted-canvas pixel coordinates.'))

    def arm_window_editor(self, armed):
        if armed and (not self.current() or self.snapshot is None or not self.valid_settings()
                      or self.settings.window_shape == 'off' or self.settings.window_keyframes):
            self.window_edit_button.setChecked(False); return
        if not armed:
            self.canvas.cancel_window_drag()
        else:
            self.pick_button.setChecked(False)
        self.canvas.setCursor(Qt.CursorShape.ArrowCursor)
        self.sync_window_tools()

    def begin_window_drag(self):
        if not self.current() or self.snapshot is None or not self.valid_settings():
            self.canvas.cancel_window_drag()
            if not self.current():
                self.invalidate()
            return
        self._window_drag_before = WindowGeometry.from_settings(self.settings)

    def _set_window_geometry(self, geometry):
        # Updating displayed numeric controls must not round the actual values
        # to their three-decimal percent presentation or touch other fields.
        for key, value in geometry.values().items():
            setattr(self.settings, key, value)
            spin = self.controls[key]; spin.blockSignals(True)
            spin.setValue(value * 100); spin.blockSignals(False)

    def update_window_drag(self, geometry):
        if not self.current():
            self.invalidate(); return
        if self._window_drag_before is None:
            return
        self._set_window_geometry(geometry)
        self.preview_timer.start()

    def finish_window_drag(self, accepted):
        before, self._window_drag_before = self._window_drag_before, None
        if before is not None and not accepted:
            self._set_window_geometry(before)
        if before is not None and not self._closed_flag and not self._invalidated:
            self.render_preview()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.canvas.cancel_window_drag():
            event.accept(); return
        super().keyPressEvent(event)

    def event(self, event):
        # Some platforms deliver activation changes only to the top-level
        # dialog, rather than to the child that owns the pointer capture.
        if event.type() in (QEvent.Type.WindowDeactivate, QEvent.Type.Hide):
            canvas = getattr(self, 'canvas', None)
            if canvas is not None:
                canvas.cancel_window_drag()
        return super().event(event)

    def pick_sample(self, x, y):
        if not self.current():
            self.invalidate(); return
        if not self.canvas.picking or self.snapshot is None or self.preview_shape is None:
            return
        height, width = self.preview_shape
        if not (0 <= x < width and 0 <= y < height):
            return
        coordinate = self.viewport.source_at(x, y) if self.viewport is not None else None
        if coordinate is None:
            return
        sx, sy = coordinate
        # Both overview and detail pick exactly the float source pixel shown.
        # Detail uses every local pixel, not an enlargement of the coarse grid.
        if not self.snapshot.valid_mask[sy, sx]:
            self.sample_label.setText(tr('Pick inside the active picture, not fitted-canvas padding.')); return
        try:
            mode = self.sample_mode.currentData()
            if mode == 'replace':
                settings, sample = initialize_from_sample(self.snapshot.frame[sy, sx], self.settings)
                settings = clear_qualifier_ranges(settings)
                selected = ('base', 0)
            elif mode == 'soften':
                selected = self.range_choice.currentData()
                settings, sample = soften_qualifier_range(
                    self.settings, self.snapshot.frame[sy, sx], target=selected,
                    channel=self.softness_channel.currentData(), valid=bool(self.snapshot.valid_mask[sy, sx]))
                changed = settings != self.settings
            else:
                # Preserve the established exact invalid-sample explanation.
                initialize_from_sample(self.snapshot.frame[sy, sx], self.settings)
                settings, sample = add_qualifier_range(self.settings, self.snapshot.frame[sy, sx], exclude=mode == 'subtract')
                field = 'exclusion_ranges' if mode == 'subtract' else 'inclusion_ranges'
                selected = (field, len(getattr(settings, field)) - 1)
        except ValueError as exc:
            self.sample_label.setText(tr_error(exc)); return
        self.settings = settings
        self.refresh_ranges(selected)
        self.sample_source_position = (sx, sy)
        self.canvas.sample_position = (x, y)
        self.pick_button.setChecked(False)
        if self.preview_choice.currentData() != 'overlay':
            self.preview_choice.setCurrentIndex(0)
        self.render_preview()
        sample_text = ('Source ({x}, {y}) · RGB {rgb} · H {h}° / S {s}% / Y′ {yprime}%\nRanges replaced; overlay is display only. Sampled underlying float source, not tint.'
                       if self.preview_choice.currentData() == 'overlay' else
                       'Source ({x}, {y}) · RGB {rgb} · H {h}° / S {s}% / Y′ {yprime}%\nRanges replaced; the matte shows the pending selection. No project changes until Apply.')
        if mode == 'soften':
            sample_text = 'Source ({x}, {y}) · RGB {rgb} · H {h}° / S {s}% / Y′ {yprime}%\nSelected channel feather sampled; core bounds unchanged. No project changes until Apply.'
        elif mode != 'replace':
            sample_text = 'Source ({x}, {y}) · RGB {rgb} · H {h}° / S {s}% / Y′ {yprime}%\nIndependent range sampled; overlay is display only. No project changes until Apply.'
        readout = tr(sample_text,
            x=sx, y=sy, rgb=', '.join(f'{float(v):.6f}' for v in self.snapshot.frame[sy, sx]),
            h=f'{sample.hue * 360:.3f}' if sample.hue is not None else tr('undefined'), s=f'{sample.saturation * 100:.3f}',
            yprime=f'{sample.luma * 100:.3f}')
        self.sample_label.setToolTip(readout)
        if mode == 'soften':
            self.sample_label.setText(tr('Source ({x}, {y}) · {result}\n{range} · {channel}. No project changes until Apply.',
                x=sx, y=sy, result=tr('Feather increased' if changed else 'Feather unchanged'),
                range=self.range_choice.currentText(), channel=self.softness_channel.currentText()))
            self.sample_label.setToolTip(readout + '\n' + tr(SOFTNESS_HELP_TEXT))
            return
        self.sample_label.setText(readout if mode == 'replace' else tr(
            'Source ({x}, {y}) · {mode} range sampled.\nNo project changes until Apply.',
            x=sx, y=sy, mode=tr('Include' if mode == 'add' else 'Exclude')))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn:
            editable = [self.enabled_check, self.window_shape, self.window_invert,
                        self.window_edit_button, self.range_choice, self.remove_range_button, self.reset_ranges_button, self.reset_matte_button, *self.controls.values(), *self.channel_checks.values()]
            target = watched if watched in editable else watched.parent()
            if target in editable:
                self.reveal_control(target)
                # QScrollArea can scroll the numeric caret after FocusIn.
                # Reapply labelled visibility once Qt finishes that focus pass.
                QTimer.singleShot(0, self, lambda control=target:
                                  self.reveal_control(control) if self.isVisible() else None)
        return super().eventFilter(watched, event)

    def reveal_control(self, target):
        # Qt's generic helper may reveal only a spin box's caret. Keep the
        # entire numeric control and (when space permits) its label visible.
        body = self.scroll.widget(); viewport_height = self.scroll.viewport().height()
        top = target.mapTo(body, QPoint()).y(); bottom = top + target.height()
        label = self.control_labels.get(target)
        if label is not None:
            label_top = label.mapTo(body, QPoint()).y()
            labelled_top = min(top, label_top)
            labelled_bottom = max(bottom, label_top + label.height())
            if labelled_bottom - labelled_top + 16 <= viewport_height:
                top, bottom = labelled_top, labelled_bottom
        bar = self.scroll.verticalScrollBar()
        if top < bar.value() + 8:
            bar.setValue(max(0, top - 8))
        elif bottom > bar.value() + viewport_height - 8:
            bar.setValue(max(0, bottom - viewport_height + 8))

    def context_current(self):
        window = self.window
        return (not self._closed_flag and not self._invalidated and not window.closing
                and self.context == (id(window.project), window.revision, window.selection,
                                      window.curve_key(), window.timeline.playhead,
                                      window.selected_clip_ids, window.selection_generation)
                and window.active_selection_type == 'clip' and window.selected_parameter_clip() is not None)

    def current(self):
        return (self.context_current() and (self.snapshot is None or
                self.snapshot.is_current(self.window.project, self.context[2], self.context[4])))

    def invalidate(self):
        if self._closed_flag:
            return
        self._invalidated = True
        self.canvas.cancel_window_drag()
        self.window_edit_button.setChecked(False); self.window_edit_button.setEnabled(False)
        self.pick_button.setChecked(False); self.pick_button.setEnabled(False)
        self.apply_button.setEnabled(False); self.canvas.setEnabled(False)
        self.zoom_choice.setEnabled(False); self.center_x.setEnabled(False); self.center_y.setEnabled(False)
        self.set_editing_enabled(False)
        self.status.setText(tr(STALE_TEXT))
        self.preview_timer.stop(); self.timer.stop()
        if self.job is not None:
            self.job.cancel.set()

    def set_editing_enabled(self, enabled):
        for control in [self.enabled_check, self.window_shape, self.window_invert, self.sample_mode, self.softness_channel,
                        self.range_choice, self.remove_range_button, self.reset_ranges_button, self.reset_matte_button, *self.controls.values(), *self.channel_checks.values()]:
            control.setEnabled(enabled)
        if enabled:
            self.sync_channel_controls()
            self.sync_animation_controls()

    def check_current(self):
        if not self.current():
            self.invalidate()

    def valid_settings(self):
        try:
            validate_qualifier(self.settings)
        except (ValueError, TypeError) as exc:
            self.apply_button.setEnabled(False)
            selected = self.selected_range()
            if selected.saturation_low > selected.saturation_high or selected.luma_low > selected.luma_high:
                self.status.setText(tr('Each minimum must be less than or equal to its maximum.'))
            elif self.settings.matte_black >= self.settings.matte_white:
                self.status.setText(tr('Matte black point must be strictly below white point.'))
            else:
                self.status.setText(tr_error(exc))
            return False
        return True

    def change_setting(self, key, value):
        if self.settings.window_keyframes and key in ('window_center_x', 'window_center_y', 'window_width', 'window_height'):
            self.sync_animation_controls(); return
        self.canvas.cancel_window_drag()
        if not self.current():
            self.invalidate(); return
        # The pair may be temporarily inverted while the user edits both ends.
        # Keep that invalid state local rather than invoking dataclass validation
        # in the Qt signal slot; validation disables Apply and explains it.
        selected = self.range_choice.currentData()
        if key in ({definition[0] for definition in _RANGE_CONTROLS} | CHANNEL_ENABLE_FIELDS) and selected and selected[0] != 'base':
            # Frozen records are replaced in this dialog's private deep copy.
            # Preserve temporary inverted endpoints until the user repairs them.
            item = copy.copy(self.selected_range())
            object.__setattr__(item, key, value)
            getattr(self.settings, selected[0])[selected[1]] = item
        else:
            setattr(self.settings, key, value)
        self.sync_channel_controls()
        valid = self.valid_settings()
        if not valid:
            self.pick_button.setChecked(False)
            self.matte = None
            self.canvas.image = QImage(); self.canvas._pixels = None; self.canvas._raster_cache = None
            self.canvas.update()
            self.sync_window_tools()
        self.pick_button.setEnabled(valid and self.snapshot is not None)
        self.apply_button.setEnabled(valid and self.snapshot is not None)
        if valid:
            self.preview_timer.start()
        if key == 'window_shape' and self.settings.window_shape == 'off':
            self.window_edit_button.setChecked(False)
        self.sync_window_tools()

    def accept_snapshot(self, snapshot):
        if not self.isVisible() or not self.context_current():
            return
        self.canvas.cancel_window_drag()
        if getattr(snapshot, 'stage', None) != 'qualifier':
            self.pick_button.setChecked(False); self.pick_button.setEnabled(False)
            self.canvas.sample_position = None
            self.viewport = None; self.sample_source_position = None
            self.zoom_choice.setEnabled(False); self.center_x.setEnabled(False); self.center_y.setEnabled(False)
            self.snapshot = None; self.matte = None; self.canvas.image = QImage(); self.canvas._pixels = None; self.canvas._raster_cache = None; self.canvas.update()
            self.apply_button.setEnabled(False)
            self.sync_window_tools()
            self.status.setText(tr('Cannot use a frame from a different color stage. Close and reopen the qualifier.'))
            return
        self.snapshot = snapshot
        self.sample_source_position = None
        height, width = snapshot.frame.shape[:2]
        for spin, size in ((self.center_x, width), (self.center_y, height)):
            spin.blockSignals(True); spin.setRange(0, size - 1)
            spin.setValue(size // 2); spin.blockSignals(False)
        self.zoom_choice.setEnabled(True)
        if not self.current():
            self.invalidate(); return
        restoration = tr('Spatial restoration: applied to active picture.' if snapshot.restoration_applied
                         else 'Spatial restoration: bypassed (disabled or zero strength).')
        self.stage.setText(tr('{stage}\n{restoration}', stage=tr(STAGE_TEXT), restoration=restoration))
        self.metadata.setText(tr('Timeline frame {timeline} · source frame {source_frame} at project rate · source time {seconds}s',
                                 timeline=self.context[4], source_frame=snapshot.source_frame,
                                 seconds=f'{float(snapshot.source_time):.6f}'))
        self.pick_button.setEnabled(self.valid_settings())
        self.render_preview()

    def accept_error(self, message):
        if self.isVisible() and self.context_current() and (self.job is None or not self.job.cancel.is_set()):
            self.status.setText(tr_error(message))
            self.apply_button.setEnabled(False)

    def render_preview(self, *_):
        self.preview_timer.stop()
        if not self.isVisible() or self._closed_flag:
            return
        if not self.current():
            self.invalidate(); return
        if not self.valid_settings() or self.snapshot is None:
            self.canvas.cancel_window_drag()
            self.canvas.image = QImage(); self.canvas._pixels = None; self.canvas._raster_cache = None
            self.canvas.update()
            self.sync_window_tools()
            return
        source = self.snapshot.frame
        # Exact float samples, not interpolation or an RGB8 proxy. Limit CPU
        # work on the GUI thread while keeping every displayed matte sample
        # mathematically identical to that location in the full export.
        zoom = self.zoom_choice.currentData()
        height, width = source.shape[:2]
        self.viewport = (overview(width, height) if zoom == 0 else
                         centered(width, height, (self.center_x.value(), self.center_y.value()), zoom))
        self.preview_stride = self.viewport.stride
        frame = self.viewport.extract(source)
        self.preview_shape = frame.shape[:2]
        self.center_x.setEnabled(zoom != 0); self.center_y.setEnabled(zoom != 0)
        self.canvas.sample_position = (self.viewport.grid_at(*self.sample_source_position)
                                      if self.sample_source_position is not None else None)
        if zoom == 0:
            self.resolution.setText(tr('Overview: {width}×{height} samples, step {step}, from {source_width}×{source_height}. Small details may be missed; use Detail. Export evaluates every pixel.',
                width=frame.shape[1], height=frame.shape[0], step=self.preview_stride,
                source_width=width, source_height=height))
        else:
            self.resolution.setText(tr('Detail: every pixel in {width}×{height} crop at ({x}, {y}) of {source_width}×{source_height} fitted canvas. Matte uses full-resolution local pixels; export covers the whole frame.',
                width=frame.shape[1], height=frame.shape[0], x=self.viewport.x, y=self.viewport.y,
                source_width=width, source_height=height))
        # The mask is computed on float source values, never a display QImage.
        # Enabling for inspection does not enable the saved/pending correction.
        # Grading also processes fitted-canvas padding in the renderer. Do not
        # hide that membership with the hue picker's active-picture valid_mask.
        coordinates = dict(canvas_size=(width, height), origin=(self.viewport.x, self.viewport.y),
                           stride=self.viewport.stride)
        # A sparse overview grid is not a neighborhood. Read actual adjacent
        # project pixels from the dense frozen frame, including crop halos.
        # Keep zero on the original exact pointwise path.
        if self.settings.matte_morphology or self.settings.matte_smoothing:
            coordinates['matte_source'] = source
        evaluated = evaluate_window(self.settings, self.context[4] - self.clip_start)
        self.matte = qualifier_mask(frame, replace(evaluated, enabled=True), **coordinates)
        mode = self.preview_choice.currentData()
        if mode == 'matte':
            preview = np.repeat(self.matte[..., None], 3, axis=-1)
        elif mode == 'overlay':
            preview = selection_overlay(frame, self.matte)
        elif mode == 'adjusted' and self.grade_enabled:
            preview = apply_qualifier(frame, evaluated, **coordinates)
        else:
            preview = frame
        self.canvas.set_frame(preview)
        self.sync_window_tools()
        self.apply_button.setEnabled(True)
        if not self.grade_enabled:
            self.status.setText(tr('Clip grading is disabled. Settings can be saved, but the adjusted view and render bypass the correction.'))
        elif not self.settings.enabled:
            self.status.setText(tr('Correction is disabled. The matte still shows the selected range. No project changes until Apply.'))
        else:
            self.status.setText(tr('No project changes until Apply.'))

    def apply_settings(self):
        # Apply is not a pointer-release substitute. If invoked during a lost
        # or interrupted gesture, save the stable pending settings instead.
        self.canvas.cancel_window_drag()
        if not self.current():
            self.invalidate(); return
        if self.snapshot is None or not self.valid_settings():
            return
        if self.settings.enabled and any(not any(getattr(entry, name) for name in CHANNEL_ENABLE_FIELDS)
                                         for entry in self.settings.exclusion_ranges):
            if QMessageBox.question(self, tr('Exclude all colors?'),
                    tr('An exclusion has all three conditions off. It excludes every valid color, so this qualifier correction will affect no pixels. Apply anyway?'),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            # The modal confirmation processes events; selection/revision may
            # have changed while it was open. Never apply to a new context.
            if not self.current():
                self.invalidate(); return
        settings = copy.deepcopy(self.settings)
        # Hide and cancel ownership before the normal edit path increments the
        # project revision. Unchanged settings are a no-op in apply_color_field.
        self.accept()
        self.window.apply_color_field('qualifier', settings)

    def _closed(self, *_):
        self._closed_flag = True
        self.canvas.cancel_window_drag()
        self.window_edit_button.setChecked(False); self.window_edit_button.setEnabled(False)
        self.set_editing_enabled(False)
        self.timer.stop(); self.preview_timer.stop()
        if self.job is not None:
            self.job.cancel.set()
        self.pick_button.setChecked(False); self.pick_button.setEnabled(False)
        self.canvas.sample_position = None
        self.snapshot = None; self.matte = None; self.preview_shape = None
        self.viewport = None; self.sample_source_position = None
        self.canvas._pan_position = None
        self.zoom_choice.setEnabled(False); self.center_x.setEnabled(False); self.center_y.setEnabled(False)
        self.canvas.image = QImage(); self.canvas._pixels = None; self.canvas._raster_cache = None; self.canvas.update()
        self.canvas.window_mapping = None; self.canvas.window_geometry = None; self.canvas.window_editing = False
        self.apply_button.setEnabled(False)


def open_qualifier(window):
    clip = window.selected_parameter_clip()
    if clip is None or window.active_selection_type != 'clip':
        return None
    existing = getattr(window, 'qualifier_dialog', None)
    if existing is not None and existing.isVisible():
        existing.raise_(); existing.activateWindow(); return existing
    window.player.pause()
    window.selective_curve_control.editor.cancel_gesture()
    dialog = QualifierDialog(window)
    window.qualifier_dialog = dialog
    project = copy.deepcopy(window.project); clip_id = clip.id; frame = window.timeline.playhead
    dialog.show()

    def operation(cancel, progress):
        return capture_picker_snapshot(project, clip_id, frame, cancel=cancel, stage='qualifier')

    dialog.job = window.start_job(tr('Reading qualifier frame…'), operation, dialog.accept_snapshot, 'qualifier')
    dialog.job.failed.connect(dialog.accept_error)
    return dialog

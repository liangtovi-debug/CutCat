"""Staged, clip-local window keys with manually decoded current-frame previews.

The main playhead and project stay untouched until one Apply transaction. Frame
navigation never launches a decode: explicit refresh keeps CPU work bounded.
Only one decode and one replaceable request are retained, including on reopen.
"""
from __future__ import annotations

import copy
from dataclasses import replace

import numpy as np
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QImage, QPainter, QPalette, QTextLayout, QTextOption
from PySide6.QtWidgets import QBoxLayout, QFormLayout, QHBoxLayout, QMessageBox, QSizePolicy, QStyle, QStyleOptionButton, QVBoxLayout

from .color_picker_source import capture_picker_snapshot, _signature, _source_identity
from .grade_transfer import is_picture_clip
from .i18n import tr, tr_error, locale_events
from .i18n_widgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QLabel,
                           QPushButton, QScrollArea, QSlider, QSpinBox, QWidget)
from .qualifier import WindowKeyframe, apply_qualifier, qualifier_mask, validate_qualifier
from .qualifier_overlay import selection_overlay
from .qualifier_viewport import overview
from .ui_qualifier import QualifierCanvas, _WINDOW_CONTROLS
from .ui_keyframe_timeline import KeyframeTimeline, TIMELINE_HELP
from .window_animation import evaluate_window

TIMING_TEXT = ('Keys use clip-local timeline frames, not source frames. Speed changes and grade paste do not scale key times. '
               'Keys outside the visible clip are retained; before the first and after the last key, geometry holds. '
               'Linear interpolates center and size; Hold keeps the outgoing key until the next key. No tracking.')
STAGE_TEXT = ('Selected clip only · input normalization, restoration and primary/RGB/hue curves, then the staged qualifier. '
              'Before LUT, effects, transforms and compositing. Manual RGB16 still decode; float32 processing, clipped 8-bit display. Not real-time playback.')
DRAFT_TEXT = ('Geometry is not yet a key. Add / replace key to preview and save it; changing frames discards this unadded draft.')
STALE_TEXT = 'Animation context is stale. Close and reopen for the current clip and playhead.'
GEOMETRY_FIELDS = ('window_center_x', 'window_center_y', 'window_width', 'window_height')
KEY_FIELDS = ('center_x', 'center_y', 'width', 'height')
MAX_VISIBLE_FRAMES = 2**31
DURATION_TEXT = 'The animation editor supports clips up to 2,147,483,648 timeline frames. Existing keys remain unchanged; shorten this clip to edit its animation.'


class _WrappingActionButton(QPushButton):
    """Keep native button chrome and the complete translated label at narrow widths."""
    def __init__(self, text):
        super().__init__(text)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        text = QTextLayout(self.text(), self.font())
        option = QTextOption(); option.setWrapMode(QTextOption.WrapMode.WordWrap)
        text.setTextOption(option); text.beginLayout(); width = 0
        while True:
            line = text.createLine()
            if not line.isValid():
                break
            line.setLineWidth(1); width = max(width, line.naturalTextWidth())
        text.endLayout()
        return QSize(int(np.ceil(width)) + 24, hint.height())

    def heightForWidth(self, width):
        bounds = self.fontMetrics().boundingRect(QRect(0, 0, max(1, width - 24), 10000),
            int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), self.text())
        return max(super().sizeHint().height(), bounds.height() + 16)

    def paintEvent(self, event):
        option = QStyleOptionButton(); self.initStyleOption(option)
        text = option.text; option.text = ''
        painter = QPainter(self)
        self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)
        rect = self.rect().adjusted(12, 8, -12, -8)
        if self.isDown():
            rect.translate(self.style().pixelMetric(QStyle.PixelMetric.PM_ButtonShiftHorizontal),
                           self.style().pixelMetric(QStyle.PixelMetric.PM_ButtonShiftVertical))
        self.style().drawItemText(painter, rect,
            int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextShowMnemonic),
            option.palette, self.isEnabled(), text, QPalette.ColorRole.ButtonText)


class WindowAnimationDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        clip = window.selected_parameter_clip()
        if not 1 <= clip.duration <= MAX_VISIBLE_FRAMES:
            raise ValueError(DURATION_TEXT)
        self.clip_id, self.clip_start, self.duration = clip.id, clip.start, clip.duration
        self.settings = copy.deepcopy(clip.grade.qualifier)
        self.grade_enabled = clip.grade.enabled
        self.context = (id(window.project), window.revision, window.selection, window.timeline.playhead,
                        window.selected_clip_ids, window.selection_generation)
        self._signature = _signature(window.project, clip.id)
        media = next(m for m in window.project.media if m.id == clip.media_id)
        try:
            self._source_identity = _source_identity(media.path)
        except (OSError, ValueError):
            self._source_identity = None
        self._source_path = media.path
        self._closed_flag = self._invalidated = False
        self._generation = 0
        self._pending = None
        self.job = self._waiting_job = self.snapshot = self.matte = self.viewport = None
        self._draft = {}
        self._draft_dirty = False
        self._selected_key = None
        self._time_selection = None
        self._selector_keys = None
        self.setWindowTitle(tr('Window animation · staged keys'))
        self.resize(740, 760); self.setMinimumSize(320, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8); layout.setSpacing(4)
        self.canvas = QualifierCanvas(self)
        self.canvas.setAccessibleName(tr('Window animation frame preview'))
        self.canvas.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.canvas.setMinimumHeight(60)
        layout.addWidget(self.canvas, 2)
        preview_row = QHBoxLayout()
        self.preview_choice = QComboBox()
        self.preview_choice.setAccessibleName(tr('Window animation preview'))
        self.preview_choice.setMinimumContentsLength(8)
        self.preview_choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.preview_choice.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        for label, mode in [('Range matte (even when correction disabled)', 'matte'),
                            ('Source before qualifier', 'source'), ('Adjusted before LUT', 'adjusted'),
                            ('Selection overlay · magenta 50%', 'overlay')]:
            self.preview_choice.addItem(tr(label), mode)
        self.refresh_button = self._button('Refresh frame', self.refresh_frame)
        preview_row.addWidget(self.preview_choice, 1); preview_row.addWidget(self.refresh_button)
        layout.addLayout(preview_row)
        self.resolution = self._label('')
        frame_row = QHBoxLayout()
        frame_label = QLabel(tr('Clip-local frame'))
        self.frame_spin = QSpinBox()
        self.frame_spin.setRange(0, self.duration - 1); self.frame_spin.setKeyboardTracking(False)
        self.frame_spin.setAccessibleName(tr('Clip-local frame'))
        self.frame_spin.setValue(max(0, min(self.duration - 1, window.timeline.playhead - clip.start)))
        frame_label.setBuddy(self.frame_spin)
        frame_row.addWidget(frame_label); frame_row.addWidget(self.frame_spin, 1)
        layout.addLayout(frame_row)
        self.frame_slider = QSlider(Qt.Orientation.Horizontal)
        self.frame_slider.setRange(0, self.duration - 1); self.frame_slider.setValue(self.frame_spin.value())
        self.frame_slider.setAccessibleName(tr('Clip-local frame slider'))
        layout.addWidget(self.frame_slider)
        self.key_timeline = KeyframeTimeline(self.duration, self)
        self.key_timeline.frameRequested.connect(self.frame_spin.setValue)
        self.key_timeline.keySelected.connect(self.select_key)
        self.key_timeline.moveRequested.connect(self.move_key)
        self.key_timeline.gestureChanged.connect(self._timing_gesture_changed)
        layout.addWidget(self.key_timeline)
        self.scroll = QScrollArea(self); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumSize(0, 0)
        content = QWidget(); content.setMinimumSize(0, 0)
        body = QVBoxLayout(content)
        self.preview_description = self._label(self.preview_choice.currentText())
        self.preview_description.setAccessibleName(tr('Window animation preview'))
        body.addWidget(self.preview_description)
        body.addWidget(self.resolution)
        self.key_status = self._label(''); body.addWidget(self.key_status)
        key_form = QFormLayout(); key_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        key_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.key_selector = QComboBox()
        self.key_selector.setAccessibleName(tr('Select visible key'))
        self.key_selector.setMinimumContentsLength(8)
        self.key_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.key_selector.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.key_selector.currentIndexChanged.connect(self._select_listed_key)
        self.key_select_label = self._label(tr('Select visible key')); self.key_select_label.setBuddy(self.key_selector)
        key_form.addRow(self.key_select_label, self.key_selector)
        self.key_time = QSpinBox(); self.key_time.setRange(0, self.duration - 1)
        self.key_time.setKeyboardTracking(False); self.key_time.setAccessibleName(tr('Selected key destination frame'))
        self.key_time_label = self._label(tr('Move key to frame')); self.key_time_label.setBuddy(self.key_time)
        key_form.addRow(self.key_time_label, self.key_time)
        body.addLayout(key_form)
        self.move_button = self._button('Move selected key', self._move_selected_key)
        body.addWidget(self.move_button)
        self.hidden_keys = self._label(''); body.addWidget(self.hidden_keys)
        self.timeline_help = self._label(tr(TIMELINE_HELP)); body.addWidget(self.timeline_help)
        nav = self.navigation_row = QHBoxLayout()
        self.previous_button = self._button('Previous key', lambda: self.jump_key(-1))
        self.next_button = self._button('Next key', lambda: self.jump_key(1))
        nav.addWidget(self.previous_button); nav.addWidget(self.next_button); body.addLayout(nav)
        self.enabled_check = QCheckBox()
        self.enabled_check.setChecked(self.settings.enabled)
        self.enabled_check.setAccessibleName(tr('Enable qualifier correction'))
        self.enabled_check.toggled.connect(lambda value: self.change_setting('enabled', value))
        enabled_row = QHBoxLayout()
        enabled_label = self.enabled_label = self._label(tr('Enable qualifier correction'))
        enabled_label.setBuddy(self.enabled_check)
        enabled_label.installEventFilter(self)
        self._check_label_pressed = False
        enabled_row.addWidget(self.enabled_check); enabled_row.addWidget(enabled_label, 1)
        body.addLayout(enabled_row)
        form = QFormLayout(); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.window_shape = QComboBox()
        self.window_shape.setAccessibleName(tr('Window shape'))
        for label, value in [('Off (color ranges only)', 'off'), ('Ellipse', 'ellipse'), ('Rectangle', 'rectangle')]:
            self.window_shape.addItem(tr(label), value)
        self.window_shape.setCurrentIndex(self.window_shape.findData(self.settings.window_shape))
        self.window_shape.setMinimumContentsLength(8)
        self.window_shape.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.window_shape.currentIndexChanged.connect(lambda: self.change_setting('window_shape', self.window_shape.currentData()))
        label = self._label(tr('Window shape')); label.setBuddy(self.window_shape)
        form.addRow(label, self.window_shape)
        self.controls, self.control_labels = {}, {self.window_shape: label,
            self.key_selector: self.key_select_label, self.key_time: self.key_time_label}
        for key, label, low, high, scale, step, suffix in _WINDOW_CONTROLS[:4]:
            spin = QDoubleSpinBox(); spin.setDecimals(3); spin.setRange(low, high)
            spin.setSingleStep(step); spin.setSuffix(suffix); spin.setKeyboardTracking(False)
            spin.setAccessibleName(tr(label)); spin.setObjectName('animation_' + key)
            spin.valueChanged.connect(lambda value, name=key: self.change_geometry(name, value / 100.))
            text = self._label(tr(label)); text.setBuddy(spin)
            form.addRow(text, spin); self.controls[key] = spin; self.control_labels[spin] = text
            spin.installEventFilter(self); spin.lineEdit().installEventFilter(self)
        self.interpolation = QComboBox(); self.interpolation.setAccessibleName(tr('Outgoing interpolation'))
        self.interpolation.addItem(tr('Linear'), 'linear'); self.interpolation.addItem(tr('Hold'), 'hold')
        self.interpolation.currentIndexChanged.connect(self.change_interpolation)
        label = self._label(tr('Outgoing interpolation')); label.setBuddy(self.interpolation)
        form.addRow(label, self.interpolation); self.control_labels[self.interpolation] = label
        body.addLayout(form)
        actions = self.actions_row = QHBoxLayout()
        self.add_button = self._button('Add / replace key', self.add_key)
        self.remove_button = self._button('Remove key', self.remove_key)
        actions.addWidget(self.add_button); actions.addWidget(self.remove_button); body.addLayout(actions)
        self.clear_button = self._button('Make current geometry static', self.make_static)
        self.clear_button.setToolTip(tr('Remove all keys, including hidden keys, and keep the evaluated geometry at this local frame. Only Apply changes the project.'))
        body.addWidget(self.clear_button)
        self.draft_note = self._label(tr(DRAFT_TEXT)); body.addWidget(self.draft_note)
        self.timing_note = self._label(tr(TIMING_TEXT)); body.addWidget(self.timing_note)
        self.stage = self._label(tr(STAGE_TEXT)); body.addWidget(self.stage)
        self.metadata = self._label(tr('Refresh explicitly to decode this local frame.'))
        body.addWidget(self.metadata); body.addStretch(1)
        self.scroll.setWidget(content); layout.addWidget(self.scroll, 3)
        self.status = self._label(tr('No project changes until Apply.'))
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.status.installEventFilter(self)
        body.insertWidget(0, self.status)
        buttons = QHBoxLayout()
        self.apply_button = self._button('Apply animation', self.apply_settings)
        self.close_button = self._button('Cancel', self.reject)
        buttons.addWidget(self.apply_button); buttons.addWidget(self.close_button); layout.addLayout(buttons)
        for control in (self.previous_button, self.next_button, self.enabled_check, self.window_shape,
                        self.interpolation, self.add_button, self.remove_button, self.clear_button,
                        self.key_selector, self.key_time, self.move_button):
            control.installEventFilter(self)
        self.key_time.lineEdit().installEventFilter(self)
        self.frame_spin.valueChanged.connect(self.change_frame)
        self.frame_slider.valueChanged.connect(self.frame_spin.setValue)
        self.preview_choice.currentIndexChanged.connect(self.render_preview)
        self.preview_timer = QTimer(self); self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(50); self.preview_timer.timeout.connect(self.render_preview)
        self.timer = QTimer(self); self.timer.setInterval(150)
        self.timer.timeout.connect(self.check_current); self.timer.start()
        self.finished.connect(self._closed)
        self._selected_key = self.local_frame if any(key.frame == self.local_frame for key in self.settings.window_keyframes) else None
        self.load_geometry(); self.update_buttons()
        from .ui_style import apply_ui_layout_tokens
        apply_ui_layout_tokens(self)
        self._rows_pending = False
        locale_events.changed.connect(self._schedule_rows)
        locale_events.changed.connect(self._refresh_preview_description)
        self.preview_choice.currentTextChanged.connect(self._refresh_preview_description)
        self.preview_choice.installEventFilter(self)
        self._refresh_preview_description()
        for button in self.findChildren(_WrappingActionButton):
            button.installEventFilter(self)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.widget().installEventFilter(self)
        self._schedule_rows()

    def _show_status(self, text):
        """Reveal an action result without taking keyboard focus from its owner."""
        self.status.setText(text)
        focus = self.focusWidget()
        message = self.status.text()
        def reveal():
            # A later user navigation supersedes an earlier queued reveal.
            if (self.isVisible() and self.focusWidget() is focus
                    and self.status.text() == message):
                self.reveal_control(self.status)
        QTimer.singleShot(0, self, reveal)

    def _refresh_preview_description(self, *_):
        text = self.preview_choice.currentText()
        self.preview_description.setText(text)
        self.preview_choice.setAccessibleDescription(text)
        for index in range(self.preview_choice.count()):
            self.preview_choice.setItemData(index, self.preview_choice.itemText(index), Qt.ItemDataRole.ToolTipRole)
        if self.preview_choice.hasFocus():
            QTimer.singleShot(0, self, self._reveal_preview_description)

    def _reveal_preview_description(self):
        if self.isVisible() and self.preview_choice.hasFocus():
            self.scroll.ensureWidgetVisible(self.preview_description, 0, 8)

    def _schedule_rows(self, *_):
        if not getattr(self, '_rows_pending', True):
            self._rows_pending = True
            QTimer.singleShot(0, self, self._fit_rows)

    def _fit_rows(self):
        self._rows_pending = False
        # Font/locale events queue both controllers. Resolve the dialog's field
        # metrics before measuring rows, regardless of callback delivery order.
        self._lumen_layout_controller.refresh()
        from .ui_style import LayoutTokens
        for button in self.findChildren(_WrappingActionButton):
            height = max(button.heightForWidth(button.width()),
                         LayoutTokens.from_font(button.font()).control_height)
            button._lumen_original_min_height = height
            if button.minimumHeight() != height:
                button.setMinimumHeight(height)
        margins = self.scroll.widget().layout().contentsMargins()
        available = self.scroll.viewport().width() - margins.left() - margins.right()
        required_height = max(button.heightForWidth(max(1, available))
            for button in (self.move_button, self.clear_button, self.add_button, self.remove_button))
        for form in self.scroll.widget().findChildren(QFormLayout):
            if form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapAllRows:
                for row in range(form.rowCount()):
                    item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                    label = item.widget() if item else None
                    if isinstance(label, QLabel):
                        if label.maximumWidth() != max(1, available):
                            label.setMaximumWidth(max(1, available))
        for control, label in self.control_labels.items():
            height = label.heightForWidth(min(max(1, available), label.maximumWidth()))
            required_height = max(required_height, max(label.minimumHeight(), height) + control.minimumHeight() + 16)
        required_height = max(required_height, self.resolution.heightForWidth(max(1, available)))
        if self.scroll.minimumHeight() != required_height + 18:
            self.scroll.setMinimumHeight(required_height + 18)
        timeline_height = self.key_timeline.minimumSizeHint().height()
        if self.key_timeline.minimumHeight() != timeline_height:
            self.key_timeline.setMinimumHeight(timeline_height)
        for row in (self.navigation_row, self.actions_row):
            buttons = [row.itemAt(i).widget() for i in range(row.count())]
            required = sum(button.sizeHint().width() for button in buttons) + max(0, row.spacing())
            row.setDirection(QBoxLayout.Direction.LeftToRight if required <= available
                             else QBoxLayout.Direction.TopToBottom)

    def _button(self, text, callback):
        button = _WrappingActionButton(tr(text)); button.setAutoDefault(False)
        button.clicked.connect(callback); return button

    @staticmethod
    def _label(text):
        label = QLabel(text); label.setWordWrap(True); return label

    @property
    def local_frame(self):
        return self.frame_spin.value()

    def context_current(self):
        window = self.window
        if (self._closed_flag or self._invalidated or window.closing
                or window.active_selection_type != 'clip'
                or window.selected_parameter_clip() is None
                or self.context != (id(window.project), window.revision, window.selection, window.timeline.playhead,
                                    window.selected_clip_ids, window.selection_generation)):
            return False
        try:
            return (_signature(window.project, self.clip_id) == self._signature
                    and (self._source_identity is None or _source_identity(self._source_path) == self._source_identity))
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    def current(self):
        return self.context_current() and (self.snapshot is None or self.snapshot.is_current(
            self.window.project, self.clip_id, self.clip_start + self.local_frame))

    def load_geometry(self):
        evaluated = evaluate_window(self.settings, self.local_frame)
        self._draft = {name: getattr(evaluated, name) for name in GEOMETRY_FIELDS}
        for name, spin in self.controls.items():
            spin.blockSignals(True); spin.setValue(self._draft[name] * 100); spin.blockSignals(False)
        earlier = [key for key in self.settings.window_keyframes if key.frame <= self.local_frame]
        mode = earlier[-1].interpolation if earlier else 'linear'
        self.interpolation.blockSignals(True)
        self.interpolation.setCurrentIndex(self.interpolation.findData(mode))
        self.interpolation.blockSignals(False)
        self._draft_dirty = False

    def change_geometry(self, name, value):
        if not self.context_current():
            self.invalidate(); return
        self._draft[name] = value; self._draft_dirty = True
        self.update_buttons()

    def change_interpolation(self, *_):
        if not self.context_current():
            self.invalidate(); return
        self._draft_dirty = True; self.update_buttons()

    def change_setting(self, name, value):
        if not self.context_current():
            self.invalidate(); return
        setattr(self.settings, name, value)
        self.preview_timer.start(); self.update_buttons()

    def change_frame(self, value):
        if not self.context_current():
            self.invalidate(); return
        self.key_timeline.cancel_gesture()
        self._selected_key = value if any(key.frame == value for key in self.settings.window_keyframes) else None
        self.frame_slider.blockSignals(True); self.frame_slider.setValue(value); self.frame_slider.blockSignals(False)
        self._generation += 1; self._pending = None
        if self.job is not None:
            self.job.cancel.set()
        self.clear_preview(); self.load_geometry(); self.update_buttons()

    def _select_listed_key(self, *_):
        frame = self.key_selector.currentData()
        if frame is not None:
            self.select_key(frame)
        else:
            self.key_timeline.cancel_gesture(); self._selected_key = None; self.update_buttons()

    def select_key(self, frame):
        if not self.context_current():
            self.invalidate(); return
        # Hidden frames are deliberately not clamped onto visible endpoints.
        if not 0 <= frame < self.duration or not any(key.frame == frame for key in self.settings.window_keyframes):
            return
        self.key_timeline.cancel_gesture()
        self._selected_key = frame
        if self.local_frame != frame:
            self.frame_spin.setValue(frame)
        else:
            self.update_buttons()

    def _move_selected_key(self):
        if self._selected_key is not None:
            self.move_key(self._selected_key, self.key_time.value())

    def _timing_gesture_changed(self, active):
        # No candidate time enters settings until release; Apply cannot race a
        # gesture. Do not rebuild controls here or keyboard focus would move.
        if hasattr(self, 'apply_button'):
            self.apply_button.setEnabled(not active and not self._closed_flag and not self._invalidated
                                         and self.snapshot is not None and not self._draft_dirty)

    def move_key(self, origin, destination):
        if not self.context_current():
            self.invalidate(); return False
        self.key_timeline.cancel_gesture()
        if (type(origin) is not int or type(destination) is not int
                or not 0 <= origin < self.duration or not 0 <= destination < self.duration):
            self._show_status(tr('Key moves must stay within clip-local frames 0–{last}. Hidden keys are read-only.', last=self.duration - 1))
            return False
        keys = self.settings.window_keyframes
        if not any(key.frame == origin for key in keys):
            return False
        if destination == origin:
            return True
        if any(key.frame == destination for key in keys):
            self._show_status(tr('Frame {frame} already has a key. Move rejected; neither key was changed.', frame=destination))
            return False
        candidate = copy.deepcopy(self.settings)
        candidate.window_keyframes = sorted([replace(key, frame=destination) if key.frame == origin else key
                                            for key in candidate.window_keyframes], key=lambda key: key.frame)
        try:
            validate_qualifier(candidate)
        except (ValueError, TypeError) as error:
            self._show_status(tr_error(error)); return False
        self.settings = candidate; self._selected_key = destination
        if self.local_frame != destination:
            self.frame_spin.setValue(destination)
        else:
            # Retime while already inspecting the destination still changes
            # evaluated geometry, not the decoded source frame.
            self.load_geometry(); self.render_preview(); self.update_buttons()
        return True

    def jump_key(self, direction):
        keys = [key.frame for key in self.settings.window_keyframes if 0 <= key.frame < self.duration
                and (key.frame < self.local_frame if direction < 0 else key.frame > self.local_frame)]
        if keys:
            self.frame_spin.setValue(max(keys) if direction < 0 else min(keys))

    def add_key(self):
        if not self.context_current():
            self.invalidate(); return
        key = WindowKeyframe(frame=self.local_frame, **{target: self._draft[source]
            for source, target in zip(GEOMETRY_FIELDS, KEY_FIELDS)}, interpolation=self.interpolation.currentData())
        candidate = copy.deepcopy(self.settings)
        candidate.window_keyframes = sorted([entry for entry in candidate.window_keyframes
            if entry.frame != self.local_frame] + [key], key=lambda entry: entry.frame)
        try:
            validate_qualifier(candidate)
        except (ValueError, TypeError) as error:
            self._show_status(tr_error(error)); return
        self.settings = candidate; self._selected_key = self.local_frame
        self.load_geometry(); self.render_preview(); self.update_buttons()

    def remove_key(self):
        if not self.context_current():
            self.invalidate(); return
        evaluated = evaluate_window(self.settings, self.local_frame)
        remaining = [entry for entry in self.settings.window_keyframes if entry.frame != self.local_frame]
        if not remaining and self.settings.window_keyframes:
            self.settings = evaluated
        else:
            self.settings.window_keyframes = remaining
        self.load_geometry(); self.render_preview(); self.update_buttons()

    def make_static(self):
        if not self.context_current():
            self.invalidate(); return
        self.settings = evaluate_window(self.settings, self.local_frame)
        self.load_geometry(); self.render_preview(); self.update_buttons()

    def clear_preview(self):
        self.snapshot = self.matte = self.viewport = None
        self.canvas.image = QImage(); self.canvas._pixels = self.canvas._raster_cache = None
        self.canvas.update()
        self.metadata.setText(tr('Refresh explicitly to decode this local frame.'))
        self.resolution.setText('')

    def update_buttons(self):
        active = not self._closed_flag and not self._invalidated
        keys = self.settings.window_keyframes
        visible = [key.frame for key in keys if 0 <= key.frame < self.duration]
        hidden = len(keys) - len(visible)
        at_key = self.local_frame in visible
        if self._selected_key not in visible:
            self._selected_key = None
        self.key_timeline.set_state(visible, self.local_frame, self._selected_key)
        self.key_timeline.setEnabled(active)
        self.key_selector.blockSignals(True)
        selector_keys = tuple((key.frame, key.interpolation) for key in keys if 0 <= key.frame < self.duration)
        if selector_keys != self._selector_keys:
            self.key_selector.clear(); self.key_selector.addItem(tr('No key selected'), None)
            for frame, mode in selector_keys:
                self.key_selector.addItem(tr('Frame {frame} · {mode}', frame=frame,
                    mode=tr('Linear' if mode == 'linear' else 'Hold')), frame)
            self._selector_keys = selector_keys
        self.key_selector.setCurrentIndex(self.key_selector.findData(self._selected_key))
        self.key_selector.blockSignals(False)
        self.key_selector.setEnabled(active and bool(visible))
        self.key_time.setEnabled(active and self._selected_key is not None)
        self.move_button.setEnabled(active and self._selected_key is not None)
        if self._selected_key is not None and self._selected_key != self._time_selection:
            self.key_time.setValue(self._selected_key)
        self._time_selection = self._selected_key
        hidden_frames = ', '.join(str(key.frame) for key in keys if not 0 <= key.frame < self.duration)
        self.hidden_keys.setText(tr('Outside clip, retained read-only: {frames}', frames=hidden_frames))
        self.hidden_keys.setVisible(bool(hidden))
        self.key_status.setText(tr('Local frame {frame} / {last} · {state} · {count} keys ({hidden} outside clip)',
            frame=self.local_frame, last=self.duration - 1, state=tr('Keyframe' if at_key else 'Interpolated / static'),
            count=len(keys), hidden=hidden))
        self.previous_button.setEnabled(active and any(frame < self.local_frame for frame in visible))
        self.next_button.setEnabled(active and any(frame > self.local_frame for frame in visible))
        self.remove_button.setEnabled(active and at_key)
        self.clear_button.setEnabled(active and bool(keys))
        for control in [self.add_button, self.refresh_button, self.enabled_check, self.enabled_label, self.window_shape,
                        self.interpolation, self.frame_spin, self.frame_slider, *self.controls.values()]:
            control.setEnabled(active)
        self.apply_button.setEnabled(active and self.snapshot is not None and not self._draft_dirty
                                     and not self.key_timeline.gesture_active)
        self.draft_note.setVisible(self._draft_dirty)
        if not active:
            return
        if self._draft_dirty:
            self.status.setText(tr(DRAFT_TEXT))
        elif self.snapshot is None:
            self.status.setText(tr('Refresh explicitly to decode this local frame.'))
        elif self.settings.window_shape == 'off':
            self.status.setText(tr('Window is off. Keys are retained but do not limit the color matte. Choose Ellipse or Rectangle to use them.'))
        elif not self.grade_enabled:
            self.status.setText(tr('Clip grading is disabled. Settings can be saved, but the adjusted view and render bypass the correction.'))
        elif not self.settings.enabled:
            self.status.setText(tr('Correction is disabled. The matte still shows the selected range. No project changes until Apply.'))
        else:
            self.status.setText(tr('No project changes until Apply.'))

    def refresh_frame(self, *_):
        if not self.context_current():
            self.invalidate(); return
        self._generation += 1
        generation = self._generation
        if self.job is not None:
            self.job.cancel.set()
        self.clear_preview()
        self._pending = (generation, self.local_frame, copy.deepcopy(self.window.project))
        self.update_buttons()
        previous = getattr(self.window, 'window_animation_job', None)
        if previous is not None and previous in self.window.jobs:
            previous.cancel.set()
            if previous is not self.job and previous is not self._waiting_job:
                self._waiting_job = previous
                previous.finished.connect(self._previous_finished)
        else:
            self._start_pending()

    def cancel_refresh(self):
        self._generation += 1; self._pending = None
        if self.job is not None:
            self.job.cancel.set()
        self.clear_preview(); self.update_buttons()
        if not self._closed_flag and not self._invalidated:
            self._show_status(tr('Frame refresh canceled. Pending keys remain here; refresh again before Apply.'))

    def _previous_finished(self):
        self._waiting_job = None
        QTimer.singleShot(0, self, self._start_pending)

    def _start_pending(self):
        if self._pending is None or not self.context_current():
            return
        generation, frame, project = self._pending; self._pending = None
        if generation != self._generation or frame != self.local_frame:
            return
        self._show_status(tr('Reading animation frame…'))
        def operation(cancel, progress):
            try:
                return capture_picker_snapshot(project, self.clip_id, self.clip_start + frame,
                                               cancel=cancel, stage='qualifier'), None
            except Exception as error:
                return None, str(error)
        def before_start(job):
            self.job = job
            job.finished.connect(lambda: self._job_finished(job))
        self.window.start_job(tr('Reading animation frame…'), operation,
            lambda result: self.accept_result(generation, frame, result), 'window_animation', before_start=before_start)

    def _job_finished(self, job):
        if self.job is job:
            self.job = None
        if self._pending is not None and not self._closed_flag:
            QTimer.singleShot(0, self, self._start_pending)

    def accept_result(self, generation, frame, result):
        if self._closed_flag or generation != self._generation or frame != self.local_frame:
            return
        if not self.context_current():
            self.invalidate(); return
        snapshot, error = result
        if error is not None:
            self.clear_preview(); self.update_buttons()
            self._show_status(tr('Frame unavailable · {error}', error=tr_error(error))); return
        self.accept_snapshot(snapshot)

    def accept_snapshot(self, snapshot):
        if self._closed_flag or not self.isVisible():
            return
        if not self.context_current():
            self.invalidate(); return
        if snapshot.stage != 'qualifier' or not snapshot.is_current(
                self.window.project, self.clip_id, self.clip_start + self.local_frame):
            self.clear_preview(); self.update_buttons()
            self._show_status(tr('Frame does not match this animation position. Refresh this frame.')); return
        self.snapshot = snapshot
        self.metadata.setText(tr('Timeline frame {timeline} · source frame {source_frame} at project rate · source time {seconds}s',
            timeline=self.clip_start + self.local_frame, source_frame=snapshot.source_frame,
            seconds=f'{float(snapshot.source_time):.6f}'))
        self.render_preview(); self.update_buttons()

    def render_preview(self, *_):
        self.preview_timer.stop()
        if self._closed_flag or not self.isVisible():
            return
        if not self.current():
            self.invalidate(); return
        if self.snapshot is None:
            return
        source = self.snapshot.frame
        height, width = source.shape[:2]
        self.viewport = overview(width, height)
        frame = self.viewport.extract(source)
        self.resolution.setText(tr('Sparse preview: {width}×{height}, step {step}. Small details may be missed; export evaluates every pixel.',
            width=frame.shape[1], height=frame.shape[0], step=self.viewport.stride))
        settings = evaluate_window(self.settings, self.local_frame)
        coordinates = dict(canvas_size=(width, height), origin=(0, 0), stride=self.viewport.stride)
        if settings.matte_morphology or settings.matte_smoothing:
            coordinates['matte_source'] = source
        self.matte = qualifier_mask(frame, replace(settings, enabled=True), **coordinates)
        mode = self.preview_choice.currentData()
        if mode == 'matte':
            preview = np.repeat(self.matte[..., None], 3, axis=-1)
        elif mode == 'overlay':
            preview = selection_overlay(frame, self.matte)
        elif mode == 'adjusted' and self.grade_enabled:
            preview = apply_qualifier(frame, settings, **coordinates)
        else:
            preview = frame
        self.canvas.set_frame(preview)
        self.update_buttons()

    def check_current(self):
        if not self.current():
            self.invalidate()

    def invalidate(self):
        if self._closed_flag or self._invalidated:
            return
        self._invalidated = True; self._generation += 1; self._pending = None
        self.key_timeline.cancel_gesture()
        if self.job is not None:
            self.job.cancel.set()
        self.clear_preview(); self.preview_timer.stop(); self.timer.stop()
        self.update_buttons(); self._show_status(tr(STALE_TEXT))

    def apply_settings(self):
        if not self.current():
            self.invalidate(); return
        if self.snapshot is None or self._draft_dirty or self.key_timeline.gesture_active:
            return
        validate_qualifier(self.settings)
        settings = copy.deepcopy(self.settings)
        self.accept()
        self.window.apply_color_field('qualifier', settings)

    def _closed(self, *_):
        self._closed_flag = True; self._generation += 1; self._pending = None
        self.key_timeline.cancel_gesture()
        self.timer.stop(); self.preview_timer.stop()
        if self.job is not None:
            self.job.cancel.set()
        self.clear_preview(); self.update_buttons()

    def eventFilter(self, watched, event):
        if watched is getattr(self, 'enabled_label', None):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._check_label_pressed = self.enabled_check.isEnabled()
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                pressed = self._check_label_pressed; self._check_label_pressed = False
                if pressed and self.enabled_check.isEnabled() and watched.rect().contains(event.position().toPoint()):
                    self.enabled_check.setFocus(Qt.FocusReason.MouseFocusReason)
                    self.enabled_check.click()
                return True
        if event.type() in (QEvent.Type.Resize, QEvent.Type.FontChange, QEvent.Type.StyleChange, QEvent.Type.LayoutRequest):
            self._schedule_rows()
        if watched is getattr(self, 'preview_choice', None) and event.type() == QEvent.Type.FocusIn:
            QTimer.singleShot(0, self, self._reveal_preview_description)
        if event.type() == QEvent.Type.FocusIn and hasattr(self, 'scroll'):
            target = watched.parent() if watched in [spin.lineEdit() for spin in [*self.controls.values(), self.key_time]] else watched
            if target is not self.scroll and self.scroll.widget().isAncestorOf(target):
                self.reveal_control(target)
                QTimer.singleShot(0, self, lambda control=target:
                    self.reveal_control(control) if self.isVisible() else None)
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.key_timeline.cancel_gesture():
            event.accept(); return
        super().keyPressEvent(event)

    def event(self, event):
        if event.type() in (QEvent.Type.WindowDeactivate, QEvent.Type.Hide) and hasattr(self, 'key_timeline'):
            self.key_timeline.cancel_gesture()
        return super().event(event)

    def reveal_control(self, target):
        body = self.scroll.widget(); height = self.scroll.viewport().height()
        top = target.mapTo(body, QPoint()).y(); bottom = top + target.height()
        label = self.control_labels.get(target)
        if label is not None:
            label_top = label.mapTo(body, QPoint()).y()
            if max(bottom, label_top + label.height()) - min(top, label_top) + 16 <= height:
                top, bottom = min(top, label_top), max(bottom, label_top + label.height())
        bar = self.scroll.verticalScrollBar()
        if bottom - top > height:
            bar.setValue(max(0, top - 8))
            return
        padding = min(8, max(0, (height - (bottom - top)) // 2))
        if top < bar.value() + padding:
            bar.setValue(max(0, top - padding))
        elif bottom > bar.value() + height - padding:
            bar.setValue(max(0, bottom - height + padding))


def open_window_animation(window):
    clip = window.selected_parameter_clip()
    if (clip is None or window.active_selection_type != 'clip'
            or not is_picture_clip(window.project, clip.id)):
        return None
    existing = getattr(window, 'window_animation_dialog', None)
    if existing is not None and existing.isVisible():
        existing.raise_(); existing.activateWindow(); return existing
    if not 1 <= clip.duration <= MAX_VISIBLE_FRAMES:
        QMessageBox.warning(window, tr('Window animation…'), tr(DURATION_TEXT))
        return None
    window.player.pause(); window.selective_curve_control.editor.cancel_gesture()
    dialog = WindowAnimationDialog(window); window.window_animation_dialog = dialog
    dialog.show(); dialog.refresh_frame()
    return dialog

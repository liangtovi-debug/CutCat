"""Single-clip staged picture animation; no decoder or preview ownership."""
from copy import deepcopy
from dataclasses import replace

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QVBoxLayout, QBoxLayout, QMessageBox
from .i18n import tr, tr_error
from .i18n_widgets import (QDialog, QLabel, QPushButton, QSpinBox, QDoubleSpinBox,
                           QComboBox, QSlider, QScrollArea, QWidget)
from .ui_keyframe_timeline import KeyframeTimeline
from .clip_motion import ClipMotionKeyframe, evaluate_clip_motion, validate_motion_keyframes

FIELDS = ('x', 'y', 'scale', 'opacity')
STALE = 'Picture animation context changed. Close and reopen this editor.'
TIMING = ('Picture keys use clip-local project frames. Position, scale and opacity are absolute. '
          'Speed and slip do not retime keys. Hidden keys are retained. Hold or Linear only.')
PREVIEW = ('No live composite preview here. Apply, then Build Preview to see the moving picture. '
           'Rotation, mirrors and transparent outside remain static; audio is unchanged.')


class ClipMotionDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        clip = window._picture_transform_clip()
        if clip is None:
            raise ValueError('Select one video clip to edit picture animation.')
        if not 1 <= clip.duration <= 2**31:
            raise ValueError('Picture animation editor requires 1 to 2,147,483,648 frames.')
        self.window, self.clip_id, self.duration = window, clip.id, clip.duration
        self.keys = deepcopy(clip.motion_keyframes)
        self.transform, self.composite = deepcopy(clip.transform), deepcopy(clip.composite)
        self.context = self._context()
        self._closed_flag = self._invalidated = self._loading = False
        self._dirty = False
        self.selected_frame = None
        self.setWindowTitle(tr('Picture animation · staged keys'))
        self.resize(580, 740); self.setMinimumSize(320, 480)
        layout = QVBoxLayout(self)
        row = QHBoxLayout(); label = QLabel(tr('Clip-local frame')); row.addWidget(label)
        self.frame_spin = QSpinBox(); self.frame_spin.setRange(0, self.duration - 1)
        self.frame_spin.setKeyboardTracking(False); self.frame_spin.setAccessibleName(tr('Clip-local frame'))
        self.frame_spin.setValue(max(0, min(self.duration - 1, window.timeline.playhead - clip.start)))
        label.setBuddy(self.frame_spin); row.addWidget(self.frame_spin); layout.addLayout(row)
        self.frame_slider = QSlider(Qt.Orientation.Horizontal); self.frame_slider.setRange(0, self.duration - 1)
        self.frame_slider.setAccessibleName(tr('Clip-local frame slider')); layout.addWidget(self.frame_slider)
        self.key_timeline = KeyframeTimeline(self.duration, self)
        self.key_timeline.setAccessibleName(tr('Picture keyframe timeline')); layout.addWidget(self.key_timeline)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); content.setMinimumSize(0, 0); body = QVBoxLayout(content)
        self._responsive_rows = []
        form = QFormLayout(); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.key_selector = QComboBox(); self.key_selector.setAccessibleName(tr('Select visible key'))
        form.addRow(tr('Select visible key'), self.key_selector)
        self.key_time = QSpinBox(); self.key_time.setRange(0, self.duration - 1)
        self.key_time.setKeyboardTracking(False); self.key_time.setAccessibleName(tr('Move key to frame'))
        form.addRow(tr('Move key to frame'), self.key_time)
        body.addLayout(form)
        self.move_button = self.button('Move selected key', self.move_selected); body.addWidget(self.move_button)
        self.hidden_keys = self.label(''); body.addWidget(self.hidden_keys)
        nav = QHBoxLayout()
        self.previous_button = self.button('Previous key', lambda: self.jump(-1))
        self.next_button = self.button('Next key', lambda: self.jump(1))
        nav.addWidget(self.previous_button); nav.addWidget(self.next_button); body.addLayout(nav)
        self._responsive_rows.append(nav)
        values = QFormLayout(); values.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        values.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.controls = {}
        for name, label, low, high, step in [('x','Position X · px',-16384,16384,1),
                ('y','Position Y · px',-16384,16384,1),('scale','Scale',.1,4,.05),
                ('opacity','Picture opacity',0,1,.05)]:
            spin = QDoubleSpinBox(); spin.setMinimumWidth(0); spin.setRange(low,high); spin.setDecimals(6)
            spin.setSingleStep(step); spin.setKeyboardTracking(False); spin.setAccessibleName(tr(label))
            spin.valueChanged.connect(lambda value, name=name: self.change_value(name,value))
            self.controls[name] = spin; values.addRow(tr(label),spin)
        self.interpolation = QComboBox(); self.interpolation.addItem(tr('Linear'),'linear'); self.interpolation.addItem(tr('Hold'),'hold')
        self.interpolation.setAccessibleName(tr('Outgoing interpolation'))
        values.addRow(tr('Outgoing interpolation'), self.interpolation); body.addLayout(values)
        self.interpolation.currentIndexChanged.connect(self.change_interpolation)
        row = QHBoxLayout()
        self.add_button = self.button('Add / replace key', self.add_key)
        self.remove_button = self.button('Remove key', self.remove_key)
        row.addWidget(self.add_button); row.addWidget(self.remove_button); body.addLayout(row)
        self._responsive_rows.append(row)
        self.static_button = self.button('Make current picture static', self.make_static); body.addWidget(self.static_button)
        body.addWidget(self.label(tr('Unadded values are discarded when changing frames. Add / replace key before Apply.')))
        body.addWidget(self.label(tr(TIMING))); body.addWidget(self.label(tr(PREVIEW))); body.addStretch()
        self.scroll.setWidget(content); layout.addWidget(self.scroll)
        self.status = self.label(tr('No project changes until Apply.')); layout.addWidget(self.status)
        row = QHBoxLayout(); self.apply_button = self.button('Apply animation',self.apply_settings)
        self.cancel_button = self.button('Cancel',self.reject)
        row.addWidget(self.apply_button); row.addWidget(self.cancel_button); layout.addLayout(row)
        self._responsive_rows.append(row)
        self.frame_spin.valueChanged.connect(self.change_frame)
        self.frame_slider.valueChanged.connect(self.frame_spin.setValue)
        self.key_timeline.frameRequested.connect(self.frame_spin.setValue)
        self.key_timeline.keySelected.connect(self.select_key)
        self.key_timeline.moveRequested.connect(self.move_key)
        self.key_timeline.gestureChanged.connect(self.update_buttons)
        self.key_selector.currentIndexChanged.connect(self.select_listed)
        self.timer = QTimer(self); self.timer.setInterval(150); self.timer.timeout.connect(self.check_current); self.timer.start()
        self.finished.connect(self.closed)
        self.load_values()

    def resizeEvent(self, event):
        direction = QBoxLayout.Direction.TopToBottom if self.width() < 440 else QBoxLayout.Direction.LeftToRight
        for row in getattr(self, '_responsive_rows', []):
            row.setDirection(direction)
        super().resizeEvent(event)

    @staticmethod
    def label(text):
        result=QLabel(text); result.setWordWrap(True); return result

    @staticmethod
    def button(text,callback):
        result=QPushButton(tr(text)); result.setAutoDefault(False); result.clicked.connect(callback); return result

    @property
    def local_frame(self): return self.frame_spin.value()

    def _context(self):
        w=self.window
        return (id(w.project),id(w.editor),w.editor.session_id,w.editor.revision,
                w.selection,w.selection_generation,tuple(w.selected_clip_ids),w.timeline.playhead)

    def current(self):
        return (not self._closed_flag and not self._invalidated and not self.window.closing
                and self.window.active_selection_type == 'clip' and self._context() == self.context
                and self.window._picture_transform_clip() is not None
                and self.window._picture_transform_clip().id == self.clip_id)

    def check_current(self):
        if not self.current():
            self._invalidated=True; self.key_timeline.cancel_gesture()
            self.scroll.setEnabled(False); self.key_timeline.setEnabled(False)
            self.frame_spin.setEnabled(False); self.frame_slider.setEnabled(False)
            self.apply_button.setEnabled(False); self.status.setText(tr(STALE))
            return False
        return True

    def evaluated(self):
        return (evaluate_clip_motion(self.keys,self.local_frame) if self.keys else
                (self.transform.x,self.transform.y,self.transform.scale,self.composite.opacity))

    def load_values(self):
        self._loading=True
        try:
            self.draft=dict(zip(FIELDS,self.evaluated()))
            for name,spin in self.controls.items(): spin.setValue(self.draft[name])
            key=next((k for k in self.keys if k.frame == self.local_frame),None)
            self.selected_frame=key.frame if key else None
            self.interpolation.setCurrentIndex(self.interpolation.findData(key.interpolation if key else 'linear'))
            self.frame_slider.setValue(self.local_frame)
            self._dirty=False
        finally: self._loading=False
        self.update_buttons()

    def change_value(self,name,value):
        if self._loading: return
        if not self.check_current(): return
        self.draft[name]=value; self._dirty=True; self.update_buttons()

    def change_interpolation(self,*_):
        if not self._loading and self.check_current():
            self._dirty=True; self.update_buttons()

    def change_frame(self,*_):
        if not self._loading and self.check_current(): self.load_values()

    def update_buttons(self,*_):
        if not hasattr(self,'apply_button'): return
        visible=[k.frame for k in self.keys if 0<=k.frame<self.duration]
        self.key_timeline.set_state(visible,self.local_frame,self.selected_frame)
        self.key_selector.blockSignals(True); self.key_selector.clear()
        self.key_selector.addItem(tr('No key selected'),None)
        for frame in visible: self.key_selector.addItem(str(frame),frame)
        self.key_selector.setCurrentIndex(max(0,self.key_selector.findData(self.selected_frame)))
        self.key_selector.blockSignals(False)
        if self.selected_frame is not None: self.key_time.setValue(self.selected_frame)
        hidden=[str(k.frame) for k in self.keys if k.frame<0 or k.frame>=self.duration]
        self.hidden_keys.setText(tr('Hidden keys retained (read-only): {frames}',frames=', '.join(hidden) if hidden else tr('None')))
        valid=self.current(); gesture=self.key_timeline.gesture_active
        self.move_button.setEnabled(valid and self.selected_frame is not None and not gesture)
        self.remove_button.setEnabled(valid and self.selected_frame is not None and not gesture)
        self.add_button.setEnabled(valid and not gesture)
        self.static_button.setEnabled(valid and bool(self.keys) and not gesture)
        self.apply_button.setEnabled(valid and not self._dirty and not gesture)
        self.previous_button.setEnabled(valid and any(f<self.local_frame for f in visible))
        self.next_button.setEnabled(valid and any(f>self.local_frame for f in visible))

    def select_key(self,frame):
        if not self.check_current(): return
        if not any(k.frame==frame and 0<=frame<self.duration for k in self.keys): return
        self.frame_spin.setValue(frame); self.load_values()

    def select_listed(self,*_):
        frame=self.key_selector.currentData()
        if frame is not None: self.select_key(frame)

    def jump(self,direction):
        if not self.check_current(): return
        choices=[k.frame for k in self.keys if 0<=k.frame<self.duration and (k.frame-self.local_frame)*direction>0]
        if choices: self.select_key(min(choices) if direction>0 else max(choices))

    def add_key(self):
        if not self.check_current() or self.key_timeline.gesture_active: return
        try:
            key=ClipMotionKeyframe(frame=self.local_frame,**self.draft,interpolation=self.interpolation.currentData())
            keys=sorted([k for k in self.keys if k.frame!=key.frame]+[key],key=lambda k:k.frame)
            validate_motion_keyframes(keys)
        except ValueError as exc:
            self.status.setText(tr_error(str(exc))); return
        self.keys=keys; self.status.setText(tr('No project changes until Apply.')); self.load_values()

    def remove_key(self):
        if not self.check_current() or self.key_timeline.gesture_active: return
        if self.selected_frame is None: return
        self.keys=[k for k in self.keys if k.frame!=self.selected_frame]; self.load_values()

    def move_selected(self):
        if self.selected_frame is not None: self.move_key(self.selected_frame,self.key_time.value())

    def move_key(self,origin,destination):
        if not self.check_current(): return
        if type(origin) is not int or type(destination) is not int or not 0<=destination<self.duration: return
        if not any(k.frame==origin and 0<=origin<self.duration for k in self.keys): return
        if origin==destination: return
        if any(k.frame==destination for k in self.keys):
            self.status.setText(tr('A key already exists at that frame.')); return
        keys=sorted([replace(k,frame=destination) if k.frame==origin else k for k in self.keys],key=lambda k:k.frame)
        validate_motion_keyframes(keys); self.keys=keys
        self.frame_spin.setValue(destination); self.load_values()

    def make_static(self):
        if not self.check_current() or not self.keys or self.key_timeline.gesture_active: return
        answer=QMessageBox.question(self,tr('Make current picture static'),
            tr('Keep the evaluated position, scale and opacity at this frame and remove ALL keys, including hidden keys? Only Apply changes the project.'),
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        if answer!=QMessageBox.StandardButton.Yes or not self.check_current(): return
        x,y,scale,opacity=self.evaluated()
        self.transform=replace(self.transform,x=x,y=y,scale=scale)
        self.composite=replace(self.composite,opacity=opacity)
        self.keys=[]; self.load_values()

    def apply_settings(self):
        if not self.check_current() or self._dirty or self.key_timeline.gesture_active: return
        clip=self.window._picture_transform_clip()
        if (clip.motion_keyframes,self.transform,self.composite)!=(self.keys,clip.transform,clip.composite):
            self.window.edit(lambda: self.window.editor.update_clip(self.clip_id,
                motion_keyframes=deepcopy(self.keys),transform=deepcopy(self.transform),composite=deepcopy(self.composite)))
        self.accept()

    def closed(self,*_):
        self._closed_flag=True; self.timer.stop(); self.key_timeline.cancel_gesture()

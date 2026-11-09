"""Explicit project-wide output gain, staged without source access or analysis.

The draft is independent of clip selection. Only Apply can change the project;
all validity checks read in-memory session/revision/scalar metadata.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QSizePolicy

from .i18n import tr
from .i18n_widgets import (QDialog, QLabel, QPushButton, QDoubleSpinBox,
                           QFormLayout, QScrollArea, QWidget)


class MasterGainDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._closed_flag = False
        self._stale = False
        self.context = self._context()
        # Preserve exact loaded precision when the displayed draft is untouched.
        self._draft_value = window.project.master_gain_db
        self.setWindowTitle(tr('Master output gain'))
        self.setModal(False)
        self.resize(560, 440)
        self.setMinimumSize(320, 300)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        body = QVBoxLayout(content)
        self.scope = self._label(tr('Project-wide output gain · after the stereo mix, before audio encoding. Clip gain, mute and fades are unchanged.'))
        body.addWidget(self.scope)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.gain = QDoubleSpinBox()
        self.gain.setRange(-96, 24)
        self.gain.setDecimals(2)
        self.gain.setSingleStep(.5)
        self.gain.setSuffix(' dB')
        self.gain.setKeyboardTracking(False)
        self.gain.setAccessibleName(tr('Master output gain · dB'))
        self.gain.setToolTip(tr('Range: −96 to +24 dB. 0 dB leaves the mix unchanged. −96 dB attenuates; it is not mute.'))
        self.gain.setValue(self._draft_value)
        form.addRow(tr('Master output gain · dB'), self.gain)
        body.addLayout(form)
        self.reset_button = QPushButton(tr('Reset draft to 0 dB'))
        self.reset_button.setAutoDefault(False)
        self.reset_button.clicked.connect(self.reset_draft)
        body.addWidget(self.reset_button)
        self.range_note = self._label(tr('Range: −96 to +24 dB. 0 dB leaves the mix unchanged. −96 dB attenuates; it is not mute.'))
        body.addWidget(self.range_note)
        self.warning = self._label(tr('Affects the entire mix, not the selected clip. Gain does not prevent clipping or repair already clipped audio. No automatic normalization, limiter or compressor.'))
        body.addWidget(self.warning)
        self.workflow = self._label(tr('Apply gain → Build preview and listen → Analyze timeline audio again. Existing analysis results become stale; preview and analysis never run automatically.'))
        body.addWidget(self.workflow)
        self.transaction = self._label(tr('No project changes until Apply. Cancel discards this draft. One Undo restores the applied gain.'))
        body.addWidget(self.transaction)
        self.status = self._label('')
        body.addWidget(self.status)
        body.addStretch()
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, 1)
        row = QHBoxLayout()
        self.apply_button = QPushButton(tr('Apply'))
        self.cancel_button = QPushButton(tr('Cancel'))
        for button in (self.apply_button, self.cancel_button):
            button.setAutoDefault(False)
            row.addWidget(button)
        layout.addLayout(row)
        self.apply_button.clicked.connect(self.apply_settings)
        self.cancel_button.clicked.connect(self.reject)
        self.gain.valueChanged.connect(self.stage_gain)
        self.finished.connect(self._closed)
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.check_current)
        self.timer.start()
        self.check_current()

    @staticmethod
    def _label(text):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return label

    def _context(self):
        w = self.window
        return (id(w.editor), w.editor.session_id, id(w.project),
                w.editor.revision, w.revision, w.project.master_gain_db)

    def check_current(self):
        if self._closed_flag:
            return
        if self.window.closing:
            self.reject()
            return
        # Latch invalidation: returning to a former state must never revive a
        # draft captured before a project/session/edit boundary.
        self._stale = self._stale or self._context() != self.context
        busy = bool(self.window.jobs)
        self.apply_button.setEnabled(not self._stale and not busy)
        self.gain.setEnabled(not self._stale)
        self.reset_button.setEnabled(not self._stale)
        if self._stale:
            self.status.setText(tr('Project changed. Close and reopen Master output gain before applying.'))
        elif busy:
            self.status.setText(tr('Wait for the current task to finish or cancel it before applying master gain.'))
        else:
            self.status.setText(tr('Draft only · the project gain is unchanged until Apply.'))

    def stage_gain(self, value):
        self._draft_value = value
        self.check_current()

    def reset_draft(self):
        # The exact loaded gain may round to zero in the spin box. Explicit
        # reset still means exactly 0 dB, even if Qt emits no valueChanged.
        self.gain.setValue(0)
        self.stage_gain(0.0)

    def apply_settings(self):
        if self._closed_flag:
            return
        # Commit pending keyboard input to the local draft, never the project.
        self.gain.interpretText()
        self.check_current()
        if not self.apply_button.isEnabled():
            return
        value = self._draft_value
        w = self.window
        before = (w.editor, w.editor.revision)
        w.edit(lambda: w.editor.set_master_gain(value))
        if before != (w.editor, w.editor.revision) or w.project.master_gain_db == value:
            self.accept()

    def _closed(self, *_):
        self._closed_flag = True
        self.timer.stop()


def open_master_gain(window):
    if window.closing:
        return None
    dialog = getattr(window, 'master_gain_dialog', None)
    if dialog is not None and not dialog._closed_flag:
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        dialog.check_current()
        return dialog
    if dialog is not None:
        dialog.deleteLater()
    dialog = MasterGainDialog(window)
    window.master_gain_dialog = dialog
    dialog.show()
    return dialog


def close_master_gain(window):
    dialog = getattr(window, 'master_gain_dialog', None)
    if dialog is not None:
        dialog.reject()

"""Measured, detached master-gain draft. No project write before explicit Apply.

Workers own source I/O. Request and monotonic edit generations guard every
callback, including failures and pre-apply verification. Closing never joins.
"""
from __future__ import annotations

import copy
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QVBoxLayout, QSizePolicy
from .audio_analysis import audio_settings_fingerprint
from .loudness_proposal import verify_loudness_proposal, validate_verified_proposal
from .i18n import tr, tr_error, locale_events
from .i18n_widgets import (QDialog, QLabel, QPushButton, QDoubleSpinBox,
                           QFormLayout, QScrollArea, QWidget)


class LoudnessProposalDialog(QDialog):
    def __init__(self, window, report):
        super().__init__(window)
        self.window = window
        self.report = report
        self.verified = None
        self._error = None
        locale_events.changed.connect(self._language_changed)
        self.job = None
        self._worker_done = False
        self.state = 'idle'
        self._generation = 0
        self._closed_flag = False
        self.context = self._context()
        self.setWindowTitle(tr('Measured master-gain proposal'))
        self.setModal(False)
        self.resize(600, 600)
        self.setMinimumSize(320, 360)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        body = QVBoxLayout(content)
        body.addWidget(self._label(tr('Measure a detached draft of the final stereo mix. Only Apply changes master output gain; clip gain, mute and fades stay unchanged.')))
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.target = QDoubleSpinBox()
        self.ceiling = QDoubleSpinBox()
        for control, value, suffix, name in (
                (self.target, -18, ' LUFS', tr('Target loudness')),
                (self.ceiling, -2, ' dBTP', tr('Estimated true-peak ceiling'))):
            control.setRange(-96, 24)
            control.setDecimals(1)
            control.setSingleStep(.1)
            control.setSuffix(suffix)
            control.setValue(value)
            control.setAccessibleName(name)
            form.addRow(name, control)
        body.addLayout(form)
        body.addWidget(self._label(tr('−18 LUFS and −2 dBTP are editable starting points, not delivery standards. Gain alone may not reach both targets.')))
        self.measure_button = QPushButton(tr('Measure gain proposal'))
        self.measure_button.setAutoDefault(False)
        body.addWidget(self.measure_button)
        self.status = self._label(tr('Draft only · the project gain is unchanged until Apply.'))
        body.addWidget(self.status)
        self.measurements = self._label('')
        self.measurements.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.measurements)
        body.addWidget(self._label(tr('This is gain only, not normalization, a limiter or compression. Estimated true peak is pre-encoding; codecs can change peaks. Build preview and listen after Apply, then analyze again.')))
        body.addWidget(self._label(tr('Estimated true peak uses finite four-times FIR reconstruction with a sample-peak lower-bound safeguard. This is not an encoded-file check or compliance certification.')))
        body.addStretch()
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, 1)
        self.apply_button = QPushButton(tr('Apply measured gain'))
        self.cancel_button = QPushButton(tr('Cancel'))
        for button in (self.apply_button, self.cancel_button):
            button.setAutoDefault(False)
            layout.addWidget(button)
        self.measure_button.clicked.connect(self.measure)
        self.apply_button.clicked.connect(self.apply_settings)
        self.cancel_button.clicked.connect(self.reject)
        self.target.valueChanged.connect(self.targets_changed)
        self.ceiling.valueChanged.connect(self.targets_changed)
        self.target.lineEdit().textEdited.connect(self.targets_changed)
        self.ceiling.lineEdit().textEdited.connect(self.targets_changed)
        self.finished.connect(self._closed)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.check_current)
        self.timer.start()
        self._update_buttons()

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
                w.editor.revision, w.revision, w.project.master_gain_db,
                audio_settings_fingerprint(w.project))

    def _current(self):
        try:
            return (not self._closed_flag and not self.window.closing
                    and self.state != 'stale' and self._context() == self.context)
        except (ValueError, TypeError, AttributeError):
            return False

    def _update_buttons(self):
        current = self._current()
        idle = not self.window.jobs
        self.measure_button.setEnabled(current and idle)
        self.apply_button.setEnabled(current and idle and self.verified is not None and self.state == 'ready')
        self.target.setEnabled(current)
        self.ceiling.setEnabled(current)

    def _language_changed(self, *_):
        if self.state == 'failed' and self._error is not None:
            self.status.setText(tr('Gain proposal unavailable · {error}', error=tr_error(self._error)))

    def _discard(self):
        self._generation += 1
        if self.job is not None and not self._worker_done:
            self.job.cancel.set()
        self.verified = None
        self._error = None
        self.measurements.setText('')

    def targets_changed(self, *_):
        self._discard()
        if self.state != 'stale':
            self.state = 'idle'
            self.status.setText(tr('Targets changed. Measure a new draft before applying.'))
        self._update_buttons()

    def check_current(self):
        if self._closed_flag:
            return
        if self.window.closing:
            self.reject()
            return
        if not self._current() and self.state != 'stale':
            self._discard()
            self.state = 'stale'
            self.status.setText(tr('Project changed. Close this proposal and analyze again.'))
        self._update_buttons()

    def _start(self, operation, done, state):
        self._generation += 1
        generation = self._generation
        self.state = state
        self._worker_done = False
        def success(value):
            if generation == self._generation:
                self._worker_done = True
            if generation != self._generation or not self._current():
                self.check_current()
                return
            result, error = value
            if error is not None:
                self.verified = None
                self.state = 'failed'
                self._error = error
                self.status.setText(tr('Gain proposal unavailable · {error}', error=tr_error(error)))
            else:
                done(result)
            self._update_buttons()
        def run(cancel, progress):
            try:
                return operation(cancel, progress), None
            except Exception as error:
                return None, str(error)
        def before_start(job):
            self.job = job
            job.finished.connect(lambda: self._finished(job, generation))
        self.window.start_job(tr('Checking measured gain proposal…'), run, success,
                              'audio_analysis', before_start=before_start)
        self._update_buttons()

    def _finished(self, job, generation):
        if self.job is job:
            self.job = None
        if not self._closed_flag and generation == self._generation and self.state in ('measuring', 'applying'):
            self._discard()
            self.state = 'canceled'
            self.status.setText(tr('Proposal canceled. The project and gain are unchanged.'))
        if not self._closed_flag:
            self._update_buttons()

    def measure(self):
        self.target.interpretText()
        self.ceiling.interpretText()
        self.check_current()
        if not self.measure_button.isEnabled():
            return
        self._discard()
        project = copy.deepcopy(self.window.project)
        report = self.report
        target, ceiling = self.target.value(), self.ceiling.value()
        self.status.setText(tr('Measuring a detached draft… The project is unchanged.'))
        self._start(lambda cancel, progress: verify_loudness_proposal(
            project, report, target, ceiling, cancel=cancel), self._measured, 'measuring')

    def _measured(self, verified):
        self.verified = verified
        self.state = 'ready'
        proposal, result = verified.proposal, verified.draft_report
        peak = max(p for p in result.estimated_true_peak_dbtp if p is not None)
        def db(value):
            return tr('Silence (−∞)') if value is None else f'{value:.2f}'
        self.measurements.setText(tr('Measured draft: {lufs:.1f} LUFS · target: {target:.1f} LUFS\nTarget difference: {miss:+.1f} LU\nSample peaks L / R: {sample_left} / {sample_right} dBFS\nEstimated true peaks L / R: {peak_left} / {peak_right} dBTP\nPeak ceiling: {ceiling:.1f} dBTP\nMaster gain: {gain:.1f} dB · change: {delta:+.1f} dB',
            lufs=result.integrated_lufs, target=proposal.target_lufs,
            miss=result.integrated_lufs - proposal.target_lufs,
            sample_left=db(result.sample_peak_dbfs[0]), sample_right=db(result.sample_peak_dbfs[1]),
            peak_left=db(result.estimated_true_peak_dbtp[0]), peak_right=db(result.estimated_true_peak_dbtp[1]),
            ceiling=proposal.ceiling_dbtp, gain=proposal.proposed_master_gain_db, delta=proposal.delta_db))
        miss = abs(result.integrated_lufs - proposal.target_lufs) > .1
        if peak > proposal.ceiling_dbtp:
            self.verified = None
            self.state = 'failed'
            self.status.setText(tr('Measured draft exceeds the peak ceiling. Nothing was applied.'))
        elif proposal.peak_limited or miss:
            self.status.setText(tr('Peak-constrained or target not reached. Review the measured values before Apply.'))
        else:
            self.status.setText(tr('Measured draft ready. Apply rechecks sources before changing master gain.'))

    def apply_settings(self):
        self.target.interpretText()
        self.ceiling.interpretText()
        self.check_current()
        if not self.apply_button.isEnabled():
            return
        verified = self.verified
        project = copy.deepcopy(self.window.project)
        self.status.setText(tr('Rechecking original and draft sources before Apply…'))
        def operation(cancel, progress):
            validate_verified_proposal(project, verified, cancel=cancel)
            return verified
        self._start(operation, self._commit, 'applying')

    def _commit(self, verified):
        # Called on the owner thread. No filesystem access or nested event loop.
        if not self._current() or self.verified is not verified:
            return
        self.state = 'committing'
        gain = verified.proposal.proposed_master_gain_db
        w = self.window
        before = (w.editor, w.editor.revision)
        w.edit(lambda: w.editor.set_master_gain(gain))
        if before != (w.editor, w.editor.revision) or w.project.master_gain_db == gain:
            self.accept()
        else:
            self.state = 'failed'
            self.verified = None

    def cancel_work(self):
        self._discard()
        if not self._closed_flag:
            self.state = 'canceled'
            self.status.setText(tr('Proposal canceled. The project and gain are unchanged.'))
            self._update_buttons()

    def _closed(self, *_):
        self._closed_flag = True
        self.timer.stop()
        self._discard()
        self.state = 'closed'


def open_loudness_proposal(window, report):
    if window.closing or window.jobs or report is None:
        return None
    old = getattr(window, 'loudness_proposal_dialog', None)
    if old is not None and not old._closed_flag:
        old.show()
        old.raise_()
        old.check_current()
        return old
    if old is not None:
        if old.job is not None:
            return old
        old.deleteLater()
    dialog = LoudnessProposalDialog(window, report)
    window.loudness_proposal_dialog = dialog
    dialog.show()
    return dialog


def close_loudness_proposal(window):
    dialog = getattr(window, 'loudness_proposal_dialog', None)
    if dialog is not None:
        dialog.reject()

"""Explicit, session-only analysis of the export graph's pre-encoding stereo mix.

No automatic gain, playback metering, saved project fields or timeline rows.
The shared job registry owns worker lifetime. Every presentation callback is
bound to the request, editor session, audio graph and current source identities.
"""
from __future__ import annotations

import copy
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QSizePolicy

from .audio_analysis import analyze_audio, audio_settings_fingerprint, result_is_current
from .ui_audio_freshness import AudioFreshnessController
from .ui_loudness_proposal import open_loudness_proposal, close_loudness_proposal
from .i18n import tr, tr_error, locale_events
from .i18n_widgets import (QDialog, QLabel, QPushButton, QFormLayout,
                           QScrollArea, QWidget, QProgressBar)


class AudioAnalysisDialog(QDialog):
    """Modeless report with explicit analysis and prompt stale-result removal."""
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.result = None
        self.job = None
        self.context = None
        self.state = 'idle'
        self._generation = 0
        self._closed_flag = False
        self._error = None
        self._candidate = None
        self._freshness_token = None
        self._freshness_generation = None
        self._last_verified = 0.0
        self._initial_publication = False
        self._analysis_job_generation = None
        if getattr(window, 'audio_analysis_freshness', None) is None:
            window.audio_analysis_freshness = AudioFreshnessController(window)
        self.freshness = window.audio_analysis_freshness
        self.freshness.checked.connect(self._freshness_checked)
        locale_events.changed.connect(self._language_changed)
        self.setWindowTitle(tr('Analyze timeline audio'))
        self.setModal(False)
        self.resize(640, 620)
        self.setMinimumSize(360, 400)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        body = QVBoxLayout(content)
        self.stage = self._label(tr('Final stereo mix · after clip gain, mute, timing, fades and master output gain; before audio encoding.'))
        body.addWidget(self.stage)
        self.status = self._label(tr('Analyze explicitly. No project settings or gain will be changed.'))
        self.status.setStyleSheet('font-weight: 600; color: #9cddcc;')
        body.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        body.addWidget(self.progress)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.values = {}
        self.metric_labels = {}
        for key, label in (
                ('lufs', tr('Integrated loudness')),
                ('left', tr('Left sample peak')),
                ('right', tr('Right sample peak')),
                ('true_left', tr('Left estimated true peak')),
                ('true_right', tr('Right estimated true peak')),
                ('overloads', tr('Overloaded channel samples')),
                ('first', tr('First overload · timeline time')),
                ('duration', tr('Analyzed duration'))):
            name = self._label(label)
            value = self._label(tr('Not analyzed'))
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.values[key] = value
            self.metric_labels[key] = name
            form.addRow(name, value)
        body.addLayout(form)
        self.definition = self._label(tr('Overload means an absolute sample value above 1.0 (0 dBFS). Counts are per channel sample, not separate clipping events.'))
        body.addWidget(self.definition)
        self.caveat = self._label(tr('Estimated true peak uses a four-times FIR reconstruction with a sample-peak lower bound. AAC or other encoding can change peaks. This is not an encoded-file check, a limiter or a compliance certification.'))
        body.addWidget(self.caveat)
        self.next_step = self._label(tr('To adjust the entire mix: Timeline → Master output gain. For one clip, use Gain in the Audio inspector. Apply gain, build preview and listen, then analyze again. Results are session-only and clear when the audio or source changes.'))
        body.addWidget(self.next_step)
        self.proposal_button = QPushButton(tr("Measured master-gain proposal"))
        self.proposal_button.setAutoDefault(False)
        self.proposal_button.clicked.connect(self.open_proposal)
        body.addWidget(self.proposal_button)
        body.addStretch()
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, 1)
        row = QHBoxLayout()
        self.analyze_button = QPushButton(tr('Analyze'))
        self.cancel_button = QPushButton(tr('Cancel analysis'))
        self.close_button = QPushButton(tr('Close'))
        for button in (self.analyze_button, self.cancel_button, self.close_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.analyze_button.clicked.connect(self.analyze)
        self.cancel_button.clicked.connect(self.cancel_analysis)
        self.close_button.clicked.connect(self.reject)
        self.finished.connect(self._closed)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.check_current)
        self.timer.start()
        self._update_buttons()
        # This modeless window owns its font and available viewport width.
        # The parent window governor must not overwrite its accessibility size.
        from .ui_style import apply_ui_layout_tokens
        apply_ui_layout_tokens(self)

    @staticmethod
    def _label(text):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return label

    def _context(self):
        return (id(self.window.editor), self.window.editor.session_id,
                audio_settings_fingerprint(self.window.project))

    def _context_current(self, context):
        if self._closed_flag or self.window.closing or not self.isVisible():
            return False
        try:
            return context == self._context()
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    def _language_changed(self, *_):
        if self.state == 'failed' and self._error is not None:
            self.status.setText(tr('Audio analysis unavailable · {error}', error=tr_error(self._error)))

    def _show_error(self, error):
        self._clear_result()
        self._error = str(error)
        self.state = 'failed'
        self.status.setText(tr('Audio analysis unavailable · {error}', error=tr_error(self._error)))
        self._update_buttons()

    def _clear_result(self):
        self.result = None
        self._error = None
        self.progress.setRange(0, 100)
        for label in self.values.values():
            label.setText(tr('Not analyzed'))
        self.progress.setValue(0)

    def _update_buttons(self):
        busy = bool(self.window.jobs)
        self.proposal_button.setEnabled(self.state == "current" and self.result is not None
                                        and not busy and not self._closed_flag and not self.window.closing)
        self.analyze_button.setEnabled(not self._closed_flag and not self.window.closing
                                       and not busy and bool(self.window.project.clips))
        self.cancel_button.setEnabled((self.job is not None and not self.job.cancel.is_set())
                                      or self.state == 'checking')

    def open_proposal(self):
        self.check_current()
        if self.state == "current" and self.result is not None:
            return open_loudness_proposal(self.window, self.result)

    def analyze(self):
        if self._closed_flag or self.window.closing:
            return
        if self.window.jobs:
            self.status.setText(tr('Wait for the current task to finish or cancel it before analyzing.'))
            self._update_buttons()
            return
        close_loudness_proposal(self.window)
        self._cancel_freshness()
        self._clear_result()
        if not self.window.project.clips:
            self.state = 'unavailable'
            self.status.setText(tr('Add a clip to the timeline before analyzing audio.'))
            self._update_buttons()
            return
        self._generation += 1
        generation = self._generation
        try:
            context = self._context()
            project = copy.deepcopy(self.window.project)
        except Exception as error:
            self._show_error(error)
            return
        self.context = context
        self.state = 'analyzing'
        self.status.setText(tr('Analyzing the pre-encoding stereo mix… Changes invalidate this request.'))

        def operation(cancel, progress):
            try:
                result = analyze_audio(project, cancel=cancel,
                    progress=lambda phase: progress({'snapshot': .05, 'decode': .2, 'verify': .95}.get(phase, .2)))
                return result, None
            except Exception as error:
                # Errors share the same context guard as successful reports.
                # No late modal warning from a canceled or stale worker.
                return None, str(error)

        def before_start(job):
            self.job = job
            self._initial_publication = True
            self._analysis_job_generation = self.window._job_generation
            job.progress.connect(lambda value: self.progress.setValue(round(value * 100))
                                 if generation == self._generation and not self._closed_flag else None)
            job.finished.connect(lambda: self._job_finished(job, generation))

        self.window.start_job(tr('Analyzing timeline audio…'), operation,
            lambda value: self._accept_result(generation, context, value),
            'audio_analysis', before_start=before_start)
        self._update_buttons()

    def _accept_result(self, generation, context, value):
        if generation != self._generation or self._closed_flag or self.window.closing:
            return
        if not self._context_current(context):
            self.invalidate()
            return
        result, error = value
        if error is not None:
            self._show_error(error)
            return
        if result is None:
            self.invalidate()
            return
        self._request_freshness(result)

    def _cancel_freshness(self):
        if self._freshness_token is not None:
            self.freshness.cancel(self._freshness_token)
        self._freshness_token = None
        self._candidate = None

    def _request_freshness(self, result):
        # This copy and context key contain in-memory model data only. All
        # source/decoder stat, resolve, lookup and descriptor I/O is off-GUI.
        try:
            project = copy.deepcopy(self.window.project)
        except Exception as error:
            self._show_error(error)
            return
        if self.state == 'current' and self.result is result:
            # Keep the last measurements readable during routine verification,
            # but revoke their current-result status until the reply arrives.
            self.result = None
            self._error = None
        else:
            self._clear_result()
        self._candidate = result
        self.state = 'checking'
        self.status.setText(tr('Checking audio sources… Results are not current until verification finishes.'))
        self.progress.setRange(0, 0)
        self._freshness_generation = self._generation
        self._freshness_token = self.freshness.request(project, result, result_is_current)
        self._update_buttons()

    def _freshness_checked(self, token, result, current):
        if (token is not self._freshness_token or result is not self._candidate
                or self._freshness_generation != self._generation
                or self.state != 'checking' or self._closed_flag or self.window.closing):
            return
        self._freshness_token = None
        if not self._context_current(self.context) or not current:
            self.invalidate()
            return
        self.result = result
        self.state = 'current'
        self._last_verified = time.monotonic()
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        if self._initial_publication:
            self._initial_publication = False
            # The initial report may finish freshness checks after another
            # tracked task has already taken ownership of shared progress.
            if (self.window._job_generation == self._analysis_job_generation
                    and not any(job is not self.job for job in self.window.jobs)):
                self.window.progress_bar.setValue(100)
        self.status.setText(tr('Current result · pre-encoding stereo mix only.'))
        self._show_result(result)
        self._update_buttons()

    def _show_result(self, result):
        if result.integrated_lufs is not None:
            self.values['lufs'].setText(tr('{value:.1f} LUFS', value=result.integrated_lufs))
        elif result.loudness_status == 'silence':
            self.values['lufs'].setText(tr('No measurable LUFS · silence'))
        elif result.loudness_status == 'too_short':
            self.values['lufs'].setText(tr('No measurable LUFS · less than 400 ms'))
        else:
            self.values['lufs'].setText(tr('No measurable LUFS · below the loudness gate'))
        for key, peak in zip(('left', 'right'), result.sample_peak_dbfs):
            self.values[key].setText(tr('Silence (−∞ dBFS)') if peak is None
                                    else tr('{value:.2f} dBFS', value=peak))
        for key, peak in zip(('true_left', 'true_right'), result.estimated_true_peak_dbtp):
            self.values[key].setText(tr('Silence (−∞ dBTP)') if peak is None
                                    else tr('{value:.2f} dBTP', value=peak))
        left, right = result.overload_counts
        self.values['overloads'].setText(tr('{total} (L: {left}; R: {right})',
                                           total=result.overload_count, left=left, right=right))
        self.values['first'].setText(tr('None') if result.first_overload_seconds is None
            else tr('{seconds:.6f} s', seconds=result.first_overload_seconds))
        self.values['duration'].setText(tr('{seconds:.3f} s · {rate} Hz · stereo',
            seconds=result.sample_count / result.sample_rate, rate=result.sample_rate))

    def _job_finished(self, job, generation):
        if self.job is job:
            self.job = None
        if self._closed_flag:
            return
        if generation == self._generation and self.state == 'analyzing':
            self.cancel_analysis()
        self._update_buttons()

    def cancel_analysis(self):
        if self.job is None and self.state not in ('analyzing', 'checking'):
            return
        self._generation += 1
        self._cancel_freshness()
        if self.job is not None:
            self.job.cancel.set()
        self._clear_result()
        self.state = 'canceled'
        self.status.setText(tr('Analysis canceled. The project and gain are unchanged.'))
        self._update_buttons()

    def invalidate(self):
        close_loudness_proposal(self.window)
        self._generation += 1
        self._cancel_freshness()
        if self.job is not None:
            self.job.cancel.set()
        self._clear_result()
        self.state = 'stale'
        self.status.setText(tr('Audio or source changed. Analyze again for a current result.'))
        self._update_buttons()

    def check_current(self):
        if self._closed_flag:
            return
        if self.window.closing:
            self.reject()
            return
        if self.state in ('current', 'analyzing', 'checking'):
            if not self._context_current(self.context):
                self.invalidate()
            elif self.state == 'current' and time.monotonic() - self._last_verified >= .5:
                self._request_freshness(self.result)
        self._update_buttons()

    def _closed(self, *_):
        self._closed_flag = True
        self._generation += 1
        self.timer.stop()
        self._cancel_freshness()
        if self.job is not None:
            self.job.cancel.set()
        self._clear_result()
        self.state = 'closed'
        # Keep this QObject alive until its tracked worker has stopped, so all
        # queued callbacks can safely observe the closed-generation guard.


def open_audio_analysis(window):
    dialog = getattr(window, 'audio_analysis_dialog', None)
    if dialog is not None and not dialog._closed_flag:
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        dialog.check_current()
        return dialog
    if dialog is not None:
        if dialog.job is not None:
            return dialog
        dialog.deleteLater()
    dialog = AudioAnalysisDialog(window)
    window.audio_analysis_dialog = dialog
    dialog.show()
    return dialog


def close_audio_analysis(window):
    close_loudness_proposal(window)
    dialog = getattr(window, 'audio_analysis_dialog', None)
    if dialog is not None:
        dialog.reject()

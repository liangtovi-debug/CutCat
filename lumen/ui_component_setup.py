"""Local component preferences and cancellable metadata-only diagnostics.

This dialog never registers a model for inference or edits the current project.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, QTimer, Qt, Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout

from .i18n import locale_events, tr, tr_error
from .i18n_widgets import (QCheckBox, QComboBox, QDialog, QFormLayout,
                           QGroupBox, QLabel, QLineEdit, QPlainTextEdit,
                           QPushButton, QScrollArea, QWidget)

FIELD_GROUPS = (
    ('Speech recognition · whisper.cpp', (('asr_runtime', 'Runtime'), ('asr_model', 'Model'))),
    ('Speech synthesis · local TTS', (('tts_model_dir', 'Local pack'),)),
    ('Semantic search · MiniLM', (('search_model', 'Model'), ('search_tokenizer', 'Tokenizer'))),
    ('Subtitle assistant · Qwen', (('assistant_runtime', 'Runtime'), ('assistant_model', 'Model'))),
)
STATUS_LABELS = {
    'unset': 'Not configured', 'missing': 'Missing', 'invalid': 'Needs attention',
    'metadata_available': 'Metadata available', 'hash_verified': 'File hash verified',
    'unavailable': 'Unavailable',
}


def preferences_path():
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)) / 'component-setup.json'


def operational_settings(window):
    """Read only the fields used by the existing inference workflows."""
    return {
        'asr_runtime': window.asr_runtime.text(), 'asr_model': window.asr_model.text(),
        'tts_model_dir': window.tts_model.text(), 'tts_pack_version': window.tts_pack.currentData(),
        'search_model': window.ai_fields['semantic-search']['model'].text(),
        'search_tokenizer': window.ai_fields['semantic-search']['tokenizer'].text(),
        'assistant_runtime': window.ai_fields['subtitle-assistant']['runtime'].text(),
        'assistant_model': window.ai_fields['subtitle-assistant']['model'].text(),
    }


def apply_operational_settings(window, settings):
    fields = {
        'asr_runtime': window.asr_runtime, 'asr_model': window.asr_model,
        'tts_model_dir': window.tts_model,
        'search_model': window.ai_fields['semantic-search']['model'],
        'search_tokenizer': window.ai_fields['semantic-search']['tokenizer'],
        'assistant_runtime': window.ai_fields['subtitle-assistant']['runtime'],
        'assistant_model': window.ai_fields['subtitle-assistant']['model'],
    }
    for key, field in fields.items():
        if key in settings:
            field.setText(settings[key])  # Existing invalidation signals stay enabled.
    if 'tts_pack_version' in settings:
        index = window.tts_pack.findData(settings['tts_pack_version'])
        if index >= 0:
            window.tts_pack.setCurrentIndex(index)


def restore_component_preferences(window, path=None):
    """Call after both caption and Local AI panels have been constructed."""
    from .component_setup import load_preferences
    path = Path(path) if path is not None else preferences_path()
    if path.is_file():
        apply_operational_settings(window, load_preferences(path))


class _ResultBridge(QObject):
    completed = Signal(int, object, object)
    stopped = Signal(int)


class ComponentSetupDialog(QDialog):
    def __init__(self, window, *, path=None, diagnose=None):
        super().__init__(window)
        from .component_setup import diagnose_components
        from .voice_catalog import PACKS
        from .ui_captions import tts_pack_label
        self.owner = window
        self.path = Path(path) if path is not None else preferences_path()
        self.diagnose = diagnose or diagnose_components
        self._generation = 0
        self._cancel = None
        self._report = None
        self._bridges = {}
        self.setWindowTitle(tr('Local AI component setup'))
        self.resize(760, 730)
        root = QVBoxLayout(self)
        note = QLabel(tr('Choose existing local files. Diagnostics inspect metadata only: no inference, downloads, package installation, or cloud calls. File checks do not establish model quality or runtime readiness.'))
        note.setWordWrap(True); root.addWidget(note)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        body = QWidget(); groups = QVBoxLayout(body); scroll.setWidget(body); root.addWidget(scroll, 1)
        current = operational_settings(window)
        self._owner_snapshot = current.copy()
        self.fields = {}
        for title, entries in FIELD_GROUPS:
            group = QGroupBox(tr(title)); form = QFormLayout(group)
            for key, label in entries:
                field = QLineEdit(current.get(key, '')); self.fields[key] = field
                field.setAccessibleName(tr('{component}: {field}', component=tr(title), field=tr(label)))
                row = QHBoxLayout(); row.addWidget(field)
                button = QPushButton(tr('Choose…'))
                button.clicked.connect(lambda checked=False, k=key: self.choose_path(k))
                row.addWidget(button); form.addRow(tr(label), row)
                field.textChanged.connect(self.invalidate)
            if entries[0][0] == 'tts_model_dir':
                self.pack = QComboBox()
                self.pack.setMinimumContentsLength(12)
                self.pack.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                for version, pack in PACKS.items():
                    self.pack.addItem(tts_pack_label(pack), version)
                self.pack.setCurrentIndex(max(0, self.pack.findData(current['tts_pack_version'])))
                self.pack.currentIndexChanged.connect(self.invalidate)
                self.pack.currentIndexChanged.connect(self.refresh_pack_notice)
                form.addRow(tr('Pack version'), self.pack)
                self.pack_info = QLabel(); self.pack_info.setWordWrap(True)
                form.addRow(self.pack_info); self.refresh_pack_notice()
            groups.addWidget(group)
        self.hashes = QCheckBox(tr('Hash selected files (large models may take longer)'))
        self.hashes.toggled.connect(self.invalidate); root.addWidget(self.hashes)
        self.status = QLabel(tr('Not checked. Choose files, then run diagnostics.'))
        self.status.setWordWrap(True); root.addWidget(self.status)
        self.results = QPlainTextEdit(); self.results.setReadOnly(True); self.results.setMinimumHeight(150)
        root.addWidget(self.results)
        row = QHBoxLayout()
        self.check_button = QPushButton(tr('Run / retry diagnostics')); self.check_button.clicked.connect(self.start_diagnostics)
        self.cancel_button = QPushButton(tr('Cancel diagnostics')); self.cancel_button.clicked.connect(self.cancel_diagnostics); self.cancel_button.setEnabled(False)
        self.apply_button = QPushButton(tr('Save and apply paths')); self.apply_button.clicked.connect(self.apply_paths)
        close = QPushButton(tr('Close')); close.clicked.connect(self.reject)
        for button in (self.check_button, self.cancel_button, self.apply_button, close): row.addWidget(button)
        root.addLayout(row)
        locale_events.changed.connect(self.render_report)
        locale_events.changed.connect(self._refresh_minimum_height)
        self._initial_screen_fit = False
        self._height_refresh_pending = False

    def refresh_pack_notice(self, *_):
        from .voice_catalog import get_pack
        from .ui_captions import tts_pack_notice
        self.pack_info.setText(tts_pack_notice(get_pack(self.pack.currentData())))

    def _refresh_minimum_height(self, *_):
        """Wrapped notes need more height than QLayout.minimumSize reports."""
        self._height_refresh_pending = False
        layout = self.layout()
        if layout is None:
            return
        # CJK fallback can resolve after the first style/layout pass. Keep
        # the final native button padding as well as the visible glyph height.
        for button in self.findChildren(QPushButton):
            required_button_height = button.minimumSizeHint().height()
            if button.minimumHeight() < required_button_height:
                # The owner's shared layout controller preserves this floor
                # when it revisits newly added dialog descendants.
                button._lumen_original_min_height = max(
                    getattr(button, '_lumen_original_min_height', 0), required_button_height)
                button.setMinimumHeight(required_button_height)
                layout.invalidate()
        layout.activate()
        required = max(layout.minimumSize().height(),
                       layout.minimumHeightForWidth(self.width()))
        if self.minimumHeight() != required:
            self.setMinimumHeight(required)

    def _fit_available_screen(self, available=None):
        """Fit once in logical pixels, preserving usable layout minima.

        If even the measured minimum exceeds the available display, retain
        that minimum rather than overlap controls or hide the action row.
        """
        self.ensurePolished()
        layout = self.layout(); layout.activate()
        available = available or self.screen().availableGeometry()
        handle = self.windowHandle()
        margins = handle.frameMargins() if handle is not None else None
        frame_width = margins.left() + margins.right() if margins else 0
        frame_height = margins.top() + margins.bottom() if margins else 0
        # Keep a small edge gap when possible; frame margins are native,
        # whereas screen geometry and widget sizes are already logical pixels.
        width = max(layout.minimumSize().width(),
                    min(self.width(), available.width() - frame_width - 16))
        height_limit = available.height() - frame_height - 16
        minimum_height = max(layout.minimumSize().height(),
                             layout.minimumHeightForWidth(width))
        if minimum_height > height_limit:
            # The report itself scrolls. On a short display retain at least
            # three text lines so native title bars do not hide the actions.
            report_floor = max(64, self.results.fontMetrics().lineSpacing() * 3 + 16)
            self.results.setMinimumHeight(max(report_floor, self.results.minimumHeight()
                                               - (minimum_height - height_limit)))
            layout.invalidate(); layout.activate()
            minimum_height = max(layout.minimumSize().height(),
                                 layout.minimumHeightForWidth(width))
        height = max(minimum_height, min(self.height(), height_limit))
        self.setMinimumHeight(minimum_height)
        self.resize(width, height)
        self._refresh_minimum_height()
        frame = self.frameGeometry()
        x = max(available.left(), min(frame.left(), available.right() - frame.width() + 1))
        y = max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1))
        self.move(x, y)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initial_screen_fit:
            self._initial_screen_fit = True
            # Let inherited fonts/style and native frame margins settle first.
            QTimer.singleShot(0, self, self._fit_available_screen)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not getattr(self, '_height_refresh_pending', False):
            self._height_refresh_pending = True
            QTimer.singleShot(0, self, self._refresh_minimum_height)

    def values(self):
        return {**{key: field.text().strip() for key, field in self.fields.items()}, 'tts_pack_version': self.pack.currentData()}

    def choose_path(self, key):
        initial = self.fields[key].text()
        if key == 'tts_model_dir':
            path = QFileDialog.getExistingDirectory(self, tr('Choose an existing local model-pack folder'), initial)
        else:
            path, _ = QFileDialog.getOpenFileName(self, tr('Choose an existing local model/runtime file'), initial, tr('All files (*)'))
        if path: self.fields[key].setText(path)

    def invalidate(self, *_):
        self._generation += 1
        if self._cancel is not None: self._cancel.set()
        self._cancel = None
        self._report = None
        if hasattr(self, 'results'):
            self.results.clear()
            self.status.setText(tr('Paths or options changed. Run diagnostics again.'))
            self.cancel_button.setEnabled(False)
            self.check_button.setEnabled(True)

    def start_diagnostics(self):
        if self.owner.jobs:
            self.status.setText(tr('Wait for the current task to finish or cancel it before running diagnostics.'))
            return
        self.invalidate()
        generation = self._generation
        values = self.values(); hashes = self.hashes.isChecked()
        bridge = _ResultBridge(); self._bridges[generation] = bridge
        bridge.completed.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        bridge.stopped.connect(self._stopped, Qt.ConnectionType.QueuedConnection)
        self.status.setText(tr('Checking local metadata…'))
        self.check_button.setEnabled(False); self.cancel_button.setEnabled(True)
        diagnose = self.diagnose
        def operation(cancel, progress):
            try: return diagnose(values, verify_hashes=hashes, cancel=cancel), None
            except Exception as exc: return None, exc
        job = self.owner.start_job(tr('Checking local metadata…'), operation,
                                  lambda result: bridge.completed.emit(generation, *result), 'componentsetup')
        self._cancel = job.cancel
        job.finished.connect(lambda: bridge.stopped.emit(generation))

    def _stopped(self, generation):
        self._bridges.pop(generation, None)
        if generation != self._generation: return
        if self._cancel is not None and self._cancel.is_set():
            self.cancel_diagnostics()

    def _finished(self, generation, report, error):
        if generation != self._generation: return
        self._cancel = None
        self.check_button.setEnabled(True); self.cancel_button.setEnabled(False)
        if error is not None:
            self.status.setText(tr('Diagnostics failed: {error}', error=tr_error(error)))
            return
        self._report = report
        self.status.setText(tr('Checks finished. Inference was not tested. Use each existing workflow’s verification before running a model.'))
        self.render_report()

    def render_report(self, *_):
        if self._report is None: return
        lines = []
        for check in self._report.checks:
            lines.append(str(tr('{component} · {status}', component=tr('{component}: {field}', component=tr(check.component), field=tr(check.key)), status=tr(STATUS_LABELS.get(check.status, check.status)))))
            lines.append(str(tr(check.message)))
            if check.path: lines.append(check.path)
            if check.recommendation: lines.append(str(tr(check.recommendation)))
            if check.version: lines.append(str(tr('Version: {version}', version=check.version)))
            if check.sha256: lines.append('SHA-256: ' + check.sha256)
            lines.append('')
        self.results.setPlainText('\n'.join(lines))

    def cancel_diagnostics(self):
        self.invalidate()
        self.status.setText(tr('Diagnostics canceled. You can retry.'))

    def apply_paths(self):
        from .component_setup import save_preferences
        if self.owner.jobs:
            self.status.setText(tr('Wait for the current task to finish or cancel it before applying paths.')); return
        if operational_settings(self.owner) != self._owner_snapshot:
            self.status.setText(tr('Local AI fields changed outside this dialog. Close and reopen setup before applying paths.')); return
        values = self.values()
        try: save_preferences(self.path, values)
        except Exception as exc:
            self.status.setText(tr('Could not save local paths: {error}', error=tr_error(exc))); return
        apply_operational_settings(self.owner, values)
        self._owner_snapshot = operational_settings(self.owner)
        self.status.setText(tr('Local paths saved and applied. No project changes; model inference still requires its normal verification.'))

    def done(self, result):
        self.cancel_diagnostics()
        super().done(result)


def open_component_setup(window):
    dialog = getattr(window, '_component_setup_dialog', None)
    if dialog is None:
        dialog = ComponentSetupDialog(window)
        window._component_setup_dialog = dialog
        # Refresh from the operational fields each time, including manual changes.
        dialog.finished.connect(lambda _result: setattr(window, '_component_setup_dialog', None))
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.show(); dialog.raise_(); dialog.activateWindow()
    return dialog

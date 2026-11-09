"""Beginner-oriented three-step API settings; deliberately no live connection.

Only explicit Apply replaces the caller's in-memory session. Cancel discards
edits. No models are fetched, no secret is read from the environment, and no
configuration is written to disk, projects, telemetry or logs.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QFrame, QSizePolicy

from .api_settings import (ApiSettingsSession, ApiSettingsValidationError,
                           validate_provider, validated_session)
from .i18n import tr
from .i18n_widgets import (QCheckBox, QDialog, QLabel, QLineEdit, QPushButton,
                           QScrollArea, QWidget)
# Explicit import also makes this optional panel's catalog visible to bundlers.
from .i18n_catalog_api_settings import VALIDATION_MESSAGES


def _label(text, *, muted=False):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if muted:
        label.setStyleSheet('color: #aab8c8; font-size: 12px;')
    return label


class ApiSettingsDialog(QDialog):
    applied = Signal(object)

    def __init__(self, session: ApiSettingsSession | None = None, parent=None):
        super().__init__(parent)
        self.original_session = session if isinstance(session, ApiSettingsSession) else ApiSettingsSession()
        self.session = self.original_session
        self._finished = False
        self._active_step = 0
        self.setWindowTitle(tr('Custom model API'))
        self.setModal(True)
        self.setMinimumSize(440, 480)
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            self.resize(min(650, max(440, available.width() - 40)),
                        min(740, max(480, available.height() - 40)))
        else:
            self.resize(650, 740)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(14)
        heading = _label(tr('Set up your AI, one step at a time'))
        heading.setStyleSheet('font-size: 21px; font-weight: 650;')
        root.addWidget(heading)
        root.addWidget(_label(tr('Add a provider, then choose a model. Online chat is not connected in this version.'), muted=True))
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); steps = QVBoxLayout(content)
        steps.setContentsMargins(0, 0, 4, 0); steps.setSpacing(14)
        scroll.setWidget(content); root.addWidget(scroll, 1)
        self.scroll = scroll
        self.step_buttons = {}; self.step_hints = {}; self.step_bodies = {}; self.step_arrows = {}
        titles = {1: tr('Add AI provider'), 2: tr('Choose a model'), 3: tr('Start chatting')}
        for number in (1, 2, 3):
            card = QFrame(); card.setObjectName('apiStepCard')
            card.setStyleSheet('QFrame#apiStepCard { border: 1px solid #394656; border-radius: 10px; }')
            box = QVBoxLayout(card); box.setContentsMargins(16, 14, 16, 14); box.setSpacing(9)
            row = QHBoxLayout(); row.setSpacing(13)
            badge = QLabel(str(number)); badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(38, 38)
            badge.setStyleSheet('background: #2578df; color: white; border: 0; border-radius: 19px; font-size: 19px; font-weight: 600;')
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            texts = QVBoxLayout(); texts.setSpacing(3)
            button = QPushButton(titles[number]); button.setFlat(True)
            button.setStyleSheet('QPushButton { text-align: left; font-size: 17px; font-weight: 650; padding: 0; border: 1px solid transparent; background: transparent; } QPushButton:focus { border-color: #9cddcc; background: #253c3a; } QPushButton:disabled { color: #8b97a7; }')
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            button.setAutoDefault(False)
            button.clicked.connect(lambda checked=False, n=number: self.show_step(n))
            texts.addWidget(button)
            hint = _label('', muted=True); texts.addWidget(hint)
            row.addLayout(texts, 1)
            arrow = QPushButton('›'); arrow.setFixedSize(32, 34); arrow.setAutoDefault(False)
            arrow.setAccessibleName(titles[number])
            arrow.setStyleSheet('QPushButton { border: 1px solid transparent; background: transparent; font-size: 26px; color: #aab8c8; } QPushButton:focus { border-color: #9cddcc; background: #253c3a; } QPushButton:disabled { color: #8b97a7; }')
            arrow.clicked.connect(lambda checked=False, n=number: self.show_step(n))
            row.addWidget(arrow, 0, Qt.AlignmentFlag.AlignTop)
            self.step_arrows[number] = arrow
            box.addLayout(row)
            body = QWidget(); body_layout = QVBoxLayout(body)
            body_layout.setContentsMargins(51, 7, 0, 0); body_layout.setSpacing(8)
            box.addWidget(body); steps.addWidget(card)
            self.step_buttons[number] = button; self.step_hints[number] = hint
            self.step_bodies[number] = body
        provider = self.step_bodies[1].layout()
        self.fields = {}
        self._field(provider, 'provider_name', tr('Provider name (optional)'), tr('For example: My AI service'))
        self._field(provider, 'base_url', tr('API address (Base URL)'), 'https://api.example.com/v1')
        provider.addWidget(_label(tr('Copy the API base address from your provider, including /v1 if specified. Not the website or the /chat/completions URL.'), muted=True))
        self._field(provider, 'api_key', tr('API key (you can add it later)'), tr('Paste the key from your provider'))
        self.fields['api_key'].setEchoMode(QLineEdit.EchoMode.Password)
        self.fields['api_key'].setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.show_key = QCheckBox(tr('Show key'))
        self.show_key.toggled.connect(lambda shown: self.fields['api_key'].setEchoMode(
            QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password))
        provider.addWidget(self.show_key)
        provider.addWidget(_label(tr('Session only. The key is never saved to your project or disk. No network request is made here.'), muted=True))
        self.next_button = QPushButton(tr('Next: choose a model')); self.next_button.setAutoDefault(False)
        self.next_button.clicked.connect(self.next_step); provider.addWidget(self.next_button)
        model = self.step_bodies[2].layout()
        self._field(model, 'model', tr('Model ID'), tr('For example: gpt-4.1-mini'))
        model.addWidget(_label(tr('Copy the exact model ID from your provider. This version does not fetch a model list.'), muted=True))
        self.step_bodies[3].layout().addWidget(_label(tr('Not available yet. Applying these fields does not enable online chat or replace offline editing.')))
        self.step_bodies[3].show()
        self.step_buttons[3].setEnabled(False)
        self.step_arrows[3].setEnabled(False)
        steps.addStretch()
        self.advanced = QPushButton(tr('Advanced details')); self.advanced.setCheckable(True)
        self.advanced.setAutoDefault(False)
        self.advanced.setStyleSheet('text-align: left; padding: 7px;')
        root.addWidget(self.advanced)
        self.advanced_body = _label(tr('Protocol: OpenAI-compatible API. HTTPS only; plain HTTP is not supported. No automatic requests, model discovery or permanent credential storage. Settings last until you close CutCat.'), muted=True)
        self.advanced_body.hide(); root.addWidget(self.advanced_body)
        self.advanced.toggled.connect(self.advanced_body.setVisible)
        self.advanced.toggled.connect(self._advanced_toggled)
        self._advanced_toggled(False)
        self.status = _label(tr('Nothing sent. Fill in the address and model, then check the fields.'), muted=True)
        self.status.setAccessibleName(tr('Configuration status'))
        root.addWidget(self.status)
        actions = QHBoxLayout(); actions.setSpacing(8)
        self.check_button = QPushButton(tr('Check fields'))
        self.check_button.setAutoDefault(False); self.check_button.clicked.connect(self.check_fields)
        self.test_button = QPushButton(tr('Test connection · unavailable'))
        self.test_button.setAutoDefault(False); self.test_button.setEnabled(False)
        self.test_button.setToolTip(tr('Online connections are not implemented. No key, text or media is sent.'))
        actions.addWidget(self.check_button); actions.addWidget(self.test_button)
        actions.addStretch(); root.addLayout(actions)
        footer = QHBoxLayout()
        self.cancel_button = QPushButton(tr('Cancel')); self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        self.apply_button = QPushButton(tr('Apply for this session')); self.apply_button.setObjectName('primary')
        self.apply_button.setAutoDefault(False); self.apply_button.clicked.connect(self.apply_settings)
        footer.addStretch(); footer.addWidget(self.cancel_button); footer.addWidget(self.apply_button)
        root.addLayout(footer)
        for name, field in self.fields.items():
            value = getattr(self.original_session, name)
            field.setText(value if isinstance(value, str) else '')
            field.textChanged.connect(self.invalidate)
        self._refresh_steps()

    def _advanced_toggled(self, expanded):
        self.advanced.setText(tr('Advanced details ▾') if expanded else tr('Advanced details ▸'))

    def _field(self, layout, name, title, placeholder):
        label = _label(title)
        field = QLineEdit(); field.setAccessibleName(title); field.setPlaceholderText(placeholder)
        field.setMinimumHeight(34); label.setBuddy(field)
        field.setMaxLength({'provider_name': 120, 'base_url': 2048, 'model': 256, 'api_key': 8192}[name])
        layout.addWidget(label); layout.addWidget(field); self.fields[name] = field

    def values(self):
        return {name: field.text() for name, field in self.fields.items()}

    def _provider_ready(self):
        data = self.values()
        try:
            validate_provider(data['provider_name'], data['base_url'], data['api_key'])
            return True
        except ApiSettingsValidationError:
            return False

    def _candidate(self):
        return validated_session(**self.values())

    def _refresh_steps(self):
        ready = self._provider_ready()
        self.step_buttons[2].setEnabled(ready)
        self.step_arrows[2].setEnabled(ready)
        self.step_hints[1].setText(tr('Copy your provider’s API address and key.'))
        self.step_hints[2].setText(tr('Enter the model ID from your provider.') if ready else tr('Complete step 1 first.'))
        self.step_hints[3].setText(tr('Online chat is not connected yet.'))
        if not ready and self._active_step == 2:
            self._active_step = 1
        for number in (1, 2):
            self.step_bodies[number].setVisible(number == self._active_step)
        try:
            self._candidate(); valid = True
        except ApiSettingsValidationError:
            valid = False
        self.apply_button.setEnabled(valid and not self._finished)

    def invalidate(self, *_):
        self.status.setText(tr('Fields changed. Check again; no connection has been tested.'))
        self._refresh_steps()

    def show_step(self, number):
        if number == 1 or (number == 2 and self._provider_ready()):
            self._active_step = number
            self._refresh_steps()

    def _show_error(self, error):
        self.status.setText(tr(VALIDATION_MESSAGES[error.code]))
        self.show_step(2 if error.field_name == 'model' else 1)
        self.fields[error.field_name].setFocus()

    def next_step(self):
        data = self.values()
        try:
            validate_provider(data['provider_name'], data['base_url'], data['api_key'])
        except ApiSettingsValidationError as error:
            self._show_error(error); return
        self.show_step(2); self.fields['model'].setFocus()

    def check_fields(self):
        try:
            self._candidate()
        except ApiSettingsValidationError as error:
            self._show_error(error); return False
        self.status.setText(tr('Format looks correct. No connection was made; key and model availability are unverified.'))
        return True

    def apply_settings(self):
        if self._finished:
            return
        try:
            candidate = self._candidate()
        except ApiSettingsValidationError as error:
            self._show_error(error); return
        self._finished = True
        if candidate != self.original_session:
            self.session = candidate
            self.applied.emit(candidate)
        self.accept()

    def done(self, result):
        self._finished = True
        self.show_key.setChecked(False)
        self.fields['api_key'].clear()
        super().done(result)


def open_api_settings(parent=None, session: ApiSettingsSession | None = None) -> ApiSettingsSession:
    """Return the original session on cancel, a replacement only after Apply.

    The caller retains the returned object in memory, invalidates old proposals
    on identity change, and must never store it on the project or in settings.
    """
    dialog = ApiSettingsDialog(session, parent)
    try:
        dialog.exec()
        return dialog.session
    finally:
        # QDialog is parent-owned: closing alone would retain old sessions and
        # credentials until the whole editor exits. Drop references immediately
        # and schedule Qt destruction; do not promise Python memory zeroization.
        dialog.fields['api_key'].clear()
        dialog.original_session = ApiSettingsSession()
        dialog.session = ApiSettingsSession()
        dialog.deleteLater()

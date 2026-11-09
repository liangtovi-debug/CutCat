"""Read-only online voice directory. No credentials, requests, or project edits."""
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout
from PySide6.QtCore import Qt
from .i18n import tr, locale_events
from .i18n_widgets import QDialog, QLabel, QComboBox, QPushButton
from .online_voice_catalog import ONLINE_VOICES


class OnlineVoiceDirectory(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('Online voice directory · not connected'))
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        self.notice = QLabel(tr('Local speech remains the default. These are documented online voices, not installed or connected services. No text is sent by this directory.'))
        self.notice.setWordWrap(True); layout.addWidget(self.notice)
        self.voices = QComboBox()
        self.voices.setMinimumContentsLength(16)
        self.voices.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for voice in ONLINE_VOICES:
            self.voices.addItem(f'{voice.name} · {voice.provider_label}', voice.key)
        layout.addWidget(self.voices)
        self.details = QLabel(); self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.details)
        self.status = QLabel(tr('Not configured · online synthesis is unavailable and untested.'))
        self.status.setWordWrap(True); layout.addWidget(self.status)
        self.privacy = QLabel(tr('Future online synthesis requires an authorized service account and review of the exact reading text and recipient before sending. Service charges and usage terms may apply. There is no automatic fallback from local speech.'))
        self.privacy.setWordWrap(True); layout.addWidget(self.privacy)
        self.edge_notice = QLabel(tr('Edge consumer tokens are not used. Microsoft voices require the official Azure Speech service; Wanwan Xiaohe requires Doubao.'))
        self.edge_notice.setWordWrap(True); layout.addWidget(self.edge_notice)
        layout.addStretch()
        row = QHBoxLayout()
        self.generate = QPushButton(tr('Generate online · unavailable'))
        self.generate.setEnabled(False); row.addWidget(self.generate)
        self.close_button = QPushButton(tr('Close')); self.close_button.clicked.connect(self.reject)
        row.addWidget(self.close_button); layout.addLayout(row)
        self.voices.currentIndexChanged.connect(self.refresh_details)
        locale_events.changed.connect(self.refresh_details)
        self.refresh_details()

    def refresh_details(self, *_):
        voice = ONLINE_VOICES[self.voices.currentIndex()]
        self.details.setText(tr('Voice ID: {voice}\nCatalog language: {locale}\nOfficial source: {source_url}',
                                voice=voice.voice_id, locale=voice.locale, source_url=voice.source_url))


def open_online_voice_directory(window):
    existing = getattr(window, '_online_voice_directory', None)
    if existing is not None and existing.isVisible():
        existing.raise_(); existing.activateWindow(); return existing
    dialog = OnlineVoiceDirectory(window)
    window._online_voice_directory = dialog
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.finished.connect(lambda *_: setattr(window, '_online_voice_directory', None))
    dialog.show()
    return dialog

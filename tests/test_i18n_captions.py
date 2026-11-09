"""Caption and voice presentation switches without touching editable content."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QWidget
from lumen.i18n import get_locale, set_locale, tr
from lumen.i18n_catalog_captions import ZH_CN
from lumen.i18n_widgets import QPushButton
from lumen.ui_captions import CaptionPanelMixin


class Panel(QWidget, CaptionPanelMixin):
    asr_job = None
    tts_job = None

    def _button(self, text, action, *_):
        button = QPushButton(text)
        button.clicked.connect(action)
        return button


@pytest.fixture
def panel(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    previous = get_locale()
    set_locale('en')
    settings = QSettings(str(tmp_path / 'voice.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr('lumen.ui_captions.QSettings', lambda *_: settings)
    w = Panel()
    w.panel = w.build_caption_panel()
    yield w
    w.close_tts_preview()
    w.panel.deleteLater()
    w.deleteLater()
    app.processEvents()
    set_locale(previous)


def test_caption_voice_switch_preserves_content_and_identity(panel):
    w = panel
    w.caption_text.setPlainText('Text\n字幕用户内容')
    w.tts_preview_text.setPlainText('Generate captions\n用户试听文本')
    w.tts_model.setText('/custom/Chinese/voice-pack')
    w.caption_font.setText('Source')
    w.tts_origin.setCurrentIndex(w.tts_origin.findData('Chinese'))
    w.tts_voice.setCurrentIndex(2)
    identity = w.tts_voice.currentData()
    origin = w.tts_origin.currentData()
    assert w.tts_preview_button.text() == 'Generate audition'
    assert 'Chinese' in w.tts_voice.currentText()
    set_locale('zh-CN')
    assert w.tts_preview_button.text() == '生成试听'
    assert w.caption_text.placeholderText() == '字幕文本'
    assert w.tts_origin.currentText() == '中文'
    assert '中文' in w.tts_voice.currentText()
    assert w.tts_voice.currentData() == identity
    assert w.tts_origin.currentData() == origin
    assert w.caption_text.toPlainText() == 'Text\n字幕用户内容'
    assert w.tts_preview_text.toPlainText() == 'Generate captions\n用户试听文本'
    assert w.tts_model.text() == '/custom/Chinese/voice-pack'
    assert w.caption_font.text() == 'Source'
    set_locale('en')
    assert w.tts_preview_button.text() == 'Generate audition'
    assert w.tts_origin.currentText() == 'Chinese'
    assert 'Chinese' in w.tts_voice.currentText()
    assert w.tts_voice.currentData() == identity


def test_voice_dynamic_status_retranslates_without_rerunning(panel):
    w = panel
    w.tts_status.setText(tr('Inserted full voice ({duration:.6f}s): {path}', duration=3.125, path='/tmp/English voice.wav'))
    set_locale('zh-CN')
    assert w.tts_status.text() == '已插入完整语音（3.125000 秒）：/tmp/English voice.wav'
    set_locale('en')
    assert w.tts_status.text() == 'Inserted full voice (3.125000s): /tmp/English voice.wav'


def test_catalog_preserves_all_template_fields():
    from string import Formatter
    def fields(value):
        return sorted((name, spec, conversion) for _, name, spec, conversion in Formatter().parse(value) if name is not None)
    for source, translated in ZH_CN.items():
        assert fields(source) == fields(translated), source


def test_voice_note_tracks_resize_history_and_language(panel):
    from PySide6.QtCore import QRect, Qt
    from lumen.ui_captions import _WrappedCaptionNote
    app = QApplication.instance()
    note = panel.panel.findChild(_WrappedCaptionNote)
    assert note is not None
    # Reproduce wide-to-narrow form history instead of testing only first show.
    for locale in ('en', 'zh-CN', 'en', 'zh-CN'):
        set_locale(locale)
        for width in (650, 330, 510, 280, 650):
            note.resize(width, note.height())
            note._fit_wrapped_height()
            app.processEvents()
            flags = int(note.alignment()) | int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs)
            needed = note.fontMetrics().boundingRect(QRect(0, 0, note.contentsRect().width(), 100000), flags, note.text()).height()
            assert note.minimumHeight() >= needed

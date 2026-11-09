"""Read-only directory tests; no cloud service or synthesis is invoked."""
from dataclasses import asdict
import socket
import pytest
from test_ui import app, window
from lumen.i18n import set_locale
from lumen.ui_online_voices import OnlineVoiceDirectory, open_online_voice_directory


@pytest.mark.parametrize('mode', ['simple', 'professional'])
@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_directory_is_offline_read_only_and_generation_disabled(window, app, monkeypatch, locale, mode):
    set_locale(locale)
    window.set_workspace_mode(mode, persist=False)
    def forbidden(*args, **kwargs):
        raise AssertionError('Directory must never access the network')
    monkeypatch.setattr(socket, 'socket', forbidden)
    before = asdict(window.project); revision = window.revision
    dialog = open_online_voice_directory(window)
    assert open_online_voice_directory(window) is dialog
    for index in range(dialog.voices.count()):
        dialog.voices.setCurrentIndex(index); app.processEvents()
        assert not dialog.generate.isEnabled()
        assert 'https://' in dialog.details.text()
        assert dialog.details.height() >= dialog.details.heightForWidth(dialog.details.width())
    assert asdict(window.project) == before and window.revision == revision
    dialog.close_button.click(); app.processEvents()
    assert window._online_voice_directory is None
    reopened = open_online_voice_directory(window)
    assert reopened is not dialog and not reopened.generate.isEnabled()
    reopened.reject(); app.processEvents(); set_locale('en')


def test_locale_switch_preserves_catalog_selection_and_status(app):
    set_locale('en'); dialog = OnlineVoiceDirectory()
    dialog.voices.setCurrentIndex(dialog.voices.count()-1)
    key = dialog.voices.currentData()
    set_locale('zh-CN'); app.processEvents()
    assert dialog.voices.currentData() == key
    assert '未配置' in dialog.status.text()
    assert '尚未接通' in dialog.windowTitle()
    assert not dialog.generate.isEnabled()
    dialog.close(); set_locale('en')

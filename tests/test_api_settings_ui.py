"""Real Qt checks for staged settings; all transport is intentionally absent."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication, QLineEdit
from PySide6.QtCore import QTimer

from lumen.api_settings import ApiSettingsSession
from lumen.i18n import get_locale, set_locale
from lumen.ui_api_settings import ApiSettingsDialog, open_api_settings


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(app):
    d = ApiSettingsDialog()
    yield d
    d.reject(); d.deleteLater(); app.processEvents()


def fill(d, *, key='synthetic-test-key'):
    d.fields['provider_name'].setText('My private provider')
    d.fields['base_url'].setText('https://custom.example:8443/service/v1/')
    d.fields['api_key'].setText(key)
    d.fields['model'].setText('org/custom-model')


def test_three_steps_clear_labels_no_automatic_connection(dialog):
    d = dialog
    assert list(d.step_buttons) == [1, 2, 3]
    assert d.step_bodies[1].isHidden() and d.step_bodies[2].isHidden()
    d.step_arrows[1].click()
    assert not d.step_bodies[1].isHidden()
    assert not d.step_buttons[2].isEnabled()
    assert not d.step_buttons[3].isEnabled()
    assert not d.apply_button.isEnabled() and not d.test_button.isEnabled()
    assert d.advanced_body.isHidden()
    assert d.fields['api_key'].echoMode() == QLineEdit.EchoMode.Password
    assert d.fields['api_key'].text() == ''
    d.fields['base_url'].setText('https://custom.example/v1')
    assert d.step_buttons[2].isEnabled()
    assert not d.apply_button.isEnabled() and not d.test_button.isEnabled()
    d.next_step()
    assert d.step_bodies[1].isHidden() and not d.step_bodies[2].isHidden()
    d.fields['model'].setText('custom-model')
    assert d.apply_button.isEnabled() and not d.test_button.isEnabled()


def test_only_explicit_apply_commits_once_and_cancel_preserves_session(app):
    prior = ApiSettingsSession('Prior', 'https://prior.example/v1', 'prior-model', 'prior-synthetic-key')
    d = ApiSettingsDialog(prior); emissions = []
    d.applied.connect(emissions.append)
    fill(d)
    assert d.check_fields()
    assert d.session is prior and not emissions
    d.reject()
    assert d.session is prior and prior.api_key == 'prior-synthetic-key'
    assert d.fields['api_key'].text() == ''
    d = ApiSettingsDialog(prior); d.applied.connect(emissions.append)
    fill(d); d.apply_settings(); d.apply_settings()
    assert len(emissions) == 1 and d.session is not prior
    assert d.session.base_url == 'https://custom.example:8443/service/v1'
    assert d.session.api_key == 'synthetic-test-key'
    assert d.fields['api_key'].text() == ''
    d2 = ApiSettingsDialog(d.session); d2.applied.connect(emissions.append)
    d2.apply_settings()
    assert len(emissions) == 1 and d2.session is d.session


def test_invalid_apply_and_local_error_never_reveal_key(dialog):
    d = dialog; fill(d, key='test secret')
    prior = d.session
    assert not d.apply_button.isEnabled() and not d.check_fields()
    d.apply_settings()
    assert d.session is prior and not d._finished
    assert 'test secret' not in d.status.text()
    d.fields['api_key'].setText('test-secret')
    d.fields['base_url'].setText('https://user:test-secret@example.com/v1')
    d.apply_settings()
    assert 'test-secret' not in d.status.text() and 'user:' not in d.status.text()
    assert d.session is prior


def test_check_feedback_invalidates_on_change_and_no_fake_success(dialog):
    locale = get_locale()
    try:
        set_locale('en'); d = dialog; fill(d)
        assert d.check_fields()
        assert 'No connection was made' in d.status.text()
        assert 'unverified' in d.status.text()
        for _ in range(3):
            assert d.check_fields()
        d.fields['model'].setText('different-model')
        assert 'Fields changed' in d.status.text()
        assert not d.test_button.isEnabled()
        d.fields['base_url'].setText('https://example.com/v1/models')
        d.apply_settings()
        assert not d.apply_button.isEnabled()
        assert '/models' in d.status.text()
        assert not d._finished
    finally:
        set_locale(locale)


def test_live_locale_switch_preserves_input_secret_and_checked_status(dialog, app):
    locale = get_locale()
    try:
        d = dialog; set_locale('en'); fill(d)
        d.next_step(); d.check_fields(); before = d.values()
        d.advanced.setChecked(True)
        set_locale('zh-CN'); app.processEvents()
        assert d.values() == before
        assert d.windowTitle() == '自定义模型 API'
        assert d.fields['model'].accessibleName() == '模型名称（Model ID）'
        assert '尚未验证' in d.status.text()
        assert not d.step_bodies[2].isHidden()
        assert not d.advanced_body.isHidden()
        set_locale('en'); app.processEvents()
        assert d.windowTitle() == 'Custom model API' and d.values() == before
        assert d.fields['api_key'].echoMode() == QLineEdit.EchoMode.Password
    finally:
        set_locale(locale)


def test_show_key_is_explicit_and_resets_on_cancel(dialog):
    d = dialog; fill(d)
    d.show_key.setChecked(True)
    assert d.fields['api_key'].echoMode() == QLineEdit.EchoMode.Normal
    d.reject()
    assert d.fields['api_key'].echoMode() == QLineEdit.EchoMode.Password
    assert d.fields['api_key'].text() == '' and d.session.api_key == ''


def test_no_file_writes_no_socket_and_no_environment_key(dialog, tmp_path, monkeypatch):
    import socket
    def forbidden(*a, **k): raise AssertionError('No network or persistence is allowed')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-be-loaded')
    monkeypatch.chdir(tmp_path)
    d = dialog
    assert not d.fields['api_key'].text()
    fill(d)
    d.check_fields(); d.apply_settings()
    assert list(tmp_path.iterdir()) == []
    assert 'must-not-be-loaded' not in repr(d.session)


def test_open_helper_cancel_returns_exact_prior_identity(app):
    prior = ApiSettingsSession('Previous', 'https://example.com/v1', 'model')
    def cancel():
        d = QApplication.activeModalWidget()
        assert isinstance(d, ApiSettingsDialog)
        fill(d); d.reject()
    QTimer.singleShot(0, cancel)
    assert open_api_settings(None, prior) is prior


def test_open_helper_releases_parent_owned_dialog_and_old_secret_references(app):
    from PySide6.QtWidgets import QWidget
    from PySide6.QtCore import QCoreApplication, QEvent
    parent = QWidget()
    prior = ApiSettingsSession('Previous', 'https://example.com/v1', 'model', 'old-synthetic-key')
    opened = []
    def apply():
        d = QApplication.activeModalWidget()
        opened.append(d)
        fill(d); d.apply_settings()
    QTimer.singleShot(0, apply)
    result = open_api_settings(parent, prior)
    assert result.api_key == 'synthetic-test-key'
    assert opened[0].original_session.api_key == ''
    assert opened[0].session.api_key == ''
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not parent.findChildren(ApiSettingsDialog)
    parent.deleteLater()


@pytest.mark.parametrize('locale', ['zh-CN', 'en'])
def test_step_title_and_chevron_have_visible_keyboard_focus(app, locale):
    from lumen.ui_style import STYLE
    previous = get_locale()
    try:
        set_locale(locale)
        d = ApiSettingsDialog(); d.setStyleSheet(STYLE); d.show(); app.processEvents()
        for button in (d.step_buttons[1], d.step_arrows[1]):
            d.cancel_button.setFocus(); app.processEvents()
            unfocused = button.grab().toImage()
            button.setFocus(); app.processEvents()
            assert button.hasFocus()
            assert button.grab().toImage() != unfocused
        d.reject(); d.deleteLater()
    finally:
        set_locale(previous)


def test_initial_dialog_fits_1080_screen_at_150_percent_scale(app, monkeypatch):
    from PySide6.QtCore import QRect
    class Screen:
        def availableGeometry(self): return QRect(0, 0, 1280, 720)
    monkeypatch.setattr(ApiSettingsDialog, 'screen', lambda self: Screen())
    d = ApiSettingsDialog()
    assert d.width() <= 1240 and d.height() <= 680
    assert d.minimumHeight() <= d.height()
    d.reject(); d.deleteLater()

"""A configured screenshot font fails closed before any fallback or capture."""
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from scripts import provision_test_font as supply
from scripts.qt_layout_font import prepare_layout_font


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def test_invalid_configured_fixture_never_uses_installed_fonts(app, monkeypatch):
    monkeypatch.setenv('LUMEN_TEST_CJK_FONT_DIR', 'configured-fixture')
    def rejected(_):
        raise supply.ProvisionError('fixture digest mismatch')
    monkeypatch.setattr(supply, 'verify_fixture', rejected)
    monkeypatch.setattr(QFontDatabase, 'addApplicationFont', lambda *_: pytest.fail('must not register unverified bytes'))
    monkeypatch.setattr(QFontDatabase, 'families', lambda *_: pytest.fail('must not fall back'))
    with pytest.raises(supply.ProvisionError, match='digest mismatch'):
        prepare_layout_font(app)


@pytest.mark.parametrize('registered,families,error', [
    (-1, [], 'could not register'), (17, ['Wrong Family'], 'family mismatch')])
def test_registration_and_identity_fail_closed(app, monkeypatch, registered, families, error):
    monkeypatch.setenv('LUMEN_TEST_CJK_FONT_DIR', 'configured-fixture')
    monkeypatch.setattr(supply, 'verify_fixture', lambda _: {'font_file': 'verified-font'})
    monkeypatch.setattr(QFontDatabase, 'addApplicationFont', lambda _: registered)
    monkeypatch.setattr(QFontDatabase, 'applicationFontFamilies', lambda _: families)
    monkeypatch.setattr(QFontDatabase, 'families', lambda *_: pytest.fail('must not fall back'))
    with pytest.raises(AssertionError, match=error):
        prepare_layout_font(app)

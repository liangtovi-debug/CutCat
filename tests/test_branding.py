"""Display-only CutCat branding and actual local Chinese glyph rendering."""
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QLocale, QSettings
from PySide6.QtGui import QFontDatabase, QTextLayout
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from lumen import ui
from lumen.branding import UI_LANGUAGE, display_name


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def cjk_font(app):
    directory = os.environ.get('LUMEN_TEST_CJK_FONT_DIR')
    if directory is None:
        pytest.skip('Provision the explicit local CJK test fixture to verify glyph rendering')
    from scripts.provision_test_font import verify_fixture
    # A configured but invalid font is a failure, never a silent skip.
    verified = verify_fixture(directory)
    font_id = QFontDatabase.addApplicationFont(verified['font_file'])
    assert font_id >= 0, 'Qt could not load the verified CJK fixture'
    yield
    assert QFontDatabase.removeApplicationFont(font_id)


def test_header_shapes_and_renders_real_chinese_glyphs(app, cjk_font):
    label = ui._make_brand_label('zh-CN')
    assert label.text() == display_name('zh-CN') == '剪辑猫'
    assert ui._font_has_brand_glyphs(label.font())
    layout = QTextLayout('剪辑猫', label.font())
    layout.beginLayout()
    layout.createLine().setLineWidth(1000)
    layout.endLayout()
    indexes = []
    for run in layout.glyphRuns():
        raw = run.rawFont()
        for index in run.glyphIndexes():
            indexes.append(index)
            assert index != 0, 'Missing-glyph box is not Chinese text'
            mask = raw.alphaMapForGlyph(index)
            assert not mask.isNull()
            assert any(bytes(mask.constBits())), 'Glyph must contain visible pixels'
    assert len(indexes) == 3
    label.resize(label.sizeHint())
    label.show()
    app.processEvents()
    chinese = label.grab().toImage()
    label.setText('   ')
    app.processEvents()
    assert chinese != label.grab().toImage(), 'Chinese name must affect visible header pixels'
    label.close()


def test_missing_local_glyphs_have_explicit_english_fallback(app, monkeypatch):
    monkeypatch.setattr(ui, '_font_has_brand_glyphs', lambda font: False)
    label = ui._make_brand_label('zh-CN')
    assert label.text() == 'CutCat'
    assert 'install a local font' in label.toolTip()
    assert 'No font is bundled' in label.accessibleDescription()
    label.close()


@pytest.mark.parametrize('os_locale', ['en_US', 'zh_CN', 'zh_TW'])
def test_window_brand_survives_refresh_and_about(app, tmp_path, monkeypatch, os_locale):
    original_locale = QLocale()
    QLocale.setDefault(QLocale(os_locale))
    settings = QSettings(str(tmp_path / 'workspace.ini'), QSettings.Format.IniFormat)
    window = ui.MainWindow(workspace_settings=settings)
    try:
        assert window.windowTitle().endswith(' — CutCat')
        header = window.findChild(QLabel, 'brandTitle')
        window.show()
        app.processEvents()
        assert header.text() == 'CutCat'
        assert not header.toolTip()
        window.project_path = Path('Existing.lumen')
        window.dirty = True
        window.refresh()
        assert window.windowTitle() == '● Existing.lumen — CutCat'
        messages = []
        from lumen.ui_help import HelpDialog
        monkeypatch.setattr(HelpDialog, 'exec', lambda dialog: messages.append((dialog.parent(), dialog.windowTitle(), dialog.body.toPlainText())))
        window.show_help()
        assert messages[0][1] == 'CutCat · About'
        assert messages[0][2].startswith('CutCat is an independent desktop editor prototype.')
    finally:
        QLocale.setDefault(original_locale)
        window.dirty = False
        window.closing = True
        window.close()
        app.processEvents()


def test_application_display_name_preserves_settings_identity():
    # QApplication's applicationName contributes to default QSettings paths.
    # The brand must change its display name, never its persisted identity.
    from lumen import main
    source = Path(main.__file__).read_text(encoding='utf-8')
    assert "app.setApplicationName('Lumen Edit')" in source
    assert "app.setApplicationDisplayName(display_name())" in source
    assert "app.setOrganizationName('LumenEditPrototype')" in source


def test_actual_language_brand_mapping():
    assert UI_LANGUAGE == 'zh-CN'
    assert display_name() == 'CutCat'
    for language in ('en', 'en-US', 'en_GB'):
        assert display_name(language) == 'CutCat'
    for language in ('zh', 'zh-CN', 'zh_TW'):
        assert display_name(language) == '剪辑猫'


def test_english_header_does_not_require_chinese_font(app, monkeypatch):
    def unexpected_font_check(font):
        raise AssertionError('English brand must not depend on Chinese glyph coverage')
    monkeypatch.setattr(ui, '_font_has_brand_glyphs', unexpected_font_check)
    label = ui._make_brand_label()
    assert label.text() == 'CutCat'
    assert not label.toolTip()
    label.close()

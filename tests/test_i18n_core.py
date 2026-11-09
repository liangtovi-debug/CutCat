"""Language is an app preference, independent of project/editor state."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QSettings, QLocale
from PySide6.QtWidgets import QApplication
from lumen.ui import MainWindow
from lumen.i18n import get_locale, set_locale, tr
from lumen.branding import display_name
from test_ui import populate
from test_workspace_ui import state

@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])

@pytest.fixture
def window(app, tmp_path):
    settings = QSettings(str(tmp_path / 'language.ini'), QSettings.Format.IniFormat)
    w = MainWindow(workspace_settings=settings, ui_language='zh-CN')
    yield w
    w.dirty = False
    w.closing = True
    w.close()
    app.processEvents()


def test_live_switch_preserves_staged_values_and_editor(window, tmp_path, app):
    w = window
    clip = populate(w, tmp_path)
    w.select_clip(clip.id)
    w.set_workspace_mode('professional')
    w.caption_text.setPlainText('Keep this staged 中文 text exactly')
    w.ai_query.setText('An untouched user question')
    w.start_spin.setValue(123)
    w.color_tabs.setCurrentIndex(2)
    w.curve_editor.selected = 1
    w.editor.update_clip(clip.id, gain_db=-3)
    before = state(w)
    identities = (w.editor, w.player, w.video, w.timeline, w.caption_text, w.bin.currentItem())
    for locale, label in [('en', 'Export film'), ('zh-CN', '导出影片'), ('en', 'Export film')]:
        w.set_ui_language(locale, persist=False)
        app.processEvents()
        assert state(w) == before
        assert identities == (w.editor, w.player, w.video, w.timeline, w.caption_text, w.bin.currentItem())
        assert w.caption_text.toPlainText() == 'Keep this staged 中文 text exactly'
        assert w.ai_query.text() == 'An untouched user question'
        assert w.start_spin.value() == 123
        assert w.color_tabs.currentIndex() == 2
        assert w.curve_editor.selected == 1
        assert w.export_button.text() == label
        assert w.ui_language == locale
    w.set_ui_language('zh-CN', persist=False)
    assert w.menuBar().actions()[0].text() == '文件'
    assert w.workspace_buttons['professional'].text() == '专业'
    assert w.side_tabs.tabText(0) == '片段'


def test_language_preference_restores_separately_from_project(window, app):
    w = window
    original = w.project.to_dict()
    w.set_ui_language('en')
    assert w.workspace_settings.value('language') == 'en'
    assert w.project.to_dict() == original
    other = MainWindow(workspace_settings=w.workspace_settings)
    try:
        assert other.ui_language == 'en'
        assert other.export_button.text() == 'Export film'
    finally:
        other.closing = True
        other.close()


def test_brand_uses_ui_language_not_os(window):
    original = QLocale()
    try:
        QLocale.setDefault(QLocale('en_US'))
        window.set_ui_language('zh-CN', persist=False)
        assert display_name() == '剪辑猫'
        assert window.windowTitle().endswith(' — 剪辑猫')
        QLocale.setDefault(QLocale('zh_CN'))
        window.set_ui_language('en', persist=False)
        assert display_name() == 'CutCat'
    finally:
        QLocale.setDefault(original)


def test_dynamic_ui_templates_keep_user_names(window):
    name = 'Save · 我的素材.mov'
    text = tr('{name}\nInput: {space}', name=name, space=tr('unknown; assumed Rec.709'))
    assert str(text) == name + '\n输入：未知；按 Rec.709 处理'
    set_locale('en')
    assert text.render() == name + '\nInput: unknown; assumed Rec.709'

import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from lumen.i18n import get_locale, register_catalog, set_locale, tr
from lumen.i18n_widgets import (QLabel, QPushButton, QComboBox, QTabWidget,
    QWidget, QFormLayout, QLineEdit, QPlainTextEdit, QAction, QMainWindow,
    QTableWidget, QListWidget, QListWidgetItem)


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    old = get_locale()
    register_catalog('zh-CN', {'Test greeting': '测试问候', 'Test world': '世界',
                             'Test {name}': '测试{name}'})
    set_locale('en')
    yield app
    set_locale(old)


def test_properties_and_user_text_remain_distinct(app):
    label = QLabel(tr('Test greeting'))
    raw = QLabel('Test greeting')
    button = QPushButton(tr('Test greeting'))
    button.setToolTip(tr('Test {name}', name='My Project'))
    edit = QLineEdit('Test greeting')
    edit.setPlaceholderText(tr('Test greeting'))
    plain = QPlainTextEdit('Test greeting')
    action = QAction(tr('Test greeting'))
    set_locale('zh-CN')
    assert label.text() == button.text() == action.text() == '测试问候'
    assert button.toolTip() == '测试My Project'
    assert raw.text() == edit.text() == plain.toPlainText() == 'Test greeting'
    assert edit.placeholderText() == '测试问候'
    label.setText('Test greeting')
    set_locale('en'); set_locale('zh-CN')
    assert label.text() == 'Test greeting'


def test_combo_keeps_index_data_and_suppresses_edit_signals(app):
    combo = QComboBox()
    combo.addItem(tr('Test greeting'), 'canonical')
    combo.addItem('Test greeting', {'project': 'Test greeting'})
    combo.setCurrentIndex(1)
    signals = []
    combo.currentIndexChanged.connect(lambda *_: signals.append('index'))
    combo.currentTextChanged.connect(lambda *_: signals.append('text'))
    set_locale('zh-CN')
    assert combo.itemText(0) == '测试问候'
    assert combo.currentText() == 'Test greeting'
    assert combo.currentIndex() == 1
    assert combo.currentData() == {'project': 'Test greeting'}
    assert combo.itemData(0) == 'canonical'
    assert signals == []
    combo.insertItem(0, 'raw'); combo.removeItem(1)
    set_locale('en'); set_locale('zh-CN')
    assert combo.itemText(1) == 'Test greeting'
    combo.clear(); combo.addItem('raw'); set_locale('en')
    assert combo.currentText() == 'raw'


def test_tabs_forms_nested_values_and_menu_status(app):
    tabs = QTabWidget()
    page = QWidget()
    tabs.addTab(page, tr('Test greeting'))
    tabs.addTab(QWidget(), 'Test greeting')
    tabs.setCurrentIndex(1)
    form = QFormLayout(page)
    field = QLineEdit('Test greeting')
    form.addRow(tr('Test greeting'), field)
    nested = QLabel(tr('Test {name}', name=tr('Test world')))
    window = QMainWindow()
    menu = window.menuBar().addMenu(tr('Test greeting'))
    window.statusBar().showMessage(tr('Test greeting'))
    set_locale('zh-CN')
    assert tabs.widget(0) is page and tabs.currentIndex() == 1
    assert tabs.tabText(0) == '测试问候' and tabs.tabText(1) == 'Test greeting'
    assert form.labelForField(field).text() == '测试问候'
    assert nested.text() == '测试世界'
    assert menu.title() == window.statusBar().currentMessage() == '测试问候'
    assert field.text() == 'Test greeting'


def test_table_headers_and_list_items(app):
    table = QTableWidget(1, 2)
    table.setHorizontalHeaderLabels([tr('Test greeting'), 'Test greeting'])
    listing = QListWidget()
    item = QListWidgetItem(tr('Test greeting'), listing)
    item.setData(Qt.ItemDataRole.UserRole, 'Test greeting')
    raw = QListWidgetItem('Test greeting', listing)
    set_locale('zh-CN')
    assert item.text() == table.horizontalHeaderItem(0).text() == '测试问候'
    assert raw.text() == table.horizontalHeaderItem(1).text() == 'Test greeting'
    assert item.data(Qt.ItemDataRole.UserRole) == 'Test greeting'
    listing.clear()
    set_locale('en')  # Deleted C++ items are safely ignored.


def test_user_edits_replace_explicit_initial_text_binding(app):
    from PySide6.QtTest import QTest
    edit = QLineEdit(tr('Test greeting'))
    edit.selectAll()
    QTest.keyClicks(edit, 'my custom title')
    plain = QPlainTextEdit(tr('Test greeting'))
    plain.selectAll()
    plain.insertPlainText('my custom captions')
    set_locale('zh-CN')
    assert edit.text() == 'my custom title'
    assert plain.toPlainText() == 'my custom captions'


def test_qt_standard_dialog_controls_follow_locale(app):
    from PySide6.QtWidgets import QMessageBox
    set_locale('zh-CN')
    box = QMessageBox()
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    assert 'Yes' not in box.button(QMessageBox.StandardButton.Yes).text()
    set_locale('en')
    box = QMessageBox()
    box.setStandardButtons(QMessageBox.StandardButton.Yes)
    assert 'Yes' in box.button(QMessageBox.StandardButton.Yes).text()


@pytest.mark.parametrize('widget_class', [QLabel, QLineEdit, QPlainTextEdit])
def test_clear_removes_initial_text_binding(app, widget_class):
    widget = widget_class(tr('Test greeting'))
    widget.clear()
    set_locale('zh-CN')
    set_locale('en')
    text = widget.toPlainText() if isinstance(widget, QPlainTextEdit) else widget.text()
    assert text == ''


def test_repeated_language_changes_release_removed_qt_translators(app):
    from PySide6.QtCore import QCoreApplication, QEvent, QTranslator
    for _ in range(8):
        set_locale('zh-CN')
        set_locale('en')
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert app.findChildren(QTranslator) == []
    set_locale('zh-CN')
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert len(app.findChildren(QTranslator)) == 1

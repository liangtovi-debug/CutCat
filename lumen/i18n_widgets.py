"""Qt drop-in widgets binding only explicitly marked ``tr(...)`` values.

No UI recreation, project mutation, or heuristic translation of user content.
"""
from __future__ import annotations

from PySide6 import QtWidgets as W, QtGui as G
from PySide6.QtCore import QSignalBlocker
from .i18n import TranslatedText, locale_events

_PROPERTIES = ('setText', 'setTitle', 'setWindowTitle', 'setToolTip', 'setStatusTip',
               'setWhatsThis', 'setPlaceholderText', 'setAccessibleName',
               'setAccessibleDescription', 'setPlainText')


class _Bindings:
    def _initialize_bindings(self):
        if not hasattr(self, '_translation_bindings'):
            self._translation_bindings = {}
            locale_events.changed.connect(self._refresh_translations)

    def _refresh_translations(self, _locale=None):
        blocker = QSignalBlocker(self)
        for setter, value in self._translation_bindings.items():
            getattr(self._translation_base, setter)(self, value.render())
        self._refresh_items()
        del blocker

    def _refresh_items(self):
        pass


def _setter(name, base):
    def set_value(self, value):
        self._initialize_bindings()
        if isinstance(value, TranslatedText):
            self._translation_bindings[name] = value
            value = value.render()
        else:
            self._translation_bindings.pop(name, None)
        self._translation_setting = True
        try:
            return getattr(base, name)(self, value)
        finally:
            self._translation_setting = False
    set_value.__name__ = name
    return set_value


def _widget(base, text_setter=None):
    def __init__(self, *args, **kwargs):
        base.__init__(self, *args, **kwargs)
        self._initialize_bindings()
        if text_setter:
            for arg in args:
                if isinstance(arg, TranslatedText):
                    getattr(self, text_setter)(arg)
                    break
        for key, value in kwargs.items():
            setter = 'set' + key[0].upper() + key[1:]
            if isinstance(value, TranslatedText) and hasattr(self, setter):
                getattr(self, setter)(value)
        if isinstance(self, W.QLineEdit):
            self.textEdited.connect(lambda _text: self._translation_bindings.pop('setText', None))
        elif isinstance(self, (W.QPlainTextEdit, W.QTextEdit)):
            def edited():
                if not getattr(self, '_translation_setting', False):
                    self._translation_bindings.pop('setPlainText', None)
                    self._translation_bindings.pop('setText', None)
            self.textChanged.connect(edited)
    namespace = {'__init__': __init__, '_translation_base': base, '__module__': __name__}
    namespace.update({name: _setter(name, base) for name in _PROPERTIES if hasattr(base, name)})
    if text_setter and hasattr(base, 'clear'):
        def clear(self):
            self._translation_bindings.pop(text_setter, None)
            return base.clear(self)
        namespace['clear'] = clear
    return type(base.__name__, (_Bindings, base), namespace)


for _name, _text_setter in {
    'QWidget': None, 'QMainWindow': None, 'QLabel': 'setText',
    'QPushButton': 'setText', 'QToolButton': None, 'QCheckBox': 'setText',
    'QRadioButton': 'setText', 'QGroupBox': 'setTitle', 'QMenu': 'setTitle',
    'QLineEdit': 'setText', 'QPlainTextEdit': 'setPlainText', 'QTextEdit': 'setText',
    'QDialog': None, 'QSpinBox': None, 'QDoubleSpinBox': None,
    'QSlider': None, 'QProgressBar': None, 'QToolBar': 'setWindowTitle',
    'QScrollArea': None, 'QListWidget': None,
}.items():
    globals()[_name] = _widget(getattr(W, _name), _text_setter)

QAction = _widget(G.QAction, 'setText')


class QComboBox(_widget(W.QComboBox)):
    def __init__(self, *args, **kwargs):
        self._item_translations = {}
        super().__init__(*args, **kwargs)

    def addItem(self, *args, **kwargs):
        self.insertItem(self.count(), *args, **kwargs)

    def addItems(self, texts):
        for text in texts:
            self.addItem(text)

    def insertItem(self, index, *args, **kwargs):
        index = max(0, min(index, self.count()))
        self._item_translations = {i + (i >= index): value for i, value in self._item_translations.items()}
        W.QComboBox.insertItem(self, index, *args, **kwargs)
        value = next((arg for arg in args if isinstance(arg, TranslatedText)), kwargs.get('text'))
        if isinstance(value, TranslatedText):
            self._item_translations[index] = value

    def insertItems(self, index, texts):
        for offset, text in enumerate(texts):
            self.insertItem(index + offset, text)

    def setItemText(self, index, text):
        if isinstance(text, TranslatedText):
            self._item_translations[index] = text
        else:
            self._item_translations.pop(index, None)
        W.QComboBox.setItemText(self, index, text.render() if isinstance(text, TranslatedText) else text)

    def removeItem(self, index):
        W.QComboBox.removeItem(self, index)
        self._item_translations = {i - (i > index): value for i, value in self._item_translations.items() if i != index}

    def clear(self):
        self._item_translations.clear()
        W.QComboBox.clear(self)

    def _refresh_items(self):
        blocker = QSignalBlocker(self.model())
        for index, value in self._item_translations.items():
            W.QComboBox.setItemText(self, index, value.render())


class QTabWidget(_widget(W.QTabWidget)):
    def __init__(self, *args, **kwargs):
        self._tab_translations = {}
        super().__init__(*args, **kwargs)

    def addTab(self, widget, *args):
        return self.insertTab(self.count(), widget, *args)

    def insertTab(self, index, widget, *args):
        result = W.QTabWidget.insertTab(self, index, widget, *args)
        if args and isinstance(args[-1], TranslatedText):
            self._tab_translations[widget] = args[-1]
        return result

    def setTabText(self, index, text):
        widget = self.widget(index)
        if isinstance(text, TranslatedText):
            self._tab_translations[widget] = text
        else:
            self._tab_translations.pop(widget, None)
        W.QTabWidget.setTabText(self, index, text.render() if isinstance(text, TranslatedText) else text)

    def removeTab(self, index):
        self._tab_translations.pop(self.widget(index), None)
        W.QTabWidget.removeTab(self, index)

    def clear(self):
        self._tab_translations.clear()
        W.QTabWidget.clear(self)

    def _refresh_items(self):
        for widget, value in self._tab_translations.items():
            index = self.indexOf(widget)
            if index >= 0:
                W.QTabWidget.setTabText(self, index, value.render())


class QFormLayout(W.QFormLayout):
    def _row_args(self, args):
        if len(args) == 2 and isinstance(args[0], TranslatedText):
            label = QLabel(args[0])
            if isinstance(args[1], W.QWidget):
                label.setBuddy(args[1])
            return (label, args[1])
        return args

    def addRow(self, *args):
        return super().addRow(*self._row_args(args))

    def insertRow(self, row, *args):
        return super().insertRow(row, *self._row_args(args))


class QStatusBar(_widget(W.QStatusBar)):
    def __init__(self, *args, **kwargs):
        self._message_translation = None
        self._message_deadline = None
        super().__init__(*args, **kwargs)
        self.messageChanged.connect(self._message_changed)

    def _message_changed(self, message):
        if not message:
            self._message_translation = None

    def showMessage(self, message, timeout=0):
        import time
        self._message_deadline = time.monotonic() + timeout / 1000 if timeout > 0 else None
        self._message_translation = message if isinstance(message, TranslatedText) else None
        W.QStatusBar.showMessage(self, str(message), timeout)

    def clearMessage(self):
        self._message_translation = None
        W.QStatusBar.clearMessage(self)

    def _refresh_items(self):
        if self._message_translation is not None:
            import time
            remaining = (max(1, int((self._message_deadline - time.monotonic()) * 1000))
                         if self._message_deadline is not None else 0)
            W.QStatusBar.showMessage(self, self._message_translation.render(), remaining)


class QListWidgetItem(W.QListWidgetItem):
    def __init__(self, *args, **kwargs):
        import weakref
        import shiboken6
        super().__init__(*args, **kwargs)
        self._text_translation = next((arg for arg in args if isinstance(arg, TranslatedText)), None)
        self._tip_translation = None
        ref = weakref.ref(self)
        def refresh(_locale):
            item = ref()
            if item is None or not shiboken6.isValid(item):
                locale_events.changed.disconnect(refresh)
                return
            view = item.listWidget()
            blockers = [QSignalBlocker(view), QSignalBlocker(view.model())] if view is not None else []
            if item._text_translation is not None:
                W.QListWidgetItem.setText(item, item._text_translation.render())
            if item._tip_translation is not None:
                W.QListWidgetItem.setToolTip(item, item._tip_translation.render())
        locale_events.changed.connect(refresh)

    def setText(self, text):
        self._text_translation = text if isinstance(text, TranslatedText) else None
        W.QListWidgetItem.setText(self, text.render() if isinstance(text, TranslatedText) else text)

    def setToolTip(self, text):
        self._tip_translation = text if isinstance(text, TranslatedText) else None
        W.QListWidgetItem.setToolTip(self, text.render() if isinstance(text, TranslatedText) else text)


class QTableWidget(_widget(W.QTableWidget)):
    def __init__(self, *args, **kwargs):
        self._header_translations = {}
        super().__init__(*args, **kwargs)

    def setHorizontalHeaderLabels(self, labels):
        labels = list(labels)
        self._header_translations = {i: value for i, value in enumerate(labels)
                                     if isinstance(value, TranslatedText)}
        W.QTableWidget.setHorizontalHeaderLabels(self, labels)

    def _refresh_items(self):
        blocker = QSignalBlocker(self.model())
        for index, value in self._header_translations.items():
            item = self.horizontalHeaderItem(index)
            if item is not None:
                item.setText(value.render())


class QMenuBar(_widget(W.QMenuBar)):
    def addMenu(self, *args):
        if args and isinstance(args[-1], TranslatedText):
            menu = QMenu(args[-1], self)
            if len(args) == 2:
                menu.setIcon(args[0])
            W.QMenuBar.addMenu(self, menu)
            return menu
        return W.QMenuBar.addMenu(self, *args)


_BaseMainWindow = QMainWindow
class QMainWindow(_BaseMainWindow):
    def statusBar(self):
        # Preserve an explicitly supplied status bar; otherwise create our own
        # before Qt lazily constructs its standard (unbound) status bar.
        bars = self.findChildren(W.QStatusBar)
        if not bars:
            self.setStatusBar(QStatusBar(self))
        return W.QMainWindow.statusBar(self)

    def menuBar(self):
        if self.menuWidget() is None:
            self.setMenuBar(QMenuBar(self))
        return W.QMainWindow.menuBar(self)


_BaseMenu = QMenu
class QMenu(_BaseMenu):
    def addAction(self, *args):
        text = next((arg for arg in args if isinstance(arg, TranslatedText)), None)
        action = W.QMenu.addAction(self, *args)
        if text is not None and action is not None:
            # Existing QAction overloads are already binding-aware.
            if isinstance(action, QAction):
                action.setText(text)
            else:
                import weakref
                import shiboken6
                ref = weakref.ref(action)
                def refresh(_locale):
                    target = ref()
                    if target is None or not shiboken6.isValid(target):
                        locale_events.changed.disconnect(refresh)
                    else:
                        blocker = QSignalBlocker(target)
                        target.setText(text.render())
                locale_events.changed.connect(refresh)
        return action

    def addMenu(self, *args):
        if args and isinstance(args[-1], TranslatedText):
            menu = QMenu(args[-1], self)
            if len(args) == 2:
                menu.setIcon(args[0])
            W.QMenu.addMenu(self, menu)
            return menu
        return W.QMenu.addMenu(self, *args)

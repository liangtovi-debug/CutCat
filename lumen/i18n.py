"""Explicit, state-preserving UI localization. Project strings are never scanned."""
from __future__ import annotations

import importlib
import pkgutil
from PySide6.QtCore import QObject, Signal

_LOCALE = 'zh-CN'
_CATALOGS: dict[str, dict[str, str]] = {'zh-CN': {}, 'en': {}}
_LOADED = False


class _LocaleEvents(QObject):
    changed = Signal(str)


locale_events = _LocaleEvents()


def _load_catalogs():
    global _LOADED
    if _LOADED:
        return
    from . import __path__
    names = {'i18n_catalog_core', 'i18n_catalog_captions',
             'i18n_catalog_panels', 'i18n_catalog_errors',
             'i18n_catalog_controlled_assistant', 'i18n_catalog_api_settings'}
    names.update(entry.name for entry in pkgutil.iter_modules(__path__)
                 if entry.name.startswith('i18n_catalog_'))
    for name in sorted(names):
        try:
            module = importlib.import_module(f'{__package__}.{name}')
        except ModuleNotFoundError as exc:
            if exc.name != f'{__package__}.{name}':
                raise
            # Partial source checkouts may not include every optional panel.
            import sys
            if getattr(sys, 'frozen', False):
                raise
            continue
        _CATALOGS['zh-CN'].update(getattr(module, 'ZH_CN', {}))
        _CATALOGS['en'].update(getattr(module, 'EN', {}))
    _LOADED = True


def register_catalog(locale: str, entries: dict[str, str]):
    """Register a catalog, useful to optional UI modules and isolated tests."""
    _CATALOGS[_normalize(locale)].update(entries)


def _normalize(locale: str) -> str:
    value = locale.replace('_', '-').lower()
    if value in ('zh', 'zh-cn', 'zh-hans'):
        return 'zh-CN'
    if value == 'en' or value.startswith('en-'):
        return 'en'
    raise ValueError(f'Unsupported UI locale: {locale}')


def get_locale() -> str:
    return _LOCALE


def set_locale(locale: str) -> None:
    """Change UI language only; the app owns settings persistence."""
    global _LOCALE
    normalized = _normalize(locale)
    _set_qt_locale(normalized)
    if normalized != _LOCALE:
        _LOCALE = normalized
        locale_events.changed.emit(normalized)


class TranslatedText(str):
    """String accepted by Qt, carrying its source for later in-place refresh."""
    def __new__(cls, source: str, values: dict | None = None):
        obj = super().__new__(cls, _render(source, values or {}))
        obj.source = source
        obj.values = dict(values or {})
        return obj

    def render(self) -> str:
        return _render(self.source, self.values)


def _render(source: str, values: dict) -> str:
    _load_catalogs()
    template = _CATALOGS[_LOCALE].get(source, source)
    if not values:
        return template
    rendered = {key: value.render() if isinstance(value, TranslatedText) else value
                for key, value in values.items()}
    try:
        return template.format(**rendered)
    except (KeyError, ValueError, IndexError):
        # A broken optional translation must not crash an editing session.
        return source.format(**rendered)


def tr(source: str, **values) -> TranslatedText:
    return TranslatedText(source, values)

# Convenient public signal alias for UI integrations.
locale_changed = locale_events.changed


def tr_error(error) -> str:
    from .i18n_errors import tr_error as translate_error
    return translate_error(error)


_QT_TRANSLATOR = None
_QT_TRANSLATOR_APP = None


def _set_qt_locale(locale: str) -> None:
    """Use Qt's shipped translations for native standard dialog controls."""
    global _QT_TRANSLATOR, _QT_TRANSLATOR_APP
    from PySide6.QtCore import QCoreApplication, QLibraryInfo, QTranslator
    app = QCoreApplication.instance()
    if app is None:
        return
    if _QT_TRANSLATOR is not None and _QT_TRANSLATOR_APP is app:
        app.removeTranslator(_QT_TRANSLATOR)
        _QT_TRANSLATOR.deleteLater()
    _QT_TRANSLATOR = None
    _QT_TRANSLATOR_APP = app
    if locale == 'zh-CN':
        translator = QTranslator(app)
        path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        from pathlib import Path
        bundled = str(Path(__file__).resolve().parent / 'assets' / 'translations')
        if (translator.load('qtbase_zh_CN', path)
                or translator.load('qtbase_zh_CN', bundled)):
            app.installTranslator(translator)
            _QT_TRANSLATOR = translator
        else:
            translator.deleteLater()


def qt_translation_loaded() -> bool:
    """Whether official Qt Chinese standard-control translations are active."""
    return _QT_TRANSLATOR is not None and not _QT_TRANSLATOR.isEmpty()

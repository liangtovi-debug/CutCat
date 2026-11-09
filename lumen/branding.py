"""Presentation names follow the active UI locale; application IDs stay stable."""
from .i18n import get_locale
UI_LANGUAGE = 'zh-CN'


def display_name(ui_language: str | None = None) -> str:
    language = (ui_language or get_locale()).replace('_', '-').split('-', 1)[0].lower()
    return '剪辑猫' if language == 'zh' else 'CutCat'

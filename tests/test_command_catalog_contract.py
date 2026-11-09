"""Reusable ownership/search contract for every present and future command.

Shortcut preferences deliberately search their dedicated catalogs, not only
runtime translations. Adding a globally translated command must not leave its
Chinese search alias or command/category ownership incomplete.
"""
import pytest

from lumen.i18n import _CATALOGS, _load_catalogs, set_locale, tr
from lumen.i18n_catalog_shortcuts import EN, ZH_CN
from lumen.shortcuts import COMMANDS
from test_shortcuts_dialog import app, controller, dialog, search


def test_every_command_label_and_category_has_consistent_owned_and_runtime_catalogs():
    _load_catalogs()
    assert set(EN) == set(ZH_CN)
    for command_id, command in COMMANDS.items():
        for field, source in (('label', command.label), ('category', command.category)):
            for locale, owned in (('en', EN), ('zh-CN', ZH_CN)):
                assert source in owned, (command_id, field, locale, source)
                assert source in _CATALOGS[locale], (command_id, field, locale, source)
                assert owned[source] == _CATALOGS[locale][source], (command_id, field, locale, source)
                assert isinstance(owned[source], str) and owned[source]


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_every_command_is_searchable_by_both_owned_languages_id_and_categories(dialog, locale):
    set_locale(locale)
    for command_id, command in COMMANDS.items():
        queries = (EN[command.label], ZH_CN[command.label], command_id,
                   EN[command.category], ZH_CN[command.category])
        for query in queries:
            assert command_id in search(dialog, query), (locale, command_id, query)
    assert search(dialog, 'Reattach source audio') == {'edit.reattach_audio'}
    assert search(dialog, '重新附加源音频') == {'edit.reattach_audio'}
    assert search(dialog, 'edit.reattach_audio') == {'edit.reattach_audio'}


def test_reattach_feature_and_shortcut_owners_share_the_same_exact_label():
    from lumen.i18n_catalog_source_audio_reattach import EN as FEATURE_EN, ZH_CN as FEATURE_ZH_CN
    source = COMMANDS['edit.reattach_audio'].label
    for locale, owned, feature in (('en', EN, FEATURE_EN), ('zh-CN', ZH_CN, FEATURE_ZH_CN)):
        set_locale(locale)
        assert owned[source] == feature[source] == tr(source)

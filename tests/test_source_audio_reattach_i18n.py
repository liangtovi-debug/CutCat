"""Audit dedicated UI/error catalogs before the broad regression gate."""
import ast
from pathlib import Path
import string

from lumen.i18n import set_locale, tr, tr_error
from lumen.i18n_catalog_source_audio_reattach import ZH_CN, EN, ERROR_ZH_CN
from lumen.i18n_catalog_errors import ZH_CN as ALL_ERRORS
from lumen.ui_source_audio_reattach import REATTACH_SCOPE, REATTACH_LIMITS, REATTACH_CHECK, REATTACH_SELECTION

ROOT = Path(__file__).resolve().parents[1]


def test_every_new_literal_error_and_strict_id_diagnostic_in_dedicated_catalog():
    messages = set()
    for filename in ('source_audio_reattach.py', 'model.py'):
        tree = ast.parse((ROOT / 'lumen' / filename).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                arg = node.exc.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if filename == 'source_audio_reattach.py' or 'reattach' in arg.value.lower(): messages.add(arg.value)
    messages.update(('Invalid reattach video clip id', 'Invalid reattach audio clip id'))
    assert messages <= ERROR_ZH_CN.keys()
    set_locale('zh-CN')
    for text in messages:
        assert ALL_ERRORS[text] == ERROR_ZH_CN[text] and tr_error(text) == ERROR_ZH_CN[text] != text


def test_new_ui_literals_and_parameters_translated_without_touching_ids():
    tree = ast.parse((ROOT / 'lumen/ui_source_audio_reattach.py').read_text())
    from lumen.i18n import _load_catalogs, _CATALOGS
    _load_catalogs()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('tr', 'action_button') and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str): assert arg.value in _CATALOGS['zh-CN']
    for text in (REATTACH_SCOPE, REATTACH_LIMITS, REATTACH_CHECK, REATTACH_SELECTION): assert text in ZH_CN
    fields = lambda value: {name for _, name, _, _ in string.Formatter().parse(value) if name is not None}
    for source, translated in ZH_CN.items():
        assert fields(source) == fields(translated) and EN[source] == source
    assert len(ERROR_ZH_CN) >= 28

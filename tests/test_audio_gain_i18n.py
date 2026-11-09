"""Dedicated strict error ownership and language-safe exact sample UI keys."""
import ast
from pathlib import Path
from string import Formatter

from lumen.i18n import _load_catalogs, _CATALOGS, set_locale, tr_error
from lumen.i18n_catalog_audio_gain import ZH_CN, EN, ERROR_ZH_CN
from lumen.i18n_catalog_errors import ZH_CN as ALL_ERRORS
from lumen.i18n_catalog_source_overwrite import ERROR_ZH_CN as SOURCE_ERRORS
from lumen.ui_audio_gain import (GAIN_SCOPE, GAIN_WORKFLOW, GAIN_HIDDEN, GAIN_SAMPLE_ERROR,
    GAIN_OFFSET_ERROR, GAIN_DUPLICATE_ERROR, GAIN_LIMIT_ERROR, GAIN_DURATION_ERROR)
from lumen.ui_audio_gain_timeline import AUDIO_GAIN_TIMELINE_HELP

ROOT=Path(__file__).resolve().parents[1]/'lumen'


def test_every_audio_gain_static_error_and_insert_alias_has_dedicated_owner():
    messages=set()
    for filename in ('audio_gain.py','model.py','source_overwrite.py','ui_audio_gain_timeline.py'):
        for node in ast.walk(ast.parse((ROOT/filename).read_text())):
            if isinstance(node,ast.Raise) and isinstance(node.exc,ast.Call) and node.exc.args:
                arg=node.exc.args[0]
                if isinstance(arg,ast.Constant) and isinstance(arg.value,str):
                    if 'audio_gain' in arg.value or 'audio gain' in arg.value.lower(): messages.add(arg.value)
    messages.update((GAIN_SAMPLE_ERROR,GAIN_OFFSET_ERROR,GAIN_DUPLICATE_ERROR,GAIN_LIMIT_ERROR,GAIN_DURATION_ERROR))
    messages.add('Source insert cannot cut clips with audio gain keyframes yet')
    assert messages <= ERROR_ZH_CN.keys()
    set_locale('zh-CN')
    for message in messages:
        assert ALL_ERRORS[message] == ERROR_ZH_CN[message] == tr_error(message) != message
    for message in ('Source overwrite cannot cut clips with audio gain keyframes yet',
                    'Source insert cannot cut clips with audio gain keyframes yet'):
        assert SOURCE_ERRORS[message] == ERROR_ZH_CN[message]


def test_ui_literals_constants_and_placeholders_have_bilingual_catalogs():
    _load_catalogs()
    for filename in ('ui_audio_gain.py','ui_audio_gain_timeline.py'):
        for node in ast.walk(ast.parse((ROOT/filename).read_text())):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in ('tr','action_button') and node.args:
                arg=node.args[0]
                if isinstance(arg,ast.Constant) and isinstance(arg.value,str): assert arg.value in _CATALOGS['zh-CN']
    for source in (GAIN_SCOPE,GAIN_WORKFLOW,GAIN_HIDDEN,AUDIO_GAIN_TIMELINE_HELP): assert source in ZH_CN
    fields=lambda text:{name for _,name,_,_ in Formatter().parse(text) if name is not None}
    for source,target in ZH_CN.items(): assert EN[source]==source and fields(source)==fields(target)

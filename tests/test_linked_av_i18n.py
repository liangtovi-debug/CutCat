"""All new linked diagnostics, UI literals and future command guards are explicit."""
import ast
from pathlib import Path
import string
from lumen.i18n import set_locale, tr_error
from lumen.i18n_catalog_linked_av import ZH_CN, EN, ERROR_ZH_CN
from lumen.i18n_catalog_errors import ZH_CN as ALL_ERRORS
from lumen.ui_linked_av_split import SPLIT_SCOPE, SPLIT_LIMITS, SPLIT_CHECK, SPLIT_SELECTION
from lumen.ui_linked_av import LINK_HELP, LINK_SELECTION, UNLINK_NOTE, LINK_TRIM_HELP, SLIP_SCOPE, SLIP_LIMITS, SLIP_CHECK
ROOT=Path(__file__).resolve().parents[1]


def test_all_linked_errors_have_exact_chinese_aliases():
    messages=set()
    for name in ('linked_av.py','linked_av_trim.py','linked_av_split.py','linked_av_roll.py','linked_av_audio_roll.py','model.py','source_audio_reattach.py','source_viewer.py'):
        for node in ast.walk(ast.parse((ROOT/'lumen'/name).read_text())):
            if isinstance(node,ast.Raise) and isinstance(node.exc,ast.Call) and node.exc.args:
                arg=node.exc.args[0]
                if isinstance(arg,ast.Constant) and isinstance(arg.value,str) and (name in ('linked_av.py','linked_av_trim.py','linked_av_split.py','linked_av_roll.py','linked_av_audio_roll.py') or 'A/V' in arg.value):
                    messages.add(arg.value)
    messages.update(('Invalid A/V link identity','Invalid linked A/V clip id','Invalid linked A/V split identity',
      'Linked A/V move cannot prove the local audio compiler contract',
      'Linked A/V move would change downstream audio at this sample-grid phase; choose another edit interval or project frame',
      'Linked A/V move would change relative audio sample alignment; choose another project frame'))
    assert messages <= ALL_ERRORS.keys()
    assert {text for text in messages if 'A/V' in text} <= ERROR_ZH_CN.keys()
    set_locale('zh-CN')
    for message in messages:
        assert ALL_ERRORS[message]==tr_error(message)!=message
        if message in ERROR_ZH_CN: assert ALL_ERRORS[message]==ERROR_ZH_CN[message]


def test_linked_ui_literals_and_format_fields_translate_exactly():
    from lumen.i18n import _load_catalogs,_CATALOGS
    _load_catalogs()
    for node in ast.walk(ast.parse((ROOT/'lumen/ui_linked_av.py').read_text())):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in ('tr','action_button') and node.args:
            arg=node.args[0]
            if isinstance(arg,ast.Constant) and isinstance(arg.value,str): assert arg.value in _CATALOGS['zh-CN']
    for node in ast.walk(ast.parse((ROOT/'lumen/ui.py').read_text())):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='tr' and node.args:
            arg=node.args[0]
            if isinstance(arg,ast.Constant) and isinstance(arg.value,str) and ('linked V+A' in arg.value or 'Linked V+A' in arg.value):
                assert arg.value in ZH_CN and arg.value in _CATALOGS['zh-CN']
    for text in (LINK_HELP,LINK_SELECTION,UNLINK_NOTE,LINK_TRIM_HELP,SLIP_SCOPE,SLIP_LIMITS,SLIP_CHECK,SPLIT_SCOPE,SPLIT_LIMITS,SPLIT_CHECK,SPLIT_SELECTION): assert text in ZH_CN
    fields=lambda value:{name for _,name,_,_ in string.Formatter().parse(value) if name is not None}
    for source,translated in ZH_CN.items():
        assert fields(source)==fields(translated) and EN[source]==source


def test_joint_split_ui_literals_translate_without_losing_format_fields():
    from lumen.i18n import _load_catalogs, _CATALOGS
    _load_catalogs()
    for node in ast.walk(ast.parse((ROOT/'lumen/ui_linked_av_split.py').read_text())):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in ('tr','action_button') and node.args:
            arg=node.args[0]
            if isinstance(arg,ast.Constant) and isinstance(arg.value,str):
                assert arg.value in _CATALOGS['zh-CN']


def test_joint_split_source_worker_errors_match_both_explicit_catalogs():
    from lumen.i18n_catalog_source_overwrite import ERROR_ZH_CN as source_errors
    from lumen.i18n_catalog_source_overwrite import ZH_CN as source_zh, EN as source_en
    module = ast.parse((ROOT / 'lumen/source_viewer.py').read_text())
    method = next(node for node in ast.walk(module)
                  if isinstance(node, ast.FunctionDef) and node.name == 'verify_linked_av_split_source')
    messages = set()
    for node in ast.walk(method):
        if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                and isinstance(node.exc.func, ast.Name) and node.exc.func.id == 'SourceViewerError'
                and node.exc.args):
            arg = node.exc.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                messages.add(arg.value)
            elif isinstance(arg, ast.JoinedStr):
                messages.add(''.join(part.value if isinstance(part, ast.Constant)
                    else '{' + ast.unparse(part.value) + '}' for part in arg.values))
    assert len(messages) == 8
    assert 'Linked A/V split exact source frames do not partition at the cut' in messages
    assert 'Cannot prove linked A/V split source timing: {exc}' in messages
    for message in messages:
        assert source_errors[message] == ERROR_ZH_CN[message]
        assert source_zh[message] == ZH_CN[message] == source_errors[message]
        assert source_en[message] == EN[message] == message


def test_affine_schema_and_dynamic_legacy_gates_have_explicit_translations():
    from lumen.model import AVLink, AV_LINK_AFFINE_ALIGNMENT, require_av_link_alignment
    import pytest
    link = AVLink('link', 'v', 'a', 'm', AV_LINK_AFFINE_ALIGNMENT)
    set_locale('zh-CN')
    for operation in ('Joint source slip', 'Joint edge trim', 'Joint split', 'Joint roll'):
        with pytest.raises(ValueError) as caught:
            require_av_link_alignment(link, operation=operation)
        message = str(caught.value)
        assert message in ERROR_ZH_CN and message in ALL_ERRORS
        assert tr_error(message) == ERROR_ZH_CN[message] != message



def test_audio_only_roll_ui_literals_constants_and_placeholders_translate():
    from lumen.i18n import _load_catalogs, _CATALOGS
    from lumen.i18n_catalog_linked_audio_roll import ZH_CN as audio_zh, EN as audio_en, ERROR_ZH_CN as audio_errors
    from lumen.ui_linked_audio_roll import AUDIO_ROLL_SELECTION, AUDIO_ROLL_SCOPE, AUDIO_ROLL_LIMITS, AUDIO_ROLL_STALE
    _load_catalogs()
    module = ast.parse((ROOT/'lumen/ui_linked_audio_roll.py').read_text())
    for node in ast.walk(module):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ('tr', 'action_button') and node.args):
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                assert arg.value in _CATALOGS['zh-CN']
    for text in (AUDIO_ROLL_SELECTION, AUDIO_ROLL_SCOPE, AUDIO_ROLL_LIMITS, AUDIO_ROLL_STALE):
        assert text in audio_zh
    fields = lambda value: {name for _, name, _, _ in string.Formatter().parse(value) if name is not None}
    for source, translated in audio_zh.items():
        assert fields(source) == fields(translated) and audio_en[source] == source
        assert translated != source and _CATALOGS['zh-CN'][source] == translated
    set_locale('zh-CN')
    for message, translated in audio_errors.items():
        assert ALL_ERRORS[message] == tr_error(message) == translated

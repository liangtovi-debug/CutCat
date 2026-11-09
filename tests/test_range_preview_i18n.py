"""Known range errors use the central catalog; user diagnostics remain verbatim."""
import ast
from pathlib import Path

import pytest

from lumen.i18n import get_locale, set_locale, tr_error
from lumen.i18n_catalog_errors import ZH_CN
from lumen.i18n_catalog_range_preview import ERROR_ZH_CN
from lumen.range_preview import FALLBACK


@pytest.fixture(autouse=True)
def restore_locale():
    before = get_locale()
    yield
    set_locale(before)


@pytest.mark.parametrize('module', [
    'render', 'color_media', 'media', 'caption_render', 'clip_composite',
    'overlay_render', 'range_preview', 'range_audio_mix',
])
def test_changed_backend_static_and_fallback_errors_are_registered(module):
    root = Path(__file__).resolve().parents[1] / 'lumen'
    tree = ast.parse((root / f'{module}.py').read_text())
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                and node.exc.args):
            continue
        argument = node.exc.args[0]
        message = None
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            message = argument.value
        elif (isinstance(argument, ast.BinOp) and isinstance(argument.op, ast.Add)
              and isinstance(argument.left, ast.Constant)
              and isinstance(argument.left.value, str)
              and isinstance(argument.right, ast.Name)
              and argument.right.id == 'FALLBACK'):
            message = argument.left.value + FALLBACK
        if message is not None:
            assert message in ZH_CN, (module, node.lineno, message)


def test_all_range_error_forms_translate_through_central_boundary():
    for message, expected in ERROR_ZH_CN.items():
        if '{error}' in message:
            continue
        assert ZH_CN[message] == expected
        set_locale('zh-CN')
        assert str(tr_error(message)) == expected
        set_locale('en')
        assert str(tr_error(message)) == message


def test_wrapped_range_diagnostics_preserve_paths_braces_and_newlines():
    detail = '/tmp/Range preview canceled./中文 {literal}.mov\nffmpeg custom code=42'
    message = f'Cannot prepare range preview: {detail} {FALLBACK}'
    set_locale('zh-CN')
    assert str(tr_error(message)) == f'无法准备片段预览：{detail} 请为此工程／片段构建完整时间线预览。'
    set_locale('en')
    assert str(tr_error(message)) == message

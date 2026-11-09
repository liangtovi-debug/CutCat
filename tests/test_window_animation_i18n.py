"""Explicit window animation errors stay readable at the UI boundary."""
import ast
from pathlib import Path
from lumen.i18n import set_locale, tr_error
from lumen.i18n_catalog_errors import ZH_CN

def test_all_new_animation_errors_are_translated():
    root = Path(__file__).resolve().parents[1] / 'lumen'
    for module in ('window_animation', 'qualifier', 'color'):
        for node in ast.walk(ast.parse((root / (module + '.py')).read_text())):
            if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args):
                continue
            argument = node.exc.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                text = argument.value
                if module == 'window_animation' or text.startswith(('Window keyframe', 'Window interpolation', 'Animated windows')):
                    assert text in ZH_CN, text
                    set_locale('zh-CN')
                    assert str(tr_error(text)) != text
                    set_locale('en')
                    assert str(tr_error(text)) == text

def test_dynamic_animation_error_preserves_user_field_and_bounds():
    set_locale('zh-CN')
    text = 'Window keyframe user_field must be finite and in .001..2'
    translated = str(tr_error(text))
    assert 'user_field' in translated and '.001' in translated and '2' in translated
    assert '技术错误详情' not in translated

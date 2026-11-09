"""Qualifier backend failures must be localized before the full release gate."""
import ast
from pathlib import Path
import pytest
from lumen.i18n import get_locale, set_locale, tr_error
from lumen.i18n_catalog_errors import ZH_CN


def test_all_static_qualifier_errors_have_explicit_translations():
    root = Path(__file__).resolve().parents[1] / 'lumen'
    for node in ast.walk(ast.parse('\n'.join((root / name).read_text() for name in ('qualifier.py', 'qualifier_picker.py', 'qualifier_overlay.py')))):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
            value = node.exc.args[0]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                assert value.value in ZH_CN, value.value


@pytest.mark.parametrize('message', [
    'Version 18 clips require qualifier settings',
    'Legacy projects cannot contain qualifier settings',
    'Qualifier requires exactly its documented settings fields',
    'Invalid qualifier settings',
    'Qualifier enabled must be a boolean',
    'Qualifier hue_width must be finite and in 0..0.5',
    'Qualifier saturation_low must not exceed saturation_high',
    'Unknown color sampling stage.',
])
def test_qualifier_schema_and_parameter_failures_translate_and_retain_english(message):
    previous = get_locale()
    try:
        set_locale('en')
        assert tr_error(message) == message
        set_locale('zh-CN')
        translated = str(tr_error(message))
        assert translated != message
        assert '技术错误详情' not in translated
        assert any('\u4e00' <= c <= '\u9fff' for c in translated)
    finally:
        set_locale(previous)


def test_overlay_dynamic_readout_and_armed_instruction_are_localized():
    from lumen.i18n import tr
    from lumen.i18n_catalog_qualifier import ZH_CN as catalog
    previous = get_locale()
    keys = [key for key in catalog if 'overlay' in key.lower()]
    assert len(keys) >= 7
    try:
        for key in keys:
            set_locale('en')
            assert str(tr(key)) == key
            set_locale('zh-CN')
            translated = str(tr(key))
            assert translated != key
            assert any('\u4e00' <= c <= '\u9fff' for c in translated)
    finally:
        set_locale(previous)

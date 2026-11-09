"""Both timing controls and new timing/audio backend errors are bilingual."""
import ast
from pathlib import Path

import pytest
from lumen.i18n import set_locale, tr_error
from lumen.i18n_catalog_errors import ZH_CN


@pytest.mark.parametrize('module', ['timing', 'audio_timing', 'asr'])
def test_new_timing_static_errors_have_explicit_translations(module):
    path = Path(__file__).resolve().parents[1] / 'lumen' / f'{module}.py'
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
            value = node.exc.args[0]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                assert value.value in ZH_CN, (module, value.value)


@pytest.mark.parametrize('message', [
    'Speed must be between 1/4 and 4',
    'Source position exceeds supported rational bounds',
    'Rubber Band 0.25x failed pitch preservation (414.29 Hz).',
    'Rubber Band 1.5x is incompatible with the fixed start-delay compensation (52.0 ms marker error).',
    'Rubber Band 2x lost a selected audio transient or source tail.',
    'Recognition cues overlap in source timings; review the transcript before importing.',
    'Transcription source bounds must be exact nonnegative times with end after start.',
])
def test_timing_errors_translate_without_losing_details(message):
    set_locale('zh-CN')
    translated = str(tr_error(message))
    assert translated != message and not translated.startswith('技术错误详情：')
    assert any('\u3400' <= char <= '\u9fff' for char in translated)
    set_locale('en'); assert tr_error(message) == message

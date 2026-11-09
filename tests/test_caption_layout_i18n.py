"""Known layout errors localize; caption identity and diagnostics stay literal."""
import ast
from pathlib import Path
import re

import pytest

from lumen.i18n import set_locale, tr_error
from lumen.i18n_catalog_errors import CAPTION_LAYOUT_ZH_CN


@pytest.mark.parametrize('reason,translated', [
    ('Caption has more than 3 manual lines; reduce text or increase the line limit.',
     '字幕的手动换行超过 3 行；请缩短字幕或提高行数上限。'),
    ('Caption needs more than 6 lines; reduce text/font size or increase the line limit.',
     '字幕需要超过 6 行；请缩短字幕、减小字号或提高行数上限。'),
    ('Caption font and outline are too tall for the safe height; reduce font size or margins.',
     '字幕字体与描边的高度超出安全区域；请减小字号或边距。'),
    ('Caption contains a grapheme too wide for the safe width; reduce font size, outline, or margins.',
     '字幕中有不可拆分的字符组合超出安全区域宽度；请减小字号、描边或边距。'),
    ('Caption text must contain at most 10000 characters.', '字幕文本最多可包含 10000 个字符。'),
])
def test_layout_wrapper_translates_known_reason_only(reason, translated):
    caption_id = 'Original caption 中文 {field} / Render cancelled.'
    message = f'Caption {caption_id} cannot fit safely: {reason}'
    set_locale('zh-CN')
    assert tr_error(reason) == translated
    assert tr_error(message) == f'字幕 {caption_id} 无法容纳于安全区域：{translated}'
    set_locale('en')
    assert tr_error(message) == message


@pytest.mark.parametrize('reason', [
    '/local/Caption line exceeds the safe width; reduce font size, outline, or margins..ttf',
    'Original text: Caption needs more than 6 lines; reduce text/font size or increase the line limit.',
    'The timeline is empty.',
    'Caption nested cannot fit safely: Caption needs more than 6 lines; reduce text/font size or increase the line limit.',
    'Unknown font diagnostic 1280 × 720\nOriginal caption 中文',
])
def test_layout_wrapper_preserves_unknown_unrelated_and_nested_diagnostics(reason):
    set_locale('zh-CN')
    assert tr_error(f'Caption raw-id cannot fit safely: {reason}') == f'字幕 raw-id 无法容纳于安全区域：{reason}'


def test_every_layout_static_error_has_an_explicit_translation():
    path = Path(__file__).resolve().parents[1] / 'lumen' / 'caption_layout.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    messages = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
            message = node.exc.args[0]
            if isinstance(message, ast.Constant) and isinstance(message.value, str):
                messages.append(message.value)
    assert messages
    set_locale('zh-CN')
    for message in messages:
        assert message in CAPTION_LAYOUT_ZH_CN
        assert re.search('[\u3400-\u9fff]', str(tr_error(message)))

"""Error localization must not rewrite filenames, captions, or diagnostics."""
import ast
from pathlib import Path
from string import Formatter

import pytest

from lumen.i18n import get_locale, set_locale, tr_error
from lumen.i18n_catalog_errors import ZH_CN
from lumen.i18n_errors import _TEMPLATES


@pytest.fixture(autouse=True)
def restore_locale():
    previous = get_locale()
    set_locale('zh-CN')
    yield
    set_locale(previous)


def test_static_error_and_english_passthrough():
    message = 'The timeline is empty.'
    assert str(tr_error(ValueError(message))) == '时间轴为空。'
    set_locale('en')
    assert tr_error(ValueError(message)) == message


def test_dynamic_paths_and_technical_details_remain_verbatim():
    path = '/Users/Invalid grade/Caption 演示 {name}.png'
    assert str(tr_error(f'Sticker file cannot be read: {path}.')) == f'无法读取贴纸文件：{path}。'
    detail = 'Permission denied: C:\\Movies\\Render cancelled.\\take.wav\ncode=5'
    assert str(tr_error(f'Cannot inspect media: {detail}')) == f'无法检查媒体：{detail}'


def test_unknown_error_keeps_entire_original_and_chinese_heading():
    message = 'FFmpeg custom failure 42\n/tmp/Speech synthesis cancelled..wav'
    assert str(tr_error(message)) == '技术错误详情：\n' + message
    set_locale('en')
    assert tr_error(message) == message


def test_captions_inside_errors_are_not_translated():
    text = 'The timeline is empty.'
    assert str(tr_error(f'Local model suggestion rejected: {text}. Original caption is unchanged.')) == (
        f'本地模型建议已拒绝：{text}。原始字幕未更改。')


def test_specific_template_and_repeated_fields_match():
    assert str(tr_error('Invalid grade: bad field')) == '调色参数无效：bad field'
    assert str(tr_error('Source transfer is sRGB, but the clip input is Rec.709. Select sRGB in Input encoding before preview or export.')) == (
        '源传递函数为 sRGB，但片段输入为 Rec.709。请在预览或导出前在输入编码中选择 sRGB。')


def test_translation_fields_match_source_fields():
    formatter = Formatter()
    for source, target in ZH_CN.items():
        fields = lambda value: {f for _, f, _, _ in formatter.parse(value) if f is not None}
        assert fields(source) == fields(target), source
        assert any('\u3400' <= char <= '\u9fff' for char in target), source


def test_backend_static_raise_messages_have_explicit_translations():
    # UI exception strings have their own catalog; cover backend behavior here.
    modules = ('model', 'media', 'render', 'local_ai', 'tts', 'tts_worker', 'asr',
               'sticker_assets', 'color', 'color_source', 'color_media', 'caption_render',
               'overlay_render', 'subtitles', 'subtitle_ai', 'semantic_search',
               'semantic_search_worker', 'voice_catalog', 'controlled_assistant',
               'video_transition', 'video_transition_render', 'video_transition_sources', 'video_transition_preflight')
    for module in modules:
        root = Path(__file__).resolve().parents[1] / 'lumen'
        for node in ast.walk(ast.parse((root / f'{module}.py').read_text())):
            if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args):
                continue
            argument = node.exc.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                assert argument.value in ZH_CN, (module, argument.value)


def test_every_template_preserves_values_and_has_full_match():
    for source, pattern in _TEMPLATES:
        names = pattern.groupindex
        values = {name: f'user_中文_{{literal}}_{name}' for name in names}
        original = source.format(**values)
        match = pattern.fullmatch(original)
        assert match is not None, source
        assert match.groupdict() == values, source
        rendered = str(tr_error(original))
        for value in values.values():
            assert value in rendered, source


def test_short_invalid_prefix_does_not_capture_unknown_diagnostics():
    assert str(tr_error('Invalid external-driver response: user file.mov')) == (
        '技术错误详情：\nInvalid external-driver response: user file.mov')
    assert str(tr_error('Invalid media path')) == '媒体路径无效'

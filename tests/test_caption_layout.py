"""Pure layout tests, independent of Qt, FFmpeg, and system CJK coverage."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import random

import pytest
import regex
from PIL import ImageFont

from lumen.caption_layout import CaptionLayoutError, layout_caption
from lumen.caption_render import CaptionRenderError, font_identity
from lumen.model import Caption, CaptionStyle


class MonoFont:
    """Deterministic metrics isolate wrap policy from individual font designs."""
    def __init__(self):
        self.measured_characters = 0

    def getmetrics(self):
        return 8, 2

    def getlength(self, text):
        return 10 * len(regex.findall(r'\X', text))

    def getbbox(self, text, anchor):
        assert anchor == 'ls'
        self.measured_characters += len(text)
        return 0, -8, self.getlength(text), 2


@pytest.fixture
def mono(monkeypatch):
    font = MonoFont()
    monkeypatch.setattr('lumen.caption_layout._load_font', lambda *args: font)
    return font


def wrap(text, width=60, height=200, **kwargs):
    return layout_caption(text, 'unused.ttf', 10, width, height, **kwargs)


def test_words_are_preferred_and_original_spaces_are_preserved(mono):
    result = wrap('one two three', width=50)
    assert result.lines == ('one ', 'two ', 'three')
    assert result.text == 'one \ntwo \nthree'
    assert result.text.replace('\n', '') == 'one two three'
    assert result.width == 50
    assert result.height == 30
    assert result.line_widths == (40, 40, 50)


def test_long_word_splits_without_hyphens_or_lost_characters(mono):
    result = wrap('abcdefghijk', width=30)
    assert result.lines == ('abc', 'def', 'ghi', 'jk')
    assert ''.join(result.lines) == 'abcdefghijk'
    assert max(result.line_widths) <= 30


def test_cjk_wraps_at_graphemes(mono):
    result = wrap('中文内容中文内容', width=40)
    assert result.lines == ('中文内容', '中文内容')
    assert result.width == 40


def test_mixed_cjk_and_latin_keeps_fitting_latin_words(mono):
    result = wrap('中文 test 文本', width=60)
    assert 'test' in result.text
    assert ''.join(result.lines) == '中文 test 文本'
    assert all(width <= 60 for width in result.line_widths)


@pytest.mark.parametrize('text,width', [
    ('你好，世界。', 30),
    ('你好，世界。', 20),
    ('中文（测试）内容。', 40),
    ('甲「乙丙」丁', 30),
    ('甲《乙丙》丁', 30),
    ('甲【乙丙】丁', 30),
    ('甲（ 乙丙） 丁', 50),
])
def test_common_cjk_punctuation_avoids_orphaned_automatic_breaks(mono, text, width):
    result = wrap(text, width=width)
    assert ''.join(result.lines) == text
    for line in result.lines[:-1]:
        assert not line.rstrip().endswith(('（', '「', '《', '【'))
    for line in result.lines[1:]:
        assert not line.lstrip().startswith(('，', '。', '）', '」', '》', '】'))


def test_impossible_punctuation_group_is_an_actionable_error(mono):
    with pytest.raises(CaptionLayoutError, match='cannot wrap punctuation'):
        wrap('甲，', width=10)


def test_manual_newlines_blank_lines_and_trailing_newline_are_preserved(mono):
    text = 'alpha\n\nbeta\n'
    result = wrap(text, width=100)
    assert result.text == text
    assert result.lines == ('alpha', '', 'beta', '')
    assert result.height == 40


def test_manual_punctuation_break_is_not_rewritten(mono):
    result = wrap('甲（\n，乙', width=100)
    assert result.text == '甲（\n，乙'


def test_empty_caption_returns_a_valid_immutable_empty_line(mono):
    result = wrap('')
    assert result.text == ''
    assert result.lines == ('',)
    assert result.width == 0
    assert result.height == 10
    with pytest.raises(FrozenInstanceError):
        result.text = 'changed'


@pytest.mark.parametrize('cluster', ['e\u0301', 'a\u0301\u0327', '👩\u200d💻', '🇨🇳', '✈\ufe0f'])
def test_extended_graphemes_never_split(mono, cluster):
    result = wrap(cluster * 4, width=10)
    assert result.lines == (cluster,) * 4
    assert ''.join(result.lines) == cluster * 4
    with pytest.raises(CaptionLayoutError, match='grapheme too wide'):
        wrap(cluster, width=9)


def test_outline_is_included_once_on_every_edge(mono):
    result = wrap('ab', width=24, height=14, outline_width=2)
    assert result.width == 24
    assert result.height == 14
    assert result.line_widths == (24,)
    with pytest.raises(CaptionLayoutError, match='grapheme too wide'):
        wrap('a', width=13, outline_width=2)
    with pytest.raises(CaptionLayoutError, match='too tall'):
        wrap('ab', width=24, height=13, outline_width=2)


def test_explicit_line_spacing_is_included_between_lines(mono):
    result = wrap('a\nb\nc', height=39, outline_width=2, line_spacing=2)
    assert result.height == 38
    assert result.line_spacing == 2
    assert result.line_height == 10
    with pytest.raises(CaptionLayoutError, match='safe height'):
        wrap('a\nb\nc', height=37, outline_width=2, line_spacing=2)


def test_tabs_remain_literal_and_reserve_four_spaces(mono):
    result = wrap('a\tb', width=60)
    assert result.text == 'a\tb'
    assert result.width == 60
    result = wrap('a\tb', width=50)
    assert result.lines == ('a\t', 'b')
    assert ''.join(result.lines) == 'a\tb'


def test_repeated_whitespace_is_never_trimmed(mono):
    text = '  a   b  '
    result = wrap(text, width=50)
    assert ''.join(result.lines) == text
    assert all(width <= 50 for width in result.line_widths)


def test_explicit_line_limit_rejects_automatic_overflow(mono):
    with pytest.raises(CaptionLayoutError, match='needs more than 2 lines'):
        wrap('abcdefghij', width=30, max_lines=2)


def test_explicit_line_limit_rejects_manual_overflow(mono):
    with pytest.raises(CaptionLayoutError, match='more than 2 manual lines'):
        wrap('a\n\nb', max_lines=2)


def test_height_rejects_wrapped_overflow_without_truncating(mono):
    with pytest.raises(CaptionLayoutError, match='too many lines for the safe height'):
        wrap('abcdefghij', width=30, height=30)


def test_extra_tall_combining_ink_is_checked_after_wrapping(mono, monkeypatch):
    original = mono.getbbox

    def tall(text, anchor):
        left, _, right, _ = original(text, anchor)
        return left, -18, right, 4

    monkeypatch.setattr(mono, 'getbbox', tall)
    result = wrap('e\u0301', height=22)
    assert result.height == 22
    assert result.ascent == 18 and result.descent == 4
    with pytest.raises(CaptionLayoutError, match='ink and outline exceed the safe height'):
        wrap('e\u0301', height=21)


def test_negative_bearings_and_right_overhang_are_counted(mono, monkeypatch):
    monkeypatch.setattr(mono, 'getbbox', lambda text, anchor: (-3, -8, 13, 2))
    result = wrap('j', width=20, outline_width=2)
    assert result.width == 20
    with pytest.raises(CaptionLayoutError, match='grapheme too wide'):
        wrap('j', width=19, outline_width=2)


def test_no_caption_mutation_or_font_size_adjustment(mono):
    caption = Caption('source', 0, 30, 'one two three')
    before = dict(vars(caption))
    result = wrap(caption.text, width=50)
    assert result.text != caption.text
    assert vars(caption) == before
    assert result.line_height == 10


def test_maximum_length_text_has_bounded_measurement_work(mono):
    rng = random.Random(407)
    text = ''.join(rng.choices('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=10_000))
    result = wrap(text, width=30, height=40_000)
    assert ''.join(result.lines) == text
    assert len(result.lines) == 3334
    assert mono.measured_characters < 200_000


def test_long_text_fails_early_at_line_limit(mono):
    with pytest.raises(CaptionLayoutError, match='needs more than 6 lines'):
        wrap('W' * 10_000, width=30, max_lines=6)
    assert mono.measured_characters < 1000


@pytest.mark.parametrize('kwargs', [
    {'font_size': 0}, {'font_size': True}, {'font_size': 1001},
    {'available_width': 0}, {'available_width': -1}, {'available_width': float('nan')},
    {'available_width': float('inf')}, {'available_width': True},
    {'available_height': 0}, {'available_height': float('inf')},
    {'outline_width': -1}, {'outline_width': 101}, {'outline_width': 1.5},
    {'line_spacing': -1}, {'line_spacing': True}, {'max_lines': 0}, {'max_lines': True},
])
def test_invalid_layout_parameters_are_rejected(mono, kwargs):
    args = dict(font_path='unused.ttf', font_size=10, available_width=100, available_height=100)
    args.update(kwargs)
    with pytest.raises(CaptionLayoutError):
        layout_caption('test', **args)


@pytest.mark.parametrize('text', ['x' * 10_001, 'a\rb', 'a\r\nb', '\x00', '\ud800', 1],
                         ids=['over-limit', 'carriage-return', 'crlf', 'nul', 'surrogate', 'not-text'])
def test_invalid_source_text_is_rejected(mono, text):
    with pytest.raises(CaptionLayoutError):
        wrap(text)


@pytest.fixture(scope='module')
def latin_font_path():
    for family in ('DejaVu Sans', 'Arial', 'Helvetica'):
        try:
            identity = font_identity(CaptionStyle(font_family=family))
            # The layout API requires an already extracted face. This unit test
            # uses an individual TTF/OTF only; renderer integration covers TTCs.
            if Path(identity[0]).suffix.lower() in ('.ttf', '.otf') and identity[-1] == 0:
                return identity[0]
        except CaptionRenderError:
            continue
    pytest.skip('No local individual Latin font for measured layout test')


def test_real_freetype_metrics_control_width_and_preserve_text(latin_font_path):
    text = 'Wide WWW and narrow iii, cafe\u0301 words, 100% literal %{n}.'
    result = layout_caption(text, latin_font_path, 24, 200, 500, outline_width=3, max_lines=10)
    font = ImageFont.truetype(latin_font_path, 24, layout_engine=ImageFont.Layout.BASIC)
    assert ''.join(result.lines) == text
    assert len(result.lines) > 1
    assert result.width <= 200 and result.height <= 500
    for line, width in zip(result.lines, result.line_widths):
        left, _, right, _ = font.getbbox(line, anchor='ls')
        assert width >= max(right, font.getlength(line), 0) - min(left, 0) + 6
    ascent, descent = font.getmetrics()
    assert result.line_height >= ascent + descent


def test_real_freetype_widths_distinguish_wide_and_narrow_letters(latin_font_path):
    wide = layout_caption('WWWW', latin_font_path, 24, 1000, 100)
    narrow = layout_caption('iiii', latin_font_path, 24, 1000, 100)
    assert wide.width > 2 * narrow.width


def test_missing_font_has_clear_error(tmp_path):
    with pytest.raises(CaptionLayoutError, match='could not load the selected local font'):
        layout_caption('hello', tmp_path / 'missing.ttf', 24, 200, 200)


def test_missing_raqm_fails_clearly_instead_of_silent_fallback(monkeypatch):
    monkeypatch.setattr('lumen.caption_layout.features.check_feature', lambda name: False)
    with pytest.raises(CaptionLayoutError, match='requires Pillow with RAQM text shaping support'):
        layout_caption('hello', 'unused.ttf', 24, 200, 200)


def test_shaped_and_basic_metrics_are_both_reserved_at_exact_width(latin_font_path):
    import math
    from lumen.caption_layout import _load_font
    text = 'e\u0301' * 10
    font = _load_font(latin_font_path, 42)
    left, _, right, _ = font.getbbox(text, anchor='ls')
    width = math.ceil(max(right, font.getlength(text), 0) - min(left, 0)) + 4
    exact = layout_caption(text, latin_font_path, 42, width, 300, outline_width=2)
    assert exact.lines == (text,)
    assert exact.width == width
    narrow = layout_caption(text, latin_font_path, 42, width - 1, 300, outline_width=2)
    assert len(narrow.lines) > 1
    assert all(value <= width - 1 for value in narrow.line_widths)
    for engine in (ImageFont.Layout.BASIC, ImageFont.Layout.RAQM):
        measured = ImageFont.truetype(latin_font_path, 42, layout_engine=engine)
        left, _, right, _ = measured.getbbox(text, anchor='ls')
        assert exact.width >= max(right, measured.getlength(text), 0) - min(left, 0) + 4


def test_shaped_combining_mark_ink_is_reserved(latin_font_path):
    text = 'a\u0301\u0302\u0303'
    result = layout_caption(text, latin_font_path, 42, 200, 300)
    for engine in (ImageFont.Layout.BASIC, ImageFont.Layout.RAQM):
        measured = ImageFont.truetype(latin_font_path, 42, layout_engine=engine)
        _, top, _, bottom = measured.getbbox(text, anchor='ls')
        assert result.ascent >= -top
        assert result.descent >= bottom
    with pytest.raises(CaptionLayoutError, match='safe height'):
        layout_caption(text, latin_font_path, 42, 200, result.height - 1)

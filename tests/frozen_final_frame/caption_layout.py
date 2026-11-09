"""Measured, nonmutating caption wrapping for the local Latin/CJK renderer.

The selected, glyph-checked font must be the same extracted face passed to
FFmpeg. Bounds take the union of Pillow FreeType BASIC and RAQM/HarfBuzz
metrics: BASIC alone can round advances down or undercount stacked accents.
RAQM is required explicitly, so missing shaping support fails safely instead of
silently changing layout. Advances, ink overhangs, outline, and font ascender/
descender metrics are all reserved. This is a conservative Latin/CJK layout
contract, not a claim of parity with every complex-script shaper or font.
Glyph coverage remains the responsibility of ``caption_render.prepare_font``.

Wrapping inserts LF characters only: original whitespace and manual LF breaks
are retained, including blank lines. Extended grapheme clusters are indivisible.
A word may be split at grapheme boundaries when it cannot fit on one line, but
common CJK opening/closing punctuation is kept off automatic line ends/starts.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import unicodedata

import regex
from PIL import ImageFont, features

from .model import MAX_CAPTION_TEXT


class CaptionLayoutError(ValueError):
    """The caption cannot be laid out without violating its safe area."""


@dataclass(frozen=True)
class CaptionLayout:
    text: str
    lines: tuple[str, ...]
    width: int
    height: int
    line_widths: tuple[int, ...]
    line_height: int
    line_spacing: int
    ascent: int
    descent: int


# A deliberately small, explicit kinsoku set, not a complete Unicode line-break
# implementation. Original/manual line starts and ends are never rewritten.
_OPENING = frozenset('([{（［｛〈《「『【〔〖〘〚“‘')
_CLOSING = frozenset(')]}）］｝〉》」』】〕〗〙〛、。，．？！：；…‥ー〜～％‰°”’!,.:;?%')
_BREAK_AFTER = frozenset('-‐–/／')
_GRAPHEMES = regex.compile(r'\X')


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise CaptionLayoutError(f'Caption {name} must be an integer from {minimum} to {maximum}.')
    return value


def _dimension(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise CaptionLayoutError(f'Caption {name} must be a positive finite number.')
    return float(value)


class _FontBounds:
    """Union of unshaped and HarfBuzz-shaped metrics from the identical face."""
    def __init__(self, basic, shaped):
        self.fonts = (basic, shaped)

    def getmetrics(self):
        metrics = [font.getmetrics() for font in self.fonts]
        return max(ascent for ascent, _ in metrics), max(descent for _, descent in metrics)

    def getlength(self, text):
        return max(font.getlength(text) for font in self.fonts)

    def getbbox(self, text, anchor):
        boxes = [font.getbbox(text, anchor=anchor) for font in self.fonts]
        return (min(box[0] for box in boxes), min(box[1] for box in boxes),
                max(box[2] for box in boxes), max(box[3] for box in boxes))


def _load_font(font_path, font_size):
    if not features.check_feature('raqm'):
        raise CaptionLayoutError('Caption layout requires Pillow with RAQM text shaping support; reinstall a supported application build.')
    try:
        path = str(Path(font_path))
        basic = ImageFont.truetype(path, font_size, layout_engine=ImageFont.Layout.BASIC)
        shaped = ImageFont.truetype(path, font_size, layout_engine=ImageFont.Layout.RAQM)
        return _FontBounds(basic, shaped)
    except (OSError, ValueError, TypeError) as error:
        raise CaptionLayoutError('Caption layout could not load the selected local font.') from error


def _is_cjk(cluster):
    return any(unicodedata.east_asian_width(char) in ('W', 'F') and char.isalnum() for char in cluster)


def _is_word(cluster):
    return not _is_cjk(cluster) and any(char.isalnum() or char == '_' for char in cluster)


def _breaks(clusters):
    """Return legal and preferred automatic break positions in linear time."""
    count = len(clusters)
    previous = [None] * (count + 1)
    following = [None] * (count + 1)
    last = None
    for index, cluster in enumerate(clusters):
        if not cluster.isspace():
            last = cluster
        previous[index + 1] = last
    last = None
    for index in range(count - 1, -1, -1):
        if not clusters[index].isspace():
            last = clusters[index]
        following[index] = last
    legal = [False] * (count + 1)
    preferred = [False] * (count + 1)
    for index in range(1, count):
        left, right = previous[index], following[index]
        legal[index] = not ((left and left[-1] in _OPENING) or (right and right[0] in _CLOSING))
        if legal[index]:
            before, after = clusters[index - 1], clusters[index]
            preferred[index] = (before.isspace() or before[-1] in _BREAK_AFTER or
                                _is_cjk(before) or _is_cjk(after) or
                                (not before.isspace() and not after.isspace() and
                                 not (_is_word(before) and _is_word(after))))
    return legal, preferred


class _Measure:
    def __init__(self, font, outline_width):
        self.font = font
        self.outline_width = outline_width
        self.cache = {}

    def __call__(self, text):
        if text not in self.cache:
            # Drawtext's explicit/default tabsize=4 advances at most four space
            # widths. Reserving four whole spaces per tab is conservative while
            # leaving each original tab intact in the textfile.
            sample = text.replace('\t', '    ')
            if sample:
                left, top, right, bottom = self.font.getbbox(sample, anchor='ls')
                advance = self.font.getlength(sample)
                width = math.ceil(max(right, advance, 0) - min(left, 0)) + 2 * self.outline_width
            else:
                top = bottom = width = 0
            self.cache[text] = (width, top, bottom)
        return self.cache[text]


def _fitting_end(clusters, start, measure, available_width):
    """Find a fitting prefix with exponential probing, then binary search.

    Unlike searching the entire remaining paragraph on every line, exponential
    probing bounds text measured even for a 10,000-character, narrow caption.
    The final candidate is always rechecked; widths are never estimated from
    character counts. Latin/CJK prefix widths are nondecreasing under these
    paired metrics; every chosen line is independently checked afterward.
    """
    remaining = len(clusters) - start
    fitted, step = 0, 1
    while True:
        candidate = min(step, remaining)
        if measure(''.join(clusters[start:start + candidate]))[0] > available_width:
            upper = candidate - 1
            break
        fitted = candidate
        if candidate == remaining:
            return start + candidate
        step *= 2
    lower = fitted + 1
    while lower <= upper:
        candidate = (lower + upper) // 2
        if measure(''.join(clusters[start:start + candidate]))[0] <= available_width:
            fitted = candidate
            lower = candidate + 1
        else:
            upper = candidate - 1
    return start + fitted


def layout_caption(text: str, font_path, font_size: int,
                   available_width: int | float, available_height: int | float, *,
                   outline_width: int = 0, line_spacing: int = 0,
                   max_lines: int | None = None) -> CaptionLayout:
    """Return immutable wrapped text and conservative pixel bounds.

    ``available_width`` and ``available_height`` are the complete safe rectangle;
    the returned bounds already include ``outline_width`` on both edges. The
    caller must not subtract that outline again. Font size is never reduced and
    no text is truncated. LF is the only accepted manual line separator (the
    project's caption model normalizes CR/CRLF before calling this function).
    Use the returned ``line_spacing`` and ``tabsize=4`` in FFmpeg drawtext.
    """
    if not isinstance(text, str) or len(text) > MAX_CAPTION_TEXT:
        raise CaptionLayoutError(f'Caption text must contain at most {MAX_CAPTION_TEXT} characters.')
    if any((ord(char) < 32 and char not in '\n\t') or 0xD800 <= ord(char) <= 0xDFFF for char in text):
        raise CaptionLayoutError('Caption text must use normalized LF breaks and contain no invalid characters.')
    _integer(font_size, 'font size', 1, 1000)
    _integer(outline_width, 'outline width', 0, 100)
    _integer(line_spacing, 'line spacing', 0, 1000)
    if max_lines is not None:
        _integer(max_lines, 'maximum lines', 1, MAX_CAPTION_TEXT + 1)
    available_width = _dimension(available_width, 'safe width')
    available_height = _dimension(available_height, 'safe height')
    paragraphs = text.split('\n')
    if max_lines is not None and len(paragraphs) > max_lines:
        raise CaptionLayoutError(f'Caption has more than {max_lines} manual lines; reduce text or increase the line limit.')
    font = _load_font(font_path, font_size)
    font_ascent, font_descent = font.getmetrics()
    base_height = max(1, font_ascent + font_descent)
    # Even empty/manual lines reserve font metrics. Early rejection also bounds
    # wrapping work for many lines; exact ink extents are checked afterward.
    height_line_limit = math.floor((available_height - 2 * outline_width + line_spacing) /
                                   (base_height + line_spacing))
    if height_line_limit < 1:
        raise CaptionLayoutError('Caption font and outline are too tall for the safe height; reduce font size or margins.')
    limit = min(height_line_limit, max_lines) if max_lines is not None else height_line_limit
    measure = _Measure(font, outline_width)
    lines = []

    def append_line(line):
        if len(lines) >= limit:
            if max_lines is not None and limit == max_lines:
                raise CaptionLayoutError(f'Caption needs more than {max_lines} lines; reduce text/font size or increase the line limit.')
            raise CaptionLayoutError('Caption has too many lines for the safe height; reduce text/font size or margins.')
        lines.append(line)

    for paragraph in paragraphs:
        clusters = _GRAPHEMES.findall(paragraph)
        if not clusters:
            append_line('')
            continue
        legal, preferred = _breaks(clusters)
        start = 0
        while start < len(clusters):
            end = _fitting_end(clusters, start, measure, available_width)
            if end == start:
                raise CaptionLayoutError('Caption contains a grapheme too wide for the safe width; reduce font size, outline, or margins.')
            if end < len(clusters):
                preferred_end = next((index for index in range(end, start, -1) if preferred[index]), None)
                if preferred_end is not None:
                    end = preferred_end
                else:
                    end = next((index for index in range(end, start, -1) if legal[index]), start)
                    if end == start:
                        raise CaptionLayoutError('Caption cannot wrap punctuation within the safe width; reduce font size, outline, or margins.')
            line = ''.join(clusters[start:end])
            # A defensive check guards the final cut independently of the search.
            if measure(line)[0] > available_width:
                raise CaptionLayoutError('Caption line exceeds the safe width; reduce font size, outline, or margins.')
            append_line(line)
            start = end

    metrics = [measure(line) for line in lines]
    ascent = max(font_ascent, max((-top for _, top, _ in metrics), default=0))
    descent = max(font_descent, max((bottom for _, _, bottom in metrics), default=0))
    line_height = max(1, ascent + descent)
    height = len(lines) * line_height + (len(lines) - 1) * line_spacing + 2 * outline_width
    if height > available_height:
        raise CaptionLayoutError('Caption ink and outline exceed the safe height; reduce text/font size or margins.')
    widths = tuple(width for width, _, _ in metrics)
    return CaptionLayout(text='\n'.join(lines), lines=tuple(lines), width=max(widths, default=0),
                         height=height, line_widths=widths, line_height=line_height,
                         line_spacing=line_spacing, ascent=ascent, descent=descent)

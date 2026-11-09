"""Translate known application error grammar, never arbitrary diagnostic data.

This is an explicit presentation boundary. Backends keep their stable exception
messages and user values (including English paths and captions) stay verbatim.
Unknown failures retain their entire original message beneath a localized heading.
"""
from __future__ import annotations

import re
from string import Formatter
from .i18n_catalog_errors import CAPTION_LAYOUT_ZH_CN, ZH_CN


def _compile_templates():
    templates = []
    for source in ZH_CN:
        if source == 'Technical error details:\n{detail}':
            continue
        parts = list(Formatter().parse(source))
        if not any(field is not None for _, field, _, _ in parts):
            continue
        pattern = []
        fields = set()
        literal_length = 0
        for literal, field, spec, conversion in parts:
            pattern.append(re.escape(literal))
            literal_length += len(literal)
            if field is not None:
                if not field.isidentifier() or spec or conversion:
                    raise ValueError('Error templates require plain named fields')
                if field in fields:
                    pattern.append(f'(?P={field})')
                else:
                    pattern.append(f'(?P<{field}>.*?)')
                    fields.add(field)
        templates.append((literal_length, source, re.compile(''.join(pattern), re.DOTALL)))
    # Match the most specific developer-authored grammar first.
    return tuple((source, pattern) for _, source, pattern in
                 sorted(templates, key=lambda row: row[0], reverse=True))


_TEMPLATES = _compile_templates()


def _caption_layout_reason(original):
    """Translate one known layout reason, never arbitrary nested diagnostics."""
    from .i18n import tr
    if original in CAPTION_LAYOUT_ZH_CN:
        return tr(original)
    for source, pattern in _TEMPLATES:
        if source not in CAPTION_LAYOUT_ZH_CN:
            continue
        match = pattern.fullmatch(original)
        if match is not None:
            return tr(source, **match.groupdict())
    return original


def tr_error(error: Exception | str) -> str:
    """Render an exception for the active UI locale, preserving captured details.

The caller must supply an actual error, never use this function on user text.
Nested exception text is retained except for the explicitly allowlisted,
single-level caption-layout reason in its renderer wrapper.
"""
    from .i18n import get_locale, tr
    original = str(error)
    if get_locale() != 'zh-CN':
        return original
    if original in ZH_CN:
        return tr(original)
    for source, pattern in _TEMPLATES:
        match = pattern.fullmatch(original)
        if match is not None:
            values = match.groupdict()
            if source == 'Caption {caption_id} cannot fit safely: {exc}':
                values['exc'] = _caption_layout_reason(values['exc'])
            if source == 'Cannot verify saved source range: {error}':
                # Translate only our exact, allowlisted pure-model reason;
                # never scan/rewrite paths or arbitrary decoder diagnostics.
                from .i18n_catalog_source_ranges import ERROR_ZH_CN
                if values['error'] in ERROR_ZH_CN:
                    values['error'] = tr(values['error'])
            return tr(source, **values)
    return tr('Technical error details:\n{detail}', detail=original)

"""Offline, literal-text caption burn-in with integer frame boundaries.

Font selection is explicit: a matching local face must cover all visible code
points. We never silently substitute a different family or accept missing glyphs.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import math
import unicodedata


class CaptionRenderError(ValueError):
    pass


def _roots(directory):
    roots = [Path(directory).expanduser()] if directory else []
    if sys.platform == 'win32':
        roots += [Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts',
                  Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Microsoft/Windows/Fonts']
    elif sys.platform == 'darwin':
        roots += [Path('/System/Library/Fonts'), Path('/Library/Fonts'), Path.home() / 'Library/Fonts']
    else:
        roots += [Path('/usr/share/fonts'), Path('/usr/local/share/fonts'),
                  Path.home() / '.local/share/fonts', Path.home() / '.fonts']
    if directory and not roots[0].is_dir():
        raise CaptionRenderError('Caption font directory does not exist.')
    return roots


@lru_cache(maxsize=4096)
def _faces(path, size, mtime, ctime):
    from fontTools.ttLib import TTFont, TTCollection, TTLibError
    try:
        if Path(path).suffix.lower() in ('.ttc', '.otc'):
            collection = TTCollection(path, lazy=True)
            fonts = collection.fonts
        else:
            collection = None
            fonts = [TTFont(path, lazy=True)]
        result = []
        try:
            for index, font in enumerate(fonts):
                names = set()
                for record in font['name'].names:
                    if record.nameID in (1, 4, 6, 16):
                        try: names.add(record.toUnicode().casefold())
                        except (UnicodeError, LookupError): pass
                regular = not bool(font['head'].macStyle & 3)
                result.append((index, frozenset(names), regular))
        finally:
            for font in fonts: font.close()
        return tuple(result)
    except (OSError, ValueError, KeyError, TTLibError, ImportError):
        return ()


def font_identity(style):
    """Return a selected absolute local font path, face index, and stat identity."""
    roots = _roots(style.font_directory)
    paths = []
    # Fontconfig is a local lookup optimization, never an authority for fallback.
    if not style.font_directory and shutil.which('fc-match'):
        try:
            match = subprocess.run(['fc-match', '-f', '%{file}', style.font_family],
                                   capture_output=True, text=True, timeout=5)
            candidate = Path(match.stdout)
            if match.returncode == 0 and candidate.is_file() and candidate.suffix.lower() in ('.ttf', '.otf', '.ttc', '.otc'): paths.append(candidate)
        except (OSError, subprocess.TimeoutExpired): pass
    for root in roots:
        if root.is_dir():
            paths.extend(sorted((p for p in root.rglob('*') if p.suffix.lower() in
                                 ('.ttf', '.otf', '.ttc', '.otc')), key=lambda p: ('regular' not in p.name.lower(), str(p))))
    family = style.font_family.casefold()
    fallback = None
    seen = set()
    for path in paths:
        try:
            path = path.resolve(strict=True)
            if path in seen: continue
            seen.add(path)
            stat = path.stat()
            identity = (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            for index, names, regular in _faces(*identity):
                if family in names:
                    choice = (*identity, index)
                    if regular: return choice
                    if fallback is None: fallback = choice
        except OSError: continue
    if fallback is not None: return fallback
    raise CaptionRenderError(f'Caption font family "{style.font_family}" was not found locally. Install a local font or choose its family and font directory.')


def prepare_font(style, captions, cache_dir):
    from fontTools.ttLib import TTFont
    identity = font_identity(style)
    path, size, mtime, ctime, index = identity
    font = TTFont(path, fontNumber=index, lazy=True)
    try:
        cmap = font.getBestCmap() or {}
        required = {ord(c) for caption in captions for c in caption.text
                    if not c.isspace() and unicodedata.category(c) != 'Cf'
                    and not (0xFE00 <= ord(c) <= 0xFE0F or 0xE0100 <= ord(c) <= 0xE01EF)}
        missing = sorted(required - cmap.keys())
        if missing:
            sample = ', '.join(f'U+{cp:04X}' for cp in missing[:8])
            raise CaptionRenderError(f'Caption font "{style.font_family}" is missing glyphs ({sample}). Choose a local font covering this language; no fallback/tofu will be exported.')
        digest = hashlib.sha256(repr(identity).encode()).hexdigest()
        destination = Path(cache_dir) / (digest + '.ttf')
        if not destination.exists():
            # Extract the selected TTC face; FFmpeg otherwise selects face zero.
            font.save(destination)
        return destination.resolve()
    finally:
        font.close()


def _filter_path(path):
    # Two filter-parser levels: option quoting, then graph escaping. Forward
    # slashes work on Windows and avoid treating Windows separators as escapes.
    value = str(Path(path).resolve()).replace('\\', '/')
    value = value.replace(':', '\\:').replace("'", "'\\\\\\''")
    return "'" + value + "'"


def caption_filters(project, cache_dir):
    """Drawtext n is zero-based, unlike ASS's centisecond time quantization."""
    if not project.captions: return []
    style = project.caption_style
    font = prepare_font(style, project.captions, cache_dir)
    from .caption_layout import layout_caption, CaptionLayoutError
    horizontal_margin = math.ceil(project.width * style.safe_margin_percent / 100)
    # Leave room for stroke at every output edge, including a requested zero margin.
    horizontal_margin = max(horizontal_margin, style.outline_width + 1)
    bottom_margin = max(style.margin_bottom, style.outline_width + 1)
    available_width = project.width - 2 * horizontal_margin
    available_height = project.height - bottom_margin - style.outline_width - 1
    filters = []
    for caption in sorted(project.captions, key=lambda c: (c.start, c.id)):
        try:
            layout = layout_caption(caption.text, font, style.font_size,
                                    available_width, available_height,
                                    outline_width=style.outline_width, max_lines=style.max_lines)
        except CaptionLayoutError as exc:
            raise CaptionRenderError(f'Caption {caption.id} cannot fit safely: {exc}') from exc
        digest = hashlib.sha256(layout.text.encode('utf-8')).hexdigest()
        textfile = Path(cache_dir) / (digest + '.txt')
        textfile.write_text(layout.text, encoding='utf-8')
        filters.append(f'drawtext=fontfile={_filter_path(font)}:textfile={_filter_path(textfile)}:'
                       f'expansion=none:fontsize={style.font_size}:fontcolor={style.color}:'
                       f'borderw={style.outline_width}:bordercolor={style.outline_color}:'
                       f'line_spacing={layout.line_spacing}:tabsize=4:'
                       f'x=(w-text_w)/2:y=h-text_h-{bottom_margin}:'
                       f"enable='gte(n,{caption.start})*lt(n,{caption.start + caption.duration})'")
    return filters

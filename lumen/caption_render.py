"""Offline, literal-text caption burn-in with integer frame boundaries.

Font selection is explicit: a matching local face must cover all visible code
points. We never silently substitute a different family or accept missing glyphs.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import math
import struct
import unicodedata
import tempfile
import threading
from collections import OrderedDict


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
    return _read_faces(path, size, mtime, ctime)


def _read_faces(path, size, mtime, ctime, check=None):
    from fontTools.ttLib import TTFont, TTCollection, TTLibError
    check_failure = None
    def checked():
        nonlocal check_failure
        try:
            check()
        except Exception as exc:
            check_failure = exc
            raise
    try:
        source = path if check is None else _guarded_font_file(path, checked)
        if Path(path).suffix.lower() in ('.ttc', '.otc'):
            collection = TTCollection(source, lazy=True)
            fonts = collection.fonts
        else:
            collection = None
            fonts = [TTFont(source, lazy=True)]
        result = []
        try:
            for index, font in enumerate(fonts):
                if check is not None: checked()
                names = set()
                for record in font['name'].names:
                    if check is not None: checked()
                    if record.nameID in (1, 4, 6, 16):
                        try: names.add(record.toUnicode().casefold())
                        except (UnicodeError, LookupError): pass
                regular = not bool(font['head'].macStyle & 3)
                result.append((index, frozenset(names), regular))
        finally:
            for font in fonts: font.close()
        return tuple(result)
    except (OSError, ValueError, KeyError, TTLibError, ImportError) as exc:
        if check_failure is not None:
            raise check_failure
        if check is not None and isinstance(exc, CaptionRenderError):
            raise
        return ()


def _guarded_font_paths(root, check, budget):
    """Still-preview-only discovery bounds; ordinary exports retain old traversal."""
    paths = []
    pending = [root]
    while pending:
        directory = pending.pop()
        check()
        budget['dirs'] += 1
        if budget['dirs'] > 2048:
            raise CaptionRenderError('Final-frame font discovery exceeds 2048 directories; use Build preview.')
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    check()
                    budget['entries'] += 1
                    if budget['entries'] > 20000:
                        raise CaptionRenderError('Final-frame font discovery exceeds 20000 entries; use Build preview.')
                    path = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                    if path.suffix.lower() in ('.ttf', '.otf', '.ttc', '.otc'):
                        budget['fonts'] += 1
                        if budget['fonts'] > 4096:
                            raise CaptionRenderError('Final-frame font discovery exceeds 4096 fonts; use Build preview.')
                        paths.append(path)
        except OSError:
            # Match pathlib's legacy inaccessible-directory traversal policy.
            continue
    return sorted(paths, key=lambda p: ('regular' not in p.name.lower(), str(p)))


def _guarded_font_size(path, size, check):
    from .file_identity import is_plain_file
    check()
    if not is_plain_file(Path(path).lstat()):
        raise CaptionRenderError('Final-frame fonts must be regular local files; use Build preview.')
    if size > 64 * 1024 * 1024:
        raise CaptionRenderError('Final-frame font exceeds 64 MiB; use Build preview.')
    check()


def _read_font_bytes(path, check, *, expected_identity=None, maximum=None):
    """Own verified bytes; never parse a path that can change under the parser."""
    from .file_identity import is_plain_file, path_matches_descriptor, same_file_state
    check()
    path = Path(path)
    before = path.lstat()
    if not is_plain_file(before):
        raise CaptionRenderError('Caption fonts and cache entries must be regular local files.')
    if expected_identity is not None and (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != expected_identity[1:4]:
        raise CaptionRenderError('Caption font changed after discovery.')
    if maximum is not None and before.st_size > maximum:
        raise CaptionRenderError('Final-frame font exceeds 64 MiB; use Build preview.'
                                     if maximum == 64 * 1024 * 1024 else
                                     'Caption font cache exceeds its verified output size.')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    try:
        stream = os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise
    with stream:
        opened = os.fstat(stream.fileno())
        if not is_plain_file(opened) or not path_matches_descriptor(before, opened):
            raise CaptionRenderError('Caption font changed while opening.')
        pieces, size = [], 0
        while True:
            check()
            block = stream.read(1024 * 1024)
            if not block: break
            size += len(block)
            if maximum is not None and size > maximum:
                raise CaptionRenderError('Final-frame font exceeds 64 MiB; use Build preview.'
                                     if maximum == 64 * 1024 * 1024 else
                                     'Caption font cache exceeds its verified output size.')
            pieces.append(block)
        after = os.fstat(stream.fileno())
        if not same_file_state(opened, after) or not same_file_state(before, path.lstat()):
            raise CaptionRenderError('Caption font changed while reading.')
    check()
    return b''.join(pieces)


def _guarded_font_file(path, check, *, expected_identity=None):
    """Read bounded verified bytes before a font parser opens its input."""
    data = _read_font_bytes(path, check, expected_identity=expected_identity, maximum=64 * 1024 * 1024)
    magic = data[:4]
    if magic == b'ttcf':
        if len(data) < 12:
            raise CaptionRenderError('Incomplete final-frame font collection; use Build preview.')
        count = struct.unpack('>I', data[8:12])[0]
        if not 1 <= count <= 64 or len(data) < 12 + count * 4:
            raise CaptionRenderError('Final-frame font collection exceeds 64 faces or is incomplete; use Build preview.')
        offsets = struct.unpack(f'>{count}I', data[12:12 + count * 4])
    elif magic in (b'\x00\x01\x00\x00', b'OTTO', b'true', b'typ1'):
        offsets = (0,)
    else:
        raise CaptionRenderError('Final-frame fonts require uncompressed SFNT/TTC, not WOFF or another format; use Build preview.')
    decoded_name_bytes = 0
    for offset in offsets:
        check()
        if offset + 12 > len(data) or data[offset:offset + 4] not in (b'\x00\x01\x00\x00', b'OTTO', b'true', b'typ1'):
            raise CaptionRenderError('Invalid final-frame font header; use Build preview.')
        tables = struct.unpack('>H', data[offset + 4:offset + 6])[0]
        if not 1 <= tables <= 256 or offset + 12 + tables * 16 > len(data):
            raise CaptionRenderError('Final-frame font exceeds 256 tables or is incomplete; use Build preview.')
        for table in range(tables):
            check()
            start = offset + 12 + table * 16
            location, length = struct.unpack('>II', data[start + 8:start + 16])
            if location + length > len(data):
                raise CaptionRenderError('Final-frame font table exceeds its verified file; use Build preview.')
            if data[start:start + 4] == b'cmap':
                _guarded_cmap(data[location:location + length], check)
            elif data[start:start + 4] == b'name':
                if length < 6:
                    raise CaptionRenderError('Incomplete final-frame font names; use Build preview.')
                version, count, strings = struct.unpack_from('>HHH', data, location)
                if version not in (0, 1) or count > 1024 or 6 + count * 12 > length or strings < 6 + count * 12:
                    raise CaptionRenderError('Final-frame font name table exceeds supported bounds; use Build preview.')
                for record in range(count):
                    check()
                    size, relative = struct.unpack_from('>HH', data, location + 6 + record * 12 + 8)
                    decoded_name_bytes += size
                    if strings + relative + size > length or decoded_name_bytes > 4 * 1024 * 1024:
                        raise CaptionRenderError('Final-frame decoded font names exceed 4 MiB or the verified table; use Build preview.')
    check()
    snapshot = io.BytesIO(data)
    # fontTools' save path consults the input object's name even for in-memory
    # readers. It is metadata only; every font read uses these verified bytes.
    snapshot.name = str(path)
    return snapshot


def _guarded_cmap(data, check):
    """Bound range expansion BEFORE fontTools builds Python character maps.

    A tiny format12/13 group can otherwise expand to billions of entries even
    in a tiny valid-size SFNT. Only the known bounded formats are admitted.
    """
    def reject():
        raise CaptionRenderError('Final-frame font character map exceeds its supported bounds; use Build preview.')
    def u16(offset):
        if offset < 0 or offset + 2 > len(data): reject()
        return struct.unpack_from('>H', data, offset)[0]
    def u32(offset):
        if offset < 0 or offset + 4 > len(data): reject()
        return struct.unpack_from('>I', data, offset)[0]
    if len(data) < 4 or u16(0) != 0: reject()
    records = u16(2)
    if not 1 <= records <= 64 or 4 + records * 8 > len(data): reject()
    seen, expanded = set(), 0
    def reserve(count):
        nonlocal expanded
        expanded += count
        if count < 0 or expanded > 1_114_112: reject()
    for index in range(records):
        check()
        offset = u32(4 + index * 8 + 4)
        if offset in seen: continue
        seen.add(offset)
        fmt = u16(offset)
        if fmt in (0, 4, 6):
            length = u16(offset + 2)
        elif fmt in (10, 12, 13):
            length = u32(offset + 4)
        elif fmt == 14:
            length = u32(offset + 2)
        else:
            reject()
        end = offset + length
        if not length or end > len(data): reject()
        if fmt == 0:
            if length < 262: reject()
            reserve(256)
        elif fmt == 6:
            first, count = u16(offset + 6), u16(offset + 8)
            if first + count > 65536 or 10 + count * 2 > length: reject()
            reserve(count)
        elif fmt == 4:
            twice = u16(offset + 6)
            if twice % 2 or not twice: reject()
            count = twice // 2
            if 16 + count * 8 > length: reject()
            previous = -1
            for group in range(count):
                check()
                last = u16(offset + 14 + group * 2)
                first = u16(offset + 16 + count * 2 + group * 2)
                if first <= previous or last < first: reject()
                previous = last
                reserve(last - first + 1)
        elif fmt == 10:
            first, count = u32(offset + 12), u32(offset + 16)
            if first + count > 0x110000 or 20 + count * 2 > length: reject()
            reserve(count)
        elif fmt in (12, 13):
            count = u32(offset + 12)
            if count > 65536 or 16 + count * 12 > length: reject()
            previous = -1
            for group in range(count):
                check()
                first, last = u32(offset + 16 + group * 12), u32(offset + 20 + group * 12)
                if first <= previous or last < first or last > 0x10FFFF: reject()
                previous = last
                reserve(last - first + 1)
        else:  # Format14 variation maps have two independently sized lists.
            count = u32(offset + 6)
            if count > 512 or 10 + count * 11 > length: reject()
            for group in range(count):
                check()
                record = offset + 10 + group * 11
                for delta, stride in ((3, 4), (7, 5)):
                    sub = u32(record + delta)
                    if not sub: continue
                    start = offset + sub
                    size = u32(start)
                    if start + 4 + size * stride > end or size > 1_114_112: reject()
                    for item in range(size):
                        check()
                        pos = start + 4 + item * stride
                        first = int.from_bytes(data[pos:pos + 3], 'big')
                        span = data[pos + 3] + 1 if stride == 4 else 1
                        if first + span > 0x110000: reject()
                        reserve(span)


def font_identity(style, *, check=None, run_process=None):
    """Return a selected absolute local font path, face index, and stat identity."""
    roots = _roots(style.font_directory)
    if check is not None: check()
    paths = []
    budget = {'dirs': 0, 'entries': 0, 'fonts': 0}
    # Fontconfig is a local lookup optimization, never an authority for fallback.
    if not style.font_directory and shutil.which('fc-match'):
        try:
            match = (run_process or subprocess.run)(['fc-match', '-f', '%{file}', style.font_family],
                                   capture_output=True, text=True, timeout=5)
            candidate = Path(match.stdout)
            if match.returncode == 0 and candidate.is_file() and candidate.suffix.lower() in ('.ttf', '.otf', '.ttc', '.otc'): paths.append(candidate)
        except (OSError, subprocess.TimeoutExpired): pass
        if check is not None: check()
    for root in roots:
        if check is not None: check()
        if root.is_dir():
            if check is None:
                paths.extend(sorted((p for p in root.rglob('*') if p.suffix.lower() in
                                     ('.ttf', '.otf', '.ttc', '.otc')), key=lambda p: ('regular' not in p.name.lower(), str(p))))
            else:
                paths.extend(_guarded_font_paths(root, check, budget))
    family = style.font_family.casefold()
    fallback = None
    seen = set()
    for path in paths:
        if check is not None: check()
        try:
            path = path.resolve(strict=True)
            if path in seen: continue
            seen.add(path)
            stat = path.stat()
            identity = (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            if check is not None:
                _guarded_font_size(path, stat.st_size, check)
            faces = _faces(*identity) if check is None else _read_faces(*identity, check=check)
            if check is not None: check()
            for index, names, regular in faces:
                if check is not None: check()
                if family in names:
                    choice = (*identity, index)
                    if regular: return choice
                    if fallback is None: fallback = choice
        except OSError: continue
    if fallback is not None: return fallback
    raise CaptionRenderError(f'Caption font family "{style.font_family}" was not found locally. Install a local font or choose its family and font directory.')


# Hashes only, scoped to an owner cache path; never share font bytes or caches
# between renderers. A new process must independently extract/validate TTC faces.
_FONT_OUTPUTS = OrderedDict()
_FONT_OUTPUTS_LOCK = threading.Lock()


def _font_cache_matches(destination, digest, size, check):
    from .file_identity import is_plain_file
    try:
        info = destination.lstat()
    except FileNotFoundError:
        return False
    if not is_plain_file(info):
        raise CaptionRenderError('Caption font cache entry must be a regular local file.')
    if info.st_size != size:
        return False
    data = _read_font_bytes(destination, check, maximum=size)
    return hashlib.sha256(data).hexdigest() == digest


def _publish_font_bytes(destination, data, check):
    """Publish only complete validated bytes, with an exclusively owned temporary."""
    digest = hashlib.sha256(data).hexdigest()
    if _font_cache_matches(destination, digest, len(data), check):
        return digest
    fd, temporary = tempfile.mkstemp(prefix='.font-', suffix='.tmp', dir=destination.parent)
    try:
        try:
            stream = os.fdopen(fd, 'wb')
        except BaseException:
            os.close(fd)
            raise
        with stream:
            for offset in range(0, len(data), 1024 * 1024):
                check()
                stream.write(data[offset:offset + 1024 * 1024])
            stream.flush()
            os.fsync(stream.fileno())
        check()
        # Another writer may have published the same immutable output meanwhile.
        if not _font_cache_matches(destination, digest, len(data), check):
            try:
                os.replace(temporary, destination)
            except PermissionError:
                # Windows may deny replacement while an identical published font
                # is open. Accept only independently verified complete bytes.
                if not _font_cache_matches(destination, digest, len(data), check):
                    raise
        return digest
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare_font(style, captions, cache_dir, *, check=None, run_process=None):
    from fontTools.ttLib import TTFont
    identity = (font_identity(style, check=check, run_process=run_process) if run_process is not None else
                font_identity(style) if check is None else font_identity(style, check=check))
    checked = check or (lambda: None)
    checked()
    path, size, mtime, ctime, index = identity
    if check is not None:
        with _guarded_font_file(path, check, expected_identity=identity) as source:
            data = source.read()
    else:
        data = _read_font_bytes(path, checked, expected_identity=identity)
    collection = data[:4] == b'ttcf'
    standalone = data[:4] in (b'\x00\x01\x00\x00', b'OTTO', b'true', b'typ1')
    if not collection and (not standalone or index != 0):
        raise CaptionRenderError('Caption font requires a valid SFNT face or collection.')
    font = TTFont(io.BytesIO(data), fontNumber=index, lazy=True)
    try:
        checked()
        names = set()
        for record in font['name'].names:
            if record.nameID in (1, 4, 6, 16):
                try:
                    names.add(record.toUnicode().casefold())
                except (UnicodeError, LookupError):
                    pass
        if style.font_family.casefold() not in names:
            raise CaptionRenderError('Caption font family changed after discovery.')
        cmap = font.getBestCmap() or {}
        checked()
        required = {ord(c) for caption in captions for c in caption.text
                    if not c.isspace() and unicodedata.category(c) != 'Cf'
                    and not (0xFE00 <= ord(c) <= 0xFE0F or 0xE0100 <= ord(c) <= 0xE01EF)}
        missing = sorted(required - cmap.keys())
        if missing:
            sample = ', '.join(f'U+{cp:04X}' for cp in missing[:8])
            raise CaptionRenderError(f'Caption font "{style.font_family}" is missing glyphs ({sample}). Choose a local font covering this language; no fallback/tofu will be exported.')
        cache = Path(cache_dir)
        if cache.is_symlink() or getattr(cache, 'is_junction', lambda: False)() or not cache.is_dir():
            raise CaptionRenderError('Caption font cache requires an owned directory.')
        key = hashlib.sha256(data + b'\0font-snapshot-v1\0' + str(index).encode()).hexdigest()
        destination = cache / (key + '.ttf')
        owner_key = str(destination.resolve(strict=False))
        if collection:
            with _FONT_OUTPUTS_LOCK:
                prior = _FONT_OUTPUTS.get(owner_key)
            if prior and _font_cache_matches(destination, prior[0], prior[1], checked):
                checked()
                return destination.resolve()
            output = io.BytesIO()
            font.recalcTimestamp = False  # Deterministic selected-face publication.
            font.save(output)  # Only a collection needs selected-face extraction.
            prepared = output.getvalue()
        else:
            prepared = data  # Preserve every original SFNT byte and all names/glyphs.
        digest = _publish_font_bytes(destination, prepared, checked)
        if collection:
            with _FONT_OUTPUTS_LOCK:
                _FONT_OUTPUTS[owner_key] = (digest, len(prepared))
                _FONT_OUTPUTS.move_to_end(owner_key)
                while len(_FONT_OUTPUTS) > 128:
                    _FONT_OUTPUTS.popitem(last=False)
        checked()
        return destination.resolve()
    finally:
        font.close()


def _filter_path(path):
    # Two filter-parser levels: option quoting, then graph escaping. Forward
    # slashes work on Windows and avoid treating Windows separators as escapes.
    value = str(Path(path).resolve()).replace('\\', '/')
    value = value.replace(':', '\\:').replace("'", "'\\\\\\''")
    return "'" + value + "'"


def caption_filters(project, cache_dir, *, check=None, frame_offset=0, run_process=None):
    """Drawtext n is zero-based, unlike ASS's centisecond time quantization."""
    if not project.captions: return []
    style = project.caption_style
    font = (prepare_font(style, project.captions, cache_dir, check=check, run_process=run_process)
            if run_process is not None else
            prepare_font(style, project.captions, cache_dir) if check is None else
            prepare_font(style, project.captions, cache_dir, check=check))
    from .caption_layout import layout_caption, CaptionLayoutError
    horizontal_margin = math.ceil(project.width * style.safe_margin_percent / 100)
    # Leave room for stroke at every output edge, including a requested zero margin.
    horizontal_margin = max(horizontal_margin, style.outline_width + 1)
    bottom_margin = max(style.margin_bottom, style.outline_width + 1)
    available_width = project.width - 2 * horizontal_margin
    available_height = project.height - bottom_margin - style.outline_width - 1
    filters = []
    for caption in sorted(project.captions, key=lambda c: (c.start, c.id)):
        if check is not None: check()
        try:
            layout = layout_caption(caption.text, font, style.font_size,
                                    available_width, available_height,
                                    outline_width=style.outline_width, max_lines=style.max_lines,
                                    **({'check': check} if check is not None else {}))
        except CaptionLayoutError as exc:
            raise CaptionRenderError(f'Caption {caption.id} cannot fit safely: {exc}') from exc
        digest = hashlib.sha256(layout.text.encode('utf-8')).hexdigest()
        textfile = Path(cache_dir) / (digest + '.txt')
        textfile.write_text(layout.text, encoding='utf-8')
        if check is not None: check()
        filters.append(f'drawtext=fontfile={_filter_path(font)}:textfile={_filter_path(textfile)}:'
                       f'expansion=none:fontsize={style.font_size}:fontcolor={style.color}:'
                       f'borderw={style.outline_width}:bordercolor={style.outline_color}:'
                       f'line_spacing={layout.line_spacing}:tabsize=4:'
                       f'x=(w-text_w)/2:y=h-text_h-{bottom_margin}:'
                       f"enable='gte(n,{caption.start - frame_offset})*lt(n,{caption.start + caption.duration - frame_offset})'")
    return filters

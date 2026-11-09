"""Bounded, offline PNG and explicit frame-pack validation.

There is deliberately no downloader, archive extraction, file discovery, or SVG/
GIF decoder here. A pack is an explicitly selected version-1 JSON manifest and
its explicitly listed PNG frames. See docs/STICKER_ASSETS.md for the format.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import struct
import threading
import zlib

from .file_identity import path_matches_descriptor, same_file_state

MAX_MANIFEST_BYTES = 64 * 1024
MAX_PNG_BYTES = 4 * 1024 * 1024
MAX_DIMENSION = 2048
MAX_FRAMES = 120
MAX_PNG_CHUNKS = 1024
MAX_TEXT_BYTES = 64 * 1024
MAX_DECODED_BYTES = 128 * 1024 * 1024
_PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
# Compressed ancillary metadata can expand independently of image dimensions.
# Reject it rather than passing a metadata decompression bomb to later readers.
_COMPRESSED_METADATA = {b'iCCP', b'zTXt', b'iTXt'}


class StickerAssetError(ValueError):
    """A local sticker is missing, unsupported, unsafe, or has changed."""


class StickerCancelled(StickerAssetError):
    """The caller cancelled validation."""


@dataclass(frozen=True, slots=True)
class StickerAsset:
    width: int
    height: int
    frames: tuple[Path, ...]
    frame_duration: int
    loop: bool
    digest: str
    frame_sha256: tuple[str, ...] = ()


def _cancelled(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise StickerCancelled('Sticker validation cancelled.')


def _local_path(value: str | os.PathLike[str]) -> Path:
    try:
        text = os.fspath(value)
        if not isinstance(text, str) or not text or any(ord(c) < 32 for c in text):
            raise StickerAssetError('Choose an explicit local PNG or JSON sticker file.')
        if text.startswith(('//', '\\\\')) or (os.name != 'nt' and '\\' in text):
            raise StickerAssetError('Sticker network paths and URLs are not supported.')
        if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', text) and not (
                os.name == 'nt' and re.match(r'^[A-Za-z]:[\\/]', text)):
            raise StickerAssetError('Sticker network paths and URLs are not supported.')
        drive = PureWindowsPath(text).drive if os.name == 'nt' else ''
        if ':' in text[len(drive):]:
            raise StickerAssetError('Sticker alternate data streams are not supported.')
        path = Path(text).expanduser()
        if '..' in path.parts:
            raise StickerAssetError('Sticker paths must not contain parent traversal (..).')
        path = path.absolute()
        if path.suffix.lower() not in ('.png', '.json'):
            raise StickerAssetError('Choose a PNG image or a JSON frame-pack manifest; archives are not supported.')
        # The user-selected directory is the trust anchor. Canonicalize its
        # ancestors once so ordinary system aliases such as macOS /tmp work.
        # The selected final file itself and all manifest descendants must not
        # be links/reparse points.
        selected = path.lstat()
        if stat.S_ISLNK(selected.st_mode) or getattr(selected, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
            raise StickerAssetError('Selected sticker file must not be a symbolic link or junction.')
        return path.parent.resolve(strict=True) / path.name
    except (TypeError, OSError, RuntimeError) as exc:
        raise StickerAssetError('Choose a valid local PNG or JSON sticker file.') from exc


def _no_links(path: Path) -> os.stat_result:
    """Reject links below the canonical selected root and detect later changes."""
    try:
        chain = (*reversed(path.parents), path)
        final = None
        for component in chain:
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                raise StickerAssetError('Sticker files and their directories must not use symbolic links or junctions.')
            if component != path and not stat.S_ISDIR(info.st_mode):
                raise StickerAssetError('Sticker parent paths must be ordinary directories.')
            final = info
        if final is None or not stat.S_ISREG(final.st_mode):
            raise StickerAssetError('Sticker input must be an ordinary local file.')
        return final
    except OSError as exc:
        raise StickerAssetError(f'Sticker file cannot be read: {path.name}.') from exc


def _open_nofollow(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    # POSIX descriptor-relative traversal prevents replacing a parent with a
    # symlink between the ancestry check and open. Windows additionally rejects
    # reparse points before/after reading; these checks are not a file lock.
    if os.open in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW'):
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.parts[1:-1]:
                next_directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = next_directory
            return os.open(path.name, flags, dir_fd=directory)
        finally:
            os.close(directory)
    return os.open(path, flags)


def _read_bounded(path: Path, limit: int, cancel=None) -> bytes:
    _cancelled(cancel)
    before = _no_links(path)
    if before.st_size > limit:
        raise StickerAssetError(f'Sticker file exceeds its {limit // 1024} KiB size limit: {path.name}.')
    try:
        fd = _open_nofollow(path)
        with os.fdopen(fd, 'rb') as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or not path_matches_descriptor(before, opened):
                raise StickerAssetError('Sticker input changed while being opened; import it again.')
            chunks = []
            total = 0
            while True:
                _cancelled(cancel)
                chunk = handle.read(min(64 * 1024, limit + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > limit:
                    raise StickerAssetError(f'Sticker file exceeds its {limit // 1024} KiB size limit: {path.name}.')
            after = os.fstat(handle.fileno())
        now = _no_links(path)
        if (total != opened.st_size or not same_file_state(opened, after)
                or not same_file_state(before, now) or not path_matches_descriptor(now, after)):
            raise StickerAssetError('Sticker input changed while being read; import it again.')
        _cancelled(cancel)
        return b''.join(chunks)
    except OSError as exc:
        raise StickerAssetError(f'Sticker file cannot be read safely: {path.name}.') from exc


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StickerAssetError(f'Duplicate sticker manifest field: {key}.')
        result[key] = value
    return result


def _invalid_constant(_value):
    raise StickerAssetError('Sticker manifest must use standard JSON values.')


def _manifest(path: Path, cancel=None) -> dict:
    try:
        data = json.loads(_read_bounded(path, MAX_MANIFEST_BYTES, cancel).decode('utf-8'),
                          object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, StickerAssetError):
            raise
        raise StickerAssetError('Sticker manifest must be valid UTF-8 JSON.') from exc
    fields = {'version', 'frames', 'frame_duration', 'loop'}
    if not isinstance(data, dict) or set(data) != fields:
        raise StickerAssetError('Sticker manifest requires exactly version, frames, frame_duration, and loop.')
    if type(data['version']) is not int or data['version'] != 1:
        raise StickerAssetError('Only sticker manifest version 1 is supported.')
    if type(data['frame_duration']) is not int or not 1 <= data['frame_duration'] <= 300:
        raise StickerAssetError('Sticker frame_duration must be an integer from 1 to 300 project frames.')
    if type(data['loop']) is not bool:
        raise StickerAssetError('Sticker loop must be a JSON boolean.')
    if not isinstance(data['frames'], list) or not 1 <= len(data['frames']) <= MAX_FRAMES:
        raise StickerAssetError(f'Sticker manifest must list between 1 and {MAX_FRAMES} PNG frames.')
    return data


def _frame_path(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or len(name) > 1024 or any(ord(c) < 32 for c in name):
        raise StickerAssetError('Sticker frames must be relative PNG filenames.')
    relative = PurePosixPath(name)
    if ('\\' in name or ':' in name or relative.is_absolute() or PureWindowsPath(name).drive
            or any(part in ('', '.', '..') for part in name.split('/')) or relative.suffix.lower() != '.png'):
        raise StickerAssetError('Sticker frames must be contained relative PNG paths, without traversal or URLs.')
    frame = root.joinpath(*relative.parts)
    # Lexical containment is enforced above; no path is resolved before checking
    # its ancestry, so a symlink cannot be hidden by normalization.
    if not frame.is_relative_to(root):
        raise StickerAssetError('Sticker frame escapes its manifest directory.')
    _no_links(frame)
    return frame


def _inputs(path, cancel=None):
    _cancelled(cancel)
    source = _local_path(path)
    _no_links(source)
    if source.suffix.lower() == '.png':
        return source, (source,), {'version': 1, 'frame_duration': 1, 'loop': True}, ('',)
    settings = _manifest(source, cancel)
    frames = []
    for name in settings['frames']:
        _cancelled(cancel)
        frames.append(_frame_path(source.parent, name))
    return source, tuple(frames), settings, tuple(settings['frames'])


def sticker_input_paths(path: str | os.PathLike[str], cancel=None) -> tuple[Path, ...]:
    """Return selected input plus ordered frame dependencies, without PNG decoding.

    Used to prevent saving a project over its own inputs. Invalid/missing paths
    and malformed manifests raise StickerAssetError. Duplicate frames are kept.
    """
    source, frames, _, _ = _inputs(path, cancel)
    return frames if source.suffix.lower() == '.png' else (source, *frames)


def _scanline_bytes(width, height, depth, color, interlace):
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    passes = ((0, 0, 1, 1),) if not interlace else (
        (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
        (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
    total = 0
    for x, y, dx, dy in passes:
        columns = max(0, (width - x + dx - 1) // dx)
        rows = max(0, (height - y + dy - 1) // dy)
        if columns and rows:
            total += rows * (1 + (columns * channels * depth + 7) // 8)
    return total


def _bounded_inflate(parts, expected, cancel):
    decoder = zlib.decompressobj()
    total = 0
    try:
        for part in parts:
            for offset in range(0, len(part), 64 * 1024):
                _cancelled(cancel)
                data = part[offset:offset + 64 * 1024]
                inflated = decoder.decompress(data, expected - total + 1)
                total += len(inflated)
                if total > expected or decoder.unconsumed_tail or decoder.unused_data:
                    raise StickerAssetError('PNG compressed pixels exceed their declared dimensions or contain trailing data.')
        if not decoder.eof or total != expected:
            raise StickerAssetError('PNG compressed pixels are incomplete or do not match the declared dimensions.')
    except zlib.error as exc:
        raise StickerAssetError('PNG contains invalid compressed pixels.') from exc


def _decode_png(data: bytes, remaining: int, cancel=None) -> tuple[int, int, int]:
    if not data.startswith(_PNG_SIGNATURE):
        raise StickerAssetError('Sticker file content is not PNG, regardless of its filename.')
    offset = len(_PNG_SIGNATURE)
    width = height = decoded_size = expected = 0
    seen = set()
    image_parts = []
    safe_chunks = [_PNG_SIGNATURE]
    ended = False
    idat_ended = False
    chunks = 0
    metadata = {b'sRGB', b'gAMA', b'cHRM', b'pHYs', b'tEXt', b'tIME', b'sBIT', b'bKGD'}
    while offset < len(data):
        _cancelled(cancel)
        if len(data) - offset < 12:
            raise StickerAssetError('PNG has a truncated chunk.')
        chunks += 1
        if chunks > MAX_PNG_CHUNKS:
            raise StickerAssetError('PNG has too many chunks (limit 1024).')
        size, kind = struct.unpack_from('>I4s', data, offset)
        end = offset + 12 + size
        if end > len(data) or not all(65 <= b <= 90 or 97 <= b <= 122 for b in kind) or kind[2] & 32:
            raise StickerAssetError('PNG has an invalid or truncated chunk.')
        body = data[offset + 8:offset + 8 + size]
        crc = struct.unpack_from('>I', data, offset + 8 + size)[0]
        if zlib.crc32(kind + body) & 0xffffffff != crc:
            raise StickerAssetError('PNG chunk checksum is invalid.')
        if not seen and kind != b'IHDR':
            raise StickerAssetError('PNG must begin with its image header.')
        if kind == b'IHDR':
            if seen or size != 13:
                raise StickerAssetError('PNG has an invalid image header.')
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', body)
            if not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION:
                raise StickerAssetError(f'Sticker PNG dimensions must be from 1 to {MAX_DIMENSION} pixels per side.')
            if depth != 8 or color not in (2, 6):
                raise StickerAssetError('Only 8-bit RGB or RGBA PNG stickers are supported; convert other formats locally.')
            if compression or filtering or interlace not in (0, 1):
                raise StickerAssetError('PNG has unsupported or invalid image header settings.')
            decoded_size = width * height * 4
            if decoded_size > remaining:
                raise StickerAssetError('Sticker pack exceeds the 128 MiB decoded-pixel budget.')
            expected = _scanline_bytes(width, height, depth, color, interlace)
        elif kind in _COMPRESSED_METADATA:
            raise StickerAssetError('PNG compressed text/profile metadata is not supported; re-save a plain PNG locally.')
        elif kind in (b'acTL', b'fcTL', b'fdAT'):
            raise StickerAssetError('Animated PNG is not supported; use an explicit PNG frame-pack manifest.')
        elif kind == b'PLTE':
            raise StickerAssetError('PNG palettes are not supported; use plain 8-bit RGB or RGBA.')
        elif kind == b'tRNS':
            if b'tRNS' in seen or b'IDAT' in seen or color != 2 or size != 6 or any(value > 255 for value in struct.unpack('>HHH', body)):
                raise StickerAssetError('PNG has invalid transparency content or ordering.')
        elif kind == b'IDAT':
            if idat_ended:
                raise StickerAssetError('PNG pixel chunks must be consecutive.')
            image_parts.append(body)
        elif kind == b'IEND':
            if size or b'IDAT' not in seen or end != len(data):
                raise StickerAssetError('PNG has an invalid ending or trailing content.')
            ended = True
        elif kind in metadata:
            if kind != b'tEXt' and kind in seen:
                raise StickerAssetError('PNG contains duplicate metadata chunks.')
            if kind in (b'sRGB', b'gAMA', b'cHRM', b'sBIT', b'bKGD', b'pHYs') and b'IDAT' in seen:
                raise StickerAssetError('PNG metadata appears after its pixels.')
            valid = True
            if kind == b'sRGB':
                valid = size == 1 and body[0] <= 3
            elif kind == b'gAMA':
                valid = size == 4 and struct.unpack('>I', body)[0] == 45455
            elif kind == b'cHRM':
                valid = size == 32 and struct.unpack('>8I', body) == (31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)
            elif kind == b'pHYs':
                valid = size == 9 and body[-1] <= 1
            elif kind == b'tIME':
                valid = size == 7
            elif kind == b'sBIT':
                valid = size == (3 if color == 2 else 4) and all(1 <= value <= 8 for value in body)
            elif kind == b'bKGD':
                valid = size == 6 and all(value <= 255 for value in struct.unpack('>3H', body))
            elif kind == b'tEXt':
                valid = size <= MAX_TEXT_BYTES and 1 <= body.find(b'\x00') <= 79
            if not valid:
                raise StickerAssetError('PNG metadata is invalid or declares unsupported color settings; use plain sRGB PNG.')
        else:
            raise StickerAssetError('PNG contains an unsupported chunk; use a plain 8-bit sRGB PNG.')
        if b'IDAT' in seen and kind not in (b'IDAT', b'IEND'):
            idat_ended = True
        if kind in (b'IHDR', b'PLTE', b'tRNS', b'IDAT', b'IEND'):
            safe_chunks.append(data[offset:end])
        seen.add(kind)
        offset = end
    if not ended:
        raise StickerAssetError('PNG is missing its ending chunk.')
    _bounded_inflate(image_parts, expected, cancel)
    _cancelled(cancel)
    # Decode only the rendering-critical bytes. Non-pixel metadata is irrelevant
    # to stickers and is never interpreted by the decoder during validation.
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice
    from PySide6.QtGui import QImageReader

    encoded = QByteArray(b''.join(safe_chunks))
    buffer = QBuffer(encoded)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, b'PNG')
    reader.setAutoDetectImageFormat(False)
    image = reader.read()
    if image.isNull() or image.width() != width or image.height() != height:
        raise StickerAssetError('Sticker PNG could not be decoded completely.')
    actual = image.sizeInBytes()
    if actual > remaining:
        raise StickerAssetError('Sticker pack exceeds the 128 MiB decoded-pixel budget.')
    _cancelled(cancel)
    return width, height, max(actual, decoded_size)


def load_sticker(path: str | os.PathLike[str], cancel: threading.Event | None = None) -> StickerAsset:
    """Validate a local PNG or version-1 JSON frame pack and hash its inputs.

    The digest is independent of the containing directory and JSON whitespace.
    It covers semantic settings, ordered manifest filenames, and every PNG byte.
    PNG files are fully validated and decoded, one at a time, within fixed bounds.
    """
    source, frames, settings, names = _inputs(path, cancel)
    budget = MAX_DECODED_BYTES
    width = height = 0
    hashes = []
    for frame, name in zip(frames, names):
        _cancelled(cancel)
        data = _read_bounded(frame, MAX_PNG_BYTES, cancel)
        w, h, size = _decode_png(data, budget, cancel)
        if width and (w, h) != (width, height):
            raise StickerAssetError('All sticker frame-pack PNG images must have the same dimensions.')
        width, height = w, h
        budget -= size
        hashes.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest()})
    identity = {'schema': 'lumen-sticker-v1', 'kind': source.suffix.lower()[1:],
                'version': settings['version'], 'width': width, 'height': height,
                'frame_duration': settings['frame_duration'], 'loop': settings['loop'], 'frames': hashes}
    canonical = json.dumps(identity, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('utf-8')
    digest = hashlib.sha256(canonical).hexdigest()
    _cancelled(cancel)
    return StickerAsset(width, height, frames, settings['frame_duration'], settings['loop'], digest,
                        tuple(item['sha256'] for item in hashes))


def verify_sticker(path: str | os.PathLike[str], expected_digest: str,
                   cancel: threading.Event | None = None) -> StickerAsset:
    """Re-validate and require the imported semantic/content identity."""
    if not isinstance(expected_digest, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', expected_digest):
        raise StickerAssetError('Sticker expected SHA-256 digest must contain 64 hexadecimal characters.')
    asset = load_sticker(path, cancel)
    if not hmac.compare_digest(asset.digest, expected_digest.lower()):
        raise StickerAssetError('Sticker assets changed since import. Import the sticker again to approve the new content.')
    return asset


def builtin_stickers() -> tuple[tuple[str, Path], ...]:
    """Return the three bundled original stickers, with UI labels and local paths."""
    root = Path(__file__).resolve().parent / 'assets' / 'stickers'
    return (('Spark star', root / 'spark_star.png'),
            ('Check bubble', root / 'check_bubble.png'),
            ('Pulse ring', root / 'pulse_ring' / 'sticker.json'))


def read_sticker_frame(path: str | os.PathLike[str], cancel=None) -> bytes:
    """Return bounded, validated bytes for an already-selected absolute PNG frame.

    This does not resolve symlink aliases: callers pass canonical frame paths
    returned by load_sticker/verify_sticker. Decode these returned bytes with an
    explicit PNG decoder, rather than reopening the mutable source path.
    """
    try:
        text = os.fspath(path)
        if not isinstance(text, str) or text.startswith(('//', '\\\\')) or any(ord(c) < 32 for c in text):
            raise StickerAssetError('Sticker frame must be an ordinary local PNG path.')
        frame = Path(text)
    except (TypeError, ValueError) as exc:
        raise StickerAssetError('Sticker frame must be an absolute PNG file path.') from exc
    if not frame.is_absolute() or frame.suffix.lower() != '.png' or '..' in frame.parts:
        raise StickerAssetError('Sticker frame must be an absolute PNG file path.')
    data = _read_bounded(frame, MAX_PNG_BYTES, cancel)
    _decode_png(data, MAX_DECODED_BYTES, cancel)
    return data

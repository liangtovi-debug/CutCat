"""Verify caption shaping before importing Pillow's font extension.

Windows uses the locally built, receipt-bound FriBidi shared library. No download
or system installation occurs here. Other hosts retain their native provider.
"""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import sys

VERSION = '1.0.18'
SOURCE_SHA256 = '1de5e8073d035388b71d0b2d452b1da1f8caeea8a7702997b401fe1b6250727f'
_handles = []
_loaded = False


class CaptionRuntimeError(RuntimeError):
    pass


def runtime_directory():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'lumen/assets/native/fribidi'
    configured = os.environ.get('LUMEN_FRIBIDI_DIR')
    return Path(configured) if configured else Path(__file__).resolve().parents[1] / 'build/runtime/fribidi'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_windows_payload(directory):
    directory = Path(directory).resolve()
    try:
        receipt = json.loads((directory / 'receipt.json').read_text(encoding='utf-8'))
        if (receipt['version'] != VERSION or receipt['architecture'] != 'x86_64'
                or receipt['source_sha256'] != SOURCE_SHA256):
            raise ValueError('Unexpected FriBidi source or architecture')
        for name in ('fribidi.dll', 'COPYING', 'fribidi-source.tar.xz', 'BUILDING.txt'):
            path = directory / name
            if path.is_symlink() or digest(path) != receipt['files'][name]:
                raise ValueError('FriBidi payload digest mismatch: ' + name)
        if digest(directory / 'fribidi-source.tar.xz') != SOURCE_SHA256:
            raise ValueError('FriBidi source digest mismatch')
        binary = (directory / 'fribidi.dll').read_bytes()
        offset = struct.unpack_from('<I', binary, 0x3c)[0]
        if binary[:2] != b'MZ' or binary[offset:offset+4] != b'PE\0\0' or struct.unpack_from('<H', binary, offset+4)[0] != 0x8664:
            raise ValueError('FriBidi must be a Windows x64 PE library')
        if 'GNU LESSER GENERAL PUBLIC LICENSE' not in (directory / 'COPYING').read_text():
            raise ValueError('Missing FriBidi license')
    except (OSError, ValueError, KeyError, TypeError, struct.error) as exc:
        raise CaptionRuntimeError('Caption shaping runtime is missing or invalid; run scripts/provision_caption_runtime.py --build on Windows x64 or reinstall the verified draft.') from exc
    return receipt


def initialize():
    global _loaded
    if _loaded or sys.platform != 'win32':
        return
    if struct.calcsize('P') != 8 or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise CaptionRuntimeError('This caption runtime supports Windows x64 only.')
    directory = runtime_directory().resolve()
    verify_windows_payload(directory)
    try:
        handle = os.add_dll_directory(str(directory))
        _handles.append(handle)  # Keep the explicit search directory alive.
        library = ctypes.WinDLL(str(directory / 'fribidi.dll'), winmode=0x1100)
        _handles.append(library)
        # Confirm the module selected by its basename is our absolute DLL.
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        kernel.GetModuleHandleW.restype = ctypes.c_void_p
        kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
        buffer = ctypes.create_unicode_buffer(32768)
        module = kernel.GetModuleHandleW('fribidi.dll')
        if not module or not kernel.GetModuleFileNameW(module, buffer, len(buffer)) or Path(buffer.value).resolve() != directory / 'fribidi.dll':
            raise CaptionRuntimeError('Caption shaping loaded an unexpected FriBidi library.')
    except OSError as exc:
        raise CaptionRuntimeError('Windows could not load the verified caption shaping library.') from exc
    _loaded = True


def require_capability():
    initialize()
    from PIL import features
    from PIL import ImageFont
    if not features.check_feature('raqm'):
        raise CaptionRuntimeError('Caption layout requires Pillow with RAQM text shaping support; no BASIC fallback is allowed.')
    # Exercise a real shaping call with Pillow's built-in scalable font.
    default = ImageFont.load_default(size=12)
    default.path.seek(0)
    shaped = ImageFont.truetype(default.path, 12, layout_engine=ImageFont.Layout.RAQM)
    if shaped.getlength('office') <= 0:
        raise CaptionRuntimeError('Caption shaping produced invalid glyph advances.')
    if sys.platform == 'win32' and features.version_feature('fribidi') != VERSION:
        raise CaptionRuntimeError('Pillow did not use the pinned FriBidi version.')
    return {name: features.version_feature(name) for name in ('raqm', 'fribidi', 'harfbuzz')}

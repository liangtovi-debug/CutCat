"""Read-only local component inspection. Never execute runtimes or fetch assets.

Metadata availability is deliberately not an inference or compatibility test.
Hash scans are opt-in and should be called on a cancellable background worker.
Preferences are application-local paths, not project data or runtime overrides.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import metadata, util
import json
import os
import re
from pathlib import Path
import stat
import sys
import tempfile
from typing import Mapping

from .file_identity import is_plain_file, path_matches_descriptor, same_file_state
from .voice_catalog import get_pack

SETTINGS_KEYS = ('asr_runtime', 'asr_model', 'tts_model_dir', 'tts_pack_version',
                 'search_model', 'search_tokenizer', 'assistant_runtime', 'assistant_model')
MAX_PREFERENCES_BYTES = 64 * 1024
MAX_PATH_CHARS = 4096
# Kept here to avoid importing modules with optional/native dependencies.
SEARCH_MODEL_SHA256 = '98a01d88b7de996cdea58c32ca71208c09968d143798814b2ea09d3439dc334f'
SEARCH_TOKENIZER_SHA256 = '2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8'


class DiagnosticCancelled(ValueError):
    pass


@dataclass(frozen=True)
class DiagnosticCheck:
    component: str
    key: str
    status: str
    message: str
    recommendation: str
    path: str = ''
    version: str = ''
    sha256: str = ''


@dataclass(frozen=True)
class DiagnosticReport:
    checks: tuple[DiagnosticCheck, ...]
    inference_tested: bool = False


def _cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise DiagnosticCancelled('Component inspection cancelled.')


def _path(value):
    if not isinstance(value, (str, Path)) or len(str(value)) > MAX_PATH_CHARS or '\0' in str(value):
        raise ValueError('Invalid local path.')
    raw = str(value)
    if raw.startswith(('//', '\\\\')) or (re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', raw)
            and not (os.name == 'nt' and re.match(r'^[A-Za-z]:[\\/]', raw))):
        raise ValueError('Only explicit local paths are supported, not URLs or network paths.')
    expanded = Path(value).expanduser()
    if str(expanded).startswith(('//', '\\\\')):
        raise ValueError('Only explicit local paths are supported, not URLs or network paths.')
    return expanded.absolute()


def _read_file(path, limit, cancel=None, hash_file=False, prefix_limit=4096):
    """Bounded descriptor reads; reject special files, leaf links and races."""
    _cancel(cancel)
    before = path.lstat()
    if not is_plain_file(before) or not 0 < before.st_size <= limit:
        raise ValueError('Select a nonempty regular file within the size limit.')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) |
                 getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not is_plain_file(opened) or not path_matches_descriptor(before, opened):
            raise ValueError('File changed during inspection.')
        prefix = stream.read(min(prefix_limit, limit))
        digest = ''
        if hash_file:
            hasher = hashlib.sha256(prefix)
            total = len(prefix)
            while True:
                _cancel(cancel)
                data = stream.read(min(1024 * 1024, limit - total + 1))
                if not data:
                    break
                total += len(data)
                if total > limit:
                    raise ValueError('File exceeds the size limit.')
                hasher.update(data)
            if total != before.st_size:
                raise ValueError('File changed during inspection.')
            digest = hasher.hexdigest()
        _cancel(cancel)
        if (not same_file_state(opened, os.fstat(stream.fileno())) or
                not same_file_state(before, path.lstat())):
            raise ValueError('File changed during inspection.')
    return prefix, digest


def _file_check(component, key, value, limit, cancel, hashes, *, expected='', executable=False, gguf=False):
    recommendation = 'Select the documented local file. No file is downloaded or executed by this check.'
    if not value:
        return DiagnosticCheck(component, key, 'unset', 'No local file selected.', recommendation)
    try:
        path = _path(value)
        prefix, digest = _read_file(path, limit, cancel, hashes)
        if executable and os.name != 'nt' and not os.access(path, os.X_OK):
            raise ValueError('The selected runtime is not executable.')
        if gguf and not prefix.startswith(b'GGUF'):
            raise ValueError('The selected file does not have a GGUF header.')
        if not executable and prefix.startswith(b'version https://git-lfs.github.com/spec/'):
            raise ValueError('The selected file is a Git LFS pointer, not a model asset.')
        if expected and hashes and digest != expected:
            return DiagnosticCheck(component, key, 'invalid', 'File hash does not match the supported release.', recommendation, str(path), sha256=digest)
        status = 'hash_verified' if hashes and expected else 'metadata_available'
        message = ('Supported release hash verified; inference has not been tested.' if status == 'hash_verified'
                   else 'Local file metadata available; compatibility and inference have not been tested.')
        return DiagnosticCheck(component, key, status, message, 'Use the existing feature to test inference explicitly.', str(path), sha256=digest)
    except FileNotFoundError:
        return DiagnosticCheck(component, key, 'missing', 'Selected local file is missing.', recommendation, str(value))
    except (OSError, ValueError) as exc:
        if isinstance(exc, DiagnosticCancelled):
            raise
        return DiagnosticCheck(component, key, 'invalid', str(exc), recommendation, str(value))


def _dependency(component, package, module, expected, cancel):
    _cancel(cancel)
    advice = ('Use a build containing the documented optional runtime. Installing packages into another Python does not modify this packaged app.'
              if getattr(sys, 'frozen', False) else
              'Install the documented optional dependency in the Python environment running CutCat, then restart the app.')
    try:
        version = metadata.version(package)
    except metadata.PackageNotFoundError:
        return DiagnosticCheck(component, package, 'unavailable', 'Optional runtime distribution metadata is unavailable; inference has not been tested.', advice)
    except (OSError, ValueError):
        return DiagnosticCheck(component, package, 'unavailable', 'Optional runtime distribution metadata could not be read.', advice)
    # Top-level spec only: dotted names can import a parent package.
    try:
        available = util.find_spec(module) is not None
    except (ImportError, ValueError, AttributeError):
        available = False
    if not available:
        return DiagnosticCheck(component, package, 'unavailable', 'Distribution metadata exists but the runtime module is unavailable.', advice, version=version)
    if version != expected:
        return DiagnosticCheck(component, package, 'invalid', 'Installed version differs from the documented runtime version.', advice, version=version)
    return DiagnosticCheck(component, package, 'metadata_available', 'Optional runtime metadata available; native loading and inference have not been tested.', 'Use the existing feature to test inference explicitly.', version=version)


def diagnose_components(settings: Mapping[str, str], *, verify_hashes=False, cancel=None):
    """Inspect explicit paths without importing or executing optional runtimes."""
    checks = []
    for component, key, limit, executable, gguf, expected in (
        ('asr', 'asr_runtime', 256 * 1024**2, True, False, ''),
        ('asr', 'asr_model', 4 * 1024**3, False, False, ''),
        ('search', 'search_model', 300 * 1024**2, False, False, SEARCH_MODEL_SHA256),
        ('search', 'search_tokenizer', 32 * 1024**2, False, False, SEARCH_TOKENIZER_SHA256),
        ('assistant', 'assistant_runtime', 256 * 1024**2, True, False, ''),
        ('assistant', 'assistant_model', 2 * 1024**3, False, True, ''),
    ):
        _cancel(cancel)
        checks.append(_file_check(component, key, settings.get(key, ''), limit, cancel,
                                  verify_hashes, expected=expected, executable=executable, gguf=gguf))
    root = settings.get('tts_model_dir', '')
    try:
        pack = get_pack(settings.get('tts_pack_version') or 'v1.0')
    except ValueError as exc:
        checks.append(DiagnosticCheck('tts', 'tts_pack_version', 'invalid', str(exc), 'Select a documented local TTS pack version.'))
        pack = None
    if not root:
        checks.append(DiagnosticCheck('tts', 'tts_model_dir', 'unset', 'No local pack directory selected.', 'Select an extracted documented local TTS pack.'))
    elif pack is not None:
        try:
            directory = _path(root)
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Select a regular local directory, without symbolic links.')
            checks.append(DiagnosticCheck('tts', 'tts_model_dir', 'metadata_available', 'Local pack directory exists; required assets are checked separately.', 'Inference has not been tested.', str(directory)))
            for name in pack.required_files:
                _cancel(cancel)
                expected = pack.expected_hashes.get(name, '')
                checks.append(_file_check('tts', name, directory / name, 2 * 1024**3, cancel, verify_hashes, expected=expected))
            for name in pack.required_dirs:
                _cancel(cancel)
                asset = directory / name
                try:
                    info = asset.lstat()
                    valid = stat.S_ISDIR(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400
                    status = 'metadata_available' if valid else 'invalid'
                except FileNotFoundError:
                    status = 'missing'
                checks.append(DiagnosticCheck('tts', name, status, 'Pack data directory metadata only; contents have not been validated.', 'Use the complete documented extracted pack.', str(asset)))
        except (OSError, ValueError) as exc:
            if isinstance(exc, DiagnosticCancelled):
                raise
            checks.append(DiagnosticCheck('tts', 'tts_model_dir', 'missing' if isinstance(exc, FileNotFoundError) else 'invalid', str(exc), 'Select an extracted documented local TTS pack.', str(root)))
    for component, package, module, expected in (
        ('tts', 'sherpa-onnx', 'sherpa_onnx', '1.12.26'),
        ('tts', 'sherpa-onnx-core', 'sherpa_onnx', '1.12.26'),
        ('search', 'onnxruntime', 'onnxruntime', '1.22.1'),
        ('search', 'tokenizers', 'tokenizers', '0.21.4'),
    ):
        checks.append(_dependency(component, package, module, expected, cancel))
    _cancel(cancel)
    return DiagnosticReport(tuple(checks))


def _preferences(settings):
    result = {}
    for key in SETTINGS_KEYS:
        value = settings.get(key, '')
        if not isinstance(value, str) or len(value) > MAX_PATH_CHARS or '\0' in value:
            raise ValueError('Invalid component preference.')
        result[key] = value
    return result


def load_preferences(path):
    """Read a bounded, versioned application-local preference file."""
    path = _path(path)
    try:
        # Reading at most 64 KiB also rejects special files before opening.
        prefix, _ = _read_file(path, MAX_PREFERENCES_BYTES, prefix_limit=MAX_PREFERENCES_BYTES)
        if path.stat().st_size > len(prefix):
            raise ValueError('Component preferences exceed the size limit.')
        try:
            data = json.loads(prefix)
        except RecursionError as exc:
            raise ValueError('Invalid component preferences format.') from exc
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('settings'), dict):
            raise ValueError('Invalid component preferences format.')
        return _preferences(data['settings'])
    except FileNotFoundError:
        return {}


def save_preferences(path, settings):
    """Atomically persist paths only. Does not alter feature runtime settings."""
    path = _path(path)
    data = json.dumps({'version': 1, 'settings': _preferences(settings)}, ensure_ascii=True).encode('utf-8')
    if len(data) > MAX_PREFERENCES_BYTES:
        raise ValueError('Component preferences exceed the size limit.')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('Preference file must not be a symbolic link.')
    fd, temporary = tempfile.mkstemp(prefix='.component-setup-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass

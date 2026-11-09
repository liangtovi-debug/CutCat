"""Versioned explicit-local speech synthesis. This module never downloads anything."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import subprocess
import stat
import time
import sys
import tempfile
import wave

from .voice_catalog import get_pack, get_voice
from .file_identity import is_plain_file, path_matches_descriptor, same_file_state


class TTSError(ValueError):
    pass


class TTSCancelled(TTSError):
    pass


def _validate_silence_scale(value):
    # Kokoro retains this fraction of its detected native pauses. Keep the
    # original 0.2 behavior by default and never request expansion above 1.0.
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not 0.2 <= value <= 1.0 or not math.isfinite(value)):
        raise TTSError('Pause preservation must be finite and between 0.2 and 1.0.')


@dataclass(frozen=True)
class TTSConfig:
    model_dir: str
    voice_id: int = 0
    speed: float = 1.0
    threads: int = 2
    pack_version: str = 'v1.0'
    silence_scale: float = 0.2

    def __post_init__(self):
        _validate_silence_scale(self.silence_scale)


@dataclass(frozen=True)
class TTSProvenance:
    text: str
    engine: str
    engine_version: str
    model_sha256: str
    voice_sha256: str
    assets_sha256: str
    voice_id: str
    speed: float
    sample_rate: int
    output_sha256: str
    created_utc: str
    silence_scale: float = 0.2
    sample_count: int | None = None

    def __post_init__(self):
        _validate_silence_scale(self.silence_scale)
        if self.sample_count is not None and (
                type(self.sample_count) is not int or not 1 <= self.sample_count <= 2**53 - 1):
            raise TTSError('Speech sample count must be a positive supported integer.')


@dataclass(frozen=True)
class SynthesisResult:
    path: str
    duration: float
    sample_rate: int
    provenance: TTSProvenance


# Compatibility export: old callers retain the exact documented v1.0 mapping.
VOICE_NAMES = [voice.name for voice in get_pack('v1.0').voices]
SUPPORTED_VOICES = tuple(enumerate(VOICE_NAMES))
_REQUIRED = get_pack('v1.0').required_files


def _cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise TTSCancelled('Speech synthesis cancelled.')


MAX_ASSET_BYTES = 2 * 1024 ** 3
MAX_WORKER_SECONDS = 1800
MAX_WORKER_LOG_BYTES = 8 * 1024 ** 2
MAX_OUTPUT_BYTES = 512 * 1024 ** 2


def _hash(path, cancel):
    # Reject devices/FIFOs and symlinks before opening. Nonblocking/no-follow also
    # closes the check/open race on platforms that support these flags.
    _cancel(cancel)
    before = path.lstat()
    if not is_plain_file(before):
        raise TTSError('Model/audio assets must be regular files, without symbolic links or special files.')
    if before.st_size > MAX_ASSET_BYTES:
        raise TTSError('An asset exceeds the 2 GiB safety limit.')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    result = hashlib.sha256(); total = 0
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not is_plain_file(opened) or not path_matches_descriptor(before, opened):
            raise TTSError('An asset changed while being opened; operation canceled.')
        while data := stream.read(1024 * 1024):
            _cancel(cancel)
            total += len(data)
            if total > before.st_size or total > MAX_ASSET_BYTES:
                raise TTSError('An asset changed size while being read; operation canceled.')
            result.update(data)
        after = os.fstat(stream.fileno())
    now = path.lstat()
    if (total != before.st_size or not is_plain_file(now) or not same_file_state(opened, after)
            or not same_file_state(before, now) or not path_matches_descriptor(now, after)):
        raise TTSError('An asset changed while being read; operation canceled.')
    return result.hexdigest()


def _validate_controls(text, config):
    """Validate before filesystem access or any native worker construction."""
    if not isinstance(text, str) or not text.strip() or len(text) > 100000 or '\x00' in text:
        raise TTSError('Enter nonempty text, at most 100,000 characters, without NUL characters.')
    if not isinstance(config, TTSConfig):
        raise TTSError('A TTSConfig is required.')
    try:
        pack = get_pack(config.pack_version)
        get_voice(config.pack_version, config.voice_id)
    except ValueError as exc:
        raise TTSError(str(exc)) from exc
    if len(text) > pack.max_text_chars:
        raise TTSError(f'{pack.display_name} accepts at most {pack.max_text_chars} characters per take. Split the reading text explicitly before generating.')
    if isinstance(config.speed, bool) or not isinstance(config.speed, (int, float)) or not math.isfinite(config.speed) or not 0.5 <= config.speed <= 2.0:
        raise TTSError('Speech speed must be between 0.5 and 2.0.')
    if type(config.threads) is not int or not 1 <= config.threads <= 4:
        raise TTSError('Threads must be an integer from 1 to 4.')
    if pack.backend == 'melo' and config.threads != 2:
        raise TTSError('Melo Chinese-English requires 2 CPU threads.')
    _validate_silence_scale(config.silence_scale)
    return pack


def _validate(text, config, output_path):
    pack = _validate_controls(text, config)
    try:
        model = Path(config.model_dir).expanduser().resolve(strict=True)
        output = Path(output_path).expanduser().absolute()
    except (TypeError, ValueError, OSError) as exc:
        raise TTSError('Select existing local model and output directories.') from exc
    if ',' in str(model):
        raise TTSError('Model directory cannot contain commas (engine path-list separator).')
    if (not model.is_dir()
            or any(not (model / name).is_file() or not (model / name).stat().st_size for name in pack.required_files)
            or any(not (model / name).is_dir() for name in pack.required_dirs)):
        raise TTSError(f'Incomplete {pack.display_name} local model pack. See docs/LOCAL_TTS.md; no automatic download is performed.')
    if output.suffix.lower() != '.wav' or not output.parent.is_dir():
        raise TTSError('Select a WAV filename in an existing local directory.')
    if output.resolve().is_relative_to(model):
        raise TTSError('Save synthesized speech outside the model pack directory.')
    if os.path.lexists(output):
        raise TTSError('Output already exists; choose a new WAV filename.')
    return model, output


def _assets(model, cancel):
    entries = []
    total = 0
    directories = 0
    for directory, dirs, files in os.walk(model, followlinks=False):
        _cancel(cancel)
        directories += 1
        if directories > 10000:
            raise TTSError('Model pack exceeds the 10,000-directory safety limit.')
        for name in dirs + files:
            path = Path(directory) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise TTSError('Model pack must not contain symbolic links.')
            if not (stat.S_ISDIR(info.st_mode) if name in dirs else stat.S_ISREG(info.st_mode)):
                raise TTSError('Model pack must contain only regular files and directories, without special files.')
        for name in files:
            _cancel(cancel)
            path = Path(directory) / name
            total += path.lstat().st_size
            if len(entries) >= 10000 or total > MAX_ASSET_BYTES:
                raise TTSError('Model pack exceeds the 10,000-file / 2 GiB safety limit.')
            entries.append((path.relative_to(model).as_posix(), _hash(path, cancel)))
    return sorted(entries)


def _response(path):
    try:
        with path.open('rb') as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError('oversized response')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError('invalid response')
        return result
    except (OSError, ValueError, TypeError) as exc:
        raise TTSError('Local speech worker returned an invalid response.') from exc


def _worker_command(request, response):
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--lumen-tts-worker', str(request), str(response)]
    return [sys.executable, '-m', 'lumen.tts_worker', str(request), str(response)]


def _synthesize(text, config, output_path, progress=None, cancel=None):
    """Generate a new mono PCM WAV. Progress callback takes a float in [0, 1].

    Cancellation kills and reaps the isolated native process, including during
    model loading. No incomplete output is published; existing paths are refused.
    """
    _cancel(cancel)
    model, output = _validate(text, config, output_path)
    pack = get_pack(config.pack_version)
    if progress:
        progress(0.0)
    assets = _assets(model, cancel)
    model_hash = dict(assets)[pack.model_asset]
    voices_hash = dict(assets)[pack.voice_asset]
    _validate_pack_identity(config.pack_version, model_hash, voices_hash)
    _validate_asset_identity(pack, dict(assets))
    assets_hash = hashlib.sha256(json.dumps(assets, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
    with tempfile.TemporaryDirectory(prefix='.lumen-tts-', dir=output.parent) as directory:
        directory = Path(directory)
        request, response, wav = directory / 'request.json', directory / 'response.json', directory / 'speech.wav'
        request.write_text(json.dumps(dict(text=text, model_dir=str(model), voice_id=config.voice_id,
                                          speed=config.speed, threads=config.threads, pack_version=config.pack_version,
                                          backend=pack.backend,
                                          silence_scale=float(config.silence_scale), output=str(wav)), ensure_ascii=False), encoding='utf-8')
        with tempfile.TemporaryFile() as log:
            try:
                process = subprocess.Popen(_worker_command(request, response), stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=log, shell=False)
            except OSError as exc:
                raise TTSError(f'Cannot start local speech worker: {exc}') from exc
            try:
                started = time.monotonic()
                while True:
                    _cancel(cancel)
                    if time.monotonic() - started > MAX_WORKER_SECONDS:
                        raise TTSError('Local speech generation exceeded the 30-minute safety limit. Try a shorter passage.')
                    if os.fstat(log.fileno()).st_size > MAX_WORKER_LOG_BYTES:
                        raise TTSError('Local speech worker exceeded the diagnostic size safety limit.')
                    if wav.exists() and wav.stat().st_size > MAX_OUTPUT_BYTES:
                        raise TTSError('Local speech output exceeded the 512 MiB safety limit; nothing was published.')
                    try:
                        code = process.wait(timeout=0.1)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                _cancel(cancel)
                if os.fstat(log.fileno()).st_size > MAX_WORKER_LOG_BYTES:
                    raise TTSError('Local speech worker exceeded the diagnostic size safety limit.')
                if not code:
                    # Pinned native frontend can skip unsupported text and still
                    # return usable audio. Do not publish a known incomplete take.
                    # Startup 'Unknown token' messages are not request text loss.
                    log.seek(0)
                    diagnostics = log.read(MAX_WORKER_LOG_BYTES + 1)
                    if any(marker in diagnostics for marker in
                           (b"Ignore OOV '", b'Skip unknown phonemes. Unicode codepoint:')):
                        if pack.backend == 'melo':
                            raise TTSError('The speech engine reported unsupported characters (possibly punctuation) or sounds. No audio was saved. Revise the reading text or choose another model.')
                        raise TTSError('The speech engine skipped unsupported text or sounds. No audio was saved. Revise the reading text or choose another model.')
                if code:
                    # Avoid exposing text/model paths from native diagnostic dumps.
                    message = f'Local speech engine failed. Check the {pack.display_name} pack and optional sherpa-onnx runtime.'
                    if response.is_file():
                        data = _response(response)
                        message = data.get('error', message)
                    raise TTSError(message)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        try:
            data = _response(response)
            if not isinstance(data.get('engine_version'), str) or not data['engine_version'].strip():
                raise TTSError('Local speech worker returned no engine version.')
            if wav.lstat().st_size > MAX_OUTPUT_BYTES:
                raise TTSError('Local speech output exceeded the 512 MiB safety limit; nothing was published.')
            with wave.open(str(wav), 'rb') as audio:
                rate, frames = audio.getframerate(), audio.getnframes()
                if rate != pack.sample_rate or frames <= 0 or audio.getnchannels() != 1 or audio.getsampwidth() != 2:
                    raise TTSError(f'Engine returned invalid mono {pack.sample_rate} Hz PCM audio.')
            if _assets(model, cancel) != assets:
                raise TTSError('Model pack changed during speech generation; output was discarded.')
            provenance = TTSProvenance(text, pack.engine, str(data['engine_version']),
                                       model_hash, voices_hash, assets_hash, str(config.voice_id), float(config.speed),
                                       rate, _hash(wav, cancel),
                                       datetime.now(timezone.utc).isoformat(), float(config.silence_scale),
                                       sample_count=frames)
            _cancel(cancel)
            # Hard link publishes atomically and never overwrites, even in a race.
            os.link(wav, output)
        except (OSError, ValueError, KeyError, wave.Error) as exc:
            raise TTSError(f'Cannot publish synthesized WAV: {exc}') from exc
    if progress:
        progress(1.0)
    return SynthesisResult(str(output), frames / rate, rate, provenance)


def _validate_pack_identity(version, model_hash, voices_hash):
    pack = get_pack(version)
    if model_hash != pack.model_sha256 or voices_hash != pack.voices_sha256:
        raise TTSError(f'The model and voice assets do not match the verified {pack.display_name} pack. '
                       'Choose the matching version and complete official pack; no download is performed.')


def _validate_asset_identity(pack, assets):
    # Kokoro preserves its existing model/voice identity contract. Melo's
    # frontend files are also pinned individually to the evaluated revision.
    if any(assets.get(name) != expected for name, expected in pack.asset_sha256):
        raise TTSError(f'The local assets do not match the verified {pack.display_name} pack. '
                       'Choose the complete official pack; no download is performed.')


def synthesize(text, config, output_path, progress=None, cancel=None):
    """Synthesize in the shared local inference slot, with cancelable native work.

    Both preview and saved-take callers enter exactly once. The pack version and
    pause-preservation setting are recorded in immutable synthesis provenance.
    """
    from .local_ai import inference_slot
    _cancel(cancel)
    with inference_slot('tts', cancel):
        return _synthesize(text, config, output_path, progress, cancel)


RUNTIME_VERSION = '1.12.26'


def validate_pack(model_dir, pack_version='v1.0', cancel=None):
    """Verify local assets and runtime availability without synthesizing/fetching."""
    config = TTSConfig(model_dir=model_dir, pack_version=pack_version)
    # Validation never creates this path; use a guaranteed fresh local name.
    from uuid import uuid4
    probe = Path(tempfile.gettempdir()) / ('lumen-pack-check-' + uuid4().hex + '.wav')
    model, _ = _validate('Local pack validation', config, probe)
    pack = get_pack(pack_version)
    assets = dict(_assets(model, cancel))
    _validate_pack_identity(pack_version, assets[pack.model_asset], assets[pack.voice_asset])
    _validate_asset_identity(pack, assets)
    for package in ('sherpa-onnx', 'sherpa-onnx-core'):
        try:
            version = metadata.version(package)
        except metadata.PackageNotFoundError as exc:
            raise TTSError('Optional local TTS runtime is missing. Explicitly install the documented '
                           'sherpa-onnx runtime; nothing is downloaded by the app.') from exc
        if version != RUNTIME_VERSION:
            raise TTSError(f'This adapter is verified with {package}=={RUNTIME_VERSION}; '
                           f'the installed version is {version}. See docs/LOCAL_TTS.md.')
    return str(model), pack_version

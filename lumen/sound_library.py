"""Offline, hash-verified sound assets and bounded local audio import.

Imports are copied, decoded, and peak-limited without modifying the selected
file. A library entry is immutable by content hash; external audio rights are
never inferred from an import. No URL, playlist, or network decoder is exposed.
"""
from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import io
import json
import logging
import math
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import subprocess
import tempfile
import threading
import time
import wave

import numpy as np

from .file_identity import is_plain_file, path_matches_descriptor, same_file_state
from .sound_sources import SOURCE_FIELDS, validate_source_metadata

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_DURATION = 60
SAMPLE_RATE = 48000
CHANNELS = 2
MAX_SAMPLES = SAMPLE_RATE * MAX_DURATION
MAX_MANIFEST_BYTES = 256 * 1024
MAX_PROCESS_SECONDS = 90
SUPPORTED_EXTENSIONS = frozenset({'.wav', '.mp3', '.flac', '.ogg', '.opus',
                                 '.m4a', '.aac', '.aif', '.aiff', '.wma', '.caf'})
CATEGORIES = ('ui', 'transitions', 'impacts', 'notifications', 'atmosphere', 'foley')
_FORMATS = 'wav,mp3,flac,ogg,mov,aac,aiff,asf,caf'
_BUILTIN_ROOT = Path(__file__).parent / 'assets' / 'sounds'
_LOG = logging.getLogger(__name__)
_DIGEST = re.compile(r'^[0-9a-fA-F]{64}$')


class SoundLibraryError(ValueError):
    """The sound is missing, unsupported, unsafe, or changed."""


class SoundCancelled(InterruptedError):
    """Local sound import was cancelled."""


@dataclass(frozen=True, slots=True)
class SoundEntry:
    id: str
    name_en: str
    name_zh: str
    category: str
    tags: tuple[str, ...]
    path: Path
    sha256: str
    sample_rate: int
    sample_count: int
    license: str
    origin: str
    source_sha256: str = ''
    peak_dbfs: float = -120.0
    author: str = ''
    source_name: str = ''
    source_url: str = ''
    license_url: str = ''
    source_file: str = ''
    byte_count: int = 0

    @property
    def duration(self) -> float:
        return self.sample_count / self.sample_rate


def _cancelled(cancel=None):
    if cancel is not None and (cancel.is_set() if hasattr(cancel, 'is_set') else cancel()):
        raise SoundCancelled('Sound import cancelled.')


def _path(value) -> Path:
    try:
        text = os.fspath(value)
        if not isinstance(text, str) or not text or any(ord(c) < 32 for c in text):
            raise SoundLibraryError('Choose an ordinary local sound file or folder.')
        if text.startswith(('//', '\\\\')) or (os.name != 'nt' and '\\' in text):
            raise SoundLibraryError('Network paths and URLs are not supported.')
        drive = PureWindowsPath(text).drive if os.name == 'nt' else ''
        if ':' in text[len(drive):] or ('..' in Path(text).parts):
            raise SoundLibraryError('URLs and parent traversal are not supported.')
        return Path(text).expanduser().absolute()
    except (TypeError, OSError, RuntimeError) as exc:
        raise SoundLibraryError('Choose a valid local path.') from exc


def _is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def _directory(path: Path, create=False) -> Path:
    """Check every component before normalization can conceal a symlink."""
    try:
        for part in (*reversed(path.parents), path):
            try:
                info = part.lstat()
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    part.mkdir()
                except FileExistsError:
                    pass
                info = part.lstat()
            if _is_link(info) or not stat.S_ISDIR(info.st_mode):
                raise SoundLibraryError('Sound folders must not use symbolic links or junctions.')
        return path.resolve(strict=True)
    except OSError as exc:
        raise SoundLibraryError('Sound folder cannot be accessed safely.') from exc


def _open_directory(path: Path) -> int:
    """Use descriptor-relative traversal where the OS supports it."""
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read(path: Path, limit=MAX_SOURCE_BYTES, cancel=None) -> bytes:
    _cancelled(cancel)
    _directory(path.parent)
    try:
        before = path.lstat()
        if not is_plain_file(before) or before.st_size > limit:
            raise SoundLibraryError('Sound input must be an ordinary file within the size limit.')
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
        if os.open in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW'):
            directory = _open_directory(path.parent)
            try:
                fd = os.open(path.name, flags, dir_fd=directory)
            finally:
                os.close(directory)
        else:
            fd = os.open(path, flags)
        with os.fdopen(fd, 'rb') as handle:
            opened = os.fstat(handle.fileno())
            if not is_plain_file(opened) or not path_matches_descriptor(before, opened):
                raise SoundLibraryError('Sound input changed while being opened.')
            parts, size = [], 0
            while True:
                _cancelled(cancel)
                chunk = handle.read(min(256 * 1024, limit + 1 - size))
                if not chunk:
                    break
                parts.append(chunk)
                size += len(chunk)
                if size > limit:
                    raise SoundLibraryError('Sound file exceeds its size limit.')
            after = os.fstat(handle.fileno())
        current = path.lstat()
        _directory(path.parent)
        if (size != opened.st_size or not same_file_state(opened, after)
                or not same_file_state(before, current) or not path_matches_descriptor(current, after)):
            raise SoundLibraryError('Sound input changed while being read.')
        _cancelled(cancel)
        return b''.join(parts)
    except OSError as exc:
        if isinstance(exc, SoundCancelled):
            raise
        raise SoundLibraryError(f'Sound file cannot be read: {path.name}.') from exc


def _digest(value):
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise SoundLibraryError('Sound requires a valid SHA-256 digest.')
    return value.lower()


def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _wav_info(data: bytes):
    try:
        with wave.open(io.BytesIO(data), 'rb') as handle:
            rate, count, channels = handle.getframerate(), handle.getnframes(), handle.getnchannels()
            if (handle.getsampwidth() != 2 or handle.getcomptype() != 'NONE'
                    or channels not in (1, 2) or rate != SAMPLE_RATE
                    or not 0 < count <= MAX_SAMPLES):
                raise SoundLibraryError('Library sounds must be PCM16, 48 kHz, mono/stereo, and at most 60 seconds.')
            raw = handle.readframes(count + 1)
        if len(raw) != count * channels * 2:
            raise SoundLibraryError('Sound WAV is truncated or has an invalid sample count.')
        samples = np.frombuffer(raw, dtype='<i2').astype(np.int32)
        peak = float(np.max(np.abs(samples))) / 32768
        if not math.isfinite(peak) or peak > 0.501:
            raise SoundLibraryError('Sound WAV exceeds its safe peak limit.')
        return rate, count, max(-120.0, 20 * math.log10(peak)) if peak else -120.0
    except (wave.Error, EOFError, OverflowError) as exc:
        raise SoundLibraryError('Sound must be a valid PCM16 WAV file.') from exc


def verify_sound(entry: SoundEntry) -> None:
    """Recheck complete file identity, samples, rate, and peak before using it."""
    expected = _digest(entry.sha256)
    data = _read(_path(entry.path))
    if hashlib.sha256(data).hexdigest() != expected:
        raise SoundLibraryError('Sound changed since import; select or import it again.')
    if type(entry.byte_count) is not int or entry.byte_count < 0 or (entry.byte_count and len(data) != entry.byte_count):
        raise SoundLibraryError('Sound size metadata does not match its WAV file.')
    rate, count, peak = _wav_info(data)
    if (type(entry.sample_rate) is not int or type(entry.sample_count) is not int
            or (rate, count) != (entry.sample_rate, entry.sample_count)):
        raise SoundLibraryError('Sound metadata does not match its WAV samples.')
    if not _finite_number(entry.peak_dbfs) or abs(peak - entry.peak_dbfs) > 0.05:
        raise SoundLibraryError('Sound peak metadata does not match its WAV samples.')


def verify_sound_file(source_path, sha256, sample_rate, sample_count, cancel=None) -> None:
    """Validate project provenance without requiring catalog-only peak metadata."""
    expected = _digest(sha256)
    data = _read(_path(source_path), cancel=cancel)
    if hashlib.sha256(data).hexdigest() != expected:
        raise SoundLibraryError('Sound changed since import; select or import it again.')
    rate, count, _peak = _wav_info(data)
    if (type(sample_rate) is not int or type(sample_count) is not int
            or (sample_rate, sample_count) != (rate, count)):
        raise SoundLibraryError('Sound metadata does not match its WAV samples.')
    _cancelled(cancel)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SoundLibraryError('Sound metadata contains a duplicate field.')
        result[key] = value
    return result


def _constant(_value):
    raise SoundLibraryError('Sound metadata must use finite JSON values.')


def _json(path):
    try:
        return json.loads(_read(path, MAX_MANIFEST_BYTES).decode('utf-8'),
                          object_pairs_hook=_object, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, SoundLibraryError):
            raise
        raise SoundLibraryError('Sound metadata must be valid UTF-8 JSON.') from exc


def _entry(data, root, local=False, external=False):
    if not isinstance(data, dict):
        raise SoundLibraryError('Sound metadata must be an object.')
    required = {'id', 'name_en', 'name_zh', 'category', 'tags', 'file', 'sha256',
                'sample_rate', 'sample_count', 'peak_dbfs', 'license', 'origin'}
    if not required <= data.keys():
        raise SoundLibraryError('Sound metadata is incomplete.')
    for field in ('id', 'name_en', 'name_zh', 'category', 'license', 'origin'):
        value = data[field]
        if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 32 for c in value):
            raise SoundLibraryError('Sound metadata contains an invalid label.')
    if data['category'] not in (('local',) if local else CATEGORIES):
        raise SoundLibraryError('Sound category is not supported.')
    expected_license = ('User-imported / rights not verified' if local else
                        'CC0-1.0' if external else 'CutCat original / CC0-1.0')
    expected_origin = 'user-imported' if local else 'external-cc0' if external else 'procedural'
    if data['license'] != expected_license or data['origin'] != expected_origin:
        raise SoundLibraryError('Sound provenance metadata is invalid.')
    try:
        validate_source_metadata(data['origin'], data['license'], data)
    except ValueError as exc:
        raise SoundLibraryError(str(exc)) from exc
    if external and (type(data.get('byte_count')) is not int or not 0 < data['byte_count'] <= MAX_SOURCE_BYTES):
        raise SoundLibraryError('Sound size metadata is invalid.')
    tags = data['tags']
    if not isinstance(tags, list) or len(tags) > 64 or any(
            not isinstance(t, str) or not t or len(t) > 100 or any(ord(c) < 32 for c in t) for t in tags):
        raise SoundLibraryError('Sound tags are invalid.')
    filename = data['file']
    if (not isinstance(filename, str) or not filename or len(filename) > 200
            or any(c in filename for c in ('/', '\\', ':'))
            or any(ord(c) < 32 for c in filename) or Path(filename).name != filename
            or Path(filename).suffix.lower() != '.wav'):
        raise SoundLibraryError('Sound files must be contained WAV basenames.')
    digest = _digest(data['sha256'])
    source = _digest(data.get('source_sha256')) if local or external else digest
    if local and (filename != digest + '.wav' or data['id'] != 'local-' + digest):
        raise SoundLibraryError('Local sound names must match their content hash.')
    if (type(data['sample_rate']) is not int or data['sample_rate'] != SAMPLE_RATE
            or type(data['sample_count']) is not int or not 0 < data['sample_count'] <= MAX_SAMPLES
            or not _finite_number(data['peak_dbfs'])):
        raise SoundLibraryError('Sound sample or peak metadata is invalid.')
    entry = SoundEntry(data['id'], data['name_en'], data['name_zh'], data['category'],
                       tuple(tags), root / filename, digest, data['sample_rate'], data['sample_count'],
                       data['license'], data['origin'], source, float(data['peak_dbfs']),
                       **{name: data.get(name, '') for name in SOURCE_FIELDS},
                       byte_count=data.get('byte_count', 0))
    verify_sound(entry)
    return entry


def builtin_sounds() -> list[SoundEntry]:
    # PyInstaller macOS bundles intentionally link Frameworks data to Resources.
    # Only this trusted shipped root may canonicalize packaging links; user
    # imports, catalog child files, and project destinations remain no-follow.
    try:
        root = _directory(_path(_BUILTIN_ROOT).resolve(strict=True))
    except OSError as exc:
        raise SoundLibraryError('Built-in sound assets are missing.') from exc
    entries = []
    for filename, external in (('catalog.json', False), ('third_party_catalog.json', True)):
        catalog = _json(root / filename)
        if (not isinstance(catalog, dict) or type(catalog.get('version')) is not int
                or catalog['version'] != 1 or not isinstance(catalog.get('sounds'), list)
                or len(catalog['sounds']) > 1024):
            raise SoundLibraryError('Only sound catalog version 1 is supported.')
        entries.extend(_entry(item, root, external=external) for item in catalog['sounds'])
    if len({entry.id for entry in entries}) != len(entries) or len({entry.path for entry in entries}) != len(entries):
        raise SoundLibraryError('Sound catalog contains duplicate identifiers.')
    return entries


def local_sounds(root) -> list[SoundEntry]:
    """Read verified local entries; isolate corrupt or incomplete sidecars."""
    path = _path(root)
    if not path.exists() and not path.is_symlink():
        return []
    path = _directory(path)
    entries = []
    for sidecar in sorted(path.glob('*.json')):
        try:
            data = _json(sidecar)
            if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1):
                raise SoundLibraryError('Only local sound version 1 is supported.')
            entry = _entry(data, path, local=True)
            if sidecar.name != entry.sha256 + '.json':
                raise SoundLibraryError('Local sound sidecar name does not match its hash.')
            entries.append(entry)
        except (SoundLibraryError, KeyError, TypeError) as exc:
            _LOG.warning('Skipping invalid local sound %s: %s', sidecar.name, exc)
    return entries


def search_sounds(entries, query='', category='all') -> list[SoundEntry]:
    terms = str(query).casefold().split()
    return [entry for entry in entries if (category in ('', 'all', None) or entry.category == category)
            and all(term in ' '.join((entry.id, entry.name_en, entry.name_zh, entry.category,
                                    entry.author, entry.source_name, *entry.tags)).casefold() for term in terms)]


def _run(command, directory, output, limit, cancel=None):
    """Drain both pipes concurrently without ever writing beyond their caps."""
    error_path = directory / 'decoder-error.txt'
    started = time.monotonic()
    process = None
    overflow = threading.Event()
    readers = []
    failures = []

    def drain(pipe, path, cap):
        try:
            with pipe, path.open('wb') as handle:
                size = 0
                while True:
                    chunk = pipe.read(min(64 * 1024, cap + 1 - size))
                    if not chunk:
                        break
                    if size + len(chunk) > cap:
                        overflow.set()
                        break
                    handle.write(chunk)
                    size += len(chunk)
        except OSError as exc:
            failures.append(exc)
            overflow.set()

    try:
        _cancelled(cancel)
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        for pipe, path, cap in ((process.stdout, output, limit), (process.stderr, error_path, 64 * 1024)):
            thread = threading.Thread(target=drain, args=(pipe, path, cap), daemon=True)
            thread.start()
            readers.append(thread)
        while process.poll() is None:
            _cancelled(cancel)
            if time.monotonic() - started > MAX_PROCESS_SECONDS:
                raise SoundLibraryError('Sound decoding timed out.')
            if overflow.is_set():
                raise SoundLibraryError('Sound decoder exceeded its output limit.')
            time.sleep(0.025)
        for thread in readers:
            thread.join(timeout=5)
        if failures:
            raise SoundLibraryError('Cannot write decoded sound safely.')
        if overflow.is_set() or any(thread.is_alive() for thread in readers):
            raise SoundLibraryError('Sound decoder exceeded its output limit.')
        _cancelled(cancel)
        if process.returncode:
            raise SoundLibraryError('Cannot decode this audio file. Check its format and integrity.')
    except OSError as exc:
        if isinstance(exc, SoundCancelled):
            raise
        raise SoundLibraryError('FFmpeg and ffprobe must be installed to import audio.') from exc
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for thread in readers:
            thread.join(timeout=5)


def _unlink_identity(path, identity, directory=None):
    kwargs = {'dir_fd': directory} if directory is not None else {}
    name = path.name if directory is not None else path
    current = os.stat(name, follow_symlinks=False, **kwargs)
    if (identity.st_dev, identity.st_ino) == (current.st_dev, current.st_ino):
        os.unlink(name, **kwargs)


def _exclusive_copy(destination, expected, directory=None):
    """Fallback for filesystems without links; O_EXCL never replaces a file."""
    kwargs = {'dir_fd': directory} if directory is not None else {}
    name = destination.name if directory is not None else destination
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(name, flags, 0o600, **kwargs)
    owned = os.fstat(fd)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(expected)
            handle.flush()
            os.fsync(handle.fileno())
        return owned
    except BaseException:
        try:
            _unlink_identity(destination, owned, directory)
        except OSError:
            pass
        raise


def _publish(stage: Path, destination: Path, expected: bytes):
    """Publish without replacement; return identity only for newly created files.

    Hard links are atomic. On filesystems explicitly reporting links unsupported,
    an exclusive copy is fully flushed before its library sidecar is published.
    Permission denials never trigger an alternate write route.
    """
    _directory(destination.parent)
    before = destination.parent.stat()
    directory = None
    owned = None
    if os.link in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW'):
        directory = _open_directory(destination.parent)
    try:
        try:
            try:
                kwargs = {'dst_dir_fd': directory} if directory is not None else {}
                os.link(stage, destination.name if directory is not None else destination,
                        follow_symlinks=False, **kwargs)
                owned = stage.stat()
            except OSError as exc:
                if (exc.errno not in {errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}
                        and getattr(exc, 'winerror', None) not in (1, 50)):
                    raise
                owned = _exclusive_copy(destination, expected, directory)
        except FileExistsError:
            if _read(destination) != expected:
                raise SoundLibraryError('An existing sound asset conflicts with this import.')
        _directory(destination.parent)
        after = destination.parent.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise SoundLibraryError('Sound destination changed during import.')
        if _read(destination) != expected:
            raise SoundLibraryError('Sound destination changed during import.')
        return owned
    finally:
        if directory is not None:
            os.close(directory)


def _rollback_owned(path, owned, expected):
    """Delete only this import's unchanged file, never prior assets."""
    try:
        _directory(path.parent)
        current = path.lstat()
        if ((owned.st_dev, owned.st_ino) != (current.st_dev, current.st_ino)
                or not is_plain_file(current) or _read(path) != expected):
            return
        if os.unlink in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW'):
            directory = _open_directory(path.parent)
            try:
                _unlink_identity(path, owned, directory)
            finally:
                os.close(directory)
        else:
            path.unlink()
    except (OSError, SoundLibraryError) as exc:
        _LOG.warning('Could not remove cancelled sound import %s: %s', path.name, exc)


def copy_sound_asset(source_path, sha256, destination) -> Path:
    """Copy a verified WAV for a portable project, never replacing conflicts."""
    expected = _digest(sha256)
    data = _read(_path(source_path))
    if hashlib.sha256(data).hexdigest() != expected:
        raise SoundLibraryError('Sound changed since import; select or import it again.')
    _wav_info(data)
    target = _path(destination)
    if target.suffix.lower() != '.wav':
        raise SoundLibraryError('Portable sound destination must be a WAV file.')
    _directory(target.parent, create=True)
    try:
        with tempfile.TemporaryDirectory(prefix='.sound-copy-', dir=target.parent) as temp:
            stage = Path(temp) / 'asset.wav'
            stage.write_bytes(data)
            _publish(stage, target, data)
    except OSError as exc:
        raise SoundLibraryError('Cannot safely save the portable sound asset.') from exc
    return target.resolve(strict=True)


def import_sound(path, root, cancel=None) -> SoundEntry:
    """Import selected audio offline; normalize peak down to 0.5, never up."""
    _cancelled(cancel)
    source = _path(path)
    if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise SoundLibraryError('Choose a supported audio file, not a video, playlist, or URL.')
    original = _read(source, cancel=cancel)
    if not original:
        raise SoundLibraryError('Sound source is empty.')
    source_hash = hashlib.sha256(original).hexdigest()
    destination = _directory(_path(root), create=True)
    try:
        with tempfile.TemporaryDirectory(prefix='.sound-import-', dir=destination) as temp:
            stage = Path(temp)
            snapshot = stage / ('source' + source.suffix.lower())
            snapshot.write_bytes(original)
            probe_file = stage / 'probe.json'
            common = ['-v', 'error', '-protocol_whitelist', 'file,pipe', '-format_whitelist', _FORMATS]
            _run([os.environ.get('LUMEN_FFPROBE', 'ffprobe'), *common, '-show_streams',
                  '-show_format', '-of', 'json', str(snapshot)], stage, probe_file, 1024 * 1024, cancel)
            try:
                info = json.loads(probe_file.read_text('utf-8'), parse_constant=_constant)
                streams = info['streams']
                if not isinstance(streams, list) or not streams:
                    raise ValueError
                audios = [item for item in streams if item.get('codec_type') == 'audio']
                videos = [item for item in streams if item.get('codec_type') == 'video'
                          and not item.get('disposition', {}).get('attached_pic')]
                if not audios or videos:
                    raise SoundLibraryError('Choose an audio-only file; cover artwork is allowed.')
                durations = [item.get('duration') for item in (*audios, info.get('format', {}))]
                for value in durations:
                    if value in (None, '', 'N/A'):
                        continue
                    seconds = float(value)
                    if not math.isfinite(seconds) or seconds <= 0 or seconds > MAX_DURATION:
                        raise SoundLibraryError('Sound must have a finite duration of at most 60 seconds.')
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                if isinstance(exc, SoundLibraryError):
                    raise
                raise SoundLibraryError('Audio metadata is invalid.') from exc
            raw_path = stage / 'decoded.f32'
            # One extra frame detects long files whose duration metadata lies.
            _run([os.environ.get('LUMEN_FFMPEG', 'ffmpeg'), '-nostdin', *common,
                  '-i', str(snapshot), '-map', '0:a:0', '-vn', '-sn', '-dn',
                  '-t', str(MAX_DURATION + 1 / SAMPLE_RATE), '-ac', str(CHANNELS),
                  '-ar', str(SAMPLE_RATE), '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1'],
                 stage, raw_path, (MAX_SAMPLES + 1) * CHANNELS * 4, cancel)
            raw = _read(raw_path, (MAX_SAMPLES + 1) * CHANNELS * 4, cancel)
            if not raw or len(raw) % (CHANNELS * 4) or len(raw) > MAX_SAMPLES * CHANNELS * 4:
                raise SoundLibraryError('Decoded sound is empty, invalid, or longer than 60 seconds.')
            samples = np.frombuffer(raw, dtype='<f4')
            if not np.isfinite(samples).all():
                raise SoundLibraryError('Decoded sound contains invalid samples.')
            peak = float(np.max(np.abs(samples)))
            gain = min(1.0, 0.5 / peak) if peak else 1.0
            pcm = np.rint(samples.astype(np.float64) * gain * 32768).astype('<i2').tobytes()
            wav_path = stage / 'asset.wav'
            with wave.open(str(wav_path), 'wb') as handle:
                handle.setnchannels(CHANNELS)
                handle.setsampwidth(2)
                handle.setframerate(SAMPLE_RATE)
                handle.writeframes(pcm)
            wav = _read(wav_path, cancel=cancel)
            rate, count, peak_dbfs = _wav_info(wav)
            digest = hashlib.sha256(wav).hexdigest()
            # Decode only a snapshot, and reject edits to the original before
            # anything becomes a visible library entry.
            if hashlib.sha256(_read(source, cancel=cancel)).hexdigest() != source_hash:
                raise SoundLibraryError('Sound source changed during import; import it again.')
            name = source.stem[:200]
            data = {'version': 1, 'id': 'local-' + digest, 'name_en': name, 'name_zh': name,
                    'category': 'local', 'tags': ['local', '本地'], 'file': digest + '.wav',
                    'sha256': digest, 'sample_rate': rate, 'sample_count': count,
                    'peak_dbfs': peak_dbfs, 'license': 'User-imported / rights not verified',
                    'origin': 'user-imported', 'source_sha256': source_hash}
            sidecar = destination / (digest + '.json')
            _cancelled(cancel)
            if sidecar.exists() or sidecar.is_symlink():
                existing = _json(sidecar)
                if not isinstance(existing, dict) or existing.get('version') != 1:
                    raise SoundLibraryError('Existing sound metadata is invalid.')
                entry = _entry(existing, destination, local=True)
                if entry.sha256 != digest:
                    raise SoundLibraryError('An existing sound asset conflicts with this import.')
                _cancelled(cancel)
                return entry
            target_wav = destination / data['file']
            metadata = (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
            metadata_path = stage / 'asset.json'
            metadata_path.write_bytes(metadata)
            new_wav = new_sidecar = False
            try:
                new_wav = _publish(wav_path, target_wav, wav)
                _cancelled(cancel)
                new_sidecar = _publish(metadata_path, sidecar, metadata)
                _cancelled(cancel)
                entry = _entry(data, destination, local=True)
                _cancelled(cancel)
                return entry
            except BaseException:
                if new_sidecar:
                    _rollback_owned(sidecar, new_sidecar, metadata)
                if new_wav and not sidecar.exists() and not sidecar.is_symlink():
                    _rollback_owned(target_wav, new_wav, wav)
                raise
    except OSError as exc:
        if isinstance(exc, SoundCancelled):
            raise
        raise SoundLibraryError('Cannot safely import the local sound.') from exc

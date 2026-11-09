"""Fail-closed decoded-frame proof for cross dissolves, on owned byte snapshots.

Container duration and media metadata never prove a transition handle. Every
call rereads the original, even if a renderer has cached preview output.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from .file_identity import is_plain_file, path_matches_descriptor, same_file_state
from .media import ffprobe_binary
from .video_transition import plan_video_transitions

MAX_SOURCE_BYTES = 16 * 1024**3
MAX_PROBE_BYTES = 64 * 1024**2
MAX_SNAPSHOT_CACHE_BYTES = 16 * 1024**3
MIN_FREE_RESERVE_BYTES = 64 * 1024**2
CONTAINERS = 'matroska,webm,mov,avi,mpeg,mpegts,ogg,asf,nut,flv'
_SNAPSHOT_LOCK = threading.Lock()


class TransitionSourceError(ValueError):
    pass


class TransitionSourceCancelled(TransitionSourceError):
    pass


@dataclass(frozen=True)
class TransitionSourceProof:
    path: Path
    sha256: str
    frame_count: int


def _check(deadline, cancel):
    if cancel is not None and (cancel() if callable(cancel) else cancel.is_set()):
        raise TransitionSourceCancelled('Video transition source validation cancelled')
    if time.monotonic() >= deadline:
        raise TransitionSourceError('Video transition source validation timed out')


def _copy_hash(source, output, deadline, cancel, max_bytes=MAX_SOURCE_BYTES):
    """Read a verified plain descriptor once; hash exactly the bytes copied."""
    source = Path(source)
    before = source.lstat()
    if not is_plain_file(before) or before.st_size > min(MAX_SOURCE_BYTES, max_bytes):
        raise TransitionSourceError('Video transition source must be a plain local file under 16 GiB')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(source, flags)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not is_plain_file(opened) or not path_matches_descriptor(before, opened):
            raise TransitionSourceError('Video transition source changed while reading')
        digest = hashlib.sha256()
        total = 0
        while True:
            _check(deadline, cancel)
            block = stream.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > min(MAX_SOURCE_BYTES, max_bytes):
                raise TransitionSourceError('Video transition source must be a plain local file under 16 GiB')
            digest.update(block)
            if output is not None:
                output.write(block)
        closed = os.fstat(stream.fileno())
        after = source.lstat()
        if (total != before.st_size or not same_file_state(opened, closed)
                or not same_file_state(before, after)
                or not path_matches_descriptor(after, closed)):
            raise TransitionSourceError('Video transition source changed while reading')
    return digest.hexdigest()


def _snapshot(source, cache, deadline, cancel):
    # Concurrent preview jobs must not each reserve the same remaining budget.
    # The cache is owned by this process; wait in cancellable bounded increments.
    while not _SNAPSHOT_LOCK.acquire(timeout=.05):
        _check(deadline, cancel)
    try:
        return _snapshot_locked(source, cache, deadline, cancel)
    finally:
        _SNAPSHOT_LOCK.release()


def _snapshot_locked(source, cache, deadline, cancel):
    _check(deadline, cancel)
    source_state = Path(source).lstat()
    if not is_plain_file(source_state) or source_state.st_size > MAX_SOURCE_BYTES:
        raise TransitionSourceError('Video transition source must be a plain local file under 16 GiB')
    needed = source_state.st_size
    if shutil.disk_usage(cache).free < needed + MIN_FREE_RESERVE_BYTES:
        raise TransitionSourceError('Not enough free disk space for video transition source snapshots')
    used = 0
    for entry in cache.iterdir():
        _check(deadline, cancel)
        info = entry.lstat()
        if is_plain_file(info):
            used += info.st_size
    if used + needed > MAX_SNAPSHOT_CACHE_BYTES:
        raise TransitionSourceError('Video transition source snapshot cache exceeds its safe size limit')
    name = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.transition-', dir=cache, delete=False) as output:
            name = Path(output.name)
            digest = _copy_hash(source, output, deadline, cancel, needed)
        target = cache / ('transition-source-' + digest + '.bin')
        if target.exists() or target.is_symlink():
            # A path or matching size/mtime is not evidence of matching bytes.
            if _copy_hash(target, None, deadline, cancel) != digest:
                raise TransitionSourceError('Video transition snapshot content changed')
            name.unlink()
        else:
            os.chmod(name, 0o444)
            os.replace(name, target)
        return target, digest
    finally:
        if name is not None and name.exists():
            name.unlink()


def _probe(path, deadline, cancel):
    command = [ffprobe_binary(), '-v', 'error', '-protocol_whitelist', 'file',
               '-format_whitelist', CONTAINERS, '-select_streams', 'V:0',
               '-show_frames', '-show_streams', '-show_entries',
               'frame=best_effort_timestamp:stream=time_base', '-of', 'json', str(path)]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    chunks = [bytearray(), bytearray()]
    overflow = threading.Event()
    failures = []

    def read(index, pipe):
        try:
            while True:
                block = pipe.read(65536)
                if not block:
                    break
                if len(chunks[index]) + len(block) > MAX_PROBE_BYTES:
                    overflow.set()
                    break
                chunks[index].extend(block)
        except Exception as exc:
            failures.append(exc)

    threads = [threading.Thread(target=read, args=(i, pipe), daemon=True)
               for i, pipe in enumerate((process.stdout, process.stderr))]
    try:
        for thread in threads:
            thread.start()
        while process.poll() is None:
            _check(deadline, cancel)
            if overflow.is_set():
                raise TransitionSourceError('Video transition decoded-frame proof exceeds safety limit')
            time.sleep(.01)
        for thread in threads:
            thread.join()
        _check(deadline, cancel)
        if overflow.is_set():
            raise TransitionSourceError('Video transition decoded-frame proof exceeds safety limit')
        if failures or process.returncode or chunks[1].strip():
            raise TransitionSourceError('Video transition source could not be decoded without errors')
        return json.loads(chunks[0])
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for thread in threads:
            if thread.ident is not None:
                thread.join()
        process.stdout.close()
        process.stderr.close()


def _decoded_count(data, fps, deadline=None, cancel=None):
    try:
        streams = data['streams']
        frames = data['frames']
        if len(streams) != 1 or not frames:
            raise ValueError()
        tick = Fraction(streams[0]['time_base'])
        if tick <= 0 or tick > 1 / fps:
            raise ValueError()
        origin = None
        previous = None
        for index, frame in enumerate(frames):
            if deadline is not None and index % 1024 == 0:
                _check(deadline, cancel)
            timestamp = frame['best_effort_timestamp']
            if type(timestamp) is not int:
                raise ValueError()
            if origin is None:
                origin = timestamp
            if previous is not None and timestamp <= previous:
                raise ValueError()
            if abs((timestamp - origin) * tick - index / fps) > tick / 2:
                raise TransitionSourceError('Video transition requires constant-frame-rate video matching project FPS')
            previous = timestamp
        return len(frames)
    except (KeyError, TypeError, ZeroDivisionError, ValueError) as exc:
        if isinstance(exc, TransitionSourceError):
            raise
        raise TransitionSourceError('Video transition source has no reliable decoded video timestamps') from exc


def prepare_transition_sources(project, cache_dir, timeout=30, cancel=None):
    """Return immutable decoded proofs keyed by media ID, without model recursion."""
    deadline = time.monotonic() + timeout
    _check(deadline, cancel)
    plans = plan_video_transitions(project)
    if not plans:
        return {}
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    sources = {media.id: media for media in project.media}
    proofs = {}
    try:
        for plan in plans:
            for side, clip, start in (('left', plan.left, plan.left_source_start),
                                      ('right', plan.right, plan.right_source_start)):
                if clip.media_id not in proofs:
                    path, digest = _snapshot(sources[clip.media_id].path, cache, deadline, cancel)
                    count = _decoded_count(_probe(path, deadline, cancel), Fraction(project.fps_num, project.fps_den), deadline, cancel)
                    proofs[clip.media_id] = TransitionSourceProof(path, digest, count)
                if start < 0 or start + plan.duration > proofs[clip.media_id].frame_count:
                    if side == 'left':
                        raise TransitionSourceError('Video transition is missing decoded left source handles')
                    raise TransitionSourceError('Video transition is missing decoded right source handles')
        return proofs
    except (OSError, json.JSONDecodeError) as exc:
        raise TransitionSourceError('Video transition source is unavailable or invalid; relink valid video media') from exc


def validate_transition_sources(project, timeout=30, cancel=None):
    with tempfile.TemporaryDirectory(prefix='cutcat-transition-proof-') as directory:
        try:
            prepare_transition_sources(project, directory, timeout, cancel)
        finally:
            # Also clean snapshots when validation fails midway on Windows.
            for path in Path(directory).iterdir():
                if is_plain_file(path.lstat()):
                    os.chmod(path, 0o600)


def decoded_frame_count(path, fps, timeout=30, cancel=None):
    """Strict actual video frame count and project-CFR proof for an owned file."""
    deadline = time.monotonic() + timeout
    _check(deadline, cancel)
    try:
        return _decoded_count(_probe(Path(path), deadline, cancel), Fraction(fps), deadline, cancel)
    except (OSError, json.JSONDecodeError) as exc:
        raise TransitionSourceError('Video transition source is unavailable or invalid; relink valid video media') from exc


@dataclass(frozen=True)
class TransitionSourceIdentity:
    """One preflight observation, not authority to bypass renderer rehashing."""
    media_id: str
    original_path: Path
    path_state: os.stat_result
    descriptor_state: os.stat_result
    sha256: str


def _source_identity(media_id, path, digest=''):
    original = Path(path).expanduser().absolute()
    before = original.lstat()
    if not is_plain_file(before) or before.st_size > MAX_SOURCE_BYTES:
        raise TransitionSourceError('Video transition source must be a plain local file under 16 GiB')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(original, flags)
    try:
        descriptor = os.fstat(fd)
        after = original.lstat()
        if (not is_plain_file(descriptor) or not same_file_state(before, after)
                or not path_matches_descriptor(after, descriptor)):
            raise TransitionSourceError('Video transition source changed while reading')
        return TransitionSourceIdentity(media_id, original, after, descriptor, digest)
    finally:
        os.close(fd)


def verify_transition_source_identities(identities):
    """Cheap preflight-commit race check; never substitutes for a render proof."""
    try:
        for previous in identities:
            current = _source_identity(previous.media_id, previous.original_path)
            if (not same_file_state(previous.path_state, current.path_state)
                    or not same_file_state(previous.descriptor_state, current.descriptor_state)):
                raise TransitionSourceError('Video transition source changed while reading')
    except OSError as exc:
        raise TransitionSourceError('Video transition source is unavailable or invalid; relink valid video media') from exc


def prepare_transition_source_certificate(project, timeout=30, cancel=None):
    """Bind complete decoded proof to stable originals for an async UI commit.

    The caller must separately bind the project edit fingerprint and call the
    cheap verification immediately at commit. Rendering always rehashes files.
    """
    from dataclasses import replace
    deadline = time.monotonic() + timeout
    _check(deadline, cancel)
    plans = plan_video_transitions(project)
    ids = {clip.media_id for plan in plans for clip in (plan.left, plan.right)}
    try:
        before = tuple(_source_identity(media.id, media.path) for media in project.media if media.id in ids)
        with tempfile.TemporaryDirectory(prefix='cutcat-transition-certificate-') as directory:
            try:
                proofs = prepare_transition_sources(project, directory, max(0, deadline - time.monotonic()), cancel)
                verify_transition_source_identities(before)
                _check(deadline, cancel)
                return tuple(replace(identity, sha256=proofs[identity.media_id].sha256) for identity in before)
            finally:
                for path in Path(directory).iterdir():
                    if is_plain_file(path.lstat()):
                        os.chmod(path, 0o600)
    except OSError as exc:
        raise TransitionSourceError('Video transition source is unavailable or invalid; relink valid video media') from exc

"""Local, data-only project recovery, separate from explicit project saves.

Snapshots contain the validated project schema and absolute input references,
not media copies, application settings, credentials, or executable objects.
Each live project owns a kernel lock which is released even after a crash.
Unresolved sessions are never silently evicted to make room for another one.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import threading
from typing import Iterator
from uuid import UUID, uuid4

from .model import Project

FORMAT = "cutcat-project-recovery"
VERSION = 1
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_SNAPSHOT = re.compile(rf"cutcat-({_UUID})\.([0-9]{{20}})\.json\Z")
_TEMP = re.compile(rf"\.cutcat-({_UUID})\.[0-9a-f]{{32}}\.tmp\Z")
_SESSION_LOCK = re.compile(rf"\.cutcat-({_UUID})\.lock\Z")


class RecoveryError(ValueError):
    """An invalid or unsafe recovery cannot be used."""


class RecoveryBusyError(RecoveryError):
    """Another live editor owns this recovery or a short store operation."""


class RecoveryCapacityError(RecoveryError):
    """Existing unresolved recoveries must be reviewed before adding another."""


@dataclass(frozen=True)
class RecoveryEntry:
    identity: str
    generation: int
    created_utc: str
    project_name: str
    original_path: str | None
    path: Path


def _identity(value: str | UUID) -> str:
    if not isinstance(value, (str, UUID)):
        raise RecoveryError("Recovery identity must be a UUID")
    try:
        parsed = str(UUID(str(value)))
    except (ValueError, AttributeError) as exc:
        raise RecoveryError("Recovery identity must be a UUID") from exc
    if isinstance(value, str) and value != parsed:
        raise RecoveryError("Recovery identity must be a canonical UUID")
    return parsed


def _unsafe(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryError("Duplicate JSON key in recovery")
        result[key] = value
    return result


def _reject_constant(value):
    raise RecoveryError("Non-finite number in recovery")


def _absolute_reference(value: str, base: Path) -> str:
    # Retain foreign-platform absolute paths as missing references, rather than
    # accidentally binding them to the recovery directory on another platform.
    if os.name != "nt" and PureWindowsPath(value).is_absolute():
        return value
    path = Path(value).expanduser()
    return str((path if path.is_absolute() else base / path).resolve())


def _project_data(project: Project, original_path: str | None) -> dict:
    data = project.to_dict()
    base = Path(original_path).parent if original_path else Path.cwd()
    for media in data["media"]:
        media["path"] = _absolute_reference(media["path"], base)
    for clip in data["clips"]:
        if clip["grade"]["lut_path"]:
            clip["grade"]["lut_path"] = _absolute_reference(clip["grade"]["lut_path"], base)
    for overlay in data["overlays"]:
        overlay["asset_path"] = _absolute_reference(overlay["asset_path"], base)
    if data["caption_style"]["font_directory"]:
        data["caption_style"]["font_directory"] = _absolute_reference(
            data["caption_style"]["font_directory"], base)
    # Re-validate normalized paths using the project's own schema.
    Project.from_dict(data)
    return data


class RecoveryStore:
    """A bounded store; call close() when the owning editor really exits.

    write() and load() acquire and retain a session lock. list_entries() hides
    active sessions by default. acquire()/claim() can reserve a recovery before
    switching UI state. A failed claim never steals a live session. The fixed
    store lock guards every session-lock open, acquisition and removal, so no
    process can be waiting on an old unlinked lock inode. Stale lock records are
    cleaned under that same guard after an abnormal exit.
    """

    def __init__(self, root: str | Path, *, generations: int = 3, max_sessions: int = 32):
        if type(generations) is not int or not 2 <= generations <= 3:
            raise ValueError("Recovery generations must be 2 or 3")
        if type(max_sessions) is not int or not 1 <= max_sessions <= 1024:
            raise ValueError("Recovery session limit must be 1..1024")
        # Construction is I/O-free so a read-only/unavailable recovery folder
        # cannot prevent the editor from opening. Errors surface on operations.
        self.root = Path(root).expanduser().absolute()
        self.generations = generations
        self.max_sessions = max_sessions
        self.unrecoverable_identities: tuple[str, ...] = ()
        self._locks: dict[str, int] = {}
        self._mutex = threading.RLock()
        self._directory_fd: int | None = None
        self._directory_identity: tuple[int, int] | None = None

    def _directory(self, *, create: bool) -> bool:
        if self._directory_identity is None:
            # Resolve trusted parent aliases once (including macOS /var), but
            # never accept a link/junction as the managed directory itself.
            try:
                info = self.root.lstat()
            except FileNotFoundError:
                pass
            else:
                if _unsafe(info):
                    raise RecoveryError("Recovery directory must not be a symlink")
            self.root = self.root.parent.resolve() / self.root.name
        # All stored ancestor names are canonical. Reject substitutions with
        # symlinks or Windows reparse points before touching any managed file.
        for part in reversed((self.root, *self.root.parents)):
            try:
                info = part.lstat()
            except FileNotFoundError:
                continue
            if _unsafe(info) or not stat.S_ISDIR(info.st_mode):
                raise RecoveryError("Unsafe recovery directory")
        if not self.root.exists():
            if not create:
                return False
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.lstat()
        if _unsafe(info) or not stat.S_ISDIR(info.st_mode):
            raise RecoveryError("Unsafe recovery directory")
        identity = (info.st_dev, info.st_ino)
        if self._directory_identity is not None and identity != self._directory_identity:
            raise RecoveryError("Recovery directory changed while in use")
        if self._directory_identity is None:
            if os.name != "nt":
                self._directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                opened = os.fstat(self._directory_fd)
                if (opened.st_dev, opened.st_ino) != identity:
                    os.close(self._directory_fd)
                    self._directory_fd = None
                    raise RecoveryError("Recovery directory changed while opening")
            self._directory_identity = identity
        return True

    def _stat(self, name: str):
        if self._directory_fd is not None:
            return os.stat(name, dir_fd=self._directory_fd, follow_symlinks=False)
        return (self.root / name).lstat()

    def _names(self) -> list[str]:
        return os.listdir(self._directory_fd if self._directory_fd is not None else self.root)

    def _open(self, name: str, flags: int) -> int:
        # Callers only supply generated names or names matched by _SNAPSHOT.
        if Path(name).name != name or "/" in name or "\\" in name:
            raise RecoveryError("Invalid managed recovery filename")
        before = None
        try:
            before = self._stat(name)
        except FileNotFoundError:
            pass
        if before is not None and (_unsafe(before) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1):
            raise RecoveryError("Recovery file is not a private regular file")
        options = flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(name if self._directory_fd is not None else self.root / name,
                     options, 0o600, **({"dir_fd": self._directory_fd} if self._directory_fd is not None else {}))
        try:
            after = os.fstat(fd)
            current = self._stat(name)
            if (_unsafe(current) or not stat.S_ISREG(after.st_mode) or after.st_nlink != 1
                    or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
                    or (before is not None and (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino))):
                raise RecoveryError("Recovery file changed while opening")
        except BaseException:
            os.close(fd)
            raise
        return fd

    @staticmethod
    def _unlock(fd: int) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _lock(self, name: str) -> int | None:
        fd = self._open(name, os.O_RDWR | os.O_CREAT)
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return None
            raise
        return fd

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        fd = self._lock(".cutcat-recovery.lock")
        if fd is None:
            raise RecoveryBusyError("Another recovery operation is in progress")
        try:
            yield
        finally:
            self._unlock(fd)

    def _remove_lock(self, name: str, fd: int) -> None:
        # The caller holds the global transaction; another process cannot open
        # this name until the kernel lease has closed and its record is removed.
        self._unlock(fd)
        try:
            self._unlink(name)
        except FileNotFoundError:
            pass

    def _clean_orphan_locks(self) -> None:
        # Called only while holding the global transaction. Active locks are
        # retained; failed or unsafe records are not read or removed.
        for name in self._names():
            match = _SESSION_LOCK.fullmatch(name)
            if not match or match[1] in self._locks:
                continue
            try:
                fd = self._lock(name)
                if fd is not None:
                    self._remove_lock(name, fd)
            except (OSError, RecoveryError):
                pass

    def acquire(self, identity: str | UUID) -> bool:
        identity = _identity(identity)
        with self._mutex:
            self._directory(create=True)
            if identity in self._locks:
                return True
            with self._transaction():
                self._clean_orphan_locks()
                fd = self._lock(f".cutcat-{identity}.lock")
                if fd is None:
                    return False
                self._locks[identity] = fd
                return True

    claim = acquire

    def release(self, identity: str | UUID) -> None:
        identity = _identity(identity)
        with self._mutex:
            fd = self._locks.pop(identity, None)
            if fd is None:
                return
            try:
                if self._directory(create=False):
                    with self._transaction():
                        releasing, fd = fd, None
                        self._remove_lock(f".cutcat-{identity}.lock", releasing)
            except (OSError, RecoveryError):
                # A concurrent short store operation must never keep a closing
                # editor's kernel lease alive. Its orphan record is cleaned on
                # a later transaction, without evicting any snapshot payload.
                pass
            finally:
                if fd is not None:
                    self._unlock(fd)

    def close(self) -> None:
        with self._mutex:
            for identity in list(self._locks):
                self.release(identity)
            if self._directory_fd is not None:
                os.close(self._directory_fd)
                self._directory_fd = None
            self._directory_identity = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _read(self, name: str) -> tuple[RecoveryEntry, Project]:
        match = _SNAPSHOT.fullmatch(name)
        if not match:
            raise RecoveryError("Invalid recovery snapshot filename")
        fd = self._open(name, os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            if os.fstat(stream.fileno()).st_size > MAX_SNAPSHOT_BYTES:
                raise RecoveryError("Recovery snapshot exceeds 32 MiB")
            payload = stream.read(MAX_SNAPSHOT_BYTES + 1)
        if len(payload) > MAX_SNAPSHOT_BYTES:
            raise RecoveryError("Recovery snapshot exceeds 32 MiB")
        try:
            data = json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            if not isinstance(data, dict) or set(data) != {
                    "format", "version", "identity", "generation", "created_utc", "original_path", "project"}:
                raise RecoveryError("Invalid recovery envelope")
            if data["format"] != FORMAT or type(data["version"]) is not int or data["version"] != VERSION:
                raise RecoveryError("Unsupported recovery format or version")
            if _identity(data["identity"]) != match[1]:
                raise RecoveryError("Recovery identity does not match filename")
            if type(data["generation"]) is not int or not 1 <= data["generation"] == int(match[2]):
                raise RecoveryError("Invalid recovery generation")
            created = data["created_utc"]
            if not isinstance(created, str) or not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00", created):
                raise RecoveryError("Invalid recovery timestamp")
            datetime.fromisoformat(created)
            original = data["original_path"]
            if original is not None and (not isinstance(original, str) or "\0" in original
                    or not (Path(original).is_absolute() or PureWindowsPath(original).is_absolute())):
                raise RecoveryError("Invalid original project path")
            project = Project.from_dict(data["project"])
            self._protect_inputs(project.to_dict(), original)
            references = [item.path for item in project.media]
            references += [item.grade.lut_path for item in project.clips if item.grade.lut_path]
            references += [item.asset_path for item in project.overlays]
            if project.caption_style.font_directory:
                references.append(project.caption_style.font_directory)
            if any(not (Path(value).is_absolute() or PureWindowsPath(value).is_absolute()) for value in references):
                raise RecoveryError("Recovery contains a relative input reference")
            return RecoveryEntry(match[1], data["generation"], created, project.name, original,
                                 self.root / name), project
        except (UnicodeError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
            raise RecoveryError(f"Invalid recovery snapshot: {exc}") from exc

    def list_entries(self, *, include_active: bool = False) -> tuple[list[RecoveryEntry], list[str]]:
        entries, warnings = [], []
        with self._mutex:
            unrecoverable = []
            self.unrecoverable_identities = ()
            if not self._directory(create=False):
                return entries, warnings
            with self._transaction():
                self._clean_orphan_locks()
                grouped: dict[str, list[str]] = {}
                for name in self._names():
                    match = _SNAPSHOT.fullmatch(name) or _TEMP.fullmatch(name)
                    if match:
                        grouped.setdefault(match[1], [])
                        if _SNAPSHOT.fullmatch(name):
                            grouped[match[1]].append(name)
                for identity, names in grouped.items():
                    lease = None
                    try:
                        if not include_active:
                            if identity in self._locks:
                                continue
                            lease = self._lock(f".cutcat-{identity}.lock")
                            if lease is None:
                                continue
                        for name in sorted(names, reverse=True):
                            try:
                                entry, _ = self._read(name)
                            except (OSError, RecoveryError) as exc:
                                warnings.append(f"Skipped recovery {name}: {exc}")
                                continue
                            entries.append(entry)
                            break
                        else:
                            unrecoverable.append(identity)
                            if not names:
                                warnings.append(f"Unfinished recovery session {identity} has no complete snapshot")
                    except (OSError, RecoveryError) as exc:
                        warnings.append(f"Skipped recovery session {identity}: {exc}")
                    finally:
                        if lease is not None:
                            self._remove_lock(f".cutcat-{identity}.lock", lease)
            self.unrecoverable_identities = tuple(sorted(unrecoverable))
        entries.sort(key=lambda item: (item.created_utc, item.identity), reverse=True)
        return entries, warnings

    def load(self, entry: RecoveryEntry) -> Project:
        if not isinstance(entry, RecoveryEntry):
            raise RecoveryError("Expected a managed recovery entry")
        identity = _identity(entry.identity)
        expected = self.root / f"cutcat-{identity}.{entry.generation:020d}.json"
        if entry.path != expected:
            raise RecoveryError("Recovery entry is outside this store")
        with self._mutex:
            already_owned = identity in self._locks
            if not self.acquire(identity):
                raise RecoveryBusyError("Recovery is open in another editor")
            try:
                actual, project = self._read(expected.name)
                if actual != entry:
                    raise RecoveryError("Recovery entry changed; refresh the recovery list")
                return project
            except BaseException:
                if not already_owned:
                    self.release(identity)
                raise

    def _unlink(self, name: str) -> None:
        info = self._stat(name)
        if _unsafe(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RecoveryError("Will not remove an unsafe recovery file")
        if self._directory_fd is not None:
            os.unlink(name, dir_fd=self._directory_fd)
        else:
            (self.root / name).unlink()

    def _sync_directory(self) -> None:
        if self._directory_fd is not None:
            os.fsync(self._directory_fd)

    def _prune_generations(self, names: list[str], keep: int) -> None:
        # Prefer validated generations, so a corrupt newer file never evicts
        # the last usable older snapshot during cleanup after an interrupted
        # prune. All names are current-session, matched managed filenames.
        if len(names) <= keep:
            return
        valid, invalid = [], []
        for name in names:
            try:
                self._read(name)
                valid.append(name)
            except (OSError, RecoveryError):
                invalid.append(name)
        retained = set((valid + invalid)[:keep])
        for name in names:
            if name not in retained:
                self._unlink(name)

    def _protect_inputs(self, data: dict, original: str | None) -> None:
        references = [item["path"] for item in data["media"]]
        references += [item["grade"]["lut_path"] for item in data["clips"] if item["grade"]["lut_path"]]
        references += [item["asset_path"] for item in data["overlays"]]
        if original:
            references.append(original)
        font = data["caption_style"]["font_directory"]
        if font:
            references.append(font)
        for value in references:
            if os.name != "nt" and PureWindowsPath(value).is_absolute():
                continue
            path = Path(value).resolve()
            if path == self.root or self.root in path.parents:
                raise RecoveryError("Project inputs and original files must be outside the recovery directory")
        if font:
            font_path = Path(font).resolve()
            if font_path == self.root or font_path in self.root.parents:
                raise RecoveryError("Recovery directory must not be inside the caption font directory")

    def write(self, project: Project, identity: str | UUID,
              original_path: str | Path | None = None) -> RecoveryEntry:
        identity = _identity(identity)
        original = str(Path(original_path).expanduser().resolve()) if original_path is not None else None
        data = _project_data(project, original)
        with self._mutex:
            self._directory(create=False)
            self._protect_inputs(data, original)
            if not self.acquire(identity):
                raise RecoveryBusyError("Recovery is open in another editor")
            with self._transaction():
                names = self._names()
                snapshots = [(name, match) for name in names if (match := _SNAPSHOT.fullmatch(name))]
                temporaries = [(name, match) for name in names if (match := _TEMP.fullmatch(name))]
                sessions = {match[1] for _, match in snapshots + temporaries}
                if identity not in sessions and len(sessions) >= self.max_sessions:
                    raise RecoveryCapacityError("Recovery storage is full; review existing recovery sessions first")
                previous = sorted((name for name, match in snapshots if match[1] == identity), reverse=True)
                own_temporaries = [name for name, match in temporaries if match[1] == identity]
                if len(own_temporaries) >= self.generations:
                    raise RecoveryCapacityError("Too many interrupted snapshots; review this recovery session first")
                generation = (int(_SNAPSHOT.fullmatch(previous[0])[2]) + 1) if previous else 1
                if len(previous) > self.generations:
                    # A prior durable write may have failed while pruning. Do
                    # not add another generation until that cleanup succeeds.
                    self._prune_generations(previous, self.generations)
                    self._sync_directory()
                    previous = [name for name in previous if name in self._names()]
                if generation >= 10**20:
                    raise RecoveryError("Recovery generation limit reached")
                created = datetime.now(timezone.utc).isoformat(timespec="microseconds")
                envelope = {"format": FORMAT, "version": VERSION, "identity": identity,
                            "generation": generation, "created_utc": created, "original_path": original,
                            "project": data}
                payload = (json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
                if len(payload) > MAX_SNAPSHOT_BYTES:
                    raise RecoveryError("Recovery snapshot exceeds 32 MiB")
                name = f"cutcat-{identity}.{generation:020d}.json"
                temporary = f".cutcat-{identity}.{uuid4().hex}.tmp"
                fd = self._open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                    # Unique monotonically numbered destinations never replace
                    # a prior generation. A link at this name is not followed.
                    if self._directory_fd is not None:
                        os.replace(temporary, name, src_dir_fd=self._directory_fd, dst_dir_fd=self._directory_fd)
                    else:
                        self._directory(create=False)
                        os.replace(self.root / temporary, self.root / name)
                    try:
                        self._sync_directory()
                    except OSError:
                        # The new name is not confirmed durable. Roll back only
                        # this write, never an older known-good generation. If
                        # cleanup also fails, the preflight cap blocks growth on
                        # the next retry rather than accumulating more files.
                        self._unlink(name)
                        raise
                finally:
                    try:
                        self._unlink(temporary)
                    except FileNotFoundError:
                        pass
                # Only prune after the new complete generation is durable.
                self._prune_generations(previous, self.generations - 1)
                # Stale first-write payloads count toward capacity and remain
                # explicitly discardable until this same session commits anew.
                for old, match in temporaries:
                    if match[1] == identity:
                        self._unlink(old)
                self._sync_directory()
                return RecoveryEntry(identity, generation, created, project.name, original, self.root / name)

    def discard(self, identity: str | UUID) -> None:
        identity = _identity(identity)
        with self._mutex:
            if not self._directory(create=False):
                return
            names = [name for name in self._names()
                     if (match := (_SNAPSHOT.fullmatch(name) or _TEMP.fullmatch(name))) and match[1] == identity]
            if not names:
                return
            already_owned = identity in self._locks
            if not self.acquire(identity):
                raise RecoveryBusyError("Recovery is open in another editor")
            try:
                with self._transaction():
                    # Validate the full set first, so an unsafe file does not
                    # cause a partially completed discard.
                    names = [name for name in self._names()
                             if (match := (_SNAPSHOT.fullmatch(name) or _TEMP.fullmatch(name))) and match[1] == identity]
                    for name in names:
                        info = self._stat(name)
                        if _unsafe(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise RecoveryError("Will not remove an unsafe recovery file")
                    for name in names:
                        self._unlink(name)
                    self._sync_directory()
            finally:
                if not already_owned:
                    self.release(identity)

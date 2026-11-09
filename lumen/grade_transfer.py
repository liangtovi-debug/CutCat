"""Window-local creative grade snapshots, with no operating-system clipboard.

Source interpretation is never a look: input_encoding is deliberately absent.
An assigned LUT, even if bypassed, must remain the same readable local file.
Snapshots retain no live model objects and can safely outlive their project.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, fields
import hashlib
import json
import os
from pathlib import Path
import stat

from .model import Editor, Grade, Project
from .file_identity import path_matches_descriptor, same_file_state

MAX_LUT_BYTES = 64 * 1024 * 1024
_CREATIVE_FIELDS = frozenset(field.name for field in fields(Grade)) - {'input_encoding'}


@dataclass(frozen=True)
class LUTIdentity:
    resolved_path: str
    sha256: str


@dataclass(frozen=True)
class CopiedGrade:
    """Immutable value snapshot; the JSON is internal memory, never a file."""

    creative_json: str
    lut_identity: LUTIdentity | None = None

    def grade_for_input(self, input_encoding: str) -> Grade:
        try:
            values = json.loads(self.creative_json)
            if not isinstance(values, dict) or set(values) != _CREATIVE_FIELDS:
                raise ValueError
            return Grade(**values, input_encoding=input_encoding)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError('Copied grade data is invalid. Copy the grade again.') from exc


def is_picture_clip(project: Project, clip_id: str) -> bool:
    clip = next((item for item in project.clips if item.id == clip_id), None)
    return bool(clip and clip.track < 2 and any(
        media.id == clip.media_id and media.has_video for media in project.media))


def _picture_clip(project: Project, clip_id: str):
    if not is_picture_clip(project, clip_id):
        raise ValueError('Select a picture clip on a video track to copy or paste a grade.')
    return next(clip for clip in project.clips if clip.id == clip_id)


def _changed_lut(before, after, *, stage):
    error = ValueError('Grade LUT changed while it was being read. Try copying the grade again.')
    names = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns',
             'st_ctime_ns', 'st_birthtime_ns')
    # Report field differences, never paths, file IDs, or LUT contents.
    error.add_note(json.dumps({
        'kind': 'grade_lut_snapshot', 'stage': stage,
        'different_fields': [name for name in names
                             if getattr(before, name, None) != getattr(after, name, None)],
        'mtime_delta_ns': after.st_mtime_ns - before.st_mtime_ns,
        'ctime_delta_ns': after.st_ctime_ns - before.st_ctime_ns,
    }, sort_keys=True))
    return error


def _read_lut_identity(path: str) -> LUTIdentity:
    """Hash exactly the bounded bytes parsed, and reject concurrent replacement.

    Checking file type before opening avoids blocking on a named pipe. A second
    descriptor check handles replacements, and all reads have a hard byte cap.
    This is a snapshot check, not a lock on future external edits of the file.
    """
    if (not isinstance(path, str) or not path or path.startswith(('//', '\\\\'))
            or '://' in path or any(ord(char) < 32 for char in path)):
        raise ValueError('Grade LUT must be a local regular .cube file.')
    try:
        source = Path(path).expanduser().resolve(strict=True)
        before = source.stat()
        if source.suffix.lower() != '.cube' or not stat.S_ISREG(before.st_mode):
            raise ValueError('Grade LUT must be a local regular .cube file.')
        if before.st_size > MAX_LUT_BYTES:
            raise ValueError('Grade LUT exceeds 64 MiB.')
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(source, flags)
        try:
            stream = os.fdopen(descriptor, 'rb')
        except BaseException:
            os.close(descriptor)
            raise
        with stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or not path_matches_descriptor(before, opened):
                raise _changed_lut(before, opened, stage='path_to_open_descriptor')
            payload = stream.read(MAX_LUT_BYTES + 1)
            after = os.fstat(stream.fileno())
        if len(payload) > MAX_LUT_BYTES:
            raise ValueError('Grade LUT exceeds 64 MiB.')
        if not same_file_state(opened, after):
            raise _changed_lut(opened, after, stage='descriptor_after_read')
        current = source.stat()
        if not same_file_state(before, current):
            raise _changed_lut(before, current, stage='path_after_read')
        if str(Path(path).expanduser().resolve(strict=True)) != str(source):
            raise ValueError('Grade LUT changed while it was being read. Try copying the grade again.')
    except (OSError, RuntimeError) as exc:
        raise ValueError('Cannot read the grade LUT. Locate the original .cube file and copy the grade again.') from exc
    try:
        # The owned parser is cached by bytes and never rereads the path.
        from .color import _load_cube
        _load_cube(payload)
    except (ValueError, UnicodeError, OverflowError) as exc:
        raise ValueError('Grade LUT is invalid. Choose a valid 3D .cube file and copy the grade again.') from exc
    return LUTIdentity(str(source), hashlib.sha256(payload).hexdigest())


def copy_grade(project: Project, clip_id: str) -> CopiedGrade:
    """Copy the complete committed creative grade without mutating the project."""
    clip = _picture_clip(project, clip_id)
    project.validate()
    values = asdict(clip.grade)
    del values['input_encoding']
    identity = _read_lut_identity(clip.grade.lut_path) if clip.grade.lut_path else None
    return CopiedGrade(json.dumps(values, allow_nan=False, sort_keys=True), identity)


def grade_for_paste(project: Project, clip_id: str, copied: CopiedGrade | None) -> Grade:
    """Prepare a fully checked independent grade, keeping the target's input."""
    clip = _picture_clip(project, clip_id)
    if copied is None:
        raise ValueError('No grade has been copied in this window.')
    if not isinstance(copied, CopiedGrade):
        raise ValueError('Copied grade data is invalid. Copy the grade again.')
    grade = copied.grade_for_input(clip.grade.input_encoding)
    candidate = deepcopy(project)
    next(item for item in candidate.clips if item.id == clip_id).grade = grade
    candidate.validate()
    if grade.lut_path:
        if not isinstance(copied.lut_identity, LUTIdentity):
            raise ValueError('Copied grade LUT identity is missing. Copy the grade again.')
        if _read_lut_identity(grade.lut_path) != copied.lut_identity:
            raise ValueError('Copied grade LUT has changed or moved. Restore the original file or copy the grade again.')
    elif copied.lut_identity is not None:
        raise ValueError('Copied grade data is invalid. Copy the grade again.')
    return grade


def paste_grade(editor: Editor, clip_id: str, copied: CopiedGrade | None) -> bool:
    """One transaction/undo step, or no edit at all for an identical grade."""
    grade = grade_for_paste(editor.project, clip_id, copied)
    if grade == _picture_clip(editor.project, clip_id).grade:
        return False
    editor.update_clip(clip_id, grade=grade)
    return True

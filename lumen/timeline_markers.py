"""Strict project-frame annotation metadata (schema 39).

Markers have no media/clip relationship, never contribute to project duration,
never move during ripple edits, and never retime when project FPS changes.
Changing FPS changes the seconds represented by their unchanged frame integers.
Out-of-range records remain valid after timeline shortening; navigation must
check ``timeline_marker_in_bounds`` before calling a clamping seek function.
"""
from __future__ import annotations

from dataclasses import dataclass, fields

MAX_TIMELINE_MARKERS = 10000
MAX_TIMELINE_MARKER_ID = 128
MAX_TIMELINE_MARKER_NAME = 128
MAX_TIMELINE_MARKER_COMMENT = 4096
MAX_TIMELINE_MARKER_FRAME = 2**53 - 1
# Bound the complete annotation collection, not just each individual string.
MAX_TIMELINE_MARKER_TEXT = 2_000_000
TIMELINE_MARKER_COLORS = ('red', 'orange', 'yellow', 'green', 'cyan', 'blue', 'purple', 'pink')


@dataclass(frozen=True)
class TimelineMarker:
    id: str
    name: str
    comment: str
    color: str
    start_frame: int
    end_frame: int | None = None


def _integer(value, label):
    if type(value) is not int or not 0 <= value <= MAX_TIMELINE_MARKER_FRAME:
        raise ValueError(f'Timeline marker {label} must be a bounded nonnegative integer')


def _text(value, label, maximum, *, comment=False):
    # Plain text only. HTML-looking values are legitimate labels, never markup.
    # Comments allow LF and tab; CR, other control characters, Unicode line
    # separators, unpaired surrogates and Unicode noncharacters are rejected
    # instead of normalized. Qt uses some noncharacters as document sentinels.
    allowed = {'\n', '\t'} if comment else set()
    if (type(value) is not str or len(value) > maximum
            or (not comment and not value.strip())
            or any((ord(char) < 32 and char not in allowed)
                   or 127 <= ord(char) < 160 or ord(char) in (0x2028, 0x2029)
                   or 0xD800 <= ord(char) <= 0xDFFF
                   or 0xFDD0 <= ord(char) <= 0xFDEF
                   or (ord(char) & 0xFFFF) in (0xFFFE, 0xFFFF) for char in value)):
        raise ValueError(f'Invalid timeline marker {label}')


def validate_timeline_marker(record):
    if type(record) is not TimelineMarker:
        raise ValueError('Invalid timeline marker')
    _text(record.id, 'id', MAX_TIMELINE_MARKER_ID)
    _text(record.name, 'name', MAX_TIMELINE_MARKER_NAME)
    _text(record.comment, 'comment', MAX_TIMELINE_MARKER_COMMENT, comment=True)
    if type(record.color) is not str or record.color not in TIMELINE_MARKER_COLORS:
        raise ValueError('Invalid timeline marker color')
    _integer(record.start_frame, 'start frame')
    if record.end_frame is not None:
        _integer(record.end_frame, 'end frame')
        if record.end_frame <= record.start_frame:
            raise ValueError('Timeline marker range must have start before exclusive end')


def validate_timeline_markers(records):
    if type(records) is not list or len(records) > MAX_TIMELINE_MARKERS:
        raise ValueError('Timeline markers must be a list of at most 10000 entries')
    identifiers = set()
    text_size = 0
    for record in records:
        validate_timeline_marker(record)
        if record.id in identifiers:
            raise ValueError('Duplicate timeline marker id')
        identifiers.add(record.id)
        text_size += len(record.id) + len(record.name) + len(record.comment)
        if text_size > MAX_TIMELINE_MARKER_TEXT:
            raise ValueError('Timeline marker text exceeds the 2000000 code-point collection limit')


def timeline_markers_from_dict(records):
    """Strict wire conversion: all fields, including a nullable end, required."""
    if type(records) is not list or len(records) > MAX_TIMELINE_MARKERS:
        raise ValueError('Timeline markers must be a list of at most 10000 entries')
    record_fields = {entry.name for entry in fields(TimelineMarker)}
    result = []
    for record in records:
        if type(record) is not dict or set(record) != record_fields:
            raise ValueError('Invalid timeline marker fields')
        result.append(TimelineMarker(**record))
    validate_timeline_markers(result)
    return result


def timeline_marker_in_bounds(record, duration_frames):
    """A whole annotation must fit; never clamp a range or its seek target."""
    validate_timeline_marker(record)
    if type(duration_frames) is not int or duration_frames < 0:
        raise ValueError('Project duration must be a nonnegative integer')
    return (record.start_frame < duration_frames
            and (record.end_frame is None or record.end_frame <= duration_frames))

"""Pure metadata for reusable source-relative selections (project schema 38).

Saved ranges do not own clips, caches or verified state. File identity is checked
by a separate worker each time the UI saves or recalls a range. Validation here
must remain independent of file availability and the current source duration:
relinking to a changed/shorter source preserves the original record for review.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from fractions import Fraction
import math
import re

MAX_SOURCE_RANGES = 10000
MAX_SOURCE_RANGE_NAME = 128
MAX_SOURCE_RANGE_ID = 128
MAX_SOURCE_RANGE_INTEGER = 2**63 - 1
MAX_SOURCE_RANGE_FRAME = 2**53 - 1
SOURCE_RANGE_BINDING_VERSION = 1


@dataclass(frozen=True)
class SourceRangeBinding:
    """Portable sampled-content/timing identity, never a path or inode."""
    algorithm_version: int
    file_size: int
    sampled_content_sha256: str
    semantic_source_signature: str


@dataclass(frozen=True)
class SourceRange:
    id: str
    name: str
    media_id: str
    in_num: int
    in_den: int
    out_num: int
    out_den: int
    capture_fps_num: int
    capture_fps_den: int
    binding: SourceRangeBinding

    @property
    def in_seconds(self) -> Fraction:
        return Fraction(self.in_num, self.in_den)

    @property
    def out_seconds(self) -> Fraction:
        return Fraction(self.out_num, self.out_den)

    @property
    def capture_fps(self) -> Fraction:
        return Fraction(self.capture_fps_num, self.capture_fps_den)


def _integer(value, label, minimum=0, maximum=MAX_SOURCE_RANGE_INTEGER):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'Saved source range {label} must be a bounded integer')


def _text(value, label, maximum):
    # These are one-line plain-text labels; HTML-looking names are allowed and
    # must be rendered as text by consumers. Control characters are not labels.
    if (type(value) is not str or not value.strip() or len(value) > maximum
            or any(ord(char) < 32 or 127 <= ord(char) < 160
                   or ord(char) in (0x2028, 0x2029)
                   or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
        raise ValueError(f'Invalid saved source range {label}')


def validate_source_range_binding(binding):
    if type(binding) is not SourceRangeBinding:
        raise ValueError('Invalid saved source range binding')
    if type(binding.algorithm_version) is not int or binding.algorithm_version != SOURCE_RANGE_BINDING_VERSION:
        raise ValueError('Unsupported saved source range binding version')
    _integer(binding.file_size, 'file size', 1)
    for value in (binding.sampled_content_sha256, binding.semantic_source_signature):
        if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
            raise ValueError('Saved source range identity must be a SHA-256 digest')


def validate_source_range(record):
    if type(record) is not SourceRange:
        raise ValueError('Invalid saved source range')
    _text(record.id, 'id', MAX_SOURCE_RANGE_ID)
    _text(record.name, 'name', MAX_SOURCE_RANGE_NAME)
    _text(record.media_id, 'media id', MAX_SOURCE_RANGE_ID)
    for label in ('in', 'out'):
        numerator, denominator = getattr(record, label + '_num'), getattr(record, label + '_den')
        _integer(numerator, label + ' numerator')
        _integer(denominator, label + ' denominator', 1, 1_000_000)
        if math.gcd(numerator, denominator) != 1:
            raise ValueError('Saved source range bounds must be reduced rational seconds')
    for value in (record.capture_fps_num, record.capture_fps_den):
        _integer(value, 'capture frame rate', 1, 1_000_000)
    if (math.gcd(record.capture_fps_num, record.capture_fps_den) != 1
            or not Fraction(1, 1000) <= record.capture_fps <= 120):
        raise ValueError('Invalid saved source range capture frame rate')
    if record.in_seconds >= record.out_seconds:
        raise ValueError('Saved source range must have In before Out')
    for value in (record.in_seconds, record.out_seconds):
        frame = value * record.capture_fps
        if frame.denominator != 1 or frame > MAX_SOURCE_RANGE_FRAME:
            raise ValueError('Saved source range bounds must align exactly to the capture frame grid')
    validate_source_range_binding(record.binding)


def validate_source_ranges(records, media_ids):
    if type(records) is not list or len(records) > MAX_SOURCE_RANGES:
        raise ValueError('Saved source ranges must be a list of at most 10000 entries')
    identifiers = set()
    for record in records:
        validate_source_range(record)
        if record.id in identifiers:
            raise ValueError('Duplicate saved source range id')
        identifiers.add(record.id)
        if record.media_id not in media_ids:
            raise ValueError('Saved source range references unknown media')


def source_ranges_from_dict(records):
    """Strict wire conversion; unknown and omitted fields are never ignored."""
    if type(records) is not list or len(records) > MAX_SOURCE_RANGES:
        raise ValueError('Saved source ranges must be a list of at most 10000 entries')
    converted = []
    record_fields = {entry.name for entry in fields(SourceRange)}
    binding_fields = {entry.name for entry in fields(SourceRangeBinding)}
    for record in records:
        if type(record) is not dict or set(record) != record_fields:
            raise ValueError('Invalid saved source range fields')
        binding = record['binding']
        if type(binding) is not dict or set(binding) != binding_fields:
            raise ValueError('Invalid saved source range binding fields')
        converted.append(SourceRange(**{**record, 'binding': SourceRangeBinding(**binding)}))
    return converted


def range_frames(record, fps, total_frames=None):
    """Map saved rational seconds exactly, never round, stretch or clamp."""
    validate_source_range(record)
    if (type(fps) is not int and not isinstance(fps, Fraction)) or fps <= 0:
        raise ValueError('Saved source range requires an exact positive project frame rate')
    start, end = record.in_seconds * fps, record.out_seconds * fps
    if start.denominator != 1 or end.denominator != 1:
        raise ValueError('Saved source range does not align exactly to the current project frame grid')
    if start > MAX_SOURCE_RANGE_FRAME or end > MAX_SOURCE_RANGE_FRAME:
        raise ValueError('Saved source range exceeds the supported frame limit')
    if total_frames is not None:
        _integer(total_frames, 'complete source frames', 0, MAX_SOURCE_RANGE_FRAME)
        if end > total_frames:
            raise ValueError('Saved source range exceeds the current complete source duration')
    return int(start), int(end)

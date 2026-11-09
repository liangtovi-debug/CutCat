"""Strict UTF-8 SubRip interchange for the independent subtitle track.

Imports round both boundaries to the nearest project frame (ties round up).
Shared timestamps stay adjacent. Real overlaps and cues that collapse to zero
frames are rejected rather than silently changing words or timings. Video edits never ripple subtitle positions.
"""
from __future__ import annotations

from fractions import Fraction
import math
from pathlib import Path
import re
from uuid import uuid4

from .model import Caption, MAX_CAPTIONS, validate_captions

_TIME = r"(\d{2,18}):([0-5]\d):([0-5]\d),(\d{3})"
_TIMING = re.compile(rf"{_TIME} --> {_TIME}")
MAX_SRT_BYTES = 32 * 1024 * 1024


def _fps(value) -> Fraction:
    try:
        if isinstance(value, bool):
            raise ValueError
        result = Fraction(str(value)) if isinstance(value, float) else Fraction(value)
        if not Fraction(1, 1000) <= result <= 1000:
            raise ValueError
        return result
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError("Frame rate must be finite and in 0.001..1000") from exc


def seconds_to_frame(seconds, fps) -> int:
    """Nearest project frame, half-up; maximum timing error is half a frame."""
    rate = _fps(fps)
    try:
        if isinstance(seconds, bool):
            raise ValueError
        value = Fraction(str(seconds)) if isinstance(seconds, float) else Fraction(seconds)
        if value < 0:
            raise ValueError
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError("Caption time must be finite and nonnegative") from exc
    return math.floor(value * rate + Fraction(1, 2))


def _milliseconds(parts) -> int:
    hours, minutes, seconds, milliseconds = map(int, parts)
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds


def parse_srt(text: str, fps) -> list[Caption]:
    rate = _fps(fps)
    if not isinstance(text, str):
        raise ValueError("SRT input must be text")
    text = text.removeprefix('\ufeff').replace('\r\n', '\n').replace('\r', '\n').strip('\n')
    if not text.strip():
        return []
    captions = []
    source_ranges = []
    blocks = re.split(r'\n[ \t]*\n', text)
    if len(blocks) > MAX_CAPTIONS:
        raise ValueError("SRT contains more than 20000 captions")
    for position, block in enumerate(blocks, 1):
        lines = block.split('\n')
        if len(lines) < 3 or not re.fullmatch(r'[0-9]{1,12}', lines[0].strip()):
            raise ValueError(f"Malformed SRT cue {position}: expected index, timing, and text")
        match = _TIMING.fullmatch(lines[1].strip())
        if match is None:
            raise ValueError(f"Malformed SRT time in cue {position}; expected HH:MM:SS,mmm --> HH:MM:SS,mmm")
        start_ms, end_ms = _milliseconds(match.groups()[:4]), _milliseconds(match.groups()[4:])
        if end_ms <= start_ms:
            raise ValueError(f"SRT cue {position} must end after its start")
        source_ranges.append((start_ms, end_ms))
        start = seconds_to_frame(Fraction(start_ms, 1000), rate)
        end = seconds_to_frame(Fraction(end_ms, 1000), rate)
        if end <= start:
            raise ValueError(f"SRT cue {position} collapses to zero frames at this project frame rate")
        captions.append(Caption(uuid4().hex, start, end - start, '\n'.join(lines[2:])))
    ordered_ranges = sorted(source_ranges)
    if any(a[1] > b[0] for a, b in zip(ordered_ranges, ordered_ranges[1:])):
        raise ValueError("SRT captions overlap in source timings; adjust cue timings")
    try:
        validate_captions(captions)
    except ValueError as exc:
        if 'overlap' in str(exc):
            raise ValueError("SRT captions overlap after nearest-frame quantization; adjust cue timings") from exc
        raise
    return captions


def _timestamp(milliseconds: int) -> str:
    seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}'


def format_srt(captions: list[Caption], fps) -> str:
    rate = _fps(fps)
    validate_captions(captions)
    blocks = []
    for index, caption in enumerate(sorted(captions, key=lambda item: item.start), 1):
        # Nearest milliseconds (half-up) preserve shared boundaries.
        start = math.floor(Fraction(caption.start * 1000, 1) / rate + Fraction(1, 2))
        end = math.floor(Fraction(caption.end * 1000, 1) / rate + Fraction(1, 2))
        if end <= start:
            raise ValueError("Caption is too short for millisecond SRT precision")
        if any(not line.strip() for line in caption.text.split('\n')):
            raise ValueError("SRT caption text cannot contain empty lines")
        blocks.append(f'{index}\n{_timestamp(start)} --> {_timestamp(end)}\n{caption.text}')
    return '\n\n'.join(blocks) + ('\n' if blocks else '')


def read_srt(path: str | Path, fps) -> list[Caption]:
    with Path(path).expanduser().open('rb') as stream:
        content = stream.read(MAX_SRT_BYTES + 1)
    if len(content) > MAX_SRT_BYTES:
        raise ValueError("SRT file exceeds 32 MiB")
    try:
        return parse_srt(content.decode('utf-8-sig'), fps)
    except UnicodeDecodeError as exc:
        raise ValueError("SRT file must be UTF-8 encoded") from exc


def write_srt(path: str | Path, captions: list[Caption], fps) -> None:
    content = format_srt(captions, fps)
    # Exclusive creation also refuses symlink aliases and never truncates data.
    with Path(path).expanduser().open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(content)

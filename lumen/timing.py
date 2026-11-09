"""Bounded, exact constant-speed timing in rational project-frame units.

Timeline positions are integer project frames. Source positions are *not*
indices in a source file's native frame rate. No binary float is accepted by
this module, and serialization has one canonical representation.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import gcd
import re
from typing import Any

MIN_SPEED = Fraction(1, 4)
MAX_SPEED = Fraction(4)
MAX_RATIO_COMPONENT = 1_000_000
MAX_SOURCE_NUMERATOR = 2**53 - 1
MAX_SOURCE_DENOMINATOR = 1_000_000


def _fraction(value: Any, name: str, *, text: bool = False) -> Fraction:
    if type(value) is int or isinstance(value, Fraction):
        return Fraction(value)
    if (text and isinstance(value, str) and 0 < len(value.strip()) <= 128
            and re.fullmatch(r"[+-]?(?:[0-9]+(?:/[0-9]+|\.[0-9]*)?|\.[0-9]+)", value.strip())):
        try:
            return Fraction(value.strip())
        except (ValueError, ZeroDivisionError, OverflowError):
            pass
    raise ValueError(f"{name} must be an exact integer, fraction or decimal string")


def validate_source_position(value: Any) -> Fraction:
    result = _fraction(value, "Source position")
    if not 0 <= result.numerator <= MAX_SOURCE_NUMERATOR or result.denominator > MAX_SOURCE_DENOMINATOR:
        raise ValueError("Source position exceeds supported rational bounds")
    return result


def parse_source_position(value: Any) -> Fraction:
    """Parse an exact UI value; reject excessive precision instead of rounding."""
    return validate_source_position(_fraction(value, "Source position", text=True))


def parse_speed(value: Any) -> Fraction:
    result = _fraction(value, "Speed", text=True)
    if not MIN_SPEED <= result <= MAX_SPEED:
        raise ValueError("Speed must be between 1/4 and 4")
    if result.numerator > MAX_RATIO_COMPONENT or result.denominator > MAX_RATIO_COMPONENT:
        raise ValueError("Speed exceeds supported rational bounds")
    return result


@dataclass(frozen=True)
class ClipTimeMap:
    source_in: Fraction
    source_out: Fraction
    speed: Fraction = Fraction(1)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_in", validate_source_position(self.source_in))
        object.__setattr__(self, "source_out", validate_source_position(self.source_out))
        # Constructors do not accept strings; UI parsing is a separate boundary.
        object.__setattr__(self, "speed", parse_speed(_fraction(self.speed, "Speed")))
        validate_time_map(self)

    def to_dict(self) -> dict[str, int]:
        validate_time_map(self)
        return {
            "source_in_num": self.source_in.numerator,
            "source_in_den": self.source_in.denominator,
            "source_out_num": self.source_out.numerator,
            "source_out_den": self.source_out.denominator,
            "speed_num": self.speed.numerator,
            "speed_den": self.speed.denominator,
        }

    @classmethod
    def from_dict(cls, value: Any) -> ClipTimeMap:
        names = ("source_in", "source_out", "speed")
        required = {f"{name}_{part}" for name in names for part in ("num", "den")}
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError("Time map must contain exactly six rational numerator/denominator fields")
        ratios = []
        for name in names:
            numerator, denominator = value[f"{name}_num"], value[f"{name}_den"]
            max_num = MAX_RATIO_COMPONENT if name == "speed" else MAX_SOURCE_NUMERATOR
            max_den = MAX_RATIO_COMPONENT if name == "speed" else MAX_SOURCE_DENOMINATOR
            if (type(numerator) is not int or type(denominator) is not int
                    or not 0 <= numerator <= max_num or not 1 <= denominator <= max_den):
                raise ValueError("Time map contains an invalid or oversized rational pair")
            if gcd(numerator, denominator) != 1:
                raise ValueError("Time map rational pairs must be reduced")
            ratios.append(Fraction(numerator, denominator))
        return cls(*ratios)


def validate_time_map(mapping: ClipTimeMap) -> None:
    if not isinstance(mapping, ClipTimeMap):
        raise ValueError("Invalid clip time map")
    validate_source_position(mapping.source_in)
    validate_source_position(mapping.source_out)
    parse_speed(mapping.speed)
    if mapping.source_out <= mapping.source_in:
        raise ValueError("Source endpoint must be after source origin")


def source_position(mapping: ClipTimeMap, local_frame: int | Fraction) -> Fraction:
    validate_time_map(mapping)
    return mapping.source_in + _fraction(local_frame, "Local frame") * mapping.speed


def source_interval(mapping: ClipTimeMap) -> tuple[Fraction, Fraction]:
    validate_time_map(mapping)
    return mapping.source_in, mapping.source_out


def source_to_local(mapping: ClipTimeMap, position: int | Fraction) -> Fraction:
    validate_time_map(mapping)
    return (_fraction(position, "Source position") - mapping.source_in) / mapping.speed


def duration_frames(mapping: ClipTimeMap) -> int:
    validate_time_map(mapping)
    duration = (mapping.source_out - mapping.source_in) / mapping.speed
    return (duration.numerator + duration.denominator - 1) // duration.denominator


def seconds_at_source(mapping: ClipTimeMap, local_frame: int | Fraction,
                      project_fps: int | Fraction) -> Fraction:
    fps = _fraction(project_fps, "Project frame rate")
    if fps <= 0:
        raise ValueError("Project frame rate must be positive")
    return source_position(mapping, local_frame) / fps


def round_half_up(value: int | Fraction) -> int:
    """Round exact ties away from zero (all timeline/sample inputs are >= 0)."""
    value = _fraction(value, "Rounding value")
    if value < 0:
        return -round_half_up(-value)
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def sample_boundary(frame: int, fps: int | Fraction, rate: int = 48000) -> int:
    if type(frame) is not int or frame < 0:
        raise ValueError("Sample boundary frame must be a nonnegative integer")
    if type(rate) is not int or rate <= 0:
        raise ValueError("Sample rate must be a positive integer")
    fps = _fraction(fps, "Project frame rate")
    if fps <= 0:
        raise ValueError("Project frame rate must be positive")
    return round_half_up(Fraction(frame * rate) / fps)


def atempo_factors(rate: int | Fraction) -> tuple[Fraction, ...]:
    """Pitch-preserving factors within FFmpeg's non-skipping [1/2, 2] range."""
    rate = parse_speed(_fraction(rate, "Speed"))
    factors = []
    while rate > 2:
        factors.append(Fraction(2))
        rate /= 2
    while rate < Fraction(1, 2):
        factors.append(Fraction(1, 2))
        rate *= 2
    if rate != 1:
        factors.append(rate)
    return tuple(factors)

"""Bounded, portable settings for local spatial quality restoration.

The three conservative controls operate on each picture independently. They
are not AI super-resolution, detail reconstruction or temporal noise reduction.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import math
from typing import Any


@dataclass
class RestorationSettings:
    enabled: bool = False
    deblock: float = 0.0
    denoise: float = 0.0
    sharpen: float = 0.0

    def __post_init__(self) -> None:
        validate_restoration_settings(self)

    def to_dict(self) -> dict[str, Any]:
        validate_restoration_settings(self)
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RestorationSettings:
        """Read the complete schema-nine representation without coercion."""
        if not isinstance(data, dict) or set(data) != {f.name for f in fields(cls)}:
            raise ValueError("Restoration requires exactly enabled, deblock, denoise and sharpen")
        return cls(**data)


def validate_restoration_settings(value: RestorationSettings) -> None:
    """Validate at every edit/load boundary, including bypassed strengths."""
    if not isinstance(value, RestorationSettings):
        raise ValueError("Invalid restoration settings")
    if type(value.enabled) is not bool:
        raise ValueError("Restoration enabled must be a boolean")
    for name in ("deblock", "denoise", "sharpen"):
        number = getattr(value, name)
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or not 0 <= number <= 1 or not math.isfinite(number)):
            raise ValueError(f"Restoration {name} must be finite and in 0.0..1.0")

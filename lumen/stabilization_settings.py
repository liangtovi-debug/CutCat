"""Bounded, portable settings for nondestructive picture stabilization.

The motion path is analyzed on the full source at the project's frame rate.
``smoothing`` is a radius on that source grid, not on the retimed output.
This first implementation corrects two-dimensional translation and rotation;
it does not provide optical-flow interpolation, rolling-shutter repair or deblur.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import math
from typing import Any


@dataclass
class StabilizationSettings:
    enabled: bool = False
    strength: float = 1.0
    smoothing: int = 15
    max_zoom: float = 1.15
    border: str = "crop"

    def __post_init__(self) -> None:
        validate_stabilization_settings(self)

    def to_dict(self) -> dict[str, Any]:
        validate_stabilization_settings(self)
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StabilizationSettings:
        """Read the complete schema-eight representation without coercion."""
        if not isinstance(data, dict) or set(data) != {f.name for f in fields(cls)}:
            raise ValueError("Stabilization requires exactly enabled, strength, smoothing, max_zoom and border")
        return cls(**data)


def validate_stabilization_settings(value: StabilizationSettings) -> None:
    """Validate again at every edit/load boundary, including disabled settings."""
    if not isinstance(value, StabilizationSettings):
        raise ValueError("Invalid stabilization settings")
    if type(value.enabled) is not bool:
        raise ValueError("Stabilization enabled must be a boolean")
    for name, low, high in (("strength", 0.0, 1.0), ("max_zoom", 1.0, 1.5)):
        number = getattr(value, name)
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or not low <= number <= high or not math.isfinite(number)):
            raise ValueError(f"Stabilization {name} must be finite and in {low}..{high}")
    if type(value.smoothing) is not int or not 1 <= value.smoothing <= 120:
        raise ValueError("Stabilization smoothing must be an integer in 1..120")
    if not isinstance(value.border, str) or value.border not in ("crop", "black"):
        raise ValueError("Stabilization border must be crop or black")

"""Pure clip-local picture geometry and opacity keyframes.

The four animated values are absolute replacements for static x/y/scale and
opacity, not offsets or multipliers. Rotation, mirrors and transparent exterior
remain static. Times are signed project frames, independent of source speed;
hidden keys remain interpolation handles after trimming, splitting or rolling.
"""
from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
from dataclasses import asdict, dataclass, fields, replace
from fractions import Fraction
import math
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .model import Clip


MAX_MOTION_KEYFRAMES = 128
MAX_MOTION_FRAME = 2**53 - 1
MOTION_FIELDS = ('x', 'y', 'scale', 'opacity')


def _validate_motion_keyframe(key: ClipMotionKeyframe) -> None:
    if not isinstance(key, ClipMotionKeyframe):
        raise ValueError('Motion keyframes require typed keyframe records')
    if type(key.frame) is not int or not -MAX_MOTION_FRAME <= key.frame <= MAX_MOTION_FRAME:
        raise ValueError('Motion keyframe frame must be a bounded signed integer')
    # Keep exactly the existing ClipTransform and ClipComposite numeric bounds.
    for name, low, high in (('x', -16384, 16384), ('y', -16384, 16384),
                            ('scale', .1, 4), ('opacity', 0, 1)):
        value = getattr(key, name)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not low <= value <= high or not math.isfinite(value)):
            raise ValueError(f'Motion keyframe {name} must be finite and in {low}..{high}')
    if type(key.interpolation) is not str or key.interpolation not in ('hold', 'linear'):
        raise ValueError('Motion interpolation must be hold or linear')


@dataclass(frozen=True)
class ClipMotionKeyframe:
    """Absolute values at a local project frame; interpolation is outgoing."""

    frame: int = 0
    x: float = 0.0
    y: float = 0.0
    scale: float = 1.0
    opacity: float = 1.0
    interpolation: str = 'linear'

    def __post_init__(self) -> None:
        _validate_motion_keyframe(self)

    def to_dict(self) -> dict[str, Any]:
        _validate_motion_keyframe(self)
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClipMotionKeyframe:
        if not isinstance(data, dict) or set(data) != {f.name for f in fields(cls)}:
            raise ValueError('Motion keyframe requires exactly its documented fields')
        return cls(**data)


def validate_motion_keyframes(entries: list[ClipMotionKeyframe]) -> None:
    if not isinstance(entries, list) or len(entries) > MAX_MOTION_KEYFRAMES:
        raise ValueError('Motion keyframes must be a list of at most 128 entries')
    previous = None
    for key in entries:
        _validate_motion_keyframe(key)
        if previous is not None and key.frame <= previous:
            raise ValueError('Motion keyframe frames must be strictly increasing and unique')
        previous = key.frame


def evaluate_clip_motion(keys: list[ClipMotionKeyframe],
                         frame: int | Fraction) -> tuple[float, float, float, float]:
    """Return (x, y, scale, opacity), without mutation or source-time scaling.

    First and last values hold outside the keyed interval. At every exact key
    its values win, including the end of an outgoing Hold segment. Empty keys
    require callers to use their unchanged static path, not an invented default.
    """
    if type(frame) is not int and not isinstance(frame, Fraction):
        raise ValueError('Motion evaluation frame must be an integer or Fraction')
    validate_motion_keyframes(keys)
    if not keys:
        raise ValueError('Motion evaluation requires at least one keyframe; use the static path')
    index = bisect_right([key.frame for key in keys], frame) - 1
    left = keys[max(0, index)]
    values = tuple(getattr(left, name) for name in MOTION_FIELDS)
    if 0 <= index < len(keys) - 1 and frame != left.frame and left.interpolation == 'linear':
        right = keys[index + 1]
        weight = float(Fraction(frame - left.frame) / (right.frame - left.frame))
        # Clamp roundoff to endpoints so extreme bounded scales/opacities never
        # escape their domain at a Fraction arbitrarily close to an endpoint.
        values = tuple(min(max(a, b), max(min(a, b), a + (b - a) * weight))
                       for a, b in zip(values, (getattr(right, name) for name in MOTION_FIELDS)))
    return values


def shift_clip_motion(clip: Clip, delta: int) -> Clip:
    """Copy a clip and shift ALL keys by -delta, rejecting overflow atomically."""
    from .model import Clip
    if not isinstance(clip, Clip):
        raise ValueError('Motion rebasing requires a clip')
    if type(delta) is not int:
        raise ValueError('Motion animation delta must be an integer')
    validate_motion_keyframes(clip.motion_keyframes)
    shifted = [replace(key, frame=key.frame - delta) for key in clip.motion_keyframes]
    validate_motion_keyframes(shifted)
    result = deepcopy(clip)
    result.motion_keyframes = shifted
    return result

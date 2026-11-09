"""Strict static clip geometry, applied to the already fitted project canvas.

Positive angles turn clockwise in screen coordinates. Mirrors act in source
axes, then uniform scale and rotation act about the frame's pixel center;
translation is last and is measured in project pixels. Uncovered pixels are
opaque black. Identity deliberately adds no filters to the legacy render path.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import math
from typing import Any


@dataclass
class ClipTransform:
    x: float = 0.0
    y: float = 0.0
    scale: float = 1.0
    rotation: float = 0.0
    mirror_x: bool = False
    mirror_y: bool = False

    def __post_init__(self) -> None:
        validate_transform(self)

    def to_dict(self) -> dict[str, Any]:
        validate_transform(self)
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClipTransform:
        if not isinstance(data, dict) or set(data) != {f.name for f in fields(cls)}:
            raise ValueError('Transform requires exactly x, y, scale, rotation, mirror_x and mirror_y')
        return cls(**data)

    def is_identity(self) -> bool:
        return is_identity(self)


def validate_transform(settings: ClipTransform) -> None:
    if not isinstance(settings, ClipTransform):
        raise ValueError('Invalid clip transform')
    for name, low, high in (('x', -16384, 16384), ('y', -16384, 16384),
                            ('scale', .1, 4), ('rotation', -180, 180)):
        value = getattr(settings, name)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not low <= value <= high or not math.isfinite(value)):
            raise ValueError(f'Transform {name} must be finite and in {low}..{high}')
    for name in ('mirror_x', 'mirror_y'):
        if type(getattr(settings, name)) is not bool:
            raise ValueError(f'Transform {name} must be a boolean')


def is_identity(settings: ClipTransform) -> bool:
    validate_transform(settings)
    return (settings.x == 0 and settings.y == 0 and settings.scale == 1
            and settings.rotation == 0 and not settings.mirror_x and not settings.mirror_y)


def transform_filters(settings: ClipTransform, width: int, height: int) -> list[str]:
    """Return a single-input FFmpeg filter chain, never allocating scaled frames.

    Call after fit/pad and clip effects, before compositing tracks/overlays.
    Inverse affine sampling keeps memory proportional to the project canvas at
    every allowed zoom/offset. RGB16 preserves grading precision and gives a
    range-independent true black border. A one-pixel guard on the right/bottom
    avoids geq's bilinear sampler clamping the last row/column to the previous
    pixel; it is immediately cropped away. Output has no alpha plane.
    """
    validate_transform(settings)
    if any(type(v) is not int or not 1 <= v <= 16384 for v in (width, height)):
        raise ValueError('Transform canvas dimensions must be integers in 1..16384')
    if is_identity(settings):
        return []
    # Common exact operations use FFmpeg's native permutations, rather than
    # evaluating inverse-coordinate expressions for every pixel. RGB without
    # alpha keeps the same opaque-canvas contract as the general path.
    if settings.x == 0 and settings.y == 0 and settings.scale == 1:
        if settings.rotation in (0, -180, 180):
            half_turn = settings.rotation != 0
            filters = ['format=gbrp16le']
            if settings.mirror_x != half_turn:
                filters.append('hflip')
            if settings.mirror_y != half_turn:
                filters.append('vflip')
            return filters
        if width == height and settings.rotation in (-90, 90):
            filters = ['format=gbrp16le']
            if settings.mirror_x:
                filters.append('hflip')
            if settings.mirror_y:
                filters.append('vflip')
            filters.append('transpose=clock' if settings.rotation == 90 else 'transpose=cclock')
            return filters
    angle = math.radians(settings.rotation)
    cosine, sine = math.cos(angle), math.sin(angle)
    # Exact quarter turns must not lose boundary pixels to floating error.
    cosine = 0.0 if abs(cosine) < 1e-12 else cosine
    sine = 0.0 if abs(sine) < 1e-12 else sine
    mx = (-1 if settings.mirror_x else 1) / settings.scale
    my = (-1 if settings.mirror_y else 1) / settings.scale
    cx, cy = (width - 1) / 2, (height - 1) / 2
    a, b, c, d = mx * cosine, mx * sine, -my * sine, my * cosine
    tx = cx - a * (cx + settings.x) - b * (cy + settings.y)
    ty = cy - c * (cx + settings.x) - d * (cy + settings.y)
    def number(v: float) -> str:
        return format(v, '.15g')
    sx = f'({number(a)}*X+{number(b)}*Y+{number(tx)})'
    sy = f'({number(c)}*X+{number(d)}*Y+{number(ty)})'
    # Registers are local to each expression/slice; never depend on prior pixels.
    expressions = []
    for plane in ('r', 'g', 'b'):
        expression = (f'st(0,{sx});st(1,{sy});'
                      f'if(between(ld(0),0,{width - 1})*between(ld(1),0,{height - 1}),'
                      f'{plane}(ld(0),ld(1)),0)')
        expressions.append(f"{plane}='{expression}'")
    return ['format=gbrp16le', f'pad={width + 1}:{height + 1}:0:0:color=black',
            'geq=' + ':'.join(expressions) + ':interpolation=bilinear',
            f'crop={width}:{height}:0:0']

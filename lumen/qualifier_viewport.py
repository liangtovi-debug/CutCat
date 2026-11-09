"""Bounded, exact source-pixel geometry for qualifier overview and detail.

Overview deliberately samples a sparse grid. Detail never resizes or averages
source RGB: it exposes a contiguous crop, so every pixel in that crop is
pickable even when the overview's sparse grid misses it. Here source means
the fitted project-canvas pixels supplied by the renderer, not camera-sensor
originals or a new decode at the media's native resolution.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from numbers import Integral, Real
import math

import numpy as np


MAX_VIEW_SIZE = 512
DETAIL_ZOOMS = (1, 2, 4, 8)


def _integer(value: int, name: str, minimum: int = 0) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral):
        raise ValueError(f'{name} must be an integer')
    if value < minimum:
        raise ValueError(f'{name} must be at least {minimum}')
    return int(value)


def _dimensions(width: int, height: int) -> tuple[int, int]:
    return _integer(width, 'Source width', 1), _integer(height, 'Source height', 1)


def _bound_size(max_size: int) -> int:
    size = _integer(max_size, 'Maximum view size', 1)
    if size > MAX_VIEW_SIZE:
        raise ValueError('Maximum view size must not exceed 512')
    return size


@dataclass(frozen=True)
class Viewport:
    """Source rectangle and sample stride, not dimensions of a resized image.

    The right and bottom boundaries are exclusive. The sampled image has
    ceil(width/stride) columns and ceil(height/stride) rows, each at most 512.
    """
    x: int
    y: int
    width: int
    height: int
    stride: int = 1

    def __post_init__(self) -> None:
        for name in ('x', 'y', 'width', 'height', 'stride'):
            value = _integer(getattr(self, name), name,
                             0 if name in ('x', 'y') else 1)
            object.__setattr__(self, name, value)
        if max(self.sample_width, self.sample_height) > MAX_VIEW_SIZE:
            raise ValueError('Viewport sample dimensions must not exceed 512')

    @property
    def sample_width(self) -> int:
        return (self.width + self.stride - 1) // self.stride

    @property
    def sample_height(self) -> int:
        return (self.height + self.stride - 1) // self.stride

    @property
    def center(self) -> tuple[float, float]:
        return self.x + (self.width - 1) / 2, self.y + (self.height - 1) / 2

    def source_grid(self) -> tuple[np.ndarray, np.ndarray]:
        """Return independent int64 (x, y) coordinate axes, each at most 512."""
        return (self.x + np.arange(self.sample_width, dtype=np.int64) * self.stride,
                self.y + np.arange(self.sample_height, dtype=np.int64) * self.stride)

    def source_point(self, x: int, y: int) -> tuple[int, int]:
        """Map one sampled-image pixel to its exact original source pixel."""
        x, y = _integer(x, 'Sample x'), _integer(y, 'Sample y')
        if x >= self.sample_width or y >= self.sample_height:
            raise ValueError('Sample point is outside the viewport')
        return self.x + x * self.stride, self.y + y * self.stride

    def source_at(self, grid_x: int, grid_y: int) -> tuple[int, int] | None:
        """Nullable mapping for UI hit tests; reject noninteger/outside points."""
        try:
            return self.source_point(grid_x, grid_y)
        except ValueError:
            return None

    def grid_at(self, source_x: int, source_y: int) -> tuple[int, int] | None:
        """Return grid indices only for an exactly represented source pixel.

        A sparse overview must not show a sample marker on a nearby but
        different color. Detail crops represent every integer pixel inside.
        """
        try:
            sx = _integer(source_x, 'Source x')
            sy = _integer(source_y, 'Source y')
        except ValueError:
            return None
        dx, dy = sx - self.x, sy - self.y
        if not (0 <= dx < self.width and 0 <= dy < self.height):
            return None
        if dx % self.stride or dy % self.stride:
            return None
        return dx // self.stride, dy // self.stride

    def extract(self, frame: np.ndarray) -> np.ndarray:
        """Return a bounded slice view, preserving channels, dtype and values.

        Works for RGB, RGBA or a 2D validity mask. Reject an out-of-frame
        rectangle instead of silently shortening it and breaking coordinates.
        """
        if not isinstance(frame, np.ndarray) or frame.ndim < 2:
            raise ValueError('Viewport extraction requires a NumPy image')
        if self.x + self.width > frame.shape[1] or self.y + self.height > frame.shape[0]:
            raise ValueError('Viewport is outside the source frame')
        return frame[self.y:self.y + self.height:self.stride,
                     self.x:self.x + self.width:self.stride]

    def pan(self, dx: int, dy: int, source_width: int, source_height: int) -> Viewport:
        """Move by integer source pixels, retaining extent and clamping edges."""
        width, height = _dimensions(source_width, source_height)
        # Deltas may be negative; unlike indices they have no lower bound.
        for value in (dx, dy):
            if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral):
                raise ValueError('Pan deltas must be integers')
        if self.width > width or self.height > height:
            raise ValueError('Viewport cannot fit the source frame')
        return replace(self, x=min(max(0, self.x + int(dx)), width - self.width),
                       y=min(max(0, self.y + int(dy)), height - self.height))


def overview(width: int, height: int, max_size: int = MAX_VIEW_SIZE) -> Viewport:
    """Use the existing sparse overview grid, anchored at source pixel (0, 0)."""
    width, height = _dimensions(width, height)
    size = _bound_size(max_size)
    stride = (max(width, height) + size - 1) // size
    return Viewport(0, 0, width, height, stride)


def centered(width: int, height: int, center: tuple[float, float] | None = None,
             zoom: int = 1, max_size: int = MAX_VIEW_SIZE) -> Viewport:
    """Create a clamped, full-resolution detail crop around a source point.

    Detail zoom 1/2/4/8 yields a maximum crop side of 512/256/128/64.
    Overview is a separate mode. A smaller source is never upsampled here.
    Fractional centers are accepted; ties round toward the greater coordinate.
    """
    width, height = _dimensions(width, height)
    size = _bound_size(max_size)
    zoom = _integer(zoom, 'Detail zoom', 1)
    if zoom not in DETAIL_ZOOMS:
        raise ValueError('Detail zoom must be 1, 2, 4 or 8')
    if center is None:
        center = ((width - 1) / 2, (height - 1) / 2)
    if not isinstance(center, (tuple, list)) or len(center) != 2:
        raise ValueError('Center must contain source x and y')
    for value in center:
        if (isinstance(value, (bool, np.bool_)) or not isinstance(value, Real)
                or not math.isfinite(value)):
            raise ValueError('Center coordinates must be finite numbers')
    side = max(1, (size + zoom - 1) // zoom)
    crop_width, crop_height = min(width, side), min(height, side)
    # Clamp before converting: enormous finite off-screen coordinates cannot
    # overflow and always select the nearest source edge.
    cx = min(max(float(center[0]), 0), width - 1)
    cy = min(max(float(center[1]), 0), height - 1)
    x = min(max(0, math.floor(cx - (crop_width - 1) / 2 + 0.5)), width - crop_width)
    y = min(max(0, math.floor(cy - (crop_height - 1) / 2 + 0.5)), height - crop_height)
    return Viewport(x, y, crop_width, crop_height)

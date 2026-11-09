"""Paint/gesture geometry for one static qualifier window, never image pixels.

The display is a sparse sample grid or a full-resolution crop. Its sample
centers represent ``origin + index * stride + .5`` in fitted-canvas edge
coordinates, not evenly spaced fractions of the complete source dimensions.
Keeping that phase is essential for odd-sized overview frames and small detail
crops. All functions use continuous logical screen coordinates: Qt's fitted
physical-pixel rectangle supplies the only device-pixel-ratio rounding.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math

from .qualifier_viewport import Viewport


GEOMETRY_FIELDS = ('window_center_x', 'window_center_y', 'window_width', 'window_height')


def _clamp(value, low, high):
    return min(high, max(low, value))


@dataclass(frozen=True)
class WindowGeometry:
    center_x: float
    center_y: float
    width: float
    height: float
    shape: str

    @classmethod
    def from_settings(cls, settings):
        return cls(*(getattr(settings, name) for name in GEOMETRY_FIELDS), settings.window_shape)

    def values(self):
        return dict(zip(GEOMETRY_FIELDS, (self.center_x, self.center_y, self.width, self.height)))


@dataclass(frozen=True)
class WindowMapping:
    viewport: Viewport
    canvas_width: int
    canvas_height: int
    left: float
    top: float
    width: float
    height: float

    def __post_init__(self):
        if (self.canvas_width <= 0 or self.canvas_height <= 0 or self.width <= 0
                or self.height <= 0 or not all(math.isfinite(value) for value in
                (self.left, self.top, self.width, self.height))):
            raise ValueError('Window display mapping requires positive finite dimensions')
        if (self.viewport.x + self.viewport.width > self.canvas_width or
                self.viewport.y + self.viewport.height > self.canvas_height):
            raise ValueError('Window viewport must lie inside the fitted canvas')

    def display_point(self, x, y):
        """Normalized fitted-canvas edge coordinates -> logical display point."""
        view = self.viewport
        gx = (x * self.canvas_width - view.x - .5) / view.stride + .5
        gy = (y * self.canvas_height - view.y - .5) / view.stride + .5
        return (self.left + gx * self.width / view.sample_width,
                self.top + gy * self.height / view.sample_height)

    def canvas_point(self, x, y):
        """Logical display point -> normalized fitted-canvas edge coordinates."""
        view = self.viewport
        gx = (x - self.left) * view.sample_width / self.width
        gy = (y - self.top) * view.sample_height / self.height
        return ((view.x + .5 + (gx - .5) * view.stride) / self.canvas_width,
                (view.y + .5 + (gy - .5) * view.stride) / self.canvas_height)

    def canvas_delta(self, dx, dy):
        view = self.viewport
        return (dx * view.sample_width * view.stride / (self.width * self.canvas_width),
                dy * view.sample_height * view.stride / (self.height * self.canvas_height))

    def bounds(self, geometry):
        left, top = self.display_point(geometry.center_x - geometry.width / 2,
                                       geometry.center_y - geometry.height / 2)
        right, bottom = self.display_point(geometry.center_x + geometry.width / 2,
                                           geometry.center_y + geometry.height / 2)
        return left, top, right, bottom

    def handles(self, geometry, minimum_radius=16.):
        """Readable paint/hit targets, separated even for a subpixel window.

        Only the handle target is offset; the core outline and stored geometry
        remain exact. Gestures use displacement from their press point so an
        offset handle never jumps or quantizes the actual shape on click.
        """
        cx, cy = self.display_point(geometry.center_x, geometry.center_y)
        left, top, right, bottom = self.bounds(geometry)
        rx, ry = max((right - left) / 2, minimum_radius), max((bottom - top) / 2, minimum_radius)
        return {'center': (cx, cy), 'left': (cx - rx, cy), 'right': (cx + rx, cy),
                'top': (cx, cy - ry), 'bottom': (cx, cy + ry)}

    def contains(self, geometry, x, y):
        nx, ny = self.canvas_point(x, y)
        dx = (nx - geometry.center_x) * 2 / geometry.width
        dy = (ny - geometry.center_y) * 2 / geometry.height
        return (math.hypot(dx, dy) <= 1 if geometry.shape == 'ellipse'
                else max(abs(dx), abs(dy)) <= 1)


@dataclass(frozen=True)
class WindowDrag:
    geometry: WindowGeometry
    mapping: WindowMapping
    handle: str
    start_x: float
    start_y: float

    def updated(self, x, y):
        """Total displacement, not accumulated rounded spin-control values.

        Edges resize symmetrically around the fixed center. Crossing the center
        stops at the minimum size; it never flips the shape or its axes.
        """
        if not all(math.isfinite(value) for value in (x, y)):
            return self.geometry
        dx, dy = self.mapping.canvas_delta(x - self.start_x, y - self.start_y)
        before = self.geometry
        if self.handle == 'center':
            return replace(before, center_x=_clamp(before.center_x + dx, 0., 1.),
                           center_y=_clamp(before.center_y + dy, 0., 1.))
        if self.handle in ('left', 'right'):
            width = before.width + (2 if self.handle == 'right' else -2) * dx
            return replace(before, width=_clamp(width, .001, 2.))
        if self.handle in ('top', 'bottom'):
            height = before.height + (2 if self.handle == 'bottom' else -2) * dy
            return replace(before, height=_clamp(height, .001, 2.))
        raise ValueError('Unknown static window handle')

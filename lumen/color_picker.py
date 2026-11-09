"""Pure hue-picker helpers for bounded encoded Rec.709 preview samples.

Picking inserts controls, not an HSL mask. Anchors subdivide the existing
piecewise-linear curve without resetting its values or other selections.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from .model import _hue_curve
from .selective_color import _hue


# One encoded 8-bit code value of RGB spread is deliberately considered gray.
MIN_PICK_CHROMA = 1.0 / 255.0
POINT_SNAP_TOLERANCE = 1e-5


@dataclass(frozen=True)
class PickerSelection:
    points: list[list[float]]
    selected_index: int
    # Closed display intervals in [0,1], split where the support crosses red.
    # These describe moving the selected control, NOT the whole active curve.
    support_intervals: list[tuple[float, float]]


def sample_hue(rgb: np.ndarray, valid: bool = True) -> float | None:
    """Return original sample hue, or None when no reliable hue can be picked.

    Only a float32 RGB triplet in [0,1] is accepted. ``valid=False`` covers
    transparent/outside-image pixels supplied by the preview mapping layer.
    Gray, black and near-gray never silently become the red selection.
    """
    if not valid or not isinstance(rgb, np.ndarray) or rgb.dtype != np.float32:
        return None
    if rgb.shape != (3,) or not np.all(np.isfinite(rgb)):
        return None
    if np.any(rgb < 0) or np.any(rgb > 1):
        return None
    high = np.max(rgb)
    chroma = high - np.min(rgb)
    if chroma <= np.float32(MIN_PICK_CHROMA):
        return None
    return float(_hue(rgb.reshape(1, 3), np.array([high]), np.array([chroma]))[0])


def _validate(points: list[list[float]]) -> None:
    # Union of both selective curve output ranges. The owner still validates
    # the particular curve key; insertion itself never changes any y values.
    _hue_curve(points, 'hue picker curve', -0.5, 2.0)


def _nearest(points: list[list[float]], hue: float) -> float | None:
    candidates = points[:-1]  # 1 is the linked duplicate of red at 0.
    closest = min(candidates, key=lambda p: min(abs(p[0] - hue), 1 - abs(p[0] - hue)))
    distance = min(abs(closest[0] - hue), 1 - abs(closest[0] - hue))
    return closest[0] if distance <= POINT_SNAP_TOLERANCE else None


def selected_support_intervals(points: list[list[float]], selected_index: int) -> list[tuple[float, float]]:
    """Actual adjacent-control support of one edit, including linked red ends."""
    _validate(points)
    if isinstance(selected_index, bool) or not isinstance(selected_index, int) or not 0 <= selected_index < len(points):
        raise ValueError('Hue picker selection is invalid')
    if selected_index in (0, len(points) - 1):
        if len(points) <= 3:
            return [(0.0, 1.0)]
        return [(0.0, float(points[1][0])), (float(points[-2][0]), 1.0)]
    return [(float(points[selected_index - 1][0]), float(points[selected_index + 1][0]))]


def insert_hue_points(points: list[list[float]], hue: float,
                      half_width: float = 1 / 12) -> PickerSelection:
    """Atomically insert a center and two circular anchors on the current line.

    Existing controls and values are preserved exactly. Nearby controls are
    selected/reused, including canonical red at x=0, rather than duplicated.
    Interpolation uses float64 coordinates (narrow valid segments stay valid).
    Capacity failure raises before mutating anything, including the input list.
    """
    _validate(points)
    if isinstance(hue, bool) or not isinstance(hue, (int, float, np.floating)) or not math.isfinite(hue) or not 0 <= hue <= 1:
        raise ValueError('Hue picker hue must be finite and between 0 and 1')
    if isinstance(half_width, bool) or not isinstance(half_width, (int, float, np.floating)) or not math.isfinite(half_width) or not 0 < half_width < 0.5:
        raise ValueError('Hue picker half width must be between 0 and 0.5')
    hue = float(hue) % 1.0
    center = _nearest(points, hue)
    if center is None:
        center = hue
    result = [list(point) for point in points]
    original = np.asarray(points, dtype=np.float64)
    # Center first so close anchors cannot take precedence over the selection.
    for x in (center, (center - half_width) % 1.0, (center + half_width) % 1.0):
        x = float(x)
        if _nearest(result, x) is not None:
            continue
        # Use the same stable convex interpolation as the renderer, without
        # its final float32 cast: subdivisions must preserve the original line.
        index = int(np.searchsorted(original[:, 0], x, side='right') - 1)
        lo, hi = original[index], original[index + 1]
        fraction = (x - lo[0]) / (hi[0] - lo[0])
        y = float(lo[1] + fraction * (hi[1] - lo[1]))
        result.append([x, y])
        result.sort(key=lambda point: point[0])
    if len(result) > 32:
        raise ValueError('Hue picker needs more control points than the 32-point limit')
    selected = next(i for i, point in enumerate(result) if point[0] == center)
    return PickerSelection(result, selected, selected_support_intervals(result, selected))


def curve_support_intervals(points: list[list[float]], neutral: float) -> list[tuple[float, float]]:
    """Return closure of ALL nonneutral curve segments, not a pixel/HSL mask.

    Isolated neutral crossings do not split display intervals. Red-wrapping
    ranges remain two normalized intervals unless the entire domain is active.
    No epsilon is used: even a small nonneutral control is a real adjustment.
    """
    _validate(points)
    if not math.isfinite(neutral):
        raise ValueError('Hue picker neutral value must be finite')
    intervals: list[tuple[float, float]] = []
    for lo, hi in zip(points, points[1:]):
        if lo[1] == neutral and hi[1] == neutral:
            continue
        start, end = float(lo[0]), float(hi[0])
        if intervals and intervals[-1][1] == start:
            intervals[-1] = (intervals[-1][0], end)
        else:
            intervals.append((start, end))
    return intervals

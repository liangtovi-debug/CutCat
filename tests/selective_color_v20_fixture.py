# Frozen pre-optimization selective-color evaluator from the v20 snapshot.
# Keep the arithmetic unchanged: this is a uint32-bit-pattern parity oracle.
# The only source adjustment is the absolute model import for test collection.
"""Bounded selective hue curves in encoded Rec.709; see SELECTIVE_COLOR.md.

Working pixels and output stay float32. Control coordinates interpolate in
float64 so distinct valid JSON coordinates never collapse into a zero-width
segment when converted to float32. No image samples are clipped or quantized.
"""
from __future__ import annotations

import numpy as np

from lumen.model import _hue_curve


_LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
# Bound temporary arrays independently of frame dimensions. The returned frame
# is the one unavoidable image-sized allocation; no hue-sector image is kept.
_BLOCK_PIXELS = 65536


def _evaluate_curve(hue: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Interpolate without constructing unbounded slopes for narrow segments."""
    index = np.searchsorted(points[:, 0], hue, side='right') - 1
    index = np.minimum(index, len(points) - 2)
    lo, hi = points[index], points[index + 1]
    fraction = (hue.astype(np.float64) - lo[:, 0]) / (hi[:, 0] - lo[:, 0])
    return (lo[:, 1] + fraction * (hi[:, 1] - lo[:, 1])).astype(np.float32)


def _hue(rgb: np.ndarray, high: np.ndarray, chroma: np.ndarray) -> np.ndarray:
    """Hexcone hue without dividing by value (which may be zero or negative)."""
    hue = np.zeros(len(rgb), dtype=np.float32)
    chromatic = chroma != 0
    # Deterministic ties agree with the usual HSV definition: R, then G, then B.
    red = chromatic & (rgb[:, 0] == high)
    green = chromatic & ~red & (rgb[:, 1] == high)
    blue = chromatic & ~red & ~green
    hue[red] = (rgb[red, 1] - rgb[red, 2]) / chroma[red]
    hue[green] = (rgb[green, 2] - rgb[green, 0]) / chroma[green] + np.float32(2)
    hue[blue] = (rgb[blue, 0] - rgb[blue, 1]) / chroma[blue] + np.float32(4)
    hue = np.remainder(hue / np.float32(6), np.float32(1))
    # A tiny negative angle can round to exactly 1 in float32 modulo. Canonical
    # red is always zero, so it cannot become a nonexistent sixth sector.
    hue[hue >= 1] = 0
    return hue


def _rotate(rgb: np.ndarray, hue: np.ndarray, offset: np.ndarray,
            low: np.ndarray, high: np.ndarray, mask: np.ndarray) -> None:
    """Rotate only selected pixels, leaving sampled-neutral values bit exact."""
    if not np.any(mask):
        return
    phase = np.remainder(hue[mask] + offset[mask], np.float32(1))
    phase[phase >= 1] = 0
    phase *= np.float32(6)
    sector = np.floor(phase).astype(np.intp)
    fraction = phase - sector.astype(np.float32)
    # One-dimensional sector tables avoid a pixels-by-sectors temporary.
    unit = np.empty((len(phase), 3), dtype=np.float32)
    for channel, table in enumerate(((1, -1, 0, 0, -2, 1),
                                      (-2, 1, 1, -1, 0, 0),
                                      (0, 0, -2, 1, 1, -1))):
        values = np.asarray(table, dtype=np.int8)[sector]
        unit[:, channel] = np.where(values == -2, fraction,
                                   np.where(values == -1, 1 - fraction, values))
    # Convex min/max reconstruction avoids a large chroma multiplication. It
    # also works when the entire RGB triplet is negative or above one.
    rgb[mask] = (low[mask, None] * (np.float32(1) - unit) +
                 high[mask, None] * unit)


def apply_selective_color(rgb: np.ndarray, hue_hue_curve: list,
                          hue_sat_curve: list) -> np.ndarray:
    """Return an independent float32 RGB frame after the two periodic curves.

    Both curves address the original incoming hue. First rotate hue preserving
    min/max; then scale saturation about the rotated encoded Rec.709 luma.
    Achromatic and sampled-neutral pixels are copied exactly. Finite values
    outside [0,1] are supported, but unrepresentable arithmetic raises ValueError
    rather than clipping or leaking NaNs. This is not an HSV-saturation control.
    """
    if not isinstance(rgb, np.ndarray) or rgb.dtype != np.float32:
        raise ValueError('Grade input must be a float32 NumPy array')
    if rgb.ndim < 1 or rgb.shape[-1] != 3:
        raise ValueError('RGB requires a final dimension of 3')
    if not np.all(np.isfinite(rgb)):
        raise ValueError('Color values must be finite')
    _hue_curve(hue_hue_curve, 'hue_hue_curve', -0.5, 0.5)
    _hue_curve(hue_sat_curve, 'hue_sat_curve', 0, 2)
    result = np.array(rgb, dtype=np.float32, order='C', copy=True)
    hue_neutral = all(point[1] == 0 for point in hue_hue_curve)
    sat_neutral = all(point[1] == 1 for point in hue_sat_curve)
    if hue_neutral and sat_neutral:
        return result
    hue_points = np.asarray(hue_hue_curve, dtype=np.float64)
    sat_points = np.asarray(hue_sat_curve, dtype=np.float64)
    pixels = result.reshape(-1, 3)
    with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
        for start in range(0, len(pixels), _BLOCK_PIXELS):
            block = pixels[start:start + _BLOCK_PIXELS]
            low, high = np.min(block, axis=-1), np.max(block, axis=-1)
            chroma = high - low
            if not np.all(np.isfinite(chroma)):
                raise ValueError('Grade produced non-finite values')
            chromatic = chroma != 0
            if not np.any(chromatic):
                continue
            hue = _hue(block, high, chroma)
            # Evaluate BOTH curves before changing RGB. Saturation selection
            # does not move to another hue range when hue rotation is enabled.
            offset = _evaluate_curve(hue, hue_points) if not hue_neutral else None
            factor = _evaluate_curve(hue, sat_points) if not sat_neutral else None
            if offset is not None:
                _rotate(block, hue, offset, low, high, chromatic & (offset != 0))
            if factor is not None:
                selected = chromatic & (factor != 1)
                values = block[selected]
                if len(values):
                    luma = np.sum(values * _LUMA, axis=-1, keepdims=True)
                    weight = factor[selected, None]
                    # Zero is explicitly gray; avoid inf*0 from an unnecessary
                    # difference at the extreme end of the float32 range.
                    nonzero = weight[:, 0] != 0
                    values[:] = np.broadcast_to(luma, values.shape)
                    if np.any(nonzero):
                        source = block[selected][nonzero]
                        values[nonzero] = (luma[nonzero] + weight[nonzero] *
                                           (source - luma[nonzero]))
                    block[selected] = values
            if not np.all(np.isfinite(block)):
                raise ValueError('Grade produced non-finite values')
    return result

"""Owned float32 SDR Rec.709 grade evaluator. See docs/COLOR_PIPELINE.md."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import shlex
import io
import numpy as np

from .model import Grade, _number
from .white_balance import validate_white_balance
from .selective_color import apply_selective_color
from .qualifier import apply_qualifier, validate_qualifier

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
IDENTITY_CURVE = ((0., 0.), (1., 1.))


def _finite(values):
    result = np.asarray(values, dtype=np.float32)
    if not np.all(np.isfinite(result)):
        raise ValueError('Color values must be finite')
    return result


def rec709_to_linear(encoded):
    """Inverse BT.709 OETF, extended symmetrically below zero, without clipping."""
    x = _finite(encoded)
    a = np.abs(x)
    return (np.sign(x) * np.where(a < np.float32(.081), a / np.float32(4.5),
            ((a + np.float32(.099)) / np.float32(1.099)) ** np.float32(1 / .45))).astype(np.float32)


def linear_to_rec709(linear):
    """BT.709 OETF, extended symmetrically below zero, without clipping."""
    x = _finite(linear)
    a = np.abs(x)
    return (np.sign(x) * np.where(a < np.float32(.018), a * np.float32(4.5),
            np.float32(1.099) * a ** np.float32(.45) - np.float32(.099))).astype(np.float32)


def srgb_to_linear(encoded):
    """IEC 61966-2-1 transfer, with an explicit signed working-range extension.

    Coefficients: ICC sRGB specification, https://registry.color.org/rgb-registry/files/sRGB.pdf
    This removes the sRGB encoding only; it is not ICC display calibration.
    """
    x = _finite(encoded)
    a = np.abs(x)
    return (np.sign(x) * np.where(a <= np.float32(.04045), a / np.float32(12.92),
            ((a + np.float32(.055)) / np.float32(1.055)) ** np.float32(2.4))).astype(np.float32)


def linear_to_srgb(linear):
    """IEC 61966-2-1 encoding, symmetrically extended without clipping."""
    x = _finite(linear)
    a = np.abs(x)
    return (np.sign(x) * np.where(a <= np.float32(.0031308), a * np.float32(12.92),
            np.float32(1.055) * a ** np.float32(1 / 2.4) - np.float32(.055))).astype(np.float32)


def normalize_input(rgb, encoding="rec709"):
    """Convert explicitly assigned Rec.709-primary RGB to the working encoding.

    sRGB and BT.709 share primaries/D65, so no gamut matrix is needed. This
    defines a transfer-only normalization, not a scene/display rendering or
    appearance transform. RGB48 codec ingress bounds source values to [0,1].
    """
    if encoding == "apple_log_bt2020":
        from .apple_log import apple_log_to_rec709
        return apple_log_to_rec709(rgb)
    if encoding == "rec709":
        return _finite(rgb).copy()
    if encoding == "srgb":
        with np.errstate(over='ignore', invalid='ignore'):
            return _finite(linear_to_rec709(srgb_to_linear(rgb)))
    if encoding == "linear_rec709":
        return _finite(linear_to_rec709(rgb))
    raise ValueError("Input encoding must be rec709, srgb, linear_rec709 or apple_log_bt2020")


def apply_curve(values, points):
    """Piecewise-linear curve, with terminal-slope extrapolation."""
    x = _finite(values)
    p = _finite(points)
    if p.ndim != 2 or p.shape[1] != 2 or len(p) < 2:
        raise ValueError('Curve needs at least two (x, y) points')
    if np.any(np.diff(p[:, 0]) <= 0):
        raise ValueError('Curve x must strictly increase')
    if np.array_equal(p[:, 0], p[:, 1]):
        return x.copy()
    indices = np.clip(np.searchsorted(p[:, 0], x, side='right') - 1, 0, len(p) - 2)
    lo, hi = p[indices], p[indices + 1]
    return (lo[..., 1] + (x - lo[..., 0]) * ((hi[..., 1] - lo[..., 1]) /
            (hi[..., 0] - lo[..., 0]))).astype(np.float32)


@dataclass(frozen=True)
class CubeLUT:
    # Index order is [blue, green, red, output_channel]: .cube red varies fastest.
    table: np.ndarray
    domain_min: np.ndarray
    domain_max: np.ndarray


def load_cube(path: str | Path) -> CubeLUT:
    """Read a bounded 3D .cube snapshot; cache by bytes, not timestamp granularity."""
    p = Path(path).expanduser().resolve(strict=True)
    stat = p.stat()
    if p.suffix.lower() != '.cube':
        raise ValueError('Only .cube 3D LUT files are supported')
    if stat.st_size > 64 * 1024 * 1024:
        raise ValueError('LUT exceeds 64 MiB')
    with p.open("rb") as stream:
        payload = stream.read(64 * 1024 * 1024 + 1)
    if len(payload) > 64 * 1024 * 1024:
        raise ValueError("LUT exceeds 64 MiB")
    return _load_cube(payload)


@lru_cache(maxsize=4)
def _load_cube(payload: bytes):
    size = None
    low, high = np.zeros(3, np.float32), np.ones(3, np.float32)
    rows, seen = [], set()
    with io.StringIO(payload.decode('utf-8-sig')) as stream:
        for number, line in enumerate(stream, 1):
            try:
                tokens = shlex.split(line, comments=True)
                if not tokens:
                    continue
                key = tokens[0]
                if key in ('TITLE', 'LUT_3D_SIZE', 'DOMAIN_MIN', 'DOMAIN_MAX'):
                    if key in seen or rows:
                        raise ValueError('Duplicate or late LUT header')
                    seen.add(key)
                    if key == 'TITLE':
                        if len(tokens) != 2:
                            raise ValueError('Invalid TITLE')
                    elif key == 'LUT_3D_SIZE':
                        if len(tokens) != 2:
                            raise ValueError('Invalid LUT size')
                        size = int(tokens[1])
                        if not 2 <= size <= 65:
                            raise ValueError('LUT size must be 2..65')
                    else:
                        if len(tokens) != 4:
                            raise ValueError('Domain must have three numbers')
                        value = _finite([float(t) for t in tokens[1:]])
                        if key == 'DOMAIN_MIN':
                            low = value
                        else:
                            high = value
                else:
                    if size is None or len(tokens) != 3:
                        raise ValueError('Expected 3D LUT header and RGB triples (1D LUTs unsupported)')
                    if len(rows) >= size ** 3:
                        raise ValueError('Too many LUT rows')
                    rows.append([float(t) for t in tokens])
            except (ValueError, OverflowError) as exc:
                raise ValueError(f'Invalid .cube line {number}: {exc}') from exc
    if size is None or len(rows) != size ** 3 or np.any(high <= low):
        raise ValueError('Invalid LUT row count or domain')
    table = _finite(rows).reshape(size, size, size, 3)
    for array in (table, low, high):
        array.setflags(write=False)
    return CubeLUT(table, low, high)


def apply_lut(rgb, lut: CubeLUT):
    """Float32 trilinear interpolation; clamp only the LUT sampling coordinates."""
    x = _finite(rgb)
    if x.ndim < 1 or x.shape[-1] != 3:
        raise ValueError('RGB requires a final dimension of 3')
    n = lut.table.shape[0]
    pos = np.clip((x - lut.domain_min) / (lut.domain_max - lut.domain_min), 0, 1) * np.float32(n - 1)
    lo = np.floor(pos).astype(np.intp)
    hi = np.minimum(lo + 1, n - 1)
    fraction = pos - lo.astype(np.float32)
    result = np.zeros_like(x)
    for r in (0, 1):
        for g in (0, 1):
            for b in (0, 1):
                ri = hi[..., 0] if r else lo[..., 0]
                gi = hi[..., 1] if g else lo[..., 1]
                bi = hi[..., 2] if b else lo[..., 2]
                weight = ((fraction[..., 0] if r else 1 - fraction[..., 0]) *
                          (fraction[..., 1] if g else 1 - fraction[..., 1]) *
                          (fraction[..., 2] if b else 1 - fraction[..., 2]))
                result += lut.table[bi, gi, ri] * weight[..., None]
    return result


def apply_grade(rgb: np.ndarray, grade: Grade, bypass: bool = False, *,
                normalized_transform=None, cancel=None) -> np.ndarray:
    """Normalize input, then evaluate creative SDR controls without clipping.

    Bypass/disabled affects creative controls only; source normalization remains.
    An optional owned picture transform runs in normalized encoded Rec.709
    float32 before the creative controls, even when the grade is bypassed.
    """
    if not isinstance(rgb, np.ndarray) or rgb.dtype != np.float32:
        raise ValueError('Grade input must be a float32 NumPy array')
    x = _finite(rgb).copy()
    if x.ndim < 1 or x.shape[-1] != 3:
        raise ValueError('RGB requires a final dimension of 3')
    _number(grade.lut_strength, 'lut_strength', 0, 1)
    _number(grade.contrast_pivot, 'contrast_pivot', 0, 1)
    x = normalize_input(x, grade.input_encoding)
    if normalized_transform is not None:
        transformed = normalized_transform(x)
        if (not isinstance(transformed, np.ndarray) or transformed.dtype != np.float32
                or transformed.shape != x.shape or not np.isfinite(transformed).all()):
            raise ValueError('Invalid normalized float picture transform output')
        x = transformed
    validate_white_balance(grade.white_balance_enabled, grade.white_balance_gains)
    if bypass or not grade.enabled:
        return x
    if grade.qualifier.enabled and grade.qualifier.window_keyframes:
        raise ValueError('Animated windows require evaluate_grade at an explicit clip-relative timeline frame.')
    lift = _finite(getattr(grade, 'lift', [0, 0, 0]))
    gamma = _finite(getattr(grade, 'gamma', [1, 1, 1]))
    gain = _finite(getattr(grade, 'gain', [1, 1, 1]))
    if any(v.shape != (3,) for v in (lift, gamma, gain)) or np.any(gamma <= 0):
        raise ValueError('Lift/gamma/gain require three channels and positive gamma')
    exposure = getattr(grade, 'linear_exposure', 0.)
    # Skip neutral transfer roundtrip: preserves exact identity, including the
    # small discontinuity in the published rounded Rec.709 coefficients.
    wb = np.asarray(grade.white_balance_gains, dtype=np.float32)
    wb_active = grade.white_balance_enabled and np.any(wb != 1)
    if wb_active or exposure or np.any(lift != 0) or np.any(gamma != 1) or np.any(gain != 1):
        x = rec709_to_linear(x)
        if wb_active:
            x *= wb
        x *= np.float32(2 ** exposure)
        x = (x + lift) * gain
        x = np.sign(x) * np.abs(x) ** (np.float32(1) / gamma)
        x = linear_to_rec709(x)
    if grade.exposure:
        x *= np.float32(2 ** grade.exposure)
    if grade.contrast != 1:
        # Encoded-domain stretch after legacy gain. Preserve operation order
        # and float32 constants: default pivot is bit-exact with v25. No
        # intermediate clamp; out-of-range samples reach later curves/LUT.
        pivot = np.float32(grade.contrast_pivot)
        x = (x - pivot) * np.float32(grade.contrast) + pivot
    if grade.saturation != 1:
        y = np.sum(x * LUMA, axis=-1, keepdims=True)
        x = y + np.float32(grade.saturation) * (x - y)
    if grade.temperature or grade.tint:
        t, tint = np.float32(grade.temperature * .25), np.float32(grade.tint * .25)
        x += np.array([t + tint / 2, -tint, -t + tint / 2], np.float32)
    if grade.shadows or grade.highlights:
        # Clamp mask weights, not image samples; no hidden range clipping.
        y = np.clip(np.sum(x * LUMA, axis=-1, keepdims=True), 0, 1)
        x += np.float32(.5) * (np.float32(grade.shadows) * (1 - y) ** 2 + np.float32(grade.highlights) * y ** 2)
    x = apply_curve(x, getattr(grade, 'master_curve', IDENTITY_CURVE))
    for channel, name in enumerate(('red_curve', 'green_curve', 'blue_curve')):
        x[..., channel] = apply_curve(x[..., channel], getattr(grade, name, IDENTITY_CURVE))
    x = apply_selective_color(x, grade.hue_hue_curve, grade.hue_sat_curve)
    # Keep the existing neutral grade path allocation/scan cost unchanged.
    validate_qualifier(grade.qualifier)
    if grade.qualifier.enabled and (grade.qualifier.exposure != 0 or
            grade.qualifier.saturation != 1 or grade.qualifier.hue_shift != 0):
        x = (apply_qualifier(x, grade.qualifier, cancel=cancel)
             if cancel is not None else apply_qualifier(x, grade.qualifier))
    if grade.lut_path and grade.lut_strength > 0:
        mapped = apply_lut(x, load_cube(grade.lut_path))
        # Blend at the same post-selective-curve, encoded Rec.709 stage as the full LUT.
        # Zero strength never samples/clamps; full strength preserves prior math.
        strength = np.float32(grade.lut_strength)
        x = mapped if grade.lut_strength == 1 else x * (np.float32(1) - strength) + mapped * strength
    if not np.all(np.isfinite(x)):
        raise ValueError('Grade produced non-finite values')
    return x.astype(np.float32, copy=False)

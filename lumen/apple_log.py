# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the ACES Project.
# Modified for CutCat: NumPy transfer functions, direct BT.2020/709 conversion,
# defensive finite handling, and an original fixed SDR rendering policy.
"""Original Apple Log (BT.2020), not Apple Log 2 or an Apple display LUT.

See docs/APPLE_LOG_MATH.md and lumen/assets/licenses/aces-input-and-colorspaces/.
The public transform consumes normalized Log RGB and returns encoded Rec.709.
"""
from __future__ import annotations

import numpy as np

R0 = -0.05641088
RT = 0.01
C = 47.28711236
B = 0.00964052
G = 0.08550479
D = 0.69336945
PT = C * (RT - R0) ** 2
# Defensive ceiling well outside camera [0, 1]; leaves matrix arithmetic finite.
_MAX_LOG_CODE = D + 512.0 * G


def _finite_array(value):
    raw = np.asarray(value)
    if np.iscomplexobj(raw):
        raise ValueError('Apple Log values must be real and finite')
    try:
        result = np.asarray(value, dtype=np.float64)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('Apple Log values must be real and finite') from exc
    if not np.all(np.isfinite(result)):
        raise ValueError('Apple Log values must be real and finite')
    return result


def apple_log_to_linear(value):
    """Decode original Apple Log to float64 linear BT.2020, including negatives.

    Codes below zero follow the official floor R0. Absurd finite codes above
    D + 512*G saturate at 2**512 - B to avoid overflow; [0, 1] is unmodified.
    """
    code = _finite_array(value)
    result = np.full_like(code, R0)
    toe = (code >= 0.0) & (code < PT)
    result[toe] = np.sqrt(code[toe] / C) + R0
    log = code >= PT
    result[log] = np.exp2((np.minimum(code[log], _MAX_LOG_CODE) - D) / G) - B
    return result


def linear_to_apple_log(value):
    """Official forward curve, useful for tests; values below R0 map to zero."""
    linear = _finite_array(value)
    result = np.zeros_like(linear)
    toe = (linear >= R0) & (linear < RT)
    result[toe] = C * np.square(linear[toe] - R0)
    log = linear >= RT
    result[log] = G * np.log2(linear[log] + B) + D
    return result


def _rgb_to_xyz(primaries):
    xy = np.asarray(primaries, dtype=np.float64)
    xyz = np.stack((xy[:, 0] / xy[:, 1], np.ones(3),
                    (1.0 - xy[:, 0] - xy[:, 1]) / xy[:, 1]))
    white = np.array([0.3127 / 0.3290, 1.0, (1.0 - 0.3127 - 0.3290) / 0.3290])
    return xyz * np.linalg.solve(xyz, white)


_BT2020_TO_XYZ = _rgb_to_xyz(((0.708, 0.292), (0.170, 0.797), (0.131, 0.046)))
_BT709_TO_XYZ = _rgb_to_xyz(((0.640, 0.330), (0.300, 0.600), (0.150, 0.060)))
BT2020_TO_BT709 = np.linalg.solve(_BT709_TO_XYZ, _BT2020_TO_XYZ)
BT709_LUMA = _BT709_TO_XYZ[1].copy()
BT2020_TO_BT709.setflags(write=False)
BT709_LUMA.setflags(write=False)


def _render_sdr_linear(rgb):
    """Fixed exposure 1, luminance shoulder, neutral-axis gamut compression."""
    luminance = rgb @ BT709_LUMA
    positive = luminance > 0.0
    mapped = np.array(np.maximum(luminance, 0.0))
    shoulder = luminance > 0.18
    mapped[shoulder] = 0.18 - 0.82 * np.expm1(-(luminance[shoulder] - 0.18) / 0.82)
    scale = np.zeros_like(mapped)
    np.divide(mapped, luminance, out=scale, where=positive)
    scaled = rgb * scale[..., None]
    delta = scaled - mapped[..., None]
    # Largest t in [0,1] with neutral + t*(RGB-neutral) inside the RGB cube.
    # This keeps mapped luminance, unlike independent channel clipping.
    limits = np.ones_like(delta)
    np.divide(1.0 - mapped[..., None], delta, out=limits, where=delta > 0.0)
    np.divide(-mapped[..., None], delta, out=limits, where=delta < 0.0)
    t = np.minimum(1.0, np.min(limits, axis=-1))
    result = mapped[..., None] + t[..., None] * delta
    # Numerical fence only (roundoff at cube faces), not the gamut transform.
    return np.clip(result, 0.0, 1.0)


def _rec709_oetf(linear):
    result = np.empty_like(linear)
    toe = linear < 0.018
    result[toe] = 4.5 * linear[toe]
    result[~toe] = 1.099 * np.power(linear[~toe], 0.45) - 0.099
    return result


def apple_log_to_rec709(rgb):
    """Convert (..., 3) finite normalized original Apple Log RGB to float32.

    Returns Rec.709 OETF-encoded RGB in [0, 1], with the same shape. This
    applies a fixed CutCat SDR look, not a scene-referred color-space inverse.
    Nonfinite inputs and wrong channel counts raise ValueError. Never mutates
    the input; callers retain responsibility for legal/full-range decoding.
    """
    values = _finite_array(rgb)
    if values.ndim == 0 or values.shape[-1] != 3:
        raise ValueError('Apple Log RGB must have shape (..., 3)')
    linear_709 = apple_log_to_linear(values) @ BT2020_TO_BT709.T
    return _rec709_oetf(_render_sdr_linear(linear_709)).astype(np.float32)

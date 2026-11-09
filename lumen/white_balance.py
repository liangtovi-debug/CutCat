"""Bounded neutral-reference white balance for SDR Rec.709-primary pictures.

This is a diagonal linear-RGB correction, not a Kelvin/illuminant estimator or
Bradford adaptation. Gains neutralize the user-designated reference while
preserving that reference's linear Y, not every pixel's luminance. See
 docs/WHITE_BALANCE.md for the sampling boundary and limitations.
"""
from __future__ import annotations

import math
import numpy as np

MIN_GAIN = .25
MAX_GAIN = 4.0
# Conservative encoded-channel limits: reject noisy deep shadows and samples
# close to clipping, where the original channel ratios cannot be trusted.
MIN_REFERENCE = .02
MAX_REFERENCE = .98


def validate_white_balance(enabled, gains):
    """Reject invalid persisted/direct settings, even when temporarily disabled."""
    if type(enabled) is not bool:
        raise ValueError('White balance enabled must be a boolean')
    if not isinstance(gains, (list, tuple)) or len(gains) != 3:
        raise ValueError('White balance gains require three finite numbers in 0.25..4')
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not MIN_GAIN <= v <= MAX_GAIN or not math.isfinite(v) for v in gains):
        raise ValueError('White balance gains require three finite numbers in 0.25..4')


def sample_white_balance(rgb, *, valid=True):
    """Return linear RGB gains, or None for an unsafe reference.

    The sample must come from the normalized/restored PRE-WB source frame.
    The caller, not this function, decides which pictured object is neutral.
    No arbitrary color/skin recognition or global automatic balancing occurs.
    """
    if not valid:
        return None
    try:
        raw = np.asarray(rgb)
        if raw.dtype.kind not in 'fiu' or raw.shape != (3,):
            return None
        sample = raw.astype(np.float64)
    except (TypeError, ValueError, OverflowError):
        return None
    if not np.isfinite(sample).all() or np.any(sample <= MIN_REFERENCE) or np.any(sample >= MAX_REFERENCE):
        return None
    # Use float64 for the three-value estimate only. Picture evaluation remains
    # float32. Rounded ITU-R BT.709-6 constants match the existing SDR pipeline.
    linear = np.where(sample < .081, sample / 4.5, ((sample + .099) / 1.099) ** (1 / .45))
    luminance = float(linear @ np.array([.2126, .7152, .0722]))
    gains = luminance / linear
    if not np.isfinite(gains).all() or np.any(gains < MIN_GAIN) or np.any(gains > MAX_GAIN):
        return None
    # Exact and numerically near-neutral references should not introduce a
    # transfer roundtrip (BT.709's published rounded join is discontinuous).
    if np.max(np.abs(gains - 1)) <= 1e-6:
        return (1.0, 1.0, 1.0)
    return tuple(float(value) for value in gains)

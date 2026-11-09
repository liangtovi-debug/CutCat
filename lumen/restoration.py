"""Conservative, deterministic per-frame spatial restoration in float32.

The input is normalized Rec.709-encoded RGB, before creative grading. This is
not linear-light filtering, AI reconstruction, super-resolution, deblur, face
retouching or a temporal denoiser. It never quantizes or clips image samples to
0..1. A fixed-strength result depends only on this frame's active picture.

Deblock finds repeated weak, flat-sided discontinuities without assuming a
codec grid size or phase. An isolated edge and high-contrast/detail edges are
protected. Repeated low-contrast graphic edges remain intrinsically ambiguous
and may soften; the feature is deliberately disabled by default. It cannot
recover compression-lost detail. Denoise can soften weak fine detail; sharpen
cannot restore lost focus. There is no guarantee against video flicker.

Scratch arrays are restricted to 64-row strips plus a one-row halo and line
counts. At most two internal full-frame working buffers coexist between
passes. The caller's array is never written. No frame history or cache is kept.
"""
from __future__ import annotations

import numpy as np

from .restoration_settings import RestorationSettings, validate_restoration_settings


RESTORATION_ALGORITHM = "spatial-float32-v1"
RESTORATION_TILE_ROWS = 64
MAX_RESTORATION_DIMENSION = 16384
# Bounds are encoded RGB sample differences, not clipping of sample values.
MAX_DEBLOCK_CORRECTION = np.float32(.008)
MAX_SHARPEN_CORRECTION = np.float32(.015)


class RestorationCancelled(RuntimeError):
    """Raised when a supplied event/callback requests cancellation."""


def _check_cancel(cancel) -> None:
    if cancel is None:
        return
    if callable(getattr(cancel, "is_set", None)):
        stopped = cancel.is_set()
    elif callable(cancel):
        stopped = cancel()  # The caller's raised exception is preserved.
    else:
        raise ValueError("Restoration cancellation must be an event or callable")
    if stopped:
        raise RestorationCancelled("Restoration cancelled.")


def _rows(height):
    for start in range(0, height, RESTORATION_TILE_ROWS):
        yield start, min(start + RESTORATION_TILE_ROWS, height)


def _neighborhood(image, start, stop):
    """One small edge-replicated halo, with no full-image padding allocation."""
    lo, hi = max(0, start - 1), min(image.shape[0], stop + 1)
    return np.pad(image[lo:hi], ((int(start == 0), int(stop == image.shape[0])),
                               (1, 1), (0, 0)), mode="edge")


def _difference(a, b):
    # Huge finite, opposite-sign float32 values can overflow a difference.
    # Consumers gate or saturate differences before arithmetic; samples stay
    # untouched, and normal working-range evaluation is entirely float32.
    with np.errstate(over="ignore", invalid="ignore"):
        return np.subtract(a, b, dtype=np.float32)


def _denoise(image, amount, cancel):
    output = np.empty_like(image)
    sigma = np.float32(.03 + .06 * amount)
    blend = np.float32(.9 * amount)
    for start, stop in _rows(image.shape[0]):
        _check_cancel(cancel)
        patch = _neighborhood(image, start, stop)
        center = patch[1:-1, 1:-1]
        shift = np.zeros_like(center)
        total = np.ones(center.shape[:2], dtype=np.float32)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == dy == 0:
                    continue
                sample = patch[1 + dy:1 + dy + stop - start,
                               1 + dx:1 + dx + image.shape[1]]
                delta = _difference(sample, center)
                distance = np.max(np.abs(delta), axis=2)
                # Compact RGB range kernel: strong edges contribute exactly
                # zero. RGB distance also protects isoluminant color edges.
                ratio = np.minimum(distance, sigma) / sigma
                weight = (np.float32(1) - ratio * ratio) ** 2
                weight *= np.float32(.75 if dx == 0 or dy == 0 else .375)
                np.clip(delta, -sigma, sigma, out=delta)
                shift += delta * weight[..., None]
                total += weight
        output[start:stop] = center + shift * (blend / total)[..., None]
    return output


def _step_candidates(image, start, stop):
    """Weak discontinuity residuals between columns 2..width-4 and 3..width-3.

    Two slopes on each side must be small. The discontinuity is measured
    relative to their mean slope, so a smooth ramp is not a candidate.
    Adjacent-row support excludes isolated noisy candidates.
    """
    lo, hi = max(0, start - 1), min(image.shape[0], stop + 1)
    patch = image[lo:hi]
    p2, p1, p0 = patch[:, :-5], patch[:, 1:-4], patch[:, 2:-3]
    q0, q1, q2 = patch[:, 3:-2], patch[:, 4:-1], patch[:, 5:]
    lp, lq = _difference(p0, p1), _difference(q1, q0)
    jump = _difference(q0, p0)
    with np.errstate(over="ignore", invalid="ignore"):
        residual = jump - np.float32(.5) * lp - np.float32(.5) * lq
    magnitude = np.max(np.abs(residual), axis=2)
    variation = np.maximum(np.max(np.abs(lp), axis=2), np.max(np.abs(lq), axis=2))
    variation = np.maximum(variation, np.max(np.abs(_difference(p1, p2)), axis=2))
    variation = np.maximum(variation, np.max(np.abs(_difference(q2, q1)), axis=2))
    mask = ((magnitude >= np.float32(.004)) & (magnitude <= np.float32(.06))
            & (variation <= np.minimum(np.float32(.012), magnitude * np.float32(.3))))
    supported = np.zeros_like(mask)
    supported[1:] |= mask[:-1]
    supported[:-1] |= mask[1:]
    mask &= supported
    a, b = start - lo, stop - lo
    return residual[a:b], mask[a:b]


def _deblock_axis(image, amount, cancel):
    """Filter column boundaries; transposition reuses this for row boundaries."""
    height, width = image.shape[:2]
    if height < 3 or width < 14:
        return image
    support = np.zeros(width - 5, dtype=np.int64)
    for start, stop in _rows(height):
        _check_cancel(cancel)
        _, candidates = _step_candidates(image, start, stop)
        support += np.count_nonzero(candidates, axis=0)
    # Require three separately supported lines. This refuses an isolated true
    # step, even if that step happens to sit exactly on an old codec grid.
    # No codec, source origin, regular spacing or integer scaling is assumed.
    eligible = np.flatnonzero(support >= max(3, int(np.ceil(height * .25))))
    selected = np.zeros(width - 5, dtype=bool)
    previous = -4
    count = 0
    for index in eligible:
        if index - previous >= 4:
            selected[index] = True
            previous = index
            count += 1
    if count < 3:
        return image
    output = image.copy()
    for start, stop in _rows(height):
        _check_cancel(cancel)
        residual, candidates = _step_candidates(image, start, stop)
        candidates &= selected[None, :]
        # Distinct four-pixel footprints do not overlap on this axis. The
        # image data is read only from the immutable source of this pass.
        delta = np.where(candidates[..., None], residual, np.float32(0))
        delta *= np.float32(.22 * amount)
        np.clip(delta, -MAX_DEBLOCK_CORRECTION * amount,
                MAX_DEBLOCK_CORRECTION * amount, out=delta)
        output[start:stop, 2:-3] += delta
        output[start:stop, 3:-2] -= delta
        output[start:stop, 1:-4] += delta * np.float32(.35)
        output[start:stop, 4:-1] -= delta * np.float32(.35)
    return output


def _sharpen(image, amount, cancel):
    output = np.empty_like(image)
    gain = np.float32(.65 * amount)
    bound = np.float32(MAX_SHARPEN_CORRECTION * amount)
    for start, stop in _rows(image.shape[0]):
        _check_cancel(cancel)
        patch = _neighborhood(image, start, stop)
        center = patch[1:-1, 1:-1]
        residual = np.zeros_like(center)
        lower, upper = center.copy(), center.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == dy == 0:
                    continue
                sample = patch[1 + dy:1 + dy + stop - start,
                               1 + dx:1 + dx + image.shape[1]]
                delta = _difference(center, sample)
                # Saturate only the residual calculation to make enormous
                # finite source values safe, never the RGB sample domain.
                np.clip(delta, -1, 1, out=delta)
                residual += delta * np.float32(2 if dx == 0 or dy == 0 else 1)
                np.minimum(lower, sample, out=lower)
                np.maximum(upper, sample, out=upper)
        residual *= np.float32(1 / 16)
        # A small noise floor avoids indiscriminately accentuating numerical
        # noise; the local envelope prevents new overshoot/undershoot halos.
        delta = np.sign(residual) * np.maximum(np.abs(residual) - np.float32(.001), 0) * gain
        np.clip(delta, -bound, bound, out=delta)
        result = center + delta
        np.maximum(result, lower, out=result)
        np.minimum(result, upper, out=result)
        output[start:stop] = result
    return output


def apply_restoration(rgb: np.ndarray, settings: RestorationSettings, cancel=None) -> np.ndarray:
    """Apply deblock → denoise → restrained sharpen to one active RGB picture.

    ``rgb`` must be a nonempty finite float32 H×W×3 array; strided and readonly
    arrays are supported. Dimensions are bounded by the editor's 16384 limit.
    Disabled/all-zero settings return the validated original array unchanged.
    Active passes never mutate it and preserve signed/out-of-range samples.

    ``cancel`` may be an event with ``is_set()`` or a callable. A truthy return
    raises RestorationCancelled; exceptions raised by a callback pass through.
    Cancellation is checked during validation and each bounded processing tile.
    """
    validate_restoration_settings(settings)
    if (not isinstance(rgb, np.ndarray) or rgb.dtype != np.float32 or rgb.ndim != 3
            or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 1):
        raise ValueError("Restoration requires a nonempty float32 HWC RGB image")
    if max(rgb.shape[:2]) > MAX_RESTORATION_DIMENSION:
        raise ValueError("Restoration image dimensions must not exceed 16384")
    for start, stop in _rows(rgb.shape[0]):
        _check_cancel(cancel)
        if not np.all(np.isfinite(rgb[start:stop])):
            raise ValueError("Restoration RGB samples must be finite")
    if not settings.enabled or not (settings.deblock or settings.denoise or settings.sharpen):
        return rgb
    result = rgb
    if settings.deblock:
        result = _deblock_axis(result, settings.deblock, cancel)
        result = _deblock_axis(result.transpose(1, 0, 2), settings.deblock, cancel).transpose(1, 0, 2)
    if settings.denoise:
        result = _denoise(result, settings.denoise, cancel)
    if settings.sharpen:
        result = _sharpen(result, settings.sharpen, cancel)
    for start, stop in _rows(result.shape[0]):
        _check_cancel(cancel)
        if not np.all(np.isfinite(result[start:stop])):
            raise ValueError("Restoration produced non-finite values")
    return result

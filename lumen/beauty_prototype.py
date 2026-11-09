"""Isolated, local-only experiment in externally masked skin smoothing.

This module is deliberately not wired into the editor, project schema, preview,
or renderer. It provides no face/skin detector and makes no claim that a supplied
mask correctly identifies skin, eyes, lips, hair, occlusions, or any person.
The caller must supply a conservative soft skin mask and a protection mask.
Every positive protection value is a *hard veto*, including soft mask overlap.
Masked-out/protected pixels are neither modified nor used as filter samples.

The input is finite float32, normalized Rec.709-encoded RGB. A fixed 5x5 spatial
range filter supplies a bounded correction; a small residual floor retains some
fine texture. No exposure, tone, color-space conversion, clipping, quantization,
warping, inpainting, learned model, network, or frame history is involved.
This is not a semantic blemish remover: sufficiently strong or broad blemishes
remain, weak unprotected texture may soften, and inaccurate masks can still
affect the wrong pixels. Fixed pixel radius is resolution-dependent. Temporal
mask jitter and flicker are not assessed or corrected by this single-frame tool.

There is one full-frame output allocation only when processing is needed. All
other image-sized scratch is bounded to 32-row strips with a two-pixel halo;
there is no full-image padding, flattened neighborhood tensor, or frame cache.
"""
from __future__ import annotations

import math
from numbers import Real

import numpy as np


BEAUTY_PROTOTYPE_ALGORITHM = "external-mask-spatial-float32-experiment-v1"
BEAUTY_PROTOTYPE_TILE_ROWS = 32
MAX_BEAUTY_PROTOTYPE_DIMENSION = 16384
MAX_BEAUTY_PROTOTYPE_CORRECTION = np.float32(.025)
_RADIUS = 2
_SPATIAL_WEIGHTS = (1, 4, 6, 4, 1)


class BeautyPrototypeCancelled(RuntimeError):
    """Raised when the experiment's cancellation callback/event requests it."""


def _check_cancel(cancel) -> None:
    if cancel is None:
        return
    if callable(getattr(cancel, "is_set", None)):
        stopped = cancel.is_set()
    elif callable(cancel):
        stopped = cancel()
    else:
        raise ValueError("Beauty cancellation must be an event or callable")
    if stopped:
        raise BeautyPrototypeCancelled("Beauty experiment cancelled.")


def _unit_parameter(value, name: str) -> float:
    if (isinstance(value, (bool, np.bool_)) or not isinstance(value, Real)
            or not 0 <= value <= 1):
        raise ValueError(f"{name} must be a finite number in 0..1")
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite number in 0..1") from error
    if not math.isfinite(normalized) or not 0 <= normalized <= 1:
        raise ValueError(f"{name} must be a finite number in 0..1")
    return normalized


def _rows(height: int):
    for start in range(0, height, BEAUTY_PROTOTYPE_TILE_ROWS):
        yield start, min(start + BEAUTY_PROTOTYPE_TILE_ROWS, height)


def _patch(array: np.ndarray, start: int, stop: int) -> np.ndarray:
    """Replicate image edges using only a strip-sized allocation."""
    height = array.shape[0]
    lo, hi = max(0, start - _RADIUS), min(height, stop + _RADIUS)
    padding = [(_RADIUS - (start - lo), _RADIUS - (hi - stop)),
               (_RADIUS, _RADIUS)]
    if array.ndim == 3:
        padding.append((0, 0))
    return np.pad(array[lo:hi], padding, mode="edge")


def _validate(rgb, skin_mask, protection_mask, cancel) -> bool:
    if (not isinstance(rgb, np.ndarray) or rgb.dtype != np.float32
            or rgb.ndim != 3 or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 1):
        raise ValueError("Beauty requires a nonempty float32 HWC RGB image")
    if max(rgb.shape[:2]) > MAX_BEAUTY_PROTOTYPE_DIMENSION:
        raise ValueError("Beauty image dimensions must not exceed 16384")
    for name, mask in (("skin_mask", skin_mask), ("protection_mask", protection_mask)):
        if (not isinstance(mask, np.ndarray) or mask.shape != rgb.shape[:2]
                or mask.dtype not in (np.dtype(np.float32), np.dtype(bool))):
            raise ValueError(f"{name} must be a matching float32 or bool HW mask")
    any_active = False
    for start, stop in _rows(rgb.shape[0]):
        _check_cancel(cancel)
        if not np.all(np.isfinite(rgb[start:stop])):
            raise ValueError("Beauty RGB samples must be finite")
        for name, mask in (("skin_mask", skin_mask), ("protection_mask", protection_mask)):
            tile = mask[start:stop]
            if not np.all(np.isfinite(tile)) or np.any(tile < 0) or np.any(tile > 1):
                raise ValueError(f"{name} must contain finite values in 0..1")
        any_active |= bool(np.any((skin_mask[start:stop] > 0)
                                  & (protection_mask[start:stop] == 0)))
    return any_active


@np.errstate(under="ignore")
def apply_beauty_prototype(
    rgb: np.ndarray,
    skin_mask: np.ndarray,
    protection_mask: np.ndarray,
    *,
    enabled: bool = False,
    smoothing_strength: float = 0.0,
    detail_protection: float = .75,
    confidence: float = 1.0,
    cancel=None,
) -> np.ndarray:
    """Evaluate one frame using caller-provided masks; disabled by default.

    Both masks have shape HxW, dtype float32 (finite 0..1) or bool. Skin values
    blend the correction and weight neighboring eligible samples. *Any*
    positive protection value excludes a pixel completely, even tiny values.
    No automatic dilation is performed; callers must provide their own margin.
    Mask holes also have exact zero delta and are excluded as contributors.

    ``smoothing_strength``, ``detail_protection``, and ``confidence`` are 0..1.
    Strength and caller-estimated confidence scale the same precomputed local
    correction continuously. Confidence is not calculated or calibrated here.
    Detail protection narrows the range gate and retains up to .004 encoded-RGB
    residual, trading weaker blemish suppression for more retained texture.
    Pre-addition per-channel correction is bounded by
    .025 * strength * confidence * skin, subject to float32 arithmetic rounding.
    Final sample differences also include addition rounding, so that is not a
    strict final-delta bound at arbitrary out-of-range magnitudes. For example,
    around 10000 the final difference can be .025390625 for a .025 correction.

    All inputs are validated, including when disabled. Disabled, zero strength,
    zero confidence, and no eligible skin return the exact input object.
    Otherwise a float32 copy is returned; inputs are never mutated. Protected,
    excluded, and numerically unchanged samples retain their original bits.
    Signed/out-of-range samples are supported without clipping to 0..1.
    Strided/read-only inputs and masks are supported. Non-finite inputs fail
    rather than being silently replaced. Harmless subnormal underflow is locally
    ignored; caller NumPy error settings are restored, never globally changed.
    ``cancel`` accepts a callable or an
    event with ``is_set()``, checked during validation and each processing tile.
    """
    if not isinstance(enabled, (bool, np.bool_)):
        raise ValueError("enabled must be a boolean")
    strength = _unit_parameter(smoothing_strength, "smoothing_strength")
    detail = _unit_parameter(detail_protection, "detail_protection")
    confidence = _unit_parameter(confidence, "confidence")
    any_active = _validate(rgb, skin_mask, protection_mask, cancel)
    if not enabled or strength == 0 or confidence == 0 or not any_active:
        return rgb

    output = rgb.copy()
    sigma = np.float32(.08 - .035 * detail)
    detail_floor = np.float32(.004 * detail)
    blend = np.float32(strength * confidence)
    height, width = rgb.shape[:2]
    for start, stop in _rows(height):
        _check_cancel(cancel)
        if not np.any((skin_mask[start:stop] > 0) & (protection_mask[start:stop] == 0)):
            continue
        source = _patch(rgb, start, stop)
        eligible = _patch(skin_mask, start, stop).astype(np.float32, copy=False)
        eligible *= _patch(protection_mask, start, stop) == 0
        center = source[_RADIUS:-_RADIUS, _RADIUS:-_RADIUS]
        center_skin = eligible[_RADIUS:-_RADIUS, _RADIUS:-_RADIUS]
        shift = np.zeros_like(center)
        total = np.ones(center.shape[:2], dtype=np.float32)

        for dy in range(-_RADIUS, _RADIUS + 1):
            for dx in range(-_RADIUS, _RADIUS + 1):
                if dx == dy == 0:
                    continue
                a, b = _RADIUS + dy, _RADIUS + dx
                sample = source[a:a + stop - start, b:b + width]
                # Extreme opposite-sign finite samples can overflow subtraction;
                # the compact range gate rejects them before bounded arithmetic.
                with np.errstate(over="ignore", invalid="ignore"):
                    delta = np.subtract(sample, center, dtype=np.float32)
                distance = np.max(np.abs(delta), axis=2)
                ratio = np.minimum(distance, sigma) / sigma
                weight = (np.float32(1) - ratio * ratio) ** 2
                weight *= np.float32(_SPATIAL_WEIGHTS[dy + _RADIUS]
                                    * _SPATIAL_WEIGHTS[dx + _RADIUS] / 36)
                weight *= eligible[a:a + stop - start, b:b + width]
                np.clip(delta, -sigma, sigma, out=delta)
                shift += delta * weight[..., None]
                total += weight

        shift /= total[..., None]
        magnitude = np.max(np.abs(shift), axis=2)
        safe_magnitude = np.maximum(magnitude, np.finfo(np.float32).tiny)
        retained = np.maximum(magnitude - detail_floor, np.float32(0))
        retained = np.minimum(retained, MAX_BEAUTY_PROTOTYPE_CORRECTION)
        gain = (retained / safe_magnitude) * blend * center_skin
        shift *= gain[..., None]
        # Assignment only where a correction is nonzero preserves exact bits,
        # including negative zero at protection/skin holes and constant regions.
        target = output[start:stop]
        np.add(center, shift, out=target, where=shift != 0)
        if not np.all(np.isfinite(target)):
            raise ValueError("Beauty experiment produced non-finite values")
    return output

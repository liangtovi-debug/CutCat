"""Pure replace/add/subtract range picking from pre-qualifier float32 RGB."""
from __future__ import annotations

from dataclasses import dataclass, fields, replace

import numpy as np

from .qualifier import (_CHROMA_EPSILON, _LUMA, QualifierSettings, QualifierRange, MAX_QUALIFIER_RANGES,
                        validate_qualifier, range_softness)


INITIAL_HUE_HALF_WIDTH = 10.0 / 360.0
INITIAL_CHANNEL_HALF_WIDTH = 0.1


@dataclass(frozen=True)
class QualifierSample:
    """Exact source RGB and renderer-matched H/HSV S/encoded Rec.709 Y′.

    Hue is measured in normalized turns, not degrees; it is None for gray
    saturation/luma softness samples. RGB is an immutable
    tuple of the original float32 values, with no display quantization.
    """
    rgb: tuple[float, float, float]
    hue: float | None
    saturation: float
    luma: float


def _checked_sample(rgb: np.ndarray, *, allow_gray: bool = False) -> QualifierSample:
    if (not isinstance(rgb, np.ndarray) or rgb.dtype != np.float32
            or rgb.shape != (3,)):
        raise ValueError('Qualifier sample requires a float32 RGB triplet')
    if not np.all(np.isfinite(rgb)):
        raise ValueError('Qualifier sample color values must be finite')
    if np.any(rgb < 0) or np.any(rgb > 1):
        raise ValueError('Qualifier sample RGB must be within 0..1')
    values = rgb.reshape(1, 3)
    low, high = np.min(values, axis=-1), np.max(values, axis=-1)
    chroma = high - low
    if chroma[0] <= _CHROMA_EPSILON and not allow_gray:
        raise ValueError('Qualifier sample has ambiguous hue (gray or near-gray)')
    # Match the mask's float32 arithmetic, including hue sector ties, canonical
    # red wrap and luma summation. UI-rounded or float64 recomputations must
    # never determine membership of the original float32 source sample.
    from .selective_color import _hue
    hue = _hue(values, high, chroma)
    saturation = np.zeros(1, dtype=np.float32)
    np.divide(chroma, high, out=saturation, where=high > 0)
    luma = np.sum(values * _LUMA, axis=-1)
    return QualifierSample(tuple(float(value) for value in rgb),
                           None if chroma[0] <= _CHROMA_EPSILON else float(hue[0]),
                           float(saturation[0]), float(luma[0]))


def sample_qualifier(rgb: np.ndarray, valid: bool = True) -> QualifierSample | None:
    """Read one original sample, returning None if it has no pickable hue.

    Accept only a finite float32 RGB triplet inside [0,1]. Gray/near-gray
    chroma <= 1e-6 has no unambiguous hue and never becomes a false red key.
    ``valid=False`` rejects transparent/outside-picture samples. Extended
    range RGB is rejected, never clipped into a different sample color.
    """
    if not isinstance(valid, (bool, np.bool_)) or not valid:
        return None
    try:
        return _checked_sample(rgb)
    except ValueError:
        return None


def _replace_range(settings: QualifierSettings, sample: QualifierSample) -> QualifierSettings:
    width = INITIAL_CHANNEL_HALF_WIDTH
    changes = dict(inclusion_ranges=[], exclusion_ranges=[])
    if settings.hue_enabled:
        changes.update(hue_center=sample.hue, hue_width=INITIAL_HUE_HALF_WIDTH)
    if settings.saturation_enabled:
        changes.update(saturation_low=max(0.0, sample.saturation - width),
                       saturation_high=min(1.0, sample.saturation + width))
    if settings.luma_enabled:
        changes.update(luma_low=max(0.0, sample.luma - width),
                       luma_high=min(1.0, sample.luma + width))
    return replace(settings, **changes)


def initialize_from_sample(
    rgb: np.ndarray, settings: QualifierSettings,
) -> tuple[QualifierSettings, QualifierSample]:
    """Replace one joint range from exact RGB, or raise for an invalid pick.

    Core half-widths start at ±10° hue and ±0.1 saturation/luma, with S/Y bounds
    clipped to [0,1]. Hue wraps circularly in the renderer. All feathers,
    adjustments and all enabled flags remain exactly as supplied. Disabled
    channel cores are not sampled; gray is valid when hue is disabled. Extra inclusion
    and exclusion ranges are cleared. Never mutate
    either input. Validate settings before reading even an invalid sample.
    """
    validate_qualifier(settings)
    sample = _checked_sample(rgb, allow_gray=not settings.hue_enabled)
    return _replace_range(settings, sample), sample


def replace_qualifier_range(
    settings: QualifierSettings, rgb: np.ndarray, valid: bool = True,
) -> tuple[QualifierSettings, QualifierSample] | None:
    """Return independent replacement settings plus sample, or None for no pick.

    Core half-widths start at ±10° hue and ±0.1 saturation/luma, with S/Y bounds
    clipped to [0,1]. Hue wraps circularly in the renderer. All feathers,
    adjustments and all enabled flags remain exactly as supplied. Disabled
    channel cores are not sampled; gray is valid when hue is disabled. Extra inclusion
    and exclusion ranges are cleared. Validate
    settings even on a rejected pick; never mutate either input.
    """
    validate_qualifier(settings)
    if not isinstance(valid, (bool, np.bool_)) or not valid:
        return None
    try:
        sample = _checked_sample(rgb, allow_gray=not settings.hue_enabled)
    except ValueError:
        return None
    return _replace_range(settings, sample), sample


def add_qualifier_range(settings: QualifierSettings, rgb: np.ndarray,
                        valid: bool = True, *, exclude: bool = False
                        ) -> tuple[QualifierSettings, QualifierSample] | None:
    """Append an independent sampled range, preserving the primary range.

    Each set holds at most eight records. New records inherit primary feathers
    and channel switches; disabled primary channel cores remain untouched;
    adjustments, window and enablement do not change. Invalid samples return
    None without consuming capacity; a valid pick at capacity raises ValueError.
    """
    validate_qualifier(settings)
    if type(exclude) is not bool:
        raise ValueError('Qualifier exclusion mode must be a boolean')
    if not isinstance(valid, (bool, np.bool_)) or not valid:
        return None
    try:
        sample = _checked_sample(rgb, allow_gray=not settings.hue_enabled)
    except ValueError:
        return None
    name = 'exclusion_ranges' if exclude else 'inclusion_ranges'
    entries = getattr(settings, name)
    if len(entries) >= MAX_QUALIFIER_RANGES:
        raise ValueError('Qualifier range set is full (maximum 8)')
    picked = _replace_range(settings, sample)
    entry = QualifierRange(**{f.name: getattr(picked, f.name) for f in fields(QualifierRange)})
    return replace(settings, **{name: [*entries, entry]}), sample


def clear_qualifier_ranges(settings: QualifierSettings) -> QualifierSettings:
    """Clear all extra inclusions and exclusions while preserving the base."""
    validate_qualifier(settings)
    return replace(settings, inclusion_ranges=[], exclusion_ranges=[])


def soften_qualifier_range(
    settings: QualifierSettings, rgb: np.ndarray, *,
    target: tuple[str, int] = ('base', 0), channel: str = 'hue',
    valid: bool = True,
) -> tuple[QualifierSettings, QualifierSample]:
    """Extend only the selected exterior feather to include a half-weight sample.

    Preserve every core bound and other channel. The selected channel's ramp
    reaches at least 0.5 at the source sample; this does not guarantee the joint
    H/S/Y key, subtractive composition or refined matte reaches 0.5. Samples
    inside the core or an already sufficient feather are numeric no-ops.
    Reject impossible feather extents rather than silently clipping them.
    Gray has no hue, but remains a valid saturation/luma measurement. Legacy
    replacement/add/subtract pickers reject gray only when hue is enabled.
    A disabled selected channel rejects softness sampling. Never mutate inputs.
    """
    validate_qualifier(settings)
    if not isinstance(channel, str) or channel not in ('hue', 'saturation', 'luma'):
        raise ValueError('Qualifier softness channel must be hue, saturation or luma')
    if (not isinstance(target, tuple) or len(target) != 2
            or not isinstance(target[0], str)
            or target[0] not in ('base', 'inclusion_ranges', 'exclusion_ranges')
            or type(target[1]) is not int or target[1] < 0):
        raise ValueError('Qualifier softness target must identify a valid range')
    name, index = target
    if name == 'base':
        if index != 0:
            raise ValueError('Qualifier base range index must be zero')
        selected = settings
    else:
        entries = getattr(settings, name)
        if index >= len(entries):
            raise ValueError('Qualifier softness target range no longer exists')
        selected = entries[index]
    if not getattr(selected, f'{channel}_enabled'):
        raise ValueError('Enable the selected channel before sampling its feather')
    if not isinstance(valid, (bool, np.bool_)) or not valid:
        raise ValueError('Qualifier softness sample must be inside visible source content')
    sample = _checked_sample(rgb, allow_gray=channel != 'hue')
    if channel == 'hue':
        distance = abs(sample.hue - (selected.hue_center % 1))
        outside = max(0.0, min(distance, 1.0 - distance) - selected.hue_width)
        limit = 0.5
        field_name = 'hue_softness'
        current = selected.hue_softness
    else:
        value = getattr(sample, channel)
        low, high = (getattr(selected, f'{channel}_{edge}') for edge in ('low', 'high'))
        if low <= value <= high:
            return replace(settings), sample
        edge = 'low' if value < low else 'high'
        outside = low - value if edge == 'low' else value - high
        field_name = f'{channel}_softness_{edge}'
        current = range_softness(selected, channel, edge)
        limit = 1.0
    softness = max(current, 2.0 * outside)
    if softness == current:
        # Do not materialize inherited edges on a numerical no-op.
        return replace(settings), sample
    if softness > limit:
        raise ValueError(f'Qualifier {channel} softness would exceed {limit:g}; '
                         'choose a closer sample or adjust the core range')
    if name == 'base':
        result = replace(settings, **{field_name: softness})
    else:
        entries = list(getattr(settings, name))
        entries[index] = replace(selected, **{field_name: softness})
        result = replace(settings, **{name: entries})
    return result, sample

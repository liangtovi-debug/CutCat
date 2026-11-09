"""Bounded additive/subtractive soft H/S/Y qualifier sets in encoded Rec.709 working RGB.

The key uses hexcone hue, HSV saturation and encoded Rec.709 luma, not HSL
lightness or linear-light luminance. Feather extends outside the selected core.
The incoming key is evaluated before hue, saturation and code-value exposure
adjustments. Optional fixed-radius matte morphology follows pointwise levels,
then smoothing. One optional ellipse or rectangle multiplies that
key in fitted project-canvas coordinates before clip geometry. This is not
semantic skin detection, tracking, a node graph or an opacity/alpha matte.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import math
from typing import Any, Iterator

import numpy as np


_LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
_BLOCK_PIXELS = 65536
_CHROMA_EPSILON = np.float32(1e-6)
_MAX_HUE_SHIFT = 0.1666666667


MAX_QUALIFIER_RANGES = 8
MAX_WINDOW_KEYFRAMES = 128
MAX_WINDOW_FRAME = 2**53 - 1
CHANNEL_ENABLE_FIELDS = frozenset(f'{channel}_enabled' for channel in ('hue', 'saturation', 'luma'))
ASYMMETRIC_SOFTNESS_FIELDS = frozenset(
    f'{channel}_softness_{edge}'
    for channel in ('saturation', 'luma') for edge in ('low', 'high')
)


def range_softness(settings, channel: str, edge: str) -> float:
    """Resolve an explicit exterior edge or its legacy symmetric fallback.

    None is intentional inheritance, not zero. Setting the legacy symmetric
    value changes only inherited edges; explicit edge overrides stay untouched.
    Hue has its original single circular feather and is not accepted here.
    """
    if channel not in ('saturation', 'luma') or edge not in ('low', 'high'):
        raise ValueError('Exterior feather requires saturation/luma and low/high')
    value = getattr(settings, f'{channel}_softness_{edge}')
    return getattr(settings, f'{channel}_softness') if value is None else value

_RANGE_BOUNDS = (
    ('hue_center', 0, 1), ('hue_width', 0, .5), ('hue_softness', 0, .5),
    ('saturation_low', 0, 1), ('saturation_high', 0, 1),
    ('saturation_softness', 0, 1), ('luma_low', 0, 1),
    ('luma_high', 0, 1), ('luma_softness', 0, 1),
)


def _validate_range(settings):
    for name in CHANNEL_ENABLE_FIELDS:
        if type(getattr(settings, name)) is not bool:
            raise ValueError(f'Qualifier {name} must be a boolean')
    for name in ASYMMETRIC_SOFTNESS_FIELDS:
        value = getattr(settings, name)
        if value is not None and (isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 0 <= value <= 1):
            raise ValueError(f'Qualifier {name} must be None or finite and in 0..1')
    for name, low, high in _RANGE_BOUNDS:
        value = getattr(settings, name)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not low <= value <= high or not math.isfinite(value)):
            raise ValueError(f'Qualifier {name} must be finite and in {low}..{high}')
    for name in ('saturation', 'luma'):
        if getattr(settings, f'{name}_low') > getattr(settings, f'{name}_high'):
            raise ValueError(f'Qualifier {name}_low must not exceed {name}_high')


@dataclass(frozen=True)
class QualifierRange:
    """Independent immutable H/S/Y range; feathers extend outside each core."""
    hue_center: float = 0.0
    hue_width: float = 0.1
    hue_softness: float = 0.05
    saturation_low: float = 0.0
    saturation_high: float = 1.0
    saturation_softness: float = 0.05
    luma_low: float = 0.0
    luma_high: float = 1.0
    luma_softness: float = 0.05

    # Explicit edge overrides; None preserves the legacy symmetric fallback.
    # Appended to preserve existing positional constructor arguments.
    saturation_softness_low: float | None = None
    saturation_softness_high: float | None = None
    luma_softness_low: float | None = None
    luma_softness_high: float | None = None

    hue_enabled: bool = True
    saturation_enabled: bool = True
    luma_enabled: bool = True

    def __post_init__(self):
        _validate_range(self)

    def to_dict(self):
        _validate_range(self)
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) != {f.name for f in fields(cls)}:
            raise ValueError('Qualifier range requires exactly its documented fields')
        return cls(**data)


def _validate_window_keyframe(key: WindowKeyframe) -> None:
    if not isinstance(key, WindowKeyframe):
        raise ValueError('Window keyframes require typed keyframe records')
    if type(key.frame) is not int or not -MAX_WINDOW_FRAME <= key.frame <= MAX_WINDOW_FRAME:
        raise ValueError('Window keyframe frame must be a bounded signed integer')
    for name, low, high in (('center_x', 0, 1), ('center_y', 0, 1),
                            ('width', .001, 2), ('height', .001, 2)):
        value = getattr(key, name)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not low <= value <= high or not math.isfinite(value)):
            raise ValueError(f'Window keyframe {name} must be finite and in {low}..{high}')
    if type(key.interpolation) is not str or key.interpolation not in ('hold', 'linear'):
        raise ValueError('Window interpolation must be hold or linear')


@dataclass(frozen=True)
class WindowKeyframe:
    """Clip-local timeline geometry; interpolation applies after this key.

    Signed keys outside a clip's visible span remain intentional interpolation
    handles after splitting or trimming. They must not be discarded or clamped.
    """
    frame: int = 0
    center_x: float = .5
    center_y: float = .5
    width: float = .5
    height: float = .5
    interpolation: str = 'linear'

    def __post_init__(self) -> None:
        _validate_window_keyframe(self)

    def to_dict(self) -> dict[str, Any]:
        _validate_window_keyframe(self)
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WindowKeyframe:
        if not isinstance(data, dict) or set(data) != {f.name for f in fields(cls)}:
            raise ValueError('Window keyframe requires exactly its documented fields')
        return cls(**data)


def validate_window_keyframes(entries: list[WindowKeyframe]) -> None:
    if not isinstance(entries, list) or len(entries) > MAX_WINDOW_KEYFRAMES:
        raise ValueError('Window keyframes must be a list of at most 128 entries')
    previous = None
    for key in entries:
        _validate_window_keyframe(key)
        if previous is not None and key.frame <= previous:
            raise ValueError('Window keyframe frames must be strictly increasing and unique')
        previous = key.frame


@dataclass
class QualifierSettings:
    enabled: bool = False
    hue_center: float = 0.0
    hue_width: float = 0.1
    hue_softness: float = 0.05
    saturation_low: float = 0.0
    saturation_high: float = 1.0
    saturation_softness: float = 0.05
    luma_low: float = 0.0
    luma_high: float = 1.0
    luma_softness: float = 0.05
    exposure: float = 0.0
    saturation: float = 1.0
    hue_shift: float = 0.0
    # Width/height are full normalized canvas dimensions. Feather is an
    # exterior fraction of normalized shape radius; invert affects only shape.
    window_shape: str = 'off'
    window_center_x: float = 0.5
    window_center_y: float = 0.5
    window_width: float = 0.5
    window_height: float = 0.5
    window_feather: float = 0.1
    window_invert: bool = False

    # Pointwise levels on the composed color key, before spatial gating.
    matte_black: float = 0.0
    matte_white: float = 1.0
    # Signed blend with radius-one square min (shrink) / max (grow).
    matte_morphology: float = 0.0
    # Blend with a fixed radius-one project-pixel binomial filter.
    matte_smoothing: float = 0.0

    inclusion_ranges: list[QualifierRange] = field(default_factory=list)
    exclusion_ranges: list[QualifierRange] = field(default_factory=list)

    # Explicit edge overrides; None preserves the legacy symmetric fallback.
    # Appended to preserve existing positional constructor arguments.
    saturation_softness_low: float | None = None
    saturation_softness_high: float | None = None
    luma_softness_low: float | None = None
    luma_softness_high: float | None = None

    hue_enabled: bool = True
    saturation_enabled: bool = True
    luma_enabled: bool = True

    # Appended for positional compatibility. Timings are local project frames,
    # not source frames, so speed changes and slips do not retime these keys.
    window_keyframes: list[WindowKeyframe] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate_qualifier(self)
        # Mutable containers belong to each settings object; records are frozen.
        self.inclusion_ranges = list(self.inclusion_ranges)
        self.exclusion_ranges = list(self.exclusion_ranges)
        self.window_keyframes = list(self.window_keyframes)

    def to_dict(self) -> dict[str, Any]:
        validate_qualifier(self)
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QualifierSettings:
        if not isinstance(data, dict) or set(data) != {f.name for f in fields(cls)}:
            raise ValueError('Qualifier requires exactly its documented settings fields')
        parsed = dict(data)
        for name in ('inclusion_ranges', 'exclusion_ranges'):
            entries = parsed[name]
            if not isinstance(entries, list) or len(entries) > MAX_QUALIFIER_RANGES:
                raise ValueError('Qualifier range sets must be bounded lists')
            parsed[name] = [QualifierRange.from_dict(entry) for entry in entries]
        entries = parsed['window_keyframes']
        if not isinstance(entries, list) or len(entries) > MAX_WINDOW_KEYFRAMES:
            raise ValueError('Window keyframes must be a bounded list')
        parsed['window_keyframes'] = [WindowKeyframe.from_dict(entry) for entry in entries]
        return cls(**parsed)


def validate_qualifier(settings: QualifierSettings) -> None:
    """Reject invalid settings even when the key or adjustments are bypassed."""
    if not isinstance(settings, QualifierSettings):
        raise ValueError('Invalid qualifier settings')
    _validate_range(settings)
    validate_window_keyframes(settings.window_keyframes)
    for name in ('inclusion_ranges', 'exclusion_ranges'):
        entries = getattr(settings, name)
        if not isinstance(entries, list) or len(entries) > MAX_QUALIFIER_RANGES:
            raise ValueError('Qualifier range sets must be lists of at most 8 entries')
        for entry in entries:
            if not isinstance(entry, QualifierRange):
                raise ValueError('Qualifier range sets require typed range records')
            _validate_range(entry)
    if type(settings.enabled) is not bool:
        raise ValueError('Qualifier enabled must be a boolean')
    if type(settings.window_invert) is not bool:
        raise ValueError('Window invert must be a boolean')
    if type(settings.window_shape) is not str or settings.window_shape not in ('off', 'ellipse', 'rectangle'):
        raise ValueError('Window shape must be off, ellipse or rectangle')
    bounds = (
        ('matte_black', 0, 1), ('matte_white', 0, 1),
        ('matte_smoothing', 0, 1), ('matte_morphology', -1, 1),
        ('window_center_x', 0, 1), ('window_center_y', 0, 1),
        ('window_width', .001, 2), ('window_height', .001, 2),
        ('window_feather', 0, 1),
        ('hue_center', 0, 1), ('hue_width', 0, 0.5),
        ('hue_softness', 0, 0.5),
        ('saturation_low', 0, 1), ('saturation_high', 0, 1),
        ('saturation_softness', 0, 1),
        ('luma_low', 0, 1), ('luma_high', 0, 1), ('luma_softness', 0, 1),
        ('exposure', -1, 1), ('saturation', 0, 2),
        ('hue_shift', -_MAX_HUE_SHIFT, _MAX_HUE_SHIFT),
    )
    for name, low, high in bounds:
        value = getattr(settings, name)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not low <= value <= high or not math.isfinite(value)):
            raise ValueError(f'Qualifier {name} must be finite and in {low}..{high}')
    for name in ('saturation', 'luma'):
        if getattr(settings, f'{name}_low') > getattr(settings, f'{name}_high'):
            raise ValueError(f'Qualifier {name}_low must not exceed {name}_high')

    if settings.matte_black >= settings.matte_white:
        raise ValueError('Matte black point must be strictly below white point')


def _validate_rgb(rgb: np.ndarray) -> None:
    if not isinstance(rgb, np.ndarray) or rgb.dtype != np.float32:
        raise ValueError('Grade input must be a float32 NumPy array')
    if rgb.ndim < 1 or rgb.shape[-1] != 3:
        raise ValueError('RGB requires a final dimension of 3')


def _blocks(rgb: np.ndarray) -> Iterator[tuple[int, np.ndarray]]:
    """Read bounded C-order chunks, including strided input, without a frame copy."""
    start = 0
    with np.nditer(rgb, flags=['external_loop', 'buffered', 'zerosize_ok'],
                   op_flags=['readonly'], order='C',
                   buffersize=_BLOCK_PIXELS * 3) as iterator:
        for channels in iterator:
            block = channels.reshape(-1, 3)
            if not np.all(np.isfinite(block)):
                raise ValueError('Color values must be finite')
            yield start, block
            start += len(block)


def _interval_weight(values: np.ndarray, low: float, high: float,
                     softness: float, softness_high: float | None = None) -> np.ndarray:
    """Inclusive plateau with independent exterior ramps in float64.

    Omitted upper softness preserves the historical symmetric path exactly.
    """
    if softness_high is None:
        softness_high = softness
    values = values.astype(np.float64)
    result = ((values >= low) & (values <= high)).astype(np.float32)
    if softness:
        below = (values < low) & (values > low - softness)
        # Divide only in the bounded ramp. Subnormal valid softness cannot
        # produce an overflowing global slope or a division by float32 zero.
        result[below] = 1 - (low - values[below]) / softness
    if softness_high:
        above = (values > high) & (values < high + softness_high)
        result[above] = 1 - (values[above] - high) / softness_high
    return result


def _mask_block(block: np.ndarray, settings: QualifierSettings) -> np.ndarray:
    result = np.zeros(len(block), dtype=np.float32)
    if not settings.enabled:
        return result
    in_range = np.all((block >= 0) & (block <= 1), axis=-1)
    if not np.any(in_range):
        return result
    # Exclude extended-range pixels BEFORE subtraction/division. Even maximum
    # finite float32 values remain a safe, bit-exact bypass, rather than being
    # clipped into a false key or overflowing while constructing their hue.
    values = block[in_range]
    low, high = np.min(values, axis=-1), np.max(values, axis=-1)
    chroma = high - low
    saturation = np.zeros(len(values), dtype=np.float32)
    np.divide(chroma, high, out=saturation, where=high > 0)
    luma = np.sum(values * _LUMA, axis=-1)
    weight = _range_weight(values, high, chroma, saturation, luma, settings)
    for entry in settings.inclusion_ranges:
        np.maximum(weight, _range_weight(values, high, chroma, saturation, luma, entry), out=weight)
    if settings.exclusion_ranges:
        excluded = np.zeros(len(values), dtype=np.float32)
        for entry in settings.exclusion_ranges:
            np.maximum(excluded, _range_weight(values, high, chroma, saturation, luma, entry), out=excluded)
        weight *= np.float32(1) - excluded
    # Preserve the historic float32 path exactly at neutral defaults. Only
    # divide inside the open ramp: even subnormal threshold gaps cannot cause
    # overflow by evaluating a huge slope outside their valid interval.
    if settings.matte_black != 0 or settings.matte_white != 1:
        values64 = weight.astype(np.float64)
        ramp = (values64 > settings.matte_black) & (values64 < settings.matte_white)
        refined = (values64 >= settings.matte_white).astype(np.float32)
        refined[ramp] = ((values64[ramp] - settings.matte_black)
                         / (settings.matte_white - settings.matte_black))
        weight = refined
    result[in_range] = weight
    return result


def _range_weight(values, high, chroma, saturation, luma, settings):
    weight = (_interval_weight(saturation, settings.saturation_low,
                              settings.saturation_high, range_softness(settings, 'saturation', 'low'),
                              range_softness(settings, 'saturation', 'high'))
              if settings.saturation_enabled else np.ones(len(values), dtype=np.float32))
    if settings.luma_enabled:
        weight *= _interval_weight(luma, settings.luma_low, settings.luma_high,
                                   range_softness(settings, 'luma', 'low'),
                                   range_softness(settings, 'luma', 'high'))
    if settings.hue_enabled and settings.hue_width < 0.5:
        # Local import avoids model -> qualifier -> selective_color -> model.
        from .selective_color import _hue
        hue = _hue(values, high, chroma).astype(np.float64)
        distance = np.abs(hue - (settings.hue_center % 1))
        distance = np.minimum(distance, 1 - distance)
        weight *= _interval_weight(distance, 0, settings.hue_width,
                                   settings.hue_softness)
        weight[chroma <= _CHROMA_EPSILON] = 0
    return weight


def _window_context(rgb, settings, canvas_size, origin, stride):
    """Validate a sampled canvas grid once, without allocating a full mask.

    Coordinates are pixel centers of the fitted project canvas, before clip
    transforms. Padding participates, exactly as it does in color grading.
    Off windows preserve the historic non-image RGB array API.
    """
    if settings.window_shape == 'off':
        return None
    if rgb.ndim != 3:
        raise ValueError('Spatial windows require a two-dimensional RGB image')
    height, width = rgb.shape[:2]
    if height == 0 or width == 0:
        raise ValueError('Invalid spatial window canvas coordinates')
    if canvas_size is None:
        canvas_size = (width, height)
    if (not isinstance(canvas_size, (tuple, list)) or len(canvas_size) != 2
            or any(type(v) is not int or v <= 0 for v in canvas_size)
            or not isinstance(origin, (tuple, list)) or len(origin) != 2
            or any(type(v) is not int or v < 0 for v in origin)
            or type(stride) is not int or stride < 1):
        raise ValueError('Invalid spatial window canvas coordinates')
    canvas_width, canvas_height = canvas_size
    x, y = origin
    if (x >= canvas_width or y >= canvas_height
            or (width and x + (width - 1) * stride >= canvas_width)
            or (height and y + (height - 1) * stride >= canvas_height)):
        raise ValueError('Spatial window samples must lie inside the canvas')
    return width, canvas_width, canvas_height, x, y, stride


def _window_block(start, count, settings, context):
    # Bounded float64 arithmetic is intentional: pixel centers and the geometry
    # never pass through the RGB8 display or depend on preview zoom/DPI.
    width, cw, ch, x, y, stride = context
    indices = np.arange(start, start + count, dtype=np.int64)
    dx = ((x + (indices % width) * stride + .5) / cw - settings.window_center_x) * (2 / settings.window_width)
    dy = ((y + (indices // width) * stride + .5) / ch - settings.window_center_y) * (2 / settings.window_height)
    radius = (np.hypot(dx, dy) if settings.window_shape == 'ellipse'
              else np.maximum(np.abs(dx), np.abs(dy)))
    weight = _interval_weight(radius, 0, 1, settings.window_feather)
    return np.float32(1) - weight if settings.window_invert else weight


def matte_support_radius(settings: QualifierSettings) -> int:
    """Required dense incoming-pixel halo for the ordered spatial stages."""
    return int(bool(settings.matte_morphology)) + int(bool(settings.matte_smoothing))


def _smoothing_context(rgb, canvas_size, origin, stride,
                       matte_source, matte_source_origin, radius=1):
    """Validate the dense source that supplies real project-pixel neighbors."""
    if rgb.ndim != 3 or not all(rgb.shape[:2]):
        raise ValueError('Spatial matte refinement requires a nonempty two-dimensional RGB image')
    height, width = rgb.shape[:2]
    if canvas_size is None:
        canvas_size = (width, height)
    if (not isinstance(canvas_size, (tuple, list)) or len(canvas_size) != 2
            or any(type(v) is not int or v <= 0 for v in canvas_size)
            or not isinstance(origin, (tuple, list)) or len(origin) != 2
            or any(type(v) is not int or v < 0 for v in origin)
            or type(stride) is not int or stride < 1):
        raise ValueError('Invalid spatial matte canvas coordinates')
    cw, ch = canvas_size
    x, y = origin
    last_x, last_y = x + (width - 1) * stride, y + (height - 1) * stride
    if last_x >= cw or last_y >= ch:
        raise ValueError('Spatial matte samples must lie inside the canvas')
    if matte_source is None:
        if stride != 1 or (x, y) != (0, 0) or (width, height) != (cw, ch):
            raise ValueError('Cropped or sparse spatial matte refinement requires a dense matte_source with its full support halo')
        source, source_origin = rgb, (0, 0)
    else:
        _validate_rgb(matte_source)
        source, source_origin = matte_source, matte_source_origin
    if (source.ndim != 3 or not all(source.shape[:2])
            or not isinstance(source_origin, (tuple, list)) or len(source_origin) != 2
            or any(type(v) is not int or v < 0 for v in source_origin)):
        raise ValueError('Invalid dense matte_source image or origin')
    sx, sy = source_origin
    sh, sw = source.shape[:2]
    if sx + sw > cw or sy + sh > ch:
        raise ValueError('Dense matte_source must lie inside the canvas')
    if (sx > max(x - radius, 0) or sy > max(y - radius, 0)
            or sx + sw <= min(last_x + radius, cw - 1)
            or sy + sh <= min(last_y + radius, ch - 1)):
        if radius == 1:
            raise ValueError('Dense matte_source must cover every sample and its one-pixel halo')
        raise ValueError('Dense matte_source must cover every sample and its two-pixel halo')
    return source, (cw, ch, x, y, stride, sx, sy)


def _check_cancel(cancel):
    if cancel is not None:
        cancel()


def _raw_image_mask(rgb, settings, cancel):
    """Classify one bounded dense halo tile, never a frame-sized temporary."""
    result = np.empty(rgb.shape[:-1], dtype=np.float32)
    flat = result.reshape(-1)
    for start, block in _blocks(rgb):
        _check_cancel(cancel)
        flat[start:start + len(block)] = _mask_block(block, settings)
    return result


def _binomial_triplet(left, center, right):
    # Tiny accepted matte weights may correctly round to zero. Do not expose
    # harmless underflow to callers that otherwise trap floating-point errors.
    with np.errstate(under='ignore'):
        return (left + np.float32(2) * center + right) * np.float32(.25)


def _blend_smoothed_matte(base, filtered, amount):
    if amount == 1:
        return filtered
    with np.errstate(under='ignore'):
        return base + amount * (filtered - base)


def _morphology_weight(source, settings, coordinates, xs, ys, cancel):
    """Evaluate ordered morphology/smoothing from bounded real source samples.

    Sparse grids classify a compact Cartesian support lattice, not the gaps
    between samples, and never treat adjacent overview pixels as neighbors.
    Both stages clamp their own coordinates to the true canvas independently.
    In particular, smoothing at an edge repeats the already-morphed edge key.
    """
    cw, ch, _x, _y, stride, sx, sy = coordinates
    radius = matte_support_radius(settings)
    if stride <= 2:
        left, right = max(int(xs[0])-radius, 0), min(int(xs[-1])+radius, cw-1)
        top, bottom = max(int(ys[0])-radius, 0), min(int(ys[-1])+radius, ch-1)
        raw = _raw_image_mask(source[top-sy:bottom-sy+1, left-sx:right-sx+1], settings, cancel)
        def at(rows, cols):
            return raw[(rows-top)[:, None], (cols-left)[None, :]]
    else:
        support = 2*radius+1
        offsets = np.arange(-radius, radius+1, dtype=np.int64)
        rows = np.clip(ys[:, None]+offsets, 0, ch-1).reshape(-1)
        cols = np.clip(xs[:, None]+offsets, 0, cw-1).reshape(-1)
        raw = _raw_image_mask(source[(rows-sy)[:, None], (cols-sx)[None, :]], settings, cancel)
        row_base = np.arange(len(ys), dtype=np.int64)*support+radius
        col_base = np.arange(len(xs), dtype=np.int64)*support+radius
        def at(rows, cols):
            return raw[(row_base+rows-ys)[:, None], (col_base+cols-xs)[None, :]]
    amount = np.float32(abs(settings.matte_morphology))
    reducer = np.minimum if settings.matte_morphology < 0 else np.maximum
    def morphed(dy, dx):
        # Clamp the morphology center BEFORE looking up its neighborhood.
        rows, cols = np.clip(ys+dy, 0, ch-1), np.clip(xs+dx, 0, cw-1)
        base = at(rows, cols)
        filtered = base.copy()
        for oy in (-1, 0, 1):
            _check_cancel(cancel)
            ry = np.clip(rows+oy, 0, ch-1)
            for ox in (-1, 0, 1):
                reducer(filtered, at(ry, np.clip(cols+ox, 0, cw-1)), out=filtered)
        return _blend_smoothed_matte(base, filtered, amount)
    base = morphed(0, 0)
    if not settings.matte_smoothing:
        return base
    horizontal = []
    for dy in (-1, 0, 1):
        values = [base if dx == dy == 0 else morphed(dy, dx) for dx in (-1, 0, 1)]
        horizontal.append(_binomial_triplet(*values))
    return _blend_smoothed_matte(base, _binomial_triplet(*horizontal),
                                 np.float32(settings.matte_smoothing))


def _smoothed_tiles(rgb, settings, source, coordinates, cancel, *, evaluate=True):
    """Yield bounded target tiles; halos always read the unchanged dense source.

    Dense targets classify each halo tile once. Sparse targets gather actual
    project-pixel support (up to 5 x 5), never neighboring overview samples.
    Morphology precedes smoothing; each stage is rounded in float32.
    """
    cw, ch, x, y, stride, sx, sy = coordinates
    height, width = rgb.shape[:2]
    # Keep each RGB/color-classification temporary bounded, even for extremely
    # wide frames. Sparse grids must not allocate their intervening canvas area.
    radius = matte_support_radius(settings)
    edge = max(1, math.isqrt(_BLOCK_PIXELS) - 2*radius)
    tile_edge = (max(1, edge // stride) if stride <= 2 else
                 max(1, math.isqrt(_BLOCK_PIXELS) // (2*radius+1))
                 if settings.matte_morphology else edge)
    amount = np.float32(settings.matte_smoothing)
    for y0 in range(0, height, tile_edge):
        y1 = min(y0 + tile_edge, height)
        ys = y + np.arange(y0, y1, dtype=np.int64) * stride
        for x0 in range(0, width, tile_edge):
            _check_cancel(cancel)
            x1 = min(x0 + tile_edge, width)
            xs = x + np.arange(x0, x1, dtype=np.int64) * stride
            target = rgb[y0:y1, x0:x1]
            original = np.ascontiguousarray(target).reshape(-1, 3)
            # A finite supplied source is not enough: the selected destination
            # must be the corresponding incoming pixels, not a later pipeline.
            centers = source[(ys - sy)[:, None], (xs - sx)[None, :]]
            if not np.array_equal(target, centers):
                raise ValueError('Dense matte_source samples must match the incoming RGB image')
            if not settings.enabled or not evaluate:
                yield (slice(y0, y1), slice(x0, x1)), original, np.zeros(len(original), np.float32)
                continue
            if settings.matte_morphology:
                weight = _morphology_weight(source, settings, coordinates, xs, ys, cancel).reshape(-1)
            else:
                if stride <= 2:
                    left, right = max(int(xs[0]) - 1, 0), min(int(xs[-1]) + 1, cw - 1)
                    top, bottom = max(int(ys[0]) - 1, 0), min(int(ys[-1]) + 1, ch - 1)
                    raw = _raw_image_mask(source[top-sy:bottom-sy+1,
                                                 left-sx:right-sx+1], settings, cancel)
                    rows = [np.clip(ys + dy, 0, ch - 1) - top for dy in (-1, 0, 1)]
                    cols = [np.clip(xs + dx, 0, cw - 1) - left for dx in (-1, 0, 1)]
                    horizontal = []
                    for row in rows:
                        horizontal.append(_binomial_triplet(raw[row[:, None], cols[0][None, :]],
                                           raw[row[:, None], cols[1][None, :]],
                                           raw[row[:, None], cols[2][None, :]]))
                    base = raw[rows[1][:, None], cols[1][None, :]]
                else:
                    horizontal = []
                    base = None
                    for dy in (-1, 0, 1):
                        row = np.clip(ys + dy, 0, ch - 1) - sy
                        values = []
                        for dx in (-1, 0, 1):
                            _check_cancel(cancel)
                            col = np.clip(xs + dx, 0, cw - 1) - sx
                            neighbors = source[row[:, None], col[None, :]]
                            mask = _raw_image_mask(neighbors, settings, cancel)
                            values.append(mask)
                            if dx == dy == 0:
                                base = mask
                        horizontal.append(_binomial_triplet(*values))
                filtered = _binomial_triplet(*horizontal)
                weight = _blend_smoothed_matte(base, filtered, amount).reshape(-1)
            # Spatial refinement may spread a key to neighbors, but must never
            # pull finite extended RGB into the key or subsequent adjustments.
            in_range = np.all((original >= 0) & (original <= 1), axis=-1)
            weight[~in_range] = 0
            if settings.window_shape != 'off':
                # Flattening a rectangular tile is not flattening the canvas.
                # Describe it as its own sampled grid at the true canvas origin.
                window_context = (x1-x0, cw, ch, int(xs[0]), int(ys[0]), stride)
                with np.errstate(under='ignore'):
                    weight *= _window_block(0, len(weight), settings, window_context)
            yield (slice(y0, y1), slice(x0, x1)), original, weight


def _validated_smoothing(rgb, settings, canvas_size, origin, stride,
                         matte_source, matte_source_origin, cancel):
    source, coordinates = _smoothing_context(rgb, canvas_size, origin, stride,
                                             matte_source, matte_source_origin,
                                             matte_support_radius(settings))
    # Validate the entire explicitly supplied source, including unselected
    # pixels, with the same bounded finite-value guarantee as ordinary input.
    for _start, _block in _blocks(source):
        _check_cancel(cancel)
    return source, coordinates


def qualifier_mask(rgb: np.ndarray, settings: QualifierSettings, *,
                   canvas_size=None, origin=(0, 0), stride=1,
                   matte_source=None, matte_source_origin=(0, 0),
                   cancel=None) -> np.ndarray:
    """Return an independent float32 key with shape ``rgb.shape[:-1]``.

    Disabled keys are zero. Each disabled channel condition contributes one
    without rewriting its bounds or feathers; all three off selects all valid
    in-range colors. An all-off exclusion therefore clears the composed key.
    Disabled hue accepts gray. A hue half-width of 0.5 bypasses hue entirely,
    including for gray and black; narrower hue keys exclude chroma <= 1e-6.
    Every finite pixel outside [0,1] is excluded, not clamped for classification.
    Inclusion maximum multiplied by one minus exclusion maximum is remapped
    by black/white points, optionally shrunk/grown, then smoothed and finally
    multiplied by the window. Signed morphology blends the center key with a
    radius-one square minimum (negative) or maximum (positive), with magnitude
    controlling blend strength, not radius. Each stage replicates canvas edges.
    Smoothing blends the morphed key with a separable [1,2,1]/4 filter of
    radius one fitted project-canvas pixel, replicating only canvas edges.
    Extended RGB is excluded again after filtering. Zero morphology preserves
    the smoothing-only path exactly; both zero bypass all added spatial/source
    validation, preserving the historic pointwise API.

    For cropped/strided images, canvas_size=(width,height), origin=(x,y), and
    stride describe the target samples. Nonzero morphology or smoothing requires a dense
    full-resolution matte_source containing those exact incoming pixels and
    every support neighbor (radius two when both stages are on, otherwise one);
    matte_source_origin locates a dense halo crop.
    Omitted source is allowed only for a complete, unstrided HWC canvas.
    A cancel callable, when provided, is called between bounded work chunks;
    its exceptions propagate unchanged.
    """
    validate_qualifier(settings)
    if settings.window_keyframes:
        raise ValueError('Animated windows require evaluate_window at an explicit clip-relative timeline frame')
    _validate_rgb(rgb)
    context = _window_context(rgb, settings, canvas_size, origin, stride)
    result = np.zeros(rgb.shape[:-1], dtype=np.float32)
    if settings.matte_smoothing or settings.matte_morphology:
        source, coordinates = _validated_smoothing(rgb, settings, canvas_size,
            origin, stride, matte_source, matte_source_origin, cancel)
        for tile, _original, weight in _smoothed_tiles(rgb, settings, source, coordinates, cancel):
            result[tile] = weight.reshape(result[tile].shape)
        return result
    flat = result.reshape(-1)
    for start, block in _blocks(rgb):
        _check_cancel(cancel)
        weight = _mask_block(block, settings)
        if context is not None:
            weight *= _window_block(start, len(block), settings, context)
        flat[start:start + len(block)] = weight
    return result


def _adjust_weighted_block(block, weight, settings):
    """Return selected indices and their adjusted values, preserving identity."""
    selected = weight > 0
    original = block[selected]
    adjusted = original.copy()
    if not np.any(selected):
        return selected, adjusted
    low, high = np.min(adjusted, axis=-1), np.max(adjusted, axis=-1)
    chroma = high - low
    if settings.hue_shift:
        from .selective_color import _hue, _rotate
        hue = _hue(adjusted, high, chroma)
        offset = np.full(len(adjusted), settings.hue_shift, dtype=np.float32)
        _rotate(adjusted, hue, offset, low, high, chroma != 0)
    if settings.saturation != 1:
        # Achromatic saturation is mathematically identity; bypass it
        # explicitly to preserve arbitrary gray values and signed zero.
        chromatic = chroma != 0
        values = adjusted[chromatic]
        luma = np.sum(values * _LUMA, axis=-1, keepdims=True)
        adjusted[chromatic] = (luma + np.float32(settings.saturation)
                                * (values - luma))
    if settings.exposure:
        adjusted *= np.float32(2 ** settings.exposure)
    weights = weight[selected, None]
    blended = adjusted.copy()
    # Blending an unchanged sample can still add a rounding error (e.g.
    # a gray selected by a partial luma key with saturation-only edits).
    partial = (weights[:, 0] != 1) & np.any(adjusted != original, axis=-1)
    blended[partial] = (original[partial] * (np.float32(1) - weights[partial])
                        + adjusted[partial] * weights[partial])
    if not np.all(np.isfinite(blended)):
        raise ValueError('Grade produced non-finite values')
    return selected, blended


def apply_qualifier(rgb: np.ndarray, settings: QualifierSettings, *,
                    canvas_size=None, origin=(0, 0), stride=1,
                    matte_source=None, matte_source_origin=(0, 0),
                    cancel=None) -> np.ndarray:
    """Apply the incoming key, returning independent, unclipped float32 RGB.

    Rotate hue preserving min/max, scale deviation from encoded Rec.709 luma,
    multiply code values by 2**exposure, then blend with the incoming pixel.
    Disabled/neutral settings and zero weights are bit-exact copies. Spatial
    source and cancellation arguments follow :func:`qualifier_mask`.
    """
    validate_qualifier(settings)
    if settings.enabled and settings.window_keyframes:
        raise ValueError('Animated windows require evaluate_window at an explicit clip-relative timeline frame')
    _validate_rgb(rgb)
    context = _window_context(rgb, settings, canvas_size, origin, stride)
    result = np.array(rgb, dtype=np.float32, order='C', copy=True)
    pixels = result.reshape(-1, 3)
    neutral = (settings.hue_shift == 0 and settings.saturation == 1
               and settings.exposure == 0)
    if settings.matte_smoothing or settings.matte_morphology:
        source, coordinates = _validated_smoothing(rgb, settings, canvas_size,
            origin, stride, matte_source, matte_source_origin, cancel)
        for tile, original, weight in _smoothed_tiles(rgb, settings, source, coordinates, cancel,
                                                      evaluate=not neutral):
            if not settings.enabled or neutral:
                continue
            # Spatially introduced subnormal weights may legitimately round
            # away during RGB blending. Preserve all other floating errors.
            with np.errstate(under='ignore'):
                selected, blended = _adjust_weighted_block(original, weight, settings)
            output = original.copy()
            output[selected] = blended
            result[tile] = output.reshape(result[tile].shape)
        return result
    for start, block in _blocks(result):
        _check_cancel(cancel)
        if not settings.enabled or neutral:
            continue
        weight = _mask_block(block, settings)
        if context is not None:
            weight *= _window_block(start, len(block), settings, context)
        selected, blended = _adjust_weighted_block(block, weight, settings)
        pixels[start:start + len(block)][selected] = blended
    return result

"""LOCAL-ONLY, supplied-polygon beauty-mask and temporal-gate experiment.

This module is deliberately not imported by production rendering, settings, or
UI. It detects nothing. Face polygons and all eye/lip/brow/hair/other exclusion
polygons must be supplied in normalized source-frame coordinates. Polygons
cannot establish whether hands, glasses, hair, or other objects occlude a face.
Unknown geometry/occlusion validity MUST remain false, yielding zero effect.
Even a nonzero result is a caller-reviewed geometric mask, not automatically
safe skin segmentation. No dependency download or model inference occurs.

PreviewSequenceGate is for chronological previews only. Exact random seeks
must use precompute_sequence_gate on complete canonical source-frame metadata,
then look up that immutable plan. No old mask, confidence, or geometry is
extrapolated through a missing/rejected detection. A zero mask/gain means the
caller must retain the original frame; this module does not alter images.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from numbers import Real
from typing import Sequence

import numpy as np


Polygon = Sequence[Sequence[float]]
MAX_DIMENSION = 16_384
MAX_PIXELS = 16_777_216
MAX_POLYGONS = 64
MAX_VERTICES = 256
MAX_SEQUENCE_FRAMES = 1_000_000
_EPS = 1e-12
# A disk this large contains the whole square pixel, not just its center.
_PIXEL_MARGIN = math.sqrt(0.5)


@dataclass(frozen=True)
class PrototypeCapabilities:
    supplied_polygons_required: bool = True
    caller_review_required: bool = True
    automatic_face_detection: bool = False
    automatic_safe_segmentation: bool = False
    detects_hand_occlusion: bool = False
    detects_glasses_occlusion: bool = False
    detects_hair_occlusion: bool = False
    production_integration: bool = False


CAPABILITIES = PrototypeCapabilities()


class BeautyMaskCancelled(RuntimeError):
    """Cancellation aborts construction; no partial mask or plan is returned."""


class _InvalidPolygon(ValueError):
    pass


def _check_cancel(cancel) -> None:
    if cancel is None:
        return
    if callable(getattr(cancel, 'is_set', None)):
        stopped = cancel.is_set()
    elif callable(cancel):
        stopped = cancel()  # Preserve the caller's exception, if any.
    else:
        raise ValueError("Mask cancellation must be an event or callable")
    if stopped:
        raise BeautyMaskCancelled("Beauty-mask prototype cancelled")


@dataclass(frozen=True)
class ProtectedPolygons:
    """Explicit filled exclusion regions, NOT detected semantic boundaries.

    A hair boundary must be represented by a closed polygon covering the hair
    to protect, not an open line. ``other`` can contain manually supplied hands,
    glasses, or any other exclusion. All exclusions apply across ALL faces.
    Empty categories do not establish that those features are absent.
    """

    eyes: Sequence[Polygon] = ()
    lips: Sequence[Polygon] = ()
    brows: Sequence[Polygon] = ()
    hair: Sequence[Polygon] = ()
    other: Sequence[Polygon] = ()


@dataclass(frozen=True)
class PolygonMaskResult:
    mask: np.ndarray
    valid: bool
    reason: str
    capabilities: PrototypeCapabilities = CAPABILITIES

    def __post_init__(self) -> None:
        self.mask.setflags(write=False)


def _finite_number(value: object) -> bool:
    if not isinstance(value, Real) or isinstance(value, (bool, np.bool_)):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _validate_size(width: int, height: int) -> None:
    if (any(isinstance(v, (bool, np.bool_)) or not isinstance(v, (int, np.integer))
            or not 1 <= v <= MAX_DIMENSION for v in (width, height))
            or int(width) * int(height) > MAX_PIXELS):
        raise ValueError("Mask requires positive integer dimensions within prototype limits")


def _cross(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    return float((b[0] - a[0]) * (c[1] - a[1])
                 - (b[1] - a[1]) * (c[0] - a[0]))


def _segments_touch(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray) -> bool:
    if np.any(np.maximum(a, b) + _EPS < np.minimum(c, d)) or np.any(
            np.maximum(c, d) + _EPS < np.minimum(a, b)):
        return False
    ab_c, ab_d = _cross(a, b, c), _cross(a, b, d)
    cd_a, cd_b = _cross(c, d, a), _cross(c, d, b)
    return ((min(ab_c, ab_d) <= _EPS and max(ab_c, ab_d) >= -_EPS)
            and (min(cd_a, cd_b) <= _EPS and max(cd_a, cd_b) >= -_EPS))


def _polygon(value: Polygon, cancel=None) -> np.ndarray:
    _check_cancel(cancel)
    # Bound both dimensions before ANY array conversion. A broadcast float32
    # view, or a long/ragged nested sequence, must not trigger a large float64
    # temporary just to discover that it exceeds the vertex limit.
    if isinstance(value, np.ndarray):
        if (value.ndim != 2 or value.shape[1] != 2
                or not 3 <= len(value) <= MAX_VERTICES):
            raise _InvalidPolygon("Polygon requires 3..256 two-dimensional vertices")
        raw = value
    elif isinstance(value, (list, tuple)):
        if not 3 <= len(value) <= MAX_VERTICES:
            raise _InvalidPolygon("Polygon requires 3..256 two-dimensional vertices")
        for vertex in value:
            if (not isinstance(vertex, (tuple, list, np.ndarray))
                    or (isinstance(vertex, np.ndarray) and vertex.ndim != 1)
                    or len(vertex) != 2 or not all(_finite_number(v) for v in vertex)):
                raise _InvalidPolygon("Polygon requires finite numeric coordinate pairs")
        raw = np.asarray(value)
    else:
        raise _InvalidPolygon("Expected a bounded polygon array or sequence")
    try:
        # Reject strings, bools, and complex values rather than coercing them.
        if raw.dtype.kind not in 'iuf':
            raise _InvalidPolygon("Polygon coordinates must be real numbers")
        points = np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _InvalidPolygon("Invalid polygon coordinates") from exc
    if not np.all(np.isfinite(points)) or np.any((points < 0.) | (points > 1.)):
        raise _InvalidPolygon("Polygon coordinates must be finite and within [0, 1]")
    if np.array_equal(points[0], points[-1]):
        points = points[:-1]
    if len(points) < 3 or len(np.unique(points, axis=0)) != len(points):
        raise _InvalidPolygon("Polygon has duplicate or missing vertices")
    after = np.roll(points, -1, axis=0)
    area2 = float(np.sum(points[:, 0] * after[:, 1] - points[:, 1] * after[:, 0]))
    if abs(area2) <= _EPS:
        raise _InvalidPolygon("Polygon has no usable area")
    for i in range(len(points)):
        _check_cancel(cancel)
        before, vertex, following = points[i - 1], points[i], after[i]
        if (abs(_cross(before, vertex, following)) <= _EPS
                and np.dot(vertex - before, following - vertex) < 0.):
            raise _InvalidPolygon("Adjacent polygon edges must not double back")
        for j in range(i + 1, len(points)):
            if j == i + 1 or (i == 0 and j == len(points) - 1):
                continue
            if _segments_touch(points[i], after[i], points[j], after[j]):
                raise _InvalidPolygon("Polygon must be simple and non-self-touching")
    return points.copy()


def _polygons(values: Sequence[Polygon], cancel=None) -> list[np.ndarray]:
    if (not isinstance(values, (tuple, list, np.ndarray))
            or (isinstance(values, np.ndarray) and values.ndim == 0)
            or len(values) > MAX_POLYGONS):
        raise _InvalidPolygon("Expected a bounded sequence of polygons")
    return [_polygon(value, cancel) for value in values]


def _apply_polygon(mask: np.ndarray, points: np.ndarray, feather: float, *,
                   exclude: bool, cancel=None) -> None:
    _check_cancel(cancel)
    height, width = mask.shape
    points = points * np.array((width, height), dtype=np.float64)
    expansion = feather + _PIXEL_MARGIN if exclude else 0.
    minimum, maximum = points.min(axis=0) - expansion, points.max(axis=0) + expansion
    x0, y0 = np.maximum(0, np.floor(minimum)).astype(int)
    x1, y1 = np.minimum((width, height), np.ceil(maximum)).astype(int)
    xx = np.arange(x0, x1, dtype=np.float64)[None, :] + .5
    # Bounded row scratch prevents every polygon edge allocating a full image.
    for top in range(y0, y1, 64):
        _check_cancel(cancel)
        bottom = min(top + 64, y1)
        yy = np.arange(top, bottom, dtype=np.float64)[:, None] + .5
        inside = np.zeros((bottom - top, x1 - x0), dtype=bool)
        distance2 = np.full(inside.shape, np.inf, dtype=np.float64)
        for a, b in zip(points, np.roll(points, -1, axis=0)):
            _check_cancel(cancel)
            dx, dy = b - a
            if dy != 0.:
                crossing = ((a[1] > yy) != (b[1] > yy)) & (
                    xx < (a[0] + (yy - a[1]) * dx / dy))
                inside ^= crossing
            fraction = np.clip(((xx - a[0]) * dx + (yy - a[1]) * dy)
                               / (dx * dx + dy * dy), 0., 1.)
            candidate = (xx - a[0] - fraction * dx) ** 2 + (yy - a[1] - fraction * dy) ** 2
            np.minimum(distance2, candidate, out=distance2)
        # Normalized coordinates can reconstruct a pixel-edge corner a few
        # ulps away (e.g. 29 -> 29.000000000000004). At exact diagonal
        # footprint contact, a tiny positive error must not admit a pixel.
        # Subtract a conservative coordinate-scaled float64 guard for both
        # branches; this can only shrink face coverage/enlarge exclusions.
        roundoff_guard = 32. * np.finfo(np.float64).eps * max(width, height)
        clearance = np.sqrt(distance2) - _PIXEL_MARGIN - roundoff_guard
        alpha = np.clip(clearance / feather, 0., 1.) if feather else (clearance > 0.)
        alpha = np.where(~inside if exclude else inside, alpha, 0.).astype(np.float32)
        region = mask[top:bottom, x0:x1]
        if exclude:
            np.minimum(region, alpha, out=region)
        else:
            np.maximum(region, alpha, out=region)


def build_face_mask(
    width: int, height: int, face_polygons: Sequence[Polygon], *,
    exclusions: ProtectedPolygons = ProtectedPolygons(),
    geometry_reviewed: bool = False, feather_px: float = 3., cancel=None,
) -> PolygonMaskResult:
    """Build [0, 1] float32 coverage from explicit normalized (x, y) polygons.

    0 and 1 denote image edges, so a pixel center is ((x+.5)/w, (y+.5)/h).
    Feathering only removes face coverage; exclusions can only remove more.
    A half-pixel-diagonal guard also protects the entire pixel footprint.
    Multiple faces are unioned BEFORE exclusions so overlaps cannot bypass them.

    ``geometry_reviewed=True`` is the caller's assertion that supplied regions
    are valid for this exact frame, including protected features and occlusions.
    Leave it false for unknown/missing coverage. It is not a segmentation score.
    Bad dimensions/options raise ValueError; missing/unreviewed/invalid geometry
    fails closed to an all-zero result, including any malformed exclusion.
    Resource limits are 16,384 pixels/axis, 16,777,216 total pixels, 64 face
    polygons plus 64 exclusions, and 256 vertices/polygon. Scratch is tiled
    in 64-row chunks. An event/callable cancellation hook is checked during
    polygon validation, each polygon/tile/edge, and before publishing a result.
    """
    _check_cancel(cancel)
    _validate_size(width, height)
    if not _finite_number(feather_px) or not 0. <= feather_px <= MAX_DIMENSION:
        raise ValueError("Feather must be a finite nonnegative pixel distance")
    mask = np.zeros((height, width), dtype=np.float32)
    if geometry_reviewed is not True:
        return PolygonMaskResult(mask, False, 'unreviewed_geometry_or_occlusion')
    try:
        faces = _polygons(face_polygons, cancel)
        if not isinstance(exclusions, ProtectedPolygons):
            raise _InvalidPolygon("Expected explicit protected polygons")
        protected = []
        for group in (exclusions.eyes, exclusions.lips, exclusions.brows,
                      exclusions.hair, exclusions.other):
            protected.extend(_polygons(group, cancel))
        if len(protected) > MAX_POLYGONS:
            raise _InvalidPolygon("Too many protected polygons")
    except _InvalidPolygon:
        return PolygonMaskResult(mask, False, 'invalid_polygon')
    if not faces:
        return PolygonMaskResult(mask, False, 'no_faces')
    for points in faces:
        _apply_polygon(mask, points, float(feather_px), exclude=False, cancel=cancel)
    for points in protected:
        _apply_polygon(mask, points, float(feather_px), exclude=True, cancel=cancel)
    _check_cancel(cancel)
    if not np.any(mask):
        return PolygonMaskResult(mask, False, 'no_usable_interior')
    return PolygonMaskResult(mask, True, 'caller_reviewed_geometry')


@dataclass(frozen=True)
class FrameObservation:
    """One source-frame observation; no detector or occlusion score is inferred.

    ``geometry_valid`` must be false whenever mask/occlusion validity is unknown.
    ``detected`` and ``geometry_valid`` default false to make omissions safe.
    Track identity denotes the exact reviewed face/face-set, not display order.
    """

    frame_index: int
    timestamp: float
    confidence: float | None = None
    track_id: str | None = None
    detected: bool = False
    geometry_valid: bool = False
    scene_cut: bool = False


@dataclass(frozen=True)
class GateConfig:
    minimum_confidence: float = .9
    ramp_frames: int = 4
    max_timestamp_gap: float = .25

    def __post_init__(self) -> None:
        if (not _finite_number(self.minimum_confidence)
                or not 0. <= self.minimum_confidence <= 1.):
            raise ValueError("Minimum confidence must be finite and within [0, 1]")
        if type(self.ramp_frames) is not int or not 2 <= self.ramp_frames <= 10_000:
            raise ValueError("Reacquisition requires 2..10000 consecutive valid frames")
        if not _finite_number(self.max_timestamp_gap) or self.max_timestamp_gap <= 0.:
            raise ValueError("Maximum timestamp gap must be finite and positive")


@dataclass(frozen=True)
class GateDecision:
    gain: float
    accepted: bool
    reason: str


class PreviewSequenceGate:
    """Sequential preview gate. Never use this mutable history for exact seeks.

    Every rejection immediately clears the ramp and returns gain zero. A cut or
    changed track begins a new ramp at zero. Invalid/nonconsecutive frame order,
    nonincreasing timestamps, or a long gap rejects the current frame. A fresh
    valid run progresses 0, 1/(ramp_frames-1), ..., 1. There is no fade-out,
    hysteresis, interpolated detection, geometry reuse, or image-history blend.
    """

    def __init__(self, config: GateConfig = GateConfig()) -> None:
        if not isinstance(config, GateConfig):
            raise ValueError("Expected validated gate configuration")
        self.config = config
        self.reset()

    def reset(self) -> None:
        self._last_index: int | None = None
        self._last_timestamp: float | None = None
        self._track: str | None = None
        self._streak = 0

    def _reject(self, reason: str) -> GateDecision:
        self._track = None
        self._streak = 0
        return GateDecision(0., False, reason)

    def step(self, observation: FrameObservation) -> GateDecision:
        if not isinstance(observation, FrameObservation):
            self.reset()
            return self._reject('invalid_observation')
        o = observation
        if (type(o.frame_index) is not int or o.frame_index < 0
                or not _finite_number(o.timestamp) or o.timestamp < 0.
                or type(o.scene_cut) is not bool):
            self.reset()
            return self._reject('invalid_frame_metadata')
        discontinuity = (self._last_index is not None and (
            o.frame_index != self._last_index + 1
            or o.timestamp <= self._last_timestamp
            or o.timestamp - self._last_timestamp > self.config.max_timestamp_gap))
        self._last_index, self._last_timestamp = o.frame_index, float(o.timestamp)
        if discontinuity:
            return self._reject('timestamp_or_frame_discontinuity')
        if o.detected is not True:
            return self._reject('missing_detection')
        if o.geometry_valid is not True:
            return self._reject('unknown_geometry_or_occlusion')
        if (not _finite_number(o.confidence) or not 0. <= o.confidence <= 1.
                or o.confidence < self.config.minimum_confidence):
            return self._reject('rejected_confidence')
        if not isinstance(o.track_id, str) or not o.track_id.strip():
            return self._reject('unknown_track')
        reason = 'acquiring'
        if o.scene_cut:
            self._streak = 0
            reason = 'scene_cut'
        elif self._track is not None and self._track != o.track_id:
            self._streak = 0
            reason = 'track_changed'
        self._track = o.track_id
        self._streak = min(self._streak + 1, self.config.ramp_frames)
        gain = (self._streak - 1) / (self.config.ramp_frames - 1)
        return GateDecision(gain, True, 'ready' if gain == 1. else reason)


@dataclass(frozen=True)
class CanonicalGatePlan:
    """Precomputed source-bound decisions; lookup never mutates preview state.

    source_key must identify the source AND analysis version/settings. Rebuild
    when either changes. Metadata covers every source frame in decode order;
    missing detections are explicit observations, never omitted rows. This
    plan guarantees gate parity only, not the quality of supplied polygons.
    """

    source_key: str
    observations: tuple[FrameObservation, ...]
    decisions: tuple[GateDecision, ...]
    config: GateConfig
    uses_canonical_sequence: bool = field(default=True, init=False)

    def decision_for(self, frame_index: int, *, timestamp: float, source_key: str) -> GateDecision:
        if source_key != self.source_key:
            return GateDecision(0., False, 'source_mismatch')
        if type(frame_index) is not int or not 0 <= frame_index < len(self.decisions):
            return GateDecision(0., False, 'frame_unavailable')
        if (not _finite_number(timestamp)
                or timestamp != self.observations[frame_index].timestamp):
            return GateDecision(0., False, 'timestamp_mismatch')
        return self.decisions[frame_index]


def precompute_sequence_gate(
    observations: Sequence[FrameObservation], *, source_key: str,
    config: GateConfig = GateConfig(), cancel=None,
) -> CanonicalGatePlan:
    """Analyze COMPLETE canonical source metadata once, before random seeking.

    The rows must be ordered 0..N-1 with finite, increasing source timestamps.
    Confidence/geometry failures remain in the plan as zero-gain observations.
    Timestamp gaps exceeding the configured limit also reset/reject safely.
    This cannot make unordered preview samples into canonical source metadata.
    Input must be a bounded list/tuple, at most MAX_SEQUENCE_FRAMES rows.
    Cancellation is checked on every validation/evaluation row, and raises
    without publishing an incomplete plan.
    """
    _check_cancel(cancel)
    if not isinstance(source_key, str) or not source_key.strip():
        raise ValueError("Canonical source and analysis key is required")
    if not isinstance(observations, (list, tuple)) or len(observations) > MAX_SEQUENCE_FRAMES:
        raise ValueError("Canonical metadata must be a bounded list or tuple")
    rows = tuple(observations)
    previous = -math.inf
    for index, row in enumerate(rows):
        _check_cancel(cancel)
        if (not isinstance(row, FrameObservation) or type(row.frame_index) is not int
                or row.frame_index != index or not _finite_number(row.timestamp)
                or row.timestamp < 0. or row.timestamp <= previous):
            raise ValueError("Canonical metadata requires every source frame in timestamp order")
        previous = float(row.timestamp)
    gate = PreviewSequenceGate(config)
    decisions = []
    for row in rows:
        _check_cancel(cancel)
        decisions.append(gate.step(row))
    _check_cancel(cancel)
    return CanonicalGatePlan(source_key, rows, tuple(decisions), config)


def gated_mask(result: PolygonMaskResult, decision: GateDecision) -> np.ndarray:
    """Scale only the CURRENT frame's mask, returning zero on any rejection.

    Caller must provide the result belonging to the decision's source frame.
    This helper has no history and cannot propagate or extrapolate old masks.
    """
    if (not result.valid or decision.accepted is not True
            or not _finite_number(decision.gain) or not 0. < decision.gain <= 1.):
        return np.zeros_like(result.mask)
    return result.mask * np.float32(decision.gain)

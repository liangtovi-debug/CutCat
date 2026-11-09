"""Owned, source-anchored 2-D stabilization and float32 RGB resampling.

Only translation and rotation from vidstab's debug global-motion rows are
used. Its estimated zoom is deliberately ignored. All transforms are pixel
coordinate transforms, with an inverse matrix mapping an output sample to
its current source frame. No image history, reflection, byte quantization,
color conversion, or letterbox padding belongs in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import io
import math
from typing import Callable, Iterable

import numpy as np

from .stabilization_settings import StabilizationSettings, validate_stabilization_settings

MAX_MOTION_ROWS = 18_000
_IDENTITY = np.eye(3, dtype=np.float64)
_CANCEL = Callable[[], None]


@dataclass(frozen=True)
class StabilizationPlan:
    """Immutable source-frame-grid result, independent of clip cuts or speed.

    ``valid`` describes usable motion estimates, not the presence of a crop.
    In crop mode ONE centered zoom is shared by the entire source, including
    invalid rows. Invalid rows have identity motion correction. In black mode
    their complete inverse matrix is identity. ``shot_ids`` is -1 for invalid
    estimates; smoothing never crosses a cut or invalid estimate.
    """

    inverse_matrices: np.ndarray
    valid: np.ndarray
    shot_ids: np.ndarray
    zooms: np.ndarray

    def __post_init__(self) -> None:
        for value in (self.inverse_matrices, self.valid, self.shot_ids, self.zooms):
            value.setflags(write=False)


def parse_vidstab_debug(
    text_or_lines: str | Iterable[str], *, expected_frames: int | None = None,
    max_rows: int = MAX_MOTION_ROWS,
) -> np.ndarray:
    """Strictly parse raw ``debug=1`` global_motions.trf six-column output.

    Columns are ``unused_time, tx, ty, alpha, zoom, extra``. The first column
    is always ZERO, not a frame counter; row order indexes the source grid.
    Any nonzero integral ``extra`` is an invalid-estimate flag. Finite zoom is
    accepted but is never used to transform the picture. Comments are allowed;
    wrong formats, nonfinite values, missing rows, and excess input fail closed.
    """
    if type(max_rows) is not int or not 1 <= max_rows <= MAX_MOTION_ROWS:
        raise ValueError(f"Stabilization row limit must be in 1..{MAX_MOTION_ROWS}")
    if expected_frames is not None and (type(expected_frames) is not int
            or not 1 <= expected_frames <= max_rows):
        raise ValueError("Expected stabilization frame count is outside the row limit")
    if isinstance(text_or_lines, str):
        if len(text_or_lines) > max_rows * 1024 + 4096:
            raise ValueError("Stabilization motion text exceeds its size limit")
        lines = io.StringIO(text_or_lines)
    else:
        lines = iter(text_or_lines)
    rows: list[list[float]] = []
    for line_number, line in enumerate(lines, 1):
        if line_number > max_rows * 3 + 1024 or not isinstance(line, str) or len(line) > 1024:
            raise ValueError("Stabilization motion text exceeds its line limit")
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        fields = line.split()
        if len(fields) != 6:
            raise ValueError(f"Stabilization motion row {line_number} requires exactly six columns")
        try:
            row = [float(value) for value in fields]
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"Invalid stabilization motion row {line_number}") from exc
        if (not all(math.isfinite(value) for value in row) or row[0] != 0.0
                or not row[5].is_integer()):
            raise ValueError(f"Invalid stabilization motion row {line_number}")
        rows.append(row)
        if len(rows) > max_rows:
            raise ValueError("Stabilization motion rows exceed the frame limit")
    if not rows or (expected_frames is not None and len(rows) != expected_frames):
        raise ValueError("Stabilization motion row count does not match the source frame count")
    return np.asarray(rows, dtype=np.float64)


def _size(value: tuple[int, int], name: str) -> tuple[int, int]:
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or any(isinstance(v, (bool, np.bool_)) or not isinstance(v, (int, np.integer))
                   or not 1 <= v <= 1_000_000 for v in value)):
        raise ValueError(f"Stabilization {name} must contain positive integer width and height")
    return int(value[0]), int(value[1])


def _pose(angle: float, displacement: np.ndarray, center: np.ndarray) -> np.ndarray:
    """Rigid pose parameterized by unwrapped angle and center displacement."""
    cosine, sine = math.cos(angle), math.sin(angle)
    matrix = np.array([[cosine, -sine, 0.], [sine, cosine, 0.], [0., 0., 1.]])
    matrix[:2, 2] = center + displacement - matrix[:2, :2] @ center
    return matrix


def _rigid_inverse(matrix: np.ndarray) -> np.ndarray:
    inverse = _IDENTITY.copy()
    inverse[:2, :2] = matrix[:2, :2].T
    inverse[:2, 2] = -(inverse[:2, :2] @ matrix[:2, 2])
    return inverse


def _affine(matrix: np.ndarray, name: str) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float64)
    if (matrix.shape != (3, 3) or not np.all(np.isfinite(matrix))
            or not np.array_equal(matrix[2], (0., 0., 1.))):
        raise ValueError(f"Stabilization {name} must be a finite affine 3 by 3 matrix")
    with np.errstate(over='ignore', invalid='ignore'):
        determinant = np.linalg.det(matrix[:2, :2])
    if not math.isfinite(determinant) or abs(determinant) < 1e-15:
        raise ValueError(f"Stabilization {name} must be invertible")
    return matrix


def proxy_to_active_matrix(analysis_size: tuple[int, int], active_size: tuple[int, int]) -> np.ndarray:
    """Exact full-picture resize pixel-center map, including even-size rounding.

    FFmpeg's centered resize maps ``x + .5`` by the width ratio. The half-pixel
    translations must be included when conjugating rotations. No canvas or
    letterbox coordinates are accepted here.
    """
    pw, ph = _size(analysis_size, 'analysis size')
    aw, ah = _size(active_size, 'active size')
    sx, sy = aw / pw, ah / ph
    return np.array([[sx, 0., (sx - 1.) / 2.],
                     [0., sy, (sy - 1.) / 2.], [0., 0., 1.]])


def _smooth(values: np.ndarray, radius: int) -> np.ndarray:
    # Truncate the symmetric box at each shot boundary. No synthetic old/new
    # frames are introduced. Float64 prefix sums keep processing O(N).
    total = np.vstack((np.zeros((1, values.shape[1])), np.cumsum(values, axis=0)))
    indices = np.arange(len(values))
    left, right = np.maximum(0, indices - radius), np.minimum(len(values), indices + radius + 1)
    return (total[right] - total[left]) / (right - left)[:, None]


def _corners(width: int, height: int) -> np.ndarray:
    return np.array([[0., 0., 1.], [width - 1., 0., 1.],
                     [0., height - 1., 1.], [width - 1., height - 1., 1.]])


def _static_crop_zoom(matrices: np.ndarray, width: int, height: int, cap: float) -> float:
    """Solve affine corner containment analytically, including bilinear support.

    All weighted bilinear taps are within the current image iff a sample is
    inside [0,w-1] x [0,h-1]. At an exact edge its outside tap has zero weight.
    Centered output zoom scales corner offsets by q=1/zoom; their inequalities
    give one upper bound on q. A common zoom avoids pumping at cuts or failures.
    """
    if np.array_equal(matrices, np.broadcast_to(_IDENTITY, matrices.shape)):
        return 1.0
    center = np.array([(width - 1.) / 2., (height - 1.) / 2., 1.])
    offsets = _corners(width, height) - center
    centers = (matrices @ center)[:, :2]
    deltas = (matrices @ offsets.T).transpose(0, 2, 1)[..., :2]
    limits = np.array([width - 1., height - 1.])
    # A centered zoom cannot recover a center already outside the current frame.
    tolerance = 1e-10
    if np.any(centers < -tolerance) or np.any(centers > limits + tolerance):
        raise ValueError("Stabilization crop exceeds max_zoom; reduce strength or use black borders")
    upper = np.full(deltas.shape, np.inf)
    np.divide(limits - centers[:, None, :], deltas, out=upper, where=deltas > 0.)
    np.divide(-centers[:, None, :], deltas, out=upper, where=deltas < 0.)
    q = min(1.0, float(np.min(upper)))
    if not math.isfinite(q) or q <= 0.0:
        raise ValueError("Stabilization crop exceeds max_zoom; reduce strength or use black borders")
    required = 1.0 / q
    if required > cap + 1e-12:
        raise ValueError(f"Stabilization crop needs {required:.6f}x, exceeding max_zoom {cap:g}x; "
                         "reduce strength or use black borders")
    # A single outward ulp prevents an edge from becoming negative through
    # matrix multiplication roundoff; never exceed the user's explicit cap.
    return min(cap, float(np.nextafter(required, math.inf))) if required > 1. else 1.


def build_stabilization_plan(
    rows: np.ndarray, settings: StabilizationSettings, *,
    analysis_size: tuple[int, int], active_size: tuple[int, int],
    cut_flags: np.ndarray | None = None, confidence: np.ndarray | None = None,
    proxy_to_active: np.ndarray | None = None, cancel_check: _CANCEL | None = None,
) -> StabilizationPlan:
    """Build correction at every source-grid frame, never per trimmed clip.

    For raw motion H_i: previous = R(-alpha) (current-c) + c + (tx,ty),
    c=(proxy_width/2,proxy_height/2). P_i=P_(i-1) H_i maps current to
    shot origin; C_i=inv(P_i) is the forward camera pose. Smooth unwrapped
    camera angle and C_i(c)-c, blend that rigid pose by strength, and use
    inverse sampler C_i inv(S_i). The exact proxy/active geometry conjugates
    this sampler before the optional inverse centered crop zoom is appended.

    A cut starts a new independent path; an invalid estimate is an identity
    motion correction and breaks both paths. The first valid frame after a
    break anchors its new path. Corrections taper to zero over at most the
    smoothing radius at both segment edges. No confidence is invented: an
    optional confidence must be positive and finite to permit a correction.
    Disabled or zero-strength settings produce exact identity without crop.
    """
    validate_stabilization_settings(settings)
    pw, ph = _size(analysis_size, 'analysis size')
    aw, ah = _size(active_size, 'active size')
    raw = np.asarray(rows, dtype=np.float64)
    if raw.ndim != 2 or raw.shape[1] != 6 or not 1 <= len(raw) <= MAX_MOTION_ROWS:
        raise ValueError(f"Stabilization requires 1..{MAX_MOTION_ROWS} six-column motion rows")
    count = len(raw)
    cuts = np.zeros(count, dtype=bool) if cut_flags is None else np.asarray(cut_flags)
    if cuts.shape != (count,) or cuts.dtype != np.bool_:
        raise ValueError("Stabilization cut flags must be one boolean per source frame")
    quality = np.ones(count) if confidence is None else np.asarray(confidence, dtype=np.float64)
    if quality.shape != (count,):
        raise ValueError("Stabilization confidence must contain one value per source frame")
    valid = (np.all(np.isfinite(raw), axis=1) & (raw[:, 0] == 0.) & (raw[:, 5] == 0.)
             & np.isfinite(quality) & (quality > 0.)
             & (np.abs(raw[:, 1]) <= pw) & (np.abs(raw[:, 2]) <= ph)
             & (np.abs(raw[:, 3]) <= math.pi))
    geometry = (proxy_to_active_matrix((pw, ph), (aw, ah)) if proxy_to_active is None
                else _affine(proxy_to_active, 'proxy to active geometry'))
    geometry_inverse = np.linalg.inv(geometry)
    matrices = np.repeat(_IDENTITY[None, :, :], count, axis=0)
    shots = np.full(count, -1, dtype=np.int32)
    zooms = np.ones(count, dtype=np.float64)
    if cancel_check:
        cancel_check()
    # Even bypass plans retain reliable segment IDs for diagnostics.
    starts: list[tuple[int, int]] = []
    index, shot_id = 0, 0
    while index < count:
        if not valid[index]:
            index += 1
            continue
        start = index
        index += 1
        while index < count and valid[index] and not cuts[index]:
            index += 1
        shots[start:index] = shot_id
        starts.append((start, index))
        shot_id += 1
    if not settings.enabled or settings.strength == 0.:
        return StabilizationPlan(matrices, valid, shots, zooms)
    center = np.array([pw / 2., ph / 2.])
    radius = settings.smoothing
    for start, stop in starts:
        if cancel_check:
            cancel_check()
        cameras = np.repeat(_IDENTITY[None, :, :], stop - start, axis=0)
        path = _IDENTITY.copy()
        for local, frame in enumerate(range(start + 1, stop), 1):
            if cancel_check and local % 128 == 0:
                cancel_check()
            tx, ty, alpha = raw[frame, 1:4]
            delta = _pose(-alpha, np.array([tx, ty]), center)
            path = path @ delta
            # Recover an exactly rigid rotation rather than accumulate scale
            # drift from long chains of otherwise rigid matrix products.
            angle = math.atan2(path[1, 0], path[0, 0])
            displacement = path[:2, :2] @ center + path[:2, 2] - center
            path = _pose(angle, displacement, center)
            cameras[local] = _rigid_inverse(path)
        angles = np.unwrap(np.arctan2(cameras[:, 1, 0], cameras[:, 0, 0]))
        displacement = (cameras[:, :2, :2] @ center) + cameras[:, :2, 2] - center
        poses = np.column_stack((angles, displacement))
        averaged = _smooth(poses, radius)
        distance = np.minimum(np.arange(stop - start), np.arange(stop - start)[::-1])
        blend = settings.strength * np.minimum(1., distance / radius)
        desired = poses + (averaged - poses) * blend[:, None]
        for local, frame in enumerate(range(start, stop)):
            if cancel_check and local % 128 == 0:
                cancel_check()
            if blend[local] == 0. or np.array_equal(desired[local], poses[local]):
                continue
            smoothed_camera = _pose(desired[local, 0], desired[local, 1:], center)
            inverse = cameras[local] @ _rigid_inverse(smoothed_camera)
            matrices[frame] = geometry @ inverse @ geometry_inverse
            matrices[frame, 2] = (0., 0., 1.)
    if not np.all(np.isfinite(matrices)):
        raise ValueError("Stabilization motion produced a nonfinite transform")
    if settings.border == 'crop':
        zoom = _static_crop_zoom(matrices, aw, ah, settings.max_zoom)
        if zoom != 1.:
            inverse_zoom = _IDENTITY.copy()
            inverse_zoom[0, 0] = inverse_zoom[1, 1] = 1. / zoom
            inverse_zoom[:2, 2] = np.array([(aw - 1.) / 2., (ah - 1.) / 2.]) * (1. - 1. / zoom)
            matrices = matrices @ inverse_zoom
            zooms[:] = zoom
    return StabilizationPlan(matrices, valid, shots, zooms)


def resample_float_rgb(
    image: np.ndarray, inverse_matrix: np.ndarray, *, border: str = 'black',
    tile_rows: int = 64, cancel_check: _CANCEL | None = None,
) -> np.ndarray:
    """Bilinearly sample current-frame float32 RGB, without range clipping.

    Scratch allocations are bounded to at most ``tile_rows`` image rows (1 to
    256); interpolation uses float64 weights and one float32 output rounding.
    A true identity returns the input unchanged, including values outside 0..1.
    Black padding is zero at each out-of-image tap; crop mode rejects uncovered
    samples rather than filling, replicating, or borrowing another frame.
    ``cancel_check`` is invoked per tile and may raise the caller's exception.
    """
    if (not isinstance(image, np.ndarray) or image.dtype != np.float32 or image.ndim != 3
            or image.shape[2] != 3 or min(image.shape[:2]) < 1):
        raise ValueError("Stabilization requires a nonempty float32 HWC RGB image")
    if border not in ('black', 'crop'):
        raise ValueError("Stabilization border must be crop or black")
    if type(tile_rows) is not int or not 1 <= tile_rows <= 256:
        raise ValueError("Stabilization tile_rows must be an integer in 1..256")
    matrix = _affine(inverse_matrix, 'inverse sampler')
    height, width = image.shape[:2]
    for start in range(0, height, tile_rows):
        if cancel_check:
            cancel_check()
        if not np.all(np.isfinite(image[start:start + tile_rows])):
            raise ValueError("Stabilization RGB samples must be finite")
    if np.array_equal(matrix, _IDENTITY):
        return image
    if border == 'crop':
        mapped = (_corners(width, height) @ matrix.T)[:, :2]
        if (np.any(mapped < -1e-9)
                or np.any(mapped > np.array([width - 1., height - 1.]) + 1e-9)):
            raise ValueError("Stabilization crop leaves uncovered pixels")
    output = np.empty_like(image)
    columns = np.arange(width, dtype=np.float64)[None, :]
    for start in range(0, height, tile_rows):
        if cancel_check:
            cancel_check()
        stop = min(start + tile_rows, height)
        row_grid = np.arange(start, stop, dtype=np.float64)[:, None]
        with np.errstate(over='ignore', invalid='ignore'):
            sx = matrix[0, 0] * columns + matrix[0, 1] * row_grid + matrix[0, 2]
            sy = matrix[1, 0] * columns + matrix[1, 1] * row_grid + matrix[1, 2]
        if not np.all(np.isfinite(sx)) or not np.all(np.isfinite(sy)):
            raise ValueError("Stabilization inverse sampler produced nonfinite coordinates")
        # Far-outside black samples can be clamped to the first fully outside
        # coordinate before integer conversion, avoiding overflow for huge shifts.
        sx = np.clip(sx, 0. if border == 'crop' else -1., width - 1. if border == 'crop' else width)
        sy = np.clip(sy, 0. if border == 'crop' else -1., height - 1. if border == 'crop' else height)
        x0, y0 = np.floor(sx).astype(np.int64), np.floor(sy).astype(np.int64)
        fx, fy = sx - x0, sy - y0
        result = np.zeros((stop - start, width, 3), dtype=np.float64)
        for dx, dy, weight in ((0, 0, (1. - fx) * (1. - fy)),
                               (1, 0, fx * (1. - fy)),
                               (0, 1, (1. - fx) * fy), (1, 1, fx * fy)):
            xx, yy = x0 + dx, y0 + dy
            if border == 'black':
                weight = weight * ((xx >= 0) & (xx < width) & (yy >= 0) & (yy < height))
            result += image[np.clip(yy, 0, height - 1), np.clip(xx, 0, width - 1)] * weight[..., None]
        output[start:stop] = result.astype(np.float32)
    return output

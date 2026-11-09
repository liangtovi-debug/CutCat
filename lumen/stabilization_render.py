"""Owned stabilization bridge; analysis proxies never become delivered pixels."""
from __future__ import annotations
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import numpy as np


@dataclass(frozen=True)
class PreparedStabilization:
    source: Path
    geometry: object
    inverse_matrices: np.ndarray
    fingerprint: str
    report: dict


def prepare_stabilization(engine, source, clip, project, cancel=None):
    from .stabilization_analysis import analyze_motion
    from .stabilization_math import build_stabilization_plan
    from .render import RenderCancelled, RenderError

    def check():
        if cancel is not None and cancel.is_set():
            raise RenderCancelled('Render cancelled.')
    check()
    try:
        analysis = analyze_motion(
            source, fps_num=project.fps_num, fps_den=project.fps_den,
            project_width=project.width, project_height=project.height,
            cache_dir=Path(engine._cache_dir.name) / 'stabilization-analysis',
            cancel=cancel, timeout=engine.timeout)
        check()
        last = (clip.timing.source_in + (clip.duration - 1) * clip.timing.speed).__floor__()
        if last >= analysis.frame_count:
            raise ValueError('Stabilization analysis ended before the selected source frame.')
        rows = np.array(analysis.raw_rows, dtype=np.float64, copy=True)
        rows[np.asarray(analysis.confidence) < .5, 5] = 1
        geometry = analysis.geometry
        key = hashlib.sha256(json.dumps({
            'analysis': analysis.cache_key, 'settings': asdict(clip.stabilization),
            'algorithm': 'owned-se2-source-wide-float-v1',
        }, sort_keys=True).encode() + rows.tobytes() + np.asarray(analysis.shot_starts).tobytes()).hexdigest()
        plans = getattr(engine, '_stabilization_plans', None)
        if plans is None:
            plans = engine._stabilization_plans = {}
        plan = plans.get(key)
        if plan is None:
            plan = build_stabilization_plan(
                rows, clip.stabilization,
                analysis_size=(geometry.proxy_width, geometry.proxy_height),
                active_size=(geometry.active_width, geometry.active_height),
                cut_flags=analysis.shot_starts, cancel_check=check)
            if len(plans) >= 8:
                plans.pop(next(iter(plans)))
            plans[key] = plan
        check()
        report = {'analysis': analysis.provenance, 'statistics': analysis.statistics,
                  'frame_count': analysis.frame_count, 'max_zoom_used': float(np.max(plan.zooms)),
                  'algorithm': 'owned-se2-source-wide-float-v1'}
        return PreparedStabilization(Path(analysis.snapshot_path), geometry,
                                     plan.inverse_matrices, key, report)
    except RenderCancelled:
        raise
    except Exception as exc:
        if cancel is not None and cancel.is_set():
            raise RenderCancelled('Render cancelled.') from exc
        raise RenderError(str(exc)) from exc


def float_picture_transform(prepared, width, height, cancel=None):
    """Warp only active picture; letterbox padding is added before creative grade."""
    from .stabilization_math import resample_float_rgb
    from .render import RenderCancelled, RenderError
    active_width, active_height = prepared.geometry.active_width, prepared.geometry.active_height
    left, top = (width - active_width) // 2, (height - active_height) // 2
    if min(left, top) < 0:
        raise RenderError('Stabilization picture geometry exceeds the project canvas.')

    def check():
        if cancel is not None and cancel.is_set():
            raise RenderCancelled('Render cancelled.')

    def transform(rgb, source_frame_index):
        check()
        if not 0 <= source_frame_index < len(prepared.inverse_matrices):
            raise RenderError('Stabilization transform index is outside the analyzed source.')
        stable = resample_float_rgb(rgb, prepared.inverse_matrices[source_frame_index],
                                    border='black', cancel_check=check)
        if (active_width, active_height) == (width, height):
            return stable
        canvas = np.zeros((height, width, 3), dtype=np.float32)
        canvas[top:top + active_height, left:left + active_width] = stable
        return canvas
    return transform

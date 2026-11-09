"""Display-only contextual range inspection; never used by grading or sampling."""
import numpy as np


def selection_overlay(frame, matte):
    """Tint selected source pixels magenta at 50% times actual range weight.

    Display clipping precedes the tint. The float source and matte are immutable
    inputs. Only bounded overview/detail grids are accepted, not export frames.
    """
    if (not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3
            or not 0 < frame.shape[0] <= 512 or not 0 < frame.shape[1] <= 512
            or not isinstance(matte, np.ndarray) or matte.shape != frame.shape[:2]):
        raise ValueError('Selection overlay requires an RGB viewport and matching matte, at most 512×512')
    if not np.isfinite(frame).all() or not np.isfinite(matte).all():
        raise ValueError('Selection overlay values must be finite')
    if np.any(matte < 0) or np.any(matte > 1):
        raise ValueError('Selection overlay matte must be within 0..1')
    alpha = np.asarray(matte, dtype=np.float32)[..., None] * np.float32(.5)
    source = np.clip(frame, 0, 1).astype(np.float32)
    return source * (np.float32(1) - alpha) + np.array([1, 0, 1], np.float32) * alpha

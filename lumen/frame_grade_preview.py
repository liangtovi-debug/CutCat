"""Faithful current-frame selected-clip grading, not a final timeline preview.

This stage includes source input normalization, active-picture restoration,
white balance, primary/RGB/selective curves, the spatial qualifier, and the LUT.
It is before effects, clip geometry, opacity, transitions and compositing. No
proxy, display raster or encoded export is used as the color source. Active
stabilization and sources with alpha are rejected rather than approximated.
"""
from __future__ import annotations

from .color_picker_source import (
    PickerSnapshot as GradePreviewSnapshot,
    PickerSourceError as GradePreviewError,
    PickerSourceCancelled as GradePreviewCancelled,
    _capture_snapshot,
)


def capture_grade_preview_snapshot(project, clip_id, timeline_frame,
                                   cancel=None, timeout=30) -> GradePreviewSnapshot:
    """Return one immutable float32 HWC frame after the complete clip grade.

    Resolution is the native fitted/padded project canvas within the source
    pipeline's pixel budget; never downsample before evaluating a spatial key.
    The total deadline covers decode, normalization, restoration, grading and
    identity checks. Cancellation is checked at processing boundaries and in
    restoration/qualifier tiles. Out-of-range working values are retained.
    Revalidate is_current(project, clip_id, timeline_frame) before display.
    """
    return _capture_snapshot(project, clip_id, timeline_frame, cancel, timeout,
                             stage='grade_preview')

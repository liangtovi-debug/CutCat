"""Pure clip-local window geometry evaluation and edit-time rebasing.

Animation is measured in local project/timeline frames, independently of source
speed. Outside the first/last key the geometry is held. Keys outside the visible
clip span remain interpolation handles, including negative frames after trims.
No function mutates its input or discards hidden keys.
"""
from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from typing import TYPE_CHECKING

from .qualifier import (MAX_WINDOW_FRAME, MAX_WINDOW_KEYFRAMES, QualifierSettings,
                        WindowKeyframe, validate_qualifier)

if TYPE_CHECKING:
    from .model import Grade


__all__ = ['WindowKeyframe', 'MAX_WINDOW_FRAME', 'MAX_WINDOW_KEYFRAMES',
           'evaluate_window', 'evaluate_grade', 'shift_window_animation']


def evaluate_window(settings: QualifierSettings, frame: int | Fraction) -> QualifierSettings:
    """Return evaluated geometry with no keys, without changing any grade state.

    The no-key static fast path returns the original object, borrowed read-only,
    preserving historical static processing exactly. Animated results own their
    mutable containers and keep all non-geometric settings unchanged.
    """
    if type(frame) is not int and not isinstance(frame, Fraction):
        raise ValueError('Window evaluation frame must be an integer or Fraction')
    validate_qualifier(settings)
    keys = settings.window_keyframes
    if not keys:
        return settings
    index = bisect_right([key.frame for key in keys], frame) - 1
    left = keys[max(0, index)]
    values = {name: getattr(left, name) for name in ('center_x', 'center_y', 'width', 'height')}
    if 0 <= index < len(keys) - 1 and frame != left.frame and left.interpolation == 'linear':
        right = keys[index + 1]
        # The local time remains exact until the final float geometry blend.
        weight = float(Fraction(frame - left.frame) / (right.frame - left.frame))
        # A Fraction arbitrarily near an endpoint may round to weight 0/1.
        # Bound roundoff to the segment, notably 2 -> .001 widths where the
        # subtractive blend can otherwise fall just below the valid minimum.
        values = {name: min(max(value, getattr(right, name)),
                            max(min(value, getattr(right, name)),
                                value + (getattr(right, name) - value) * weight))
                  for name, value in values.items()}
    return replace(settings, window_keyframes=[],
                   **{f'window_{name}': value for name, value in values.items()})


def evaluate_grade(grade: Grade, frame: int | Fraction) -> Grade:
    """Borrow a static grade or replace only its evaluated qualifier.

    Like the existing renderer, callers must treat the grade's other shared
    fields as read-only. Importing Grade locally avoids a model import cycle.
    """
    from .model import Grade
    if not isinstance(grade, Grade):
        raise ValueError('Window evaluation requires a grade')
    settings = evaluate_window(grade.qualifier, frame)
    return grade if settings is grade.qualifier else replace(grade, qualifier=settings)


def shift_window_animation(grade: Grade, delta: int) -> Grade:
    """Copy a grade and subtract a timeline-left-edge delta from every key.

    Do not scale by source speed, clip to a duration or synthesize endpoint keys:
    keeping all signed handles preserves exact hold/linear interpolation on both
    sides of splits, trims and rolls. Overflow rejects the surrounding edit.
    """
    from .model import Grade
    if not isinstance(grade, Grade):
        raise ValueError('Window rebasing requires a grade')
    if type(delta) is not int:
        raise ValueError('Window animation delta must be an integer')
    validate_qualifier(grade.qualifier)
    shifted = [replace(key, frame=key.frame - delta)
               for key in grade.qualifier.window_keyframes]
    result = deepcopy(grade)
    result.qualifier.window_keyframes = shifted
    return result

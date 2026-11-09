"""Trusted bounded local-project-frame expressions for main-picture animation.

Balanced interval trees bound parser nesting at 128 keys. No user expression,
source timestamp, wall clock, or speed is admitted. The renderer runs these
expressions after the exact retime cache and before adding clip.start to PTS.
"""
from __future__ import annotations

from .clip_motion import validate_motion_keyframes

MOTION_ALGORITHM = 'local-project-N-straight-geometric-motion-frame-cache-v2'


def motion_expression(keys, field):
    validate_motion_keyframes(keys)
    if not keys or field not in ('x', 'y', 'scale', 'opacity'):
        raise ValueError('Motion expression requires keys and a supported field')
    def number(value):
        return format(value, '.17g')
    def segment(index):
        left = keys[index]
        value = getattr(left, field)
        if index == len(keys) - 1 or left.interpolation == 'hold':
            return number(value)
        right = keys[index + 1]
        end = getattr(right, field)
        if value == end:
            return number(value)
        # Subtract time before float division, not slope*N + intercept.
        # Exact right endpoints enter the next interval, never this blend.
        return (f'clip({number(value)}+({number(end)}-{number(value)})*'
                f'((N-({left.frame}))/({right.frame}-{left.frame})),'
                f'{number(min(value,end))},{number(max(value,end))})')
    def tree(low, high):
        if low == high:
            return segment(low)
        middle = (low + high + 1) // 2
        return f'if(lt(N,{keys[middle].frame}),{tree(low,middle-1)},{tree(middle,high)})'
    return f'if(lt(N,{keys[0].frame}),{number(getattr(keys[0],field))},{tree(0,len(keys)-1)})'

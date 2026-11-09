"""Numeric-only radial vignette shape shared by the RGB8 and RGB16 paths.

The legacy shape is deliberately handled by each caller's original filter:
opting into shape controls must not alter existing projects or default output.
The new falloff operates on encoded RGB, not scene-linear light or an oval mask.
"""
from __future__ import annotations

import math


def validated_vignette_shape(effect):
    """Validate even bypassed entries; blur cannot carry unused shape settings."""
    radius = getattr(effect, 'radius', 1.0)
    feather = getattr(effect, 'feather', 1.0)
    for value in (radius, feather):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not .25 <= value <= 2 or not math.isfinite(value)):
            raise ValueError('Invalid clip effect radius or feather; expected 0.25 to 2.')
    if effect.kind == 'blur' and (radius != 1 or feather != 1):
        raise ValueError('Invalid clip effect: blur radius and feather must be 1.')
    return float(radius), float(feather)


def has_active_shaped_vignette(effects):
    """Select the opt-in RGB8 compositor branch after project validation."""
    return any(effect.enabled and effect.kind == 'vignette' and effect.amount > 0
               and validated_vignette_shape(effect) != (1, 1) for effect in effects)


def shaped_vignette_filter(amount, radius, feather):
    """Evaluate normalized geometry for the current canvas, never cached sizes.

    The callers validate the numeric domain. Integer offsets match the legacy
    optical geometry, including odd and one-pixel dimensions. geq truncates
    positive samples at each integer output boundary; nearest sampling keeps
    the final row and column intact. Clamping prevents cosine wraparound when
    radius is small. No user-authored expression enters this fixed template.
    """
    angle = format(float(amount) * math.pi / 2, '.17g')
    radius = format(float(radius), '.17g')
    feather = format(float(feather), '.17g')
    distance = 'hypot(trunc(X-W/2),trunc(Y-H/2))/hypot(W/2,H/2)'
    q = f'min(1,pow(({distance})/{radius},1/{feather}))'
    gain = f'pow(cos({angle}*{q}),4)'
    return 'geq=' + ':'.join(f"{c}='{c}(X,Y)*{gain}'" for c in 'rgb') + ':interpolation=nearest'

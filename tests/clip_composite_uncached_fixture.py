"""Frozen pre-cache clip_composite_filters reference; synthetic acceptance only.

Original file SHA256: fc9699e0c9300122a6dffaa911c8a8abf7b719c9d20b0fffa729e89fbc04e403
Exact function UTF-8 SHA256: 4ac5dc8db601ef5de5d38443524ad23d5a9a08349f993ebbc552e65431597c5a
Keep this function unchanged to retain independent regression coverage.
"""
from __future__ import annotations
import math
from lumen.clip_composite import ClipComposite, validate_composite, _CONTEXT
from lumen.clip_transform import ClipTransform, validate_transform

def clip_composite_filters(settings: ClipComposite, transform: ClipTransform,
                           width: int, height: int, *, high_bit: bool, motion_keys=None) -> tuple[str, ...]:
    """Return (underlay, RGB16 foreground, source-over) typed filter chains.

    Inputs: yuv422p10le for 10-bit or yuv420p for 8-bit underlay, and gbrp16le
    foreground *before* clip geometry. Output matches the underlay format.
    The default returns (), so callers keep their legacy transform/overlay path.
    This pure builder does not establish support: callers must first call
    verify_clip_composite_support for the chosen output depth each export.

    Geometry is the clip_transform.py inverse pixel-center mapping, including
    its exact quarter-turn normalization and hard center-sampled outer bounds.
    A one-pixel guard protects geq's bilinear last-row/column sampling. Straight
    RGB color remains unmultiplied; only alpha is multiplied by clip opacity.
    Underlay nearest expansion then 4:4:4 composition and area reduction preserve
    untouched YUV samples exactly, with a discarded pair-aligned scaler guard.
    """
    validate_composite(settings)
    validate_transform(transform)
    if any(type(v) is not int or not 1 <= v <= 16384 for v in (width, height)):
        raise ValueError('Composite canvas dimensions must be integers in 1..16384')
    if type(high_bit) is not bool:
        raise ValueError('Composite high_bit must be a boolean')
    if width % 2 or (not high_bit and height % 2):
        raise ValueError('Composite canvas must be even for its output chroma subsampling')
    if motion_keys is not None:
        from .clip_motion import validate_motion_keyframes
        validate_motion_keyframes(motion_keys)
    if settings.is_legacy() and not motion_keys:
        return ()
    angle = math.radians(transform.rotation)
    cosine, sine = math.cos(angle), math.sin(angle)
    cosine = 0.0 if abs(cosine) < 1e-12 else cosine
    sine = 0.0 if abs(sine) < 1e-12 else sine
    mx = (-1 if transform.mirror_x else 1) / transform.scale
    my = (-1 if transform.mirror_y else 1) / transform.scale
    cx, cy = (width - 1) / 2, (height - 1) / 2
    a, b, c, d = mx * cosine, mx * sine, -my * sine, my * cosine
    tx = cx - a * (cx + transform.x) - b * (cy + transform.y)
    ty = cy - c * (cx + transform.x) - d * (cy + transform.y)
    def number(value: float) -> str:
        return format(value, '.15g')
    sx = f'({number(a)}*X+{number(b)}*Y+{number(tx)})'
    sy = f'({number(c)}*X+{number(d)}*Y+{number(ty)})'
    setup = ''
    if motion_keys:
        from .clip_motion_render import motion_expression
        # Registers are independent for each geq plane. Evaluate all four
        # parameters together before the same inverse RGB/coverage mapping.
        setup = ''.join(f'st({i},{motion_expression(motion_keys, name)});'
                        for i, name in enumerate(('x', 'y', 'scale', 'opacity'), 2))
        sign_x, sign_y = (-1 if transform.mirror_x else 1), (-1 if transform.mirror_y else 1)
        dx, dy = f'(X-{number(cx)}-ld(2))', f'(Y-{number(cy)}-ld(3))'
        sx = f'(({number(cosine)}*{dx}+{number(sine)}*{dy})*{sign_x}/ld(4)+{number(cx)})'
        sy = f'(({number(-sine)}*{dx}+{number(cosine)}*{dy})*{sign_y}/ld(4)+{number(cy)})'
    coordinates = setup + f'st(0,{sx});st(1,{sy});'
    inside = f'between(ld(0),0,{width - 1})*between(ld(1),0,{height - 1})'
    expressions = [f"{plane}='{coordinates}if({inside},{plane}(ld(0),ld(1)),0)'"
                   for plane in ('r', 'g', 'b')]
    alpha = '65535*ld(5)' if motion_keys else number(65535 * settings.opacity)
    if settings.transparent_outside_transform:
        alpha = f'{coordinates}{alpha}*{inside}'
    elif motion_keys:
        alpha = setup + alpha
    expressions.append(f"a='{alpha}'")
    picture_format, full_format, alpha_format, overlay_format = (
        ('yuv422p10le', 'yuv444p10le', 'yuva444p10le', 'yuv444p10') if high_bit else
        ('yuv420p', 'yuv444p', 'yuva444p', 'yuv444'))
    main = (f'format={picture_format},' + _CONTEXT +
            ',scale=flags=neighbor:in_color_matrix=bt709:out_color_matrix=bt709:'
            f'in_range=tv:out_range=tv,format={full_format},' + _CONTEXT)
    foreground = (f'format=gbrp16le,scale=flags=neighbor:in_range=full:out_range=full,'
                  f'format=gbrap16le,pad={width + 1}:{height + 1}:0:0:color=black,'
                  'geq=' + ':'.join(expressions) + ':interpolation=bilinear,'
                  f'crop={width}:{height}:0:0,'
                  'zscale=matrixin=gbr:rangein=full:matrix=709:range=limited:dither=none,'
                  f'format={alpha_format},' + _CONTEXT)
    # Height guard is also pair-aligned for 4:2:0. It is not needed for 4:2:2.
    pad_height, crop_height = ('ih', 'ih') if high_bit else ('ih+2', 'ih-2')
    composite = (f'overlay=format={overlay_format}:eof_action=pass:repeatlast=0:'
                 f'shortest=0:alpha=straight,pad=iw+2:{pad_height}:0:0:color=black,'
                 'scale=flags=area:in_color_matrix=bt709:out_color_matrix=bt709:'
                 f'in_range=tv:out_range=tv,format={picture_format},'
                 f'crop=iw-2:{crop_height}:0:0,' + _CONTEXT)
    return main, foreground, composite

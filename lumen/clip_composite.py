"""Static SDR clip source-over with explicit geometric coverage and precision.

Both inputs have already been fitted/padded to the project canvas. The picture
source is straight RGB16, including opaque fit/pad black bars. Geometry produces
straight alpha from inverse source coordinates, never from source brightness.
Mixing uses gamma-coded Rec.709 YUV 4:4:4; this is not linear-light/HDR blending.
The legacy opaque/default route is deliberately absent from this helper.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import math
from typing import Any

from .clip_transform import ClipTransform, validate_transform


@dataclass
class ClipComposite:
    transparent_outside_transform: bool = False
    opacity: float = 1.0

    def __post_init__(self) -> None:
        validate_composite(self)

    def to_dict(self) -> dict[str, Any]:
        validate_composite(self)
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClipComposite:
        if not isinstance(data, dict) or set(data) != {f.name for f in fields(cls)}:
            raise ValueError('Composite requires exactly transparent_outside_transform and opacity')
        return cls(**data)

    def is_legacy(self) -> bool:
        validate_composite(self)
        return not self.transparent_outside_transform and self.opacity == 1


def validate_composite(settings: ClipComposite) -> None:
    if not isinstance(settings, ClipComposite):
        raise ValueError('Invalid clip composite')
    if type(settings.transparent_outside_transform) is not bool:
        raise ValueError('Composite transparent_outside_transform must be a boolean')
    value = settings.opacity
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 1):
        raise ValueError('Composite opacity must be finite and in 0..1')


COMPOSITE_ALGORITHM = 'static-geometric-straight-rec709-444-v2'

_CONTEXT = 'setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709'


def clip_composite_filters(settings: ClipComposite, transform: ClipTransform,
                           width: int, height: int, *, high_bit: bool, motion_keys=None,
                           motion_frame_offset=0) -> tuple[str, ...]:
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
    if type(motion_frame_offset) is not int or motion_frame_offset < 0:
        raise ValueError('Composite motion frame offset must be a nonnegative integer')
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
        if motion_frame_offset:
            # Only the generated motion clock changes; the per-plane register
            # cache below still keys off each emitted local frame N.
            setup = setup.replace('N', f'(N+{motion_frame_offset})')
        # geq keeps independent expression registers for each plane/slice.
        # These four values depend only on its local frame N, not X/Y. Cache
        # them without changing any interpolation or inverse-map arithmetic.
        # N starts at zero; the N+1 marker cannot match the zero-initialized
        # register on the first pixel, including slices that start below Y=0.
        setup = f'if(eq(ld(9),N+1),0,{setup}st(9,N+1));'
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
    # Pad RGB before adding alpha: FFmpeg 9 can corrupt RGB when padding
    # the planar 16-bit RGBA conversion. Keep the guard for geq edge sampling.
    # Its RGB16-to-RGBA scale conversion also needs one thread for exact pixels;
    # geq and the rest of the graph retain the requested filter thread budget.
    foreground = (f'format=gbrp16le,pad={width + 1}:{height + 1}:0:0:color=black,'
                  'scale=flags=neighbor:in_range=full:out_range=full:threads=1,format=gbrap16le,'
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


CLIP_COMPOSITE_ERROR = (
    'This FFmpeg build cannot composite transformed clips with verified SDR precision. '
    'Install a compatible FFmpeg build with zscale and native full-chroma alpha support, '
    'or reset clip opacity and disable transparent transformed edges.')
# Transparent identity, half-alpha geometry, both end pairs, precision source,
# fully clipped geometry, and fading an intentionally opaque black exterior.
_PROBE_CASES = ((0, 1, 0, True), (0, .5, .5, True),
                (-63, 1, 1, True), (63, 1, 1, True), (0, 1, 1, True),
                (16384, 1, 1, True), (0, .5, .5, False))


def _probe_values(high_bit):
    maximum = 1024 if high_bit else 256
    csize = 2048 if high_bit else 1024
    planes = ([i % maximum for i in range(4096)],
              [(i * 113) % maximum for i in range(csize)],
              [(i * 173) % maximum for i in range(csize)])
    rgb = []
    for y in range(64):
        for x in range(64):
            if y < 16:
                value = round((y * 64 + x) * 65535 / 1023)
                color = (value, value, value)
            elif y < 32:
                value = 32778 if x < 32 else 32958
                color = (value, value, value)
            elif y < 48:
                color = (65535, 0, 0)
            elif y < 56:
                color = (0, 0, 65535)
            elif y < 60:
                color = (0, 0, 0)
            else:
                color = (65535, 65535, 65535)
            rgb.append(color)
    return planes, rgb


def _probe_inputs(high_bit):
    import struct
    planes, rgb = _probe_values(high_bit)
    picture = [sample for plane in planes for sample in plane]
    picture_bytes = (struct.pack(f'<{len(picture)}H', *picture) if high_bit else bytes(picture))
    foreground = [pixel[channel] for channel in (1, 2, 0) for pixel in rgb]
    return picture_bytes, struct.pack('<12288H', *foreground)


def _valid_probe(data, high_bit, cases=None):
    """Analytic fixed-fixture oracle, independent of emitted geq expressions."""
    import struct
    cases = _PROBE_CASES if cases is None else cases
    picture, rgb = _probe_values(high_bit)
    count = sum(len(plane) for plane in picture)
    frame_bytes = count * (2 if high_bit else 1)
    if len(data) != frame_bytes * len(cases):
        return False
    samples = struct.unpack(f'<{count * len(cases)}H', data) if high_bit else data
    maximum = 1023 if high_bit else 255
    if max(samples) > maximum:
        return False
    factor = 4 if high_bit else 1
    for frame, (shift, scale, opacity, transparent) in enumerate(cases):
        actual = samples[frame * count:(frame + 1) * count]
        foreground, alpha = [], []
        for y in range(64):
            for x in range(64):
                sx = (x - 31.5 - shift) / scale + 31.5
                sy = (y - 31.5) / scale + 31.5
                inside = 0 <= sx <= 63 and 0 <= sy <= 63
                a = opacity if inside or not transparent else 0
                alpha.append(a)
                color = [0., 0., 0.]
                if inside:
                    x0, y0 = int(sx), int(sy)
                    x1, y1 = min(x0 + 1, 63), min(y0 + 1, 63)
                    fx, fy = sx - x0, sy - y0
                    for weight, yy, xx in (((1 - fx) * (1 - fy), y0, x0),
                                           (fx * (1 - fy), y0, x1),
                                           ((1 - fx) * fy, y1, x0), (fx * fy, y1, x1)):
                        for channel in range(3):
                            color[channel] += weight * rgb[yy * 64 + xx][channel] / 65535
                r, g, b = color
                luma = .2126 * r + .7152 * g + .0722 * b
                foreground.append((factor * (16 + 219 * luma),
                                   factor * (128 + 224 * (b - luma) / 1.8556),
                                   factor * (128 + 224 * (r - luma) / 1.5748)))
        offset = 0
        for plane, original in enumerate(picture):
            rows = 64 if plane == 0 or high_bit else 32
            cols = 64 if plane == 0 else 32
            for y in range(rows):
                for x in range(cols):
                    i = y * cols + x
                    covered = ([y * 64 + x] if plane == 0 else
                               [y * 64 + x * 2, y * 64 + x * 2 + 1] if high_bit else
                               [y * 128 + x * 2, y * 128 + x * 2 + 1,
                                y * 128 + 64 + x * 2, y * 128 + 64 + x * 2 + 1])
                    if not any(alpha[j] for j in covered):
                        if actual[offset + i] != original[i]:
                            return False
                    else:
                        expected = sum(original[i] * (1 - alpha[j]) + foreground[j][plane] * alpha[j]
                                       for j in covered) / len(covered)
                        if abs(actual[offset + i] - expected) > 2:
                            return False
            offset += len(original)
        if high_bit and frame == 4:
            # A 10-bit tag and many dithered values alone are insufficient.
            # This mean error plus two same-8-bit flat patches reject reduction.
            ramp = actual[:1024]
            reference = [64 + 876 * rgb[i][0] / 65535 for i in range(1024)]
            if (len(set(ramp)) < 875 or sum(abs(a - b) for a, b in zip(ramp, reference)) / 1024 >= .3
                    or actual[20 * 64 + 48] - actual[20 * 64 + 16] < 2):
                return False
    return True


def verify_clip_composite_support(high_bit: bool, timeout: float, cancel=None,
                                  error_type=RuntimeError, cancelled_type=RuntimeError, *, _motion=False, filter_complex_threads=1) -> None:
    """Repeat a bounded synthetic behavior probe for each requested export.

    No user media is read. Automatic converters are disabled; both RGB source
    precision and exact all-code transparent-underlay identity are checked.
    Failure never silently falls back to an opaque or eight-bit intermediate.
    Child processes are reaped and all owned files removed on every exit path.
    """
    import os
    from pathlib import Path
    import subprocess
    import tempfile
    import time
    from .media import ffmpeg_binary

    if type(high_bit) is not bool:
        raise ValueError('Composite high_bit must be a boolean')
    if type(filter_complex_threads) is not int or filter_complex_threads not in (1, 2, 4):
        raise ValueError('Composite filter threads must be 1, 2, or 4')
    if cancel is not None and cancel.is_set():
        raise cancelled_type('Render cancelled.')
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout < 0):
        raise ValueError('Composite probe timeout must be finite and nonnegative')
    process = None
    began = time.monotonic()
    picture, foreground = _probe_inputs(high_bit)
    cases = ((-4, .5, 0, True), (0, .75, .5, True), (4, 1, 1, True),
             (4, 1, 1, True), (0, 1, 1, True), (16384, 1, 1, True), (0, .5, .5, True)) if _motion else _PROBE_CASES
    count = len(cases)
    expected_size = len(picture) * count
    pixel_format = 'yuv422p10le' if high_bit else 'yuv420p'
    with tempfile.TemporaryDirectory(prefix='lumen-clip-composite-probe-') as directory, \
            tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output, \
            tempfile.TemporaryFile() as log:
        try:
            source.write(picture * (count if _motion else 1))
            source.seek(0)
            layer_path = Path(directory) / 'source.gbrp16le'
            layer_path.write_bytes(foreground * (count if _motion else 1))
            graph = [f'[0:v]split={count}' + ''.join(f'[m{i}]' for i in range(count)),
                     f'[1:v]split={count}' + ''.join(f'[f{i}]' for i in range(count))]
            for i, (shift, scale, opacity, transparent) in enumerate(_PROBE_CASES):
                main, layer, overlay = clip_composite_filters(
                    ClipComposite(transparent, opacity), ClipTransform(x=shift, scale=scale),
                    64, 64, high_bit=high_bit)
                graph.extend([f'[m{i}]{main}[main{i}]', f'[f{i}]{layer}[layer{i}]',
                              f'[main{i}][layer{i}]{overlay}[out{i}]'])
            graph.append(''.join(f'[out{i}]' for i in range(count)) + f'concat=n={count}:v=1:a=0[out]')
            if _motion:
                from .clip_motion import ClipMotionKeyframe
                keys = [ClipMotionKeyframe(frame=i, x=cases[i][0], scale=cases[i][1],
                                           opacity=cases[i][2], interpolation='linear' if i == 0 else 'hold')
                        for i in (0, 2, 4, 5, 6)]
                main, layer, overlay = clip_composite_filters(ClipComposite(True), ClipTransform(),
                                                              64, 64, high_bit=high_bit, motion_keys=keys)
                graph = [f'[0:v]setpts=PTS+7,{main}[main]',
                         f'[1:v]setpts=PTS+7,{layer}[layer]', f'[main][layer]{overlay}[out]']
            command = [ffmpeg_binary(), '-hide_banner', '-v', 'error', '-nostdin',
                       '-noauto_conversion_filters', '-filter_complex_threads', str(filter_complex_threads),
                       '-f', 'rawvideo', '-pix_fmt', pixel_format, '-s', '64x64', '-r', '30000/1001' if _motion else '1',
                       '-protocol_whitelist', 'file,pipe', '-i', 'pipe:0',
                       '-f', 'rawvideo', '-pix_fmt', 'gbrp16le', '-s', '64x64', '-r', '30000/1001' if _motion else '1',
                       '-protocol_whitelist', 'file,pipe', '-i', str(layer_path),
                       '-filter_complex', ';'.join(graph), '-map', '[out]', '-frames:v', str(count),
                       '-an', '-c:v', 'rawvideo', '-threads', '1', '-pix_fmt', pixel_format,
                       '-fps_mode', 'passthrough', '-f', 'rawvideo', 'pipe:1']
            if cancel is not None and cancel.is_set():
                raise cancelled_type('Render cancelled.')
            process = subprocess.Popen(command, stdin=source, stdout=output, stderr=log)
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    raise cancelled_type('Render cancelled.')
                if time.monotonic() - began > min(timeout, 30):
                    raise error_type('Render timed out checking clip compositing support.')
                if os.fstat(output.fileno()).st_size > expected_size or os.fstat(log.fileno()).st_size > 65536:
                    raise error_type('Clip compositing capability probe exceeded its output budget.')
                time.sleep(.025)
            if cancel is not None and cancel.is_set():
                raise cancelled_type('Render cancelled.')
            if os.fstat(output.fileno()).st_size > expected_size or os.fstat(log.fileno()).st_size > 65536:
                raise error_type('Clip compositing capability probe exceeded its output budget.')
            output.seek(0)
            payload = output.read(expected_size + 1)
            if process.returncode or not _valid_probe(payload, high_bit, cases):
                failure = error_type(CLIP_COMPOSITE_ERROR)
                reason = 'process exit' if process.returncode else 'pixel oracle mismatch'
                failure.add_note(f'Composite probe: {reason}; returncode={process.returncode}; '
                                 f'bytes={len(payload)}/{expected_size}; high_bit={high_bit}; '
                                 f'motion={_motion}; filter_threads={filter_complex_threads}')
                log.seek(0)
                diagnostic = log.read(4096).decode('utf-8', errors='replace').strip()
                if diagnostic:
                    failure.add_note('FFmpeg stderr (first 4096 bytes): ' + diagnostic)
                raise failure
        except OSError as exc:
            raise error_type(CLIP_COMPOSITE_ERROR) from exc
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()


COMPOSITE_RGB8_BLUR_ERROR = (
    'This FFmpeg build cannot apply verified Gaussian blur to 8-bit picture-in-picture clips. '
    'Install a compatible FFmpeg build, disable clip blur, or reset clip compositing.')


def _rgb8_blur_input(width, height, sigma):
    if width == 2:
        return bytes([100]) * (width * height * 3)
    # Two untouched constant planes prove channel/level preservation. A point
    # impulse detects the sigma=2 operator; a finite block impulse remains
    # measurable after sigma=30 even at eight-bit output precision.
    red = bytearray([32]) * 4096
    if sigma == 2:
        red[32 * 64 + 32] = 232
    else:
        for y in range(24, 40):
            red[y * 64 + 24:y * 64 + 40] = bytes([232]) * 16
    return bytes([100]) * 4096 + bytes([73]) * 4096 + red


def _valid_rgb8_blur(data, original, width, sigma):
    if len(data) != len(original):
        return False
    if width == 2:
        return data == original
    if data[:4096] != bytes([100]) * 4096 or data[4096:8192] != bytes([73]) * 4096:
        return False
    red = data[8192:]
    center, left, right = (red[32 * 64 + x] for x in (32, 31, 33))
    if sigma == 2:
        # Three diffusion steps have a normalized discrete exponential impulse
        # response. These integer intervals cover rounding, not a no-op, flat
        # fill or mere darkening. Independent convolution tests cover the field.
        return (43 <= center <= 45 and 40 <= left <= 42 and 40 <= right <= 42
                and abs(left - right) <= 1 and min(red) == 32 and max(red) == center
                and all(red[y * 64 + x] == 32 for y, x in ((0, 0), (0, 63), (63, 0), (63, 63)))
                and 170 <= sum(red) - 32 * 4096 <= 230)
    # At sigma 30 the finite-frame recursive boundary continuation matters.
    # Probe a broad impulse, not a point which rounds away entirely in RGB8.
    # Bounds include scalar floating rounding and require actual spatial work,
    # channel preservation and a non-flat field, not a universal Gaussian oracle.
    return (44 <= center <= 48 and 34 <= red[0] <= 38 and 39 <= red[-1] <= 43
            and center - red[0] >= 6 and 38 <= red[32 * 64] <= 42
            and 41 <= red[32 * 64 + 63] <= 45 and 33 <= min(red) <= 38
            and max(red) <= 49 and 168000 <= sum(red) <= 176000)


def ensure_composite_rgb8_blur_support(timeout: float, cancel=None,
                                      error_type=RuntimeError, cancelled_type=RuntimeError) -> None:
    """Verify the scalar RGB8 effect route only when a caller needs PIP blur.

    Known optimized gblur paths corrupt narrow RGB frames at either bit depth.
    The matching final render must also use ``-cpuflags 0`` and pin its legacy
    eight-bit effects to gbrp. This gate runs each export, never caches support,
    never substitutes a no-op, and does not apply to unchanged legacy graphs.
    Three synthetic probes share one bounded wall-time budget; no user media.
    """
    import os
    import subprocess
    import tempfile
    import time
    from .media import ffmpeg_binary

    if cancel is not None and cancel.is_set():
        raise cancelled_type('Render cancelled.')
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout < 0):
        raise ValueError('Composite blur probe timeout must be finite and nonnegative')
    began = time.monotonic()
    for width, height, sigma in ((2, 48, 30), (64, 64, 2), (64, 64, 30)):
        original = _rgb8_blur_input(width, height, sigma)
        command = [ffmpeg_binary(), '-hide_banner', '-v', 'error', '-nostdin',
                   '-cpuflags', '0', '-filter_threads', '1', '-noauto_conversion_filters',
                   '-f', 'rawvideo', '-pix_fmt', 'gbrp', '-s', f'{width}x{height}', '-r', '1',
                   '-protocol_whitelist', 'file,pipe', '-i', 'pipe:0',
                   '-vf', f'gblur=sigma={sigma}:steps=3,format=gbrp', '-frames:v', '1',
                   '-an', '-c:v', 'rawvideo', '-threads', '1', '-pix_fmt', 'gbrp',
                   '-f', 'rawvideo', 'pipe:1']
        process = None
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output, \
                tempfile.TemporaryFile() as log:
            try:
                source.write(original)
                source.seek(0)
                if cancel is not None and cancel.is_set():
                    raise cancelled_type('Render cancelled.')
                process = subprocess.Popen(command, stdin=source, stdout=output, stderr=log)
                while process.poll() is None:
                    if cancel is not None and cancel.is_set():
                        raise cancelled_type('Render cancelled.')
                    if time.monotonic() - began > min(timeout, 30):
                        raise error_type('Render timed out checking picture-in-picture blur support.')
                    if os.fstat(output.fileno()).st_size > len(original) or os.fstat(log.fileno()).st_size > 65536:
                        raise error_type('Picture-in-picture blur capability probe exceeded its output budget.')
                    time.sleep(.025)
                if cancel is not None and cancel.is_set():
                    raise cancelled_type('Render cancelled.')
                if time.monotonic() - began > min(timeout, 30):
                    raise error_type('Render timed out checking picture-in-picture blur support.')
                if os.fstat(output.fileno()).st_size > len(original) or os.fstat(log.fileno()).st_size > 65536:
                    raise error_type('Picture-in-picture blur capability probe exceeded its output budget.')
                output.seek(0)
                if process.returncode or not _valid_rgb8_blur(output.read(len(original) + 1), original, width, sigma):
                    raise error_type(COMPOSITE_RGB8_BLUR_ERROR)
            except OSError as exc:
                raise error_type(COMPOSITE_RGB8_BLUR_ERROR) from exc
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait()


CLIP_MOTION_ERROR = (
    'This FFmpeg build cannot animate pictures with verified SDR precision. '
    'Install a compatible FFmpeg build, or convert picture animation to static '
    'and reset clip compositing. No static-frame or low-precision fallback was used.')


def verify_clip_motion_support(high_bit, timeout, cancel=None, error_type=RuntimeError, cancelled_type=RuntimeError, *,
                               filter_complex_threads=1):
    """Require varying N-dependent geometry/alpha, not merely static support."""
    try:
        return verify_clip_composite_support(high_bit, timeout, cancel, error_type, cancelled_type, _motion=True,
                                             filter_complex_threads=filter_complex_threads)
    except error_type as exc:
        if str(exc) == CLIP_COMPOSITE_ERROR:
            raise error_type(CLIP_MOTION_ERROR) from exc
        raise

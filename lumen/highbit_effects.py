"""Bounded native RGB16 effects for SDR delivery, never implicit 8-bit effects.

Gaussian blur and radial vignette operate on gamma-coded RGB, not linear light.
Vignette follows the legacy radial geometry, not its 8-bit/YUV rounding.
Zero amounts and disabled entries bypass exactly. Effects preserve list order.
No capability result or generated media is cached: each export probes its binary.
FFmpeg CPU dispatch is disabled for active high-bit blur exports: known optimized
gblur implementations corrupt narrow RGB16 frames even with one filter thread.
"""
from __future__ import annotations

import os
import math
import struct
import subprocess
import tempfile
import time

from .media import ffmpeg_binary
from .overlay_render import effect_filters, OverlayRenderError
from .vignette_shape import shaped_vignette_filter, validated_vignette_shape

UNSUPPORTED_EFFECT_ERROR = ('ProRes 10-bit SDR export supports Gaussian blur and radial vignette only. '
                            'Other unverified clip effects are blocked. '
                            'Disable them or choose H.264 8-bit.')
HIGHBIT_EFFECT_ERROR = ('This FFmpeg build cannot apply Gaussian blur directly to 16-bit RGB video. '
                       'Choose H.264 8-bit or install a compatible FFmpeg build.')

VIGNETTE_ERROR = ('This FFmpeg build cannot apply radial vignette directly to 16-bit RGB video. '
                  'Choose H.264 8-bit or install a compatible FFmpeg build.')

def _vignette_filter(amount):
    # FFmpeg vf_vignette.c get_natural_factor: default center W/2,H/2;
    # signed offsets are truncated to int, including odd canvases. SAR is 1
    # because the graded project canvas has square pixels. Unlike the legacy
    # filter this evaluates in double precision and multiplies native RGB16.
    angle = format(float(amount) * math.pi / 2, '.17g')
    gain = f'pow(cos({angle}*hypot(trunc(X-W/2),trunc(Y-H/2))/hypot(W/2,H/2)),4)'
    # Nearest is essential: bilinear sampling can clamp the last row/column.
    return 'geq=' + ':'.join(f"{c}='{c}(X,Y)*{gain}'" for c in 'rgb') + ':interpolation=nearest'


def highbit_effect_filters(effects):
    # Validate the entire legacy numeric vocabulary first, including bypassed
    # entries, so this profile cannot silently accept malformed project data.
    filters = effect_filters(effects)
    if not filters:
        return []
    ordered = []
    for effect in effects:
        if not effect.enabled or effect.amount == 0:
            continue
        radius, feather = validated_vignette_shape(effect)
        if effect.kind == 'blur':
            ordered.append(f'gblur=sigma={format(float(effect.amount), ".12g")}:steps=3')
        elif radius == 1 and feather == 1:
            ordered.append(_vignette_filter(effect.amount))
        else:
            ordered.append(shaped_vignette_filter(effect.amount, radius, feather))
    return ['format=gbrp16le', *ordered, 'format=gbrp16le']


def _probe_frame():
    # G plane: a non-8-bit constant. B plane: one-code step. R plane:
    # isolated impulse. This tests low-bit retention and actual spatial work.
    values = [10001] * (64 * 64)
    values += [10001 + (x >= 32) for y in range(64) for x in range(64)]
    red = [10001] * (64 * 64)
    red[32 * 64 + 32] = 50001
    values += red
    return struct.pack('<12288H', *values)


def _valid_probe(data):
    if len(data) != 24576:
        return False
    values = struct.unpack('<12288H', data)
    # Preserve constant low bits and independently distinguish a one-code step
    # well outside the blur transition. A hidden 8-bit path cannot do either.
    if any(v != 10001 for v in values[:4096]):
        return False
    blue = values[4096:8192]
    if any(blue[y * 64 + x] != target for y in range(64)
           for x, target in ((4, 10001), (59, 10002))):
        return False
    red = values[8192:]
    center = red[32 * 64 + 32]
    neighbor = red[32 * 64 + 31]
    # Wide intervals permit platform floating-point rounding, but exclude a no-op,
    # destructive channel conversion, or a flat fill masquerading as blur.
    if not (11500 < center < 13500 and 11000 < neighbor < center):
        return False
    if abs(red[32 * 64 + 33] - neighbor) > 2:
        return False
    if any(abs(red[y * 64 + x] - 10001) > 1 for y, x in ((0, 0), (0, 63), (63, 0), (63, 63))):
        return False
    # Near-unity impulse energy also rejects simply darkening a wide region.
    return abs(sum(red) - (10001 * 4096 + 40000)) <= 512


def ensure_highbit_effects(project, timeout, cancel, error_type, cancelled_type):
    """Require observed native RGB16 effects, with bounded cancellation/cleanup."""
    if cancel is not None and cancel.is_set():
        raise cancelled_type('Render cancelled.')
    effects = [e for c in project.clips if c.track < 2 for e in getattr(c, 'effects', [])]
    try:
        filters = highbit_effect_filters(effects)
    except (ValueError, OverlayRenderError) as exc:
        raise error_type(str(exc)) from exc
    if not filters:
        return
    # Match the final compositor's scalar execution, including the narrow
    # image regression. This is a precision choice, not a format-only check.
    if any(e.enabled and e.amount > 0 and e.kind == 'blur' for e in effects):
        _run_probe(timeout, cancel, error_type, cancelled_type, narrow=False)
        _run_probe(timeout, cancel, error_type, cancelled_type, narrow=True)
    active_vignettes = [e for e in effects if e.enabled and e.amount > 0 and e.kind == 'vignette']
    if any(validated_vignette_shape(e) == (1, 1) for e in active_vignettes):
        for width, height in ((14, 10), (3, 7), (1, 9)):
            _run_vignette_probe(timeout, cancel, error_type, cancelled_type, width, height)
    if any(validated_vignette_shape(e) != (1, 1) for e in active_vignettes):
        # Fixed-size evidence is bounded independently of project/clip count.
        # Both branches of the clamp, reciprocal feather powers, odd signed
        # offsets and one-pixel sampling must work without hidden conversion.
        for radius, feather in ((.75, 1.5), (1.5, .5)):
            for width, height in ((14, 10), (3, 7), (1, 9)):
                _run_shaped_vignette_probe(timeout, cancel, error_type, cancelled_type,
                                           width, height, radius, feather)


def highbit_effect_input_options(project):
    """Global FFmpeg flags only for exports containing effective video blur."""
    if any(e.enabled and e.amount > 0 and e.kind == 'blur'
           for c in project.clips if c.track < 2 for e in getattr(c, 'effects', [])):
        return ['-cpuflags', '0']
    return []


def _run_probe(timeout, cancel, error_type, cancelled_type, *, narrow):
    command = [ffmpeg_binary(), '-hide_banner', '-v', 'error', '-nostdin',
               '-cpuflags', '0', '-filter_threads', '1',
               '-noauto_conversion_filters', '-f', 'rawvideo', '-pix_fmt', 'gbrp16le',
               '-s', '2x48' if narrow else '64x64', '-r', '1', '-i', 'pipe:0',
               '-vf', f'gblur=sigma={30 if narrow else 2}:steps=3,format=gbrp16le',
               '-frames:v', '1', '-an', '-c:v', 'rawvideo', '-threads', '1',
               '-pix_fmt', 'gbrp16le', '-f', 'rawvideo', 'pipe:1']
    original = (struct.pack('<288H', *([10001] * 96 + [10002] * 96 + [20003] * 96))
                if narrow else _probe_frame())
    process = None
    began = time.monotonic()
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as log:
        source.write(original)
        source.seek(0)
        try:
            if cancel is not None and cancel.is_set():
                raise cancelled_type('Render cancelled.')
            process = subprocess.Popen(command, stdin=source, stdout=output, stderr=log)
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    raise cancelled_type('Render cancelled.')
                if time.monotonic() - began > min(timeout, 30):
                    raise error_type('Render timed out checking high-bit effect support.')
                if max(os.fstat(output.fileno()).st_size, os.fstat(log.fileno()).st_size) > 65536:
                    raise error_type('High-bit effect capability probe exceeded its output budget.')
                time.sleep(.025)
            if cancel is not None and cancel.is_set():
                raise cancelled_type('Render cancelled.')
            if max(os.fstat(output.fileno()).st_size, os.fstat(log.fileno()).st_size) > 65536:
                raise error_type('High-bit effect capability probe exceeded its output budget.')
            output.seek(0)
            data = output.read(24577)
            valid = data == original if narrow else _valid_probe(data)
            if process.returncode or not valid:
                raise error_type(HIGHBIT_EFFECT_ERROR)
        except OSError as exc:
            raise error_type(HIGHBIT_EFFECT_ERROR) from exc
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()


def _vignette_probe_samples(width, height):
    """Small full-image probe with independent scalar geometry and low-bit ramps."""
    original, expected = [], []
    angle = .65 * math.pi / 2
    radius = math.sqrt((width / 2) ** 2 + (height / 2) ** 2)
    for channel in range(3):
        for y in range(height):
            for x in range(width):
                value = 10001 + channel * 19001 + y * width + x
                distance = math.sqrt(int(x - width / 2) ** 2 + int(y - height / 2) ** 2)
                factor = math.cos(angle * distance / radius) ** 4
                original.append(value)
                expected.append(int(value * factor))
    return struct.pack(f'<{len(original)}H', *original), expected


def _run_vignette_probe(timeout, cancel, error_type, cancelled_type, width, height):
    """No cached capability; bounded raw I/O, deterministic geometry and cleanup."""
    original, expected = _vignette_probe_samples(width, height)
    _run_vignette_probe_data(timeout, cancel, error_type, cancelled_type, width, height,
                            original, expected, _vignette_filter(.65))


def _shaped_vignette_probe_samples(width, height, radius, feather):
    """Independent scalar oracle, bounded before allocation or FFmpeg launch."""
    if (type(width) is not int or type(height) is not int or width <= 0 or height <= 0
            or width * height > 1024):
        raise ValueError('Invalid vignette capability probe dimensions.')
    original, expected = [], []
    diagonal = math.sqrt(width * width + height * height) / 2
    for channel in range(3):
        for y in range(height):
            for x in range(width):
                value = 10001 + channel * 19001 + y * width + x
                dx, dy = int(x - width / 2), int(y - height / 2)
                u = math.sqrt(dx * dx + dy * dy) / diagonal
                q = min(1, (u / radius) ** (1 / feather))
                gain = math.cos(.65 * math.pi * q / 2) ** 4
                original.append(value)
                expected.append(int(value * gain))
    return struct.pack(f'<{len(original)}H', *original), expected


def _run_shaped_vignette_probe(timeout, cancel, error_type, cancelled_type,
                               width, height, radius, feather):
    original, expected = _shaped_vignette_probe_samples(width, height, radius, feather)
    _run_vignette_probe_data(timeout, cancel, error_type, cancelled_type, width, height,
                            original, expected, shaped_vignette_filter(.65, radius, feather))


def _run_vignette_probe_data(timeout, cancel, error_type, cancelled_type, width, height,
                             original, expected, filter_graph):
    """Shared lifecycle only; default fixture, expression and probe paths stay exact."""
    command = [ffmpeg_binary(), '-hide_banner', '-v', 'error', '-nostdin',
               '-filter_threads', '1', '-noauto_conversion_filters',
               '-f', 'rawvideo', '-pix_fmt', 'gbrp16le', '-s', f'{width}x{height}',
               '-r', '1', '-i', 'pipe:0', '-vf', filter_graph + ',format=gbrp16le',
               '-frames:v', '1', '-an', '-c:v', 'rawvideo', '-threads', '1',
               '-pix_fmt', 'gbrp16le', '-f', 'rawvideo', 'pipe:1']
    process = None
    began = time.monotonic()
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as log:
        source.write(original)
        source.seek(0)
        try:
            if cancel is not None and cancel.is_set():
                raise cancelled_type('Render cancelled.')
            process = subprocess.Popen(command, stdin=source, stdout=output, stderr=log)
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    raise cancelled_type('Render cancelled.')
                if time.monotonic() - began > min(timeout, 30):
                    raise error_type('Render timed out checking high-bit effect support.')
                if max(os.fstat(output.fileno()).st_size, os.fstat(log.fileno()).st_size) > 65536:
                    raise error_type('High-bit effect capability probe exceeded its output budget.')
                time.sleep(.025)
            if cancel is not None and cancel.is_set():
                raise cancelled_type('Render cancelled.')
            if max(os.fstat(output.fileno()).st_size, os.fstat(log.fileno()).st_size) > 65536:
                raise error_type('High-bit effect capability probe exceeded its output budget.')
            output.seek(0)
            data = output.read(len(original) + 1)
            if process.returncode or len(data) != len(original):
                raise error_type(VIGNETTE_ERROR)
            actual = struct.unpack(f'<{len(expected)}H', data)
            if any(abs(a - b) > 1 for a, b in zip(actual, expected)):
                raise error_type(VIGNETTE_ERROR)
        except OSError as exc:
            raise error_type(VIGNETTE_ERROR) from exc
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()

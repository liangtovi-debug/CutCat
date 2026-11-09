"""Native 10-bit caption drawing, never an 8-bit whole-picture overlay.

The existing literal-text/layout/font contract is shared with preview. FFmpeg's
high-depth drawtext blends glyph coverage in nonlinear Rec.709 Y'CbCr code
values, not linear light. #RRGGBB style values are Rec.709 encoded RGB here.
Explicit link metadata must precede drawing: unspecified YUV defaults to 601.
The small FreeType coverage mask may be 8-bit; the picture planes remain 10-bit.
"""
from __future__ import annotations

import os
import subprocess
import struct
import tempfile
import time

from .caption_render import _filter_path, caption_filters, prepare_font
from .media import ffmpeg_binary

HIGHBIT_CAPTION_ERROR = ('This FFmpeg build cannot burn captions directly into 10-bit Rec.709 video. '
                        'Choose H.264 8-bit or install a compatible FFmpeg build.')
_CONTEXT = 'setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709'


def _probe_frame():
    # Non-8-bit luma codes expose a hidden whole-frame reduction. Neutral
    # chroma is exact; this fixture is independent of the user's media.
    return struct.pack('<8192H', *([64 + i % 876 for i in range(4096)] + [512] * 4096))


def _valid_probe(data, original):
    if len(data) != 16384:
        return False
    samples = struct.unpack('<8192H', data)
    if max(samples) > 1023:
        return False
    # The fixed red box begins at (16,16). Uncovered top rows must preserve
    # every source code, not merely produce a 10-bit output label.
    for begin, width in ((0, 64), (4096, 32), (6144, 32)):
        if data[begin * 2:(begin + width * 8) * 2] != original[begin * 2:(begin + width * 8) * 2]:
            return False
    # Drawtext's native scaling/coverage integer rounding differs slightly
    # from textbook legal endpoints. This tolerance accepts that but excludes
    # the measured BT.601 red luma (~325) and a no-op renderer.
    for y in range(20, 24):
        for x in range(20, 24):
            values = (samples[y * 64 + x], samples[4096 + y * 32 + x // 2],
                      samples[6144 + y * 32 + x // 2])
            if any(abs(value - target) > 4 for value, target in zip(values, (250, 409, 960))):
                return False
    return True


def highbit_caption_filters(project, cache_dir, timeout, cancel, error_type, cancelled_type):
    """Pin typed links and fail closed if this binary needs an implicit scaler.

    Probe each export, with a bounded one-frame graph and automatic conversion
    disabled. Do not infer capability from an encoder name, version, output tag,
    or a success that could conceal an 8-bit intermediate. No preview/cache
    artifact is reused as a high-depth input.
    """
    if cancel is not None and cancel.is_set():
        raise cancelled_type('Render cancelled.')
    filters = caption_filters(project, cache_dir)
    if not filters:
        return []
    font = prepare_font(project.caption_style, project.captions, cache_dir)
    probe_filter = (_CONTEXT + ',drawtext=fontfile=' + _filter_path(font) +
                    ':text=M:fontsize=12:fontcolor=red:x=16:y=16:'
                    'box=1:boxcolor=red:boxw=16:boxh=16,format=yuv422p10le')
    command = [ffmpeg_binary(), '-hide_banner', '-v', 'error', '-nostdin',
               '-noauto_conversion_filters', '-f', 'rawvideo', '-pix_fmt', 'yuv422p10le',
               '-s', '64x64', '-r', '1', '-i', 'pipe:0', '-vf', probe_filter,
               '-frames:v', '1', '-an', '-c:v', 'rawvideo', '-threads', '1',
               '-pix_fmt', 'yuv422p10le', '-f', 'rawvideo', 'pipe:1']
    process = None
    began = time.monotonic()
    original = _probe_frame()
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
                    raise error_type('Render timed out checking high-bit caption support.')
                if max(os.fstat(output.fileno()).st_size, os.fstat(log.fileno()).st_size) > 65536:
                    raise error_type('High-bit caption capability probe exceeded its output budget.')
                time.sleep(.025)
            if cancel is not None and cancel.is_set():
                raise cancelled_type('Render cancelled.')
            if max(os.fstat(output.fileno()).st_size, os.fstat(log.fileno()).st_size) > 65536:
                raise error_type('High-bit caption capability probe exceeded its output budget.')
            # Raw 4:2:2 with 16-bit storage: two samples per pixel. A wrong
            # output type or incomplete process is not a supported capability.
            output.seek(0)
            if process.returncode or not _valid_probe(output.read(16385), original):
                raise error_type(HIGHBIT_CAPTION_ERROR)
        except OSError as exc:
            raise error_type(HIGHBIT_CAPTION_ERROR) from exc
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()
    return ['format=yuv422p10le', _CONTEXT, *filters, 'format=yuv422p10le']

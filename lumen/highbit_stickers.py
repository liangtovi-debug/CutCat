"""Verified static SDR art over a genuinely 10-bit Rec.709 picture.

The shared Qt renderer still places/transforms/composites sticker art in 8-bit
premultiplied RGBA, then exports straight RGBA through lossless FFV1/BGRA. Its
legacy gamma-encoded SDR RGB code values are interpreted using the Rec.709
matrix here. This is NOT an sRGB-to-Rec.709 transfer conversion, ICC handling,
linear-light compositing, HDR, or a claim that the art itself gains precision.
Only the art layer is 8-bit; the picture never visits an 8-bit format.
"""
from __future__ import annotations

import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time

from .media import ffmpeg_binary

STATIC_PNG_ERROR = ('ProRes 10-bit SDR export supports only verified static PNG stickers. '
                    'Animated frame-pack manifests are not yet supported; disable those '
                    'stickers or choose H.264 8-bit.')
HIGHBIT_STICKER_ERROR = ('This FFmpeg build cannot composite stickers directly into 10-bit Rec.709 video. '
                        'Choose H.264 8-bit or install a compatible FFmpeg build.')
_CONTEXT = 'setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709'
_MAIN_FILTER = ('format=yuv422p10le,' + _CONTEXT +
                ',scale=flags=neighbor:in_color_matrix=bt709:out_color_matrix=bt709:'
                'in_range=tv:out_range=tv,format=yuv444p10le,' + _CONTEXT)
# BGRA from the owned FFV1 cache goes straight to this explicit scaler. No
# implicit RGB/YUV conversion, transfer-function change, or premultiplication.
_LAYER_FILTER = ('scale=in_color_matrix=bt709:out_color_matrix=bt709:'
                 'in_range=full:out_range=tv:flags=accurate_rnd+full_chroma_int,'
                 'format=yuva444p10le,' + _CONTEXT)
# Composite before subsampling the art: straight RGBA -> 4:2:2 followed by
# alpha blending would attenuate saturated edges twice. Some overlay builds
# also weight 4:2:2 alpha pairs asymmetrically. 4:4:4 avoids both. The picture's
# chroma is duplicated exactly above and box-averaged here, so transparent
# regions and frames are identities, not a chroma up/downsampling blur.
# Two discarded padding pixels avoid swscale's width-two area special case,
# which selects one chroma pixel instead of averaging the pair. Padding stays
# pair-aligned, never touches visible samples, and is cropped after reduction.
_OVERLAY_FILTER = ('overlay=format=yuv444p10:eof_action=pass:repeatlast=0:'
                   'shortest=0:alpha=straight,'
                   'pad=iw+2:ih:0:0:color=black,'
                   'scale=flags=area:in_color_matrix=bt709:out_color_matrix=bt709:'
                   'in_range=tv:out_range=tv,format=yuv422p10le,'
                   'crop=iw-2:ih:0:0,' + _CONTEXT)
_FRAME_BYTES = 16384  # 64 x 64 planar 4:2:2 samples, stored in 16-bit words.


def _probe_frame():
    """All 1024 code values catch reduction, chroma blur, and range clipping."""
    return struct.pack('<8192H', *([i % 1024 for i in range(4096)] +
                                  [(i * 113) % 1024 for i in range(2048)] +
                                  [(i * 173) % 1024 for i in range(2048)]))


def _probe_layer():
    # Opaque and half-transparent red interiors test matrix, range, and straight
    # alpha. The second all-transparent frame must preserve the entire picture.
    first = bytearray(64 * 64 * 4)
    for y in range(16, 32):
        for x in range(16, 48):
            offset = (y * 64 + x) * 4
            first[offset:offset + 4] = bytes((0, 0, 255, 255 if x < 32 else 128))
    for y in range(40, 48):
        # Odd/even coverage and the final chroma pair expose subsampled-alpha
        # heuristics and transparent-black contamination at saturated edges.
        for x, alpha in ((1, 255), (20, 255), (41, 128), (62, 255)):
            offset = (y * 64 + x) * 4
            first[offset:offset + 4] = bytes((0, 0, 255, alpha))
    return first + bytes(64 * 64 * 4)


def _valid_probe(data, original):
    if len(original) != _FRAME_BYTES or len(data) != _FRAME_BYTES * 2:
        return False
    if data[_FRAME_BYTES:] != original:
        return False  # Fully transparent layer is an exact identity, all planes.
    samples = struct.unpack('<8192H', data[:_FRAME_BYTES])
    source = struct.unpack('<8192H', original)
    if max(samples) > 1023:
        return False
    alpha_values = _probe_layer()[3:_FRAME_BYTES:4]
    for offset, width, red in ((0, 64, 250), (4096, 32, 409), (6144, 32, 960)):
        for y in range(64):
            for x in range(width):
                index = offset + y * width + x
                alpha = (alpha_values[y * 64 + x] / 255 if width == 64 else
                         sum(alpha_values[y * 64 + x * 2:y * 64 + x * 2 + 2]) / 510)
                if alpha == 0:
                    if samples[index] != source[index]:
                        return False
                else:
                    # Allow only integer RGB conversion and blend rounding,
                    # never a wrong matrix, doubled alpha, or chroma-edge blur.
                    expected = source[index] * (1 - alpha) + red * alpha
                    if abs(samples[index] - expected) > 2:
                        return False
    return True


def highbit_sticker_filters(project, assets, timeout, cancel, error_type, cancelled_type):
    """Return typed main/layer/overlay filters only after a behavioral probe.

    ``assets`` must come from RenderEngine's current source verification. The
    probe is repeated each export, not cached by binary name/version, and uses
    no user media. Automatic filter conversion is disabled so a declared output
    pixel format cannot hide an 8-bit picture intermediate. Temporary input,
    output and logs are bounded and cleaned on success, failure and cancellation.
    """
    if cancel is not None and cancel.is_set():
        raise cancelled_type('Render cancelled.')
    from .sticker_assets import StickerAsset
    overlays = [o for o in getattr(project, 'overlays', []) if o.enabled]
    if not overlays:
        return ()
    for overlay in overlays:
        if Path(overlay.asset_path).suffix.lower() != '.png':
            raise error_type(STATIC_PNG_ERROR)
        asset = assets.get(overlay.id)
        if (not isinstance(asset, StickerAsset) or len(asset.frames) != 1
                or len(asset.frame_sha256) != 1 or asset.frame_duration != 1
                or not asset.loop or asset.digest != overlay.asset_sha256.lower()):
            raise error_type('High-bit sticker source identity is missing or unsupported. '
                             'Verify a static PNG before rendering.')
    process = None
    began = time.monotonic()
    original = _probe_frame()
    with tempfile.TemporaryDirectory(prefix='lumen-sticker-probe-') as directory, \
            tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output, \
            tempfile.TemporaryFile() as log:
        try:
            source.write(original * 2)
            source.seek(0)
            # A closed owned file is reopenable by FFmpeg on Windows as well.
            layer = Path(directory) / 'layer.bgra'
            layer.write_bytes(_probe_layer())
            graph = (f'[0:v]{_MAIN_FILTER}[main];[1:v]{_LAYER_FILTER}[stickers];'
                     f'[main][stickers]{_OVERLAY_FILTER},format=yuv422p10le[out]')
            command = [ffmpeg_binary(), '-hide_banner', '-v', 'error', '-nostdin',
                       '-noauto_conversion_filters', '-filter_complex_threads', '1',
                       '-f', 'rawvideo', '-pix_fmt', 'yuv422p10le', '-s', '64x64',
                       '-r', '1', '-protocol_whitelist', 'file,pipe', '-i', 'pipe:0',
                       '-f', 'rawvideo', '-pix_fmt', 'bgra', '-s', '64x64', '-r', '1',
                       '-protocol_whitelist', 'file,pipe', '-i', str(layer),
                       '-filter_complex', graph, '-map', '[out]', '-frames:v', '2',
                       '-an', '-c:v', 'rawvideo', '-threads', '1', '-pix_fmt',
                       'yuv422p10le', '-f', 'rawvideo', 'pipe:1']
            if cancel is not None and cancel.is_set():
                raise cancelled_type('Render cancelled.')
            process = subprocess.Popen(command, stdin=source, stdout=output, stderr=log)
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    raise cancelled_type('Render cancelled.')
                if time.monotonic() - began > min(timeout, 30):
                    raise error_type('Render timed out checking high-bit sticker support.')
                if max(os.fstat(output.fileno()).st_size, os.fstat(log.fileno()).st_size) > 65536:
                    raise error_type('High-bit sticker capability probe exceeded its output budget.')
                time.sleep(.025)
            if cancel is not None and cancel.is_set():
                raise cancelled_type('Render cancelled.')
            if max(os.fstat(output.fileno()).st_size, os.fstat(log.fileno()).st_size) > 65536:
                raise error_type('High-bit sticker capability probe exceeded its output budget.')
            output.seek(0)
            if process.returncode or not _valid_probe(output.read(_FRAME_BYTES * 2 + 1), original):
                raise error_type(HIGHBIT_STICKER_ERROR)
        except OSError as exc:
            raise error_type(HIGHBIT_STICKER_ERROR) from exc
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()
    return _MAIN_FILTER, _LAYER_FILTER, _OVERLAY_FILTER

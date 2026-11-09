"""Bounded, metadata-aware conversion of decoded preview pixels to an owned image.

Qt 6.8's CPU YUV conversion can apply a fixed BT.601/video-range matrix even
when the frame says BT.709. Do not repair its RGB result: convert the original
mapped YUV samples using their explicit matrix and range instead.

This is an 8-bit SDR matrix decoder, not a transfer-function/gamut transform,
HDR tone mapper or display-calibration path. Subsampled chroma uses deterministic
nearest-neighbor replication: sample (x // 2, y // 2) for 4:2:0, or (x // 2, y)
for 4:2:2. No chroma-siting metadata is exposed by QVideoFrameFormat. The frame's
presentation rotation/mirroring is not applied, matching QVideoFrame.toImage().
Mapped RGB is already backend-converted: preserve it, never apply a YUV matrix
or range expansion again. That compatibility path does not verify the backend's
upstream color conversion. Returned images own their memory after unmapping.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat


MAX_FRAME_SIDE = 8192
MAX_FRAME_PIXELS = 16_777_216
MAX_MAPPED_BYTES = 128 * 1024 * 1024
CONVERSION_ROWS = 64

_P = QVideoFrameFormat.PixelFormat
_CS = QVideoFrameFormat.ColorSpace
_CR = QVideoFrameFormat.ColorRange
_CT = QVideoFrameFormat.ColorTransfer

_PLANAR_420 = {_P.Format_YUV420P, _P.Format_YV12}
_SEMIPLANAR_420 = {_P.Format_NV12, _P.Format_NV21}
_PACKED_422 = {_P.Format_UYVY, _P.Format_YUYV}
_YUV_FORMATS = _PLANAR_420 | _SEMIPLANAR_420 | _PACKED_422 | {_P.Format_YUV422P}

# Byte positions, not machine-endian uint32 values. Preserve premultiplied
# samples with the corresponding QImage format rather than unpremultiplying.
_RGB_FORMATS = {
    _P.Format_ARGB8888: ((1, 2, 3, 0), False, False),
    _P.Format_ARGB8888_Premultiplied: ((1, 2, 3, 0), True, False),
    _P.Format_XRGB8888: ((1, 2, 3, 0), False, True),
    _P.Format_BGRA8888: ((2, 1, 0, 3), False, False),
    _P.Format_BGRA8888_Premultiplied: ((2, 1, 0, 3), True, False),
    _P.Format_BGRX8888: ((2, 1, 0, 3), False, True),
    _P.Format_ABGR8888: ((3, 2, 1, 0), False, False),
    _P.Format_XBGR8888: ((3, 2, 1, 0), False, True),
    _P.Format_RGBA8888: ((0, 1, 2, 3), False, False),
    _P.Format_RGBX8888: ((0, 1, 2, 3), False, True),
}


class PreviewColorError(ValueError):
    """A safe conversion was impossible; ``reason`` is diagnostic detail.

    Keep the exception text stable so the UI can translate one message. Do not
    silently substitute QVideoFrame.toImage() after this error.
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__('Preview color conversion is unavailable for this frame.')


def _plane(frame, index, rows, row_bytes):
    """View only validated active samples; stride padding is never a sample."""
    stride, size = frame.bytesPerLine(index), frame.mappedBytes(index)
    needed = (rows - 1) * stride + row_bytes
    if stride < row_bytes or size < needed or size > MAX_MAPPED_BYTES:
        raise PreviewColorError(f'Invalid stride or mapped size for plane {index}')
    data = memoryview(frame.bits(index))
    if not data.c_contiguous or data.nbytes < needed or data.nbytes < size:
        raise PreviewColorError(f'Truncated or noncontiguous buffer for plane {index}')
    return np.ndarray((rows, row_bytes), dtype=np.uint8, buffer=data,
                      strides=(stride, 1))


def _image_buffer(width, height, channels, premultiplied=False):
    image_format = (QImage.Format.Format_RGB888 if channels == 3 else
                    QImage.Format.Format_RGBA8888_Premultiplied if premultiplied else
                    QImage.Format.Format_RGBA8888)
    image = QImage(width, height, image_format)
    if image.isNull():
        raise PreviewColorError('Could not allocate the preview image')
    # Clear padding too, so the owned image contains no uninitialized bytes.
    image.fill(0)
    pixels = np.ndarray((height, width, channels), dtype=np.uint8,
                        buffer=image.bits(), strides=(image.bytesPerLine(), channels, 1))
    return image, pixels


def _rgb_image(frame, pixel_format, width, height):
    order, premultiplied, opaque = _RGB_FORMATS[pixel_format]
    raw = _plane(frame, 0, height, width * 4).reshape(height, width, 4)
    image, output = _image_buffer(width, height, 4, premultiplied)
    for start in range(0, height, CONVERSION_ROWS):
        stop = min(start + CONVERSION_ROWS, height)
        for channel, source in enumerate(order):
            output[start:stop, :, channel] = (255 if channel == 3 and opaque else
                                              raw[start:stop, :, source])
    return image


def _yuv_planes(frame, pixel_format, width, height):
    chroma_width = (width + 1) // 2
    vertical_subsampling = 1 if pixel_format in (_PACKED_422 | {_P.Format_YUV422P}) else 2
    chroma_height = (height + vertical_subsampling - 1) // vertical_subsampling
    if pixel_format in _PACKED_422:
        packed = _plane(frame, 0, height, chroma_width * 4)
        if pixel_format == _P.Format_UYVY:
            return packed[:, 1::2][:, :width], packed[:, 0::4], packed[:, 2::4], 1
        return packed[:, 0::2][:, :width], packed[:, 1::4], packed[:, 3::4], 1
    y = _plane(frame, 0, height, width)
    if pixel_format in _SEMIPLANAR_420:
        uv = _plane(frame, 1, chroma_height, chroma_width * 2)
        u, v = uv[:, 0::2], uv[:, 1::2]
        if pixel_format == _P.Format_NV21:
            u, v = v, u
    else:
        u = _plane(frame, 1, chroma_height, chroma_width)
        v = _plane(frame, 2, chroma_height, chroma_width)
        if pixel_format == _P.Format_YV12:
            u, v = v, u
    return y, u, v, vertical_subsampling


def _yuv_image(frame, pixel_format, width, height, color_space, color_range):
    y_plane, u_plane, v_plane, subsampling = _yuv_planes(frame, pixel_format, width, height)
    kr, kb = (.2126, .0722) if color_space == _CS.ColorSpace_BT709 else (.299, .114)
    kg = 1.0 - kr - kb
    limited = color_range == _CR.ColorRange_Video
    y_offset, y_scale, c_scale = (16., 255. / 219., 255. / 224.) if limited else (0., 1., 1.)
    image, output = _image_buffer(width, height, 3)
    # Only the owned uint8 output is full-frame. Float intermediates are bounded
    # by CONVERSION_ROWS, not the height of a 4K (or portrait) input.
    for start in range(0, height, CONVERSION_ROWS):
        stop = min(start + CONVERSION_ROWS, height)
        y = (y_plane[start:stop].astype(np.float32) - y_offset) * y_scale
        chroma_start, chroma_stop = start // subsampling, (stop + subsampling - 1) // subsampling
        cb = u_plane[chroma_start:chroma_stop].repeat(subsampling, axis=0)
        cr = v_plane[chroma_start:chroma_stop].repeat(subsampling, axis=0)
        cb = cb[:stop - start].repeat(2, axis=1)[:, :width].astype(np.float32)
        cr = cr[:stop - start].repeat(2, axis=1)[:, :width].astype(np.float32)
        cb = (cb - 128.) * c_scale
        cr = (cr - 128.) * c_scale
        # Clamp only the final RGB, not Y/Cb/Cr first: legal superblack/white
        # and out-of-gamut input samples may affect individual RGB channels.
        for channel, values in enumerate((y + (2. - 2. * kr) * cr,
                                          y - kb * (2. - 2. * kb) / kg * cb
                                            - kr * (2. - 2. * kr) / kg * cr,
                                          y + (2. - 2. * kb) * cb)):
            np.clip(values, 0., 255., out=values)
            np.rint(values, out=values)
            output[start:stop, :, channel] = values
    return image


def preview_frame_image(frame: QVideoFrame) -> QImage:
    """Return an owned SDR image, or raise :class:`PreviewColorError`.

    Accept mapped 8-bit YUV420P, YV12, NV12/NV21, YUV422P, UYVY/YUYV and
    packed 8-bit RGB(A). YUV requires explicit BT.601/BT.709 matrix and full/
    video range; neither resolution nor filename is a color-metadata fallback.
    Unsupported/high-bit-depth formats, explicit HDR/wide gamut, unknown YUV
    matrix/range/transfer and truncated planes fail closed. YUV requires an
    explicit BT.709/BT.601 SDR transfer: its encoded values are preserved, not
    converted. Callers must supply their normalized SDR proxy, not use this as
    an input color transform. Packed RGB may have unknown metadata because it
    is an explicitly unverified, backend-converted compatibility path.
    """
    try:
        if not frame.isValid():
            raise PreviewColorError('Invalid video frame')
        width, height = frame.width(), frame.height()
        if (width <= 0 or height <= 0 or max(width, height) > MAX_FRAME_SIDE
                or width * height > MAX_FRAME_PIXELS):
            raise PreviewColorError('Preview frame dimensions exceed the safe conversion budget')
        surface = frame.surfaceFormat()
        pixel_format = frame.pixelFormat()
        if surface.pixelFormat() != pixel_format or surface.frameSize() != frame.size():
            raise PreviewColorError('Inconsistent video frame format')
        color_space, color_range = surface.colorSpace(), surface.colorRange()
        if (surface.colorTransfer() in (_CT.ColorTransfer_ST2084, _CT.ColorTransfer_STD_B67)
                or color_space in (_CS.ColorSpace_BT2020, _CS.ColorSpace_AdobeRgb)):
            raise PreviewColorError('HDR or wide-gamut preview frames are unsupported')
        if pixel_format in _YUV_FORMATS:
            if (color_space not in (_CS.ColorSpace_BT601, _CS.ColorSpace_BT709)
                    or color_range not in (_CR.ColorRange_Full, _CR.ColorRange_Video)):
                raise PreviewColorError('YUV preview requires explicit BT.601/BT.709 matrix and range')
            if surface.colorTransfer() not in (_CT.ColorTransfer_BT709, _CT.ColorTransfer_BT601):
                raise PreviewColorError('YUV preview requires explicit BT.601/BT.709 SDR transfer')
            expected_planes = (1 if pixel_format in _PACKED_422 else
                               2 if pixel_format in _SEMIPLANAR_420 else 3)
        elif pixel_format in _RGB_FORMATS:
            expected_planes = 1
        else:
            raise PreviewColorError(f'Unsupported preview pixel format: {pixel_format.name}')
        if surface.scanLineDirection() not in (QVideoFrameFormat.Direction.TopToBottom,
                                               QVideoFrameFormat.Direction.BottomToTop):
            raise PreviewColorError('Unsupported video scanline direction')
        if not frame.map(QVideoFrame.MapMode.ReadOnly):
            raise PreviewColorError('Could not map video frame for reading')
        try:
            if frame.planeCount() != expected_planes:
                raise PreviewColorError('Unexpected mapped plane count')
            sizes = [frame.mappedBytes(i) for i in range(expected_planes)]
            if any(size <= 0 for size in sizes) or sum(sizes) > MAX_MAPPED_BYTES:
                raise PreviewColorError('Mapped video planes exceed the safe conversion budget')
            image = (_yuv_image(frame, pixel_format, width, height, color_space, color_range)
                     if pixel_format in _YUV_FORMATS else _rgb_image(frame, pixel_format, width, height))
            if surface.scanLineDirection() == QVideoFrameFormat.Direction.BottomToTop:
                image = image.mirrored(False, True)
                if image.isNull():
                    raise PreviewColorError('Could not allocate the oriented preview image')
            return image
        finally:
            frame.unmap()
    except PreviewColorError:
        raise
    except (AttributeError, BufferError, MemoryError, OverflowError, RuntimeError, TypeError, ValueError) as exc:
        raise PreviewColorError(f'Invalid or unreadable preview frame: {type(exc).__name__}') from exc

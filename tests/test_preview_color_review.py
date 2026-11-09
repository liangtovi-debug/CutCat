"""Independent native-buffer review of channel, alpha and row ownership contracts.

Expected byte layouts are written explicitly, not imported from the converter.
These tests use allocated QVideoFrames, not the decoder or Qt's CPU RGB oracle.
Qt 6.8.3's imageFormatFromPixelFormat switches the ARGB32 mapping by host
endianness; the video enum names and RGBA8888 output specify byte positions.
"""
from __future__ import annotations

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat
from PySide6.QtWidgets import QApplication

from lumen.preview_color import PreviewColorError, preview_frame_image


P = QVideoFrameFormat.PixelFormat
CS = QVideoFrameFormat.ColorSpace
CR = QVideoFrameFormat.ColorRange
CT = QVideoFrameFormat.ColorTransfer
D = QVideoFrameFormat.Direction


def rgba_bytes(image):
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    raw = np.frombuffer(rgba.constBits(), np.uint8).reshape(rgba.height(), rgba.bytesPerLine())
    return raw[:, :rgba.width() * 4].reshape(rgba.height(), rgba.width(), 4).copy()


def native_frame(pixel_format, width=6, height=66, *, bottom_up=False, unknown_transfer=False):
    fmt = QVideoFrameFormat(QSize(width, height), pixel_format)
    fmt.setColorSpace(CS.ColorSpace_BT709)
    fmt.setColorRange(CR.ColorRange_Video)
    fmt.setColorTransfer(CT.ColorTransfer_Unknown if unknown_transfer else CT.ColorTransfer_BT709)
    fmt.setScanLineDirection(D.BottomToTop if bottom_up else D.TopToBottom)
    frame = QVideoFrame(fmt)
    assert frame.isValid()
    return frame


@pytest.mark.parametrize('pixel_format,source,expected,premultiplied', [
    (P.Format_ARGB8888, (128, 32, 64, 96), (32, 64, 96, 128), False),
    (P.Format_ARGB8888_Premultiplied, (128, 32, 64, 96), (32, 64, 96, 128), True),
    (P.Format_XRGB8888, (0, 32, 64, 96), (32, 64, 96, 255), False),
    (P.Format_BGRA8888, (96, 64, 32, 128), (32, 64, 96, 128), False),
    (P.Format_BGRA8888_Premultiplied, (96, 64, 32, 128), (32, 64, 96, 128), True),
    (P.Format_BGRX8888, (96, 64, 32, 0), (32, 64, 96, 255), False),
    (P.Format_ABGR8888, (128, 96, 64, 32), (32, 64, 96, 128), False),
    (P.Format_XBGR8888, (0, 96, 64, 32), (32, 64, 96, 255), False),
    (P.Format_RGBA8888, (32, 64, 96, 128), (32, 64, 96, 128), False),
    (P.Format_RGBX8888, (32, 64, 96, 0), (32, 64, 96, 255), False),
])
def test_native_rgb_byte_order_alpha_and_owned_output(pixel_format, source, expected, premultiplied):
    frame = native_frame(pixel_format, width=3, height=2)
    assert frame.map(QVideoFrame.MapMode.WriteOnly)
    stride = frame.bytesPerLine(0)
    try:
        frame.bits(0)[:] = b'\xef' * frame.mappedBytes(0)
        for row in range(2):
            frame.bits(0)[row * stride:row * stride + 12] = bytes(source) * 3
    finally:
        frame.unmap()
    image = preview_frame_image(frame)
    assert image.format() == (QImage.Format.Format_RGBA8888_Premultiplied if premultiplied
                              else QImage.Format.Format_RGBA8888)
    assert bytes(image.constBits()) == bytes(expected) * 6
    # Reading must not preserve a view onto a producer's subsequently reused buffer.
    assert frame.map(QVideoFrame.MapMode.WriteOnly)
    frame.bits(0)[:] = b'\0' * frame.mappedBytes(0)
    frame.unmap()
    del frame
    assert bytes(image.constBits()) == bytes(expected) * 6
    if premultiplied:
        assert image.pixelColor(0, 0).getRgb() == (64, 128, 191, 128)
    else:
        assert image.pixelColor(0, 0).getRgb() == expected


def reference_709(y, u, v):
    """Independent scalar inverse using normalized luma and a solved G channel."""
    yy, cb, cr = (int(y) - 16) / 219, (int(u) - 128) / 224, (int(v) - 128) / 224
    r, b = yy + 1.5748 * cr, yy + 1.8556 * cb
    g = (yy - .2126 * r - .0722 * b) / .7152
    return [round(255 * min(1, max(0, value))) for value in (r, g, b)]


@pytest.mark.parametrize('pixel_format', [P.Format_UYVY, P.Format_YUYV, P.Format_YUV422P,
                                         P.Format_YUV420P, P.Format_YV12,
                                         P.Format_NV12, P.Format_NV21])
@pytest.mark.parametrize('bottom_up', [False, True])
def test_native_subsampled_chroma_and_scanline_order_cross_chunk_boundary(pixel_format, bottom_up):
    width, height = 6, 130
    frame = native_frame(pixel_format, width, height, bottom_up=bottom_up)
    subsampling = 1 if pixel_format in (P.Format_UYVY, P.Format_YUYV, P.Format_YUV422P) else 2
    y = np.fromfunction(lambda row, col: 16 + (row * 3 + col * 17) % 220,
                        (height, width), dtype=int).astype(np.uint8)
    u = np.fromfunction(lambda row, col: 20 + (row * 7 + col * 13) % 220,
                        (height // subsampling, width // 2), dtype=int).astype(np.uint8)
    v = np.fromfunction(lambda row, col: 22 + (row * 11 + col * 19) % 216,
                        (height // subsampling, width // 2), dtype=int).astype(np.uint8)
    if pixel_format == P.Format_UYVY:
        planes = [np.stack((u, y[:, 0::2], v, y[:, 1::2]), axis=2).reshape(height, width * 2)]
    elif pixel_format == P.Format_YUYV:
        planes = [np.stack((y[:, 0::2], u, y[:, 1::2], v), axis=2).reshape(height, width * 2)]
    elif pixel_format in (P.Format_NV12, P.Format_NV21):
        uv = (u, v) if pixel_format == P.Format_NV12 else (v, u)
        planes = [y, np.stack(uv, axis=2).reshape(height // 2, width)]
    else:
        planes = [y, v, u] if pixel_format == P.Format_YV12 else [y, u, v]
    assert frame.map(QVideoFrame.MapMode.WriteOnly)
    try:
        for index, plane in enumerate(planes):
            stride = frame.bytesPerLine(index)
            raw = frame.bits(index)
            raw[:] = b'\xef' * len(raw)
            for row in range(plane.shape[0]):
                raw[row * stride:row * stride + plane.shape[1]] = plane[row].tobytes()
    finally:
        frame.unmap()
    image = preview_frame_image(frame)
    expected = np.array([[reference_709(y[row, col], u[row // subsampling, col // 2],
                                        v[row // subsampling, col // 2])
                          for col in range(width)] for row in range(height)], dtype=np.uint8)
    if bottom_up:
        expected = expected[::-1]
    np.testing.assert_allclose(rgba_bytes(image)[..., :3], expected, rtol=0, atol=1)
    assert not frame.isMapped()


def test_native_nested_readonly_and_rejected_write_mapping_keep_caller_ownership():
    source = QImage(3, 2, QImage.Format.Format_RGBA8888)
    source.fill(QColor(32, 64, 96, 128))
    frame = QVideoFrame(source)
    assert frame.map(QVideoFrame.MapMode.ReadOnly)
    try:
        result = preview_frame_image(frame)
        assert frame.isMapped() and frame.mapMode() == QVideoFrame.MapMode.ReadOnly
        assert result.pixelColor(0, 0) == source.pixelColor(0, 0)
    finally:
        frame.unmap()
    assert not frame.isMapped()
    assert frame.map(QVideoFrame.MapMode.WriteOnly)
    try:
        with pytest.raises(PreviewColorError):
            preview_frame_image(frame)
        assert frame.isMapped() and frame.mapMode() == QVideoFrame.MapMode.WriteOnly
    finally:
        frame.unmap()


@pytest.mark.parametrize('transfer', [CT.ColorTransfer_Unknown, CT.ColorTransfer_Linear,
                                      CT.ColorTransfer_Gamma22, CT.ColorTransfer_Gamma28,
                                      CT.ColorTransfer_ST2084, CT.ColorTransfer_STD_B67])
def test_unknown_or_non_proxy_transfer_does_not_guess_sdr(transfer):
    fmt = QVideoFrameFormat(QSize(4, 4), P.Format_YUV420P)
    fmt.setColorSpace(CS.ColorSpace_BT709)
    fmt.setColorRange(CR.ColorRange_Video)
    fmt.setColorTransfer(transfer)
    frame = QVideoFrame(fmt)
    assert frame.isValid()
    with pytest.raises(PreviewColorError):
        preview_frame_image(frame)
    assert not frame.isMapped()


def test_direct_viewer_unsupported_frame_does_not_leave_previous_pixels():
    from lumen.ui_color_reference import VideoViewer

    app = QApplication.instance() or QApplication([])
    viewer = VideoViewer()
    source = QImage(4, 4, QImage.Format.Format_RGBA8888)
    source.fill(QColor('red'))
    viewer.set_frame(QVideoFrame(source))
    assert not viewer.image.isNull()
    try:
        viewer.set_frame(native_frame(P.Format_AYUV, 4, 4))
        assert viewer.image.isNull() and viewer.frame_provenance is None and viewer.color_error
    finally:
        viewer.close()
        viewer.deleteLater()
        app.processEvents()

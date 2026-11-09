"""Independent numerical and mapped-buffer contracts for preview color.

These tests do not use Qt's YUV conversion as their color oracle. A separate
forward RGB->YCbCr matrix is inverted in float64 for reference values.
"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat as F

import lumen.preview_color as preview
from lumen.preview_color import PreviewColorError, preview_frame_image

P, CS, CR, CT = F.PixelFormat, F.ColorSpace, F.ColorRange, F.ColorTransfer
YUV = ('YUV420P', 'YV12', 'NV12', 'NV21', 'YUV422P', 'UYVY', 'YUYV')
MATRICES = {
    '709': np.array([[.2126, .7152, .0722],
                     [-.11457210605733996, -.38542789394266003, .5],
                     [.5, -.45415290830581656, -.04584709169418339]]),
    '601': np.array([[.299, .587, .114],
                     [-.16873589164785552, -.33126410835214445, .5],
                     [.5, -.41868758915834525, -.08131241084165478]]),
}


class MappedFrame:
    """A fake with independently controlled odd sizes, strides and plane bounds."""

    def __init__(self, surface, arrays, padding=3):
        self.surface = surface
        self.strides, self.planes = [], []
        for index, array in enumerate(arrays):
            array = np.asarray(array, dtype=np.uint8)
            stride = array.shape[1] + padding + index
            # The last row need not include trailing padding. Production must
            # validate the active extent rather than require rows * stride.
            data = bytearray([207] * ((array.shape[0] - 1) * stride + array.shape[1]))
            for row in range(array.shape[0]):
                data[row * stride:row * stride + array.shape[1]] = array[row].tobytes()
            self.strides.append(stride)
            self.planes.append(data)
        self.sizes = [len(p) for p in self.planes]
        self.valid = True
        self.map_ok = True
        self.maps = self.unmaps = 0
        self.mapped = False
        self.erase_on_unmap = False

    def isValid(self): return self.valid
    def width(self): return self.surface.frameWidth()
    def height(self): return self.surface.frameHeight()
    def size(self): return self.surface.frameSize()
    def surfaceFormat(self): return self.surface
    def pixelFormat(self): return self.surface.pixelFormat()
    def planeCount(self): return len(self.planes)
    def bytesPerLine(self, plane): return self.strides[plane]
    def mappedBytes(self, plane): return self.sizes[plane]
    def bits(self, plane):
        assert self.mapped
        return memoryview(self.planes[plane])
    def map(self, mode):
        assert mode == QVideoFrame.MapMode.ReadOnly
        self.maps += 1
        self.mapped = self.map_ok
        return self.map_ok
    def unmap(self):
        assert self.mapped
        self.mapped = False
        self.unmaps += 1
        if self.erase_on_unmap:
            for plane in self.planes:
                plane[:] = bytes(len(plane))
    def toImage(self):
        raise AssertionError('The converter must never delegate YUV decoding to Qt')


def surface(width, height, name='NV12', matrix='709', limited=True):
    fmt = F(QSize(width, height), getattr(P, 'Format_' + name))
    fmt.setColorSpace(getattr(CS, 'ColorSpace_BT' + matrix))
    fmt.setColorRange(CR.ColorRange_Video if limited else CR.ColorRange_Full)
    fmt.setColorTransfer(CT.ColorTransfer_BT709)
    return fmt


def yuv_frame(y, u, v, name='NV12', matrix='709', limited=True, padding=3):
    y, u, v = (np.asarray(a, dtype=np.uint8) for a in (y, u, v))
    height, width = y.shape
    if name in ('YUV420P', 'YUV422P'):
        planes = [y, u, v]
    elif name == 'YV12':
        planes = [y, v, u]
    elif name in ('NV12', 'NV21'):
        uv = np.stack((u, v) if name == 'NV12' else (v, u), axis=2)
        planes = [y, uv.reshape(u.shape[0], -1)]
    else:
        packed = np.zeros((height, u.shape[1] * 4), dtype=np.uint8)
        padded_y = np.pad(y, ((0, 0), (0, width % 2)), mode='edge')
        if name == 'UYVY':
            packed[:, 0::4], packed[:, 2::4] = u, v
            packed[:, 1::2] = padded_y
        else:
            packed[:, 1::4], packed[:, 3::4] = u, v
            packed[:, 0::2] = padded_y
        planes = [packed]
    return MappedFrame(surface(width, height, name, matrix, limited), planes, padding)


def image_rgb(image):
    rgb = image.convertToFormat(QImage.Format.Format_RGB888)
    return np.ndarray((rgb.height(), rgb.width(), 3), dtype=np.uint8,
                      buffer=rgb.constBits(), strides=(rgb.bytesPerLine(), 3, 1)).copy()


def reference(y, u, v, matrix, limited, vertical=2):
    """Invert an independent forward transform, with no production helpers."""
    height, width = y.shape
    values = np.empty((height, width, 3), dtype=np.float64)
    for row in range(height):
        for col in range(width):
            yy = (int(y[row, col]) - (16 if limited else 0)) / (219 if limited else 255)
            cb = (int(u[row // vertical, col // 2]) - 128) / (224 if limited else 255)
            cr = (int(v[row // vertical, col // 2]) - 128) / (224 if limited else 255)
            values[row, col] = np.linalg.solve(MATRICES[matrix], (yy, cb, cr)) * 255
    return np.rint(np.clip(values, 0, 255)).astype(np.uint8)


@pytest.mark.parametrize('name', YUV)
@pytest.mark.parametrize('matrix', ['601', '709'])
@pytest.mark.parametrize('limited', [False, True])
def test_matrix_range_planes_odd_size_and_padding_match_independent_math(name, matrix, limited):
    y = np.array([[0, 16, 63, 128, 235], [255, 100, 200, 90, 180], [64, 128, 192, 30, 220]], np.uint8)
    vertical = 1 if name in ('YUV422P', 'UYVY', 'YUYV') else 2
    u = np.array([[16, 128, 240], [202, 35, 155], [90, 234, 46]][:3 if vertical == 1 else 2], np.uint8)
    v = np.array([[240, 128, 16], [51, 228, 95], [201, 88, 170]][:3 if vertical == 1 else 2], np.uint8)
    frame = yuv_frame(y, u, v, name, matrix, limited)
    actual = image_rgb(preview_frame_image(frame))
    expected = reference(y, u, v, matrix, limited, vertical)
    assert np.max(np.abs(actual.astype(int) - expected.astype(int))) <= 1
    assert frame.maps == frame.unmaps == 1 and not frame.mapped


@pytest.mark.parametrize('name', YUV)
@pytest.mark.parametrize('matrix', ['601', '709'])
@pytest.mark.parametrize('limited', [False, True])
def test_black_white_endpoints_and_neutral_do_not_tint(name, matrix, limited):
    low, high = (16, 235) if limited else (0, 255)
    y = np.array([[low, high, 128, 0, 255]] * 4, np.uint8)
    vertical = 1 if name in ('YUV422P', 'UYVY', 'YUYV') else 2
    neutral = np.full((4 // vertical, 3), 128, np.uint8)
    rgb = image_rgb(preview_frame_image(yuv_frame(y, neutral, neutral, name, matrix, limited)))
    np.testing.assert_array_equal(rgb[:, 0], 0)
    np.testing.assert_array_equal(rgb[:, 1], 255)
    np.testing.assert_array_equal(rgb[:, :, 0], rgb[:, :, 1])
    np.testing.assert_array_equal(rgb[:, :, 0], rgb[:, :, 2])
    np.testing.assert_array_equal(rgb[:, 2], 130 if limited else 128)


@pytest.mark.parametrize('matrix', ['601', '709'])
@pytest.mark.parametrize('limited', [False, True])
def test_saturated_primaries_and_secondaries_match_independent_forward_matrix(matrix, limited):
    rgb = np.array([[255, 0, 0], [0, 255, 0], [0, 0, 255],
                    [255, 255, 0], [0, 255, 255], [255, 0, 255]], np.uint8)
    encoded = (rgb.astype(np.float64) / 255) @ MATRICES[matrix].T
    encoded *= (219, 224, 224) if limited else (255, 255, 255)
    encoded += (16, 128, 128) if limited else (0, 128, 128)
    samples = np.rint(encoded).clip(0, 255).astype(np.uint8)
    y = np.tile(samples[:, 0].repeat(2), (2, 1))
    u, v = samples[:, 1][None], samples[:, 2][None]
    actual = image_rgb(preview_frame_image(yuv_frame(y, u, v, matrix=matrix, limited=limited)))
    expected = np.tile(rgb.repeat(2, axis=0)[None], (2, 1, 1))
    assert np.max(np.abs(actual.astype(int) - expected.astype(int))) <= 2


def test_709_is_not_silently_decoded_as_601():
    y, u, v = np.full((2, 2), 63), np.array([[102]]), np.array([[240]])
    rgb709 = image_rgb(preview_frame_image(yuv_frame(y, u, v, matrix='709')))
    rgb601 = image_rgb(preview_frame_image(yuv_frame(y, u, v, matrix='601')))
    assert tuple(rgb709[0, 0]) == (255, 1, 0)
    assert rgb601[0, 0, 0] < 235
    assert not np.array_equal(rgb601, rgb709)


def test_chroma_replication_crosses_row_chunks_without_phase_shift():
    y = np.full((131, 5), 128, np.uint8)
    u = np.arange(66 * 3, dtype=np.uint8).reshape(66, 3)
    v = np.flip(u, axis=0).copy()
    actual = image_rgb(preview_frame_image(yuv_frame(y, u, v)))
    expected = reference(y, u, v, '709', True)
    assert np.max(np.abs(actual.astype(int) - expected.astype(int))) <= 1


@pytest.mark.parametrize('field,value', [
    ('setColorSpace', CS.ColorSpace_Undefined),
    ('setColorSpace', CS.ColorSpace_BT2020),
    ('setColorSpace', CS.ColorSpace_AdobeRgb),
    ('setColorRange', CR.ColorRange_Unknown),
    ('setColorTransfer', CT.ColorTransfer_ST2084),
    ('setColorTransfer', CT.ColorTransfer_STD_B67),
    ('setColorTransfer', CT.ColorTransfer_Unknown),
    ('setColorTransfer', CT.ColorTransfer_Linear),
    ('setColorTransfer', CT.ColorTransfer_Gamma22),
    ('setColorTransfer', CT.ColorTransfer_Gamma28),
])
def test_unknown_matrix_range_hdr_or_wide_gamut_rejected_before_map(field, value):
    frame = yuv_frame(np.full((2, 2), 128), [[128]], [[128]])
    getattr(frame.surface, field)(value)
    with pytest.raises(PreviewColorError) as error:
        preview_frame_image(frame)
    assert str(error.value) == 'Preview color conversion is unavailable for this frame.'
    assert error.value.reason
    assert frame.maps == frame.unmaps == 0


@pytest.mark.parametrize('transfer', [CT.ColorTransfer_BT601, CT.ColorTransfer_BT709])
def test_matrix_decoder_preserves_supported_sdr_transfer_codes_without_transform(transfer):
    frame = yuv_frame(np.full((2, 2), 128), [[128]], [[128]], limited=False)
    frame.surface.setColorTransfer(transfer)
    np.testing.assert_array_equal(image_rgb(preview_frame_image(frame)), 128)


@pytest.mark.parametrize('name', ['P010', 'P016', 'YUV420P10', 'Y8', 'Y16', 'Jpeg', 'IMC1', 'AYUV'])
def test_unsupported_pixel_formats_do_not_fall_back_to_qt(name):
    frame = MappedFrame(surface(2, 2, name), [np.zeros((2, 8), np.uint8)])
    with pytest.raises(PreviewColorError, match='Preview color conversion'):
        preview_frame_image(frame)
    assert frame.maps == frame.unmaps == 0


@pytest.mark.parametrize('size', [(0, 2), (-1, 2), (2, 0), (8193, 2), (4097, 4096)])
def test_invalid_or_excessive_dimensions_fail_before_mapping(size):
    frame = MappedFrame(surface(*size), [np.zeros((2, 8), np.uint8)])
    with pytest.raises(PreviewColorError):
        preview_frame_image(frame)
    assert frame.maps == frame.unmaps == 0


def test_invalid_frame_and_failed_mapping_are_not_unmapped():
    frame = yuv_frame(np.full((2, 2), 128), [[128]], [[128]])
    frame.valid = False
    with pytest.raises(PreviewColorError): preview_frame_image(frame)
    assert frame.maps == frame.unmaps == 0
    frame.valid = True
    frame.map_ok = False
    with pytest.raises(PreviewColorError): preview_frame_image(frame)
    assert frame.maps == 1 and frame.unmaps == 0


@pytest.mark.parametrize('damage', ['stride', 'negative_stride', 'size', 'short_buffer', 'plane_count', 'budget'])
def test_invalid_mapped_planes_always_unmap(damage):
    frame = yuv_frame(np.full((3, 5), 128), np.full((2, 3), 128), np.full((2, 3), 128))
    if damage == 'stride': frame.strides[1] = 5
    elif damage == 'negative_stride': frame.strides[0] = -1
    elif damage == 'size': frame.sizes[1] = 6
    elif damage == 'short_buffer': frame.planes[1] = bytearray(6)
    elif damage == 'plane_count': frame.planes.pop()
    elif damage == 'budget': frame.sizes[0] = preview.MAX_MAPPED_BYTES
    with pytest.raises(PreviewColorError): preview_frame_image(frame)
    assert frame.maps == frame.unmaps == 1 and not frame.mapped


def test_conversion_exception_unmaps_and_becomes_stable_error(monkeypatch):
    frame = yuv_frame(np.full((2, 2), 128), [[128]], [[128]])
    def broken(*args): raise RuntimeError('Simulated platform failure')
    monkeypatch.setattr(preview, '_yuv_image', broken)
    with pytest.raises(PreviewColorError) as error: preview_frame_image(frame)
    assert isinstance(error.value.__cause__, RuntimeError)
    assert frame.maps == frame.unmaps == 1 and not frame.mapped


def test_returned_image_does_not_alias_unmapped_planes():
    frame = yuv_frame(np.full((2, 2), 63), [[102]], [[240]])
    frame.erase_on_unmap = True
    image = preview_frame_image(frame)
    assert all(not any(plane) for plane in frame.planes)
    np.testing.assert_array_equal(image_rgb(image), [[[255, 1, 0]] * 2] * 2)
    del frame
    assert image.pixelColor(0, 0) == QColor(255, 1, 0)


@pytest.mark.parametrize('name,bytes_,premultiplied', [
    ('ARGB8888', [255, 24, 90, 170], False),
    ('ARGB8888_Premultiplied', [128, 12, 45, 85], True),
    ('XRGB8888', [0, 24, 90, 170], False),
    ('BGRA8888', [170, 90, 24, 255], False),
    ('BGRA8888_Premultiplied', [85, 45, 12, 128], True),
    ('BGRX8888', [170, 90, 24, 0], False),
    ('ABGR8888', [255, 170, 90, 24], False),
    ('XBGR8888', [0, 170, 90, 24], False),
    ('RGBA8888', [24, 90, 170, 255], False),
    ('RGBX8888', [24, 90, 170, 0], False),
])
def test_rgb_passthrough_byte_order_alpha_and_no_matrix_expansion(name, bytes_, premultiplied):
    frame = MappedFrame(F(QSize(3, 2), getattr(P, 'Format_' + name)),
                        [np.array([bytes_ * 3] * 2, np.uint8)])
    frame.erase_on_unmap = True
    image = preview_frame_image(frame)
    if premultiplied:
        assert image.format() == QImage.Format.Format_RGBA8888_Premultiplied
        assert image.pixelColor(1, 1).alpha() == 128
        # Preserve associated components exactly; QImage may round one code
        # during conversion to straight RGB, which is not matrix conversion.
        assert list(image.constBits()[:4]) == [12, 45, 85, 128]
    else:
        assert image.pixelColor(1, 1) == QColor(24, 90, 170, 255)
    assert frame.maps == frame.unmaps == 1


@pytest.mark.parametrize('image_format', [QImage.Format.Format_ARGB32,
    QImage.Format.Format_ARGB32_Premultiplied, QImage.Format.Format_RGB32,
    QImage.Format.Format_RGBA8888, QImage.Format.Format_RGBX8888])
def test_actual_qimage_video_frames_are_owned_rgb_compatible(image_format):
    original = QImage(5, 3, image_format)
    original.fill(QColor(24, 90, 170))
    frame = QVideoFrame(original)
    actual = preview_frame_image(frame)
    assert not frame.isMapped()
    assert actual.pixelColor(4, 2) == QColor(24, 90, 170)
    assert frame.map(QVideoFrame.MapMode.WriteOnly)
    frame.bits(0)[:] = bytes(frame.mappedBytes(0))
    frame.unmap()
    assert actual.pixelColor(4, 2) == QColor(24, 90, 170)


@pytest.mark.parametrize('name', YUV)
def test_actual_qt_allocated_even_yuv_planes(name):
    frame = QVideoFrame(surface(6, 4, name))
    assert frame.map(QVideoFrame.MapMode.WriteOnly)
    if name in ('UYVY', 'YUYV'):
        values = bytes([102, 63, 240, 63] if name == 'UYVY' else [63, 102, 63, 240])
        for row in range(4):
            start = row * frame.bytesPerLine(0)
            frame.bits(0)[start:start + 12] = values * 3
    else:
        frame.bits(0)[:] = bytes([63]) * frame.mappedBytes(0)
        if name in ('NV12', 'NV21'):
            pair = bytes([102, 240] if name == 'NV12' else [240, 102])
            frame.bits(1)[:] = pair * (frame.mappedBytes(1) // 2)
        else:
            for index, value in enumerate((240, 102) if name == 'YV12' else (102, 240), 1):
                frame.bits(index)[:] = bytes([value]) * frame.mappedBytes(index)
    frame.unmap()
    actual = image_rgb(preview_frame_image(frame))
    np.testing.assert_array_equal(actual, np.tile([255, 1, 0], (4, 6, 1)))
    assert not frame.isMapped()


def test_existing_read_only_mapping_is_balanced_not_stolen():
    image = QImage(2, 2, QImage.Format.Format_RGB32)
    image.fill(QColor(24, 90, 170))
    frame = QVideoFrame(image)
    assert frame.map(QVideoFrame.MapMode.ReadOnly)
    try:
        actual = preview_frame_image(frame)
        assert actual.pixelColor(1, 1) == QColor(24, 90, 170)
        assert frame.isMapped()
    finally:
        frame.unmap()
    assert not frame.isMapped()


def test_bottom_to_top_scanlines_are_reversed():
    y = np.array([[16, 16], [235, 235]], np.uint8)
    frame = yuv_frame(y, [[128]], [[128]])
    frame.surface.setScanLineDirection(F.Direction.BottomToTop)
    actual = image_rgb(preview_frame_image(frame))
    np.testing.assert_array_equal(actual[0], 255)
    np.testing.assert_array_equal(actual[1], 0)

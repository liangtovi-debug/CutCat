"""Independent decoded-plane and RGB checks for metadata-aware preview conversion.

The reference is the system FFmpeg executable, not the application's converter
or QVideoFrame.toImage(). FFmpeg is instructed to use the tagged matrix/range and
nearest-neighbour chroma expansion, matching this deliberately bounded 8-bit
preview path. A maximum of two 8-bit RGB codes allows different integer/float
rounding; it does not excuse a matrix/range mismatch or certify a monitor.
"""
from fractions import Fraction
import json
import os
from pathlib import Path
import subprocess
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest
from PySide6.QtCore import QUrl, qVersion
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame, QVideoFrameFormat, QVideoSink
from PySide6.QtWidgets import QApplication

from lumen.media import ffmpeg_available, ffmpeg_binary, ffprobe_binary


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def full_rgb(image):
    """Independent full-resolution extraction, not the downsampled scope helper."""
    image = image.convertToFormat(QImage.Format.Format_RGB888)
    data = np.frombuffer(image.constBits(), np.uint8, count=image.sizeInBytes())
    return data.reshape(image.height(), image.bytesPerLine())[:, :image.width() * 3].reshape(
        image.height(), image.width(), 3).copy()


def ff(*args, data=None):
    return subprocess.run([ffmpeg_binary(), '-hide_banner', '-loglevel', 'error',
                           '-nostdin', '-y', '-threads', '1', *map(str, args)],
                          input=data, capture_output=True, check=True, timeout=60).stdout


def synthetic_yuv420(width, height, count=6, full=False):
    """Padded-decoder strides, legal neutral extremes, ramps and colored edges."""
    # Vertical eight-pixel patches ensure that real chroma boundaries occur.
    uv = np.array([[128, 128], [90, 170], [170, 100], [64, 192],
                   [192, 64], [128, 128]], dtype=np.uint8)
    columns = np.arange(width // 2) // 8 % len(uv)
    u = np.repeat(uv[columns, 0][None], height // 2, axis=0)
    v = np.repeat(uv[columns, 1][None], height // 2, axis=0)
    low, high = (0, 255) if full else (16, 235)
    base = np.rint(np.linspace(low, high, height)).astype(np.int16)
    frames = []
    for n in range(count):
        y = np.repeat(np.clip(base + n * 3, low, high)[:, None], width, axis=1).astype(np.uint8)
        # Keep exact black/white rows for range expansion assertions.
        y[:2] = low
        y[-2:] = high
        frames.append(y.tobytes() + u.tobytes() + v.tobytes())
    return b''.join(frames)


def encode_fixture(path, width=98, height=66, count=6, *, matrix='bt709', full=False, fps=Fraction(30)):
    raw = synthetic_yuv420(width, height, count, full)
    ff('-f', 'rawvideo', '-pixel_format', 'yuv420p', '-video_size', f'{width}x{height}',
       '-framerate', str(fps), '-colorspace', matrix, '-color_range', 'pc' if full else 'tv',
       '-color_trc', 'bt709', '-color_primaries', 'bt709', '-i', 'pipe:0', '-an', '-c:v', 'libx264', '-preset', 'ultrafast',
       '-qp', '0', '-pix_fmt', 'yuv420p', '-colorspace', matrix, '-color_range', 'pc' if full else 'tv',
       '-color_trc', 'bt709', '-color_primaries', 'bt709', path, data=raw)
    return raw


def independent_rgb(path, width, height, *, matrix, full):
    # Explicit matrix/range eliminates libswscale's untagged SD/HD heuristic.
    # full_chroma_int avoids a different chroma shortcut in its RGB packer.
    filter_ = (f'scale=in_color_matrix={matrix}:in_range={"pc" if full else "tv"}'
               ':out_range=pc:flags=neighbor+accurate_rnd+full_chroma_int')
    return np.frombuffer(ff('-i', path, '-an', '-vf', filter_, '-pix_fmt', 'rgb24',
                            '-f', 'rawvideo', 'pipe:1'), np.uint8).reshape(-1, height, width, 3)


def visible_yuv420(frame):
    """Copy visible planes only, never assume decoder stride equals width."""
    assert frame.map(QVideoFrame.MapMode.ReadOnly)
    try:
        width, height = frame.width(), frame.height()
        fmt = frame.pixelFormat()
        planar = (QVideoFrameFormat.PixelFormat.Format_YUV420P,
                  QVideoFrameFormat.PixelFormat.Format_YV12)
        semiplanar = (QVideoFrameFormat.PixelFormat.Format_NV12,
                      QVideoFrameFormat.PixelFormat.Format_NV21)
        assert fmt in (*planar, *semiplanar), fmt
        dimensions = [(height, width), (height // 2, width // 2), (height // 2, width // 2)]
        if fmt in semiplanar:
            dimensions = [(height, width), (height // 2, width)]
        result = []
        for plane, (rows, columns) in enumerate(dimensions):
            stride = frame.bytesPerLine(plane)
            values = np.frombuffer(frame.bits(plane), np.uint8, count=frame.mappedBytes(plane))
            result.append(np.ndarray((rows, columns), np.uint8, buffer=values,
                                     strides=(stride, 1)).copy())
        if fmt in semiplanar:
            result = [result[0], result[1][:, 0::2], result[1][:, 1::2]]
        if fmt in (QVideoFrameFormat.PixelFormat.Format_YV12,
                   QVideoFrameFormat.PixelFormat.Format_NV21):
            result[1], result[2] = result[2], result[1]
        return b''.join(part.tobytes() for part in result)
    finally:
        frame.unmap()


def decode_actual(app, path, fps, count):
    player, sink = QMediaPlayer(), QVideoSink()
    player.setVideoSink(sink)
    frames = {}

    def receive(frame):
        if frame.isValid():
            index = round(Fraction(frame.startTime(), 1_000_000) * fps)
            frames[index] = QVideoFrame(frame)

    sink.videoFrameChanged.connect(receive)
    try:
        player.setSource(QUrl.fromLocalFile(str(path.resolve())))
        player.play()
        deadline = time.monotonic() + 15
        while len(frames) < count and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)
        assert set(frames) == set(range(count)), (sorted(frames), player.errorString())
        return frames
    finally:
        player.stop()
        player.setSource(QUrl())
        player.deleteLater()
        sink.deleteLater()
        app.processEvents()


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def save_measurement(name, result):
    destination = os.environ.get('LUMEN_PREVIEW_COLOR_ARTIFACTS')
    if destination:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (name + '.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')


@pytest.mark.parametrize('fps', [Fraction(30), Fraction(30000, 1001)], ids=['30', '29.97'])
@pytest.mark.parametrize('matrix,full', [('bt709', False), ('bt709', True),
                                         ('smpte170m', False), ('smpte170m', True)],
                         ids=['709-limited', '709-full', '601-limited', '601-full'])
def test_actual_h264_planes_metadata_and_rgb_match_independent_ffmpeg(app, tmp_path, matrix, full, fps):
    from lumen.preview_color import PreviewColorError, preview_frame_image

    width, height, count = 98, 66, 6
    path = tmp_path / 'tagged-patches-and-ramp.mp4'
    raw = encode_fixture(path, width, height, count, matrix=matrix, full=full, fps=fps)
    tagged = json.loads(subprocess.run(
        [ffprobe_binary(), '-v', 'error', '-select_streams', 'v:0', '-show_entries',
         'stream=pix_fmt,color_space,color_range,color_transfer,color_primaries',
         '-of', 'json', str(path)], capture_output=True, text=True, check=True, timeout=30).stdout)['streams'][0]
    assert tagged['color_space'] == matrix
    assert tagged['color_range'] == ('pc' if full else 'tv')
    assert tagged['color_transfer'] == tagged['color_primaries'] == 'bt709'
    independent = independent_rgb(path, width, height, matrix=matrix, full=full)
    frames = decode_actual(app, path, fps, count)
    expected_space = (QVideoFrameFormat.ColorSpace.ColorSpace_BT709 if matrix == 'bt709'
                      else QVideoFrameFormat.ColorSpace.ColorSpace_BT601)
    expected_range = (QVideoFrameFormat.ColorRange.ColorRange_Full if full
                      else QVideoFrameFormat.ColorRange.ColorRange_Video)
    frame_bytes = width * height * 3 // 2
    measurements = []
    images = []
    for index, frame in sorted(frames.items()):
        fmt = frame.surfaceFormat()
        assert abs(frame.startTime() - float(index / fps * 1_000_000)) <= 1
        if (fmt.colorSpace() == QVideoFrameFormat.ColorSpace.ColorSpace_Undefined
                or fmt.colorRange() == QVideoFrameFormat.ColorRange.ColorRange_Unknown):
            # Qt's FFmpeg fallback for full-range YUVJ420P can remap the planes
            # and discard colorSpace. Never guess that this is still source YUV.
            # Native backends that preserve valid metadata use the normal
            # independent-RGB branch below instead.
            assert full, 'Normalized limited-range preview metadata must survive decoding'
            with pytest.raises(PreviewColorError):
                preview_frame_image(frame)
            assert not frame.isMapped()
            remapped = visible_yuv420(frame)
            source = raw[index * frame_bytes:(index + 1) * frame_bytes]
            measurements.append({'frame': index, 'conversion_rejected': True,
                                 'decoded_planes_equal_source': remapped == source,
                                 'decoded_first_luma': remapped[0], 'source_first_luma': source[0],
                                 'reason': 'Qt decoder dropped matrix or range metadata',
                                 'decoded_color_space': str(fmt.colorSpace()),
                                 'decoded_color_range': str(fmt.colorRange()),
                                 'decoded_pixel_format': str(fmt.pixelFormat())})
            continue
        assert fmt.colorSpace() == expected_space
        assert fmt.colorRange() == expected_range
        assert abs(frame.startTime() - float(index / fps * 1_000_000)) <= 1
        assert not frame.isMapped()
        before = visible_yuv420(frame)
        # Lossless encode + native decoder equality isolates RGB conversion.
        assert before == raw[index * frame_bytes:(index + 1) * frame_bytes]
        image = preview_frame_image(frame)
        assert not image.isNull() and not frame.isMapped()
        assert visible_yuv420(frame) == before
        actual = full_rgb(image)
        delta = np.abs(actual.astype(np.int16) - independent[index].astype(np.int16))
        assert delta.max() <= 2, (matrix, full, index, int(delta.max()), float(delta.mean()))
        # The first patch has neutral chroma: black/white must expand exactly.
        assert np.all(actual[:2, :16] == 0)
        assert np.all(actual[-2:, :16] == 255)
        images.append((image, actual.copy()))
        old_delta = np.abs(full_rgb(frame.toImage()).astype(np.int16)
                           - independent[index].astype(np.int16))
        measurements.append({'frame': index, 'start_us': frame.startTime(),
                             'corrected_max_codes': int(delta.max()),
                             'corrected_mean_codes': float(delta.mean()),
                             'original_qt_max_codes': int(old_delta.max()),
                             'original_qt_mean_codes': float(old_delta.mean())})
    # Returned images must remain valid after every decoder frame is released.
    frames.clear()
    del frame
    for image, copied in images:
        np.testing.assert_array_equal(full_rgb(image), copied)
    save_measurement(f'{matrix}-{"full" if full else "limited"}-{fps.numerator}-{fps.denominator}',
                     {'qt_version': qVersion(), 'size': [width, height], 'fps': str(fps),
                      'matrix': matrix, 'full_range': full, 'source_stream_tags': tagged,
                      'decoded_planes_exact': bool(images),
                      'rejected_ambiguous_frames': sum(bool(m.get('conversion_rejected')) for m in measurements),
                      'rgb_reference': 'FFmpeg scale nearest/accurate_rnd/full_chroma_int',
                      'tolerance_8bit_codes': 2, 'frames': measurements})

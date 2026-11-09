"""Picker restoration agrees with independently predicted real delivery pixels."""
from copy import deepcopy
from fractions import Fraction

import numpy as np
import pytest

from lumen.color_picker_source import capture_picker_snapshot
from lumen.model import Grade
from lumen.render import RenderEngine
from lumen.restoration_settings import RestorationSettings
from lumen.timing import ClipTimeMap
from test_color_picker_restoration import (ff, make_source, normalize_reference,
                                           primary_reference, restoration_reference)
from lumen.media import ffmpeg_available

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def yuv_reference(rgb, ten_bit):
    red, green, blue = np.moveaxis(np.asarray(rgb, dtype=float), -1, 0)
    luma = .2126 * red + .7152 * green + .0722 * blue
    scale = 4 if ten_bit else 1
    return scale * np.stack([16 + 219 * luma,
                             128 + 224 * (blue - luma) / (2 * (1 - .0722)),
                             128 + 224 * (red - luma) / (2 * (1 - .2126))], axis=-1)


def decode_yuv(path, width, height, ten_bit):
    fmt, dtype = ('yuv422p10le', '<u2') if ten_bit else ('yuv420p', 'u1')
    raw = ff('-i', path, '-map', '0:V:0', '-an', '-pix_fmt', fmt, '-f', 'rawvideo', 'pipe:1')
    count, chroma = width * height, width * height // (2 if ten_bit else 4)
    frames = np.frombuffer(raw, dtype=dtype).reshape(-1, count + 2 * chroma)
    return [frames[:, :count].reshape(-1, height, width).astype(float),
            frames[:, count:count + chroma].reshape(-1, height if ten_bit else height // 2, width // 2).astype(float),
            frames[:, count + chroma:].reshape(-1, height if ten_bit else height // 2, width // 2).astype(float)]


def subsample_reference(full, channel, ten_bit):
    plane = full[..., channel]
    if not channel:
        return plane
    count, height, width = plane.shape
    vertical = 1 if ten_bit else 2
    return plane.reshape(count, height // vertical, vertical, width // 2, 2).mean(axis=(2, 4))


@pytest.mark.parametrize('creative', [False, True], ids=['creative-bypassed', 'primary-rgb-enabled'])
def test_restored_picker_matches_lossless_stage_preview_and_prores(tmp_path, creative):
    width, height = 96, 64
    yy, xx = np.mgrid[:height, :width]
    texture = .025 * (2 * ((xx // 2 + yy // 2) % 2) - 1)
    rgb = np.array([.24, .41, .62])[None, None, :] + texture[..., None]
    frames = np.rint(65535 * np.stack([rgb + index * .025 for index in range(7)])).astype('<u2')
    project, frames = make_source(tmp_path, frames, transfer='iec61966-2-1')
    clip = project.clips[0]
    clip.duration, clip.source_in = 3, Fraction(3, 2)
    clip.time_map = ClipTimeMap(Fraction(3, 2), Fraction(6), Fraction(3, 2))
    clip.restoration = RestorationSettings(enabled=True, deblock=.5, denoise=1., sharpen=.25)
    clip.grade = Grade(enabled=creative, input_encoding='srgb', exposure=.15,
                       temperature=.2, tint=.1,
                       red_curve=[[0., 0.], [.5, .6], [1., 1.]])
    expected = []
    unprocessed = []
    for local, index in enumerate((1, 3, 4)):
        normalized = normalize_reference(frames[index].astype(float) / 65535, 'srgb')
        restored = restoration_reference(normalized, clip.restoration)
        expected.append(primary_reference(restored, clip.grade))
        unprocessed.append(primary_reference(normalized, clip.grade))
        snapshot = capture_picker_snapshot(project, clip.id, local)
        assert snapshot.source_frame == index
        np.testing.assert_allclose(snapshot.frame, expected[-1], rtol=0, atol=6e-7)
    expected, unprocessed = np.asarray(expected), np.asarray(unprocessed)
    assert expected.min() > 0 and expected.max() < 1
    assert np.abs(expected - unprocessed).mean() > .004, 'Fixture must expose an omitted restoration stage'
    engine = RenderEngine(timeout=90)
    try:
        stage = engine._graded_segment(project.media[0], clip, project, None)
        rgb16 = np.frombuffer(ff('-i', stage, '-pix_fmt', 'rgb48le', '-f', 'rawvideo', 'pipe:1'),
                              dtype='<u2').reshape(3, height, width, 3)
        # A separate lossless decode checks every pixel, including time-map phase.
        np.testing.assert_allclose(rgb16, np.rint(expected * 65535), atol=1, rtol=0)
        for profile in ('preview', 'prores_422_hq_10bit'):
            ten = profile.startswith('prores')
            output = (engine.preview(project) if profile == 'preview' else engine.render(
                project, tmp_path / 'restored.mov', export_profile=profile))
            planes = decode_yuv(output, width, height, ten)
            reference = yuv_reference(expected, ten)
            wrong = yuv_reference(unprocessed, ten)
            errors, omitted_errors = [], []
            for channel, plane in enumerate(planes):
                target = subsample_reference(reference, channel, ten)
                omitted = subsample_reference(wrong, channel, ten)
                # Exclude the codec's picture-edge extension, not any interior
                # texture. Limits are in native 8/10-bit decoded code values.
                actual = plane[:, 4:-4, 4:-4]
                error = np.abs(actual - target[:, 4:-4, 4:-4])
                assert error.mean() < (1.1 if ten else 1.25), (profile, channel, error.mean())
                assert np.quantile(error, .99) < (3. if ten else 4.), (profile, channel, error.max())
                errors.append(error.mean())
                omitted_errors.append(np.abs(actual - omitted[:, 4:-4, 4:-4]).mean())
            assert errors[0] < omitted_errors[0] * .65, \
                f'{profile} luma must follow restored samples, not the original source'
    finally:
        engine.close()

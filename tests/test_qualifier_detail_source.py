"""Exact detail samples survive sparse overview loss and drive real delivery.

The 4K single-pixel fixture is deliberately invisible in the old 512-bounded
stride grid. Codec acceptance uses larger patches: chroma-subsampled delivery
cannot preserve an isolated 4K RGB pixel, and must not be its sampling oracle.
All expected colors use independent float64 scalar references.
"""
from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest

from lumen.color_picker_source import capture_picker_snapshot
from lumen.media import ffmpeg_available
from lumen.model import Grade
from lumen.qualifier import qualifier_mask, QualifierSettings
from lumen.qualifier_picker import initialize_from_sample
from lumen.restoration_settings import RestorationSettings
from test_color_picker_restoration import primary_reference, restoration_reference
from test_color_picker_restoration_render import (decode_yuv, subsample_reference,
                                                  yuv_reference)
from test_qualifier_picker_render import independent_ranges
from test_qualifier_render import (decode_rgb, engine, mask_reference,
                                   qualifier_reference, selective_reference,
                                   source_project)

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def test_4k_single_rgb16_pixel_survives_exact_snapshot_but_not_sparse512(tmp_path):
    height, width = 2160, 3840
    image = np.empty((height, width, 3), dtype='<u2')
    image[:] = [13003, 29009, 47017]
    # Neither coordinate lies on the eight-pixel legacy overview lattice.
    y, x = 1083, 1925
    detail = np.array([48013, 11027, 7019], dtype='<u2')
    image[y, x] = detail
    project, _ = source_project(tmp_path, frames=image[None], name='4k-detail')
    before = deepcopy(project)
    snapshot = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    assert snapshot.frame.shape == (height, width, 3)
    assert snapshot.frame.dtype == np.float32 and snapshot.valid_mask[y, x]
    assert snapshot.stage == 'qualifier'
    exact = detail.astype(np.float32) / np.float32(65535)
    np.testing.assert_array_equal(snapshot.frame[y, x], exact)
    # Positive precision control: display quantization would change the sample.
    assert np.max(abs(exact - np.rint(exact * 255) / 255)) > 1e-4
    stride = (max(height, width) + 511) // 512
    assert stride == 8 and y % stride and x % stride
    overview = snapshot.frame[::stride, ::stride]
    background = image[0, 0].astype(np.float32) / np.float32(65535)
    np.testing.assert_array_equal(overview, np.broadcast_to(background, overview.shape))
    base = QualifierSettings(enabled=True, exposure=.2)
    picked, sample = initialize_from_sample(snapshot.frame[y, x], base)
    np.testing.assert_array_equal(sample.rgb, exact)
    expected = independent_ranges(detail.astype(float) / 65535, base)
    assert mask_reference(exact[None], expected)[0] == 1
    assert mask_reference(background[None], expected)[0] == 0
    assert qualifier_mask(exact[None], picked)[0] == 1
    assert not qualifier_mask(overview, picked).any()
    assert project == before
    assert not snapshot.frame.flags.writeable


@pytest.mark.parametrize('mode', ['corrected', 'disabled', 'neutral'])
def test_detail_sample_stage_and_deliveries_follow_restoration_primary_and_curves(
        tmp_path, engine, mode):
    height, width = 64, 128
    yy, xx = np.mgrid[:height, :width]
    image = np.empty((height, width, 3), dtype=float)
    image[:, :64] = [.60, .23, .15]
    image[:, 64:] = [.15, .45, .54]
    # Denoise has a measurable effect without destroying the broad color key.
    image += (.006 * (2 * ((xx // 2 + yy // 2) % 2) - 1))[..., None]
    frames = np.rint(image[None] * 65535).astype('<u2')
    base = QualifierSettings(enabled=mode != 'disabled', exposure=.2,
                             saturation=.75, hue_shift=.11)
    if mode == 'neutral':
        base = replace(base, exposure=0, saturation=1, hue_shift=0)
    grade = Grade(exposure=-.1, temperature=.04, tint=.02,
                  red_curve=[[0., .01], [.5, .46], [1., .93]],
                  hue_hue_curve=[[0., .025], [1., .025]],
                  hue_sat_curve=[[0., .9], [1., .9]], qualifier=base)
    project, frames = source_project(tmp_path, frames=frames, grade=grade, name=mode)
    clip = project.clips[0]
    clip.restoration = RestorationSettings(enabled=True, denoise=.8, sharpen=.2)
    before = deepcopy(project)
    incoming = frames[0].astype(float) / 65535
    restored = restoration_reference(incoming, clip.restoration)
    prekey = selective_reference(primary_reference(restored, grade), grade)
    omitted = selective_reference(primary_reference(incoming, grade), grade)
    assert np.max(abs(prekey - omitted)) > .001
    snapshot = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    assert snapshot.restoration_applied
    np.testing.assert_allclose(snapshot.frame, prekey, atol=7e-7, rtol=0)
    y, x = 31, 29
    picked, sample = initialize_from_sample(snapshot.frame[y, x], base)
    np.testing.assert_array_equal(sample.rgb, snapshot.frame[y, x])
    expected_settings = independent_ranges(prekey[y, x], base)
    for field in ('hue_center', 'hue_width', 'saturation_low', 'saturation_high',
                  'luma_low', 'luma_high'):
        assert getattr(picked, field) == pytest.approx(getattr(expected_settings, field), abs=7e-7)
    assert project == before
    # Enabled is not implicitly changed by initializing ranges from a detail.
    assert picked.enabled == base.enabled
    baseline_rgb, baseline_yuv = None, {}
    if mode != 'corrected':
        # Sampling alone must preserve the actual decoded result, not just
        # remain within a codec tolerance of an analytical approximation.
        baseline_rgb = decode_rgb(engine._graded_segment(
            project.media[0], clip, project, None), width, height)
        for delivery in ('preview', 'prores_422_hq_10bit'):
            ten = delivery.startswith('prores')
            output = (engine.preview(project) if not ten else engine.render(
                project, tmp_path / f'{mode}-baseline.mov', export_profile=delivery))
            baseline_yuv[delivery] = decode_yuv(output, width, height, ten)
    clip.grade.qualifier = picked
    expected = qualifier_reference(prekey, expected_settings)
    if mode == 'corrected':
        assert mask_reference(prekey[[y], [x]], expected_settings)[0] == 1
        assert mask_reference(prekey[[y], [96]], expected_settings)[0] == 0
        assert np.max(abs(expected[y, x] - prekey[y, x])) > .05
    else:
        np.testing.assert_array_equal(expected, prekey)
    # Correction itself cannot feed back into a later pre-qualifier sample.
    fresh = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    np.testing.assert_array_equal(fresh.frame, snapshot.frame)
    stage = engine._graded_segment(project.media[0], clip, project, None)
    actual_rgb = decode_rgb(stage, width, height)
    np.testing.assert_allclose(actual_rgb, expected, atol=3 / 65535, rtol=0)
    if baseline_rgb is not None:
        np.testing.assert_array_equal(actual_rgb, baseline_rgb)
    for delivery in ('preview', 'prores_422_hq_10bit'):
        ten = delivery.startswith('prores')
        output = (engine.preview(project) if not ten else engine.render(
            project, tmp_path / f'{mode}.mov', export_profile=delivery))
        reference = yuv_reference(expected[None], ten)
        for channel, actual in enumerate(decode_yuv(output, width, height, ten)):
            if delivery in baseline_yuv:
                np.testing.assert_array_equal(actual, baseline_yuv[delivery][channel])
            target = subsample_reference(reference, channel, ten)
            # Measure both patch interiors; exclude the chroma transition and
            # codec edge extension, not the spatial-restoration texture.
            sx, sy = (1 if channel == 0 else 2), (2 if channel and not ten else 1)
            for left, right in ((12, 52), (76, 116)):
                region = (slice(None), slice(12 // sy, 52 // sy),
                          slice(left // sx, right // sx))
                error = abs(actual[region] - target[region])
                assert error.mean() < (1.2 if ten else 1.5), (delivery, channel, error.mean())
                assert np.quantile(error, .99) < (3. if ten else 4.), (delivery, channel, error.max())

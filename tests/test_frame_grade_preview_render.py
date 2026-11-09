"""Independent RGB16 acceptance for the complete pre-composite grade stage.

Scalar/float64 references predict input transfer, restoration, WB, primary/RGB,
selective curves, spatial qualification and a nontrivial trilinear LUT. A real
lossless render segment is decoded independently; comparing two calls to the
production grade evaluator is deliberately not the acceptance oracle.
"""
from copy import deepcopy
from fractions import Fraction

import numpy as np
import pytest

from lumen.frame_grade_preview import capture_grade_preview_snapshot, GradePreviewError
from lumen.media import ffmpeg_available
from lumen.model import Grade
from lumen.qualifier import QualifierSettings
from lumen.render import RenderEngine
from lumen.restoration_settings import RestorationSettings
from lumen.timing import ClipTimeMap
from test_color_picker_restoration import (ff, make_source, normalize_reference,
                                           primary_reference, restoration_reference)
from test_qualifier_render import selective_reference

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
LUMA = np.array([.2126, .7152, .0722])


def white_balance_reference(rgb, grade):
    x = np.asarray(rgb, float)
    if not grade.white_balance_enabled:
        return x.copy()
    a = abs(x)
    linear = np.sign(x) * np.where(a < .081, a / 4.5, ((a + .099) / 1.099) ** (1 / .45))
    linear *= np.array(grade.white_balance_gains)
    a = abs(linear)
    return np.sign(linear) * np.where(a < .018, 4.5 * a, 1.099 * a ** .45 - .099)


def spatial_qualifier_reference(rgb, settings):
    """Independent luma-only matte, levels -> morphology -> smooth -> window."""
    y = rgb @ LUMA
    matte = np.ones_like(y)
    matte[y < settings.luma_low] = np.maximum(0., 1 - (
        settings.luma_low - y[y < settings.luma_low]) / settings.luma_softness)
    matte[y > settings.luma_high] = np.maximum(0., 1 - (
        y[y > settings.luma_high] - settings.luma_high) / settings.luma_softness)
    in_range = np.all((rgb >= 0) & (rgb <= 1), axis=-1)
    matte[~in_range] = 0
    matte = np.clip((matte - settings.matte_black) /
                    (settings.matte_white - settings.matte_black), 0, 1)
    h, w = matte.shape
    if settings.matte_morphology:
        padded = np.pad(matte, 1, mode='edge')
        neighbors = np.stack([padded[dy:dy+h, dx:dx+w]
                              for dy in range(3) for dx in range(3)])
        filtered = neighbors.max(axis=0) if settings.matte_morphology > 0 else neighbors.min(axis=0)
        matte += abs(settings.matte_morphology) * (filtered - matte)
    if settings.matte_smoothing:
        padded = np.pad(matte, 1, mode='edge')
        filtered = np.zeros_like(matte)
        for dy, wy in enumerate((1, 2, 1)):
            for dx, wx in enumerate((1, 2, 1)):
                filtered += padded[dy:dy+h, dx:dx+w] * (wy * wx / 16)
        matte += settings.matte_smoothing * (filtered - matte)
    matte[~in_range] = 0
    if settings.window_shape != 'off':
        yy, xx = np.mgrid[:h, :w]
        dx = ((xx + .5) / w - settings.window_center_x) * 2 / settings.window_width
        dy = ((yy + .5) / h - settings.window_center_y) * 2 / settings.window_height
        radius = np.hypot(dx, dy) if settings.window_shape == 'ellipse' else np.maximum(abs(dx), abs(dy))
        window = np.clip(1 - (radius - 1) / settings.window_feather, 0, 1)
        if settings.window_invert:
            window = 1 - window
        matte *= window
    adjusted = (y[..., None] + settings.saturation * (rgb - y[..., None])) * 2 ** settings.exposure
    return rgb + matte[..., None] * (adjusted - rgb)


def lut_reference(rgb, strength):
    # This polynomial is exactly representable by trilinear interpolation of
    # the eight LUT corners, but it is not an identity or channel-only ramp.
    r, g, b = np.moveaxis(np.clip(rgb, 0, 1), -1, 0)
    mapped = np.stack([.05 + .7 * b + .1 * r * g,
                       .03 + .8 * r, .02 + .75 * g + .08 * b * r], axis=-1)
    return rgb * (1 - strength) + mapped * strength


def complete_reference(frame, clip, canvas_height, *, omit=None):
    rgb = normalize_reference(frame.astype(float) / 65535, clip.grade.input_encoding)
    if omit != 'restoration':
        rgb = restoration_reference(rgb, clip.restoration)
    active_height, width = frame.shape[:2]
    canvas = np.zeros((canvas_height, width, 3))
    top = (canvas_height - active_height) // 2
    canvas[top:top + active_height] = rgb
    rgb = primary_reference(white_balance_reference(canvas, clip.grade), clip.grade)
    if omit != 'selective':
        rgb = selective_reference(rgb, clip.grade)
    if omit != 'qualifier':
        rgb = spatial_qualifier_reference(rgb, clip.grade.qualifier)
    return rgb if omit == 'lut' else lut_reference(rgb, clip.grade.lut_strength)


@pytest.mark.parametrize('shape,morphology', [('ellipse', .65), ('rectangle', -.55)])
def test_complete_current_frame_matches_independent_rgb16_clip_render(tmp_path, shape, morphology):
    width, height = 48, 32
    yy, xx = np.mgrid[:height, :width]
    texture = (((11 * xx + 7 * yy) % 13) - 6) * .00065 + .025 * ((xx // 8) % 2)
    base = np.stack([.18 + .25 * xx / width + texture,
                     .3 + .1 * yy / height - .7 * texture,
                     .57 - .18 * xx / width + .5 * texture], axis=-1)
    frames = np.rint(65535 * np.stack([base + k * .007 for k in range(7)])).astype('<u2')
    project, frames = make_source(tmp_path, frames, name='full-grade', transfer='iec61966-2-1')
    project.height = 48  # Padding must stay out of restoration, but in grading.
    clip = project.clips[0]
    clip.start, clip.source_in, clip.duration = 5, Fraction(3, 2), 3
    clip.time_map = ClipTimeMap(Fraction(3, 2), Fraction(6), Fraction(3, 2))
    clip.restoration = RestorationSettings(enabled=True, deblock=.7, denoise=.8, sharpen=.4)
    clip.grade = Grade(input_encoding='srgb', exposure=.12, contrast=1.05,
        temperature=.08, tint=.05, white_balance_enabled=True,
        white_balance_gains=[1.03, .97, 1.04],
        red_curve=[[0., 0.], [.5, .53], [1., 1.]],
        hue_hue_curve=[[0., .025], [1., .025]],
        hue_sat_curve=[[0., .86], [1., .86]])
    clip.grade.qualifier = QualifierSettings(enabled=True, hue_enabled=False,
        saturation_enabled=False, luma_low=.29, luma_high=.40, luma_softness=.08,
        exposure=.45, saturation=.85, matte_black=.1, matte_white=.9,
        matte_morphology=morphology, matte_smoothing=.7,
        window_shape=shape, window_center_x=.48, window_center_y=.52,
        window_width=.6, window_height=.65, window_feather=.2)
    lut = tmp_path / 'cross-channel.cube'
    corners = [lut_reference(np.array([r, g, b]), 1.)
               for b in (0, 1) for g in (0, 1) for r in (0, 1)]
    lut.write_text('LUT_3D_SIZE 2\n' + ''.join(' '.join(map(str, c)) + '\n' for c in corners))
    clip.grade.lut_path, clip.grade.lut_strength = str(lut), .65
    # It is the selected-clip grade stage, even if clip geometry is nonidentity.
    clip.transform.mirror_x, clip.transform.x = True, 7.
    before = deepcopy(clip)
    references, snapshots = [], []
    for local, source_index in enumerate((1, 3, 4)):
        expected = complete_reference(frames[source_index], clip, project.height)
        result = capture_grade_preview_snapshot(project, clip.id, clip.start + local)
        assert result.source_frame == source_index and result.source_time == Fraction(source_index, 6)
        assert result.active_rect == (0, 8, 48, 32) and result.restoration_applied
        assert result.valid_mask[8:40].all() and not result.valid_mask[:8].any()
        np.testing.assert_allclose(result.frame, expected, atol=1.2e-6, rtol=0)
        for omitted in ('restoration', 'selective', 'qualifier', 'lut'):
            wrong = complete_reference(frames[source_index], clip, project.height, omit=omitted)
            assert np.max(abs(expected - wrong)) > .0004, f'Fixture must expose omitted {omitted}'
        coarse = np.rint(frames[source_index].astype(float) / 65535 * 255) / 255
        coarse = np.rint(coarse * 65535).astype('<u2')
        assert np.max(abs(result.frame - complete_reference(coarse, clip, project.height))) > .0003
        references.append(expected)
        snapshots.append(result.frame)
    assert clip == before
    engine = RenderEngine(timeout=90)
    try:
        stage = engine._graded_segment(project.media[0], clip, project, None)
        decoded = np.frombuffer(ff('-i', stage, '-pix_fmt', 'rgb48le', '-f', 'rawvideo', 'pipe:1'),
                                dtype='<u2').reshape(3, 48, 48, 3)
        # One native 16-bit code is the complete tolerance, not an RGB8/codecs
        # allowance. Every pixel, including matte boundaries and pads, counts.
        expected_codes = np.rint(np.clip(references, 0, 1) * 65535)
        np.testing.assert_allclose(decoded, expected_codes, atol=1, rtol=0)
        np.testing.assert_allclose(decoded, np.rint(np.clip(snapshots, 0, 1) * 65535), atol=1, rtol=0)
    finally:
        engine.close()


def test_real_alpha_source_is_rejected(tmp_path):
    from PIL import Image
    project, _ = make_source(tmp_path, np.full((8, 12, 3), 30001, '<u2'))
    transparent = tmp_path / 'alpha.png'
    Image.new('RGBA', (12, 8), (40, 70, 90, 120)).save(transparent)
    project.media[0].path = str(transparent)
    with pytest.raises(GradePreviewError, match='transparent'):
        capture_grade_preview_snapshot(project, project.clips[0].id, 0)

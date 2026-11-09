"""Independent renderer-boundary regression coverage for current-frame grading.

Capture the renderer's actual decoded/normalized full-frame result immediately
before its RGB16 delivery clamp. This catches drift in the source, temporal,
SAR/padding, restoration, and spatial-key bridge, not merely grade arithmetic.
"""
from fractions import Fraction
import shutil
import subprocess

import numpy as np
import pytest

from lumen.frame_grade_preview import capture_grade_preview_snapshot
from lumen.model import Clip, ClipEffect, Media, Project
from lumen.render import RenderEngine
from lumen.timing import ClipTimeMap


@pytest.fixture
def reference_project(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg required for independent renderer-boundary comparison')
    rng = np.random.default_rng(413)
    frames = rng.integers(513, 64000, size=(14, 7, 11, 3), dtype=np.uint16)
    source = tmp_path / 'odd-sar-chart.mkv'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb48le',
        '-video_size', '11x7', '-r', '7', '-i', 'pipe:0', '-vf', 'setsar=4/3',
        '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', '-threads', '1', str(source),
    ], input=frames.astype('<u2').tobytes(), check=True, capture_output=True)
    cube = tmp_path / 'channel-mix.cube'
    rows = ['LUT_3D_SIZE 2', 'DOMAIN_MIN -0.5 -0.5 -0.5', 'DOMAIN_MAX 1.5 1.5 1.5']
    rows += [f'{.15 + .7 * b} {.05 + .8 * r} {.12 + .6 * g}'
             for b in (0, 1) for g in (0, 1) for r in (0, 1)]
    cube.write_text('\n'.join(rows) + '\n')
    media = Media('m', str(source), 'review-chart', 2, 11, 7, fps_num=7)
    clip = Clip('c', 'm', 0, 2, 1, 5)
    grade = clip.grade
    grade.input_encoding = 'srgb'
    grade.white_balance_enabled = True
    grade.white_balance_gains = [1.2, .9, 1.1]
    grade.linear_exposure = .2
    grade.exposure = .1
    grade.contrast, grade.contrast_pivot = 1.15, .36
    grade.temperature = .04
    grade.red_curve = [[0, 0], [.4, .5], [1, 1]]
    grade.hue_hue_curve = [[0, .02], [.5, -.04], [1, .02]]
    grade.hue_sat_curve = [[0, 1.1], [.5, .8], [1, 1.1]]
    qualifier = grade.qualifier
    qualifier.enabled = True
    qualifier.hue_enabled = False
    qualifier.saturation_low, qualifier.saturation_high = .25, .8
    qualifier.luma_low, qualifier.luma_high = .1, .8
    qualifier.window_shape = 'ellipse'
    qualifier.window_center_x, qualifier.window_center_y = .37, .61
    qualifier.window_width, qualifier.window_height = .8, .65
    qualifier.matte_morphology, qualifier.matte_smoothing = .55, .7
    qualifier.exposure, qualifier.saturation, qualifier.hue_shift = .4, 1.2, .025
    grade.lut_path, grade.lut_strength = str(cube), .63
    return Project(width=17, height=13, fps_num=5, media=[media], clips=[clip])


@pytest.mark.parametrize('retimed', [False, True])
@pytest.mark.parametrize('restored', [False, True])
def test_float_snapshot_matches_actual_renderer_boundary(reference_project, monkeypatch, retimed, restored):
    from lumen import color
    project = reference_project
    clip = project.clips[0]
    if retimed:
        clip.time_map = ClipTimeMap(Fraction(1, 2), Fraction(8), Fraction(3, 2))
        clip.source_in = Fraction(1, 2)
    clip.restoration.enabled = restored
    clip.restoration.denoise, clip.restoration.sharpen = .27, .31
    captured = []
    original = color.apply_grade

    def record_boundary(rgb, grade, *args, **kwargs):
        result = original(rgb, grade, *args, **kwargs)
        captured.append(result.copy())
        return result

    monkeypatch.setattr(color, 'apply_grade', record_boundary)
    engine = RenderEngine(timeout=30)
    try:
        engine._graded_segment(project.media[0], clip, project, cancel=None)
    finally:
        engine.close()
    assert len(captured) == clip.duration
    for local in (0, 2, 4):
        snapshot = capture_grade_preview_snapshot(project, clip.id, clip.start + local)
        np.testing.assert_array_equal(snapshot.frame, captured[local])
        assert snapshot.frame.shape == (13, 17, 3)
        assert snapshot.restoration_applied is restored
        assert snapshot.active_rect[3] < project.height
        assert snapshot.valid_mask.sum() == snapshot.active_rect[2] * snapshot.active_rect[3]


def test_post_grade_effects_geometry_and_composite_are_excluded(reference_project):
    project = reference_project
    first = capture_grade_preview_snapshot(project, 'c', 4)
    clip = project.clips[0]
    clip.effects = [ClipEffect('review-effect', 'blur', 7)]
    clip.fade_in, clip.fade_out = 2, 2
    clip.transform.x, clip.transform.y = .35, -.3
    clip.transform.scale, clip.transform.rotation = .6, 23
    clip.transform.mirror_x = True
    project.clips.append(Clip('upper', 'm', 1, 0, 0, 8))
    second = capture_grade_preview_snapshot(project, 'c', 4)
    assert not first.is_current(project)
    np.testing.assert_array_equal(second.frame, first.frame)


def test_disabled_grade_with_linear_input_restoration_matches_real_renderer(reference_project, monkeypatch):
    from lumen import color
    project = reference_project
    clip = project.clips[0]
    clip.grade.enabled = False
    clip.grade.input_encoding = 'linear_rec709'
    clip.grade.lut_path = '/missing/disabled-grade-must-not-read.cube'
    clip.restoration.enabled = True
    clip.restoration.deblock, clip.restoration.denoise, clip.restoration.sharpen = .2, .3, .4
    original, captured = color.apply_grade, []
    def record(rgb, grade, *args, **kwargs):
        result = original(rgb, grade, *args, **kwargs)
        captured.append(result.copy())
        return result
    monkeypatch.setattr(color, 'apply_grade', record)
    engine = RenderEngine(timeout=30)
    try:
        engine._graded_segment(project.media[0], clip, project, cancel=None)
    finally:
        engine.close()
    snapshot = capture_grade_preview_snapshot(project, 'c', clip.start + 2)
    np.testing.assert_array_equal(snapshot.frame, captured[2])
    assert snapshot.identity.lut_identity is None and snapshot.restoration_applied


def test_declared_limited_yuv10_matches_real_renderer_ingress(reference_project, tmp_path, monkeypatch):
    from lumen import color
    project = reference_project
    media, clip = project.media[0], project.clips[0]
    target = tmp_path / 'limited-yuv10.mkv'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-i', media.path,
        '-vf', 'scale=out_range=limited:out_color_matrix=bt709,format=yuv444p10le',
        '-c:v', 'ffv1', '-threads', '1', '-color_range', 'tv', '-colorspace', 'bt709',
        '-color_trc', 'bt709', '-color_primaries', 'bt709', str(target),
    ], check=True, capture_output=True)
    media.path = str(target)
    clip.grade.input_encoding = 'rec709'
    original, captured = color.apply_grade, []
    def record(rgb, grade, *args, **kwargs):
        result = original(rgb, grade, *args, **kwargs)
        captured.append(result.copy())
        return result
    monkeypatch.setattr(color, 'apply_grade', record)
    engine = RenderEngine(timeout=30)
    try:
        engine._graded_segment(media, clip, project, cancel=None)
    finally:
        engine.close()
    snapshot = capture_grade_preview_snapshot(project, 'c', clip.start + 3)
    np.testing.assert_array_equal(snapshot.frame, captured[3])


def test_lut_symlink_aba_cannot_render_a_different_identity(reference_project, tmp_path, monkeypatch):
    """A normal symlink retarget can leave both resolved target stat/hash unchanged."""
    from pathlib import Path
    from lumen import color_picker_source as sampler
    from lumen.frame_grade_preview import GradePreviewError
    project = reference_project
    grade = project.clips[0].grade
    original_path = Path(grade.lut_path)
    alternate = tmp_path / 'alternate.cube'
    alternate.write_text('LUT_3D_SIZE 2\n' + '0.01 0.02 0.03\n' * 8)
    link = tmp_path / 'selected-lut.cube'
    try:
        link.symlink_to(original_path)
    except OSError:
        pytest.skip('Symlinks unavailable')
    grade.lut_path = str(link)
    expected = capture_grade_preview_snapshot(project, 'c', 4)
    original_apply = sampler.apply_grade
    def retarget_during_evaluation(*args, **kwargs):
        link.unlink()
        link.symlink_to(alternate)
        try:
            return original_apply(*args, **kwargs)
        finally:
            link.unlink()
            link.symlink_to(original_path)
    monkeypatch.setattr(sampler, 'apply_grade', retarget_during_evaluation)
    try:
        actual = capture_grade_preview_snapshot(project, 'c', 4)
    except GradePreviewError:
        return  # Rejecting a raced identity is also correct.
    assert actual.identity.lut_identity == expected.identity.lut_identity
    np.testing.assert_array_equal(actual.frame, expected.frame,
        err_msg='Returned frame must use the LUT whose stable target identity is attached')

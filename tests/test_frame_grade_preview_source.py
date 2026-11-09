"""Full-grade snapshot boundaries, immutable identity, and bounded cancellation."""
from copy import deepcopy
import json
import os
from pathlib import Path
import threading

import numpy as np
import pytest

from lumen import color, color_picker_source as sampler
from lumen.color_picker_source import capture_picker_snapshot
from lumen.frame_grade_preview import (capture_grade_preview_snapshot,
                                       GradePreviewError, GradePreviewCancelled)
from lumen.model import Clip, Media, Project
from lumen.qualifier import QualifierSettings
from lumen.restoration_settings import RestorationSettings


def write_lut(path, swapped=False):
    path.write_text('LUT_3D_SIZE 2\n' + ''.join(
        f'{b if swapped else r} {g} {r if swapped else b}\n'
        for b in (0, 1) for g in (0, 1) for r in (0, 1)))
    return str(path)


@pytest.fixture
def source(tmp_path, monkeypatch):
    width, height = 384, 192  # Cross the old 65536-pixel flattened boundary.
    path = tmp_path / 'source.mkv'
    path.write_bytes(b'synthetic local source identity')
    samples = np.arange(width * height * 3, dtype=np.uint32).reshape(height, width, 3)
    samples = ((samples * 71 + 1025) % 65536).astype('<u2')
    probe = json.dumps({'streams': [{'width': width, 'height': height,
                                    'pix_fmt': 'rgb48le'}]}).encode()
    monkeypatch.setattr(sampler, 'ffprobe_binary', lambda: 'test-probe')
    monkeypatch.setattr(sampler, 'ffmpeg_binary', lambda: 'test-decode')
    monkeypatch.setattr(sampler, '_run', lambda args, *rest:
                        probe if args[0] == 'test-probe' else samples.tobytes())
    monkeypatch.setattr(sampler, 'active_picture_size', lambda *args, **kwargs: (width, height))
    project = Project(width=width, height=height, fps_num=6,
                      media=[Media('m', str(path), 'source', 2., width, height, fps_num=6)],
                      clips=[Clip('c', 'm', 0, 3, 0, 12)])
    return project, samples


def test_exact_rgb16_snapshot_metadata_and_immutable_arrays(source):
    project, samples = source
    result = capture_grade_preview_snapshot(project, 'c', 7)
    np.testing.assert_array_equal(result.frame, samples.astype(np.float32) / np.float32(65535))
    assert result.stage == 'grade_preview' and result.source_frame == 4
    assert result.valid_mask.all() and result.active_rect == (0, 0, 384, 192)
    assert result.identity.lut_identity is None
    assert result.is_current(project, 'c', 7)
    assert not result.is_current(project, 'other', 7)
    assert not result.is_current(project, 'c', 8)
    for array in (result.frame, result.valid_mask):
        with pytest.raises(ValueError):
            array.setflags(write=True)


def test_full_hwc_grade_once_keeps_spatial_key_and_lut(source, tmp_path, monkeypatch):
    project, samples = source
    grade = project.clips[0].grade
    grade.hue_hue_curve = [[0., .035], [1., .035]]
    grade.hue_sat_curve = [[0., .8], [1., .8]]
    grade.qualifier = QualifierSettings(enabled=True, hue_width=.5, exposure=.3,
        window_shape='ellipse', window_width=.7, window_height=.6,
        matte_morphology=.4, matte_smoothing=.6)
    grade.lut_path = write_lut(tmp_path / 'swap.cube', True)
    grade.lut_strength = .7
    original, calls = sampler.apply_grade, []
    def record(rgb, settings, **kwargs):
        calls.append(rgb.shape)
        assert settings == grade
        assert kwargs['cancel'] is not None
        return original(rgb, settings, **kwargs)
    monkeypatch.setattr(sampler, 'apply_grade', record)
    before = deepcopy(grade)
    result = capture_grade_preview_snapshot(project, 'c', 3)
    expected = color.apply_grade(samples.astype(np.float32) / np.float32(65535), grade)
    np.testing.assert_array_equal(result.frame, expected)
    assert calls == [samples.shape] and grade == before
    assert result.identity.lut_identity[-1]
    no_window = deepcopy(grade)
    no_window.qualifier.window_shape = 'off'
    assert np.max(abs(result.frame - color.apply_grade(
        samples.astype(np.float32) / np.float32(65535), no_window))) > .01


@pytest.mark.parametrize('encoding', ['srgb', 'linear_rec709'])
def test_normalization_once_before_restoration_even_when_grade_disabled(source, monkeypatch, encoding):
    project, samples = source
    clip = project.clips[0]
    clip.grade.enabled, clip.grade.input_encoding = False, encoding
    clip.grade.lut_path = '/never/read/disabled.cube'
    clip.restoration = RestorationSettings(enabled=True, denoise=.8)
    calls, restored = [], []
    original = color.normalize_input
    expected = original(samples.astype(np.float32) / np.float32(65535), encoding)
    def normalize(rgb, assigned):
        calls.append((rgb.shape, assigned))
        return original(rgb, assigned)
    def make_restore(settings, active_size, width, height, cancel, error):
        def restore(rgb):
            assert not cancel.is_set()
            np.testing.assert_array_equal(rgb, expected)
            restored.append(rgb.shape)
            return rgb + np.float32(.001)
        return restore
    monkeypatch.setattr(color, 'normalize_input', normalize)
    monkeypatch.setattr(sampler, 'normalized_restoration', make_restore)
    result = capture_grade_preview_snapshot(project, 'c', 3)
    assert calls == [(samples.shape, encoding)] and restored == [samples.shape]
    np.testing.assert_array_equal(result.frame, expected + np.float32(.001))
    assert result.restoration_applied and clip.grade.input_encoding == encoding


def test_keeps_unclipped_working_rgb_and_excludes_clip_transform(source):
    project, samples = source
    clip = project.clips[0]
    clip.grade.exposure = 1.
    clip.grade.temperature = -.5
    clip.transform.mirror_x, clip.transform.x = True, 13.
    result = capture_grade_preview_snapshot(project, 'c', 3)
    expected = samples.astype(np.float32) / np.float32(65535) * np.float32(2)
    expected += np.array([-.125, 0., .125], np.float32)
    np.testing.assert_array_equal(result.frame, expected)
    assert result.frame.min() < 0 and result.frame.max() > 1


def test_active_lut_identity_and_fast_display_guard(source, tmp_path, monkeypatch):
    project, _ = source
    path = tmp_path / 'external.cube'
    project.clips[0].grade.lut_path = write_lut(path)
    result = capture_grade_preview_snapshot(project, 'c', 3)
    def unexpected(*args, **kwargs):
        pytest.fail('GUI is_current must not read/hash LUT bytes')
    monkeypatch.setattr(sampler, '_grade_lut_identity', unexpected)
    assert result.is_current(project)
    original = path.stat()
    write_lut(path, True)
    # Restoring mtime must not hide an edit: ctime/inode remain guarded.
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert not result.is_current(project)
    path.unlink()
    assert not result.is_current(project)


def test_worker_detects_lut_content_change_even_when_stat_is_unchanged(source, tmp_path, monkeypatch):
    project, _ = source
    path = tmp_path / 'external.cube'
    project.clips[0].grade.lut_path = write_lut(path)
    original_identity = sampler._source_identity
    fixed = original_identity(path)
    monkeypatch.setattr(sampler, '_source_identity', lambda target:
                        fixed if Path(target) == path else original_identity(target))
    original = sampler.apply_grade
    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        write_lut(path, True)
        return result
    monkeypatch.setattr(sampler, 'apply_grade', mutate)
    with pytest.raises(GradePreviewError, match='LUT changed'):
        capture_grade_preview_snapshot(project, 'c', 3)


@pytest.mark.parametrize('restore_link', [False, True], ids=['retargeted', 'retargeted-then-restored'])
def test_evaluation_pins_captured_lut_target_but_checks_original_path(source, tmp_path, monkeypatch, restore_link):
    project, _ = source
    target, alternate, link = (tmp_path / name for name in ('original.cube', 'alternate.cube', 'selected.cube'))
    write_lut(target)
    write_lut(alternate, True)
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip('Symlinks unavailable')
    grade = project.clips[0].grade
    grade.lut_path = str(link)
    expected = capture_grade_preview_snapshot(project, 'c', 3)
    original, visited = sampler.apply_grade, []

    def retarget(rgb, copied_grade, **kwargs):
        assert copied_grade.lut_path == str(target.resolve())
        assert grade.lut_path == str(link), 'Never replace the user/project LUT path'
        visited.append(1)
        link.unlink()
        link.symlink_to(alternate)
        try:
            return original(rgb, copied_grade, **kwargs)
        finally:
            if restore_link:
                link.unlink()
                link.symlink_to(target)

    monkeypatch.setattr(sampler, 'apply_grade', retarget)
    if restore_link:
        result = capture_grade_preview_snapshot(project, 'c', 3)
        np.testing.assert_array_equal(result.frame, expected.frame)
        assert result.identity.lut_identity == expected.identity.lut_identity
        assert result.is_current(project, 'c', 3)
    else:
        with pytest.raises(GradePreviewError, match='LUT changed'):
            capture_grade_preview_snapshot(project, 'c', 3)
        assert not expected.is_current(project)
    assert visited == [1] and grade.lut_path == str(link)


@pytest.mark.parametrize('mutation', ['grade', 'source', 'project'])
def test_changes_during_processing_are_rejected(source, monkeypatch, mutation):
    project, _ = source
    original = sampler.apply_grade
    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        if mutation == 'grade':
            project.clips[0].grade.exposure = .5
        elif mutation == 'project':
            project.fps_num = 7
        else:
            Path(project.media[0].path).write_bytes(b'replaced source')
        return result
    monkeypatch.setattr(sampler, 'apply_grade', mutate)
    with pytest.raises(GradePreviewError, match='changed during'):
        capture_grade_preview_snapshot(project, 'c', 3)


@pytest.mark.parametrize('stop', ['cancel', 'deadline'])
@pytest.mark.parametrize('boundary', ['normalize', 'grade', 'qualifier'])
def test_processing_boundaries_enforce_cancel_and_total_deadline(source, monkeypatch, stop, boundary):
    import lumen.qualifier as qualifier
    project, _ = source
    cancel, now, visits = threading.Event(), [0.], []
    monkeypatch.setattr(sampler.time, 'monotonic', lambda: now[0])
    def stopped():
        if stop == 'cancel':
            cancel.set()
        else:
            now[0] = 31.
    module, name = ((color, 'normalize_input') if boundary == 'normalize' else
                    (sampler, 'apply_grade') if boundary == 'grade' else
                    (qualifier, '_raw_image_mask'))
    if boundary == 'qualifier':
        project.clips[0].grade.qualifier = QualifierSettings(enabled=True, hue_width=.5,
                                                           exposure=.3, matte_smoothing=.8)
    original = getattr(module, name)
    def stop_after_stage(*args, **kwargs):
        visits.append(1)
        result = original(*args, **kwargs)
        stopped()
        return result
    monkeypatch.setattr(module, name, stop_after_stage)
    with pytest.raises(GradePreviewCancelled if stop == 'cancel' else GradePreviewError,
                       match='cancelled|timed out'):
        capture_grade_preview_snapshot(project, 'c', 3, cancel, timeout=30)
    assert visits == [1]


@pytest.mark.parametrize('timeout', [True, 0, -1, float('inf'), float('nan')])
def test_invalid_timeout_rejected(source, timeout):
    with pytest.raises(GradePreviewError, match='timeout'):
        capture_grade_preview_snapshot(source[0], 'c', 3, timeout=timeout)


def test_unsupported_states_and_existing_picker_lut_isolation(source):
    project, _ = source
    clip = project.clips[0]
    clip.grade.lut_path = '/intentionally/missing.cube'
    # Existing picker never starts requiring a LUT, even when it is active.
    assert capture_picker_snapshot(project, 'c', 3).identity.lut_identity is None
    with pytest.raises(GradePreviewError):
        capture_grade_preview_snapshot(project, 'c', 3)
    clip.grade.lut_strength = 0.
    assert capture_grade_preview_snapshot(project, 'c', 3).identity.lut_identity is None
    clip.stabilization.enabled, clip.stabilization.strength = True, .5
    with pytest.raises(GradePreviewError, match='stabilization'):
        capture_grade_preview_snapshot(project, 'c', 3)
    clip.stabilization.enabled = False
    project.width = project.height = 16384
    with pytest.raises(GradePreviewError, match='budget'):
        capture_grade_preview_snapshot(project, 'c', 3)
    with pytest.raises(GradePreviewError, match='Unknown'):
        capture_picker_snapshot(project, 'c', 3, stage='grade_preview')

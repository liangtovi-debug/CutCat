"""Original synthetic LUT acceptance: controls, persistence and real RGB renders."""
from contrast_legacy_fixture import strip_contrast_pivot_field
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest

from lumen.color import apply_grade, apply_lut, load_cube
from lumen.media import ffmpeg_available, ffmpeg_binary, probe
from lumen.model import Clip, Editor, Grade, Media, Project
from lumen.render import RenderEngine, RenderError
from test_ui import app, window, populate
from PySide6.QtWidgets import QFileDialog


def cube(path, mapping=lambda r, g, b: (1 - r, g, b), size=2, headers=''):
    """Create our own red-fastest cube, never a third-party look asset."""
    values = np.linspace(0, 1, size)
    rows = [' '.join(format(float(v), '.9g') for v in mapping(r, g, b))
            for b in values for g in values for r in values]
    path.write_text(f'\ufeff# Original acceptance fixture\nTITLE "Original fixture"\n'
                    f'LUT_3D_SIZE {size}\n{headers}\n' + '\n'.join(rows) + '\n',
                    encoding='utf-8')
    return path.resolve()


def model_project(path, grade):
    return Project(media=[Media('m', str(path.resolve()), 'Source', 1)],
                   clips=[Clip('c', 'm', 0, 0, 0, 2, grade=grade)])


@pytest.mark.parametrize('strength', [0, .5, 1])
def test_strength_blends_after_curves_without_output_clipping(tmp_path, strength):
    path = cube(tmp_path / 'range.cube', lambda r, g, b: (2 * b, r - 1, g))
    x = np.array([[-.2, .4, 1.2], [.2, .3, .4]], np.float32)
    grade = Grade(exposure=.5, red_curve=[[0, .1], [1, 1.1]],
                  lut_path=str(path), lut_strength=strength)
    before = apply_grade(x, replace(grade, lut_path=''))
    mapped = apply_lut(before, load_cube(path))
    expected = before if strength == 0 else mapped if strength == 1 else (before + mapped) / 2
    actual = apply_grade(x, grade)
    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(apply_grade(x, grade, bypass=True), x)
    np.testing.assert_array_equal(apply_grade(x, replace(grade, enabled=False)), x)
    assert actual.dtype == np.float32
    assert np.any(actual < 0) or np.any(actual > 1)


def test_zero_strength_never_opens_or_clamps_lut(tmp_path):
    x = np.array([[-.5, .25, 1.5]], np.float32)
    grade = Grade(lut_path=str(tmp_path / 'missing.cube'), lut_strength=0)
    np.testing.assert_array_equal(apply_grade(x, grade), x)


@pytest.mark.parametrize('strength', [-.01, 1.01, float('nan'), float('inf'), True, '0.5', None])
def test_invalid_strength_rejected_and_editor_change_rolls_back(tmp_path, strength):
    project = model_project(tmp_path / 'media.mp4', Grade(lut_strength=.25))
    editor = Editor(project)
    before = project.to_dict()
    invalid = Grade(lut_strength=strength)
    with pytest.raises(ValueError, match='lut_strength must be finite and in 0..1'):
        editor.update_clip('c', grade=invalid)
    assert editor.project.to_dict() == before and not editor.can_undo
    with pytest.raises(ValueError, match='lut_strength must be finite and in 0..1'):
        apply_grade(np.zeros((1, 3), np.float32), invalid)


@pytest.mark.parametrize('version', [1, 2, 3, 4, 5, 6])
def test_old_project_missing_strength_keeps_full_effect(tmp_path, version):
    path = cube(tmp_path / 'look.cube')
    payload = model_project(tmp_path / 'media.mp4', Grade(lut_path=str(path))).to_dict()
    payload['version'] = version; strip_contrast_pivot_field(payload)
    if payload['version'] < 17:
        for old_clip in payload.get('clips', []):
            old_clip.get('grade', {}).pop('hue_hue_curve', None)
            old_clip.get('grade', {}).pop('hue_sat_curve', None)
            old_clip.get('grade', {}).pop('qualifier', None)
    if payload['version'] < 16:
        for old_clip in payload.get('clips', []): old_clip.pop('transform', None)
    payload['clips'][0].pop('restoration')
    payload['clips'][0].pop('stabilization')
    timing = payload['clips'][0].pop('time_map')
    payload['clips'][0]['source_in'] = timing['source_in_num'] // timing['source_in_den']
    del payload['clips'][0]['grade']['lut_strength']
    restored = Project.from_dict(payload)
    assert restored.clips[0].grade.lut_strength == 1
    x = np.array([[.2, .4, .8]], np.float32)
    np.testing.assert_allclose(apply_grade(x, restored.clips[0].grade), [[.8, .4, .8]], atol=1e-7)


@pytest.mark.parametrize('size', [2, 65])
def test_supported_size_utf8_bom_comments_and_per_channel_domains(tmp_path, size):
    path = cube(tmp_path / 'look.CUBE', lambda r, g, b: (b, r, g), size,
                'DOMAIN_MIN -1 -2 -3\nDOMAIN_MAX 1 2 3')
    lut = load_cube(path)
    assert lut.table.shape == (size, size, size, 3)
    x = np.array([[0, 1, -1.5], [-9, 9, 9]], np.float32)
    np.testing.assert_allclose(apply_lut(x, lut), [[.25, .5, .75], [1, 0, 1]], atol=2e-7)


@pytest.mark.parametrize('suffix', ['.3dl', '.look', '.cube3D'])
def test_unsupported_extensions_are_explicit_errors(tmp_path, suffix):
    path = cube(tmp_path / ('look' + suffix))
    with pytest.raises(ValueError, match=r'Only \.cube 3D LUT files are supported'):
        load_cube(path)


@pytest.mark.parametrize('extra', ['LUT_1D_SIZE 2', 'LUT_3D_INPUT_RANGE 0 1', 'LUT_3D_SIZE 2'])
def test_unsupported_or_duplicate_headers_are_errors(tmp_path, extra):
    path = cube(tmp_path / 'bad.cube', headers=extra)
    with pytest.raises(ValueError, match=r'Invalid \.cube line'):
        load_cube(path)


def test_ui_lut_import_strength_undo_save_reopen_and_remove(window, tmp_path, monkeypatch):
    w = window
    clip = populate(w, tmp_path)
    path = cube(tmp_path / "original's look.cube")
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(path), ''))
    w.choose_lut()
    assert w.selected_clip().grade.lut_path == str(path)
    assert w.lut_label.text() == path.name and w.lut_strength.isEnabled()
    w.lut_strength.setValue(.4)
    assert w.selected_clip().grade.lut_strength == .4
    w.undo()
    assert w.selected_clip().grade.lut_strength == 1
    w.redo()
    assert w.selected_clip().grade.lut_strength == .4
    saved = tmp_path / 'graded.lumen'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(saved), ''))
    assert w.save_project(True)
    payload = json.loads(saved.read_text(encoding='utf-8'))
    assert payload['clips'][0]['grade']['lut_path'] == path.name
    assert payload['clips'][0]['grade']['lut_strength'] == .4
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(saved), ''))
    w.open_project()
    w.select_clip(clip.id)
    assert w.selected_clip().grade.lut_path == str(path)
    assert w.lut_strength.value() == .4
    w.remove_lut_button.click()
    assert w.selected_clip().grade.lut_path == '' and not w.lut_strength.isEnabled()
    w.undo()
    assert w.selected_clip().grade.lut_path == str(path)
    assert w.selected_clip().grade.lut_strength == .4
    assert not w.test_warnings


@pytest.mark.parametrize('content', ['LUT_1D_SIZE 2\n0 0 0\n1 1 1', 'LUT_3D_SIZE 2\n0 0 0'])
def test_ui_invalid_lut_preserves_project_and_undo(window, tmp_path, monkeypatch, content):
    w = window
    populate(w, tmp_path)
    path = tmp_path / 'bad.cube'
    path.write_text(content, encoding='utf-8')
    before = w.project.to_dict(), w.revision, deepcopy(w.editor._undo)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(path), ''))
    w.choose_lut()
    assert (w.project.to_dict(), w.revision, w.editor._undo) == before
    assert len(w.test_warnings) == 1
    assert 'LUT' in w.test_warnings[0]


def real_project(tmp_path, grade):
    rgb = np.broadcast_to(np.array([.2, .4, .7]) * 65535, (48, 64, 3)).astype('<u2')
    source = tmp_path / 'original-rgb16.mkv'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb48le',
                    '-s', '64x48', '-r', '30', '-color_range', 'pc', '-color_primaries', 'bt709',
                    '-color_trc', 'bt709', '-colorspace', 'rgb', '-i', 'pipe:0', '-frames:v', '3',
                    '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', '-color_range', 'pc',
                    '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'rgb', str(source)],
                   input=rgb.tobytes() * 3, check=True, capture_output=True)
    media = probe(source)
    return Project(width=64, height=48, media=[media],
                   clips=[Clip('c', media.id, 0, 0, 0, 1, grade=grade)]), rgb.astype(np.float32) / 65535


def decoded(path):
    result = subprocess.run([ffmpeg_binary(), '-v', 'error', '-i', str(path), '-frames:v', '1',
                             '-pix_fmt', 'rgb48le', '-f', 'rawvideo', 'pipe:1'],
                            capture_output=True, check=True)
    return np.frombuffer(result.stdout, dtype='<u2').reshape(48, 64, 3).astype(np.float32) / 65535


@pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
@pytest.mark.parametrize('strength', [0, .5, 1])
def test_saved_lut_strength_affects_real_preview_export_through_common_evaluator(tmp_path, monkeypatch, strength):
    from lumen import color
    path = cube(tmp_path / 'look.cube')
    p, rgb = real_project(tmp_path, Grade(lut_path=str(path), lut_strength=strength))
    destination = tmp_path / 'edit.lumen'
    p.save(destination)
    p = Project.load(destination)
    expected = rgb.copy()
    expected[..., 0] = (1 - strength) * rgb[..., 0] + strength * (1 - rgb[..., 0])
    evaluator = color.apply_grade
    calls = []
    def inspect(values, grade):
        calls.append((grade.lut_path, grade.lut_strength))
        return evaluator(values, grade)
    monkeypatch.setattr(color, 'apply_grade', inspect)
    results = []
    for mode in ('preview', 'export'):
        engine = RenderEngine(timeout=30)
        try:
            calls.clear()
            output = engine.preview(p) if mode == 'preview' else engine.render(p, tmp_path / 'export.mp4')
            assert calls and all(call == (str(path), strength) for call in calls)
            results.append(decoded(output))
            np.testing.assert_allclose(results[-1], expected, atol=.025)
            stage = engine._graded_segment(p.media[0], p.clips[0], p, None)
            np.testing.assert_allclose(decoded(stage), expected, atol=2/65535)
        finally:
            engine.close()
    np.testing.assert_allclose(results[0], results[1], atol=.025)


@pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
def test_zero_strength_missing_lut_renders_and_strength_changes_invalidate_cache(tmp_path):
    path = tmp_path / 'missing.cube'
    p, rgb = real_project(tmp_path, Grade(lut_path=str(path), lut_strength=0))
    engine = RenderEngine(timeout=30)
    try:
        zero = engine.preview(p)
        np.testing.assert_allclose(decoded(zero), rgb, atol=.025)
        np.testing.assert_allclose(decoded(engine.render(p, tmp_path / 'zero.mp4')), rgb, atol=.025)
        cube(path)
        p.clips[0].grade.lut_strength = .5
        half = engine.preview(p)
        p.clips[0].grade.lut_strength = 1
        full = engine.preview(p)
        assert len({zero, half, full}) == 3
        assert decoded(zero)[24, 32, 0] < decoded(half)[24, 32, 0] < decoded(full)[24, 32, 0]
    finally:
        engine.close()


@pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
@pytest.mark.parametrize('suffix,content,message', [
    ('.cube', 'LUT_3D_SIZE 2\n0 0 0', 'Invalid LUT row count or domain'),
    ('.3dl', 'LUT_3D_SIZE 2\n0 0 0', r'Only \.cube 3D LUT files are supported'),
])
def test_bad_active_lut_fails_preview_export_without_partial_outputs(tmp_path, suffix, content, message):
    path = tmp_path / ('bad' + suffix)
    path.write_text(content, encoding='utf-8')
    p, _ = real_project(tmp_path, Grade(lut_path=str(path), lut_strength=.5))
    engine = RenderEngine(timeout=30)
    try:
        with pytest.raises(RenderError, match=message):
            engine.preview(p)
        output = tmp_path / 'must-not-exist.mp4'
        with pytest.raises(RenderError, match=message):
            engine.render(p, output)
        assert not output.exists()
        assert not list(Path(engine._cache_dir.name).glob('*.partial.mkv'))
    finally:
        engine.close()

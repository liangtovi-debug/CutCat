"""Qualifier data remains portable, strictly validated and transactional."""
from native_path_fixture import bind_missing_project_paths
from contrast_legacy_fixture import strip_contrast_pivot_field
from copy import deepcopy
from dataclasses import asdict, fields
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from lumen.color import apply_grade
from lumen.color_source import SourceColor
from lumen.grade_transfer import copy_grade, paste_grade
from lumen.model import VERSION, Clip, Editor, Grade, Media, Project
from lumen.project_recovery import RecoveryStore
from lumen.qualifier import QualifierSettings
from lumen.render import RenderEngine
from test_grade_transfer import project_pair


def settings():
    return QualifierSettings(
        enabled=True, hue_center=.987654321, hue_width=.0314159265,
        hue_softness=.0527182818, saturation_low=.223456789,
        saturation_high=.834567891, saturation_softness=.123456789,
        luma_low=.145678912, luma_high=.756789123, luma_softness=.112345678,
        exposure=.234567891, saturation=.876543219, hue_shift=-.123456789)


def project():
    media = Media('video', '/synthetic-local-fixture.mp4', 'Synthetic fixture', 10,
                  64, 48, True, False)
    return Project(width=64, height=48, media=[media], clips=[
        Clip('picture', 'video', 0, 0, 0, 30, grade=Grade(exposure=.25))])


def test_schema18_roundtrip_retains_all_settings_without_shared_objects(tmp_path):
    original = project(); bind_missing_project_paths(original, tmp_path)
    original.clips[0].grade.qualifier = settings()
    path = tmp_path / 'qualified.lumen.json'
    original.save(path)
    stored = json.loads(path.read_text())
    assert stored['version'] == VERSION
    assert stored['clips'][0]['grade']['qualifier'] == asdict(settings())
    assert Project.load(path) == original
    loaded = Project.from_dict(stored)
    loaded.clips[0].grade.qualifier.hue_center = .25
    assert original.clips[0].grade.qualifier == settings()
    assert stored['clips'][0]['grade']['qualifier'] == asdict(settings())
    first, second = Grade(), Grade()
    first.qualifier.enabled = True
    first.qualifier.exposure = .75
    assert second.qualifier == QualifierSettings()


def test_schema17_missing_qualifier_migrates_to_exact_disabled_identity():
    old = project().to_dict()
    old['version'] = 17; strip_contrast_pivot_field(old)
    del old['clips'][0]['grade']['qualifier']
    restored = Project.from_dict(old)
    grade = restored.clips[0].grade
    assert grade.qualifier == QualifierSettings()
    assert not grade.qualifier.enabled and grade.exposure == .25
    rgb = np.array([[-.23, .45, 1.17], [.25, .25, .25], [0., .2, .6]], np.float32)
    np.testing.assert_array_equal(apply_grade(rgb, grade), rgb * np.float32(2 ** .25))
    assert restored.to_dict()['version'] == VERSION
    assert 'qualifier' not in old['clips'][0]['grade']


@pytest.mark.parametrize('mode', ['missing-v18', 'new-field-v17'])
def test_schema_boundary_cannot_drop_or_smuggle_qualifier_settings(mode):
    payload = project().to_dict()
    if mode == 'missing-v18':
        del payload['clips'][0]['grade']['qualifier']
    else:
        payload['version'] = 17; strip_contrast_pivot_field(payload)
    with pytest.raises(ValueError):
        Project.from_dict(payload)


@pytest.mark.parametrize('field', [item.name for item in fields(QualifierSettings)])
def test_loaded_settings_require_every_declared_field(field):
    payload = project().to_dict()
    del payload['clips'][0]['grade']['qualifier'][field]
    with pytest.raises(ValueError):
        Project.from_dict(payload)


@pytest.mark.parametrize('invalid', [None, [], True, 'enabled', {'unknown': 1}])
def test_malformed_qualifier_container_is_not_silently_defaulted(invalid):
    payload = project().to_dict()
    payload['clips'][0]['grade']['qualifier'] = invalid
    with pytest.raises(ValueError):
        Project.from_dict(payload)


def test_unknown_qualifier_field_is_rejected():
    payload = project().to_dict()
    payload['clips'][0]['grade']['qualifier']['future_setting'] = 0
    with pytest.raises(ValueError):
        Project.from_dict(payload)


INVALID_FIELDS = [
    ('enabled', 1), ('hue_center', -.01), ('hue_center', 1.01),
    ('hue_width', -.01), ('hue_width', .51), ('hue_softness', -.01),
    ('saturation_low', -.01), ('saturation_high', 1.01),
    ('saturation_softness', -.01), ('luma_low', -.01), ('luma_high', 1.01),
    ('luma_softness', -.01), ('exposure', -1.01), ('exposure', 1.01),
    ('saturation', -.01), ('saturation', 2.01),
    ('hue_shift', -.17), ('hue_shift', .17),
    ('hue_center', True), ('exposure', float('nan')),
    ('luma_softness', float('inf')), ('saturation', '1'),
    ('saturation_low', .95), ('luma_low', .9),
]


@pytest.mark.parametrize('field,invalid', INVALID_FIELDS)
def test_invalid_dictionary_edit_rolls_back_entire_project_and_history(field, invalid):
    editor = Editor(project())
    before = deepcopy(editor.project)
    grade = asdict(before.clips[0].grade)
    grade['qualifier'] = asdict(settings())
    grade['qualifier'][field] = invalid
    with pytest.raises(ValueError):
        editor.update_clip('picture', grade=grade)
    assert editor.project == before
    assert editor.revision == 0 and not editor.can_undo and not editor.can_redo


@pytest.mark.parametrize('enabled', [False, True])
def test_invalid_in_place_settings_cannot_save_even_when_bypassed(enabled):
    candidate = project()
    candidate.clips[0].grade.qualifier.enabled = enabled
    candidate.clips[0].grade.qualifier.hue_width = float('nan')
    with pytest.raises(ValueError):
        candidate.to_dict()


def test_dictionary_edit_is_one_independent_undoable_transaction():
    editor = Editor(project())
    before = deepcopy(editor.project)
    grade = asdict(before.clips[0].grade)
    grade['qualifier'] = asdict(settings())
    editor.update_clip('picture', grade=grade)
    after = deepcopy(editor.project)
    assert after.clips[0].grade.qualifier == settings()
    assert editor.revision == 1 and len(editor._undo) == 1
    grade['qualifier']['hue_center'] = .5
    assert editor.project == after
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after


def test_recovery_preserves_qualified_grade_and_does_not_mutate_live_state(tmp_path):
    candidate = project(); bind_missing_project_paths(candidate, tmp_path)
    candidate.clips[0].grade.qualifier = settings()
    before = deepcopy(candidate)
    identity = uuid4()
    with RecoveryStore(tmp_path / 'recovery') as store:
        entry = store.write(candidate, identity)
        recovered = store.load(entry)
        assert recovered == before and candidate == before
        assert json.loads(entry.path.read_text())['project']['version'] == VERSION
        recovered.clips[0].grade.qualifier.exposure = -.5
        assert candidate == before
        assert store.load(entry) == before


@pytest.mark.parametrize('encoding', ['rec709', 'srgb', 'linear_rec709'])
def test_copy_paste_keeps_target_input_and_non_grade_state_then_undo_redo(tmp_path, encoding):
    candidate, _ = project_pair(tmp_path)
    candidate.clips[0].grade.qualifier = settings()
    candidate.clips[1].grade.input_encoding = encoding
    before = deepcopy(candidate)
    copied = copy_grade(candidate, 'source')
    contents = json.loads(copied.creative_json)
    assert contents['qualifier'] == asdict(settings())
    assert 'input_encoding' not in contents
    editor = Editor(candidate)
    assert paste_grade(editor, 'target', copied)
    expected = deepcopy(before)
    expected.clips[1].grade = deepcopy(before.clips[0].grade)
    expected.clips[1].grade.input_encoding = encoding
    assert editor.project == expected
    assert editor.revision == 1 and len(editor._undo) == 1
    assert not paste_grade(editor, 'target', copied)
    assert editor.revision == 1
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == expected
    editor.project.clips[0].grade.qualifier.hue_center = .25
    editor.project.clips[1].grade.qualifier.exposure = -.5
    assert copied.grade_for_input(encoding).qualifier == settings()
    assert editor.project.clips[1].grade.qualifier.hue_center == settings().hue_center


CACHE_EDITS = [
    ('enabled', True), ('hue_center', .2), ('hue_width', .2), ('hue_softness', .1),
    ('saturation_low', .2), ('saturation_high', .8), ('saturation_softness', .1),
    ('luma_low', .2), ('luma_high', .8), ('luma_softness', .1),
    ('exposure', .4), ('saturation', .8), ('hue_shift', .1),
    ('matte_smoothing', .7), ('matte_morphology', -.4),
]


@pytest.mark.parametrize('field,value', CACHE_EDITS)
def test_every_in_place_qualifier_setting_participates_in_both_cache_keys(
        tmp_path, monkeypatch, field, value):
    """Keep real cache-key code; stub only expensive external media processes."""
    import lumen.color_media as color_media
    candidate = project()
    source = tmp_path / 'source.mkv'
    source.write_bytes(b'local cache-key fixture')
    candidate.media[0].path = str(source)
    color = SourceColor('gbrp16le', 'rgb', 'bt709', 'bt709', 'pc', True, 'gbr', 'full')
    monkeypatch.setattr(color_media, 'read_source_color', lambda *a, **k: color)
    monkeypatch.setattr(color_media, 'source_rgb_filters', lambda *a, **k: [])
    stage_calls, preview_calls = [], []
    def fake_grade(source, target, *args, **kwargs):
        stage_calls.append(target)
        Path(target).write_bytes(b'lossless cache fixture')
    def fake_render(project, target, *args, **kwargs):
        preview_calls.append(target)
        Path(target).write_bytes(b'preview cache fixture')
        return target
    monkeypatch.setattr(color_media, 'grade_segment', fake_grade)
    engine = RenderEngine()
    monkeypatch.setattr(engine, 'render', fake_render)
    try:
        clip, media = candidate.clips[0], candidate.media[0]
        qualifier = clip.grade.qualifier
        original = deepcopy(getattr(qualifier, field))
        before_stage = engine._graded_segment(media, clip, candidate, None)
        before_preview = engine.preview(candidate)
        setattr(qualifier, field, value)
        assert clip.grade.qualifier is qualifier
        after_stage = engine._graded_segment(media, clip, candidate, None)
        after_preview = engine.preview(candidate)
        assert before_stage != after_stage and before_preview != after_preview
        assert len(stage_calls) == len(preview_calls) == 2
        setattr(qualifier, field, original)
        assert engine._graded_segment(media, clip, candidate, None) == before_stage
        assert engine.preview(candidate) == before_preview
        assert len(stage_calls) == len(preview_calls) == 2
    finally:
        engine.close()

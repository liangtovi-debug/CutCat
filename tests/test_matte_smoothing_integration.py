"""Project v22 smoothing: exact migration, portability, history, and caches."""
from contrast_legacy_fixture import strip_contrast_pivot_field
from qualifier_legacy_fixture import strip_asymmetric_fields, inherited_asymmetric_fields
from copy import deepcopy
from dataclasses import asdict, replace
import json
from uuid import uuid4
import pytest
from lumen.model import Project, Editor, VERSION
from lumen.qualifier import QualifierSettings, QualifierRange
from lumen.grade_transfer import copy_grade, paste_grade
from lumen.project_recovery import RecoveryStore
from test_qualifier_integration import project
from test_grade_transfer import project_pair


@pytest.mark.parametrize('version', [18, 19, 20, 21])
def test_every_legacy_qualifier_migrates_zero_smoothing_without_mutation(version):
    original = project()
    q = QualifierSettings(enabled=True, hue_center=.123456789,
        matte_black=.2, matte_white=.8, window_shape='ellipse',
        inclusion_ranges=[QualifierRange(hue_center=.6)])
    original.clips[0].grade.qualifier = q
    data = original.to_dict(); data['version'] = version; strip_contrast_pivot_field(data)
    old = data['clips'][0]['grade']['qualifier']; del old['matte_smoothing']; del old['matte_morphology']
    strip_asymmetric_fields(old)
    if version < 21:
        old.pop('matte_black'); old.pop('matte_white')
    if version < 20:
        old.pop('inclusion_ranges'); old.pop('exclusion_ranges')
    if version == 18:
        for key in list(old):
            if key.startswith('window_'): old.pop(key)
    before = deepcopy(data)
    restored = Project.from_dict(data)
    expected = QualifierSettings.from_dict(inherited_asymmetric_fields({**QualifierSettings().to_dict(), **old}))
    assert restored.clips[0].grade.qualifier == expected
    assert restored.clips[0].grade.qualifier.matte_smoothing == 0
    assert restored.to_dict()['version'] == VERSION and data == before
    # Even a zero-valued future field must not be smuggled into old schemas.
    old['matte_smoothing'] = 0
    with pytest.raises(ValueError, match='exactly the legacy qualifier fields'):
        Project.from_dict(data)


def test_schema22_requires_smoothing_and_21_reader_rejects_before_payload(monkeypatch):
    import lumen.model as model
    data = project().to_dict()
    del data['clips'][0]['grade']['qualifier']['matte_smoothing']
    with pytest.raises(ValueError, match='exactly its documented settings fields'):
        Project.from_dict(data)
    data['clips'] = 'must not be parsed'
    monkeypatch.setattr(model, 'VERSION', 21)
    with pytest.raises(ValueError, match='Unsupported project format or version'):
        Project.from_dict(data)


@pytest.mark.parametrize('invalid', [True, -.001, 1.001, float('nan'), float('inf'), '0.5', None])
def test_invalid_smoothing_edit_is_atomic_even_disabled(invalid):
    editor = Editor(project()); before = deepcopy(editor.project)
    grade = asdict(before.clips[0].grade); grade['qualifier']['matte_smoothing'] = invalid
    with pytest.raises(ValueError): editor.update_clip('picture', grade=grade)
    assert editor.project == before and editor.revision == 0
    assert not editor.can_undo and not editor.can_redo


def test_non_neutral_smoothing_save_recovery_copy_and_single_undo(tmp_path):
    p, _ = project_pair(tmp_path); editor = Editor(p); before = deepcopy(p)
    q = QualifierSettings(enabled=True, matte_smoothing=.678912345678,
        matte_black=.123456789, matte_white=.876543219,
        exclusion_ranges=[QualifierRange(hue_center=.7)], window_shape='rectangle')
    grade = asdict(p.clips[0].grade); grade['qualifier'] = q.to_dict()
    editor.update_clip('source', grade=grade); after = deepcopy(editor.project)
    assert len(editor._undo) == 1
    path = tmp_path/'smoothing.lumen.json'; editor.project.save(path)
    assert Project.load(path) == after
    assert json.loads(path.read_text())['version'] == VERSION
    transfer = copy_grade(editor.project, 'source')
    assert transfer.grade_for_input('srgb').qualifier == q
    with RecoveryStore(tmp_path/'recovery') as store:
        record = store.write(editor.project, uuid4())
        assert store.load(record) == after
        assert not any(key in record.path.read_text() for key in ('matte_source', 'viewport'))
    editor.undo(); assert editor.project == before
    editor.redo(); assert editor.project == after
    assert paste_grade(editor, 'target', transfer)
    assert editor.project.clips[1].grade.qualifier == q
    assert not paste_grade(editor, 'target', transfer)
    editor.project.clips[0].grade.qualifier.matte_smoothing = .1
    assert editor.project.clips[1].grade.qualifier.matte_smoothing == q.matte_smoothing
    assert transfer.grade_for_input('srgb').qualifier == q

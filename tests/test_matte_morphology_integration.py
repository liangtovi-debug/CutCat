"""Version-23 morphology migration, transaction/history, transfer and recovery."""
from contrast_legacy_fixture import strip_contrast_pivot_field
from qualifier_legacy_fixture import strip_asymmetric_fields, inherited_asymmetric_fields
from copy import deepcopy
from dataclasses import asdict
import json
from uuid import uuid4

import numpy as np
import pytest

from lumen.model import Project, Editor, VERSION
from lumen.qualifier import QualifierSettings, QualifierRange, apply_qualifier
from lumen.grade_transfer import copy_grade, paste_grade
from lumen.project_recovery import RecoveryStore
from test_qualifier_integration import project
from test_grade_transfer import project_pair


@pytest.mark.parametrize('version', [18, 19, 20, 21, 22])
def test_every_legacy_qualifier_defaults_morphology_to_exact_zero(version):
    p = project()
    q = QualifierSettings(enabled=True, hue_width=.5, matte_smoothing=.7,
        matte_black=.2, matte_white=.8, window_shape='ellipse', exposure=.5,
        inclusion_ranges=[QualifierRange(hue_center=.4)])
    p.clips[0].grade.qualifier = q
    data = p.to_dict(); data['version'] = version; strip_contrast_pivot_field(data)
    old = data['clips'][0]['grade']['qualifier']; old.pop('matte_morphology')
    strip_asymmetric_fields(old)
    if version < 22: old.pop('matte_smoothing')
    if version < 21: old.pop('matte_black'); old.pop('matte_white')
    if version < 20: old.pop('inclusion_ranges'); old.pop('exclusion_ranges')
    if version == 18:
        for key in list(old):
            if key.startswith('window_'): old.pop(key)
    before = deepcopy(data)
    restored = Project.from_dict(data)
    expected = QualifierSettings.from_dict(inherited_asymmetric_fields({**QualifierSettings().to_dict(), **old}))
    assert restored.clips[0].grade.qualifier == expected
    assert expected.matte_morphology == 0
    rgb = np.random.default_rng(23).random((9, 11, 3), dtype=np.float32)
    np.testing.assert_array_equal(apply_qualifier(rgb, restored.clips[0].grade.qualifier),
                                   apply_qualifier(rgb, expected))
    assert restored.to_dict()['version'] == VERSION and data == before
    old['matte_morphology'] = 0
    with pytest.raises(ValueError, match='exactly the legacy qualifier fields'):
        Project.from_dict(data)


def test_current_schema_requires_morphology_and_v22_reader_rejects_before_payload(monkeypatch):
    import lumen.model as model
    data = project().to_dict()
    del data['clips'][0]['grade']['qualifier']['matte_morphology']
    with pytest.raises(ValueError, match='exactly its documented settings fields'):
        Project.from_dict(data)
    data['clips'] = 'must not be parsed'
    monkeypatch.setattr(model, 'VERSION', 22)
    with pytest.raises(ValueError, match='Unsupported project format or version'):
        Project.from_dict(data)


@pytest.mark.parametrize('invalid', [True, -1.001, 1.001, float('nan'), float('inf'), '0.5', None])
def test_invalid_morphology_edit_is_atomic_even_disabled(invalid):
    editor = Editor(project()); before = deepcopy(editor.project)
    grade = asdict(before.clips[0].grade); grade['qualifier']['matte_morphology'] = invalid
    with pytest.raises(ValueError): editor.update_clip('picture', grade=grade)
    assert editor.project == before and editor.revision == 0
    assert not editor.can_undo and not editor.can_redo


@pytest.mark.parametrize('amount', [-.678912345678, .678912345678])
def test_precise_morphology_save_recovery_copy_paste_single_undo(tmp_path, amount):
    p, _ = project_pair(tmp_path); editor = Editor(p); before = deepcopy(p)
    q = QualifierSettings(enabled=True, matte_morphology=amount, matte_smoothing=.7,
        matte_black=.123456789, matte_white=.876543219,
        exclusion_ranges=[QualifierRange(hue_center=.7)], window_shape='rectangle')
    grade = asdict(p.clips[0].grade); grade['qualifier'] = q.to_dict()
    editor.update_clip('source', grade=grade); after = deepcopy(editor.project)
    assert len(editor._undo) == 1
    path = tmp_path/'morphology.lumen.json'; editor.project.save(path)
    assert Project.load(path) == after and json.loads(path.read_text())['version'] == VERSION
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
    editor.project.clips[0].grade.qualifier.matte_morphology = .1
    assert editor.project.clips[1].grade.qualifier.matte_morphology == q.matte_morphology
    assert transfer.grade_for_input('srgb').qualifier == q

"""Strict matte schema migration and normal project transactions."""
from native_path_fixture import bind_missing_project_paths
from contrast_legacy_fixture import strip_contrast_pivot_field
from qualifier_legacy_fixture import strip_asymmetric_fields, inherited_asymmetric_fields
from copy import deepcopy
from dataclasses import asdict
import json
from uuid import uuid4
import pytest
from lumen.model import VERSION, Project, Editor
from lumen.qualifier import QualifierSettings, QualifierRange
from lumen.grade_transfer import copy_grade
from lumen.project_recovery import RecoveryStore
from test_qualifier_integration import project


@pytest.mark.parametrize('version', [18, 19, 20])
def test_legacy_exact_migration_neutral_matte_and_no_mutation(version):
    p = project().to_dict(); p['version'] = version; strip_contrast_pivot_field(p)
    q = p['clips'][0]['grade']['qualifier']
    strip_asymmetric_fields(q)
    q.pop('matte_black'); q.pop('matte_white'); q.pop('matte_smoothing'); q.pop('matte_morphology')
    if version < 20:
        q.pop('inclusion_ranges'); q.pop('exclusion_ranges')
    if version == 18:
        for name in list(q):
            if name.startswith('window_'): del q[name]
    before = deepcopy(p)
    loaded = Project.from_dict(p)
    assert loaded.clips[0].grade.qualifier == QualifierSettings()
    assert loaded.to_dict()['version'] == VERSION and p == before
    for name, value in [('matte_black', 0.), ('matte_white', 1.)]:
        forged = deepcopy(p); forged['clips'][0]['grade']['qualifier'][name] = value
        with pytest.raises(ValueError): Project.from_dict(forged)


@pytest.mark.parametrize('field', ['matte_black', 'matte_white'])
def test_current_schema_requires_both_fields(field):
    p = project().to_dict(); del p['clips'][0]['grade']['qualifier'][field]
    with pytest.raises(ValueError): Project.from_dict(p)


def test_old_reader_rejects_new_version_before_parsing(monkeypatch):
    from lumen import model
    p = project().to_dict(); p['clips'] = 'not parseable'
    monkeypatch.setattr(model, 'VERSION', 20)
    with pytest.raises(ValueError, match='Unsupported project format or version'):
        model.Project.from_dict(p)


def test_non_neutral_levels_save_copy_history_and_recovery(tmp_path):
    editor = Editor(project()); bind_missing_project_paths(editor.project, tmp_path); before = deepcopy(editor.project)
    q = QualifierSettings(enabled=True, matte_black=.123456789, matte_white=.876543219,
        inclusion_ranges=[QualifierRange(hue_center=.6)], window_shape='ellipse')
    grade = asdict(editor.project.clips[0].grade); grade['qualifier'] = q.to_dict()
    editor.update_clip('picture', grade=grade); after = deepcopy(editor.project)
    path = tmp_path/'matte.lumen'; editor.project.save(path)
    assert Project.load(path) == after
    assert json.loads(path.read_text())['version'] == VERSION
    transfer = copy_grade(editor.project, 'picture')
    assert transfer.grade_for_input('srgb').qualifier == q
    editor.undo(); assert editor.project == before
    editor.redo(); assert editor.project == after
    with RecoveryStore(tmp_path/'recovery') as store:
        entry = store.write(editor.project, uuid4())
        assert store.load(entry) == after

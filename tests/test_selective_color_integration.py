"""Selective curves remain transactional, portable, and isolated on grade copy."""
from native_path_fixture import bind_missing_project_paths
from contrast_legacy_fixture import strip_contrast_pivot_field
from copy import deepcopy
from dataclasses import asdict
import json

import numpy as np
import pytest

from lumen.color import apply_grade
from lumen.grade_transfer import copy_grade, paste_grade
from lumen.model import VERSION, Clip, Editor, Grade, Media, Project
from test_grade_transfer import project_pair


HUE = [[0., .0314159265], [.2, -.123456789], [.75, .22], [1., .0314159265]]
SAT = [[0., .7654321], [.2, 1.23456789], [.75, .5], [1., .7654321]]


def project():
    media = Media('video', '/synthetic-local-fixture.mp4', 'Synthetic fixture', 10,
                  64, 48, True, False)
    return Project(width=64, height=48, media=[media], clips=[
        Clip('picture', 'video', 0, 0, 0, 30, grade=Grade(exposure=.25))])


def legacy_payload(version):
    data = project().to_dict()
    data['version'] = version; strip_contrast_pivot_field(data)
    clip = data['clips'][0]
    clip['grade'].pop('hue_hue_curve')
    clip['grade'].pop('hue_sat_curve')
    clip['grade'].pop('qualifier')
    if version < 16:
        clip.pop('transform')
    if version < 9:
        clip.pop('restoration')
    if version < 8:
        clip.pop('stabilization')
    if version < 7:
        clip.pop('time_map')
        clip['source_in'] = 0
    return data


@pytest.mark.parametrize('version', range(1, 17))
def test_all_legacy_schemas_migrate_to_exact_neutral_selective_curves(version):
    restored = Project.from_dict(legacy_payload(version))
    grade = restored.clips[0].grade
    assert grade.hue_hue_curve == [[0., 0.], [1., 0.]]
    assert grade.hue_sat_curve == [[0., 1.], [1., 1.]]
    assert grade.exposure == .25
    rgb = np.array([[-.23, .45, 1.17], [.25, .25, .25], [0., .2, .6]], np.float32)
    np.testing.assert_array_equal(apply_grade(rgb, grade), rgb * np.float32(2 ** .25))
    assert restored.to_dict()['version'] == VERSION


def test_schema17_roundtrip_and_defaults_are_independent(tmp_path):
    original = project(); bind_missing_project_paths(original, tmp_path)
    original.clips[0].grade.hue_hue_curve = deepcopy(HUE)
    original.clips[0].grade.hue_sat_curve = deepcopy(SAT)
    path = tmp_path / 'selective.lumen.json'
    original.save(path)
    stored = json.loads(path.read_text())
    assert stored['version'] == VERSION
    assert stored['clips'][0]['grade']['hue_hue_curve'] == HUE
    assert stored['clips'][0]['grade']['hue_sat_curve'] == SAT
    assert Project.load(path) == original
    restored = Project.from_dict(stored)
    restored.clips[0].grade.hue_hue_curve[1][1] = .1
    restored.clips[0].grade.hue_sat_curve[1][1] = .8
    assert original.clips[0].grade.hue_hue_curve == HUE
    assert stored['clips'][0]['grade']['hue_sat_curve'] == SAT
    first, second = Grade(), Grade()
    first.hue_hue_curve[0][1] = .2
    first.hue_sat_curve[0][1] = .5
    assert second.hue_hue_curve == [[0., 0.], [1., 0.]]
    assert second.hue_sat_curve == [[0., 1.], [1., 1.]]


@pytest.mark.parametrize('field,value', [('hue_hue_curve', HUE), ('hue_sat_curve', SAT)])
def test_edit_of_either_curve_is_one_undoable_transaction(field, value):
    editor = Editor(project())
    before = deepcopy(editor.project)
    grade = asdict(editor.project.clips[0].grade)
    grade[field] = deepcopy(value)
    editor.update_clip('picture', grade=grade)
    after = deepcopy(editor.project)
    assert getattr(after.clips[0].grade, field) == value
    assert editor.revision == 1 and len(editor._undo) == 1
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after


@pytest.mark.parametrize('encoding', ['rec709', 'srgb', 'linear_rec709'])
def test_selective_grade_copy_preserves_input_and_every_non_grade_property(tmp_path, encoding):
    original, _ = project_pair(tmp_path)
    original.clips[0].grade.hue_hue_curve = deepcopy(HUE)
    original.clips[0].grade.hue_sat_curve = deepcopy(SAT)
    original.clips[1].grade.input_encoding = encoding
    before = deepcopy(original)
    copied = copy_grade(original, 'source')
    contents = json.loads(copied.creative_json)
    assert contents['hue_hue_curve'] == HUE and contents['hue_sat_curve'] == SAT
    assert 'input_encoding' not in contents
    editor = Editor(original)
    assert paste_grade(editor, 'target', copied)
    after = deepcopy(editor.project)
    expected = deepcopy(before)
    expected.clips[1].grade = deepcopy(before.clips[0].grade)
    expected.clips[1].grade.input_encoding = encoding
    assert after == expected
    assert editor.revision == 1 and len(editor._undo) == 1
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after
    # Source and target never share curve lists, including after undo/redo.
    editor.project.clips[0].grade.hue_hue_curve[1][1] = .4
    editor.project.clips[1].grade.hue_sat_curve[1][1] = 1.9
    assert editor.project.clips[1].grade.hue_hue_curve == HUE
    assert copied.grade_for_input(encoding).hue_sat_curve == SAT
    assert copied.grade_for_input(encoding).hue_hue_curve == HUE


@pytest.mark.parametrize('field,invalid', [
    ('hue_hue_curve', [[0., .1], [1., 0.]]),
    ('hue_hue_curve', [[0., .51], [1., .51]]),
    ('hue_sat_curve', [[0., 1.], [.5, float('nan')], [1., 1.]]),
    ('hue_sat_curve', [[0., -1.], [1., -1.]]),
])
def test_invalid_selective_edit_rolls_back_project_and_history(field, invalid):
    editor = Editor(project())
    before = deepcopy(editor.project)
    grade = asdict(before.clips[0].grade)
    grade[field] = invalid
    with pytest.raises(ValueError):
        editor.update_clip('picture', grade=grade)
    assert editor.project == before and editor.revision == 0 and not editor.can_undo

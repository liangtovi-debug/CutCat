"""Schema-27 white-balance persistence and strict historical compatibility."""
from native_path_fixture import bind_missing_project_paths
from copy import deepcopy
from dataclasses import asdict, fields
import json
from uuid import uuid4

import numpy as np
import pytest

from lumen.color import apply_grade
from lumen.grade_transfer import copy_grade, paste_grade
from lumen.model import Editor, Grade, Project, VERSION
from lumen.project_recovery import RecoveryStore
from test_contrast_pivot_integration import legacy_payload
from test_grade_transfer import project_pair
from test_qualifier_integration import project


@pytest.mark.parametrize('version', range(1, 27))
def test_all_old_schemas_default_to_disabled_identity_without_mutation(version):
    payload, expected = legacy_payload(version)
    original = deepcopy(payload)
    restored = Project.from_dict(payload)
    assert payload == original and restored == expected
    grade = restored.clips[0].grade
    assert grade.white_balance_enabled is False
    assert grade.white_balance_gains == [1., 1., 1.]
    assert restored.to_dict()['version'] == VERSION
    rgb = np.array([[-.25, -.0, 1.25], [1/65535, .18, .731234567]], np.float32)
    assert apply_grade(rgb, grade).tobytes() == apply_grade(rgb, expected.clips[0].grade).tobytes()
    grade.contrast = 1.; grade.exposure = 0.
    assert apply_grade(rgb, grade).tobytes() == rgb.tobytes()


@pytest.mark.parametrize('version', range(1, 27))
@pytest.mark.parametrize('field,value', [('white_balance_enabled', False),
                                         ('white_balance_enabled', None),
                                         ('white_balance_gains', [1., 1., 1.]),
                                         ('white_balance_gains', None)])
def test_old_schemas_reject_smuggled_white_balance_even_default_or_null(version, field, value):
    payload, _ = legacy_payload(version)
    payload['clips'][0]['grade'][field] = value
    with pytest.raises(ValueError, match='[Ww]hite.balance'):
        Project.from_dict(payload)


@pytest.mark.parametrize('field', ['white_balance_enabled', 'white_balance_gains'])
def test_current_schema_requires_each_field(field):
    payload = project().to_dict()
    del payload['clips'][0]['grade'][field]
    with pytest.raises(ValueError, match='[Ww]hite.balance'):
        Project.from_dict(payload)


INVALID = [('white_balance_enabled', value) for value in (0, 1, None, 'true', [], {}, np.bool_(True))]
INVALID += [('white_balance_gains', value) for value in (
    None, True, 1., '1,1,1', {}, [], [1., 1.], [1., 1., 1., 1.],
    [True, 1., 1.], [None, 1., 1.], ['1', 1., 1.],
    [.249999, 1., 1.], [4.000001, 1., 1.],
    [float('nan'), 1., 1.], [1., float('inf'), 1.], [1., 1., -float('inf')],
    [np.float32(1.), 1., 1.])]


@pytest.mark.parametrize('field,value', INVALID)
@pytest.mark.parametrize('enabled', [False, True])
def test_invalid_values_fail_save_load_and_atomic_edit_even_when_grade_disabled(field, value, enabled):
    p = project(); p.clips[0].grade.enabled = enabled
    editor = Editor(p); original = deepcopy(p)
    pending = asdict(p.clips[0].grade); pending[field] = value
    with pytest.raises(ValueError, match='[Ww]hite.balance'):
        editor.update_clip('picture', grade=pending)
    assert editor.project == original and editor.revision == 0
    assert not editor.can_undo and not editor.can_redo
    payload = original.to_dict(); payload['clips'][0]['grade'][field] = value
    with pytest.raises(ValueError, match='[Ww]hite.balance'):
        Project.from_dict(payload)
    setattr(p.clips[0].grade, field, value)
    with pytest.raises(ValueError, match='[Ww]hite.balance'):
        p.to_dict()


@pytest.mark.parametrize('gains', [[.25, 1., 4.], [1.123456789012345, .987654321098765, 1.23456789012345]])
def test_full_precision_disk_recovery_and_atomic_history(tmp_path, gains):
    p = project(); bind_missing_project_paths(p, tmp_path); editor = Editor(p); before = deepcopy(p)
    pending = asdict(p.clips[0].grade)
    pending.update(white_balance_enabled=True, white_balance_gains=list(gains))
    editor.update_clip('picture', grade=pending)
    after = deepcopy(editor.project)
    pending['white_balance_gains'][0] = 2.
    assert editor.project == after and editor.revision == len(editor._undo) == 1
    path = tmp_path / 'white-balance.lumen'; editor.project.save(path)
    payload = json.loads(path.read_text())
    assert payload['version'] == VERSION
    assert payload['clips'][0]['grade']['white_balance_gains'] == gains
    assert Project.load(path) == after
    with RecoveryStore(tmp_path / 'recovery') as store:
        entry = store.write(editor.project, uuid4())
        assert store.load(entry) == after
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after


@pytest.mark.parametrize('encoding', ['rec709', 'srgb', 'linear_rec709'])
def test_copy_paste_preserves_white_balance_and_isolates_mutable_gains(tmp_path, encoding):
    p, _ = project_pair(tmp_path)
    p.clips[0].grade.white_balance_enabled = True
    p.clips[0].grade.white_balance_gains = [1.123456789012345, .987654321098765, 1.23456789012345]
    p.clips[1].grade.input_encoding = encoding
    before = deepcopy(p); copied = copy_grade(p, 'source'); editor = Editor(p)
    assert paste_grade(editor, 'target', copied)
    expected = deepcopy(before)
    expected.clips[1].grade = deepcopy(before.clips[0].grade)
    expected.clips[1].grade.input_encoding = encoding
    assert editor.project == expected and editor.revision == len(editor._undo) == 1
    assert not paste_grade(editor, 'target', copied) and editor.revision == 1
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == expected
    editor.project.clips[0].grade.white_balance_gains[0] = 2.
    editor.project.clips[1].grade.white_balance_gains[0] = 3.
    extracted = copied.grade_for_input(encoding)
    assert extracted.white_balance_gains == before.clips[0].grade.white_balance_gains
    extracted.white_balance_gains[0] = 4.
    assert copied.grade_for_input(encoding).white_balance_gains == before.clips[0].grade.white_balance_gains


def test_appended_fields_preserve_old_constructor_and_independent_defaults():
    assert [field.name for field in fields(Grade)][-3:] == ['contrast_pivot', 'white_balance_enabled', 'white_balance_gains']
    a = Grade(.25, 1.7, .8, .1, -.1, .2, -.2, False, '')
    b = Grade()
    assert (a.exposure, a.contrast, a.saturation, a.enabled) == (.25, 1.7, .8, False)
    assert a.white_balance_enabled is False and a.white_balance_gains == [1., 1., 1.]
    a.white_balance_gains[0] = 2.
    assert b.white_balance_gains == [1., 1., 1.]


def test_v26_reader_rejects_schema27_before_parsing_contents(monkeypatch):
    import lumen.model as model
    payload = project().to_dict(); payload['clips'] = 'deliberately malformed'
    monkeypatch.setattr(model, 'VERSION', 26)
    with pytest.raises(ValueError, match='Unsupported'):
        Project.from_dict(payload)


def test_future_schema_rejected_before_parsing_contents():
    payload = project().to_dict(); payload['version'] = VERSION + 1
    payload['clips'] = 'deliberately malformed'
    with pytest.raises(ValueError, match='Unsupported'):
        Project.from_dict(payload)


def test_schema26_preserves_existing_nondefault_pivot_while_defaulting_white_balance():
    payload, expected = legacy_payload(26)
    payload['clips'][0]['grade']['contrast_pivot'] = .123456789012345
    expected.clips[0].grade.contrast_pivot = .123456789012345
    assert Project.from_dict(payload) == expected


@pytest.mark.parametrize('wb_enabled', [False, True])
def test_nondefault_gains_persist_when_white_balance_is_disabled_or_enabled(wb_enabled):
    p = project(); grade = p.clips[0].grade
    grade.white_balance_enabled = wb_enabled
    grade.white_balance_gains = [.25, 1.123456789012345, 4.]
    restored = Project.from_dict(json.loads(json.dumps(p.to_dict())))
    assert restored == p
    assert restored.clips[0].grade.white_balance_enabled is wb_enabled
    restored.clips[0].grade.white_balance_gains[0] = 2.
    assert p.clips[0].grade.white_balance_gains[0] == .25

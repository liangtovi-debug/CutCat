"""Schema-28 vignette shape, historical vocabulary and transaction contracts."""
from copy import deepcopy
from dataclasses import asdict, fields
import json
from uuid import uuid4

import numpy as np
import pytest

from lumen.model import ClipEffect, Editor, Project, VERSION
from lumen.project_recovery import RecoveryStore
from lumen.ui_recovery import project_fingerprint
from test_contrast_pivot_integration import legacy_payload
from test_overlay_model import video_project
from vignette_legacy_fixture import strip_vignette_shape_fields


def project(**changes):
    p = video_project()
    p.clips[0].effects = [ClipEffect('shape', 'vignette', .4, **changes)]
    return p


def old_payload(version):
    if version == 27:
        p = project(); data = p.to_dict(); data['version'] = version
        strip_vignette_shape_fields(data)
    else:
        data, p = legacy_payload(version)
        p.clips[0].effects = [ClipEffect('shape', 'vignette', .4)]
        data['clips'][0]['effects'] = [{'id': 'shape', 'kind': 'vignette', 'amount': .4, 'enabled': True}]
    return data, p


def test_default_fields_and_positional_constructor_remain_compatible():
    assert VERSION >= 28
    assert [item.name for item in fields(ClipEffect)] == ['id', 'kind', 'amount', 'enabled', 'radius', 'feather']
    assert ClipEffect('old', 'vignette', .4, False) == ClipEffect('old', 'vignette', .4, False, 1., 1.)
    assert ClipEffect('blur', 'blur', 3.).radius == 1.


@pytest.mark.parametrize('version', range(1, 28))
def test_old_schemas_default_shape_without_mutating_payload(version):
    data, expected = old_payload(version); before = deepcopy(data)
    restored = Project.from_dict(data)
    assert restored == expected and data == before
    assert restored.clips[0].effects[0].radius == restored.clips[0].effects[0].feather == 1.
    assert restored.to_dict()['version'] == VERSION


@pytest.mark.parametrize('version', range(1, 28))
@pytest.mark.parametrize('field', ['radius', 'feather'])
@pytest.mark.parametrize('value', [1., None])
def test_old_schemas_reject_even_default_or_null_new_fields(version, field, value):
    data, _ = old_payload(version); data['clips'][0]['effects'][0][field] = value
    with pytest.raises(ValueError, match='Legacy.*vignette'):
        Project.from_dict(data)


@pytest.mark.parametrize('field', ['radius', 'feather'])
@pytest.mark.parametrize('kind', ['blur', 'vignette'])
def test_current_schema_requires_both_fields_on_every_effect(field, kind):
    p = project(); p.clips[0].effects[0].kind = kind
    data = p.to_dict(); del data['clips'][0]['effects'][0][field]
    with pytest.raises(ValueError, match='Version 28.*vignette'):
        Project.from_dict(data)


INVALID = [True, False, None, '1', [], {}, .249999, 2.000001,
           float('nan'), float('inf'), -float('inf'), np.float32(1.), np.bool_(True),
           pytest.param(10**1000, id='huge-positive'), pytest.param(-10**1000, id='huge-negative')]


@pytest.mark.parametrize('field', ['radius', 'feather'])
@pytest.mark.parametrize('value', INVALID)
@pytest.mark.parametrize('active', [True, False])
def test_invalid_shape_fails_save_load_and_atomic_edit_even_bypassed(field, value, active):
    p = project(enabled=active); p.clips[0].effects[0].amount = .4 if active else 0
    editor = Editor(p); before = deepcopy(p)
    with pytest.raises(ValueError, match='vignette'):
        editor.update_effect('clip', 'shape', **{field: value})
    assert editor.project == before and editor.revision == 0
    assert not editor.can_undo and not editor.can_redo
    data = before.to_dict(); data['clips'][0]['effects'][0][field] = value
    with pytest.raises(ValueError, match='vignette'): Project.from_dict(data)
    setattr(p.clips[0].effects[0], field, value)
    with pytest.raises(ValueError, match='vignette'): p.to_dict()


@pytest.mark.parametrize('field', ['radius', 'feather'])
@pytest.mark.parametrize('active', [True, False])
def test_blur_rejects_nondefault_shape_even_bypassed(field, active):
    p = project(enabled=active); p.clips[0].effects[0].kind = 'blur'
    p.clips[0].effects[0].amount = 3. if active else 0
    editor = Editor(p); before = deepcopy(p)
    with pytest.raises(ValueError, match='Blur effects require default'):
        editor.update_effect('clip', 'shape', **{field: .5})
    assert editor.project == before and not editor.can_undo
    data = before.to_dict(); data['clips'][0]['effects'][0][field] = .5
    with pytest.raises(ValueError, match='Blur effects require default'): Project.from_dict(data)
    setattr(p.clips[0].effects[0], field, .5)
    with pytest.raises(ValueError, match='Blur effects require default'): p.to_dict()


@pytest.mark.parametrize('radius,feather', [(.25, 2.), (2., .25), (1.123456789012345, .987654321098765)])
def test_full_precision_save_recovery_atomic_undo_and_redo(tmp_path, radius, feather):
    p = project(); editor = Editor(p); before = deepcopy(p)
    editor.update_effect('clip', 'shape', amount=.7, enabled=False, radius=radius, feather=feather)
    after = deepcopy(editor.project)
    assert editor.revision == len(editor._undo) == 1
    path = tmp_path / 'vignette.lumen'; editor.project.save(path)
    data = json.loads(path.read_text())
    assert data['version'] == VERSION and Project.load(path) == after
    assert data['clips'][0]['effects'][0] == asdict(after.clips[0].effects[0])
    with RecoveryStore(tmp_path / 'recovery') as store:
        entry = store.write(editor.project, uuid4())
        assert store.load(entry) == after
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after


def test_add_and_split_deepcopy_shape_and_preserve_source():
    p = video_project(); editor = Editor(p)
    original = ClipEffect('shape', 'vignette', .7, False, .45, 1.8)
    editor.add_effect('clip', original)
    original.radius = 2.
    assert editor.project.clips[0].effects[0].radius == .45
    editor.split_clip('clip', 30)
    assert len(editor.project.clips) == 2
    assert [c.effects[0].radius for c in editor.project.clips] == [.45, .45]
    editor.project.clips[1].effects[0].feather = .25
    assert editor.project.clips[0].effects[0].feather == 1.8


@pytest.mark.parametrize('field', ['radius', 'feather'])
def test_project_fingerprint_tracks_disabled_shape_edits(field):
    p = project(enabled=False); before = project_fingerprint(p)
    setattr(p.clips[0].effects[0], field, .75)
    assert project_fingerprint(p) != before


def test_v27_reader_rejects_new_schema_before_decoding(monkeypatch):
    import lumen.model as model
    data = project().to_dict(); data['clips'] = 'malformed'
    monkeypatch.setattr(model, 'VERSION', 27)
    with pytest.raises(ValueError, match='Unsupported'): Project.from_dict(data)


@pytest.mark.parametrize('entry', [None, 1, [], 'vignette'])
def test_malformed_effect_entries_fail_cleanly(entry):
    data = project().to_dict(); data['clips'][0]['effects'] = [entry]
    with pytest.raises(ValueError, match='Invalid clip effect'): Project.from_dict(data)


def test_unknown_effect_property_is_not_silently_discarded():
    data = project().to_dict(); data['clips'][0]['effects'][0]['raduis'] = .7
    with pytest.raises(ValueError, match='Malformed project'): Project.from_dict(data)

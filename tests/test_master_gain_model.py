"""Schema-34 project-wide gain is explicit, bounded and transaction-safe."""
from native_path_fixture import bind_missing_project_paths
from concurrent.futures import ThreadPoolExecutor
from media_bins_legacy_fixture import strip_media_bins_fields
from copy import deepcopy
from dataclasses import fields
from decimal import Decimal
from fractions import Fraction
import json
import math
from uuid import uuid4

import pytest

from lumen.model import Editor, Project, VERSION
from lumen.project_recovery import RecoveryStore
from test_clip_composite_model import legacy_payload as composite_legacy_payload, project


INVALID = [True, False, None, '', '0', [], {}, complex(0), Decimal('0'), Fraction(0),
           -96.0000001, 24.0000001, float('nan'), float('inf'), -float('inf'),
           10**400, -(10**400)]


def historical_payload(version):
    if version < 31:
        return composite_legacy_payload(version)
    data = project().to_dict()
    data['version'] = version
    strip_media_bins_fields(data)
    data.pop('master_gain_db')
    if version < 32:
        for clip in data['clips']:
            clip.pop('motion_keyframes')
    return data


def test_current_schema_and_appended_default_preserve_project_constructor():
    assert VERSION >= 34
    assert fields(Project)[11].name == 'master_gain_db'
    p = Project('Original', 1920, 1080, 24, 1)
    data = p.to_dict()
    assert data['version'] == VERSION and data['master_gain_db'] == 0.0
    assert type(data['master_gain_db']) is float
    assert (p.name, p.width, p.height, p.fps) == ('Original', 1920, 1080, 24)
    assert Project.from_dict(data) == p


@pytest.mark.parametrize('version', range(1, 34))
def test_every_historical_schema_migrates_absence_to_zero_without_mutation(version):
    data = historical_payload(version)
    before = deepcopy(data)
    restored = Project.from_dict(data)
    assert data == before and 'master_gain_db' not in data
    assert restored == project()
    assert restored.master_gain_db == 0.0
    assert math.copysign(1, restored.master_gain_db) == 1
    assert restored.to_dict()['version'] == VERSION


@pytest.mark.parametrize('version', range(1, 34))
@pytest.mark.parametrize('value', [0, 0.0, -0.0, -6, None, False])
def test_every_historical_schema_rejects_smuggled_gain_even_neutral(version, value):
    data = historical_payload(version)
    data['master_gain_db'] = value
    original = deepcopy(data)
    with pytest.raises(ValueError, match='Legacy projects cannot contain master output gain'):
        Project.from_dict(data)
    assert data == original


def test_current_schema_requires_gain_and_prior_reader_rejects_before_decoding(monkeypatch):
    data = project().to_dict()
    del data['master_gain_db']
    with pytest.raises(ValueError, match='require master output gain'):
        Project.from_dict(data)
    data = project().to_dict()
    data['clips'] = 'must not be decoded by an older reader'
    monkeypatch.setattr('lumen.model.VERSION', 33)
    with pytest.raises(ValueError, match='Unsupported project format or version'):
        Project.from_dict(data)


@pytest.mark.parametrize('value', INVALID)
def test_invalid_gain_rejected_by_validation_serializer_reader_and_atomic_editor(value):
    p = project()
    p.master_gain_db = value
    with pytest.raises(ValueError, match='master output gain'):
        p.validate()
    with pytest.raises(ValueError, match='master output gain'):
        p.to_dict()
    data = project().to_dict()
    data['master_gain_db'] = value
    with pytest.raises(ValueError, match='master output gain'):
        Project.from_dict(data)
    editor = Editor(project())
    editor.set_master_gain(-3)
    editor.undo()
    before = deepcopy(editor.project)
    undo, redo, revision = deepcopy(editor._undo), deepcopy(editor._redo), editor.revision
    with pytest.raises(ValueError, match='master output gain'):
        editor.set_master_gain(value)
    assert editor.project == before
    assert (editor._undo, editor._redo, editor.revision) == (undo, redo, revision)


@pytest.mark.parametrize('value', [-96, -96.0, -6.123456789012345, -0.0, 0, 0.0, 24, 24.0])
def test_bounds_and_full_precision_persist_to_disk_and_recovery(tmp_path, value):
    p = project(); bind_missing_project_paths(p, tmp_path)
    p.master_gain_db = value
    before = deepcopy(p)
    path = tmp_path / 'master-gain.lumen'
    p.save(path)
    stored = json.loads(path.read_text())
    assert stored['version'] == VERSION and stored['master_gain_db'] == value
    assert type(stored['master_gain_db']) is float
    if value == 0:
        assert math.copysign(1, stored['master_gain_db']) == 1
    assert Project.load(path) == before
    with RecoveryStore(tmp_path / 'recovery') as store:
        entry = store.write(p, uuid4())
        assert store.load(entry) == before
        envelope = json.loads(entry.path.read_text())
        assert envelope['project']['version'] == VERSION
        assert envelope['project']['master_gain_db'] == value


def test_one_edit_undo_redo_and_noop_preserve_all_other_state_and_redo():
    editor = Editor(project())
    before = deepcopy(editor.project)
    editor.set_master_gain(-6.123456789012345)
    after = deepcopy(editor.project)
    expected = deepcopy(before)
    expected.master_gain_db = -6.123456789012345
    assert after == expected and len(editor._undo) == editor.revision == 1
    editor.set_master_gain(-6.123456789012345)
    assert len(editor._undo) == editor.revision == 1
    assert editor.undo() and editor.project == before
    revision = editor.revision
    editor.set_master_gain(-0.0)
    assert editor.project == before and editor.revision == revision and editor.can_redo
    assert math.copysign(1, editor.project.master_gain_db) == 1
    assert editor.redo() and editor.project == after
    editor.set_master_gain(0)
    assert editor.project == before
    assert editor.undo() and editor.project == after


@pytest.mark.parametrize('value', [0, -6])
def test_gain_changes_and_noops_reject_non_owner_thread(value):
    editor = Editor(project())
    before = deepcopy(editor.project)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(editor.set_master_gain, value)
        with pytest.raises(ValueError, match='owning thread'):
            future.result()
    assert editor.project == before and editor.revision == 0
    assert not editor.can_undo and not editor.can_redo


def test_validation_failure_rolls_back_gain_and_history(monkeypatch):
    editor = Editor(project())
    editor.set_master_gain(-3)
    editor.undo()
    before = deepcopy(editor.project)
    undo, redo, revision = deepcopy(editor._undo), deepcopy(editor._redo), editor.revision
    original = Project.validate
    def reject_changed_gain(self):
        original(self)
        if self.master_gain_db == -6:
            raise ValueError('injected post-change validation failure')
    monkeypatch.setattr(Project, 'validate', reject_changed_gain)
    with pytest.raises(ValueError, match='injected post-change'):
        editor.set_master_gain(-6)
    assert editor.project == before
    assert (editor._undo, editor._redo, editor.revision) == (undo, redo, revision)


def test_invalid_save_preserves_existing_file(tmp_path):
    p = project()
    path = tmp_path / 'keep-original.lumen'
    p.save(path)
    original = path.read_bytes()
    p.master_gain_db = float('nan')
    with pytest.raises(ValueError, match='master output gain'):
        p.save(path)
    assert path.read_bytes() == original

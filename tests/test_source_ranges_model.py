"""Schema-38 source metadata: strict rationals, atomic history, no file I/O."""
from copy import deepcopy
from dataclasses import asdict, fields, replace
from fractions import Fraction
import json
from pathlib import Path
from threading import Thread
from uuid import uuid4

import pytest

from lumen.model import Clip, ClipGroup, Editor, Media, Project, VERSION
from lumen.project_recovery import RecoveryStore
from lumen.source_ranges import (MAX_SOURCE_RANGES, MAX_SOURCE_RANGE_FRAME,
    SourceRange, SourceRangeBinding, range_frames, validate_source_range,
    validate_source_range_binding, validate_source_ranges)


def binding():
    return SourceRangeBinding(1, 4096, 'a'*64, 'b'*64)


def project(fps=Fraction(30)):
    return Project(fps_num=fps.numerator, fps_den=fps.denominator,
                   media=[Media('m', '/missing/source.mp4', 'Source', 120,
                                64, 48, True, True),
                          Media('n', '/missing/other.mp4', 'Other', 120)])


def record(**changes):
    return replace(SourceRange('range', '精选 / <b>A</b> 🎬', 'm', 1, 2, 3, 2, 30, 1, binding()), **changes)


def populated():
    value = project()
    value.source_ranges = [record()]
    return value


def legacy(version):
    payload = {'format': 'lumen-project', 'version': version}
    if version >= 33: payload['video_transitions'] = []
    if version >= 34: payload['master_gain_db'] = 0.0
    if version >= 35: payload.update(media_bins=[], media_bin_ids={})
    if version >= 36: payload['clip_groups'] = []
    if version >= 37: payload['track_edit_locks'] = [False]*4
    return payload


@pytest.mark.parametrize('version', range(1, 38))
def test_all_legacy_schemas_migrate_empty_and_preserve_input(version):
    payload = legacy(version)
    before = deepcopy(payload)
    loaded = Project.from_dict(payload)
    assert loaded.source_ranges == [] and payload == before
    assert loaded.to_dict()['version'] == VERSION == 43


@pytest.mark.parametrize('version', range(1, 38))
@pytest.mark.parametrize('value', [[], None, [asdict(record())]])
def test_all_legacy_versions_reject_even_empty_new_field(version, value):
    with pytest.raises(ValueError, match='Legacy projects cannot contain saved source ranges'):
        Project.from_dict({**legacy(version), 'source_ranges': value})


def test_current_required_field_forward_fence_and_independent_default():
    a, b = project(), project()
    a.source_ranges.append(record())
    assert b.source_ranges == []
    payload = a.to_dict()
    payload.pop('source_ranges')
    with pytest.raises(ValueError, match='require saved source ranges'):
        Project.from_dict(payload)
    payload = b.to_dict(); payload['version'] = VERSION + 1
    with pytest.raises(ValueError, match='Unsupported project'):
        Project.from_dict(payload)
    assert fields(Project)[-3].name == 'source_ranges'
    assert fields(Project)[-2].name == 'timeline_markers'
    assert fields(Project)[-1].name == 'av_links'


@pytest.mark.parametrize('value', [None, {}, (), '', 3, [None], [{}], [[]], [True]])
def test_malformed_collection_rejected_in_memory_and_wire(value):
    p = project(); p.source_ranges = value
    with pytest.raises(ValueError): p.validate()
    payload = project().to_dict(); payload['source_ranges'] = value
    with pytest.raises(ValueError): Project.from_dict(payload)


@pytest.mark.parametrize('field', [f.name for f in fields(SourceRange)])
def test_every_record_field_required(field):
    payload = populated().to_dict()
    del payload['source_ranges'][0][field]
    with pytest.raises(ValueError, match='fields'): Project.from_dict(payload)


@pytest.mark.parametrize('field', [f.name for f in fields(SourceRangeBinding)])
def test_every_binding_field_required(field):
    payload = populated().to_dict()
    del payload['source_ranges'][0]['binding'][field]
    with pytest.raises(ValueError, match='binding fields'): Project.from_dict(payload)


@pytest.mark.parametrize('key', ['path', 'state', 'verified', 'session_token', 'future'])
@pytest.mark.parametrize('nested', [False, True])
def test_unknown_and_cached_state_fields_rejected(key, nested):
    payload = populated().to_dict()
    target = payload['source_ranges'][0]
    if nested: target = target['binding']
    target[key] = '/tmp/session-cache'
    with pytest.raises(ValueError, match='fields'): Project.from_dict(payload)


@pytest.mark.parametrize('key,value', [
    ('id', ''), ('id', ' '), ('id', 'a'*129), ('id', None), ('id', 'bad\0id'),
    ('name', ''), ('name', '\t'), ('name', 'a'*129), ('name', ['name']),
    ('name', 'two\nlines'), ('name', 'a\x7fb'), ('name', True),
    ('name', '\ud800'), ('id', '\udfff'),
    ('name', 'A\u2028B'), ('name', 'A\u2029B'),
    ('media_id', ''), ('media_id', 'unknown'), ('media_id', []), ('media_id', 'm'*129),
    ('in_num', True), ('in_num', -1), ('in_num', .5), ('in_num', '1'),
    ('in_num', 2**63), ('in_den', 0), ('in_den', -2), ('in_den', 1_000_001),
    ('in_den', 2.0), ('in_den', False), ('out_num', 0), ('out_num', True),
    ('out_num', 2**63), ('out_den', 0), ('out_den', None),
    ('capture_fps_num', 0), ('capture_fps_num', True), ('capture_fps_num', 121),
    ('capture_fps_num', 1_000_001), ('capture_fps_den', 0),
    ('capture_fps_den', True), ('capture_fps_den', 1_000_001),
    ('binding', None), ('binding', {}),
])
def test_strict_record_fields_reject_without_mutation(key, value):
    payload = populated().to_dict()
    payload['source_ranges'][0][key] = value
    before = deepcopy(payload)
    with pytest.raises(ValueError): Project.from_dict(payload)
    assert payload == before


@pytest.mark.parametrize('changes', [
    {'in_num': 0, 'in_den': 2}, {'in_num': 2, 'in_den': 4},
    {'out_num': 6, 'out_den': 4}, {'capture_fps_num': 60, 'capture_fps_den': 2},
    {'in_num': 3}, {'out_num': 1}, {'in_den': 7},
    {'capture_fps_num': 1, 'capture_fps_den': 1001},
])
def test_noncanonical_or_non_capture_grid_bounds_rejected(changes):
    with pytest.raises(ValueError): validate_source_range(record(**changes))


@pytest.mark.parametrize('key,value', [
    ('algorithm_version', 0), ('algorithm_version', 2), ('algorithm_version', True),
    ('algorithm_version', 1.0), ('file_size', 0), ('file_size', -1),
    ('file_size', True), ('file_size', 2**63), ('file_size', '4096'),
    ('sampled_content_sha256', 'a'*63), ('sampled_content_sha256', 'A'*64),
    ('sampled_content_sha256', 'g'*64), ('sampled_content_sha256', None),
    ('semantic_source_signature', 'b'*65), ('semantic_source_signature', []),
])
def test_binding_version_size_and_hash_strict(key, value):
    with pytest.raises(ValueError): validate_source_range_binding(replace(binding(), **{key: value}))
    payload = populated().to_dict(); payload['source_ranges'][0]['binding'][key] = value
    with pytest.raises(ValueError): Project.from_dict(payload)


def test_bounded_count_unique_ids_and_utf8_name():
    p = populated()
    p.source_ranges = [replace(record(), id=str(index), name='🎬'*128) for index in range(MAX_SOURCE_RANGES)]
    p.validate()
    p.source_ranges.append(record())
    with pytest.raises(ValueError, match='at most 10000'): p.validate()
    p.source_ranges = [record(), record()]
    with pytest.raises(ValueError, match='Duplicate'): p.validate()


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(25), Fraction(30), Fraction(24000,1001), Fraction(30000,1001)])
def test_capture_rational_roundtrip_and_recall_on_original_grid(fps):
    editor = Editor(project(fps))
    saved = editor.save_source_range('m', 'First', 7, 17, binding())
    assert saved.in_seconds == Fraction(7)/fps and saved.out_seconds == Fraction(17)/fps
    assert saved.capture_fps == fps
    assert range_frames(saved, fps) == (7,17)
    assert Project.from_dict(editor.project.to_dict()) == editor.project


@pytest.mark.parametrize('capture,target,start,end,expected', [
    (Fraction(24), Fraction(30), 4, 8, (5,10)),
    (Fraction(30), Fraction(25), 6, 12, (5,10)),
    (Fraction(25), Fraction(24), 25, 50, (24,48)),
    (Fraction(24000,1001), Fraction(30000,1001), 4, 8, (5,10)),
    (Fraction(30000,1001), Fraction(24000,1001), 5, 10, (4,8)),
])
def test_exact_cross_grid_conversion(capture, target, start, end, expected):
    e = Editor(project(capture)); saved = e.save_source_range('m', 'A', start, end, binding())
    before = deepcopy(e.project)
    assert range_frames(saved, target) == expected
    assert e.project == before


@pytest.mark.parametrize('capture,target', [
    (Fraction(24),Fraction(30)), (Fraction(25),Fraction(30)),
    (Fraction(30),Fraction(24)), (Fraction(24000,1001),Fraction(30000,1001)),
    (Fraction(30000,1001),Fraction(30)),
])
def test_nonintegral_recall_rejects_without_round_or_clamp(capture, target):
    e = Editor(project(capture)); saved = e.save_source_range('m', 'A', 1, 3, binding())
    original = deepcopy(saved)
    with pytest.raises(ValueError, match='align exactly'): range_frames(saved, target)
    assert saved == original
    with pytest.raises(ValueError, match='complete source duration'): range_frames(saved, capture, 2)


@pytest.mark.parametrize('fps', [None, 30.0, True, False, '30', Fraction(0), Fraction(-30)])
def test_recall_requires_exact_positive_rate(fps):
    with pytest.raises(ValueError): range_frames(record(), fps)


def test_long_range_metadata_is_not_limited_to_apply_proof_budget():
    e = Editor(project()); saved = e.save_source_range('m','Long',0,3600,binding())
    assert saved.out_seconds == 120 and range_frames(saved,30) == (0,3600)


def snapshot(editor):
    return deepcopy((editor.project, editor._undo, editor._redo, editor.revision))


def test_save_rename_delete_each_one_undo_and_noop_retains_redo():
    e = Editor(project()); original = deepcopy(e.project)
    saved = e.save_source_range('m','First',15,45,binding())
    first = deepcopy(e.project)
    assert len(e._undo) == 1 and e.revision == 1
    e.rename_source_range(saved.id,'Renamed'); renamed = deepcopy(e.project)
    assert len(e._undo) == 2 and e.revision == 2
    e.delete_source_range(saved.id)
    assert len(e._undo) == 3 and not e.project.source_ranges
    assert e.undo() and e.project == renamed
    assert e.undo() and e.project == first
    revision = e.revision
    e.rename_source_range(saved.id,'First')
    assert e.revision == revision and e.can_redo
    assert e.redo() and e.project == renamed
    assert e.redo() and not e.project.source_ranges
    for _ in range(3): assert e.undo()
    assert e.project == original
    assert saved.name == 'First'  # Immutable external snapshot cannot edit history.


@pytest.mark.parametrize('action', [
    lambda e: e.save_source_range('unknown','A',0,1,binding()),
    lambda e: e.save_source_range('m','',0,1,binding()),
    lambda e: e.save_source_range('m','A',True,1,binding()),
    lambda e: e.save_source_range('m','A',0,False,binding()),
    lambda e: e.save_source_range('m','A',10,10,binding()),
    lambda e: e.save_source_range('m','A',11,10,binding()),
    lambda e: e.save_source_range('m','A',0,3601,binding()),
    lambda e: e.save_source_range('m','A',0,MAX_SOURCE_RANGE_FRAME+1,binding()),
    lambda e: e.rename_source_range('missing','A'),
    lambda e: e.delete_source_range('missing'),
    lambda e: e.rename_source_range('range',''),
])
def test_invalid_transactions_leave_entire_history_unchanged(action):
    e = Editor(populated()); before = snapshot(e)
    with pytest.raises(ValueError): action(e)
    assert snapshot(e) == before


def test_saved_ranges_protect_unused_media_but_never_change_clips_locks_groups():
    p = project()
    p.clips = [Clip('a','m',0,0,0,30), Clip('b','m',0,30,30,30)]
    p.clip_groups = [ClipGroup('g',['a','b'])]
    p.track_edit_locks = [True,True,True,True]
    e = Editor(p); original = deepcopy(p)
    saved = e.save_source_range('n','Unused by timeline',0,30,binding())
    assert not any(clip.media_id == 'n' for clip in e.project.clips)
    before = snapshot(e)
    with pytest.raises(ValueError, match='referenced by saved source ranges'): e.remove_unused_media(['n'])
    assert snapshot(e) == before
    e.rename_source_range(saved.id,'B'); e.delete_source_range(saved.id)
    assert e.project == original
    e.remove_unused_media(['n'])
    assert [m.id for m in e.project.media] == ['m']


def test_pure_validation_conversion_and_operations_never_stat_or_decode(monkeypatch):
    e = Editor(project())
    def forbidden(*args, **kwargs): pytest.fail('Pure source-range model performed I/O')
    monkeypatch.setattr(Path,'stat',forbidden)
    monkeypatch.setattr(Path,'open',forbidden)
    monkeypatch.setattr(Path,'resolve',forbidden)
    saved = e.save_source_range('m','A',15,45,binding())
    e.project.validate()
    loaded = Project.from_dict(e.project.to_dict())
    assert range_frames(loaded.source_ranges[0],Fraction(30)) == (15,45)
    e.rename_source_range(saved.id,'B'); e.delete_source_range(saved.id)


def test_missing_changed_shorter_relink_preserves_metadata_in_save_recovery(tmp_path):
    e = Editor(populated()); saved = deepcopy(e.project.source_ranges)
    # A changed/missing source is loadable and saveable, not silently rebound.
    e.project.media[0].path = str(tmp_path/'new-location-but-missing.mov')
    e.project.media[0].duration = .1
    e.project.media[0].audio_offset = .2
    e.project.fps_num = 25
    e.project.validate()
    assert e.project.source_ranges == saved
    target = tmp_path/'项目.lumen'; e.project.save(target)
    reopened = Project.load(target)
    assert reopened.source_ranges == saved
    wire = json.loads(target.read_text())['source_ranges']
    assert wire == [asdict(saved[0])]
    assert set(wire[0]['binding']) == {'algorithm_version','file_size','sampled_content_sha256','semantic_source_signature'}
    with RecoveryStore(tmp_path/'recovery') as recovery:
        entry = recovery.write(reopened,str(uuid4()),target)
        assert recovery.load(entry).source_ranges == saved
    with pytest.raises(ValueError): range_frames(saved[0],reopened.fps,2)
    e.rename_source_range(saved[0].id,'Still manageable'); e.delete_source_range(saved[0].id)


def test_worker_cannot_mutate_editor():
    e = Editor(populated()); before = snapshot(e); errors=[]
    def operation():
        try: e.rename_source_range('range','Worker')
        except ValueError as exc: errors.append(str(exc))
    thread=Thread(target=operation); thread.start(); thread.join()
    assert errors and 'owning thread' in errors[0] and snapshot(e) == before

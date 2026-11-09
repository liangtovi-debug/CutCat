"""Strict persisted edit locks protect whole records and atomic transactions."""
from native_path_fixture import bind_missing_project_paths
from copy import deepcopy
from dataclasses import replace
from threading import Thread
from uuid import uuid4

import pytest

from lumen.model import (Caption, Clip, ClipEffect, Editor, Grade, Media, Overlay,
                         Project, VERSION, VideoTransition)
from lumen.project_recovery import RecoveryStore
from lumen.clip_transform import ClipTransform
from lumen.clip_composite import ClipComposite
from lumen.clip_motion import ClipMotionKeyframe


def sample():
    return Project(media=[Media('m', '/missing/source.mov', 'Source', 20, 64, 48, True, True),
                          Media('other', '/missing/other.mov', 'Other', 20, 64, 48, True, True)],
                   clips=[Clip('a', 'm', 0, 10, 30, 20), Clip('b', 'm', 0, 30, 60, 20),
                          Clip('c', 'm', 1, 60, 30, 20), Clip('d', 'm', 2, 60, 30, 20)],
                   captions=[Caption('caption', 10, 10, 'Independent')],
                   overlays=[Overlay('sticker', '/missing/sticker.png', 'a'*64, 10, 10)])


def locked(root=None):
    project = sample()
    if root is not None:
        bind_missing_project_paths(project, root)
    editor = Editor(project)
    editor.set_track_edit_lock(0, True)
    return editor


def snapshot(editor):
    return deepcopy((editor.project, editor._undo, editor._redo, editor.revision, editor.session_id))


def rejected(editor, action, match=None):
    before = snapshot(editor)
    with pytest.raises(ValueError, match=match):
        action()
    assert snapshot(editor) == before


def legacy(version):
    value = {'format': 'lumen-project', 'version': version}
    if version >= 33:
        value['video_transitions'] = []
    if version >= 34:
        value['master_gain_db'] = 0.0
    if version >= 35:
        value.update(media_bins=[], media_bin_ids={})
    if version >= 36:
        value['clip_groups'] = []
    return value


@pytest.mark.parametrize('version', range(1, 37))
def test_every_legacy_version_migrates_unlocked_and_rejects_injection(version):
    assert Project.from_dict(legacy(version)).track_edit_locks == [False]*4
    for value in ([False]*4, [True]*4, None):
        with pytest.raises(ValueError, match='Legacy projects cannot contain track edit locks'):
            Project.from_dict({**legacy(version), 'track_edit_locks': value})


@pytest.mark.parametrize('value', [None, {}, (), (False,)*4, '', [], [False]*3,
                                   [False]*5, [0]*4, [1]*4, ['false']*4,
                                   [False, False, False, None]])
def test_lock_field_is_strict_in_memory_and_json(value):
    project = Project(track_edit_locks=value)
    with pytest.raises(ValueError, match='exactly four booleans'):
        project.validate()
    data = Project().to_dict()
    data['track_edit_locks'] = value
    with pytest.raises(ValueError, match='exactly four booleans'):
        Project.from_dict(data)


def test_current_schema_requires_locks_and_defaults_are_independent():
    a, b = Project(), Project()
    a.track_edit_locks[0] = True
    assert b.track_edit_locks == [False]*4
    data = a.to_dict()
    assert data['version'] == VERSION == 43
    assert Project.from_dict(data) == a
    del data['track_edit_locks']
    with pytest.raises(ValueError, match='require track edit locks'):
        Project.from_dict(data)


def test_save_load_and_recovery_preserve_locks(tmp_path):
    project = locked(tmp_path).project
    path = tmp_path/'project.lumen'
    project.save(path)
    assert Project.load(path).to_dict() == project.to_dict()
    store = RecoveryStore(tmp_path/'recovery')
    try:
        entry = store.write(project, str(uuid4()), path)
        assert store.load(entry).to_dict() == project.to_dict()
    finally:
        store.close()


@pytest.mark.parametrize('track', [True, False, -1, 4, 0.0, '0', None])
def test_lock_api_rejects_bad_tracks(track):
    editor = locked()
    rejected(editor, lambda: editor.set_track_edit_lock(track, True))
    with pytest.raises(ValueError):
        editor.is_track_edit_locked(track)


@pytest.mark.parametrize('value', [0, 1, None, 'true', [], {}])
def test_lock_api_rejects_non_boolean(value):
    editor = locked()
    rejected(editor, lambda: editor.set_track_edit_lock(0, value))


def test_lock_history_noops_and_owner_thread():
    editor = locked()
    before = snapshot(editor)
    editor.set_track_edit_lock(0, True)
    editor.update_clip('a', start=10)
    editor.move_clips(['a'], 0)
    editor.trim_edge('a', 'right', 0)
    editor.roll_cut('a', 30)
    assert snapshot(editor) == before
    assert editor.undo()
    assert editor.project.track_edit_locks == [False]*4
    assert editor.redo()
    assert editor.is_track_edit_locked(0)
    errors = []
    def worker():
        try:
            editor.set_track_edit_lock(0, False)
        except ValueError as exc:
            errors.append(str(exc))
    thread = Thread(target=worker)
    thread.start()
    thread.join()
    assert errors and 'owning thread' in errors[0]
    assert editor.is_track_edit_locked(0)
    editor.set_track_edit_lock(0, False)
    editor.move_clip('a', 5)
    editor.undo()
    editor.undo()
    assert editor.is_track_edit_locked(0)
    assert editor.project.clips[0].start == 10


@pytest.mark.parametrize('action', [
    lambda e: e.move_clip('a', 5), lambda e: e.move_clip('a', 10, 1),
    lambda e: e.move_clip('c', 60, 0), lambda e: e.add_clip('m', 0, 100),
    lambda e: e.delete_clip('a'), lambda e: e.delete_clips(['a', 'c']),
    lambda e: e.ripple_delete_clip('a'), lambda e: e.split_clip('a', 20),
    lambda e: e.trim_clip('a', 31, 19), lambda e: e.trim_clip_source('a', 31, 49),
    lambda e: e.trim_clip_left('a', 1), lambda e: e.slip_clip('a', 31),
    lambda e: e.set_clip_speed('a', 2), lambda e: e.update_clip('a', gain_db=-3),
    lambda e: e.update_clip('a', muted=True), lambda e: e.update_clip('a', fade_in=2),
    lambda e: e.update_clip('a', grade=Grade(exposure=.5)),
    lambda e: e.update_clip('a', media_id='other'),
    lambda e: e.update_clip('a', transform=ClipTransform(x=3)),
    lambda e: e.update_clip('a', composite=ClipComposite(opacity=.5)),
    lambda e: e.update_stabilization('a', enabled=True),
    lambda e: e.update_restoration('a', enabled=True),
    lambda e: e.add_effect('a', ClipEffect('v', 'vignette', .3)),
    lambda e: e.move_clips(['a', 'c'], 1), lambda e: e.trim_edge('a', 'left', 1),
    lambda e: e.roll_cut('a', 31), lambda e: e.preview_move_clips(['a', 'c'], 1),
    lambda e: e.preview_trim_edge('a', 'left', 1), lambda e: e.preview_roll_cut('a', 31),
    lambda e: e.group_clips(['a', 'c']),
    lambda e: e.update_clip('a', motion_keyframes=[ClipMotionKeyframe(0, x=3)]),
    lambda e: e.prepare_video_transition('a', 'b', 4),
    lambda e: e.add_video_transition('a', 'b', 4),
])
def test_all_edit_paths_reject_atomically(action):
    editor = locked()
    rejected(editor, lambda: action(editor), 'edit-locked|Unlock the track')


def test_empty_locked_destination_and_other_tracks():
    editor = Editor(sample())
    editor.set_track_edit_lock(3, True)
    rejected(editor, lambda: editor.add_clip('m', 3, 100), 'edit-locked')
    rejected(editor, lambda: editor.move_clip('d', 60, 3), 'edit-locked')
    editor.move_clip('c', 80)
    editor._commit(lambda: setattr(editor.project, 'width', 1920))


def test_generic_unlock_edit_and_nested_transaction_bypasses_fail():
    editor = locked()
    rejected(editor, lambda: editor._commit(lambda: editor.project.track_edit_locks.__setitem__(0, False)))
    def unlock_edit():
        editor.project.track_edit_locks[0] = False
        editor.project.clips[0].gain_db = -6
    rejected(editor, lambda: editor._commit(unlock_edit))
    rejected(editor, lambda: editor._commit(lambda: editor.set_track_edit_lock(0, False)), 'Nested')
    rejected(editor, lambda: editor._commit(lambda: editor.undo()), 'Nested')
    rejected(editor, lambda: editor._commit(lambda: setattr(editor, 'project', Project())), 'Nested')
    def batch():
        editor.project.clips[2].start += 1
        editor.project.clips[0].grade.exposure = .5
    rejected(editor, lambda: editor._commit(batch), 'edit-locked')
    # Numeric boolean equality must not allow malformed lock state through.
    rejected(editor, lambda: editor._commit(lambda: setattr(editor.project, 'track_edit_locks', [1, 0, 0, 0])))


def test_dedicated_lock_transaction_cannot_carry_extra_edits():
    editor = locked()
    def change():
        editor.project.track_edit_locks[0] = False
        editor.project.name = 'smuggled'
    rejected(editor, lambda: editor._Editor__commit(change, track_edit_locks=[False]*4), 'only the requested')


def test_locked_group_blocks_membership_and_whole_group_edits():
    editor = Editor(sample())
    editor.group_clips(['a', 'c'])
    editor.set_track_edit_lock(0, True)
    rejected(editor, lambda: editor.ungroup_clips(['c']), 'Unlock the track')
    rejected(editor, lambda: editor.move_clips(['a', 'c'], 1), 'edit-locked')
    rejected(editor, lambda: editor.delete_clips(['a', 'c']), 'edit-locked')


@pytest.mark.parametrize('field,value', [('width', 1920), ('height', 1080), ('fps_num', 24), ('fps_den', 2)])
def test_populated_locks_block_project_geometry_and_rate(field, value):
    editor = locked()
    rejected(editor, lambda: editor._commit(lambda: setattr(editor.project, field, value)), 'populated tracks')


@pytest.mark.parametrize('field,value', [('path', '/missing/relinked.mov'), ('name', 'Renamed'),
                                        ('input_encoding', 'srgb'), ('duration', 30)])
def test_referenced_media_is_protected_even_when_shared(field, value):
    editor = locked()
    rejected(editor, lambda: editor._commit(lambda: setattr(editor.project.media[0], field, value)), 'source media')


def test_transition_add_change_and_removal_are_protected():
    editor = Editor(sample())
    transition = VideoTransition('t', 'a', 'b', 4)
    editor._commit(lambda: editor.project.video_transitions.append(transition))
    editor.set_track_edit_lock(0, True)
    rejected(editor, lambda: editor.delete_video_transition('t'), 'transitions')
    rejected(editor, lambda: editor._commit(lambda: editor.project.video_transitions.__setitem__(0, replace(transition, duration_frames=6))), 'transitions')
    other = locked()
    rejected(other, lambda: other._commit(lambda: other.project.video_transitions.append(transition)), 'transitions')


def test_unrelated_anchors_pool_bins_and_master_output_remain_editable():
    editor = locked()
    protected = deepcopy(editor.project.clips[:2])
    editor.update_caption('caption', text='Updated')
    editor.update_overlay('sticker', start=15)
    editor.set_master_gain(-4)
    editor._commit(lambda: setattr(editor.project, 'name', 'Renamed project'))
    editor._commit(lambda: setattr(editor.project.media[1], 'name', 'Unused renamed source'))
    editor.add_media(Media('new', '/missing/new.mov', 'New', 5, 64, 48))
    folder = editor.create_media_bin('Bin')
    editor.move_media_to_bin(['m', 'new'], folder.id)
    editor.rename_media_bin(folder.id, 'Renamed bin')
    editor.remove_unused_media(['other'])
    editor.move_clip('c', 90)
    assert editor.project.clips[:2] == protected
    assert editor.project.track_edit_locks == [True, False, False, False]


@pytest.mark.parametrize('track', range(4))
def test_all_four_tracks_protect_audio_and_video_records(track):
    project = sample()
    project.clips = [Clip('locked', 'm', track, 10, 30, 20)]
    editor = Editor(project)
    editor.set_track_edit_lock(track, True)
    rejected(editor, lambda: editor.update_clip('locked', gain_db=-3), 'edit-locked')
    rejected(editor, lambda: editor.add_clip('m', track, 100), 'edit-locked')
    rejected(editor, lambda: editor.delete_clip('locked'), 'edit-locked')


def test_generic_locking_is_not_an_authorization_path():
    editor = Editor(sample())
    rejected(editor, lambda: editor._commit(lambda: editor.project.track_edit_locks.__setitem__(0, True)), 'set_track_edit_lock')


def test_effect_order_and_hidden_animation_changes_are_protected():
    project = sample()
    project.clips[0].effects = [ClipEffect('blur', 'blur', 1), ClipEffect('v', 'vignette', .3)]
    project.clips[0].motion_keyframes = [ClipMotionKeyframe(0), ClipMotionKeyframe(10, x=3)]
    editor = Editor(project)
    editor.set_track_edit_lock(0, True)
    rejected(editor, lambda: editor.update_effect('a', 'blur', amount=2), 'edit-locked')
    rejected(editor, lambda: editor.delete_effect('a', 'blur'), 'edit-locked')
    rejected(editor, lambda: editor.move_effect('a', 'blur', 1), 'edit-locked')
    rejected(editor, lambda: editor.convert_clip_motion_to_static('a', 5), 'edit-locked')


def test_unrelated_groups_can_be_created_edited_and_removed():
    editor = locked()
    editor.group_clips(['c', 'd'])
    editor.move_clips(['c', 'd'], 3)
    editor.ungroup_clips(['c'])
    assert editor.project.clip_groups == []
    assert editor.project.clips[0].start == 10


def test_locked_clip_id_reassignment_and_source_deletion_are_atomic():
    editor = locked()
    rejected(editor, lambda: editor._commit(lambda: setattr(editor.project.clips[0], 'id', 'replacement')), 'edit-locked')
    rejected(editor, lambda: editor._commit(lambda: editor.project.media.pop(0)), 'source media')
    def replace_locked_with_unlocked():
        editor.project.clips[0], editor.project.clips[2] = (
            replace(editor.project.clips[2], id='a', track=0),
            replace(editor.project.clips[0], id='c', track=1))
    rejected(editor, lambda: editor._commit(replace_locked_with_unlocked), 'edit-locked')


@pytest.mark.parametrize('undo_lock', [False, True])
def test_transition_proof_minted_before_lock_and_lock_undo_aba_is_stale(monkeypatch, undo_lock):
    from lumen.video_transition_preflight import validate_video_transition_request
    monkeypatch.setattr('lumen.video_transition_sources.prepare_transition_source_certificate', lambda *a, **k: ())
    monkeypatch.setattr('lumen.video_transition_sources.verify_transition_source_identities', lambda *a, **k: None)
    editor = Editor(sample())
    before = deepcopy(editor.project)
    certificate = validate_video_transition_request(editor.prepare_video_transition('a', 'b', 4))
    editor.set_track_edit_lock(0, True)
    if undo_lock:
        editor.undo()
        assert editor.project == before
    rejected(editor, lambda: editor.commit_video_transition(certificate), 'Unlock the track|stale')
    assert editor.project.video_transitions == []


def test_nested_grade_and_source_interpretation_records_are_protected():
    editor = locked()
    rejected(editor, lambda: editor._commit(lambda: editor.project.clips[0].grade.gain.__setitem__(0, 1.1)), 'edit-locked')
    rejected(editor, lambda: editor._commit(lambda: editor.project.clips[0].grade.master_curve[1].__setitem__(1, .9)), 'edit-locked')
    rejected(editor, lambda: editor._commit(lambda: setattr(editor.project.clips[0].grade.qualifier, 'enabled', True)), 'edit-locked')
    rejected(editor, lambda: editor.update_clip('a', grade=Grade(input_encoding='srgb')), 'edit-locked')


def test_unlocked_ripple_does_not_touch_downstream_locked_other_track():
    project = sample()
    project.clips.extend([Clip('upstream', 'm', 1, 0, 0, 10),
                          Clip('locked-tail', 'm', 0, 100, 100, 20)])
    editor = Editor(project)
    editor.set_track_edit_lock(0, True)
    protected = deepcopy([clip for clip in editor.project.clips if clip.track == 0])
    editor.ripple_delete_clip('upstream')
    assert [clip for clip in editor.project.clips if clip.track == 0] == protected
    assert next(clip for clip in editor.project.clips if clip.id == 'c').start == 50
    # Any caller proposing a broader downstream ripple must roll back even
    # when its explicit selected clip is on an unlocked track.
    def cross_track_ripple():
        selected = next(clip for clip in editor.project.clips if clip.id == 'c')
        editor.project.clips.remove(selected)
        for clip in editor.project.clips:
            if clip.start >= selected.end:
                clip.start -= selected.duration
    rejected(editor, lambda: editor._commit(cross_track_ripple), 'edit-locked')

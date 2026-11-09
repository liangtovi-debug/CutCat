"""Persistent clip membership, schema fencing and guarded atomic timeline edits."""
from native_path_fixture import bind_missing_project_paths
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from threading import Thread

import pytest

from lumen.clip_motion import ClipMotionKeyframe
from lumen.model import (Caption, Clip, ClipGroup, Editor, Grade, Media, MediaBin,
                         Overlay, Project, VERSION, VideoTransition, expanded_clip_ids,
                         MAX_CLIP_GROUPS, MAX_CLIP_GROUP_MEMBERS, validate_clip_groups)
from lumen.timing import ClipTimeMap


def sample():
    """Unsorted clips, disjoint tracks, exact fractional source tail and anchors."""
    mapping = ClipTimeMap(Fraction(5, 4), Fraction(83, 4), Fraction(3, 2))
    return Project(
        media=[Media('m', '/missing/source.mov', 'Source', 30, 64, 48, True, True),
               Media('other', '/missing/other.mov', 'Other', 30, 64, 48, True, True)],
        clips=[Clip('b', 'm', 2, 40, 0, 13, time_map=mapping, gain_db=-4),
               Clip('a', 'm', 0, 20, 30, 20, grade=Grade(exposure=.4),
                    motion_keyframes=[ClipMotionKeyframe(0), ClipMotionKeyframe(20, x=7)]),
               Clip('c', 'm', 1, 65, 90, 15),
               Clip('d', 'm', 3, 15, 20, 25),
               Clip('e', 'm', 0, 130, 10, 20)],
        captions=[Caption('caption', 23, 10, 'Independent caption')],
        overlays=[Overlay('sticker', '/missing/sticker.png', 'a'*64, 32, 10)],
        media_bins=[MediaBin('bin', 'Footage')], media_bin_ids={'m': 'bin'},
    )


def grouped():
    editor = Editor(sample())
    editor.group_clips(['a', 'b'])
    return editor


def snapshot(editor):
    return deepcopy((editor.project, editor._undo, editor._redo, editor.revision))


def atomic_failure(editor, action, match=None):
    before = snapshot(editor)
    with pytest.raises(ValueError, match=match):
        action()
    assert snapshot(editor) == before


def legacy(version):
    data = {'format': 'lumen-project', 'version': version}
    if version >= 33:
        data['video_transitions'] = []
    if version >= 34:
        data['master_gain_db'] = 0.0
    if version >= 35:
        data.update(media_bins=[], media_bin_ids={})
    return data


@pytest.mark.parametrize('version', range(1, 36))
def test_every_legacy_schema_migrates_to_empty_groups(version):
    project = Project.from_dict(legacy(version))
    assert project.clip_groups == []
    assert project.to_dict()['version'] == VERSION == 43
    assert project.to_dict()['clip_groups'] == []


@pytest.mark.parametrize('version', range(1, 36))
@pytest.mark.parametrize('value', [[], [{'id': 'g', 'clip_ids': ['a', 'b']}]])
def test_every_legacy_schema_rejects_injected_group_field(version, value):
    with pytest.raises(ValueError, match='Legacy projects cannot contain clip groups'):
        Project.from_dict({**legacy(version), 'clip_groups': value})


def test_current_schema_requires_explicit_group_field():
    data = Project().to_dict()
    del data['clip_groups']
    with pytest.raises(ValueError, match='require a clip groups list'):
        Project.from_dict(data)


@pytest.mark.parametrize('value', [
    None, {}, 'groups', [None], [3], [[]], [{}],
    [{'id': 'g'}], [{'clip_ids': ['a', 'b']}],
    [{'id': 'g', 'clip_ids': ['a', 'b'], 'parent_id': None}],
    [{'id': '', 'clip_ids': ['a', 'b']}],
    [{'id': '  ', 'clip_ids': ['a', 'b']}],
    [{'id': [], 'clip_ids': ['a', 'b']}],
    [{'id': 'bad\0id', 'clip_ids': ['a', 'b']}],
    [{'id': 'g', 'clip_ids': 'ab'}],
    [{'id': 'g', 'clip_ids': {'a': 1, 'b': 2}}],
    [{'id': 'g', 'clip_ids': []}], [{'id': 'g', 'clip_ids': ['a']}],
    [{'id': 'g', 'clip_ids': ['a', 'a']}],
    [{'id': 'g', 'clip_ids': ['a', None]}],
    [{'id': 'g', 'clip_ids': ['a', []]}],
    [{'id': 'g', 'clip_ids': ['a', 'unknown']}],
    [{'id': 'g', 'clip_ids': ['a', 'caption']}],
    [{'id': 'g', 'clip_ids': ['a', 'sticker']}],
    [{'id': 'g', 'clip_ids': ['a', 'b']}, {'id': 'g', 'clip_ids': ['c', 'd']}],
    [{'id': 'g', 'clip_ids': ['a', 'b']}, {'id': 'h', 'clip_ids': ['b', 'c']}],
    [{'id': 'g', 'clip_ids': ['a', 'b']}, {'id': 'h', 'clip_ids': ['g', 'c']}],
])
def test_invalid_serialized_groups_rejected(value):
    data = sample().to_dict()
    data['clip_groups'] = value
    with pytest.raises(ValueError):
        Project.from_dict(data)


@pytest.mark.parametrize('groups', [(), {}, [None], [{'id': 'g', 'clip_ids': ['a', 'b']}],
                                    [ClipGroup('g', ('a', 'b'))],
                                    [ClipGroup('g', ['a', 'b']), ClipGroup('h', ['b', 'c'])]])
def test_invalid_live_group_structures_rejected(groups):
    project = sample()
    project.clip_groups = groups
    with pytest.raises(ValueError):
        project.validate()


def test_group_roundtrip_undo_redo_stable_id_and_no_unrelated_change(tmp_path):
    editor = Editor(sample()); bind_missing_project_paths(editor.project, tmp_path)
    original = deepcopy(editor.project)
    group = editor.group_clips(iter(['a', 'b']))
    assert isinstance(group, ClipGroup) and group.id
    assert group.clip_ids == ['b', 'a']
    expected = deepcopy(original)
    expected.clip_groups = [deepcopy(group)]
    assert editor.project == expected
    assert editor.revision == 1 and len(editor._undo) == 1
    assert Project.from_dict(editor.project.to_dict()) == expected
    path = tmp_path / 'group.lumen'
    editor.project.save(path)
    assert Project.load(path) == expected
    assert editor.undo() and editor.project == original
    assert editor.redo() and editor.project == expected
    assert editor.project.clip_groups[0].id == group.id
    clone = deepcopy(editor)
    clone.ungroup_clips(['a'])
    assert editor.project == expected and clone.project == original


def test_pure_expansion_in_project_order_supports_empty_and_multiple_groups():
    editor = grouped()
    editor.group_clips(['c', 'd'])
    before = snapshot(editor)
    assert expanded_clip_ids(editor.project, ['a']) == ['b', 'a']
    assert editor.expanded_clip_ids(['d', 'a', 'e']) == ['b', 'a', 'c', 'd', 'e']
    assert editor.expanded_clip_ids(['e', 'a', 'b']) == ['b', 'a', 'e']
    assert editor.expanded_clip_ids(iter(['c'])) == ['c', 'd']
    assert editor.expanded_clip_ids([]) == []
    assert snapshot(editor) == before


@pytest.mark.parametrize('ids', ['a', b'a', {'a': 1}, None, 4, ['a', 'a'],
                                ['a', []], ['a', None], ['a', 'unknown'],
                                ['a', 'caption'], ['a', 'sticker']])
def test_selection_parsing_never_mutates_on_invalid_input(ids):
    editor = grouped()
    for operation in (editor.group_clips, editor.ungroup_clips, editor.expanded_clip_ids):
        atomic_failure(editor, lambda: operation(ids))


def test_group_noop_and_unbound_ungroup_keep_redo_revision_and_id():
    editor = grouped()
    group = deepcopy(editor.project.clip_groups[0])
    editor.update_clip('a', gain_db=-3)
    assert editor.undo() and editor.can_redo
    before = snapshot(editor)
    assert editor.group_clips(['a', 'b']) == group
    editor.ungroup_clips(['c'])
    editor.move_clips(['a', 'b'], 0)
    editor.trim_edge('a', 'right', 0)
    editor.slip_clip('a', 30)
    editor.set_clip_speed('a', 1)
    assert snapshot(editor) == before
    assert editor.redo() and editor._clip('a').gain_db == -3


@pytest.mark.parametrize('ids', [[], ['a'], ['a', 'c'], ['a', 'b', 'c'], ['c', 'd', 'a', 'b']])
def test_group_requires_ungrouped_members_and_never_merges(ids):
    editor = grouped()
    editor.group_clips(['c', 'd'])
    atomic_failure(editor, lambda: editor.group_clips(ids))


def test_ungroup_touched_groups_only_is_one_undo_and_preserves_every_clip():
    editor = grouped()
    editor.group_clips(['c', 'd'])
    before = deepcopy(editor.project)
    history = len(editor._undo)
    editor.ungroup_clips(['a', 'e'])
    assert editor.project.clip_groups == before.clip_groups[1:]
    expected = deepcopy(before)
    expected.clip_groups = before.clip_groups[1:]
    assert editor.project == expected and len(editor._undo) == history + 1
    assert editor.undo() and editor.project == before
    editor.ungroup_clips(['a', 'd'])
    expected.clip_groups = []
    assert editor.project == expected
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == expected


def test_whole_group_translation_is_one_exact_rigid_edit():
    editor = grouped()
    original = deepcopy(editor.project)
    history = len(editor._undo)
    preview = editor.preview_move_clips(['a', 'b'], 17)
    assert editor.project == original
    moved = editor.move_clips(['a', 'b'], 17)
    assert moved == preview and [clip.id for clip in moved] == ['b', 'a']
    expected = deepcopy(original)
    expected.clips = [replace(clip, start=clip.start+17) if clip.id in ('a', 'b') else clip
                      for clip in expected.clips]
    assert editor.project == expected and len(editor._undo) == history + 1
    assert editor.undo() and editor.project == original
    assert editor.redo() and editor.project == expected
    editor.move_clips(['a', 'b', 'c'], -10)
    assert editor._clip('a').start == 27 and editor._clip('c').start == 55


@pytest.mark.parametrize('ids,delta', [(['a'], 0), (['a'], 1), (['b'], -1),
                                      (['a', 'b'], -21), (['a', 'b'], 100),
                                      (['a', 'b'], True), (['a', 'b'], 1.5)])
def test_partial_and_invalid_group_moves_are_atomic_in_preview_and_commit(ids, delta):
    editor = grouped()
    for operation in (editor.preview_move_clips, editor.move_clips):
        atomic_failure(editor, lambda: operation(ids, delta))


def test_delete_whole_group_and_unbound_selection_removes_only_complete_record():
    editor = grouped()
    editor.group_clips(['c', 'd'])
    original = deepcopy(editor.project)
    history = len(editor._undo)
    editor.delete_clips(['a', 'b', 'e'])
    expected = deepcopy(original)
    expected.clips = [clip for clip in original.clips if clip.id in ('c', 'd')]
    expected.clip_groups = original.clip_groups[1:]
    assert editor.project == expected and len(editor._undo) == history + 1
    assert editor.undo() and editor.project == original
    assert editor.redo() and editor.project == expected
    editor.delete_clips(['c', 'd'])
    assert editor.project.clips == [] and editor.project.clip_groups == []
    assert editor.project.media == original.media
    assert editor.project.captions == original.captions and editor.project.overlays == original.overlays


@pytest.mark.parametrize('action', [
    lambda e: e.move_clip('a', 21), lambda e: e.move_clip('a', 20, 1),
    lambda e: e.delete_clip('a'), lambda e: e.delete_clips(['a']),
    lambda e: e.delete_clips(['a', 'c', 'd']),
    lambda e: e.split_clip('a', 30), lambda e: e.trim_clip('a', 31, 19),
    lambda e: e.trim_clip_source('a', 31, 50), lambda e: e.trim_clip_left('a', 1),
    lambda e: e.preview_trim_edge('a', 'left', 1), lambda e: e.trim_edge('a', 'right', 1),
    lambda e: e.slip_clip('a', 31), lambda e: e.set_clip_speed('a', 2),
    lambda e: e.update_clip('a', media_id='other'),
    lambda e: e.update_clip('a', source_in=31),
    lambda e: e.update_clip('a', duration=19), lambda e: e.update_clip('a', track=1),
    lambda e: e.ripple_delete_clip('a'),
])
def test_individual_destructive_operations_require_ungroup_and_preserve_redo(action):
    editor = grouped()
    editor.update_clip('a', gain_db=-3)
    editor.undo()
    atomic_failure(editor, lambda: action(editor), 'Ungroup')


def test_grouped_roll_checks_both_endpoints_and_allows_zero_delta():
    project = sample()
    project.clips[2] = Clip('c', 'm', 0, 40, 90, 15)
    editor = Editor(project)
    editor.group_clips(['c', 'd'])  # The requested left clip is ungrouped.
    original = snapshot(editor)
    editor.preview_roll_cut('a', 40)
    editor.roll_cut('a', 40)
    assert snapshot(editor) == original
    for operation in (editor.preview_roll_cut, editor.roll_cut):
        atomic_failure(editor, lambda: operation('a', 41), 'Ungroup')
    editor.ungroup_clips(['c'])
    editor.roll_cut('a', 41)
    assert editor._clip('a').end == 41 and editor._clip('c').start == 41


@pytest.mark.parametrize('group_ids', [('a', 'c'), ('b', 'c')])
def test_ripple_rejects_grouped_downstream_even_if_every_member_shifts(group_ids):
    project = sample()
    project.clips = [Clip('remove', 'm', 0, 0, 0, 10),
                     Clip('a', 'm', 0, 20, 0, 10),
                     Clip('b', 'm', 1, 20, 0, 10),
                     Clip('c', 'm', 0, 40, 0, 10)]
    editor = Editor(project)
    editor.group_clips(group_ids)
    atomic_failure(editor, lambda: editor.ripple_delete_clip('remove'), 'Ungroup')


def test_ripple_ignores_groups_that_are_not_removed_or_shifted():
    editor = grouped()
    before = deepcopy(editor.project)
    editor.ripple_delete_clip('e')
    before.clips = [clip for clip in before.clips if clip.id != 'e']
    assert editor.project == before


def mutate(editor, kind):
    project = editor.project
    if kind == 'remove-part':
        project.clips.remove(editor._clip('a'))
    elif kind == 'remove-all-leave-record':
        project.clips = [clip for clip in project.clips if clip.id not in ('a', 'b')]
    elif kind == 'remove-record':
        project.clip_groups.clear()
    elif kind == 'replace-member':
        project.clip_groups[0].clip_ids = ['a', 'c']
    elif kind == 'reorder-members':
        project.clip_groups[0].clip_ids.reverse()
    elif kind == 'new-group':
        project.clip_groups.append(ClipGroup('new', ['c', 'd']))
    elif kind == 'new-id':
        project.clip_groups[0].id = 'new'
    elif kind == 'unequal-shifts':
        editor._clip('a').start += 2
        editor._clip('b').start += 1
    elif kind == 'vertical':
        editor._clip('a').track = 1
        editor._clip('b').track = 3
    elif kind == 'source':
        for clip in project.clips:
            if clip.id in ('a', 'b'):
                clip.media_id = 'other'
    elif kind == 'timing':
        editor._set_clip_timing(editor._clip('a'), ClipTimeMap(31, 51))
        editor._set_clip_timing(editor._clip('b'), ClipTimeMap(2, 15))
    elif kind == 'membership-with-unrelated-state':
        project.clip_groups.clear()
        project.name = 'Sneaky edit'


@pytest.mark.parametrize('kind', ['remove-part', 'remove-all-leave-record', 'remove-record',
                                 'replace-member', 'reorder-members', 'new-group', 'new-id',
                                 'unequal-shifts', 'vertical', 'source', 'timing',
                                 'membership-with-unrelated-state'])
def test_generic_commit_cannot_bypass_group_invariants(kind):
    editor = grouped()
    atomic_failure(editor, lambda: editor._commit(lambda: mutate(editor, kind)))


def test_generic_commit_allows_rigid_move_and_complete_deletion_only_with_record():
    editor = grouped()
    original = deepcopy(editor.project)
    def move():
        for clip in editor.project.clips:
            if clip.id in ('a', 'b'):
                clip.start += 3
    editor._commit(move)
    assert editor._clip('a').start == 23 and editor._clip('b').start == 43
    def delete():
        editor.project.clips = [clip for clip in editor.project.clips if clip.id not in ('a', 'b')]
        editor.project.clip_groups.clear()
    editor._commit(delete)
    assert editor.project.clip_groups == [] and len(editor.project.clips) == 3
    assert editor.undo() and editor.undo() and editor.project == original


def test_membership_transaction_cannot_modify_unrelated_state():
    editor = grouped()
    def contaminated_change():
        editor.project.clip_groups.clear()
        editor.project.clips[0].gain_db = -10
    atomic_failure(editor, lambda: editor._Editor__commit(contaminated_change, group_membership=[]),
                   'may change only')
    before = snapshot(editor)
    with pytest.raises(TypeError):
        editor._commit(lambda: None, group_membership=[])
    assert snapshot(editor) == before


def test_generic_commit_cannot_nest_membership_edit_and_leave_history_after_rollback():
    editor = grouped()
    atomic_failure(editor, lambda: editor._commit(lambda: editor.ungroup_clips(['a'])), 'Nested')
    editor.ungroup_clips(['a'])  # The failure does not poison the next transaction.
    assert not editor.project.clip_groups


def test_grade_gain_and_media_id_preserving_relink_remain_independent():
    editor = grouped()
    group = deepcopy(editor.project.clip_groups)
    editor.update_clip('a', grade=Grade(exposure=.8), gain_db=-8)
    editor.update_clip('b', muted=True)
    editor.set_master_gain(-3)
    editor._commit(lambda: setattr(editor.project.media[0], 'path', '/new-location/source.mov'))
    assert editor.project.clip_groups == group
    assert editor._clip('a').grade.exposure == .8 and editor._clip('a').gain_db == -8
    assert editor._clip('b').muted and editor.project.master_gain_db == -3


def transition_editor():
    project = Project(media=sample().media, clips=[
        Clip('a', 'm', 0, 20, 30, 40), Clip('b', 'm', 0, 60, 100, 40),
        Clip('c', 'm', 2, 10, 0, 10)],
        video_transitions=[VideoTransition('transition', 'a', 'b', 10)])
    return Editor(project)


def test_transitions_and_groups_keep_separate_relational_validation_without_cascade():
    editor = transition_editor()
    editor.group_clips(['a', 'c'])
    atomic_failure(editor, lambda: editor.move_clips(['a', 'c'], 2))
    atomic_failure(editor, lambda: editor.delete_clips(['a', 'c']))
    assert editor.project.video_transitions[0].id == 'transition'
    editor.ungroup_clips(['c'])
    editor.group_clips(['a', 'b'])
    before = deepcopy(editor.project)
    editor.move_clips(['a', 'b'], 2)
    assert editor.project.video_transitions == before.video_transitions
    atomic_failure(editor, lambda: editor.delete_clips(['a', 'b']))
    editor.delete_video_transition('transition')
    editor.delete_clips(['a', 'b'])
    assert editor.project.clip_groups == [] and [c.id for c in editor.project.clips] == ['c']


def test_group_mutations_require_owner_thread():
    editor = grouped()
    errors = []
    before = snapshot(editor)
    def run():
        for operation in (lambda: editor.group_clips(['c', 'd']),
                          lambda: editor.ungroup_clips(['a'])):
            try:
                operation()
            except ValueError as error:
                errors.append(str(error))
    worker = Thread(target=run)
    worker.start(); worker.join()
    assert len(errors) == 2 and all('owning thread' in error for error in errors)
    assert snapshot(editor) == before


def test_group_and_member_count_boundaries_are_validated_without_recursion():
    ids = [str(index) for index in range(max(2 * MAX_CLIP_GROUPS, MAX_CLIP_GROUP_MEMBERS) + 1)]
    groups = [ClipGroup(f'group-{index}', ids[2*index:2*index+2])
              for index in range(MAX_CLIP_GROUPS)]
    validate_clip_groups(groups, set(ids))
    with pytest.raises(ValueError, match='at most 10000'):
        validate_clip_groups([*groups, ClipGroup('overflow', ['unused-a', 'unused-b'])], set(ids))
    validate_clip_groups([ClipGroup('g', ids[:MAX_CLIP_GROUP_MEMBERS])], set(ids))
    with pytest.raises(ValueError, match='2..20000'):
        validate_clip_groups([ClipGroup('g', ids[:MAX_CLIP_GROUP_MEMBERS+1])], set(ids))


@pytest.mark.parametrize('kind', ['group-count', 'member-count'])
def test_schema_bounds_are_rejected_before_group_objects_are_constructed(kind, monkeypatch):
    data = Project().to_dict()
    if kind == 'group-count':
        data['clip_groups'] = [{'id': 'g', 'clip_ids': ['a', 'b']}] * (MAX_CLIP_GROUPS+1)
    else:
        data['clip_groups'] = [{'id': 'g', 'clip_ids': ['a'] * (MAX_CLIP_GROUP_MEMBERS+1)}]
    def forbidden_constructor(**kwargs):
        pytest.fail('Bounded payload must be rejected before constructing groups')
    monkeypatch.setattr('lumen.model.ClipGroup', forbidden_constructor)
    with pytest.raises(ValueError):
        Project.from_dict(data)


@pytest.mark.parametrize('kind', ['replace-project', 'undo', 'redo'])
def test_nested_project_and_history_changes_cannot_escape_group_transaction_rollback(kind):
    editor = grouped()
    editor.update_clip('a', gain_db=-9)
    assert editor.undo() and editor.can_redo and editor.can_undo
    session = editor.session_id
    def change():
        # There is already a pending live mutation to roll back, not just a
        # forbidden no-op callback. Neither history nor session may leak.
        editor._clip('a').start += 1
        if kind == 'replace-project':
            editor.project = Project()
        elif kind == 'undo':
            editor.undo()
        else:
            editor.redo()
    atomic_failure(editor, lambda: editor._commit(change), 'Nested')
    assert editor.session_id == session
    assert editor.redo() and editor._clip('a').gain_db == -9
    assert editor.undo() and editor._clip('a').gain_db == 0
    editor.project = Project()
    assert editor.session_id != session and editor.project == Project()
    assert not editor.can_undo and not editor.can_redo

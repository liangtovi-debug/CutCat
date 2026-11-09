"""Nondestructive media organization, schema fencing and atomic history."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from lumen.model import (Editor, Media, MediaBin, Project, VERSION,
                         MAX_MEDIA_BINS, MAX_MEDIA_BIN_DEPTH)


def media(identifier='a', path=None):
    return Media(identifier, path or f'/unavailable/{identifier}.mp4', identifier, 2,
                 has_video=True, has_audio=True)


def project():
    return Project(media=[media('a'), media('b'), media('c')])


def legacy(version):
    data = {'format': 'lumen-project', 'version': version}
    if version >= 33:
        data['video_transitions'] = []
    if version >= 34:
        data['master_gain_db'] = 0.0
    return data


@pytest.mark.parametrize('version', range(1, 35))
def test_all_legacy_versions_migrate_without_organization(version):
    value = Project.from_dict(legacy(version))
    assert value.media_bins == [] and value.media_bin_ids == {}
    assert value.to_dict()['version'] == VERSION == 43


@pytest.mark.parametrize('version', range(1, 35))
@pytest.mark.parametrize('key,value', [('media_bins', []), ('media_bin_ids', {})])
def test_all_legacy_versions_reject_even_empty_injected_fields(version, key, value):
    data = legacy(version)
    data[key] = value
    with pytest.raises(ValueError):
        Project.from_dict(data)


@pytest.mark.parametrize('field', ['media_bins', 'media_bin_ids'])
def test_current_schema_requires_both_fields(field):
    data = Project().to_dict()
    del data[field]
    with pytest.raises(ValueError):
        Project.from_dict(data)


@pytest.mark.parametrize('bins,membership', [
    ({}, {}), ([None], {}), ([{'id':'x','name':'X'}], {}),
    ([{'id':'x','name':'X','parent_id':None,'extra':1}], {}),
    ([{'id':'x','name':'X','parent_id':None}]*2, {}),
    ([{'id':'x','name':'','parent_id':None}], {}),
    ([{'id':'x','name':' '*3,'parent_id':None}], {}),
    ([{'id':'x','name':'x'*129,'parent_id':None}], {}),
    ([{'id':'x','name':'X','parent_id':'missing'}], {}),
    ([{'id':'x','name':'X','parent_id':'x'}], {}),
    ([], []), ([], {'a': None}), ([], {'a': 'missing'}),
    ([{'id':'x','name':'X','parent_id':None}], {'missing': 'x'}),
    ([{'id':'x','name':'X','parent_id':None}], {'a': ''}),
    ([{'id':[], 'name':'X','parent_id':None}], {}),
    ([{'id':'x','name':'X','parent_id':[]}], {}),
])
def test_malformed_serialized_organization_rejected(bins, membership):
    data = project().to_dict()
    data.update(media_bins=bins, media_bin_ids=membership)
    with pytest.raises(ValueError):
        Project.from_dict(data)


def test_roundtrip_and_unicode_names_without_media_mutation():
    value = project()
    before = deepcopy(value.media)
    value.media_bins = [MediaBin('x', '镜头 / A'), MediaBin('y', '🎬'*128, 'x')]
    value.media_bin_ids = {'a': 'y'}
    assert Project.from_dict(value.to_dict()) == value
    assert value.media == before
    assert 'bin_id' not in value.to_dict()['media'][0]


def test_maximum_bins_and_depth_are_bounded_without_recursion():
    value = Project(media_bins=[MediaBin(str(i), str(i)) for i in range(MAX_MEDIA_BINS)])
    value.validate()
    value.media_bins.append(MediaBin('overflow', 'overflow'))
    with pytest.raises(ValueError):
        value.validate()
    value = Project(media_bins=[MediaBin(str(i), str(i), str(i-1) if i else None)
                               for i in range(MAX_MEDIA_BIN_DEPTH)])
    value.validate()
    value.media_bins.append(MediaBin('too-deep', 'deep', str(MAX_MEDIA_BIN_DEPTH-1)))
    with pytest.raises(ValueError):
        value.validate()


def test_indirect_cycle_rejected():
    value = Project(media_bins=[MediaBin('a','A','b'), MediaBin('b','B','c'), MediaBin('c','C','a')])
    with pytest.raises(ValueError):
        value.validate()


def assert_atomic_failure(editor, action):
    before, undo, redo, revision = deepcopy(editor.project), deepcopy(editor._undo), deepcopy(editor._redo), editor.revision
    with pytest.raises(ValueError):
        action()
    assert editor.project == before
    assert editor._undo == undo and editor._redo == redo and editor.revision == revision


def test_crud_reparent_move_delete_and_single_step_history():
    editor = Editor(project())
    original = deepcopy(editor.project)
    folder = editor.create_media_bin('Footage')
    child = editor.create_media_bin('B', folder.id)
    editor.rename_media_bin(child.id, 'Renamed')
    editor.move_media_to_bin(['a','c','a'], child.id)
    assert editor.project.media_bin_ids == {'a': child.id, 'c': child.id}
    assert len(editor._undo) == 4
    assert_atomic_failure(editor, lambda: editor.delete_media_bin(child.id))
    assert_atomic_failure(editor, lambda: editor.delete_media_bin(folder.id))
    assert_atomic_failure(editor, lambda: editor.reparent_media_bin(folder.id, child.id))
    editor.reparent_media_bin(child.id)
    editor.move_media_to_bin(['a','c'])
    editor.delete_media_bin(child.id)
    editor.delete_media_bin(folder.id)
    for _ in range(8):
        assert editor.undo()
    assert editor.project == original
    for _ in range(8):
        assert editor.redo()
    assert editor.project == original


def test_reparent_checks_descendant_depth_and_preserves_state():
    editor = Editor(Project(media_bins=[MediaBin(str(i), str(i), str(i-1) if i else None)
                                       for i in range(16)] + [MediaBin('extra','Extra')]))
    assert_atomic_failure(editor, lambda: editor.reparent_media_bin('0', 'extra'))
    assert_atomic_failure(editor, lambda: editor.reparent_media_bin('extra', '15'))


def test_noops_keep_redo_and_revision():
    editor = Editor(project())
    item = editor.create_media_bin('A')
    editor.rename_media_bin(item.id, 'B')
    editor.undo()
    revision = editor.revision
    editor.rename_media_bin(item.id, 'A')
    editor.reparent_media_bin(item.id)
    editor.move_media_to_bin(['a'], None)
    editor.move_media_to_bin([], item.id)
    editor.remove_unused_media([])
    assert editor.import_media_batch([media('duplicate', media('a').path)], item.id) == []
    assert editor.import_media_batch([]) == []
    assert editor.revision == revision and editor.can_redo
    assert editor.redo()
    assert editor.project.media_bins[0].name == 'B'


def test_batch_import_deduplicates_current_and_batch_paths_without_moving_existing():
    editor = Editor(project())
    folder = editor.create_media_bin('Imports')
    before = deepcopy(editor.project)
    new = media('d')
    added = editor.import_media_batch([media('dup-a', media('a').path), new,
                                       media('dup-d', new.path), media('e')], folder.id)
    assert [item.id for item in added] == ['d','e']
    assert [item.id for item in editor.project.media] == ['a','b','c','d','e']
    assert editor.project.media_bin_ids == {'d':folder.id,'e':folder.id}
    new.name = 'caller mutated'
    assert editor.project.media[-2].name == 'd'
    editor.undo()
    assert editor.project == before
    editor.redo()
    assert [item.id for item in editor.project.media] == ['a','b','c','d','e']


def test_import_is_atomic_on_bad_later_metadata_and_identity_conflicts():
    editor = Editor(project())
    assert_atomic_failure(editor, lambda: editor.import_media_batch([media('d'), media('a', '/new.mp4')]))
    bad = media('e')
    bad.duration = -1
    assert_atomic_failure(editor, lambda: editor.import_media_batch([media('d'), bad]))
    assert_atomic_failure(editor, lambda: editor.import_media_batch([media('d'), None]))
    assert_atomic_failure(editor, lambda: editor.import_media_batch([], 'missing'))


def test_remove_unused_is_atomic_used_muted_and_keeps_order_memberships():
    editor = Editor(project())
    folder = editor.create_media_bin('Bin')
    editor.move_media_to_bin(['a','b'], folder.id)
    clip = editor.add_clip('b', 0)
    editor.update_clip(clip.id, muted=True)
    assert_atomic_failure(editor, lambda: editor.remove_unused_media(['a','b']))
    assert_atomic_failure(editor, lambda: editor.remove_unused_media(['a','missing']))
    before = deepcopy(editor.project)
    editor.remove_unused_media(['c','a'])
    assert [item.id for item in editor.project.media] == ['b']
    assert editor.project.media_bin_ids == {'b': folder.id}
    editor.undo()
    assert editor.project == before
    assert [item.id for item in editor.project.media] == ['a','b','c']


def test_bad_target_and_unknown_media_never_partially_move():
    editor = Editor(project())
    item = editor.create_media_bin('A')
    assert_atomic_failure(editor, lambda: editor.move_media_to_bin(['a','unknown'], item.id))
    assert_atomic_failure(editor, lambda: editor.move_media_to_bin(['a'], 'unknown'))
    assert_atomic_failure(editor, lambda: editor.move_media_to_bin('a', item.id))
    assert_atomic_failure(editor, lambda: editor.rename_media_bin(item.id, '\x00'))
    assert_atomic_failure(editor, lambda: editor.create_media_bin('A', 'unknown'))


def test_organization_and_import_never_touch_source_files(tmp_path):
    source = tmp_path/'source.mp4'
    source.write_bytes(b'untouched source')
    editor = Editor()
    with patch.object(Path, 'resolve', side_effect=AssertionError('filesystem resolve')), \
         patch.object(Path, 'is_file', side_effect=AssertionError('filesystem stat')), \
         patch.object(Path, 'unlink', side_effect=AssertionError('filesystem deletion')):
        folder = editor.create_media_bin('A')
        editor.import_media_batch([media('a', str(source))], folder.id)
        editor.move_media_to_bin(['a'])
        editor.remove_unused_media(['a'])
        editor.delete_media_bin(folder.id)
    assert source.read_bytes() == b'untouched source'


def test_import_deduplicates_platform_path_case_without_filesystem_access():
    editor = Editor(Project(media=[media('a', '/Media/A.MP4')]))
    with patch('lumen.model.os.path.normcase', side_effect=str.lower):
        added = editor.import_media_batch([media('dup', '/media/a.mp4'),
                                          media('b', '/Media/B.MP4'),
                                          media('dup-b', '/media/b.mp4')])
    assert [item.id for item in added] == ['b']
    assert [item.id for item in editor.project.media] == ['a', 'b']


@pytest.mark.parametrize('bad', [None, 2, True, 'a', b'a'])
def test_invalid_batch_containers_rejected_atomically(bad):
    editor = Editor(project())
    assert_atomic_failure(editor, lambda: editor.move_media_to_bin(bad))
    assert_atomic_failure(editor, lambda: editor.remove_unused_media(bad))
    assert_atomic_failure(editor, lambda: editor.import_media_batch(bad))

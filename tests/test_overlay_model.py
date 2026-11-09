"""Schema, safety, and transactional editing contracts for local visual assets."""
from contrast_legacy_fixture import strip_contrast_pivot_field
from copy import deepcopy
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import tempfile

import pytest

from lumen.model import (
    Caption, Clip, ClipEffect, Editor, MAX_CAPTION_FRAME, Media, Overlay,
    OverlayKeyframe, Project, VERSION,
)


def sticker(**changes):
    path = (Path(tempfile.gettempdir()) / 'sticker.png').resolve()
    return Overlay(**(dict(id='sticker', asset_path=str(path),
                           asset_sha256='a' * 64, start=15, duration=30) | changes))


def video_project(**changes):
    path = (Path(tempfile.gettempdir()) / 'video.mp4').resolve()
    data = dict(media=[Media('video', str(path), 'Video', 2,
                            has_audio=True)],
                clips=[Clip('clip', 'video', 0, 0, 0, 60)])
    return Project(**(data | changes))


def test_identity_defaults_are_independent():
    one, two = sticker(), sticker(id='two')
    assert one.keyframes == [OverlayKeyframe(0, 0, 0, 1, 1)]
    one.keyframes[0].x = 99
    assert two.keyframes[0].x == 0
    first = Clip('first', 'video', 0, 0, 0, 30)
    second = Clip('second', 'video', 0, 30, 0, 30)
    first.effects.append(ClipEffect('blur', 'blur', 2))
    assert second.effects == []
    Project(overlays=[one, two]).validate()  # Overlay overlap is intentional.


def test_version_five_roundtrip_and_relative_paths(tmp_path):
    tmp_path = tmp_path.resolve()
    assets = tmp_path / 'assets'
    assets.mkdir()
    png = assets / '世界.png'
    png.write_bytes(b'Original PNG placeholder; schema does not decode images.')
    item = sticker(asset_path=str(png), keyframes=[OverlayKeyframe(),
                    OverlayKeyframe(29, 120.5, -20, 2, 0)], z_order=-3)
    project = video_project(overlays=[item])
    project.clips[0].effects = [ClipEffect('blur', 'blur', 2.5),
                               ClipEffect('vignette', 'vignette', .25, False)]
    original = deepcopy(project)
    path = tmp_path / 'project.lumen'
    project.save(path)
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['version'] == VERSION
    assert data['overlays'][0]['asset_path'] == 'assets/世界.png'
    assert project == original
    restored = Project.load(path)
    assert restored == original
    assert isinstance(restored.overlays[0], Overlay)
    assert isinstance(restored.overlays[0].keyframes[0], OverlayKeyframe)
    assert isinstance(restored.clips[0].effects[0], ClipEffect)
    assert restored.to_dict() == original.to_dict()


@pytest.mark.parametrize('version', [1, 2, 3, 4])
def test_migrate_legacy_versions_without_visual_features(version):
    data = video_project().to_dict()
    data['version'] = version; strip_contrast_pivot_field(data)
    if data['version'] < 17:
        for old_clip in data.get('clips', []):
            old_clip.get('grade', {}).pop('hue_hue_curve', None)
            old_clip.get('grade', {}).pop('hue_sat_curve', None)
            old_clip.get('grade', {}).pop('qualifier', None)
    if data['version'] < 16:
        for old_clip in data.get('clips', []): old_clip.pop('transform', None)
    del data['overlays']
    for clip in data['clips']:
        clip.pop('restoration')
        clip.pop('stabilization')
        mapping = clip.pop('time_map')
        clip['source_in'] = mapping['source_in_num']
        del clip['effects']
    restored = Project.from_dict(data)
    assert restored.overlays == []
    assert restored.clips[0].effects == []
    assert restored.to_dict()['version'] == VERSION


def test_overlay_defaults_migrate_and_input_is_not_mutated():
    data = Project(overlays=[sticker()]).to_dict()
    for name in ['z_order', 'enabled', 'keyframes']:
        del data['overlays'][0][name]
    before = deepcopy(data)
    restored = Project.from_dict(data)
    assert data == before
    assert restored.overlays == [sticker()]
    restored.overlays[0].keyframes[0].x = 100
    assert Project.from_dict(data).overlays == [sticker()]


def test_all_timeline_items_preserve_duration_when_bypassed():
    project = Project(overlays=[sticker(start=70, duration=20, enabled=False)])
    assert project.duration_frames == 90
    project.overlays[0].enabled = True
    assert project.duration_frames == 90
    project.captions = [Caption('caption', 100, 10, 'Still independent')]
    assert project.duration_frames == 110
    assert Project().duration_frames == 0


@pytest.mark.parametrize('changes', [
    {'id': ''}, {'id': None}, {'asset_path': ''}, {'asset_path': None},
    {'asset_path': 'https://example.com/image.png'}, {'asset_path': 'file:///tmp/a.png'},
    {'asset_path': 'data:image/png;base64,abc'}, {'asset_path': '//host/share/a.png'},
    {'asset_path': r'\\host\share\a.png'}, {'asset_path': 'a.jpg'},
    {'asset_path': 'a.png\x00'}, {'asset_path': 'a\nb.png'}, {'asset_path': '\ud800.png'},
    {'asset_sha256': 'a' * 63}, {'asset_sha256': 'x' * 64}, {'asset_sha256': None},
    {'start': -1}, {'start': 1.5}, {'start': True}, {'start': MAX_CAPTION_FRAME},
    {'duration': 0}, {'duration': False}, {'duration': math.inf},
    {'duration': MAX_CAPTION_FRAME}, {'z_order': False}, {'z_order': 1.5},
    {'z_order': 2**31}, {'enabled': 1}, {'enabled': None},
    {'keyframes': []}, {'keyframes': {}}, {'keyframes': [None]},
    {'keyframes': [OverlayKeyframe(0), OverlayKeyframe(0)]},
    {'keyframes': [OverlayKeyframe(5), OverlayKeyframe(1)]},
    {'keyframes': [OverlayKeyframe(30)]}, {'keyframes': [OverlayKeyframe(-1)]},
    {'keyframes': [OverlayKeyframe(True)]}, {'keyframes': [OverlayKeyframe(1.0)]},
])
def test_strict_overlay_validation(changes):
    with pytest.raises(ValueError):
        Project(overlays=[sticker(**changes)]).validate()


@pytest.mark.parametrize('name,bad_values', [
    ('x', [-16385, 16385, math.nan, math.inf, -math.inf, True, None, '0']),
    ('y', [-16385, 16385, math.nan, math.inf, -math.inf, True, None, '0']),
    ('scale', [0, .009, 16.001, math.nan, math.inf, True, None, '1']),
    ('opacity', [-.001, 1.001, math.nan, math.inf, True, None, '1']),
])
def test_strict_keyframe_numbers(name, bad_values):
    for value in bad_values:
        with pytest.raises(ValueError):
            Project(overlays=[sticker(keyframes=[replace(OverlayKeyframe(), **{name: value})])]).validate()


def test_overlay_boundary_values_and_missing_local_assets_are_valid():
    Project(overlays=[sticker(start=0, duration=MAX_CAPTION_FRAME,
        z_order=-(2**31), keyframes=[OverlayKeyframe(10, -16384, 16384, .01, 0),
                                  OverlayKeyframe(MAX_CAPTION_FRAME - 1, 16384, -16384, 16, 1)])]).validate()
    for path in ['assets/local.PNG', 'assets/local.json', r'C:\assets\local.png']:
        Project(overlays=[sticker(asset_path=path, asset_sha256='A' * 64)]).validate()


def test_collection_limits_and_duplicate_ids():
    project = Project(overlays=[sticker(id=str(i)) for i in range(256)])
    project.validate()
    project.overlays.append(sticker(id='extra'))
    with pytest.raises(ValueError, match='256'):
        project.validate()
    project.overlays = [sticker(), sticker()]
    with pytest.raises(ValueError, match='Duplicate'):
        project.validate()
    project.overlays = [sticker(duration=200, keyframes=[OverlayKeyframe(i) for i in range(128)])]
    project.validate()
    project.overlays[0].keyframes.append(OverlayKeyframe(128))
    with pytest.raises(ValueError, match='128'):
        project.validate()


@pytest.mark.parametrize('changes', [
    {'id': ''}, {'id': []}, {'kind': 'sharpen'}, {'kind': []},
    {'kind': None}, {'amount': -1}, {'amount': 30.01}, {'amount': math.nan},
    {'amount': math.inf}, {'amount': True}, {'amount': '1'}, {'amount': None},
    {'enabled': 0}, {'enabled': None}, {'kind': 'vignette', 'amount': 1.01},
])
def test_strict_effect_validation(changes):
    project = video_project()
    project.clips[0].effects = [ClipEffect(**(dict(id='effect', kind='blur', amount=3) | changes))]
    with pytest.raises(ValueError):
        project.validate()


def test_effect_bounds_order_limits_and_video_tracks():
    project = video_project()
    project.clips[0].effects = [ClipEffect('a', 'blur', 0), ClipEffect('b', 'blur', 30),
                               ClipEffect('c', 'vignette', 0), ClipEffect('d', 'vignette', 1)]
    project.validate()
    assert Project.from_dict(project.to_dict()).clips[0].effects == project.clips[0].effects
    project.clips[0].effects = [ClipEffect(str(i), 'blur', 1) for i in range(16)]
    project.validate()
    project.clips[0].effects.append(ClipEffect('extra', 'blur', 1))
    with pytest.raises(ValueError, match='16'):
        project.validate()
    project.clips[0].effects = [ClipEffect('same', 'blur', 1), ClipEffect('same', 'vignette', 1)]
    with pytest.raises(ValueError, match='Duplicate'):
        project.validate()
    project.clips[0].effects = [ClipEffect('effect', 'blur', 1, False)]
    project.clips[0].track = 2
    with pytest.raises(ValueError, match='video tracks'):
        project.validate()


@pytest.mark.parametrize('field,value', [
    ('overlays', {}), ('overlays', [None]), ('overlays', [[]]), ('overlays', ['bad']),
    ('effects', {}), ('effects', [None]), ('effects', [[]]), ('effects', ['bad']),
    ('keyframes', {}), ('keyframes', [None]), ('keyframes', [[]]), ('keyframes', ['bad']),
    ('keyframes', [{'unknown': 0}]),
])
def test_malformed_nested_json_is_rejected(field, value):
    data = video_project(overlays=[sticker()]).to_dict()
    if field == 'overlays':
        data[field] = value
    elif field == 'effects':
        data['clips'][0][field] = value
    else:
        data['overlays'][0][field] = value
    with pytest.raises(ValueError):
        Project.from_dict(data)


def test_overlay_edits_are_atomic_copied_and_undoable():
    ed = Editor(video_project(captions=[Caption('caption', 0, 20, 'Caption')]))
    original = deepcopy(ed.project)
    input_item = sticker()
    added = ed.add_overlay(input_item)
    assert added is not input_item
    input_item.keyframes[0].x = 20
    assert added.keyframes[0].x == 0
    keys = [OverlayKeyframe(0, 30), OverlayKeyframe(29, 60)]
    ed.update_overlay('sticker', start=100, keyframes=keys, enabled=False)
    keys[0].x = 900
    assert ed.project.overlays[0].keyframes[0].x == 30
    before_invalid = deepcopy(ed.project)
    for changes in [{'duration': 29}, {'keyframes': []}, {'start': -1}, {'id': 'new'}, {'unknown': 1}]:
        with pytest.raises(ValueError):
            ed.update_overlay('sticker', **changes)
        assert ed.project == before_invalid
    with pytest.raises(ValueError):
        ed.add_overlay(sticker())
    assert ed.project == before_invalid
    assert ed.project.clips == original.clips and ed.project.captions == original.captions
    ed.delete_overlay('sticker')
    assert not ed.project.overlays
    assert ed.undo() and ed.project == before_invalid
    assert ed.undo() and ed.project.overlays == [sticker()]
    assert ed.undo() and ed.project == original and not ed.can_undo
    assert ed.redo() and ed.project.overlays == [sticker()]
    assert ed.redo() and ed.project == before_invalid
    assert ed.redo() and not ed.project.overlays and not ed.can_redo


def test_effect_edits_reorder_and_undo_without_aliasing():
    ed = Editor(video_project())
    original = deepcopy(ed.project)
    input_effect = ClipEffect('blur', 'blur', 2)
    assert ed.add_effect('clip', input_effect) is not input_effect
    input_effect.amount = 30
    assert ed.project.clips[0].effects[0].amount == 2
    ed.add_effect('clip', ClipEffect('vignette', 'vignette', .5))
    ed.update_effect('clip', 'blur', amount=5, enabled=False)
    ed.move_effect('clip', 'blur', 1)
    assert [effect.id for effect in ed.project.clips[0].effects] == ['vignette', 'blur']
    before_invalid = deepcopy(ed.project)
    for changes in [{'amount': 31}, {'id': 'new'}, {'kind': 'unknown'}, {'unknown': 1}]:
        with pytest.raises(ValueError):
            ed.update_effect('clip', 'blur', **changes)
        assert ed.project == before_invalid
    for index in [-1, 2, True, 1.0]:
        with pytest.raises(ValueError):
            ed.move_effect('clip', 'blur', index)
        assert ed.project == before_invalid
    with pytest.raises(ValueError):
        ed.move_clip('clip', 0, track=2)
    assert ed.project == before_invalid
    ed.delete_effect('clip', 'vignette')
    assert [effect.id for effect in ed.project.clips[0].effects] == ['blur']
    for _ in range(5):
        assert ed.undo()
    assert ed.project == original and not ed.can_undo
    for _ in range(5):
        assert ed.redo()
    assert ed.project.clips[0].effects == [ClipEffect('blur', 'blur', 5, False)]


def test_noops_unknown_ids_and_failed_edits_preserve_history():
    ed = Editor(video_project(overlays=[sticker()]))
    ed.add_effect('clip', ClipEffect('blur', 'blur', 1))
    ed.undo()
    before = deepcopy(ed.project)
    ed.update_overlay('sticker', enabled=True)
    assert ed.can_redo and not ed.can_undo
    for action in [lambda: ed.update_overlay('missing', enabled=False),
                   lambda: ed.delete_overlay('missing'),
                   lambda: ed.add_effect('missing', ClipEffect('e', 'blur', 1)),
                   lambda: ed.update_effect('clip', 'missing', amount=1),
                   lambda: ed.delete_effect('clip', 'missing'),
                   lambda: ed.move_effect('clip', 'missing', 0)]:
        with pytest.raises(ValueError):
            action()
        assert ed.project == before and ed.can_redo and not ed.can_undo
    ed.redo()
    before = deepcopy(ed.project)
    ed.move_effect('clip', 'blur', 0)
    ed.undo()
    assert ed.project.clips[0].effects == []  # A no-op move adds no undo point.
    ed.redo()
    assert ed.project == before


def test_split_clip_has_independent_effect_stack_and_overlay_timing():
    ed = Editor(video_project(overlays=[sticker()]))
    ed.add_effect('clip', ClipEffect('blur', 'blur', 5))
    left, right = ed.split_clip('clip', 30)
    assert left.effects == right.effects
    assert left.effects is not right.effects
    ed.update_effect(right.id, 'blur', amount=2)
    assert ed.project.clips[0].effects[0].amount == 5
    assert ed.project.overlays == [sticker()]


def test_overlay_inputs_are_protected_even_disabled_missing_or_aliased(tmp_path):
    png = tmp_path / 'sticker.png'
    png.write_bytes(b'original')
    project = Project(overlays=[sticker(asset_path=str(png), enabled=False)])
    hardlink = tmp_path / 'hardlink.lumen'
    os.link(png, hardlink)
    symlink = tmp_path / 'symlink.lumen'
    symlink.symlink_to(png)
    for destination in [png, hardlink, symlink]:
        with pytest.raises(ValueError, match='overwrite'):
            project.save(destination)
        assert png.read_bytes() == b'original'
    png.unlink()
    with pytest.raises(ValueError, match='overwrite'):
        project.save(png)
    assert not png.exists()


def frame_pack(tmp_path):
    root = tmp_path.resolve() / 'pack'
    root.mkdir()
    frames = [root / 'first.png', root / 'second.png']
    for frame in frames:
        frame.write_bytes(b'Original frame; source protection never decodes PNGs.')
    manifest = root / 'animation.json'
    manifest.write_text(json.dumps(dict(version=1, frames=[frame.name for frame in frames],
                                        frame_duration=3, loop=True)))
    project = Project(overlays=[sticker(asset_path=str(manifest))])
    return project, manifest, frames


def test_frame_pack_manifest_and_all_frames_are_save_protected(tmp_path):
    project, manifest, frames = frame_pack(tmp_path)
    alias = tmp_path / 'aliased-frame.lumen'
    os.link(frames[-1], alias)
    before = {path: path.read_bytes() for path in [manifest, *frames, alias]}
    for path in before:
        with pytest.raises(ValueError, match='overwrite'):
            project.save(path)
        assert path.read_bytes() == before[path]
    target = manifest.parent / 'safe.lumen'
    project.save(target)
    assert Project.load(target) == project


def test_unreadable_pack_preserves_directory_but_can_save_elsewhere(tmp_path):
    project, manifest, frames = frame_pack(tmp_path)
    manifest.write_text('broken manifest')
    for target in [manifest, *frames, manifest.parent / 'new.lumen']:
        with pytest.raises(ValueError, match='unreadable overlay'):
            project.save(target)
    assert manifest.read_text() == 'broken manifest'
    target = tmp_path / 'safe.lumen'
    project.save(target)
    assert Project.load(target) == project
    manifest.unlink()
    with pytest.raises(ValueError, match='unreadable overlay'):
        project.save(frames[0])
    project.save(target)
    assert Project.load(target) == project


def test_valid_manifest_with_missing_frame_is_saved_without_overwriting_recoverable_input(tmp_path):
    project, manifest, frames = frame_pack(tmp_path)
    frames[-1].unlink()
    with pytest.raises(ValueError, match='unreadable overlay'):
        project.save(frames[-1])
    assert not frames[-1].exists()
    project.save(tmp_path / 'safe.lumen')
    assert manifest.exists()


def test_unreadable_pack_protects_existing_frame_hardlink_outside_pack(tmp_path):
    project, manifest, frames = frame_pack(tmp_path)
    alias = tmp_path / 'aliased-frame.lumen'
    os.link(frames[0], alias)
    expected = alias.read_bytes()
    frames[-1].unlink()  # Dependency enumeration cannot complete.
    with pytest.raises(ValueError, match='unreadable overlay'):
        project.save(alias)
    assert alias.read_bytes() == expected
    ordinary_project = tmp_path / 'ordinary.lumen'
    ordinary_project.write_text('old project')
    project.save(ordinary_project)  # An ordinary save elsewhere remains usable.
    assert Project.load(ordinary_project) == project

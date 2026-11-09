"""Schema43 relationship foundation, with frozen genuine schema42 provenance.

No audio-only roll command is introduced by this milestone. Independent maps
below are constructed directly from rational algebra, never candidate helpers.
"""
from native_path_fixture import bind_missing_project_paths
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

import pytest

from lumen.model import (AVLink, AV_LINK_ALIGNMENT, AV_LINK_AFFINE_ALIGNMENT,
    AudioMixAnchor, Clip, ClipGroup, Editor, Media, Project, VERSION,
    av_link_timing_matches)
from lumen.timing import ClipTimeMap, duration_frames

FIXTURE = Path(__file__).parent / 'fixtures/schema42-before-affine-av-links.json'
FIXTURE_SHA = 'e343deddab36a51128981229a10d607d993ba56c81c215a1482a03dc4c54565c'


def affine_project(speed=Fraction(1), fps=Fraction(24)):
    origin = Fraction(161, 4)
    vmap = ClipTimeMap(origin, origin + 10 * speed - speed / 4, speed)
    amap = ClipTimeMap(origin - 2 * speed, origin + 13 * speed - speed / 2, speed)
    media = Media('m', '/not-opened.mov', 'source', 1000, 64, 48, True, True,
                  fps.numerator, fps.denominator)
    video = Clip('v', 'm', 0, 10, vmap.source_in, duration_frames(vmap), time_map=vmap,
                 source_audio_enabled=False, muted=True, gain_db=7,
                 audio_mix_anchor=AudioMixAnchor(0, 2, 'historical'))
    audio = Clip('a', 'm', 2, 8, amap.source_in, duration_frames(amap), time_map=amap,
                 muted=True, gain_db=-3, audio_mix_anchor=AudioMixAnchor(0, 2, 'historical'))
    return Project(fps_num=fps.numerator, fps_den=fps.denominator, media=[media],
                   clips=[video, audio], av_links=[AVLink('l', 'v', 'a', 'm', AV_LINK_AFFINE_ALIGNMENT)])


def state(editor):
    return deepcopy((editor.project, editor._undo, editor._redo, editor.revision))


def test_genuine_schema42_fixture_retains_strict_mode_and_every_nonversion_value():
    raw = FIXTURE.read_bytes()
    assert sha256(raw).hexdigest() == FIXTURE_SHA
    data = json.loads(raw); before = deepcopy(data)
    assert data['version'] == 42 and data['av_links'][0]['alignment'] == AV_LINK_ALIGNMENT
    loaded = Project.from_dict(data)
    assert data == before and loaded.av_links[0].alignment == AV_LINK_ALIGNMENT
    current = loaded.to_dict()
    assert current['version'] == VERSION == 43
    current['version'] = 42
    assert current == before


@pytest.mark.parametrize('speed', [Fraction(1,4), Fraction(2,3), Fraction(1), Fraction(3,2), Fraction(4)])
@pytest.mark.parametrize('fps', [Fraction(24), Fraction(24000,1001), Fraction(30000,1001), Fraction(60000,1001)])
def test_affine_exact_intercept_accepts_unequal_edges_and_independent_partial_tails(speed, fps):
    project = affine_project(speed, fps); before = deepcopy(project)
    project.validate()
    video, audio = project.clips
    assert video.start != audio.start and video.duration != audio.duration
    assert video.speed == audio.speed == speed
    assert video.timing.source_in - video.start * speed == audio.timing.source_in - audio.start * speed
    assert video.duration * speed - (video.source_out - video.source_in) == speed / 4
    assert audio.duration * speed - (audio.source_out - audio.source_in) == speed / 2
    assert av_link_timing_matches(video, audio, AV_LINK_AFFINE_ALIGNMENT)
    assert not av_link_timing_matches(video, audio, AV_LINK_ALIGNMENT)
    assert Project.from_dict(project.to_dict()) == project == before


def test_visible_edge_equality_is_not_strict_timing_or_a_downgrade():
    project = affine_project()
    for clip, tail in zip(project.clips, [Fraction(1,2), Fraction(1,4)]):
        clip.start = 0; clip.duration = 11
        clip.source_in = 10; clip.time_map = ClipTimeMap(10, 20 + tail)
    project.validate()
    assert av_link_timing_matches(*project.clips, AV_LINK_AFFINE_ALIGNMENT)
    assert not av_link_timing_matches(*project.clips, AV_LINK_ALIGNMENT)
    data = project.to_dict(); before = deepcopy(data)
    loaded = Project.from_dict(data)
    assert data == before and loaded.av_links[0].alignment == AV_LINK_AFFINE_ALIGNMENT
    data['av_links'][0]['alignment'] = AV_LINK_ALIGNMENT
    with pytest.raises(ValueError, match='complete rational'): Project.from_dict(data)


@pytest.mark.parametrize('equal_geometry', [False, True])
def test_schema42_rejects_affine_mode_even_when_its_geometry_is_strict(equal_geometry):
    data = json.loads(FIXTURE.read_text()) if equal_geometry else affine_project().to_dict()
    data['version'] = 42; data['av_links'][0]['alignment'] = AV_LINK_AFFINE_ALIGNMENT
    before = deepcopy(data)
    with pytest.raises(ValueError, match='Version 42 A/V links require'): Project.from_dict(data)
    assert data == before


def test_schema43_mixed_modes_are_preserved_not_inferred_or_normalized():
    project = affine_project()
    project.clips.extend([Clip('v1', 'm', 1, 60, 50, 8, source_audio_enabled=False),
                          Clip('a1', 'm', 3, 60, 50, 8)])
    project.av_links.append(AVLink('strict', 'v1', 'a1', 'm'))
    data = project.to_dict(); loaded = Project.from_dict(data)
    assert loaded == project and [link.alignment for link in loaded.av_links] == [AV_LINK_AFFINE_ALIGNMENT, AV_LINK_ALIGNMENT]
    # An ordinary explicit Link command still creates only strict v1.
    editor = Editor(loaded); editor.unlink_av_clips(['v1'])
    assert editor.link_av_clips('v1', 'a1').alignment == AV_LINK_ALIGNMENT


@pytest.mark.parametrize('mode', [None, False, 1, [], {}, '', 'same-source-affine-sync-v3', 'offset-v1'])
def test_schema43_unknown_alignment_rejects_without_wire_mutation(mode):
    data = affine_project().to_dict(); data['av_links'][0]['alignment'] = mode
    before = deepcopy(data)
    with pytest.raises(ValueError, match='alignment'): Project.from_dict(data)
    assert data == before


@pytest.mark.parametrize('mutation', ['speed', 'intercept', 'duration', 'v_audio', 'a_audio',
    'media', 'role', 'duplicate', 'group', 'source_end', 'rational'])
def test_affine_mode_does_not_weaken_individual_identity_ownership_or_timing_checks(mutation):
    data = affine_project().to_dict()
    video, audio = data['clips']
    if mutation == 'speed':
        # Same intercept at t=8 but a different slope must still reject.
        audio['time_map'] = ClipTimeMap(Fraction(185,4), Fraction(301,4), Fraction(2)).to_dict()
        audio['duration'] = 15
    elif mutation == 'intercept':
        audio['time_map'] = ClipTimeMap(Fraction(157,4), Fraction(215,4)).to_dict()
    elif mutation == 'duration': audio['duration'] += 1
    elif mutation == 'v_audio': video['source_audio_enabled'] = True
    elif mutation == 'a_audio': audio['source_audio_enabled'] = False
    elif mutation == 'media':
        data['media'].append({**data['media'][0], 'id': 'other'})
        audio['media_id'] = 'other'
    elif mutation == 'role': audio['track'] = 1
    elif mutation == 'duplicate': data['av_links'].append({**data['av_links'][0], 'id': 'other'})
    elif mutation == 'group': data['clip_groups'] = [{'id': 'g', 'clip_ids': ['v', 'a']}]
    elif mutation == 'source_end':
        audio['time_map'] = ClipTimeMap(Fraction(153,4), 999999).to_dict()
        audio['duration'] = 999961
    elif mutation == 'rational':
        audio['time_map']['source_in_num'] *= 2
        audio['time_map']['source_in_den'] *= 2
    before = deepcopy(data)
    match = 'Affine A/V links require' if mutation in ('speed', 'intercept') else None
    with pytest.raises(ValueError, match=match): Project.from_dict(data)
    assert data == before


@pytest.mark.parametrize('operation', ['start', 'source', 'metadata', 'fps', 'ownership', 'delete'])
def test_generic_batch_fences_reject_even_synchronized_affine_edits(operation):
    editor = Editor(affine_project()); before = state(editor)
    def change():
        if operation == 'start':
            for clip in editor.project.clips: clip.start += 5
        elif operation == 'source':
            for clip in editor.project.clips:
                old = clip.timing
                clip.time_map = ClipTimeMap(old.source_in + 1, old.source_out + 1, old.speed)
                clip.source_in = clip.time_map.source_in
        elif operation == 'metadata': editor.project.av_links[0] = replace(editor.project.av_links[0], id='changed')
        elif operation == 'fps': editor.project.fps_num = 30
        elif operation == 'ownership': editor.project.clips[0].source_audio_enabled = True
        elif operation == 'delete': editor.project.clips.clear()
    with pytest.raises(ValueError): editor._commit(change)
    assert state(editor) == before


@pytest.mark.parametrize('track', [0, 2])
@pytest.mark.parametrize('operation', ['move', 'delete', 'unlink'])
def test_affine_existing_whole_pair_operations_keep_both_track_locks(track, operation):
    editor = Editor(affine_project()); editor.set_track_edit_lock(track, True); before = state(editor)
    change = {'move': lambda: editor.move_clips(['v','a'], 24),
              'delete': lambda: editor.delete_clips(['v','a']),
              'unlink': lambda: editor.unlink_av_clips(['v'])}[operation]
    with pytest.raises(ValueError, match='[Ll]ock'): change()
    assert state(editor) == before


def test_affine_whole_move_unlink_delete_roundtrip_recovery_and_history(tmp_path):
    from lumen.project_recovery import RecoveryStore
    editor = Editor(affine_project()); bind_missing_project_paths(editor.project, tmp_path); original = deepcopy(editor.project)
    editor.move_clips(['v', 'a'], 24); moved = deepcopy(editor.project)
    assert moved.av_links == original.av_links
    for old, new in zip(original.clips, moved.clips):
        expected = deepcopy(old); expected.start += 24
        assert new == expected
    assert editor.undo() and editor.project == original
    assert editor.redo() and editor.project == moved
    target = tmp_path / 'affine.lumen'; editor.project.save(target)
    assert Project.load(target) == moved
    store = RecoveryStore(tmp_path / 'recovery')
    try:
        entry = store.write(editor.project, str(uuid4()), target)
        assert store.load(entry) == moved
    finally: store.close()
    editor.unlink_av_clips(['a']); unlinked = deepcopy(moved); unlinked.av_links = []
    assert editor.project == unlinked and not editor._clip('v').source_audio_enabled
    assert editor.undo() and editor.project == moved
    editor.delete_clips(['v','a']); assert not editor.project.clips and not editor.project.av_links
    assert editor.undo() and editor.project == moved


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(24000,1001), Fraction(30000,1001), Fraction(60000,1001)])
@pytest.mark.parametrize('muted', [False, True])
def test_affine_copy_preserves_unequal_maps_mode_ownership_and_fresh_mix_slots(tmp_path, fps, muted):
    from lumen.clip_copy_paste import (prepare_clip_copy, verify_clip_copy, accept_clip_copy,
        plan_clip_paste, verify_clip_paste, apply_clip_paste)
    project = affine_project(fps=fps)
    path = tmp_path / 'source.mov'; path.write_bytes(b'bounded-copy-identity' * 30)
    project.media[0].path = str(path); project.clips[1].muted = muted
    editor = Editor(project); original = deepcopy(project); live = list(project.clips)
    request = prepare_clip_copy(editor, ['a'])
    clipboard = accept_clip_copy(editor, request, verify_clip_copy(request))
    assert clipboard.clip_count == 2 and clipboard.anchor == 8
    # 100 frames is an exact sample translation for every chosen project rate.
    plan = plan_clip_paste(editor, clipboard, 108)
    clones = apply_clip_paste(editor, clipboard, plan, verify_clip_paste(plan, clipboard))
    assert editor.project.av_links[1].alignment == AV_LINK_AFFINE_ALIGNMENT
    assert editor.project.av_links[1].clip_ids == tuple(clip.id for clip in clones)
    for old, clone in zip(original.clips, clones):
        expected = deepcopy(old); expected.id = clone.id; expected.start += 100; expected.audio_mix_anchor = None
        assert clone == expected
    assert all(old is current for old, current in zip(live, editor.project.clips))
    accepted = deepcopy(editor.project)
    assert editor.undo() and editor.project == original
    assert editor.redo() and editor.project == accepted


@pytest.mark.parametrize('operation', ['separate', 'reattach', 'group', 'partial_move', 'partial_delete'])
def test_affine_relationship_does_not_authorize_ownership_or_partial_operations(operation):
    from lumen.source_audio_separation import plan_source_audio_separation
    from lumen.source_audio_reattach import plan_source_audio_reattach
    editor = Editor(affine_project()); before = state(editor)
    actions = {'separate': lambda: plan_source_audio_separation(editor, 'v', 3),
        'reattach': lambda: plan_source_audio_reattach(editor, 'v', 'a'),
        'group': lambda: editor.group_clips(['v','a']),
        'partial_move': lambda: editor.move_clips(['v'], 24),
        'partial_delete': lambda: editor.delete_clips(['a'])}
    with pytest.raises(ValueError): actions[operation]()
    assert state(editor) == before


def test_unlinking_affine_pair_still_does_not_authorize_reattaching_unequal_maps():
    from lumen.source_audio_reattach import plan_source_audio_reattach
    editor = Editor(affine_project()); editor.unlink_av_clips(['v']); before = state(editor)
    with pytest.raises(ValueError, match='identical timeline'): plan_source_audio_reattach(editor, 'v', 'a')
    assert state(editor) == before


@pytest.mark.parametrize('field', ['id', 'video_clip_id', 'audio_clip_id', 'media_id', 'alignment'])
def test_genuine_schema42_link_wire_fields_remain_exact(field):
    data = json.loads(FIXTURE.read_text()); data['av_links'][0].pop(field)
    with pytest.raises(ValueError, match='fields'): Project.from_dict(data)
    data = json.loads(FIXTURE.read_text()); data['av_links'][0]['future'] = False
    with pytest.raises(ValueError, match='fields'): Project.from_dict(data)

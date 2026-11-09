"""Explicit session-only participation and atomic multi-collection editing."""
from native_path_fixture import bind_missing_project_paths
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
from itertools import combinations
from uuid import uuid4

import pytest
from lumen.model import (MAX_CAPTION_FRAME, Caption, Clip, ClipEffect, Editor,
                         Media, Overlay, OverlayKeyframe, Project, VERSION, VideoTransition)
from lumen.project_recovery import RecoveryStore
from lumen.source_insert import _guard_pcm_translation
from lumen.timing import sample_boundary
from test_source_overwrite_model import rejected, state


def sample(fps=Fraction(30)):
    media = Media('m', '/missing.mov', 'Source', 100, 64, 48, True, True, fps.numerator, fps.denominator)
    clips = [Clip(f'before{t}', 'm', t, 0, 0, 10) for t in range(4)]
    clips += [Clip(f'after{t}', 'm', t, 25 + 5*t, 60 + 5*t, 10, gain_db=-3, fade_in=1, fade_out=2) for t in range(4)]
    return Editor(Project(fps_num=fps.numerator, fps_den=fps.denominator, media=[media], clips=clips,
        captions=[Caption('cb', 0, 10, 'before'), Caption('ca', 20, 10, 'after', 'zh-CN')],
        overlays=[Overlay('sb', '/first.png', 'a'*64, 0, 10),
                  Overlay('sa', '/second.png', 'b'*64, 30, 10,
                    keyframes=[OverlayKeyframe(), OverlayKeyframe(8, 3, 7, 2, .8)])]))


def options(target):
    others = [t for t in range(4) if t != target]
    return [tuple(sorted((target, *part))) for count in range(4) for part in combinations(others, count)]


@pytest.mark.parametrize('target', range(4))
@pytest.mark.parametrize('subset', range(8))
@pytest.mark.parametrize('captions,stickers', [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30), Fraction(30000, 1001)])
def test_all_subsets_exact_records_full_impact_single_transaction(target, subset, captions, stickers, fps):
    editor = sample(fps); original = deepcopy(editor.project); before = state(editor)
    tracks = options(target)[subset]
    live = {n: {x.id: x for x in getattr(editor.project, n)} for n in ('clips', 'captions', 'overlays')}
    plan = editor.plan_source_insert('m', target, 10, 0, 5, participating_tracks=tracks,
                                     follow_captions=captions, follow_stickers=stickers)
    assert state(editor) == before and plan.participating_tracks == tracks
    assert plan.follow_captions is captions and plan.follow_stickers is stickers
    assert plan.cross_track == (len(tracks) > 1 or captions or stickers)
    assert len(plan.item_impacts) == 12 and not plan.remnants and not plan.proof_clip_ids
    editor.apply_source_insert(plan)
    for category, field, follows in [('clip', 'clips', None), ('caption', 'captions', captions), ('sticker', 'overlays', stickers)]:
        current = {x.id: x for x in getattr(editor.project, field)}
        for old in getattr(original, field):
            moves = old.start >= 10 and (old.track in tracks if category == 'clip' else follows)
            expected = deepcopy(old); expected.start += 5 if moves else 0
            assert current[old.id] == expected
            impact = next(x for x in plan.item_impacts if (x.category, x.item_id) == (category, old.id))
            assert (impact.previous_start, impact.start, impact.duration) == (old.start, expected.start, old.duration)
            assert impact.disposition == ('move' if moves else 'fixed')
            if not moves: assert current[old.id] is live[field][old.id]
    assert len(editor._undo) == 1 and editor.revision == 1
    after = deepcopy(editor.project)
    for name in ('media', 'media_bins', 'media_bin_ids', 'source_ranges', 'clip_groups', 'video_transitions', 'track_edit_locks', 'master_gain_db', 'caption_style'):
        assert getattr(after, name) == getattr(original, name)
    assert Project.from_dict(after.to_dict()) == after and VERSION == 43
    assert editor.undo() and editor.project == original
    assert editor.redo() and editor.project == after


@pytest.mark.parametrize('category', ['clip', 'caption', 'sticker'])
@pytest.mark.parametrize('start,duration', [(5, 5), (10, 5), (15, 5), (5, 6), (0, 20)])
def test_follower_boundaries_or_straddle_atomic_refusal(category, start, duration):
    editor = sample(); editor.project.clips = []; editor.project.captions = []; editor.project.overlays = []
    name = {'clip': 'clips', 'caption': 'captions', 'sticker': 'overlays'}[category]
    item = (Clip('item', 'm', 1, start, 0, duration) if category == 'clip' else
            Caption('item', start, duration, 'text') if category == 'caption' else
            Overlay('item', '/missing.png', 'a'*64, start, duration))
    setattr(editor.project, name, [item])
    args = dict(participating_tracks=(0, 1), follow_captions=True, follow_stickers=True)
    if start < 10 < start + duration:
        rejected(editor, lambda: editor.plan_source_insert('m', 0, 10, 0, 5, **args), 'spans the insertion point')
    else:
        editor.apply_source_insert(editor.plan_source_insert('m', 0, 10, 0, 5, **args))
        moved = next(x for x in getattr(editor.project, name) if x.id == 'item')
        assert moved.start == start + (5 if start >= 10 else 0)


def test_default_and_explicit_target_only_preserve_spanning_nonparticipants():
    editor = sample(); editor.project.clips[1].duration = 20
    editor.project.captions[0].duration = 15; editor.project.overlays[0].duration = 15
    for kwargs in ({}, {'participating_tracks': (0,)}):
        clone = Editor(deepcopy(editor.project)); original = deepcopy(clone.project)
        plan = clone.plan_source_insert('m', 0, 10, 0, 5, **kwargs)
        assert not plan.cross_track
        clone.apply_source_insert(plan)
        assert clone.project.captions == original.captions and clone.project.overlays == original.overlays
        assert clone._clip('before1') == original.clips[1]


@pytest.mark.parametrize('kind', ['group', 'transition', 'effect', 'lock', 'speed'])
def test_unsafe_followers_reject_without_unlock_ungroup_or_settings_loss(kind):
    editor = sample(); follower = editor._clip('after1')
    if kind == 'group': editor.group_clips(['after1', 'after2'])
    elif kind == 'transition':
        editor.project.clips.append(Clip('endpoint', 'm', 1, follower.end, 100, 10))
        editor.project.video_transitions.append(VideoTransition('t', follower.id, 'endpoint', 4))
    elif kind == 'effect': follower.effects = [ClipEffect('fx', 'blur', 1, enabled=False)]
    elif kind == 'lock': editor.set_track_edit_lock(1, True)
    else:
        from lumen.timing import ClipTimeMap
        follower.time_map = ClipTimeMap(follower.source_in, follower.source_in + 20, 2)
    editor.project.validate()
    rejected(editor, lambda: editor.plan_source_insert('m', 0, 10, 0, 5, participating_tracks=(0, 1)))
    old = deepcopy(follower)
    editor.apply_source_insert(editor.plan_source_insert('m', 0, 10, 0, 5))
    assert editor._clip(follower.id) == old


@pytest.mark.parametrize('kwargs', [
    {'participating_tracks': []}, {'participating_tracks': ()}, {'participating_tracks': (1,)},
    {'participating_tracks': (0, 0)}, {'participating_tracks': (0, True)}, {'participating_tracks': (0, 4)},
    {'participating_tracks': (0, -1)}, {'participating_tracks': (0, 1.0)}, {'follow_captions': 1},
    {'follow_captions': None}, {'follow_stickers': 'yes'}, {'follow_stickers': 0}])
def test_invalid_choice_types_atomic(kwargs):
    editor = sample()
    rejected(editor, lambda: editor.plan_source_insert('m', 0, 10, 0, 5, **kwargs))


@pytest.mark.parametrize('category', ['clip', 'caption', 'sticker'])
def test_followers_cannot_overflow(category):
    editor = sample()
    item = editor._clip('after1') if category == 'clip' else editor.project.captions[-1] if category == 'caption' else editor.project.overlays[-1]
    item.start = MAX_CAPTION_FRAME - item.duration
    rejected(editor, lambda: editor.plan_source_insert('m', 0, 10, 0, 5,
        participating_tracks=(0, 1), follow_captions=True, follow_stickers=True))


def test_empty_choice_intent_is_bound_and_plan_immutable():
    editor = sample(); editor.project.clips = []; editor.project.captions = []; editor.project.overlays = []
    plan = editor.plan_source_insert('m', 0, 10, 0, 5, participating_tracks=(2, 0))
    assert plan.participating_tracks == (0, 2)
    with pytest.raises(FrozenInstanceError): plan.follow_captions = True
    for changed in [replace(plan, follow_captions=True), replace(plan, follow_stickers=True),
                    replace(plan, participating_tracks=(0,)), replace(plan, participating_tracks=(2, 0)),
                    replace(plan, follow_captions=1)]:
        rejected(editor, lambda: editor.apply_source_insert(changed))


def test_stale_caption_sticker_and_lock_change_rejects():
    for mutate in [lambda e: setattr(e.project.captions[-1], 'text', 'new'),
                   lambda e: setattr(e.project.overlays[-1], 'start', 31),
                   lambda e: e.set_track_edit_lock(1, True)]:
        editor = sample()
        plan = editor.plan_source_insert('m', 0, 10, 0, 5, participating_tracks=(0, 1), follow_captions=True, follow_stickers=True)
        mutate(editor)
        rejected(editor, lambda: editor.apply_source_insert(plan), 'stale')


def test_commit_guard_sees_all_collections_together_and_rollback_is_complete(monkeypatch):
    editor = sample(); original = state(editor)
    plan = editor.plan_source_insert('m', 0, 10, 0, 5, participating_tracks=(0, 1), follow_captions=True, follow_stickers=True)
    seen = []
    def fail(before, after):
        if editor.project == after:
            seen.append((editor._clip('after1').start, editor.project.captions[-1].start, editor.project.overlays[-1].start))
            raise ValueError('injected commit guard')
    monkeypatch.setattr(editor, '_guard_track_edit_locks', fail)
    with pytest.raises(ValueError, match='injected commit guard'): editor.apply_source_insert(plan)
    assert seen == [(35, 25, 35)] and state(editor) == original


def test_save_reopen_recovery_preserve_all_collections_and_one_undo(tmp_path):
    editor = sample(); bind_missing_project_paths(editor.project, tmp_path); original = deepcopy(editor.project)
    editor.apply_source_insert(editor.plan_source_insert('m', 0, 10, 0, 5,
        participating_tracks=(0, 1, 2, 3), follow_captions=True, follow_stickers=True))
    path = tmp_path / 'participation.lumen'; editor.project.save(path)
    assert Project.load(path) == editor.project
    store = RecoveryStore(tmp_path / 'recovery')
    try:
        entry = store.write(editor.project, str(uuid4()), path)
        assert store.load(entry) == editor.project
    finally: store.close()
    assert len(editor._undo) == 1 and editor.undo() and editor.project == original


def test_common_sample_counterexample_not_just_local_compilation():
    fps = Fraction(30000, 1001); editor = sample(fps)
    editor.project.clips = [Clip('target', 'm', 0, 1, 0, 5), Clip('follower', 'm', 2, 2, 0, 5)]
    for old in editor.project.clips:
        moved = deepcopy(old); moved.start += 1
        _guard_pcm_translation(old, moved, editor.project)
    assert sample_boundary(2, fps)-sample_boundary(1, fps) == 1601
    assert sample_boundary(3, fps)-sample_boundary(2, fps) == 1602
    rejected(editor, lambda: editor.plan_source_insert('m', 0, 1, 0, 1, participating_tracks=(0, 2)), 'relative audio synchronization')
    editor.apply_source_insert(editor.plan_source_insert('m', 0, 1, 0, 1))
    assert editor._clip('follower').start == editor._clip('target').start == 2


@pytest.mark.parametrize('silent_kind', ['muted', 'no_audio', 'offset_silent'])
def test_inaudible_followers_do_not_invent_common_displacement(silent_kind):
    editor = sample(Fraction(30000, 1001)); editor.project.clips = [Clip('follow', 'm', 2, 2, 0, 5)]
    if silent_kind == 'muted': editor.project.clips[0].muted = True
    elif silent_kind == 'no_audio': editor.project.clips[0].track = 1; editor.project.media[0].has_audio = False
    else: editor.project.media[0].audio_offset = 50
    track = editor.project.clips[0].track
    editor.apply_source_insert(editor.plan_source_insert('m', 0, 1, 0, 1, participating_tracks=(0, track)))
    assert editor._clip('follow').start == 3

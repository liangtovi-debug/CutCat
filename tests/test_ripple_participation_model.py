"""Single committed interval, explicit followers, atomic fail-closed plans."""
from native_path_fixture import bind_missing_project_paths
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from itertools import combinations
from threading import Thread
from uuid import uuid4

import pytest
from lumen.model import (Caption, Clip, ClipEffect, Editor, Grade, Media, Overlay,
                         OverlayKeyframe, Project, VERSION, VideoTransition)
from lumen.project_recovery import RecoveryStore
from lumen.clip_audio_translation import guard_local_audio_translation
from lumen.clip_transform import ClipTransform
from lumen.clip_motion import ClipMotionKeyframe
from lumen.timing import ClipTimeMap, sample_boundary
from test_track_edit_locks_model import rejected, snapshot


def sample(target=0, fps=Fraction(30)):
    media = Media('m', '/missing.mov', 'Source', 100, 64, 48, True, True,
                  fps.numerator, fps.denominator)
    clips = [Clip(f'before{t}', 'm', t, 0, 0, 10) for t in range(4)]
    clips += [Clip(f'after{t}', 'm', t, 25 + 5*t, 60 + 5*t, 10,
                   gain_db=-3, fade_in=1, fade_out=2) for t in range(4)]
    clips += [Clip('selected', 'm', target, 10, 20, 5)]
    return Editor(Project(fps_num=fps.numerator, fps_den=fps.denominator, media=[media], clips=clips,
        captions=[Caption('cb', 0, 10, 'before'), Caption('ca', 20, 10, 'after', 'zh-CN')],
        overlays=[Overlay('sb', '/first.png', 'a'*64, 0, 10),
                  Overlay('sa', '/second.png', 'b'*64, 30, 10,
                          keyframes=[OverlayKeyframe(), OverlayKeyframe(8, 3, 7, 2, .8)])]))


def options(target):
    others = [t for t in range(4) if t != target]
    return [tuple(sorted((target, *part))) for n in range(4) for part in combinations(others, n)]


@pytest.mark.parametrize('target', range(4))
@pytest.mark.parametrize('subset', range(8))
@pytest.mark.parametrize('captions,stickers', [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30), Fraction(30000, 1001)])
def test_all_subsets_preserve_complete_records_and_single_undo(target, subset, captions, stickers, fps):
    editor = sample(target, fps); original = deepcopy(editor.project); before = snapshot(editor)
    tracks = options(target)[subset]
    live = {n: {x.id: x for x in getattr(editor.project, n)} for n in ('clips', 'captions', 'overlays')}
    plan = editor.plan_ripple_delete('selected', participating_tracks=tracks,
                                     follow_captions=captions, follow_stickers=stickers)
    assert snapshot(editor) == before
    assert (plan.start, plan.end, plan.duration, plan.track) == (10, 15, 5, target)
    assert plan.cross_track == (len(tracks) > 1 or captions or stickers)
    assert len(plan.item_impacts) == 13
    editor.apply_ripple_delete(plan)
    expected = deepcopy(original); expected.clips = [x for x in expected.clips if x.id != 'selected']
    for category, name, follows in [('clip','clips',None), ('caption','captions',captions), ('sticker','overlays',stickers)]:
        actual = {x.id:x for x in getattr(editor.project,name)}
        for item in getattr(expected,name):
            old_start = item.start
            moved = item.start >= 15 and (item.track in tracks if category == 'clip' else follows)
            if moved: item.start -= 5
            assert actual[item.id] == item
            record = next(x for x in plan.item_impacts if (x.category,x.item_id)==(category,item.id))
            assert (record.previous_start,record.start,record.disposition)==(old_start,item.start,'move' if moved else 'fixed')
            if not moved: assert actual[item.id] is live[name][item.id]
    assert editor.project == expected and VERSION == 43
    assert len(editor._undo) == 1 and editor.revision == 1
    assert Project.from_dict(expected.to_dict()) == expected
    assert editor.undo() and editor.project == original
    assert editor.redo() and editor.project == expected


@pytest.mark.parametrize('category', ['clip', 'caption', 'sticker'])
@pytest.mark.parametrize('start,duration', [(0,10),(5,5),(15,1),(16,5),(9,2),(10,1),(11,2),(14,1),(14,2),(10,5),(5,20)])
def test_exact_boundaries_and_any_intersection_including_wholly_contained(category,start,duration):
    editor = sample(); editor.project.clips = [editor._clip('selected')]
    editor.project.captions=[]; editor.project.overlays=[]
    item = (Clip('item','m',1,start,0,duration) if category=='clip' else
            Caption('item',start,duration,'text') if category=='caption' else
            Overlay('item','/sticker.png','a'*64,start,duration))
    name = {'clip':'clips','caption':'captions','sticker':'overlays'}[category]
    getattr(editor.project,name).append(item)
    args=dict(participating_tracks=(0,1),follow_captions=True,follow_stickers=True)
    if start < 15 and start+duration > 10:
        rejected(editor,lambda:editor.plan_ripple_delete('selected',**args),'intersects the deleted interval')
    else:
        editor.apply_ripple_delete(editor.plan_ripple_delete('selected',**args))
        assert next(x for x in getattr(editor.project,name) if x.id=='item').start == start-(5 if start>=15 else 0)
    # Unchecked intersecting records must remain completely unchanged.
    fresh=sample(); fresh.project.clips=[fresh._clip('selected')]
    fresh.project.captions=[]; fresh.project.overlays=[]
    getattr(fresh.project,name).append(deepcopy(item)); old=deepcopy(item)
    fresh.apply_ripple_delete(fresh.plan_ripple_delete('selected'))
    assert next(x for x in getattr(fresh.project,name) if x.id=='item') == old


@pytest.mark.parametrize('tracks', [(), [], [0], (1,), (0,0), (0,4), (True,), (0,False), '0', 0])
def test_invalid_participant_choices_are_atomic(tracks):
    editor=sample(); rejected(editor,lambda:editor.plan_ripple_delete('selected',participating_tracks=tracks))


@pytest.mark.parametrize('field', ['follow_captions','follow_stickers'])
@pytest.mark.parametrize('value', [0,1,None,[], 'true'])
def test_follower_flags_are_strict(field,value):
    editor=sample(); rejected(editor,lambda:editor.plan_ripple_delete('selected',**{field:value}))


@pytest.mark.parametrize('field,value', [('selected_clip_id','after0'), ('track',1), ('start',9), ('end',16),
    ('candidate_signature','bad'),('project_signature','bad'),('revision',True),('fps_num',True),
    ('participating_tracks',(0,1)),('follow_captions',True),('follow_stickers',True),('item_impacts',()),
    ('participating_tracks',()),('follow_captions',1)])
def test_tampered_plan_including_empty_participant_intent_rejects(field,value):
    editor=sample(); editor.project.clips=[editor._clip('selected')]; editor.project.captions=[];editor.project.overlays=[]
    plan=editor.plan_ripple_delete('selected')
    rejected(editor,lambda:editor.apply_ripple_delete(replace(plan,**{field:value})))


def test_impact_bool_tamper_and_reordered_tracks_reject():
    editor=sample(); plan=editor.plan_ripple_delete('selected',participating_tracks=(0,1))
    changed=replace(plan.item_impacts[0],track=False)
    rejected(editor,lambda:editor.apply_ripple_delete(replace(plan,item_impacts=(changed,*plan.item_impacts[1:]))))
    rejected(editor,lambda:editor.apply_ripple_delete(replace(plan,participating_tracks=(1,0))))


@pytest.mark.parametrize('kind', ['edit','undo_aba','lock','relink','new_session','direct_mutation','repeated'])
def test_all_stale_paths_preserve_current_state(kind):
    editor=sample(); plan=editor.plan_ripple_delete('selected')
    if kind=='edit': editor.update_clip('after0',gain_db=-7)
    elif kind=='undo_aba': editor.update_clip('after0',gain_db=-7);editor.undo()
    elif kind=='lock': editor.set_track_edit_lock(0,True)
    elif kind=='relink': editor.project.media[0].path='/changed.mov'
    elif kind=='new_session': editor.project=deepcopy(editor.project)
    elif kind=='direct_mutation': editor.project.captions[0].text='changed without revision'
    else: editor.apply_ripple_delete(plan)
    rejected(editor,lambda:editor.apply_ripple_delete(plan),'stale')


@pytest.mark.parametrize('kind', ['selected_group','follower_group','target_lock','follower_lock','transition'])
def test_groups_locks_transitions_fail_closed(kind):
    editor=sample()
    if kind=='selected_group': editor.group_clips(['selected','before1'])
    elif kind=='follower_group': editor.group_clips(['after1','after2'])
    elif kind=='target_lock': editor.set_track_edit_lock(0,True)
    elif kind=='follower_lock': editor.set_track_edit_lock(1,True)
    else:
        editor._clip('after1').source_in=30
        editor.project.clips.append(Clip('pair','m',1,editor._clip('after1').end,50,10))
        editor.project.video_transitions=[VideoTransition('transition','after1','pair',4)]
    rejected(editor,lambda:editor.plan_ripple_delete('selected',participating_tracks=(0,1)))


def test_empty_or_fixed_locked_follower_is_not_a_mutation():
    editor=sample(); editor.project.clips=[c for c in editor.project.clips if c.id!='after1']
    editor.set_track_edit_lock(1,True)
    plan=editor.plan_ripple_delete('selected',participating_tracks=(0,1))
    editor.apply_ripple_delete(plan)
    assert editor.project.track_edit_locks==[False,True,False,False]


def test_old_valid_transition_pair_translation_remains_supported_new_mode_rejects():
    editor=sample(); editor.project.clips=[editor._clip('selected'),Clip('left','m',0,25,30,10),Clip('right','m',0,35,50,10)]
    editor.project.video_transitions=[VideoTransition('transition','left','right',4)]
    rejected(editor,lambda:editor.plan_ripple_delete('selected'),'transition')
    editor.ripple_delete_clip('selected')
    assert [(c.id,c.start) for c in editor.project.clips]==[('left',20),('right',30)]
    assert len(editor.project.video_transitions)==1


@pytest.mark.parametrize('operation', ['legacy','participation'])
def test_whole_retime_complex_picture_records_remain_eligible_without_decode(operation,monkeypatch):
    editor=sample(); clip=editor._clip('after0')
    clip.duration=10;clip.source_in=Fraction(5,4);clip.time_map=ClipTimeMap(Fraction(5,4),Fraction(65,4),Fraction(3,2))
    clip.effects=[ClipEffect('blur','blur',1.5)];clip.transform=ClipTransform(x=3,y=-2,scale=1.1)
    clip.motion_keyframes=[ClipMotionKeyframe(0),ClipMotionKeyframe(5,2,3,1.2,.8)]
    old=deepcopy(clip)
    def forbidden(*args,**kwargs): raise AssertionError('planning must not run subprocesses or decode')
    monkeypatch.setattr('subprocess.run',forbidden);monkeypatch.setattr('subprocess.Popen',forbidden)
    if operation=='legacy':editor.ripple_delete_clip('selected')
    else:editor.apply_ripple_delete(editor.plan_ripple_delete('selected'))
    assert editor._clip('after0')==replace(old,start=old.start-5)


def test_common_negative_displacement_not_duration_rounding():
    fps=Fraction(30000,1001);editor=sample(fps=fps)
    editor.project.clips=[Clip('selected','m',0,1,0,1),Clip('a','m',0,2,0,5),Clip('b','m',2,3,0,5)]
    for old in editor.project.clips[1:]:
        guard_local_audio_translation(old,replace(old,start=old.start-1),editor.project,operation='test')
    assert sample_boundary(1,fps)-sample_boundary(2,fps)==-1601
    assert sample_boundary(2,fps)-sample_boundary(3,fps)==-1602
    assert -sample_boundary(1,fps)==-1602
    rejected(editor,lambda:editor.plan_ripple_delete('selected',participating_tracks=(0,2)),'relative audio synchronization')
    # Target-only follows actual cut shift -1601 and still succeeds.
    editor.apply_ripple_delete(editor.plan_ripple_delete('selected'))
    assert editor._clip('a').start==1 and editor._clip('b').start==3


@pytest.mark.parametrize('silent_kind',['muted','no_audio','compiled_silent'])
def test_no_invented_common_shift_for_inaudible_followers(silent_kind):
    editor=sample(fps=Fraction(30000,1001))
    editor.project.clips=[Clip('selected','m',0,1,0,1),Clip('follow','m',1,3,0,5)]
    if silent_kind=='muted':editor._clip('follow').muted=True
    elif silent_kind=='no_audio':editor.project.media[0].has_audio=False
    else:editor.project.media[0].audio_offset=50
    editor.apply_ripple_delete(editor.plan_ripple_delete('selected',participating_tracks=(0,1)))
    assert editor._clip('follow').start==2


def test_all_collection_final_guard_failure_rolls_back_history_and_redo(monkeypatch):
    editor=sample();editor.update_clip('after0',gain_db=-5);editor.undo()
    plan=editor.plan_ripple_delete('selected',participating_tracks=(0,1,2,3),follow_captions=True,follow_stickers=True)
    original=snapshot(editor);seen=[]
    def fail(before):
        seen.append((editor._clip('after3').start,editor.project.captions[1].start,editor.project.overlays[1].start))
        raise ValueError('injected commit guard')
    monkeypatch.setattr(editor,'_guard_clip_group_edit',fail)
    with pytest.raises(ValueError,match='injected commit guard'):editor.apply_ripple_delete(plan)
    assert seen==[(35,15,25)] and snapshot(editor)==original


def test_extent_all_items_save_reopen_recovery_and_undo(tmp_path):
    editor=sample();editor.project.overlays.append(Overlay('anchor','/last.png','c'*64,100,10)); bind_missing_project_paths(editor.project, tmp_path)
    original=deepcopy(editor.project);assert original.duration_frames==110
    editor.apply_ripple_delete(editor.plan_ripple_delete('selected',participating_tracks=(0,1,2,3),follow_captions=True))
    assert editor.project.duration_frames==110
    path=tmp_path/'ripple.lumen';editor.project.save(path);assert Project.load(path)==editor.project
    store=RecoveryStore(tmp_path/'recovery')
    try:
        entry=store.write(editor.project,str(uuid4()),path);assert store.load(entry)==editor.project
    finally:store.close()
    assert len(editor._undo)==1 and editor.undo() and editor.project==original


def test_owner_thread_guards_both_prepare_and_apply():
    editor=sample();plan=editor.plan_ripple_delete('selected');before=snapshot(editor);errors=[]
    def run():
        for fn in (lambda:editor.plan_ripple_delete('selected'),lambda:editor.apply_ripple_delete(plan)):
            try:fn()
            except ValueError as exc:errors.append(str(exc))
    thread=Thread(target=run);thread.start();thread.join()
    assert len(errors)==2 and all('owning thread' in x for x in errors) and snapshot(editor)==before

"""Captured real-neighbor gap, complete-record translations, no deletion."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from itertools import combinations
from threading import Thread
from uuid import uuid4

import pytest
from lumen.model import (Caption, Clip, ClipEffect, Editor, Media, Overlay,
                         OverlayKeyframe, Project, VERSION, VideoTransition)
from lumen.clip_audio_translation import guard_local_audio_translation
from lumen.clip_motion import ClipMotionKeyframe
from lumen.clip_transform import ClipTransform
from lumen.project_recovery import RecoveryStore
from lumen.timing import ClipTimeMap, sample_boundary
from test_track_edit_locks_model import rejected, snapshot


def sample(target=0, fps=Fraction(30)):
    media = Media('m', '/missing.mov', 'Source', 1000, 64, 48, True, True,
                  fps.numerator, fps.denominator)
    clips = [Clip(f'left{t}', 'm', t, 0, 0, 10) for t in range(4)]
    clips += [Clip(f'right{t}', 'm', t, 15 if t == target else 25+5*t, 60+5*t, 10,
                   gain_db=-3, fade_in=1, fade_out=2) for t in range(4)]
    return Editor(Project(fps_num=fps.numerator, fps_den=fps.denominator, media=[media], clips=clips,
        captions=[Caption('cb',0,10,'before'),Caption('ca',20,10,'after','zh-CN')],
        overlays=[Overlay('sb','/first.png','a'*64,0,10),
                  Overlay('sa','/second.png','b'*64,30,10,
                    keyframes=[OverlayKeyframe(),OverlayKeyframe(8,3,7,2,.8)])]))


def bare(fps=Fraction(30)):
    editor=sample(fps=fps)
    editor.project.clips=[editor._clip('left0'),editor._clip('right0')]
    editor.project.captions=[];editor.project.overlays=[]
    return editor


def options(target):
    others=[t for t in range(4) if t != target]
    return [tuple(sorted((target,*part))) for n in range(4) for part in combinations(others,n)]


@pytest.mark.parametrize('target',range(4))
@pytest.mark.parametrize('subset',range(8))
@pytest.mark.parametrize('captions,stickers',[(False,False),(True,False),(False,True),(True,True)])
@pytest.mark.parametrize('fps',[Fraction(24),Fraction(30),Fraction(30000,1001)])
def test_all_subsets_complete_records_zero_deletions_undo_reopen(target,subset,captions,stickers,fps):
    editor=sample(target,fps); original=deepcopy(editor.project); before=snapshot(editor)
    live={n:{x.id:x for x in getattr(editor.project,n)} for n in ('clips','captions','overlays')}
    tracks=options(target)[subset]
    plan=editor.plan_gap_close(target,12,participating_tracks=tracks,
                              follow_captions=captions,follow_stickers=stickers)
    assert snapshot(editor)==before
    assert (plan.track,plan.playhead,plan.start,plan.end,plan.duration)==(target,12,10,15,5)
    assert (plan.left_clip_id,plan.right_clip_id)==(f'left{target}',f'right{target}')
    assert plan.cross_track==(len(tracks)>1 or captions or stickers)
    assert len(plan.item_impacts)==12 and all(i.disposition in ('move','fixed') for i in plan.item_impacts)
    editor.apply_gap_close(plan);expected=deepcopy(original)
    for category,name,follows in [('clip','clips',None),('caption','captions',captions),('sticker','overlays',stickers)]:
        actual={x.id:x for x in getattr(editor.project,name)}
        assert list(actual)==[x.id for x in getattr(original,name)]
        for item in getattr(expected,name):
            old_start=item.start
            moved=item.start>=15 and (item.track in tracks if category=='clip' else follows)
            if moved:item.start-=5
            assert actual[item.id]==item
            impact=next(x for x in plan.item_impacts if (x.category,x.item_id)==(category,item.id))
            assert (impact.previous_start,impact.start,impact.disposition)==(old_start,item.start,'move' if moved else 'fixed')
            if not moved:assert actual[item.id] is live[name][item.id]
    assert editor.project==expected and VERSION==43
    assert len(editor._undo)==1 and editor.revision==1
    assert Project.from_dict(expected.to_dict())==expected
    assert editor.undo() and editor.project==original
    assert editor.redo() and editor.project==expected


@pytest.mark.parametrize('playhead,valid',[(0,False),(9,False),(10,True),(14,True),(15,False),(24,False),(25,False),(100,False)])
def test_half_open_capture_and_no_leading_trailing_or_inside_clip(playhead,valid):
    editor=bare()
    if valid: assert (editor.plan_gap_close(0,playhead).start,editor.plan_gap_close(0,playhead).end)==(10,15)
    else:rejected(editor,lambda:editor.plan_gap_close(0,playhead),'No interior gap')


@pytest.mark.parametrize('kind',['empty','single','touching','leading'])
def test_no_synthetic_gap(kind):
    editor=bare()
    if kind=='empty':editor.project.clips=[]
    elif kind=='single':editor.project.clips=editor.project.clips[:1]
    elif kind=='touching':editor._clip('right0').start=10
    else:
        for c in editor.project.clips:c.start+=20
    rejected(editor,lambda:editor.plan_gap_close(0,10),'No interior gap')


def test_sorted_neighbors_one_frame_gap_and_only_one_of_multiple_gaps():
    editor=bare(); editor.project.media[0].has_audio=False
    editor._clip('right0').start=11
    editor.project.clips.append(Clip('later','m',0,25,100,10))
    editor.project.clips.reverse()
    plan=editor.plan_gap_close(0,10)
    assert (plan.start,plan.end,plan.duration,plan.left_clip_id,plan.right_clip_id)==(10,11,1,'left0','right0')
    editor.apply_gap_close(plan)
    assert [(c.id,c.start) for c in editor.project.clips]==[('later',24),('right0',10),('left0',0)]
    assert editor._clip('later').start-editor._clip('right0').end==4


@pytest.mark.parametrize('field,value',[('track',True),('track',-1),('track',4),('track',1.0),('track','0'),
    ('playhead',True),('playhead',-1),('playhead',1.5),('playhead',None),('playhead',10**15)])
def test_strict_target(field,value):
    args=dict(track=0,playhead=10);args[field]=value;editor=bare()
    rejected(editor,lambda:editor.plan_gap_close(**args))


@pytest.mark.parametrize('tracks',[(),[],[0],(1,),(0,0),(0,4),(True,),(0,False),'0',0])
def test_strict_participation(tracks):
    editor=sample();rejected(editor,lambda:editor.plan_gap_close(0,12,participating_tracks=tracks))


@pytest.mark.parametrize('field',['follow_captions','follow_stickers'])
@pytest.mark.parametrize('value',[0,1,None,[],'true'])
def test_strict_follower_flags(field,value):
    editor=sample();rejected(editor,lambda:editor.plan_gap_close(0,12,**{field:value}))


@pytest.mark.parametrize('category',['clip','caption','sticker'])
@pytest.mark.parametrize('start,duration',[(0,10),(5,5),(15,1),(16,5),(9,2),(10,1),(11,2),(14,1),(14,2),(10,5),(5,20)])
def test_any_intersection_including_contained_rejects_unchecked_exactly_fixed(category,start,duration):
    editor=bare();item=(Clip('item','m',1,start,0,duration) if category=='clip' else
        Caption('item',start,duration,'text') if category=='caption' else Overlay('item','/x.png','c'*64,start,duration))
    name={'clip':'clips','caption':'captions','sticker':'overlays'}[category]
    getattr(editor.project,name).append(item)
    args=dict(participating_tracks=(0,1),follow_captions=True,follow_stickers=True)
    if start<15 and start+duration>10:
        rejected(editor,lambda:editor.plan_gap_close(0,12,**args),'intersects the gap')
    else:
        editor.apply_gap_close(editor.plan_gap_close(0,12,**args))
        assert next(x for x in getattr(editor.project,name) if x.id=='item').start==start-(5 if start>=15 else 0)
    fresh=bare();getattr(fresh.project,name).append(deepcopy(item));old=deepcopy(item)
    fresh.apply_gap_close(fresh.plan_gap_close(0,12))
    assert next(x for x in getattr(fresh.project,name) if x.id=='item')==old


@pytest.mark.parametrize('field,value',[('left_clip_id','other'),('right_clip_id','other'),('track',1),('playhead',11),
    ('start',9),('end',16),('candidate_signature','bad'),('project_signature','bad'),('revision',True),('fps_num',True),
    ('participating_tracks',(0,1)),('follow_captions',True),('follow_stickers',True),('item_impacts',()),
    ('participating_tracks',()),('follow_captions',1),('session_id',None),('left_clip_id',1)])
def test_forged_plan_including_other_playhead_in_same_gap_rejects(field,value):
    editor=bare();plan=editor.plan_gap_close(0,12)
    rejected(editor,lambda:editor.apply_gap_close(replace(plan,**{field:value})))


def test_impact_bool_forgery_and_reordered_tracks_reject():
    editor=sample();plan=editor.plan_gap_close(0,12,participating_tracks=(0,1))
    bad=replace(plan.item_impacts[0],track=False)
    rejected(editor,lambda:editor.apply_gap_close(replace(plan,item_impacts=(bad,*plan.item_impacts[1:]))))
    rejected(editor,lambda:editor.apply_gap_close(replace(plan,participating_tracks=(1,0))))


@pytest.mark.parametrize('kind',['edit','undo_aba','redo_aba','lock','relink','new_session','direct','repeat',
    'replace_neighbor_same_bounds','insert_neighbor','move_neighbor'])
def test_stale_and_neighbor_identity_are_bound(kind):
    editor=sample();plan=editor.plan_gap_close(0,12)
    if kind=='edit':editor.update_clip('right1',gain_db=-7)
    elif kind=='undo_aba':editor.update_clip('right1',gain_db=-7);editor.undo()
    elif kind=='redo_aba':editor.update_clip('right1',gain_db=-7);editor.undo();editor.redo()
    elif kind=='lock':editor.set_track_edit_lock(0,True)
    elif kind=='relink':editor.project.media[0].path='/changed.mov'
    elif kind=='new_session':editor.project=deepcopy(editor.project)
    elif kind=='direct':editor.project.captions[0].text='changed'
    elif kind=='repeat':editor.apply_gap_close(plan)
    elif kind=='replace_neighbor_same_bounds':editor._clip('right0').id='replacement'
    elif kind=='insert_neighbor':editor.project.clips.append(Clip('inside','m',0,11,100,1))
    else:editor._clip('right0').start=16
    rejected(editor,lambda:editor.apply_gap_close(plan),'stale')


@pytest.mark.parametrize('kind',['target_lock','follower_lock','group','transition'])
def test_moving_relationships_fail_closed(kind):
    editor=sample()
    if kind=='target_lock':editor.set_track_edit_lock(0,True)
    elif kind=='follower_lock':editor.set_track_edit_lock(1,True)
    elif kind=='group':editor.group_clips(['right1','right2'])
    else:
        editor._clip('right1').source_in=30
        editor.project.clips.append(Clip('pair','m',1,editor._clip('right1').end,50,10))
        editor.project.video_transitions=[VideoTransition('tr','right1','pair',4)]
    rejected(editor,lambda:editor.plan_gap_close(0,12,participating_tracks=(0,1)))


def test_fixed_lock_group_and_transition_are_allowed():
    editor=sample();editor.project.clips=[c for c in editor.project.clips if c.id!='right1']
    editor._clip('left1').duration=5;editor._clip('left1').source_in=20
    editor.project.clips.append(Clip('fixedpair','m',1,5,30,5))
    editor.project.video_transitions=[VideoTransition('tr','left1','fixedpair',2)]
    editor.group_clips(['left2','left3']);editor.set_track_edit_lock(1,True)
    before=deepcopy(editor.project)
    editor.apply_gap_close(editor.plan_gap_close(0,12,participating_tracks=(0,1,2,3)))
    assert editor.project.video_transitions==before.video_transitions
    assert editor.project.clip_groups==before.clip_groups
    assert editor.project.track_edit_locks==before.track_edit_locks


def test_whole_complex_retimed_long_translation_has_no_decode_or_source_cut_budget(monkeypatch):
    editor=bare();clip=editor._clip('right0');clip.duration=1200
    clip.source_in=Fraction(5,4);clip.time_map=ClipTimeMap(Fraction(5,4),Fraction(7205,4),Fraction(3,2))
    clip.effects=[ClipEffect('blur','blur',1.5)];clip.transform=ClipTransform(x=3,y=-2,scale=1.1)
    clip.motion_keyframes=[ClipMotionKeyframe(0),ClipMotionKeyframe(1000,2,3,1.2,.8)]
    old=deepcopy(clip)
    def forbidden(*args,**kwargs):raise AssertionError('gap planning/apply must never decode or spawn subprocesses')
    monkeypatch.setattr('subprocess.run',forbidden);monkeypatch.setattr('subprocess.Popen',forbidden)
    editor.apply_gap_close(editor.plan_gap_close(0,12))
    assert editor._clip('right0')==replace(old,start=10)


def test_same_track_common_sample_shift_even_when_each_local_length_equals_8008():
    fps=Fraction(30000,1001);editor=bare(fps)
    editor.project.clips=[Clip('left','m',0,0,0,1),Clip('right','m',0,2,0,5),Clip('later','m',0,8,10,5)]
    for old in editor.project.clips[1:]:
        guard_local_audio_translation(old,replace(old,start=old.start-1),editor.project,operation='test')
    assert sample_boundary(1,fps)-sample_boundary(2,fps)==-1601
    assert sample_boundary(7,fps)-sample_boundary(8,fps)==-1602
    rejected(editor,lambda:editor.plan_gap_close(0,1),'relative audio synchronization')
    # This new promise must not alter accepted local-only legacy ripple policy.
    editor.project.clips.append(Clip('selected','m',0,1,0,1))
    editor.ripple_delete_clip('selected')
    assert editor._clip('right').start==1 and editor._clip('later').start==7


@pytest.mark.parametrize('kind',['muted','no_audio','compiled_silent'])
def test_inaudible_follower_has_no_common_sample_constraint(kind):
    editor=bare(Fraction(30000,1001));editor.project.clips=[Clip('left','m',0,0,0,1),Clip('right','m',0,2,0,5),Clip('later','m',0,8,10,5)]
    if kind=='muted':editor._clip('later').muted=True
    elif kind=='no_audio':editor.project.media[0].has_audio=False
    else:editor.project.media[0].audio_offset=50
    editor.apply_gap_close(editor.plan_gap_close(0,1));assert editor._clip('later').start==7


def test_three_collections_final_guard_failure_rolls_back_history_and_redo(monkeypatch):
    editor=sample();editor.update_clip('right0',gain_db=-5);editor.undo()
    plan=editor.plan_gap_close(0,12,participating_tracks=(0,1,2,3),follow_captions=True,follow_stickers=True)
    before=snapshot(editor);seen=[]
    def fail(_):
        seen.append((editor._clip('right3').start,editor.project.captions[1].start,editor.project.overlays[1].start))
        raise ValueError('injected final guard')
    monkeypatch.setattr(editor,'_guard_clip_group_edit',fail)
    with pytest.raises(ValueError,match='injected final guard'):editor.apply_gap_close(plan)
    assert seen==[(35,15,25)] and snapshot(editor)==before


@pytest.mark.parametrize('asset_directory', ['assets', '素材 with spaces'])
def test_extent_all_collections_save_reopen_recovery_and_single_undo(tmp_path, asset_directory):
    editor=sample();editor.project.overlays.append(Overlay('anchor','/anchor.png','c'*64,100,10))
    # Normalize fixtures before the undo snapshot; loading resolves references
    # using the host platform's drive and separators. Assets remain missing.
    assets = (tmp_path / asset_directory).resolve()
    for media in editor.project.media:
        media.path = str(assets / f'{media.id}.mov')
    for overlay in editor.project.overlays:
        overlay.asset_path = str(assets / f'{overlay.id}.png')
    before=deepcopy(editor.project);assert before.duration_frames==110
    editor.apply_gap_close(editor.plan_gap_close(0,12,participating_tracks=(0,1,2,3),follow_captions=True))
    assert editor.project.duration_frames==110
    path=tmp_path/'gap.lumen';editor.project.save(path);assert Project.load(path)==editor.project
    store=RecoveryStore(tmp_path/'recovery')
    try:
        entry=store.write(editor.project,str(uuid4()),path);assert store.load(entry)==editor.project
    finally:store.close()
    assert len(editor._undo)==1 and editor.undo() and editor.project==before


def test_owner_thread_and_live_normalization_are_not_bypassed():
    editor=bare();editor.project.master_gain_db=0
    plan=editor.plan_gap_close(0,12)
    assert type(editor.project.master_gain_db) is int
    errors=[]
    def other_thread():
        for action in (lambda:editor.plan_gap_close(0,12),lambda:editor.apply_gap_close(plan)):
            try:action()
            except ValueError as exc:errors.append(str(exc))
    t=Thread(target=other_thread);t.start();t.join()
    assert len(errors)==2 and all('owning thread' in text for text in errors)
    assert type(editor.project.master_gain_db) is int and not editor._undo

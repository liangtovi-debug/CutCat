"""Whole-clip copy semantics, narrow transactions and detached dependency checks."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
import hashlib
import os
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

import pytest

from lumen.clip_copy_paste import (prepare_clip_copy, verify_clip_copy, accept_clip_copy,
    plan_clip_paste, verify_clip_paste, apply_clip_paste, ClipCopyCancelled)
from lumen.model import (Caption, Clip, ClipEffect, ClipGroup, Editor, Grade, Media,
                         Overlay, Project, VERSION, VideoTransition, MAX_CAPTION_FRAME)
from lumen.clip_motion import ClipMotionKeyframe
from lumen.qualifier import WindowKeyframe
from lumen.timing import ClipTimeMap
from lumen.timeline_markers import TimelineMarker
from test_track_edit_locks_model import rejected, snapshot


def sample(tmp_path, fps=Fraction(24)):
    path = tmp_path / 'media.mov'; path.write_bytes(b'local-media-content' * 20)
    media = Media('m', str(path), 'Source', 1000, 64, 48, True, True,
                  fps.numerator, fps.denominator)
    project = Project(fps_num=fps.numerator, fps_den=fps.denominator, media=[media], clips=[
        Clip('a', 'm', 0, 10, 30, 10, gain_db=-3, fade_in=2, fade_out=3),
        Clip('b', 'm', 1, 15, 60, 8), Clip('c', 'm', 2, 12, 20, 5),
        Clip('d', 'm', 3, 30, 50, 5), Clip('e', 'm', 0, 50, 70, 5)],
        captions=[Caption('caption', 3, 10, 'unchanged')],
        overlays=[Overlay('sticker', str((tmp_path / 'not-copied.png').resolve()), 'a'*64, 40, 5)],
        timeline_markers=[TimelineMarker(id='marker', start_frame=9, name='fixed', color='yellow', comment='')])
    return Editor(project)


def copy(editor, ids=('a',)):
    request = prepare_clip_copy(editor, ids)
    return accept_clip_copy(editor, request, verify_clip_copy(request))


def paste(editor, clipboard, destination=100):
    plan = plan_clip_paste(editor, clipboard, destination)
    return apply_clip_paste(editor, clipboard, plan, verify_clip_paste(plan, clipboard))


def test_copy_is_deep_frozen_no_history_mutation_and_copy_locked_source(tmp_path):
    editor = sample(tmp_path); editor.set_track_edit_lock(0, True)
    before = snapshot(editor); original = editor._clip('a')
    clipboard = copy(editor)
    assert snapshot(editor) == before and clipboard.anchor == 10 and clipboard.clip_count == 1
    with pytest.raises(FrozenInstanceError): clipboard.request.payload = 'changed'
    editor.set_track_edit_lock(0, False)
    editor._clip('a').grade.lift[0] = .2
    editor._clip('a').start = 25
    (clone,) = paste(editor, clipboard)
    assert clone.start == 100 and clone.grade.lift == [0,0,0]
    assert clone.grade is not original.grade
    assert clone.grade.lift is not original.grade.lift


def test_multiple_groups_scattered_clips_new_identities_one_undo_redo_save_recovery(tmp_path):
    from lumen.project_recovery import RecoveryStore
    editor = sample(tmp_path)
    editor.group_clips(['a','b']); editor.group_clips(['c','d'])
    before = snapshot(editor); live = list(editor.project.clips)
    clipboard = copy(editor, ('a','c','e'))
    assert clipboard.clip_count == 5 and snapshot(editor) == before
    result = paste(editor, clipboard)
    expected = deepcopy(editor.project)
    assert len(result) == 5 and editor.revision == before[3] + 1
    assert all(a is b for a,b in zip(live, editor.project.clips))
    assert [c.start for c in result] == [100,105,102,120,140]
    for old, clone in zip(before[0].clips, result):
        value = deepcopy(old); value.id = clone.id; value.start += 90
        assert value == clone
    groups = editor.project.clip_groups
    assert groups[:2] == before[0].clip_groups
    assert len({g.id for g in groups}) == 4
    assert groups[2].clip_ids == [result[0].id,result[1].id]
    assert groups[3].clip_ids == [result[2].id,result[3].id]
    assert editor.project.captions == before[0].captions
    assert editor.project.overlays == before[0].overlays
    assert editor.project.timeline_markers == before[0].timeline_markers
    assert editor.project.source_ranges == before[0].source_ranges and VERSION == 43
    assert Project.from_dict(editor.project.to_dict()) == expected
    assert editor.undo() and editor.project == before[0]
    assert editor.redo() and editor.project == expected
    path=tmp_path/'saved.lumen';editor.project.save(path); assert Project.load(path)==expected
    store=RecoveryStore(tmp_path/'recover')
    try:
        entry=store.write(editor.project,str(uuid4()),path)
        assert store.load(entry) == expected
    finally:store.close()


def test_full_complex_parameters_time_map_hidden_keys_effect_ids_and_input_encoding(tmp_path):
    editor=sample(tmp_path);clip=editor._clip('a')
    clip.time_map=ClipTimeMap(Fraction(5,4),Fraction(79,4),Fraction(2));clip.source_in=Fraction(5,4)
    clip.duration=10
    clip.grade.input_encoding='linear_rec709';clip.grade.exposure=.8
    clip.effects=[ClipEffect('effect1','blur',2),ClipEffect('effect2','vignette',.3,False)]
    clip.motion_keyframes=[ClipMotionKeyframe(-5,x=2),ClipMotionKeyframe(99,x=20,opacity=.6)]
    clip.grade.qualifier.window_keyframes=[WindowKeyframe(-4),WindowKeyframe(80,center_x=.7)]
    clip.transform.scale=.8;clip.composite.opacity=.6;clip.stabilization.strength=.4
    clip.restoration.denoise=.5
    original=deepcopy(clip);clipboard=copy(editor);(clone,)=paste(editor,clipboard)
    for a,b in zip(original.effects,clone.effects): assert a.id != b.id;a.id=b.id
    original.id=clone.id;original.start=clone.start
    assert original==clone and clone.time_map==ClipTimeMap(Fraction(5,4),Fraction(79,4),Fraction(2))
    clone.effects[0].amount=4;clone.grade.qualifier.window_keyframes=[]
    assert editor._clip('a').effects[0].amount==2
    assert len(editor._clip('a').grade.qualifier.window_keyframes)==2


@pytest.mark.parametrize('ids',[[],['missing'],['caption'],['sticker'],'a',None,[True]])
def test_invalid_selection_does_not_mutate(tmp_path,ids):
    editor=sample(tmp_path);rejected(editor,lambda:prepare_clip_copy(editor,ids))


@pytest.mark.parametrize('destination',[True,False,-1,1.0,None,'100',MAX_CAPTION_FRAME,MAX_CAPTION_FRAME+1])
def test_destination_validation_atomic(tmp_path,destination):
    editor=sample(tmp_path);clipboard=copy(editor)
    rejected(editor,lambda:plan_clip_paste(editor,clipboard,destination))


@pytest.mark.parametrize('destination',[0,1,9,10,11,19,45,50,54])
def test_collision_never_overwrites_ripples_or_seeks_free_gap(tmp_path,destination):
    editor=sample(tmp_path);clipboard=copy(editor)
    if destination==0: # [0,10) is exactly adjacent and valid.
        result=paste(editor,clipboard,0); assert result[0].start==0
    else:rejected(editor,lambda:plan_clip_paste(editor,clipboard,destination),'overlap')


def test_paste_repeated_ids_independent_and_redo_cleared_only_on_success(tmp_path):
    editor=sample(tmp_path);clipboard=copy(editor);a=paste(editor,clipboard,100)[0]
    b=paste(editor,clipboard,120)[0]; assert a.id != b.id
    assert editor.undo(); state=snapshot(editor)
    rejected(editor,lambda:plan_clip_paste(editor,clipboard,100),'overlap')
    assert snapshot(editor)==state and editor.can_redo
    c=paste(editor,clipboard,140)[0]
    assert c.id not in (a.id,b.id) and not editor.can_redo


@pytest.mark.parametrize('track',range(4))
def test_every_destination_track_lock_atomic(tmp_path,track):
    editor=sample(tmp_path);clipboard=copy(editor,('a','b','c','d'))
    editor.set_track_edit_lock(track,True)
    rejected(editor,lambda:plan_clip_paste(editor,clipboard,100),'edit-locked')


def test_current_revision_since_copy_allowed_but_pending_plan_stales(tmp_path):
    editor=sample(tmp_path);clipboard=copy(editor)
    editor._commit(lambda:setattr(editor._clip('b').grade,'exposure',.4))
    plan=plan_clip_paste(editor,clipboard,100);receipt=verify_clip_paste(plan,clipboard)
    editor._commit(lambda:setattr(editor._clip('c'),'gain_db',-4))
    rejected(editor,lambda:apply_clip_paste(editor,clipboard,plan,receipt),'stale')
    assert paste(editor,clipboard,100)


@pytest.mark.parametrize('change',['undo','redo','replace','direct','lock','fps'])
def test_pending_plan_rejects_revision_session_signature_races(tmp_path,change):
    editor=sample(tmp_path);clipboard=copy(editor)
    editor._commit(lambda:setattr(editor._clip('a').grade,'exposure',.1))
    if change=='redo':editor.undo()
    plan=plan_clip_paste(editor,clipboard,100);receipt=verify_clip_paste(plan,clipboard)
    if change=='undo':editor.undo()
    elif change=='redo':editor.redo()
    elif change=='replace':editor.project=deepcopy(editor.project)
    elif change=='direct':editor._clip('b').grade.exposure=.6
    elif change=='lock':editor.set_track_edit_lock(0,True)
    elif change=='fps':editor.project.fps_num=30
    rejected(editor,lambda:apply_clip_paste(editor,clipboard,plan,receipt),'stale')


@pytest.mark.parametrize('change',['missing','relink','interpretation','fps','session'])
def test_snapshot_dependency_context_cannot_be_rebased(tmp_path,change):
    editor=sample(tmp_path);clipboard=copy(editor)
    if change=='missing':editor.project.clips=[];editor.project.media=[]
    elif change=='relink':editor.project.media[0].path='/other.mov'
    elif change=='interpretation':editor.project.media[0].input_encoding='apple_log_bt2020'
    elif change=='fps':editor.project.fps_num=30
    elif change=='session':editor.project=deepcopy(editor.project)
    rejected(editor,lambda:plan_clip_paste(editor,clipboard,100))


@pytest.mark.parametrize('field',['destination','new_clip_ids','new_group_ids','new_effect_ids','candidate_signature','copy_token','token'])
def test_forged_plan_not_equal_to_verification(tmp_path,field):
    editor=sample(tmp_path);clipboard=copy(editor)
    plan=plan_clip_paste(editor,clipboard,100);receipt=verify_clip_paste(plan,clipboard)
    values={'destination':120,'new_clip_ids':('fresh',),'new_group_ids':('g',),'new_effect_ids':(('e',),),
            'candidate_signature':'bad','copy_token':'bad','token':'bad'}
    bad=replace(plan,**{field:values[field]})
    rejected(editor,lambda:apply_clip_paste(editor,clipboard,bad,receipt),'stale')


def test_new_copy_cannot_apply_previous_plan_and_request_stale(tmp_path):
    editor=sample(tmp_path);a=copy(editor);plan=plan_clip_paste(editor,a,100);proof=verify_clip_paste(plan,a)
    b=copy(editor)
    rejected(editor,lambda:apply_clip_paste(editor,b,plan,proof),'stale')
    request=prepare_clip_copy(editor,['a']);verified=verify_clip_copy(request)
    editor._commit(lambda:setattr(editor._clip('a').grade,'exposure',.2))
    rejected(editor,lambda:accept_clip_copy(editor,request,verified),'stale')


def test_original_endpoint_or_group_expanded_endpoint_refuses(tmp_path):
    editor=sample(tmp_path);editor.project.clips=[Clip('a','m',0,10,10,10),Clip('b','m',0,20,30,10),Clip('c','m',1,5,10,5)]
    editor.project.video_transitions=[VideoTransition('transition','a','b',4)]
    editor.project.validate()
    rejected(editor,lambda:prepare_clip_copy(editor,['a']),'transition endpoint')
    rejected(editor,lambda:prepare_clip_copy(editor,['b']),'transition endpoint')
    editor.group_clips(['a','c'])
    rejected(editor,lambda:prepare_clip_copy(editor,['c']),'transition endpoint')


def test_narrow_group_append_rejects_hijacking_old_clips_and_generic_bypass(tmp_path):
    editor=sample(tmp_path);fresh=Clip('new','m',0,100,0,5)
    rejected(editor,lambda:editor._commit_clip_paste([fresh],[ClipGroup('bad',['a','new'])]),'only new clips')
    rejected(editor,lambda:editor._commit(lambda:editor.project.clip_groups.append(ClipGroup('bad',['a','b']))),'Group/Ungroup')
    editor.group_clips(['a','b']);old=deepcopy(editor.project)
    rejected(editor,lambda:editor._commit_clip_paste([fresh],[ClipGroup('bad',['new','missing'])]))
    assert editor.project==old


@pytest.mark.parametrize('kind',['delete','replace','content','same_mtime','lut_delete','lut_content','lut_same_mtime'])
def test_dependency_change_detected_without_mutation(tmp_path,kind):
    editor=sample(tmp_path);lut=tmp_path/'look.cube';lut.write_bytes(b'LUT-content')
    editor._clip('a').grade.lut_path=str(lut)
    clipboard=copy(editor);plan=plan_clip_paste(editor,clipboard,100)
    path=lut if kind.startswith('lut') else Path(editor.project.media[0].path)
    before=path.stat();data=path.read_bytes()
    if 'delete' in kind:path.unlink()
    elif kind=='replace':path.unlink();path.write_bytes(data)
    else:
        path.write_bytes(bytes([data[0]^1])+data[1:])
        if 'mtime' in kind:os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
    state=snapshot(editor)
    with pytest.raises((OSError,ValueError)):verify_clip_paste(plan,clipboard)
    assert snapshot(editor)==state


def test_copy_missing_and_symlink_lut_fail_without_state_mutation(tmp_path):
    editor=sample(tmp_path);request=prepare_clip_copy(editor,['a'])
    Path(editor.project.media[0].path).unlink()
    with pytest.raises(OSError):verify_clip_copy(request)
    editor=sample(tmp_path);link=tmp_path/'linked.cube';link.symlink_to(editor.project.media[0].path)
    editor._clip('a').grade.lut_path=str(link)
    with pytest.raises(ValueError,match='plain local LUT'):verify_clip_copy(prepare_clip_copy(editor,['a']))


def test_cancellation_timeout_dependency_budget_and_two_pass_change(tmp_path,monkeypatch):
    import lumen.clip_copy_paste as module
    editor=sample(tmp_path);request=prepare_clip_copy(editor,['a']);cancel=Event();cancel.set()
    with pytest.raises(ClipCopyCancelled):verify_clip_copy(request,cancel)
    monkeypatch.setattr(module,'MAX_VERIFY_SECONDS',-1)
    with pytest.raises(ValueError,match='budget'):verify_clip_copy(request)
    monkeypatch.setattr(module,'MAX_VERIFY_SECONDS',60)
    monkeypatch.setattr(module,'MAX_DEPENDENCY_BYTES',1)
    with pytest.raises(ValueError,match='read budget'):verify_clip_copy(request)
    monkeypatch.setattr(module,'MAX_DEPENDENCY_BYTES',256*1024*1024)
    identity=module._identity;calls=[]
    def changed(*args):
        result=identity(*args);calls.append(result)
        if len(calls)==1:Path(editor.project.media[0].path).write_bytes(b'changed')
        return result
    monkeypatch.setattr(module,'_identity',changed)
    with pytest.raises(ValueError,match='changed during'):verify_clip_copy(request)


def test_owner_only_capture_plan_apply_worker_verifies_no_live_editor(tmp_path):
    editor=sample(tmp_path);clipboard=copy(editor);plan=plan_clip_paste(editor,clipboard,100)
    results=[]
    def worker():
        results.append(verify_clip_paste(plan,clipboard))
        for call in (lambda:prepare_clip_copy(editor,['a']),lambda:plan_clip_paste(editor,clipboard,100),
                     lambda:apply_clip_paste(editor,clipboard,plan,results[0])):
            try:call()
            except ValueError as exc:results.append(str(exc))
    thread=Thread(target=worker);thread.start();thread.join(10)
    assert not thread.is_alive() and len(results)==4
    assert all('owning thread' in value for value in results[1:])
    assert apply_clip_paste(editor,clipboard,plan,results[0])


def test_capture_and_plan_and_apply_do_no_file_io(tmp_path,monkeypatch):
    editor=sample(tmp_path);clipboard=copy(editor)
    plan=plan_clip_paste(editor,clipboard,100);receipt=verify_clip_paste(plan,clipboard)
    def forbidden(*args,**kwargs):raise AssertionError('GUI path must not read files')
    for name in ('open','stat','lstat','resolve','read_bytes','read_text'):
        monkeypatch.setattr(Path,name,forbidden)
    request=prepare_clip_copy(editor,['a'])
    assert request and plan_clip_paste(editor,clipboard,120)
    assert apply_clip_paste(editor,clipboard,plan,receipt)


def test_original_clips_can_be_deleted_after_copy_but_media_reference_remains(tmp_path):
    editor=sample(tmp_path);editor.group_clips(['a','b']);clipboard=copy(editor,['a'])
    editor.delete_clips(['a','b'])
    clones=paste(editor,clipboard,100)
    assert len(clones)==2 and len(editor.project.clip_groups)==1
    assert [c.source_in for c in clones]==[30,60]


def test_lut_actual_read_loop_cannot_exceed_shared_budget(tmp_path,monkeypatch):
    import lumen.clip_copy_paste as module
    editor=sample(tmp_path);path=tmp_path/'large.cube';path.write_bytes(b'0'*(2*1024*1024))
    editor._clip('a').grade.lut_path=str(path)
    request=prepare_clip_copy(editor,['a'])
    maximum=3*65536+1024*1024+1
    monkeypatch.setattr(module,'MAX_DEPENDENCY_BYTES',maximum)
    real_open=Path.open;read=[]
    class Counted:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):self.stream.__enter__();return self
        def __exit__(self,*args):return self.stream.__exit__(*args)
        def fileno(self):return self.stream.fileno()
        def read(self,count):
            read.append(count);return self.stream.read(count)
    def wrapped(self,*args,**kwargs):
        stream=real_open(self,*args,**kwargs)
        return Counted(stream) if self==path else stream
    monkeypatch.setattr(Path,'open',wrapped)
    with pytest.raises(ValueError,match='read budget'):verify_clip_copy(request)
    assert read==[1024*1024] and sum(read)<maximum


@pytest.mark.parametrize('limit',['clips','payload','dependencies','lut-size'])
def test_documented_resource_limits_reject_atomically(tmp_path,monkeypatch,limit):
    import lumen.clip_copy_paste as module
    editor=sample(tmp_path);before=snapshot(editor)
    if limit=='clips':
        monkeypatch.setattr(module,'MAX_COPY_CLIPS',1)
        rejected(editor,lambda:prepare_clip_copy(editor,['a','b']),'selected timeline clips')
    elif limit=='payload':
        monkeypatch.setattr(module,'MAX_COPY_PAYLOAD_BYTES',1)
        rejected(editor,lambda:prepare_clip_copy(editor,['a']),'parameter budget')
    else:
        path=tmp_path/'bounded.cube';path.write_bytes(b'LUT')
        editor._clip('a').grade.lut_path=str(path);before=snapshot(editor)
        request=prepare_clip_copy(editor,['a'])
        if limit=='dependencies':monkeypatch.setattr(module,'MAX_COPY_DEPENDENCIES',1)
        else:
            with path.open('wb') as stream:stream.truncate(module.MAX_LUT_BYTES+1)
        with pytest.raises(ValueError,match='budget'):verify_clip_copy(request)
    assert snapshot(editor)==before


def test_narrow_append_rollback_after_final_collision_validation(tmp_path):
    editor=sample(tmp_path);editor.group_clips(['a','b'])
    first=Clip('new-a','m',0,100,0,5);second=Clip('new-b','m',0,102,10,5)
    rejected(editor,lambda:editor._commit_clip_paste([first,second],[ClipGroup('new-group',['new-a','new-b'])]),'overlap')
    assert not any(c.id.startswith('new') for c in editor.project.clips)

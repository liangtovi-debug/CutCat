"""Persistent role-explicit A/V links, exact pair edits and central fences."""
from native_path_fixture import bind_missing_project_paths
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, fields, replace
from fractions import Fraction
import json
from threading import Thread

import pytest

from lumen.model import (AVLink, AudioMixAnchor, Caption, Clip, ClipEffect, ClipGroup,
    Editor, Grade, Media, Overlay, Project, VERSION, VideoTransition)
from lumen.audio_gain import AudioGainKeyframe
from lumen.clip_motion import ClipMotionKeyframe
from lumen.qualifier import WindowKeyframe
from lumen.timeline_markers import TimelineMarker
from lumen.timing import ClipTimeMap, sample_boundary


def sample(fps=Fraction(24), mapping=None, *, link=False):
    mapping = mapping or ClipTimeMap(Fraction(21, 4), Fraction(95, 4), Fraction(2))
    from lumen.timing import duration_frames
    video = Clip('v','m',0,5,mapping.source_in,duration_frames(mapping),
        time_map=mapping, source_audio_enabled=False, gain_db=7, muted=True,
        audio_mix_anchor=AudioMixAnchor(0,2,'old-v'), fade_in=2, fade_out=3)
    video.motion_keyframes=[ClipMotionKeyframe(-4,x=2), ClipMotionKeyframe(70,x=8)]
    video.grade.qualifier.window_keyframes=[WindowKeyframe(-3), WindowKeyframe(60,center_x=.7)]
    video.audio_gain_keyframes=[AudioGainKeyframe(-300,-8),AudioGainKeyframe(300000,-3)]
    video.effects=[ClipEffect('fx','blur',2)]
    audio=Clip('a','m',2,5,mapping.source_in,duration_frames(mapping),time_map=mapping,
        gain_db=-4,fade_in=2,fade_out=3,audio_mix_anchor=AudioMixAnchor(0,2,'old-v'),
        audio_gain_keyframes=[AudioGainKeyframe(-200,-7), AudioGainKeyframe(800000,3)])
    audio.grade.exposure=.7  # Stored/hidden audio-side picture fields remain intact.
    media=Media('m','/not-opened.mov','Source',1000,64,48,True,True,fps.numerator,fps.denominator)
    project=Project(fps_num=fps.numerator,fps_den=fps.denominator,media=[media],
        clips=[video,audio,Clip('other','m',1,80,30,5)],
        captions=[Caption('caption',4,7,'fixed')],
        overlays=[Overlay('overlay','/not-opened.png','a'*64,3,8)],
        timeline_markers=[TimelineMarker(id='mark',name='fixed',start_frame=4,color='blue',comment='')])
    editor=Editor(project)
    if link:editor.link_av_clips('v','a')
    return editor


def state(editor):
    return deepcopy((editor.project,editor._undo,editor._redo,editor.revision,editor.session_id))


@pytest.mark.parametrize('linked', [False, True])
def test_unrelated_delete_preserves_generic_commit_dispatch_and_pair(linked):
    from unittest.mock import Mock
    editor = sample(link=linked)
    before = deepcopy(editor.project)
    revision = editor.revision
    undo_count = len(editor._undo)
    editor._commit = Mock(wraps=editor._commit)
    editor.delete_clips(['other'])
    expected = deepcopy(before)
    expected.clips = [clip for clip in expected.clips if clip.id != 'other']
    assert editor._commit.call_count == 1
    assert editor.project == expected
    assert editor.revision == revision + 1 and len(editor._undo) == undo_count + 1
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == expected


def rejected(editor, change, pattern=None):
    before=state(editor)
    with pytest.raises(ValueError,match=pattern):change()
    assert state(editor)==before


def test_schema42_metadata_only_link_unlink_exact_history_and_roundtrip(tmp_path):
    editor=sample(); bind_missing_project_paths(editor.project, tmp_path);before=deepcopy(editor.project);assert VERSION==43
    assert fields(Project)[-1].name=='av_links'
    link=editor.link_av_clips('v','a');assert len(editor._undo)==1 and editor.revision==1
    assert link.clip_ids==('v','a') and link.media_id=='m' and link.alignment=='same-source-zero-offset-v1'
    with pytest.raises(FrozenInstanceError):link.media_id='other'
    expected=deepcopy(before);expected.av_links=[link];assert editor.project==expected
    assert editor.expanded_clip_ids(['v'])==['v','a']
    assert editor.expanded_clip_ids(['a','other'])==['v','a','other']
    assert Project.from_dict(expected.to_dict())==expected
    path=tmp_path/'linked.lumen';editor.project.save(path);assert Project.load(path)==expected
    editor.undo();assert editor.project==before;editor.redo();assert editor.project==expected
    editor.unlink_av_clips(['a']);assert editor.project==before and not editor._clip('v').source_audio_enabled
    assert editor._clip('a').source_audio_enabled and len(editor._undo)==2
    editor.undo();assert editor.project==expected


def test_true_noop_link_unlink_slip_and_move_preserve_redo_revision():
    editor=sample(link=True);editor.update_clip('other',gain_db=-3);editor.undo()
    before=state(editor)
    assert editor.link_av_clips('v','a')==editor.project.av_links[0]
    editor.unlink_av_clips(['other'])
    editor.slip_linked_av('a',editor._clip('a').source_in)
    editor.move_clips(['v','a'],0)
    assert state(editor)==before and editor.can_redo


@pytest.mark.parametrize('fps',[Fraction(24),Fraction(24000,1001),Fraction(30000,1001),Fraction(60000,1001)])
@pytest.mark.parametrize('origin',['0','1/3','25/4','17.125'])
@pytest.mark.parametrize('role',['v','a'])
def test_joint_slip_preserves_full_partial_map_and_every_other_record(fps,origin,role):
    editor=sample(fps,link=True);old=deepcopy(editor.project);before=state(editor)
    oldmap=editor._clip('v').timing;new=Fraction(origin)
    expected=deepcopy(old)
    for clip in expected.clips[:2]:
        clip.time_map=ClipTimeMap(new,new+oldmap.source_out-oldmap.source_in,oldmap.speed)
        clip.source_in=new;clip._normalize_legacy_timing()
    result=editor.slip_linked_av(role,origin)
    assert result.id==role and editor.project==expected
    assert editor._clip('a').timing==editor._clip('v').timing
    assert editor._clip('a').timing.source_out-new==Fraction(37,2)
    assert sample_boundary(editor._clip('a').end,fps)-sample_boundary(editor._clip('a').start,fps)==sample_boundary(old.clips[1].end,fps)-sample_boundary(old.clips[1].start,fps)
    assert len(editor._undo)==len(before[1])+1 and editor.revision==before[3]+1
    editor.undo();assert editor.project==old;editor.redo();assert editor.project==expected


@pytest.mark.parametrize('origin',[-1,True,1.25,'1/1000001','999999999999999999999999999',None,{},'999999'])
def test_bad_joint_source_rejects_whole_pair(origin):
    editor=sample(link=True);rejected(editor,lambda:editor.slip_linked_av('v',origin))


@pytest.mark.parametrize('track',[0,2])
@pytest.mark.parametrize('operation',['link','unlink','slip','move','delete'])
def test_either_role_lock_rejects_all_changed_pair_operations(track,operation):
    editor=sample(link=operation!='link');editor.set_track_edit_lock(track,True)
    change={'link':lambda:editor.link_av_clips('v','a'), 'unlink':lambda:editor.unlink_av_clips(['v']),
        'slip':lambda:editor.slip_linked_av('a',10), 'move':lambda:editor.move_clips(['v','a'],20),
        'delete':lambda:editor.delete_clips(['v','a'])}[operation]
    rejected(editor,change,'[Ll]ock')


@pytest.mark.parametrize('role',['v','a'])
@pytest.mark.parametrize('operation',['move','delete','speed','trim','trim_source','trim_left','slip','track','media','duration','split'])
def test_all_independent_timing_paths_reject_and_restore_history(role,operation):
    editor=sample(link=True)
    change={'move':lambda:editor.move_clip(role,20), 'delete':lambda:editor.delete_clip(role),
        'speed':lambda:editor.set_clip_speed(role,1),'trim':lambda:editor.trim_clip(role,3,7),
        'trim_source':lambda:editor.trim_clip_source(role,3,20),'trim_left':lambda:editor.trim_clip_left(role,1),
        'slip':lambda:editor.slip_clip(role,3),'track':lambda:editor.update_clip(role,track=1 if role=='v' else 3),
        'media':lambda:editor.update_clip(role,media_id='unknown'), 'duration':lambda:editor.update_clip(role,duration=7),
        'split':lambda:editor.split_clip(role,8)}[operation]
    rejected(editor,change)


@pytest.mark.parametrize('mutation',['pair_move','pair_timing','pair_delete','link_delete','link_add','link_replace','fps','reconnect','group'])
def test_central_generic_transaction_cannot_smuggle_matching_pair_or_membership(mutation):
    editor=sample(link=True)
    def change():
        p=editor.project
        if mutation=='pair_move':
            for clip in p.clips[:2]:clip.start+=10
        elif mutation=='pair_timing':
            for clip in p.clips[:2]:Editor._set_clip_timing(clip,ClipTimeMap(10,30,2))
        elif mutation=='pair_delete':p.clips=p.clips[2:];p.av_links=[]
        elif mutation=='link_delete':p.av_links=[]
        elif mutation=='link_add':p.av_links.append(replace(p.av_links[0],id='new'))
        elif mutation=='link_replace':p.av_links[0]=replace(p.av_links[0],id='new')
        elif mutation=='fps':p.fps_num=30
        elif mutation=='reconnect':p.clips[0].source_audio_enabled=True;p.clips.remove(p.clips[1]);p.av_links=[]
        elif mutation=='group':p.clip_groups.append(ClipGroup('g',['v','a']))
    rejected(editor,lambda:editor._commit(change))


def test_explicit_role_parameter_edits_independent_other_member_locked_and_unlink_not_resync():
    editor=sample(link=True);editor.set_track_edit_lock(0,True);video=deepcopy(editor._clip('v'))
    editor.update_clip('a',gain_db=-10,muted=True,fade_out=1)
    assert editor._clip('v')==video and editor._clip('a').gain_db==-10
    editor.set_track_edit_lock(0,False);editor.unlink_av_clips(['v']);editor.slip_clip('a',8)
    rejected(editor,lambda:editor.link_av_clips('v','a'),'identical')
    assert not editor._clip('v').source_audio_enabled


def test_move_whole_pair_original_tracks_and_delete_without_hidden_cascade():
    editor=sample(link=True);old=deepcopy(editor.project)
    rejected(editor,lambda:editor.preview_move_clips(['v'],20),'both')
    rejected(editor,lambda:editor.move_clips(['a'],20),'both')
    rejected(editor,lambda:editor.delete_clips(['v']),'both')
    editor.move_clips(['v','a'],20);moved=deepcopy(editor.project)
    expected=deepcopy(old)
    for clip in expected.clips[:2]:clip.start+=20
    assert moved==expected
    editor.delete_clips(['v','a']);assert editor.project.clips==old.clips[2:] and editor.project.av_links==[]
    assert editor.project.media==old.media
    editor.undo();assert editor.project==moved;editor.undo();assert editor.project==old


def test_muted_A_cannot_bypass_full_local_audio_phase_guard():
    # 29.97 fps is 1601.6 samples/frame; this fade phase changes at +4.
    editor=sample(Fraction(30000,1001),ClipTimeMap(0,10),link=True)
    editor._clip('a').muted=True
    rejected(editor,lambda:editor.preview_move_clips(['v','a'],4),'sample-grid')
    rejected(editor,lambda:editor.move_clips(['v','a'],4),'sample-grid')
    editor.move_clips(['v','a'],5)
    assert editor._clip('a').muted and editor._clip('a').start==10


@pytest.mark.parametrize('operation',['insert','overwrite','gap','ripple','group','reattach'])
def test_affected_legacy_batch_plans_require_unlink(operation):
    editor=sample(mapping=ClipTimeMap(0,10),link=True)
    editor.project.clips.append(Clip('left','m',0,0,0,2))
    from lumen.source_audio_reattach import plan_source_audio_reattach
    change={'insert':lambda:editor.plan_source_insert('m',0,3,0,2,participating_tracks=(0,2)),
        'overwrite':lambda:editor.plan_source_overwrite('m',0,6,0,2),
        'gap':lambda:editor.plan_gap_close(0,3,participating_tracks=(0,2)),
        'ripple':lambda:editor.plan_ripple_delete('left',participating_tracks=(0,2)),
        'group':lambda:editor.group_clips(['v','a']),
        'reattach':lambda:plan_source_audio_reattach(editor,'v','a')}[operation]
    rejected(editor,change,'Unlink')


def test_unrelated_batch_edits_work_without_automatic_link_changes():
    editor=sample(mapping=ClipTimeMap(0,10),link=True);link=editor.project.av_links[0]
    editor.update_clip('other',gain_db=-5);editor.move_clip('other',90)
    editor.plan_source_insert('m',1,70,0,2)
    editor.plan_source_overwrite('m',1,90,0,2)
    assert editor.project.av_links==[link]


@pytest.mark.parametrize('field,value',[('id',''),('id',True),('video_clip_id','missing'),('audio_clip_id','v'),
    ('media_id','unknown'),('alignment','offset-v1'),('alignment',None)])
def test_schema_strict_invalid_link_fields(field,value):
    editor=sample(link=True);data=editor.project.to_dict();data['av_links'][0][field]=value
    before=deepcopy(data)
    with pytest.raises(ValueError):Project.from_dict(data)
    assert data==before


@pytest.mark.parametrize('value',[None,{},(),False,'links',[{}], [{'id':'l'}]])
def test_schema_strict_list_and_exact_wire_records(value):
    data=sample().project.to_dict();data['av_links']=value
    with pytest.raises(ValueError):Project.from_dict(data)


@pytest.mark.parametrize('field',['id','video_clip_id','audio_clip_id','media_id','alignment'])
def test_schema42_missing_or_unknown_fields_reject(field):
    # Retain the original node IDs; current-writer assertions migrated to 43.
    data=sample(link=True).project.to_dict();data['av_links'][0].pop(field)
    with pytest.raises(ValueError,match='fields'):Project.from_dict(data)
    data=sample(link=True).project.to_dict();data['av_links'][0]['future']=False
    with pytest.raises(ValueError,match='fields'):Project.from_dict(data)


@pytest.mark.parametrize('mutation',['duplicate','multi','wrong_roles','v_enabled','a_disabled','start','duration','map','group'])
def test_schema_relations_reject_drift_or_ambiguous_membership(mutation):
    p=sample(link=True).project
    if mutation=='duplicate':p.av_links.append(p.av_links[0])
    elif mutation=='multi':p.av_links.append(replace(p.av_links[0],id='other'))
    elif mutation=='wrong_roles':p.av_links[0]=replace(p.av_links[0],video_clip_id='a',audio_clip_id='v')
    elif mutation=='v_enabled':p.clips[0].source_audio_enabled=True
    elif mutation=='a_disabled':p.clips[1].source_audio_enabled=False
    elif mutation=='start':p.clips[1].start+=1
    elif mutation=='duration':Editor._set_clip_timing(p.clips[1],ClipTimeMap(0,5))
    elif mutation=='map':Editor._set_clip_timing(p.clips[1],ClipTimeMap(Fraction(25,4),Fraction(99,4),2))
    elif mutation=='group':p.clip_groups=[ClipGroup('g',['v','a'])]
    with pytest.raises(ValueError):p.validate()


def test_owner_thread_rejects_before_any_link_mutation():
    editor=sample();before=state(editor);errors=[]
    def work():
        try:editor.link_av_clips('v','a')
        except ValueError as error:errors.append(str(error))
    thread=Thread(target=work);thread.start();thread.join()
    assert errors and 'owning thread' in errors[0] and state(editor)==before

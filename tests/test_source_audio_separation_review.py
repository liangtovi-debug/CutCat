"""Independent adversarial review of the exact source-audio transaction boundary."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

import pytest

from lumen.model import AudioMixAnchor, Clip, Editor, Media, Project
from lumen.source_audio_separation import (plan_source_audio_separation,
    verify_source_audio_separation, apply_source_audio_separation)
from test_track_edit_locks_model import snapshot


def editor():
    return Editor(Project(media=[Media('m', '/unread-by-commit.mov', 'AV', 10,
        64, 48, True, True)], clips=[Clip('v', 'm', 0, 2, 1, 8)]))


def audio_for(e):
    c=e._clip('v')
    return Clip('new-audio', c.media_id, 2, c.start, c.source_in, c.duration,
        gain_db=c.gain_db, muted=c.muted, fade_in=c.fade_in, fade_out=c.fade_out,
        time_map=deepcopy(c.time_map), audio_mix_anchor=AudioMixAnchor(c.track,c.start,c.id))


def test_paste_primitive_cannot_mint_historical_handoff_anchor():
    e=editor(); candidate=audio_for(e); before=snapshot(e)
    with pytest.raises(ValueError): e._commit_clip_paste([candidate], [])
    assert snapshot(e)==before


@pytest.mark.parametrize('field,value', [
    ('source_audio_enabled',False), ('gain_db',-3), ('muted',True), ('fade_in',1),
    ('fade_out',1), ('start',3), ('source_in',2), ('duration',7),
    ('audio_mix_anchor',None), ('audio_mix_anchor',AudioMixAnchor(3,1,'other')),
    ('media_id','other'), ('track',0), ('id','v'),
])
def test_narrow_primitive_rejects_any_unapproved_contract(field,value):
    e=editor(); candidate=audio_for(e); setattr(candidate,field,value);before=snapshot(e)
    with pytest.raises(ValueError): e._commit_source_audio_separation('v',candidate)
    assert snapshot(e)==before


@pytest.mark.parametrize('picture', ['grade','transform','effects'])
def test_narrow_primitive_rejects_picture_settings(picture):
    from lumen.model import ClipEffect
    e=editor(); candidate=audio_for(e)
    if picture=='grade':candidate.grade.exposure=1
    elif picture=='transform':candidate.transform.scale=1.1
    else:candidate.effects=[ClipEffect('e','blur',1)]
    before=snapshot(e)
    with pytest.raises(ValueError):e._commit_source_audio_separation('v',candidate)
    assert snapshot(e)==before


@pytest.mark.parametrize('locked',[0,2])
def test_narrow_primitive_rechecks_both_locks(locked):
    e=editor();candidate=audio_for(e);e.set_track_edit_lock(locked,True);before=snapshot(e)
    with pytest.raises(ValueError):e._commit_source_audio_separation('v',candidate)
    assert snapshot(e)==before


def test_narrow_primitive_rechecks_collision_without_planner():
    e=editor();candidate=audio_for(e);e.project.clips.append(Clip('occupied','m',2,5,0,2))
    before=snapshot(e)
    with pytest.raises(ValueError):e._commit_source_audio_separation('v',candidate)
    assert snapshot(e)==before


def test_narrow_primitive_cannot_be_used_to_restore_suppressed_picture():
    e=editor();e._commit_source_audio_separation('v',audio_for(e));before=snapshot(e)
    another=audio_for(e);another.id='another';another.track=3
    with pytest.raises(ValueError):e._commit_source_audio_separation('v',another)
    assert snapshot(e)==before


@pytest.mark.parametrize('special',['disabled','anchor'])
def test_generic_callback_cannot_change_or_create_special_audio_state(special):
    e=editor();before=snapshot(e)
    def modify():
        if special=='disabled':e._clip('v').source_audio_enabled=False
        else:e._clip('v').audio_mix_anchor=AudioMixAnchor(1,0,'historical')
    with pytest.raises(ValueError):e._commit(modify)
    assert snapshot(e)==before
    new=Clip('new','m',2,30,0,2)
    if special=='disabled':new.source_audio_enabled=False
    else:new.audio_mix_anchor=AudioMixAnchor(1,0,'historical')
    with pytest.raises(ValueError):e._commit(lambda:e.project.clips.append(new))
    assert snapshot(e)==before


def test_historical_anchor_has_no_live_clip_reference_requirement():
    e=editor();source=e._clip('v');source.audio_mix_anchor=AudioMixAnchor(3,99,'deleted historical id')
    candidate=audio_for(e);candidate.audio_mix_anchor=source.audio_mix_anchor
    audio=e._commit_source_audio_separation('v',candidate)
    assert audio.audio_mix_anchor==source.audio_mix_anchor
    e.delete_clip('v')
    assert e._clip(audio.id).audio_mix_anchor.clip_id=='deleted historical id'
    assert Project.from_dict(e.project.to_dict())==e.project


def test_shared_historical_slot_cannot_reorder_on_new_audio_identity():
    # This is a valid schema40 project. Multiple historical anchors are not
    # live references and may survive imported descendants/earlier saves.
    anchor=AudioMixAnchor(0,0,'historical')
    e=Editor(Project(media=[Media('m','/unread.mov','AV',10,64,48,True,True)], clips=[
        Clip('0','m',0,0,0,8,audio_mix_anchor=anchor),
        Clip('1','m',1,0,0,8,audio_mix_anchor=anchor),
        Clip('2','m',2,0,0,8,audio_mix_anchor=anchor)]))
    # Old order is 0,1,2. A fresh 'f' handoff would order 1,2,f unless rejected;
    # [1,-1,2^-25] proves this is not a harmless relabeling in real f32 audio.
    candidate=Clip('f','m',3,0,0,8,audio_mix_anchor=anchor)
    before=snapshot(e)
    with pytest.raises(ValueError):e._commit_source_audio_separation('0',candidate)
    assert snapshot(e)==before


def test_handoff_allows_shared_historical_anchor_at_distinct_current_starts():
    from lumen.audio_components import audio_mix_order_key
    anchor=AudioMixAnchor(0,0,'historical')
    e=Editor(Project(media=[Media('m','/unread.mov','AV',10,64,48,True,True)],clips=[
        Clip('v','m',0,2,0,8,audio_mix_anchor=anchor),
        Clip('peer','m',1,3,0,8,audio_mix_anchor=anchor)]))
    before=[c.id for c in sorted(e.project.clips,key=audio_mix_order_key)]
    new=Clip('f','m',2,2,0,8,audio_mix_anchor=anchor)
    e._commit_source_audio_separation('v',new)
    after=[c.id for c in sorted(e.project.clips,key=audio_mix_order_key) if c.source_audio_enabled]
    assert after==['f' if x=='v' else x for x in before]


@pytest.mark.parametrize('muted',[False,True])
def test_handoff_rejects_peer_matching_implicit_source_slot(muted):
    e=editor();source=e._clip('v')
    e.project.clips.append(Clip('peer','m',1,source.start,0,8,muted=muted,
        audio_mix_anchor=AudioMixAnchor(source.track,source.start,source.id)))
    before=snapshot(e)
    with pytest.raises(ValueError):e._commit_source_audio_separation('v',audio_for(e))
    assert snapshot(e)==before

"""Exact candidate/transaction proofs, independent of media qualification I/O."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from threading import Thread

import pytest

from lumen.audio_gain import AudioGainKeyframe, MAX_AUDIO_GAIN_SAMPLE
from lumen.clip_motion import ClipMotionKeyframe, MAX_MOTION_FRAME
from lumen.linked_av_split import (LinkedAVSplitVerification, apply_linked_av_split,
    linked_av_split_candidate, plan_linked_av_split, qualify_linked_av_split)
from lumen.model import AudioMixAnchor, Clip, Project
from lumen.qualifier import MAX_WINDOW_FRAME, WindowKeyframe
from lumen.timing import ClipTimeMap
from test_linked_av_model import sample, state


def editor_for(fps=Fraction(24), start=5, origin=80, length=20):
    editor=sample(fps, ClipTimeMap(origin,origin+length),link=True)
    for clip in editor.project.clips[:2]:
        clip.start=start
        for effect in clip.effects:effect.enabled=False
        clip.grade.qualifier.enabled=False
        clip.grade.qualifier.window_keyframes=[WindowKeyframe(-3),WindowKeyframe(8,center_x=.7),WindowKeyframe(70)]
        clip.audio_gain_keyframes=[AudioGainKeyframe(-300,-8),AudioGainKeyframe(20000,3,'hold'),AudioGainKeyframe(800000,-3)]
    editor.project.validate()
    return editor


def boundary(frame,fps):
    v=Fraction(frame*48000,1)/fps
    return (2*v.numerator+v.denominator)//(2*v.denominator)


def unit_receipt(plan):
    # Model unit tests intentionally isolate the worker; real media proof is a
    # separate gate, never inferred from these synthetic receipts.
    return LinkedAVSplitVerification(plan,(),object(),'apply')


def rejected(editor,frame,match=None):
    before=state(editor); live=editor.project; identities=tuple(editor.project.clips)
    for f in (lambda:qualify_linked_av_split(editor.project,'v',frame),
              lambda:plan_linked_av_split(editor,'a',frame)):
        with pytest.raises(ValueError,match=match):f()
        assert state(editor)==before and editor.project is live
        assert all(a is b for a,b in zip(editor.project.clips,identities))


@pytest.mark.parametrize('fps',[Fraction(24),Fraction(30),Fraction(24000,1001),Fraction(30000,1001),Fraction(60000,1001)])
@pytest.mark.parametrize('role',['v','a'])
@pytest.mark.parametrize('cut',[8,13,22])
def test_all_fields_exact_keys_new_pair_one_undo_roundtrip(fps,role,cut):
    editor=editor_for(fps); before=state(editor); old=deepcopy(editor.project)
    identities={c.id:c for c in editor.project.clips}
    plan=plan_linked_av_split(editor,role,cut)
    assert state(editor)==before and len(set(plan.new_ids))==3
    right_v,right_a=apply_linked_av_split(editor,plan,unit_receipt(plan))
    expected=deepcopy(old); records=[];k=cut-5; sample_delta=boundary(cut,fps)-boundary(5,fps)
    for original in expected.clips:
        if original.id not in ('v','a'):records.append(original);continue
        right=deepcopy(original);right.id=plan.new_ids[0 if original.id=='v' else 1];right.start=cut
        right.source_in=80+k;right.duration=20-k;right.time_map=None;right.fade_in=0
        right.motion_keyframes=[replace(key,frame=key.frame-k) for key in right.motion_keyframes]
        right.grade.qualifier.window_keyframes=[replace(key,frame=key.frame-k) for key in right.grade.qualifier.window_keyframes]
        right.audio_gain_keyframes=[replace(key,sample=key.sample-sample_delta) for key in right.audio_gain_keyframes]
        original.duration=k;original.fade_out=0
        records.extend((original,right))
    expected.clips=records;expected.av_links.append(replace(old.av_links[0],id=plan.new_ids[2],video_clip_id=right_v.id,audio_clip_id=right_a.id))
    assert editor.project==expected
    assert (right_v.id,right_a.id)==plan.new_ids[:2]
    assert all(editor._clip(cid) is value for cid,value in identities.items())
    assert not right_v.source_audio_enabled and right_a.source_audio_enabled
    assert right_v.audio_mix_anchor==old.clips[0].audio_mix_anchor
    assert right_a.audio_mix_anchor==old.clips[1].audio_mix_anchor
    assert len(editor._undo)==len(before[1])+1 and editor.revision==before[3]+1
    assert Project.from_dict(editor.project.to_dict())==expected
    editor.undo();assert editor.project==old
    editor.redo();assert editor.project==expected


@pytest.mark.parametrize('frame',[True,False,3.0,Fraction(3),'13',None,-1,2**53])
def test_invalid_cut_types(frame):rejected(editor_for(),frame)


@pytest.mark.parametrize('frame',[0,5,25,26])
def test_strict_interior_cut(frame):rejected(editor_for(),frame,'strictly inside')


@pytest.mark.parametrize('mapping',[ClipTimeMap(80,100,Fraction(2)),ClipTimeMap(Fraction(321,4),Fraction(401,4)),ClipTimeMap(80,Fraction(399,4))])
def test_retimed_fractional_origin_partial_tail_reject_without_mutation(mapping):
    editor=sample(mapping=mapping,link=True)
    for clip in editor.project.clips[:2]:
        for e in clip.effects:e.enabled=False
    if mapping.speed != 1:
        rejected(editor,10,'1x source timing')
    else:
        # This retained node now verifies the intentional new 1x exact-map
        # capability rather than the former fractional-map refusal.
        before=state(editor); plan=plan_linked_av_split(editor,'a',10)
        assert state(editor)==before
        right=apply_linked_av_split(editor,plan,unit_receipt(plan))
        assert all(clip.source_out==mapping.source_out for clip in right)
        assert all(clip.source_in==mapping.source_in+5 for clip in right)


@pytest.mark.parametrize('field,role',[('fade_in','v'),('fade_in','a'),('fade_out','v'),('fade_out','a')])
def test_crossing_fade_reject_never_clamps(field,role):
    editor=editor_for();setattr(editor._clip(role),field,11)
    rejected(editor,15,'fade')
    setattr(editor._clip(role),field,10)
    plan=plan_linked_av_split(editor,role,15);apply_linked_av_split(editor,plan,unit_receipt(plan))
    assert getattr(editor._clip(role if field=='fade_in' else plan.new_ids[0 if role=='v' else 1]),field)==10


@pytest.mark.parametrize('track',[0,2])
def test_either_partner_lock(track):
    editor=editor_for();editor.set_track_edit_lock(track,True);rejected(editor,13,'[Ll]ock')


@pytest.mark.parametrize('kind,role',[('motion','v'),('window','v'),('window','a'),('gain','v'),('gain','a')])
def test_hidden_key_overflow_reject_atomic(kind,role):
    editor=editor_for();clip=editor._clip(role)
    if kind=='motion':clip.motion_keyframes=[ClipMotionKeyframe(-MAX_MOTION_FRAME)]
    elif kind=='window':clip.grade.qualifier.window_keyframes=[WindowKeyframe(-MAX_WINDOW_FRAME)]
    else:clip.audio_gain_keyframes=[AudioGainKeyframe(-MAX_AUDIO_GAIN_SAMPLE,0)]
    rejected(editor,13)


@pytest.mark.parametrize('kind',['effect','stabilization','restoration','mixed_fps','budget'])
def test_unproven_picture_branches_and_budget_reject(kind):
    editor=editor_for();v=editor._clip('v')
    if kind=='effect':v.effects[0].enabled=True
    elif kind=='stabilization':v.stabilization.enabled=True;v.stabilization.strength=.5
    elif kind=='restoration':v.restoration.enabled=True;v.restoration.denoise=.5
    elif kind=='mixed_fps':editor.project.media[0].fps_num=60
    else:
        for clip in editor.project.clips[:2]:clip.duration=721
        editor.project.clips[-1].start=900
    rejected(editor,13)


@pytest.mark.parametrize('muted',[False,True])
@pytest.mark.parametrize('offset',[0,.01,.2,-.01])
def test_pcm_lattice_rejects_sample_phase_even_when_muted(muted,offset):
    editor=editor_for(Fraction(30000,1001),start=1);editor.project.media[0].audio_offset=offset
    editor._clip('a').muted=muted
    # 1 -> 4 produces 4804 samples, while source 80 -> 83 produces4805.
    rejected(editor,4,'sample-grid boundary')


@pytest.mark.parametrize('offset',[0,-.01,3.5,4.,10.])
@pytest.mark.parametrize('muted',[False,True])
def test_offset_leading_silence_and_padding_lattice(offset,muted):
    editor=editor_for();editor.project.media[0].audio_offset=offset;editor._clip('a').muted=muted
    plan=plan_linked_av_split(editor,'a',13)
    apply_linked_av_split(editor,plan,unit_receipt(plan))
    assert editor._clip(plan.new_ids[1]).muted==muted


@pytest.mark.parametrize('muted',[False,True])
def test_order_change_reject_against_unchanged_potential_contribution(muted):
    editor=editor_for();other=editor._clip('other');other.start=10
    other.audio_mix_anchor=deepcopy(editor._clip('a').audio_mix_anchor);other.muted=muted
    editor._clip('a').muted=muted;editor.project.validate()
    rejected(editor,13,'historical audio mixing order')


@pytest.mark.parametrize('anchor',[None,AudioMixAnchor(0,2,'old-v')])
def test_uuid_independent_ambiguous_tie_reject(anchor):
    editor=editor_for();audio=editor._clip('a');audio.audio_mix_anchor=anchor
    other=editor._clip('other');other.start=13
    other.audio_mix_anchor=(AudioMixAnchor(2,13,'other') if anchor is None else deepcopy(anchor))
    editor.project.validate();rejected(editor,13,'ambiguous')


def test_suppressed_unchanged_contribution_does_not_falsely_reject_order():
    editor=editor_for();other=editor._clip('other');other.start=10
    other.audio_mix_anchor=deepcopy(editor._clip('a').audio_mix_anchor);other.source_audio_enabled=False
    plan=plan_linked_av_split(editor,'v',13);apply_linked_av_split(editor,plan,unit_receipt(plan))


@pytest.mark.parametrize('new_ids',[('v','r-a','r-l'),('r','r','r-l'),('r-v','r-a'),['r-v','r-a','r-l'],('r-v','r-a',True)])
def test_identity_validation(new_ids):
    editor=editor_for();before=state(editor)
    with pytest.raises(ValueError):linked_av_split_candidate(editor.project,'v',13,new_ids)
    assert state(editor)==before


def test_generic_and_linked_trim_authority_cannot_create_special_audio_state():
    editor=editor_for();candidate=linked_av_split_candidate(editor.project,'v',13,('rv','ra','rl'))
    before=state(editor)
    def change():editor._project=deepcopy(candidate)
    with pytest.raises(ValueError,match='Link/Unlink'):editor._commit(change)
    assert state(editor)==before
    with pytest.raises(ValueError,match='Special source audio state'):
        editor._Editor__commit(change,linked_av_candidate=candidate)
    assert state(editor)==before


@pytest.mark.parametrize('tamper',['candidate','frame','ids','preview','revision_aba','session','snapshot'])
def test_forged_stale_preview_and_aba_reject(tamper):
    editor=editor_for();plan=plan_linked_av_split(editor,'a',13);receipt=unit_receipt(plan)
    if tamper=='candidate':plan=replace(plan,candidate_signature='bad')
    elif tamper=='frame':plan=replace(plan,frame=14)
    elif tamper=='ids':plan=replace(plan,new_ids=('newv','newa','newl'))
    elif tamper=='preview':receipt=replace(receipt,phase='preview')
    elif tamper=='revision_aba':editor.update_clip('other',gain_db=-7);editor.undo()
    elif tamper=='session':editor.project=deepcopy(editor.project)
    elif tamper=='snapshot':editor.project.clips[-1].muted=True
    if tamper in ('candidate','frame','ids'):receipt=replace(receipt,plan=plan)
    before=state(editor)
    with pytest.raises(ValueError):apply_linked_av_split(editor,plan,receipt)
    assert state(editor)==before


def test_owner_thread_and_replay_rejected():
    editor=editor_for();plan=plan_linked_av_split(editor,'v',13);receipt=unit_receipt(plan);errors=[]
    def worker():
        try:apply_linked_av_split(editor,plan,receipt)
        except Exception as exc:errors.append(exc)
    before=state(editor);thread=Thread(target=worker);thread.start();thread.join()
    assert len(errors)==1 and 'owning thread' in str(errors[0]);assert state(editor)==before
    apply_linked_av_split(editor,plan,receipt);after=state(editor)
    with pytest.raises(ValueError):apply_linked_av_split(editor,plan,receipt)
    assert state(editor)==after


def test_plan_uses_one_set_of_real_ids_no_retry(monkeypatch):
    import lumen.linked_av_split as split
    calls=[]
    class UUID:
        def __init__(self,value):self.hex=value
    def uuid():calls.append(1);return UUID('v')
    monkeypatch.setattr(split,'uuid4',uuid)
    editor=editor_for();before=state(editor)
    with pytest.raises(ValueError,match='distinct and unused'):plan_linked_av_split(editor,'v',13)
    assert len(calls)==3 and state(editor)==before


@pytest.mark.parametrize('audio_id',['00000000000000000000000000000000','ffffffffffffffffffffffffffffffff'])
@pytest.mark.parametrize('other_start',[10,13,19])
def test_unanchored_new_uuid_cannot_change_qualification_outcome(audio_id,other_start):
    editor=editor_for();editor._clip('a').audio_mix_anchor=None
    other=editor._clip('other');other.start=other_start
    other.audio_mix_anchor=AudioMixAnchor(2,13,'88888888888888888888888888888888')
    editor.project.validate()
    with pytest.raises(ValueError,match='ambiguous'):
        qualify_linked_av_split(editor.project,'v',13)
    with pytest.raises(ValueError,match='ambiguous'):
        linked_av_split_candidate(editor.project,'v',13,('new-v',audio_id,'new-link'))


def test_successful_fractional_fps_key_rebase_uses_global_difference_not_delta():
    fps=Fraction(30000,1001);editor=editor_for(fps,start=1,origin=81)
    before=deepcopy(editor.project);plan=plan_linked_av_split(editor,'a',4)
    delta=boundary(4,fps)-boundary(1,fps)
    assert delta==4804 and boundary(3,fps)==4805
    right=apply_linked_av_split(editor,plan,unit_receipt(plan))
    for original,current in zip(before.clips[:2],right):
        assert current.audio_gain_keyframes==[replace(key,sample=key.sample-4804) for key in original.audio_gain_keyframes]
        assert current.audio_gain_keyframes!=[replace(key,sample=key.sample-4805) for key in original.audio_gain_keyframes]


def test_dedicated_transaction_rejects_any_extra_project_change(monkeypatch):
    editor=editor_for();plan=plan_linked_av_split(editor,'a',13);before=state(editor)
    original=editor._clip
    def lookup(clip_id):
        value=original(clip_id)
        if clip_id==plan.new_ids[0]:editor.project.name='unexpected unrelated write'
        return value
    monkeypatch.setattr(editor,'_clip',lookup)
    with pytest.raises(ValueError,match='only its approved pair candidate'):
        apply_linked_av_split(editor,plan,unit_receipt(plan))
    assert state(editor)==before

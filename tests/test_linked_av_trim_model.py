"""Joint edge trim: exact pair-only derivation, all hidden keys and fences."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from threading import Thread

import pytest

from lumen.audio_gain import AudioGainKeyframe, MAX_AUDIO_GAIN_SAMPLE
from lumen.clip_motion import ClipMotionKeyframe, MAX_MOTION_FRAME
from lumen.model import (AudioMixAnchor, Clip, ClipGroup, Editor, MAX_CAPTION_FRAME,
                         Project, VideoTransition)
from lumen.qualifier import MAX_WINDOW_FRAME, WindowKeyframe
from lumen.timing import ClipTimeMap, MAX_SOURCE_NUMERATOR
from test_linked_av_model import sample, state


def editor_for(speed=Fraction(3, 2), fps=Fraction(30000, 1001), start=7):
    editor = sample(fps, ClipTimeMap(80, 80 + Fraction(79, 4) * speed, speed), link=True)
    for clip in editor.project.clips[:2]:
        clip.start = start
        clip.motion_keyframes = [ClipMotionKeyframe(-8, x=.3), ClipMotionKeyframe(5,x=.8,interpolation='hold'),
                                 ClipMotionKeyframe(70,x=1.4)] if clip.track < 2 else []
        clip.grade.qualifier.enabled = False
        clip.grade.qualifier.window_keyframes = [WindowKeyframe(-5), WindowKeyframe(7,center_x=.7,interpolation='hold'), WindowKeyframe(60)]
        clip.audio_gain_keyframes = [AudioGainKeyframe(-200,-7), AudioGainKeyframe(20000,3,'hold'), AudioGainKeyframe(800000,-2)]
    editor.project.validate()
    return editor


def rounded_sample(frame, fps):
    value = Fraction(frame * 48000, 1) / fps
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def rejected(editor, edge, delta, match=None, clip_id='v'):
    before, project, identities = state(editor), editor.project, tuple(editor.project.clips)
    for method in (editor.preview_trim_linked_av, editor.trim_linked_av):
        with pytest.raises(ValueError, match=match):
            method(clip_id, edge, delta)
        assert state(editor) == before
        assert editor.project is project
        assert all(a is b for a,b in zip(editor.project.clips, identities))


@pytest.mark.parametrize('speed', [Fraction(1,4),Fraction(1),Fraction(3,2),Fraction(4)])
@pytest.mark.parametrize('fps', [Fraction(30),Fraction(24000,1001),Fraction(30000,1001),Fraction(60000,1001)])
@pytest.mark.parametrize('edge,delta', [('left',-3),('left',2),('right',-3),('right',2)])
@pytest.mark.parametrize('role', ['v','a'])
def test_pair_exact_map_residue_every_field_hidden_keys_and_one_history(speed,fps,edge,delta,role):
    editor=editor_for(speed,fps);before=state(editor);old=deepcopy(editor.project)
    identities={c.id:c for c in editor.project.clips}
    expected=deepcopy(old)
    for clip in expected.clips[:2]:
        oldmap=clip.timing
        origin=oldmap.source_in+delta*speed if edge=='left' else oldmap.source_in
        endpoint=oldmap.source_out+delta*speed if edge=='right' else oldmap.source_out
        clip.time_map=ClipTimeMap(origin,endpoint,speed)
        clip.source_in=origin
        clip.duration += -delta if edge=='left' else delta
        if edge=='left':
            sample_delta=rounded_sample(clip.start+delta,fps)-rounded_sample(clip.start,fps)
            clip.start+=delta
            clip.motion_keyframes=[replace(k,frame=k.frame-delta) for k in clip.motion_keyframes]
            clip.grade.qualifier.window_keyframes=[replace(k,frame=k.frame-delta) for k in clip.grade.qualifier.window_keyframes]
            clip.audio_gain_keyframes=[replace(k,sample=k.sample-sample_delta) for k in clip.audio_gain_keyframes]
    preview=editor.preview_trim_linked_av(role,edge,delta)
    assert tuple(c.id for c in preview)==('v','a')
    assert tuple(expected.clips[:2])==preview
    assert state(editor)==before
    assert all(preview[i] is not identities[c.id] for i,c in enumerate(preview))
    result=editor.trim_linked_av(role,edge,delta)
    assert result is identities[role]
    assert editor.project==expected
    assert all(c is identities[c.id] for c in editor.project.clips)
    assert editor._clip('v').timing==editor._clip('a').timing
    for new,previous in zip(editor.project.clips[:2],old.clips[:2]):
        assert new.duration*speed-(new.source_out-new.source_in)==previous.duration*speed-(previous.source_out-previous.source_in)==speed/4
        assert (new.fade_in,new.fade_out)==(previous.fade_in,previous.fade_out)
    assert len(editor._undo)==len(before[1])+1 and editor.revision==before[3]+1
    assert Project.from_dict(editor.project.to_dict())==expected
    editor.undo(); assert editor.project==old
    editor.redo(); assert editor.project==expected


@pytest.mark.parametrize('edge', ['left','right'])
def test_noop_detached_preview_preserves_redo_revision_and_identity_even_when_locked(edge):
    editor=editor_for();editor.update_clip('other',gain_db=-4);editor.undo()
    editor.project.track_edit_locks=[True,False,True,False]
    before=state(editor);original=editor._clip('a')
    v,a=editor.preview_trim_linked_av('a',edge,0)
    v.grade.qualifier.window_keyframes.clear();a.audio_gain_keyframes.clear()
    assert state(editor)==before
    assert editor.trim_linked_av('a',edge,0) is original and state(editor)==before


@pytest.mark.parametrize('edge', ['', 'Left','out',None,True,0,[]])
def test_invalid_edge(edge):rejected(editor_for(),edge,1,'edge')


@pytest.mark.parametrize('delta',[True,False,1.0,Fraction(1),'1',None,float('nan'),float('inf'),2**53,-2**53])
@pytest.mark.parametrize('edge',['left','right'])
def test_invalid_delta(delta,edge):rejected(editor_for(),edge,delta,'trim delta')


@pytest.mark.parametrize('edge,delta',[('left',20),('left',21),('right',-20),('right',-21)])
def test_empty_or_negative_duration(edge,delta):rejected(editor_for(),edge,delta,'one timeline frame')


@pytest.mark.parametrize('role',['v','a'])
@pytest.mark.parametrize('edge,delta',[('left',1),('right',-1)])
@pytest.mark.parametrize('fade',['fade_in','fade_out'])
def test_either_role_fade_rejects_without_clamping(role,edge,delta,fade):
    editor=editor_for();setattr(editor._clip(role),fade,20)
    rejected(editor,edge,delta,'fade')
    setattr(editor._clip(role),fade,19)
    result=editor.trim_linked_av(role,edge,delta)
    assert getattr(result,fade)==19 and result.duration==19


@pytest.mark.parametrize('track',[0,2])
@pytest.mark.parametrize('edge,delta',[('left',-3),('right',3)])
def test_partner_collision_and_exact_adjacency(track,edge,delta):
    editor=editor_for(start=30)
    neighbor=Clip('neighbor','m',track,0 if edge=='left' else 52,0,28 if edge=='left' else 10)
    editor.project.clips.append(neighbor);editor.project.clips.reverse();editor.project.validate()
    rejected(editor,edge,delta,'overlap')
    editor.trim_linked_av('v',edge,-2 if edge=='left' else 2)
    assert editor._clip('a').timing==editor._clip('v').timing


@pytest.mark.parametrize('track',[0,2])
@pytest.mark.parametrize('edge',['left','right'])
def test_either_role_lock(track,edge):
    editor=editor_for();editor.set_track_edit_lock(track,True)
    rejected(editor,edge,1,'[Ll]ock')


def test_negative_start_source_zero_and_source_eof():
    rejected(editor_for(),'left',-8,'trim start')
    editor=editor_for(Fraction(4),start=50)
    for c in editor.project.clips[:2]:
        c.time_map=ClipTimeMap(12,90,4);c.source_in=Fraction(12)
    rejected(editor,'left',-4,'rational bounds')
    editor.trim_linked_av('v','left',-3);assert editor._clip('v').source_in==0
    editor=editor_for(Fraction(4));bound=editor.project.media_frames(editor.project.media[0])
    for c in editor.project.clips[:2]:
        c.time_map=ClipTimeMap(bound-87,bound-8,4);c.source_in=bound-87
    editor.trim_linked_av('a','right',2);assert editor._clip('a').source_out==bound
    rejected(editor,'right',1,'source media')


@pytest.mark.parametrize('edge',['left','right'])
def test_coprime_denominator_rational_overflow(edge):
    speed=Fraction(999978,999979)
    origin=Fraction(9999831,999983) if edge=='left' else Fraction(10)
    endpoint=Fraction(49999151,999983) if edge=='right' else Fraction(50)
    editor=sample(mapping=ClipTimeMap(origin,endpoint,speed),link=True)
    rejected(editor,edge,1,'rational bounds')


def test_source_numerator_overflow():
    endpoint=Fraction(MAX_SOURCE_NUMERATOR,1000000)
    editor=editor_for(Fraction(4),Fraction(1000))
    editor.project.media[0].duration=100000000
    for c in editor.project.clips[:2]:
        c.time_map=ClipTimeMap(endpoint-76,endpoint,4);c.source_in=endpoint-76;c.duration=19
    rejected(editor,'right',1,'rational bounds')


@pytest.mark.parametrize('kind,role',[('motion','v'),('window','v'),('window','a'),('gain','v'),('gain','a')])
@pytest.mark.parametrize('delta',[-1,1])
def test_all_hidden_key_overflow_is_atomic(kind,role,delta):
    editor=editor_for();clip=editor._clip(role)
    if kind=='motion':clip.motion_keyframes=[ClipMotionKeyframe(-MAX_MOTION_FRAME if delta>0 else MAX_MOTION_FRAME)]
    elif kind=='window':clip.grade.qualifier.window_keyframes=[WindowKeyframe(-MAX_WINDOW_FRAME if delta>0 else MAX_WINDOW_FRAME)]
    else:clip.audio_gain_keyframes=[AudioGainKeyframe(-MAX_AUDIO_GAIN_SAMPLE if delta>0 else MAX_AUDIO_GAIN_SAMPLE,0)]
    rejected(editor,'left',delta)
    editor.trim_linked_av(role,'right',delta)


def test_fractional_sample_delta_uses_global_phase_not_rounded_delta():
    fps=Fraction(30000,1001);editor=editor_for(fps=fps,start=1)
    delta=rounded_sample(2,fps)-rounded_sample(1,fps)
    assert delta!=rounded_sample(1,fps)
    old=deepcopy(editor.project)
    editor.trim_linked_av('v','left',1)
    for a,b in zip(old.clips[:2],editor.project.clips[:2]):
        assert b.audio_gain_keyframes==[replace(k,sample=k.sample-delta) for k in a.audio_gain_keyframes]


def order_fixture(*, muted=False, suppressed=False):
    editor=editor_for(start=5)
    # Track 1 overlaps by design, so the order matters independently of track collisions.
    other=editor._clip('other');other.start=7;other.duration=20
    other.audio_mix_anchor=deepcopy(editor._clip('a').audio_mix_anchor)
    other.muted=muted;other.source_audio_enabled=not suppressed
    editor.project.validate();return editor


@pytest.mark.parametrize('muted',[False,True])
def test_changed_historical_tie_order_rejects_even_if_muted(muted):
    editor=order_fixture(muted=muted);editor._clip('a').muted=muted
    rejected(editor,'left',3,'historical audio mixing order')
    # An accepted trim need not reject merely because historical anchors are shared.
    editor.trim_linked_av('a','left',1)
    assert editor._clip('a').start==6


def test_suppressed_other_video_does_not_create_false_mix_order_conflict():
    editor=order_fixture(suppressed=True)
    editor.trim_linked_av('v','left',3);assert editor._clip('a').start==8


def test_right_trim_retains_order_and_all_structural_fields():
    editor=order_fixture();before=deepcopy(editor.project)
    editor.trim_linked_av('v','right',3)
    for old,new in zip(before.clips,editor.project.clips):
        assert (new.source_audio_enabled,new.audio_mix_anchor)==(old.source_audio_enabled,old.audio_mix_anchor)


@pytest.mark.parametrize('corruption',['missing_media','stale_pair_map','stale_pair_start','missing_role','group','enabled_video'])
def test_invalid_existing_project_is_never_repaired(corruption):
    editor=editor_for()
    if corruption=='missing_media':editor.project.media.clear()
    elif corruption=='stale_pair_map':editor._clip('a').time_map=ClipTimeMap(81,110,2);editor._clip('a').source_in=81
    elif corruption=='stale_pair_start':editor._clip('a').start+=1
    elif corruption=='missing_role':editor.project.clips.remove(editor._clip('a'))
    elif corruption=='group':editor.project.clip_groups=[ClipGroup('g',['v','a'])]
    else:editor._clip('v').source_audio_enabled=True
    rejected(editor,'right',1)


def test_unknown_or_unlinked_clip_and_owner_thread():
    for clip_id in ['unknown','other']:
        rejected(editor_for(),'right',1,'existing linked A/V pair',clip_id)
    editor=editor_for();before=state(editor);errors=[]
    def worker():
        for delta in (0,1):
            try:editor.trim_linked_av('v','right',delta)
            except ValueError as error:errors.append(str(error))
    thread=Thread(target=worker);thread.start();thread.join()
    assert len(errors)==2 and all('owning thread' in message for message in errors)
    assert state(editor)==before


def test_no_independent_trim_unlink_clamp_or_audio_translation_path(monkeypatch):
    import lumen.clip_audio_translation as translation
    editor=editor_for()
    def forbidden(*args,**kwargs):pytest.fail('Joint trim must not use independent edits/clamping/audio conservation guards')
    for name in ('trim_edge','preview_trim_edge','trim_clip_left','unlink_av_clips','link_av_clips','_set_clip_timing'):
        monkeypatch.setattr(editor,name,forbidden)
    monkeypatch.setattr(translation,'guard_local_audio_translation',forbidden)
    monkeypatch.setattr(translation,'guard_common_audio_translation',forbidden)
    editor.trim_linked_av('v','left',2)
    assert len(editor.project.av_links)==1


def test_generic_matching_pair_trim_and_split_remain_forbidden():
    editor=editor_for();editor.trim_linked_av('v','right',1);before=state(editor)
    def change():
        for c in editor.project.clips[:2]:
            Editor._set_clip_timing(c,ClipTimeMap(c.source_in,c.source_out+1,c.speed))
    with pytest.raises(ValueError,match='Unlink'):editor._commit(change)
    assert state(editor)==before
    for role in ('v','a'):
        with pytest.raises(ValueError):editor.split_clip(role,editor._clip(role).start+2)
        assert state(editor)==before


@pytest.mark.parametrize('edge',['left','right'])
def test_timeline_end_overflow_and_noop_noncanonical_map(edge):
    editor=editor_for();old=editor._clip('v')
    for c in editor.project.clips[:2]:c.start=MAX_CAPTION_FRAME-old.duration
    if edge=='right':rejected(editor,edge,1,'trim end')
    else:
        for c in editor.project.clips[:2]:c.start+=1
        rejected(editor,edge,-1,'trim end')
    editor=sample(mapping=ClipTimeMap(20,40),link=True)
    for c in editor.project.clips[:2]:c.time_map=ClipTimeMap(20,40)
    editor.project.track_edit_locks=[True,False,True,False]
    before=state(editor)
    assert editor.preview_trim_linked_av('v',edge,0)==tuple(editor.project.clips[:2])
    editor.trim_linked_av('v',edge,0)
    assert state(editor)==before


@pytest.mark.parametrize('edge,delta',[('left',19),('right',-19)])
def test_one_frame_partial_pair_tail_does_not_grow(edge,delta):
    editor=editor_for();old=deepcopy(editor.project)
    for c in editor.project.clips[:2]:c.fade_in=c.fade_out=0
    result=editor.trim_linked_av('v',edge,delta)
    assert result.duration==1
    assert result.source_out-result.source_in==old.clips[0].speed*Fraction(3,4)
    assert editor._clip('v').timing==editor._clip('a').timing

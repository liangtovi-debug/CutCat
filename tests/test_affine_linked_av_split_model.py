"""Fresh independent scalar oracles for exact 1x affine through-split.

No renderer/media proof is inferred from the deliberately synthetic Apply receipt.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from threading import Thread

import pytest

from lumen.audio_gain import AudioGainKeyframe, MAX_AUDIO_GAIN_SAMPLE
from lumen.clip_motion import ClipMotionKeyframe, MAX_MOTION_FRAME
from lumen.linked_av_split import (linked_av_split_candidate, plan_linked_av_split,
    qualify_linked_av_split, apply_linked_av_split)
from lumen.model import AVLink, AV_LINK_AFFINE_ALIGNMENT, AudioMixAnchor, Project
from lumen.qualifier import WindowKeyframe, MAX_WINDOW_FRAME
from lumen.timing import ClipTimeMap
from test_affine_linked_av_trim_model import snapshot
from test_linked_av_split_model import editor_for, unit_receipt


def sample(frame, fps):
    value = Fraction(frame)*48000/fps
    return (2*value.numerator+value.denominator)//(2*value.denominator)


def affine_editor(fps=Fraction(24), geometry='leading', fractional=False):
    editor = editor_for(fps, start=5, origin=10, length=16)
    v, a = editor._clip('v'), editor._clip('a')
    a.start, a.duration = {'leading': (3, 20), 'trailing': (7, 17),
        'equal-edges': (5, 16), 'equal-maps': (5, 16), 'disjoint': (30, 12)}[geometry]
    intercept = fps*Fraction(3, 16) if fractional else Fraction(5)
    for clip, tail in ((v, Fraction(1, 4)), (a, Fraction(1, 4) if geometry == 'equal-maps' else Fraction(1, 2))):
        origin = clip.start+intercept
        clip.source_in, clip.time_map = origin, ClipTimeMap(origin, origin+clip.duration-tail)
        clip.fade_in, clip.fade_out = 1, 2
        clip.audio_gain_keyframes = [AudioGainKeyframe(-17, -8), AudioGainKeyframe(10000, 2, 'hold'), AudioGainKeyframe(900000, -4)]
        clip.grade.qualifier.window_keyframes = [WindowKeyframe(-4), WindowKeyframe(9, center_x=.7), WindowKeyframe(90)]
    v.motion_keyframes = [ClipMotionKeyframe(-7, x=.2), ClipMotionKeyframe(8, x=.7), ClipMotionKeyframe(80, x=.9)]
    editor.project.av_links = [AVLink('pair', 'v', 'a', 'm', AV_LINK_AFFINE_ALIGNMENT)]
    editor.project.validate()
    return editor


def expected_project(old, frame, ids):
    """Scalar old-record oracle; do not call production split/shift helpers."""
    expected = deepcopy(old)
    records = []
    for old_clip in old.clips:
        left = deepcopy(old_clip)
        if old_clip.id not in ('v', 'a'):
            records.append(left)
            continue
        k = frame-old_clip.start
        origin, endpoint = old_clip.source_in, old_clip.source_out
        boundary = origin+k
        delta = sample(frame, old.fps)-sample(old_clip.start, old.fps)
        right = deepcopy(old_clip)
        right.id = ids[0 if old_clip.id == 'v' else 1]
        right.start, right.source_in, right.duration = frame, boundary, old_clip.duration-k
        right.time_map, right.fade_in = ClipTimeMap(boundary, endpoint), 0
        right.audio_gain_keyframes = [replace(key, sample=key.sample-delta) for key in old_clip.audio_gain_keyframes]
        right.motion_keyframes = [replace(key, frame=key.frame-k) for key in old_clip.motion_keyframes]
        right.grade.qualifier.window_keyframes = [replace(key, frame=key.frame-k) for key in old_clip.grade.qualifier.window_keyframes]
        left.duration, left.time_map, left.fade_out = k, ClipTimeMap(origin, boundary), 0
        for clip in (left, right):
            if clip.source_in.denominator == 1 and clip.source_out-clip.source_in == clip.duration:
                clip.time_map, clip.source_in = None, int(clip.source_in)
        records.extend((left, right))
    expected.clips = records
    expected.av_links.append(replace(old.av_links[0], id=ids[2], video_clip_id=ids[0], audio_clip_id=ids[1]))
    return expected


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30), Fraction(24000,1001), Fraction(30000,1001), Fraction(60000,1001)])
@pytest.mark.parametrize('geometry', ['leading', 'trailing', 'equal-edges', 'equal-maps'])
@pytest.mark.parametrize('fractional', [False, True], ids=['integral-origin', 'fractional-origin'])
@pytest.mark.parametrize('role', ['v', 'a'])
def test_own_role_exact_map_keys_modes_identity_and_history(fps, geometry, fractional, role):
    editor = affine_editor(fps, geometry, fractional)
    before = snapshot(editor); old = deepcopy(editor.project)
    objects = {clip.id: clip for clip in editor.project.clips}
    plan = plan_linked_av_split(editor, role, 12)
    assert snapshot(editor) == before
    expected = expected_project(old, 12, plan.new_ids)
    right = apply_linked_av_split(editor, plan, unit_receipt(plan))
    assert editor.project == expected
    assert all(editor._clip(cid) is clip for cid, clip in objects.items())
    assert all(link.alignment == AV_LINK_AFFINE_ALIGNMENT for link in editor.project.av_links)
    for original, current in zip(old.clips[:2], right):
        assert current.source_out == original.source_out
        assert current.duration-(current.source_out-current.source_in) == original.duration-(original.source_out-original.source_in)
        assert current.source_in-current.start == original.source_in-original.start
    assert right[0].source_in == right[1].source_in
    assert not right[0].source_audio_enabled and right[1].source_audio_enabled
    assert len(editor._undo) == len(before[0][1])+1 and editor.revision == before[0][3]+1
    assert Project.from_dict(editor.project.to_dict()) == expected
    editor.undo(); assert editor.project == old
    editor.redo(); assert editor.project == expected


def test_fractional_fps_distinct_global_gain_deltas_including_hidden_video_keys():
    editor = affine_editor(Fraction(30000,1001), 'leading')
    # tV=1,tA=3,cut4: qV=4804/qA=1601, not S(3)=4805/S(1)=1602.
    video=editor._clip('v'); video.start=1; video.source_in=6
    video.time_map=ClipTimeMap(6,6+video.duration-Fraction(1,4))
    editor.project.validate()
    old = deepcopy(editor.project); plan = plan_linked_av_split(editor, 'a', 4)
    right = apply_linked_av_split(editor, plan, unit_receipt(plan))
    deltas = [sample(4,old.fps)-sample(clip.start,old.fps) for clip in old.clips[:2]]
    assert deltas == [4804, 1601]
    assert [sample(4-clip.start,old.fps) for clip in old.clips[:2]] == [4805,1602]
    for before, after, delta in zip(old.clips[:2], right, deltas):
        assert after.audio_gain_keyframes == [replace(key,sample=key.sample-delta) for key in before.audio_gain_keyframes]
    assert right[0].audio_gain_keyframes != [replace(key,sample=key.sample-deltas[1]) for key in old.clips[0].audio_gain_keyframes]


@pytest.mark.parametrize('geometry,frame', [('leading',3),('leading',5),('leading',21),('leading',23),('trailing',6),('trailing',7),('disjoint',12),('disjoint',31)])
@pytest.mark.parametrize('role', ['v','a'])
def test_cut_must_be_strictly_inside_both_roles_raw_identity_atomic(geometry, frame, role):
    editor=affine_editor(geometry=geometry); editor.project.master_gain_db=-0.0
    before=snapshot(editor)
    for action in (lambda:qualify_linked_av_split(editor.project,role,frame),
                   lambda:plan_linked_av_split(editor,role,frame)):
        with pytest.raises(ValueError,match='strictly inside both'): action()
        assert snapshot(editor)==before


@pytest.mark.parametrize('role', ['v','a'])
@pytest.mark.parametrize('kind', ['fade-in','fade-out','gain','window','lock','order','ambiguous'])
def test_each_role_bounds_locks_and_potential_mix_fail_atomically(role,kind):
    editor=affine_editor(); clip=editor._clip(role)
    if kind=='fade-in': clip.fade_in=12-clip.start+1
    elif kind=='fade-out': clip.fade_out=clip.end-12+1
    elif kind=='gain': clip.audio_gain_keyframes=[AudioGainKeyframe(-MAX_AUDIO_GAIN_SAMPLE,0)]
    elif kind=='window': clip.grade.qualifier.window_keyframes=[WindowKeyframe(-MAX_WINDOW_FRAME)]
    elif kind=='lock': editor.set_track_edit_lock(clip.track,True)
    else:
        other=editor._clip('other'); other.start=12 if kind=='ambiguous' else 10
        other.audio_mix_anchor=deepcopy(editor._clip('a').audio_mix_anchor)
        other.muted=True; editor.project.validate()
    before=snapshot(editor)
    with pytest.raises(ValueError): plan_linked_av_split(editor,role,12)
    assert snapshot(editor)==before


def test_hidden_motion_bounds_fail_atomically():
    editor=affine_editor(); editor._clip('v').motion_keyframes=[ClipMotionKeyframe(-MAX_MOTION_FRAME)]
    before=snapshot(editor)
    with pytest.raises(ValueError): plan_linked_av_split(editor,'a',12)
    assert snapshot(editor)==before


@pytest.mark.parametrize('muted',[False,True])
def test_affine_audio_phase_refused_even_when_user_muted(muted):
    editor=affine_editor(Fraction(30000,1001))
    for clip in editor.project.clips[:2]:
        clip.source_in += 1
        clip.time_map=ClipTimeMap(clip.source_in,clip.source_out+1)
    editor._clip('a').muted=muted; editor.project.validate(); before=snapshot(editor)
    # The source delta S(18)-S(9)=14415 differs from S(12)-S(3)=14414.
    assert sample(18,editor.project.fps)-sample(9,editor.project.fps)==14415
    with pytest.raises(ValueError,match='sample-grid'): plan_linked_av_split(editor,'a',12)
    assert snapshot(editor)==before


def test_audio_only_source_extension_is_charged_to_30_second_budget():
    editor=affine_editor(); a=editor._clip('a'); a.duration=730
    a.time_map=ClipTimeMap(a.source_in,a.source_in+a.duration-Fraction(1,2))
    editor.project.validate(); before=snapshot(editor)
    with pytest.raises(ValueError,match='30 seconds'): plan_linked_av_split(editor,'v',12)
    assert snapshot(editor)==before


def test_owner_thread_and_one_apply_receipt_remain_mandatory():
    editor=affine_editor(); before=snapshot(editor); errors=[]
    def worker():
        try: plan_linked_av_split(editor,'a',12)
        except ValueError as error: errors.append(str(error))
    thread=Thread(target=worker);thread.start();thread.join()
    assert len(errors)==1 and 'owning thread' in errors[0] and snapshot(editor)==before
    plan=plan_linked_av_split(editor,'v',12)
    with pytest.raises(ValueError,match='fresh verified'): apply_linked_av_split(editor,plan,None)
    assert snapshot(editor)==before


@pytest.mark.parametrize('speed',[Fraction(1,4),Fraction(2,3),Fraction(3,2),Fraction(4)])
def test_affine_retime_still_rejects_without_weakening_dsp_contract(speed):
    editor=affine_editor()
    for clip in editor.project.clips[:2]:
        origin=10+clip.start*speed
        clip.source_in=origin
        clip.time_map=ClipTimeMap(origin,origin+clip.duration*speed-speed/4,speed)
    editor.project.validate();before=snapshot(editor)
    with pytest.raises(ValueError,match='1x source timing'):
        plan_linked_av_split(editor,'a',12)
    assert snapshot(editor)==before

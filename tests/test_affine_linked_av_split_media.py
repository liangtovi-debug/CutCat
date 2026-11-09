"""Real affine through-split evidence; immutable old scenes are the oracle.

Actual local Preview and Apply decode source CFR evidence. Complete production
video+audio muxes retain their original bytes, while an independent scalar PCM
oracle reads the original procedural stereo arrays. No candidate-derived timing
or expected signal is used. These tests do not claim native or lossy PCM parity.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from lumen.audio_gain import AudioGainKeyframe as Key
from lumen.clip_motion import ClipMotionKeyframe
from lumen.linked_av_split import (apply_linked_av_split, plan_linked_av_split,
                                   verify_linked_av_split)
from lumen.media import ffmpeg_available
from lumen.model import (AVLink, AV_LINK_AFFINE_ALIGNMENT, AudioMixAnchor, Clip,
                         Editor, Project)
from lumen.qualifier import WindowKeyframe
from lumen.timing import ClipTimeMap
from test_linked_audio_roll_media import boundary, render_scene, verified_scene
from test_linked_av_pcm import assert_sources_untouched, source_fingerprints
from test_linked_av_roll_media import expected_frames, raw_unit_pcm
from test_linked_av_split_media import split_source
from test_retime_video import (HEIGHT, WIDTH, assert_delivery_frames, decode,
                              engine, ff, segment_pixels)
from test_source_audio_separation_pcm import pcm, samples

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='Local FFmpeg/ffprobe required')


def affine_project(media, fps, geometry, intercept, *, animated=False, muted=False):
    """A deliberately unequal pair, fixed independent contributions and gaps."""
    video_start, video_duration = 3, 10
    audio_start, audio_duration = {'leading': (1, 14), 'trailing': (5, 12),
                                   'equal-edges': (3, 10)}[geometry]
    v_origin, a_origin = intercept+video_start, intercept+audio_start
    video = Clip('video', media.id, 0, video_start, v_origin, video_duration,
        time_map=ClipTimeMap(v_origin, v_origin+video_duration-Fraction(1,4)),
        source_audio_enabled=False, gain_db=18, fade_in=1, fade_out=2,
        audio_gain_keyframes=[Key(-71,12),Key(99999,-96)])
    audio = Clip('audio', media.id, 2, audio_start, a_origin, audio_duration,
        time_map=ClipTimeMap(a_origin, a_origin+audio_duration-Fraction(1,2)),
        muted=muted, gain_db=-4.125, fade_in=1, fade_out=2,
        audio_mix_anchor=AudioMixAnchor(0,video_start,'video'),
        audio_gain_keyframes=[Key(-67,-8),Key(1023,3,'hold'),Key(1024,-6),Key(40000,7)])
    for clip in (video,audio):
        clip.grade.qualifier.enabled=False
        clip.grade.qualifier.window_keyframes=[WindowKeyframe(-9),WindowKeyframe(7,center_x=.7),WindowKeyframe(27)]
    video.motion_keyframes=[ClipMotionKeyframe(-8),ClipMotionKeyframe(30)]
    if animated:
        video.motion_keyframes=[ClipMotionKeyframe(-8,x=.15,scale=.9),
            ClipMotionKeyframe(4,x=.6,scale=.65),ClipMotionKeyframe(30,x=.9,scale=.8)]
        video.grade.qualifier.enabled=True
        video.grade.exposure=.2
    project=Project(width=WIDTH,height=HEIGHT,fps_num=fps.numerator,fps_den=fps.denominator,
        media=[media],master_gain_db=-2.125,clips=[video,audio,
            Clip('fixed-picture',media.id,1,0,25,2,source_audio_enabled=False),
            Clip('fixed-audio',media.id,3,0,1,2,gain_db=-12,
                 audio_mix_anchor=AudioMixAnchor(0,0,'prior'))],
        av_links=[AVLink('affine-pair','video','audio',media.id,AV_LINK_AFFINE_ALIGNMENT)])
    return Editor(project)


def expected_split(old, cut, new_ids):
    """Expected records from OLD/request; no candidate or shift helper."""
    expected=deepcopy(old); records=[]
    for clip in expected.clips:
        if clip.id not in ('video','audio'):
            records.append(clip);continue
        original=deepcopy(clip); local=cut-original.start
        source_cut=Fraction(original.source_in)+local
        right=deepcopy(original)
        right.id=new_ids[0 if clip.id=='video' else 1]
        right.start,right.duration=cut,original.duration-local
        right.source_in=source_cut
        right.time_map=ClipTimeMap(source_cut,original.source_out)
        right.fade_in=0
        right.motion_keyframes=[replace(key,frame=key.frame-local) for key in original.motion_keyframes]
        right.grade.qualifier.window_keyframes=[replace(key,frame=key.frame-local)
            for key in original.grade.qualifier.window_keyframes]
        offset=boundary(cut,old.fps)-boundary(original.start,old.fps)
        right.audio_gain_keyframes=[replace(key,sample=key.sample-offset)
            for key in original.audio_gain_keyframes]
        clip.duration,clip.fade_out=local,0
        clip.time_map=ClipTimeMap(original.source_in,source_cut)
        # Explicit existing canonical wire rule, not production normalization.
        for item in (clip,right):
            if Fraction(item.source_in).denominator==1 and item.source_out-item.source_in==item.duration:
                item.source_in=int(item.source_in);item.time_map=None
        records.extend((clip,right))
    expected.clips=records
    expected.av_links.append(replace(old.av_links[0],id=new_ids[2],
        video_clip_id=new_ids[0],audio_clip_id=new_ids[1]))
    return expected


def assert_own_tails(old, new, new_ids, cut):
    prior={c.id:c for c in old.clips}; current={c.id:c for c in new.clips}
    for role,right_id in zip(('video','audio'),new_ids):
        original,left,right=prior[role],current[role],current[right_id]
        assert left.start==original.start and left.end==right.start==cut and right.end==original.end
        assert left.source_in==original.source_in and left.source_out==right.source_in
        assert right.source_out==original.source_out
        assert right.duration-(right.source_out-right.source_in)==original.duration-(original.source_out-original.source_in)
        assert left.source_in-left.start==right.source_in-right.start==original.source_in-original.start
        assert (left.fade_in,right.fade_out)==(original.fade_in,original.fade_out)
        assert left.fade_out==right.fade_in==0
        assert left.audio_mix_anchor==right.audio_mix_anchor==original.audio_mix_anchor
        assert left.source_audio_enabled==right.source_audio_enabled==original.source_audio_enabled
    assert all(link.alignment==AV_LINK_AFFINE_ALIGNMENT for link in new.av_links)
    assert new.duration_frames==old.duration_frames


CASES=[
    pytest.param(Fraction(24), 0, 'leading', Fraction(5), False, False, id='24-partial-tail-switches-left-to-legacy'),
    pytest.param(Fraction(24), .3, 'leading', Fraction(45,8), False, False, id='24-J-leading-silence'),
    pytest.param(Fraction(30), 0, 'trailing', Fraction(45,8), True, False, id='30-L-motion-window'),
    pytest.param(Fraction(24000,1001), -.1, 'equal-edges', Fraction(45,8), False, False, id='23.976-equal-edges-unequal-tails'),
    pytest.param(Fraction(30000,1001), 0, 'leading', Fraction(45,8), False, False, id='29.97-own-sample-origins'),
    pytest.param(Fraction(60000,1001), 0, 'trailing', Fraction(25,4), False, False, id='59.94-L-unequal-tails'),
    pytest.param(Fraction(24), 0, 'leading', Fraction(5,8), False, True, id='24-actual-audio-EOF-inside-right'),
]


@pytest.mark.parametrize('fps,offset,geometry,intercept,animated,short_audio',CASES)
def test_affine_split_real_source_full_scene_and_pcm_conservation(
        tmp_path,engine,fps,offset,geometry,intercept,animated,short_audio):
    media,pictures,raw=split_source(tmp_path,fps,offset,short_audio)
    paths=(tmp_path/'numbered.mov',Path(media.path)); fingerprints=source_fingerprints(paths)
    editor=affine_project(media,fps,geometry,intercept,animated=animated)
    before=deepcopy(editor.project);raw_by_id={media.id:raw};cut=4 if geometry=='leading' else 6
    wanted=raw_unit_pcm(before,raw_by_id)
    old_scene,old_pcm=verified_scene(engine,before,before,raw_by_id,tmp_path/'before.mkv')
    old_segment=segment_pixels(engine,before,before.clips[0])
    if not animated:
        expected_segments,expected_scene=expected_frames(before,{media.id:pictures})
        np.testing.assert_array_equal(old_segment,expected_segments['video'])
        assert_delivery_frames(old_scene,expected_scene)
    plan=plan_linked_av_split(editor,'audio',cut)
    expected=expected_split(before,cut,plan.new_ids)
    # Conservation is established independently before calling Apply.
    assert raw_unit_pcm(expected,raw_by_id)==wanted
    preview=verify_linked_av_split(plan)
    assert editor.project==before and not editor._undo
    verified=verify_linked_av_split(plan,preview)
    right_video,right_audio=apply_linked_av_split(editor,plan,verified)
    assert editor.project==expected and len(editor._undo)==1 and editor.revision==1
    assert_own_tails(before,editor.project,plan.new_ids,cut)
    scene,actual=verified_scene(engine,editor.project,before,raw_by_id,tmp_path/'after.mkv',picture_reference=old_scene)
    assert actual==old_pcm
    np.testing.assert_array_equal(np.concatenate((segment_pixels(engine,editor.project,editor._clip('video')),
        segment_pixels(engine,editor.project,right_video))),old_segment)
    if fps==Fraction(30000,1001):
        qv=boundary(cut,fps)-boundary(3,fps);qa=boundary(cut,fps)-boundary(1,fps)
        assert (qv,qa,boundary(cut-3,fps),boundary(cut-1,fps))==(1601,4804,1602,4805)
        for wrong_delta in (qv,boundary(cut-1,fps)):
            wrong=deepcopy(expected);wa=next(c for c in wrong.clips if c.id==right_audio.id)
            wa.audio_gain_keyframes=[replace(k,sample=k.sample-wrong_delta) for k in before.clips[1].audio_gain_keyframes]
            assert raw_unit_pcm(wrong,raw_by_id)!=wanted,'Fixture must expose incorrect role/sample-origin rebasing'
        filled=deepcopy(expected)
        for clip in filled.clips:
            if clip.id in plan.new_ids[:2]:clip.time_map=ClipTimeMap(clip.source_in,clip.source_in+clip.duration)
        assert raw_unit_pcm(filled,raw_by_id)!=wanted,'Fixture must expose audio tail loss'
    if animated:
        wrong=deepcopy(expected);wv=next(c for c in wrong.clips if c.id==right_video.id)
        wv.motion_keyframes=deepcopy(before.clips[0].motion_keyframes)
        wrong_scene,_=render_scene(engine,wrong,tmp_path/'wrong-unshifted-motion.mkv')
        assert not np.array_equal(wrong_scene,old_scene),'Full-scene oracle must expose missing motion rebase'
    if short_audio:
        # The real stream ends at 16,800 samples, after the cut and before A Out.
        a=before.clips[1];origin=boundary(a.source_in,fps)
        eof=boundary(a.start,fps)+len(raw)-origin
        assert boundary(cut,fps)<eof<boundary(a.end,fps)
        rendered=samples(actual)
        assert np.count_nonzero(rendered[boundary(cut,fps):eof])
        assert not np.count_nonzero(rendered[eof:boundary(a.end,fps)])
        assert len(samples(ff('-i',media.path,'-map','0:a:0','-vn','-ar','48000','-ac','2',
            '-c:a','pcm_f32le','-f','f32le','pipe:1')))==16800
    saved=tmp_path/'split.lumen';editor.project.save(saved);reopened=Project.load(saved)
    assert reopened==expected and pcm(reopened)==old_pcm
    assert editor.undo() and editor.project==before and pcm(editor.project)==old_pcm
    assert editor.redo() and editor.project==expected and pcm(editor.project)==old_pcm
    if fps==Fraction(30000,1001):
        for label,project in [('reopened',reopened),('redo',editor.project)]:
            verified_scene(engine,project,before,raw_by_id,tmp_path/f'{label}.mkv',picture_reference=old_scene)
    assert_sources_untouched(paths,fingerprints)
    (tmp_path/'affine-split-proof.json').write_text(json.dumps({
        'basis':'Immutable old maps, source RGB16 and original stereo samples; actual Preview and Apply',
        'fps':str(fps),'cut':cut,'source_sha256':hashlib.sha256(Path(media.path).read_bytes()).hexdigest(),
        'frame_sha256':hashlib.sha256(scene.tobytes()).hexdigest(),
        'pcm_sha256':hashlib.sha256(actual).hexdigest(),'frames':len(scene),'samples':len(actual)//8,
        'actual_audio_eof_tested':short_audio,'full_scene_motion_negative_control':animated},indent=2)+'\n')


def test_affine_split_muted_then_unmute_retains_potential_pcm(tmp_path,engine):
    fps=Fraction(30000,1001);media,pictures,raw=split_source(tmp_path,fps)
    editor=affine_project(media,fps,'leading',Fraction(45,8),muted=True)
    before=deepcopy(editor.project);raw_by_id={media.id:raw}
    old_scene,old_pcm=verified_scene(engine,before,before,raw_by_id,tmp_path/'muted-before.mkv')
    potential=deepcopy(before);potential.clips[1].muted=False
    plan=plan_linked_av_split(editor,'video',4);receipt=verify_linked_av_split(plan)
    _,right=apply_linked_av_split(editor,plan,verify_linked_av_split(plan,receipt))
    assert editor._clip('audio').muted and right.muted
    verified_scene(engine,editor.project,before,raw_by_id,tmp_path/'muted-after.mkv',picture_reference=old_scene)
    editor.update_clip('audio',muted=False);editor.update_clip(right.id,muted=False)
    scene,actual=verified_scene(engine,editor.project,potential,raw_by_id,tmp_path/'unmuted.mkv',picture_reference=old_scene)
    assert actual!=old_pcm and actual==pcm(potential)
    assert not editor._clip('video').source_audio_enabled and not editor._clip(plan.new_ids[0]).source_audio_enabled
    wrong=deepcopy(editor.project);wrong.clips[0].source_audio_enabled=True
    assert raw_unit_pcm(wrong,raw_by_id)!=raw_unit_pcm(potential,raw_by_id)


def test_affine_split_real_preview_export_cache_and_roundtrip(tmp_path,engine):
    fps=Fraction(30000,1001);media,pictures,raw=split_source(tmp_path,fps)
    editor=affine_project(media,fps,'leading',Fraction(45,8));before=deepcopy(editor.project)
    paths=(tmp_path/'numbered.mov',Path(media.path));fingerprints=source_fingerprints(paths)
    _,frames=expected_frames(before,{media.id:pictures})
    old_preview=engine.preview(before);assert_delivery_frames(decode(old_preview,rgb16=False),frames)
    plan=plan_linked_av_split(editor,'audio',4);receipt=verify_linked_av_split(plan)
    apply_linked_av_split(editor,plan,verify_linked_av_split(plan,receipt))
    current=engine.preview(editor.project)
    assert current!=old_preview and engine.preview(editor.project)==current
    exported=engine.render(editor.project,tmp_path/'affine-split-export.mp4')
    audios=[]
    for path in (current,exported):
        assert_delivery_frames(decode(path,rgb16=False),frames)
        audio=ff('-i',path,'-map','0:a:0','-vn','-ar','48000','-ac','2','-c:a','pcm_f32le','-f','f32le','pipe:1')
        assert boundary(before.duration_frames,fps)<=len(samples(audio))<boundary(before.duration_frames,fps)+1024
        audios.append(audio)
    assert audios[0]==audios[1]  # Identical AAC paths; not AAC-versus-lossless PCM.
    saved=tmp_path/'cache.lumen';editor.project.save(saved);reopened=Project.load(saved)
    assert reopened==editor.project and engine.preview(reopened)==current
    assert editor.undo() and editor.project==before and engine.preview(editor.project)==old_preview
    assert editor.redo() and engine.preview(editor.project)==current
    assert_sources_untouched(paths,fingerprints)

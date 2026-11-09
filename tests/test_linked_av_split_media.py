"""Real through-split evidence from original numbered RGB/stereo, never candidate oracles.

Both Preview and Apply execute actual source qualification. Original source bytes,
source-frame IDs, original complete rendered scenes and original complete stereo
PCM are captured BEFORE the operation and compared after split/save/undo/redo.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pytest

from lumen.audio_gain import AudioGainKeyframe as Key
from lumen.clip_motion import ClipMotionKeyframe
from lumen.linked_av_split import (apply_linked_av_split, plan_linked_av_split,
                                   verify_linked_av_split)
from lumen.media import ffmpeg_available, probe
from lumen.model import AudioMixAnchor, Clip, Editor, Project
from lumen.qualifier import WindowKeyframe
from lumen.source_viewer import SourceViewerError
from test_audio_mix import _pcm
from test_gap_close_render import lossless_scene
from test_linked_av_trim_media import raw_unit_pcm, sample
from test_retime_video import (HEIGHT, WIDTH, decode, engine, ff, frame_id_rgb,
                              segment_pixels)
from test_source_audio_separation_pcm import pcm, samples
from synthetic_offset_diagnostics import collect_synthetic_offset_evidence

pytestmark=pytest.mark.skipif(not ffmpeg_available(),reason='Local FFmpeg/ffprobe required')


def split_source(root,fps,offset=0,short_audio=False,vfr=False):
    pictures=frame_id_rgb();picture=root/'numbered.mov';path=root/'source.mov'
    # True16-bit PNG retains frame-ID source pixels exactly; MOV's explicit720000Hz
    # timebase represents all chosen project rates without millisecond jitter.
    args=['-f','rawvideo','-pix_fmt','rgb48le','-s',f'{WIDTH}x{HEIGHT}','-r',str(fps),'-i','pipe:0']
    if vfr:args+=['-vf',"settb=1/720000,setpts=N*24000+if(mod(N\\,2)\\,3600\\,0)"]
    ff(*args,'-fps_mode','passthrough','-an','-c:v','png','-pix_fmt','rgb48be',
       '-video_track_timescale','720000','-color_range','pc','-color_primaries','bt709',
       '-color_trc','bt709','-colorspace','rgb',picture,data=pictures.astype('<u2').tobytes())
    np.testing.assert_array_equal(decode(picture),pictures)
    count=16800 if short_audio else 48000*4;n=np.arange(count,dtype=np.int64)
    raw=np.column_stack((((n*17+n//127)%1024-512)/1024,
                          ((n*31+n//251)%1024-512)/2048)).astype('<f4')
    raw[::997,0]=.75;raw[::1423,1]=-.625
    # Set encoded audio timestamps explicitly: FFmpeg 9 rebases raw-input
    # -itsoffset during encoding. MOV edit lists retain unequal track starts.
    ff('-i',picture,'-f','f32le','-ar','48000','-ac','2','-i','pipe:0',
       '-map','0:v','-map','1:a','-af',f'asetpts=PTS+({offset})/TB','-c:v','copy','-c:a','pcm_f32le','-video_track_timescale','720000','-avoid_negative_ts','make_non_negative','-use_editlist','1',path,data=raw.tobytes())
    media=probe(path)
    assert Fraction(media.fps_num,media.fps_den)==fps or vfr
    try:
        assert media.audio_offset==offset
    except AssertionError as exc:
        try:
            diagnostic = collect_synthetic_offset_evidence(path)
        except Exception:
            diagnostic = {'kind': 'synthetic_split_source_offset', 'status': 'unavailable'}
        exc.add_note(json.dumps(diagnostic, sort_keys=True))
        raise
    np.testing.assert_array_equal(decode(path),pictures)
    decoded_audio=ff('-i',path,'-map','0:a:0','-vn','-ar','48000','-ac','2',
                     '-c:a','pcm_f32le','-f','f32le','pipe:1')
    np.testing.assert_array_equal(samples(decoded_audio),raw)
    return media,pictures,raw


def split_project(media,fps,*,gain=False,animated=False,muted=False):
    # Source6 and timeline1 share phase at29.97/59.94, but the accepted cut1->4
    # needs S(4)-S(1), not S(3); this kills a duration-rounded gain-key rebase.
    v=Clip('v',media.id,0,1,6,10,source_audio_enabled=False,muted=True,gain_db=12,fade_in=2,fade_out=3)
    a=Clip('a',media.id,2,1,6,10,muted=muted,audio_mix_anchor=AudioMixAnchor(0,1,'v'),fade_in=2,fade_out=3)
    for clip in (v,a):
        clip.grade.qualifier.enabled=False
        clip.grade.qualifier.window_keyframes=[WindowKeyframe(-9),WindowKeyframe(27,center_x=.7)]
        clip.audio_gain_keyframes=[Key(-67,-8),Key(1023,3,'hold'),Key(1024,-6),Key(40000,7)] if gain else []
    if gain:a.gain_db=-4.125
    if animated:
        v.motion_keyframes=[ClipMotionKeyframe(-8,x=.15,scale=.9),ClipMotionKeyframe(4,x=.6,scale=.65),ClipMotionKeyframe(30,x=.9,scale=.8)]
        v.grade.qualifier.enabled=True
        v.grade.qualifier.window_keyframes=[WindowKeyframe(-5),WindowKeyframe(8,center_x=.7),WindowKeyframe(30,center_x=.5)]
        v.grade.exposure=.2
    project=Project(width=WIDTH,height=HEIGHT,fps_num=fps.numerator,fps_den=fps.denominator,media=[media],clips=[v,a,
        Clip('background',media.id,1,14,30,2,source_audio_enabled=False),
        Clip('mix-before',media.id,3,0,1,13,gain_db=-12,audio_mix_anchor=AudioMixAnchor(0,0,'prior')),
        Clip('mix-after',media.id,3,14,2,2,gain_db=-18)])
    editor=Editor(project);editor.link_av_clips('v','a');return editor


CASES=[(Fraction(24),0,False,False,False),
       (Fraction(30),0,True,True,False),
       (Fraction(24000,1001),0,True,False,False),
       (Fraction(30000,1001),0,True,False,False),
       (Fraction(60000,1001),0,True,False,False),
       (Fraction(24),.3,True,False,False),
       (Fraction(24),-.1,True,False,False),
       (Fraction(24),0,True,False,True)]


@pytest.mark.parametrize('fps,offset,gain,animated,short_audio',CASES,
    ids=['24-plain','30-animation','23.976-gain','29.97-sample-phase','59.94-sample-phase','leading-silence','early-offset','short-audio-padding'])
def test_real_worker_through_split_picture_and_pcm_conservation(tmp_path,engine,fps,offset,gain,animated,short_audio):
    media,pictures,raw=split_source(tmp_path,fps,offset,short_audio)
    editor=split_project(media,fps,gain=gain,animated=animated)
    before=deepcopy(editor.project);source_paths=(tmp_path/'numbered.mov',Path(media.path))
    def fingerprints():
        return {str(path):(hashlib.sha256(path.read_bytes()).hexdigest(),path.stat().st_mtime_ns,path.stat().st_mode,path.stat().st_size) for path in source_paths}
    fingerprint=fingerprints()
    old_pcm=pcm(before);independent=raw_unit_pcm(before,raw)
    np.testing.assert_array_max_ulp(samples(old_pcm),samples(independent),maxulp=2)
    old_segment=segment_pixels(engine,before,before.clips[0])
    if not animated:np.testing.assert_array_equal(old_segment,pictures[6:16])
    old_scene=lossless_scene(engine,before,tmp_path/'old.mkv')
    plan=plan_linked_av_split(editor,'a',4);preview=verify_linked_av_split(plan)
    assert editor.project==before
    verified=verify_linked_av_split(plan,preview);right_v,right_a=apply_linked_av_split(editor,plan,verified)
    assert editor.project.duration_frames==before.duration_frames
    joined=np.concatenate([segment_pixels(engine,editor.project,editor._clip('v')),segment_pixels(engine,editor.project,right_v)])
    np.testing.assert_array_equal(joined,old_segment)
    np.testing.assert_array_equal(lossless_scene(engine,editor.project,tmp_path/'after.mkv'),old_scene)
    actual=pcm(editor.project)
    assert actual==old_pcm
    assert len(actual)==sample(before.duration_frames,fps)*8
    inputs,graph,_=engine._compile(editor.project)
    assert _pcm(inputs,graph,video_output=os.devnull)==old_pcm
    if fps==Fraction(30000,1001):
        assert sample(4,fps)-sample(1,fps)==4804 and sample(3,fps)==4805
        wrong=deepcopy(editor.project);wrong_a=next(c for c in wrong.clips if c.id==right_a.id)
        wrong_a.audio_gain_keyframes=[replace(key,sample=key.sample-4805) for key in before.clips[1].audio_gain_keyframes]
        assert pcm(wrong)!=old_pcm
    saved=tmp_path/'split.lumen';editor.project.save(saved);reopened=Project.load(saved)
    assert reopened==editor.project and pcm(reopened)==old_pcm
    after=deepcopy(editor.project);editor.undo();assert editor.project==before and pcm(editor.project)==old_pcm
    editor.redo();assert editor.project==after and pcm(editor.project)==old_pcm
    assert fingerprints()==fingerprint


def test_real_user_muted_pair_preserves_potential_audio_after_unmute(tmp_path,engine):
    fps=Fraction(30);media,pictures,raw=split_source(tmp_path,fps)
    editor=split_project(media,fps,gain=True,muted=True);before=deepcopy(editor.project)
    potential=deepcopy(before);next(c for c in potential.clips if c.id=='a').muted=False
    old=pcm(potential);plan=plan_linked_av_split(editor,'v',4)
    receipt=verify_linked_av_split(plan);receipt=verify_linked_av_split(plan,receipt)
    _,right=apply_linked_av_split(editor,plan,receipt)
    assert editor._clip('a').muted and right.muted
    editor._clip('a').muted=right.muted=False
    assert pcm(editor.project)==old


def test_real_vfr_rejected_even_if_container_fps_claims_are_patched(tmp_path,monkeypatch):
    import lumen.source_viewer as viewer
    fps=Fraction(30);media,_,_=split_source(tmp_path,fps,vfr=True)
    media.fps_num,media.fps_den=30,1
    editor=split_project(media,fps);plan=plan_linked_av_split(editor,'v',4);before=deepcopy(editor.project)
    original=viewer._run
    def claimed(args,*a,**kw):
        result=original(args,*a,**kw)
        if '-show_streams' in args:
            data=json.loads(result)
            for stream in data['streams']:
                if stream.get('codec_type')=='video':stream['avg_frame_rate']=stream['r_frame_rate']='30/1'
            return json.dumps(data).encode()
        return result
    monkeypatch.setattr(viewer,'_run',claimed)
    with pytest.raises(SourceViewerError,match='grid|durations|variable-rate|incomplete'):
        verify_linked_av_split(plan)
    assert editor.project==before


def test_real_file_replacement_between_preview_and_apply_is_rejected(tmp_path):
    fps=Fraction(30);media,_,_=split_source(tmp_path,fps);editor=split_project(media,fps)
    plan=plan_linked_av_split(editor,'v',4);preview=verify_linked_av_split(plan);before=deepcopy(editor.project)
    path=Path(media.path);content=path.read_bytes();path.write_bytes(content)
    with pytest.raises(ValueError,match='dependencies changed'):verify_linked_av_split(plan,preview)
    assert editor.project==before

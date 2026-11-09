"""Actual two-source handles, analytic RGB16 blend and unchanged PCM integration."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
import hashlib
import subprocess
import threading

import numpy as np
import pytest

from lumen.media import ffmpeg_binary, probe
from lumen.model import Project, Clip, Editor, Grade, ClipEffect
from lumen.render import RenderEngine, RenderError, RenderCancelled
from lumen.video_transition import VideoTransition
from lumen.video_transition_render import prepare_video_transitions, picture_filters, blend_filter


def command(args, data=None):
    result = subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', *args], input=data,
                            capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr.decode()
    return result.stdout


def fixture(tmp_path, fps=Fraction(30), width=64, height=32, count=24, audio=False):
    frames=[];media=[]
    x=np.arange(width,dtype=np.uint32)[None,:]
    y=np.arange(height,dtype=np.uint32)[:,None]
    for side in range(2):
        a=np.stack([np.stack(np.broadcast_arrays((x*701+i*181+side*12001)%60000,
            (y*401+i*307+side*901)%60000, (x*231+y*113+i*509+side*7001)%60000),-1)
            for i in range(count)]).astype('<u2')
        path=tmp_path/f'side{side}.mkv'
        args=['-f','rawvideo','-pix_fmt','rgb48le','-s',f'{width}x{height}','-r',str(fps),'-i','pipe:0']
        if audio: args += ['-f','lavfi','-i',f'sine=frequency={220+side*110}:sample_rate=48000:duration=2']
        args += ['-c:v','ffv1','-pix_fmt','gbrp16le','-threads','1']
        if audio: args += ['-c:a','pcm_s16le']
        args += ['-color_range','pc','-colorspace','rgb','-color_primaries','bt709','-color_trc','bt709',str(path)]
        command(args,a.tobytes());frames.append(a);media.append(probe(path))
    project=Project(width=width,height=height,fps_num=fps.numerator,fps_den=fps.denominator,media=media,
        clips=[Clip('left',media[0].id,0,0,2,10),Clip('right',media[1].id,0,10,8,10)])
    return project,frames


def decode_rgb(path,w,h):
    raw=command(['-i',str(path),'-map','0:V:0','-pix_fmt','rgb48le','-fps_mode','passthrough',
                 '-f','rawvideo','pipe:1'])
    return np.frombuffer(raw,dtype='<u2').reshape(-1,h,w,3)


def compile_raw(engine,project,high=False):
    inputs,graph,duration=engine._compile(project,export_profile='prores_422_hq_10bit' if high else 'h264_8bit')
    raw=command(['-filter_complex_threads','1',*inputs,'-filter_complex',graph,'-map','[vout]',
                 '-map','[aout]','-c:v','rawvideo','-c:a','pcm_s16le','-f','nut','pipe:1'])
    return raw,graph


@pytest.mark.parametrize('duration',[2,3,4,5,8,9])
@pytest.mark.parametrize('high',[False,True])
def test_actual_numbered_handles_analytic_rgb16(tmp_path,duration,high):
    project,frames=fixture(tmp_path)
    project.video_transitions=[VideoTransition('t','left','right',duration)]
    engine=RenderEngine()
    try:
        prepared,_=prepare_video_transitions(engine,project,high)
        got=decode_rgb(prepared[0].path,project.width,project.height)
        L=duration//2;R=duration-L
        weights=np.arange(duration,dtype=np.float64)[:,None,None,None]/(duration-1)
        expected=frames[0][12-L:12+R]*(1-weights)+frames[1][8-L:8+R]*weights
        assert len(got)==duration
        np.testing.assert_allclose(got,expected,atol=1.01,rtol=0)
        np.testing.assert_array_equal(got[0],frames[0][12-L])
        np.testing.assert_array_equal(got[-1],frames[1][8+R-1])
    finally:engine.close()


@pytest.mark.parametrize('high',[False,True])
@pytest.mark.parametrize('fps',[Fraction(30),Fraction(30000,1001)])
def test_final_track_output_and_pcm_unchanged(tmp_path,high,fps):
    project,_=fixture(tmp_path,fps,audio=True)
    engine=RenderEngine()
    try:
        before,bg=compile_raw(engine,project,high)
        project.video_transitions=[VideoTransition('t','left','right',5)]
        after,ag=compile_raw(engine,project,high)
        outputs=[]
        for label,data in [('before',before),('after',after)]:
            path=tmp_path/(label+'.nut');path.write_bytes(data)
            pcm=command(['-i',str(path),'-map','0:a','-f','s16le','pipe:1'])
            pic=decode_rgb(path,project.width,project.height)
            outputs.append((pcm,pic))
        assert outputs[0][0]==outputs[1][0]
        assert len(outputs[1][1])==20
        np.testing.assert_array_equal(outputs[0][1][:8],outputs[1][1][:8])
        np.testing.assert_array_equal(outputs[0][1][13:],outputs[1][1][13:])
        assert not np.array_equal(outputs[0][1][9:12],outputs[1][1][9:12])
        assert bg.split('[silence]')[0] == ag.split('[silence]')[0]
        assert project.clips[0].source_in==2 and project.clips[0].duration==10
    finally:engine.close()


@pytest.mark.parametrize('high',[False,True])
def test_upper_track_occludes_lower_transition(tmp_path,high):
    project,_=fixture(tmp_path)
    project.clips.append(Clip('top',project.media[1].id,1,0,0,20))
    engine=RenderEngine()
    try:
        before,_=compile_raw(engine,project,high)
        project.video_transitions=[VideoTransition('t','left','right',5)]
        after,graph=compile_raw(engine,project,high)
        for label,data in [('before',before),('after',after)]: (tmp_path/(label+'.nut')).write_bytes(data)
        np.testing.assert_array_equal(decode_rgb(tmp_path/'before.nut',64,32),decode_rgb(tmp_path/'after.nut',64,32))
        assert graph.index('[transition0]')<graph.index('[over2]')
    finally:engine.close()


def test_cancel_publishes_no_transition(tmp_path):
    project,_=fixture(tmp_path);project.video_transitions=[VideoTransition('t','left','right',5)]
    event=threading.Event();event.set();engine=RenderEngine()
    try:
        with pytest.raises(RenderCancelled):prepare_video_transitions(engine,project,cancel=event)
        assert not list(Path(engine._cache_dir.name).rglob('*.mkv'))
    finally:engine.close()


def test_new_transition_graph_does_not_opt_into_animation_threading(tmp_path):
    from lumen.render import _composite_filter_threads
    from lumen.clip_motion import ClipMotionKeyframe
    project,_=fixture(tmp_path)
    project.video_transitions=[VideoTransition('t','left','right',5)]
    project.clips.append(Clip('top',project.media[0].id,1,0,0,20,
                             motion_keyframes=[ClipMotionKeyframe(0,x=1)]))
    project.validate()
    assert _composite_filter_threads(project)==1

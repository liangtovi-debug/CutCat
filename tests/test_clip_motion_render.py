"""Actual numbered media, fractional retime, output geometry and PCM acceptance."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import os
from pathlib import Path
import wave

import numpy as np
import pytest
from lumen.clip_motion import ClipMotionKeyframe as K
from lumen.clip_composite import ClipComposite
from lumen.clip_transform import ClipTransform
from lumen.model import Clip, Project, ClipEffect
from lumen.media import probe,ffmpeg_available
from lumen.render import RenderEngine
from lumen.timing import ClipTimeMap
from test_color_highbit_reference import rgb_source,ff,video_metadata
from test_highbit_caption_reference import latin_font
from test_clip_composite_pixels import independent_blend,unpack_planes

W,H=64,48
pytestmark=pytest.mark.skipif(not ffmpeg_available(),reason='FFmpeg required')


@pytest.fixture(scope='module',params=[Fraction(24000,1001),Fraction(30000,1001)])
def sources(request,tmp_path_factory):
    rate=request.param;root=tmp_path_factory.mktemp('moving-pip')
    upper=[];lower=[]
    for n in range(26):
        rgb=np.zeros((H,W,3),np.uint16);rgb[:24,32:]=65535
        rgb[24:,:32]=(16+n*8)*257;rgb[24:,32:]=(65535,n*4*257,0)
        upper.append(rgb)
        bg=np.empty_like(rgb);bg[:]=(30+n,100,180);lower.append(bg*257)
    media=[]
    for i,arrays in enumerate((lower,upper)):
        video=rgb_source(root,np.stack(arrays),name=f'numbered-{i}',fps=rate)
        sound=root/f'sound-{i}.wav';count=round(26*48000/rate)
        data=np.rint(np.sin(np.arange(count)*(431+i*317)*2*np.pi/48000)*7000).astype('<i2')
        with wave.open(str(sound),'wb') as out:
            out.setparams((1,2,48000,0,'NONE','not compressed'));out.writeframes(data.tobytes())
        target=root/f'av-{i}.mkv';ff('-i',video.path,'-i',sound,'-c','copy',target);media.append(probe(target))
    return rate,media,np.stack(upper)


@pytest.fixture
def engine():
    e=RenderEngine(timeout=90)
    yield e
    e.close()


def project(sources,speed=Fraction(1),*,animated=True):
    fps,media,_=sources
    keys=[K(0,x=-4,y=2,scale=.5,opacity=0),K(4,x=4,y=-2,scale=.7,opacity=1)] if animated else []
    return Project(width=W,height=H,fps_num=fps.numerator,fps_den=fps.denominator,media=deepcopy(media),clips=[
        Clip('bottom',media[0].id,0,0,0,10),
        Clip('top',media[1].id,1,3,0,5,time_map=ClipTimeMap(Fraction(5,2),Fraction(5,2)+Fraction(9,2)*speed,speed),
             composite=ClipComposite(True),motion_keyframes=keys)])


def compiled(engine,p,high=False):
    profile='prores_422_hq_10bit' if high else 'h264_8bit';fmt='yuv422p10le' if high else 'yuv420p'
    inputs,graph,_=engine._compile(p,export_profile=profile)
    raw=ff('-filter_complex_threads','1',*inputs,'-filter_complex',graph,'-map','[vout]',
        '-frames:v',p.duration_frames,'-r',f'{p.fps_num}/{p.fps_den}','-fps_mode','cfr','-c:v','rawvideo','-threads','1',
        '-pix_fmt',fmt,'-f','rawvideo','pipe:1','-map','[aout]','-f','null',os.devnull)
    size=W*H*(4 if high else 1.5);size=int(size)
    assert len(raw)==p.duration_frames*size
    return [unpack_planes(raw[i*size:(i+1)*size],W,H,high) for i in range(p.duration_frames)],graph


def pcm(engine,p,tmp_path,name):
    inputs,graph,_=engine._compile(p)
    raw=ff('-filter_complex_threads','1',*inputs,'-filter_complex',graph,
        '-map','[aout]','-c:a','pcm_f32le','-f','f32le','pipe:1','-map','[vout]','-f','null',os.devnull)
    return raw,graph


@pytest.mark.parametrize('speed',[Fraction(1,4),Fraction(3,2),Fraction(4)])
@pytest.mark.parametrize('high',[False,True])
def test_actual_numbered_fractional_source_tail_local_N(sources,engine,speed,high):
    p=project(sources,speed);top=p.clips[1]
    actual,graph=compiled(engine,p,high)
    bottom=deepcopy(p);bottom.clips=bottom.clips[:1];under,_=compiled(engine,bottom,high)
    segment=engine._graded_segment(p.media[1],top,p,None)
    raw=ff('-i',segment,'-pix_fmt','gbrp16le','-fps_mode','passthrough','-f','rawvideo','pipe:1')
    prepared=np.frombuffer(raw,'<u2').reshape(5,3,H,W).transpose(0,2,3,1)[...,[2,0,1]]
    indices=[int(Fraction(5,2)+k*speed) for k in range(5)]
    for k,index in enumerate(indices):np.testing.assert_allclose(prepared[k],sources[2][index],atol=3,rtol=0)
    for frame in range(10):
        if not 3<=frame<8:
            for a,b in zip(actual[frame],under[frame]):np.testing.assert_array_equal(a,b)
            continue
        k=frame-3
        expected,clean=independent_blend(prepared[k],under[frame],ClipComposite(True,k/4),
            ClipTransform(x=-4+2*k,y=2-k,scale=.5+.05*k),high)
        for a,b,c,u in zip(actual[frame],expected,clean,under[frame]):
            np.testing.assert_allclose(a,b,atol=3,rtol=0);np.testing.assert_array_equal(a[c],u[c])
    assert 'N' in graph and 'setpts=PTS+3' in graph
    if high: assert 'format=rgba' not in graph and 'format=gbrp,' not in graph


@pytest.mark.parametrize('speed',[Fraction(1,4),Fraction(3,2),Fraction(4)])
def test_motion_cannot_change_pcm_or_fractional_sample_budget(sources,engine,tmp_path,speed):
    p=project(sources,speed);a,graph=pcm(engine,p,tmp_path,'animated')
    p.clips[1].motion_keyframes=[];b,static=pcm(engine,p,tmp_path,'static')
    assert a==b
    frames=Fraction(p.duration_frames*48000*p.fps_den,p.fps_num)
    expected=(2*frames.numerator+frames.denominator)//(2*frames.denominator)
    assert len(a)==expected*2*4
    assert [x for x in graph.split(';') if ':a:0]' in x]==[x for x in static.split(';') if ':a:0]' in x]


@pytest.mark.parametrize('high',[False,True])
def test_legacy_static_settings_do_not_bypass_animated_opacity(sources,engine,high):
    p=project(sources);p.clips[1].composite=ClipComposite()
    a,graph=compiled(engine,p,high)
    assert 'alpha=straight' in graph
    assert '65535*ld(5)' in graph
    bottom=deepcopy(p);bottom.clips=bottom.clips[:1];b,_=compiled(engine,bottom,high)
    for x,y in zip(a[3],b[3]):np.testing.assert_array_equal(x,y)


def test_grade_cache_reused_after_motion_edit(sources,engine):
    p=project(sources);c=p.clips[1]
    first=engine._graded_segment(p.media[1],c,p,None);stamp=first.stat().st_mtime_ns
    c.motion_keyframes=[K(0,x=80,opacity=.2),K(4,x=-20,opacity=.8)]
    second=engine._graded_segment(p.media[1],c,p,None)
    assert first==second and second.stat().st_mtime_ns==stamp


@pytest.mark.parametrize('high',[False,True])
def test_delivery_codec_and_preview_render_animation(sources,engine,tmp_path,high):
    p=project(sources);p.clips[1].muted=True;p.clips[0].muted=True
    target=tmp_path/('motion.mov' if high else 'motion.mp4')
    engine.render(p,target,export_profile='prores_422_hq_10bit' if high else 'h264_8bit')
    info=video_metadata(target);assert info['pix_fmt']==('yuv422p10le' if high else 'yuv420p')
    raw=ff('-i',target,'-pix_fmt','rgb24','-f','rawvideo','pipe:1')
    frames=np.frombuffer(raw,np.uint8).reshape(-1,H,W,3);assert len(frames)==10
    # Frame3 has zero alpha; frame7 has moved/scaled opaque white quadrant.
    assert np.max(abs(frames[3,20,40].astype(float)-[33,100,180]))<12
    assert np.min(frames[7,18,40])>235
    if not high:
        proxy=engine.preview(p);assert proxy.exists()
        previous=proxy;p.clips[1].motion_keyframes=[K(0,opacity=0),K(4,opacity=0)]
        assert engine.preview(p)!=previous


@pytest.mark.parametrize('high',[False,True])
def test_effect_grade_animation_caption_sticker_order(sources,engine,tmp_path,high,latin_font):
    from lumen.qualifier import QualifierSettings,WindowKeyframe
    from lumen.model import Caption,CaptionStyle,OverlayKeyframe
    from test_overlay_render import sticker
    p=project(sources);top=p.clips[1]
    top.transform=ClipTransform(rotation=19,mirror_x=True)
    top.effects=[ClipEffect('blur','blur',1),ClipEffect('vig','vignette',.2,radius=.8,feather=.9)]
    top.grade.qualifier=QualifierSettings(enabled=True,hue_width=.5,exposure=.2,window_shape='ellipse',
        window_keyframes=[WindowKeyframe(0,.25,.5,.6,.6),WindowKeyframe(4,.75,.5,.6,.6)])
    overlay,_=sticker(tmp_path,color=(255,0,0,255),start=0,duration=10,keyframes=[OverlayKeyframe(0,x=.1,y=.1,scale=.4)])
    p.overlays=[overlay]
    p.captions=[Caption('caption',0,10,'A')]
    p.caption_style=CaptionStyle(font_family=latin_font,font_size=10,margin_bottom=4,outline_width=0)
    actual,graph=compiled(engine,p,high)
    static=deepcopy(p);static.clips[1].motion_keyframes=[]
    static.clips[1].transform=replace(top.transform,x=0,y=0,scale=.6)
    static.clips[1].composite.opacity=.5
    expected,static_graph=compiled(engine,static,high)
    for a,b in zip(actual[5],expected[5]):np.testing.assert_allclose(a,b,atol=2,rtol=0)
    assert graph.index('gblur=')<graph.index('geq=')
    assert graph.index('[over1]')<graph.index('[stickered]')<graph.index('drawtext=')


def test_motion_algorithm_version_invalidates_preview(sources,engine,monkeypatch):
    import lumen.clip_motion_render as motion
    p=project(sources);one=engine.preview(p)
    monkeypatch.setattr(motion,'MOTION_ALGORITHM',motion.MOTION_ALGORITHM+'-test')
    two=engine.preview(p);assert one!=two and two.exists()


@pytest.mark.parametrize('high',[False,True])
def test_dynamic_probe_failure_blocks_publication_even_if_static_passes(sources,engine,tmp_path,monkeypatch,high):
    from lumen.render import RenderError
    import lumen.clip_composite as composite
    def reject(*args, **kwargs):raise RenderError('Dynamic alpha unavailable')
    monkeypatch.setattr(composite,'verify_clip_motion_support',reject)
    p=project(sources);path=tmp_path/('blocked.mov' if high else 'blocked.mp4')
    with pytest.raises(RenderError,match='Dynamic alpha unavailable'):
        engine.render(p,path,export_profile='prores_422_hq_10bit' if high else 'h264_8bit')
    assert not path.exists()

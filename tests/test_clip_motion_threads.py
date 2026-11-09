"""Cross-schedule byte identity, bounded policy, and actual timeline acceptance."""
from copy import deepcopy
from fractions import Fraction
import os
from pathlib import Path
import subprocess
import threading

import pytest
from lumen.clip_motion import ClipMotionKeyframe as K
from lumen.clip_transform import ClipTransform
from lumen.clip_composite import verify_clip_composite_support, verify_clip_motion_support
from lumen.media import ffmpeg_binary
from lumen.model import Clip, Project, ClipEffect, Caption
from lumen.render import _composite_filter_threads, RenderCancelled
from test_clip_motion_cache_exact import CASES, render, candidate
from test_clip_motion_render import sources, engine, project


@pytest.mark.parametrize('case,high',[(n,h) for n,(_,height,_,_) in CASES.items() for h in (False,True) if h or height%2==0])
@pytest.mark.parametrize('transparent',[False,True])
@pytest.mark.parametrize('stage',['rgb16_alpha','composite'])
def test_exact_bytes_across_all_supported_thread_counts(tmp_path,case,high,transparent,stage):
    outputs=[render(candidate,tmp_path,case,high,transparent,n,stage,count=128 if case=='maximum_keys_extreme_frames' else 8) for n in (1,2,4)]
    assert outputs[0]==outputs[1]==outputs[2]


@pytest.mark.parametrize('cpus,expected',[(None,1),(1,1),(2,2),(3,2),(4,4),(8,4),(128,4)])
def test_thread_budget_uses_only_verified_counts(monkeypatch,cpus,expected):
    p=Project(clips=[Clip('c','m',0,0,0,4,motion_keyframes=[K()])])
    monkeypatch.setattr(os,'cpu_count',lambda:cpus)
    assert _composite_filter_threads(p)==expected
    p.clips[0].motion_keyframes=[]
    assert _composite_filter_threads(p)==1


@pytest.mark.parametrize('reason',['effect','caption','overlay','window','audio_only'])
def test_complex_or_nonvideo_paths_stay_single_thread(reason,monkeypatch):
    monkeypatch.setattr(os,'cpu_count',lambda:4)
    from lumen.qualifier import WindowKeyframe
    p=Project(clips=[Clip('c','m',0,0,0,4,motion_keyframes=[K()]),Clip('other','m',1,0,0,4)])
    if reason=='effect':p.clips[1].effects=[ClipEffect('e','vignette',0,enabled=False)]
    if reason=='caption':p.captions=[Caption('c',0,4,'A')]
    if reason=='overlay':p.overlays=[object()]
    if reason=='window':p.clips[1].grade.qualifier.window_keyframes=[WindowKeyframe(0)]
    if reason=='audio_only':p.clips[0].track=2
    assert _composite_filter_threads(p)==1


@pytest.mark.parametrize('count',[True,False,0,3,5,-1,1.0,'4'])
@pytest.mark.parametrize('probe',[verify_clip_composite_support,verify_clip_motion_support])
def test_probe_rejects_unverified_thread_counts(count,probe):
    with pytest.raises(ValueError,match='threads'):
        probe(False,30,filter_complex_threads=count)


@pytest.mark.parametrize('count',[1,2,4])
@pytest.mark.parametrize('high',[False,True])
@pytest.mark.parametrize('probe',[verify_clip_composite_support,verify_clip_motion_support])
def test_probe_uses_selected_count_and_cancellation(monkeypatch,count,high,probe):
    original=subprocess.Popen;seen=[]
    def record(command,*args,**kwargs):
        seen.append(command)
        return original(command,*args,**kwargs)
    monkeypatch.setattr(subprocess,'Popen',record)
    probe(high,30,filter_complex_threads=count)
    assert len(seen)==1
    assert seen[0][seen[0].index('-filter_complex_threads')+1]==str(count)
    event=threading.Event();event.set()
    with pytest.raises(RenderCancelled):probe(high,30,event,cancelled_type=RenderCancelled,filter_complex_threads=count)
    with pytest.raises(RuntimeError,match='timed out'):probe(high,0,filter_complex_threads=count)


def compiled_bytes(engine,p,high,n,tmp_path):
    fmt='yuv422p10le' if high else 'yuv420p'
    inputs,graph,_=engine._compile(p,export_profile='prores_422_hq_10bit' if high else 'h264_8bit',filter_complex_threads=n)
    graphfile=tmp_path/f'graph-{n}.txt';graphfile.write_text(graph)
    audio=tmp_path/f'audio-{n}.raw'
    cmd=[ffmpeg_binary(),'-v','error','-filter_complex_threads',str(n),*inputs,'-/filter_complex',str(graphfile),'-map','[vout]','-frames:v',str(p.duration_frames),'-r',f'{p.fps_num}/{p.fps_den}','-fps_mode','cfr','-c:v','rawvideo','-threads','1','-pix_fmt',fmt,'-f','rawvideo','pipe:1','-map','[aout]','-c:a','pcm_f32le','-f','f32le','-y',str(audio)]
    got=subprocess.run(cmd,capture_output=True,timeout=90)
    assert got.returncode==0,got.stderr.decode()
    samples=Fraction(p.duration_frames*48000*p.fps_den,p.fps_num)
    count=(2*samples.numerator+samples.denominator)//(2*samples.denominator)
    assert len(audio.read_bytes())==count*2*4
    assert len(got.stdout)==p.duration_frames*p.width*p.height*(4 if high else 1.5)
    return got.stdout,audio.read_bytes()


@pytest.mark.parametrize('high',[False,True])
@pytest.mark.parametrize('speed',[Fraction(1,4),Fraction(3,2),Fraction(4)])
@pytest.mark.parametrize('edit',['none','split','trim'])
def test_actual_fractional_timeline_video_pcm_exact_across_threads(sources,engine,tmp_path,high,speed,edit):
    from lumen.model import Editor
    p=project(sources,speed)
    # The nonanimated neighboring clip also runs in the same graph.
    p.clips[0].transform=ClipTransform(scale=.85,rotation=-19,mirror_y=True)
    p.clips[1].motion_keyframes=[K(-3,x=-7,scale=.4,opacity=.2),K(2,x=3,scale=.7,opacity=.8,interpolation='hold'),K(12,x=5,scale=.6,opacity=.5)]
    editor=Editor(p)
    if edit=='split':editor.split_clip('top',5)
    if edit=='trim':editor.trim_edge('top','left',1)
    outputs=[compiled_bytes(engine,editor.project,high,n,tmp_path) for n in (1,2,4)]
    assert outputs[0]==outputs[1]==outputs[2]


@pytest.mark.parametrize('high',[False,True])
def test_export_uses_same_bounded_count_for_probes_and_final(sources,engine,tmp_path,monkeypatch,high):
    monkeypatch.setattr(os,'cpu_count',lambda:4)
    p=project(sources);seen=[];original=subprocess.Popen
    def record(command,*args,**kwargs):
        if '-filter_complex_threads' in command:seen.append(command)
        return original(command,*args,**kwargs)
    monkeypatch.setattr(subprocess,'Popen',record)
    engine.render(p,tmp_path/('thread.mov' if high else 'thread.mp4'),export_profile='prores_422_hq_10bit' if high else 'h264_8bit')
    expected=str(_composite_filter_threads(p))
    assert len(seen)>=3
    assert all(cmd[cmd.index('-filter_complex_threads')+1]==expected for cmd in seen)
    assert sum('-progress' in cmd for cmd in seen)==1


def test_final_cancel_reaps_bounded_compositor(sources,engine,tmp_path,monkeypatch):
    monkeypatch.setattr(os,'cpu_count',lambda:4)
    p=project(sources);event=threading.Event();children=[];original=subprocess.Popen
    def start(command,*args,**kwargs):
        child=original(command,*args,**kwargs)
        if '-progress' in command:children.append(child);event.set()
        return child
    monkeypatch.setattr(subprocess,'Popen',start)
    target=tmp_path/'cancelled.mp4'
    with pytest.raises(RenderCancelled):engine.render(p,target,cancel=event)
    assert len(children)==1 and children[0].poll() is not None
    assert not target.exists() and not list(tmp_path.glob('.lumen-render-*'))


def test_two_simultaneous_exports_keep_each_filter_budget_bounded(sources,tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from lumen.render import RenderEngine
    monkeypatch.setattr(os,'cpu_count',lambda:4)
    p=project(sources);original=subprocess.Popen;counts={0:[],1:[]};lock=threading.Lock();local=threading.local()
    def start(command,*args,**kwargs):
        if '-filter_complex_threads' in command:
            with lock:counts[local.index].append(int(command[command.index('-filter_complex_threads')+1]))
        return original(command,*args,**kwargs)
    monkeypatch.setattr(subprocess,'Popen',start)
    engine=RenderEngine(timeout=90)
    def run(i):
        local.index=i;copy=deepcopy(p)
        if i==1:copy.clips[1].motion_keyframes=[]
        return engine.render(copy,tmp_path/f'parallel-{i}.mp4')
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:outputs=list(pool.map(run,(0,1)))
    finally:engine.close()
    assert all(path.exists() for path in outputs)
    assert len(counts[0])>=3 and set(counts[0])=={4}
    assert len(counts[1])>=2 and set(counts[1])=={1}


@pytest.mark.parametrize('high',[False,True])
@pytest.mark.parametrize('stage',['rgb16_alpha','composite'])
@pytest.mark.parametrize('keys',[[],[K(i,x=(i%7)-3,scale=.3+(i%5)/10,opacity=(i%4)/3,interpolation='hold' if i%2 else 'linear') for i in range(32)]])
def test_static_and_32_keys_across_threads(tmp_path,monkeypatch,high,stage,keys):
    monkeypatch.setitem(CASES,'extra',(64,6,keys,ClipTransform(rotation=19,mirror_x=True)))
    outputs=[render(candidate,tmp_path,'extra',high,True,n,stage,count=34) for n in (1,2,4)]
    assert outputs[0]==outputs[1]==outputs[2]


@pytest.mark.parametrize('high',[False,True])
def test_effect_window_caption_sticker_export_keeps_scalar_protection(sources,engine,tmp_path,monkeypatch,high,latin_font):
    from lumen.qualifier import QualifierSettings,WindowKeyframe
    from lumen.model import CaptionStyle,OverlayKeyframe
    from test_overlay_render import sticker
    p=project(sources);top=p.clips[1]
    top.effects=[ClipEffect('blur','blur',1),ClipEffect('vig','vignette',.2,radius=.8,feather=.9)]
    top.grade.qualifier=QualifierSettings(enabled=True,hue_width=.5,exposure=.2,window_shape='ellipse',window_keyframes=[WindowKeyframe(0,.25,.5,.6,.6),WindowKeyframe(4,.75,.5,.6,.6)])
    overlay,_=sticker(tmp_path,color=(255,0,0,255),start=0,duration=10,keyframes=[OverlayKeyframe(0,x=.1,y=.1,scale=.4)])
    p.overlays=[overlay];p.captions=[Caption('caption',0,10,'A')]
    p.caption_style=CaptionStyle(font_family=latin_font,font_size=10,margin_bottom=4,outline_width=0)
    seen=[];original=subprocess.Popen
    def record(command,*args,**kwargs):
        if '-filter_complex_threads' in command:seen.append(command)
        return original(command,*args,**kwargs)
    monkeypatch.setattr(subprocess,'Popen',record)
    engine.render(p,tmp_path/('effects.mov' if high else 'effects.mp4'),export_profile='prores_422_hq_10bit' if high else 'h264_8bit')
    assert seen and all(cmd[cmd.index('-filter_complex_threads')+1]=='1' for cmd in seen)
    final=next(cmd for cmd in seen if '-progress' in cmd)
    assert final[final.index('-cpuflags')+1]=='0'


from test_highbit_caption_reference import latin_font


@pytest.mark.parametrize('count',[True,False,0,3,5,-1,1.0,'4'])
def test_compile_rejects_unverified_thread_counts(engine,count):
    with pytest.raises(ValueError,match='threads'):
        engine._compile(Project(),filter_complex_threads=count)


@pytest.mark.parametrize('count',[1,2,4])
def test_inflight_probe_cancel_reaps_process(monkeypatch,count):
    event=threading.Event();children=[];original=subprocess.Popen
    def start(command,*args,**kwargs):
        child=original(command,*args,**kwargs);children.append(child);event.set();return child
    monkeypatch.setattr(subprocess,'Popen',start)
    with pytest.raises(RenderCancelled):
        verify_clip_motion_support(False,30,event,cancelled_type=RenderCancelled,filter_complex_threads=count)
    assert len(children)==1 and children[0].poll() is not None


@pytest.mark.parametrize('windows_file_lock', [False, True])
def test_final_timeout_reaps_bounded_compositor(sources,engine,tmp_path,monkeypatch,windows_file_lock):
    from lumen.render import RenderError
    monkeypatch.setattr(os,'cpu_count',lambda:4)
    children=[];original=subprocess.Popen
    def start(command,*args,**kwargs):
        child=original(command,*args,**kwargs)
        if '-progress' in command:children.append(child);engine.timeout=0
        return child
    monkeypatch.setattr(subprocess,'Popen',start)
    if windows_file_lock:
        unlink = Path.unlink
        def locked_unlink(path, *args, **kwargs):
            for child in children:
                command = child.args
                progress_path = Path(command[command.index('-progress') + 1])
                if path == progress_path and child.poll() is None:
                    error = PermissionError('Progress file is held by the running encoder')
                    error.winerror = 32
                    raise error
            return unlink(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'unlink', locked_unlink)
    target=tmp_path/'timeout.mp4'
    with pytest.raises(RenderError,match='timed out'):engine.render(project(sources),target)
    assert len(children)==1 and children[0].poll() is not None
    assert not target.exists() and not list(tmp_path.glob('.lumen-render-*'))


@pytest.mark.parametrize('origin',['compile','static_probe','motion_probe'])
@pytest.mark.parametrize('locale',['en','zh-CN'])
def test_thread_budget_diagnostic_has_explicit_localization(engine,origin,locale):
    from lumen.i18n import get_locale,set_locale,tr_error
    english='Composite filter threads must be 1, 2, or 4'
    expected=english if locale=='en' else '画面合成滤镜线程数必须为 1、2 或 4'
    previous=get_locale()
    try:
        set_locale(locale)
        with pytest.raises(ValueError) as caught:
            if origin=='compile':engine._compile(Project(),filter_complex_threads=3)
            else:(verify_clip_composite_support if origin=='static_probe' else verify_clip_motion_support)(False,30,filter_complex_threads=3)
        assert str(caught.value)==english
        assert str(tr_error(caught.value))==expected
    finally:set_locale(previous)

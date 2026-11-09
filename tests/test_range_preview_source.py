"""Admission, ownership, freshness and resource gates for session range playback."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
import threading

import pytest

from lumen.model import Clip, Media, Project
from lumen.range_preview import (MAX_CACHE_BYTES, RangePreviewError, RangePreviewCancelled,
    capture_range_preview, verify_range_preview, validate_range, project_signature)
from scripts.benchmark_final_frame_preview import make_scene, make_source


def project():
    return Project(width=64, height=48, fps_num=24, media=[
        Media('m', '/missing-but-not-inspected', 'test', 1000, 64, 48, True, False)],
        clips=[Clip('c', 'm', 0, 0, 0, 240)])


@pytest.mark.parametrize('lo,hi', [(True, 4), (0, False), (-1, 5), (0, 0), (6, 5), (0, 241), (1.0, 5)])
def test_invalid_ranges_reject_without_media_io(lo, hi):
    with pytest.raises(RangePreviewError):
        validate_range(project(), lo, hi)


@pytest.mark.parametrize('size', [(1921, 1080), (1920,1081), (63,48), (64,47)])
def test_unsupported_canvas_has_explicit_full_fallback(size):
    p=project();p.width,p.height=size
    with pytest.raises(RangePreviewError,match='Build full timeline preview'):
        validate_range(p,0,24)


def test_global_clock_bounds_not_project_duration():
    p=project();p.clips[0].start=24*3600
    assert validate_range(p,24*3600,24*3600+24)==tuple(p.clips)
    assert p.clips[0].start==24*3600
    p.clips[0].source_in=24*301
    with pytest.raises(RangePreviewError, match='300 seconds'):
        validate_range(p,24*3600,24*3600+24)


@pytest.mark.parametrize('seconds', [0, -1, True, float('nan'), float('inf')])
def test_bad_deadlines_reject(seconds):
    with pytest.raises(RangePreviewError,match='timeout'):
        capture_range_preview(project(),0,24,timeout=seconds)


def test_pre_cancel_does_no_media_work(monkeypatch):
    from lumen import range_preview as backend
    cancel=threading.Event();cancel.set()
    monkeypatch.setattr(backend,'_dependencies',lambda *a: pytest.fail('I/O after cancellation'))
    with pytest.raises(RangePreviewCancelled):
        capture_range_preview(project(),0,24,cancel=cancel)


def test_actual_capture_owns_output_and_stales_on_source_change(tmp_path):
    source=make_source(tmp_path/'source',Fraction(24))
    p=Project(width=64,height=48,fps_num=24,media=[source],clips=[Clip('v',source.id,0,0,0,16)])
    before=project_signature(p)
    result=capture_range_preview(p,3,7)
    assert result.path.is_file()
    assert result.identity.start_frame==3 and result.identity.end_frame==7
    assert result.work.graded_frames==result.work.composite_frames==4
    assert result.work.mixed_samples==8000 and result.work.sticker_frames==0
    assert project_signature(p)==before and verify_range_preview(result,p)
    changed=deepcopy(p);changed.clips[0].gain_db=-6
    assert not verify_range_preview(result,changed)
    path=Path(source.path);old=path.stat();path.touch()
    assert not verify_range_preview(result,p)
    result.close();result.close()
    assert not result.path.exists()
    assert not verify_range_preview(result,p)


def test_capture_does_not_open_unrelated_tail_or_call_single_still(tmp_path,monkeypatch):
    from lumen import final_frame_preview
    monkeypatch.setattr(final_frame_preview,'capture_final_frame_preview',lambda *a,**k:pytest.fail('single-frame loop'))
    source=make_source(tmp_path/'source',Fraction(24))
    p=Project(width=64,height=48,fps_num=24,media=[source,
        Media('tail','/does-not-exist-unused-tail','tail',100,64,48,True,False)],
        clips=[Clip('v',source.id,0,0,0,16),Clip('far','tail',0,24*3600,0,24)])
    result=capture_range_preview(p,3,7)
    try:
        assert result.work.active_clip_ids==('v',)
        assert result.work.graded_frames==4
        assert verify_range_preview(result,p)
    finally:result.close()


def test_capture_rejects_changed_dependency_before_publication(tmp_path,monkeypatch):
    from lumen import range_preview as backend
    source=make_source(tmp_path/'source',Fraction(24))
    p=Project(width=64,height=48,fps_num=24,media=[source],clips=[Clip('v',source.id,0,0,0,16)])
    original=backend._encode
    output_paths=[]
    def mutate(*args):
        original(*args);output_paths.append(args[-2]);Path(source.path).touch()
    monkeypatch.setattr(backend,'_encode',mutate)
    with pytest.raises(RangePreviewError,match='dependencies changed'):
        capture_range_preview(p,3,7)
    assert output_paths and all(not p.exists() for p in output_paths)


def test_cache_budget_failure_reaps_owned_directory(tmp_path,monkeypatch):
    from lumen import range_preview as backend
    source=make_source(tmp_path/'source',Fraction(24))
    p=Project(width=64,height=48,fps_num=24,media=[source],clips=[Clip('v',source.id,0,0,0,16)])
    monkeypatch.setattr(backend,'MAX_CACHE_BYTES',1)
    with pytest.raises(RangePreviewError,match='cache budget'):
        capture_range_preview(p,3,7)


def test_manifest_source_is_rejected_before_external_children(tmp_path):
    path=tmp_path/'source.m3u8';path.write_text('#EXTM3U\nhttp://example.invalid/video.ts\n')
    p=project();p.media[0].path=str(path)
    with pytest.raises(RangePreviewError,match='self-contained'):
        capture_range_preview(p,0,24)


def test_probe_adapter_cancels_and_reaps_process(monkeypatch):
    import subprocess, sys, time
    from lumen import range_preview as backend
    cancel=threading.Event();guard=backend._guard(cancel,30)
    children=[];original=subprocess.Popen
    def spawn(*args,**kwargs):
        child=original(*args,**kwargs);children.append(child);return child
    monkeypatch.setattr(subprocess,'Popen',spawn)
    timer=threading.Timer(.1,cancel.set);timer.start();began=time.monotonic()
    try:
        with pytest.raises(RangePreviewCancelled):
            backend._checked_run(guard,[sys.executable,'-c','import time;time.sleep(20)'],capture_output=True,timeout=30)
    finally:timer.cancel()
    assert time.monotonic()-began<2
    assert len(children)==1 and children[0].poll() is not None


def test_probe_adapter_uses_total_deadline_and_output_budget():
    import sys,time
    from lumen import range_preview as backend
    began=time.monotonic()
    with pytest.raises(RangePreviewError,match='timed out'):
        backend._checked_run(backend._guard(None,.08),[sys.executable,'-c','import time;time.sleep(20)'],capture_output=True,timeout=30)
    assert time.monotonic()-began<2
    with pytest.raises(RangePreviewError,match='output budget'):
        backend._checked_run(backend._guard(None,10),[sys.executable,'-c','import sys;sys.stderr.write("x"*70000)'],capture_output=True)


def test_probe_adapter_preserves_text_and_redirected_output(tmp_path):
    import subprocess,sys
    from lumen import range_preview as backend
    guard=backend._guard(None,10)
    result=backend._checked_run(guard,[sys.executable,'-c','print("metadata")'],capture_output=True,text=True,check=True)
    assert result.stdout=='metadata\n' and result.stderr==''
    with (tmp_path/'probe').open('w+b') as output:
        result=backend._checked_run(guard,[sys.executable,'-c','print("tail")'],stdout=output,stderr=subprocess.PIPE)
        assert result.stdout is None and result.stderr==b''
        output.seek(0);assert output.read()==b'tail\n'


def test_unsupported_audio_preflights_before_any_picture_grading(tmp_path,monkeypatch):
    from scripts.benchmark_range_preview import make_av_source
    from lumen.render import RenderEngine
    source=make_av_source(tmp_path/'source',count=800)
    p=Project(width=64,height=48,fps_num=24,media=[source],clips=[Clip('v',source.id,0,0,0,790)])
    monkeypatch.setattr(RenderEngine,'_graded_segment',lambda *a,**k:pytest.fail('unsupported audio must reject before grading'))
    with pytest.raises(RangePreviewError,match='30 seconds'):
        capture_range_preview(p,750,755)


def test_muxed_flash_and_audio_burst_remain_synchronized(tmp_path):
    """Actual H.264/AAC playback ingress, not only the pre-encoder graphs."""
    import numpy as np
    import subprocess
    from lumen.media import ffmpeg_binary,probe
    rate=24;sr=48000;count=96
    source=tmp_path/'flash-burst.mkv';audio=tmp_path/'burst.f32'
    samples=np.zeros((count*sr//rate,2),dtype='<f4')
    lo,hi=20*sr//rate,21*sr//rate
    tone=.4*np.sin(2*np.pi*1000*np.arange(hi-lo)/sr)
    samples[lo:hi,0]=tone;samples[lo:hi,1]=tone
    audio.write_bytes(samples.tobytes())
    subprocess.run([ffmpeg_binary(),'-v','error','-nostdin','-y','-f','lavfi','-i',
        "color=black:s=64x48:r=24:d=4,drawbox=color=white:t=fill:enable='eq(n,20)'",
        '-f','f32le','-ar',str(sr),'-ac','2','-i',str(audio),'-c:v','ffv1','-threads','1',
        '-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709',
        '-c:a','pcm_f32le',str(source)],capture_output=True,check=True,timeout=30)
    media=probe(source)
    p=Project(width=64,height=48,fps_num=rate,media=[media],clips=[Clip('av',media.id,0,0,0,count)])
    snapshot=capture_range_preview(p,7,48)
    try:
        video=subprocess.run([ffmpeg_binary(),'-v','error','-nostdin','-i',str(snapshot.path),
            '-an','-pix_fmt','yuv420p','-threads','1','-f','rawvideo','pipe:1'],
            capture_output=True,check=True,timeout=30).stdout
        pcm=subprocess.run([ffmpeg_binary(),'-v','error','-nostdin','-i',str(snapshot.path),
            '-vn','-ar',str(sr),'-ac','2','-c:a','pcm_f32le','-f','f32le','pipe:1'],
            capture_output=True,check=True,timeout=30).stdout
        frames=np.frombuffer(video,np.uint8).reshape(41,64*48*3//2)
        means=frames[:,:64*48].mean(axis=1)
        assert int(np.argmax(means))==13 and means[13]>200
        decoded=np.frombuffer(pcm,'<f4').reshape(-1,2)[:41*sr//rate]
        energy=np.mean(decoded.astype(np.float64)**2,axis=1)
        assert energy.sum()>10
        sound_center=float((np.arange(len(energy))+.5)@energy/energy.sum()/sr)
        flash_center=(13+.5)/rate
        # AAC is lossy with priming/padding; decoded event alignment, not
        # sample equality, is the post-encoding contract under test.
        assert abs(sound_center-flash_center)<.002
        assert np.max(np.abs(decoded[:12*sr//rate]))<.01
    finally:snapshot.close()


def test_fontconfig_discovery_uses_cancellable_adapter(monkeypatch):
    from lumen import caption_render
    from lumen.model import CaptionStyle
    seen=[]
    monkeypatch.setattr(caption_render,'_roots',lambda _:[])
    monkeypatch.setattr(caption_render.shutil,'which',lambda _: '/local/fc-match')
    def runner(args,**kwargs):
        seen.append((args,kwargs));raise RangePreviewCancelled('Range preview canceled.')
    with pytest.raises(RangePreviewCancelled):
        caption_render.font_identity(CaptionStyle(),check=lambda:None,run_process=runner)
    assert len(seen)==1 and seen[0][0][0]=='fc-match' and seen[0][1]['timeout']==5


def test_real_retimed_capture_uses_guarded_capability_probe(tmp_path,monkeypatch):
    from lumen import audio_timing
    from lumen.timing import ClipTimeMap
    from scripts.benchmark_range_preview import make_av_source
    from lumen import range_preview as backend
    source=make_av_source(tmp_path/'source')
    p=Project(width=64,height=48,fps_num=24,media=[source],clips=[
        Clip('retimed-av',source.id,0,0,0,16,time_map=ClipTimeMap(Fraction(0),Fraction(24),Fraction(3,2)))])
    original=audio_timing._probe_audio_compensation;seen=[]
    def inspect(*args,**kwargs):
        assert callable(kwargs.get('run_process'));seen.append(True)
        return original(*args,**kwargs)
    monkeypatch.setattr(audio_timing,'_probe_audio_compensation',inspect)
    with backend._AUDIO_CAPABILITY_LOCK:backend._AUDIO_CAPABILITIES.clear()
    snapshot=capture_range_preview(p,2,8)
    try:
        assert seen==[True]
        assert snapshot.work.composite_frames==6 and snapshot.work.mixed_samples==12000
        assert verify_range_preview(snapshot,p)
    finally:snapshot.close()

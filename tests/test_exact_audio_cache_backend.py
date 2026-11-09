"""Independent f32, resource/lifecycle and retained-renderer PCM acceptance.

No waveform proxy or production timing helper builds the expected samples.
Fixtures are synthetic. Full source PCM is decoded only in this small oracle;
production must stream/drop prefixes and retain just bounded requested spans.
"""
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import wave

import numpy as np
import pytest

from lumen.media import ffmpeg_available, ffmpeg_binary, probe
from lumen.model import Clip, Editor, Project
from lumen.render import RenderEngine
from synthetic_aac_tail import understate_audio_duration
from lumen.source_viewer import (SourcePreviewBackend, SourceViewerSession,
    SourceViewerError, SourceViewerCancelled, _identity, _probe)

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg required')


@pytest.fixture(scope='module')
def cache_media():
    with tempfile.TemporaryDirectory(prefix='cutcat-exact-cache-tests-') as directory:
        yield Path(directory)


def ff(*args, data=None):
    return subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-threads', '1',
                           *map(str, args)], input=data, capture_output=True,
                          check=True, timeout=40).stdout


def source(root, rate=44100, codec='aac', seconds=44):
    extension = {'aac': 'm4a', 'libmp3lame': 'mp3', 'flac': 'flac'}[codec]
    path = root/f'exact-{rate}-{codec}-{seconds}.{extension}'
    if not path.exists():
        # Different channels, transient markers and broadband signal expose
        # phase/priming errors that a sinusoid or int16-only oracle can conceal.
        rng = np.random.default_rng(901371)
        raw = rng.integers(-9000, 9001, (rate*seconds, 2), dtype=np.int16)
        raw[::rate//7, 0] = 20000
        raw[rate//11::rate//5, 1] = -20000
        ff('-f', 's16le', '-ar', rate, '-ac', 2, '-i', 'pipe:0', '-c:a', codec,
           '-threads', 1, path, data=raw.astype('<i2').tobytes())
    return path


def origin_oracle(path):
    return ff('-i', path, '-map', '0:a:0', '-af',
              'asetpts=PTS-STARTPTS,aresample=48000:async=1:first_pts=0,'
              'aformat=sample_fmts=fltp:channel_layouts=stereo',
              '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1')


def setup(path, fps=Fraction(25)):
    media = probe(path)
    project = Project(width=16, height=16, fps_num=fps.numerator,
                      fps_den=fps.denominator, media=[media])
    return project, SourceViewerSession(project, media.id)


def exact_round(value):
    return (2*value.numerator+value.denominator)//(2*value.denominator)


def boundary(frame, fps):
    return exact_round(Fraction(frame*48000)/fps)


def selected_oracle(full, session, start, end, *, known_audio_end=None):
    offset = Fraction(str(session.media.audio_offset))
    lo = exact_round(max(Fraction(0), Fraction(start)/session.fps-offset)*48000)
    hi = exact_round(max(Fraction(0), Fraction(end)/session.fps-offset)*48000)
    leading = max(0, exact_round((offset-Fraction(start)/session.fps)*48000))
    count = boundary(end,session.fps)-boundary(start,session.fps)
    if known_audio_end is not None:
        hi = min(hi, known_audio_end)
    return (bytes(leading*8)+full[lo*8:hi*8]+bytes(count*8))[:count*8]


@pytest.mark.parametrize('codec', ['aac', 'libmp3lame', 'flac'])
@pytest.mark.parametrize('rate', [22050, 44100, 96000])
def test_late_sparse_f32_equals_independent_origin_without_seek(cache_media, codec, rate, monkeypatch):
    path = source(cache_media, rate, codec)
    project, session = setup(path, Fraction(30000,1001))
    full = origin_oracle(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    backend = SourcePreviewBackend()
    commands = []
    real = subprocess.Popen
    def captured(command, *args, **kwargs):
        commands.append(command)
        return real(command, *args, **kwargs)
    try:
        deadline = time.monotonic()+60
        identity = _identity(session,None,deadline); info = _probe(session,identity,None,deadline)
        monkeypatch.setattr(subprocess,'Popen',captured)
        for start,end in [(3,17),(967,986),(1203,1227)]:
            got = backend._audio_f32(identity,info,session,start,end,None,deadline,pad=False)
            assert got == selected_oracle(full,session,start,end)
        decoders = [c for c in commands if 'pcm_f32le' in c]
        assert len(decoders)==3
        assert all('-ss' not in c for c in decoders)
        expected = sum((boundary(b,session.fps)-boundary(a,session.fps))*8
                       for a,b in [(3,17),(967,986),(1203,1227)])
        assert sum(v[1] for v in backend._audio_cache.values())==expected
        # Prefixes (over forty seconds) never exist as retained files.
        assert sum(p.stat().st_size for p in Path(backend._audio_temporary.name).iterdir())==expected
        commands.clear()
        assert backend._audio_f32(identity,info,session,970,980,None,deadline,pad=False)==selected_oracle(full,session,970,980)
        assert not commands
        assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    finally:
        backend.close()


def av_source(root, *, offset=0.137, origin=5, audio_seconds=44):
    path = root/f'av-origin-{offset}-{origin}-{audio_seconds}.mp4'
    if not path.exists():
        audio = source(root,44100,'aac',audio_seconds)
        ff('-f','lavfi','-i','testsrc2=size=16x16:rate=25:duration=44',
           '-itsoffset',offset,'-i',audio,'-map','0:v','-map','1:a',
           '-vf',f'setpts=PTS+{origin}/TB','-af',f'asetpts=PTS+{origin}/TB',
           '-c:v','libx264','-preset','ultrafast','-g',25,'-threads',1,
           '-c:a','aac','-video_track_timescale',90000,'-r',25,path)
    return path


@pytest.mark.parametrize('offset,origin', [(0.137,5),(-0.113,5),(0,7)])
def test_embedded_aac_nonzero_starts_offsets_late_windows(cache_media,offset,origin):
    path=av_source(cache_media,offset=offset,origin=origin)
    project,session=setup(path)
    full=origin_oracle(path)
    backend=SourcePreviewBackend()
    try:
        deadline=time.monotonic()+60
        identity=_identity(session,None,deadline); info=_probe(session,identity,None,deadline)
        assert info['origin']!=0
        for start,end in [(0,10),(7,30),(801,827),(1040,1060)]:
            assert backend._audio_f32(identity,info,session,start,end,None,deadline)==selected_oracle(full,session,start,end)
    finally:
        backend.close()


def production_pcm(engine,project,root,name):
    # Compile the unchanged actual full renderer. Consume both outputs, so its
    # duration/scheduling remain the same as real export; no cache is an input.
    inputs,graph,_=engine._compile(project)
    path=root/(name+'.mkv')
    ff(*inputs,'-filter_complex_threads',1,'-filter_complex',graph,
       '-map','[vout]','-map','[aout]','-c:v','ffv1','-threads',1,'-c:a','pcm_f32le',path)
    data=ff('-i',path,'-map','0:a:0','-c:a','pcm_f32le','-f','f32le','pipe:1')
    path.unlink()
    return data


@pytest.mark.parametrize('codec,track', [('aac',2),('libmp3lame',2),('flac',2),('embedded',0)])
def test_actual_old_new_renderer_pcm_retains_outside_and_inserts_exact_late_samples(cache_media,codec,track):
    path=av_source(cache_media) if codec=='embedded' else source(cache_media,44100,codec)
    project,session=setup(path)
    media=project.media[0]
    project.clips=[Clip('retained',media.id,track,10,800,70),Clip('downstream',media.id,track,90,1000,10)]
    editor=Editor(project)
    snapshot=deepcopy(project)
    plan=editor.plan_source_overwrite(media.id,track,25,850,875)
    engine=RenderEngine(timeout=60)
    backend=SourcePreviewBackend()
    try:
        old=production_pcm(engine,project,cache_media,f'old-{codec}')
        frame=backend.inspect_frame(session)
        assert backend.verify_overwrite_sources(session,frame.identity,project,plan)
        assert project==snapshot
        editor.apply_source_overwrite(plan)
        new=production_pcm(engine,project,cache_media,f'new-{codec}')
        lo,hi=25*1920*8,50*1920*8
        assert new[:lo]==old[:lo]
        assert new[hi:]==old[hi:]
        assert new[lo:hi]==selected_oracle(origin_oracle(path),session,850,875)
        assert editor.undo() and editor.project==snapshot
        assert editor.redo()
    finally:
        backend.close()


def verified_inputs(backend,path):
    project,session=setup(path)
    deadline=time.monotonic()+60
    identity=_identity(session,None,deadline); info=_probe(session,identity,None,deadline)
    return project,session,identity,info,deadline


def test_sparse_multiple_windows_single_scan_cache_tamper_and_lru(cache_media,monkeypatch):
    backend=SourcePreviewBackend(max_storage_bytes=48000*8*2)
    try:
        _,session,identity,info,deadline=verified_inputs(backend,source(cache_media))
        windows=[(48000*32,48000*32+12000),(48000*40,48000*40+24000)]
        backend._prepare_pcm(identity,info,session,windows,None,deadline)
        assert backend._scan_samples==windows[-1][1]
        assert backend._storage_bytes()==36000*8
        entries=list(backend._audio_cache.values())
        entries[0][0].write_bytes(bytes(entries[0][1]))
        # Hash mismatch is never reused. Exactly the corrupt requested span is
        # replaced, not the intact other span or the discarded forty-s prefix.
        backend._prepare_pcm(identity,info,session,windows,None,deadline)
        assert backend._scan_samples==windows[-1][1]+windows[0][1]
        assert not entries[0][0].exists() and entries[1][0].exists()
        for sec in (36,37,38):
            backend._prepare_pcm(identity,info,session,[(sec*48000,(sec+1)*48000)],None,deadline)
            assert backend._storage_bytes()<=backend.max_storage_bytes
        assert not entries[1][0].exists()
        directory=Path(backend._audio_temporary.name)
    finally:
        backend.close()
    assert not directory.exists()


def test_cancel_mid_origin_scan_and_timeout_cleanup_reap_processes(cache_media,monkeypatch):
    import lumen.source_viewer as module
    backend=SourcePreviewBackend(); cancel=threading.Event(); children=[]
    real=module._Pipeline.spawn
    def spawn(pipe,*args,**kwargs):
        value=real(pipe,*args,**kwargs); children.append(value[0]); return value
    monkeypatch.setattr(module._Pipeline,'spawn',spawn)
    try:
        _,session,identity,info,deadline=verified_inputs(backend,source(cache_media))
        backend.progress_callback=lambda message,**values: cancel.set() if float(values.get('scanned',0))>0 else None
        with pytest.raises(SourceViewerCancelled):
            backend._prepare_pcm(identity,info,session,[(40*48000,41*48000)],cancel,deadline)
        assert not backend._audio_cache and backend._pending_bytes==0
        assert not list(Path(backend._audio_temporary.name).iterdir())
        assert children and all(p.poll() is not None for p in children)
        cancel.clear(); backend.progress_callback=None
        with pytest.raises(SourceViewerError,match='runtime budget'):
            backend._prepare_pcm(identity,info,session,[(40*48000,41*48000)],None,time.monotonic()-1)
        assert not backend._audio_cache and backend._pending_bytes==0
    finally:
        backend.close()


def test_scan_and_shared_pending_storage_caps_fail_before_decoder(cache_media,monkeypatch):
    import lumen.source_viewer as module
    backend=SourcePreviewBackend(max_scan_samples=32*48000,max_storage_bytes=100)
    try:
        _,session,identity,info,deadline=verified_inputs(backend,source(cache_media))
        monkeypatch.setattr(module._Pipeline,'spawn',lambda *a,**kw: pytest.fail('resource preflight must precede decoder'))
        with pytest.raises(SourceViewerError,match='scan sample budget'):
            backend._prepare_pcm(identity,info,session,[(32*48000,33*48000)],None,deadline)
        with pytest.raises(SourceViewerError,match='storage byte budget'):
            backend._prepare_pcm(identity,info,session,[(100,200)],None,deadline)
        assert backend._storage_bytes()==0 and backend._audio_temporary is None
    finally:
        backend.close()


def test_apply_cumulative_scan_cap_all_sources_before_picture_or_audio_decode(cache_media,monkeypatch):
    early=probe(av_source(cache_media,offset=0,origin=5))
    late=probe(av_source(cache_media,offset=0,origin=7))
    project=Project(width=16,height=16,fps_num=25,media=[early,late],
                    clips=[Clip('old',late.id,0,0,800,40)])
    session=SourceViewerSession(project,early.id); editor=Editor(project)
    plan=editor.plan_source_overwrite(early.id,0,10,800,810)
    backend=SourcePreviewBackend(max_scan_samples=50*48000)
    try:
        identity=backend.inspect_frame(session).identity
        monkeypatch.setattr(backend,'_prove_cfr',lambda *a,**kw: pytest.fail('all-source scan preflight first'))
        monkeypatch.setattr(backend,'_prepare_pcm',lambda *a,**kw: pytest.fail('all-source scan preflight first'))
        with pytest.raises(SourceViewerError,match='scan sample budget'):
            backend.verify_overwrite_sources(session,identity,project,plan)
        assert editor.revision==0
    finally:
        backend.close()


def test_picture_apply_missing_claimed_embedded_samples_rejects_and_known_absent_tail_passes(cache_media,monkeypatch):
    import lumen.source_viewer as module
    path=av_source(cache_media,offset=.137,origin=5,audio_seconds=1)
    project,session=setup(path); editor=Editor(project)
    backend=SourcePreviewBackend()
    try:
        identity=backend.inspect_frame(session).identity
        # Audio is genuinely over long before this selected picture span.
        late=editor.plan_source_overwrite(session.media_id,0,0,800,825)
        assert backend.verify_overwrite_sources(session,identity,project,late)
        assert not backend._audio_cache
        early=editor.plan_source_overwrite(session.media_id,0,0,0,50)
        real=module._probe
        def dishonest(*args,**kwargs):
            info=real(*args,**kwargs); info['audio_duration']=Fraction(2); return info
        monkeypatch.setattr(module,'_probe',dishonest)
        with pytest.raises(SourceViewerError,match='missing or incomplete'):
            backend.verify_overwrite_sources(session,identity,project,early)
        assert editor.revision==0 and not project.clips
    finally:
        backend.close()


def test_warm_apply_revalidates_source_identity_and_refuses_stale_bytes(cache_media):
    with tempfile.TemporaryDirectory(prefix='cutcat-cache-replaced-') as directory:
        path=Path(directory)/'replace.m4a'; path.write_bytes(source(cache_media).read_bytes())
        project,session=setup(path); editor=Editor(project)
        plan=editor.plan_source_overwrite(session.media_id,2,0,800,825)
        backend=SourcePreviewBackend()
        try:
            identity=backend.inspect_frame(session).identity
            assert backend.verify_overwrite_sources(session,identity,project,plan)
            with path.open('r+b') as stream:
                stream.seek(128); value=stream.read(1); stream.seek(128); stream.write(bytes([value[0]^1]))
            with pytest.raises(SourceViewerError,match='Source changed'):
                backend.verify_overwrite_sources(session,identity,project,plan)
            assert editor.revision==0 and not project.clips
        finally:
            backend.close()


def test_shared_pending_audition_pcm_accounting_and_failed_reservation(cache_media):
    project,session=setup(source(cache_media))
    backend=SourcePreviewBackend()
    observed=[]
    backend.progress_callback=lambda message,**values: observed.append(backend._storage_bytes())
    try:
        result=backend.audition(session,800,825)
        assert result.path.exists() and backend._audio_cache
        assert observed and max(observed)<=48*1024*1024
        assert max(observed)>=24*1024*1024+48000*12  # pending mp4 + int16 + f32
        assert backend._pending_bytes==0
        assert backend._storage_bytes()==sum(v[1] for c in (backend._cache,backend._audio_cache) for v in c.values())
    finally:
        backend.close()
    tiny=SourcePreviewBackend(max_storage_bytes=100)
    try:
        with pytest.raises(SourceViewerError,match='storage byte budget'):
            tiny.audition(session,800,825)
        assert tiny._pending_bytes==0 and tiny._storage_bytes()==0
    finally:
        tiny.close()


def test_sparse_count_cap_and_serial_lock_wait_are_cancelable(cache_media,monkeypatch):
    import lumen.source_viewer as module
    backend=SourcePreviewBackend()
    try:
        with pytest.raises(SourceViewerError,match='32 sparse'):
            backend._merge_windows([(n*4,n*4+1) for n in range(33)])
        project,session=setup(source(cache_media)); cancel=threading.Event(); errors=[]
        monkeypatch.setattr(module,'_identity',lambda *a,**kw: pytest.fail('second worker cannot enter source IO'))
        backend._lock.acquire()
        def wait():
            try: backend.inspect_frame(session,cancel)
            except BaseException as exc: errors.append(exc)
        worker=threading.Thread(target=wait); worker.start()
        time.sleep(.04); cancel.set(); worker.join(1)
        backend._lock.release()
        assert not worker.is_alive() and len(errors)==1 and isinstance(errors[0],SourceViewerCancelled)
    finally:
        backend.close()


@pytest.mark.parametrize('extra', [-8,8])
def test_decoder_incomplete_or_excess_output_cannot_publish(cache_media,monkeypatch,extra):
    import lumen.source_viewer as module
    import sys
    backend=SourcePreviewBackend()
    try:
        _,session,identity,info,deadline=verified_inputs(backend,source(cache_media))
        real=module._Pipeline.spawn
        def fake(pipe,args,*a,**kw):
            return real(pipe,[sys.executable,'-c',f'import sys; sys.stdout.buffer.write(bytes({200*8+extra}))'],*a,**kw)
        monkeypatch.setattr(module._Pipeline,'spawn',fake)
        with pytest.raises(SourceViewerError,match='missing or incomplete|normalized sample budget'):
            backend._prepare_pcm(identity,info,session,[(100,200)],None,deadline)
        assert backend._pending_bytes==0 and not backend._audio_cache
        assert not list(Path(backend._audio_temporary.name).iterdir())
    finally:
        backend.close()


def test_decoder_replacement_during_scan_cannot_publish(cache_media,monkeypatch):
    backend=SourcePreviewBackend()
    try:
        _,session,identity,info,deadline=verified_inputs(backend,source(cache_media))
        original=backend._decoder_identity()
        def replace(message,**values):
            if float(values.get('scanned',0))>0:
                monkeypatch.setattr(backend,'_decoder_identity',lambda: (original[0],('changed',)))
        backend.progress_callback=replace
        with pytest.raises(SourceViewerError,match='decoder changed'):
            backend._prepare_pcm(identity,info,session,[(32*48000,33*48000)],None,deadline)
        assert not backend._audio_cache and backend._pending_bytes==0
    finally:
        backend.close()


def copied_short_aac(root,rate=44100,offset=0,origin=0):
    """Copied AAC with an explicitly underestimated duration, not implicit padding."""
    path=root/f'copied-short-{rate}-{offset}-{origin}.mp4'
    if not path.exists():
        audio=source(root,rate,'aac',1)
        ff('-f','lavfi','-i','testsrc2=size=16x16:rate=25:duration=3',
           '-itsoffset',offset+origin,'-i',audio,'-map','0:v','-map','1:a',
           '-vf',f'setpts=PTS+{origin}/TB','-c:v','libx264','-preset','ultrafast',
           '-g',25,'-threads',1,'-c:a','copy','-video_track_timescale',90000,'-r',25,path)
        understate_audio_duration(path)
    return path


@pytest.mark.parametrize('rate,offset,origin',[(22050,0,0),(44100,0,0),(96000,0,0),(44100,.137,5),(44100,-.113,5)])
def test_copied_aac_real_tail_after_duration_is_proven_auditioned_and_rendered(cache_media,rate,offset,origin):
    path=copied_short_aac(cache_media,rate,offset,origin)
    project,session=setup(path);editor=Editor(project);backend=SourcePreviewBackend()
    try:
        identity=backend.inspect_frame(session).identity
        deadline=time.monotonic()+60;info=_probe(session,identity,None,deadline)
        full=origin_oracle(path); declared=math.ceil(info['audio_duration']*48000)
        assert len(full)//8>declared  # actual AAC tail must exist in this fixture
        assert np.max(np.abs(np.frombuffer(full[declared*8:],dtype='<f4')),initial=0)>.001
        start=math.floor((info['audio_duration']+Fraction(str(session.media.audio_offset)))*25)
        end=start+3
        plan=editor.plan_source_overwrite(session.media_id,0,0,start,end)
        assert backend.verify_overwrite_sources(session,identity,project,plan)
        assert backend._audio_cache and backend._audio_eof
        assert next(iter(backend._audio_eof.values()))==len(full)//8
        expected=selected_oracle(full,session,start,end)
        assert backend._audio_f32(identity,info,session,start,end,None,deadline)==expected
        editor.apply_source_overwrite(plan)
        assert production_pcm(RenderEngine(),project,cache_media,f'copied-tail-{rate}-{offset}-{origin}')==expected
    finally:
        backend.close()


def test_affected_copied_aac_tail_preserved_and_checked_with_silent_selected_source(cache_media):
    old=probe(copied_short_aac(cache_media));silent=cache_media/'silent-three.mp4'
    if not silent.exists():
        ff('-f','lavfi','-i','color=size=16x16:rate=25:duration=3','-c:v','libx264',
           '-preset','ultrafast','-g',25,'-threads',1,'-video_track_timescale',90000,'-r',25,silent)
    new=probe(silent)
    project=Project(width=16,height=16,fps_num=25,media=[old,new],
                    clips=[Clip('old-tail',old.id,0,0,24,6)])
    editor=Editor(project);session=SourceViewerSession(project,new.id)
    plan=editor.plan_source_overwrite(new.id,0,3,0,1)
    backend=SourcePreviewBackend()
    try:
        before=production_pcm(RenderEngine(),project,cache_media,'tail-before')
        assert np.max(np.abs(np.frombuffer(before[1920*8:2*1920*8],dtype='<f4')),initial=0)>.001
        identity=backend.inspect_frame(session).identity
        assert backend.verify_overwrite_sources(session,identity,project,plan)
        assert backend._audio_cache and all(key[0].path==old.path for key in backend._audio_cache)
        assert next(iter(backend._audio_eof.values()))==len(origin_oracle(old.path))//8
        editor.apply_source_overwrite(plan)
        after=production_pcm(RenderEngine(),project,cache_media,'tail-after')
        assert after[:3*1920*8]==before[:3*1920*8]
        assert after[4*1920*8:]==before[4*1920*8:]
        assert after[3*1920*8:4*1920*8]==bytes(1920*8)
    finally:
        backend.close()


def test_real_eof_warm_silence_revalidates_source_and_is_not_metadata_only(cache_media,monkeypatch):
    import lumen.source_viewer as module
    with tempfile.TemporaryDirectory(prefix='cutcat-eof-replaced-') as directory:
        path=Path(directory)/'copy.mp4';path.write_bytes(copied_short_aac(cache_media).read_bytes())
        project,session=setup(path);editor=Editor(project)
        plan=editor.plan_source_overwrite(session.media_id,0,0,50,55)
        backend=SourcePreviewBackend();audio_decodes=[]
        real=module._Pipeline.spawn
        def capture(pipe,command,*args,**kwargs):
            if 'pcm_f32le' in command:audio_decodes.append(command)
            return real(pipe,command,*args,**kwargs)
        monkeypatch.setattr(module._Pipeline,'spawn',capture)
        try:
            identity=backend.inspect_frame(session).identity
            assert backend.verify_overwrite_sources(session,identity,project,plan)
            assert len(audio_decodes)==1 and backend._audio_eof and not backend._audio_cache
            actual_eof=len(origin_oracle(path))//8
            assert next(iter(backend._audio_eof.values()))==actual_eof
            assert backend._scan_samples==actual_eof
            assert backend.verify_overwrite_sources(session,identity,project,plan)
            assert len(audio_decodes)==1 and backend._scan_samples==0
            info=_probe(session,identity,None,time.monotonic()+30)
            assert backend._audio_f32(identity,info,session,50,55,None,time.monotonic()+30)==bytes(5*1920*8)
            # Even a zero-retained-PCM warm hit cannot authorize old EOF after
            # a source replacement; no model mutation is permitted.
            path.write_bytes(copied_short_aac(cache_media,96000).read_bytes())
            with pytest.raises(SourceViewerError,match='Source changed'):
                backend.verify_overwrite_sources(session,identity,project,plan)
            assert editor.revision==0 and not project.clips
        finally:
            backend.close()
        assert not backend._audio_eof


def test_eof_cache_is_decoder_bound_count_bounded_and_revalidates_claims(cache_media,monkeypatch):
    from dataclasses import replace
    import lumen.source_viewer as module
    backend=SourcePreviewBackend()
    try:
        _,session,identity,info,deadline=verified_inputs(backend,copied_short_aac(cache_media))
        backend._prepare_pcm(identity,info,session,[(96000,97000)],None,deadline,allow_absent=True)
        actual_eof=next(iter(backend._audio_eof.values()))
        assert actual_eof>math.ceil(info['audio_duration']*48000)
        dishonest=dict(info,audio_duration=Fraction(2))
        with pytest.raises(SourceViewerError,match='missing or incomplete'):
            backend._prepare_pcm(identity,dishonest,session,[(96000,97000)],None,deadline,allow_absent=True)
        with pytest.raises(SourceViewerError,match='missing or incomplete'):
            backend._prepare_pcm(identity,info,session,[(96000,97000)],None,deadline,allow_absent=False)
        old_decoder=backend._decoder_identity()
        monkeypatch.setattr(backend,'_decoder_identity',lambda:(old_decoder[0],('replacement',)))
        assert backend._known_eof(identity,info) is None
        monkeypatch.setattr(backend,'_decoder_identity',lambda:old_decoder)
        # Owned EOF metadata is bounded independently of retained file bytes.
        real_revalidate=backend._revalidate
        monkeypatch.setattr(backend,'_revalidate',lambda *args:None)
        for number in range(35):
            other=replace(identity,signature=f'eof-fixture-{number}')
            backend._prepare_pcm(other,info,session,[(96000,97000)],None,deadline,allow_absent=True)
            assert len(backend._audio_eof)<=32 and not backend._audio_cache
        assert backend._known_eof(identity,info) is None
        monkeypatch.setattr(backend,'_revalidate',real_revalidate)
    finally:
        backend.close()
    assert not backend._audio_eof


def test_cold_absence_cannot_bypass_scan_cap_or_cancel_then_warm_eof_needs_no_scan(cache_media,monkeypatch):
    import lumen.source_viewer as module
    backend=SourcePreviewBackend(max_scan_samples=48000)
    try:
        _,session,identity,info,deadline=verified_inputs(backend,copied_short_aac(cache_media))
        # Header understates duration, but actual decode runs longer. Must not
        # assume absent audio and bypass the cap at a late requested window.
        with pytest.raises(SourceViewerError,match='scan sample budget'):
            backend._prepare_pcm(identity,info,session,[(96000,97000)],None,deadline,allow_absent=True)
        assert not backend._audio_eof and backend._audio_temporary is None
    finally:backend.close()
    backend=SourcePreviewBackend();cancel=threading.Event()
    try:
        _,session,identity,info,deadline=verified_inputs(backend,copied_short_aac(cache_media))
        backend.progress_callback=lambda message,**values:cancel.set() if float(values.get('scanned',0))>0 else None
        with pytest.raises(SourceViewerCancelled):
            backend._prepare_pcm(identity,info,session,[(96000,97000)],cancel,deadline,allow_absent=True)
        assert not backend._audio_eof and not backend._audio_cache and backend._pending_bytes==0
        backend.progress_callback=None
        backend._prepare_pcm(identity,info,session,[(96000,97000)],None,deadline,allow_absent=True)
        backend._scan_samples=backend.max_scan_samples
        monkeypatch.setattr(module._Pipeline,'spawn',lambda *a,**kw:pytest.fail('verified absent window needs no new scan'))
        backend._prepare_pcm(identity,info,session,[(96000,97000)],None,deadline,allow_absent=True)
    finally:backend.close()

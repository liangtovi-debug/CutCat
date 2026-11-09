"""Actual FFmpeg PCM and H.264/AAC integration of inserted local sound effects."""
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import subprocess
import wave
import numpy as np
import pytest
from lumen.media import ffmpeg_available, probe
from lumen.model import Editor, Media, Project, SoundProvenance
from lumen.render import RenderEngine, RenderError
from lumen.sound_library import builtin_sounds
from test_sounds_model import sound

pytestmark=pytest.mark.skipif(not ffmpeg_available(),reason='FFmpeg required')


def decode(path):
    result=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-vn','-ac','1','-ar','48000','-f','f32le','pipe:1'],capture_output=True,check=True)
    return np.frombuffer(result.stdout,dtype='<f4')


@pytest.mark.parametrize('fps',[(24,1),(30,1),(30000,1001)])
@pytest.mark.parametrize('preview',[False,True])
def test_real_tail_at_exact_insert_frame_and_silence(tmp_path,fps,preview):
    # The only audible source segment is beyond the last whole project frame.
    rate=48000; count=48624; samples=np.zeros(count,dtype='<i2')
    samples[48144:]=(13000*np.sin(2*np.pi*1000*np.arange(count-48144)/rate)).astype('<i2')
    path=tmp_path/'tail.wav'
    with wave.open(str(path),'wb') as wav:
        wav.setparams((1,2,rate,0,'NONE','not compressed')); wav.writeframes(samples.tobytes())
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    media=Media('tail',str(path),'Tail',count/rate,has_video=False,has_audio=True,
                sound_provenance=SoundProvenance('tail','procedural','CC0',sha,sha,rate,count))
    editor=Editor(Project(width=64,height=48,fps_num=fps[0],fps_den=fps[1])); clip=editor.add_sound_clip(media,2,12)
    engine=RenderEngine(timeout=30)
    try:
        output=engine.preview(editor.project) if preview else engine.render(editor.project,tmp_path/'out.mp4')
        audio=decode(output)
        assert probe(output).duration==pytest.approx(clip.end/float(editor.project.fps),abs=.003)
    finally: engine.close()
    start=round(clip.start/float(editor.project.fps)*48000)
    assert np.max(np.abs(audio[:max(1,start-1024)]))<.001
    assert np.sqrt(np.mean(audio[start+48240:start+48528]**2))>.12
    assert hashlib.sha256(path.read_bytes()).hexdigest()==sha


def test_actual_pcm_graph_matches_original_at_exact_start(tmp_path):
    media=sound(tmp_path,56001); editor=Editor(Project(width=64,height=48))
    editor.add_sound_clip(media,2,7)
    engine=RenderEngine(timeout=30)
    try:
        inputs,graph,duration=engine._compile(editor.project)
        result=subprocess.run(['ffmpeg','-v','error','-filter_complex_threads','1',*inputs,
            '-filter_complex',graph,'-map','[aout]','-ac','2','-ar','48000','-f','f32le','pipe:1',
            '-map','[vout]','-f','null',os.devnull],
            capture_output=True,check=True)
    finally: engine.close()
    audio=np.frombuffer(result.stdout,dtype='<f4').reshape(-1,2)
    with wave.open(media.path,'rb') as wav: original=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')/32768
    start=7*1600
    assert len(audio)==editor.project.duration_frames*1600
    assert np.max(np.abs(audio[:start]))==0
    # FFmpeg's mono to stereo pan uses sqrt(1/2) for each channel.
    np.testing.assert_allclose(audio[start:start+len(original),0],original/np.sqrt(2),atol=1e-6)
    assert np.max(np.abs(audio[start+len(original):]))==0
    assert np.max(np.abs(audio))<.501


def test_modified_file_and_forged_pcm_metadata_are_rejected(tmp_path):
    media=sound(tmp_path,4800); editor=Editor(Project(width=64,height=48)); editor.add_sound_clip(media,2,0)
    engine=RenderEngine(timeout=30)
    try:
        engine.preview(editor.project)
        original=Path(media.path).read_bytes(); Path(media.path).write_bytes(original[:-1]+b'x')
        with pytest.raises(RenderError): engine.preview(editor.project)
        Path(media.path).write_bytes(original)
        forged=replace(media,sound_provenance=replace(media.sound_provenance,sample_count=1),duration=1/48000)
        bad=Editor(Project(width=64,height=48)); bad.add_sound_clip(forged,2,0)
        with pytest.raises(RenderError,match='samples'): engine.render(bad.project,tmp_path/'forged.mp4')
        assert not (tmp_path/'forged.mp4').exists()
    finally: engine.close()


def test_portable_reopen_export_after_source_removed(tmp_path):
    media=sound(tmp_path); editor=Editor(Project(width=64,height=48)); editor.add_sound_clip(media,2,3)
    path=tmp_path/'portable.lumen'; editor.project.save(path); Path(media.path).unlink()
    reopened=Project.load(path); assert not reopened.missing_media()
    engine=RenderEngine(timeout=30)
    try: audio=decode(engine.render(reopened,tmp_path/'reopened.mp4'))
    finally: engine.close()
    assert np.sqrt(np.mean(audio[6000:18000]**2))>.1


@pytest.mark.parametrize('pack', ['Kenney Interface Sounds 1.0', 'Kenney Impact Sounds 1.0',
    'Kenney RPG Audio', 'Swish - bamboo', 'Birds of Hawaii', 'Rain (Ambient'])
def test_external_pcm_source_interval_is_exact_after_portable_reopen(tmp_path,pack):
    from fractions import Fraction
    from test_external_sound_assets import external_media
    # Select a representative actual asset by source family, including stereo.
    entries=[e for e in builtin_sounds() if e.origin=='external-cc0' and pack.casefold() in e.source_name.casefold()]
    assert entries, pack
    entry=max(entries,key=lambda e:e.sample_count)
    local=tmp_path/'standalone.wav'; local.write_bytes(entry.path.read_bytes())
    entry=replace(entry,path=local); media=external_media(entry)
    editor=Editor(Project(width=64,height=48)); clip=editor.add_sound_clip(media,2,7)
    # Sample-exact non-frame endpoints exercise the selected source interval.
    offset=37; take=min(entry.sample_count-offset,32001)
    editor.trim_clip_source(clip.id,Fraction(offset,1600),Fraction(offset+take,1600))
    path=tmp_path/'film.lumen';editor.project.save(path);local.unlink()
    reopened=Project.load(path)
    assert reopened.media[0].sound_provenance==media.sound_provenance
    engine=RenderEngine(timeout=30)
    try:
        inputs,graph,_=engine._compile(reopened)
        result=subprocess.run(['ffmpeg','-v','error','-filter_complex_threads','1',*inputs,
            '-filter_complex',graph,'-map','[aout]','-ac','2','-ar','48000','-f','f32le','pipe:1',
            '-map','[vout]','-f','null',os.devnull],capture_output=True,check=True,timeout=30)
        exported=engine.render(reopened,tmp_path/'export.mp4')
        assert np.isfinite(decode(exported)).all()
    finally:engine.close()
    pcm=np.frombuffer(result.stdout,dtype='<f4').reshape(-1,2)
    with wave.open(reopened.media[0].path,'rb') as source:
        channels=source.getnchannels()
        original=np.frombuffer(source.readframes(source.getnframes()),dtype='<i2').reshape(-1,channels)/32768
    expected=original[offset:offset+take]
    if channels==1:expected=np.repeat(expected,2,axis=1)/np.sqrt(2)
    start=7*1600
    np.testing.assert_allclose(pcm[start:start+take],expected,atol=1e-6)
    assert np.max(np.abs(pcm[:start]))==0
    if len(pcm)>start+take:assert np.max(np.abs(pcm[start+take:]))==0
    assert np.max(np.abs(pcm))<=.5

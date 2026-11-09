"""Real decoded picture, PCM and delivery acceptance for two-sided roll edits.

Synthetic frame IDs and separate source tones are independent output oracles.
No model/runtime download, user footage, speaker playback or native build.
"""
from copy import deepcopy
from fractions import Fraction
import math
from pathlib import Path
import tempfile
import wave
import numpy as np
import pytest

from lumen.audio_timing import compile_audio_timing
from lumen.media import probe, ffmpeg_available
from lumen.model import Clip, Editor, Project
from lumen.render import RenderEngine
from lumen.timing import ClipTimeMap
from test_retime_video import (WIDTH, HEIGHT, PROJECT_RATES, assert_delivery_frames,
    decode, expected_indices, ff, frame_id_rgb, frame_sources, make_project,
    segment_pixels, engine)
from test_retime_audio import dominant_hz

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="FFmpeg/ffprobe required")


def av_source(path, fps, reverse, frequency):
    pixels=frame_id_rgb()
    if reverse: pixels=pixels[::-1].copy()
    picture=path.with_suffix('.picture.mkv')
    ff('-f','rawvideo','-pix_fmt','rgb48le','-s',f'{WIDTH}x{HEIGHT}','-r',str(fps),
       '-i','pipe:0','-an','-c:v','ffv1','-level','3','-pix_fmt','gbrp16le','-threads','1',
       '-color_range','pc','-color_primaries','bt709','-color_trc','bt709','-colorspace','rgb',
       picture, data=pixels.astype('<u2').tobytes())
    count=math.ceil(Fraction(len(pixels)*48000)/fps)
    sound=path.with_suffix('.wav')
    samples=.35*np.sin(2*np.pi*frequency*np.arange(count)/48000)
    with wave.open(str(sound),'wb') as stream:
        stream.setparams((1,2,48000,0,'NONE','not compressed'))
        stream.writeframes(np.rint(samples*32767).astype('<i2').tobytes())
    ff('-i',picture,'-i',sound,'-map','0:v','-map','1:a','-c','copy',path)
    np.testing.assert_array_equal(decode(path),pixels)
    return probe(path),pixels


@pytest.fixture(scope='module')
def av_sources(tmp_path_factory):
    root=tmp_path_factory.mktemp('roll-av')
    return {fps:(av_source(root/f'a-{fps.numerator}.mkv',fps,False,440),
                 av_source(root/f'b-{fps.numerator}.mkv',fps,True,880)) for fps in PROJECT_RATES}


def clip(cid,mid,start,origin,endpoint,speed):
    return Clip(cid,mid,0,start,0,math.ceil((endpoint-origin)/speed),
                time_map=ClipTimeMap(origin,endpoint,speed))


def pcm_decode(path):
    return np.frombuffer(ff('-i',path,'-vn','-ar','48000','-ac','1','-c:a','pcm_f32le',
                            '-f','f32le','pipe:1'),dtype='<f4')


def exact_pcm(engine,project):
    inputs,graph,_=engine._compile(project)
    # Consume both production outputs. Discarding the generated picture via
    # nullsink can stop FFmpeg's audio-only graph before its last silence block.
    # A lossless dual-stream mux keeps the same scheduling contract as export.
    with tempfile.TemporaryDirectory(prefix='roll-pcm-') as directory:
        output=Path(directory)/'compiled.mkv'
        ff(*inputs,'-filter_complex_threads','1','-filter_complex',graph,
           '-map','[vout]','-map','[aout]','-c:v','ffv1','-c:a','pcm_f32le',output)
        return pcm_decode(output)


def independently_rounded_samples(frame,fps):
    exact=Fraction(frame*48000)/fps
    return (2*exact.numerator+exact.denominator)//(2*exact.denominator)


@pytest.mark.parametrize('fps',PROJECT_RATES,ids=['23.976','29.97'])
@pytest.mark.parametrize('delta',[-3,3])
def test_mixed_speed_roll_actual_preview_export_pcm_cache_and_roundtrip(tmp_path,av_sources,engine,fps,delta):
    (ma,pa),(mb,pb)=av_sources[fps]
    a=clip('left',ma.id,3,Fraction(5,4),Fraction(103,4),Fraction(3,2))
    b=clip('right',mb.id,a.end,Fraction(81,4),Fraction(171,4),Fraction(2,3))
    marker=Clip('fixed',ma.id,0,b.end+3,50,2)
    e=Editor(Project(width=WIDTH,height=HEIGHT,fps_num=fps.numerator,fps_den=fps.denominator,
                     media=[deepcopy(ma),deepcopy(mb)],clips=[a,b,marker]))
    before=deepcopy(e.project)
    old_segments=[engine._graded_segment(m,c,e.project,None) for m,c in ((ma,a),(mb,b))]
    old_preview=engine.preview(e.project)
    new_cut=a.end+delta
    e.roll_cut('left',new_cut); a,b=e.project.clips[:2]
    expected_a=pa[expected_indices(a.source_in,a.source_out,a.speed)]
    expected_b=pb[expected_indices(b.source_in,b.source_out,b.speed)]
    expected=np.concatenate([expected_a,expected_b])
    changed_segments=[engine._graded_segment(m,c,e.project,None) for m,c in ((ma,a),(mb,b))]
    assert all(old!=new for old,new in zip(old_segments,changed_segments))
    for path,pixels in zip(changed_segments,(expected_a,expected_b)):
        np.testing.assert_array_equal(decode(path),pixels)
    new_preview=engine.preview(e.project); assert new_preview!=old_preview
    exported=engine.render(e.project,tmp_path/'rolled.mp4')
    for path in (new_preview,exported):
        pictures=decode(path,rgb16=False)
        assert len(pictures)==before.duration_frames
        assert pictures[:a.start].max()<=3 and pictures[b.end:marker.start].max()<=3
        assert_delivery_frames(pictures[a.start:b.end],expected)
        assert_delivery_frames(pictures[marker.start:marker.end],pa[50:52])
        # Both sides of the moved boundary are checked through full delivery.
        assert_delivery_frames(pictures[new_cut-1:new_cut+1],expected[a.duration-1:a.duration+1])
        audio=pcm_decode(path)
        expected_samples=independently_rounded_samples(e.project.duration_frames,fps)
        assert expected_samples<=len(audio)<expected_samples+1024  # AAC codec padding, not edit time.
        for c,hz in ((a,440),(b,880)):
            lo=independently_rounded_samples(c.start+5,fps)
            hi=independently_rounded_samples(c.start+9,fps)
            assert abs(dominant_hz(audio[lo:hi])-hz)<10
            assert np.sqrt(np.mean(audio[lo:hi]**2))>.10
    pcm=exact_pcm(engine,e.project)
    assert len(pcm)==independently_rounded_samples(e.project.duration_frames,fps)
    # A window inside the reassigned three-frame region must switch source.
    # Interior-only tones cannot catch an audio cut accidentally left in place.
    old_cut=before.clips[0].end
    lo=independently_rounded_samples(Fraction(min(old_cut,new_cut)*4+3,4),fps)
    hi=independently_rounded_samples(Fraction(max(old_cut,new_cut)*4-3,4),fps)
    new_hz=440 if delta>0 else 880
    old_hz=880 if delta>0 else 440
    assert abs(dominant_hz(pcm[lo:hi])-new_hz)<30
    assert abs(dominant_hz(pcm_decode(old_preview)[lo:hi])-old_hz)<30
    plans=[compile_audio_timing(c,fps) for c in (a,b)]
    assert plans[0].start_sample+plans[0].sample_count==plans[1].start_sample
    assert sum(p.sample_count for p in plans)==independently_rounded_samples(b.end,fps)-independently_rounded_samples(a.start,fps)
    for c,hz in ((a,440),(b,880)):
        lo=independently_rounded_samples(c.start+5,fps); hi=independently_rounded_samples(c.start+9,fps)
        assert abs(dominant_hz(pcm[lo:hi])-hz)<10
    saved=tmp_path/'rolled.lumen'; e.project.save(saved); loaded=Project.load(saved)
    assert loaded==e.project and engine.preview(loaded)==new_preview
    assert e.undo() and e.project==before and engine.preview(e.project)==old_preview
    assert e.redo() and engine.preview(e.project)==new_preview


@pytest.mark.parametrize('fps',PROJECT_RATES,ids=['23.976','29.97'])
@pytest.mark.parametrize('speed',[Fraction(1,2),Fraction(3,2),Fraction(4)])
def test_split_then_roll_preserves_all_decoded_picture_frames(frame_sources,engine,fps,speed):
    source=frame_sources[fps]
    e=Editor(make_project(source,fps,origin=Fraction(9,8),endpoint=Fraction(251,8),speed=speed))
    whole=segment_pixels(engine,e.project)
    split=max(2,e.project.duration_frames//2)
    left,_=e.split_clip('picture',split)
    for delta in (-1,1):
        e.roll_cut(left.id,split+delta)
        joined=np.concatenate([segment_pixels(engine,e.project,c) for c in e.project.clips])
        np.testing.assert_array_equal(joined,whole)
        assert e.undo()


@pytest.mark.parametrize('fps',PROJECT_RATES,ids=['23.976','29.97'])
@pytest.mark.parametrize('delta',[-3,3])
def test_roll_real_pcm_selects_exact_changed_source_samples_and_excludes_tails(tmp_path,engine,fps,delta):
    # Time-varying deterministic PCM catches stale source trim as well as a
    # stale timeline cut. Compare every output sample before lossy AAC encoding.
    rng=np.random.default_rng(841)
    arrays=[]; media=[]
    for index in range(2):
        pcm=rng.integers(-10000,10001,48000*4,dtype=np.int16)
        path=tmp_path/f'varying-{index}.wav'
        with wave.open(str(path),'wb') as stream:
            stream.setparams((1,2,48000,0,'NONE','not compressed'));stream.writeframes(pcm.astype('<i2').tobytes())
        arrays.append(pcm.astype(np.float32)/32768);media.append(probe(path))
    a=clip('a',media[0].id,7,Fraction(3,4),Fraction(85,4),Fraction(1));a.track=2
    b=clip('b',media[1].id,a.end,Fraction(41,4),Fraction(163,4),Fraction(1));b.track=2
    e=Editor(Project(width=WIDTH,height=HEIGHT,fps_num=fps.numerator,fps_den=fps.denominator,media=media,clips=[a,b]))
    cut=a.end+delta; outer=b.end
    e.roll_cut('a',cut)
    actual=exact_pcm(engine,e.project)
    expected=np.zeros(independently_rounded_samples(outer,fps),dtype=np.float32)
    # Oracle formulas do not read production timing plans or modified maps.
    for data,start,end,origin,endpoint in (
        (arrays[0],7,cut,Fraction(3,4),Fraction(85,4)+delta),
        (arrays[1],cut,outer,Fraction(41,4)+delta,Fraction(163,4))):
        lo=math.floor(origin/fps*48000+Fraction(1,2)); hi=math.floor(endpoint/fps*48000+Fraction(1,2))
        out=independently_rounded_samples(start,fps); end_sample=independently_rounded_samples(end,fps)
        selected=data[lo:hi][:end_sample-out]
        expected[out:out+len(selected)]=selected
        # Remaining partial-frame budget must be silence, not next-source data.
        assert end_sample-out>len(selected)
    np.testing.assert_allclose(actual,expected,rtol=3e-7,atol=3e-7)

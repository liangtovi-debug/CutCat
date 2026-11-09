"""Synthetic decoded picture/PCM acceptance for track-local ripple deletion.

Exercises production preview/export, exact source selections, retained gaps,
independent audio anchors, cache invalidation, project save and undo/redo.
"""
from copy import deepcopy
from fractions import Fraction
import math
import wave

import numpy as np
import pytest

from lumen.media import ffmpeg_available, probe
from lumen.model import Clip, Editor, Project
from lumen.timing import ClipTimeMap
from test_retime_video import (WIDTH, HEIGHT, PROJECT_RATES, assert_delivery_frames,
                               decode, expected_indices, engine)
from test_retime_audio import dominant_hz
from test_roll_media import (av_sources, exact_pcm, independently_rounded_samples,
                             pcm_decode)

pytestmark=pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


@pytest.mark.parametrize('fps', PROJECT_RATES, ids=['23.976','29.97'])
@pytest.mark.parametrize('anchored_audio', [False, True], ids=['shortened','audio-anchor'])
def test_ripple_actual_preview_export_frames_audio_and_history(tmp_path, av_sources, engine, fps, anchored_audio):
    (ma, pa), (mb, pb)=av_sources[fps]
    a=Clip('before', ma.id, 0, 2, 0, 10)
    removed=Clip('remove', ma.id, 0, 15, 10, 9)
    b=Clip('later', mb.id, 0, 27, 0, 17,
           time_map=ClipTimeMap(Fraction(5,4), Fraction(103,4), Fraction(3,2)))
    clips=[b, removed, a]
    if anchored_audio: clips.append(Clip('anchor', ma.id, 2, 45, 0, 12))
    e=Editor(Project(width=WIDTH,height=HEIGHT,fps_num=fps.numerator,fps_den=fps.denominator,
                     media=[deepcopy(ma),deepcopy(mb)],clips=clips))
    before=deepcopy(e.project); old_preview=engine.preview(e.project)
    old_segment=engine._graded_segment(mb, b, e.project, None)
    e.ripple_delete_clip('remove'); b=e._clip('later')
    assert b.start==18 and b.end==35
    # Moving a clip must not alter its independent source selection.
    expected=pb[expected_indices(Fraction(5,4),Fraction(103,4),Fraction(3,2))]
    np.testing.assert_array_equal(decode(engine._graded_segment(mb,b,e.project,None)), decode(old_segment))
    np.testing.assert_array_equal(decode(old_segment), expected)
    preview=engine.preview(e.project); assert preview!=old_preview
    exported=engine.render(e.project,tmp_path/'ripple.mp4')
    duration=57 if anchored_audio else 35
    for path in (preview,exported):
        picture=decode(path,rgb16=False)
        assert len(picture)==duration
        assert picture[:2].max()<=3 and picture[12:18].max()<=3
        assert_delivery_frames(picture[2:12],pa[:10])
        assert_delivery_frames(picture[18:35],expected)
        if anchored_audio: assert picture[35:].max()<=3
        audio=pcm_decode(path)
        sample=lambda frame: independently_rounded_samples(frame,fps)
        assert sample(duration)<=len(audio)<sample(duration)+1024
        # This region was the removed 440 Hz clip; it must now be the shifted
        # survivor's 880 Hz source audio, not stale or independently timed audio.
        assert abs(dominant_hz(audio[sample(20):sample(23)])-880)<15
        assert abs(dominant_hz(pcm_decode(old_preview)[sample(20):sample(23)])-440)<15
        assert np.max(np.abs(audio[sample(13):sample(17)]))<.005
        if anchored_audio:
            assert abs(dominant_hz(audio[sample(47):sample(52)])-440)<10
            assert np.max(np.abs(audio[sample(37):sample(43)]))<.005
    exact=exact_pcm(engine,e.project)
    assert len(exact)==independently_rounded_samples(duration,fps)
    saved=tmp_path/'ripple.lumen'; e.project.save(saved); loaded=Project.load(saved)
    assert loaded==e.project and engine.preview(loaded)==preview
    assert e.undo() and e.project==before and engine.preview(e.project)==old_preview
    assert e.redo() and engine.preview(e.project)==preview


@pytest.mark.parametrize('fps', PROJECT_RATES, ids=['23.976','29.97'])
def test_ripple_exact_pcm_source_shift_gaps_partial_tail_and_other_track_anchor(tmp_path, engine, fps):
    rng=np.random.default_rng(392)
    arrays=[]; media=[]
    for index in range(2):
        pcm=rng.integers(-9000,9001,48000*4,dtype=np.int16)
        path=tmp_path/f'source-{index}.wav'
        with wave.open(str(path),'wb') as stream:
            stream.setparams((1,2,48000,0,'NONE','not compressed'))
            stream.writeframes(pcm.astype('<i2').tobytes())
        arrays.append(pcm.astype(np.float32)/32768); media.append(probe(path))
    clips=[Clip('before',media[0].id,2,2,1,6),
           Clip('remove',media[0].id,2,10,12,7),
           Clip('later',media[0].id,2,20,0,12,
                time_map=ClipTimeMap(Fraction(7,4),Fraction(53,4))),
           Clip('anchor',media[1].id,3,30,8,8)]
    e=Editor(Project(width=WIDTH,height=HEIGHT,fps_num=fps.numerator,fps_den=fps.denominator,
                     media=media,clips=clips))
    e.ripple_delete_clip('remove'); actual=exact_pcm(engine,e.project)
    sample=lambda frame: independently_rounded_samples(frame,fps)
    expected=np.zeros(sample(38),dtype=np.float32)
    # Literal independent oracle: same exact source sample spans, changed
    # timeline placement only. The other track remains at frame 30, not 23.
    for data,start,end,origin,endpoint in (
        (arrays[0],2,8,Fraction(1),Fraction(7)),
        (arrays[0],13,25,Fraction(7,4),Fraction(53,4)),
        (arrays[1],30,38,Fraction(8),Fraction(16))):
        lo=math.floor(origin/fps*48000+Fraction(1,2))
        hi=math.floor(endpoint/fps*48000+Fraction(1,2))
        out=sample(start); limit=sample(end)
        selected=data[lo:hi][:limit-out]
        expected[out:out+len(selected)]=selected
    np.testing.assert_allclose(actual,expected,rtol=3e-7,atol=3e-7)
    assert np.max(np.abs(actual[sample(8):sample(13)]))==0
    assert np.max(np.abs(actual[sample(25):sample(30)]))==0

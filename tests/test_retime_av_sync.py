"""Actual muxed A/V marker acceptance, including a delayed audio stream."""
from fractions import Fraction
import subprocess
import wave
import numpy as np
import pytest
from lumen.media import probe, ffmpeg_binary, ffmpeg_available
from lumen.model import Project, Clip, Editor
from lumen.render import RenderEngine

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg required')

@pytest.mark.parametrize('fps', [Fraction(24000, 1001), Fraction(30000, 1001)])
@pytest.mark.parametrize('speed', [Fraction(1, 4), Fraction(3, 2), Fraction(4)])
def test_delayed_audio_marker_tracks_exact_picture_marker(tmp_path, fps, speed):
    rate = f'{fps.numerator}/{fps.denominator}'
    frames = np.zeros((60, 48, 64, 3), np.uint8)
    frames[24] = 255
    picture = tmp_path / 'picture.mkv'
    subprocess.run([ffmpeg_binary(), '-v','error','-f','rawvideo','-pix_fmt','rgb24',
                    '-s','64x48','-r',rate,'-i','pipe:0','-an','-c:v','ffv1',str(picture)],
                   input=frames.tobytes(), capture_output=True, check=True)
    offset = Fraction(1, 8)
    event = Fraction(24) / fps
    samples = np.zeros(round(float(Fraction(60)/fps-offset)*48000), np.float32)
    begin = round(float(event-offset)*48000)
    # Symmetric 20ms tone burst centered at the source frame's start marker.
    first = begin-480
    samples[first:first+960] = .6*np.sin(2*np.pi*1000*np.arange(960)/48000)
    sound = tmp_path / 'sound.wav'
    with wave.open(str(sound),'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes(np.rint(samples*32767).astype('<i2').tobytes())
    source = tmp_path / 'source.mkv'
    subprocess.run([ffmpeg_binary(),'-v','error','-i',str(picture),'-itsoffset',str(float(offset)),
                    '-i',str(sound),'-map','0:v','-map','1:a','-c','copy',str(source)],
                   capture_output=True,check=True)
    media = probe(source)
    assert abs(media.audio_offset-float(offset)) < .002
    p = Project(width=64,height=48,fps_num=fps.numerator,fps_den=fps.denominator,
                media=[media],clips=[Clip('c',media.id,0,3,0,49)])
    editor=Editor(p);editor.set_clip_speed('c',speed);p=editor.project
    with_engine=RenderEngine(timeout=60)
    try:
        output=with_engine.render(p,tmp_path/'delivery.mp4')
        raw=subprocess.run([ffmpeg_binary(),'-v','error','-i',str(output),'-an',
                            '-pix_fmt','rgb24','-f','rawvideo','pipe:1'],capture_output=True,check=True).stdout
        pictures=np.frombuffer(raw,np.uint8).reshape(-1,48,64,3)
        bright=np.flatnonzero(pictures[:,24,32,0]>220)
        expected_frame=3+int(Fraction(24)/speed)
        assert len(bright) and int(bright[0])==expected_frame
        pcm=subprocess.run([ffmpeg_binary(),'-v','error','-i',str(output),'-vn','-ac','1',
                            '-ar','48000','-f','f32le','pipe:1'],capture_output=True,check=True).stdout
        audio=np.frombuffer(pcm,'<f4');expected=float(Fraction(expected_frame)/fps)
        lo=max(0,round((expected-.12)*48000));hi=min(len(audio),round((expected+.12)*48000))
        energy=audio[lo:hi].astype(np.float64)**2
        assert energy.sum() > .1
        centroid=float(np.dot(np.arange(lo,hi)/48000,energy)/energy.sum())
        # This is a measured marker tolerance, not sample-identical time stretching.
        assert abs(centroid-expected) < 1/float(fps)
    finally:
        with_engine.close()

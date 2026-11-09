import copy
from pathlib import Path
import subprocess
import threading
import wave
import struct
import math
import pytest
from lumen.media import probe, ffmpeg_available, MediaError
from lumen.model import Project, Clip, Grade
from lumen.render import RenderEngine, RenderError, RenderCancelled

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')

def ff(*args):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *map(str, args)], check=True, capture_output=True)

@pytest.fixture
def sources(tmp_path):
    result = {}
    for color in ('red', 'blue', 'gray'):
        p = tmp_path / (color + '.mp4')
        ff('-f', 'lavfi', '-i', f'color={color}:s=64x48:r=30:d=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', p)
        result[color] = probe(p)
    return result

@pytest.fixture
def engine():
    e = RenderEngine(timeout=30)
    yield e
    e.close()

def project(media, clips, **kw):
    return Project(width=64, height=48, media=list(media), clips=clips, **kw)

def pixels(path, frame):
    p = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-vf', f'select=eq(n\\,{frame})', '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'], capture_output=True, check=True)
    assert len(p.stdout) == 64 * 48 * 3
    return tuple(p.stdout[(24 * 64 + 32) * 3:(24 * 64 + 32) * 3 + 3])

def audio(path):
    p = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-vn', '-ac', '1', '-ar', '48000', '-f', 'f32le', 'pipe:1'], capture_output=True, check=True)
    return struct.unpack('<' + 'f' * (len(p.stdout) // 4), p.stdout)

def rms(samples, start, end):
    x = samples[int(start * 48000):int(end * 48000)]
    return math.sqrt(sum(v*v for v in x) / len(x))

def test_probe_and_cuts_gaps_overlay(tmp_path, sources, engine):
    red, blue = sources['red'], sources['blue']
    assert red.has_video and not red.has_audio
    p = project([red, blue], [Clip('r', red.id, 0, 0, 0, 15), Clip('r2', red.id, 0, 30, 0, 30), Clip('b', blue.id, 1, 40, 0, 10)])
    out = engine.render(p, tmp_path / 'out.mp4')
    assert pixels(out, 14)[0] > 230
    assert max(pixels(out, 15)) < 5
    assert max(pixels(out, 29)) < 5
    assert pixels(out, 30)[0] > 230
    assert pixels(out, 39)[0] > 230
    assert pixels(out, 40)[2] > 230
    assert pixels(out, 49)[2] > 230
    assert pixels(out, 50)[0] > 230
    assert max(abs(x) for x in audio(out)) < .001
    assert engine.frame(p, 40, width=64).startswith(b'\x89PNG')
    assert engine.preview(p) == engine.preview(p)

def test_grade_bypass(tmp_path, sources, engine):
    m = sources['gray']
    p = project([m], [Clip('g', m.id, 0, 0, 0, 10)])
    original = pixels(engine.render(p, tmp_path / 'original.mp4'), 3)
    p.clips[0].grade.exposure = 1
    brighter = pixels(engine.render(p, tmp_path / 'bright.mp4'), 3)
    assert brighter[0] > original[0] + 70
    p.clips[0].grade.enabled = False
    bypass = pixels(engine.render(p, tmp_path / 'bypass.mp4'), 3)
    assert bypass == original

def test_output_safety_and_cancel(tmp_path, sources, engine):
    m = sources['red']
    p = project([m], [Clip('r', m.id, 0, 0, 0, 30)])
    existing = tmp_path / 'existing.mp4'
    existing.write_bytes(b'keep')
    with pytest.raises(RenderError): engine.render(p, existing)
    assert existing.read_bytes() == b'keep'
    with pytest.raises(RenderError): engine.render(p, m.path)
    cancel = threading.Event(); cancel.set()
    with pytest.raises(RenderCancelled): engine.render(p, tmp_path / 'cancel.mp4', cancel=cancel)
    assert not (tmp_path / 'cancel.mp4').exists()
    assert not list(tmp_path.glob('.lumen-render-*'))

def test_shifted_audio_trim_gain_mute(tmp_path, engine):
    source = tmp_path / 'offset.mkv'
    ff('-f', 'lavfi', '-i', 'color=red:s=64x48:r=30:d=2', '-itsoffset', '0.5', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1.5', '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-c:a', 'pcm_s16le', source)
    m = probe(source)
    assert abs(m.audio_offset - .5) < .01
    c = Clip('av', m.id, 0, 0, 0, 60)
    p = project([m], [c])
    samples = audio(engine.render(p, tmp_path / 'offset-out.mp4'))
    assert rms(samples, .05, .4) < .001
    assert rms(samples, .6, .9) > .05
    c.source_in = 30; c.duration = 30; c.gain_db = -12
    samples = audio(engine.render(p, tmp_path / 'trim.mp4'))
    assert .015 < rms(samples, .1, .8) < .03
    c.muted = True
    samples = audio(engine.render(p, tmp_path / 'mute.mp4'))
    assert rms(samples, .1, .8) < .001

def test_rational_rate_audio_only(tmp_path, engine):
    source = tmp_path / 'tone.wav'
    ff('-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100:duration=2', source)
    m = probe(source)
    p = project([m], [Clip('a', m.id, 2, 3, 0, 30, fade_in=3, fade_out=3)], fps_num=30000, fps_den=1001)
    out = engine.render(p, tmp_path / 'audio.mp4')
    info = probe(out)
    assert (info.fps_num, info.fps_den) == (30000, 1001)
    assert max(pixels(out, 20)) < 5
    samples = audio(out)
    assert rms(samples, .01, .07) < .001
    assert rms(samples, .3, .7) > .05

def test_hdr_rejected(tmp_path):
    p = tmp_path / 'hdr.mp4'
    ff('-f', 'lavfi', '-i', 'color=white:s=64x48:r=30:d=0.2', '-c:v', 'libx264', '-color_trc', 'smpte2084', '-color_primaries', 'bt2020', '-colorspace', 'bt2020nc', p)
    with pytest.raises(MediaError, match='SDR Rec.709'): probe(p)

def test_lut_path_escaping_and_failure_cleanup(tmp_path, sources, engine):
    lut = tmp_path / "identity's [a],b; space.cube"
    lut.write_text('LUT_3D_SIZE 2\n' + '\n'.join(f'{r} {g} {b}' for b in (0,1) for g in (0,1) for r in (0,1)) + '\n')
    m = sources['gray']
    p = project([m], [Clip('g', m.id, 0, 0, 0, 5, grade=Grade(lut_path=str(lut)))])
    out = engine.render(p, tmp_path / 'lut.mp4')
    assert 120 < pixels(out, 2)[0] < 135
    lut.write_text('not a lut')
    with pytest.raises(RenderError): engine.render(p, tmp_path / 'bad-lut.mp4')
    assert not (tmp_path / 'bad-lut.mp4').exists()
    assert not list(tmp_path.glob('.lumen-render-*'))

def test_cancel_running_and_timeout(tmp_path, sources, engine):
    m = sources['red']
    # Larger scale ensures subprocess is alive when cancellation is raised.
    p = Project(width=1920, height=1080, media=[m], clips=[Clip('r', m.id, 0, 0, 0, 60)])
    cancel = threading.Event()
    timer = threading.Timer(.2, cancel.set)
    timer.start()
    try:
        with pytest.raises(RenderCancelled): engine.render(p, tmp_path / 'cancel-live.mp4', cancel=cancel)
    finally: timer.cancel()
    assert not (tmp_path / 'cancel-live.mp4').exists()
    assert not list(tmp_path.glob('.lumen-render-*'))
    engine.timeout = .001
    with pytest.raises(RenderError, match='timed out'): engine.render(p, tmp_path / 'timeout.mp4')
    assert not (tmp_path / 'timeout.mp4').exists()
    assert not list(tmp_path.glob('.lumen-render-*'))

def test_audio_mix_fades_and_negative_offset(tmp_path, engine):
    source = tmp_path / 'audio.wav'
    ff('-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2', source)
    m = probe(source)
    p = project([m], [Clip('a', m.id, 2, 0, 0, 30, fade_in=9, fade_out=9)])
    s = audio(engine.render(p, tmp_path / 'fades.mp4'))
    assert rms(s, .01, .04) < rms(s, .4, .6) * .2
    assert rms(s, .96, .99) < rms(s, .4, .6) * .2
    p.clips[0].fade_in = p.clips[0].fade_out = 0
    p.clips.append(Clip('b', m.id, 3, 0, 0, 30))
    mixed = audio(engine.render(p, tmp_path / 'mix.mp4'))
    assert 1.8 < rms(mixed, .3, .7) / rms(s, .4, .6) < 2.2
    # Audio starts before the first video; trim to the video's timestamp origin.
    offset = tmp_path / 'negative-offset.mkv'
    ff('-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2', '-itsoffset', '0.5', '-f', 'lavfi', '-i', 'color=red:s=64x48:r=30:d=1.5', '-map', '1:v', '-map', '0:a', '-c:v', 'libx264', '-c:a', 'pcm_s16le', offset)
    n = probe(offset)
    assert abs(n.audio_offset + .5) < .01
    p = project([n], [Clip('n', n.id, 0, 0, 0, 30)])
    s = audio(engine.render(p, tmp_path / 'neg.mp4'))
    assert rms(s, .1, .5) > .05

def test_mixed_rate_source_in_and_fractional_boundaries(tmp_path, engine):
    source = tmp_path / 'two-colors.mkv'
    ff('-f', 'lavfi', '-i', 'color=red:s=80x60:r=24:d=1', '-f', 'lavfi', '-i', 'color=blue:s=80x60:r=24:d=1', '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-c:v', 'libx264', source)
    m = probe(source)
    p = project([m], [Clip('blue', m.id, 0, 7, 31, 12), Clip('red', m.id, 0, 19, 0, 11)], fps_num=30000, fps_den=1001)
    out = engine.render(p, tmp_path / 'fractional.mp4')
    assert max(pixels(out, 6)) < 5
    assert pixels(out, 7)[2] > 230
    assert pixels(out, 18)[2] > 230
    assert pixels(out, 19)[0] > 230
    assert pixels(out, 29)[0] > 230
    info = probe(out)
    assert (info.fps_num, info.fps_den) == (30000,1001)

def test_changed_shorter_source_rejected(tmp_path, sources, engine):
    m = sources['red']
    p = project([m], [Clip('r', m.id, 0, 0, 0, 50)])
    ff('-f', 'lavfi', '-i', 'color=red:s=64x48:r=30:d=0.4', '-c:v', 'libx264', m.path)
    with pytest.raises(ValueError): engine.render(p, tmp_path / 'shortened.mp4')
    assert not (tmp_path / 'shortened.mp4').exists()

def test_cover_art_does_not_replace_video(tmp_path, sources, engine):
    cover = tmp_path / 'cover.png'
    ff('-f', 'lavfi', '-i', 'color=blue:s=64x48:r=1:d=1', '-frames:v', '1', cover)
    source = tmp_path / 'with-cover.mp4'
    ff('-i', cover, '-i', sources['red'].path, '-map', '0:v', '-map', '1:v', '-c:v:0', 'png', '-disposition:v:0', 'attached_pic', '-c:v:1', 'copy', source)
    m = probe(source)
    p = project([m], [Clip('r', m.id, 0, 0, 0, 15)])
    out = engine.render(p, tmp_path / 'cover-out.mp4')
    assert pixels(out, 3)[0] > 230

@pytest.mark.parametrize('field,value', [('contrast', 4), ('saturation', 4), ('temperature', 1), ('tint', -1), ('shadows', 1), ('highlights', -1)])
def test_grade_controls_render_at_supported_limits(tmp_path, sources, engine, field, value):
    m = sources['gray']
    g = Grade()
    setattr(g, field, value)
    p = project([m], [Clip('g', m.id, 0, 0, 0, 5, grade=g)])
    out = engine.render(p, tmp_path / 'grade.mp4')
    assert len(pixels(out, 2)) == 3

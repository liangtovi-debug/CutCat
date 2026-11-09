"""Independent frozen full-mix bytes versus session-only range PCM.

The reference below is copied from the accepted f91f80ac full audio compiler
(audio_mix.py SHA-256 899712de16a203aa932821b8f2ba701fe8bc08c8a681ec563f3ea833fa39c847).
It never calls the range builder, never crops/rebases model records and only
slices the finished full PCM bytes. Existing source timing and gain primitives
are unchanged by this feature; they are shared codec-boundary dependencies.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import subprocess

import numpy as np
import pytest

from lumen.audio_gain import AudioGainKeyframe, compile_audio_gain_filters
from lumen.audio_timing import compile_audio_timing
from lumen.media import ffmpeg_available, ffmpeg_binary
from lumen.model import AudioMixAnchor, Clip, Media, Project
from lumen.range_audio_mix import (RangeAudioMixBuilder, RangeAudioMixError,
                                   compile_range_audio_mix)
from lumen.timing import ClipTimeMap, duration_frames, sample_boundary

real = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def frozen_full_mix(project, media):
    """Full old graph; independent of AudioMixBuilder and range compilation."""
    fps = Fraction(project.fps_num, project.fps_den)
    end = sample_boundary(project.duration_frames, fps, 48000)
    clips = sorted(project.clips, key=lambda c: (c.track, c.start, c.id))
    graph = [f'anullsrc=r=48000:cl=stereo,atrim=end_sample={end}[silence]']
    inputs, labels, slots = [], [], {}
    for index, clip in enumerate(clips):
        source = media[clip.media_id]
        if not (source.has_audio and clip.source_audio_enabled and not clip.muted):
            continue
        plan = compile_audio_timing(clip, fps, source.audio_offset)
        if plan.silent:
            continue
        inputs += ['-protocol_whitelist', 'file,pipe', '-vn', '-sn', '-dn', '-i', str(source.path)]
        # Input is seven tokens; keep an explicit stream counter independent
        # of absolute input argument length.
        slot = len(labels)
        filters = [*plan.filters, f'volume={format(float(clip.gain_db), ".12g")}dB']
        filters += compile_audio_gain_filters(clip.audio_gain_keyframes, plan.sample_count)
        if clip.fade_in:
            count = sample_boundary(clip.start + clip.fade_in, fps, 48000) - plan.start_sample
            filters += [f'afade=t=in:ss=0:ns={count}']
        if clip.fade_out:
            start = sample_boundary(clip.end - clip.fade_out, fps, 48000) - plan.start_sample
            filters += [f'afade=t=out:ss={start}:ns={plan.sample_count - start}']
        filters += [f'adelay={plan.start_sample}S:all=1']
        label = f'a{index}'
        graph.append(f'[{slot}:a:0]' + ','.join(filters) + f'[{label}]')
        labels.append(label)
        anchor = clip.audio_mix_anchor
        slots[label] = (((anchor.track, anchor.start, anchor.clip_id) if anchor else
                         (clip.track, clip.start, clip.id)), clip.start, clip.id)
    if any(c.audio_mix_anchor is not None for c in clips):
        labels.sort(key=slots.__getitem__)
    gain = (f',volume={format(float(project.master_gain_db), ".12g")}dB:precision=float'
            if project.master_gain_db else '')
    graph.append(''.join(f'[{label}]' for label in ['silence', *labels])
                 + f'amix=inputs={len(labels)+1}:normalize=0:duration=first,'
                 + f'atrim=end_sample={end}{gain}[aout]')
    return inputs, ';'.join(graph)


def pcm(inputs, graph):
    return subprocess.run([ffmpeg_binary(), '-hide_banner', '-v', 'error', '-nostdin',
        '-filter_complex_threads', '1', *inputs, '-filter_complex', graph,
        '-map', '[aout]', '-ar', '48000', '-ac', '2', '-c:a', 'pcm_f32le',
        '-f', 'f32le', 'pipe:1'], capture_output=True, check=True, timeout=30).stdout


@pytest.fixture
def source(tmp_path):
    t = np.arange(48000 * 5, dtype=np.float64)
    samples = np.column_stack((.3*np.sin(t*.073) + .1, .2*np.cos(t*.137) - .08)).astype('<f4')
    samples[::1024, 0] = .75
    samples[1023::1024, 1] = -.625
    path = tmp_path / 'range-source.wav'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-f', 'f32le',
        '-ar', '48000', '-ac', '2', '-i', 'pipe:0', '-c:a', 'pcm_f32le', str(path)],
        input=samples.tobytes(), capture_output=True, check=True, timeout=20)
    return Media('source', str(path), 'Synthetic stereo', 5, has_video=False, has_audio=True)


def project_for(source, fps, speed, offset):
    source = replace(source, audio_offset=offset)
    a = Clip('a', source.id, 2, 7, 3, 66, gain_db=2.75, fade_in=7, fade_out=9,
             audio_gain_keyframes=[AudioGainKeyframe(-100, -3), AudioGainKeyframe(44000, 7),
                                   AudioGainKeyframe(78003, -9, 'hold'), AudioGainKeyframe(180000, 4)])
    if speed != 1:
        a.time_map = ClipTimeMap(Fraction(7, 3), Fraction(223, 3), speed)
        a.source_in = a.time_map.source_in
        a.duration = duration_frames(a.time_map)
    b = Clip('b', source.id, 3, 13, 11, 47, gain_db=-4.125, fade_in=3, fade_out=5,
             audio_mix_anchor=AudioMixAnchor(0, 0, 'historical-video'))
    # A suppressed picture-like slot and muted tail must not produce audio.
    suppressed = Clip('suppressed', source.id, 2, 0, 0, 2, source_audio_enabled=False)
    tail = Clip('tail', source.id, 2, max(a.end, b.end) + 10, 0, 4, muted=True)
    project = Project(width=64, height=48, fps_num=fps.numerator, fps_den=fps.denominator,
                      media=[source], clips=[tail, b, suppressed, a], master_gain_db=-1.234)
    project.validate()
    return project, {source.id: source}


@real
@pytest.mark.parametrize('fps', [Fraction(24), Fraction(24000,1001), Fraction(30000,1001)])
@pytest.mark.parametrize('speed,offset', [(Fraction(1), 0), (Fraction(1), .375),
                                        (Fraction(3,2), -.075), (Fraction(2,3), .125)])
def test_range_pcm_is_byte_exact_full_frozen_slice(source, fps, speed, offset):
    project, checked = project_for(source, fps, speed, offset)
    before = deepcopy(project.to_dict())
    full = pcm(*frozen_full_mix(project, checked))
    # Cross start, interior automation, clip end/fade and silent tail boundaries.
    ranges = [(0, 17), (12, 38), (31, min(68, project.duration_frames)),
              (project.duration_frames - 17, project.duration_frames)]
    for start, end in ranges:
        compiled = compile_range_audio_mix(project, checked, start, end)
        actual = pcm(compiled.inputs, compiled.graph)
        lo, hi = sample_boundary(start, fps), sample_boundary(end, fps)
        expected = full[lo * 8:hi * 8]
        assert actual == expected, (fps, speed, offset, start, end,
                                   len(actual), len(expected))
        assert len(actual) == compiled.end_sample * 8 == (hi-lo) * 8
    assert project.to_dict() == before


def test_fragment_keeps_original_clocks_and_exact_global_difference():
    media = Media('m', '/not-read.wav', 'm', 10, has_video=False, has_audio=True)
    clip = Clip('c', 'm', 2, 7, 3, 99, fade_in=5, fade_out=10,
                audio_gain_keyframes=[AudioGainKeyframe(100, 2), AudioGainKeyframe(50000, -9)])
    project = Project(fps_num=30000, fps_den=1001, media=[media], clips=[clip])
    builder = RangeAudioMixBuilder(project, 31, 60)
    fragment = builder.clip_fragment(0, media, input_index=4)
    plan = compile_audio_timing(clip, project.fps)
    lo, hi = sample_boundary(31, project.fps) - plan.start_sample, sample_boundary(60, project.fps) - plan.start_sample
    assert fragment.fragment.startswith('[4:a:0]')
    assert f'atrim=start_sample={lo}:end_sample={hi},asetpts=PTS-STARTPTS,adelay=0S' in fragment.fragment
    assert f'atrim=end_sample={plan.sample_count}' in fragment.fragment
    assert 'lt(n,100)' in fragment.fragment and 'lt(n,50000)' in fragment.fragment
    assert f'afade=t=out:ss={sample_boundary(96, project.fps)-plan.start_sample}' in fragment.fragment
    assert builder.work['c'].original_local_start_sample == lo
    assert builder.end_sample == sample_boundary(60, project.fps) - sample_boundary(31, project.fps)


@pytest.mark.parametrize('start,end', [(False, 1), (0, True), (-1, 1), (2, 2), (3, 2), (0, 400)])
def test_invalid_and_excessive_ranges_reject(start, end):
    project = Project(clips=[Clip('c', 'm', 2, 0, 0, 1000)])
    with pytest.raises(RangeAudioMixError):
        RangeAudioMixBuilder(project, start, end)


def test_long_stateful_prefix_rejects_without_rebasing():
    media = Media('m', '/not-read.wav', 'm', 100, has_video=False, has_audio=True)
    project = Project(media=[media], clips=[Clip('c', 'm', 2, 0, 0, 2000)])
    with pytest.raises(RangeAudioMixError, match='30 seconds'):
        compile_range_audio_mix(project, {'m': media}, 901, 920)


def test_unrelated_tail_and_silent_sources_are_not_inputs(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Range graph compilation performed file I/O')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    media = Media('m', '/audio.wav', 'm', 100, has_video=False, has_audio=True)
    missing = replace(media, id='missing', path='/missing.wav')
    late = replace(media, id='late', path='/late.wav', audio_offset=10)
    clips = [Clip('near', 'm', 2, 0, 0, 30), Clip('tail', 'missing', 2, 900, 0, 300),
             Clip('muted', 'missing', 3, 0, 0, 30, muted=True),
             Clip('late', 'late', 1, 0, 0, 30)]
    project = Project(media=[media, missing, late], clips=clips)
    compiled = compile_range_audio_mix(project, {'m':media, 'missing':missing, 'late':late}, 3, 23)
    assert compiled.source_ids == ('m',)
    assert '/missing.wav' not in compiled.inputs and '/late.wav' not in compiled.inputs
    assert '/audio.wav' in compiled.inputs
    assert 'amix=inputs=2' in compiled.graph


def test_source_history_bound_includes_late_source_in():
    media = Media('m', '/not-read.wav', 'm', 1000, has_video=False, has_audio=True)
    clip = Clip('c', 'm', 2, 0, 9300, 30)
    project = Project(media=[media], clips=[clip])
    with pytest.raises(RangeAudioMixError, match='300 seconds'):
        compile_range_audio_mix(project, {'m': media}, 3, 23)


@real
def test_44100_resampler_history_and_fractional_single_frame_slice(source, tmp_path):
    path = tmp_path / 'source-44100.wav'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-i', source.path,
                    '-ar', '44100', '-c:a', 'pcm_f32le', str(path)],
                   capture_output=True, check=True, timeout=20)
    source = replace(source, path=str(path), audio_offset=.035)
    clip = Clip('c', source.id, 2, 0, 1, 120, fade_in=7, fade_out=9,
                audio_gain_keyframes=[AudioGainKeyframe(-33, -6), AudioGainKeyframe(43013, 2)])
    project = Project(fps_num=30000, fps_den=1001, media=[source], clips=[clip])
    project.validate()
    full = pcm(*frozen_full_mix(project, {source.id: source}))
    for start, end in [(1, 2), (17, 34), (111, 120)]:
        compiled = compile_range_audio_mix(project, {source.id: source}, start, end)
        a, b = sample_boundary(start, project.fps), sample_boundary(end, project.fps)
        assert pcm(compiled.inputs, compiled.graph) == full[a*8:b*8]
    assert sample_boundary(2, project.fps) - sample_boundary(1, project.fps) == 1601


@real
def test_long_global_gap_does_not_create_global_silence(source):
    clip = Clip('c', source.id, 2, 108000, 0, 60, gain_db=-7,
                audio_gain_keyframes=[AudioGainKeyframe(0, -3), AudioGainKeyframe(96000, 2)])
    project = Project(media=[source], clips=[clip])
    before = deepcopy(project.to_dict())
    ranged = compile_range_audio_mix(project, {source.id:source}, 108003, 108023)
    # Integer 30 fps gives identical sample phase. Independent short full
    # reference avoids allocating one hour of synthetic output for this test.
    short = replace(project, clips=[replace(clip, start=0)])
    expected = pcm(*frozen_full_mix(short, {source.id:source}))
    assert pcm(ranged.inputs, ranged.graph) == expected[4800*8:36800*8]
    assert 'adelay=172800000' not in ranged.graph
    assert ranged.end_sample == 32000
    assert project.to_dict() == before


@real
def test_demand_stops_audio_processing_before_unrelated_active_clip_tail(source):
    import re
    clip = Clip('c', source.id, 2, 0, 0, 150)
    project = Project(media=[source], clips=[clip])
    compiled = compile_range_audio_mix(project, {source.id:source}, 3, 9)
    # Instrument ingress only in the test, before any source selection. This
    # measures PCM fed into the original stateful chain, not wall-clock guesses.
    graph = compiled.graph.replace('[0:a:0]', '[0:a:0]ashowinfo,')
    result = subprocess.run([ffmpeg_binary(), '-hide_banner', '-v', 'info', '-nostdin',
        '-filter_complex_threads', '1', *compiled.inputs, '-filter_complex', graph,
        '-map', '[aout]', '-ar', '48000', '-ac', '2', '-c:a', 'pcm_f32le',
        '-f', 'f32le', 'pipe:1'], capture_output=True, check=True, timeout=30)
    processed = sum(map(int, re.findall(rb'nb_samples:(\d+)', result.stderr)))
    assert len(result.stdout) == (9-3) * 1600 * 8
    assert 9*1600 <= processed < 48000, (processed, result.stderr.decode(errors='replace'))


@real
@pytest.mark.parametrize('codec,suffix', [('aac', '.m4a'), ('libmp3lame', '.mp3')])
def test_compressed_decode_prefix_matches_frozen_full(source, tmp_path, codec, suffix):
    from lumen.media import probe
    path = tmp_path / ('compressed' + suffix)
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-i', source.path,
                    '-ar', '44100', '-c:a', codec, str(path)],
                   capture_output=True, check=True, timeout=20)
    checked = probe(path)
    clip = Clip('c', checked.id, 2, 7, 11, 89, fade_in=7, fade_out=9,
                audio_gain_keyframes=[AudioGainKeyframe(-33, -6), AudioGainKeyframe(43013, 2)])
    project = Project(fps_num=24000, fps_den=1001, media=[checked], clips=[clip])
    project.validate()
    full = pcm(*frozen_full_mix(project, {checked.id:checked}))
    for start, end in [(17, 34), (87, 96)]:
        compiled = compile_range_audio_mix(project, {checked.id:checked}, start, end)
        a, b = sample_boundary(start, project.fps), sample_boundary(end, project.fps)
        assert pcm(compiled.inputs, compiled.graph) == full[a*8:b*8]

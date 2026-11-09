"""Frozen export-graph and real pre-AAC PCM regression for the shared mixer."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import hashlib
import os
from pathlib import Path
import subprocess

import numpy as np
import pytest

from lumen.audio_mix import AudioMixBuilder, compile_audio_mix, ordered_clips
from lumen.audio_timing import compile_audio_timing
from lumen.media import ffmpeg_available, ffmpeg_binary
from lumen.model import Clip, Editor, Media, Project
from lumen.render import RenderEngine
from lumen.timing import ClipTimeMap, duration_frames, sample_boundary


real = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def _legacy_graph(project, checked):
    """Independent frozen audio block from pre-extraction render.py.

    Baseline SHA-256: 4967170da0d1a487d5456cb34e48513823e6534e3f741d9ea6eb3500a75150c9.
    This intentionally does not invoke any shared mixer helper. Real PCM from
    this old graph is the oracle, including its original input-number holes.
    """
    fps = Fraction(project.fps_num, project.fps_den)
    end_sample = sample_boundary(project.duration_frames, fps, 48000)
    graph = [f'anullsrc=r=48000:cl=stereo,atrim=end_sample={end_sample}[silence]']
    inputs, audio = [], ['silence']
    for index, clip in enumerate(sorted(project.clips, key=lambda c: (c.track, c.start, c.id))):
        m = checked[clip.media_id]
        inputs += ['-protocol_whitelist', 'file,pipe', '-i', str(m.path)]
        if m.has_audio and not clip.muted:
            plan = compile_audio_timing(clip, fps, m.audio_offset)
            if not plan.silent:
                af = [*plan.filters, f'volume={format(float(clip.gain_db), ".12g")}dB']
                if clip.fade_in:
                    fade_samples = sample_boundary(clip.start + clip.fade_in, fps, 48000) - plan.start_sample
                    af += [f'afade=t=in:ss=0:ns={fade_samples}']
                if clip.fade_out:
                    fade_start = sample_boundary(clip.end - clip.fade_out, fps, 48000) - plan.start_sample
                    af += [f'afade=t=out:ss={fade_start}:ns={plan.sample_count - fade_start}']
                af += [f'adelay={plan.start_sample}S:all=1']
                graph += [f'[{index}:a:0]' + ','.join(af) + f'[a{index}]']
                audio += [f'a{index}']
    graph += [''.join(f'[{a}]' for a in audio)
              + f'amix=inputs={len(audio)}:normalize=0:duration=first,atrim=end_sample={end_sample}[aout]']
    return inputs, ';'.join(graph)


def _pcm(inputs, graph, output_label="aout", video_output=None, meter_output=False):
    result = subprocess.run(
        [ffmpeg_binary(), '-hide_banner', '-v', 'error', '-nostdin',
         '-filter_complex_threads', '1', *inputs, '-filter_complex', graph,
         '-map', f'[{output_label}]', '-ar', '48000', '-ac', '2', '-c:a', 'pcm_f32le',
         '-f', 'f32le', 'pipe:1',
         *(['-map', '[vout]', '-an', '-f', 'null', video_output] if video_output else []),
         *(['-map', '[measured]', '-vn', '-sn', '-dn', '-f', 'null', os.devnull]
           if meter_output else [])],
        capture_output=True, timeout=30, check=True)
    return result.stdout


@pytest.fixture
def pcm_source(tmp_path):
    rate, count = 48000, 48000 * 4
    t = np.arange(count) / rate
    # Distinct channels, sharp impulses and a selected-tail marker detect
    # altered gain/normalization, phase, channel routing, trim and padding.
    source = np.column_stack((.39 * np.sin(2 * np.pi * 440 * t),
                              .23 * np.sin(2 * np.pi * 997 * t))).astype('<f4')
    source[12000:12020, 0] = .9
    source[44000:44020, 1] = -.8
    source[95000:96000, 0] += .1
    path = tmp_path / 'synthetic.wav'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-f', 'f32le',
                    '-ar', str(rate), '-ac', '2', '-i', 'pipe:0',
                    '-c:a', 'pcm_f32le', str(path)], input=source.tobytes(),
                   capture_output=True, check=True, timeout=20)
    return Media('source', str(path), 'Synthetic', 4, has_video=False, has_audio=True)


def _edited_project(media, fps, case):
    first = Clip('first', media.id, 2, 7, 3, 60, gain_db=2.75, fade_in=7, fade_out=9)
    second = Clip('second', media.id, 3, 13, 11, 47, gain_db=-4.125, fade_in=3, fade_out=5)
    # A muted item controls the true timeline tail, not the last audible sample.
    tail = Clip('muted', media.id, 2, 90, 0, 13, muted=True)
    project = Project(width=64, height=48, fps_num=fps.numerator, fps_den=fps.denominator,
                      media=[media], clips=[tail, second, first])
    if case == 'retime':
        first.time_map = ClipTimeMap(Fraction(5, 3), Fraction(127, 2), Fraction(3, 2))
        first.source_in = first.time_map.source_in
        first.duration = duration_frames(first.time_map)
        second.time_map = ClipTimeMap(Fraction(7, 5), Fraction(163, 7), Fraction(2, 3))
        second.source_in = second.time_map.source_in
        second.duration = duration_frames(second.time_map)
    elif case == 'split-move-trim':
        editor = Editor(project)
        left, right = editor.split_clip('first', 29)
        right.id = 'split-right'
        editor.move_clip(right.id, 46)
        editor.trim_clip_source('second', Fraction(17, 3), Fraction(137, 3))
        project = editor.project
    project.validate()
    return project


@real
@pytest.mark.parametrize('fps', [Fraction(24000, 1001), Fraction(30000, 1001)],
                         ids=['23.976', '29.97'])
@pytest.mark.parametrize('case,offset', [('ordinary', .125), ('retime', -.075),
                                       ('split-move-trim', .3)])
def test_shared_audio_only_pcm_is_byte_identical_to_frozen_export(pcm_source, fps, case, offset):
    source = replace(pcm_source, audio_offset=offset)
    project = _edited_project(source, fps, case)
    checked = {source.id: source}
    original = deepcopy(project.to_dict())
    legacy_inputs, legacy_graph = _legacy_graph(project, checked)
    current = compile_audio_mix(project, checked)
    old_pcm = _pcm(legacy_inputs, legacy_graph)
    new_pcm = _pcm(current.inputs, current.graph)
    assert new_pcm == old_pcm, 'The shared mix changed pre-AAC PCM bytes'
    # ebur128 requires double input. Its analysis branch must not change the
    # float mix negotiated by export or the PCM used for sample-peak counts.
    metered = (current.graph + ';[aout]asplit=2[pcm][meter];'
               '[meter]ebur128=framelog=verbose[measured]')
    assert _pcm(current.inputs, metered, 'pcm', meter_output=True) == old_pcm
    assert len(new_pcm) == current.end_sample * 2 * 4
    samples = np.frombuffer(new_pcm, dtype='<f4').reshape(-1, 2)
    assert np.max(np.abs(samples)) > .1
    assert np.max(np.abs(samples[-1000:])) == 0
    assert current.source_ids == (source.id,)
    assert project.to_dict() == original


def test_fragment_contract_preserves_global_edges_gain_fades_and_first_stream():
    media = Media('m', '/unused.wav', 'source', 4, has_video=False, has_audio=True, audio_offset=.2)
    clip = Clip('c', 'm', 2, 7, 1, 30, gain_db=-3.123456789012345, fade_in=3, fade_out=5)
    project = Project(fps_num=30000, fps_den=1001, media=[media], clips=[clip])
    builder = AudioMixBuilder(project)
    fragment = builder.clip_fragment(0, media, input_index=9)
    assert builder.clips == (clip,)
    assert builder.fps == Fraction(30000, 1001)
    assert builder.end_sample == sample_boundary(37, builder.fps)
    assert fragment.fragment.startswith('[9:a:0]')
    assert fragment.fragment.endswith('[a0]')
    assert 'volume=-3.12345678901dB' in fragment.fragment
    start = sample_boundary(7, builder.fps)
    assert f'afade=t=in:ss=0:ns={sample_boundary(10, builder.fps) - start}' in fragment.fragment
    out = sample_boundary(32, builder.fps) - start
    assert f'afade=t=out:ss={out}:ns={fragment.timing.sample_count - out}' in fragment.fragment
    assert f'adelay={start}S:all=1[a0]' in fragment.fragment
    assert builder.final_fragment(['a0']) == (
        f'[silence][a0]amix=inputs=2:normalize=0:duration=first,atrim=end_sample={builder.end_sample}[aout]')


def test_ordering_is_track_start_id_not_insertion_or_source_order():
    clips = [Clip('z', 'm', 3, 0, 0, 1), Clip('b', 'm', 2, 2, 0, 1),
             Clip('a', 'm', 2, 2, 0, 1), Clip('c', 'm', 2, 0, 0, 1)]
    assert [clip.id for clip in ordered_clips(Project(clips=clips))] == ['c', 'a', 'b', 'z']


def test_audio_only_builder_does_not_probe_decode_grade_or_open_unused_inputs(monkeypatch):
    import lumen.media
    import lumen.render
    def forbidden(*args, **kwargs):
        pytest.fail('Audio graph compilation touched a source or picture pipeline')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(lumen.media, 'probe', forbidden)
    monkeypatch.setattr(lumen.render, 'probe', forbidden)
    monkeypatch.setattr(RenderEngine, '_graded_segment', forbidden)
    video = Media('video', '/missing-video.mkv', 'video', 4, has_video=True, has_audio=False)
    muted = Media('muted', '/missing-muted.wav', 'muted', 4, has_video=False, has_audio=True)
    late = Media('late', '/missing-late.wav', 'late', 4, has_video=False, has_audio=True, audio_offset=20)
    audible = replace(muted, id='audible', path='/checked-snapshot.wav')
    project = Project(media=[video, muted, late, audible], clips=[
        Clip('picture', 'video', 0, 0, 0, 30), Clip('muted', 'muted', 2, 0, 0, 30, muted=True),
        Clip('late', 'late', 2, 30, 0, 30), Clip('audible', 'audible', 3, 0, 0, 30)])
    plan = compile_audio_mix(project, {m.id: m for m in project.media})
    assert plan.inputs == ('-protocol_whitelist', 'file,pipe', '-vn', '-sn', '-dn', '-i', '/checked-snapshot.wav')
    assert plan.source_ids == ('audible',)
    assert '[0:a:0]' in plan.graph and '[a3]' in plan.graph
    assert ':V:' not in plan.graph and 'color=' not in plan.graph


@real
def test_fully_muted_project_is_exact_duration_silence_without_source_inputs(pcm_source):
    project = Project(fps_num=24000, fps_den=1001, media=[pcm_source], clips=[
        Clip('mute', pcm_source.id, 2, 13, 0, 47, muted=True)])
    plan = compile_audio_mix(project, {pcm_source.id: pcm_source})
    assert plan.inputs == () and plan.source_ids == ()
    result = _pcm(plan.inputs, plan.graph)
    assert len(result) == plan.end_sample * 2 * 4
    assert result == bytes(len(result))


def _graph_fixture(tmp_path):
    path = tmp_path / 'source.mkv'
    path.touch()
    media = Media('source', str(path), 'source', 4, 64, 48, True, True,
                  fps_num=30000, fps_den=1001, audio_offset=.125)
    mapping = ClipTimeMap(Fraction(5, 3), Fraction(127, 2), Fraction(3, 2))
    return Project(width=64, height=48, fps_num=30000, fps_den=1001, media=[media], clips=[
        Clip('sound', 'source', 2, 3, 1, 54, gain_db=-4, fade_in=9, fade_out=5),
        Clip('upper', 'source', 1, 7, 3, 45, gain_db=-3, fade_in=3),
        Clip('lower', 'source', 0, 0, 0, duration_frames(mapping), time_map=mapping, fade_out=7),
        Clip('muted-tail', 'source', 2, 90, 0, 12, muted=True)])


def test_full_renderer_graph_is_byte_identical_including_interspersed_video(tmp_path, monkeypatch):
    """The digest is captured from the frozen renderer, not the new mixer."""
    import lumen.audio_timing
    import lumen.render
    project = _graph_fixture(tmp_path)
    monkeypatch.setattr(lumen.render, 'probe', lambda *_args, **_kw: project.media[0])
    monkeypatch.setattr(lumen.audio_timing, 'ensure_retime_audio_capability', lambda *_args: {})
    monkeypatch.setattr(RenderEngine, '_graded_segment',
                        lambda self, media, clip, project, cancel: Path('/graded-' + clip.id + '.mkv'))
    engine = RenderEngine()
    try:
        inputs, graph, duration = engine._compile(project)
    finally:
        engine.close()
    assert hashlib.sha256(graph.encode()).hexdigest() == '1ddf70756b17cc70196a365f933205c9b1b4bb1f022f77aa2b6c9f8c5b8bf56e'
    assert inputs == (['-protocol_whitelist', 'file,pipe', '-i', str(project.media[0].path)] * 4
                      + ['-protocol_whitelist', 'file,pipe', '-i', str(Path('/graded-lower.mkv')),
                         '-protocol_whitelist', 'file,pipe', '-i', str(Path('/graded-upper.mkv'))])
    assert duration == 102 / (30000 / 1001)


@real
def test_audio_only_matches_real_full_renderer_first_stream_and_short_audio_tail(tmp_path):
    from lumen.media import probe
    path = tmp_path / 'video-with-short-first-audio.mkv'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin',
                    '-f', 'lavfi', '-i', 'color=c=red:s=64x48:r=30000/1001:d=3',
                    '-f', 'lavfi', '-i',
                    'aevalsrc=0.35*sin(2*PI*440*t)|0.25*sin(2*PI*997*t):s=48000:d=0.8',
                    '-f', 'lavfi', '-i', 'aevalsrc=0.9|0.9:s=48000:d=3',
                    '-map', '0:v:0', '-map', '1:a:0', '-map', '2:a:0',
                    '-c:v', 'ffv1', '-pix_fmt', 'yuv420p', '-c:a', 'pcm_f32le', str(path)],
                   capture_output=True, check=True, timeout=20)
    media = probe(path)
    project = Project(width=64, height=48, fps_num=30000, fps_den=1001, media=[media], clips=[
        Clip('picture', media.id, 0, 0, 0, 60),
        Clip('overlap', media.id, 2, 7, 5, 30, gain_db=-3, fade_out=3),
        Clip('muted-tail', media.id, 3, 95, 0, 10, muted=True)])
    engine = RenderEngine(timeout=30)
    try:
        inputs, graph, _duration = engine._compile(project)
        exported = _pcm(inputs, graph, video_output=str(tmp_path / 'discard'))
    finally:
        engine.close()
    compiled = compile_audio_mix(project, {media.id: media})
    audio_only = _pcm(compiled.inputs, compiled.graph)
    assert audio_only == exported
    assert len(audio_only) == compiled.end_sample * 8
    samples = np.frombuffer(audio_only, dtype='<f4').reshape(-1, 2)
    # The long loud second stream must never enter the mix. The first stream
    # is actually short despite the video's longer container duration.
    assert np.max(np.abs(samples[:30000])) > .2
    assert np.max(np.abs(samples[48000:])) == 0


@real
def test_mix_is_not_normalized_or_limited_before_analysis(pcm_source):
    project = Project(media=[pcm_source], clips=[
        Clip('a', pcm_source.id, 2, 0, 0, 60, gain_db=6),
        Clip('b', pcm_source.id, 3, 0, 0, 60, gain_db=6)])
    mix = compile_audio_mix(project, {pcm_source.id: pcm_source})
    combined = np.frombuffer(_pcm(mix.inputs, mix.graph), dtype='<f4')
    assert np.max(np.abs(combined)) > 3
    assert np.count_nonzero(np.abs(combined) > 1) > 10000


@real
def test_metered_terminal_retimed_clip_uses_scheduled_null_output(pcm_source, tmp_path):
    # An anullsink on the meter branch can panic FFmpeg's EOF scheduler when
    # a retimed clip ends at the source and timeline ends simultaneously. A
    # real mapped null-muxer output keeps both branches scheduled to EOF.
    path = tmp_path / 'exact-two-seconds.wav'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-i', pcm_source.path,
                    '-t', '2', '-c:a', 'pcm_f32le', str(path)],
                   capture_output=True, timeout=20, check=True)
    source = replace(pcm_source, path=str(path), duration=2)
    project = Project(media=[source], clips=[
        Clip('retimed', source.id, 2, 0, 0, 40,
             time_map=ClipTimeMap(0, 60, Fraction(3, 2)))])
    project.validate()
    inputs, graph = _legacy_graph(project, {source.id: source})
    expected = _pcm(inputs, graph)
    plan = compile_audio_mix(project, {source.id: source})
    metered = (plan.graph + ';[aout]asplit=2[pcm][meter];'
               '[meter]ebur128=framelog=verbose[measured]')
    actual = _pcm(plan.inputs, metered, 'pcm', meter_output=True)
    assert actual == expected
    assert len(actual) == plan.end_sample * 8 == 64000 * 8

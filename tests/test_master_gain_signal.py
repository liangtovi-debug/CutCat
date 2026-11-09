"""Independent post-mix gain proofs, with lossless PCM and AAC kept separate.

The frozen pre-master mixer in test_audio_mix is deliberately independent of
AudioMixBuilder. Its decoded float PCM is multiplied numerically here, never
by another FFmpeg volume filter or the production gain helper. Sample/channel
identity is asserted before AAC; public AAC exports have bounded-error checks,
not a losslessness claim. Video is decoded and compared independently.
"""
from copy import deepcopy
from media_bins_legacy_fixture import strip_media_bins_fields
from dataclasses import replace
from fractions import Fraction
import hashlib
import os
from pathlib import Path
import re
import subprocess

import numpy as np
import pytest

from lumen import audio_analysis as aa
from lumen.audio_mix import AudioMixBuilder, compile_audio_mix
from lumen.media import ffmpeg_available, ffmpeg_binary, probe
from lumen.model import Caption, Clip, Project
from lumen.render import RenderEngine, RenderError
from lumen.timing import sample_boundary
from lumen.video_transition import VideoTransition

from test_audio_mix import _edited_project, _legacy_graph, _pcm, pcm_source
from test_video_transition_render import fixture as transition_fixture


real = pytest.mark.skipif(not ffmpeg_available(), reason='Local FFmpeg/ffprobe required')


def _samples(raw):
    return np.frombuffer(raw, dtype='<f4').reshape(-1, 2)


def _scale_reference(samples, db):
    # FFmpeg's explicit float volume mode multiplies float input by a float
    # coefficient. Compute that coefficient independently, not from its graph.
    return samples * np.float32(10.0 ** (db / 20.0))


def _assert_scaled(actual, original, db):
    expected = _scale_reference(original, db)
    assert actual.shape == original.shape
    np.testing.assert_allclose(actual, expected, rtol=3e-7, atol=0)
    # A scalar gain cannot move an onset, create a tail, or swap channels.
    np.testing.assert_array_equal(actual == 0, expected == 0)


@pytest.mark.parametrize('gain', [-96.0, -12.375, 6.25, 24.0])
def test_master_filter_occurs_once_after_mix_and_exact_trim(gain):
    project = Project(clips=[Clip('a', 'm', 2, 7, 0, 30)], master_gain_db=gain)
    builder = AudioMixBuilder(project)
    graph = builder.final_fragment(['a0', 'a1'])
    assert graph == (f'[silence][a0][a1]amix=inputs=3:normalize=0:duration=first,'
                     f'atrim=end_sample={builder.end_sample},'
                     f'volume={gain:.12g}dB:precision=float[aout]')
    assert graph.count('volume=') == 1
    assert not any(word in graph for word in ('alimiter', 'loudnorm', 'dynaudnorm', 'compand'))


@pytest.mark.parametrize('gain', [0.0, -0.0])
def test_zero_master_preserves_frozen_terminal_graph_exactly(gain):
    project = Project(clips=[Clip('a', 'm', 2, 7, 0, 30)], master_gain_db=gain)
    builder = AudioMixBuilder(project)
    assert builder.final_fragment(['a0']) == (
        '[silence][a0]amix=inputs=2:normalize=0:duration=first,'
        f'atrim=end_sample={builder.end_sample}[aout]')


@real
@pytest.mark.parametrize('fps', [Fraction(24000, 1001), Fraction(30000, 1001)],
                         ids=['23.976', '29.97'])
@pytest.mark.parametrize('case,offset', [('ordinary', .125), ('retime', -.075),
                                       ('split-move-trim', .3)])
@pytest.mark.parametrize('gain', [-96.0, -12.375, 6.25, 24.0])
def test_numeric_master_multiply_preserves_fractional_edits(pcm_source, fps, case, offset, gain):
    media = replace(pcm_source, audio_offset=offset)
    project = _edited_project(media, fps, case)
    # Exercise a real schema-33 migration, not just a newly created default.
    legacy_document = project.to_dict()
    legacy_document['version'] = 33
    strip_media_bins_fields(legacy_document)
    legacy_document.pop('master_gain_db')
    untouched_legacy = deepcopy(legacy_document)
    project = Project.from_dict(legacy_document)
    assert project.master_gain_db == 0.0 and legacy_document == untouched_legacy
    checked = {media.id: media}
    original = deepcopy(project.to_dict())
    inputs, graph = _legacy_graph(project, checked)
    frozen = _pcm(inputs, graph)
    zero = compile_audio_mix(project, checked)
    assert _pcm(zero.inputs, zero.graph) == frozen

    project.master_gain_db = gain
    mix = compile_audio_mix(project, checked)
    actual = _samples(_pcm(mix.inputs, mix.graph))
    reference = _samples(frozen)
    _assert_scaled(actual, reference, gain)
    assert len(actual) == sample_boundary(project.duration_frames, fps, 48000)
    assert np.max(np.abs(actual[-1000:])) == 0
    assert mix.graph.count(':precision=float') == 1
    project.master_gain_db = 0.0
    assert project.to_dict() == original


@real
def test_attenuation_recovers_overloaded_float_mix_without_preclipping(pcm_source):
    project = Project(media=[pcm_source], clips=[
        Clip('a', pcm_source.id, 2, 0, 0, 60, gain_db=6),
        Clip('b', pcm_source.id, 3, 0, 0, 60, gain_db=6)])
    inputs, graph = _legacy_graph(project, {pcm_source.id: pcm_source})
    original = _samples(_pcm(inputs, graph))
    assert np.max(np.abs(original)) > 3.5
    overloaded = np.abs(original) > 1
    assert overloaded.sum() > 10000

    project.master_gain_db = -18.0
    plan = compile_audio_mix(project, {pcm_source.id: pcm_source})
    actual = _samples(_pcm(plan.inputs, plan.graph))
    _assert_scaled(actual, original, -18.0)
    assert np.max(np.abs(actual)) < .46
    clipped_then_scaled = _scale_reference(np.clip(original, -1, 1), -18.0)
    assert np.max(np.abs(actual[overloaded] - clipped_then_scaled[overloaded])) > .3
    # More than two amplitudes above the hypothetical clipped ceiling prove
    # recovery of the actual waveform, not merely a lowered flat-topped signal.
    assert len(np.unique(actual[overloaded])) > 100


@real
@pytest.mark.parametrize('gain', [-96.0, 24.0])
def test_master_silent_tail_keeps_exact_length_without_source_inputs(pcm_source, gain):
    project = Project(fps_num=30000, fps_den=1001, media=[pcm_source],
                      clips=[Clip('muted', pcm_source.id, 2, 13, 0, 47, muted=True)],
                      master_gain_db=gain)
    mix = compile_audio_mix(project, {pcm_source.id: pcm_source})
    assert mix.inputs == ()
    raw = _pcm(mix.inputs, mix.graph)
    assert len(raw) == sample_boundary(60, Fraction(30000, 1001), 48000) * 8
    assert raw == bytes(len(raw))


@real
@pytest.mark.parametrize('gain', [-96.0, 24.0])
def test_caption_only_master_is_exact_stereo_silence(gain):
    project = Project(fps_num=30000, fps_den=1001,
                      captions=[Caption('caption', 7, 30, 'No audio')], master_gain_db=gain)
    mix = compile_audio_mix(project, {})
    assert mix.inputs == () and mix.source_ids == ()
    raw = _pcm(mix.inputs, mix.graph)
    expected_count = sample_boundary(37, Fraction(30000, 1001), 48000)
    assert len(raw) == expected_count * 8 and raw == bytes(len(raw))
    result = aa.analyze_audio(project)
    assert result.sample_count == expected_count and result.sample_rate == 48000
    assert result.sample_peaks == result.estimated_true_peaks == (0.0, 0.0)
    assert result.integrated_lufs is None and result.loudness_status == 'silence'
    assert result.overload_count == 0


@real
@pytest.mark.parametrize('gain', [0.0, -96.0, 24.0])
def test_empty_timeline_keeps_analysis_preview_and_export_rejection(tmp_path, gain):
    project = Project(master_gain_db=gain)
    with pytest.raises(aa.AudioAnalysisError, match='timeline is empty'):
        aa.analyze_audio(project)
    engine = RenderEngine(timeout=30)
    try:
        with pytest.raises(RenderError, match='timeline is empty'):
            engine._compile(project)
        with pytest.raises(RenderError, match='timeline is empty'):
            engine.preview(project)
        with pytest.raises(RenderError, match='timeline is empty'):
            engine.render(project, tmp_path / 'empty.mp4')
        assert not (tmp_path / 'empty.mp4').exists()
    finally:
        engine.close()


@real
def test_reanalysis_measures_post_master_mix_and_invalidates_old_result(pcm_source):
    project = Project(width=64, height=48, media=[pcm_source], clips=[
        Clip('a', pcm_source.id, 2, 0, 0, 60, gain_db=6),
        Clip('b', pcm_source.id, 3, 0, 0, 60, gain_db=6)])
    source_hash = hashlib.sha256(Path(pcm_source.path).read_bytes()).hexdigest()
    before = aa.analyze_audio(project)
    assert before.overload_count > 10000 and aa.result_is_current(project, before)
    project.master_gain_db = -18.0
    snapshot = deepcopy(project.to_dict())
    assert not aa.result_is_current(project, before)
    after = aa.analyze_audio(project)
    assert aa.result_is_current(project, after)
    assert after.sample_count == before.sample_count == 96000
    assert after.overload_count == 0 and after.first_overload_seconds is None
    assert after.integrated_lufs == pytest.approx(before.integrated_lufs - 18, abs=.11)
    np.testing.assert_allclose(after.sample_peaks, np.array(before.sample_peaks) * 10**(-18/20),
                               rtol=3e-7)
    np.testing.assert_allclose(after.estimated_true_peaks,
                               np.array(before.estimated_true_peaks) * 10**(-18/20), rtol=3e-7)
    assert after.fingerprint != before.fingerprint
    assert project.to_dict() == snapshot
    assert hashlib.sha256(Path(pcm_source.path).read_bytes()).hexdigest() == source_hash


def _run(args):
    return subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', *args],
                          capture_output=True, check=True, timeout=60).stdout


def _decode_audio(path):
    return _samples(_run(['-i', str(path), '-map', '0:a:0', '-vn', '-ar', '48000', '-ac', '2',
                          '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1']))


def _decode_video(path):
    return _run(['-i', str(path), '-map', '0:v:0', '-an', '-pix_fmt', 'rgb48le',
                 '-fps_mode', 'passthrough', '-f', 'rawvideo', 'pipe:1'])


@pytest.fixture
def public_project(tmp_path):
    path = tmp_path / 'av.mkv'
    _run(['-f', 'lavfi', '-i', 'testsrc2=size=64x48:rate=30000/1001:duration=1.2',
          '-f', 'lavfi', '-i',
          'aevalsrc=0.23*sin(2*PI*997*t)|0.13*sin(2*PI*3001*t):s=48000:d=1.2',
          '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'ffv1', '-c:a', 'pcm_f32le', str(path)])
    media = probe(path)
    return Project(width=64, height=48, fps_num=30000, fps_den=1001, media=[media],
                   clips=[Clip('av', media.id, 0, 0, 0, 30)])


@real
@pytest.mark.parametrize('mode', ['preview', 'h264', 'prores'])
@pytest.mark.parametrize('gain', [-9.75, 6.25])
def test_public_export_gain_once_preencoder_aac_bounded_and_video_unchanged(
        tmp_path, public_project, monkeypatch, mode, gain):
    project = public_project
    engine = RenderEngine(timeout=60)
    graphs = []
    compile_original = engine._compile
    def capture(*args, **kwargs):
        result = compile_original(*args, **kwargs)
        graphs.append(result)
        return result
    monkeypatch.setattr(engine, '_compile', capture)
    def render(name):
        if mode == 'preview':
            return engine.preview(project)
        return engine.render(project, tmp_path / (name + ('.mov' if mode == 'prores' else '.mp4')),
                             export_profile='prores_422_hq_10bit' if mode == 'prores' else 'h264_8bit')
    source_hash = hashlib.sha256(Path(project.media[0].path).read_bytes()).hexdigest()
    try:
        first = render('zero')
        baseline_audio = _decode_audio(first)
        baseline_video = _decode_video(first)
        project.master_gain_db = gain
        before = deepcopy(project.to_dict())
        second = render('master')
        assert first != second and len(graphs) == 2
        gained_audio = _decode_audio(second)
        assert _decode_video(second) == baseline_video
        for result in graphs:
            assert result[2] == 30 / (30000 / 1001)
        old_inputs, old_graph, _ = graphs[0]
        new_inputs, new_graph, _ = graphs[1]
        assert new_inputs == old_inputs
        assert new_graph.count(':precision=float') == 1
        assert re.sub(r',volume=[-+0-9.e]+dB:precision=float', '', new_graph) == old_graph

        # Re-run the exact graph handed to the actual public encoder, mapping
        # video to a real null output so FFmpeg schedules both branches to EOF.
        old_pcm = _samples(_pcm(old_inputs, old_graph, video_output=os.devnull))
        new_pcm = _samples(_pcm(new_inputs, new_graph, video_output=os.devnull))
        _assert_scaled(new_pcm, old_pcm, gain)
        assert len(new_pcm) == 48048
        assert gained_audio.shape == baseline_audio.shape
        assert len(gained_audio) >= len(new_pcm)
        body = slice(2048, len(new_pcm) - 2048)
        actual, expected = gained_audio[body], new_pcm[body]
        assert np.isfinite(gained_audio).all()
        assert np.mean(np.abs(actual - expected)) > 1e-6  # AAC is not lossless.
        relative_error = np.sqrt(np.mean((actual - expected)**2, axis=0)) / np.sqrt(
            np.mean(expected**2, axis=0))
        assert np.max(relative_error) < .04
        np.testing.assert_allclose(np.sqrt(np.mean(actual**2, axis=0)),
                                   np.sqrt(np.mean(expected**2, axis=0)), rtol=.015)
        assert project.to_dict() == before
        if mode == 'preview':
            project.master_gain_db = 0.0
            assert engine.preview(project) == first
            assert len(graphs) == 2  # Old gain state is a distinct, reusable cache entry.
    finally:
        engine.close()
    assert hashlib.sha256(Path(project.media[0].path).read_bytes()).hexdigest() == source_hash


@real
@pytest.mark.parametrize('high', [False, True], ids=['h264', 'prores'])
@pytest.mark.parametrize('fps', [Fraction(24000, 1001), Fraction(30000, 1001)],
                         ids=['23.976', '29.97'])
def test_cross_dissolve_keeps_audio_edges_and_gain_matches_frozen_mix(tmp_path, high, fps):
    project, _ = transition_fixture(tmp_path, fps=fps, audio=True)
    project.video_transitions = [VideoTransition('transition', 'left', 'right', 5)]
    project.clips[0].fade_in = 3
    project.clips[1].fade_out = 4
    project.clips.append(Clip('overlap', project.media[0].id, 2, 3, 1, 14, gain_db=-4))
    project.validate()
    inputs, graph = _legacy_graph(project, {media.id: media for media in project.media})
    frozen = _samples(_pcm(inputs, graph))
    project.master_gain_db = -7.375
    engine = RenderEngine(timeout=60)
    try:
        inputs, graph, _ = engine._compile(
            project, export_profile='prores_422_hq_10bit' if high else 'h264_8bit')
        actual = _samples(_pcm(inputs, graph, video_output=os.devnull))
    finally:
        engine.close()
    _assert_scaled(actual, frozen, project.master_gain_db)
    assert len(actual) == sample_boundary(20, fps, 48000)
    assert graph.count(':precision=float') == 1

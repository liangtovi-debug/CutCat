"""Public export checks; pre-encoding measurement is not lossy AAC identity."""
from pathlib import Path
import copy
import hashlib
import subprocess
import wave

import numpy as np
import pytest

from lumen import audio_analysis as aa
from lumen.media import ffmpeg_available, ffmpeg_binary, probe
from lumen.model import Clip, Project
from lumen.render import RenderEngine

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='Local FFmpeg required')


def source(tmp_path, seconds=1):
    n = np.arange(48000 * seconds)
    # Asymmetric, nontrivial PCM makes lossy-encoding differences measurable.
    values = np.column_stack((.43 * np.sin(2*np.pi*997*n/48000),
                              .19 * np.sin(2*np.pi*3001*n/48000)))
    samples = np.rint(values * 32767).astype('<i2')
    p = tmp_path/'stereo.wav'
    with wave.open(str(p), 'wb') as w:
        w.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        w.writeframes(samples.tobytes())
    return p, samples.astype(float)/32768


def decode(path):
    proc = subprocess.run([ffmpeg_binary(), '-v', 'error', '-i', str(path), '-vn',
                           '-map', '0:a:0', '-ac', '2', '-ar', '48000',
                           '-f', 'f32le', 'pipe:1'], check=True, capture_output=True, timeout=30)
    return np.frombuffer(proc.stdout, dtype='<f4').reshape(-1, 2)


@pytest.mark.parametrize('preview', [False, True])
def test_public_h264_aac_export_is_distinct_from_preencoding_measurement(tmp_path, preview):
    p, pcm = source(tmp_path)
    media = probe(p)
    project = Project(width=64, height=48, media=[media],
                      clips=[Clip('stereo', media.id, 2, 0, 0, 30)])
    before = copy.deepcopy(project.to_dict()); original = hashlib.sha256(p.read_bytes()).hexdigest()
    result = aa.analyze_audio(project)
    np.testing.assert_allclose(result.sample_peaks, np.abs(pcm).max(axis=0), atol=1e-7)
    assert result.overload_count == 0 and result.sample_count == 48000
    engine = RenderEngine(timeout=30)
    try:
        output = engine.preview(project) if preview else engine.render(project, tmp_path/'export.mp4')
        decoded = decode(output)
        assert probe(output).has_video and probe(output).has_audio
    finally:
        engine.close()
    assert len(decoded) >= len(pcm)  # AAC packet padding does not define analysis duration.
    assert np.isfinite(decoded).all()
    assert np.mean(np.abs(decoded[:len(pcm)] - pcm)) > 1e-6
    # Encoding remains usable without asserting impossible lossy equality.
    np.testing.assert_allclose(np.sqrt(np.mean(decoded[2048:45000]**2, axis=0)),
                               np.sqrt(np.mean(pcm[2048:45000]**2, axis=0)), atol=.015)
    assert project.to_dict() == before and hashlib.sha256(p.read_bytes()).hexdigest() == original


def test_analysis_never_invokes_video_rendering_for_real_av_source(tmp_path, monkeypatch):
    p, _ = source(tmp_path)
    movie = tmp_path/'av.mkv'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-f', 'lavfi', '-i',
                    'color=red:s=64x48:r=30:d=1', '-i', str(p), '-map', '0:v:0',
                    '-map', '1:a:0', '-c:v', 'ffv1', '-c:a', 'pcm_s16le', str(movie)],
                   check=True, capture_output=True, timeout=30)
    m = probe(movie)
    project = Project(width=64, height=48, media=[m], clips=[Clip('av', m.id, 0, 0, 0, 30)])
    project.clips[0].grade.exposure = 2
    project.clips[0].composite.opacity = .5
    def no_video(*args, **kwargs):
        pytest.fail('Audio analysis invoked video processing')
    for name in ('_compile', '_graded_segment', '_overlay_segment', 'render', 'preview', 'frame'):
        monkeypatch.setattr(RenderEngine, name, no_video)
    import lumen.video_transition_render as transitions
    import lumen.clip_composite as composite
    import lumen.highbit_captions as captions
    monkeypatch.setattr(transitions, 'prepare_video_transitions', no_video)
    monkeypatch.setattr(composite, 'verify_clip_composite_support', no_video)
    monkeypatch.setattr(captions, 'highbit_caption_filters', no_video)
    commands = []
    graphs = []
    original = subprocess.Popen
    def capture(command, *args, **kwargs):
        commands.append(command)
        if '-/filter_complex' in command:
            graphs.append(Path(command[command.index('-/filter_complex') + 1]).read_text())
        return original(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', capture)
    result = aa.analyze_audio(project)
    assert result.sample_count == 48000 and result.integrated_lufs is not None
    decodes = [command for command in commands if '-/filter_complex' in command]
    assert decodes and len(graphs) == len(decodes)
    for command, graph in zip(decodes, graphs):
        assert ':V:' not in graph and ':v:' not in graph
        assert not any(token in graph for token in ('overlay', 'zscale', 'scale=', 'drawtext', 'color='))
        assert '-vn' in command


def test_manual_gain_adjust_reanalysis_changes_metrics_not_other_settings(tmp_path):
    p, _ = source(tmp_path)
    m = probe(p)
    project = Project(width=64, height=48, media=[m], clips=[
        Clip('a', m.id, 2, 0, 0, 30, gain_db=6),
        Clip('b', m.id, 3, 0, 0, 30, gain_db=6)])
    first = aa.analyze_audio(project)
    assert first.overload_count > 0
    assert first.first_overload_seconds is not None
    assert aa.result_is_current(project, first)
    for clip in project.clips:
        clip.gain_db = -6
    assert not aa.result_is_current(project, first)
    before = copy.deepcopy(project.to_dict())
    second = aa.analyze_audio(project)
    assert second.overload_count == 0 and second.first_overload_seconds is None
    assert second.integrated_lufs == pytest.approx(first.integrated_lufs - 12, abs=.11)
    np.testing.assert_allclose(np.array(second.sample_peaks)/first.sample_peaks, 10**(-12/20), rtol=2e-6)
    assert second.fingerprint != first.fingerprint and project.to_dict() == before


def test_real_opposite_phase_tracks_cancel_after_mix_not_before_channels(tmp_path):
    p, _ = source(tmp_path)
    with wave.open(str(p), 'rb') as w:
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2')
    inverse = tmp_path/'inverse.wav'
    with wave.open(str(inverse), 'wb') as w:
        w.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        w.writeframes((-raw).astype('<i2').tobytes())
    a, b = probe(p), probe(inverse)
    project = Project(width=64, height=48, media=[a, b], clips=[
        Clip('a', a.id, 2, 0, 0, 30), Clip('b', b.id, 3, 0, 0, 30)])
    result = aa.analyze_audio(project)
    assert result.sample_peaks == (0., 0.)
    assert result.integrated_lufs is None and result.loudness_status == 'silence'
    assert result.overload_count == 0


@pytest.mark.parametrize('bad', [float('nan'), float('inf')])
def test_actual_nonfinite_float_source_never_publishes_measurement(tmp_path, bad):
    raw = np.zeros((48000, 2), dtype='<f4'); raw[12000, 0] = bad
    path = tmp_path/'nonfinite.wav'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-f', 'f32le', '-ar', '48000',
                    '-ac', '2', '-i', 'pipe:0', '-c:a', 'pcm_f32le', str(path)],
                   input=raw.tobytes(), capture_output=True, check=True, timeout=30)
    media = probe(path)
    project = Project(width=64, height=48, media=[media],
                      clips=[Clip('bad', media.id, 2, 0, 0, 30)])
    with pytest.raises(aa.AudioAnalysisError):
        aa.analyze_audio(project)

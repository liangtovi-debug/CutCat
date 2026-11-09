"""Synthetic PCM tests for generated-audio integration, not TTS quality claims."""
from dataclasses import replace
import hashlib
from pathlib import Path
import subprocess
import threading
import wave

import numpy as np
import pytest

from lumen.media import ffmpeg_available, probe
from lumen.model import Clip, Project, SpeechProvenance
from lumen.render import RenderCancelled, RenderEngine, RenderError

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


@pytest.fixture(params=[24000, 48000])
def speech_project(tmp_path, request):
    source = tmp_path / 'generated.wav'
    rate = request.param
    samples = np.zeros(round(1.013 * rate), dtype='<i2')
    # Only the last 10 ms contains audio: flooring to 30 frames loses it all.
    start = round(1.003 * rate)
    samples[start:] = (16000 * np.sin(2 * np.pi * 1000 * np.arange(len(samples) - start) / rate)).astype('<i2')
    with wave.open(str(source), 'wb') as wav:
        wav.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(samples.tobytes())
    provenance = SpeechProvenance(
        text='Original synthesis text', engine='synthetic-test', engine_version='1',
        model_sha256='a' * 64, voice_sha256='b' * 64, assets_sha256='c' * 64,
        voice_id='test', speed=1.0, sample_rate=rate, created_utc='2026-10-14T00:00:00Z',
        source_caption_id='caption-original', source_start=0, source_duration=20,
        output_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    media = replace(probe(source), speech_provenance=provenance)
    project = Project(width=64, height=48, media=[media])
    project.clips = [Clip('voice', media.id, 2, 0, 0, project.media_frames(media))]
    return project


@pytest.fixture
def engine():
    result = RenderEngine(timeout=30)
    yield result
    result.close()


def decoded_audio(path):
    result = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-vn', '-ac', '1',
                             '-ar', '48000', '-f', 'f32le', 'pipe:1'], capture_output=True, check=True)
    return np.frombuffer(result.stdout, dtype='<f4')


@pytest.mark.parametrize('preview', [False, True])
def test_non_frame_aligned_ending_survives(speech_project, tmp_path, engine, preview):
    project = speech_project
    assert project.media[0].duration == pytest.approx(1.013)
    assert project.clips[0].duration == 31
    assert 0 < project.duration_frames / 30 - project.media[0].duration < 1 / 30
    output = engine.preview(project) if preview else engine.render(project, tmp_path / 'export.mp4')
    samples = decoded_audio(output)
    # AAC is lossy; measure the real ending rather than byte-identical samples.
    ending = samples[round(1.005 * 48000):round(1.011 * 48000)]
    assert np.sqrt(np.mean(ending ** 2)) > .1
    assert np.sqrt(np.mean(samples[4800:24000] ** 2)) < .001
    assert probe(output).duration == pytest.approx(31 / 30, abs=.002)
    assert Path(project.media[0].path).is_file()


@pytest.mark.parametrize('preview', [False, True])
def test_tampered_speech_rejected_and_original_restored(speech_project, tmp_path, engine, preview):
    project = speech_project
    source = Path(project.media[0].path)
    original = source.read_bytes()
    if preview:
        engine.preview(project)  # Establish an existing cached result.
    changed = bytearray(original)
    changed[-20] ^= 32  # A valid WAV of the same size and duration, different samples.
    source.write_bytes(changed)
    with pytest.raises(RenderError, match='Generated speech source has changed'):
        engine.preview(project) if preview else engine.render(project, tmp_path / 'changed.mp4')
    assert not (tmp_path / 'changed.mp4').exists()
    assert not list(tmp_path.glob('.lumen-render-*'))
    assert source.read_bytes() == bytes(changed)
    source.write_bytes(original)
    result = engine.preview(project) if preview else engine.render(project, tmp_path / 'restored.mp4')
    assert result.is_file()
    assert source.read_bytes() == original


def test_reopened_provenance_and_audio(speech_project, tmp_path, engine):
    project = speech_project
    path = tmp_path / 'speech.lumen'
    project.save(path)
    restored = Project.load(path)
    assert restored.media[0].speech_provenance == project.media[0].speech_provenance
    assert restored.clips[0].duration == 31
    samples = decoded_audio(engine.render(restored, tmp_path / 'reopened.mp4'))
    assert np.max(np.abs(samples[48000:48624])) > .2


def test_hash_once_per_unique_source(speech_project, engine, monkeypatch):
    project = speech_project
    media = project.media[0]
    duplicate = replace(media, id='duplicate')
    project.media.append(duplicate)
    project.clips.append(Clip('second', duplicate.id, 3, 0, 0, 31))
    original_open = Path.open
    reads = []
    def tracked_open(path, *args, **kwargs):
        if path == Path(media.path) and args == ('rb',):
            reads.append(path)
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', tracked_open)
    engine._compile(project)
    assert len(reads) == 1


def test_generated_source_and_cancel_safety(speech_project, tmp_path, engine):
    project = speech_project
    source = Path(project.media[0].path)
    original = source.read_bytes()
    with pytest.raises(RenderError):
        engine.render(project, source)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RenderCancelled):
        engine.render(project, tmp_path / 'cancel.mp4', cancel=cancel)
    assert source.read_bytes() == original
    assert not (tmp_path / 'cancel.mp4').exists()
    assert not list(tmp_path.glob('.lumen-render-*'))


def test_cached_preview_still_checks_audio_bytes(speech_project, engine, monkeypatch):
    project = speech_project
    source = Path(project.media[0].path)
    cached = engine.preview(project)
    old_stat = source.stat()
    changed = bytearray(source.read_bytes())
    changed[-20] ^= 32
    source.write_bytes(changed)
    original_stat = Path.stat
    # Simulate unchanged filesystem metadata. Content identity must not rely on
    # size/timestamps, and even a cache hit must refuse changed generated audio.
    def frozen_source_stat(path, *args, **kwargs):
        if path == source:
            return old_stat
        return original_stat(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', frozen_source_stat)
    assert cached.exists()
    with pytest.raises(RenderError, match='Generated speech source has changed'):
        engine.preview(project)

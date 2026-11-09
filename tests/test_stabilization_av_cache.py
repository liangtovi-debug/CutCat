"""Actual muxed A/V and cache/cancellation acceptance for picture stabilization."""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
from pathlib import Path
import shutil
import subprocess
import threading
import wave

import numpy as np
import pytest

from lumen import stabilization_analysis as analysis
from lumen.media import ffmpeg_available, ffmpeg_binary, probe
from lumen.model import Clip, Project
from lumen.render import RenderCancelled, RenderEngine, RenderError
from lumen.stabilization_settings import StabilizationSettings
from lumen.timing import ClipTimeMap, duration_frames, sample_boundary

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='External FFmpeg required')


@pytest.fixture(scope='module', autouse=True)
def required_filters():
    result = subprocess.run([ffmpeg_binary(), '-hide_banner', '-filters'], capture_output=True, text=True, timeout=30)
    if any(name not in result.stdout for name in ('vidstabdetect', 'vidstabtransform', 'rubberband', 'zscale')):
        pytest.skip('External FFmpeg needs libvidstab, rubberband, zscale')


def _source(tmp_path, fps=Fraction(30), *, marker=True):
    rng = np.random.default_rng(421)
    base = np.repeat(np.repeat(rng.integers(0, 256, (20, 32, 3), dtype=np.uint8), 12, 0), 10, 1)
    for _ in range(2):
        base = ((base.astype(np.uint16)+np.roll(base, 1, 0)+np.roll(base, 1, 1))//3).astype(np.uint8)
    frames = [np.roll(base, (round(4*np.cos(i*.9)), round(5*np.sin(i*1.2))), (0, 1)) for i in range(60)]
    if marker:
        frames[24] = np.full_like(base, 255)
    picture = tmp_path / 'picture.mkv'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                    '-s', '320x240', '-r', f'{fps.numerator}/{fps.denominator}', '-i', 'pipe:0',
                    '-c:v', 'ffv1', '-threads', '1', str(picture)],
                   input=b''.join(frame.tobytes() for frame in frames), capture_output=True, check=True, timeout=30)
    offset = Fraction(1, 8)
    samples = np.zeros(round(float(Fraction(60)/fps-offset)*48000), np.float64)
    center = round(float(Fraction(24)/fps-offset)*48000)
    samples[center-480:center+480] = .6*np.sin(2*np.pi*1000*np.arange(960)/48000)
    sound = tmp_path / 'sound.wav'
    with wave.open(str(sound), 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(48000)
        stream.writeframes(np.rint(samples*32767).astype('<i2').tobytes())
    source = tmp_path / 'source.mkv'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-y', '-i', str(picture),
                    '-itsoffset', str(float(offset)), '-i', str(sound), '-map', '0:v', '-map', '1:a',
                    '-c', 'copy', str(source)], capture_output=True, check=True, timeout=30)
    return source


def _project(source, *, fps=Fraction(30), speed=Fraction(1), origin=Fraction(0), enabled=True):
    media = probe(source)
    mapping = ClipTimeMap(origin, Fraction(54)+origin, speed)
    clip = Clip('clip', media.id, 0, 3, origin, duration_frames(mapping), time_map=mapping,
                stabilization=StabilizationSettings(enabled=enabled, smoothing=5, border='crop'))
    return Project(width=320, height=240, fps_num=fps.numerator, fps_den=fps.denominator,
                   media=[media], clips=[clip])


def _audio(path, *, encoded=False):
    args = [ffmpeg_binary(), '-v', 'error', '-i', str(path), '-map', '0:a:0', '-vn']
    args += ['-c:a', 'copy', '-f', 'adts', 'pipe:1'] if encoded else ['-ac', '1', '-ar', '48000', '-f', 'f32le', 'pipe:1']
    return subprocess.run(args, capture_output=True, check=True, timeout=30).stdout


def _pictures(path):
    raw = subprocess.run([ffmpeg_binary(), '-v', 'error', '-i', str(path), '-an',
                          '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'],
                         capture_output=True, check=True, timeout=30).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 240, 320, 3)


@pytest.mark.parametrize('fps,speed,origin', [
    (Fraction(30), Fraction(1), Fraction(0)),
    (Fraction(30), Fraction(3, 2), Fraction(1, 2)),
    (Fraction(30000, 1001), Fraction(3, 2), Fraction(1, 2)),
])
def test_real_stabilization_preserves_encoded_audio_samples_offset_and_markers(tmp_path, fps, speed, origin):
    source = _source(tmp_path, fps)
    before = source.read_bytes()
    enabled = _project(source, fps=fps, speed=speed, origin=origin)
    disabled = deepcopy(enabled); disabled.clips[0].stabilization.enabled = False
    assert abs(enabled.media[0].audio_offset-.125) < .002
    engine = RenderEngine(timeout=90)
    try:
        original = engine.render(disabled, tmp_path / 'disabled.mp4')
        stabilized = engine.render(enabled, tmp_path / 'enabled.mp4')
        # Picture stabilization must not touch any encoded audio payload.
        assert _audio(stabilized, encoded=True) == _audio(original, encoded=True)
        a, b = _audio(stabilized), _audio(original)
        assert a == b
        samples = np.frombuffer(a, '<f4')
        budget = sample_boundary(enabled.duration_frames, fps, 48000)
        assert budget <= len(samples) < budget + 1024  # AAC packet padding only.
        pictures = _pictures(stabilized)
        assert len(pictures) == enabled.duration_frames
        bright = np.flatnonzero(pictures[:, 80:160, 100:220].mean(axis=(1, 2, 3)) > 225)
        selected = [(origin + k*speed).__floor__() for k in range(enabled.clips[0].duration)]
        expected_frames = [enabled.clips[0].start+k for k, frame in enumerate(selected) if frame == 24]
        assert bright.tolist() == expected_frames
        event = float((Fraction(enabled.clips[0].start) + (24-origin)/speed)/fps)
        lo, hi = max(0, round((event-.12)*48000)), min(len(samples), round((event+.12)*48000))
        energy = samples[lo:hi].astype(np.float64)**2
        assert energy.sum() > .1
        centroid = float(np.dot(np.arange(lo, hi)/48000, energy)/energy.sum())
        assert abs(centroid-event) < 1/float(fps)
        assert abs(centroid-expected_frames[0]/float(fps)) < 1/float(fps)
        assert source.read_bytes() == before
    finally:
        engine.close()


def test_preview_cache_rechecks_content_and_rebuilds_owned_snapshot(tmp_path, monkeypatch):
    source = _source(tmp_path, marker=False)
    project = _project(source)
    engine = RenderEngine(timeout=90)
    try:
        first = engine.preview(project)
        second = engine.preview(project)
        assert first == second
        caches = list((Path(engine._cache_dir.name) / 'stabilization-analysis').glob('*.source.media'))
        assert len(caches) == 1
        caches[0].write_bytes(b'corrupt snapshot')
        # Analysis validation repairs the owned snapshot even on a picture cache hit.
        assert engine.preview(project) == first
        assert caches[0].read_bytes() == source.read_bytes()
        state = source.stat()
        with source.open('ab') as stream: stream.write(b'\0')
        import os
        os.utime(source, ns=(state.st_atime_ns, state.st_mtime_ns))
        third = engine.preview(project)
        assert third != first and third.exists()
        assert len(list((Path(engine._cache_dir.name) / 'stabilization-analysis').glob('*.npz'))) == 2
    finally:
        engine.close()


def test_analysis_cancel_during_pass_then_same_engine_recovers(tmp_path, monkeypatch):
    source = _source(tmp_path, marker=False)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    project = _project(source)
    engine = RenderEngine(timeout=90)
    event = threading.Event()
    actual = analysis._two_passes
    def cancel_at_motion(*args, **kwargs):
        event.set()
        return actual(*args, **kwargs)
    monkeypatch.setattr(analysis, '_two_passes', cancel_at_motion)
    try:
        with pytest.raises(RenderCancelled):
            engine.render(project, tmp_path / 'cancelled.mp4', cancel=event)
        assert not (tmp_path / 'cancelled.mp4').exists()
        assert not list(Path(engine._cache_dir.name).rglob('*.partial*'))
        assert not list(Path(engine._cache_dir.name).rglob('*.npz'))
        monkeypatch.setattr(analysis, '_two_passes', actual)
        event.clear()
        result = engine.render(project, tmp_path / 'recovered.mp4', cancel=event)
        assert len(_pictures(result)) == project.duration_frames
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    finally:
        engine.close()


def test_external_motion_failure_then_same_engine_recovers(tmp_path, monkeypatch):
    source = _source(tmp_path, marker=False)
    project = _project(source)
    engine = RenderEngine(timeout=90)
    actual = analysis._two_passes
    def fail(*args, **kwargs):
        raise analysis.StabilizationAnalysisError('Injected external motion decoder failure')
    monkeypatch.setattr(analysis, '_two_passes', fail)
    try:
        with pytest.raises(RenderError, match='motion decoder failure'):
            engine.render(project, tmp_path / 'failed.mp4')
        assert not (tmp_path / 'failed.mp4').exists()
        assert not list(Path(engine._cache_dir.name).rglob('*.npz'))
        monkeypatch.setattr(analysis, '_two_passes', actual)
        result = engine.render(project, tmp_path / 'recovered.mp4')
        assert result.exists()
    finally:
        engine.close()

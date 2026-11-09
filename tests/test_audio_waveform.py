from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import wave

import numpy as np
import pytest

from lumen import audio_waveform as aw
from lumen.audio_timing import SAMPLE_RATE, compile_audio_timing
from lumen.model import Clip
from lumen.timing import ClipTimeMap, duration_frames


def clip(source_in=0, source_out=30, speed=1, start=0):
    mapping = ClipTimeMap(Fraction(source_in), Fraction(source_out), Fraction(speed))
    return Clip('clip', 'media', 2, start, mapping.source_in, duration_frames(mapping), time_map=mapping)


def pcm_peaks(samples, block_size=1, **kwargs):
    return aw.peaks_from_samples(samples, block_size=block_size, **kwargs)


def write_wav(path, samples):
    values = np.asarray(samples)
    channels = 1 if values.ndim == 1 else values.shape[1]
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(SAMPLE_RATE)
        stream.writeframes(np.rint(values * 32767).astype('<i2').tobytes())
    return path


@pytest.fixture
def ffmpeg():
    if not shutil.which(aw.ffmpeg_binary()) or not shutil.which(aw.ffprobe_binary()):
        pytest.skip('Local FFmpeg/ffprobe unavailable')


def test_peak_silence_impulses_antiphase_and_readonly():
    pcm = np.zeros((1000, 2), dtype=np.float32)
    pcm[130] = [.7, -.7]
    pcm[700] = [.2, -.9]
    peaks = aw.peaks_from_samples(pcm)
    assert peaks.sample_count == 1000
    assert peaks.levels[0].tolist() == pytest.approx([0, .7, 0, 0, 0, .9, 0, 0])
    assert peaks.levels[-1].tolist() == pytest.approx([.9])
    assert aw.peaks_from_samples(np.zeros(1024)).levels[-1].tolist() == [0]
    with pytest.raises(ValueError):
        peaks.levels[0].setflags(write=True)


def test_nonfinite_and_overrange_samples_are_safe():
    peaks = pcm_peaks(np.array([np.nan, np.inf, -np.inf, -2, 0]))
    assert peaks.levels[0].tolist() == [0, 1, 1, 1, 0]


def test_streaming_builder_compacts_without_losing_impulses():
    rng = np.random.default_rng(17)
    pcm = rng.uniform(-1, 1, (1013, 2)).astype(np.float32)
    builder = aw._PeakBuilder(block_size=2, max_bins=16)
    for a, b in zip([0, 1, 14, 18, 61, 188, 1000], [1, 14, 18, 61, 188, 1000, 1013]):
        builder.feed(pcm[a:b])
    peaks = builder.finish()
    expected = [np.abs(pcm[i:i+peaks.block_size]).max()
                for i in range(0, len(pcm), peaks.block_size)]
    assert peaks.sample_count == len(pcm)
    assert np.allclose(peaks.levels[0], expected)
    assert len(peaks.levels[0]) <= 16
    assert peaks.nbytes < 16 * 4 * 2
    assert peaks.levels[-1][0] == np.abs(pcm).max()


def test_empty_envelope_maps_to_silence():
    peaks = pcm_peaks(np.array([]))
    assert peaks.nbytes == 0
    assert aw.map_peaks(peaks, clip(), Fraction(30), pixels=10).tolist() == [0] * 10


def test_map_trim_speed_gain_and_fades():
    pcm = np.zeros(SAMPLE_RATE * 2)
    pcm[SAMPLE_RATE // 2:SAMPLE_RATE] = .75
    pcm[SAMPLE_RATE:SAMPLE_RATE+SAMPLE_RATE//4] = .25
    peaks = pcm_peaks(pcm)
    selected = clip(15, 45, 2)
    expected = [.75, .75, .25, 0]
    assert aw.map_peaks(peaks, selected, Fraction(30), pixels=4).tolist() == expected
    selected.gain_db = -40
    selected.muted = True
    selected.fade_in = 10
    selected.fade_out = 10
    assert aw.map_peaks(peaks, selected, Fraction(30), pixels=4).tolist() == expected


@pytest.mark.parametrize('speed', [Fraction(1, 4), Fraction(2, 3), Fraction(1), Fraction(3, 2), Fraction(4)])
def test_map_fractional_rate_global_leading_offset(speed):
    fps = Fraction(30000, 1001)
    selected = clip(0, 6, speed, start=7)
    offset = Fraction(13, 1000)
    plan = compile_audio_timing(selected, fps, offset)
    pcm = np.ones(SAMPLE_RATE, dtype=np.float32)
    peaks = pcm_peaks(pcm)
    leading_edge = (Fraction(plan.start_sample + plan.leading_samples, SAMPLE_RATE) * fps - selected.start)
    # A window ending on the exact global onset contains only leading silence.
    assert not aw.map_peaks(peaks, selected, fps, offset, 0, leading_edge, 20).any()
    after = aw.map_peaks(peaks, selected, fps, offset, leading_edge,
                         leading_edge + fps / SAMPLE_RATE * 8, 4)
    assert np.all(after == 1)


def test_negative_audio_offset_selects_later_audio_samples():
    pcm = np.zeros(SAMPLE_RATE * 2)
    pcm[SAMPLE_RATE:SAMPLE_RATE*3//2] = .5
    peaks = pcm_peaks(pcm)
    assert aw.map_peaks(peaks, clip(0, 30), Fraction(30), -1, pixels=2).tolist() == [.5, 0]


def test_short_audio_eof_never_repeats_tail():
    peaks = pcm_peaks(np.ones(SAMPLE_RATE // 2))
    assert aw.map_peaks(peaks, clip(0, 30), Fraction(30), pixels=4).tolist() == [1, 1, 0, 0]


def test_final_rounded_picture_frame_padding_is_silent():
    # One project frame at 1.5x lasts 2/3 output frames, rounded up to one.
    selected = clip(0, 1, Fraction(3, 2))
    peaks = pcm_peaks(np.ones(SAMPLE_RATE))
    assert selected.duration == 1
    assert aw.map_peaks(peaks, selected, Fraction(30), pixels=3).tolist() == [1, 1, 0]


def test_exclusive_source_endpoint_does_not_leak_adjacent_impulse():
    pcm = np.zeros(SAMPLE_RATE)
    pcm[1600] = 1
    selected = clip(0, 1)
    assert not aw.map_peaks(pcm_peaks(pcm), selected, Fraction(30), pixels=20).any()
    # With coarser peaks, a base bin crossing the trim is conservatively omitted.
    assert not aw.map_peaks(aw.peaks_from_samples(pcm), selected, Fraction(30), pixels=20).any()


def test_source_start_does_not_leak_earlier_impulse():
    pcm = np.zeros(SAMPLE_RATE)
    pcm[1599] = 1
    assert not aw.map_peaks(aw.peaks_from_samples(pcm), clip(1, 2), Fraction(30), pixels=10).any()


def test_map_subframe_window_and_outside_clip_are_bounded():
    pcm = np.zeros(SAMPLE_RATE)
    pcm[400:800] = .5
    peaks = pcm_peaks(pcm)
    assert aw.map_peaks(peaks, clip(), Fraction(30), 0, Fraction(1, 4), Fraction(1, 2), 2).tolist() == [.5, .5]
    assert aw.map_peaks(peaks, clip(), Fraction(30), 0, -30, 60, 3).tolist() == [0, .5, 0]


def test_range_maxima_matches_naive_base_bin_query():
    rng = np.random.default_rng(8)
    pcm = rng.uniform(0, 1, 1937).astype(np.float32)
    peaks = pcm_peaks(pcm)
    left = rng.integers(0, len(pcm), 1500)
    right = np.array([rng.integers(a, len(pcm) + 1) for a in left])
    expected = np.array([max(pcm[a:b], default=0) for a, b in zip(left, right)])
    assert np.array_equal(aw._range_maxima(peaks, left, right), expected)


def test_lru_memory_entries_replacement_remove_and_clear():
    small = pcm_peaks(np.ones(8))
    cache = aw.PeakCache(max_bytes=small.nbytes*2, max_entries=2)
    assert cache.put('a', small) and cache.put('b', small)
    assert cache.get('a') is small
    assert cache.put('c', small)
    assert cache.keys() == ('a', 'c')
    assert cache.get('b') is None
    assert cache.nbytes == 2 * small.nbytes
    assert cache.put('a', small)
    assert cache.keys() == ('c', 'a')
    huge = pcm_peaks(np.ones(1000))
    assert not cache.put('huge', huge)
    assert len(cache) == 2
    cache.remove('a')
    cache.remove('missing')
    assert len(cache) == 1 and cache.nbytes == small.nbytes
    cache.clear()
    assert len(cache) == 0 and cache.nbytes == 0


def test_identity_detects_same_path_replacement_and_cache_rejects_stale(tmp_path):
    path = tmp_path / 'audio.wav'
    path.write_bytes(b'first')
    first = aw.identity(path)
    replacement = tmp_path / 'next.wav'
    replacement.write_bytes(b'other')
    replacement.replace(path)
    second = aw.identity(path)
    assert first != second
    peaks = pcm_peaks([1], file_identity=first)
    cache = aw.PeakCache()
    assert cache.put(first, peaks)
    assert not cache.put(second, peaks)
    assert cache.get(second) is None


def test_identity_rejects_missing_and_directory(tmp_path):
    for path in (tmp_path / 'missing', tmp_path):
        with pytest.raises(aw.WaveformError):
            aw.identity(path)


def test_real_decoder_silence_and_antiphase(ffmpeg, tmp_path):
    pcm = np.zeros((4800, 2))
    pcm[1000:1100] = [.75, -.75]
    path = write_wav(tmp_path / 'phase.wav', pcm)
    peaks = aw.extract_peaks(path)
    assert peaks.identity == aw.identity(path)
    assert peaks.sample_count == len(pcm)
    assert float(peaks.levels[-1][0]) == pytest.approx(.75, abs=.0001)
    assert peaks.levels[0][0] == 0
    silent = write_wav(tmp_path / 'silence.wav', np.zeros(1000))
    assert aw.extract_peaks(silent).levels[-1][0] == 0


def test_real_decoder_selects_first_audio_stream(ffmpeg, tmp_path):
    first = write_wav(tmp_path / 'first.wav', np.zeros(4800))
    second = write_wav(tmp_path / 'second.wav', np.ones(4800)*.8)
    combined = tmp_path / 'two.mkv'
    subprocess.run([aw.ffmpeg_binary(), '-v', 'error', '-nostdin', '-i', str(first),
                    '-i', str(second), '-map', '0:a:0', '-map', '1:a:0', '-c:a', 'pcm_s16le',
                    str(combined)], check=True, capture_output=True, timeout=10)
    assert aw.extract_peaks(combined).levels[-1][0] == 0


def test_real_extraction_limits_and_no_partial_success(ffmpeg, tmp_path):
    path = write_wav(tmp_path / 'long.wav', np.ones(4800)*.5)
    with pytest.raises(aw.WaveformError, match='duration limit'):
        aw.extract_peaks(path, limits=aw.ExtractionLimits(max_samples=100))
    with pytest.raises(aw.WaveformError, match='output limit'):
        aw.extract_peaks(path, limits=aw.ExtractionLimits(max_pcm_bytes=100))


def test_pre_cancel_does_not_spawn_decoder(monkeypatch, tmp_path):
    event = threading.Event()
    event.set()
    def forbidden(*args, **kwargs):
        pytest.fail('Pre-cancelled extraction must not start a process')
    monkeypatch.setattr(aw.subprocess, 'Popen', forbidden)
    with pytest.raises(aw.WaveformCancelled):
        aw.extract_peaks(tmp_path / 'missing', event)


@pytest.mark.parametrize('cancelled', [True, False], ids=['cancel', 'timeout'])
def test_stalled_pipe_is_killed_and_reaped(monkeypatch, cancelled):
    original = subprocess.Popen
    processes = []
    def tracked(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(aw.subprocess, 'Popen', tracked)
    event = threading.Event()
    timer = threading.Timer(.15, event.set) if cancelled else None
    if timer:
        timer.start()
    began = time.monotonic()
    error = aw.WaveformCancelled if cancelled else aw.WaveformError
    try:
        with pytest.raises(error, match='cancelled' if cancelled else 'timed out'):
            list(aw._stream_process([sys.executable, '-c', 'import time; time.sleep(30)'],
                                    aw._Budget(10 if cancelled else .15, event), 1024))
    finally:
        if timer:
            timer.cancel()
    assert time.monotonic() - began < 3
    assert len(processes) == 1 and processes[0].poll() is not None
    assert processes[0].stdout.closed


def test_flooding_child_is_output_bounded_and_reaped(monkeypatch):
    original = subprocess.Popen
    processes = []
    def tracked(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(aw.subprocess, 'Popen', tracked)
    with pytest.raises(aw.WaveformError, match='output limit'):
        list(aw._stream_process([sys.executable, '-c',
                                'import os\nwhile True: os.write(1, b"x"*65536)'],
                               aw._Budget(5, None), 100))
    assert processes[0].poll() is not None


def test_source_changed_while_decoding_is_rejected(monkeypatch, tmp_path):
    path = tmp_path / 'source.wav'
    path.write_bytes(b'old')
    def fake_stream(command, budget, limit):
        if '-show_entries' in command:
            yield json.dumps({'streams': [{'channels': 1}]}).encode()
        else:
            yield np.ones(1000, dtype='<f4').tobytes()
            path.write_bytes(b'new source')
    monkeypatch.setattr(aw, '_stream_process', fake_stream)
    with pytest.raises(aw.WaveformError, match='changed'):
        aw.extract_peaks(path)


def test_malformed_partial_pcm_is_rejected(monkeypatch, tmp_path):
    path = tmp_path / 'source.wav'
    path.write_bytes(b'a')
    def fake_stream(command, budget, limit):
        if '-show_entries' in command:
            yield b'{"streams":[{"channels":2}]}'
        else:
            yield b'12345'
    monkeypatch.setattr(aw, '_stream_process', fake_stream)
    with pytest.raises(aw.WaveformError, match='incomplete'):
        aw.extract_peaks(path)


@pytest.mark.parametrize('value', [True, -1, 16385, 1.5])
def test_display_width_validation(value):
    with pytest.raises(ValueError):
        aw.map_peaks(pcm_peaks([0]), clip(), Fraction(30), pixels=value)


@pytest.mark.parametrize('changes', [{'timeout': float('inf')}, {'timeout': 0}, {'max_bins': 3},
                                    {'max_channels': 33}, {'block_size': 0}])
def test_extraction_limit_validation(changes):
    with pytest.raises(ValueError):
        aw.ExtractionLimits(**changes)


def test_missing_decoder_has_no_partial_cacheable_result(ffmpeg, monkeypatch, tmp_path):
    path = write_wav(tmp_path / 'audio.wav', np.zeros(1000))
    original = path.read_bytes()
    monkeypatch.setattr(aw, 'ffmpeg_binary', lambda: str(tmp_path / 'missing-ffmpeg'))
    with pytest.raises(aw.WaveformError, match='Cannot run'):
        aw.extract_peaks(path)
    assert path.read_bytes() == original


def test_unsupported_media_fails_without_touching_source(ffmpeg, tmp_path):
    path = tmp_path / 'unsupported.bin'
    path.write_bytes(b'not audio or video')
    before = aw.identity(path)
    with pytest.raises(aw.WaveformError):
        aw.extract_peaks(path)
    assert aw.identity(path) == before


def test_decoding_command_remains_local_and_channel_preserving(monkeypatch, tmp_path):
    path = tmp_path / 'source.wav'
    path.write_bytes(b'fixture')
    commands = []
    def fake_stream(command, budget, limit):
        commands.append(command)
        if '-show_entries' in command:
            yield b'{"streams":[{"channels":6}]}'
        else:
            yield np.array([[.1, -.1, .9, -.9, .2, -.2]], dtype='<f4').tobytes()
    monkeypatch.setattr(aw, '_stream_process', fake_stream)
    peaks = aw.extract_peaks(path)
    assert peaks.levels[-1][0] == pytest.approx(.9)
    assert len(commands) == 2
    assert all(c[c.index('-protocol_whitelist')+1] == 'file,pipe' for c in commands)
    assert commands[0][commands[0].index('-select_streams')+1] == 'a:0'
    assert commands[1][commands[1].index('-map')+1] == '0:a:0'
    assert commands[1][commands[1].index('-ac')+1] == '6'
    assert commands[1][-1] == 'pipe:1'

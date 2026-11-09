"""Bounded original-speed extraction keeps rational source timestamp origins."""
from fractions import Fraction
import json
import math
from pathlib import Path
import struct
import wave

import pytest
from lumen import asr
from lumen.asr import ASRConfig, ASRError, transcribe
from lumen.media import ffmpeg_available


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path / 'source.wav'; source.write_bytes(b'fixture')
    runtime = tmp_path / 'whisper-cli'; runtime.write_bytes(b'fixture'); runtime.chmod(0o700)
    model = tmp_path / 'model.bin'; model.write_bytes(b'fixture')
    return source, ASRConfig(runtime, model)


def fake_recognition(command):
    output = Path(command[command.index('-of') + 1]).with_suffix('.json')
    output.write_text(json.dumps({'transcription': [{'offsets': {'from': 0, 'to': 100}, 'text': ' cue'}]}))


@pytest.mark.parametrize('offset,begin,end', [
    (0, Fraction(1, 7), Fraction(3, 7)),
    (.5, Fraction(1, 3), Fraction(2, 3)),
    (-.1, Fraction(1, 60), Fraction(31, 60)),
])
def test_source_window_uses_sample_bounds_and_exact_origin(inputs, monkeypatch, offset, begin, end):
    source, config = inputs; calls = []
    monkeypatch.setattr(asr, '_audio_offset', lambda *_: offset)
    def run(command, cancel, label, **kwargs):
        calls.append(command)
        if label == 'Local speech recognition': fake_recognition(command)
    monkeypatch.setattr(asr, '_run', run)
    result = transcribe(source, config, source_start=begin, source_end=end)
    origin = Fraction(str(offset))
    first = max(0, math.ceil((begin - origin) * 16000))
    last = max(0, math.ceil((end - origin) * 16000))
    filters = calls[0][calls[0].index('-af') + 1]
    assert f'atrim=start_sample={first}:end_sample={last}' in filters
    assert 'atempo' not in filters and 'rubberband' not in filters
    assert result[0].start_seconds == origin + Fraction(first, 16000)
    assert result[0].end_seconds == result[0].start_seconds + Fraction(1, 10)
    assert isinstance(result[0].start_seconds, Fraction)
    assert not Path(calls[0][-1]).parent.exists()


def test_source_window_without_audio_returns_empty(inputs, monkeypatch):
    source, config = inputs
    monkeypatch.setattr(asr, '_audio_offset', lambda *_: 2.0)
    monkeypatch.setattr(asr, '_run', lambda *_: pytest.fail('No extraction or recognition needed'))
    assert transcribe(source, config, source_start=Fraction(0), source_end=Fraction(1)) == []


@pytest.mark.parametrize('begin,end', [(None, 1), (0, None), (-1, 1), (1, 1), (2, 1), (True, 2), (.1, 1), (0, float('inf'))])
def test_source_window_requires_exact_valid_bounds(inputs, begin, end):
    with pytest.raises(ASRError, match='source bounds'):
        transcribe(*inputs, source_start=begin, source_end=end)


@pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg is required')
def test_real_pcm_extraction_does_not_include_outside_samples(inputs, monkeypatch):
    source, config = inputs
    with wave.open(str(source), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
        wav.writeframes(struct.pack('<16000h', *range(16000)))
    original_run = asr._run; captured = {}
    def run(command, cancel, label, output_path=None, **kwargs):
        if label == 'Local speech recognition':
            with wave.open(command[command.index('-f') + 1], 'rb') as wav:
                captured['samples'] = struct.unpack('<' + str(wav.getnframes()) + 'h', wav.readframes(wav.getnframes()))
            fake_recognition(command)
        else:
            original_run(command, cancel, label, output_path)
    monkeypatch.setattr(asr, '_run', run)
    begin, end = Fraction(1, 7), Fraction(3, 7)
    result = transcribe(source, config, source_start=begin, source_end=end)
    first, last = math.ceil(begin * 16000), math.ceil(end * 16000)
    assert captured['samples'] == tuple(range(first, last))
    assert result[0].start_seconds == Fraction(first, 16000)


def test_window_probe_offset_avoids_binary_float_sample_shift(inputs, monkeypatch):
    source, config = inputs; calls = []
    def run(command, cancel, label, output_path=None, **kwargs):
        if label == 'Audio timing inspection':
            Path(output_path).write_text(json.dumps({'streams': [
                {'codec_type': 'video', 'start_time': '0.5'},
                {'codec_type': 'audio', 'start_time': '0.7'}]}))
        elif label == 'Local speech recognition':
            fake_recognition(command)
        else:
            calls.append(command)
    monkeypatch.setattr(asr, '_run', run)
    result = transcribe(source, config, source_start=Fraction(1), source_end=Fraction(2))
    filters = calls[0][calls[0].index('-af') + 1]
    assert 'atrim=start_sample=12800:end_sample=28800' in filters
    assert result[0].start_seconds == 1

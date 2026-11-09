import json
import os
from pathlib import Path
import subprocess
import threading
import time

import pytest
from lumen.asr import ASRConfig, ASRError, ASRCancelled, TranscriptSegment, _parse_transcript, _run, transcribe


def test_json_offsets_are_milliseconds():
    assert _parse_transcript({'result': {'language': 'zh'}, 'transcription': [
        {'offsets': {'from': 1230, 'to': 4560}, 'text': '  你好世界  '}
    ]}) == [TranscriptSegment(1.23, 4.56, '你好世界', 'zh')]


@pytest.mark.parametrize('offsets', [
    {'from': -1, 'to': 10}, {'from': 2, 'to': 1}, {'from': float('nan'), 'to': 10},
    {'from': 0, 'to': float('inf')}, {'from': True, 'to': 10}, {'from': '100', 'to': 10}, {},
])
def test_invalid_offsets_fail_closed(offsets):
    with pytest.raises(ASRError):
        _parse_transcript({'transcription': [{'offsets': offsets, 'text': 'hello'}]})


@pytest.mark.parametrize('value', [None, [], {}, {'transcription': None}, {'transcription': [{'text': 'x'}]}])
def test_malformed_json(value):
    with pytest.raises(ASRError):
        _parse_transcript(value)


def test_empty_transcript_and_blank_segments():
    assert _parse_transcript({'transcription': []}) == []
    assert _parse_transcript({'transcription': [{'offsets': {'from': 0, 'to': 100}, 'text': '  '}]}) == []


def test_out_of_order_fails():
    with pytest.raises(ASRError):
        _parse_transcript({'transcription': [
            {'offsets': {'from': 1000, 'to': 2000}, 'text': 'a'},
            {'offsets': {'from': 0, 'to': 1000}, 'text': 'b'},
        ]})


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setattr("lumen.asr._audio_offset", lambda *args: 0.0)
    source = tmp_path / 'media ; $(echo surprise).mp4'
    model = tmp_path / 'model.bin'
    runtime = tmp_path / 'whisper-cli'
    for path in (source, model, runtime):
        path.write_text('fixture')
    runtime.chmod(0o700)
    return source, ASRConfig(runtime, model)


def test_commands_progress_and_cleanup(inputs, monkeypatch):
    source, config = inputs
    calls, progress = [], []
    def run(command, cancel, label, **kwargs):
        calls.append(command)
        if label == 'Local speech recognition':
            output = Path(command[command.index('-of') + 1]).with_suffix('.json')
            output.write_text(json.dumps({'result': {'language': 'en'}, 'transcription': [
                {'offsets': {'from': 0, 'to': 1200}, 'text': ' hello'}]}))
    monkeypatch.setattr('lumen.asr._run', run)
    result = transcribe(source, config, progress.append)
    assert result[0].end_seconds == 1.2
    assert progress == [0., .15, .95, 1.]
    assert all(type(v) is float for v in progress)
    ffmpeg, whisper = calls
    assert ffmpeg[ffmpeg.index('-protocol_whitelist') + 1] == 'file,pipe'
    assert ffmpeg[ffmpeg.index('-i') + 1] == str(source)
    assert '-n' in ffmpeg and '-y' not in ffmpeg
    for flag, value in [('-ac', '1'), ('-ar', '16000'), ('-c:a', 'pcm_s16le')]:
        assert ffmpeg[ffmpeg.index(flag) + 1] == value
    assert '-tr' not in whisper  # never translate Chinese into English
    assert not Path(ffmpeg[-1]).parent.exists()
    assert source.read_text() == 'fixture'


def test_cleanup_on_failure(inputs, monkeypatch):
    source, config = inputs
    captured = []
    def fail(command, *args, **kwargs):
        captured.append(command[-1])
        raise ASRError('decode failed')
    monkeypatch.setattr('lumen.asr._run', fail)
    with pytest.raises(ASRError, match='decode failed'):
        transcribe(source, config)
    assert not Path(captured[0]).parent.exists()


def test_cancel_before_paths_checked():
    cancel = threading.Event(); cancel.set()
    with pytest.raises(ASRCancelled):
        transcribe('absent', ASRConfig('absent', 'absent'), cancel=cancel)


@pytest.mark.parametrize('threads', [0, -1, 257, True, 1.5])
def test_invalid_threads(inputs, threads):
    source, config = inputs
    with pytest.raises(ASRError, match='Threads'):
        transcribe(source, ASRConfig(config.runtime_path, config.model_path, threads=threads))


@pytest.mark.parametrize('language', ['--help', 'English', '', 'en;echo', None])
def test_invalid_languages(inputs, language):
    source, config = inputs
    with pytest.raises(ASRError, match='Language'):
        transcribe(source, ASRConfig(config.runtime_path, config.model_path, language=language))


def test_missing_runtime(inputs):
    source, config = inputs
    with pytest.raises(ASRError, match='runtime'):
        transcribe(source, ASRConfig('no-runtime', config.model_path))


def test_subprocess_failure_has_diagnostic():
    import sys
    with pytest.raises(ASRError, match='broken fixture'):
        _run([sys.executable, '-c', 'import sys; print("broken fixture"); sys.exit(9)'], None, 'Test')


def test_cancel_kills_running_process():
    import sys
    cancel = threading.Event()
    timer = threading.Timer(.15, cancel.set)
    start = time.monotonic()
    timer.start()
    try:
        with pytest.raises(ASRCancelled):
            _run([sys.executable, '-c', 'import time; time.sleep(60)'], cancel, 'Test')
    finally:
        timer.cancel()
    assert time.monotonic() - start < 3


@pytest.mark.parametrize('audio_delay,video_delay,expected_duration,leading_silence', [
    (1, 0, 3, True), (0, 1, 1, False),
])
def test_real_ffmpeg_normalizes_audio_to_video_origin(tmp_path, monkeypatch, audio_delay, video_delay, expected_duration, leading_silence):
    import shutil
    import wave
    import array
    import lumen.asr as asr
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg required')
    source = tmp_path / 'shifted.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-n',
                    '-itsoffset', str(video_delay), '-f', 'lavfi', '-i', 'color=s=16x16:r=10:d=2',
                    '-itsoffset', str(audio_delay), '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=2',
                    '-c:v', 'ffv1', '-c:a', 'pcm_s16le', str(source)], check=True)
    runtime, model = tmp_path / 'runtime', tmp_path / 'model'
    runtime.write_text('test'); runtime.chmod(0o700); model.write_text('test')
    original_run = asr._run
    def run(command, cancel, label, output_path=None, **kwargs):
        if label != 'Local speech recognition':
            return original_run(command, cancel, label, output_path)
        wav = command[command.index('-f') + 1]
        with wave.open(wav, 'rb') as audio:
            assert audio.getframerate() == 16000
            assert audio.getnchannels() == 1 and audio.getsampwidth() == 2
            assert audio.getnframes() / 16000 == pytest.approx(expected_duration, abs=.02)
            samples = array.array('h', audio.readframes(8000))
            assert (max(map(abs, samples)) == 0) == leading_silence
        Path(command[command.index('-of') + 1] + '.json').write_text('{"transcription": []}')
    monkeypatch.setattr(asr, '_run', run)
    assert transcribe(source, ASRConfig(runtime, model)) == []


def test_json_size_limit(tmp_path):
    from lumen.asr import _read_json
    output = tmp_path / 'oversize.json'
    with output.open('wb') as f:
        f.truncate(16 * 1024 * 1024 + 1)
    with pytest.raises(ASRError, match='16 MiB'):
        _read_json(output)


def test_runtime_environment_removes_backend_overrides(monkeypatch):
    from lumen.asr import _runtime_environment
    for key in ('GGML_BACKEND_PATH', 'ggml_backend_path', 'WHISPER_MODEL', 'LLAMA_ARG_MODEL_URL', 'HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN'):
        monkeypatch.setenv(key, 'untrusted-test-value')
    monkeypatch.setenv('PATH', '/existing/tool/path')
    env = _runtime_environment(3)
    assert not any(key.upper().startswith(('GGML_', 'WHISPER_', 'LLAMA_', 'HUGGING_FACE_')) for key in env)
    assert 'HF_TOKEN' not in env and env['HF_HUB_OFFLINE'] == '1'
    assert env['TRANSFORMERS_OFFLINE'] == '1'
    assert env['PATH'] == '/existing/tool/path'
    assert env['OMP_NUM_THREADS'] == env['OPENBLAS_NUM_THREADS'] == env['MKL_NUM_THREADS'] == '3'
    assert os.environ['GGML_BACKEND_PATH'] == 'untrusted-test-value'


def test_transcription_uses_private_runtime_working_directory(inputs, monkeypatch):
    source, config = inputs
    observed = {}
    monkeypatch.setenv('GGML_BACKEND_PATH', '/untrusted/backend')
    def run(command, cancel, label, **kwargs):
        if label == 'Local speech recognition':
            observed.update(kwargs)
            private = Path(kwargs['cwd'])
            assert private.is_dir() and private != Path.cwd()
            assert 'GGML_BACKEND_PATH' not in kwargs['env']
            output = Path(command[command.index('-of') + 1]).with_suffix('.json')
            assert output.parent == private
            output.write_text(json.dumps({'transcription': []}))
    monkeypatch.setattr('lumen.asr._run', run)
    assert transcribe(source, config) == []
    assert not Path(observed['cwd']).exists()


def test_real_subprocess_respects_explicit_cwd_and_environment(tmp_path):
    import sys
    from lumen.asr import _runtime_environment
    output = tmp_path / 'observed.json'
    command = [sys.executable, '-c', 'import os,json;print(json.dumps({"cwd":os.getcwd(),"offline":os.environ.get("HF_HUB_OFFLINE")}))']
    _run(command, None, 'Test', output, cwd=tmp_path, env=_runtime_environment(2))
    assert json.loads(output.read_text()) == {'cwd': str(tmp_path), 'offline': '1'}

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import sys
import threading
import time
import wave
import pytest
from lumen import tts


@pytest.fixture
def pack(tmp_path, monkeypatch):
    # Synthetic fixture: worker/publication tests intentionally bypass real model fingerprints.
    monkeypatch.setattr(tts, '_validate_pack_identity', lambda *a: None)
    root = tmp_path / 'pack'
    root.mkdir()
    for name in tts._REQUIRED:
        (root / name).write_bytes(b'test')
    (root / 'espeak-ng-data').mkdir()
    (root / 'dict').mkdir()
    return root


@pytest.mark.parametrize('key,value', [('voice_id', -1), ('voice_id', 53), ('voice_id', True), ('voice_id', 1.0),
    ('speed', 0), ('speed', float('nan')), ('speed', float('inf')), ('speed', True), ('speed', '1'),
    ('threads', 0), ('threads', 5), ('threads', 33), ('threads', True), ('threads', 2.5)])
def test_bad_controls(pack, tmp_path, key, value):
    args = {key: value}
    with pytest.raises(tts.TTSError):
        tts.synthesize('Hello', tts.TTSConfig(str(pack), **args), tmp_path / 'x.wav')


# Short IDs preserve oversized inputs without exceeding Windows' environment limit.
@pytest.mark.parametrize('text', ['', '  ', None, 'a\x00b', pytest.param('a' * 100001, id='overlength')])
def test_bad_text(pack, tmp_path, text):
    with pytest.raises(tts.TTSError):
        tts.synthesize(text, tts.TTSConfig(str(pack)), tmp_path / 'x.wav')


def test_missing_pack(tmp_path):
    with pytest.raises(tts.TTSError, match='existing local'):
        tts.synthesize('Hello', tts.TTSConfig(str(tmp_path / 'no')), tmp_path / 'x.wav')


def test_incomplete_pack(pack, tmp_path):
    (pack / 'tokens.txt').unlink()
    with pytest.raises(tts.TTSError, match='Incomplete'):
        tts.synthesize('Hello', tts.TTSConfig(str(pack)), tmp_path / 'x.wav')


def test_existing_and_invalid_output(pack, tmp_path):
    output = tmp_path / 'x.wav'
    output.write_bytes(b'original')
    with pytest.raises(tts.TTSError, match='already exists'):
        tts.synthesize('Hello', tts.TTSConfig(str(pack)), output)
    assert output.read_bytes() == b'original'
    with pytest.raises(tts.TTSError, match='WAV'):
        tts.synthesize('Hello', tts.TTSConfig(str(pack)), tmp_path / 'x.mp3')


def test_pre_cancel(pack, tmp_path):
    event = threading.Event()
    event.set()
    with pytest.raises(tts.TTSCancelled):
        tts.synthesize('Hello', tts.TTSConfig(str(pack)), tmp_path / 'x.wav', cancel=event)
    assert not list(tmp_path.glob('.lumen-tts-*'))


def test_native_process_cancel_cleanup(pack, tmp_path, monkeypatch):
    monkeypatch.setattr(tts, '_worker_command', lambda a, b: [sys.executable, '-c', 'import time; time.sleep(60)'])
    event = threading.Event()
    timer = threading.Timer(0.4, event.set)
    timer.start()
    start = time.monotonic()
    try:
        with pytest.raises(tts.TTSCancelled):
            tts.synthesize('Hello', tts.TTSConfig(str(pack)), tmp_path / 'x.wav', cancel=event)
    finally:
        timer.cancel()
    assert time.monotonic() - start < 3
    assert not (tmp_path / 'x.wav').exists()
    assert not list(tmp_path.glob('.lumen-tts-*'))


def test_worker_failure_cleanup(pack, tmp_path, monkeypatch):
    monkeypatch.setattr(tts, '_worker_command', lambda a, b: [sys.executable, '-c', 'raise SystemExit(4)'])
    with pytest.raises(tts.TTSError, match='engine failed'):
        tts.synthesize('Hello', tts.TTSConfig(str(pack)), tmp_path / 'x.wav')
    assert not list(tmp_path.glob('.lumen-tts-*'))


@pytest.mark.parametrize('version,voice_id', [('v1.0',0),('v1.1',102)])
def test_success_snapshot_no_overwrite(pack, tmp_path, monkeypatch, version, voice_id):
    script = tmp_path / 'fake.py'
    script.write_text('''import json, sys, wave
from pathlib import Path
r=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
with wave.open(r['output'], 'wb') as w:
 w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(b'\\x01\\x00'*24000)
Path(sys.argv[2]).write_text(json.dumps({'engine_version':'test'}),encoding='utf-8')
''', encoding='utf-8')
    monkeypatch.setattr(tts, '_worker_command', lambda a, b: [sys.executable, str(script), str(a), str(b)])
    updates = []
    result = tts.synthesize('Hello 世界', tts.TTSConfig(str(pack),voice_id=voice_id,pack_version=version), tmp_path / 'x.wav', progress=updates.append)
    assert result.duration == 1 and result.sample_rate == 24000
    assert result.provenance.text == 'Hello 世界'
    assert result.provenance.engine == 'sherpa-onnx/Kokoro-' + version
    assert result.provenance.voice_id == str(voice_id)
    assert len(result.provenance.assets_sha256) == 64
    assert len(result.provenance.output_sha256) == 64
    assert updates == [0.0, 1.0]
    with pytest.raises(FrozenInstanceError):
        result.provenance.text = 'changed'


def test_frozen_command(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert tts._worker_command('a', 'b') == [sys.executable, '--lumen-tts-worker', 'a', 'b']


def test_voice_map():
    assert len(tts.SUPPORTED_VOICES) == 53
    assert tts.SUPPORTED_VOICES[45] == (45, 'zf_xiaobei')


def test_output_inside_pack(pack):
    with pytest.raises(tts.TTSError, match='outside'):
        tts.synthesize('Hello', tts.TTSConfig(str(pack)), pack / 'speech.wav')


def test_symlink_asset_rejected(pack, tmp_path):
    external = tmp_path / 'external'
    external.write_text('data')
    (pack / 'linked').symlink_to(external)
    with pytest.raises(tts.TTSError, match='symbolic links'):
        tts.synthesize('Hello', tts.TTSConfig(str(pack)), tmp_path / 'speech.wav')


@pytest.mark.parametrize('content', ['[1]', 'invalid', pytest.param('x' * 65537, id='oversized-response')])
def test_invalid_worker_response(tmp_path, content):
    path = tmp_path / 'response.json'
    path.write_text(content)
    with pytest.raises(tts.TTSError, match='invalid response'):
        tts._response(path)


def test_missing_optional_runtime_explicit_error(pack, tmp_path, monkeypatch):
    monkeypatch.setattr(tts, '_worker_command', lambda a, b: [sys.executable, '-S', '-m', 'lumen.tts_worker', str(a), str(b)])
    with pytest.raises(tts.TTSError, match='runtime is missing'):
        tts.synthesize('Hello', tts.TTSConfig(str(pack)), tmp_path / 'x.wav')
    assert not (tmp_path / 'x.wav').exists()
    assert not list(tmp_path.glob('.lumen-tts-*'))


def test_existing_output_race_never_overwritten(pack, tmp_path, monkeypatch):
    original = tts.os.link
    def race(source, destination):
        Path(destination).write_bytes(b'concurrent writer')
        return original(source, destination)
    script = tmp_path / 'fake.py'
    script.write_text('''import json, sys, wave
from pathlib import Path
r=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
with wave.open(r['output'],'wb') as w:
 w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(b'\\x01\\x00'*24)
Path(sys.argv[2]).write_text(json.dumps({'engine_version':'test'}),encoding='utf-8')
''', encoding='utf-8')
    monkeypatch.setattr(tts, '_worker_command', lambda a, b: [sys.executable, str(script), str(a), str(b)])
    monkeypatch.setattr(tts.os, 'link', race)
    with pytest.raises(tts.TTSError, match='Cannot publish'):
        tts.synthesize('Hello', tts.TTSConfig(str(pack)), tmp_path / 'x.wav')
    assert (tmp_path / 'x.wav').read_bytes() == b'concurrent writer'
    assert not list(tmp_path.glob('.lumen-tts-*'))


def test_fifo_asset_rejected_before_open_can_block(pack, tmp_path):
    if not hasattr(tts.os, 'mkfifo'): pytest.skip('No FIFO support on this platform')
    fifo = pack/'unexpected-fifo'; tts.os.mkfifo(fifo)
    start=time.monotonic()
    with pytest.raises(tts.TTSError, match='special files'):
        tts._assets(pack, threading.Event())
    assert time.monotonic()-start < 1
    with pytest.raises(tts.TTSError, match='regular files'):
        tts._hash(fifo, threading.Event())


def test_hash_checks_special_file_swap_before_read(pack, tmp_path, monkeypatch):
    if not hasattr(tts.os, 'mkfifo'): pytest.skip('No FIFO support on this platform')
    path=pack/'tokens.txt'; original=tts.os.open
    def swap(target,flags):
        path.unlink();tts.os.mkfifo(path)
        return original(target,flags)
    monkeypatch.setattr(tts.os,'open',swap)
    with pytest.raises(tts.TTSError,match='changed while being opened'):
        tts._hash(path,None)


def test_native_worker_timeout_reaps_and_cleans(pack,tmp_path,monkeypatch):
    monkeypatch.setattr(tts,'MAX_WORKER_SECONDS',.15)
    monkeypatch.setattr(tts,'_worker_command',lambda a,b:[sys.executable,'-c','import time;time.sleep(60)'])
    start=time.monotonic()
    with pytest.raises(tts.TTSError,match='safety limit'):
        tts.synthesize('text',tts.TTSConfig(str(pack)),tmp_path/'x.wav')
    assert time.monotonic()-start < 3
    assert not list(tmp_path.glob('.lumen-tts-*')) and not (tmp_path/'x.wav').exists()


def test_native_worker_log_limit_reaps_and_cleans(pack,tmp_path,monkeypatch):
    monkeypatch.setattr(tts,'MAX_WORKER_LOG_BYTES',1000)
    monkeypatch.setattr(tts,'_worker_command',lambda a,b:[sys.executable,'-c',"import sys,time;sys.stdout.write('x'*2000);sys.stdout.flush();time.sleep(60)"])
    with pytest.raises(tts.TTSError,match='diagnostic size'):
        tts.synthesize('text',tts.TTSConfig(str(pack)),tmp_path/'x.wav')
    assert not list(tmp_path.glob('.lumen-tts-*')) and not (tmp_path/'x.wav').exists()


def test_fast_exit_worker_log_limit_checked_after_wait(pack,tmp_path,monkeypatch):
    monkeypatch.setattr(tts,'MAX_WORKER_LOG_BYTES',1000)
    monkeypatch.setattr(tts,'_worker_command',lambda a,b:[sys.executable,'-c',"import sys;sys.stdout.write('x'*2000)"])
    with pytest.raises(tts.TTSError,match='diagnostic size'):
        tts.synthesize('text',tts.TTSConfig(str(pack)),tmp_path/'x.wav')
    assert not list(tmp_path.glob('.lumen-tts-*'))

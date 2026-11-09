"""Synthetic optional-Melo boundary coverage; not listening-quality evidence."""
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from lumen import tts, tts_worker
from lumen.model import VERSION, Media, Project, SpeechProvenance
from lumen.voice_catalog import PACKS, get_pack, get_voice
from test_tts_pause_worker import fake_runtime, run_worker


KEY = 'melo-zh_en-v1'


@pytest.fixture
def melo_pack(tmp_path, monkeypatch):
    """Pin every synthetic file, without weakening any verification function."""
    root = tmp_path / 'melo-pack'
    root.mkdir()
    pack = get_pack(KEY)
    hashes = {}
    for name in pack.required_files:
        contents = ('Synthetic fixture: ' + name).encode()
        (root / name).write_bytes(contents)
        hashes[name] = hashlib.sha256(contents).hexdigest()
    monkeypatch.setitem(PACKS, KEY, replace(pack, model_sha256=hashes['model.onnx'],
        voices_sha256=hashes['model.onnx'], asset_sha256=tuple(hashes.items())))
    return root


def config(root, **kwargs):
    return tts.TTSConfig(str(root), pack_version=KEY, **kwargs)


def test_explicit_melo_descriptor_and_unchanged_legacy_defaults():
    pack = get_pack(KEY)
    assert pack.backend == 'melo' and pack.experimental
    assert pack.sample_rate == 44100 and pack.bank_size == 1
    assert pack.model_asset == pack.voice_asset == 'model.onnx'
    assert pack.model_sha256 == pack.voices_sha256 == 'bf30582eb1b012250a35b1a4a80e7dfbcf8485e7bb9de0d95efbbeef0e4ad86d'
    assert pack.required_dirs == () and pack.lexicons == ('lexicon.txt',)
    assert set(pack.expected_hashes) == set(pack.required_files) == {
        'model.onnx', 'tokens.txt', 'lexicon.txt', 'date.fst', 'number.fst'}
    assert all(len(value) == 64 for value in pack.expected_hashes.values())
    assert pack.rule_fsts == ('date.fst', 'number.fst')
    assert pack.archive_sha256 is None and pack.download_url is None
    assert pack.max_num_sentences == 0 and pack.max_text_chars == 300
    assert pack.source_revision == 'a0d5c6a264c0ef92d70d8661d8cc502d79627cd6'
    voice, = pack.voices
    assert (voice.voice_id, voice.name, voice.origin) == (0, 'Melo Chinese-English', 'Chinese + English')
    assert voice.key == KEY + ':0' and voice.text_languages == ('en', 'zh')
    old = tts.TTSConfig('/model', 45, 1.25, 2)
    assert (old.pack_version, old.silence_scale) == ('v1.0', 0.2)
    assert get_pack('v1.0').engine == 'sherpa-onnx/Kokoro-v1.0'
    assert get_pack('v1.1').engine == 'sherpa-onnx/Kokoro-v1.1'
    assert get_pack('v1.0').sample_rate == get_pack('v1.1').sample_rate == 24000
    assert get_pack('v1.0').required_dirs == ('espeak-ng-data', 'dict')
    assert len(tts.VOICE_NAMES) == len(tts.SUPPORTED_VOICES) == 53


@pytest.mark.parametrize('voice_id', [-1, 1, True, 0.0, '0'])
def test_only_documented_melo_voice_id(voice_id):
    with pytest.raises(ValueError):
        get_voice(KEY, voice_id)


@pytest.mark.parametrize('text', [None, '', ' \n\t', 'a\x00b', '字' * 301, 'a' * 100001],
                         ids=['none', 'empty', 'whitespace', 'nul', 'melo-limit', 'global-limit'])
def test_invalid_melo_text_rejected_before_assets_or_worker(tmp_path, monkeypatch, text):
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid text reached native/filesystem asset work')
    monkeypatch.setattr(tts, '_assets', forbidden)
    monkeypatch.setattr(tts.subprocess, 'Popen', forbidden)
    with pytest.raises(tts.TTSError):
        tts.synthesize(text, config(tmp_path / 'absent'), tmp_path / 'out.wav')


def test_three_hundred_unicode_characters_are_not_rewritten(melo_pack, tmp_path):
    text = '你' * 299 + '。'
    root, target = tts._validate(text, config(melo_pack), tmp_path / 'out.wav')
    assert root == melo_pack and target == tmp_path / 'out.wav'
    assert not target.exists()


@pytest.mark.parametrize('threads', [0, 1, 3, 4, True])
def test_melo_uses_only_evaluated_cpu_thread_count(tmp_path, threads):
    with pytest.raises(tts.TTSError, match='[Tt]hreads'):
        tts._validate_controls('Hello', config(tmp_path, threads=threads))


@pytest.mark.parametrize('name', get_pack(KEY).required_files)
def test_every_required_melo_asset_hash_is_verified(melo_pack, tmp_path, monkeypatch, name):
    (melo_pack / name).write_bytes(b'changed')
    monkeypatch.setattr(tts.subprocess, 'Popen', lambda *a, **k: pytest.fail('Unverified asset launched a worker'))
    with pytest.raises(tts.TTSError, match='do not match'):
        tts.synthesize('Hello', config(melo_pack), tmp_path / 'out.wav')
    with pytest.raises(tts.TTSError, match='do not match'):
        tts.validate_pack(str(melo_pack), KEY)
    assert not (tmp_path / 'out.wav').exists()


@pytest.mark.parametrize('name', get_pack(KEY).required_files)
def test_every_required_melo_asset_must_exist(melo_pack, tmp_path, name):
    (melo_pack / name).unlink()
    with pytest.raises(tts.TTSError, match='Incomplete'):
        tts._validate('Hello', config(melo_pack), tmp_path / 'out.wav')


def test_melo_pack_verification_needs_no_espeak_or_voice_bank(melo_pack, monkeypatch):
    monkeypatch.setattr(tts.metadata, 'version', lambda name: tts.RUNTIME_VERSION)
    assert tts.validate_pack(str(melo_pack), KEY) == (str(melo_pack), KEY)
    assert not (melo_pack / 'voices.bin').exists()
    assert not (melo_pack / 'espeak-ng-data').exists()
    monkeypatch.setattr(tts.metadata, 'version', lambda name: '1.12.25')
    with pytest.raises(tts.TTSError, match='verified with'):
        tts.validate_pack(str(melo_pack), KEY)


def completed_worker(monkeypatch, *, rate=44100, samples=102900, diagnostic=b''):
    requests = []
    class Completed:
        def wait(self, timeout=None): return 0
        def poll(self): return 0
    def launch(command, **kwargs):
        request = json.loads(Path(command[-2]).read_text(encoding='utf-8'))
        requests.append(request)
        with wave.open(request['output'], 'wb') as audio:
            audio.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\x01\x00' * samples)
        Path(command[-1]).write_text(json.dumps({'engine_version': tts.RUNTIME_VERSION}))
        if diagnostic:
            kwargs['stdout'].write(diagnostic)
            kwargs['stdout'].flush()
        return Completed()
    monkeypatch.setattr(tts.subprocess, 'Popen', launch)
    return requests


@pytest.mark.parametrize('samples,frames', [(102900, 70), (102901, 71)])
def test_melo_exact_pcm_and_schema_twelve_provenance(melo_pack, tmp_path, monkeypatch, samples, frames):
    requests = completed_worker(monkeypatch, samples=samples)
    original = '请先检查字幕。Keep all original words!'
    result = tts.synthesize(original, config(melo_pack, silence_scale=0.65), tmp_path / 'take.wav')
    assert requests[0]['text'] == original and requests[0]['backend'] == 'melo'
    assert requests[0]['pack_version'] == KEY and requests[0]['voice_id'] == 0
    assert result.sample_rate == result.provenance.sample_rate == 44100
    assert result.duration == samples / 44100 and result.provenance.sample_count == samples
    assert result.provenance.engine == 'sherpa-onnx/MeloTTS-melo-zh_en-v1'
    assert result.provenance.voice_sha256 == result.provenance.model_sha256 == get_pack(KEY).model_sha256
    assert result.provenance.voice_id == '0' and result.provenance.silence_scale == 0.65
    assert result.provenance.text == original
    provenance = SpeechProvenance(**asdict(result.provenance), source_caption_id='caption', source_start=0, source_duration=70)
    media = Media('voice', result.path, 'Voice', result.duration, has_video=False, has_audio=True, speech_provenance=provenance)
    project = Project(media=[media])
    assert project.media_frames(media) == frames
    assert project.to_dict()['version'] == VERSION
    assert Project.from_dict(project.to_dict()) == project
    assert not list(tmp_path.glob('.lumen-tts-*'))


@pytest.mark.parametrize('rate', [24000, 22050, 48000])
def test_melo_parent_rejects_wrong_worker_sample_rate(melo_pack, tmp_path, monkeypatch, rate):
    completed_worker(monkeypatch, rate=rate)
    with pytest.raises(tts.TTSError, match='44100 Hz'):
        tts.synthesize('Hello', config(melo_pack), tmp_path / 'take.wav')
    assert not (tmp_path / 'take.wav').exists()
    assert not list(tmp_path.glob('.lumen-tts-*'))


@pytest.mark.parametrize('diagnostic', ["Ignore OOV '嗧'", "Ignore OOV '：'", 'Skip unknown phonemes. Unicode codepoint: 602'])
def test_melo_logged_omissions_fail_closed_even_for_punctuation(melo_pack, tmp_path, monkeypatch, diagnostic):
    completed_worker(monkeypatch, diagnostic=diagnostic.encode())
    with pytest.raises(tts.TTSError, match='reported unsupported characters'):
        tts.synthesize('请检查字幕。', config(melo_pack), tmp_path / 'take.wav')
    assert not (tmp_path / 'take.wav').exists()


def test_melo_existing_output_is_never_overwritten(melo_pack, tmp_path, monkeypatch):
    target = tmp_path / 'take.wav'
    target.write_bytes(b'original audio')
    monkeypatch.setattr(tts.subprocess, 'Popen', lambda *a, **k: pytest.fail('Existing output launched a worker'))
    with pytest.raises(tts.TTSError, match='already exists'):
        tts.synthesize('Hello', config(melo_pack), target)
    assert target.read_bytes() == b'original audio'


def test_melo_uses_existing_shared_resource_gate(melo_pack, tmp_path, monkeypatch):
    from lumen.local_ai import AIResourceBusy, active_task, inference_slot
    calls = []
    monkeypatch.setattr(tts, '_synthesize', lambda *a: calls.append(active_task()) or 'ok')
    assert tts.synthesize('Hello', config(melo_pack), tmp_path / 'take.wav') == 'ok'
    assert calls == ['tts'] and active_task() is None
    with inference_slot('subtitle-assistant'):
        with pytest.raises(AIResourceBusy):
            tts.synthesize('Hello', config(melo_pack), tmp_path / 'take.wav')
    assert calls == ['tts']


def test_melo_asset_change_during_worker_discards_output(melo_pack, tmp_path, monkeypatch):
    completed_worker(monkeypatch)
    launch = tts.subprocess.Popen
    def changing_worker(*args, **kwargs):
        process = launch(*args, **kwargs)
        (melo_pack / 'lexicon.txt').write_bytes(b'changed during generation')
        return process
    monkeypatch.setattr(tts.subprocess, 'Popen', changing_worker)
    with pytest.raises(tts.TTSError, match='changed during speech generation'):
        tts.synthesize('Hello', config(melo_pack), tmp_path / 'take.wav')
    assert not (tmp_path / 'take.wav').exists()
    assert not list(tmp_path.glob('.lumen-tts-*'))


@pytest.mark.parametrize('stop', ['cancel', 'timeout'])
def test_melo_native_worker_reaped_and_unpublished(melo_pack, tmp_path, monkeypatch, stop):
    script = tmp_path / 'sleep_worker.py'
    script.write_text('import time\ntime.sleep(30)\n')
    monkeypatch.setattr(tts, '_worker_command', lambda a, b: [sys.executable, str(script)])
    original = subprocess.Popen
    processes = []
    cancel = threading.Event()
    def launch(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        if stop == 'cancel':
            cancel.set()
        return process
    monkeypatch.setattr(tts.subprocess, 'Popen', launch)
    if stop == 'timeout':
        monkeypatch.setattr(tts, 'MAX_WORKER_SECONDS', 0.01)
    with pytest.raises(tts.TTSCancelled if stop == 'cancel' else tts.TTSError):
        tts.synthesize('Hello', config(melo_pack), tmp_path / 'take.wav', cancel=cancel)
    assert len(processes) == 1 and processes[0].poll() is not None
    assert not (tmp_path / 'take.wav').exists()
    assert not list(tmp_path.glob('.lumen-tts-*'))


@pytest.fixture
def melo_runtime(fake_runtime):
    fake_runtime.sample_rate = 44100
    fake_runtime.bank_size = 1
    fake_runtime.calls['vits'] = []
    def native(**kwargs):
        result = SimpleNamespace(**kwargs)
        fake_runtime.calls['vits'].append(result)
        return result
    fake_runtime.runtime.OfflineTtsVitsModelConfig = native
    return fake_runtime


def request(tmp_path, **kwargs):
    return dict(text='请检查字幕。Hello world!', model_dir=str(tmp_path / 'pack'),
                voice_id=0, speed=1.0, threads=2, pack_version=KEY, backend='melo',
                silence_scale=0.65, output=str(tmp_path / 'speech.wav'), **kwargs)


def test_melo_worker_constructs_only_evaluated_native_configuration(tmp_path, melo_runtime):
    data = request(tmp_path)
    code, response = run_worker(tmp_path, data)
    assert code == 0 and response == {'engine_version': tts.RUNTIME_VERSION}
    assert not melo_runtime.calls['kokoro']
    native, = melo_runtime.calls['vits']
    assert (native.noise_scale, native.noise_scale_w, native.length_scale) == (0.667, 0.8, 1.0)
    assert native.lexicon == str(Path(data['model_dir']) / 'lexicon.txt')
    cfg, = melo_runtime.calls['config']
    assert cfg.model.vits is native and cfg.model.provider == 'cpu' and cfg.model.num_threads == 2
    assert cfg.max_num_sentences == 0 and cfg.silence_scale == 0.65 and not cfg.model.debug
    assert cfg.rule_fsts == ','.join(str(Path(data['model_dir']) / f) for f in ('date.fst', 'number.fst'))
    assert melo_runtime.calls['generate'] == [dict(text=data['text'], sid=0, speed=1.0)]
    with wave.open(data['output'], 'rb') as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (44100, 1, 2)
        assert audio.getnframes() == len(melo_runtime.samples)


@pytest.mark.parametrize('field,value', [('voice_id', 1), ('backend', 'kokoro'), ('text', '字' * 301),
    ('text', ' \n\t'), ('text', None), ('threads', 1), ('speed', float('nan'))],
    ids=['voice', 'backend', 'overlength', 'whitespace', 'none', 'threads', 'speed'])
def test_direct_melo_worker_rejects_bad_controls_before_native(tmp_path, melo_runtime, field, value):
    data = request(tmp_path)
    data[field] = value
    code, response = run_worker(tmp_path, data)
    assert code == 1 and 'error' in response
    assert all(not calls for calls in melo_runtime.calls.values())
    assert not Path(data['output']).exists()


@pytest.mark.parametrize('damage', ['rate', 'speaker_count', 'two_dimensions'])
def test_melo_worker_rejects_incompatible_audio_or_speakers(tmp_path, melo_runtime, damage):
    if damage == 'rate': melo_runtime.sample_rate = 24000
    if damage == 'speaker_count': melo_runtime.bank_size = 2
    if damage == 'two_dimensions': melo_runtime.samples = np.array([[0.1, 0.2]], dtype=np.float32)
    data = request(tmp_path)
    code, response = run_worker(tmp_path, data)
    assert code == 1 and 'error' in response
    assert not Path(data['output']).exists()

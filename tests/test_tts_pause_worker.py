"""Mocked native boundary tests, not real Kokoro or listening-quality evidence.

The fake sherpa_onnx module records constructor and generate arguments. NumPy and
WAV writing are real so the worker's PCM serialization is checked independently.
"""
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import wave

import numpy as np
import pytest

from lumen import tts_worker
from lumen.tts import RUNTIME_VERSION
from lumen.voice_catalog import get_pack


@pytest.fixture
def fake_runtime(monkeypatch):
    calls = {name: [] for name in ('kokoro', 'model', 'config', 'engine', 'generate')}
    runtime = ModuleType('sherpa_onnx')
    runtime.__version__ = RUNTIME_VERSION
    state = SimpleNamespace(
        samples=np.array([0.0, 0.25, -0.25, 1.5, -1.5, 0.125], dtype=np.float32),
        sample_rate=24000, bank_size=54, calls=calls, runtime=runtime)

    def constructor(name):
        def create(**kwargs):
            result = SimpleNamespace(**kwargs)
            if name == 'config':
                result.validate = lambda: True
            calls[name].append(result)
            return result
        return create

    class MockOfflineTts:
        def __init__(self, config):
            calls['engine'].append(config)
            self.num_speakers = state.bank_size

        def generate(self, text, *, sid, speed):
            calls['generate'].append(dict(text=text, sid=sid, speed=speed))
            return SimpleNamespace(samples=state.samples, sample_rate=state.sample_rate)

    runtime.OfflineTtsKokoroModelConfig = constructor('kokoro')
    runtime.OfflineTtsModelConfig = constructor('model')
    runtime.OfflineTtsConfig = constructor('config')
    runtime.OfflineTts = MockOfflineTts
    monkeypatch.setitem(sys.modules, 'sherpa_onnx', runtime)
    return state


def request_data(tmp_path, version='v1.0', voice_id=0):
    return dict(text='2026年10月18日，价格3.14元。 AI 与 U.S.A.\nKeep every word!',
                model_dir=str(tmp_path / 'mock-pack'), voice_id=voice_id,
                speed=0.95, threads=2, pack_version=version,
                output=str(tmp_path / 'speech.wav'))


def run_worker(tmp_path, data):
    request = tmp_path / 'request.json'
    response = tmp_path / 'response.json'
    request.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    code = tts_worker.main([str(request), str(response)])
    return code, json.loads(response.read_text(encoding='utf-8'))


@pytest.mark.parametrize('version,voice_id', [('v1.0', 0), ('v1.1', 102)])
@pytest.mark.parametrize('pause', [0.2, 0.65, 1.0, None],
                         ids=['minimum', 'chosen', 'maximum', 'legacy-missing'])
def test_mock_worker_passes_pause_to_native_config_and_writes_pcm(
        tmp_path, fake_runtime, version, voice_id, pause):
    pack = get_pack(version)
    fake_runtime.bank_size = pack.bank_size
    data = request_data(tmp_path, version, voice_id)
    if pause is not None:
        data['silence_scale'] = pause

    code, response = run_worker(tmp_path, data)

    assert code == 0 and response == {'engine_version': RUNTIME_VERSION}
    config, = fake_runtime.calls['config']
    assert config.silence_scale == (0.2 if pause is None else pause)
    assert config.max_num_sentences == 1
    assert config.model.provider == 'cpu' and config.model.num_threads == 2
    assert not config.model.debug
    assert config.rule_fsts == ','.join(str(Path(data['model_dir']) / name)
                                      for name in pack.rule_fsts)
    assert fake_runtime.calls['engine'] == [config]
    assert fake_runtime.calls['generate'] == [dict(text=data['text'], sid=voice_id, speed=0.95)]
    with wave.open(data['output'], 'rb') as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 24000)
        assert audio.getnframes() == len(fake_runtime.samples)
        assert audio.getcomptype() == 'NONE'
        actual = audio.readframes(audio.getnframes())
    expected = (np.clip(fake_runtime.samples, -1.0, 1.0) * 32767).astype('<i2').tobytes()
    assert actual == expected


@pytest.mark.parametrize('pause', [
    pytest.param(True, id='true'), pytest.param(False, id='false'),
    pytest.param(None, id='null'), pytest.param('0.2', id='string'),
    pytest.param([], id='list'), pytest.param({}, id='object'),
    pytest.param(float('nan'), id='nan'), pytest.param(float('inf'), id='positive-inf'),
    pytest.param(float('-inf'), id='negative-inf'), pytest.param(-1, id='negative'),
    pytest.param(0, id='zero'), pytest.param(0.199999, id='below-minimum'),
    pytest.param(1.000001, id='above-maximum'), pytest.param(1.5, id='unsafe-expansion'),
    pytest.param(10 ** 400, id='huge-integer'),
])
def test_direct_worker_request_rejects_unsafe_pause_before_native_construction(
        tmp_path, fake_runtime, pause):
    data = request_data(tmp_path)
    data['silence_scale'] = pause

    code, response = run_worker(tmp_path, data)

    assert code == 1 and 'error' in response
    assert all(not calls for calls in fake_runtime.calls.values())
    assert not Path(data['output']).exists()
    assert data['text'] not in response['error']
    assert data['model_dir'] not in response['error']


@pytest.mark.parametrize('samples', [
    pytest.param([], id='empty'), pytest.param([0.0, 0.0], id='silent'),
    pytest.param([0.1, float('nan')], id='nan'),
    pytest.param([0.1, float('inf')], id='infinite'),
])
def test_mock_worker_rejects_invalid_audio_without_publishing_wav(
        tmp_path, fake_runtime, samples):
    fake_runtime.samples = np.asarray(samples, dtype=np.float32)
    data = request_data(tmp_path)
    data['silence_scale'] = 0.65

    code, response = run_worker(tmp_path, data)

    assert code == 1 and 'error' in response
    assert len(fake_runtime.calls['generate']) == 1
    assert not Path(data['output']).exists()


def test_mock_worker_rejects_unpinned_runtime_before_native_construction(tmp_path, fake_runtime):
    fake_runtime.runtime.__version__ = '1.12.25'
    data = request_data(tmp_path)
    data['silence_scale'] = 0.65

    code, response = run_worker(tmp_path, data)

    assert code == 1 and 'error' in response
    assert all(not calls for calls in fake_runtime.calls.values())
    assert not Path(data['output']).exists()

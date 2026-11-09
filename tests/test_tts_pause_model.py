"""Bounded pause controls and provenance, with mocked synthesis only.

The test WAVs below are deterministic PCM fixtures, not native Kokoro output.
These tests establish request/persistence boundaries, not listening quality.
"""
from contrast_legacy_fixture import strip_contrast_pivot_field
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, fields
import json
from pathlib import Path
import wave

import pytest

from lumen import model, tts
from lumen.model import Media, Project, SpeechProvenance, VERSION


def tts_provenance_data():
    # Original field order is intentional: legacy positional callers must work.
    return dict(text='Hello. 世界。', engine='sherpa-onnx/Kokoro-v1.0',
                engine_version='mock-boundary', model_sha256='a' * 64,
                voice_sha256='b' * 64, assets_sha256='c' * 64, voice_id='0',
                speed=1.0, sample_rate=24000, output_sha256='d' * 64,
                created_utc='2026-10-18T18:00:00Z')


def speech_provenance_data():
    data = tts_provenance_data()
    output_sha256 = data.pop('output_sha256')
    return dict(**data, source_caption_id='caption', source_start=0,
                source_duration=30, output_sha256=output_sha256)


def speech_project(tmp_path, silence_scale=0.2):
    provenance = SpeechProvenance(**speech_provenance_data(), silence_scale=silence_scale)
    return Project(media=[Media('speech', str(tmp_path / 'speech.wav'), 'Speech', 1,
                                has_video=False, has_audio=True,
                                speech_provenance=provenance)])


def test_default_and_legacy_positional_constructors_preserve_field_order():
    config = tts.TTSConfig('pack', 0, 1.0, 2, 'v1.0')
    generated = tts.TTSProvenance(*tts_provenance_data().values())
    persisted = SpeechProvenance(*speech_provenance_data().values())
    for value in (config, generated, persisted):
        assert fields(value)[-1 if isinstance(value, tts.TTSConfig) else -2].name == 'silence_scale'
        assert value.silence_scale == 0.2
        assert asdict(value)['silence_scale'] == 0.2
        with pytest.raises(FrozenInstanceError):
            value.silence_scale = 0.7


@pytest.mark.parametrize('version', range(4, 11))
def test_old_speech_projects_default_pause_and_roundtrip(tmp_path, version):
    data = speech_project(tmp_path).to_dict()
    data['version'] = version; strip_contrast_pivot_field(data)
    if data['version'] < 17:
        for old_clip in data.get('clips', []):
            old_clip.get('grade', {}).pop('hue_hue_curve', None)
            old_clip.get('grade', {}).pop('hue_sat_curve', None)
            old_clip.get('grade', {}).pop('qualifier', None)
    if data['version'] < 16:
        for old_clip in data.get('clips', []): old_clip.pop('transform', None)
    del data['media'][0]['speech_provenance']['silence_scale']
    del data['media'][0]['speech_provenance']['sample_count']
    original = deepcopy(data)
    loaded = Project.from_dict(data)
    assert data == original
    assert loaded.media[0].speech_provenance.silence_scale == 0.2
    path = tmp_path / 'migrated.lumen'
    loaded.save(path)
    assert Project.load(path) == loaded
    assert loaded.to_dict()['version'] == VERSION


def test_schema_ten_reader_gate_rejects_pause_field_before_decoding(tmp_path, monkeypatch):
    data = speech_project(tmp_path, 0.8).to_dict()
    assert data['version'] == VERSION
    assert data['media'][0]['speech_provenance']['silence_scale'] == 0.8
    monkeypatch.setattr(model, 'VERSION', 10)
    with pytest.raises(ValueError, match='^Unsupported project format or version$'):
        Project.from_dict(data)


@pytest.mark.parametrize('value', [0.2, 0.35, 0.8, 1.0, 1])
def test_valid_pause_boundaries_and_persistence(tmp_path, value):
    config = tts.TTSConfig('pack', silence_scale=value)
    generated = tts.TTSProvenance(**tts_provenance_data(), silence_scale=value)
    project = speech_project(tmp_path, value)
    assert config.silence_scale == generated.silence_scale == value
    path = tmp_path / 'speech.lumen'
    project.save(path)
    assert Project.load(path) == project
    stored = json.loads(path.read_text(encoding='utf-8'))
    assert stored['media'][0]['speech_provenance']['silence_scale'] == value


INVALID_SCALES = [
    pytest.param(True, id='true'), pytest.param(False, id='false'),
    pytest.param(None, id='none'), pytest.param('0.2', id='string'),
    pytest.param([], id='list'), pytest.param({}, id='dict'),
    pytest.param(float('nan'), id='nan'), pytest.param(float('inf'), id='positive-inf'),
    pytest.param(float('-inf'), id='negative-inf'), pytest.param(-1, id='negative'),
    pytest.param(0, id='zero'), pytest.param(0.1, id='below-minimum'),
    pytest.param(0.199999, id='just-below-minimum'),
    pytest.param(1.000001, id='just-above-maximum'), pytest.param(1.5, id='unsafe-expansion'),
    pytest.param(10 ** 400, id='huge-integer'),
]


@pytest.mark.parametrize('value', INVALID_SCALES)
def test_invalid_pause_controls_and_provenance_are_rejected(tmp_path, value):
    with pytest.raises(tts.TTSError, match='Pause preservation'):
        tts.TTSConfig('pack', silence_scale=value)
    with pytest.raises(tts.TTSError, match='Pause preservation'):
        tts.TTSProvenance(**tts_provenance_data(), silence_scale=value)
    with pytest.raises(ValueError, match='speech pause preservation'):
        SpeechProvenance(**speech_provenance_data(), silence_scale=value)
    data = speech_project(tmp_path).to_dict()
    data['media'][0]['speech_provenance']['silence_scale'] = value
    with pytest.raises(ValueError, match='speech pause preservation'):
        Project.from_dict(data)


def test_synthesis_revalidates_pause_before_reading_pack_or_launching_worker(tmp_path, monkeypatch):
    config = tts.TTSConfig('missing-pack')
    object.__setattr__(config, 'silence_scale', 1.5)
    monkeypatch.setattr(tts, '_assets', lambda *args: pytest.fail('Invalid input read model assets'))
    monkeypatch.setattr(tts.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('Invalid input launched worker'))
    with pytest.raises(tts.TTSError, match='Pause preservation'):
        tts.synthesize('Hello.', config, tmp_path / 'speech.wav')
    assert not (tmp_path / 'speech.wav').exists()
    assert not list(tmp_path.glob('.lumen-tts-*'))


@pytest.mark.parametrize('version,voice_id', [('v1.0', 0), ('v1.1', 102)])
@pytest.mark.parametrize('silence_scale', [0.2, 0.65, 1.0])
def test_mocked_synthesis_request_and_saved_provenance_keep_pause(
        tmp_path, monkeypatch, version, voice_id, silence_scale):
    root = tmp_path / 'pack'
    root.mkdir()
    for name in tts.get_pack(version).required_files:
        (root / name).write_bytes(b'synthetic test asset')
    (root / 'espeak-ng-data').mkdir()
    (root / 'dict').mkdir()
    monkeypatch.setattr(tts, '_validate_pack_identity', lambda *args: None)
    requests = []

    class CompletedMockWorker:
        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    def mock_worker(command, **kwargs):
        request = json.loads(Path(command[-2]).read_text(encoding='utf-8'))
        requests.append(request)
        with wave.open(request['output'], 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(b'\x01\x00' * 240)
        Path(command[-1]).write_text(json.dumps({'engine_version': 'mock-boundary'}), encoding='utf-8')
        return CompletedMockWorker()

    monkeypatch.setattr(tts.subprocess, 'Popen', mock_worker)
    text = 'Hello. 世界。\nKeep the original text.'
    config = tts.TTSConfig(str(root), voice_id=voice_id, speed=0.9,
                           pack_version=version, silence_scale=silence_scale)
    result = tts.synthesize(text, config, tmp_path / 'saved.wav')
    assert len(requests) == 1
    assert requests[0]['silence_scale'] == silence_scale
    assert requests[0]['text'] == text
    assert requests[0]['voice_id'] == voice_id
    assert requests[0]['speed'] == 0.9
    assert requests[0]['pack_version'] == version
    assert result.provenance.silence_scale == silence_scale
    assert result.provenance.text == text
    assert result.provenance.speed == 0.9
    assert result.provenance.engine_version == 'mock-boundary'
    assert result.duration == 0.01
    assert result.provenance.sample_count == 240
    assert Path(result.path).is_file()
    assert not list(tmp_path.glob('.lumen-tts-*'))

    # This is the existing UI's asdict transfer boundary, without importing Qt.
    provenance = SpeechProvenance(**asdict(result.provenance), source_caption_id='caption',
                                  source_start=0, source_duration=30)
    project = Project(media=[Media('speech', result.path, 'Saved', result.duration,
                                  has_video=False, has_audio=True, speech_provenance=provenance)])
    path = tmp_path / 'saved.lumen'
    project.save(path)
    loaded = Project.load(path)
    assert loaded == project
    assert loaded.media[0].speech_provenance.silence_scale == silence_scale

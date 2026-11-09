"""Exact versioned identities, source binding, and bilingual filter semantics."""
from dataclasses import FrozenInstanceError
from collections import Counter
import pytest
from lumen.voice_catalog import PACKS, get_pack, get_voice, filter_voices
from lumen.tts import TTSConfig, TTSError, _validate_pack_identity


def test_exact_supported_version_counts_and_origins():
    assert set(PACKS) == {'v1.0', 'v1.1', 'melo-zh_en-v1'}
    assert len(get_pack('v1.0').voices) == 53
    pack = get_pack('v1.1')
    assert pack.bank_size == 103 and len(pack.voices) == 103
    assert Counter(v.origin_language for v in pack.voices) == {'en': 3, 'zh': 100}
    assert [v.name for v in pack.voices[:3]] == ['af_maple', 'af_sol', 'bf_vale']
    assert get_voice('v1.1', 10).name == 'zf_008'
    assert get_voice('v1.1', 11).name == 'zf_017'  # Not a fabricated consecutive suffix.
    assert get_voice('v1.1', 57).name == 'zf_099'
    assert get_voice('v1.1', 58).name == 'zm_009'
    assert get_voice('v1.1', 102).name == 'zm_100'
    assert get_voice('v1.0', 45).name == 'zf_xiaobei'
    assert get_pack('v1.0').bank_size == 54  # Undocumented extra slot stays hidden.


@pytest.mark.parametrize('version,sid', [('v1.2', 0), ('v1.1', 103), ('v1.0', 53),
    ('v1.1', -1), ('v1.1', True), ('v1.1', 3.0), (None, 0), ([], 0)])
def test_only_explicit_pack_voice_ids(version, sid):
    with pytest.raises(ValueError): get_voice(version, sid)


def test_text_filters_are_not_origin_exclusive():
    for version in PACKS:
        assert filter_voices(version, text_language='en') == get_pack(version).voices
        assert filter_voices(version, text_language='zh') == get_pack(version).voices
        assert not filter_voices(version, text_language='ja')
    assert len(filter_voices('v1.1', origin='Chinese', text_language='en')) == 100
    assert len(filter_voices('v1.0', origin='US English', text_language='zh')) == 20


def test_favorites_identity_includes_version():
    key = get_voice('v1.0', 0).key
    assert filter_voices('v1.0', favorites={key}, favorites_only=True) == (get_voice('v1.0', 0),)
    assert not filter_voices('v1.1', favorites={key}, favorites_only=True)
    assert get_voice('v1.0', 0).name != get_voice('v1.1', 0).name
    with pytest.raises(FrozenInstanceError): get_voice('v1.1', 0).name = 'invented'


def test_pinned_model_and_voice_fingerprints_bind_catalog():
    for version, pack in PACKS.items():
        assert len(pack.model_sha256) == len(pack.voices_sha256) == 64
        if version == 'melo-zh_en-v1':
            assert pack.archive_sha256 is None
        else:
            assert len(pack.archive_sha256) == 64
        _validate_pack_identity(version, pack.model_sha256, pack.voices_sha256)
        with pytest.raises(TTSError, match='do not match'):
            _validate_pack_identity(version, pack.model_sha256, '0' * 64)
    with pytest.raises(TTSError):
        _validate_pack_identity('v1.1', PACKS['v1.0'].model_sha256, PACKS['v1.0'].voices_sha256)


def test_old_config_positional_compatibility():
    config = TTSConfig('/model', 45, 1.25, 2)
    assert config.pack_version == 'v1.0' and config.voice_id == 45


def test_pack_verification_requires_matching_runtime(tmp_path, monkeypatch):
    from lumen import tts
    pack = PACKS['v1.1']
    monkeypatch.setattr(tts, '_validate', lambda *a: (tmp_path, tmp_path/'never-created.wav'))
    monkeypatch.setattr(tts, '_assets', lambda *a: [('model.onnx',pack.model_sha256),('voices.bin',pack.voices_sha256)])
    monkeypatch.setattr(tts.metadata, 'version', lambda name: tts.RUNTIME_VERSION)
    assert tts.validate_pack(str(tmp_path), 'v1.1') == (str(tmp_path), 'v1.1')
    monkeypatch.setattr(tts.metadata, 'version', lambda name: 'unverified')
    with pytest.raises(TTSError, match='verified with'): tts.validate_pack(str(tmp_path), 'v1.1')
    def missing(name): raise tts.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(tts.metadata, 'version', missing)
    with pytest.raises(TTSError, match='runtime is missing'): tts.validate_pack(str(tmp_path), 'v1.1')


def test_synthesis_uses_shared_resource_gate(tmp_path, monkeypatch):
    from lumen import tts
    from lumen.local_ai import inference_slot, AIResourceBusy, active_task
    calls=[]
    monkeypatch.setattr(tts, '_synthesize', lambda *a: calls.append(active_task()) or 'synthesized')
    assert tts.synthesize('text',TTSConfig('pack'),tmp_path/'voice.wav') == 'synthesized'
    assert calls == ['tts'] and active_task() is None
    with inference_slot('subtitle-assistant'):
        with pytest.raises(AIResourceBusy): tts.synthesize('text',TTSConfig('pack'),tmp_path/'voice.wav')
    assert calls == ['tts']

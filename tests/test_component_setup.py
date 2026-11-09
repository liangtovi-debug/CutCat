import importlib
from pathlib import Path
import subprocess
import threading

import pytest

from lumen import component_setup as setup


def checks(settings=None, **kwargs):
    return {c.key: c for c in setup.diagnose_components(settings or {}, **kwargs).checks}


def test_empty_configuration_is_not_ready(monkeypatch):
    def missing(package):
        raise setup.metadata.PackageNotFoundError(package)
    monkeypatch.setattr(setup.metadata, 'version', missing)
    report = setup.diagnose_components({})
    assert report.inference_tested is False
    result = {c.key: c for c in report.checks}
    assert result['asr_model'].status == 'unset'
    assert result['tts_model_dir'].status == 'unset'
    assert result['onnxruntime'].status == 'unavailable'


def test_no_native_import_or_execution(monkeypatch, tmp_path):
    import builtins
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert name.split('.')[0] not in {'sherpa_onnx', 'onnxruntime', 'tokenizers', 'numpy'}
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('executed runtime'))
    import socket
    import urllib.request
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('network request'))
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: pytest.fail('download request'))
    monkeypatch.setattr(setup.metadata, 'version', lambda name: {'sherpa-onnx': '1.12.26', 'sherpa-onnx-core': '1.12.26', 'onnxruntime': '1.22.1', 'tokenizers': '0.21.4'}[name])
    monkeypatch.setattr(setup.util, 'find_spec', lambda name: object())
    runtime = tmp_path / 'runtime'
    runtime.write_text('#!/bin/sh\ntouch NEVER_EXECUTE\n')
    runtime.chmod(0o700)
    result = checks({'asr_runtime': str(runtime)})
    assert result['asr_runtime'].status == 'metadata_available'
    assert result['onnxruntime'].status == 'metadata_available'
    assert not (tmp_path / 'NEVER_EXECUTE').exists()


def test_path_states_and_gguf(tmp_path):
    data = tmp_path / 'model'
    data.write_bytes(b'bad model')
    result = checks({'asr_runtime': str(tmp_path / 'missing'), 'asr_model': str(tmp_path), 'assistant_model': str(data)})
    assert result['asr_runtime'].status == 'missing'
    assert result['asr_model'].status == 'invalid'
    assert result['assistant_model'].status == 'invalid'
    data.write_bytes(b'GGUFnot necessarily a compatible model')
    assert checks({'assistant_model': str(data)})['assistant_model'].status == 'metadata_available'


def test_symlink_and_fifo_rejected(tmp_path):
    import os
    data = tmp_path / 'data'
    data.write_bytes(b'file')
    link = tmp_path / 'link'
    try:
        link.symlink_to(data)
    except OSError:
        pytest.skip('symlinks unavailable')
    assert checks({'asr_model': str(link)})['asr_model'].status == 'invalid'
    if hasattr(os, 'mkfifo'):
        fifo = tmp_path / 'fifo'
        os.mkfifo(fifo)
        assert checks({'asr_model': str(fifo)})['asr_model'].status == 'invalid'


def test_hashes_are_opt_in_and_pinned(tmp_path, monkeypatch):
    path = tmp_path / 'model.onnx'
    path.write_bytes(b'model')
    def no_hash(*args, **kwargs):
        pytest.fail('metadata scan hashed content')
    with monkeypatch.context() as m:
        m.setattr(setup.hashlib, 'sha256', no_hash)
        result = checks({'search_model': str(path)})['search_model']
        assert result.status == 'metadata_available'
        assert result.sha256 == ''
    assert checks({'search_model': str(path)}, verify_hashes=True)['search_model'].status == 'invalid'
    monkeypatch.setattr(setup, 'SEARCH_MODEL_SHA256', setup.hashlib.sha256(b'model').hexdigest())
    result = checks({'search_model': str(path)}, verify_hashes=True)['search_model']
    assert result.status == 'hash_verified'
    assert 'inference has not been tested' in result.message


def test_cancellation(tmp_path):
    event = threading.Event()
    event.set()
    with pytest.raises(setup.DiagnosticCancelled):
        setup.diagnose_components({}, cancel=event)
    path = tmp_path / 'model'
    path.write_bytes(b'a' * (2 * 1024**2))
    class LaterCancellation:
        count = 0
        def is_set(self):
            self.count += 1
            return self.count >= 3
    with pytest.raises(setup.DiagnosticCancelled):
        setup._read_file(path, 3 * 1024**2, LaterCancellation(), True)


def test_dependency_versions_and_frozen_guidance(monkeypatch):
    monkeypatch.setattr(setup.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(setup.metadata, 'version', lambda name: '0.0')
    monkeypatch.setattr(setup.util, 'find_spec', lambda name: object())
    check = checks()['onnxruntime']
    assert check.status == 'invalid'
    assert 'another Python does not modify' in check.recommendation
    monkeypatch.setattr(setup.util, 'find_spec', lambda name: None)
    assert checks()['onnxruntime'].status == 'unavailable'


def test_tts_pack_reports_required_assets(tmp_path):
    result = checks({'tts_model_dir': str(tmp_path), 'tts_pack_version': 'v1.1'})
    assert result['model.onnx'].status == 'missing'
    assert result['espeak-ng-data'].status == 'missing'
    assert checks({'tts_pack_version': 'unknown'})['tts_pack_version'].status == 'invalid'


def test_preferences_roundtrip_no_extra_fields(tmp_path):
    path = tmp_path / 'config' / 'components.json'
    assert setup.load_preferences(path) == {}
    setup.save_preferences(path, {'asr_model': '/local/model', 'secret': 'discard'})
    assert setup.load_preferences(path)['asr_model'] == '/local/model'
    assert 'secret' not in path.read_text()
    setup.save_preferences(path, {'asr_model': '模' * 1500})
    assert setup.load_preferences(path)['asr_model'] == '模' * 1500
    with pytest.raises(ValueError):
        setup.save_preferences(path, {'asr_model': 'a' * 4097})
    path.write_bytes(b'{}' * setup.MAX_PREFERENCES_BYTES)
    with pytest.raises(ValueError):
        setup.load_preferences(path)


def test_preference_link_rejected(tmp_path):
    original = tmp_path / 'original'
    original.write_text('original')
    link = tmp_path / 'linked'
    try:
        link.symlink_to(original)
    except OSError:
        pytest.skip('symlinks unavailable')
    with pytest.raises(ValueError):
        setup.save_preferences(link, {})
    assert original.read_text() == 'original'


@pytest.mark.parametrize('path', ['https://example.com/model', '//server/model', r'\\server\model', 'file:///tmp/model'])
def test_remote_paths_rejected_without_io(path, monkeypatch):
    monkeypatch.setattr(Path, 'lstat', lambda *args, **kwargs: pytest.fail('remote filesystem access'))
    result = checks({'asr_model': path})['asr_model']
    assert result.status == 'invalid'
    assert 'network paths' in result.message


def test_deeply_nested_preferences_report_a_bounded_format_error(tmp_path):
    path = tmp_path / 'preferences.json'
    path.write_text('[' * 2000 + '0' + ']' * 2000)
    with pytest.raises(ValueError, match='Invalid component preferences format'):
        setup.load_preferences(path)

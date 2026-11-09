"""Fail-closed runtime receipts, packaging and real shaping capability."""
import hashlib
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest
from PIL import features
from lumen import caption_runtime as runtime
from scripts import build_app


@pytest.fixture
def payload(tmp_path, monkeypatch):
    source = b'test source archive identity'
    monkeypatch.setattr(runtime, 'SOURCE_SHA256', hashlib.sha256(source).hexdigest())
    binary = bytearray(128)
    binary[:2] = b'MZ'
    struct.pack_into('<I', binary, 0x3c, 64)
    binary[64:68] = b'PE\0\0'
    struct.pack_into('<H', binary, 68, 0x8664)
    files = {'fribidi.dll': bytes(binary), 'COPYING': b'GNU LESSER GENERAL PUBLIC LICENSE',
             'fribidi-source.tar.xz': source, 'BUILDING.txt': b'Build and replace shared library'}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    receipt = {'version': runtime.VERSION, 'source_sha256': runtime.SOURCE_SHA256,
               'architecture': 'x86_64', 'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    (tmp_path / 'receipt.json').write_text(json.dumps(receipt))
    return tmp_path


def test_verified_payload_and_packaging_preserve_dll_source_license_and_receipt(payload, monkeypatch):
    runtime.verify_windows_payload(payload)
    monkeypatch.setattr(runtime, 'runtime_directory', lambda: payload)
    monkeypatch.setattr(runtime, 'require_capability', lambda: {'raqm': 'verified separately'})
    args = build_app.caption_bundle_args('win32')
    assert args[:2] == ['--add-binary', str(payload / 'fribidi.dll') + ';lumen/assets/native/fribidi']
    assert args.count('--add-data') == 4
    for name in ('receipt.json', 'COPYING', 'fribidi-source.tar.xz', 'BUILDING.txt'):
        assert str(payload / name) + ';lumen/assets/native/fribidi' in args


@pytest.mark.parametrize('name', ['fribidi.dll', 'COPYING', 'fribidi-source.tar.xz', 'BUILDING.txt'])
def test_tampered_or_missing_payload_is_rejected(payload, name):
    path = payload / name
    path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(runtime.CaptionRuntimeError):
        runtime.verify_windows_payload(payload)
    path.unlink()
    with pytest.raises(runtime.CaptionRuntimeError):
        runtime.verify_windows_payload(payload)


def test_wrong_machine_rejected_even_with_matching_digest(payload):
    path = payload / 'fribidi.dll'
    binary = bytearray(path.read_bytes())
    struct.pack_into('<H', binary, 68, 0x14c)
    path.write_bytes(binary)
    receipt = json.loads((payload / 'receipt.json').read_text())
    receipt['files']['fribidi.dll'] = runtime.digest(path)
    (payload / 'receipt.json').write_text(json.dumps(receipt))
    with pytest.raises(runtime.CaptionRuntimeError):
        runtime.verify_windows_payload(payload)


def test_wrong_source_rejected_even_with_matching_file_receipt(payload):
    path = payload / 'fribidi-source.tar.xz'
    path.write_bytes(b'other source')
    receipt = json.loads((payload / 'receipt.json').read_text())
    receipt['files'][path.name] = runtime.digest(path)
    (payload / 'receipt.json').write_text(json.dumps(receipt))
    with pytest.raises(runtime.CaptionRuntimeError):
        runtime.verify_windows_payload(payload)


def test_frozen_runtime_ignores_development_override(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'sys', SimpleNamespace(frozen=True, _MEIPASS=str(tmp_path)))
    monkeypatch.setenv('LUMEN_FRIBIDI_DIR', '/unexpected')
    assert runtime.runtime_directory() == tmp_path / 'lumen/assets/native/fribidi'


def test_real_shaping_capability():
    assert runtime.require_capability()['raqm']


def test_missing_raqm_never_silently_uses_basic(monkeypatch):
    monkeypatch.setattr(features, 'check_feature', lambda name: False)
    with pytest.raises(runtime.CaptionRuntimeError, match='no BASIC fallback'):
        runtime.require_capability()


def test_preflight_writes_failure_report_without_claiming_capability(tmp_path, monkeypatch):
    import subprocess
    from scripts import provision_caption_runtime as provision
    report = tmp_path / 'report.json'
    monkeypatch.setattr(provision.sys, 'argv', ['probe', '--report', str(report)])
    monkeypatch.setattr(provision.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess([], 0, '{"raqm":false,"fribidi":null}', ''))
    def reject():
        raise runtime.CaptionRuntimeError('RAQM unavailable')
    monkeypatch.setattr(provision, 'require_capability', reject)
    # Exercise the capability failure independently of host DLL availability.
    monkeypatch.setattr(provision, 'sys', SimpleNamespace(platform='linux', executable='python'))
    with pytest.raises(runtime.CaptionRuntimeError):
        provision.main()
    result = json.loads(report.read_text())
    assert result['status'] == 'failed' and result['before']['raqm'] is False
    assert result['error_type'] == 'CaptionRuntimeError' and 'after' not in result


def test_unverified_download_cannot_reach_native_compilation(tmp_path, monkeypatch):
    import io
    from scripts import provision_caption_runtime as provision
    class Response(io.BytesIO):
        def geturl(self): return provision.URL
    monkeypatch.setattr(provision, 'sys', SimpleNamespace(platform='win32', executable='python'))
    monkeypatch.setattr(provision.platform, 'machine', lambda: 'AMD64')
    monkeypatch.setattr(provision.importlib.metadata, 'version', provision.TOOLS.__getitem__)
    monkeypatch.setattr(provision.urllib.request, 'urlopen', lambda *a, **k: Response(b'not the official archive'))
    monkeypatch.setattr(provision.subprocess, 'run', lambda *a, **k: pytest.fail('Unverified source must not execute'))
    with pytest.raises(RuntimeError, match='SHA256 mismatch'):
        provision.build(tmp_path / 'runtime')
    assert not (tmp_path / 'runtime').exists()


def test_application_checks_capability_before_opening_editor(monkeypatch):
    from PySide6 import QtWidgets
    from lumen import main, ui
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(QtWidgets, 'QApplication', lambda args: app)
    messages = []
    monkeypatch.setattr(QtWidgets.QMessageBox, 'critical', lambda *args: messages.append(args[-1]))
    monkeypatch.setattr(ui, 'MainWindow', lambda: pytest.fail('Must not open editor without required shaping'))
    def reject(): raise runtime.CaptionRuntimeError('RAQM unavailable')
    monkeypatch.setattr(runtime, 'require_capability', reject)
    assert main.main(['cutcat']) == 2
    assert messages == ['RAQM unavailable']


def test_fresh_pytest_initializes_before_pillow_test_module_import(tmp_path):
    import os
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    hook = (root / 'tests/conftest.py').read_text()
    hook += '''
from lumen import caption_runtime as _runtime
_original_initialize = _runtime.initialize
def _record_initialize():
    _original_initialize()
    os.environ['LUMEN_TEST_RUNTIME_INITIALIZED'] = 'yes'
_runtime.initialize = _record_initialize
'''
    (tmp_path / 'conftest.py').write_text(hook)
    (tmp_path / 'test_import_order.py').write_text('''
import os
assert os.environ.get('LUMEN_TEST_RUNTIME_INITIALIZED') == 'yes'
from PIL import ImageFont, features

def test_actual_shaper_available():
    assert features.check_feature('raqm')
''')
    environment = dict(os.environ)
    environment.pop('LUMEN_TEST_RUNTIME_INITIALIZED', None)
    environment['PYTHONPATH'] = str(root) + os.pathsep + environment.get('PYTHONPATH', '')
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(tmp_path / 'test_import_order.py')],
                            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '1 passed' in result.stdout


def test_initial_probe_failure_also_leaves_diagnostic_receipt(tmp_path, monkeypatch):
    import subprocess
    from scripts import provision_caption_runtime as provision
    report = tmp_path / 'probe-failure.json'
    monkeypatch.setattr(provision.sys, 'argv', ['probe', '--report', str(report)])
    def fail(*args, **kwargs): raise subprocess.CalledProcessError(1, ['python', 'probe'])
    monkeypatch.setattr(provision.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        provision.main()
    result = json.loads(report.read_text())
    assert result['status'] == 'failed' and result['error_type'] == 'CalledProcessError'
    assert 'before' not in result and 'after' not in result

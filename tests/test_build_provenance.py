"""Bind frozen smoke to the source actually packaged, not a later checkout."""
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from lumen import build_provenance as provenance
from lumen import self_test
from scripts import build_app


def identity():
    return {'schema': 1, 'commit': 'a' * 40, 'tree': 'b' * 40,
            'source_sha256': 'c' * 64, 'tracked_files': 43, 'clean': True}


def test_source_identity_is_strict_owned_and_path_free():
    original = identity()
    actual = provenance.validate_source_provenance(original)
    assert actual == original and actual is not original
    original['commit'] = 'd' * 40
    assert actual['commit'] == 'a' * 40
    assert set(actual) == {'schema', 'commit', 'tree', 'source_sha256', 'tracked_files', 'clean'}


@pytest.mark.parametrize('field,value', [
    ('schema', True), ('schema', 1.), ('schema', 2),
    ('commit', 'a' * 39), ('commit', 'A' * 40), ('commit', 1),
    ('tree', 'g' * 40), ('tree', '../source'), ('tree', None),
    ('source_sha256', 'c' * 63), ('source_sha256', 'c' * 64 + '\n'),
    ('tracked_files', True), ('tracked_files', 0), ('tracked_files', -1),
    ('tracked_files', 43.), ('tracked_files', '43'), ('clean', False), ('clean', 1),
])
def test_invalid_source_identity_fields_rejected(field, value):
    data = identity(); data[field] = value
    with pytest.raises(ValueError):
        provenance.validate_source_provenance(data)


@pytest.mark.parametrize('data', [None, [], 'identity', {}, {'schema': 1}])
def test_nonobject_or_incomplete_identity_rejected(data):
    with pytest.raises(ValueError):
        provenance.validate_source_provenance(data)


def test_unknown_fields_including_absolute_paths_are_rejected():
    data = identity(); data['root'] = '/private/source'
    with pytest.raises(ValueError, match='exactly'):
        provenance.validate_source_provenance(data)


@pytest.mark.parametrize('raw', [b'', b'[]', b'{broken', b'\xff', b' ' * 8193,
    b'{"schema":1,"schema":1}', b'{"commit":"a","commit":"b"}'],
    ids=['empty', 'array', 'broken-json', 'invalid-utf8', 'oversized', 'duplicate-schema', 'duplicate-commit'])
def test_corrupt_or_ambiguous_bundle_is_not_accepted(tmp_path, raw):
    path = tmp_path / 'build-provenance.json'; path.write_bytes(raw)
    with pytest.raises(ValueError):
        provenance.read_build_provenance(path)


def test_missing_bundle_fails_closed(tmp_path):
    with pytest.raises(ValueError, match='missing or unreadable'):
        provenance.read_build_provenance(tmp_path / 'missing.json')


def test_runtime_frozen_bundle_identity_and_unfrozen_null(tmp_path, monkeypatch):
    path = tmp_path / 'build-provenance.json'
    monkeypatch.setattr(provenance, '_BUNDLED_PROVENANCE', path)
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    assert provenance.built_source() is None  # No bundle required by source runs.
    path.write_text(json.dumps(identity()))
    assert provenance.built_source() is None  # Even a nearby file grants no native claim.
    monkeypatch.setattr(sys, 'frozen', True)
    assert provenance.built_source() == identity()
    path.write_text('{"schema": 0}')
    with pytest.raises(ValueError):
        provenance.built_source()


@pytest.mark.parametrize('contents', [None, '{}'])
def test_frozen_selftest_records_provenance_failure_before_qt_import(tmp_path, monkeypatch, contents):
    import builtins
    path = tmp_path / 'build-provenance.json'
    if contents is not None:
        path.write_text(contents)
    monkeypatch.setattr(provenance, '_BUNDLED_PROVENANCE', path)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    original = builtins.__import__
    def reject_qt(name, *args, **kwargs):
        if name.startswith('PySide6'):
            pytest.fail('Invalid frozen provenance must fail before Qt imports')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', reject_qt)
    with pytest.raises(provenance.BuildProvenanceError):
        self_test.run(tmp_path / 'smoke')
    report = json.loads((tmp_path / 'smoke' / 'failure.json').read_text())
    assert report['passed'] is False and report['frozen'] is True
    assert report['exception_type'] == 'BuildProvenanceError'
    assert not (tmp_path / 'smoke' / 'result.json').exists()


@pytest.fixture
def native_build(tmp_path, monkeypatch):
    qt = pytest.importorskip('PySide6.QtCore')
    icons = tmp_path / 'lumen' / 'assets' / 'app'; icons.mkdir(parents=True)
    for filename in ('cutcat.ico', 'cutcat.icns'):
        (icons / filename).write_bytes(b'native icon fixture')
    qt_directory = tmp_path / 'qt'; qt_directory.mkdir()
    (qt_directory / 'qtbase_zh_CN.qm').write_bytes(b'translation fixture')
    versions = {'PyInstaller': '6.12.0', 'PySide6': '6.8.3', 'pyinstaller-hooks-contrib': '2025.1',
                'numpy': '2.2.4', 'fonttools': '4.61.1', 'Pillow': '13.0.0', 'regex': '2026.9.29'}
    monkeypatch.setattr(build_app, 'ROOT', tmp_path)
    monkeypatch.setattr(build_app, 'caption_bundle_args', lambda platform: [])
    monkeypatch.setattr(build_app.importlib.metadata, 'version', versions.__getitem__)
    monkeypatch.setattr(build_app, 'sys', SimpleNamespace(
        platform='win32', version_info=(3, 12), executable=sys.executable))
    monkeypatch.setattr(sys, 'argv', ['build_app.py'])
    monkeypatch.setattr(qt, 'QLibraryInfo', SimpleNamespace(
        path=lambda _: str(qt_directory), LibraryPath=SimpleNamespace(TranslationsPath='translations')))
    return tmp_path


@pytest.mark.parametrize('platform,separator', [('win32', ';'), ('darwin', ':')])
def test_build_packages_generated_provenance_and_rechecks_source(native_build, monkeypatch, platform, separator):
    root = native_build
    build_app.sys.platform = platform
    calls, commands = [], []
    def capture(path):
        assert path == root
        calls.append('capture')
        return identity()
    def build(command, **kwargs):
        calls.append('build'); commands.append(command)
        assert kwargs == {'cwd': root, 'check': True}
        assert json.loads((root / 'build/launcher/build-provenance.json').read_text()) == identity()
    monkeypatch.setattr(build_app, 'capture_source_provenance', capture)
    monkeypatch.setattr(build_app.subprocess, 'run', build)
    assert build_app.main() == 0
    assert calls == ['capture', 'build', 'capture']
    expected = str(root / 'build/launcher/build-provenance.json') + separator + 'lumen/assets'
    assert expected in commands[0]
    assert not (root / 'lumen/assets/build-provenance.json').exists()


def test_dirty_or_unavailable_source_cannot_start_build(native_build, monkeypatch):
    def dirty(root):
        raise ValueError('Dirty source refused')
    monkeypatch.setattr(build_app, 'capture_source_provenance', dirty)
    monkeypatch.setattr(build_app.subprocess, 'run', lambda *a, **k: pytest.fail('Must not build dirty source'))
    with pytest.raises(ValueError, match='Dirty source refused'):
        build_app.main()
    assert not (native_build / 'build').exists()


@pytest.mark.parametrize('failure', ['changed', 'dirty'])
def test_source_change_during_pyinstaller_cannot_claim_success(native_build, monkeypatch, capsys, failure):
    calls = []
    def capture(root):
        calls.append(root)
        if len(calls) == 1:
            return identity()
        if failure == 'dirty':
            raise ValueError('Source became dirty')
        return {**identity(), 'source_sha256': 'd' * 64}
    monkeypatch.setattr(build_app, 'capture_source_provenance', capture)
    monkeypatch.setattr(build_app.subprocess, 'run', lambda *a, **k: None)
    with pytest.raises((ValueError, RuntimeError), match='Source'):
        build_app.main()
    assert len(calls) == 2
    assert 'Draft created' not in capsys.readouterr().out
    assert json.loads((native_build / 'build/launcher/build-provenance.json').read_text()) == identity()


def test_build_validates_capture_schema_before_writing(native_build, monkeypatch):
    monkeypatch.setattr(build_app, 'capture_source_provenance', lambda root: {**identity(), 'clean': False})
    with pytest.raises(ValueError, match='clean'):
        build_app.main()
    assert not (native_build / 'build').exists()


def test_failed_packager_retains_original_nonzero_error(native_build, monkeypatch):
    failure = subprocess.CalledProcessError(23, ['PyInstaller'])
    monkeypatch.setattr(build_app, 'capture_source_provenance', lambda root: identity())
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(build_app.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError) as actual:
        build_app.main()
    assert actual.value is failure


def test_build_script_direct_entry_imports_without_root_pythonpath(tmp_path):
    # Isolated Python omits PYTHONPATH/current-directory assistance. --help must
    # still load both developer and runtime provenance helpers without building.
    result = subprocess.run([sys.executable, '-I', str(build_app.ROOT / 'scripts/build_app.py'), '--help'],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert '--console' in result.stdout

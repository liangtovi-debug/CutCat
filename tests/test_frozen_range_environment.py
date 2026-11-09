"""The accepted oracle keeps real ZIP provenance on POSIX and Windows paths."""
import inspect
import ntpath
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
import zipimport

import pytest

from scripts.benchmark_range_preview import frozen_archive, verify_frozen_imports


def module_snapshot(archive, name):
    loader = zipimport.zipimporter(str(archive) + ('/lumen' if name != 'lumen' else ''))
    filename = loader.get_filename(name)
    return SimpleNamespace(__loader__=loader, __file__=filename,
                           __spec__=SimpleNamespace(origin=filename))


def test_actual_isolated_zip_imports_pass_without_candidate_modules(tmp_path):
    archive = frozen_archive()
    code = inspect.getsource(verify_frozen_imports) + '\nimport sys\nfrom lumen import model\n'
    code += 'print(len(verify_frozen_imports(sys.argv[1], sys.modules)))\n'
    result = subprocess.run([sys.executable, '-c', code, str(archive)], cwd=tmp_path,
                            env=dict(os.environ, PYTHONPATH=str(archive)),
                            check=True, capture_output=True, text=True, timeout=30)
    assert int(result.stdout.strip()) > 1


def test_windows_zip_separators_are_normalized_without_prefix_acceptance():
    archive = frozen_archive()
    module = module_snapshot(archive, 'lumen')
    module.__file__ = module.__file__.replace('/', '\\')
    module.__spec__.origin = module.__file__
    windows_os = SimpleNamespace(path=ntpath, fspath=os.fspath)
    # Exercise the actual validator with Windows path rules, on any host.
    with patch.dict(sys.modules, {'os': windows_os}):
        assert verify_frozen_imports(archive, {'lumen': module}) == {'lumen': module.__file__}
    module.__file__ += '.candidate'
    module.__spec__.origin = module.__file__
    with patch.dict(sys.modules, {'os': windows_os}):
        with pytest.raises(AssertionError, match='origin mismatch'):
            verify_frozen_imports(archive, {'lumen': module})


@pytest.mark.parametrize('field', ['__file__', '__spec__', '__loader__'])
def test_candidate_or_spoofed_module_origin_is_rejected(field):
    archive = frozen_archive()
    module = module_snapshot(archive, 'lumen')
    if field == '__file__': module.__file__ = str(Path('lumen/__init__.py').resolve())
    elif field == '__spec__': module.__spec__ = SimpleNamespace(origin='candidate/lumen/__init__.py')
    else: module.__loader__ = SimpleNamespace(archive=str(archive))
    with pytest.raises(AssertionError):
        verify_frozen_imports(archive, {'lumen': module})


def test_other_real_zip_archive_is_rejected(tmp_path):
    archive = frozen_archive()
    other = tmp_path / 'same-bytes-different-archive.zip'
    other.write_bytes(archive.read_bytes())
    with pytest.raises(AssertionError, match='another archive'):
        verify_frozen_imports(archive, {'lumen': module_snapshot(other, 'lumen')})


def test_absent_application_modules_are_rejected():
    with pytest.raises(AssertionError, match='absent'):
        verify_frozen_imports(frozen_archive(), {})


def test_optimized_python_cannot_disable_integrity_checks(tmp_path):
    from scripts import benchmark_range_preview as oracle
    from scripts.provision_frozen_reference import verify_manifest
    for function, arguments in [(oracle.frozen_archive, ''),
                                (oracle.verify_frozen_imports, 'None, {}'),
                                (oracle.initialize_reference_runtime, 'None, None, {}'),
                                (verify_manifest, '')]:
        code = inspect.getsource(function) + f'\n{function.__name__}({arguments})'
        result = subprocess.run([sys.executable, '-O', '-c', code], cwd=tmp_path,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode != 0
        assert 'require non-optimized Python' in result.stderr


@pytest.mark.parametrize('mutation', ['content', 'replacement'])
def test_tool_lease_rejects_changes_even_with_same_size_and_mtime(tmp_path, mutation):
    from scripts.benchmark_range_preview import reference_tool_lease, verify_reference_tool_leases
    import hashlib
    executable = tmp_path / 'tool'
    executable.write_bytes(b'original')
    with reference_tool_lease(executable) as stream:
        before = os.fstat(stream.fileno())
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        leases = [(executable, stream, digest, before)]
        verify_reference_tool_leases(leases)
        if sys.platform == 'win32':
            with pytest.raises(OSError):
                if mutation == 'content': executable.write_bytes(b'modified')
                else: executable.unlink()
            verify_reference_tool_leases(leases)
        else:
            if mutation == 'replacement': executable.unlink()
            executable.write_bytes(b'modified')
            os.utime(executable, ns=(before.st_atime_ns, before.st_mtime_ns))
            with pytest.raises(RuntimeError, match='changed'):
                verify_reference_tool_leases(leases)
    assert stream.closed


def test_bootstrap_bytes_are_checked_before_execution(tmp_path):
    from scripts.benchmark_range_preview import initialize_reference_runtime
    marker = tmp_path / 'executed'
    bootstrap = tmp_path / 'bootstrap.py'
    bootstrap.write_text(f'open({str(marker)!r}, "w").close()')
    code = inspect.getsource(initialize_reference_runtime)
    code += f'\ninitialize_reference_runtime({str(bootstrap)!r}, "wrong", {{}})'
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'bootstrap changed' in result.stderr
    assert not marker.exists()


def test_common_oracle_tools_are_child_only(monkeypatch, tmp_path):
    from scripts.benchmark_range_preview import oracle_child_environment
    monkeypatch.setenv('LUMEN_FFMPEG', '/production/ffmpeg9')
    monkeypatch.setenv('LUMEN_FFPROBE', '/production/ffprobe9')
    monkeypatch.setenv('LUMEN_REFERENCE_FFMPEG', '/oracle/ffmpeg7')
    monkeypatch.setenv('LUMEN_REFERENCE_FFPROBE', '/oracle/ffprobe7')
    monkeypatch.setenv('PYTHONOPTIMIZE', '2')
    before = dict(os.environ)
    for pythonpath in (frozen_archive(), Path(__file__).resolve().parents[1]):
        child = oracle_child_environment(tmp_path, pythonpath)
        assert child['LUMEN_FFMPEG'] == '/oracle/ffmpeg7'
        assert child['LUMEN_FFPROBE'] == '/oracle/ffprobe7'
        assert child['PYTHONPATH'] == str(pythonpath)
        assert 'PYTHONOPTIMIZE' not in child
    assert dict(os.environ) == before


def test_windows_oracle_never_falls_back_to_production_tools(monkeypatch, tmp_path):
    from scripts import benchmark_range_preview as oracle
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.delenv('LUMEN_REFERENCE_FFMPEG', raising=False)
    monkeypatch.setenv('LUMEN_FFMPEG', 'production9.exe')
    with pytest.raises(RuntimeError, match='Provision'):
        oracle.oracle_child_environment(tmp_path, tmp_path)

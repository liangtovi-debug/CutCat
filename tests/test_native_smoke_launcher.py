"""Synthetic launch contracts only, never proof of a real frozen package."""
import json
from pathlib import Path
import sys

import pytest

from scripts import run_native_smoke as launcher


def fixture(tmp_path):
    executable = tmp_path / 'frozen-app'
    executable.write_bytes(b'synthetic launcher fixture')
    return executable, tmp_path / 'native-smoke', tmp_path / 'native-smoke-launch'


@pytest.mark.parametrize('report', [
    {'passed': True, 'frozen': True, 'platform': sys.platform},
    {'passed': False, 'frozen': True, 'platform': sys.platform},
    {'passed': True, 'frozen': False, 'platform': sys.platform},
    {'passed': True, 'frozen': True, 'platform': 'wrong-platform'},
    {'passed': 'true', 'frozen': True, 'platform': sys.platform},
    {}, None,
])
def test_success_requires_matching_frozen_runtime_report(tmp_path, monkeypatch, report):
    executable, output, diagnostics = fixture(tmp_path)
    def fake(command, **kwargs):
        assert command == [str(executable), '--lumen-self-test', str(output)]
        assert not output.exists(), 'The app must create its own output directory exclusively'
        assert diagnostics.exists()
        output.mkdir()
        if report is not None:
            (output / 'result.json').write_text(json.dumps(report))
        Path(kwargs['log_path']).write_text('synthetic launch only')
        return {'returncode': 0, 'timed_out': False, 'elapsed_seconds': 0.1}
    monkeypatch.setattr(launcher, 'run_process', fake)
    expected = report == {'passed': True, 'frozen': True, 'platform': sys.platform}
    assert launcher.run_smoke(executable, output, diagnostics) == (0 if expected else 1)
    status = json.loads((diagnostics / 'process-result.json').read_text())
    assert status['success'] is expected
    assert (diagnostics / 'stdout-stderr.log').exists()


@pytest.mark.parametrize('failure', ['nonzero', 'timeout', 'launch_error'])
def test_failure_before_app_result_retains_process_status(tmp_path, monkeypatch, failure):
    executable, output, diagnostics = fixture(tmp_path)
    def fake(*args, **kwargs):
        if failure == 'launch_error':
            raise OSError('synthetic launch error')
        return {'returncode': 9, 'timed_out': failure == 'timeout', 'elapsed_seconds': 1}
    monkeypatch.setattr(launcher, 'run_process', fake)
    assert launcher.run_smoke(executable, output, diagnostics) == 1
    status = json.loads((diagnostics / 'process-result.json').read_text())
    assert status['success'] is False and status['error']
    assert not output.exists()


@pytest.mark.parametrize('existing', ['app', 'launch'])
def test_does_not_replace_previous_smoke_evidence(tmp_path, existing):
    executable, output, diagnostics = fixture(tmp_path)
    path = output if existing == 'app' else diagnostics
    path.mkdir()
    (path / 'existing').write_text('keep')
    with pytest.raises(FileExistsError):
        launcher.run_smoke(executable, output, diagnostics)
    assert (path / 'existing').read_text() == 'keep'


@pytest.mark.parametrize('nested', ['same', 'launch_inside_app', 'app_inside_launch'])
def test_diagnostic_directory_must_not_conflict_with_app_output(tmp_path, nested):
    executable, output, diagnostics = fixture(tmp_path)
    if nested == 'same':
        diagnostics = output
    elif nested == 'launch_inside_app':
        diagnostics = output / 'launch'
    else:
        output = diagnostics / 'app'
    with pytest.raises(ValueError):
        launcher.run_smoke(executable, output, diagnostics)
    assert not output.exists() and not diagnostics.exists()


@pytest.mark.parametrize('timeout', [0, -1, float('inf'), float('nan')])
def test_rejects_invalid_timeout(tmp_path, timeout):
    executable, output, diagnostics = fixture(tmp_path)
    with pytest.raises(ValueError):
        launcher.run_smoke(executable, output, diagnostics, timeout=timeout)
    assert not diagnostics.exists()


@pytest.mark.parametrize('mutation', [None, 'archive', 'executable'])
def test_launch_binds_archive_and_executable_and_rejects_mutation(tmp_path, monkeypatch, mutation):
    executable, output, diagnostics = fixture(tmp_path)
    archive = tmp_path / 'native.zip'
    archive.write_bytes(b'synthetic archive before launch')
    original_archive = launcher.file_fingerprint(archive)
    original_executable = launcher.file_fingerprint(executable)
    def fake(*args, **kwargs):
        output.mkdir()
        (output / 'result.json').write_text(json.dumps({'passed': True, 'frozen': True, 'platform': sys.platform}))
        if mutation:
            (archive if mutation == 'archive' else executable).write_bytes(b'mutated during smoke')
        return {'returncode': 0, 'timed_out': False, 'elapsed_seconds': 0.1}
    monkeypatch.setattr(launcher, 'run_process', fake)
    assert launcher.run_smoke(executable, output, diagnostics, archive=archive) == (0 if mutation is None else 1)
    status = json.loads((diagnostics / 'process-result.json').read_text())
    assert status['archive'] == original_archive
    assert status['archive_path'] == str(archive)
    assert status['executable_fingerprint'] == original_executable
    assert status['success'] is (mutation is None)
    if mutation:
        assert 'changed during runtime smoke' in status['error']


@pytest.mark.parametrize('missing_or_empty', ['missing', 'empty'])
def test_invalid_archive_fails_before_app_launch(tmp_path, monkeypatch, missing_or_empty):
    executable, output, diagnostics = fixture(tmp_path)
    archive = tmp_path / 'native.zip'
    if missing_or_empty == 'empty':
        archive.touch()
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid archive must prevent launch')
    monkeypatch.setattr(launcher, 'run_process', forbidden)
    assert launcher.run_smoke(executable, output, diagnostics, archive=archive) == 1
    status = json.loads((diagnostics / 'process-result.json').read_text())
    assert not status['success'] and 'launch input' in status['error']
    assert not output.exists()


def test_native_workflow_supplies_each_exact_archive():
    workflow = (Path(__file__).parents[1] / '.github/workflows/desktop-ci.yml').read_text()
    assert '--archive dist/LumenEdit-Draft-Windows.zip --timeout 600' in workflow
    assert '--archive dist/LumenEdit-Draft-macOS.tar.gz --timeout 600' in workflow

"""Exercise the real collection hook in isolated pytest subprocesses."""
from pathlib import Path
import os
import subprocess
import sys

import pytest

from scripts.pytest_node_ids import MAX_NODE_ID_UTF16_UNITS, pytest_collection_modifyitems


@pytest.mark.parametrize('text', ['a' * 1025, '\U0001f408' * 513], ids=['ascii', 'utf16-surrogate-pairs'])
def test_guard_bounds_actual_utf16_units_without_echoing_payload(text):
    from types import SimpleNamespace
    with pytest.raises(pytest.UsageError, match='Add short descriptive ids=') as exc:
        pytest_collection_modifyitems([SimpleNamespace(nodeid='test.py::test_large[' + text + ']')])
    assert len(str(exc.value)) < 500
    assert text not in str(exc.value)
    assert MAX_NODE_ID_UTF16_UNITS == 1024


def run_fixture(tmp_path, explicit):
    test = tmp_path / 'test_payload.py'
    ids = ", ids=['overflow-budget']" if explicit else ''
    test.write_text("import pytest\n@pytest.mark.parametrize('payload', ['x' * 70000]" + ids + ")\ndef test_payload(payload):\n    assert len(payload) == 70000\n    assert payload == 'x' * 70000\n", encoding='utf-8')
    env = dict(os.environ)
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get('PYTHONPATH', '')
    return subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'scripts.pytest_node_ids',
                           '--confcutdir', str(tmp_path), str(test)],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)


def test_huge_automatic_id_is_rejected_before_test_setup(tmp_path):
    result = run_fixture(tmp_path, False)
    assert result.returncode == 4, result.stdout + result.stderr
    assert 'Add short descriptive ids=' in result.stderr
    assert 'environment variable is longer' not in result.stderr
    assert len(result.stderr) < 2000


def test_short_id_keeps_full_large_payload_and_executes_assertions(tmp_path):
    result = run_fixture(tmp_path, True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '1 passed' in result.stdout

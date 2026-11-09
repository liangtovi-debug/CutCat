"""Portable CI execution contracts; synthetic fixtures are not native evidence."""
from __future__ import annotations

from collections import Counter
import copy
import json
import os
import subprocess
from pathlib import Path
import time

import pytest

from scripts import run_bounded_tests as runner


def test_shards_are_stable_exact_once_and_keep_modules_together():
    nodes = [f'tests/test_{name}.py::test_{i}' for name, count in [('a', 3), ('b', 2), ('c', 2), ('d', 8)]
             for i in range(count)]
    plan = runner.make_shards(nodes, 5)
    assert runner.make_shards(nodes, 5) == plan
    assert Counter(node for shard in plan for node in shard) == Counter(nodes)
    for filename in {node.split('::')[0] for node in nodes}:
        assert sum(any(node.startswith(filename + '::') for node in shard) for shard in plan) == 1
    assert all(len(shard) <= 5 or len({node.split('::')[0] for node in shard}) == 1 for shard in plan)
    assert all(shard == sorted(shard, key=nodes.index) for shard in plan)


@pytest.mark.parametrize('nodes', [[], ['a', 'a'], [''], ['a\nb'], ['a\rb'], [None]])
def test_invalid_or_duplicate_collection_fails(nodes):
    with pytest.raises(ValueError):
        runner.make_shards(nodes)


def receipt(nodes):
    return {'schema': 1, 'exitstatus': 0, 'collected': nodes[:], 'started': nodes[:],
            'finished': nodes[:], 'collection_reports': [],
            'reports': [{'nodeid': node, 'when': when, 'outcome': 'passed'}
                        for node in nodes for when in ['setup', 'call', 'teardown']]}


def verify(tmp_path, value, expected=None, **kwargs):
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    return runner.verify_receipt(path, expected or ['a', 'b'], **kwargs)


def test_exact_receipt_validates_every_phase(tmp_path):
    assert verify(tmp_path, receipt(['a', 'b'])) == {'passed': 2}


@pytest.mark.parametrize('mutation', [
    lambda r: r.update(exitstatus=1),
    lambda r: r.update(schema=2),
    lambda r: r['collected'].reverse(),
    lambda r: r['collected'].append('a'),
    lambda r: r['started'].pop(),
    lambda r: r['started'].append('b'),
    lambda r: r['finished'].reverse(),
    lambda r: r['reports'].pop(),
    lambda r: r['reports'].pop(1),
    lambda r: r['reports'].append(copy.deepcopy(r['reports'][0])),
    lambda r: r['reports'][1].update(nodeid='unexpected'),
    lambda r: r['reports'][1].update(when='unknown'),
    lambda r: r['reports'][1].update(outcome='failed'),
    lambda r: r['reports'][2].update(outcome='skipped'),
    lambda r: r['collection_reports'].append({'outcome': 'failed'}),
])
def test_incomplete_or_inconsistent_receipts_fail_closed(tmp_path, mutation):
    value = receipt(['a', 'b'])
    mutation(value)
    with pytest.raises(ValueError):
        verify(tmp_path, value)


@pytest.mark.parametrize('phase,xfail,label', [('setup', False, 'skipped'), ('call', False, 'skipped'),
                                             ('setup', True, 'xfailed'), ('call', True, 'xfailed')])
def test_skips_remain_explicit_and_require_successful_teardown(tmp_path, phase, xfail, label):
    value = receipt(['a'])
    for report in value['reports']:
        if report['when'] == phase:
            report['outcome'] = 'skipped'
            if xfail:
                report['wasxfail'] = 'expected fixture condition'
    if phase == 'setup':
        value['reports'] = [report for report in value['reports'] if report['when'] != 'call']
    assert verify(tmp_path, value, ['a']) == {label: 1}


def test_collect_only_cannot_claim_executed_results(tmp_path):
    value = receipt(['a'])
    with pytest.raises(ValueError):
        verify(tmp_path, value, ['a'], collection=True)
    value.update(started=[], finished=[], reports=[])
    assert verify(tmp_path, value, ['a'], collection=True) == {}


def project(tmp_path, sources):
    root = tmp_path / 'project'
    root.mkdir()
    for name, source in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')
    return root


def execute(tmp_path, sources, **options):
    root = project(tmp_path, sources)
    output = tmp_path / 'results'
    code = runner.execute(root=root, output=output, paths=['tests'], workers=2, target_nodes=2,
                          collection_timeout=30, shard_timeout=30, suite_timeout=60, **options)
    return code, output, json.loads((output / 'summary.json').read_text())


def test_real_subprocesses_preserve_exact_results_and_special_ids(tmp_path, monkeypatch):
    # Hidden parent selection must not silently drop tests in child pytest.
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k impossible_name')
    code, output, summary = execute(tmp_path, {
        'pytest.ini': '[pytest]\ndisable_test_id_escaping_and_forfeit_all_rights_to_community_support = True\n',
        'tests/test_a.py': "import pytest\n@pytest.mark.parametrize('v', [1, 2], ids=['has spaces', '中文'])\ndef test_a(v): assert v > 0\n",
        'tests/test_b.py': "import pytest\ndef test_b(): pytest.skip('explicit skip')\n",
        'tests/test_c.py': "import pytest\n@pytest.mark.xfail(reason='synthetic')\ndef test_c(): assert False\n",
    })
    assert code == 0 and summary['success'] and summary['exact_once']
    assert summary['collected_nodes'] == summary['verified_nodes'] == 4
    assert summary['outcomes'] == {'passed': 2, 'skipped': 1, 'xfailed': 1}
    assert summary['native_validation_claimed'] is False
    plan = json.loads((output / 'plan.json').read_text())
    actual = []
    for folder in sorted(output.glob('shard-*')):
        value = json.loads((folder / 'receipt.json').read_text())
        events = [json.loads(line) for line in (folder / 'events.jsonl').read_text().splitlines()]
        assert [event['nodeid'] for event in events if event['event'] == 'started'] == value['started']
        assert events[-1]['event'] == 'session_finish'
        actual += value['started']
        for report in value['reports']:
            if report['outcome'] == 'skipped' and 'wasxfail' not in report:
                assert 'explicit skip' in report['detail']
    assert Counter(actual) == Counter(plan['collected'])
    assert (output / 'results.xml').is_file()


def test_collection_error_stops_before_launching_shards(tmp_path):
    code, output, summary = execute(tmp_path, {'tests/test_bad.py': 'raise RuntimeError("collection fixture failure")\n'})
    assert code == 1 and not summary['success']
    assert not list(output.glob('shard-*'))
    assert 'collection failed' in summary['error']
    assert (output / 'collection' / 'events.jsonl').is_file()


def test_failed_test_is_not_retried_or_aggregated_as_pass(tmp_path):
    code, output, summary = execute(tmp_path, {'tests/test_bad.py': 'def test_bad(): assert False\n'})
    assert code == 1 and not summary['success']
    value = json.loads((output / 'shard-000' / 'receipt.json').read_text())
    assert value['started'] == ['tests/test_bad.py::test_bad']
    assert any(report['outcome'] == 'failed' for report in value['reports'])
    assert not (output / 'results.xml').exists()


def test_timeout_preserves_started_journal_and_reports_incomplete(tmp_path):
    root = project(tmp_path, {'tests/test_hang.py': 'import time\ndef test_hang(): time.sleep(30)\n'})
    output = tmp_path / 'results'
    start = time.monotonic()
    code = runner.execute(root=root, output=output, paths=['tests'], workers=1,
                          collection_timeout=30, shard_timeout=2, suite_timeout=30)
    assert time.monotonic() - start < 20
    summary = json.loads((output / 'summary.json').read_text())
    assert code == 1 and not summary['success']
    assert summary['completed_shards'][0]['timed_out']
    events = [json.loads(line) for line in (output / 'shard-000' / 'events.jsonl').read_text().splitlines()]
    assert any(event['event'] == 'started' for event in events)
    assert not any(event['event'] == 'finished' for event in events)
    assert not (output / 'results.xml').exists()


def test_whole_suite_budget_limits_collection_too(tmp_path):
    root = project(tmp_path, {'tests/test_slow_import.py': 'import time\ntime.sleep(30)\ndef test_a(): pass\n'})
    output = tmp_path / 'results'
    start = time.monotonic()
    code = runner.execute(root=root, output=output, paths=['tests'], collection_timeout=30,
                          shard_timeout=30, suite_timeout=0.3)
    assert code == 1 and time.monotonic() - start < 20
    assert json.loads((output / 'collection' / 'status.json').read_text())['timed_out']
    assert not list(output.glob('shard-*'))


@pytest.mark.parametrize('options', [{'workers': 0}, {'workers': 5}, {'target_nodes': 0},
                                     {'shard_timeout': 0}, {'suite_timeout': float('inf')},
                                     {'collection_timeout': float('nan')}])
def test_invalid_limits_rejected_before_any_test_process(tmp_path, options):
    with pytest.raises(ValueError):
        runner.execute(root=tmp_path, output=tmp_path / 'out', paths=['tests'], **options)
    assert not (tmp_path / 'out').exists()


def test_existing_evidence_is_never_overwritten(tmp_path):
    output = tmp_path / 'out'
    output.mkdir()
    (output / 'summary.json').write_text('keep this')
    with pytest.raises(FileExistsError):
        runner.execute(root=tmp_path, output=output, paths=['tests'])
    assert (output / 'summary.json').read_text() == 'keep this'


def test_child_environment_isolates_artifact_and_cache_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv('PYTEST_ADDOPTS', '--maxfail=1')
    monkeypatch.setenv('PYTEST_CURRENT_TEST', 'parent test')
    environment = runner.child_environment(tmp_path, tmp_path / 'shard')
    assert 'PYTEST_ADDOPTS' not in environment and 'PYTEST_CURRENT_TEST' not in environment
    assert environment['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'
    assert environment['PYTHONUTF8'] == '1' and environment['PYTHONIOENCODING'] == 'utf-8'
    assert environment['AUTO_FRAME_REVIEW_EVIDENCE'] == str(tmp_path / 'shard' / 'ui')
    assert environment['MORPHOLOGY_EVIDENCE_DIR'] == str(tmp_path / 'shard' / 'morphology')


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process-group signal contract')
def test_timeout_kills_descendants_even_when_leader_exits(tmp_path):
    pidfile = tmp_path / 'child.pid'
    code = ('import subprocess, sys, time; '
            'p = subprocess.Popen([sys.executable, "-c", '
            '"import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"]); '
            f'open({str(pidfile)!r}, "w").write(str(p.pid)); time.sleep(60)')
    result = runner.run_process([runner.sys.executable, '-c', code], root=tmp_path,
                                environment=dict(os.environ), log_path=tmp_path / 'log', timeout=1)
    assert result['timed_out']
    child = int(pidfile.read_text())
    # POSIX kill(pid, 0) also succeeds for zombies. On Linux, a killed zombie
    # is harmless and waits only for the host init to reap it.
    deadline = time.monotonic() + 3
    while True:
        state = subprocess.run(['ps', '-o', 'stat=', '-p', str(child)], capture_output=True,
                               text=True, timeout=2, check=False).stdout.strip()
        if not state or state.startswith('Z'):
            break
        assert time.monotonic() < deadline, f'Descendant still running: {state}'
        time.sleep(0.01)


def test_failure_does_not_launch_more_shards(tmp_path):
    root = project(tmp_path, {'tests/test_a.py': 'def test_a(): assert False\n',
                              'tests/test_b.py': 'def test_b(): pass\n',
                              'tests/test_c.py': 'def test_c(): pass\n'})
    output = tmp_path / 'results'
    code = runner.execute(root=root, output=output, paths=['tests'], workers=1, target_nodes=1,
                          collection_timeout=30, shard_timeout=30, suite_timeout=60)
    summary = json.loads((output / 'summary.json').read_text())
    assert code == 1 and summary['unstarted_shards'] == [1, 2]
    assert len(list(output.glob('shard-*'))) == 1


def test_windows_timeout_uses_bounded_tree_termination(monkeypatch):
    from types import SimpleNamespace
    calls = []
    process = SimpleNamespace(pid=123, poll=lambda: None,
                              kill=lambda: calls.append('kill'),
                              wait=lambda timeout: calls.append(('wait', timeout)))
    monkeypatch.setattr(runner, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(runner.subprocess, 'run', lambda command, **kwargs: calls.append((command, kwargs)))
    runner.terminate_tree(process)
    assert calls[0][0] == ['taskkill', '/PID', '123', '/T', '/F']
    assert calls[0][1]['timeout'] == 15
    assert 'kill' in calls and ('wait', 5) in calls


def test_malformed_receipt_does_not_pass(tmp_path):
    path = tmp_path / 'receipt.json'
    path.write_text('{"schema":')
    with pytest.raises(ValueError):
        runner.verify_receipt(path, ['a'])


def test_no_process_launch_after_exhausted_deadline(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Must not spawn after the budget expired')
    monkeypatch.setattr(runner.subprocess, 'Popen', forbidden)
    result = runner.run_process([], root=tmp_path, environment={}, log_path=tmp_path / 'log', timeout=0)
    assert result['timed_out'] and result['returncode'] is None
    assert not (tmp_path / 'log').exists()


def assert_native_full_budget_contract(source):
    import re
    job_caps = re.findall(r'^    timeout-minutes: (.+)$', source, re.M)
    assert job_caps == ["${{ matrix.os == 'windows-2022' && 200 || 100 }}"]
    lines = source.splitlines()
    title = '      - name: Run exact full regression in bounded local shards'
    assert lines.count(title) == 1
    start = lines.index(title) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith('      - ')), len(lines))
    full = lines[start:end]
    assert [line.strip() for line in full if line.startswith('        timeout-minutes:')] == [
        "timeout-minutes: ${{ runner.os == 'Windows' && 152 || 62 }}"]
    # Exact command equality rejects cross-platform leakage, added retries,
    # accidental ordinary-shard inflation and shell failure masking alike.
    expected = (
        'run: python scripts/run_bounded_tests.py --output build/test-shards --workers 2 '
        '--target-nodes 200 --collection-timeout 180 --shard-timeout 900 '
        "--suite-timeout ${{ runner.os == 'Windows' && 9000 || 3600 }} "
        "${{ runner.os == 'Windows' && '--cost-profile scripts/windows_full_costs.json "
        '--target-seconds 450 --isolated-module-timeout '
        "tests/test_final_frame_preview_oracle.py=2100' || '' }}")
    assert [line.strip() for line in full if line.startswith('        run:')] == [expected]


def test_native_workflow_uses_two_bounded_workers_and_preserves_diagnostics():
    # This is a wiring regression check, not a replacement for GitHub's YAML
    # parser or evidence that native runners have executed successfully.
    source = (Path(__file__).parents[1] / '.github/workflows/desktop-ci.yml').read_text()
    assert '  workflow_dispatch:\n' in source
    assert '  push:\n  pull_request:' in source
    assert 'max-parallel: 1' in source
    assert_native_full_budget_contract(source)
    assert 'python -m pytest' not in source
    assert 'python scripts/write_native_manifest.py' in source
    assert 'build/test-shards/**/*.jsonl' in source
    assert 'build/test-shards/**/*.json' in source
    assert 'build/native-smoke-launch/' in source
    assert 'build/pyinstaller/**/warn-*.txt' in source
    assert 'build/pyinstaller/**/*.toc' in source
    assert 'dist/native-manifest.json' in source
    assert 'if: always()' in source
    assert '[skip ci]' in source


@pytest.mark.parametrize('before,after', [
    ("${{ matrix.os == 'windows-2022' && 200 || 100 }}", '200'),
    ("${{ matrix.os == 'windows-2022' && 200 || 100 }}", "${{ matrix.os == 'macos-15' && 200 || 100 }}"),
    ("${{ runner.os == 'Windows' && 152 || 62 }}", '152'),
    ("&& 9000 || 3600", "&& 9500 || 3600"),
    ("&& 9000 || 3600", "&& 9000 || 9000"),
    ('--shard-timeout 900', '--shard-timeout 2100'),
    ('--target-seconds 450', '--target-seconds 900'),
    ('scripts/windows_full_costs.json', 'scripts/other_costs.json'),
    ('tests/test_final_frame_preview_oracle.py=2100', 'tests/test_other.py=2100'),
    ('tests/test_final_frame_preview_oracle.py=2100', 'tests/test_final_frame_preview_oracle.py=3000'),
    ('--isolated-module-timeout tests/test_final_frame_preview_oracle.py=2100', ''),
    ("tests/test_final_frame_preview_oracle.py=2100' || '' }}",
     "tests/test_final_frame_preview_oracle.py=2100' || '' }}; exit 0"),
])
def test_native_full_budget_contract_rejects_wiring_mutations(before, after):
    source = (Path(__file__).parents[1] / '.github/workflows/desktop-ci.yml').read_text()
    assert before in source
    with pytest.raises(AssertionError):
        assert_native_full_budget_contract(source.replace(before, after, 1))


def test_cost_bounded_shards_preserve_every_node_and_module():
    nodes=['tests/a.py::one','tests/a.py::two','tests/b.py::one','tests/b.py::two','tests/c.py::one','tests/d.py::one']
    costs={'tests/a.py':80,'tests/b.py':80,'tests/c.py':20,'tests/d.py':20}
    plan=runner.make_shards(nodes,100,estimated_seconds=costs,target_seconds=100)
    assert len(plan)==2
    assert Counter(n for shard in plan for n in shard)==Counter(nodes)
    for shard in plan:
        files={n.split('::')[0] for n in shard}
        assert sum(costs[f] for f in files)<=100
    for file in costs:
        assert sum(any(n.startswith(file+'::') for n in shard) for shard in plan)==1
    assert plan==runner.make_shards(nodes,100,estimated_seconds=costs,target_seconds=100)


@pytest.mark.parametrize('costs,limit',[({},100),({'tests/a.py':float('nan')},100),({'tests/a.py':0},100),({'tests/a.py':True},100),({'tests/a.py':1},float('inf'))])
def test_invalid_or_incomplete_scheduling_costs_fail_closed(costs,limit):
    with pytest.raises(ValueError):
        runner.make_shards(['tests/a.py::one'],25,estimated_seconds=costs,target_seconds=limit)


def test_cost_receipt_hash_binds_the_bytes_used_even_if_file_changes_during_collection(tmp_path):
    import hashlib
    profile=tmp_path/'costs.json'
    original=json.dumps({'schema':1,'module_seconds':{'tests/test_a.py':5}}).encode()
    profile.write_bytes(original)
    hook=("from pathlib import Path\n"
          "def pytest_sessionfinish(session,exitstatus):\n"
          "    if session.config.option.collectonly:\n"
          f"        Path({str(profile)!r}).write_text('{{\"schema\":1,\"module_seconds\":{{\"tests/test_a.py\":100}}}}')\n")
    code,output,summary=execute(tmp_path,{'tests/test_a.py':'def test_a(): pass\n','conftest.py':hook},
                                cost_profile=profile,target_seconds=10)
    assert code==0
    assert summary['scheduling_costs']['profile_sha256']==hashlib.sha256(original).hexdigest()
    assert summary['scheduling_costs']['estimated_shard_seconds']==[5]


@pytest.mark.parametrize('authorized', [False, True])
def test_isolated_timeout_real_scheduler_keeps_41_nodes_first_and_exact_once(tmp_path, monkeypatch, authorized):
    slow = 'tests/test_slow.py'
    fast = 'tests/test_fast.py'
    root = project(tmp_path, {
        slow: 'import pytest\n@pytest.mark.parametrize("n", range(41))\ndef test_node(n): assert n >= 0\n',
        fast: 'def test_fast(): pass\n',
    })
    profile = tmp_path / 'costs.json'
    profile.write_text(json.dumps({'schema': 1, 'module_seconds': {slow: 1389.828, fast: 2}}))
    original = runner.run_process
    launched = []
    def observed(command, **kwargs):
        launched.append((Path(kwargs['log_path']).parent.name, kwargs['timeout']))
        return original(command, **kwargs)
    monkeypatch.setattr(runner, 'run_process', observed)
    output = tmp_path / 'results'
    requests = [(slow, 2100)] if authorized else []
    code = runner.execute(root=root, output=output, paths=['tests'], workers=1,
                          target_nodes=200, collection_timeout=30, suite_timeout=3600,
                          cost_profile=profile, target_seconds=300,
                          isolated_module_timeouts=requests)
    summary = json.loads((output / 'summary.json').read_text())
    plan = json.loads((output / 'plan.json').read_text())
    caps = [2100.0 if authorized else 900.0, 900.0]
    assert code == 0 and summary['success'] and summary['exact_once']
    assert summary['outcomes'] == {'passed': 42} and summary['verified_nodes'] == 42
    assert len(plan['shards'][0]) == 41 and all(n.startswith(slow + '::') for n in plan['shards'][0])
    assert plan['shards'][1] == [fast + '::test_fast']
    assert Counter(n for shard in plan['shards'] for n in shard) == Counter(plan['collected'])
    assert summary['shard_timeout_seconds'] == 900  # Cost weights never grant time.
    assert summary['isolated_module_timeouts_seconds'] == (dict(requests) if authorized else {})
    assert summary['resolved_shard_timeout_seconds'] == plan['resolved_shard_timeout_seconds'] == caps
    assert plan['isolated_module_timeouts_seconds'] == summary['isolated_module_timeouts_seconds']
    assert launched == [('collection', 30), ('shard-000', caps[0]), ('shard-001', caps[1])]
    assert [s['shard_timeout_seconds'] for s in summary['completed_shards']] == caps
    assert [s['effective_timeout_seconds'] for s in summary['completed_shards']] == caps


@pytest.mark.parametrize('requests', [
    [('tests/test_a.py', True)], [('tests/test_a.py', False)],
    [('tests/test_a.py', float('nan'))], [('tests/test_a.py', float('inf'))],
    [('tests/test_a.py', -1)], [('tests/test_a.py', 0)], [('tests/test_a.py', '2100')],
    [('tests/test_a.py', 3601)], [('tests/test_a.py', 2100), ('tests/test_a.py', 2100)],
    [('', 2100)], [(None, 2100)], [('tests/test_a.py::test_a', 2100)],
    [('./tests/test_a.py', 2100)], [('../tests/test_a.py', 2100)],
    [('/tests/test_a.py', 2100)], [('tests\\test_a.py', 2100)],
    [('tests/test_a.py\n', 2100)], [('tests/test_a.py',)], True, {'tests/test_a.py': 2100},
])
def test_isolated_invalid_requests_fail_before_collection(tmp_path, monkeypatch, requests):
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid timeout configuration must not launch collection or tests')
    monkeypatch.setattr(runner, 'run_process', forbidden)
    with pytest.raises(ValueError):
        runner.execute(root=tmp_path, output=tmp_path / 'results', paths=['tests'],
                       isolated_module_timeouts=requests)
    assert not (tmp_path / 'results').exists()


@pytest.mark.parametrize('key', ['tests/test_missing.py', 'tests/test_unselected.py', 'tests/TEST_A.py'])
def test_isolated_unknown_or_uncollected_module_stops_before_tests(tmp_path, key):
    marker = tmp_path / 'executed'
    root = project(tmp_path, {
        'tests/test_a.py': f'from pathlib import Path\ndef test_a(): Path({str(marker)!r}).touch()\n',
        'tests/test_unselected.py': 'def test_other(): pass\n',
    })
    output = tmp_path / 'results'
    code = runner.execute(root=root, output=output, paths=['tests/test_a.py'],
                          isolated_module_timeouts=[(key, 2100)])
    summary = json.loads((output / 'summary.json').read_text())
    assert code == 1 and not summary['success'] and 'not collected' in summary['error']
    assert summary['isolated_module_timeouts_seconds'] == {key: 2100}
    assert not marker.exists() and not list(output.glob('shard-*'))
    assert json.loads((output / 'collection/receipt.json').read_text())['started'] == []


def test_isolated_timeout_rejects_mixed_shard_without_replanning_or_launch(tmp_path):
    root = project(tmp_path, {'tests/test_a.py': 'def test_a(): assert False\n',
                              'tests/test_b.py': 'def test_b(): assert False\n'})
    output = tmp_path / 'results'
    code = runner.execute(root=root, output=output, paths=['tests'], target_nodes=200,
                          isolated_module_timeouts=[('tests/test_a.py', 2100)])
    summary = json.loads((output / 'summary.json').read_text())
    assert code == 1 and 'singleton' in summary['error']
    assert summary['completed_shards'] == [] and not list(output.glob('shard-*'))
    assert not (output / 'results.xml').exists()


def test_isolated_timeout_rejects_split_module_plan():
    nodes = ['tests/a.py::a', 'tests/a.py::b']
    with pytest.raises(ValueError, match='singleton'):
        runner.resolve_shard_timeouts([[nodes[0]], [nodes[1]]], nodes, 900, {'tests/a.py': 2100})


@pytest.mark.parametrize('cap,remaining,effective', [(2100, 3000, 2100), (2100, 2100, 2100),
                                                   (2100, 17.25, 17.25), (900, 3000, 900),
                                                   (2100, 0, 0), (2100, -1, 0)])
def test_isolated_shard_records_effective_cap_and_obeys_suite_boundary(tmp_path, monkeypatch, cap, remaining, effective):
    from types import SimpleNamespace
    monkeypatch.setattr(runner, 'time', SimpleNamespace(monotonic=lambda: 1000))
    calls = []
    expected = ['tests/test_a.py::test_a']
    def process(command, **kwargs):
        calls.append(kwargs['timeout'])
        folder = Path(kwargs['log_path']).parent
        if kwargs['timeout'] <= 0:
            return {'returncode': None, 'timed_out': True, 'elapsed_seconds': 0}
        (folder / 'receipt.json').write_text(json.dumps(receipt(expected)))
        (folder / 'results.xml').write_text('<testsuite><testcase name="test_a"/></testsuite>')
        return {'returncode': 0, 'timed_out': False, 'elapsed_seconds': 0}
    monkeypatch.setattr(runner, 'run_process', process)
    result = runner.run_shard(0, expected, root=tmp_path, output=tmp_path,
                              shard_timeout=cap, deadline=1000 + remaining)
    assert calls == [effective]
    assert result['shard_timeout_seconds'] == cap and result['effective_timeout_seconds'] == effective
    assert result['success'] is (effective > 0)
    assert json.loads((tmp_path / 'shard-000/status.json').read_text()) == result


def test_isolated_override_timeout_is_incomplete_and_stops_later_work(tmp_path):
    root = project(tmp_path, {'tests/test_a_slow.py': 'import time\ndef test_slow(): time.sleep(30)\n',
                              'tests/test_z_next.py': 'def test_next(): pass\n'})
    output = tmp_path / 'results'
    started = time.monotonic()
    code = runner.execute(root=root, output=output, paths=['tests'], workers=1, target_nodes=1,
                          collection_timeout=5, shard_timeout=.01, suite_timeout=10,
                          isolated_module_timeouts=[('tests/test_a_slow.py', 2)])
    summary = json.loads((output / 'summary.json').read_text())
    assert code == 1 and not summary['success'] and time.monotonic() - started < 20
    assert summary['unstarted_shards'] == [1]
    assert len(summary['completed_shards']) == 1
    result = summary['completed_shards'][0]
    assert result['timed_out'] and result['shard_timeout_seconds'] == result['effective_timeout_seconds'] == 2
    assert len(list(output.glob('shard-*'))) == 1 and not (output / 'results.xml').exists()
    events = [json.loads(s) for s in (output / 'shard-000/events.jsonl').read_text().splitlines()]
    assert [e['nodeid'] for e in events if e['event'] == 'started'] == ['tests/test_a_slow.py::test_slow']
    assert not any(e['event'] == 'finished' for e in events)


def test_isolated_timeout_cli_passes_explicit_pairs_and_preserves_default(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, 'execute', lambda **kwargs: calls.append(kwargs) or 0)
    assert runner.main(['--isolated-module-timeout', 'tests/test_a.py=2100']) == 0
    assert calls[-1]['isolated_module_timeouts'] == [('tests/test_a.py', 2100)]
    assert calls[-1]['shard_timeout'] == 900 and calls[-1]['suite_timeout'] == 3600
    assert runner.main([]) == 0 and calls[-1]['isolated_module_timeouts'] is None


@pytest.mark.parametrize('value', ['tests/a.py', '=2100', 'tests/a.py=', 'tests/a.py=true',
                                  'tests/a.py=nan', 'tests/a.py=inf', 'tests/a.py=0', 'tests/a.py=-1'])
def test_isolated_timeout_cli_rejects_malformed_request_before_execute(monkeypatch, value):
    def forbidden(**kwargs): pytest.fail('Malformed CLI must not execute')
    monkeypatch.setattr(runner, 'execute', forbidden)
    with pytest.raises(SystemExit) as error:
        runner.main(['--isolated-module-timeout', value])
    assert error.value.code == 2


def test_isolated_two_workers_keep_caps_and_stop_after_failure_without_retry(tmp_path, monkeypatch):
    import threading
    release = tmp_path / 'release_running_shard'
    root = project(tmp_path, {
        'tests/test_long.py': 'def test_long(): assert False\n',
        'tests/test_normal.py': (
            'import time\nfrom pathlib import Path\ndef test_normal():\n'
            '    deadline = time.monotonic() + 10\n'
            f'    while not Path({str(release)!r}).exists():\n'
            '        assert time.monotonic() < deadline\n        time.sleep(.01)\n'),
        'tests/test_later.py': 'def test_later(): assert False, "Must never start after failure"\n',
    })
    profile = tmp_path / 'costs.json'
    profile.write_text(json.dumps({'schema': 1, 'module_seconds': {
        'tests/test_long.py': 1389.828, 'tests/test_normal.py': 20, 'tests/test_later.py': 10}}))
    original_process, original_wait = runner.run_process, runner.wait
    simultaneous = threading.Barrier(2, timeout=10)
    caps = {}
    def process(command, **kwargs):
        folder = Path(kwargs['log_path']).parent.name
        if folder.startswith('shard-'):
            caps[folder] = kwargs['timeout']
            simultaneous.wait()  # A serial implementation cannot satisfy this.
        return original_process(command, **kwargs)
    def wait_and_release(pending, **kwargs):
        completed, rest = original_wait(pending, **kwargs)
        if not release.exists():
            assert any(not future.result()['success'] for future in completed)
            assert len(rest) == 1
            release.touch()  # Let the already-running peer finish, not retry.
        return completed, rest
    monkeypatch.setattr(runner, 'run_process', process)
    monkeypatch.setattr(runner, 'wait', wait_and_release)
    output = tmp_path / 'results'
    code = runner.execute(root=root, output=output, paths=['tests'], workers=2, target_nodes=1,
                          collection_timeout=30, cost_profile=profile, target_seconds=300,
                          isolated_module_timeouts=[('tests/test_long.py', 2100)])
    summary = json.loads((output / 'summary.json').read_text())
    assert code == 1 and not summary['success'] and summary['unstarted_shards'] == [2]
    assert caps == {'shard-000': 2100, 'shard-001': 900}
    assert [s['success'] for s in summary['completed_shards']] == [False, True]
    assert summary['resolved_shard_timeout_seconds'] == [2100, 900, 900]
    assert not (output / 'shard-002').exists() and not (output / 'results.xml').exists()
    for index, name in [(0, 'long'), (1, 'normal')]:
        observed = json.loads((output / f'shard-{index:03d}/receipt.json').read_text())
        assert observed['started'] == observed['finished'] == [f'tests/test_{name}.py::test_{name}']


@pytest.mark.parametrize('option', ['collection_timeout', 'shard_timeout', 'suite_timeout'])
@pytest.mark.parametrize('value', [True, False, '900'])
def test_isolated_limits_cannot_hide_bool_or_nonnumeric_suite_budget(tmp_path, option, value):
    with pytest.raises(ValueError):
        runner.execute(root=tmp_path, output=tmp_path / 'results', paths=['tests'],
                       isolated_module_timeouts=[('tests/test_a.py', 1)], **{option: value})
    assert not (tmp_path / 'results').exists()


@pytest.mark.parametrize('suffix', ['::test_a', '::TestGroup', '::TestGroup::test_a', '::test_a[param]'])
@pytest.mark.parametrize('spelling', ['relative', 'dot', 'dotdot', 'absolute', 'backslash'])
def test_isolated_module_rejects_explicit_node_or_class_selection_before_collection(tmp_path, monkeypatch, suffix, spelling):
    forms = {'relative': 'tests/test_a.py', 'dot': './tests/test_a.py',
             'dotdot': 'tests/../tests/test_a.py', 'absolute': str(tmp_path / 'tests/test_a.py'),
             'backslash': 'tests\\test_a.py'}
    def forbidden(*args, **kwargs):
        pytest.fail('Partial exceptional-module selection must not launch collection or tests')
    monkeypatch.setattr(runner, 'run_process', forbidden)
    with pytest.raises(ValueError, match='partial module selection'):
        runner.execute(root=tmp_path, output=tmp_path / 'results', paths=[forms[spelling] + suffix],
                       isolated_module_timeouts=[('tests/test_a.py', 2100)])
    assert not (tmp_path / 'results').exists()


@pytest.mark.parametrize('with_override', [False, True])
def test_isolated_selection_guard_does_not_change_other_module_selection(tmp_path, with_override):
    root = project(tmp_path, {'tests/test_a.py': 'def test_one(): pass\ndef test_two(): pass\n',
                              'tests/test_b.py': 'def test_one(): pass\ndef test_two(): assert False\n'})
    output = tmp_path / 'results'
    requests = [('tests/test_a.py', 2100)] if with_override else None
    code = runner.execute(root=root, output=output, paths=['tests/test_a.py', 'tests/test_b.py::test_one'],
                          target_nodes=1, isolated_module_timeouts=requests)
    summary = json.loads((output / 'summary.json').read_text())
    assert code == 0 and summary['verified_nodes'] == 3 and summary['outcomes'] == {'passed': 3}
    assert summary['resolved_shard_timeout_seconds'] == [2100 if with_override else 900, 900]


@pytest.mark.parametrize('selector', ['@selectors.txt', '@/absolute/selectors.txt', '-k',
                                     '--deselect=tests/test_a.py::test_two', '--ignore=tests/test_a.py', '--pyargs'])
def test_isolated_module_rejects_response_files_and_embedded_pytest_options(tmp_path, monkeypatch, selector):
    (tmp_path / 'selectors.txt').write_text('tests/test_a.py::test_one\n')
    def forbidden(*args, **kwargs):
        pytest.fail('Opaque selectors must be rejected before collection, without expanding the file')
    monkeypatch.setattr(runner, 'run_process', forbidden)
    with pytest.raises(ValueError, match='explicit file/directory selectors'):
        runner.execute(root=tmp_path, output=tmp_path / 'results', paths=[selector],
                       isolated_module_timeouts=[('tests/test_a.py', 2100)])
    assert not (tmp_path / 'results').exists()


def test_response_file_selection_without_override_is_unchanged(tmp_path):
    root = project(tmp_path, {'tests/test_a.py': 'def test_one(): pass\ndef test_two(): assert False\n'})
    selectors = tmp_path / 'selectors.txt'
    selectors.write_text('tests/test_a.py::test_one\n')
    output = tmp_path / 'results'
    assert runner.execute(root=root, output=output, paths=['@' + str(selectors)]) == 0
    summary = json.loads((output / 'summary.json').read_text())
    assert summary['verified_nodes'] == 1 and summary['outcomes'] == {'passed': 1}


@pytest.mark.parametrize('selection', ['response', 'keyword', 'deselect', 'marker'])
def test_isolated_real_cli_refuses_hidden_partial_selection_before_collection(tmp_path, selection):
    collected = tmp_path / 'collection_started'
    root = project(tmp_path, {'tests/test_a.py':
        f'from pathlib import Path\nPath({str(collected)!r}).touch()\n'
        'def test_one(): pass\ndef test_two(): assert False\n'})
    response = tmp_path / 'selectors.txt'
    response.write_text('tests/test_a.py::test_one\n')
    choices = {'response': ['@' + str(response)], 'keyword': ['tests', '-k', 'one'],
               'deselect': ['tests', '--deselect=tests/test_a.py::test_two'],
               'marker': ['tests', '-m', 'smoke']}
    output = tmp_path / 'results'
    process = subprocess.run([runner.sys.executable, str(Path(runner.__file__).resolve()),
                              '--root', str(root), '--output', str(output),
                              '--isolated-module-timeout', 'tests/test_a.py=2100',
                              '--', *choices[selection]], capture_output=True, text=True, timeout=15)
    assert process.returncode == 2
    assert 'explicit file/directory selectors' in process.stderr
    assert not output.exists() and not collected.exists()

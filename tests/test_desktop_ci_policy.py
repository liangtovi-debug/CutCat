"""Budget/selection wiring tests, not a GitHub runner or native-build simulator.

actionlint separately validates YAML/schema/expression contexts. This bounded
evaluator exercises only the Boolean/JSON expressions used by this workflow.
No YAML/runtime dependency is added to the application or native package.
"""
import json
import math
from pathlib import Path
import re
from types import SimpleNamespace

import pytest


SOURCE = (Path(__file__).parents[1] / '.github/workflows/desktop-ci.yml').read_text()


def expression(field, indent):
    match = re.search(r'^' + ' ' * indent + re.escape(field) + r': \$\{\{ (.+) \}\}$', SOURCE, re.M)
    assert match, f'Missing expression: {field}'
    return match[1]


def evaluate(value, event, platform='', attempt=1, runner_os=''):
    # Input strings stay data, never interpolated into the expression code.
    return eval(value.replace('&&', ' and ').replace('||', ' or '),
                {'__builtins__': {}}, {
                    # GitHub's loose equality coerces its numeric attempt string.
                    'github': SimpleNamespace(event_name=event, run_attempt=int(attempt)),
                    'inputs': SimpleNamespace(platform=platform),
                    'matrix': SimpleNamespace(os=platform),
                    'runner': SimpleNamespace(os=runner_os),
                    # Only string-array membership for the ASCII runner labels
                    # is modeled here, not all GitHub contains coercions.
                    'contains': lambda sequence, item: any(
                        candidate.casefold() == item.casefold() for candidate in sequence),
                    'fromJSON': json.loads,
                    'format': lambda template, *args: template.format(*args),
                })


@pytest.mark.parametrize('platform', ['windows-2022', 'macos-15'])
def test_manual_dispatch_selects_only_requested_platform(platform):
    assert evaluate(expression('if', 4), 'workflow_dispatch', platform)
    assert evaluate(expression('os', 8), 'workflow_dispatch', platform) == [platform]


@pytest.mark.parametrize('platform', ['WINDOWS-2022', 'Windows-2022', 'MACOS-15', 'macOS-15'])
def test_case_variants_pass_expression_guard_without_normalizing_matrix(platform):
    # contains() ignores case, but format()/fromJSON() preserve input spelling.
    # This does not simulate dispatch API choice validation or runner allocation.
    assert evaluate(expression('if', 4), 'workflow_dispatch', platform)
    assert evaluate(expression('os', 8), 'workflow_dispatch', platform) == [platform]


@pytest.mark.parametrize('platform', [
    '', 'ubuntu-latest', 'both', '"]', 'windows-2022,macos-15',
    'WINDOWS-2025', 'MACOS-15-INTEL', 'windows', 'windows-2022 ',
    ' macos-15', 'windows-2022-extra', 'prefix-macos-15',
])
def test_invalid_manual_choice_is_rejected_before_matrix_expansion(platform):
    assert not evaluate(expression('if', 4), 'workflow_dispatch', platform)


@pytest.mark.parametrize('platform', ['windows-2022', 'macos-15'])
@pytest.mark.parametrize('attempt', [2, 3, 100, '2'])
def test_manual_reruns_do_not_allocate_another_desktop_job(platform, attempt):
    assert not evaluate(expression('if', 4), 'workflow_dispatch', platform, attempt)


@pytest.mark.parametrize('event', ['push', 'pull_request'])
@pytest.mark.parametrize('attempt', [1, 2])
def test_automatic_platform_coverage_and_reruns_remain_available(event, attempt):
    assert evaluate(expression('if', 4), event, attempt=attempt)
    assert evaluate(expression('os', 8), event) == ['windows-2022', 'macos-15']


def test_manual_default_and_shared_serialization():
    inputs = SOURCE.split('    inputs:\n', 1)[1].split('  push:', 1)[0]
    assert 'type: choice' in inputs and 'required: true' in inputs
    assert 'default: windows-2022' in inputs
    assert re.findall(r'^          - (.+)$', inputs, re.M) == ['windows-2022', 'macos-15']
    concurrency = SOURCE.split('\nconcurrency:\n', 1)[1].split('\njobs:', 1)[0]
    assert 'group: desktop-draft-validation' in concurrency
    assert 'cancel-in-progress: false' in concurrency
    assert '${{' not in concurrency  # neither branch nor platform may split the lock
    assert 'max-parallel: 1' in SOURCE
    assert '  push:\n  pull_request:' in SOURCE
    assert 'contents: read' in SOURCE


def test_failure_stops_work_except_bounded_diagnostics():
    steps = re.split(r'^      - ', SOURCE.split('    steps:\n', 1)[1], flags=re.M)[1:]
    assert [step.splitlines()[0] for step in steps] == [
        "uses: actions/checkout@v4",
        "uses: actions/setup-python@v5",
        "name: Install Python dependencies",
        "name: Build and verify caption shaping runtime",
        "name: Provision pinned external FFmpeg and verify capabilities",
        "name: Provision pinned reference-only FFmpeg (Windows)",
        "name: Provision licensed Chinese test font (CI cache only)",
        "name: Check responsive inspector in one bounded process",
        "name: Retrieve verified v25 source for same-runtime exact color tests",
        "name: Run exact full regression in bounded local shards",
        "name: Check UI alignment and capture native Qt screenshots",
        "name: Build unsigned native draft",
        "name: Archive and re-extract native package",
        "name: Run packaged runtime smoke (Windows)",
        "name: Run packaged runtime smoke (macOS)",
        "name: Capture dependency manifest",
        "name: Bind native package hashes to exact source and validation",
        "uses: actions/upload-artifact@v4",
        "name: Retain diagnostics even on failure",
    ]
    # The verified private baseline must be fetched before exact regression;
    # it is an ordinary success-gated prerequisite, not a failure bypass.
    assert 'Retrieve verified v25 source' in steps[8]
    assert re.findall(r'^        timeout-minutes: (.+)$', steps[8], re.M) == ['5']
    assert re.findall(r'^          GH_TOKEN: (.+)$', steps[8], re.M) == ['${{ github.token }}']
    assert re.findall(r'^        if: (.+)$', steps[8], re.M) in ([], ['${{ success() }}'])
    # Compare the complete trailing script, not a substring that could accept
    # a shell suffix masking a failed fetch or an extra command continuation.
    assert steps[8].partition('\n        run: ')[2].strip() == (
        'python scripts/fetch_contrast_v25_archive.py --output build/private-v25.zip')
    assert 'Run exact full regression' in steps[9]
    assert 'Check UI alignment' in steps[10]
    assert 'if: ${{ success() }}' in steps[10]
    for step in steps[:-1]:
        # No explicit always()/failure()/!cancelled() bypass. Ordinary platform
        # predicates inherit GitHub's default success() status check.
        conditions = re.findall(r'^        if: (.+)$', step, re.M)
        assert all(value in ('${{ success() }}', "runner.os == 'Windows'",
                             "runner.os == 'macOS'") for value in conditions)
        assert 'continue-on-error:' not in step
    diagnostics = steps[-1]
    assert 'Retain diagnostics even on failure' in diagnostics
    assert 'if: always()' in diagnostics and 'timeout-minutes: 5' in diagnostics
    assert 'actions/upload-artifact@v4' in diagnostics
    assert 'build/test-shards/**/*.jsonl' in diagnostics



@pytest.mark.parametrize('before,after', [
    ('        timeout-minutes: 5',
     "        if: runner.os == 'Windows'\n        timeout-minutes: 5"),
    ('        timeout-minutes: 5',
     "        if: runner.os == 'macOS'\n        timeout-minutes: 5"),
    ('--output build/private-v25.zip',
     '--output build/private-v25.zip; exit 0'),
    ('--output build/private-v25.zip',
     '--output build/private-v25.zip\n          ; exit 0'),
    ('        timeout-minutes: 5', '        timeout-minutes: 50'),
    ('        timeout-minutes: 5', '        timeout-minutes: 500'),
])
def test_private_v25_prerequisite_contract_rejects_relaxed_guards(monkeypatch, before, after):
    steps = re.split(r'^      - ', SOURCE.split('    steps:\n', 1)[1], flags=re.M)[1:]
    prefetch = steps[8]
    assert before in prefetch
    changed = prefetch.replace(before, after, 1)
    monkeypatch.setitem(globals(), 'SOURCE', SOURCE.replace(prefetch, changed, 1))
    with pytest.raises(AssertionError):
        test_failure_stops_work_except_bounded_diagnostics()


def test_private_v25_prerequisite_contract_accepts_explicit_success(monkeypatch):
    steps = re.split(r'^      - ', SOURCE.split('    steps:\n', 1)[1], flags=re.M)[1:]
    prefetch = steps[8]
    changed = prefetch.replace('        timeout-minutes: 5',
                              '        if: ${{ success() }}\n        timeout-minutes: 5', 1)
    monkeypatch.setitem(globals(), 'SOURCE', SOURCE.replace(prefetch, changed, 1))
    test_failure_stops_work_except_bounded_diagnostics()

def test_native_acceptance_gates_and_budget_not_shortened():
    assert expression('timeout-minutes', 4) == "matrix.os == 'windows-2022' && 200 || 100"
    steps = re.split(r'^      - ', SOURCE.split('    steps:\n', 1)[1], flags=re.M)[1:]
    assert re.findall(r'^        timeout-minutes: (.+)$', steps[9], re.M) == [
        "${{ runner.os == 'Windows' && 152 || 62 }}"]
    assert re.findall(r'^        if: (.+)$', steps[9], re.M) in ([], ['${{ success() }}'])
    full = next(line.strip() for line in SOURCE.splitlines()
                if 'run: python scripts/run_bounded_tests.py --output build/test-shards' in line)
    assert full == ('run: python scripts/run_bounded_tests.py --output build/test-shards '
                    '--workers 2 --target-nodes 200 --collection-timeout 180 '
                    "--shard-timeout 900 --suite-timeout ${{ runner.os == 'Windows' && 9000 || 3600 }} "
                    "${{ runner.os == 'Windows' && '--cost-profile scripts/windows_full_costs.json "
                    '--target-seconds 450 --isolated-module-timeout '
                    "tests/test_final_frame_preview_oracle.py=2100' || '' }}")
    # Reject trailing commands/continuations as well as a selected subset or
    # a larger global shard cap. macOS keeps its prior command and deadlines.
    assert steps[9].partition('\n        run: ')[2].strip() == full.removeprefix('run: ')
    for command in ('python scripts/build_app.py', 'python scripts/archive_native.py',
                    'python scripts/write_native_manifest.py'):
        assert command in SOURCE
    assert SOURCE.count('run: python scripts/run_native_smoke.py') == 2
    assert SOURCE.count('--archive dist/LumenEdit-Draft-') == 2
    assert 'python -m pip install -e ".[test,build]"' in SOURCE


@pytest.mark.parametrize('platform,runner_os,job,step,suite', [
    ('windows-2022', 'Windows', 200, 152, 9000),
    ('macos-15', 'macOS', 100, 62, 3600),
])
def test_full_budget_expressions_keep_platforms_separate(platform, runner_os, job, step, suite):
    assert evaluate(expression('timeout-minutes', 4), 'workflow_dispatch', platform) == job
    steps = re.split(r'^      - ', SOURCE.split('    steps:\n', 1)[1], flags=re.M)[1:]
    full = steps[9]
    value = re.search(r'timeout-minutes: \$\{\{ (.+) \}\}', full)[1]
    assert evaluate(value, 'workflow_dispatch', runner_os=runner_os) == step
    command = full.partition('\n        run: ')[2].strip()
    resolved = re.sub(r'\$\{\{ (.+?) \}\}', lambda match: str(evaluate(
        match[1], 'workflow_dispatch', runner_os=runner_os)), command).strip()
    expected = ('python scripts/run_bounded_tests.py --output build/test-shards '
                '--workers 2 --target-nodes 200 --collection-timeout 180 '
                f'--shard-timeout 900 --suite-timeout {suite}')
    if runner_os == 'Windows':
        expected += (' --cost-profile scripts/windows_full_costs.json --target-seconds 450 '
                     '--isolated-module-timeout tests/test_final_frame_preview_oracle.py=2100')
    assert resolved == expected
    assert suite < step * 60 < job * 60


@pytest.mark.parametrize('before,after', [
    ('      - name: Run exact full regression in bounded local shards',
     "      - name: Run exact full regression in bounded local shards\n        if: runner.os == 'Windows'"),
    ('      - name: Run exact full regression in bounded local shards',
     "      - name: Run exact full regression in bounded local shards\n        if: runner.os == 'macOS'"),
    ("&& 200 || 100", "&& 250 || 100"),
    ("&& 200 || 100", "&& 200 || 200"),
    ("&& 152 || 62", "&& 152 || 152"),
    ("&& 152 || 62", "&& 62 || 62"),
    ("&& 9000 || 3600", "&& 15000 || 3600"),
    ("&& 9000 || 3600", "&& 9000 || 9000"),
    ('--workers 2 --target-nodes', '--workers 3 --target-nodes'),
    ('--shard-timeout 900 --suite-timeout', '--shard-timeout 2100 --suite-timeout'),
    ('--target-seconds 450', '--target-seconds 900'),
    ('--isolated-module-timeout tests/test_final_frame_preview_oracle.py=2100',
     '--isolated-module-timeout tests/test_final_frame_preview_oracle.py=9000'),
    ('--isolated-module-timeout tests/test_final_frame_preview_oracle.py=2100',
     '--isolated-module-timeout tests/test_production_final_frame.py=2100'),
    ('scripts/windows_full_costs.json', 'scripts/range_diagnostic_costs.json'),
    ('--output build/test-shards', 'tests/test_final_frame_preview_oracle.py --output build/test-shards'),
    ("=2100' || '' }}", "=2100' || '' }}; exit 0"),
    ("=2100' || '' }}", "=2100' || '' }}\n          echo ignored"),
])
def test_full_budget_contract_rejects_scope_or_timeout_relaxation(monkeypatch, before, after):
    assert before in SOURCE
    monkeypatch.setitem(globals(), 'SOURCE', SOURCE.replace(before, after, 1))
    with pytest.raises((AssertionError, StopIteration)):
        test_native_acceptance_gates_and_budget_not_shortened()


def test_full_regression_contract_accepts_explicit_success(monkeypatch):
    before = '      - name: Run exact full regression in bounded local shards'
    monkeypatch.setitem(globals(), 'SOURCE', SOURCE.replace(
        before, before + '\n        if: ${{ success() }}', 1))
    test_failure_stops_work_except_bounded_diagnostics()
    test_native_acceptance_gates_and_budget_not_shortened()


def test_windows_cost_profile_is_complete_estimates_not_runtime_authorization():
    root = Path(__file__).parents[1]
    profile = json.loads((root / 'scripts/windows_full_costs.json').read_text())
    assert profile['schema'] == 1
    costs = profile['module_seconds']
    assert set(costs) == {path.relative_to(root).as_posix() for path in (root / 'tests').glob('test_*.py')}
    assert all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) and value > 0 for value in costs.values())
    assert costs['tests/test_final_frame_preview_oracle.py'] > 900
    assert 'timeout' not in costs  # Estimates cannot implicitly widen hard caps.
    assert profile['provenance']['windows_runs'] == [42440005164, 42445430613]
    assert profile['provenance']['source_commit'] == '4b832adcce3f75281c4376bb2ce744d77b0c20ce'
    assert profile['provenance']['is_runtime_guarantee'] is False


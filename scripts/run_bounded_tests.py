"""Run one exact pytest collection in bounded, isolated, deterministic shards.

No retry, resume, external submission, test exclusion, or native-proof claim.
Every collected test must start, finish, and yield valid phase reports exactly
once. Collection and subprocess failures retain incomplete JSONL evidence.
Timeout exceptions require file/directory selection, not explicit partial nodes
of the exceptional module, response files, or embedded pytest options. This
contract covers the resulting collection; it is not a security sandbox against
plugins deliberately changing collection.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(PLUGIN_ROOT))
from scripts.source_provenance import capture_source_provenance, SourceProvenanceUnavailable


def source_provenance(root):
    try:
        return capture_source_provenance(root)
    except SourceProvenanceUnavailable as error:
        return {'available': False, 'reason': str(error)}


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.pending')
    temporary.write_text(json.dumps(value, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def validate_nodes(nodes):
    if not nodes or any(not isinstance(node, str) or not node or '\n' in node or '\r' in node for node in nodes):
        raise ValueError('Collection must contain nonempty single-line node IDs')
    if len(nodes) != len(set(nodes)):
        raise ValueError('Duplicate collected node IDs are not permitted')


def make_shards(nodes, target_nodes=200, *, estimated_seconds=None, target_seconds=None):
    """First-fit largest-file bins, with stable ties; never split a test module."""
    validate_nodes(nodes)
    if target_nodes < 1:
        raise ValueError('target_nodes must be positive')
    files = {}
    for node in nodes:
        files.setdefault(node.split('::', 1)[0], []).append(node)
    weighted = estimated_seconds is not None
    if weighted:
        if (not isinstance(estimated_seconds, dict) or isinstance(target_seconds, bool)
                or not isinstance(target_seconds, (int, float)) or not math.isfinite(target_seconds) or target_seconds <= 0):
            raise ValueError('Cost scheduling requires a finite positive target and module estimates')
        for filename in files:
            value = estimated_seconds.get(filename)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('Missing or invalid module cost: ' + filename)
    elif target_seconds is not None:
        raise ValueError('A time target requires module estimates')
    costs = {name: float(estimated_seconds[name]) if weighted else len(group) for name, group in files.items()}
    shards, totals = [], []
    for filename, group in sorted(files.items(), key=lambda item: (-costs[item[0]], item[0])):
        candidates = [i for i, shard in enumerate(shards) if len(shard) + len(group) <= target_nodes
                      and (not weighted or totals[i] + costs[filename] <= target_seconds)]
        index = min(candidates, key=lambda i: (totals[i], i)) if candidates else len(shards)
        if index == len(shards):
            shards.append([])
            totals.append(0)
        shards[index].extend(group)
        totals[index] += costs[filename]
    order = {node: i for i, node in enumerate(nodes)}
    for shard in shards:
        shard.sort(key=order.__getitem__)
    flattened = [node for shard in shards for node in shard]
    if Counter(flattened) != Counter(nodes):
        raise ValueError('Shard plan does not cover the exact collection')
    return shards


def validate_isolated_module_timeouts(requests, suite_timeout=None):
    """Explicit pairs preserve duplicate detection; weights never grant time."""
    if requests is None:
        return {}
    if not isinstance(requests, (list, tuple)):
        raise ValueError('Isolated module timeouts require a sequence of (module, seconds) pairs')
    result = {}
    for item in requests:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError('Each isolated module timeout requires one (module, seconds) pair')
        module, seconds = item
        if (not isinstance(module, str) or not module or module.strip() != module
                or any(c in module for c in ('\\', ':', '\n', '\r'))
                or PurePosixPath(module).is_absolute() or '..' in PurePosixPath(module).parts
                or PurePosixPath(module).as_posix() != module or not module.endswith('.py')):
            raise ValueError('Isolated timeout module must be an exact root-relative Python module path')
        if module in result:
            raise ValueError('Duplicate isolated module timeout: ' + module)
        if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                or not math.isfinite(seconds) or seconds <= 0):
            raise ValueError('Isolated module timeout must be finite positive seconds: ' + module)
        if suite_timeout is not None and seconds > suite_timeout:
            raise ValueError('Isolated module timeout exceeds the whole-suite cap: ' + module)
        result[module] = float(seconds)
    return result


def parse_isolated_module_timeout(value):
    module, separator, seconds = value.rpartition('=')
    try:
        if not separator:
            raise ValueError('Expected MODULE=SECONDS')
        requests = validate_isolated_module_timeouts([(module, float(seconds))])
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    return next(iter(requests.items()))


def validate_isolated_module_selection(root, paths, module_timeouts):
    """Do not silently grant a whole-module cap to an explicit class/test slice."""
    if not module_timeouts:
        return
    identity = lambda path: os.path.normcase(str((root / path.replace('\\', '/')).resolve()))
    targets = {identity(module) for module in module_timeouts}
    for selector in paths:
        selector = str(selector)
        # Pytest expands @files and parses options in its positional arguments.
        # Do not let either obscure a partial selection of an exceptional module.
        # This does not affect our own exact-node response files in run_shard.
        if selector.startswith(('@', '-')):
            raise ValueError('Isolated module timeouts require explicit file/directory selectors, not response files or options')
        module, separator, _ = selector.partition('::')
        if separator and identity(module) in targets:
            raise ValueError('Isolated module timeout forbids explicit partial module selection: ' + str(selector))


def resolve_shard_timeouts(plan, nodes, shard_timeout, module_timeouts):
    """Validate the finished plan, without splitting, regrouping, or reordering."""
    files = {node.split('::', 1)[0] for node in nodes}
    unknown = set(module_timeouts) - files
    if unknown:
        raise ValueError('Isolated module timeout key was not collected: ' + sorted(unknown)[0])
    caps = [shard_timeout] * len(plan)
    for module, seconds in module_timeouts.items():
        expected = [node for node in nodes if node.split('::', 1)[0] == module]
        indices = [i for i, shard in enumerate(plan)
                   if any(node.split('::', 1)[0] == module for node in shard)]
        if len(indices) != 1 or plan[indices[0]] != expected:
            raise ValueError('Isolated module timeout requires one complete singleton module shard: ' + module)
        caps[indices[0]] = seconds
    return caps


def verify_receipt(path, expected, *, collection=False):
    receipt = json.loads(Path(path).read_text(encoding='utf-8'))
    if receipt.get('schema') != 1 or receipt.get('exitstatus') != 0:
        raise ValueError('Receipt is missing, incompatible, or has a nonzero pytest exit')
    if receipt.get('collected') != expected:
        raise ValueError('Receipt collection differs from the planned exact node IDs/order')
    if any(report.get('outcome') == 'failed' for report in receipt.get('collection_reports', [])):
        raise ValueError('Collection errors are not allowed')
    if collection:
        if any(receipt.get(key) != [] for key in ('started', 'finished', 'reports')):
            raise ValueError('Collection-only subprocess unexpectedly executed tests')
        return {}
    if receipt.get('started') != expected or receipt.get('finished') != expected:
        raise ValueError('Missing, duplicate, reordered, or unexpected starts/finishes')
    phases = {node: {} for node in expected}
    for report in receipt.get('reports', []):
        node, when = report.get('nodeid'), report.get('when')
        if node not in phases or when not in {'setup', 'call', 'teardown'} or when in phases[node]:
            raise ValueError('Unexpected node, phase, or duplicate result report')
        if report.get('outcome') not in {'passed', 'skipped'}:
            raise ValueError('Failing or invalid phase result')
        phases[node][when] = report
    counts = Counter()
    for node, reports in phases.items():
        if 'setup' not in reports or 'teardown' not in reports or reports['teardown']['outcome'] != 'passed':
            raise ValueError(f'Missing setup/teardown or incomplete cleanup: {node}')
        setup = reports['setup']['outcome']
        required = {'setup', 'call', 'teardown'} if setup == 'passed' else {'setup', 'teardown'}
        if set(reports) != required:
            raise ValueError(f'Missing or unexpected call phase: {node}')
        outcome = reports.get('call', reports['setup'])
        label = outcome['outcome']
        if 'wasxfail' in outcome:
            label = 'xfailed' if label == 'skipped' else 'xpassed'
        counts[label] += 1
    return dict(counts)


def terminate_tree(process):
    """Bound cleanup too. Windows taskkill /T covers FFmpeg descendants."""
    if os.name == 'nt':
        try:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=False, timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        # Even when the leader exited, kill children that ignored SIGTERM.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Could not terminate the bounded pytest process tree')


def run_process(command, *, root, environment, log_path, timeout):
    if timeout <= 0:
        return {'returncode': None, 'timed_out': True, 'elapsed_seconds': 0.0,
                'error': 'Whole-suite deadline elapsed before launch'}
    started = time.monotonic()
    with Path(log_path).open('x', encoding='utf-8') as log:
        kwargs = {'start_new_session': True} if os.name != 'nt' else {
            'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
        process = subprocess.Popen(command, cwd=root, env=environment, stdout=log,
                                   stderr=subprocess.STDOUT, **kwargs)
        timed_out = False
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate_tree(process)
            returncode = process.returncode
            log.write(f'\nBounded pytest deadline exceeded ({timeout:.3f} seconds). Process tree terminated.\n')
    return {'returncode': returncode, 'timed_out': timed_out,
            'elapsed_seconds': round(time.monotonic() - started, 3)}


def pytest_command(root, folder):
    return [sys.executable, '-m', 'pytest', '--rootdir', str(root), '-q', '-o', 'addopts=',
            '-o', 'faulthandler_timeout=300', '-o', 'cache_dir=' + str(folder / 'cache'),
            '--basetemp', str(folder / 'tmp'), '-p', 'scripts.pytest_bounded_receipt',
            '--bounded-receipt', str(folder / 'receipt.json'),
            '--bounded-events', str(folder / 'events.jsonl')]


def child_environment(root, folder):
    environment = dict(os.environ)
    environment.pop('PYTEST_ADDOPTS', None)
    environment.pop('PYTEST_CURRENT_TEST', None)
    environment['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    # pytest response files and captured logs must be UTF-8 on Windows too.
    environment['PYTHONUTF8'] = '1'
    environment['PYTHONIOENCODING'] = 'utf-8'
    environment.setdefault('QT_QPA_PLATFORM', 'offscreen')
    environment['PYTHONPATH'] = os.pathsep.join(filter(None, [str(PLUGIN_ROOT), str(root), environment.get('PYTHONPATH')]))
    environment['AUTO_FRAME_REVIEW_EVIDENCE'] = str(folder / 'ui')
    environment['MORPHOLOGY_EVIDENCE_DIR'] = str(folder / 'morphology')
    environment['RANGE_ORACLE_EVIDENCE'] = str(folder / 'range-timings')
    return environment


def run_shard(index, expected, *, root, output, shard_timeout, deadline):
    folder = output / f'shard-{index:03d}'
    folder.mkdir()
    # pytest's response file avoids Windows' short command-line limit. Each
    # exact node ID is an argument, not a shell-quoted/globbed test selector.
    nodes_path = folder / 'nodes.txt'
    nodes_path.write_text('\n'.join(expected) + '\n', encoding='utf-8')
    command = pytest_command(root, folder) + ['@' + str(nodes_path), '--junitxml=' + str(folder / 'results.xml')]
    result = {'shard': index, 'expected_nodes': len(expected), 'success': False,
              'shard_timeout_seconds': shard_timeout}
    print(json.dumps({'shard': index, 'starting_nodes': len(expected),
                      'shard_timeout_seconds': shard_timeout}), flush=True)
    try:
        environment = child_environment(root, folder)
        timeout = max(0.0, min(shard_timeout, deadline - time.monotonic()))
        result['effective_timeout_seconds'] = timeout
        result.update(run_process(command, root=root, environment=environment,
                                  log_path=folder / 'pytest.log', timeout=timeout))
        if result['timed_out'] or result['returncode'] != 0:
            raise ValueError('pytest timed out or returned nonzero; partial journal retained')
        result['outcomes'] = verify_receipt(folder / 'receipt.json', expected)
        tree = ET.parse(folder / 'results.xml').getroot()
        if (len(list(tree.iter('testcase'))) != len(expected)
                or list(tree.iter('failure')) or list(tree.iter('error'))):
            raise ValueError('JUnit coverage/results disagree with the exact receipt')
        result['success'] = True
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
    write_json(folder / 'status.json', result)
    print(json.dumps(result), flush=True)
    return result


def execute(*, root, output, paths, workers=2, target_nodes=200,
            collection_timeout=180.0, shard_timeout=900.0, suite_timeout=3600.0,
            cost_profile=None, target_seconds=None, isolated_module_timeouts=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    if not root.is_dir():
        raise ValueError('Test root does not exist')
    if not 1 <= workers <= 4 or target_nodes < 1:
        raise ValueError('Use 1..4 workers and a positive shard target')
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) or value <= 0
           for value in (collection_timeout, shard_timeout, suite_timeout)):
        raise ValueError('All timeouts must be finite positive seconds')
    module_timeouts = validate_isolated_module_timeouts(isolated_module_timeouts, suite_timeout)
    validate_isolated_module_selection(root, paths, module_timeouts)
    estimates = None
    if cost_profile is not None:
        profile_bytes = Path(cost_profile).read_bytes()
        profile_sha256 = hashlib.sha256(profile_bytes).hexdigest()
        profile = json.loads(profile_bytes.decode('utf-8'))
        if profile.get('schema') != 1 or not isinstance(profile.get('module_seconds'), dict):
            raise ValueError('Invalid scheduling cost profile')
        estimates = profile['module_seconds']
    elif target_seconds is not None:
        raise ValueError('A time target requires a cost profile')
    # Never overwrite or silently reuse a previous run's success/failure receipt.
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    deadline = started + suite_timeout
    summary = {'schema': 1, 'success': False, 'mode': 'exact collection in isolated module-preserving shards',
               'native_validation_claimed': False, 'root': str(root), 'paths': paths,
               'workers': workers, 'target_nodes': target_nodes,
               'collection_timeout_seconds': collection_timeout, 'shard_timeout_seconds': shard_timeout,
               'suite_timeout_seconds': suite_timeout,
               'isolated_module_timeouts_seconds': module_timeouts, 'completed_shards': []}
    write_json(output / 'summary.json', summary)
    try:
        initial_provenance = source_provenance(root)
        summary['source_provenance_start'] = initial_provenance
        write_json(output / 'summary.json', summary)
        collection = output / 'collection'
        collection.mkdir()
        process = run_process(pytest_command(root, collection) + ['--collect-only', *paths], root=root,
                              environment=child_environment(root, collection), log_path=collection / 'pytest.log',
                              timeout=min(collection_timeout, deadline - time.monotonic()))
        write_json(collection / 'status.json', process)
        if process['timed_out'] or process['returncode'] != 0:
            raise ValueError('Test collection failed or timed out; inspect collection evidence')
        receipt = json.loads((collection / 'receipt.json').read_text(encoding='utf-8'))
        nodes = receipt['collected']
        validate_nodes(nodes)
        verify_receipt(collection / 'receipt.json', nodes, collection=True)
        plan = make_shards(nodes, target_nodes, estimated_seconds=estimates, target_seconds=target_seconds)
        shard_timeouts = resolve_shard_timeouts(plan, nodes, shard_timeout, module_timeouts)
        if estimates is not None:
            summary['scheduling_costs'] = {'profile_sha256': profile_sha256,
                                           'target_seconds': target_seconds,
                                           'estimated_shard_seconds': [sum(estimates[f] for f in {n.split('::')[0] for n in group}) for group in plan]}
        digest = hashlib.sha256(json.dumps(nodes, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
        write_json(output / 'plan.json', {'schema': 1, 'collection_sha256': digest, 'collected': nodes,
                                        'shards': plan, 'isolated_module_timeouts_seconds': module_timeouts,
                                        'resolved_shard_timeout_seconds': shard_timeouts})
        summary.update(collected_nodes=len(nodes), collection_sha256=digest,
                       collection_reports=receipt['collection_reports'], total_shards=len(plan),
                       resolved_shard_timeout_seconds=shard_timeouts)
        write_json(output / 'summary.json', summary)
        # Only fill available slots. On a failed shard, finish running slots but
        # never start more work, retry, or disguise unstarted tests as passes.
        next_index = 0
        failed = False
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {}
            while pending or (next_index < len(plan) and not failed):
                while not failed and len(pending) < workers and next_index < len(plan):
                    if time.monotonic() >= deadline:
                        failed = True
                        summary['error'] = 'Whole-suite deadline exceeded; remaining shards were not launched'
                        break
                    future = pool.submit(run_shard, next_index, plan[next_index], root=root, output=output,
                                         shard_timeout=shard_timeouts[next_index], deadline=deadline)
                    pending[future] = next_index
                    next_index += 1
                if not pending:
                    break
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    pending.pop(future)
                    result = future.result()
                    summary['completed_shards'].append(result)
                    failed |= not result['success']
                summary['completed_shards'].sort(key=lambda item: item['shard'])
                write_json(output / 'summary.json', summary)
        summary['unstarted_shards'] = list(range(next_index, len(plan)))
        if failed or next_index != len(plan):
            raise ValueError('Bounded regression failed or was incomplete; inspect per-shard status and events')
        outcomes = Counter()
        all_nodes = []
        aggregate = ET.Element('testsuites')
        for index, expected in enumerate(plan):
            folder = output / f'shard-{index:03d}'
            outcomes.update(verify_receipt(folder / 'receipt.json', expected))
            all_nodes.extend(expected)
            tree = ET.parse(folder / 'results.xml').getroot()
            for suite in ([tree] if tree.tag == 'testsuite' else list(tree)):
                aggregate.append(suite)
        if Counter(all_nodes) != Counter(nodes) or len(all_nodes) != len(set(all_nodes)):
            raise ValueError('Final aggregate has dropped or duplicate tests')
        final_provenance = source_provenance(root)
        summary['source_provenance_end'] = final_provenance
        if initial_provenance.get('clean') is True and final_provenance != initial_provenance:
            raise ValueError('Source provenance changed during regression; results cannot certify the starting source')
        ET.ElementTree(aggregate).write(output / 'results.xml', encoding='utf-8', xml_declaration=True)
        summary.update(success=True, exact_once=True, outcomes=dict(outcomes), verified_nodes=len(all_nodes))
    except Exception as error:
        summary['error'] = f'{type(error).__name__}: {error}'
    summary['elapsed_seconds'] = round(time.monotonic() - started, 3)
    write_json(output / 'summary.json', summary)
    print(json.dumps(summary), flush=True)
    return 0 if summary['success'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='*', default=['tests'])
    parser.add_argument('--root', type=Path, default=PLUGIN_ROOT)
    parser.add_argument('--output', type=Path, default=Path('build/test-shards'))
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--target-nodes', type=int, default=200)
    parser.add_argument('--cost-profile', type=Path)
    parser.add_argument('--target-seconds', type=float)
    parser.add_argument('--collection-timeout', type=float, default=180)
    parser.add_argument('--shard-timeout', type=float, default=900)
    parser.add_argument('--isolated-module-timeout', action='append', type=parse_isolated_module_timeout,
                        metavar='MODULE=SECONDS',
                        help='Explicit cap for a complete singleton module shard; repeatable, never exceeds suite cap')
    parser.add_argument('--suite-timeout', type=float, default=3600)
    args = parser.parse_args(argv)
    try:
        return execute(root=args.root, output=args.output, paths=args.paths or ['tests'], workers=args.workers,
                       target_nodes=args.target_nodes, collection_timeout=args.collection_timeout,
                       shard_timeout=args.shard_timeout, suite_timeout=args.suite_timeout,
                       cost_profile=args.cost_profile, target_seconds=args.target_seconds,
                       isolated_module_timeouts=args.isolated_module_timeout)
    except (ValueError, OSError) as error:
        parser.exit(2, f'Cannot start bounded regression: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())

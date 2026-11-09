"""Bound a re-extracted frozen app's smoke run and retain launch failures.

Launch diagnostics are deliberately outside the self-test's exclusive output
folder. This wrapper never builds, downloads, signs, publishes, or retries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys

# Support both `python scripts/run_native_smoke.py` and package import in tests.
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_bounded_tests import run_process, write_json


def file_fingerprint(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f'Missing, empty, or symlinked launch input: {path.name}')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'name': path.name, 'bytes': path.stat().st_size, 'sha256': digest}


def run_smoke(executable, app_output, diagnostic_output, *, timeout=600, archive=None):
    executable, app_output, diagnostic_output = (Path(path).resolve() for path in
                                                 (executable, app_output, diagnostic_output))
    archive = Path(archive).resolve() if archive is not None else None
    if not executable.is_file():
        raise ValueError(f'Frozen executable does not exist: {executable}')
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Smoke timeout must be finite positive seconds')
    if app_output == diagnostic_output or app_output in diagnostic_output.parents or diagnostic_output in app_output.parents:
        raise ValueError('App output and launch diagnostics must be separate, non-nested directories')
    if app_output.exists() or diagnostic_output.exists():
        raise FileExistsError('Refusing to overwrite previous smoke evidence; use fresh output directories')
    diagnostic_output.mkdir(parents=True)
    status = {'schema': 1, 'success': False, 'executable': str(executable),
              'app_output': str(app_output), 'platform': sys.platform, 'timeout_seconds': timeout}
    if archive is not None:
        status['archive_path'] = str(archive)
    status_path = diagnostic_output / 'process-result.json'
    write_json(status_path, status)
    try:
        status['executable_fingerprint'] = file_fingerprint(executable)
        if archive is not None:
            status['archive'] = file_fingerprint(archive)
        write_json(status_path, status)
        status.update(run_process([str(executable), '--lumen-self-test', str(app_output)],
                                  root=Path.cwd(), environment=dict(os.environ),
                                  log_path=diagnostic_output / 'stdout-stderr.log', timeout=timeout))
        if status['timed_out'] or status['returncode'] != 0:
            raise ValueError('Frozen runtime exited nonzero or timed out')
        report = json.loads((app_output / 'result.json').read_text(encoding='utf-8'))
        if report.get('passed') is not True or report.get('frozen') is not True or report.get('platform') != sys.platform:
            raise ValueError('Smoke result must confirm passed=true, frozen=true, and the current native platform')
        if file_fingerprint(executable) != status['executable_fingerprint']:
            raise ValueError('Frozen executable changed during runtime smoke')
        if archive is not None and file_fingerprint(archive) != status['archive']:
            raise ValueError('Native archive changed during runtime smoke')
        status.update(success=True, result_path=str(app_output / 'result.json'))
    except Exception as error:
        status['error'] = f'{type(error).__name__}: {error}'
    write_json(status_path, status)
    print(json.dumps(status), flush=True)
    return 0 if status['success'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--archive', type=Path, help='Exact archive re-extracted for this smoke; required by native CI')
    parser.add_argument('--output', type=Path, default=Path('build/native-smoke'))
    parser.add_argument('--diagnostics', type=Path, default=Path('build/native-smoke-launch'))
    parser.add_argument('--timeout', type=float, default=600)
    args = parser.parse_args(argv)
    try:
        return run_smoke(args.executable, args.output, args.diagnostics, timeout=args.timeout, archive=args.archive)
    except (OSError, ValueError) as error:
        parser.exit(2, f'Cannot start native smoke: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Compare explicit local natural-speech fixtures with base/small × auto/zh.

No downloads, cloud calls, reference prompts, training, or CI integration. Select
only a trusted local whisper.cpp executable. Manifest schema (extra descriptive
fields are retained): {"schema_version": 1, "fixtures": [{"id": "sample",
"audio_path": "relative-to-manifest.wav", "transcript": "verbatim reference",
"provenance": "source URL and selection details", "license": "name and source"}]}.
An optional fixture sha256 is checked before inference. License/provenance fields
are declarations, not legal verification. The output must be a NEW local file.

Each run has a cooperative deadline: the real adapter kills/reaps its active
subprocess when its cancellation event is set. Overall inference time is also
bounded; input hashing, report writing and metric calculation are outside that
budget. Ctrl-C cancels the adapter and preserves completed results. Raw output
means the adapter's parsed segments (it trims segment text), not CLI JSON/logs.
CER uses Unicode code points. Normalization is NFC, casefold, NFC, removal of
Unicode P* punctuation and whitespace only; no Traditional/Simplified conversion,
number expansion, transliteration, symbol removal, or other orthographic mapping.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import sys
import threading
import time
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lumen.asr import ASRConfig, ASRCancelled, transcribe

MAX_FIXTURES = 32
MAX_REFERENCE_CHARACTERS = 10000
MAX_DISTANCE_CELLS = 25_000_000
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_TIMEOUT_SECONDS = 3600
LANGUAGES = ('auto', 'zh')
MODELS = ('base', 'small')


class ComparisonError(ValueError):
    """Invalid local configuration or unsafe output destination."""


def normalize_text(text):
    """Normalize text for CER without silently changing Chinese orthography."""
    text = unicodedata.normalize('NFC', unicodedata.normalize('NFC', text).casefold())
    return ''.join(c for c in text if not c.isspace() and not unicodedata.category(c).startswith('P'))


def edit_distance(reference, hypothesis):
    """Exact code-point Levenshtein distance, O(min(n,m)) auxiliary memory."""
    # Trim exact common ends before checking the bounded dynamic-program budget.
    start = 0
    while start < min(len(reference), len(hypothesis)) and reference[start] == hypothesis[start]:
        start += 1
    reference, hypothesis = reference[start:], hypothesis[start:]
    end = 0
    while end < min(len(reference), len(hypothesis)) and reference[-end - 1] == hypothesis[-end - 1]:
        end += 1
    if end:
        reference, hypothesis = reference[:-end], hypothesis[:-end]
    if len(reference) * len(hypothesis) > MAX_DISTANCE_CELLS:
        raise ComparisonError('CER exceeds the bounded edit-distance work limit.')
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for i, left in enumerate(reference, 1):
        current = [i]
        for j, right in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def score(reference, hypothesis):
    distance = edit_distance(reference, hypothesis)
    return {'edit_distance': distance, 'reference_characters': len(reference),
            'hypothesis_characters': len(hypothesis),
            'cer': distance / len(reference) if reference else None}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _local_path(value):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        return False
    text = str(value)
    if text.startswith(('//', '\\\\')):
        return False
    scheme = re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', text)
    return not scheme or (os.name == 'nt' and bool(re.match(r'^[A-Za-z]:[\\/]', text)))


def local_file(value, label, *, directory=None):
    if not _local_path(value):
        raise ComparisonError(f'{label} must be an explicit existing local file.')
    path = Path(value).expanduser()
    if directory is not None and not path.is_absolute():
        path = directory / path
    try:
        path = path.resolve(strict=True)
        if not path.is_file():
            raise OSError('not a regular file')
    except (OSError, ValueError) as exc:
        raise ComparisonError(f'{label} must be an explicit existing local file: {value}') from exc
    return path


def load_manifest(path):
    path = local_file(path, 'Manifest')
    with path.open('rb') as stream:
        content = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(content) > MAX_MANIFEST_BYTES:
        raise ComparisonError('Manifest exceeds the 1 MiB limit.')
    try:
        def reject_constant(value):
            raise ValueError(f'Nonfinite JSON number: {value}')
        manifest = json.loads(content.decode('utf-8'), parse_constant=reject_constant)
    except (UnicodeError, ValueError) as exc:
        raise ComparisonError(f'Invalid manifest JSON: {exc}') from exc
    if not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1:
        raise ComparisonError('Manifest schema_version must be 1.')
    fixtures = manifest.get('fixtures')
    if not isinstance(fixtures, list) or not 1 <= len(fixtures) <= MAX_FIXTURES:
        raise ComparisonError(f'Manifest must contain 1–{MAX_FIXTURES} fixtures.')
    ids, resolved = set(), []
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            raise ComparisonError('Each fixture must be an object.')
        for key in ('id', 'audio_path', 'transcript', 'provenance', 'license'):
            if not isinstance(fixture.get(key), str) or not fixture[key].strip():
                raise ComparisonError(f'Each fixture needs a nonempty string {key}.')
        if fixture['id'] in ids:
            raise ComparisonError(f'Duplicate fixture id: {fixture["id"]}')
        ids.add(fixture['id'])
        if len(fixture['transcript']) > MAX_REFERENCE_CHARACTERS:
            raise ComparisonError('Reference transcript exceeds 10000 code points.')
        expected_hash = fixture.get('sha256')
        if expected_hash is not None and (not isinstance(expected_hash, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', expected_hash)):
            raise ComparisonError('Fixture sha256 must be a 64-character hexadecimal digest.')
        audio = local_file(fixture['audio_path'], 'Fixture audio', directory=path.parent)
        resolved.append((fixture, audio))
    return path, manifest, resolved


def _positive_timeout(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= MAX_TIMEOUT_SECONDS:
        raise ComparisonError(f'{label} must be positive and at most {MAX_TIMEOUT_SECONDS} seconds.')


def _snapshot(path):
    return {'path': str(path), 'sha256': sha256(path), 'bytes': path.stat().st_size}


def _audit(before):
    records = []
    for old in before:
        try:
            current = _snapshot(Path(old['path']))
            records.append({**current, 'unchanged': current['sha256'] == old['sha256'] and current['bytes'] == old['bytes']})
        except OSError as exc:
            records.append({'path': old['path'], 'sha256': None, 'unchanged': False, 'error': str(exc)})
    return records


class _CancelToken:
    def __init__(self, timeout, external):
        self.timeout, self.external = timeout, external

    def is_set(self):
        return self.timeout.is_set() or self.external.is_set()


def _execute(audio, config, timeout_seconds, cancel, transcriber):
    expired = threading.Event()
    token = _CancelToken(expired, cancel)
    timer = threading.Timer(timeout_seconds, expired.set)
    timer.daemon = True
    result = {'status': 'ok', 'elapsed_seconds': 0.0, 'raw_segments': [],
              'raw_output': None, 'error': None}
    started = time.monotonic()
    timer.start()
    try:
        segments = transcriber(audio, config, cancel=token)
        result['raw_segments'] = [asdict(segment) for segment in segments]
        result['raw_output'] = ''.join(segment.text for segment in segments)
    except KeyboardInterrupt:
        cancel.set()
        result.update(status='cancelled', error={'type': 'KeyboardInterrupt', 'message': 'Interrupted by user.'})
    except Exception as exc:
        result.update(status='cancelled' if isinstance(exc, ASRCancelled) else 'error',
                      error={'type': type(exc).__name__, 'message': str(exc)})
    finally:
        timer.cancel()
        timer.join()
        result['elapsed_seconds'] = time.monotonic() - started
    if expired.is_set() or result['elapsed_seconds'] >= timeout_seconds:
        result['status'] = 'timed_out'
        if result['error'] is None:
            result['error'] = {'type': 'DeadlineExceeded', 'message': 'Run deadline expired.'}
    elif cancel.is_set():
        result['status'] = 'cancelled'
    return result


def _summaries(runs, fixture_count):
    summaries = []
    for model in MODELS:
        for language in LANGUAGES:
            good = [r for r in runs if r['model'] == model and r['language'] == language and r['status'] == 'ok']
            summary = {'model': model, 'language': language, 'successful_fixtures': len(good),
                       'total_fixtures': fixture_count, 'elapsed_seconds': sum(r['elapsed_seconds'] for r in good)}
            for kind in ('raw', 'normalized'):
                distance = sum(r['metrics'][kind]['edit_distance'] for r in good)
                chars = sum(r['metrics'][kind]['reference_characters'] for r in good)
                summary[kind] = {'edit_distance': distance, 'reference_characters': chars,
                                 'cer': distance / chars if chars else None}
            summaries.append(summary)
    return summaries


def run_comparison(*, manifest_path, runtime_path, base_model_path, small_model_path, output_path,
                   timeout_seconds=120, total_timeout_seconds=1800, threads=4,
                   cancel=None, transcriber=None):
    """Run the full matrix; an injected callable is explicitly marked TEST evidence.

    No hypothesis/reference text is supplied to the ASR callable. The only
    arguments are a local audio path, ASRConfig and a cancellation token.
    """
    _positive_timeout(timeout_seconds, 'Per-run timeout')
    _positive_timeout(total_timeout_seconds, 'Total timeout')
    if type(threads) is not int or not 1 <= threads <= 4:
        raise ComparisonError('Threads must be an integer between 1 and 4.')
    manifest_path, manifest, fixtures = load_manifest(manifest_path)
    runtime = local_file(runtime_path, 'Runtime')
    if os.name != 'nt' and not os.access(runtime, os.X_OK):
        raise ComparisonError('Runtime must be executable.')
    models = {'base': local_file(base_model_path, 'Base model'),
              'small': local_file(small_model_path, 'Small model')}
    if models['base'].samefile(models['small']):
        raise ComparisonError('Base and small model paths must identify different files.')
    if not _local_path(output_path):
        raise ComparisonError('Output must be a new local file.')
    output = Path(output_path).expanduser().absolute()
    # Exclusive create below also rejects dangling symlinks and hardlink aliases.
    if os.path.lexists(output):
        raise ComparisonError('Output already exists; refusing to overwrite any input or report.')
    paths = list(dict.fromkeys([manifest_path, runtime, *models.values(), *(p for _, p in fixtures)]))
    before = [_snapshot(path) for path in paths]
    digests = {item['path']: item['sha256'] for item in before}
    for fixture, audio in fixtures:
        if fixture.get('sha256') and fixture['sha256'].lower() != digests[str(audio)]:
            raise ComparisonError(f'Fixture SHA-256 mismatch: {fixture["id"]}')
    test_double = transcriber is not None
    if transcriber is None:
        transcriber = transcribe
    if cancel is None:
        cancel = threading.Event()
    report = {'schema_version': 1, 'status': 'running',
              'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'evidence_kind': 'injected_callable_test' if test_double else 'local_adapter_execution',
              'backend': 'injected callable' if test_double else 'lumen.asr.transcribe',
              'platform': platform.platform(), 'python': platform.python_version(),
              'manifest': manifest, 'inputs_before': before,
              'configuration': {'runtime': str(runtime), 'models': {k: str(v) for k, v in models.items()},
                                'languages': list(LANGUAGES), 'threads': threads,
                                'timeout_seconds': timeout_seconds, 'total_timeout_seconds': total_timeout_seconds},
              'methodology': {'raw_output': 'Concatenated adapter segment text, no inserted separator; adapter trims each segment. CLI JSON/stdout/stderr are not available.',
                              'cer_unit': 'Unicode code point', 'cer_denominator': 'reference character count; null if zero',
                              'normalization': 'NFC, casefold, NFC, remove Unicode P* punctuation and whitespace',
                              'orthographic_normalizations': 'Not implemented; Traditional/Simplified Chinese, symbols, digits and diacritics remain distinct.',
                              'reference_used_as_prompt': False, 'training': False,
                              'deadline_scope': 'Cooperative adapter execution, including decode/inference; hashing, metrics and report I/O excluded.',
                              'provenance_and_license': 'Manifest declarations retained; not independently verified by this runner.',
                              'summary': 'Micro-averaged CER over successful fixtures only; compare coverage before interpreting.'},
              'runs': []}
    def persist(stream):
        stream.seek(0)
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
        stream.truncate()
        stream.flush()
    # Keep the exclusive-created descriptor open. A later path/symlink swap cannot
    # redirect checkpoint writes into an input file.
    with output.open('x', encoding='utf-8') as stream:
        persist(stream)
        execution_seconds = 0.0
        stop_reason = None
        for fixture, audio in fixtures:
            for model in MODELS:
                for language in LANGUAGES:
                    remaining = total_timeout_seconds - execution_seconds
                    if cancel.is_set():
                        stop_reason = stop_reason or 'cancelled'
                    if remaining <= 0:
                        stop_reason = stop_reason or 'total_timeout'
                    record = {'fixture_id': fixture['id'], 'model': model, 'language': language,
                              'reference': fixture['transcript'], 'normalized_reference': normalize_text(fixture['transcript']),
                              'metrics': None}
                    if stop_reason:
                        record.update(status='skipped', skip_reason=stop_reason, elapsed_seconds=0.0,
                                      raw_segments=[], raw_output=None, error=None)
                    else:
                        effective_timeout = min(timeout_seconds, remaining)
                        record['effective_timeout_seconds'] = effective_timeout
                        record.update(_execute(audio, ASRConfig(runtime, models[model], language, threads),
                                               effective_timeout, cancel, transcriber))
                        execution_seconds += record['elapsed_seconds']
                        audit = _audit([item for item in before if item['path'] == str(audio)])
                        record['source_after'] = audit[0]
                        if not audit[0]['unchanged']:
                            record['status'] = 'input_changed'
                            stop_reason = 'input_changed'
                        if record['raw_output'] is not None:
                            record['normalized_output'] = normalize_text(record['raw_output'])
                        if record['status'] == 'ok':
                            try:
                                record['metrics'] = {'raw': score(fixture['transcript'], record['raw_output']),
                                                     'normalized': score(record['normalized_reference'], record['normalized_output'])}
                            except ComparisonError as exc:
                                record.update(status='scoring_error', error={'type': type(exc).__name__, 'message': str(exc)})
                    report['runs'].append(record)
                    persist(stream)
        report['inputs_after'] = _audit(before)
        report['inputs_unchanged'] = all(item['unchanged'] for item in report['inputs_after'])
        report['summary'] = _summaries(report['runs'], len(fixtures))
        report['execution_seconds'] = execution_seconds
        report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        if not report['inputs_unchanged']:
            report['status'] = 'input_changed'
        elif cancel.is_set():
            report['status'] = 'cancelled'
        elif stop_reason == 'total_timeout':
            report['status'] = 'timed_out'
        elif any(run['status'] != 'ok' for run in report['runs']):
            report['status'] = 'completed_with_errors'
        else:
            report['status'] = 'completed'
        persist(stream)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for arg in ('manifest', 'runtime', 'base-model', 'small-model', 'output'):
        parser.add_argument(f'--{arg}', type=Path, required=True)
    parser.add_argument('--timeout-seconds', type=float, default=120)
    parser.add_argument('--total-timeout-seconds', type=float, default=1800)
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args(argv)
    try:
        report = run_comparison(manifest_path=args.manifest, runtime_path=args.runtime,
                                base_model_path=args.base_model, small_model_path=args.small_model,
                                output_path=args.output, timeout_seconds=args.timeout_seconds,
                                total_timeout_seconds=args.total_timeout_seconds, threads=args.threads)
    except (ComparisonError, OSError) as exc:
        print(f'ASR comparison failed: {exc}', file=sys.stderr)
        return 2
    print(json.dumps({'status': report['status'], 'report': str(args.output.absolute()),
                      'inputs_unchanged': report['inputs_unchanged'], 'summary': report['summary']},
                     ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'completed' else 130 if report['status'] == 'cancelled' else 1


if __name__ == '__main__':
    raise SystemExit(main())

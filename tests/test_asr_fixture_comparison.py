"""Deterministic harness tests; injected callables are NOT speech inference."""
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from lumen.asr import ASRCancelled, ASRError, TranscriptSegment, _run
from scripts import compare_asr_fixtures as runner


@pytest.mark.parametrize('reference,hypothesis,distance', [
    ('', '', 0), ('a', '', 1), ('', '猫咪', 2), ('kitten', 'sitting', 3),
    ('你好世界', '你好世间', 1), ('abc', 'xyzabc', 3), ('abc', 'abcc', 1),
    ('😀猫', '😺猫', 1), ('café', 'cafe\u0301', 2), ('abcXYZdef', 'abcQQdef', 3),
])
def test_exact_unicode_codepoint_edit_distance(reference, hypothesis, distance):
    assert runner.edit_distance(reference, hypothesis) == distance
    assert runner.edit_distance(hypothesis, reference) == distance


def test_multilingual_normalization_and_no_orthographic_conversion():
    assert runner.normalize_text(' Café\tSTRASSE，猫！\n') == 'caféstrasse猫'
    assert runner.normalize_text('cafe\u0301 Straße、貓。') == 'caféstrasse貓'
    assert runner.normalize_text('À$＋①１ + 1—2…🙂') == 'à$＋①１+12🙂'
    assert runner.score(runner.normalize_text('猫'), runner.normalize_text('貓'))['cer'] == 1
    assert runner.normalize_text('⼀') != runner.normalize_text('一')  # no NFKC


def test_raw_and_normalized_scores_differ_without_losing_raw_evidence():
    raw = runner.score('你好，World！', '你好world')
    normalized = runner.score(runner.normalize_text('你好，World！'), runner.normalize_text('你好world'))
    assert raw['edit_distance'] == 3
    assert normalized['cer'] == 0
    assert runner.score('', '')['cer'] is None
    assert runner.score('', 'x')['cer'] is None
    assert runner.score('a', 'aaaa')['cer'] == 3  # CER may exceed 1


def test_edit_distance_work_is_bounded(monkeypatch):
    monkeypatch.setattr(runner, 'MAX_DISTANCE_CELLS', 3)
    with pytest.raises(runner.ComparisonError, match='work limit'):
        runner.edit_distance('ab', 'cd')
    assert runner.edit_distance('longprefix猫suffix', 'longprefix貓suffix') == 1


@pytest.fixture
def comparison(tmp_path):
    audio = tmp_path / 'natural.wav'
    audio.write_bytes(b'unit test bytes, not real speech')
    runtime = tmp_path / 'whisper-cli'
    runtime.write_bytes(b'not an actual executable')
    runtime.chmod(0o700)
    base, small = tmp_path / 'base.bin', tmp_path / 'small.bin'
    base.write_bytes(b'base test placeholder')
    small.write_bytes(b'small test placeholder')
    manifest = tmp_path / 'fixtures.json'
    document = {'schema_version': 1, 'reference_page': 'https://example.invalid/dataset', 'fixtures': [
        {'id': 'test-fixture', 'audio_path': audio.name, 'transcript': '你好，World！',
         'provenance': 'Deterministic unit-test placeholder, not natural-speech evidence.',
         'license': 'Unit test', 'sha256': runner.sha256(audio), 'duration_seconds': 1.0}]}
    manifest.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
    return {'manifest_path': manifest, 'runtime_path': runtime, 'base_model_path': base,
            'small_model_path': small, 'output_path': tmp_path / 'comparison.json',
            'timeout_seconds': 1, 'total_timeout_seconds': 5, 'threads': 2}


def _read_manifest(comparison):
    return json.loads(comparison['manifest_path'].read_text(encoding='utf-8'))


def _write_manifest(comparison, data):
    comparison['manifest_path'].write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def test_full_matrix_configuration_and_report_are_explicit(comparison):
    calls = []
    def test_double(audio, config, *, cancel):
        calls.append((audio, config))
        assert not cancel.is_set()
        return [TranscriptSegment(0.25, 1.25, '你好', 'zh'), TranscriptSegment(1.25, 2, 'world', 'zh')]
    report = runner.run_comparison(**comparison, transcriber=test_double)
    assert len(calls) == 4
    assert [(c.model_path.name, c.language, c.threads) for _, c in calls] == [
        ('base.bin', 'auto', 2), ('base.bin', 'zh', 2), ('small.bin', 'auto', 2), ('small.bin', 'zh', 2)]
    assert all(audio == comparison['manifest_path'].parent / 'natural.wav' for audio, _ in calls)
    assert report['evidence_kind'] == 'injected_callable_test'
    assert report['backend'] == 'injected callable'
    assert report['status'] == 'completed'
    assert report['inputs_unchanged'] is True
    assert len(report['inputs_before']) == 5
    assert report['manifest']['reference_page'].endswith('/dataset')
    assert report['manifest']['fixtures'][0]['duration_seconds'] == 1
    assert report['methodology']['reference_used_as_prompt'] is False
    assert report['methodology']['training'] is False
    for run in report['runs']:
        assert run['raw_output'] == '你好world'
        assert run['raw_segments'][0] == {'start_seconds': .25, 'end_seconds': 1.25, 'text': '你好', 'language': 'zh'}
        assert run['metrics']['raw']['edit_distance'] == 3
        assert run['metrics']['normalized']['cer'] == 0
        assert run['source_after']['unchanged'] is True
        assert run['elapsed_seconds'] >= 0
    assert all(s['successful_fixtures'] == 1 and s['normalized']['cer'] == 0 for s in report['summary'])
    saved = json.loads(comparison['output_path'].read_text(encoding='utf-8'))
    assert saved == report


@pytest.mark.parametrize('key,value', [
    ('threads', 0), ('threads', 5), ('threads', True), ('threads', 1.2),
    ('timeout_seconds', 0), ('timeout_seconds', -1), ('timeout_seconds', float('nan')),
    ('timeout_seconds', float('inf')), ('timeout_seconds', 3601), ('timeout_seconds', True),
    ('total_timeout_seconds', 0), ('total_timeout_seconds', 3601),
])
def test_invalid_config_fails_before_inference(comparison, key, value):
    comparison[key] = value
    with pytest.raises(runner.ComparisonError):
        runner.run_comparison(**comparison, transcriber=lambda *a, **k: pytest.fail('must not infer'))
    assert not comparison['output_path'].exists()


@pytest.mark.parametrize('field', ['id', 'audio_path', 'transcript', 'provenance', 'license'])
def test_manifest_requires_explicit_reference_and_provenance(comparison, field):
    data = _read_manifest(comparison)
    del data['fixtures'][0][field]
    _write_manifest(comparison, data)
    with pytest.raises(runner.ComparisonError, match=field):
        runner.load_manifest(comparison['manifest_path'])


@pytest.mark.parametrize('mutation,match', [
    (lambda d: d.update(schema_version=True), 'schema_version'),
    (lambda d: d.update(fixtures=[]), 'fixtures'),
    (lambda d: d['fixtures'].append(d['fixtures'][0]), 'Duplicate'),
    (lambda d: d['fixtures'][0].update(audio_path='https://example.invalid/remote.wav'), 'local file'),
    (lambda d: d['fixtures'][0].update(sha256='bad'), 'sha256'),
    (lambda d: d['fixtures'][0].update(transcript='x' * 10001), '10000'),
])
def test_manifest_validation(comparison, mutation, match):
    data = _read_manifest(comparison)
    mutation(data)
    _write_manifest(comparison, data)
    with pytest.raises(runner.ComparisonError, match=match):
        runner.load_manifest(comparison['manifest_path'])


def test_manifest_size_is_bounded(comparison):
    comparison['manifest_path'].write_bytes(b' ' * (runner.MAX_MANIFEST_BYTES + 1))
    with pytest.raises(runner.ComparisonError, match='1 MiB'):
        runner.load_manifest(comparison['manifest_path'])


def test_expected_source_hash_must_match(comparison):
    (comparison['manifest_path'].parent / 'natural.wav').write_bytes(b'changed')
    with pytest.raises(runner.ComparisonError, match='SHA-256 mismatch'):
        runner.run_comparison(**comparison)
    assert not comparison['output_path'].exists()


def test_distinct_model_files_required(comparison):
    comparison['small_model_path'] = comparison['base_model_path']
    with pytest.raises(runner.ComparisonError, match='different files'):
        runner.run_comparison(**comparison)


@pytest.mark.parametrize('target', ['manifest_path', 'runtime_path', 'base_model_path', 'small_model_path'])
def test_output_cannot_overwrite_inputs(comparison, target):
    comparison['output_path'] = comparison[target]
    original = comparison[target].read_bytes()
    with pytest.raises(runner.ComparisonError, match='overwrite'):
        runner.run_comparison(**comparison)
    assert comparison[target].read_bytes() == original


def test_output_cannot_overwrite_audio_or_existing_report(comparison):
    for destination in (comparison['manifest_path'].parent / 'natural.wav', comparison['output_path']):
        if not destination.exists():
            destination.write_text('previous report')
        original = destination.read_bytes()
        with pytest.raises(runner.ComparisonError, match='overwrite'):
            runner.run_comparison(**{**comparison, 'output_path': destination})
        assert destination.read_bytes() == original


@pytest.mark.skipif(os.name == 'nt', reason='POSIX symlink fixture')
def test_output_cannot_follow_dangling_symlink(comparison):
    comparison['output_path'].symlink_to(comparison['output_path'].parent / 'not-created')
    with pytest.raises(runner.ComparisonError, match='overwrite'):
        runner.run_comparison(**comparison)
    assert not comparison['output_path'].resolve().exists()


def test_backend_errors_preserved_and_later_configurations_continue(comparison):
    def failing(*args, **kwargs):
        raise ASRError('whisper-cli failed (exit 9): exact diagnostic')
    report = runner.run_comparison(**comparison, transcriber=failing)
    assert report['status'] == 'completed_with_errors'
    assert len(report['runs']) == 4
    assert all(r['status'] == 'error' and r['metrics'] is None for r in report['runs'])
    assert report['runs'][0]['error'] == {'type': 'ASRError', 'message': 'whisper-cli failed (exit 9): exact diagnostic'}
    assert all(s['successful_fixtures'] == 0 and s['raw']['cer'] is None for s in report['summary'])


def test_deadline_cancels_and_reaps_real_subprocess(comparison):
    # This is a real process/cancellation test, NOT an ASR-accuracy test.
    def subprocess_double(audio, config, *, cancel):
        _run([sys.executable, '-c', 'import time; time.sleep(30)'], cancel, 'test process')
        pytest.fail('sleep should be cancelled')
    started = time.monotonic()
    report = runner.run_comparison(**{**comparison, 'timeout_seconds': .03}, transcriber=subprocess_double)
    assert time.monotonic() - started < 5
    assert all(r['status'] == 'timed_out' for r in report['runs'])
    assert all(r['error']['type'] == 'ASRCancelled' for r in report['runs'])
    assert report['inputs_unchanged'] is True


def test_total_deadline_skips_remaining_matrix(comparison):
    def waiting(audio, config, *, cancel):
        while not cancel.is_set():
            time.sleep(.002)
        raise ASRCancelled('cancelled at deadline')
    report = runner.run_comparison(**{**comparison, 'total_timeout_seconds': .02}, transcriber=waiting)
    assert report['status'] == 'timed_out'
    assert report['runs'][0]['status'] == 'timed_out'
    assert all(r['status'] == 'skipped' and r['skip_reason'] == 'total_timeout' for r in report['runs'][1:])


def test_pre_cancel_skips_all_and_preserves_report(comparison):
    cancel = threading.Event()
    cancel.set()
    report = runner.run_comparison(**comparison, cancel=cancel, transcriber=lambda *a, **k: pytest.fail('cancelled'))
    assert report['status'] == 'cancelled'
    assert all(r['status'] == 'skipped' for r in report['runs'])
    assert report['inputs_unchanged'] is True


def test_keyboard_interrupt_preserves_result_and_skips_rest(comparison):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    report = runner.run_comparison(**comparison, transcriber=interrupted)
    assert report['status'] == 'cancelled'
    assert report['runs'][0]['error']['type'] == 'KeyboardInterrupt'
    assert all(r['status'] == 'skipped' for r in report['runs'][1:])


def test_source_mutation_is_reported_and_stops_inference(comparison):
    def changed(audio, config, *, cancel):
        audio.write_bytes(b'accidentally changed by test double')
        return [TranscriptSegment(0, 1, '你好', 'zh')]
    report = runner.run_comparison(**comparison, transcriber=changed)
    assert report['status'] == 'input_changed'
    assert report['inputs_unchanged'] is False
    assert report['runs'][0]['raw_output'] == '你好'
    assert report['runs'][0]['status'] == 'input_changed'
    assert all(r['skip_reason'] == 'input_changed' for r in report['runs'][1:])


def test_metric_limit_keeps_raw_output(comparison, monkeypatch):
    monkeypatch.setattr(runner, 'MAX_DISTANCE_CELLS', 1)
    report = runner.run_comparison(**comparison, transcriber=lambda *a, **k: [TranscriptSegment(0, 1, 'completely different')])
    assert report['status'] == 'completed_with_errors'
    assert all(r['status'] == 'scoring_error' and r['raw_output'] == 'completely different' for r in report['runs'])


def test_cli_invalid_configuration_returns_error_without_inference(comparison, capsys):
    argv = []
    for flag, key in [('manifest', 'manifest_path'), ('runtime', 'runtime_path'), ('base-model', 'base_model_path'),
                      ('small-model', 'small_model_path'), ('output', 'output_path')]:
        argv += [f'--{flag}', str(comparison[key])]
    assert runner.main(argv + ['--timeout-seconds', '0']) == 2
    assert 'ASR comparison failed' in capsys.readouterr().err
    assert not comparison['output_path'].exists()


@pytest.mark.parametrize('value', ['https://example.invalid/file', 'file:///tmp/audio.wav',
                                  '//server/share.wav', r'\\server\share.wav', ''])
def test_local_paths_exclude_urls_and_network_shares(value):
    assert not runner._local_path(value)
    with pytest.raises(runner.ComparisonError, match='local file'):
        runner.local_file(value, 'Input')


def test_nonfinite_extra_manifest_metadata_rejected_before_report(comparison):
    data = _read_manifest(comparison)
    data['untrusted_extra_number'] = float('nan')
    _write_manifest(comparison, data)
    with pytest.raises(runner.ComparisonError, match='Nonfinite'):
        runner.run_comparison(**comparison)
    assert not comparison['output_path'].exists()

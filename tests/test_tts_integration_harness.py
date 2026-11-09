"""Runner infrastructure tests only; these do not claim real model acceptance."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import wave

import numpy as np
import pytest

from scripts.verify_tts_setup_integration import (
    AcceptanceError, PACK_VERSION, TEXTS, VOICE_IDS, TTSDialogs,
    TTSProcessRecorder, compare_pcm, local_pack, main, pack_evidence,
    parse_args, sha256, waveform_evidence,
)


def input_args(tmp_path):
    pack = tmp_path / 'existing-pack'
    pack.mkdir(exist_ok=True)
    return ['--model-pack', str(pack), '--output-dir', str(tmp_path / 'evidence')]


def test_explicit_existing_pack_fresh_output_defaults(tmp_path):
    args = parse_args(input_args(tmp_path))
    assert args.model_pack.is_absolute() and args.output_dir.is_absolute()
    assert args.track == 2 and args.timeline_start == 15 and args.timeout == 600
    assert not args.output_dir.exists()
    args.output_dir.mkdir()
    with pytest.raises(SystemExit):
        parse_args(input_args(tmp_path))


def test_both_languages_are_sampled_for_four_documented_ids_only():
    from lumen.voice_catalog import get_voice
    assert PACK_VERSION == 'v1.1' and VOICE_IDS == (0, 2, 3, 58)
    assert [get_voice(PACK_VERSION, sid).name for sid in VOICE_IDS] == ['af_maple', 'bf_vale', 'zf_001', 'zm_009']
    assert set(TEXTS) == {'en', 'zh'}
    assert TEXTS['en'].isascii() and not TEXTS['zh'].isascii()


@pytest.mark.parametrize('value', ['https://example.invalid/model', '//server/share', '\\\\server\\share'])
def test_remote_pack_rejected_before_qt_or_runtime(value):
    with pytest.raises(argparse.ArgumentTypeError, match='local'):
        local_pack(value)


def test_missing_file_or_link_pack_rejected(tmp_path):
    file = tmp_path / 'model.bin'; file.write_bytes(b'not a directory')
    for path in (tmp_path / 'missing', file):
        with pytest.raises(argparse.ArgumentTypeError):
            local_pack(path)
    link = tmp_path / 'link'
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        return
    with pytest.raises(argparse.ArgumentTypeError):
        local_pack(link)


@pytest.mark.parametrize('extra', [
    ['--timeout', 'nan'], ['--timeout', 'inf'], ['--timeout', '-1'], ['--timeout', '0'],
    ['--timeline-start', '-1'], ['--timeline-start', '0'], ['--track', '1'],
])
def test_invalid_limits_rejected(tmp_path, extra):
    with pytest.raises(SystemExit):
        parse_args(input_args(tmp_path) + extra)


def test_output_inside_pack_rejected_and_input_untouched(tmp_path):
    args = input_args(tmp_path)
    with pytest.raises(SystemExit):
        parse_args(args[:-1] + [str(tmp_path / 'existing-pack' / 'new')])
    assert list((tmp_path / 'existing-pack').iterdir()) == []


@pytest.mark.parametrize('value', ['https://example.invalid/output', '//server/share/output', '\\\\server\\share\\output'])
def test_remote_output_rejected(tmp_path, value):
    with pytest.raises(SystemExit):
        parse_args(input_args(tmp_path)[:-1] + [value])


def test_incomplete_pack_fails_closed_without_native_import(tmp_path):
    with pytest.raises(AcceptanceError, match='incomplete'):
        pack_evidence(tmp_path)
    assert not list(tmp_path.iterdir())


def test_unexpected_overflow_question_is_not_approved():
    from PySide6.QtWidgets import QMessageBox
    dialogs = TTSDialogs()
    dialogs.overflow_answers.append(True)
    assert dialogs.question(None, 'Unrelated warning', 'Delete something?') == QMessageBox.StandardButton.Cancel
    assert dialogs.overflow_answers == [True]
    with pytest.raises(AcceptanceError, match='Unexpected UI diagnostics'):
        dialogs.require_no_warnings()
    assert dialogs.question(None, 'Keep the full voice?', 'Actual expected overflow') == QMessageBox.StandardButton.Yes
    assert not dialogs.overflow_answers


def test_overflow_decline_and_accept_are_explicit():
    from PySide6.QtWidgets import QMessageBox
    dialogs = TTSDialogs(); dialogs.overflow_answers.extend([False, True])
    assert dialogs.question(None, 'Keep the full voice?', 'full duration') == QMessageBox.StandardButton.No
    assert dialogs.question(None, 'Keep the full voice?', 'full duration') == QMessageBox.StandardButton.Yes
    assert [row['keep_full_voice'] for row in dialogs.messages] == [False, True]
    assert dialogs.question(None, 'Keep the full voice?', 'unexpected third') == QMessageBox.StandardButton.Cancel


def test_process_observation_forwards_real_process_unchanged(tmp_path):
    observer = TTSProcessRecorder(tmp_path)
    env = dict(os.environ, CUTCAT_TTS_HARNESS='transparent')
    # A harmless Python process accepts trailing argv. This exercises recorder
    # plumbing only; it is explicitly not a speech engine or render fixture.
    command = [sys.executable, '-c', 'import os; print(os.environ["CUTCAT_TTS_HARNESS"]); print(os.getcwd())',
               '-filter_complex', 'test-only-graph[aout]']
    process = observer(command, cwd=tmp_path, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0 and not stderr
    assert stdout.splitlines() == ['transparent', str(tmp_path)]
    assert observer.workers() == []
    assert observer.evidence()[0]['argv'] == command
    assert observer.evidence()[0]['returncode'] == 0
    assert (tmp_path / 'render-graph-1.txt').read_text() == 'test-only-graph[aout]'


def write_test_pcm(path, rate=24000, channels=1, silent=False):
    # Diagnostic unit fixture. It must never enter acceptance report cases.
    values = np.zeros(6000, dtype='<i2') if silent else (6000 * np.sin(np.arange(6000) * .12)).astype('<i2')
    with wave.open(str(path), 'wb') as wav:
        wav.setparams((channels, 2, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(values.tobytes())


def test_measured_waveform_contains_actual_pcm_bounds_and_hash(tmp_path):
    path = tmp_path / 'diagnostic-unit-fixture.wav'; write_test_pcm(path)
    destination = tmp_path / 'waveform.json'
    result = waveform_evidence(path, destination)
    assert result['sample_count'] == 6000 and result['sample_rate'] == 24000
    assert result['sha256'] == sha256(path) and result['rms'] > .1
    data = json.loads(destination.read_text())
    assert len(data['envelope']) == 200
    assert data['source_sha256'] == sha256(path)
    assert all(item['minimum'] <= item['maximum'] for item in data['envelope'])
    assert sum(item['sample_count'] for item in data['envelope']) == 6000
    assert [item['start_sample'] for item in data['envelope']] == list(range(0, 6000, 30))


@pytest.mark.parametrize('options', [{'rate': 48000}, {'channels': 2}, {'silent': True}])
def test_invalid_or_silent_waveform_rejected(tmp_path, options):
    path = tmp_path / 'invalid-diagnostic-fixture.wav'; write_test_pcm(path, **options)
    with pytest.raises(AcceptanceError):
        waveform_evidence(path, tmp_path / 'never-accepted.json')


def pcm_example():
    reference = np.random.default_rng(13).normal(0, .1, 6000)
    start, budget = 2400, 9600
    output = np.zeros(budget + 128)
    output[start:start + len(reference)] = reference
    return reference, output, start, budget


def test_pcm_comparison_uses_prescribed_sample_offset_and_reports_padding():
    result = compare_pcm(*pcm_example())
    assert result['exact_offset_correlation'] == pytest.approx(1)
    assert result['best_local_lag_samples'] == 0
    assert result['aac_decoded_padding_samples'] == 128
    assert result['leading_silence_peak'] == 0


@pytest.mark.parametrize('damage', ['shift', 'silence', 'head_noise', 'nan', 'short', 'extra_padding'])
def test_pcm_comparison_rejects_timing_or_waveform_regressions(damage):
    reference, output, start, budget = pcm_example()
    if damage == 'shift': output = np.roll(output, 5)
    elif damage == 'silence': output[:] = 0
    elif damage == 'head_noise': output[0] = .1
    elif damage == 'nan': output[start] = np.nan
    elif damage == 'short': output = output[:budget - 1]
    elif damage == 'extra_padding': output = np.pad(output, (0, 3000))
    with pytest.raises(AcceptanceError):
        compare_pcm(reference, output, start, budget)


def test_failed_workflow_retains_partial_evidence_without_quality_claims(tmp_path, monkeypatch):
    import scripts.verify_tts_setup_integration as runner
    # Isolate main's error-reporting branch. No actual synthesis function is
    # replaced or invoked, and this result can never be mistaken for acceptance.
    monkeypatch.setattr(runner, 'pack_evidence', lambda path: {'unit_test_only': True, 'path': str(path)})
    monkeypatch.setattr(runner, 'runtime_evidence', lambda: {'unit_test_only': True})
    def fail(_args, report):
        report['stage'] = 'deliberate harness unit failure'
        raise AcceptanceError('No inference was executed in this unit test.')
    monkeypatch.setattr(runner, 'run_workflow', fail)
    assert main(input_args(tmp_path)) == 1
    report = json.loads((tmp_path / 'evidence' / 'acceptance.json').read_text())
    assert report['status'] == 'failed' and report['pack_unchanged']
    assert report['stage'] == 'deliberate harness unit failure'
    assert 'No inference was executed' in report['error']
    assert report['sampled_voice_count'] == 4 and report['catalog_voice_count'] == 103
    for key in ('speech_quality_evaluated', 'all_catalog_voices_synthesized', 'physical_speaker_playback_verified',
                'native_windows_macos_validated', 'downloads_performed', 'installations_performed'):
        assert report[key] is False
    assert 'cases' not in report and 'shared_graph' not in report


def test_waveform_irregular_bucket_offsets_are_exact(tmp_path):
    path = tmp_path / 'odd-sample-count.wav'
    values = (5000 * np.sin(np.arange(6103) * .2)).astype('<i2')
    with wave.open(str(path), 'wb') as wav:
        wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        wav.writeframes(values.tobytes())
    waveform_evidence(path, tmp_path / 'waveform.json')
    blocks = json.loads((tmp_path / 'waveform.json').read_text())['envelope']
    offset = 0
    for block in blocks:
        assert block['start_sample'] == offset
        offset += block['sample_count']
    assert offset == 6103


def test_silent_reference_cannot_pass_pcm_comparison():
    reference, output, start, budget = pcm_example()
    reference[:] = 0
    with pytest.raises(AcceptanceError, match='finite measurable PCM'):
        compare_pcm(reference, output, start, budget)

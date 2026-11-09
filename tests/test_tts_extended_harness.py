"""Extended runner infrastructure only: no model, synthesis or listening claim.

Random arrays below are diagnostic timing fixtures, never generated speech and
never inputs to an acceptance report. Error-report tests stop before workflow
execution. Real acceptance remains a separately invoked network-denied run.
"""
from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
import json

import numpy as np
import pytest

import scripts.verify_tts_extended_workflow as runner
from scripts.verify_tts_extended_workflow import (
    AcceptanceError, ALTERNATE_VOICE_ID, CAPTION_FRAMES, ExtendedTTSDialogs,
    LONG_VOICE_ID, LONG_ZH, PACK_VERSION, SHORT_EN, SHORT_ZH, TAKE_TEXTS,
    TAKE_VOICE_IDS, TRIM_SOURCE_IN, VOICE_IDS, audition_specs, check_provenance,
    checked_worker, compare_timeline_pcm, parse_args, sha256, source_evidence,
    timeline_reference,
)


def input_args(tmp_path):
    pack = tmp_path / 'already-existing-pack'
    pack.mkdir(exist_ok=True)
    return ['--model-pack', str(pack), '--output-dir', str(tmp_path / 'fresh-evidence')]


def test_additional_ids_use_exact_upstream_names_and_no_existing_sample_ids():
    from lumen.voice_catalog import get_voice
    from scripts.verify_tts_setup_integration import VOICE_IDS as previous_ids
    assert PACK_VERSION == 'v1.1'
    assert VOICE_IDS == (1, 10, 30, 57, 59, 80, 102)
    assert not set(VOICE_IDS) & set(previous_ids)
    assert [get_voice(PACK_VERSION, sid).name for sid in VOICE_IDS] == [
        'af_sol', 'zf_008', 'zf_047', 'zf_099', 'zm_010', 'zm_054', 'zm_100']
    assert set(TAKE_VOICE_IDS + (ALTERNATE_VOICE_ID, LONG_VOICE_ID)) <= set(VOICE_IDS)


def test_all_additional_ids_receive_short_mandarin_and_sid1_receives_english():
    specs = audition_specs()
    assert len(specs) == 9 and len({spec['name'] for spec in specs}) == 9
    short_zh = [spec for spec in specs if spec['language'] == 'zh' and not spec['long_passage']]
    assert [spec['voice_id'] for spec in short_zh] == list(VOICE_IDS)
    assert all(spec['text'] == SHORT_ZH for spec in short_zh)
    english = [spec for spec in specs if spec['language'] == 'en']
    assert len(english) == 1 and english[0]['voice_id'] == 1 and english[0]['text'] == SHORT_EN
    assert SHORT_EN.isascii() and not SHORT_ZH.isascii()


def test_long_passage_is_genuinely_long_original_text_not_repeated_padding():
    long_cases = [spec for spec in audition_specs() if spec['long_passage']]
    assert len(long_cases) == 1 and long_cases[0]['text'] == LONG_ZH
    assert long_cases[0]['voice_id'] == LONG_VOICE_ID
    assert 150 <= sum('\u4e00' <= char <= '\u9fff' for char in LONG_ZH) <= 220
    sentences = [part for part in LONG_ZH.split('。') if part]
    assert len(sentences) >= 5 and len(sentences) == len(set(sentences))
    assert LONG_ZH.endswith('让故事在安静的河岸边从容结束。')
    assert len(TAKE_TEXTS) == 2 and TAKE_TEXTS[0] != TAKE_TEXTS[1]
    assert CAPTION_FRAMES == 30 and 0 < TRIM_SOURCE_IN < CAPTION_FRAMES


def test_fresh_local_output_and_chinese_font_defaults(tmp_path):
    args = parse_args(input_args(tmp_path))
    assert args.model_pack.is_absolute() and args.output_dir.is_absolute()
    assert args.ui_language == 'zh-CN' and args.font_family == 'Noto Sans CJK SC'
    assert (args.track, args.timeline_start, args.timeout) == (2, 15, 600)
    assert not args.output_dir.exists()
    args.output_dir.mkdir()
    with pytest.raises(SystemExit):
        parse_args(input_args(tmp_path))


@pytest.mark.parametrize('extra', [
    ['--timeout', 'nan'], ['--timeout', 'inf'], ['--timeout', '0'], ['--timeout', '-1'],
    ['--timeline-start', '-1'], ['--timeline-start', '2'], ['--track', '1'], ['--track', '4'],
])
def test_invalid_limits_fail_before_native_runtime(tmp_path, extra):
    with pytest.raises(SystemExit):
        parse_args(input_args(tmp_path) + extra)
    assert not (tmp_path / 'fresh-evidence').exists()


@pytest.mark.parametrize('target', ['https://example.invalid/out', '//server/share/out', '\\\\server\\share\\out'])
def test_remote_output_is_rejected(tmp_path, target):
    with pytest.raises(SystemExit):
        parse_args(input_args(tmp_path)[:-1] + [target])


def test_output_inside_model_pack_is_rejected(tmp_path):
    args = input_args(tmp_path)
    with pytest.raises(SystemExit):
        parse_args(args[:-1] + [str(tmp_path / 'already-existing-pack' / 'new')])
    assert not list((tmp_path / 'already-existing-pack').iterdir())


def test_only_exact_queued_overlap_notice_is_permitted(tmp_path):
    from lumen.i18n import tr
    dialogs = ExtendedTTSDialogs()
    take = tmp_path / 'exact-durable-take.wav'
    dialogs.overlap_paths.append(take)
    dialogs.message('warning', None, tr('Audio lane overlap'), f'Occupied lane. WAV preserved: {take}')
    dialogs.require_no_warnings()
    assert not dialogs.overlap_paths and dialogs.messages[-1]['kind'] == 'expected_overlap'
    dialogs.message('warning', None, tr('Audio lane overlap'), f'Unqueued repeat: {take}')
    with pytest.raises(AcceptanceError, match='Unexpected UI diagnostics'):
        dialogs.require_no_warnings()


@pytest.mark.parametrize('wrong', ['title', 'path', 'kind'])
def test_other_warnings_do_not_consume_queued_overlap(tmp_path, wrong):
    from lumen.i18n import tr
    dialogs = ExtendedTTSDialogs()
    take = tmp_path / 'expected.wav'; dialogs.overlap_paths.append(take)
    title = 'Unrelated failure' if wrong == 'title' else tr('Audio lane overlap')
    text = 'Wrong saved path' if wrong == 'path' else str(take)
    kind = 'unexpected_question' if wrong == 'kind' else 'warning'
    dialogs.message(kind, None, title, text)
    assert dialogs.overlap_paths == [take]
    with pytest.raises(AcceptanceError):
        dialogs.require_no_warnings()


class RecordedProcessFixture:
    """Recorder contract only, not a worker process or synthesis implementation."""
    def __init__(self, rows):
        self.rows = rows

    def evidence(self):
        return self.rows


def worker_row(tmp_path):
    return {'pid': 12345, 'returncode': 0, 'is_tts_worker': True,
            'request': {'text': LONG_ZH, 'voice_id': LONG_VOICE_ID, 'pack_version': PACK_VERSION,
                        'speed': 1.0, 'threads': 2, 'model_dir': str(tmp_path)}}


def test_worker_record_requires_complete_original_long_text(tmp_path):
    row = worker_row(tmp_path)
    result = checked_worker(RecordedProcessFixture([row]), 0, LONG_ZH, LONG_VOICE_ID, tmp_path)
    assert result == {'pid': 12345, 'returncode': 0, 'complete_text_received': True,
                      'request_character_count': len(LONG_ZH)}
    # Earlier real workers and unrelated renderer observations are excluded.
    result = checked_worker(RecordedProcessFixture([row, {'is_tts_worker': False}, row]),
                            1, LONG_ZH, LONG_VOICE_ID, tmp_path)
    assert result['pid'] == row['pid']


@pytest.mark.parametrize('field,value', [
    ('text', LONG_ZH[:-20]), ('voice_id', 3), ('pack_version', 'v1.0'),
    ('speed', 1.25), ('threads', 4), ('model_dir', '/some/other/model'),
], ids=['truncated_text', 'wrong_voice', 'wrong_pack', 'changed_speed', 'changed_threads', 'wrong_model'])
def test_changed_worker_request_cannot_pass(tmp_path, field, value):
    row = worker_row(tmp_path); row['request'][field] = value
    with pytest.raises(AcceptanceError, match='complete original text'):
        checked_worker(RecordedProcessFixture([row]), 0, LONG_ZH, LONG_VOICE_ID, tmp_path)


@pytest.mark.parametrize('status', [None, -9, 1])
def test_incomplete_or_failed_worker_cannot_pass(tmp_path, status):
    row = worker_row(tmp_path); row['returncode'] = status
    with pytest.raises(AcceptanceError, match='exactly one successful'):
        checked_worker(RecordedProcessFixture([row]), 0, LONG_ZH, LONG_VOICE_ID, tmp_path)


@pytest.mark.parametrize('count', [0, 2])
def test_missing_or_unexpected_extra_worker_cannot_pass(tmp_path, count):
    with pytest.raises(AcceptanceError, match='exactly one successful'):
        checked_worker(RecordedProcessFixture([worker_row(tmp_path)] * count),
                       0, LONG_ZH, LONG_VOICE_ID, tmp_path)


def provenance_fixture(tmp_path):
    # An arbitrary identity fixture, deliberately not an audio file.
    path = tmp_path / 'diagnostic-identity.bin'; path.write_bytes(b'identity unit test; not speech')
    report = {'runtime': {'packages': {'sherpa-onnx': {'version': 'unit-version'}}},
              'pack': {'assets': {'model.onnx': 'model-hash', 'voices.bin': 'voices-hash'},
                       'assets_sha256': 'asset-hash'}}
    provenance = {'text': LONG_ZH, 'voice_id': str(LONG_VOICE_ID), 'output_sha256': sha256(path),
                  'engine': 'sherpa-onnx/Kokoro-v1.1', 'engine_version': 'unit-version',
                  'model_sha256': 'model-hash', 'voice_sha256': 'voices-hash',
                  'assets_sha256': 'asset-hash', 'sample_rate': 24000, 'speed': 1.0}
    return path, report, provenance


def test_full_text_and_exact_saved_bytes_are_required_in_provenance(tmp_path):
    path, report, provenance = provenance_fixture(tmp_path)
    check_provenance(provenance, LONG_ZH, LONG_VOICE_ID, path, report)
    path.write_bytes(b'changed identity fixture')
    with pytest.raises(AcceptanceError, match='Provenance differs'):
        check_provenance(provenance, LONG_ZH, LONG_VOICE_ID, path, report)


@pytest.mark.parametrize('field', ['text', 'voice_id', 'engine', 'engine_version',
                                  'model_sha256', 'voice_sha256', 'assets_sha256', 'sample_rate', 'speed'])
def test_altered_provenance_is_rejected(tmp_path, field):
    path, report, provenance = provenance_fixture(tmp_path)
    provenance[field] = 'changed diagnostic value'
    with pytest.raises(AcceptanceError):
        check_provenance(provenance, LONG_ZH, LONG_VOICE_ID, path, report)


def test_caption_provenance_must_preserve_full_original_snapshot(tmp_path):
    from lumen.model import Caption
    path, report, provenance = provenance_fixture(tmp_path)
    cue = Caption('unit-caption', 15, 30, LONG_ZH)
    provenance.update(source_caption_id=cue.id, source_start=cue.start, source_duration=cue.duration)
    check_provenance(provenance, LONG_ZH, LONG_VOICE_ID, path, report, cue)
    provenance['source_duration'] = 15
    with pytest.raises(AcceptanceError, match='caption snapshot'):
        check_provenance(provenance, LONG_ZH, LONG_VOICE_ID, path, report, cue)


def diagnostic_timeline(*, overlap=False, fractional=False):
    from lumen.model import Clip, Media, Project
    # Deliberately synthetic random diagnostic samples, not mocked synthesis.
    rng = np.random.default_rng(8802)
    arrays = {'first': rng.normal(0, .12, 12800), 'second': rng.normal(0, .1, 5001)}
    project = Project(fps_num=30000 if fractional else 30, fps_den=1001 if fractional else 1)
    project.media = [Media(name, f'/unit-fixtures/{name}.wav', name, 1,
                           has_video=False, has_audio=True) for name in arrays]
    project.clips = [Clip('first-clip', 'first', 2, 5, 2, 3),
                     Clip('second-clip', 'second', 3 if overlap else 2, 7 if overlap else 8, 0, 4)]
    project.validate()
    return project, arrays


@pytest.mark.parametrize('fractional', [False, True])
@pytest.mark.parametrize('overlap', [False, True])
def test_reference_uses_actual_source_trim_exact_offsets_and_partial_frame_padding(fractional, overlap):
    from lumen.timing import sample_boundary
    project, sources = diagnostic_timeline(overlap=overlap, fractional=fractional)
    reference, plans = timeline_reference(project, sources)
    expected = np.zeros(sample_boundary(project.duration_frames, project.fps, 48000))
    for clip in project.clips:
        start = sample_boundary(clip.start, project.fps, 48000)
        lo = sample_boundary(int(clip.source_in), project.fps, 48000)
        hi = sample_boundary(int(clip.source_out), project.fps, 48000)
        selected = sources[clip.media_id][lo:hi]
        expected[start:start + len(selected)] += selected
    assert np.array_equal(reference, expected)
    assert plans[0]['plan']['source_start_sample'] == sample_boundary(2, project.fps, 48000)
    assert plans[1]['source_tail_padding_samples'] > 0
    output = np.pad(reference, (0, 128))
    result = compare_timeline_pcm(project, sources, output)
    assert result['exact_offset_correlation'] == pytest.approx(1)
    assert result['best_local_lag_samples'] == 0 and result['aac_decoded_padding_samples'] == 128
    assert len(result['clips']) == 2


def test_reference_adds_overlapping_lanes_without_automatic_normalization():
    project, sources = diagnostic_timeline(overlap=True)
    reference, _ = timeline_reference(project, sources)
    # At 30 fps each frame is exactly 1600 samples; overlap is frame 7 to 8.
    assert np.array_equal(reference[11200:12800], sources['first'][6400:8000] + sources['second'][:1600])
    project.clips[1].gain_db = -6
    quieter, _ = timeline_reference(project, sources)
    assert np.allclose(quieter[11200:12800], sources['first'][6400:8000] + sources['second'][:1600] * 10 ** (-6 / 20))


@pytest.mark.parametrize('damage', ['shift', 'missing_second', 'untrimmed_first', 'early_noise', 'nan', 'short', 'extra_padding'])
def test_two_take_pcm_check_rejects_timing_source_and_missing_voice_errors(damage):
    project, sources = diagnostic_timeline()
    reference, _ = timeline_reference(project, sources)
    output = np.pad(reference, (0, 128))
    if damage == 'shift':
        output = np.roll(output, 5)
    elif damage == 'missing_second':
        output[12800:] = 0
    elif damage == 'untrimmed_first':
        output[8000:12800] = sources['first'][:4800]
    elif damage == 'early_noise':
        output[0] = .1
    elif damage == 'nan':
        output[9000] = np.nan
    elif damage == 'short':
        output = output[:len(reference) - 1]
    elif damage == 'extra_padding':
        output = np.pad(output, (0, 3000))
    with pytest.raises(AcceptanceError):
        compare_timeline_pcm(project, sources, output)


@pytest.mark.parametrize('change', ['muted', 'fade_in', 'fade_out', 'offset', 'speed', 'video_lane'])
def test_reference_fails_closed_on_settings_outside_this_acceptance_scope(change):
    from lumen.timing import ClipTimeMap
    project, sources = diagnostic_timeline()
    clip = project.clips[0]
    if change == 'muted': clip.muted = True
    elif change == 'fade_in': clip.fade_in = 1
    elif change == 'fade_out': clip.fade_out = 1
    elif change == 'offset': project.media[0].audio_offset = .1
    elif change == 'speed': clip.time_map = ClipTimeMap(Fraction(2), Fraction(8), Fraction(2))
    else: clip.track = 0
    with pytest.raises(AcceptanceError, match='supports only'):
        timeline_reference(project, sources)


@pytest.mark.parametrize('values', [np.array([]), np.array([np.nan]), np.zeros((2, 2))])
def test_invalid_reference_array_is_rejected(values):
    project, sources = diagnostic_timeline(); sources['first'] = values
    with pytest.raises(AcceptanceError, match='nonempty finite mono'):
        timeline_reference(project, sources)


def test_compiler_plan_mismatch_cannot_hide_timing_regression(monkeypatch):
    import lumen.audio_timing as audio_timing
    project, sources = diagnostic_timeline()
    original = audio_timing.compile_audio_timing
    monkeypatch.setattr(audio_timing, 'compile_audio_timing', lambda *args: replace(original(*args), start_sample=1))
    with pytest.raises(AcceptanceError, match='independently calculated'):
        timeline_reference(project, sources)


def test_source_identity_covers_runner_helpers_and_production_timing():
    result = source_evidence()
    assert {'scripts/verify_tts_extended_workflow.py', 'scripts/verify_tts_setup_integration.py',
            'lumen/ui_captions.py', 'lumen/tts.py', 'lumen/tts_worker.py',
            'lumen/render.py', 'lumen/audio_timing.py', 'lumen/timing.py'} <= set(result)
    assert all(len(value) == 64 for value in result.values())


def test_failed_workflow_retains_partial_evidence_and_never_claims_real_acceptance(tmp_path, monkeypatch):
    # Only main's error-report plumbing is isolated. No model or synthesis
    # function is replaced or called, and this branch always reports failure.
    monkeypatch.setattr(runner, 'pack_evidence', lambda path: {'unit_test_only': True, 'path': str(path)})
    monkeypatch.setattr(runner, 'runtime_evidence', lambda: {'unit_test_only': True})
    def fail(_args, report):
        report['stage'] = 'deliberate infrastructure-only failure'
        raise AcceptanceError('No inference was executed in this harness unit test.')
    monkeypatch.setattr(runner, 'run_workflow', fail)
    assert runner.main(input_args(tmp_path)) == 1
    report = json.loads((tmp_path / 'fresh-evidence' / 'acceptance.json').read_text())
    assert report['status'] == 'failed' and report['pack_unchanged'] and report['source_unchanged']
    assert report['sampled_voice_count'] == 7 and report['catalog_voice_count'] == 103
    assert report['planned_audition_count'] == 9 and report['planned_saved_take_count'] == 3
    for flag in ('all_catalog_voices_synthesized', 'speech_quality_evaluated', 'spoken_accuracy_evaluated',
                 'physical_speaker_playback_verified', 'native_windows_macos_validated',
                 'downloads_performed', 'installations_performed'):
        assert report[flag] is False
    assert 'No inference was executed' in report['error'] and 'cases' not in report


def test_source_change_during_attempt_forces_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'pack_evidence', lambda _path: {'unit_test_only': True})
    monkeypatch.setattr(runner, 'runtime_evidence', lambda: {'unit_test_only': True})
    identities = iter([{'diagnostic': 'before'}, {'diagnostic': 'after'}])
    monkeypatch.setattr(runner, 'source_evidence', lambda: next(identities))
    def fail(_args, _report):
        raise AcceptanceError('Deliberate infrastructure-only failure.')
    monkeypatch.setattr(runner, 'run_workflow', fail)
    assert runner.main(input_args(tmp_path)) == 1
    report = json.loads((tmp_path / 'fresh-evidence' / 'acceptance.json').read_text())
    assert report['source_unchanged'] is False and 'changed during' in report['input_verification_error']

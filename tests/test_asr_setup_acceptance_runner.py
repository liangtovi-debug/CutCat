"""Runner infrastructure only: these tests never claim native ASR acceptance."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.verify_asr_setup_workflow import (
    AcceptanceError, DialogAutomation, ProcessRecorder, local_file,
    main, normalized_graph, parse_args, require, reviewed_caption_text, sha256, wait_until,
)


def input_args(tmp_path):
    files = []
    for name in ('runtime', 'model', 'synthetic.wav'):
        path = tmp_path / name
        path.write_bytes(b'Runner-input-validation fixture, never executed.')
        files.append(path)
    return ['--runtime', str(files[0]), '--model', str(files[1]),
            '--fixture', str(files[2]), '--output-dir', str(tmp_path / 'new-evidence')]


def test_explicit_inputs_and_new_output_directory(tmp_path):
    args = parse_args(input_args(tmp_path))
    assert args.runtime.is_absolute() and args.fixture.is_absolute()
    assert not args.output_dir.exists()
    assert args.timeline_start == 15
    assert not args.exercise_interruptions
    assert args.ui_language == 'en'
    args.output_dir.mkdir()
    with pytest.raises(SystemExit):
        parse_args(input_args(tmp_path))


def test_ui_locale_is_independent_of_recognition_language(tmp_path):
    args = parse_args(input_args(tmp_path) + ['--language', 'auto', '--ui-language', 'zh-CN'])
    assert args.language == 'auto' and args.ui_language == 'zh-CN'


@pytest.mark.parametrize('value', ['https://example.invalid/model.bin', '//server/share/model.bin',
                                  '\\\\server\\share\\model.bin'])
def test_network_inputs_rejected_before_qt(value):
    with pytest.raises(argparse.ArgumentTypeError, match='local files'):
        local_file(value)


def test_missing_empty_and_directory_inputs_rejected(tmp_path):
    empty = tmp_path / 'empty'
    empty.touch()
    for path in (tmp_path / 'missing', empty, tmp_path):
        with pytest.raises(argparse.ArgumentTypeError):
            local_file(path)


@pytest.mark.parametrize('extra', [['--timeout', 'nan'], ['--timeout', 'inf'],
                                  ['--timeout', '0'], ['--timeline-start', '-1']])
def test_invalid_limits_rejected(tmp_path, extra):
    with pytest.raises(SystemExit):
        parse_args(input_args(tmp_path) + extra)


def test_normalization_preserves_real_cue_identity_and_time(tmp_path):
    from lumen.caption_render import _filter_path
    left = tmp_path / "first's cache"
    right = tmp_path / 'second cache'
    def graph(cache):
        return (f'drawtext=fontfile={_filter_path(cache / "font-hash.ttf")}'
                f':textfile={_filter_path(cache / "cue-hash.txt")}'
                ":enable='gte(n,15)*lt(n,90)'")
    a, b = normalized_graph(graph(left), left), normalized_graph(graph(right), right)
    assert a == b
    assert 'font-hash.ttf' in a and 'cue-hash.txt' in a
    assert "gte(n,15)*lt(n,90)" in a
    assert normalized_graph(graph(left).replace('cue-hash', 'different-cue'), left) != a
    assert normalized_graph(graph(left).replace('lt(n,90)', 'lt(n,91)'), left) != a


def test_process_observation_delegates_unchanged_to_real_process(tmp_path):
    # A local Python process exercises the observer; it is not an ASR fixture.
    recorder = ProcessRecorder(sys.executable, tmp_path)
    environment = dict(os.environ, CUTCAT_RUNNER_OBSERVATION_TEST='observed')
    command = [sys.executable, '-c',
               'import os; print(os.getcwd()); print(os.environ["CUTCAT_RUNNER_OBSERVATION_TEST"])']
    process = recorder(command, cwd=tmp_path, env=environment,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0 and not stderr
    assert stdout.splitlines() == [str(tmp_path), 'observed']
    assert recorder.runtime_processes() == [process]
    assert recorder.evidence() == [{'pid': process.pid, 'argv': command,
                                    'is_runtime': True, 'cwd': str(tmp_path), 'returncode': 0}]


def test_unexpected_dialogs_and_warnings_fail_closed():
    dialogs = DialogAutomation()
    with pytest.raises(AcceptanceError, match='Unexpected open-file'):
        dialogs.open()
    with pytest.raises(AcceptanceError, match='Unexpected save-file'):
        dialogs.save()
    dialogs.open_paths.append(Path('explicit.lumen'))
    assert dialogs.open() == ('explicit.lumen', '')
    dialogs.messages.append({'kind': 'warning', 'title': 'Failure', 'text': 'real error'})
    with pytest.raises(AcceptanceError, match='real error'):
        dialogs.require_no_warnings()
    dialogs.require_no_warnings(1)


def test_wait_and_require_are_not_optimizable_asserts():
    class App:
        calls = 0
        def processEvents(self):
            self.calls += 1
    app = App()
    wait_until(app, lambda: app.calls >= 2, 1, 'fake event-loop unit condition')
    with pytest.raises(AcceptanceError, match='deliberate timeout'):
        wait_until(app, lambda: False, 0, 'deliberate timeout')
    with pytest.raises(AcceptanceError, match='must remain active'):
        require(False, 'must remain active under python -O')


@pytest.mark.parametrize('text', [
    'Hello world. This is a local speech recognition test. We are editing a short video today.',
    '你好，欢迎使用视频编辑器。今天我们测试本地语音识别。这个视频有中文字幕。',
])
def test_explicit_review_edit_wraps_without_inventing_recognition(text):
    edited = reviewed_caption_text(text)
    assert edited.endswith('\n[UI edit test]')
    body = edited.removesuffix('\n[UI edit test]')
    assert ''.join(body.split()) == ''.join(text.split())
    assert max(map(len, body.splitlines())) <= (42 if text.isascii() else 22)


def test_failed_workflow_writes_honest_partial_evidence(tmp_path, monkeypatch):
    import scripts.verify_asr_setup_workflow as runner
    def fail(_args, report):
        report['stage'] = 'deliberate infrastructure failure'
        raise AcceptanceError('No inference was executed in this unit test.')
    monkeypatch.setattr(runner, 'run_workflow', fail)
    assert main(input_args(tmp_path)) == 1
    report = json.loads((tmp_path / 'new-evidence' / 'acceptance.json').read_text())
    assert report['status'] == 'failed'
    assert report['stage'] == 'deliberate infrastructure failure'
    assert 'No inference was executed' in report['error']
    assert 'recognition' not in report and 'shared_graph' not in report
    assert report['inputs']['fixture']['sha256'] == sha256(tmp_path / 'synthetic.wav')
    assert report['inputs_unchanged']

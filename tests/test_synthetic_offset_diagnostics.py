import json
from fractions import Fraction
import subprocess

import pytest

import synthetic_offset_diagnostics as diagnostics
import test_linked_av_split_media as fixture


def test_allowlist_excludes_paths_tags_and_version_suffix(monkeypatch):
    secret = '/private/user/media-secret.mov'
    def read(command):
        if '-version' in command:
            return 'ffmpeg version 9.0.2-private-build ' + secret, 'ok'
        key = 'packets' if '-read_intervals' in command else 'streams'
        return json.dumps({key: [{'start_time': '0.300000', 'pts_time': '-0.100000',
            'time_base': '1/48000', 'pts': secret, 'filename': secret,
            'tags': {'comment': secret}, 'index': 1, 'start_pts': '1\n' + secret}]}), 'ok'
    monkeypatch.setattr(diagnostics, '_read', read)
    result = diagnostics.collect_synthetic_offset_evidence(secret)
    text = json.dumps(result)
    assert secret not in text and 'private-build' not in text
    assert result['ffmpeg_version'] == '9.0.2'
    assert result['a:0']['stream'] == {'index': '1', 'start_time': '0.300000', 'time_base': '1/48000'}
    assert result['a:0']['packet'] == {'pts_time': '-0.100000'}


@pytest.mark.parametrize('failure', ['timeout', 'process_error', 'output_limit'])
def test_read_failures_do_not_publish_command_or_stderr(monkeypatch, failure):
    def run(command, **kwargs):
        assert kwargs['stderr'] == subprocess.DEVNULL and kwargs['timeout'] == 5
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(['/private/secret'], 5)
        kwargs['stdout'].write(b'x' * (65537 if failure == 'output_limit' else 0))
        return subprocess.CompletedProcess(command, 1 if failure == 'process_error' else 0)
    monkeypatch.setattr(subprocess, 'run', run)
    assert diagnostics._read(['/private/secret']) == (None, failure)


@pytest.mark.parametrize('offset', [.3, -.1])
def test_real_synthetic_mismatch_keeps_assertion_and_records_timestamps(tmp_path, monkeypatch, offset):
    original = fixture.probe
    def wrong_probe(path):
        media = original(path)
        media.audio_offset = 0
        return media
    monkeypatch.setattr(fixture, 'probe', wrong_probe)
    with pytest.raises(AssertionError) as caught:
        fixture.split_source(tmp_path, Fraction(24), offset)
    evidence = json.loads(caught.value.__notes__[0])
    assert str(tmp_path) not in caught.value.__notes__[0]
    for role in ('v:0', 'a:0'):
        assert evidence[role]['stream_status'] == evidence[role]['packet_status'] == 'ok'
        assert 'pts_time' in evidence[role]['packet']
    v = evidence['v:0']['stream']; a = evidence['a:0']['stream']
    assert float(a['start_time']) - float(v['start_time']) == offset
    assert evidence['ffmpeg_version'] != 'unavailable'


def test_successful_fixture_does_not_run_diagnostics(tmp_path, monkeypatch):
    def forbidden(path):
        pytest.fail('Diagnostics must be failure-only')
    monkeypatch.setattr(fixture, 'collect_synthetic_offset_evidence', forbidden)
    media, _, _ = fixture.split_source(tmp_path, Fraction(24), .3)
    assert media.audio_offset == .3


def test_diagnostic_exception_cannot_replace_original_assertion(tmp_path, monkeypatch):
    original = fixture.probe
    def wrong_probe(path):
        media = original(path)
        media.audio_offset = 0
        return media
    def unavailable(path):
        raise RuntimeError('/private/secret')
    monkeypatch.setattr(fixture, 'probe', wrong_probe)
    monkeypatch.setattr(fixture, 'collect_synthetic_offset_evidence', unavailable)
    with pytest.raises(AssertionError) as caught:
        fixture.split_source(tmp_path, Fraction(24), .3)
    assert json.loads(caught.value.__notes__[0]) == {
        'kind': 'synthetic_split_source_offset', 'status': 'unavailable'}


def test_invalid_json_is_reported_without_raw_output(monkeypatch):
    monkeypatch.setattr(diagnostics, '_read', lambda command: ('/private/secret', 'ok'))
    evidence = diagnostics.collect_synthetic_offset_evidence('/private/secret')
    assert '/private/secret' not in json.dumps(evidence)
    for role in ('v:0', 'a:0'):
        assert evidence[role]['stream_status'] == evidence[role]['packet_status'] == 'invalid_json'

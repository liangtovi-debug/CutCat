"""Decoded EOF evidence, not a blanket ceil of container metadata."""
from contrast_legacy_fixture import strip_contrast_pivot_field
from dataclasses import replace
from fractions import Fraction
import json
import subprocess
from types import SimpleNamespace

import pytest

from lumen import media as inspection
from lumen.media import ffmpeg_binary, ffmpeg_available, probe
from lumen.model import Clip, Editor, Media, Project
from lumen.render import RenderEngine
from lumen.timing import ClipTimeMap


def ff(*args):
    return subprocess.run([ffmpeg_binary(), '-v', 'error', '-y', *map(str, args)],
                          check=True, capture_output=True, timeout=40).stdout


def frames(path, rate=None):
    filters = ['setpts=PTS-STARTPTS']
    if rate is not None:
        filters += [f'fps={rate}:start_time=0']
    return ff('-i', path, '-vf', ','.join(filters), '-an', '-pix_fmt', 'rgb24',
              '-fps_mode', 'passthrough', '-f', 'rawvideo', 'pipe:1')


@pytest.mark.skipif(not ffmpeg_available(), reason='External FFmpeg required')
@pytest.mark.parametrize('source_rate,project_rate', [
    ('30000/1001', '30000/1001'), ('24000/1001', '24000/1001'),
    ('60000/1001', '60000/1001'), ('25', '25'),
    ('30000/1001', '30'), ('24000/1001', '30'), ('30000/1001', '60'),
])
def test_real_tail_preview_export_and_trim(tmp_path, source_rate, project_rate):
    source = tmp_path / 'source.mkv'
    ff('-f', 'lavfi', '-i', f'color=black:s=64x48:r={source_rate}',
       '-vf', "drawbox=color=white:t=fill:enable='eq(n,23)'", '-frames:v', 24,
       '-c:v', 'ffv1', source)
    rate = Fraction(project_rate)
    m = probe(source)
    p = Project(width=64, height=48, fps_num=rate.numerator, fps_den=rate.denominator, media=[m])
    oracle = frames(source, project_rate)
    expected = len(oracle) // (64 * 48 * 3)
    assert m.decoded_video_end_num is not None
    assert p.media_frames(m) == expected
    p.clips = [Clip('tail', m.id, 0, 0, 0, expected)]
    p.validate()
    restored = Project.from_dict(p.to_dict())
    assert restored.media_frames(restored.media[0]) == expected
    engine = RenderEngine(timeout=60)
    try:
        for result in (engine.preview(p), engine.render(p, tmp_path / 'export.mp4')):
            decoded = frames(result)
            assert len(decoded) == len(oracle)
            assert min(decoded[-64 * 48 * 3:]) > 230  # real final white frame, not black padding
        assert engine.frame(p, expected - 1, width=64).startswith(b'\x89PNG')
        # Exact tail selection plus half-speed sampling must remain in bounds.
        p.clips[0] = Clip('tail', m.id, 0, 0, 0, 2,
                         time_map=ClipTimeMap(Fraction(expected - 1), Fraction(expected), Fraction(1, 2)))
        p.validate()
        assert min(frames(engine.render(p, tmp_path / 'trim.mp4'))) > 230
        p.clips[0].time_map = ClipTimeMap(Fraction(expected - 1), Fraction(expected + 1))
        with pytest.raises(ValueError):
            p.validate()
    finally:
        engine.close()


def test_legacy_audio_and_longer_audio_are_not_rounded_up():
    legacy = Media('m', 'm', 'm', .8, fps_num=30000, fps_den=1001)
    p = Project(fps_num=30000, fps_den=1001)
    assert p.media_frames(legacy) == 23
    assert p.media_frames(replace(legacy, decoded_video_end_num=4, decoded_video_end_den=5)) == 24
    longer_audio = replace(legacy, duration=2, has_audio=True,
                           decoded_video_end_num=4, decoded_video_end_den=5)
    assert p.media_frames(longer_audio) == 59
    assert p.media_frames(replace(legacy, has_video=False)) == 23


@pytest.mark.parametrize('updates', [
    {'decoded_video_end_num': 4}, {'decoded_video_end_den': 5},
    {'decoded_video_end_num': True, 'decoded_video_end_den': 5},
    {'decoded_video_end_num': 4, 'decoded_video_end_den': 0},
    {'decoded_video_end_num': 8, 'decoded_video_end_den': 10},
    {'decoded_video_end_num': 4, 'decoded_video_end_den': 5, 'has_video': False},
    {'decoded_video_end_num': 100, 'decoded_video_end_den': 1},
])
def test_invalid_endpoint_rejected(updates):
    with pytest.raises(ValueError):
        Project(media=[replace(Media('m', 'm', 'm', .8), **updates)]).validate()


def test_legacy_format_cannot_smuggle_endpoint():
    p = Project(media=[Media('m', 'm', 'm', .8, decoded_video_end_num=4, decoded_video_end_den=5)])
    data = p.to_dict(); data['version'] = 14; strip_contrast_pivot_field(data)
    if data['version'] < 17:
        for old_clip in data.get('clips', []):
            old_clip.get('grade', {}).pop('hue_hue_curve', None)
            old_clip.get('grade', {}).pop('hue_sat_curve', None)
            old_clip.get('grade', {}).pop('qualifier', None)
    if data['version'] < 16:
        for old_clip in data.get('clips', []): old_clip.pop('transform', None)
    with pytest.raises(ValueError, match='Legacy projects'):
        Project.from_dict(data)


@pytest.mark.parametrize('payload,stderr', [
    ({'frames': []}, b''),
    ({'frames': [{'best_effort_timestamp': 767}]}, b''),
    ({'frames': [{'duration': 33}]}, b''),
    ({'frames': [{'best_effort_timestamp': 767, 'duration': 33}]}, b'decode error'),
    ({'frames': [{'best_effort_timestamp': 767, 'duration': 33},
                 {'best_effort_timestamp': 766, 'duration': 33}]}, b''),
])
def test_missing_or_damaged_tail_preserves_legacy(monkeypatch, payload, stderr):
    def run(command, **kwargs):
        assert kwargs['timeout'] == 10
        kwargs['stdout'].write(json.dumps(payload).encode())
        return SimpleNamespace(returncode=0, stderr=stderr)
    monkeypatch.setattr(inspection.subprocess, 'run', run)
    assert inspection._decoded_video_end('source', {'index': 0, 'time_base': '1/1000'}, 0, .8) is None


def test_variable_rate_nonzero_origin_is_not_guessed(monkeypatch):
    def run(command, **kwargs):
        kwargs['stdout'].write(json.dumps({'frames': [
            {'best_effort_timestamp': 5000, 'duration': 100},
            {'best_effort_timestamp': 5100, 'duration': 300},
            {'best_effort_timestamp': 5400, 'duration': 40},
        ]}).encode())
        return SimpleNamespace(returncode=0, stderr=b'')
    monkeypatch.setattr(inspection.subprocess, 'run', run)
    assert inspection._decoded_video_end('source', {'index': 2, 'time_base': '1/1000', 'start_pts': 5000, 'avg_frame_rate': '30/1'}, 5, .44) is None


@pytest.mark.skipif(not ffmpeg_available(), reason='External FFmpeg required')
@pytest.mark.parametrize('offset', [0, 5])
def test_real_vfr_and_shifted_timestamps_match_normalized_decode(tmp_path, offset):
    path = tmp_path / 'variable.mkv'
    ff('-f', 'lavfi', '-i', 'testsrc2=s=64x48:r=30:d=1',
       '-vf', "select='not(mod(n,3))+eq(n,29)'", '-fps_mode', 'vfr',
       '-output_ts_offset', offset, '-c:v', 'ffv1', path)
    m = probe(path)
    assert m.decoded_video_end_num is None  # VFR EOF extrapolation is outside this bounded CFR repair
    Project(media=[m]).validate()


@pytest.mark.skipif(not ffmpeg_available(), reason='External FFmpeg required')
def test_real_longer_audio_not_truncated(tmp_path):
    path = tmp_path / 'long-audio.mkv'
    ff('-f', 'lavfi', '-i', 'color=white:s=64x48:r=30000/1001:d=0.8',
       '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
       '-c:v', 'ffv1', '-c:a', 'pcm_s16le', path)
    m = probe(path)
    p = Project(fps_num=30000, fps_den=1001, media=[m])
    assert p.media_frames(m) == 59
    assert m.has_audio and Fraction(m.decoded_video_end_num, m.decoded_video_end_den) < 1


def test_tail_timeout_and_oversized_output_fail_closed(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 10)
    monkeypatch.setattr(inspection.subprocess, 'run', timeout)
    assert inspection._decoded_video_end('source', {'index': 0, 'time_base': '1/1000'}, 0, .8) is None
    def oversized(*args, **kwargs):
        kwargs['stdout'].write(b' ' * (1024 * 1024 + 1))
        return SimpleNamespace(returncode=0, stderr=b'')
    monkeypatch.setattr(inspection.subprocess, 'run', oversized)
    assert inspection._decoded_video_end('source', {'index': 0, 'time_base': '1/1000'}, 0, .8) is None


@pytest.mark.skipif(not ffmpeg_available(), reason='External FFmpeg required')
def test_real_cfr_nonzero_origin_uses_relative_decoded_extent(tmp_path):
    path = tmp_path / 'shifted.mkv'
    ff('-f', 'lavfi', '-i', 'testsrc2=s=64x48:r=30000/1001',
       '-frames:v', 24, '-output_ts_offset', 5, '-c:v', 'ffv1', path)
    m = probe(path)
    p = Project(fps_num=30000, fps_den=1001, media=[m])
    assert m.decoded_video_end_num is not None and m.duration < 1
    assert p.media_frames(m) == len(frames(path, '30000/1001')) // (64 * 48 * 3) == 24
    p.validate()


def test_malformed_timebase_fails_closed():
    assert inspection._decoded_video_end('source', {'time_base': '1/0'}, 0, .8) is None


@pytest.mark.skipif(not ffmpeg_available(), reason='External FFmpeg required')
@pytest.mark.parametrize('count', [1, 2, 7, 25, 61])
def test_short_cfr_extent_matches_actual_normalized_frames(tmp_path, count):
    path = tmp_path / 'short.mkv'
    ff('-f', 'lavfi', '-i', 'testsrc2=s=64x48:r=30000/1001',
       '-frames:v', count, '-c:v', 'ffv1', path)
    m = probe(path)
    p = Project(fps_num=30000, fps_den=1001, media=[m])
    assert p.media_frames(m) == len(frames(path, '30000/1001')) // (64 * 48 * 3) == count


@pytest.mark.parametrize('anchor,duration,seek', [(0, .066, None), (5, .8, None),
                                                 (0, 2, None), (0, 2.1, '0.100000000%'),
                                                 (5, 4, '7.000000000%')])
def test_tail_seeks_only_when_skipping_a_long_prefix(anchor, duration, seek):
    """A seek to zero is not a sequential read; it can lose short-file frames."""
    calls = []
    origin = int(anchor * 1000)
    def run(command, **kwargs):
        calls.append(command)
        actual = command[command.index('-read_intervals') + 1] if '-read_intervals' in command else None
        assert actual == seek
        assert kwargs['timeout'] == 10
        # Model a demuxer with empty output after an unnecessary short seek.
        payload = {'frames': [] if duration <= 2 and actual is not None else [
            {'best_effort_timestamp': origin, 'duration': 33},
            {'best_effort_timestamp': origin + 33, 'duration': 33}]}
        kwargs['stdout'].write(json.dumps(payload).encode())
        return SimpleNamespace(returncode=0, stderr=b'')
    result = inspection._decoded_video_end('source', {
        'index': 0, 'time_base': '1/1000', 'start_pts': origin,
        'avg_frame_rate': '30000/1001'}, anchor, duration, run_process=run)
    assert result == Fraction(33, 500)
    assert len(calls) == 1  # Never retry an empty or damaged tail.


@pytest.mark.skipif(not ffmpeg_available(), reason='External FFmpeg required')
@pytest.mark.parametrize('count,offset', [(1, 0), (2, 0), (24, 0), (2, 5), (90, 0)])
def test_sequential_short_and_seeked_long_endpoints_match_real_decode(tmp_path, count, offset):
    path = tmp_path / 'raw-source.mkv'
    encoded = subprocess.run([ffmpeg_binary(), '-v', 'error', '-y',
        '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '16x16', '-r', '30000/1001',
        '-i', 'pipe:0', '-frames:v', str(count), '-output_ts_offset', str(offset),
        '-c:v', 'ffv1', str(path)], input=bytes([100]) * (16 * 16 * 3 * count),
        capture_output=True, timeout=40)
    assert encoded.returncode == 0
    actual = len(frames(path, '30000/1001')) // (16 * 16 * 3)
    assert actual == count
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.run(command, **kwargs)
    m = inspection._probe(path, run_process=run)
    assert m.decoded_video_end_num is not None
    tails = [command for command in calls if '-show_frames' in command]
    assert len(tails) == 1
    # Matroska's shifted format.duration includes its origin; retain the
    # existing conservative long-file path when metadata overstates length.
    assert ('-read_intervals' in tails[0]) == (count == 90 or offset == 5)
    p = Project(fps_num=30000, fps_den=1001, media=[m],
                clips=[Clip('whole', m.id, 0, 0, 0, count)])
    assert p.media_frames(m) == actual
    p.validate()
    p.clips[0].duration = count + 1
    with pytest.raises(ValueError, match='exceeds source duration'):
        p.validate()

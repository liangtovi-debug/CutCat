"""Independently prove synthetic linked-edit sources retain A/V timestamps."""
from fractions import Fraction
import json
import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from lumen.media import ffmpeg_available, ffprobe_binary
from test_linked_av_pcm import marked_source
from test_linked_av_roll_media import sources
from test_linked_audio_roll_media import short_sources


@pytest.mark.skipif(not ffmpeg_available(), reason='Local FFmpeg/ffprobe required')
@pytest.mark.parametrize('factory', ['marked', 'roll', 'audio_roll'])
@pytest.mark.parametrize('offset', [-.113, 0, .137])
@pytest.mark.parametrize('rate', ['30000/1001', '60000/1001'])
def test_linked_fixture_stream_and_first_packet_offsets(tmp_path, factory, offset, rate):
    fps = Fraction(rate)
    root = tmp_path / 'synthetic'
    if factory != 'audio_roll':
        root.mkdir()
    if factory == 'marked':
        media = [marked_source(root, fps, offset).media]
    elif factory == 'roll':
        media = [item.media for item in sources(root, fps, offset)]
    else:
        pair, _ = short_sources(root, fps, offset)
        media = [item.media for item in pair]
    # Each factory already requires full original RGB16 and stereo PCM equality.
    # Independently read stream and packet PTS; never use production probe for
    # the timestamp oracle, or a value derived from the edited project.
    evidence = []
    for index, item in enumerate(media):
        expected = Fraction(str(offset if index == 0 else -.113))
        times = {}
        raw_times = {}
        for role in ('v:0', 'a:0'):
            result = subprocess.run([ffprobe_binary(), '-v', 'error',
                '-select_streams', role, '-read_intervals', '%+#1',
                '-show_entries', 'stream=start_pts,time_base:packet=pts',
                '-of', 'json', '-protocol_whitelist', 'file,pipe', item.path],
                capture_output=True, timeout=10, check=True)
            assert not result.stderr
            data = json.loads(result.stdout)
            stream = data['streams'][0]
            tb = Fraction(stream['time_base'])
            frame_result = subprocess.run([ffprobe_binary(), '-v', 'error',
                '-select_streams', role, '-read_intervals', '%+#1',
                '-show_entries', 'frame=pts,best_effort_timestamp', '-of', 'json',
                '-protocol_whitelist', 'file,pipe', item.path],
                capture_output=True, timeout=10, check=True)
            assert not frame_result.stderr
            first_frame = json.loads(frame_result.stdout)['frames'][0]
            assert int(first_frame['pts']) == int(first_frame['best_effort_timestamp'])
            raw_times[role] = {'time_base': str(tb),
                'stream_start_pts': int(stream['start_pts']),
                'first_packet_pts': int(data['packets'][0]['pts']),
                'first_frame_pts': int(first_frame['pts']),
                'first_frame_best_effort_timestamp': int(first_frame['best_effort_timestamp'])}
            times[role] = (int(stream['start_pts']) * tb,
                           int(data['packets'][0]['pts']) * tb,
                           int(first_frame['pts']) * tb)
        assert times['a:0'][0] - times['v:0'][0] == expected
        assert times['a:0'][1] - times['v:0'][1] == expected
        assert times['a:0'][2] - times['v:0'][2] == expected
        assert item.audio_offset == pytest.approx(float(expected), abs=1e-9)
        evidence.append({'source_index': index, 'expected_offset': str(expected),
                         'times': {role: list(map(str, values)) for role, values in times.items()},
                         'raw_timestamps': raw_times,
                         'source_sha256': hashlib.sha256(Path(item.path).read_bytes()).hexdigest()})
    directory = os.environ.get('AUTO_FRAME_REVIEW_EVIDENCE')
    if directory:
        destination = Path(directory)
        destination.mkdir(parents=True, exist_ok=True)
        name = f'linked-offset-{factory}-{fps.numerator}-{fps.denominator}-{offset}.json'
        (destination / name).write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')

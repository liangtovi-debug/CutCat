"""Real AAC packets survive an intentionally underestimated fixture header."""
import json
import math
import subprocess

import numpy as np
import pytest

from lumen.media import ffprobe_binary
from synthetic_aac_tail import understate_audio_duration
from test_exact_audio_cache_backend import origin_oracle, source


def evidence(path):
    return json.loads(subprocess.check_output([
        ffprobe_binary(), '-v', 'error', '-select_streams', 'a:0',
        '-show_entries', 'stream=duration:packet=data_hash',
        '-show_data_hash', 'sha256', '-of', 'json', str(path)], text=True))


@pytest.mark.parametrize('rate', [22050, 44100, 96000])
def test_understated_duration_preserves_aac_packets_and_real_audible_tail(tmp_path, rate):
    path = source(tmp_path, rate, 'aac', 1)
    before = evidence(path)
    understate_audio_duration(path)
    after = evidence(path)
    assert before['packets']
    assert [packet['data_hash'] for packet in after['packets']] == [
        packet['data_hash'] for packet in before['packets']]
    assert all(packet['data_hash'].startswith('SHA256:') for packet in after['packets'])
    assert float(after['streams'][0]['duration']) < float(before['streams'][0]['duration'])
    declared = math.ceil(float(after['streams'][0]['duration']) * 48000)
    decoded = np.frombuffer(origin_oracle(path), dtype='<f4').reshape(-1, 2)
    assert len(decoded) > declared
    assert np.max(np.abs(decoded[declared:]), initial=0) > .001

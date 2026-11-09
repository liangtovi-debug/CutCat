"""Decode and audit the actual bundled original WAVs, not only metadata."""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import wave

import numpy as np
import pytest

from scripts.create_original_sounds import LICENSE, SAMPLE_RATE, SOUNDS, generate, pcm16


ASSETS = Path(__file__).resolve().parents[1] / 'lumen' / 'assets' / 'sounds'
CATALOG = json.loads((ASSETS / 'catalog.json').read_text(encoding='utf-8'))


def test_catalog_schema_and_original_provenance():
    assert set(CATALOG) == {'version', 'sounds'}
    assert type(CATALOG['version']) is int and CATALOG['version'] == 1
    sounds = CATALOG['sounds']
    assert len(sounds) == 24
    assert Counter(item['category'] for item in sounds) == {
        'ui': 5, 'transitions': 5, 'impacts': 5, 'notifications': 5, 'atmosphere': 4,
    }
    assert len({item['id'] for item in sounds}) == 24
    assert len({item['file'] for item in sounds}) == 24
    assert len({item['sha256'] for item in sounds}) == 24
    assert {item['file'] for item in sounds} <= {path.name for path in ASSETS.glob('*.wav')}
    for item in sounds:
        assert set(item) == {
            'id', 'name_en', 'name_zh', 'category', 'tags', 'file', 'sha256',
            'sample_rate', 'sample_count', 'peak_dbfs', 'license', 'origin',
        }
        assert re.fullmatch(r'[a-z][a-z0-9_]+', item['id'])
        assert item['file'] == f"{item['id']}.wav"
        assert item['name_en'].isascii() and item['name_en'].strip()
        assert re.search(r'[\u4e00-\u9fff]', item['name_zh'])
        assert len(item['tags']) == len(set(item['tags'])) >= 4
        assert any(tag.isascii() for tag in item['tags'])
        assert any(re.search(r'[\u4e00-\u9fff]', tag) for tag in item['tags'])
        assert re.fullmatch(r'[a-f0-9]{64}', item['sha256'])
        assert item['license'] == LICENSE
        assert item['origin'] == 'procedural'
    license_text = (ASSETS / 'LICENSE.txt').read_text(encoding='utf-8')
    assert 'CC0-1.0' in license_text
    assert 'https://creativecommons.org/publicdomain/zero/1.0/legalcode' in license_text
    assert 'without warranties' in license_text


@pytest.mark.parametrize('item', CATALOG['sounds'], ids=lambda item: item['id'])
def test_decoded_sound_is_valid_safe_non_silent_and_matches_catalog(item):
    path = ASSETS / item['file']
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == item['sha256']
    assert raw[:4] == b'RIFF' and raw[8:12] == b'WAVE'
    with wave.open(str(path), 'rb') as source:
        assert source.getnchannels() == 1
        assert source.getsampwidth() == 2
        assert source.getcomptype() == 'NONE'
        assert source.getframerate() == SAMPLE_RATE == item['sample_rate']
        assert source.getnframes() == item['sample_count']
        assert 0.08 <= source.getnframes() / source.getframerate() <= 8.0
        frames = source.readframes(source.getnframes() + 1)
        assert len(frames) == 2 * source.getnframes()
        assert source.readframes(1) == b''
    samples = np.frombuffer(frames, dtype='<i2').astype(np.float64) / 32768.0
    assert np.isfinite(samples).all()
    peak = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(samples * samples)))
    assert 0.10 <= peak <= 0.5  # At least 6 dB of headroom; not a silent placeholder.
    assert rms > 0.005
    assert abs(float(np.mean(samples))) < 0.005
    assert np.count_nonzero(samples) > len(samples) * 0.45
    assert math.isclose(20 * math.log10(peak), item['peak_dbfs'], abs_tol=0.000001)
    assert item['peak_dbfs'] <= -6.0205
    assert samples[0] == samples[-1] == 0.0
    # Both ends must approach silence smoothly, not only force one zero sample.
    assert np.max(np.abs(samples[:24])) < peak * 0.025
    assert np.max(np.abs(samples[-24:])) < peak * 0.025
    assert np.max(np.abs(np.diff(samples[:24]))) < 0.005
    assert np.max(np.abs(np.diff(samples[-24:]))) < 0.005


def test_regeneration_is_byte_identical_and_has_no_extra_outputs(tmp_path):
    paths = generate(tmp_path)
    assert len(paths) == 25
    assert {path.name for path in paths} == {item['file'] for item in CATALOG['sounds']} | {'catalog.json'}
    for path in paths:
        assert path.read_bytes() == (ASSETS / path.name).read_bytes(), path.name
    assert {path.name for path in tmp_path.iterdir()} == {path.name for path in paths}


def test_decoded_waveforms_are_not_scaled_or_time_stretched_copies():
    profiles = []
    for item in CATALOG['sounds']:
        with wave.open(str(ASSETS / item['file']), 'rb') as source:
            samples = np.frombuffer(source.readframes(source.getnframes()), dtype='<i2').astype(np.float64)
        # Compare decoded audio after removing gain and duration differences,
        # so metadata changes alone cannot make a duplicated effect look unique.
        profile = np.interp(np.linspace(0, len(samples) - 1, 4096), np.arange(len(samples)), samples)
        profile -= profile.mean()
        profile /= np.linalg.norm(profile)
        profiles.append(profile)
    correlations = np.abs(np.asarray(profiles) @ np.asarray(profiles).T)
    np.fill_diagonal(correlations, 0.0)
    assert np.max(correlations) < 0.98


def test_synthesis_does_not_depend_on_global_random_state_or_order():
    spec = next(sound for sound in SOUNDS if sound.id == 'atmosphere_rain')
    before = pcm16(spec)
    state = np.random.get_state()
    try:
        np.random.seed(123456)
        np.random.uniform(size=1000)
        pcm16(SOUNDS[0])
        after = pcm16(spec)
    finally:
        np.random.set_state(state)
    np.testing.assert_array_equal(before, after)

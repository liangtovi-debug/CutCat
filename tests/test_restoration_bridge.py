"""Contract and failure-path tests for the owned normalized float bridge."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import threading

import numpy as np
import pytest

from lumen import color, color_media
from lumen.model import Grade
from lumen.render import RenderCancelled, RenderError
from lumen.restoration_settings import RestorationSettings


def test_creative_bypass_does_not_skip_explicit_normalized_transform():
    rgb = np.full((3, 5, 3), .5, np.float32)
    calls = []
    def operation(value):
        calls.append(value.copy())
        return value + np.float32(.1234567)
    grade = Grade(input_encoding='srgb', exposure=2)
    result = color.apply_grade(rgb, grade, bypass=True, normalized_transform=operation)
    expected = color.normalize_input(rgb, 'srgb')
    np.testing.assert_array_equal(calls[0], expected)
    np.testing.assert_array_equal(result, expected + np.float32(.1234567))
    np.testing.assert_array_equal(rgb, np.full_like(rgb, .5))
    assert np.max(np.abs(result*65535-np.rint(result*65535))) > .1


@pytest.mark.parametrize('bad', [
    lambda rgb: rgb.astype(np.float64),
    lambda rgb: rgb[:-1],
    lambda rgb: np.full_like(rgb, np.nan),
    lambda rgb: None,
])
def test_malformed_normalized_transform_is_rejected(bad):
    with pytest.raises(ValueError, match='Invalid normalized float picture'):
        color.apply_grade(np.ones((3, 5, 3), np.float32), Grade(), normalized_transform=bad)


def test_black_source_edges_are_real_picture_not_inferred_padding(monkeypatch):
    from lumen import restoration
    source = np.zeros((12, 14, 3), np.float32)
    source[4:8, 5:9] = .5
    calls = []
    def transform(value, settings, cancel=None):
        calls.append(value.copy())
        return value + np.float32(.2)
    monkeypatch.setattr(restoration, 'apply_restoration', transform)
    callback = color_media.normalized_restoration(RestorationSettings(enabled=True, denoise=.5),
                                                 (10, 8), 14, 12, None, RenderCancelled)
    result = callback(source)
    assert calls[0].shape == (8, 10, 3)
    assert calls[0][0, 0].max() == 0  # Real black edges remain inside the evaluator.
    np.testing.assert_array_equal(result[2:10, 2:12], source[2:10, 2:12] + np.float32(.2))
    np.testing.assert_array_equal(result[:2], source[:2])
    np.testing.assert_array_equal(result[:, :2], source[:, :2])
    np.testing.assert_array_equal(source[2, 2], 0)


def test_active_geometry_probe_cancellation_kills_and_reaps_process(monkeypatch):
    actual = subprocess.Popen
    processes = []
    event = threading.Event()
    def slow_probe(*args, **kwargs):
        process = actual([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        processes.append(process)
        event.set()
        return process
    monkeypatch.setattr(color_media.subprocess, 'Popen', slow_probe)
    with pytest.raises(RenderCancelled):
        color_media.active_picture_size(Path('unused.mkv'), ['format=rgb48le'], 80, 80,
                                        30, event, RenderError, RenderCancelled)
    assert len(processes) == 1 and processes[0].poll() is not None


@pytest.mark.parametrize('header', [
    '#dimensions 0: 10x20\n#dimensions 0: 10x20\n',
    '#dimensions 0: 0x20\n', '#dimensions 0: 100x20\n',
    '#dimensions 0: unknown\n', 'x'*70000,
], ids=['duplicate-dimensions', 'zero-size', 'exceeds-canvas', 'malformed-dimensions', 'output-budget'])
def test_active_geometry_probe_rejects_bad_or_unbounded_metadata(monkeypatch, header):
    actual = subprocess.Popen
    def probe(*args, **kwargs):
        expression = repr(header) if len(header) < 1000 else "'x'*70000"
        return actual([sys.executable, '-c', 'import sys; sys.stdout.write(' + expression + ')'], **kwargs)
    monkeypatch.setattr(color_media.subprocess, 'Popen', probe)
    with pytest.raises(RenderError):
        color_media.active_picture_size(Path('unused.mkv'), [], 80, 80, 30, None,
                                        RenderError, RenderCancelled)

"""Nondefault shape capability probes fail closed, remain bounded, never cache."""
import math
import re
import struct
import threading

import pytest

from lumen import highbit_effects as high
from lumen.media import ffmpeg_available
from lumen.model import ClipEffect
from lumen.render import RenderCancelled, RenderError
from test_highbit_effects import ensure, scene
from test_vignette_shape_rendering import effect


def shaped_scene():
    p = scene()
    p.clips[0].effects = [effect()]
    return p


class Process:
    def __init__(self, command, *, stdin, stdout, stderr, failure=None, finished=False):
        self.command, self.streams = command, (stdin, stdout, stderr)
        self.running = not finished and failure in ('cancel', 'timeout', 'output-budget', 'log-budget')
        self.returncode = int(failure == 'returncode')
        self.waited = self.killed = False
        width, height = map(int, command[command.index('-s') + 1].split('x'))
        graph = command[command.index('-vf') + 1]
        match = re.search(r'hypot\(W/2,H/2\)\)/([0-9.e+-]+),1/([0-9.e+-]+)', graph)
        assert match, 'The nondefault equation must actually be probed.'
        radius, feather = map(float, match.groups())
        raw = stdin.read()
        values = struct.unpack(f'<{len(raw) // 2}H', raw)
        result = []
        for i, value in enumerate(values):
            y, x = divmod(i % (width * height), width)
            u = math.hypot(int(x - width * .5), int(y - height * .5)) / math.hypot(width * .5, height * .5)
            q = min(1., math.pow(u / radius, 1. / feather))
            if failure == 'default-shape':
                q = u
            if failure == 'no-clamp':
                q = math.pow(u / radius, 1. / feather)
            if failure == 'wrong-feather':
                q = min(1., math.pow(u / radius, feather))
            result.append(int(value * math.cos(.65 * q * math.pi * .5) ** 4))
        if failure == 'eightbit':
            result = [round(value / 257) * 257 for value in result]
        if failure == 'lowbit':
            result = [value + 3 for value in result]
        payload = struct.pack(f'<{len(result)}H', *result)
        if failure == 'noop': payload = raw
        if failure == 'empty': payload = b''
        if failure == 'short': payload = payload[:-1]
        if failure == 'long': payload += b'\0'
        if failure == 'output-budget': payload = b'x' * 65537
        stdout.write(payload); stdout.flush()
        stderr.write(b'x' * 65537 if failure == 'log-budget' else b''); stderr.flush()

    def poll(self): return None if self.running else self.returncode
    def kill(self): self.killed = True; self.running = False
    def wait(self): self.waited = True; return self.returncode


def test_nondefault_probes_actual_shape_uncached_odd_and_narrow_native_data(monkeypatch):
    processes = []
    def start(command, **kwargs):
        process = Process(command, **kwargs); processes.append(process); return process
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    p = shaped_scene()
    # Project effect count must not multiply capability processes.
    p.clips[0].effects = [effect(radius=.25 + i / 10, id=f'v{i}') for i in range(16)]
    ensure(p); ensure(p)
    assert len(processes) == 12
    assert [p.command[p.command.index('-s') + 1] for p in processes] == ['14x10', '3x7', '1x9'] * 4
    for process in processes:
        assert '-noauto_conversion_filters' in process.command
        assert '-cpuflags' not in process.command
        assert process.command.count('gbrp16le') == 2
        assert process.waited and not process.killed and all(stream.closed for stream in process.streams)


@pytest.mark.parametrize('failure', ['returncode', 'empty', 'short', 'long', 'noop',
                                    'eightbit', 'lowbit', 'default-shape', 'no-clamp', 'wrong-feather'])
@pytest.mark.parametrize('failed_size', ['14x10', '3x7', '1x9'])
def test_bad_response_at_every_probe_geometry_is_rejected(monkeypatch, failure, failed_size):
    processes = []
    def start(command, **kwargs):
        size = command[command.index('-s') + 1]
        process = Process(command, failure=failure if size == failed_size else None, **kwargs)
        processes.append(process); return process
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    with pytest.raises(RenderError, match='cannot apply radial vignette directly'):
        ensure(shaped_scene())
    assert all(p.waited and not p.killed and all(stream.closed for stream in p.streams) for p in processes)


@pytest.mark.parametrize('failure', ['cancel', 'timeout', 'output-budget', 'log-budget'])
def test_probe_interrupt_reaps_running_process_and_closes_files(monkeypatch, failure):
    processes, cancel = [], threading.Event()
    def start(command, **kwargs):
        process = Process(command, failure=failure, **kwargs); processes.append(process)
        if failure == 'cancel': cancel.set()
        return process
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    with pytest.raises(RenderCancelled if failure == 'cancel' else RenderError):
        ensure(shaped_scene(), cancel=cancel, timeout=0 if failure == 'timeout' else 30)
    assert all(p.waited and p.killed and all(stream.closed for stream in p.streams) for p in processes)


@pytest.mark.parametrize('failure', ['output-budget', 'log-budget'])
def test_finished_probe_cannot_evade_io_budget(monkeypatch, failure):
    processes = []
    def start(command, **kwargs):
        process = Process(command, failure=failure, finished=True, **kwargs)
        processes.append(process); return process
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    with pytest.raises(RenderError, match='output budget'):
        ensure(shaped_scene())
    assert all(p.waited and not p.killed and all(stream.closed for stream in p.streams) for p in processes)


def test_missing_binary_closes_all_probe_streams(monkeypatch):
    streams = []
    def missing(command, **kwargs):
        streams.extend(kwargs.values()); raise FileNotFoundError('missing ffmpeg')
    monkeypatch.setattr(high.subprocess, 'Popen', missing)
    with pytest.raises(RenderError, match='compatible FFmpeg'):
        ensure(shaped_scene())
    assert len(streams) == 3 and all(stream.closed for stream in streams)


def test_zero_disabled_audio_only_and_precancel_do_not_launch_shape_probes(monkeypatch):
    monkeypatch.setattr(high.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not launch'))
    p = shaped_scene(); p.clips[0].effects[0].amount = 0; ensure(p)
    p = shaped_scene(); p.clips[0].effects[0].enabled = False; ensure(p)
    p = shaped_scene(); p.clips[0].track = 2; ensure(p)
    cancel = threading.Event(); cancel.set()
    with pytest.raises(RenderCancelled): ensure(shaped_scene(), cancel=cancel)


def test_malformed_bypassed_shape_fails_before_probe(monkeypatch):
    monkeypatch.setattr(high.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not launch'))
    p = shaped_scene()
    p.clips[0].effects[0].enabled = False
    p.clips[0].effects[0].feather = float('nan')
    with pytest.raises(RenderError, match='Invalid clip effect'):
        ensure(p)


def test_cancel_during_nondefault_fixture_prevents_process_launch(monkeypatch):
    cancel = threading.Event()
    original = high._shaped_vignette_probe_samples
    def prepare(*args):
        result = original(*args); cancel.set(); return result
    monkeypatch.setattr(high, '_shaped_vignette_probe_samples', prepare)
    monkeypatch.setattr(high.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not launch'))
    with pytest.raises(RenderCancelled): ensure(shaped_scene(), cancel=cancel)


def test_default_and_shaped_effects_probe_both_branches_only_when_used(monkeypatch):
    calls = []
    monkeypatch.setattr(high, '_run_vignette_probe', lambda *args: calls.append(('legacy', args[-2:])))
    monkeypatch.setattr(high, '_run_shaped_vignette_probe', lambda *args: calls.append(('shape', args[-4:])))
    p = shaped_scene(); p.clips[0].effects.append(ClipEffect('old', 'vignette', .3))
    ensure(p)
    assert [name for name, _ in calls] == ['legacy'] * 3 + ['shape'] * 6
    p.clips[0].effects[0].enabled = False; calls.clear(); ensure(p)
    assert [name for name, _ in calls] == ['legacy'] * 3
    p.clips[0].effects[0].enabled = True; p.clips[0].effects[-1].amount = 0
    calls.clear(); ensure(p)
    assert [name for name, _ in calls] == ['shape'] * 6


@pytest.mark.parametrize('width,height', [(True, 10), (10, False), (0, 9), (9, 0),
                                         (-1, 9), (1.5, 9), (1025, 1), (33, 32), (10**9, 10**9)])
def test_probe_geometry_budget_checked_before_frame_creation(width, height):
    with pytest.raises(ValueError, match='probe dimensions'):
        high._shaped_vignette_probe_samples(width, height, .75, 1.5)


@pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
def test_real_binary_passes_all_nondefault_shape_capability_checks():
    ensure(shaped_scene())

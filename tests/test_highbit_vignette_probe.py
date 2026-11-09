"""Fail-closed capability evidence independent of production sample math."""
import math
import struct
import threading
import pytest
from lumen import highbit_effects as high
from lumen.model import ClipEffect
from lumen.render import RenderCancelled, RenderError
from test_highbit_effects import scene, ensure


def vignette_scene(amount=.65, enabled=True):
    p = scene()
    p.clips[0].effects = [ClipEffect('v', 'vignette', amount, enabled)]
    return p


class Process:
    def __init__(self, command, *, stdin, stdout, stderr, failure=None):
        self.command, self.streams = command, (stdin, stdout, stderr)
        self.running = failure in ('cancel', 'timeout', 'output-budget', 'log-budget')
        self.returncode = int(failure == 'returncode')
        self.killed = self.waited = False
        w, h = map(int, command[command.index('-s') + 1].split('x'))
        data = stdin.read(); samples = struct.unpack(f'<{len(data)//2}H', data)
        out = []
        for i, value in enumerate(samples):
            y, x = divmod(i % (w*h), w)
            d = math.hypot(math.trunc(x - w*.5), math.trunc(y - h*.5))
            theta = .65 * math.pi * .5 * d / math.hypot(w*.5, h*.5)
            out.append(int(value * math.cos(theta)**4))
        if failure == 'eightbit': out = [round(v/257)*257 for v in out]
        result = struct.pack(f'<{len(out)}H', *out)
        if failure == 'noop': result = data
        if failure == 'empty': result = b''
        if failure == 'short': result = result[:-1]
        if failure == 'long': result += b'\0'
        if failure == 'output-budget': result = b'x'*65537
        if failure == 'lowbit': result = struct.pack(f'<{len(out)}H', *[v+3 for v in out])
        stdout.write(result); stdout.flush()
        stderr.write(b'x'*65537 if failure == 'log-budget' else b''); stderr.flush()
    def poll(self): return None if self.running else self.returncode
    def kill(self): self.killed = True; self.running = False
    def wait(self): self.waited = True; return self.returncode


def test_probe_rechecks_binary_and_odd_narrow_boundaries(monkeypatch):
    processes = []
    def start(command, **kwargs):
        p = Process(command, **kwargs); processes.append(p); return p
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    ensure(vignette_scene()); ensure(vignette_scene())
    assert [p.command[p.command.index('-s')+1] for p in processes] == ['14x10','3x7','1x9']*2
    for p in processes:
        assert '-noauto_conversion_filters' in p.command and '-cpuflags' not in p.command
        assert 'interpolation=nearest' in p.command[p.command.index('-vf')+1]
        assert p.waited and not p.killed and all(f.closed for f in p.streams)


@pytest.mark.parametrize('failure', ['returncode','empty','short','long','noop','eightbit','lowbit'])
@pytest.mark.parametrize('failed_size', ['14x10','3x7','1x9'])
def test_each_probe_rejects_bad_pixels_and_closes_files(monkeypatch, failure, failed_size):
    processes = []
    def start(command, **kwargs):
        size = command[command.index('-s')+1]
        p = Process(command, failure=failure if size == failed_size else None, **kwargs)
        processes.append(p); return p
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    with pytest.raises(RenderError, match='cannot apply radial vignette directly'):
        ensure(vignette_scene())
    assert all(p.waited and not p.killed and all(f.closed for f in p.streams) for p in processes)


@pytest.mark.parametrize('failure', ['cancel','timeout','output-budget','log-budget'])
def test_interrupts_reap_and_close_all_streams(monkeypatch, failure):
    cancel = threading.Event(); processes = []
    def start(command, **kwargs):
        p = Process(command, failure=failure, **kwargs); processes.append(p)
        if failure == 'cancel': cancel.set()
        return p
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    with pytest.raises(RenderCancelled if failure == 'cancel' else RenderError):
        ensure(vignette_scene(), timeout=0 if failure == 'timeout' else 30, cancel=cancel)
    assert all(p.killed and p.waited and all(f.closed for f in p.streams) for p in processes)


def test_missing_binary_closes_files(monkeypatch):
    streams = []
    def fail(command, **kwargs):
        streams.extend(kwargs.values()); raise FileNotFoundError('missing')
    monkeypatch.setattr(high.subprocess, 'Popen', fail)
    with pytest.raises(RenderError, match='compatible FFmpeg'): ensure(vignette_scene())
    assert len(streams) == 3 and all(f.closed for f in streams)


def test_bypass_audio_pre_cancel_never_start_probe(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail('must bypass')
    monkeypatch.setattr(high.subprocess, 'Popen', forbidden)
    ensure(vignette_scene(0)); ensure(vignette_scene(1, False))
    p = vignette_scene(); p.clips[0].track = 2; ensure(p)
    cancel = threading.Event(); cancel.set()
    with pytest.raises(RenderCancelled): ensure(vignette_scene(), cancel=cancel)


def test_cancel_during_fixture_creation_prevents_launch(monkeypatch):
    cancel = threading.Event(); original = high._vignette_probe_samples
    def prepare(*args):
        result = original(*args); cancel.set(); return result
    monkeypatch.setattr(high, '_vignette_probe_samples', prepare)
    monkeypatch.setattr(high.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not launch'))
    with pytest.raises(RenderCancelled): ensure(vignette_scene(), cancel=cancel)


def test_mixed_effects_probe_each_verified_operator_without_broad_cpu_flags(monkeypatch):
    calls = []
    monkeypatch.setattr(high, '_run_probe', lambda *a, narrow: calls.append(('blur', narrow)))
    monkeypatch.setattr(high, '_run_vignette_probe', lambda *a: calls.append(('vignette', a[-2:])))
    p = vignette_scene(); p.clips[0].effects.append(ClipEffect('b', 'blur', 2))
    ensure(p)
    assert calls == [('blur', False), ('blur', True), ('vignette', (14,10)),
                     ('vignette', (3,7)), ('vignette', (1,9))]
    assert high.highbit_effect_input_options(p) == ['-cpuflags','0']
    p.clips[0].effects[-1].enabled = False
    assert high.highbit_effect_input_options(p) == []

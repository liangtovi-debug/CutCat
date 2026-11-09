"""Decoded handles, exact timestamps, fresh content, and bounded subprocesses."""
from fractions import Fraction
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace as NS

import pytest

from lumen import video_transition_sources as vs


def data(points, time_base='1/1000'):
    return {'streams': [{'time_base': time_base}],
            'frames': [{'best_effort_timestamp': p} for p in points]}


def project(monkeypatch, path, left=10, right=10, duration=6):
    clip = NS(media_id='source')
    plan = NS(left=clip, right=clip, left_source_start=left,
              right_source_start=right, duration=duration)
    monkeypatch.setattr(vs, 'plan_video_transitions', lambda p: [plan])
    return NS(media=[NS(id='source', path=str(path))], fps_num=30, fps_den=1)


def test_millisecond_cfr_rounding_is_not_vfr():
    assert vs._decoded_count(data([0, 33, 67, 100, 133, 167]), Fraction(30)) == 6
    assert vs._decoded_count(data([100, 133, 167, 200]), Fraction(30)) == 4


@pytest.mark.parametrize('points', [[0, 33, 68], [0, 33, 100], [0, 40, 80], [0, 33, 33]])
def test_vfr_different_rate_missing_and_duplicate_frames_fail(points):
    with pytest.raises(vs.TransitionSourceError):
        vs._decoded_count(data(points), Fraction(30))


@pytest.mark.parametrize('payload', [data([]), {'streams': [], 'frames': []},
                                    data([0, None]), data([0, '33']), data([0], '0/1')])
def test_missing_or_invalid_timestamps_fail(payload):
    with pytest.raises(vs.TransitionSourceError, match='reliable decoded'):
        vs._decoded_count(payload, Fraction(30))


def make_video(tmp_path, *, fps=30, frames=30, audio=False):
    path = tmp_path / f'source-{fps}-{frames}-{audio}.mkv'
    args = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=c=red:s=32x32:r={fps}:d={frames/fps}']
    if audio:
        args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=mono', '-t', '3', '-c:a', 'pcm_s16le']
    args += ['-c:v', 'ffv1', str(path)]
    subprocess.run(args, check=True, timeout=15, capture_output=True)
    return path


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='local FFmpeg required')
def test_real_cfr_snapshot_and_fresh_content_every_call(tmp_path, monkeypatch):
    path = make_video(tmp_path)
    p = project(monkeypatch, path)
    cache = tmp_path / 'cache'
    proof = vs.prepare_transition_sources(p, cache)['source']
    assert proof.frame_count == 30
    assert proof.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert proof.path.read_bytes() == path.read_bytes()
    original = vs._copy_hash
    seen = []
    def track(source, *args):
        seen.append(Path(source))
        return original(source, *args)
    monkeypatch.setattr(vs, '_copy_hash', track)
    again = vs.prepare_transition_sources(p, cache)['source']
    assert again == proof and path in seen and proof.path in seen
    replacement = make_video(tmp_path, frames=18)
    path.write_bytes(replacement.read_bytes())
    changed = vs.prepare_transition_sources(p, cache)['source']
    assert changed.sha256 != proof.sha256 and changed.frame_count == 18
    path.unlink()
    with pytest.raises(vs.TransitionSourceError, match='relink'):
        vs.prepare_transition_sources(p, cache)


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='local FFmpeg required')
@pytest.mark.parametrize('side', ['left', 'right'])
def test_short_video_long_audio_cannot_supply_one_missing_handle(tmp_path, monkeypatch, side):
    path = make_video(tmp_path, frames=15, audio=True)
    p = project(monkeypatch, path, left=10 if side == 'left' else 0,
                right=10 if side == 'right' else 0)
    with pytest.raises(vs.TransitionSourceError, match=f'decoded {side} source handles'):
        vs.prepare_transition_sources(p, tmp_path / 'cache')


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='local FFmpeg required')
def test_real_differing_fps_rejected(tmp_path, monkeypatch):
    path = make_video(tmp_path, fps=25)
    p = project(monkeypatch, path)
    with pytest.raises(vs.TransitionSourceError, match='constant-frame-rate'):
        vs.prepare_transition_sources(p, tmp_path / 'cache')


def test_symlink_and_cache_tampering_rejected(tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'original')
    link = tmp_path / 'link'
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip('symlinks unavailable')
    with pytest.raises(vs.TransitionSourceError, match='plain local'):
        vs._snapshot(link, tmp_path, time.monotonic() + 5, None)
    snapshot, _ = vs._snapshot(source, tmp_path, time.monotonic() + 5, None)
    os.chmod(snapshot, 0o600)
    snapshot.write_bytes(b'tampered')
    with pytest.raises(vs.TransitionSourceError, match='snapshot content changed'):
        vs._snapshot(source, tmp_path, time.monotonic() + 5, None)


def test_source_changed_while_copying_rejected(tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'original')
    class MutatingOutput:
        def write(self, block):
            source.write_bytes(b'mutation')
    with pytest.raises(vs.TransitionSourceError, match='changed while reading'):
        vs._copy_hash(source, MutatingOutput(), time.monotonic() + 5, None)


def test_cancel_and_timeout_before_start(tmp_path, monkeypatch):
    p = project(monkeypatch, tmp_path / 'missing')
    event = threading.Event(); event.set()
    with pytest.raises(vs.TransitionSourceCancelled):
        vs.prepare_transition_sources(p, tmp_path, cancel=event)
    with pytest.raises(vs.TransitionSourceError, match='timed out'):
        vs.prepare_transition_sources(p, tmp_path, timeout=0)


def fake_process(monkeypatch, code):
    original = subprocess.Popen
    processes = []
    def start(*args, **kwargs):
        process = original([sys.executable, '-c', code], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(vs.subprocess, 'Popen', start)
    return processes


def test_decode_stderr_even_success_is_failure(monkeypatch):
    processes = fake_process(monkeypatch, 'import sys; print("{}"); print("decode failure",file=sys.stderr)')
    with pytest.raises(vs.TransitionSourceError, match='decoded without errors'):
        vs._probe(Path('unused'), time.monotonic() + 5, None)
    assert processes[0].poll() == 0


def test_timeout_reaps_process(monkeypatch):
    processes = fake_process(monkeypatch, 'import time; time.sleep(30)')
    with pytest.raises(vs.TransitionSourceError, match='timed out'):
        vs._probe(Path('unused'), time.monotonic() + .1, None)
    assert processes[0].poll() is not None


def test_output_overflow_reaps_process(monkeypatch):
    monkeypatch.setattr(vs, 'MAX_PROBE_BYTES', 128)
    processes = fake_process(monkeypatch, 'import sys,time; sys.stdout.write("x"*100000);sys.stdout.flush();time.sleep(30)')
    with pytest.raises(vs.TransitionSourceError, match='safety limit'):
        vs._probe(Path('unused'), time.monotonic() + 5, None)
    assert processes[0].poll() is not None


def test_same_size_and_mtime_replacement_still_changes_digest(tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'original')
    old = source.stat()
    first, digest = vs._snapshot(source, tmp_path, time.monotonic() + 5, None)
    source.write_bytes(b'mutation')
    os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns))
    second, changed = vs._snapshot(source, tmp_path, time.monotonic() + 5, None)
    assert digest != changed and first != second
    assert first.read_bytes() == b'original' and second.read_bytes() == b'mutation'


def test_mid_decode_cancel_reaps_process(monkeypatch):
    processes = fake_process(monkeypatch, 'import time; time.sleep(30)')
    event = threading.Event()
    timer = threading.Timer(.05, event.set)
    timer.start()
    try:
        with pytest.raises(vs.TransitionSourceCancelled):
            vs._probe(Path('unused'), time.monotonic() + 5, event)
    finally:
        timer.cancel()
    assert processes[0].poll() is not None


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='local FFmpeg required')
def test_actual_vfr_is_rejected(tmp_path):
    target = tmp_path / 'vfr.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=blue:s=32x32:r=30:d=1', '-vf',
                    "select='not(eq(n,10))'", '-fps_mode', 'vfr', '-c:v', 'ffv1', str(target)],
                   capture_output=True, check=True, timeout=15)
    with pytest.raises(vs.TransitionSourceError, match='constant-frame-rate'):
        vs.decoded_frame_count(target, Fraction(30))


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='local FFmpeg required')
def test_validate_temporary_cleanup_on_error(tmp_path, monkeypatch):
    path = make_video(tmp_path, frames=15)
    p = project(monkeypatch, path)
    created = []
    original = vs.tempfile.TemporaryDirectory
    def temporary(**kwargs):
        result = original(dir=tmp_path, **kwargs)
        created.append(Path(result.name))
        return result
    monkeypatch.setattr(vs.tempfile, 'TemporaryDirectory', temporary)
    with pytest.raises(vs.TransitionSourceError, match='source handles'):
        vs.validate_transition_sources(p)
    assert created and not created[0].exists()


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='local FFmpeg required')
def test_exact_last_handle_present_succeeds(tmp_path, monkeypatch):
    path = make_video(tmp_path, frames=16)
    p = project(monkeypatch, path, left=10, right=10)
    assert vs.prepare_transition_sources(p, tmp_path / 'cache')['source'].frame_count == 16


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='local FFmpeg required')
def test_real_truncated_source_rejected(tmp_path, monkeypatch):
    path = make_video(tmp_path)
    original = path.read_bytes()
    path.write_bytes(original[:len(original) // 2])
    p = project(monkeypatch, path)
    with pytest.raises(vs.TransitionSourceError):
        vs.prepare_transition_sources(p, tmp_path / 'cache')


def test_cancel_during_copy_removes_partial_snapshot(tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'x' * 2 * 1024**2)
    cache = tmp_path / 'cache'; cache.mkdir()
    checks = 0
    def cancel():
        nonlocal checks
        checks += 1
        return checks >= 2
    with pytest.raises(vs.TransitionSourceCancelled):
        vs._snapshot(source, cache, time.monotonic() + 5, cancel)
    assert not list(cache.iterdir())


def test_free_space_budget_refuses_copy_without_touching_original(tmp_path, monkeypatch):
    source = tmp_path / 'source'; source.write_bytes(b'original')
    cache = tmp_path / 'cache'; cache.mkdir()
    monkeypatch.setattr(vs.shutil, 'disk_usage', lambda p: NS(free=vs.MIN_FREE_RESERVE_BYTES + 7))
    with pytest.raises(vs.TransitionSourceError, match='free disk space'):
        vs._snapshot(source, cache, time.monotonic() + 5, None)
    assert source.read_bytes() == b'original' and not list(cache.iterdir())


def test_cache_budget_includes_pending_duplicate_copy(tmp_path, monkeypatch):
    source = tmp_path / 'source'; source.write_bytes(b'original')
    cache = tmp_path / 'cache'; cache.mkdir()
    snapshot, digest = vs._snapshot(source, cache, time.monotonic() + 5, None)
    monkeypatch.setattr(vs, 'MAX_SNAPSHOT_CACHE_BYTES', 15)
    with pytest.raises(vs.TransitionSourceError, match='cache exceeds'):
        vs._snapshot(source, cache, time.monotonic() + 5, None)
    assert snapshot.read_bytes() == source.read_bytes() == b'original'
    assert list(cache.iterdir()) == [snapshot]


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='local FFmpeg required')
def test_certificate_digest_bound_to_original_and_cheap_commit_check(tmp_path, monkeypatch):
    path = make_video(tmp_path)
    p = project(monkeypatch, path)
    certificate = vs.prepare_transition_source_certificate(p)
    assert len(certificate) == 1
    assert certificate[0].sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(vs, '_copy_hash', lambda *args: pytest.fail('Commit check must not rehash'))
    vs.verify_transition_source_identities(certificate)
    old = path.stat()
    content = bytearray(path.read_bytes()); content[-1] ^= 1; path.write_bytes(content)
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    with pytest.raises(vs.TransitionSourceError, match='changed while reading'):
        vs.verify_transition_source_identities(certificate)


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='local FFmpeg required')
def test_certificate_refuses_original_change_after_snapshot_before_decode(tmp_path, monkeypatch):
    path = make_video(tmp_path)
    p = project(monkeypatch, path)
    original = vs._probe
    def mutate(snapshot, *args):
        result = original(snapshot, *args)
        path.write_bytes(b'changed')
        return result
    monkeypatch.setattr(vs, '_probe', mutate)
    with pytest.raises(vs.TransitionSourceError, match='changed while reading'):
        vs.prepare_transition_source_certificate(p)


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='local FFmpeg required')
def test_certificate_commit_refuses_missing_relinked_original(tmp_path, monkeypatch):
    path = make_video(tmp_path)
    p = project(monkeypatch, path)
    certificate = vs.prepare_transition_source_certificate(p)
    path.unlink()
    with pytest.raises(vs.TransitionSourceError, match='relink'):
        vs.verify_transition_source_identities(certificate)


def test_snapshot_lock_wait_is_cancellable(tmp_path):
    event = threading.Event(); event.set()
    vs._SNAPSHOT_LOCK.acquire()
    try:
        with pytest.raises(vs.TransitionSourceCancelled):
            vs._snapshot(tmp_path / 'unused', tmp_path, time.monotonic() + 5, event)
    finally:
        vs._SNAPSHOT_LOCK.release()

"""Real offline libvidstab probes plus security, cancellation, cache and geometry checks."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import zipfile

import numpy as np
import pytest

from lumen import stabilization_analysis as sa


def _probe_data(**changes):
    stream = {'codec_type': 'video', 'index': 0, 'width': 1920, 'height': 1080,
              'sample_aspect_ratio': '1:1', 'field_order': 'progressive', 'duration': '2'}
    stream.update(changes)
    return {'streams': [stream], 'format': {'duration': '2'}}


def _geometry(**changes):
    return sa._geometry_from_probe(_probe_data(**changes), 1920, 1080)


def test_exact_active_geometry_sar_rotation_and_pixel_centers():
    g = _geometry(width=720, height=576, sample_aspect_ratio='16:15')
    assert (g.active_width, g.active_height) == (1440, 1080)
    assert (g.proxy_width, g.proxy_height) == (640, 480)
    assert g.normalization_filters == ['scale=1440:1080', 'setsar=1']
    assert g.proxy_filter_chain(30000, 1001)[:2] == ['setpts=PTS-STARTPTS', 'fps=fps=30000/1001:start_time=0']
    np.testing.assert_allclose(g.proxy_to_active, [[2.25, 0, .625], [0, 2.25, .625], [0, 0, 1]])
    rotated = _geometry(side_data_list=[{'side_data_type': 'Display Matrix', 'rotation': -90}])
    assert (rotated.active_width, rotated.active_height) == (606, 1080)
    assert rotated.rotation == 270
    assert rotated.proxy_width % 2 == rotated.proxy_height % 2 == 0
    # An exact 16:9 DAR must not lose two pixels to binary float roundoff.
    wide = _geometry(width=320, height=240, sample_aspect_ratio='4:3')
    assert (wide.active_width, wide.active_height) == (1920, 1080)


@pytest.mark.parametrize('changes', [
    {'field_order': 'tt'}, {'field_order': 'bb'}, {'sample_aspect_ratio': '999:1'},
    {'width': 0}, {'height': 17000}, {'duration': 'nan'}, {'duration': '601'},
    {'side_data_list': [{'side_data_type': 'Display Matrix', 'rotation': 17}]},
    {'side_data_list': [{'side_data_type': 'Display Matrix', 'rotation': 0,
                        'displaymatrix': '0: -65536 0 0\n1: 0 65536 0\n2: 0 0 1073741824'}]},
])
def test_unsupported_geometry_fails_closed(changes):
    with pytest.raises(sa.StabilizationAnalysisError):
        _geometry(**changes)


@pytest.mark.parametrize('rate', [(0, 1), (30, 0), (241, 1), (1, 2), (True, 1), (30.0, 1)])
def test_invalid_fps(rate):
    with pytest.raises(sa.StabilizationAnalysisError):
        sa._rate(*rate)


def test_no_fields_is_error_not_confidence(tmp_path):
    g = _geometry(width=320, height=240)
    # Textureless image remains unreliable even when extra==0.
    quality = tmp_path / 'quality.gray'
    quality.write_bytes(bytes([120]) * 96 * 72 * 3)
    rows = np.zeros((3, 6)); rows[0, 5] = 1
    cuts, confidence, stats = sa._quality_metrics(quality, (96, 72), rows, g, sa._Budget())
    assert confidence.tolist() == [0, 0, 0]
    assert cuts.tolist() == [True, False, False]
    assert stats['no_fields_frames'] == 1
    assert stats['low_confidence_frames'] == 3


def test_scene_cut_independent_of_extra_flag(tmp_path):
    g = _geometry(width=320, height=240)
    rng = np.random.default_rng(71)
    a = rng.integers(0, 75, (72, 96), dtype=np.uint8)
    b = rng.integers(175, 255, (72, 96), dtype=np.uint8)
    quality = tmp_path / 'quality.gray'
    quality.write_bytes(a.tobytes() + a.tobytes() + b.tobytes())
    rows = np.zeros((3, 6))
    cuts, confidence, stats = sa._quality_metrics(quality, (96, 72), rows, g, sa._Budget())
    assert cuts.tolist() == [True, False, True]
    assert confidence.tolist() == [0, 1, 0]
    assert stats['cut_count'] == 1


def test_snapshot_hash_and_content_preserved(tmp_path):
    source, snapshot = tmp_path / 'original', tmp_path / 'copy'
    source.write_bytes(b'local source bytes' * 100)
    before = source.read_bytes()
    digest, state = sa._snapshot(source, snapshot, sa._Budget())
    assert digest == hashlib.sha256(before).hexdigest()
    assert source.read_bytes() == snapshot.read_bytes() == before
    assert sa.same_file_state(state, source.stat())


def test_snapshot_is_bounded_and_special_files_rejected(tmp_path, monkeypatch):
    source = tmp_path / 'original'; source.write_bytes(b'12345')
    monkeypatch.setattr(sa, 'MAX_SOURCE_BYTES', 4)
    with pytest.raises(sa.StabilizationAnalysisError):
        sa._snapshot(source, tmp_path / 'copy', sa._Budget())
    if hasattr(os, 'mkfifo'):
        fifo = tmp_path / 'fifo'; os.mkfifo(fifo)
        with pytest.raises(sa.StabilizationAnalysisError):
            sa._snapshot(fifo, tmp_path / 'copy', sa._Budget())


def test_process_timeout_kills_and_reaps(tmp_path, monkeypatch):
    actual_popen, processes = subprocess.Popen, []
    def track(*args, **kwargs):
        p = actual_popen(*args, **kwargs); processes.append(p); return p
    monkeypatch.setattr(sa.subprocess, 'Popen', track)
    start = time.monotonic()
    with pytest.raises(sa.StabilizationAnalysisError, match='timed out'):
        sa._run([sys.executable, '-c', 'import time; time.sleep(60)'],
                cwd=tmp_path, budget=sa._Budget(timeout=.12))
    assert time.monotonic() - start < 3
    assert len(processes) == 1 and processes[0].poll() is not None


def test_process_cancel_kills_and_reaps(tmp_path, monkeypatch):
    actual_popen, processes = subprocess.Popen, []
    def track(*args, **kwargs):
        p = actual_popen(*args, **kwargs); processes.append(p); return p
    monkeypatch.setattr(sa.subprocess, 'Popen', track)
    cancel = threading.Event(); timer = threading.Timer(.15, cancel.set); timer.start()
    try:
        with pytest.raises(sa.StabilizationAnalysisCancelled):
            sa._run([sys.executable, '-c', 'import time; time.sleep(60)'],
                    cwd=tmp_path, budget=sa._Budget(cancel, 10))
    finally:
        timer.cancel()
    assert len(processes) == 1 and processes[0].poll() is not None


def test_process_error_contains_bounded_diagnostic(tmp_path):
    with pytest.raises(sa.StabilizationAnalysisError, match='broken decoder'):
        sa._run([sys.executable, '-c', 'import sys; print("broken decoder",file=sys.stderr); sys.exit(2)'],
                cwd=tmp_path, budget=sa._Budget())


def test_pre_cancel_has_no_cache_or_subprocess(tmp_path, monkeypatch):
    event = threading.Event(); event.set()
    monkeypatch.setattr(sa, '_binary', lambda *_: pytest.fail('cancel must precede tools'))
    with pytest.raises(sa.StabilizationAnalysisCancelled):
        sa.analyze_motion('unused', fps_num=30, fps_den=1, project_width=320,
                          project_height=240, cache_dir=tmp_path / 'cache', cancel=event)
    assert not (tmp_path / 'cache').exists()


@pytest.fixture(scope='module')
def local_tools():
    ffmpeg, ffprobe = sa.ffmpeg_binary(), sa.ffprobe_binary()
    if not shutil.which(ffmpeg) or not shutil.which(ffprobe):
        pytest.skip('External FFmpeg/ffprobe unavailable')
    filters = subprocess.run([ffmpeg, '-hide_banner', '-filters'], capture_output=True, text=True, timeout=20)
    if 'vidstabdetect' not in filters.stdout or 'vidstabtransform' not in filters.stdout:
        pytest.skip('External FFmpeg lacks libvidstab')
    return ffmpeg, ffprobe


def _texture():
    rng = np.random.default_rng(421)
    base = np.repeat(np.repeat(rng.integers(0, 256, (20, 32, 3), dtype=np.uint8), 12, 0), 10, 1)
    for _ in range(2):
        base = ((base.astype(np.uint16) + np.roll(base, 1, 0) + np.roll(base, 1, 1)) // 3).astype(np.uint8)
    return base


def _write_video(ffmpeg, path, frames, rate=30, vf=None):
    args = [ffmpeg, '-v', 'error', '-y', '-threads', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
            '-s', '320x240', '-r', str(rate), '-i', 'pipe:0']
    if vf:
        args += ['-vf', vf, '-fps_mode', 'vfr']
    args += ['-c:v', 'ffv1', '-threads', '1', str(path)]
    subprocess.run(args, input=b''.join(frame.tobytes() for frame in frames), capture_output=True,
                   check=True, timeout=30)


@pytest.fixture(scope='module')
def motion_fixture(local_tools, tmp_path_factory):
    root = tmp_path_factory.mktemp('motion-fixture')
    base = _texture()
    shifts = [(round(5*np.sin(i*1.2)), round(4*np.cos(i*.9))) for i in range(30)]
    path = root / 'quote [source]; space.mkv'
    _write_video(local_tools[0], path, [np.roll(base, (y, x), (0, 1)) for x, y in shifts])
    return path, shifts


def _analyze(path, cache_dir=None, **kwargs):
    return sa.analyze_motion(path, fps_num=kwargs.pop('fps_num', 30), fps_den=kwargs.pop('fps_den', 1),
                             project_width=320, project_height=240, cache_dir=cache_dir, **kwargs)


def test_real_known_translation_source_intact_and_exact_count(motion_fixture, tmp_path):
    path, shifts = motion_fixture
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = _analyze(path, tmp_path / 'cache')
    assert result.frame_count == 30
    assert result.raw_rows.shape == (30, 6)
    np.testing.assert_allclose(result.raw_rows[1:, 1:3], -np.diff(shifts, axis=0), atol=.65)
    assert np.count_nonzero(result.confidence) >= 27
    assert result.statistics['cut_count'] == 0
    assert result.raw_rows[0, 5] == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert hashlib.sha256(result.snapshot_path.read_bytes()).hexdigest() == before
    assert result.provenance['source_sha256'] == before
    assert len(result.provenance['ffmpeg']['sha256']) == 64
    assert result.provenance['sampling'].endswith('before floor(O+kR)')
    assert not list((tmp_path / 'cache').glob('*.partial'))


def test_cache_hit_rehash_and_corrupt_snapshot_regeneration(motion_fixture, tmp_path, monkeypatch):
    path, _ = motion_fixture
    first = _analyze(path, tmp_path)
    actual = sa._two_passes
    monkeypatch.setattr(sa, '_two_passes', lambda *a, **k: pytest.fail('valid cache should skip motion passes'))
    second = _analyze(path, tmp_path)
    assert second.cache_hit and second.cache_key == first.cache_key
    second.snapshot_path.write_bytes(b'tampered')
    third = _analyze(path, tmp_path)
    assert third.cache_hit and third.snapshot_path.read_bytes() == path.read_bytes()
    monkeypatch.setattr(sa, '_two_passes', actual)
    (tmp_path / (first.cache_key + '.npz')).write_bytes(b'corrupt cache')
    fourth = _analyze(path, tmp_path)
    assert not fourth.cache_hit
    np.testing.assert_array_equal(fourth.raw_rows, first.raw_rows)


def test_content_changes_invalidate_even_restored_timestamp(motion_fixture, tmp_path):
    path, _ = motion_fixture
    source = tmp_path / 'source.mkv'; shutil.copyfile(path, source)
    cache = tmp_path / 'cache'
    first = _analyze(source, cache)
    before = source.stat()
    # Matroska ignores trailing padding but content identity must still change.
    with source.open('ab') as stream:
        stream.write(b'\0')
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    second = _analyze(source, cache)
    assert not second.cache_hit and second.cache_key != first.cache_key
    assert second.provenance['source_sha256'] != first.provenance['source_sha256']


def test_source_mutation_during_passes_fails_without_motion_cache(motion_fixture, tmp_path, monkeypatch):
    path, _ = motion_fixture
    source = tmp_path / 'source.mkv'; shutil.copyfile(path, source)
    actual = sa._two_passes
    def mutate(*args, **kwargs):
        result = actual(*args, **kwargs)
        with source.open('ab') as stream: stream.write(b'\0')
        return result
    monkeypatch.setattr(sa, '_two_passes', mutate)
    with pytest.raises(sa.StabilizationAnalysisError, match='Source changed'):
        _analyze(source, tmp_path / 'cache')
    assert not list((tmp_path / 'cache').glob('*.npz'))


def test_runtime_capability_rejects_wrong_motion_convention(local_tools, tmp_path, monkeypatch):
    monkeypatch.setattr(sa, '_CAPABILITIES', set())
    actual = sa._parse_debug
    def wrong(*args, **kwargs):
        rows = actual(*args, **kwargs)
        rows[:, 1:3] *= -1
        return rows
    monkeypatch.setattr(sa, '_parse_debug', wrong)
    with pytest.raises(sa.StabilizationAnalysisError, match='compatibility test'):
        sa._capability_gate(sa._binary(local_tools[0]), {'test': 'wrong-sign'}, sa._Budget())
    assert not sa._CAPABILITIES


def test_raw_debug_format_and_count_fail_closed(tmp_path):
    path = tmp_path / 'global_motions.trf'
    for text in ('0 0 0 0 0\n', '1 0 0 0 0 0\n', '0 nan 0 0 0 0\n', '0 0 0 0 0 0.3\n'):
        path.write_text(text)
        with pytest.raises(sa.StabilizationAnalysisError, match='debug format'):
            sa._parse_debug(path)
    path.write_text('0 0 0 0 0 1\n')
    with pytest.raises(sa.StabilizationAnalysisError, match='debug format'):
        sa._parse_debug(path, expected_frames=2)


def test_real_no_fields_all_unreliable(local_tools, tmp_path):
    path = tmp_path / 'blank.mkv'
    _write_video(local_tools[0], path, [np.full((240, 320, 3), 80, dtype=np.uint8)] * 10)
    result = _analyze(path)
    assert result.frame_count == 10
    assert not np.any(result.confidence)
    assert result.statistics['no_fields_frames'] == 10
    assert result.snapshot_path.exists()


def test_real_vfr_resampled_on_canonical_project_fps(local_tools, tmp_path):
    path = tmp_path / 'vfr.mkv'
    base = _texture()
    _write_video(local_tools[0], path, [np.roll(base, i, axis=1) for i in range(10)], rate=10,
                 vf="select='not(eq(n,3)+eq(n,7))'")
    at30 = _analyze(path, tmp_path / 'cache', fps_num=30)
    at15 = _analyze(path, tmp_path / 'cache', fps_num=15)
    assert at30.frame_count == 30 and at15.frame_count == 15
    assert at30.cache_key != at15.cache_key
    assert not at15.cache_hit


def test_real_known_rotation(local_tools, tmp_path):
    path = tmp_path / 'rotation.mkv'
    base = _texture().astype(float)
    yy, xx = np.indices((240, 320), dtype=float)
    angles = .015 * np.sin(np.arange(30)*1.1)
    frames = []
    for a in angles:
        x = np.cos(a)*(xx-160) + np.sin(a)*(yy-120) + 160
        y = -np.sin(a)*(xx-160) + np.cos(a)*(yy-120) + 120
        x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
        fx, fy = (x-x0)[..., None], (y-y0)[..., None]
        valid = (x0 >= 0) & (x0 < 319) & (y0 >= 0) & (y0 < 239)
        x0, y0 = np.clip(x0, 0, 318), np.clip(y0, 0, 238)
        rgb = (base[y0,x0]*(1-fx)*(1-fy)+base[y0,x0+1]*fx*(1-fy)+
               base[y0+1,x0]*(1-fx)*fy+base[y0+1,x0+1]*fx*fy)
        rgb[~valid] = 0
        frames.append(np.rint(rgb).astype(np.uint8))
    _write_video(local_tools[0], path, frames)
    result = _analyze(path)
    # libvidstab's alpha sign is opposite image-space SE(2), hence positive
    # observed image-angle increment, unlike its negative dx/dy convention.
    expected = np.diff(angles)
    assert np.corrcoef(result.raw_rows[1:, 3], expected)[0, 1] > .92
    assert np.median(np.abs(result.raw_rows[1:, 3] - expected)) < .003
    assert result.statistics['cut_count'] == 0
    assert np.count_nonzero(result.confidence) >= 25


def test_actual_max_frame_bound(motion_fixture, tmp_path, monkeypatch):
    path, _ = motion_fixture
    monkeypatch.setattr(sa, 'MAX_FRAMES', 20)
    monkeypatch.setattr(sa, '_capability_gate', lambda *a, **k: pytest.fail('frame bound must precede capability decode'))
    monkeypatch.setattr(sa, '_two_passes', lambda *a, **k: pytest.fail('frame bound must precede motion decode'))
    with pytest.raises(sa.StabilizationAnalysisError, match='18,000|frame'):
        _analyze(path, tmp_path)
    assert not list(tmp_path.glob('*.npz'))


def test_cache_zip_inflation_is_rejected(tmp_path):
    path = tmp_path / 'cache.npz'
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('rows.npy', bytes(sa.MAX_CACHE_BYTES + 1))
    assert sa._cache_load(path, 'key', _geometry(), {}, tmp_path / 'source') is None


def test_actual_ffprobe_sar_and_interlace(local_tools, tmp_path):
    plain, fields = tmp_path / 'sar.mkv', tmp_path / 'fields.mkv'
    base = _texture()
    _write_video(local_tools[0], plain, [base] * 3, vf='setsar=4/3')
    g = sa.probe_source_geometry(plain, project_width=1920, project_height=1080)
    assert (g.sar_num, g.sar_den) == (4, 3)
    assert (g.active_width, g.active_height) == (1920, 1080)
    _write_video(local_tools[0], fields, [base] * 3, vf='setfield=tff')
    with pytest.raises(sa.StabilizationAnalysisError, match='Interlaced'):
        sa.probe_source_geometry(fields, project_width=1920, project_height=1080)


def test_actual_ffprobe_rotation_when_supported(local_tools, tmp_path):
    plain, rotated = tmp_path / 'plain.mp4', tmp_path / 'rotated.mp4'
    # MP4 MPEG-4 encoder is built in; no optional codec dependency.
    subprocess.run([local_tools[0], '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'testsrc2=s=320x240:r=30:d=0.2', '-c:v', 'mpeg4', str(plain)],
                   capture_output=True, check=True, timeout=30)
    result = subprocess.run([local_tools[0], '-v', 'error', '-y', '-display_rotation', '90',
                             '-i', str(plain), '-c', 'copy', str(rotated)], capture_output=True, timeout=30)
    if result.returncode and b'Unrecognized option' in result.stderr:
        pytest.skip('This external FFmpeg predates explicit rotation fixture option')
    assert result.returncode == 0, result.stderr
    geometry = sa.probe_source_geometry(rotated, project_width=1920, project_height=1080)
    assert geometry.rotation == 90
    assert (geometry.active_width, geometry.active_height) == (810, 1080)


@pytest.mark.parametrize('stream,size', [('stdout', 2*1024**2+1), ('stderr', 8*1024**2+1)])
def test_finished_process_diagnostic_limits_are_still_enforced(tmp_path, monkeypatch, stream, size):
    actual_popen, processes = subprocess.Popen, []
    def already_finished(*args, **kwargs):
        process = actual_popen(*args, **kwargs)
        process.wait(timeout=10)
        processes.append(process)
        return process
    monkeypatch.setattr(sa.subprocess, 'Popen', already_finished)
    with pytest.raises(sa.StabilizationAnalysisError, match='large analysis process diagnostic'):
        sa._run([sys.executable, '-c', f'import sys; sys.{stream}.buffer.write(b"x"*{size})'],
                cwd=tmp_path, budget=sa._Budget())
    assert len(processes) == 1 and processes[0].poll() == 0


@pytest.mark.parametrize('duration,fps,match', [('601', 30, 'seconds'), ('400', 60, '18,000')])
def test_oversized_metadata_rejects_before_any_capability_or_motion_decode(tmp_path, monkeypatch, duration, fps, match):
    source = tmp_path / 'source.media'
    source.write_bytes(b'owned bounded snapshot; probe response is controlled in this test')
    monkeypatch.setattr(sa, '_binary', lambda value: value)
    data = _probe_data(duration=duration)
    def probe_only(args, **kwargs):
        assert '-show_streams' in args and '-show_format' in args
        return json.dumps(data).encode()
    monkeypatch.setattr(sa, '_run', probe_only)
    for name in ('_tool_identity', '_capability_gate', '_two_passes'):
        monkeypatch.setattr(sa, name, lambda *a, **k: pytest.fail('metadata limits must precede expensive tool preparation/decode'))
    with pytest.raises(sa.StabilizationAnalysisError, match=match):
        _analyze(source, tmp_path / 'cache', fps_num=fps)
    assert not (tmp_path / 'cache').exists()


def test_actual_duration_metadata_bound_precedes_any_decode(motion_fixture, tmp_path, monkeypatch):
    path, _ = motion_fixture
    monkeypatch.setattr(sa, 'MAX_SECONDS', .5)
    for name in ('_tool_identity', '_capability_gate', '_two_passes'):
        monkeypatch.setattr(sa, name, lambda *a, **k: pytest.fail('duration bound must precede expensive tool preparation/decode'))
    with pytest.raises(sa.StabilizationAnalysisError, match='seconds'):
        _analyze(path, tmp_path / 'cache')
    assert not (tmp_path / 'cache').exists()

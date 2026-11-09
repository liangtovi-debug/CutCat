"""Independent bounded source-viewer timing, identity and resource oracles."""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
import pytest

from lumen.media import ffmpeg_available, ffmpeg_binary, ffprobe_binary, probe
from lumen.model import Media, Project
from lumen.source_viewer import (
    SourceViewerSession, SourcePreviewBackend, SourceViewerError,
    SourceViewerCancelled, MAX_AUDITION_SECONDS, _run,
)


@pytest.fixture(scope='module')
def own_media():
    if not ffmpeg_available():
        pytest.skip('FFmpeg/ffprobe unavailable')
    # Remove only our own reproducible, sub-10MB media. No broad pytest cleanup.
    with tempfile.TemporaryDirectory(prefix='cutcat-source-backend-test-') as root:
        yield Path(root)


def make_numbered(root, rate='24', count=96, *, offset=0, audio=True):
    path = root / f'numbered-{rate.replace("/", "_")}-{count}-{offset}-{audio}.mkv'
    if path.exists():
        return path
    frames = np.stack([np.full((16, 16, 3), (i * 601) % 65535, dtype='<u2')
                       for i in range(count)])
    command = [ffmpeg_binary(), '-v', 'error', '-nostdin', '-f', 'rawvideo',
               '-pix_fmt', 'rgb48le', '-s', '16x16', '-r', rate, '-i', 'pipe:0']
    if audio:
        command += ['-f', 'lavfi', '-i',
                    f'sine=frequency=440:sample_rate=48000:duration={float(Fraction(count) / Fraction(rate))}']
    if offset:
        command += ['-vf', f'setpts=PTS+{offset}/TB']
        if audio:
            command += ['-af', f'asetpts=PTS+{offset}/TB']
    command += ['-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'gbrp16le',
                '-c:a', 'pcm_s16le', '-threads', '1', str(path)]
    subprocess.run(command, input=frames.tobytes(), check=True, capture_output=True, timeout=15)
    return path


def session_for(path, fps=Fraction(30)):
    media = probe(path)
    project = Project(width=16, height=16, fps_num=fps.numerator,
                      fps_den=fps.denominator, media=[media])
    return project, SourceViewerSession(project, media.id)


def frame_oracle(path, fps, frame):
    # No backend timing/filter builder: decode FROM ORIGIN then normalize the
    # entire prefix on the absolute project grid, finally select one frame.
    command = [ffmpeg_binary(), '-v', 'error', '-nostdin', '-threads', '1',
               '-i', str(path), '-an', '-vf',
               f'setpts=PTS-STARTPTS,fps=fps={fps.numerator}/{fps.denominator}:start_time=0,'
               f'trim=start_frame={frame}:end_frame={frame+1}',
               '-frames:v', '1', '-threads', '1', '-pix_fmt', 'rgb48le',
               '-f', 'rawvideo', 'pipe:1']
    raw = subprocess.run(command, check=True, capture_output=True, timeout=15).stdout
    return np.rint(np.frombuffer(raw, dtype='<u2').astype(np.float32) / 65535 * 255).astype(np.uint8).tobytes()


def test_construction_marks_and_model_currency_are_io_free(monkeypatch):
    media = Media('m', '/must/not/be/read', 'source', 3.1)
    project = Project(width=1920, height=1080, fps_num=24, media=[media])
    def fail(*args, **kwargs):
        raise AssertionError('GUI/session path attempted I/O')
    monkeypatch.setattr(Path, 'stat', fail)
    monkeypatch.setattr(Path, 'lstat', fail)
    monkeypatch.setattr(subprocess, 'Popen', fail)
    before = deepcopy(project)
    session = SourceViewerSession(project, 'm')
    assert session.total_frames == 74
    assert (session.width, session.height) == (640, 360)
    session.seek(7)
    session.mark_in()
    session.seek(12)
    session.mark_out()
    assert session.selection() == (7, 13)
    assert session.cursor == 12
    assert session.matches(project)
    assert project == before
    session.clear_marks()
    assert session.selection() == (0, 74)
    changed = deepcopy(project)
    changed.fps_num = 30
    assert not session.matches(changed)
    with pytest.raises(SourceViewerError):
        session.seek(74)
    with pytest.raises(SourceViewerError):
        session.seek(True)


@pytest.mark.parametrize('rate,fps,offset', [
    ('24', Fraction(30), 0), ('30', Fraction(24), 0),
    ('24000/1001', Fraction(30000, 1001), 0), ('24', Fraction(30), 5),
])
def test_seeked_numbered_frames_match_independent_global_grid(own_media, rate, fps, offset):
    path = make_numbered(own_media, rate, offset=offset)
    project, session = session_for(path, fps)
    before = deepcopy(project)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    backend = SourcePreviewBackend()
    try:
        for frame in (0, 1, 2, 5, 31, session.total_frames - 2):
            session.seek(frame)
            result = backend.inspect_frame(session)
            assert result.pixels == frame_oracle(path, fps, frame), (rate, fps, offset, frame)
            assert result.is_current(session, project)
        session.seek(0)
        assert not result.is_current(session, project)
        assert backend.verify_identity(session, result.identity)
    finally:
        backend.close()
    assert project == before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_audition_exact_short_window_audio_and_cache(own_media):
    path = make_numbered(own_media)
    project, session = session_for(path)
    backend = SourcePreviewBackend()
    try:
        result = backend.audition(session, 61, 73)
        data = json.loads(subprocess.run([
            ffprobe_binary(), '-v', 'error', '-count_frames', '-show_streams',
            '-of', 'json', str(result.path)], capture_output=True, check=True, timeout=10).stdout)
        video = next(s for s in data['streams'] if s['codec_type'] == 'video')
        assert int(video['nb_read_frames']) == 12
        assert Fraction(video['avg_frame_rate']) == 30
        audio = subprocess.run([ffmpeg_binary(), '-v', 'error', '-i', str(result.path),
                                '-map', '0:a:0', '-f', 's16le', '-ac', '2', 'pipe:1'],
                               check=True, capture_output=True, timeout=10).stdout
        assert np.max(np.abs(np.frombuffer(audio, dtype='<i2'))) > 1000
        first = subprocess.run([ffmpeg_binary(), '-v', 'error', '-i', str(result.path),
                                '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'],
                               check=True, capture_output=True, timeout=10).stdout
        expected = np.frombuffer(frame_oracle(path, session.fps, 61), dtype=np.uint8)
        assert np.max(np.abs(np.frombuffer(first, dtype=np.uint8).astype(int) - expected)) <= 3
        cached = backend.audition(session, 61, 73)
        assert cached.path == result.path
        assert result.path.stat().st_size < 100000
        assert len(list(result.path.parent.iterdir())) == 1
        # Cache corruption is rebuilt, not returned or allowed to become input.
        result.path.write_bytes(b'corrupt')
        rebuilt = backend.audition(session, 61, 73)
        assert rebuilt.path != result.path
        assert not result.path.exists()
    finally:
        backend.close()
    assert not rebuilt.path.exists()


def test_audio_only_blank_inspection_and_exact_pcm_range(own_media):
    path = own_media / 'audio.wav'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=720:duration=1.01:sample_rate=48000',
                    '-c:a', 'pcm_s16le', str(path)], check=True, capture_output=True, timeout=10)
    project, session = session_for(path)
    assert session.total_frames == 30
    backend = SourcePreviewBackend()
    try:
        frame = backend.inspect_frame(session)
        assert frame.pixels == bytes(16 * 16 * 3)
        audition = backend.audition(session, 15, 30)
        assert audition.path.stat().st_size > 0
        with pytest.raises(SourceViewerError, match='complete'):
            backend.audition(session, 30, 31)
    finally:
        backend.close()


def test_partial_tail_not_rounded_into_source_handle(own_media):
    path = make_numbered(own_media, '24', 13, audio=False)
    project, session = session_for(path)
    assert session.total_frames == 16  # 13/24*30 = 16.25, never 17.
    backend = SourcePreviewBackend()
    try:
        backend.audition(session, 15, 16)
        with pytest.raises(SourceViewerError, match='complete'):
            backend.audition(session, 16, 17)
    finally:
        backend.close()


def test_source_identity_change_and_cache_revalidation(own_media):
    original = make_numbered(own_media)
    path = own_media / 'identity.mkv'
    shutil.copyfile(original, path)
    project, session = session_for(path)
    backend = SourcePreviewBackend()
    try:
        result = backend.inspect_frame(session)
        saved = path.stat()
        with path.open('r+b') as stream:
            stream.seek(-1, 2)
            stream.write(b'!')
        import os
        os.utime(path, ns=(saved.st_atime_ns, saved.st_mtime_ns))
        with pytest.raises(SourceViewerError, match='changed'):
            backend.verify_identity(session, result.identity)
        assert result.is_current(session, project)  # This check is explicitly model-only.
    finally:
        backend.close()


def test_before_after_identity_change_discards_result(own_media, monkeypatch):
    import lumen.source_viewer as module
    original = make_numbered(own_media)
    path = own_media / 'changed-during.mkv'
    shutil.copyfile(original, path)
    _, session = session_for(path)
    old_pixels = module._pixels
    def changed(*args, **kwargs):
        result = old_pixels(*args, **kwargs)
        with path.open('ab') as stream:
            stream.write(b'x')
        return result
    monkeypatch.setattr(module, '_pixels', changed)
    backend = SourcePreviewBackend()
    try:
        with pytest.raises(SourceViewerError, match='changed'):
            backend.inspect_frame(session)
    finally:
        backend.close()


def test_corrupt_source_and_budget_cleanup(own_media):
    path = own_media / 'corrupt.mkv'
    path.write_bytes(b'not media')
    session = SourceViewerSession(Project(media=[Media('m', str(path), 'bad', 1)]), 'm')
    backend = SourcePreviewBackend()
    try:
        with pytest.raises(SourceViewerError):
            backend.inspect_frame(session)
    finally:
        backend.close()
    _, session = session_for(make_numbered(own_media))
    backend = SourcePreviewBackend(max_output_bytes=100)
    try:
        with pytest.raises(SourceViewerError, match='budget'):
            backend.audition(session, 0, 10)
        assert not backend._cache
        assert not list(Path(backend._temporary.name).iterdir())
    finally:
        backend.close()


def test_lru_cache_never_exceeds_entry_or_byte_budget(own_media):
    _, session = session_for(make_numbered(own_media))
    backend = SourcePreviewBackend(max_output_bytes=20000, max_cache_bytes=30000, max_cache_entries=2)
    paths = []
    try:
        for start in (0, 10, 20, 30):
            paths.append(backend.audition(session, start, start + 4).path)
            assert len(backend._cache) <= 2
            assert sum(p.stat().st_size for p in paths if p.exists()) <= 30000
            assert all(p.suffix == '.mp4' for p in Path(backend._temporary.name).iterdir())
        assert not paths[0].exists()
    finally:
        backend.close()


def test_pre_cancel_deadline_stream_byte_cap_and_child_reaping():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(SourceViewerCancelled):
        _run([sys.executable, '-c', 'pass'], 1, cancel, time.monotonic() + 2)
    with pytest.raises(SourceViewerError, match='byte budget'):
        _run([sys.executable, '-c', 'import sys;sys.stdout.buffer.write(b"x"*10000)'],
             100, None, time.monotonic() + 2)
    began = time.monotonic()
    with pytest.raises(SourceViewerError, match='runtime budget'):
        _run([sys.executable, '-c', 'import time;time.sleep(30)'],
             100, None, time.monotonic() + .1)
    assert time.monotonic() - began < 3
    cancel.clear()
    timer = threading.Timer(.1, cancel.set)
    timer.start()
    try:
        with pytest.raises(SourceViewerCancelled):
            _run([sys.executable, '-c', 'import time;time.sleep(30)'],
                 100, cancel, time.monotonic() + 5)
    finally:
        timer.cancel()
        timer.join()


def test_duration_limit_and_fps_guards(own_media):
    _, session = session_for(make_numbered(own_media))
    session.total_frames = 3000
    session.media.duration = 100
    backend = SourcePreviewBackend()
    try:
        with pytest.raises(SourceViewerError):
            backend.audition(session, 0, 901)
    finally:
        backend.close()
    assert MAX_AUDITION_SECONDS == 30
    for fps in (0, 121, True):
        with pytest.raises(SourceViewerError):
            SourceViewerSession(Project(fps_num=fps, media=[Media('m', 'unused', 'm', 1)]), 'm')


def make_exact_mp4(root, *, vfr=False):
    path = root / ('exact-vfr.mp4' if vfr else 'exact-cfr.mp4')
    if not path.exists():
        original = make_numbered(root, '30', 96)
        command = [ffmpeg_binary(), '-v', 'error', '-i', str(original)]
        if vfr:
            # Preserve the 5 ms odd-frame offset through both filter and
            # encoder timebases; MP4 timescale cannot restore rounded PTS.
            command += ['-vf', 'settb=1/90000,setpts=3000*N+450*mod(N\\,2)',
                        '-enc_time_base:v', '1:90000', '-fps_mode', 'passthrough']
        command += ['-c:v', 'libx264rgb', '-crf', '0', '-preset', 'ultrafast',
                    '-threads', '1', '-c:a', 'aac', '-video_track_timescale', '90000', str(path)]
        subprocess.run(command, check=True, capture_output=True, timeout=10)
    return path


@pytest.mark.parametrize('vfr', [False, True])
def test_exact_mp4_fixture_decoded_timestamps(own_media, vfr):
    # Inspect decoded frames independently of the viewer and its rate claims.
    # A container timescale alone cannot preserve sub-frame encoder timestamps.
    path = make_exact_mp4(own_media, vfr=vfr)
    result = subprocess.run(
        [ffprobe_binary(), '-v', 'error', '-select_streams', 'v:0',
         '-show_frames', '-show_streams', '-show_entries',
         'frame=best_effort_timestamp:stream=time_base', '-of', 'json', str(path)],
        check=True, capture_output=True, timeout=15,
    )
    value = json.loads(result.stdout)
    assert len(value['streams']) == 1
    assert Fraction(value['streams'][0]['time_base']) == Fraction(1, 90000)
    timestamps = [int(frame['best_effort_timestamp']) for frame in value['frames']]
    assert timestamps == [3000 * n + (450 * (n % 2) if vfr else 0)
                          for n in range(96)]
    assert set(b - a for a, b in zip(timestamps, timestamps[1:])) == (
        {2550, 3450} if vfr else {3000}
    )


def test_matched_cfr_overwrite_selected_and_remnant_proof(own_media):
    from lumen.model import Editor, Clip
    path = make_exact_mp4(own_media)
    project, session = session_for(path)
    project.clips = [Clip('old', session.media_id, 0, 0, 0, 60)]
    editor = Editor(project)
    plan = editor.plan_source_overwrite(session.media_id, 0, 20, 10, 20)
    backend = SourcePreviewBackend()
    try:
        frame = backend.inspect_frame(session)
        assert backend.verify_overwrite_sources(session, frame.identity, project, plan)
        clip = editor.apply_source_overwrite(plan)
        assert clip.source_in == 10 and clip.duration == 10
    finally:
        backend.close()


def test_actual_vfr_rejected_even_when_imported_average_fps_matches(own_media, monkeypatch):
    from lumen.model import Editor
    import lumen.source_viewer as module
    path = make_exact_mp4(own_media, vfr=True)
    project, _ = session_for(path)
    project.media[0].fps_num, project.media[0].fps_den = 30, 1
    session = SourceViewerSession(project, project.media[0].id)
    plan = Editor(project).plan_source_overwrite(session.media_id, 0, 0, 0, 30)
    backend = SourcePreviewBackend()
    try:
        # Even a matching imported/container average/rate claim is not the
        # proof: actual decoded PTS still have to follow the absolute grid.
        original_run = module._run
        def claimed(args, *rest, **kwargs):
            result = original_run(args, *rest, **kwargs)
            if '-show_streams' in args:
                value = json.loads(result)
                for stream in value['streams']:
                    if stream.get('codec_type') == 'video':
                        stream['avg_frame_rate'] = stream['r_frame_rate'] = '30/1'
                return json.dumps(value).encode()
            return result
        monkeypatch.setattr(module, '_run', claimed)
        frame = backend.inspect_frame(session)
        with pytest.raises(SourceViewerError, match='grid|durations|variable-rate'):
            backend.verify_overwrite_sources(session, frame.identity, project, plan)
    finally:
        backend.close()


def test_overwrite_aggregate_proof_budget_rejected_before_decode(own_media, monkeypatch):
    from types import SimpleNamespace
    path = make_exact_mp4(own_media)
    project, session = session_for(path)
    backend = SourcePreviewBackend()
    try:
        frame = backend.inspect_frame(session)
        plan = SimpleNamespace(media_id=session.media_id, source_in=0, source_out=901,
                               track=0, affected_clip_ids=(), remnants=())
        import lumen.source_viewer as module
        monkeypatch.setattr(module, '_probe', lambda *a, **kw: pytest.fail('budget should precede decode'))
        with pytest.raises(SourceViewerError, match='30 seconds'):
            backend.verify_overwrite_sources(session, frame.identity, project, plan)
    finally:
        backend.close()


def test_inspection_input_normalization_is_not_silently_bypassed(own_media):
    from lumen.color import normalize_input
    # Valid Apple Log source RGB16 with BT.2020 tags in MOV/ProRes is accepted
    # by existing fixtures elsewhere; directly exercise shared normalization
    # here without relying on a platform-specific codec tag retention path.
    from lumen.source_viewer import _pixels
    _, session = session_for(make_numbered(own_media))
    session.media.input_encoding = 'apple_log_bt2020'
    raw = np.array([0, 16384, 32768, 49152, 60000, 65535], dtype='<u2')
    expected = np.rint(np.clip(normalize_input(raw.reshape(-1, 3).astype(np.float32) / 65535,
                                               'apple_log_bt2020'), 0, 1) * 255).astype(np.uint8)
    assert _pixels(raw.tobytes(), session, None, time.monotonic() + 3) == expected.tobytes()
    assert expected.tobytes() != np.rint(raw.astype(np.float32) / 65535 * 255).astype(np.uint8).tobytes()


def test_low_disk_rejects_before_any_output_allocation(own_media, monkeypatch):
    from types import SimpleNamespace
    import lumen.source_viewer as module
    _, session = session_for(make_numbered(own_media))
    backend = SourcePreviewBackend()
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda path: SimpleNamespace(free=1))
    monkeypatch.setattr(backend, '_directory', lambda: pytest.fail('must not allocate before preflight'))
    try:
        with pytest.raises(SourceViewerError, match='temporary disk space'):
            backend.audition(session, 0, 10)
        assert backend._temporary is None and not backend._cache
    finally:
        backend.close()


def test_late_window_in_long_source_seeks_without_full_transcode(own_media):
    path = make_numbered(own_media, '5', 350, audio=False)  # 70 seconds, <1MB.
    project, session = session_for(path)
    backend = SourcePreviewBackend()
    try:
        assert backend._temporary is None  # Opening never renders a proxy.
        session.seek(1951)
        frame = backend.inspect_frame(session)
        assert frame.pixels == frame_oracle(path, session.fps, 1951)
        assert backend._temporary is None
        audition = backend.audition(session, 1801, 1951)
        assert audition.start_frame == 1801 and audition.end_frame == 1951
        assert audition.path.stat().st_size < 1024 * 1024
    finally:
        backend.close()


def test_cancel_during_frame_stream_cleans_partial_outputs(own_media, monkeypatch):
    import lumen.source_viewer as module
    _, session = session_for(make_numbered(own_media))
    cancel = threading.Event()
    original = module._pixels
    count = 0
    def cancelled(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            cancel.set()
        return original(*args, **kwargs)
    monkeypatch.setattr(module, '_pixels', cancelled)
    backend = SourcePreviewBackend()
    try:
        with pytest.raises(SourceViewerCancelled):
            backend.audition(session, 0, 60, cancel=cancel)
        assert not backend._cache
        assert not list(Path(backend._temporary.name).iterdir())
    finally:
        backend.close()


def make_audio(path, seconds):
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    f'sine=frequency=800:duration={seconds}:sample_rate=48000',
                    '-c:a', 'pcm_s16le', str(path)], check=True, capture_output=True, timeout=10)


@pytest.mark.parametrize('suffix', ['wav', 'mka'])
def test_shorter_replaced_audio_rejected_before_apply(own_media, suffix):
    from lumen.model import Editor
    path = own_media / f'replaced-before-open.{suffix}'
    make_audio(path, 2)
    project, _ = session_for(path)
    make_audio(path, 1)  # Replace on disk before viewer captures its identity.
    session = SourceViewerSession(project, project.media[0].id)
    plan = Editor(project).plan_source_overwrite(session.media_id, 2, 0, 0, 45)
    backend = SourcePreviewBackend()
    try:
        frame = backend.inspect_frame(session)
        with pytest.raises(SourceViewerError, match='complete|missing'):
            backend.verify_overwrite_sources(session, frame.identity, project, plan)
    finally:
        backend.close()


def test_affected_audio_source_checked_not_only_selected_source(own_media):
    from lumen.model import Editor, Clip
    old = own_media / 'affected-audio.mka'
    new = own_media / 'selected-audio.wav'
    make_audio(old, 2)
    make_audio(new, 1)
    old_media, new_media = probe(old), probe(new)
    project = Project(width=16, height=16, media=[old_media, new_media],
                      clips=[Clip('existing', old_media.id, 2, 0, 0, 60)])
    session = SourceViewerSession(project, new_media.id)
    plan = Editor(project).plan_source_overwrite(new_media.id, 2, 20, 0, 10)
    make_audio(old, 1)
    backend = SourcePreviewBackend()
    try:
        frame = backend.inspect_frame(session)
        with pytest.raises(SourceViewerError, match='complete|missing'):
            backend.verify_overwrite_sources(session, frame.identity, project, plan)
    finally:
        backend.close()


@pytest.mark.parametrize('field,value', [('width', 18), ('fps_num', 25), ('audio_offset', .25)])
def test_changed_import_metadata_rejected_on_worker(own_media, field, value):
    project, _ = session_for(make_numbered(own_media))
    setattr(project.media[0], field, value)
    session = SourceViewerSession(project, project.media[0].id)
    backend = SourcePreviewBackend()
    try:
        with pytest.raises(SourceViewerError, match='changed since import'):
            backend.inspect_frame(session)
    finally:
        backend.close()


def test_real_apple_log_rgb16_ingress_inspection_and_audition(own_media):
    from lumen.color import normalize_input
    path = own_media / 'source-apple-log.mkv'
    rgb = np.broadcast_to(np.array([30011, 31007, 32003], '<u2'), (48, 64, 3)).copy()
    rgb += np.arange(64, dtype=np.uint16)[None, :, None] * 13
    rgb += np.arange(48, dtype=np.uint16)[:, None, None] * 7
    raw = np.moveaxis(rgb[..., [1, 2, 0]], -1, 0).copy().tobytes()
    tags = ['-colorspace', 'rgb', '-color_range', 'pc', '-color_primaries', 'bt2020']
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'gbrp16le',
                    '-s', '64x48', '-r', '30', *tags, '-i', 'pipe:0', '-frames:v', '3',
                    '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'gbrp16le', '-threads', '1',
                    *tags, str(path)], input=raw * 3, check=True, capture_output=True, timeout=10)
    media = probe(path, input_encoding='apple_log_bt2020')
    project = Project(width=64, height=48, media=[media])
    session = SourceViewerSession(project, media.id)
    expected = np.rint(np.clip(normalize_input(rgb.astype(np.float32) / 65535,
                                               'apple_log_bt2020'), 0, 1) * 255).astype(np.uint8)
    backend = SourcePreviewBackend()
    try:
        frame = backend.inspect_frame(session)
        np.testing.assert_array_equal(np.frombuffer(frame.pixels, dtype=np.uint8).reshape(48, 64, 3), expected)
        result = backend.audition(session, 0, 2)
        encoded = subprocess.run([ffmpeg_binary(), '-v', 'error', '-i', str(result.path),
                                  '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'],
                                 check=True, capture_output=True, timeout=10).stdout
        np.testing.assert_allclose(np.frombuffer(encoded, np.uint8).reshape(48, 64, 3), expected, atol=5)
    finally:
        backend.close()


def test_truncated_audio_cannot_pass_claimed_header_duration(own_media, monkeypatch):
    from lumen.model import Editor
    import lumen.source_viewer as module
    path = own_media / 'truncated-duration.wav'
    make_audio(path, 2)
    project, session = session_for(path)
    # Keep the original RIFF/data length claims, remove the actual tail bytes.
    with path.open('r+b') as stream:
        stream.truncate(path.stat().st_size // 2)
    plan = Editor(project).plan_source_overwrite(session.media_id, 2, 0, 0, 45)
    backend = SourcePreviewBackend()
    try:
        frame = backend.inspect_frame(session)
        original_probe = module._probe
        def misleading_header(*args, **kwargs):
            info = original_probe(*args, **kwargs)
            info['capacity'], info['audio_capacity'] = 60, 60
            info['capacity_proven'] = True
            return info
        monkeypatch.setattr(module, '_probe', misleading_header)
        with pytest.raises(SourceViewerError, match='decode|complete|missing'):
            backend.verify_overwrite_sources(session, frame.identity, project, plan)
    finally:
        backend.close()


def test_generated_source_provenance_hash_not_just_size_or_duration(own_media):
    from lumen.model import SoundProvenance
    path = own_media / 'generated-source.wav'
    make_audio(path, 1)
    project, _ = session_for(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    project.media[0].sound_provenance = SoundProvenance(
        'test-tone', 'procedural', 'CC0-1.0', digest, digest, 48000, 48000)
    session = SourceViewerSession(project, project.media[0].id)
    backend = SourcePreviewBackend()
    try:
        backend.inspect_frame(session)
        with path.open('r+b') as stream:
            stream.seek(-10, 2)
            stream.write(b'\x00\x00')
        with pytest.raises(SourceViewerError, match='Generated source has changed'):
            backend.inspect_frame(session)
    finally:
        backend.close()


def make_random_pcm(root, rate, seconds=8):
    import wave
    path = root / f'random-{rate}-{seconds}.wav'
    if not path.exists():
        samples = np.random.default_rng(76391).integers(-12000, 12001, rate * seconds, dtype=np.int16)
        with wave.open(str(path), 'wb') as stream:
            stream.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
            stream.writeframes(samples.astype('<i2').tobytes())
    return path


def origin_pcm_oracle(path):
    # Independent FROM-ORIGIN reference. Never reuse backend seek/offset/filter
    # helpers, and slice the fully normalized samples in Python afterward.
    return subprocess.run([ffmpeg_binary(), '-v', 'error', '-i', str(path), '-map', '0:a:0',
                           '-af', 'asetpts=PTS-STARTPTS,aresample=48000:async=1:first_pts=0,'
                           'aformat=sample_fmts=fltp:channel_layouts=stereo',
                           '-ac', '2', '-ar', '48000', '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1'],
                          check=True, capture_output=True, timeout=15).stdout


@pytest.mark.parametrize('rate', [22050, 32000, 44100, 48000, 96000])
@pytest.mark.parametrize('fps', [Fraction(25), Fraction(24000, 1001), Fraction(30000, 1001)])
def test_non48k_nonzero_in_and_late_seek_match_full_origin_pcm_exactly(own_media, rate, fps):
    from lumen.source_viewer import _identity, _probe
    from lumen.timing import sample_boundary
    path = make_random_pcm(own_media, rate)
    project, session = session_for(path, fps)
    full = origin_pcm_oracle(path)
    backend = SourcePreviewBackend()
    try:
        deadline = time.monotonic() + 30
        identity = _identity(session, None, deadline)
        info = _probe(session, identity, None, deadline)
        for start, end in [(5, 45), (50, 51), (120, 140),
                           (session.total_frames - 2, session.total_frames)]:
            actual = backend._audio(identity, info, session, start, end, None, deadline, pad=False)
            begin, finish = sample_boundary(start, fps), sample_boundary(end, fps)
            assert len(actual) == (finish-begin) * 4
            assert actual == full[begin*4:finish*4], (rate, fps, start, end)
    finally:
        backend.close()


@pytest.mark.parametrize('rate', [22050, 44100, 96000])
def test_positive_offset_video_audio_resampling_matches_origin_pcm(own_media, rate):
    from lumen.source_viewer import _identity, _probe
    from lumen.timing import round_half_up
    source = make_random_pcm(own_media, rate)
    path = own_media / f'offset-video-{rate}.mov'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=16x16:rate=25:duration=8.2', '-itsoffset', '0.2', '-i', str(source),
                    '-map', '0:v', '-map', '1:a', '-c:v', 'png', '-threads', '1', '-c:a', 'pcm_s16le',
                    '-video_track_timescale', '90000', str(path)], check=True, capture_output=True, timeout=15)
    project, session = session_for(path, Fraction(25))
    assert session.media.audio_offset == .2
    full = origin_pcm_oracle(path)
    backend = SourcePreviewBackend()
    try:
        deadline = time.monotonic() + 30
        identity = _identity(session, None, deadline)
        info = _probe(session, identity, None, deadline)
        for start, end in [(0, 10), (8, 35), (120, 140), (201, 205)]:
            actual = backend._audio(identity, info, session, start, end, None, deadline, pad=False)
            lo = round_half_up(max(Fraction(0), Fraction(start, 25) - Fraction(1, 5)) * 48000)
            hi = round_half_up(max(Fraction(0), Fraction(end, 25) - Fraction(1, 5)) * 48000)
            leading = max(0, round_half_up((Fraction(1, 5) - Fraction(start, 25)) * 48000))
            expected = (bytes(leading * 4) + full[lo*4:hi*4])[:(end-start)*1920*4]
            assert len(actual) == (end-start)*1920*4
            assert actual == expected, (rate, start, end)
    finally:
        backend.close()


@pytest.mark.parametrize('suffix', ['ffconcat', 'wav', 'mov'])
def test_manifest_dependencies_rejected_even_when_renamed(own_media, suffix):
    from types import SimpleNamespace
    from lumen.source_viewer import _identity
    component = own_media / 'manifest-component.wav'
    make_audio(component, 2)
    path = own_media / f'manifest-hidden.{suffix}'
    path.write_text("ffconcat version 1.0\nfile 'manifest-component.wav'\nduration 2\n")
    project, session = session_for(path)  # Existing import can inspect concat.
    backend = SourcePreviewBackend()
    try:
        old_identity = _identity(session, None, time.monotonic()+5)
        plan = SimpleNamespace(media_id=session.media_id, source_in=0, source_out=10,
                               track=2, affected_clip_ids=(), remnants=())
        for altered in (False, True):
            if altered:
                make_audio(component, 1)
            with pytest.raises(SourceViewerError):
                backend.inspect_frame(session)
            with pytest.raises(SourceViewerError):
                backend.audition(session, 0, 10)
            with pytest.raises(SourceViewerError):
                backend.verify_overwrite_sources(session, old_identity, project, plan)
            assert not backend._cache
    finally:
        backend.close()


def test_valid_source_replaced_by_manifest_cannot_reuse_cached_proxy(own_media):
    component = own_media / 'cache-component.wav'
    path = own_media / 'cache-replaced.wav'
    make_audio(component, 2)
    make_audio(path, 2)
    project, session = session_for(path)
    backend = SourcePreviewBackend()
    try:
        cached = backend.audition(session, 0, 10)
        assert cached.path.exists()
        path.write_text("ffconcat version 1.0\nfile 'cache-component.wav'\nduration 2\n")
        with pytest.raises(SourceViewerError):
            backend.audition(session, 0, 10)
        assert not backend._cache and not cached.path.exists()
    finally:
        backend.close()


@pytest.mark.parametrize('fps', [Fraction(24000, 1001), Fraction(30000, 1001)])
def test_exact_fractional_rate_last_frame_remains_inspectable_and_provable(own_media, fps):
    from lumen.model import Editor
    path = own_media / f'complete-last-{fps.numerator}_{fps.denominator}.mov'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-f', 'lavfi', '-i',
                    f'testsrc2=size=16x16:rate={fps.numerator}/{fps.denominator}',
                    '-frames:v', '100', '-c:v', 'png', '-pix_fmt', 'rgb24', '-threads', '1',
                    '-video_track_timescale', str(fps.numerator), str(path)],
                   check=True, capture_output=True, timeout=15)
    project, session = session_for(path, fps)
    assert session.total_frames == 100
    session.seek(99)
    session.mark_in()
    session.mark_out()
    assert session.selection() == (99, 100)
    backend = SourcePreviewBackend()
    try:
        frame = backend.inspect_frame(session)
        assert frame.source_frame == 99
        assert frame.pixels == frame_oracle(path, fps, 99)
        plan = Editor(project).plan_source_overwrite(session.media_id, 0, 0, 99, 100)
        assert backend.verify_overwrite_sources(session, frame.identity, project, plan)
        session.clear_marks()
        assert session.selection() == (0, 100)
        whole = Editor(project).plan_source_overwrite(session.media_id, 0, 0, 0, 100)
        assert backend.verify_overwrite_sources(session, frame.identity, project, whole)
    finally:
        backend.close()


def make_short_offset_av(root, name, audio_seconds):
    path = root / name
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=16x16:rate=25:duration=2', '-itsoffset', '0.2',
                    '-f', 'lavfi', '-i', f'sine=frequency=941:sample_rate=44100:duration={audio_seconds}',
                    '-map', '0:v', '-map', '1:a', '-c:v', 'png', '-threads', '1', '-c:a', 'pcm_s16le',
                    '-video_track_timescale', '90000', '-movflags', '+faststart', str(path)],
                   check=True, capture_output=True, timeout=15)
    return path


def test_known_short_delayed_embedded_audio_only_pads_verified_absent_tail(own_media):
    from lumen.source_viewer import _identity, _probe
    path = make_short_offset_av(own_media, 'legitimate-short-audio.mov', '0.6')
    project, session = session_for(path, Fraction(25))
    full = origin_pcm_oracle(path)
    assert len(full) == 28800 * 4
    backend = SourcePreviewBackend()
    try:
        deadline = time.monotonic()+30
        identity = _identity(session, None, deadline)
        info = _probe(session, identity, None, deadline)
        assert info['audio_duration'] == Fraction(3, 5)
        actual = backend._audio(identity, info, session, 0, 50, None, deadline, pad=True)
        assert actual == bytes(9600 * 4) + full + bytes(57600 * 4)
        assert backend._audio(identity, info, session, 25, 50, None, deadline, pad=True) == bytes(48000 * 4)
        result = backend.audition(session, 0, 50)
        assert result.path.exists()
        with pytest.raises(SourceViewerError, match='missing|incomplete'):
            backend._audio(identity, info, session, 0, 50, None, deadline, pad=False)
    finally:
        backend.close()


def test_short_embedded_audio_cannot_pad_missing_claimed_samples(own_media):
    from lumen.source_viewer import _identity, _probe
    path = make_short_offset_av(own_media, 'claimed-short-audio.mov', '0.6')
    damaged = make_short_offset_av(own_media, 'shorter-than-claimed.mov', '0.3')
    project, session = session_for(path, Fraction(25))
    backend = SourcePreviewBackend()
    try:
        deadline = time.monotonic()+30
        identity = _identity(session, None, deadline)
        info = _probe(session, identity, None, deadline)
        # A source changed/truncated after its header was read: even audition
        # may not turn the absent claimed .3s into plausible silence.
        shutil.copyfile(damaged, path)
        with pytest.raises(SourceViewerError, match='missing|incomplete'):
            backend._audio(identity, info, session, 0, 50, None, deadline, pad=True)
    finally:
        backend.close()


@pytest.mark.parametrize('extension,codec', [('m4a', 'aac'), ('mp3', 'libmp3lame'), ('flac', 'flac')])
def test_compressed_audio_priming_matches_origin_without_input_seek(own_media, extension, codec):
    from lumen.source_viewer import _identity, _probe
    from lumen.timing import sample_boundary
    original = make_random_pcm(own_media, 44100)
    path = own_media / f'codec-priming.{extension}'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-i', str(original), '-c:a', codec, str(path)],
                   check=True, capture_output=True, timeout=15)
    project, session = session_for(path, Fraction(25))
    full = origin_pcm_oracle(path)
    backend = SourcePreviewBackend()
    try:
        deadline = time.monotonic()+30
        identity = _identity(session, None, deadline)
        info = _probe(session, identity, None, deadline)
        for start, end in [(5, 45), (120, 140), (175, 180)]:
            actual = backend._audio(identity, info, session, start, end, None, deadline, pad=False)
            expected = full[sample_boundary(start, session.fps)*4:sample_boundary(end, session.fps)*4]
            assert actual == expected, (extension, start, end)
    finally:
        backend.close()


def test_late_compressed_audio_matches_origin_and_reuses_verified_window(own_media, monkeypatch):
    from lumen.source_viewer import _identity, _probe
    import lumen.source_viewer as module
    original = make_random_pcm(own_media, 44100, 40)
    path = own_media / 'long-compressed.m4a'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-i', str(original), '-c:a', 'aac', str(path)],
                   check=True, capture_output=True, timeout=15)
    project, session = session_for(path, Fraction(25))
    backend = SourcePreviewBackend()
    try:
        deadline = time.monotonic()+30
        identity = _identity(session, None, deadline)
        info = _probe(session, identity, None, deadline)
        expected = origin_pcm_oracle(path)[32*48000*4:33*48000*4]
        assert backend._audio(identity, info, session, 800, 825, None, deadline, pad=False) == expected
        monkeypatch.setattr(module._Pipeline, 'spawn', lambda *a, **kw: pytest.fail('verified warm window must not decode'))
        assert backend._audio(identity, info, session, 800, 825, None, deadline, pad=False) == expected
    finally:
        backend.close()


def make_long_video_aac(root):
    path = root / 'long-video-aac.mp4'
    if not path.exists():
        audio = make_random_pcm(root, 44100, 40)
        subprocess.run([ffmpeg_binary(), '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=16x16:rate=25:duration=40', '-i', str(audio),
                        '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-preset', 'ultrafast',
                        '-g', '25', '-threads', '1', '-c:a', 'aac', '-video_track_timescale', '90000', str(path)],
                       check=True, capture_output=True, timeout=20)
    return path


def test_late_video_aac_selected_source_proves_picture_and_pcm_without_mutation(own_media, monkeypatch):
    from lumen.model import Editor
    path = make_long_video_aac(own_media)
    project, session = session_for(path, Fraction(25))
    editor = Editor(project)
    plan = editor.plan_source_overwrite(session.media_id, 0, 0, 800, 825)
    before = deepcopy(project)
    revision = editor.revision
    session.seek(800)
    backend = SourcePreviewBackend()
    try:
        frame = backend.inspect_frame(session)  # Read-only late still stays available.
        assert frame.source_frame == 800 and len(frame.pixels) == 16*16*3
        assert backend.verify_overwrite_sources(session, frame.identity, project, plan)
        assert backend._audio_cache
        assert sum(entry[1] for entry in backend._audio_cache.values()) == 48000*8
        assert editor.revision == revision and project == before
        assert not project.clips
    finally:
        backend.close()


def test_late_video_aac_affected_source_receives_exact_pcm_proof(own_media, monkeypatch):
    from lumen.model import Editor, Clip
    late = probe(make_long_video_aac(own_media))
    early_path = make_numbered(own_media, '25', 100, audio=False)
    early = probe(early_path)
    project = Project(width=16, height=16, fps_num=25, media=[early, late],
                      clips=[Clip('late-old', late.id, 0, 0, 800, 40)])
    session = SourceViewerSession(project, early.id)
    editor = Editor(project)
    plan = editor.plan_source_overwrite(early.id, 0, 10, 0, 10)
    before = deepcopy(project)
    revision = editor.revision
    backend = SourcePreviewBackend()
    try:
        frame = backend.inspect_frame(session)
        assert backend.verify_overwrite_sources(session, frame.identity, project, plan)
        assert backend._audio_cache
        assert all(key[0].path == late.path for key in backend._audio_cache)
        assert sum(entry[1] for entry in backend._audio_cache.values()) == 40*1920*8
        assert editor.revision == revision and project == before
    finally:
        backend.close()

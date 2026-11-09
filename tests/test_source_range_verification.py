"""Real bounded source-header/identity checks, without a saved verification cache."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import wave

import pytest

from lumen.media import ffmpeg_available, ffmpeg_binary, probe
from lumen.model import Project, SoundProvenance
from lumen.source_ranges import SourceRange
from lumen.source_viewer import (SourcePreviewBackend, SourceViewerSession,
                                 SourceViewerCancelled, SourceViewerError)
from lumen.source_range_verification import verify_source_range


@pytest.fixture
def local_sources():
    if not ffmpeg_available():
        pytest.skip('FFmpeg/ffprobe unavailable')
    # Bounded synthetic assets only; immediate cleanup keeps aggregate disk low.
    with tempfile.TemporaryDirectory(prefix='cutcat-range-verify-') as root:
        yield Path(root)


@pytest.fixture
def backend():
    value = SourcePreviewBackend()
    try:
        yield value
    finally:
        value.close()


def wav(root, name='source.wav', seconds=2, rate=48000):
    path = root / name
    # A deterministic nonzero waveform, generated without a source decoder.
    pattern = b'\x10\x05\xf0\xfa' * (rate // 2)
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        for _ in range(seconds):
            stream.writeframesraw(pattern)
    return path


def project_session(path, fps=Fraction(30)):
    media = probe(path)
    project = Project(width=16, height=16, fps_num=fps.numerator,
                      fps_den=fps.denominator, media=[media])
    return project, SourceViewerSession(project, media.id)


def record_for(session, result):
    start, end = Fraction(result.in_frame, 1) / session.fps, Fraction(result.out_frame, 1) / session.fps
    return SourceRange('range', 'Selected <take> 片段', session.media_id,
                       start.numerator, start.denominator, end.numerator, end.denominator,
                       session.fps.numerator, session.fps.denominator, result.binding)


def save_selection(backend, project, session, start=6, end=24):
    session.in_frame, session.out_frame = start, end
    saved = verify_source_range(backend, session)
    record = record_for(session, saved)
    project.source_ranges = [record]
    return record, saved


def test_save_close_reopen_unverified_and_real_fresh_recall(local_sources, backend, monkeypatch):
    project, session = project_session(wav(local_sources))
    before = deepcopy(project)
    record, result = save_selection(backend, project, session)
    assert result.is_current(session, project)
    assert record.binding.file_size == Path(session.media.path).stat().st_size
    assert len(record.binding.sampled_content_sha256) == 64
    assert backend._storage_bytes() == 0
    assert backend._temporary is None and backend._audio_temporary is None
    saved = local_sources / 'saved.lumen'
    project.save(saved)
    reopened = Project.load(saved)
    new_session = SourceViewerSession(reopened, session.media_id)
    assert not result.is_current(new_session, reopened)
    import lumen.source_range_verification as module
    calls = []
    original = module._identity
    def fresh(*args):
        calls.append(True)
        return original(*args)
    monkeypatch.setattr(module, '_identity', fresh)
    verified = verify_source_range(backend, new_session, reopened.source_ranges[0])
    assert calls == [True]
    assert (verified.in_frame, verified.out_frame) == (6, 24)
    assert verified.binding == result.binding
    assert new_session.selection() == (0, 60)  # worker never applies marks
    assert project.media == before.media and project.clips == before.clips


def test_same_bytes_relink_path_name_project_canvas_rate_and_organization_independent(local_sources, backend):
    project, session = project_session(wav(local_sources), Fraction(24))
    record, result = save_selection(backend, project, session, 12, 36)
    relocated = local_sources / 'different-name.wav'
    shutil.copyfile(session.media.path, relocated)
    old_inode = Path(session.media.path).stat().st_ino
    assert relocated.stat().st_ino != old_inode
    project.media[0].path, project.media[0].name = str(relocated), 'renamed label'
    project.width, project.height, project.fps_num = 640, 360, 30
    current = SourceViewerSession(project, session.media_id)
    recalled = verify_source_range(backend, current, record)
    assert recalled.binding == result.binding
    assert (recalled.in_frame, recalled.out_frame) == (15, 45)
    assert recalled.identity.path != result.identity.path
    assert recalled.identity.signature != result.identity.signature
    persisted = json.dumps(asdict(record.binding))
    assert str(relocated) not in persisted and str(old_inode) not in persisted


@pytest.mark.parametrize('fps,start,end,okay', [
    (Fraction(25), 6, 24, True), (Fraction(24), 6, 24, False),
    (Fraction(60), 6, 24, True), (Fraction(30000, 1001), 6, 24, False),
])
def test_exact_grid_or_reject_without_mutation(local_sources, backend, fps, start, end, okay):
    project, session = project_session(wav(local_sources))
    record, result = save_selection(backend, project, session, start, end)
    project.fps_num, project.fps_den = fps.numerator, fps.denominator
    current = SourceViewerSession(project, session.media_id)
    before = deepcopy((project, current.__dict__))
    if okay:
        recalled = verify_source_range(backend, current, record)
        assert (recalled.in_frame, recalled.out_frame) == (int(Fraction(start, 30) * fps), int(Fraction(end, 30) * fps))
    else:
        with pytest.raises(SourceViewerError, match='align exactly'):
            verify_source_range(backend, current, record)
    assert (project, current.__dict__) == before


def test_missing_and_shorter_sources_keep_record_and_marks(local_sources, backend):
    project, session = project_session(wav(local_sources))
    record, result = save_selection(backend, project, session, 30, 60)
    path = Path(session.media.path)
    path.unlink()
    with pytest.raises(SourceViewerError, match='Cannot verify'):
        verify_source_range(backend, session, record)
    assert project.source_ranges == [record] and session.selection() == (30, 60)
    wav(local_sources, seconds=1)
    with pytest.raises(SourceViewerError, match='exceeds complete'):
        verify_source_range(backend, session, record)
    # Reimporting shorter metadata still does not clamp/discard the saved data.
    changed = probe(path); changed.id = session.media_id
    project.media = [changed]
    project.validate()
    current = SourceViewerSession(project, session.media_id)
    with pytest.raises(SourceViewerError, match='complete source duration'):
        verify_source_range(backend, current, record)
    assert project.source_ranges == [record]
    saved = local_sources/'shorter.lumen'; project.save(saved)
    assert Project.load(saved).source_ranges == [record]


def test_same_path_same_size_changed_bytes_reject_without_rebinding(local_sources, backend):
    project, session = project_session(wav(local_sources))
    record, result = save_selection(backend, project, session)
    path = Path(session.media.path)
    original_size = path.stat().st_size
    with path.open('r+b') as stream:
        stream.seek(-8, 2); stream.write(b'\x20\x10' * 4)
    assert path.stat().st_size == original_size
    with pytest.raises(SourceViewerError, match='content or interpretation changed'):
        verify_source_range(backend, session, record)
    assert project.source_ranges == [record]
    new = verify_source_range(backend, session)
    assert new.binding != record.binding  # only explicit Save may bind new bytes


def test_changed_file_during_probe_rejected_on_save_and_recall(local_sources, backend, monkeypatch):
    project, session = project_session(wav(local_sources))
    record, result = save_selection(backend, project, session)
    original = backend._probe_source
    def changes_during_probe(*args):
        info = original(*args)
        with Path(session.media.path).open('r+b') as stream:
            stream.seek(-2, 2); stream.write(b'\x01\x01')
        return info
    monkeypatch.setattr(backend, '_probe_source', changes_during_probe)
    for requested in (record, None):
        with pytest.raises(SourceViewerError, match='changed'):
            verify_source_range(backend, session, requested)
        assert project.source_ranges == [record]
    assert not backend._lock.locked()


@pytest.mark.parametrize('field,value', [('duration', 1.5), ('audio_offset', .013),
                                        ('input_encoding', 'srgb'), ('fps_num', 25)])
def test_semantic_metadata_not_silently_reinterpreted(local_sources, backend, field, value):
    project, session = project_session(wav(local_sources))
    record, result = save_selection(backend, project, session)
    setattr(project.media[0], field, value)
    current = SourceViewerSession(project, session.media_id)
    with pytest.raises(SourceViewerError, match='changed'):
        verify_source_range(backend, current, record)
    assert project.source_ranges == [record]


def test_raw_probe_semantic_change_is_bound_but_filename_tags_are_not(local_sources, backend, monkeypatch):
    project, session = project_session(wav(local_sources))
    record, result = save_selection(backend, project, session)
    original = backend._probe_source
    def harmless(*args):
        info = original(*args)
        info['source_format']['filename'] = '/different/location.wav'
        info['source_format']['tags'] = {'title': 'Different title', 'encoder': '/arbitrary/build/path'}
        info['source_streams'][0].setdefault('tags', {})['title'] = 'another name'
        return info
    monkeypatch.setattr(backend, '_probe_source', harmless)
    assert verify_source_range(backend, session, record).binding == result.binding
    def changed(*args):
        info = harmless(*args)
        info['source_streams'][0]['channel_layout'] = 'stereo'
        return info
    monkeypatch.setattr(backend, '_probe_source', changed)
    with pytest.raises(SourceViewerError, match='interpretation changed'):
        verify_source_range(backend, session, record)


def test_real_delayed_embedded_audio_origin_portable(local_sources, backend):
    source = wav(local_sources)
    target = local_sources/'delayed.mp4'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-f', 'lavfi',
        '-i', 'color=red:size=16x16:rate=30:duration=2', '-itsoffset', '0.013',
        '-i', str(source), '-c:v', 'libx264', '-c:a', 'aac', '-threads', '1',
        str(target)], check=True, capture_output=True, timeout=15)
    project, session = project_session(target)
    record, result = save_selection(backend, project, session)
    moved = local_sources/'moved.mp4'; shutil.copyfile(target, moved)
    project.media[0].path = str(moved)
    current = SourceViewerSession(project, session.media_id)
    assert verify_source_range(backend, current, record).binding == result.binding
    project.media[0].audio_offset += .1
    with pytest.raises(SourceViewerError, match='offset changed'):
        verify_source_range(backend, SourceViewerSession(project, session.media_id), record)


def test_long_save_metadata_never_decodes_or_allocates_cache(local_sources, backend, monkeypatch):
    project, session = project_session(wav(local_sources, seconds=40, rate=8000))
    import lumen.source_viewer as module
    commands = []
    original = module._run
    def no_decode(args, *other):
        commands.append(args)
        assert Path(args[0]).name.startswith('ffprobe')
        return original(args, *other)
    monkeypatch.setattr(module, '_run', no_decode)
    result = verify_source_range(backend, session)
    assert (result.in_frame, result.out_frame) == (0, 1200)
    assert len(commands) == 1
    assert backend._storage_bytes() == 0
    assert backend._temporary is None and backend._audio_temporary is None
    from lumen.model import Editor
    editor = Editor(project)
    plan = editor.plan_source_overwrite(session.media_id, 2, 0, 0, 1200)
    with pytest.raises(SourceViewerError, match='30 seconds'):
        backend.verify_overwrite_sources(session, result.identity, project, plan)
    assert len(commands) == 1  # proof rejection occurs before any decoding


def test_generated_source_full_hash_provenance_and_portability(local_sources, backend):
    path = wav(local_sources, seconds=1)
    project, _ = project_session(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    project.media[0].sound_provenance = SoundProvenance(
        'test-wave', 'procedural', 'CC0-1.0', digest, digest, 48000, 48000)
    session = SourceViewerSession(project, project.media[0].id)
    record, result = save_selection(backend, project, session)
    moved = local_sources/'generated-moved.wav'; shutil.copyfile(path, moved)
    project.media[0].path = str(moved)
    current = SourceViewerSession(project, session.media_id)
    assert verify_source_range(backend, current, record).binding == result.binding
    with moved.open('r+b') as stream:
        stream.seek(-2, 2); stream.write(b'\x01\x00')
    with pytest.raises(SourceViewerError, match='Generated source has changed'):
        verify_source_range(backend, current, record)


def test_cancel_deadline_and_closed_backend_release_lock(local_sources, monkeypatch):
    project, session = project_session(wav(local_sources))
    backend = SourcePreviewBackend(timeout=.01)
    cancel = threading.Event(); cancel.set()
    with pytest.raises(SourceViewerCancelled):
        verify_source_range(backend, session, cancel=cancel)
    assert not backend._lock.locked()
    import lumen.source_range_verification as module
    import time
    original = module._identity
    def too_late(*args):
        time.sleep(.015)
        return original(*args)
    monkeypatch.setattr(module, '_identity', too_late)
    with pytest.raises(SourceViewerError, match='runtime budget'):
        verify_source_range(backend, session)
    assert not backend._lock.locked()
    backend.close()
    with pytest.raises(SourceViewerError, match='closed'):
        verify_source_range(backend, session)
    assert not backend._lock.locked()


def test_result_currency_no_io_and_other_media_refused(local_sources, backend, monkeypatch):
    project, session = project_session(wav(local_sources))
    record, result = save_selection(backend, project, session)
    def fail(*args, **kwargs):
        raise AssertionError('pure result currency performed I/O')
    monkeypatch.setattr(Path, 'stat', fail); monkeypatch.setattr(Path, 'lstat', fail)
    monkeypatch.setattr(subprocess, 'Popen', fail)
    assert result.is_current(session, project)
    session.mark_in()
    assert not result.is_current(session, project)
    with pytest.raises(SourceViewerError, match='another media'):
        verify_source_range(backend, session, replace(record, media_id='other'))


@pytest.mark.parametrize('capture,current,marks,expected', [
    (Fraction(24000, 1001), Fraction(30000, 1001), (4, 12), (5, 15)),
    (Fraction(30000, 1001), Fraction(24000, 1001), (5, 15), (4, 12)),
    (Fraction(24000, 1001), Fraction(30000, 1001), (1, 12), None),
])
def test_fractional_rate_portable_exact_mapping(local_sources, backend, capture, current, marks, expected):
    project, session = project_session(wav(local_sources), capture)
    record, result = save_selection(backend, project, session, *marks)
    project.fps_num, project.fps_den = current.numerator, current.denominator
    new_session = SourceViewerSession(project, session.media_id)
    if expected is None:
        with pytest.raises(SourceViewerError, match='align exactly'):
            verify_source_range(backend, new_session, record)
    else:
        recalled = verify_source_range(backend, new_session, record)
        assert recalled.binding == record.binding
        assert (recalled.in_frame, recalled.out_frame) == expected
    assert project.source_ranges == [record]


def test_cancel_while_waiting_for_shared_worker_lock(local_sources):
    project, session = project_session(wav(local_sources))
    backend = SourcePreviewBackend(timeout=1)
    backend._lock.acquire()
    cancel = threading.Event()
    errors = []
    def operation():
        try:
            verify_source_range(backend, session, cancel=cancel)
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=operation)
    thread.start(); cancel.set(); thread.join(1)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], SourceViewerCancelled)
    assert backend._lock.locked()  # cancellation never releases somebody else's lock
    backend._lock.release(); backend.close()


def test_invalid_record_fails_before_io(local_sources, backend, monkeypatch):
    project, session = project_session(wav(local_sources))
    def fail(*args, **kwargs):
        raise AssertionError('invalid saved record performed I/O')
    monkeypatch.setattr(Path, 'lstat', fail)
    with pytest.raises(SourceViewerError, match='Invalid saved source range'):
        verify_source_range(backend, session, {'media_id': session.media_id})
    assert not backend._lock.locked()

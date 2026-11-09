"""Bounded proof scopes: only new/split trims decode, moves still revalidate."""
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import wave

import pytest

from lumen.media import ffmpeg_available, probe
from lumen.model import Clip, Editor, Project
from lumen.source_viewer import (SourcePreviewBackend, SourceViewerCancelled,
                                SourceViewerError, SourceViewerSession)

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg required')


def pcm_file(path, seconds, value=100):
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(8000)
        block = value.to_bytes(2, 'little', signed=True) * 8000
        for _ in range(seconds): stream.writeframesraw(block)
    return probe(path)


@pytest.fixture
def inputs():
    with tempfile.TemporaryDirectory(prefix='cutcat-insert-proof-') as directory:
        root = Path(directory)
        selected = pcm_file(root / 'selected.wav', 2)
        moved = pcm_file(root / 'moved.wav', 66)
        project = Project(width=16, height=16, media=[selected, moved], clips=[
            Clip('downstream', moved.id, 2, 20, 900, 1080, gain_db=-2, fade_in=3, fade_out=4)])
        yield root, Editor(project), selected, moved


def prepared(editor, selected, backend):
    session = SourceViewerSession(editor.project, selected.id)
    identity = backend.inspect_frame(session).identity
    return session, identity


def test_long_downstream_move_has_no_full_source_decode_and_all_identities_rechecked(inputs, monkeypatch):
    _, editor, selected, moved = inputs
    backend = SourcePreviewBackend()
    try:
        session, identity = prepared(editor, selected, backend)
        plan = editor.plan_source_insert(selected.id, 2, 0, 0, 30)
        assert plan.proof_clip_ids == () and len(plan.translations) == 1
        prepared_ids, probed_ids, revalidated_ids, deadlines = [], [], [], []
        prepare = backend._prepare_pcm; probe_source = backend._probe_source; revalidate = backend._revalidate
        def observe_prepare(identity, info, request, windows, cancel, deadline, **kwargs):
            prepared_ids.append((request.media_id, windows)); deadlines.append(deadline)
            return prepare(identity, info, request, windows, cancel, deadline, **kwargs)
        def observe_probe(request, identity, cancel, deadline):
            probed_ids.append(request.media_id); deadlines.append(deadline)
            return probe_source(request, identity, cancel, deadline)
        def observe_revalidate(request, identity, cancel, deadline):
            revalidated_ids.append(request.media_id); deadlines.append(deadline)
            return revalidate(request, identity, cancel, deadline)
        monkeypatch.setattr(backend, '_prepare_pcm', observe_prepare)
        monkeypatch.setattr(backend, '_probe_source', observe_probe)
        monkeypatch.setattr(backend, '_revalidate', observe_revalidate)
        assert backend.verify_insert_sources(session, identity, editor.project, plan)
        assert prepared_ids == [(selected.id, [(0, 48000)])]
        assert set(probed_ids) == set(revalidated_ids) == {selected.id, moved.id}
        assert len(set(deadlines)) == 1
        assert backend._scan_samples == 48000
        assert sum(entry[1] for entry in backend._audio_cache.values()) == 48000 * 8
    finally: backend.close()


def test_split_original_range_still_counts_against_same_30_second_budget(inputs, monkeypatch):
    _, editor, selected, moved = inputs
    editor.project.clips[0].fade_in = editor.project.clips[0].fade_out = 0
    plan = editor.plan_source_insert(selected.id, 2, 500, 0, 30)
    assert plan.proof_clip_ids == ('downstream',)
    backend = SourcePreviewBackend()
    try:
        session, identity = prepared(editor, selected, backend)
        def forbidden(*a, **kw): raise AssertionError('Decode started before aggregate budget rejection')
        monkeypatch.setattr(backend, '_prepare_pcm', forbidden)
        before = deepcopy(editor.project)
        with pytest.raises(SourceViewerError, match='30 seconds'):
            backend.verify_insert_sources(session, identity, editor.project, plan)
        assert editor.project == before and not editor._undo
    finally: backend.close()


def test_downstream_file_changed_during_selected_proof_rejects(inputs, monkeypatch):
    root, editor, selected, moved = inputs
    backend = SourcePreviewBackend()
    try:
        session, identity = prepared(editor, selected, backend)
        plan = editor.plan_source_insert(selected.id, 2, 0, 0, 30)
        prepare = backend._prepare_pcm
        def change_downstream(*args, **kwargs):
            result = prepare(*args, **kwargs)
            pcm_file(root / 'moved.wav', 66, 101)
            return result
        monkeypatch.setattr(backend, '_prepare_pcm', change_downstream)
        with pytest.raises(SourceViewerError, match='changed'):
            backend.verify_insert_sources(session, identity, editor.project, plan)
        assert not editor._undo and editor.revision == 0
    finally: backend.close()


@pytest.mark.parametrize('restriction', ['cancel', 'scan', 'storage', 'selected_replaced'])
def test_insert_uses_existing_fail_closed_resource_and_identity_gates(inputs, restriction):
    root, editor, selected, moved = inputs
    if restriction == 'scan':
        selected = moved
        backend = SourcePreviewBackend(max_scan_samples=48000)
        lo, hi = 60 * 30, 61 * 30
    else:
        backend = SourcePreviewBackend(max_storage_bytes=100) if restriction == 'storage' else SourcePreviewBackend()
        lo, hi = 0, 30
    try:
        session, identity = prepared(editor, selected, backend)
        plan = editor.plan_source_insert(selected.id, 2, 0, lo, hi)
        cancel = threading.Event()
        if restriction == 'cancel': cancel.set()
        if restriction == 'selected_replaced': pcm_file(Path(selected.path), 2, 101)
        with pytest.raises(SourceViewerError):
            backend.verify_insert_sources(session, identity, editor.project, plan, cancel=cancel)
        assert not editor._undo and editor.revision == 0
    finally: backend.close()


def test_wrong_plan_type_rejected_before_source_io(inputs, monkeypatch):
    _, editor, selected, _ = inputs
    backend = SourcePreviewBackend()
    try:
        plan = editor.plan_source_overwrite(selected.id, 2, 0, 0, 10)
        with pytest.raises(SourceViewerError, match='Invalid source insert plan'):
            backend.verify_insert_sources(None, None, editor.project, plan)
    finally: backend.close()

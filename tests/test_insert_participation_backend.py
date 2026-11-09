"""Explicit followers reuse bounded proof, not full-timeline decoding."""
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
import threading

import pytest

from lumen.media import ffmpeg_available
from lumen.model import Caption, Clip, Editor, Project
from lumen.source_viewer import SourcePreviewBackend, SourceViewerError, SourceViewerSession
from test_retime_video import make_source
from test_source_insert_backend import pcm_file, prepared

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


@pytest.fixture
def cross_inputs(tmp_path):
    selected = pcm_file(tmp_path / 'selected.wav', 2)
    moved = pcm_file(tmp_path / 'moved.wav', 66)
    fixed = pcm_file(tmp_path / 'fixed.wav', 2)
    project = Project(width=16, height=16, media=[selected, moved, fixed], clips=[
        Clip('follower', moved.id, 3, 20, 900, 1080, gain_db=-2, fade_in=3, fade_out=4),
        Clip('fixed', fixed.id, 2, 0, 0, 5)], captions=[Caption('caption', 20, 10, 'Follow')])
    return tmp_path, Editor(project), selected, moved, fixed


def test_long_follower_only_fresh_identity_and_compiler_not_full_source_decode(cross_inputs, monkeypatch):
    _, editor, selected, moved, fixed = cross_inputs
    backend = SourcePreviewBackend()
    try:
        session, identity = prepared(editor, selected, backend)
        plan = editor.plan_source_insert(selected.id, 2, 10, 0, 30,
                                         participating_tracks=(2, 3), follow_captions=True)
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
        before = deepcopy(editor.project)
        assert backend.verify_insert_sources(session, identity, editor.project, plan)
        assert editor.project == before
        assert prepared_ids == [(selected.id, [(0, 48000)])]
        assert set(probed_ids) == set(revalidated_ids) == {selected.id, moved.id}
        assert fixed.id not in probed_ids
        assert len(set(deadlines)) == 1
        assert backend._scan_samples == 48000
        assert sum(entry[1] for entry in backend._audio_cache.values()) == 48000 * 8
    finally:
        backend.close()


def test_audio_destination_can_move_silent_picture_follower_with_identity_only(tmp_path, monkeypatch):
    selected = pcm_file(tmp_path / 'selected.wav', 2)
    picture = make_source(tmp_path / 'silent-picture.mkv', Fraction(30)).media
    project = Project(width=64, height=48, media=[selected, picture], clips=[
        Clip('silent-picture', picture.id, 0, 40, 0, 20)])
    editor = Editor(project); backend = SourcePreviewBackend()
    try:
        session, identity = prepared(editor, selected, backend)
        plan = editor.plan_source_insert(selected.id, 2, 10, 0, 30, participating_tracks=(0, 2))
        def forbidden(*a, **kw):
            raise AssertionError('A whole moved picture must not consume CFR source proof')
        monkeypatch.setattr(backend, '_prove_cfr', forbidden)
        assert backend.verify_insert_sources(session, identity, project, plan)
        editor.apply_source_insert(plan)
        assert next(c for c in project.clips if c.id == 'silent-picture').start == 70
    finally:
        backend.close()


def test_changed_follower_source_during_selected_proof_rejects_every_collection(cross_inputs, monkeypatch):
    root, editor, selected, moved, _ = cross_inputs
    backend = SourcePreviewBackend()
    try:
        session, identity = prepared(editor, selected, backend)
        plan = editor.plan_source_insert(selected.id, 2, 10, 0, 30,
                                         participating_tracks=(2, 3), follow_captions=True)
        before = deepcopy((editor.project, editor._undo, editor._redo, editor.revision))
        prepare = backend._prepare_pcm
        def mutate_follower(*a, **kw):
            result = prepare(*a, **kw)
            pcm_file(root / 'moved.wav', 66, 101)
            return result
        monkeypatch.setattr(backend, '_prepare_pcm', mutate_follower)
        with pytest.raises(SourceViewerError, match='changed'):
            backend.verify_insert_sources(session, identity, editor.project, plan)
        assert (editor.project, editor._undo, editor._redo, editor.revision) == before
    finally:
        backend.close()


def test_selected_plus_destination_split_budget_unchanged_with_long_follower(cross_inputs, monkeypatch):
    _, editor, selected, moved, _ = cross_inputs
    editor.project.clips.append(Clip('long-target', moved.id, 2, 10, 0, 960))
    editor.project.clips[0].start = 1000
    editor.project.captions[0].start = 1000
    plan = editor.plan_source_insert(selected.id, 2, 500, 0, 30,
                                     participating_tracks=(2, 3), follow_captions=True)
    assert plan.proof_clip_ids == ('long-target',)
    backend = SourcePreviewBackend()
    try:
        session, identity = prepared(editor, selected, backend)
        def forbidden(*a, **kw):
            raise AssertionError('Decode started before the unchanged aggregate proof cap')
        monkeypatch.setattr(backend, '_prepare_pcm', forbidden)
        before = deepcopy(editor.project)
        with pytest.raises(SourceViewerError, match='30 seconds'):
            backend.verify_insert_sources(session, identity, editor.project, plan)
        assert editor.project == before and not editor._undo
    finally:
        backend.close()


@pytest.mark.parametrize('restriction', ['cancel', 'scan', 'storage', 'selected_replaced'])
def test_cross_track_insert_keeps_cancel_scan_storage_and_source_identity_gates(cross_inputs, restriction):
    _, editor, selected, moved, _ = cross_inputs
    if restriction == 'scan':
        selected = moved
        backend = SourcePreviewBackend(max_scan_samples=48000)
        lo, hi = 60 * 30, 61 * 30
    else:
        backend = SourcePreviewBackend(max_storage_bytes=100) if restriction == 'storage' else SourcePreviewBackend()
        lo, hi = 0, 30
    try:
        session, identity = prepared(editor, selected, backend)
        plan = editor.plan_source_insert(selected.id, 2, 10, lo, hi,
                                         participating_tracks=(2, 3), follow_captions=True)
        before = deepcopy((editor.project, editor._undo, editor._redo, editor.revision))
        cancel = threading.Event()
        if restriction == 'cancel': cancel.set()
        if restriction == 'selected_replaced': pcm_file(Path(selected.path), 2, 101)
        with pytest.raises(SourceViewerError):
            backend.verify_insert_sources(session, identity, editor.project, plan, cancel=cancel)
        assert (editor.project, editor._undo, editor._redo, editor.revision) == before
    finally:
        backend.close()

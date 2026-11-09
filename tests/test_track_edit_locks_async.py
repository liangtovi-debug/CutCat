"""Queued work cannot cross a lock edit into unintended timeline mutation.

Synthesis and measurements are mocked deterministically, not runtime claims.
Read-only audio analysis intentionally remains valid across signal-neutral locks.
"""
from copy import deepcopy
from pathlib import Path
import threading

import pytest

from lumen.model import Media
from lumen.ui_audio_analysis import open_audio_analysis
from lumen.ui_loudness_proposal import open_loudness_proposal
import lumen.ui_loudness_proposal as proposal_module
from test_master_gain_ui import app, window
from test_ui import window as speech_window, pump_until
from test_audio_analysis_ui import held_analysis, settle as settle_analysis
from test_loudness_proposal_ui import ready, verified, settle
from test_tts_workflow_revalidation import configure_mock_synthesis, isolated_voice_settings


def frozen_state(window):
    return (deepcopy(window.project), deepcopy(window.editor._undo),
            deepcopy(window.editor._redo), window.editor.revision, window.revision)


@pytest.mark.parametrize('phase', ['measure', 'apply'])
@pytest.mark.parametrize('undo_lock', [False, True], ids=['lock', 'lock-undo-ABA'])
def test_pending_loudness_draft_cannot_commit_across_lock_revision(
        window, monkeypatch, phase, undo_lock):
    started, release = threading.Event(), threading.Event()
    value = verified()
    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return value if phase == 'measure' else None
    if phase == 'apply':
        dialog = ready(window, monkeypatch)
        monkeypatch.setattr(proposal_module, 'validate_verified_proposal', blocked)
        dialog.apply_settings()
    else:
        monkeypatch.setattr(proposal_module, 'verify_loudness_proposal', blocked)
        dialog = open_loudness_proposal(window, object())
        dialog.measure()
    try:
        assert started.wait(2)
        window.editor.set_track_edit_lock(2, True); window.changed()
        if undo_lock:
            window.undo()
        before = frozen_state(window)
        dialog.check_current()
        assert not dialog.apply_button.isEnabled()
    finally:
        release.set(); settle(window)
    assert frozen_state(window) == before
    assert window.project.master_gain_db == 0
    assert dialog.verified is None
    assert not window.test_warnings


def test_read_only_analysis_started_before_lock_keeps_signal_valid_report(window, monkeypatch):
    started, release = held_analysis(monkeypatch, window)
    paths = {Path(m.path): Path(m.path).read_bytes() for m in window.project.media}
    dialog = open_audio_analysis(window); dialog.analyze()
    try:
        assert started.wait(2)
        window.editor.set_track_edit_lock(2, True); window.changed()
        before = frozen_state(window)
    finally:
        release.set(); settle_analysis(window)
    assert dialog.state == 'current' and dialog.result is not None
    assert frozen_state(window) == before
    assert all(path.read_bytes() == contents for path, contents in paths.items())
    assert not window.test_warnings


def test_queued_synthesis_result_after_lock_saves_take_without_insertion(
        speech_window, app, tmp_path, monkeypatch):
    w = speech_window
    _, durable, calls, _ = configure_mock_synthesis(w, tmp_path, monkeypatch)
    w.speak_selected()
    job = w.tts_job
    # Let the worker finish without delivering its queued result to the GUI.
    assert job.wait(3000)
    original_bytes = durable.read_bytes()
    assert len(calls) == 1 and not w.project.media
    w.editor.set_track_edit_lock(2, True); w.changed()
    before = frozen_state(w)
    pump_until(app, lambda: not w.jobs)
    assert frozen_state(w) == before
    assert not w.project.media and not w.project.clips and w.pending_speech is None
    assert durable.read_bytes() == original_bytes
    assert w.test_warnings


def test_pending_saved_voice_cannot_insert_into_newly_locked_free_track(
        speech_window, app, tmp_path, monkeypatch):
    w = speech_window
    cue, durable, calls, _ = configure_mock_synthesis(w, tmp_path, monkeypatch)
    w.editor.add_media(Media('existing', str(tmp_path / 'existing.wav'), 'Existing',
                            2, has_video=False, has_audio=True))
    w.editor.add_clip('existing', 2, 0)
    w.changed(); w.select_caption(cue.id)
    w.speak_selected(); pump_until(app, lambda: not w.jobs)
    assert w.pending_speech is not None
    source_bytes = durable.read_bytes()
    provenance = deepcopy(w.pending_speech[0].speech_provenance)
    w.tts_track.setCurrentIndex(w.tts_track.findData(3))
    w.editor.set_track_edit_lock(3, True); w.changed()
    before = frozen_state(w)
    w.insert_pending_speech()
    assert frozen_state(w) == before
    assert durable.read_bytes() == source_bytes
    assert w.pending_speech is not None
    assert w.pending_speech[0].speech_provenance == provenance
    w.editor.set_track_edit_lock(3, False); w.changed()
    before_insert = deepcopy(w.project)
    w.insert_pending_speech()
    assert w.pending_speech is None and w.selected_clip().track == 3
    assert w.project.media[-1].speech_provenance == provenance
    w.undo(); assert w.project == before_insert
    w.redo(); assert w.project.media[-1].speech_provenance == provenance
    assert len(calls) == 1 and durable.read_bytes() == source_bytes


def test_sound_selection_preceding_lock_cannot_append_on_locked_track(window):
    w = window
    entry = w.selected_sound()
    source_bytes = entry.path.read_bytes()
    w.sound_track.setCurrentIndex(w.sound_track.findData(3))
    w.sound_position.setCurrentIndex(w.sound_position.findData('append'))
    w.editor.set_track_edit_lock(3, True); w.changed()
    before = frozen_state(w)
    w.insert_sound()
    assert frozen_state(w) == before
    assert entry.path.read_bytes() == source_bytes
    assert w.test_warnings or not w.sound_insert_button.isEnabled()

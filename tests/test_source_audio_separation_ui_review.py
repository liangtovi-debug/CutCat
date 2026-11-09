"""Independent UI lifetime/intent review for staged source audio separation."""
from copy import deepcopy
from pathlib import Path
import threading
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

import lumen.ui_source_audio_separation as ui
from lumen.model import Editor
from test_source_audio_separation_ui import app, window, video_window, dialog, preview, held, wait_started
from test_clip_copy_paste_ui import settle, state


@pytest.mark.parametrize('phase',['preview','apply'])
def test_cancelled_old_worker_cannot_touch_reopened_or_new_session(video_window,app,monkeypatch,phase):
    w=video_window;d=dialog(w,app)
    if phase=='apply':preview(w,app,d)
    started,release=held(monkeypatch);getattr(d,phase)();wait_started(app,started)
    expected=deepcopy(w.project)
    try:
        d.reject()
        # An explicitly installed new editor session must make both result and
        # dialog lifetime stale even if every project parameter is identical.
        w.editor=Editor(deepcopy(w.project));w.refresh()
        assert w.source_audio_separation_dialog is None
    finally:
        release.set();settle(w,app)
    assert w.project==expected and not w.editor.can_undo
    w.select_clip('v')  # New sessions deliberately clear the old selection.
    fresh=dialog(w,app,None)
    assert fresh is not d and fresh.preview_receipt is None and fresh.plan is None
    assert not fresh.apply_button.isEnabled() and not w.test_warnings


def test_failed_apply_requires_new_preview_then_clean_retry(video_window,app):
    w=video_window;d=dialog(w,app);preview(w,app,d)
    before=state(w);source=Path(w.project.media[0].path)
    original=source.read_bytes();source.write_bytes(b'replaced source during review')
    d.apply();settle(w,app)
    assert state(w)==before and not d.apply_button.isEnabled() and d.preview_receipt is None
    assert 'changed' in d.impact.text()
    # Restoring content still has a new filesystem identity. Only an explicit
    # fresh Preview may authorize another Apply attempt.
    source.write_bytes(original);preview(w,app,d)
    assert 'Cannot separate' not in d.impact.text()
    d.apply();settle(w,app)
    assert len(w.project.clips)==3 and len(w.editor._undo)==1 and d._closed_flag


def test_open_cancel_discards_preview_but_keeps_project(video_window,app,monkeypatch):
    w=video_window;d=dialog(w,app);preview(w,app,d);before=state(w)
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *_a,**_k:('',''))
    w.open_project();app.processEvents()
    assert state(w)==before and d._closed_flag and w.source_audio_separation_dialog is None
    fresh=dialog(w,app,None)
    assert fresh.preview_receipt is None and not fresh.apply_button.isEnabled()


@pytest.mark.parametrize('choice',[QMessageBox.StandardButton.No,QMessageBox.StandardButton.Yes])
def test_main_close_during_work_has_explicit_cancel_boundary(video_window,app,monkeypatch,choice):
    w=video_window;d=dialog(w,app);started,release=held(monkeypatch)
    d.preview();wait_started(app,started);before=deepcopy(w.project)
    monkeypatch.setattr(QMessageBox,'question',lambda *_a,**_k:choice)
    w.dirty=False
    try:
        w.close();app.processEvents()
        if choice==QMessageBox.StandardButton.No:
            assert not w.closing and not d._closed_flag
        else:
            assert w.closing and d.preview_receipt is None
    finally:
        release.set();settle(w,app)
    assert w.project==before and not w.editor.can_undo
    if choice==QMessageBox.StandardButton.No:
        assert d.preview_receipt is not None and d.apply_button.isEnabled()
    else:
        deadline=time.monotonic()+2
        while w.isVisible() and time.monotonic()<deadline:app.processEvents();time.sleep(.005)
        assert not w.isVisible() and d._closed_flag and w.source_audio_separation_dialog is None


def test_video_only_source_does_not_claim_embedded_audio(video_window,app):
    w=video_window;w.project.media[0].has_audio=False;w.refresh_inspector();app.processEvents()
    assert not w.can_separate_source_audio() and not w.separate_source_audio_button.isEnabled()
    assert 'Source audio is part of this clip' not in w.source_audio_status.text()
    assert 'Separate it to edit' not in w.source_audio_status.text()


def test_separation_history_restores_component_selection(video_window,app):
    w=video_window;d=dialog(w,app);preview(w,app,d);d.apply();settle(w,app)
    audio_id=w.project.clips[-1].id
    assert w.selected_clip_ids==(audio_id,)
    w.undo()
    assert w.selected_clip_ids==('v',)
    assert w.editor._clip('v').source_audio_enabled
    w.redo()
    assert w.selected_clip_ids==(audio_id,)
    assert not w.editor._clip('v').source_audio_enabled


def test_separation_selection_receipt_ignores_unrelated_history_and_new_session(video_window,app):
    w=video_window;d=dialog(w,app);preview(w,app,d);d.apply();settle(w,app)
    audio_id=w.project.clips[-1].id
    w.edit(lambda:w.editor.update_clip(audio_id,gain_db=-12))
    w.undo();assert w.selected_clip_ids==(audio_id,) and w.editor._clip(audio_id).gain_db==-6
    w.undo();assert w.selected_clip_ids==('v',) and len(w.project.clips)==2
    w.redo();assert w.selected_clip_ids==(audio_id,)
    w.redo();assert w.selected_clip_ids==(audio_id,) and w.editor._clip(audio_id).gain_db==-12
    assert w._source_audio_selection_history
    w.editor=Editor(deepcopy(w.project));w.refresh()
    assert not w._source_audio_selection_history and not w.selected_clip_ids


def test_discarded_separation_redo_receipts_are_pruned(video_window,app):
    w=video_window
    d=dialog(w,app);preview(w,app,d);d.apply();settle(w,app)
    first=w.project.clips[-1].id
    assert len(w._source_audio_selection_history)==1
    w.undo();assert w.selected_clip_ids==('v',)
    d=dialog(w,app);preview(w,app,d);d.apply();settle(w,app)
    second=w.project.clips[-1].id
    assert second!=first and not w.editor.can_redo
    assert len(w._source_audio_selection_history)==1
    assert next(iter(w._source_audio_selection_history.values()))==('v',second)
    w.undo();assert w.selected_clip_ids==('v',)
    w.redo();assert w.selected_clip_ids==(second,)

"""Clip-set ownership, explicit command limits, non-dirty selection and focus."""
from copy import deepcopy

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QLineEdit, QComboBox, QSpinBox
from lumen.model import Clip, Editor
from lumen.ui_recovery import project_fingerprint
from test_speed_ui import app, window


def group(w):
    w.project.clips.extend([Clip('second', 'm', 1, 60, 0, 20),
                            Clip('third', 'm', 2, 80, 0, 20)])
    w.refresh(); w.select_clip('c'); w.toggle_clip_selection('second')
    return w.selected_clip_ids


def state(w):
    return deepcopy((w.project.to_dict(), w.editor.revision, w.revision,
                     w.editor._undo, w.editor._redo, w.dirty, w.preview_revision))


def test_selection_is_ephemeral_and_last_toggle_clears(window):
    w = window; ids = group(w); before = state(w)
    assert ids == ('c', 'second') and w.selected_clip() is None
    assert not w.inspector.isEnabled() and 'Move and delete only' in w.clip_name.text()
    assert not w.split_clip_button.isEnabled() and w.delete_clips_button.isEnabled()
    assert not w.shortcuts.enabled('edit.split') and w.shortcuts.enabled('edit.delete')
    generation = w.selection_generation
    w.toggle_clip_selection('c'); assert w.selected_clip().id == 'second'
    w.toggle_clip_selection('second'); assert w.selected_clip_ids == () and w.selection == ''
    assert w.selection_generation == generation + 2 and state(w) == before


def test_atomic_delete_one_history_preserves_assets_and_nonripple(window):
    w = window; group(w); before = deepcopy(w.project)
    revision = w.revision; depth = len(w.editor._undo)
    w.delete_selected()
    assert [c.id for c in w.project.clips] == ['third']
    assert w.project.clips[0].start == 80 and w.project.media == before.media
    assert len(w.editor._undo) == depth + 1 and w.revision == revision + 1
    assert w.selected_clip_ids == ()
    w.undo(); assert w.project == before and w.selected_clip_ids == ()
    w.redo(); assert [c.id for c in w.project.clips] == ['third']


def test_group_noop_preserves_undo_redo_dirty_and_preview(window):
    w = window; ids = group(w)
    w.editor.move_clips(ids, 4); w.changed(); w.undo()
    before = state(w)
    w.move_selected_clips(ids, 0)
    w.edit(lambda: w.editor.move_clips(ids, 0))
    assert state(w) == before


def test_same_ids_new_editor_clears_selection(window):
    w = window; group(w); generation = w.selection_generation
    w.editor = Editor(deepcopy(w.project)); w.refresh()
    assert w.selected_clip_ids == () and w.selection == ''
    assert w.selection_generation > generation


def test_multiselect_single_clip_commands_do_not_apply(window):
    w = window; group(w); before = state(w)
    for command in (w.split_selected, w.ripple_delete_selected, w.apply_timing,
                    w.apply_audio, w.apply_stabilization, w.reset_stabilization,
                    w.apply_restoration, w.reset_restoration, w.apply_speed):
        command()
    assert state(w) == before


@pytest.mark.parametrize('kind', ['text', 'number', 'combo', 'media', 'dialog', 'ime'])
def test_delete_and_split_focus_never_erase_set(window, app, kind):
    w = window; group(w); w.show(); app.processEvents()
    extra = None
    if kind in ('text', 'number', 'combo'):
        focus = {'text': QLineEdit, 'number': QSpinBox, 'combo': QComboBox}[kind]()
        w.addToolBar('Focus safety').addWidget(focus)
        app.processEvents()
    elif kind == 'media': focus = w.bin
    elif kind == 'ime': focus = w.timeline
    else:
        extra = QDialog(w); layout = QVBoxLayout(extra); focus = QLineEdit(); layout.addWidget(focus); extra.show()
    focus.setFocus(); app.processEvents()
    if kind == 'ime':
        QApplication.sendEvent(focus, QInputMethodEvent('拼', []))
    before = state(w)
    w.delete_active_selection(); w.split_selected()
    assert state(w) == before
    if kind == 'ime': QApplication.sendEvent(focus, QInputMethodEvent('', []))
    if extra: extra.close()


def test_single_selection_restores_asr_eligibility(window):
    w = window; group(w)
    w.asr_source.setCurrentIndex(0)
    assert not w.asr_button.isEnabled()
    w.select_clip('c'); assert w.asr_button.isEnabled()
    w.clear_clip_selection(); w.update_playback_controls()
    assert not w.asr_button.isEnabled()
    w.select_clip('c'); assert w.asr_button.isEnabled()


@pytest.mark.parametrize('helper', ['shortcuts', 'color'])
def test_smoke_helpers_restore_complete_selection_on_failure(window, monkeypatch, tmp_path, helper):
    from lumen.self_test import _shortcut_runtime_checks
    from lumen.color_pipeline_smoke import capture_color_pipeline_ui
    w = window; ids = group(w); original = w.editor; anchor = w.selection
    before = state(w); refresh = w.refresh; calls = 0
    def fail_first_refresh():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('injected temporary session failure')
        return refresh()
    monkeypatch.setattr(w, 'refresh', fail_first_refresh)
    with pytest.raises(RuntimeError, match='injected temporary'):
        if helper == 'shortcuts': _shortcut_runtime_checks(w, tmp_path, [])
        else: capture_color_pipeline_ui(w, deepcopy(w.project), tmp_path, [])
    assert w.editor is original and w.selected_clip_ids == ids and w.selection == anchor
    assert state(w) == before

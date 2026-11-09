"""Actual inspector controls for exactly four linked roles; no source work."""
from copy import deepcopy
from fractions import Fraction

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from lumen.i18n import set_locale, tr
from lumen.model import AudioMixAnchor, Clip, Editor, Media, Project
from lumen.timing import ClipTimeMap
from lumen.ui_linked_av_roll import ROLL_SCOPE, ROLL_SELECTION, ROLL_STALE, ROLL_TOOLTIP
from test_master_gain_ui import app, window


ROLES = ('lv', 'la', 'rv', 'ra')


@pytest.fixture
def roll_window(window, tmp_path):
    w = window
    first = tmp_path / 'first.mov'; first.write_bytes(b'not decoded by the roll inspector')
    second = tmp_path / 'second.mov'; second.write_bytes(b'not hashed by the roll inspector')
    left = ClipTimeMap(Fraction(1, 2), 31, Fraction(3, 2))
    right = ClipTimeMap(20, Fraction(101, 2), Fraction(2, 3))
    w.editor = Editor(Project(media=[
        Media('first', str(first), 'First <picture>', 20, 64, 48, True, True),
        Media('second', str(second), 'Second & sound', 20, 64, 48, True, True)], clips=[
        Clip('lv', 'first', 0, 10, 0, 21, time_map=left, source_audio_enabled=False),
        Clip('la', 'first', 2, 10, 0, 21, time_map=left, fade_in=2, fade_out=3,
             gain_db=-6, audio_mix_anchor=AudioMixAnchor(0, 10, 'lv')),
        Clip('rv', 'second', 0, 31, 0, 46, time_map=right, source_audio_enabled=False),
        Clip('ra', 'second', 2, 31, 0, 46, time_map=right, fade_in=3, fade_out=2,
             gain_db=-9, audio_mix_anchor=AudioMixAnchor(0, 31, 'rv')),
        Clip('other', 'first', 1, 4, 40, 15)]))
    w.editor.link_av_clips('lv', 'la'); w.editor.link_av_clips('rv', 'ra')
    w.refresh()
    select_roles(w)
    return w


def select_roles(w, anchor='lv', ids=ROLES):
    w.active_selection_type = 'clip'
    w.set_selected_clip_ids(ids, anchor=anchor)
    w.refresh_inspector()


def state(w):
    return deepcopy((w.project, w.editor._undo, w.editor._redo)), w.editor.revision, w.revision


@pytest.mark.parametrize('anchor', ROLES)
def test_four_roles_preview_atomic_apply_undo_and_active_role(roll_window, anchor):
    w = roll_window; select_roles(w, anchor)
    before = deepcopy(w.project); depth = len(w.editor._undo); revision = w.revision
    assert w.inspector.isEnabled() and w.selected_parameter_clip() is None
    assert w.roll_cut_spin.value() == 31 and not w.apply_roll_button.isEnabled()
    assert w.roll_cut_spin.isEnabled() and w.roll_group.isEnabled()
    for panel in w._roll_other_panels: assert not panel.isEnabled()
    assert not w.gain.isEnabled() and not w.apply_timing_button.isEnabled()
    w.roll_cut_spin.setValue(33)
    assert w.project == before and len(w.editor._undo) == depth and w.revision == revision
    text = w.roll_range_note.text()
    for label in ('Left V', 'Left A', 'Right V', 'Right A', *ROLES): assert label in text
    assert '23 frames' in text and '44 frames' in text
    assert 'First <picture>' in text and w.roll_range_note.textFormat() == Qt.TextFormat.PlainText
    assert w.apply_roll_button.isEnabled() and w.cancel_roll_button.isEnabled()
    w.apply_roll_button.click()
    assert w.revision == revision + 1 and len(w.editor._undo) == depth + 1
    assert w.selection == anchor and tuple(w.selected_clip_ids) == ROLES
    assert [(w.editor._clip(cid).start, w.editor._clip(cid).end) for cid in ROLES] == [(10,33),(10,33),(33,77),(33,77)]
    assert w.editor._clip('other') == before.clips[-1]
    assert w.project.av_links == before.av_links
    after = deepcopy(w.project); w.apply_roll(); assert w.project == after and w.revision == revision + 1
    w.undo(); assert w.project == before and w.selection == anchor
    w.redo(); assert w.project == after and w.selection == anchor
    assert not w.test_warnings


@pytest.mark.parametrize('cancel', ['button', 'zero'])
def test_cancel_or_zero_preserves_objects_revision_and_redo(roll_window, cancel):
    w = roll_window
    w.editor.roll_linked_av(ROLES, 33); w.undo()
    before = state(w); identities = tuple(map(id, w.project.clips))
    w.roll_cut_spin.setValue(34)
    if cancel == 'button': w.cancel_roll_button.click()
    else: w.roll_cut_spin.setValue(31)
    w.apply_roll()
    assert state(w) == before and tuple(map(id, w.project.clips)) == identities
    assert w.roll_cut_spin.value() == 31 and not w.apply_roll_button.isEnabled()


@pytest.mark.parametrize('change', ['raw_project', 'editor_revision', 'undo_aba', 'role', 'selection', 'selection_aba', 'project_identity', 'new_editor'])
def test_staged_capture_rejects_stale_apply_without_renewal(roll_window, change):
    w = roll_window; w.roll_cut_spin.setValue(33)
    capture = w._linked_roll_capture
    if change == 'raw_project': w.project.name = 'Changed without notification'
    elif change == 'editor_revision': w.editor.update_clip('other', gain_db=-4)
    elif change == 'undo_aba':
        w.editor.update_clip('other', gain_db=-4); w.editor.undo()
    elif change == 'role': w.set_selected_clip_ids(ROLES, anchor='ra')
    elif change == 'selection': w.set_selected_clip_ids(('lv',), anchor='lv')
    elif change == 'selection_aba':
        w.set_selected_clip_ids(('lv',), anchor='lv'); w.set_selected_clip_ids(ROLES, anchor='lv')
    elif change == 'project_identity': w.editor.project = deepcopy(w.project)
    else: w.editor = Editor(deepcopy(w.project))
    before = state(w)
    w.apply_roll()
    assert state(w) == before and w._linked_roll_capture is capture and capture.stale
    assert w.roll_cut_spin.value() == 33 and not w.apply_roll_button.isEnabled()
    assert w.roll_range_note.text() == ROLL_STALE
    assert w.roll_range_note.accessibleDescription() == ROLL_STALE
    assert w.roll_range_note.toolTip() == ROLL_STALE
    w.apply_roll(); assert state(w) == before


def test_timer_marks_unnotified_stale_draft_and_cancel_reloads(roll_window):
    w = roll_window; w.roll_cut_spin.setValue(33)
    w.project.name = 'Raw edit'; before = state(w)
    w.check_linked_roll_current()
    assert not w._linked_roll_timer.isActive() and w._linked_roll_capture.stale
    assert w.roll_range_note.text() == ROLL_STALE and not w.roll_cut_spin.isEnabled()
    w.cancel_roll_button.click()
    assert state(w) == before and not w._linked_roll_capture.stale
    assert w.roll_cut_spin.value() == 31 and w.roll_cut_spin.isEnabled()


@pytest.mark.parametrize('ids', [('lv', 'la'), ('lv', 'la', 'rv', 'ra', 'other'), ('lv', 'la', 'other')])
def test_incomplete_or_extra_selection_never_reduces_to_eligible_subset(roll_window, ids):
    w = roll_window; select_roles(w, ids=ids)
    before = state(w); w.roll_cut_spin.setValue(33); w.apply_roll()
    assert state(w) == before and not w.apply_roll_button.isEnabled()
    if len(ids) > 2: assert w.roll_range_note.text() == ROLL_SELECTION


@pytest.mark.parametrize('mode', ['gap', 'different_video_track', 'different_audio_track', 'missing_link'])
def test_nonqualifying_four_selection_does_not_enable_inspector(roll_window, mode):
    w = roll_window
    if mode == 'gap':
        for cid in ('rv','ra'): w.editor._clip(cid).start += 1
    elif mode == 'different_video_track': w.editor._clip('rv').track = 1
    elif mode == 'different_audio_track': w.editor._clip('ra').track = 3
    else: w.project.av_links.pop()
    w.project.validate(); select_roles(w)
    before = state(w); w.apply_roll()
    assert not w.inspector.isEnabled() and not w.apply_roll_button.isEnabled() and state(w) == before


@pytest.mark.parametrize('guard', ['locked_video', 'locked_audio', 'job', 'disabled_window', 'disabled_central', 'closing', 'not_clip'])
def test_joint_apply_global_guards_prevent_mutation(roll_window, guard):
    w = roll_window; w.roll_cut_spin.setValue(33)
    if guard == 'locked_video': w.editor.set_track_edit_lock(0, True)
    elif guard == 'locked_audio': w.editor.set_track_edit_lock(2, True)
    elif guard == 'job': w.jobs.add('test-job')
    elif guard == 'disabled_window': w.setEnabled(False)
    elif guard == 'disabled_central': w.centralWidget().setEnabled(False)
    elif guard == 'closing': w.closing = True
    else: w.active_selection_type = 'effect'
    before = state(w); w.apply_roll(); assert state(w) == before
    if guard == 'job': w.jobs.remove('test-job')
    w.setEnabled(True); w.centralWidget().setEnabled(True); w.closing = False


@pytest.mark.parametrize('cut', [0,10,77,100])
def test_invalid_candidate_rejects_without_history_or_half_edit(roll_window, cut):
    w = roll_window; before = state(w)
    w.roll_cut_spin.setValue(cut)
    assert not w.apply_roll_button.isEnabled()
    w.apply_roll()
    assert state(w) == before and w.test_warnings


def test_preview_never_reads_or_hashes_source_and_cancel_never_renders(roll_window, monkeypatch):
    w = roll_window
    def forbidden(*args, **kwargs): raise AssertionError('Source I/O or rendering from roll inspector')
    import lumen.media, lumen.file_identity, lumen.render
    monkeypatch.setattr(lumen.media, 'probe', forbidden)
    monkeypatch.setattr(lumen.render.RenderEngine, 'render', forbidden)
    from pathlib import Path
    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    before = state(w)
    for cut in (33,30,31,34): w.roll_cut_spin.setValue(cut)
    w.cancel_roll_button.click()
    assert state(w) == before and not w.jobs


@pytest.mark.parametrize('locale', ['en','zh-CN'])
@pytest.mark.parametrize('mode', ['simple','professional'])
def test_localized_narrow_four_role_preview_and_mode_preserves_stage(roll_window, app, locale, mode):
    w = roll_window; w.set_workspace_mode(mode, persist=False); set_locale(locale)
    w.resize(1000,700); w.show(); app.processEvents()
    w.roll_cut_spin.setValue(33); before = state(w)
    w.main_split.setSizes([180,450,300]); w.inspector_tabs.setCurrentIndex(0)
    app.processEvents()
    assert w.apply_roll_button.text() == str(tr('Apply joint roll edit'))
    assert w.cancel_roll_button.text() == str(tr('Cancel roll draft'))
    assert w.roll_anchor_note.text() == str(tr(ROLL_SCOPE))
    assert w.roll_cut_spin.toolTip() == str(tr(ROLL_TOOLTIP))
    for source in ('Left V','Left A','Right V','Right A'): assert str(tr(source)) in w.roll_range_note.text()
    assert w.roll_range_note.width() <= w.roll_group.width()
    assert w.apply_roll_button.width() <= w.roll_group.width()
    for other_mode in ('simple','professional'):
        w.set_workspace_mode(other_mode, persist=False)
        assert w.roll_cut_spin.value() == 33 and w.apply_roll_button.isEnabled()
    assert state(w) == before
    set_locale('en')


def test_restoring_single_and_single_pair_reenables_only_existing_role_policy(roll_window):
    w = roll_window
    w.select_clip('other')
    assert w.selected_parameter_clip().id == 'other' and w.inspector.isEnabled()
    assert w.gain.isEnabled() and w.apply_timing_button.isEnabled()
    assert w.apply_roll_button.text() == 'Apply roll edit' and not w.cancel_roll_button.isVisible()
    w.select_clip('ra')
    assert w.selected_parameter_clip().id == 'ra' and w.gain.isEnabled()
    assert not w.apply_timing_button.isEnabled() and not w.apply_roll_button.isEnabled()
    select_roles(w, 'ra'); assert w.selected_parameter_clip() is None and not w.gain.isEnabled()


@pytest.mark.parametrize('guard', ['modal', 'popup', 'ime'])
def test_modal_popup_and_ime_block_staged_joint_apply(roll_window, app, guard):
    from PySide6.QtGui import QInputMethodEvent
    from PySide6.QtWidgets import QApplication, QDialog, QMenu
    from test_shortcuts_dispatch import activate
    w = roll_window; activate(app,w,w.timeline); w.roll_cut_spin.setValue(33)
    before = state(w); popup = dialog = None
    try:
        if guard == 'modal':
            dialog = QDialog(w); dialog.setModal(True); dialog.show(); app.processEvents()
            assert QApplication.activeModalWidget() is dialog
        elif guard == 'popup':
            popup = QMenu(w); popup.addAction('Popup item')
            popup.popup(w.timeline.mapToGlobal(w.timeline.rect().topLeft())); app.processEvents()
            assert QApplication.activePopupWidget() is popup
        else:
            QApplication.sendEvent(w.timeline,QInputMethodEvent('拼音',[]))
            assert w.shortcuts._composing is w.timeline
        w.refresh_roll_range()
        assert not w.apply_roll_button.isEnabled()
        w.apply_roll()
        assert state(w) == before and w.roll_cut_spin.value() == 33
    finally:
        if popup is not None: popup.close(); popup.deleteLater()
        if dialog is not None: dialog.close(); dialog.deleteLater()
        if guard == 'ime': QApplication.sendEvent(w.timeline,QInputMethodEvent('',[]))
        activate(app,w,w.timeline)
    w.refresh_roll_range()
    assert w.apply_roll_button.isEnabled() and state(w) == before


@pytest.mark.parametrize('locked_track', [None, 0, 2])
def test_track_lock_refresh_keeps_roll_shell_without_enabling_other_edits(roll_window, locked_track):
    w = roll_window; w.roll_cut_spin.setValue(33)
    if locked_track is not None: w.editor.set_track_edit_lock(locked_track, True)
    before = state(w)
    w.refresh_track_lock_controls()
    assert w.inspector.isEnabled() and w.selected_parameter_clip() is None
    assert all(not panel.isEnabled() for panel in w._roll_other_panels)
    assert not w.gain.isEnabled() and not w.apply_timing_button.isEnabled()
    if locked_track is None:
        assert w.roll_cut_spin.isEnabled() and w.apply_roll_button.isEnabled()
        w.cancel_roll_button.click(); assert w.roll_cut_spin.value() == 31
    else:
        assert not w.roll_cut_spin.isEnabled() and not w.apply_roll_button.isEnabled()
        w.apply_roll()
    assert state(w) == before

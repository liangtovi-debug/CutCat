"""Nonmodal audio-cut staging, captured intent and one atomic GUI edit.

Identity-only sources are never decoded. These tests exercise real widgets and
model application, not audio/video render equivalence or native packaging.
"""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QMenu

from lumen.i18n import set_locale, tr
from lumen.model import AV_LINK_AFFINE_ALIGNMENT, AV_LINK_ALIGNMENT, Editor, Project
from lumen.shortcuts import COMMANDS
from test_linked_av_roll_ui import ROLES, app, window, roll_window, select_roles, state
from test_shortcuts_dispatch import activate


def raw(clip):
    return json.dumps(asdict(clip), default=str, sort_keys=True)


def open_dialog(window, app):
    activate(app, window, window.timeline)
    dialog = window.open_linked_audio_roll()
    assert dialog is not None and dialog.isVisible() and not dialog.isModal()
    return dialog


def stage(dialog, cut):
    dialog.cut_edit.setText(str(cut))
    assert dialog.plan is not None and dialog.apply_button.isEnabled(), dialog.status.text()
    return dialog.plan


@pytest.mark.parametrize('anchor', ROLES)
@pytest.mark.parametrize('entry', ['direct', 'menu'])
def test_separate_entry_is_nonmodal_and_stages_one_audio_only_edit(roll_window, app, anchor, entry):
    w = roll_window; select_roles(w, anchor); activate(app, w, w.timeline)
    before = deepcopy(w.project); depth, revision = len(w.editor._undo), w.revision
    identities = {clip.id: clip for clip in w.project.clips}
    pictures = {key: raw(identities[key]) for key in ('lv', 'rv')}
    assert w.can_roll_linked_audio()
    assert COMMANDS['edit.audio_roll'].label == 'Roll linked audio cut…'
    assert w.shortcuts.bindings['edit.audio_roll'] == ()
    if entry == 'menu':
        w.shortcuts.update_enabled(); assert w.shortcuts.actions['edit.audio_roll'].isEnabled()
        w.shortcuts.actions['edit.audio_roll'].trigger(); d = w.linked_audio_roll_dialog
    else: d = w.open_linked_audio_roll()
    assert d is not None and d.isVisible() and not d.isModal()
    assert isinstance(d.cut_edit, QLineEdit) and d.cut_edit.maxLength() == 32
    assert d.cut_edit.text() == '31' and not d.apply_button.isEnabled()
    assert w.open_linked_audio_roll() is d
    plan = stage(d, 33)
    assert plan.cut == 33 and not plan.reset and not d.reset_requested
    assert w.project == before and w.revision == revision and len(w.editor._undo) == depth
    assert not w.jobs
    for key in ROLES: assert key in d.impact.text()
    for label in (d.summary, d.impact, d.status, d.scope, d.limits):
        assert label.textFormat() == Qt.TextFormat.PlainText and label.wordWrap()
    d.apply_button.click(); d.apply()
    assert d._closed_flag and w.linked_audio_roll_dialog is None
    assert w.revision == revision+1 and len(w.editor._undo) == depth+1
    assert w.selection == anchor and tuple(w.selected_clip_ids) == ROLES
    assert [(w.editor._clip(key).start, w.editor._clip(key).end) for key in ROLES] == [
        (10,31), (10,33), (31,77), (33,77)]
    for key in ('lv', 'rv'):
        assert w.editor._clip(key) is identities[key] and raw(w.editor._clip(key)) == pictures[key]
    assert w.editor._clip('other') is identities['other'] and w.project.clips[-1] == before.clips[-1]
    assert all(link.alignment == AV_LINK_AFFINE_ALIGNMENT for link in w.project.av_links)
    after = deepcopy(w.project); w.undo(); assert w.project == before
    w.redo(); assert w.project == after and w.selection == anchor
    assert not w.test_warnings


@pytest.mark.parametrize('dismiss', ['cancel', 'escape', 'close'])
def test_cancel_escape_and_close_preserve_objects_revision_redo_and_reopen_fresh(roll_window, app, dismiss):
    w = roll_window
    w.editor.roll_linked_audio(ROLES, 33); w.undo()
    before, identities = state(w), tuple(map(id, w.project.clips))
    d = open_dialog(w, app); old_plan = stage(d, 34)
    if dismiss == 'cancel': d.cancel_button.click()
    elif dismiss == 'escape': QTest.keyClick(d, Qt.Key.Key_Escape)
    else: d.close()
    assert d._closed_flag and state(w) == before and tuple(map(id, w.project.clips)) == identities
    assert w.editor.can_redo and not w.jobs and w.linked_audio_roll_dialog is None
    d.apply(); assert state(w) == before
    new = open_dialog(w, app)
    assert new is not d and new.cut_edit.text() == '31' and not new.apply_button.isEnabled()
    assert new.plan is not old_plan


@pytest.mark.parametrize('change', ['raw-project', 'editor-revision', 'undo-aba', 'role',
    'role-aba', 'selection', 'selection-aba', 'project-identity', 'reload', 'new-editor',
    'video-lock', 'audio-lock', 'job', 'job-aba', 'playing-after'])
def test_context_expiry_is_sticky_and_apply_never_recaptures(roll_window, app, monkeypatch, change):
    w = roll_window; d = open_dialog(w, app); plan = stage(d, 33); context = d.context
    if change == 'raw-project': w.project.name = 'Changed without notification'
    elif change == 'editor-revision': w.editor.update_clip('other', gain_db=-4)
    elif change == 'undo-aba': w.editor.update_clip('other', gain_db=-4); w.editor.undo()
    elif change == 'role': w.set_selected_clip_ids(ROLES, anchor='ra')
    elif change == 'role-aba': w.set_selected_clip_ids(ROLES, anchor='ra'); w.set_selected_clip_ids(ROLES, anchor='lv')
    elif change == 'selection': w.set_selected_clip_ids(('lv',), anchor='lv')
    elif change == 'selection-aba': w.set_selected_clip_ids(('lv',), anchor='lv'); w.set_selected_clip_ids(ROLES, anchor='lv')
    elif change == 'project-identity': w.editor.project = deepcopy(w.project)
    elif change == 'reload': w.editor.project = Project.from_dict(w.project.to_dict())
    elif change == 'new-editor': w.editor = Editor(deepcopy(w.project))
    elif change.endswith('lock'): w.editor.set_track_edit_lock(0 if change == 'video-lock' else 2, True)
    elif change == 'job-aba': w._job_generation += 1  # Job began and finished between checks.
    elif change == 'playing-after':
        monkeypatch.setattr(w.player, 'playbackState', lambda: QMediaPlayer.PlaybackState.PlayingState)
        w.player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
    else: w.jobs.add('foreign-job')
    try:
        before = state(w); d.check_current()
        assert d._stale or d._closed_flag
        d.apply(); d.stage_align(); d.cut_edit.setText('34'); d._draft_changed()
        assert d.plan in (None, plan) and d.context is context and state(w) == before
        assert d._closed_flag or not d.apply_button.isEnabled()
        if change == 'job':
            w.jobs.remove('foreign-job'); d.check_current(); d.apply()
            assert d._stale and d.plan in (None, plan) and d.context is context and state(w) == before
    finally:
        w.jobs.discard('foreign-job')


@pytest.mark.parametrize('guard', ['video-lock', 'audio-lock', 'job', 'window-disabled',
    'central-disabled', 'closing', 'not-clip', 'playing', 'wrong-active', 'partial', 'extra'])
def test_entry_qualification_rejects_global_and_exact_selection_failures(roll_window, app, monkeypatch, guard):
    w = roll_window; activate(app, w, w.timeline)
    if guard.endswith('lock'): w.editor.set_track_edit_lock(0 if guard == 'video-lock' else 2, True)
    elif guard == 'job': w.jobs.add('foreign-job')
    elif guard == 'window-disabled': w.setEnabled(False)
    elif guard == 'central-disabled': w.centralWidget().setEnabled(False)
    elif guard == 'closing': w.closing = True
    elif guard == 'not-clip': w.active_selection_type = 'effect'
    elif guard == 'playing': monkeypatch.setattr(w.player, 'playbackState', lambda: QMediaPlayer.PlaybackState.PlayingState)
    elif guard == 'wrong-active': w.selection = 'other'
    elif guard == 'partial': w.set_selected_clip_ids(('lv', 'la'), anchor='lv')
    else: w.set_selected_clip_ids((*ROLES, 'other'), anchor='lv')
    try:
        before = state(w)
        assert not w.can_roll_linked_audio()
        assert w.open_linked_audio_roll() is None and w.linked_audio_roll_dialog is None
        assert state(w) == before
    finally:
        w.jobs.discard('foreign-job'); w.closing = False
        w.setEnabled(True); w.centralWidget().setEnabled(True)


@pytest.mark.parametrize('guard', ['modal', 'popup', 'ime'])
def test_modal_popup_and_ime_temporarily_block_apply_without_replanning(roll_window, app, guard):
    w = roll_window; d = open_dialog(w, app); plan = stage(d, 33)
    before = state(w); popup = modal = None
    try:
        if guard == 'modal':
            modal = QDialog(d); modal.setModal(True); modal.show(); app.processEvents()
            assert QApplication.activeModalWidget() is modal
        elif guard == 'popup':
            popup = QMenu(d); popup.addAction('Popup item')
            popup.popup(d.cut_edit.mapToGlobal(d.cut_edit.rect().bottomLeft())); app.processEvents()
            assert QApplication.activePopupWidget() is popup
        else:
            activate(app, d, d.cut_edit)
            QApplication.sendEvent(d.cut_edit, QInputMethodEvent('拼音', [])); app.processEvents()
        d.check_current(); assert not d.apply_button.isEnabled()
        assert not d.align_button.isEnabled()
        d.stage_align(); assert not d.reset_requested and d.plan is plan
        d.apply(); assert state(w) == before and d.plan is plan and not d._stale
    finally:
        if popup is not None: popup.close(); popup.deleteLater()
        if modal is not None: modal.close(); modal.deleteLater()
        if guard == 'ime': QApplication.sendEvent(d.cut_edit, QInputMethodEvent('', []))
        activate(app, d, d.cut_edit)
    d.check_current()
    assert d.apply_button.isEnabled() and d.plan is plan and state(w) == before


def test_integer_input_invalid_edges_and_limits_never_apply_or_clamp(roll_window, app):
    w = roll_window; d = open_dialog(w, app); before = state(w)
    for text in ('', 'not-a-frame', '31.5', '1/2', '-1', '1e2', '10', '12', '75', '77', str(2**53), '9'*32):
        d.cut_edit.setText(text)
        assert d.cut_edit.text() == text and not d.apply_button.isEnabled()
        d.apply(); assert state(w) == before and not w.jobs
    d.cut_edit.setText('9'*100)
    assert len(d.cut_edit.text()) == 32 and not d.apply_button.isEnabled()
    for cut in (13, 74):
        plan = stage(d, cut)
        assert plan.cut == cut and not plan.reset and state(w) == before
    d.cut_edit.setText('31'); assert not d.apply_button.isEnabled()
    d.apply(); assert state(w) == before


def test_line_edit_accepts_valid_frame_beyond_qt_signed_int32_without_clamping(roll_window, app):
    w = roll_window; offset = 2**31
    # Avoid forcing legacy timeline transport controls to display this span;
    # qualification and the new dialog use the model's 53-bit integer contract.
    for clip in w.project.clips: clip.start += offset
    w.project.validate(); before = state(w)
    d = open_dialog(w, app)
    assert d.cut_edit.text() == str(offset+31)
    plan = stage(d, offset+33)
    assert plan.cut == offset+33 and d.cut_edit.text() == str(offset+33)
    d.cancel_button.click(); assert state(w) == before


def test_align_is_explicit_staging_and_metadata_only_downgrade_is_undoable(roll_window, app):
    w = roll_window; original = deepcopy(w.project)
    w.editor.roll_linked_audio(ROLES, 28); w.refresh(); select_roles(w)
    d = open_dialog(w, app); before = state(w)
    d.stage_align()
    assert d.reset_requested and d.plan.reset and d.plan.cut == 31 and d.cut_edit.text() == '31'
    assert d.apply_button.isEnabled() and state(w) == before
    d.apply(); assert w.project == original
    w.undo(); assert w.project == before[0][0]
    w.editor.roll_linked_audio(ROLES, 31); w.refresh(); select_roles(w)
    aligned_v2 = deepcopy(w.project)
    assert aligned_v2.clips == original.clips
    assert all(link.alignment == AV_LINK_AFFINE_ALIGNMENT for link in aligned_v2.av_links)
    d = open_dialog(w, app); before = state(w); ids = tuple(map(id, w.project.clips))
    assert not d.apply_button.isEnabled() and not d.reset_requested
    d.stage_align(); assert d.plan.reset and d.apply_button.isEnabled() and state(w) == before
    d.apply(); assert w.project == original and tuple(map(id, w.project.clips)) == ids
    w.undo(); assert w.project == aligned_v2


def test_manual_cut_after_staged_align_clears_reset_intent(roll_window, app):
    w = roll_window; w.editor.roll_linked_audio(ROLES, 28); w.refresh(); select_roles(w)
    d = open_dialog(w, app); before = state(w)
    d.stage_align(); assert d.reset_requested and d.plan.reset
    plan = stage(d, 33)
    assert not d.reset_requested and not plan.reset and plan.cut == 33
    d.apply(); assert w.editor._clip('la').end == w.editor._clip('ra').start == 33
    assert all(link.alignment == AV_LINK_AFFINE_ALIGNMENT for link in w.project.av_links)
    assert w.editor.revision == before[1]+1


def test_existing_joint_roll_keeps_separate_four_role_semantics(roll_window, app):
    w = roll_window; original = deepcopy(w.project)
    d = open_dialog(w, app); stage(d, 33); d.cancel_button.click()
    activate(app, w, w.timeline)
    assert w.roll_cut_spin.value() == 31 and w.roll_cut_spin.isEnabled()
    w.roll_cut_spin.setValue(34); w.apply_roll_button.click()
    assert [(w.editor._clip(key).start, w.editor._clip(key).end) for key in ROLES] == [
        (10,34), (10,34), (34,77), (34,77)]
    assert all(link.alignment == AV_LINK_ALIGNMENT for link in w.project.av_links)
    w.undo(); assert w.project == original
    d = open_dialog(w, app); stage(d, 33); d.apply()
    assert w.can_roll_linked_audio() and not w.apply_roll_button.isEnabled()


def test_preview_and_cancel_never_observe_sources_render_or_start_worker(roll_window, app, monkeypatch):
    w = roll_window; before = state(w)
    sources = [Path(item.path) for item in w.project.media]
    source_bytes = [p.read_bytes() for p in sources]
    d = open_dialog(w, app)
    def forbidden(*args, **kwargs):
        raise AssertionError('Audio-cut staging must do no source I/O, media work or worker launch')
    import lumen.media, lumen.file_identity, lumen.render, lumen.clip_copy_paste
    with monkeypatch.context() as m:
        m.setattr(lumen.media, 'probe', forbidden)
        m.setattr(lumen.render.RenderEngine, 'render', forbidden)
        m.setattr(lumen.clip_copy_paste, '_dependencies', forbidden)
        m.setattr(w, 'start_job', forbidden)
        m.setattr(Path, 'read_bytes', forbidden)
        for cut in (33, 28, 31, 34): d.cut_edit.setText(str(cut))
        d.stage_align(); d.cancel_button.click()
    assert state(w) == before and not w.jobs
    assert [p.read_bytes() for p in sources] == source_bytes


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_localized_narrow_plaintext_labels_accessibility_and_fixed_picture_classification(roll_window, app, locale, mode):
    w = roll_window; w.set_workspace_mode(mode, persist=False); set_locale(locale)
    d = open_dialog(w, app); before = state(w)
    d.fit_available_screen(QRect(0, 0, 390, 720)); app.processEvents()
    assert d.width() <= 358 and d.height() <= 656
    assert d.apply_button.text() == tr('Apply audio-only roll')
    assert d.align_button.text() == tr('Align audio cut to picture')
    for button in (d.align_button, d.apply_button, d.cancel_button):
        assert button.accessibleName() == button.text() and button.toolTip() == button.text()
        assert button.width() <= d.width()
    for cut, marker in ((28, 'J'), (33, 'L'), (31, '')):
        d.cut_edit.setText(str(cut)); app.processEvents()
        assert '31' in d.summary.text() and str(cut) in d.summary.text()
        if marker: assert marker in d.summary.text()
        for label in (d.summary, d.impact, d.status, d.scope, d.limits):
            assert label.textFormat() == Qt.TextFormat.PlainText and label.wordWrap()
        assert state(w) == before
    assert not d.apply_button.isEnabled()
    # Locale-only refresh is not a new capture or a history change.
    stage(d, 33); plan = d.plan
    set_locale('zh-CN' if locale == 'en' else 'en'); d.refresh_text()
    assert d.plan is plan and d.cut_edit.text() == '33' and state(w) == before
    set_locale('en')


def test_j_l_classification_is_relative_to_fixed_picture_not_previous_audio_cut(roll_window, app):
    w = roll_window; w.editor.roll_linked_audio(ROLES, 28); w.refresh(); select_roles(w)
    d = open_dialog(w, app); before = state(w)
    stage(d, 30)  # Positive displacement from current audio 28, still before V31.
    assert tr('J cut · incoming audio starts before picture') in d.summary.text()
    assert '28' in d.summary.text() and '31' in d.summary.text() and '30' in d.summary.text()
    stage(d, 33)
    assert tr('L cut · outgoing audio continues after picture') in d.summary.text()
    d.stage_align()
    assert tr('Aligned audio and picture cuts') in d.summary.text()
    assert d.plan.reset and state(w) == before and not w.jobs

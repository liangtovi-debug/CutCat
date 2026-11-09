"""Explicit two-ID UI, fresh-worker lifecycle and exact atomic reattachment."""
from copy import deepcopy
from pathlib import Path
import threading
import time

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit, QMessageBox

from lumen.i18n import set_locale, tr_error
from lumen.model import AudioMixAnchor, Clip, Editor, Media, Project
from lumen.shortcuts import COMMANDS, default_bindings
import lumen.ui_source_audio_reattach as ui
from test_master_gain_ui import app, window
from test_clip_copy_paste_ui import settle, state
from test_shortcuts_dispatch import activate


def select_pair(w, ids=('video-id', 'audio-id')):
    w.active_selection_type = 'clip'; w.set_selected_clip_ids(ids)
    w.refresh_inspector(); w.update_playback_controls()


@pytest.fixture
def pair_window(window, app, tmp_path):
    w = window
    path = tmp_path / 'av.mov'; path.write_bytes(b'original AV identity-only source' * 100)
    w.editor = Editor(Project(media=[Media('media-id', str(path), 'Video', 4, 64, 48, True, True)], clips=[
        Clip('video-id', 'media-id', 0, 5, 12, 30, source_audio_enabled=False),
        Clip('audio-id', 'media-id', 2, 5, 12, 30, gain_db=-6, fade_in=2, fade_out=3,
             audio_mix_anchor=AudioMixAnchor(0, 5, 'video-id')),
        Clip('other-id', 'media-id', 1, 5, 12, 30)]))
    w.refresh(); select_pair(w); activate(app, w, w.timeline)
    return w


def dialog(w, app):
    activate(app, w, w.timeline)
    d = w.open_source_audio_reattach()
    assert d is not None and d.isVisible()
    app.processEvents()
    return d


def preview(w, app, d):
    assert d.preview_button.isEnabled(), d.impact.text()
    d.preview(); settle(w, app)
    assert d.preview_receipt is not None and d.apply_button.isEnabled(), d.impact.text()


@pytest.mark.parametrize('mode', ['simple', 'professional'])
@pytest.mark.parametrize('muted', [False, True])
def test_explicit_pair_preview_delete_only_one_atomic_undo(pair_window, app, mode, muted):
    w = pair_window; w.set_workspace_mode(mode, persist=False)
    w.editor._clip('audio-id').muted = muted
    w.editor._clip('video-id').grade.exposure = 1
    before = state(w); d = dialog(w, app)
    assert 'video-id' in d.source.text() and 'audio-id' in d.source.text()
    assert 'KEEP V0' in d.source.text() and 'DELETE A0' in d.source.text()
    assert not d.apply_button.isEnabled() and w.open_source_audio_reattach() is d
    assert 'not linked' in d.limits.text() and 'delete only' in d.scope.text()
    preview(w, app, d); assert state(w) == before
    d.apply(); d.apply(); settle(w, app)
    assert d._closed_flag and w.source_audio_reattach_dialog is None
    assert [c.id for c in w.project.clips] == ['video-id', 'other-id']
    video = w.editor._clip('video-id')
    assert video.source_audio_enabled and video.muted is muted and video.gain_db == -6
    assert video.grade.exposure == 1 and w.project.clips[1] == before[0].clips[2]
    assert w.selected_clip_ids == ('video-id',) and len(w.editor._undo) == len(before[1]) + 1
    assert 'Source audio enabled' in w.source_audio_status.text()
    assert 'Independent source audio' not in w.source_audio_status.text()
    after = deepcopy(w.project)
    w.undo(); assert w.project == before[0]
    w.redo(); assert w.project == after
    assert w.dirty and not w.test_warnings and w.progress_label.text() == 'Ready · local files only'


@pytest.mark.parametrize('ids', [(), ('video-id',), ('audio-id',), ('video-id', 'audio-id', 'other-id'), ('video-id', 'other-id')])
def test_wrong_selection_disabled_with_reason_no_pair_guess(pair_window, app, ids):
    w = pair_window; select_pair(w, ids); before = state(w)
    assert not w.can_reattach_source_audio() and w.open_source_audio_reattach() is None
    action = w.shortcuts.actions['edit.reattach_audio']
    assert not action.isEnabled() and 'Select exactly two' in action.toolTip()
    assert state(w) == before


@pytest.mark.parametrize('failure', ['enabled_v', 'disabled_a', 'v_lock', 'a_lock', 'group', 'different_source', 'timing', 'ambiguous_anchor'])
def test_unsupported_selection_or_preview_disabled_reason(pair_window, app, failure):
    w = pair_window
    if failure == 'enabled_v': w.editor._clip('video-id').source_audio_enabled = True
    elif failure == 'disabled_a': w.editor._clip('audio-id').source_audio_enabled = False
    elif failure == 'v_lock': w.editor.set_track_edit_lock(0, True)
    elif failure == 'a_lock': w.editor.set_track_edit_lock(2, True)
    elif failure == 'group': w.editor.group_clips(['video-id', 'audio-id'])
    elif failure == 'different_source':
        m = deepcopy(w.project.media[0]); m.id = 'different'; w.project.media.append(m)
        w.editor._clip('audio-id').media_id = 'different'
    elif failure == 'timing': w.editor.update_clip('audio-id', duration=29)
    else: w.editor._clip('other-id').audio_mix_anchor = AudioMixAnchor(0, 5, 'video-id')
    w.refresh(); select_pair(w); before = state(w)
    if failure in ('enabled_v', 'disabled_a', 'v_lock', 'a_lock'):
        assert not w.can_reattach_source_audio() and w.open_source_audio_reattach() is None
        assert w.shortcuts.actions['edit.reattach_audio'].toolTip()
    else:
        d = dialog(w, app)
        assert d.plan is None and not d.preview_button.isEnabled() and not d.apply_button.isEnabled()
        assert 'Cannot reattach' in d.impact.text()
    assert state(w) == before


@pytest.mark.parametrize('dismiss', ['cancel', 'close', 'escape'])
@pytest.mark.parametrize('stage', ['draft', 'previewed'])
def test_dismiss_and_reopen_has_no_receipt(pair_window, app, dismiss, stage):
    w = pair_window; before = state(w); d = dialog(w, app)
    if stage == 'previewed': preview(w, app, d)
    if dismiss == 'cancel': d.cancel_button.click()
    elif dismiss == 'close': d.close()
    else: QTest.keyClick(d, Qt.Key.Key_Escape)
    app.processEvents()
    assert state(w) == before and d._closed_flag and not d.timer.isActive()
    fresh = dialog(w, app)
    assert fresh is not d and fresh.preview_receipt is None and not fresh.apply_button.isEnabled()


def held(monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = ui.verify_source_audio_reattach; owner = threading.get_ident()
    def work(*args, **kwargs):
        assert threading.get_ident() != owner
        started.set(); assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(ui, 'verify_source_audio_reattach', work)
    return started, release


def wait_started(app, started):
    end = time.monotonic() + 3
    while not started.is_set() and time.monotonic() < end:
        app.processEvents(); time.sleep(.005)
    assert started.is_set()


@pytest.mark.parametrize('phase', ['preview', 'apply'])
@pytest.mark.parametrize('race', ['cancel', 'close', 'selection', 'selection_back', 'edit_undo', 'replace', 'unversioned', 'lock', 'relink'])
def test_worker_stale_cancel_aba_never_commits(pair_window, app, monkeypatch, phase, race):
    w = pair_window; d = dialog(w, app)
    if phase == 'apply': preview(w, app, d)
    started, release = held(monkeypatch)
    getattr(d, phase)(); wait_started(app, started)
    try:
        if race == 'cancel': w.cancel_jobs()
        elif race == 'close': d.reject()
        elif race == 'selection': w.select_clip('other-id')
        elif race == 'selection_back': w.select_clip('other-id'); select_pair(w)
        elif race == 'edit_undo': w.editor.update_clip('audio-id', gain_db=-3); w.editor.undo(); w.refresh()
        elif race == 'replace': w.editor = Editor(deepcopy(w.project)); w.refresh()
        elif race == 'unversioned': w.editor._clip('audio-id').gain_db = -3
        elif race == 'lock': w.editor.set_track_edit_lock(2, True); w.changed()
        else: w.project.media[0].path += '.relinked'
        d.check_current(); expected = state(w)
    finally:
        release.set(); settle(w, app)
    assert state(w) == expected and len(w.project.clips) == 3
    assert not w.editor._clip('video-id').source_audio_enabled
    assert d.preview_receipt is None and not d.apply_button.isEnabled()
    assert 'Checking' not in w.progress_label.text() and not w.test_warnings
    if d._stale and not d._closed_flag:
        assert 'preview has expired' in d.impact.text() and 'Exact timing and source match' not in d.impact.text()


@pytest.mark.parametrize('phase', ['preview', 'apply'])
@pytest.mark.parametrize('failure', ['missing', 'changed', 'worker'])
def test_failed_worker_requires_fresh_preview(pair_window, app, monkeypatch, phase, failure):
    w = pair_window; d = dialog(w, app)
    if phase == 'apply': preview(w, app, d)
    path = Path(w.project.media[0].path)
    if failure == 'missing': path.unlink()
    elif failure == 'changed' and phase == 'apply': path.write_bytes(b'replaced after preview')
    else: monkeypatch.setattr(ui, 'verify_source_audio_reattach', lambda *_a, **_k: (_ for _ in ()).throw(ValueError('source check failed')))
    before = state(w); getattr(d, phase)(); settle(w, app)
    assert state(w) == before and d.preview_receipt is None and not d.apply_button.isEnabled()
    assert d.preview_button.isEnabled() and w.test_warnings
    assert w.progress_label.text() == 'Audio reattachment check failed · project unchanged'


@pytest.mark.parametrize('navigation', ['new', 'open_cancel', 'new_session'])
def test_navigation_discards_staged_pair(pair_window, app, monkeypatch, navigation):
    w = pair_window; d = dialog(w, app); preview(w, app, d)
    if navigation == 'new':
        monkeypatch.setattr(QInputDialog, 'getItem', lambda *_a, **_k: ('1280 × 720 · 30 fps', True))
        w.dirty = False; w.new_project(); assert not w.project.clips
    elif navigation == 'open_cancel':
        before = state(w); monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *_a, **_k: ('', ''))
        w.open_project(); assert state(w) == before
    else: w.editor = Editor(deepcopy(w.project)); w.refresh()
    app.processEvents()
    assert d._closed_flag and w.source_audio_reattach_dialog is None


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_live_localization_narrow_footer_accessibility(pair_window, app, locale, mode):
    w = pair_window; w.set_workspace_mode(mode, persist=False)
    d = dialog(w, app); preview(w, app, d)
    before = state(w); plan, receipt = d.plan, d.preview_receipt
    set_locale(locale); app.processEvents(); d.fit_available_screen(QRect(0, 0, 420, 600)); app.processEvents()
    assert d.width() <= 388 and d.height() <= 536 and d.scroll.horizontalScrollBar().maximum() == 0
    for button in (d.preview_button, d.apply_button, d.cancel_button):
        assert button.isVisible() and button.isEnabled() and button.accessibleName()
        assert button.width() >= button.fontMetrics().horizontalAdvance(button.text()) + 8
    assert d.plan is plan and d.preview_receipt is receipt and state(w) == before
    assert 'video-id' in d.source.text() and 'audio-id' in d.source.text()
    if locale == 'zh-CN':
        assert '重新附加' in d.windowTitle() and '不是联动' in d.limits.text()
        assert '重新附加预览后' in tr_error('Source changed after reattach preview; preview again')


def test_sparse_multiple_candidates_only_explicit_audio_deleted(pair_window, app):
    w = pair_window
    other_audio = deepcopy(w.editor._clip('audio-id')); other_audio.id = 'other-audio'; other_audio.track = 3
    other_audio.audio_mix_anchor = None; other_audio.gain_db = -12
    w.project.clips.append(other_audio); w.refresh(); select_pair(w, ('video-id', 'other-audio'))
    before_audio = deepcopy(w.editor._clip('audio-id')); d = dialog(w, app); preview(w, app, d)
    assert 'audio ID other-audio' in d.source.text()
    d.apply(); settle(w, app)
    assert w.editor._clip('audio-id') == before_audio
    assert w.editor._clip('video-id').gain_db == -12 and len(w.project.clips) == 3


def test_command_unbound_and_terminal_generation_guard(pair_window, app):
    from types import SimpleNamespace
    w = pair_window
    assert COMMANDS['edit.reattach_audio'].category == 'Edit'
    assert default_bindings('windows')['edit.reattach_audio'] == () == default_bindings('macos')['edit.reattach_audio']
    w.shortcuts.actions['edit.reattach_audio'].trigger()
    assert w.source_audio_reattach_dialog is not None
    w._job_generation += 1; w.progress_label.setText('Newer result'); w.progress_bar.setValue(73)
    ui._finish_job(w, SimpleNamespace(cancel=threading.Event()), w._job_generation - 1, {'completed': True, 'failed': False})
    assert w.progress_label.text() == 'Newer result' and w.progress_bar.value() == 73


def test_failed_apply_retry_is_explicit_and_clean(pair_window, app):
    w = pair_window; d = dialog(w, app); preview(w, app, d); before = state(w)
    source = Path(w.project.media[0].path); original = source.read_bytes()
    source.write_bytes(b'source replaced between preview and apply')
    d.apply(); settle(w, app)
    assert state(w) == before and not d.apply_button.isEnabled() and d.preview_receipt is None
    source.write_bytes(original); preview(w, app, d)
    assert 'Cannot reattach' not in d.impact.text()
    d.apply(); settle(w, app)
    assert d._closed_flag and len(w.project.clips) == 2 and len(w.editor._undo) == 1


@pytest.mark.parametrize('choice', [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])
def test_main_close_during_worker_obeys_cancel_boundary(pair_window, app, monkeypatch, choice):
    w = pair_window; d = dialog(w, app); started, release = held(monkeypatch)
    d.preview(); wait_started(app, started); before = deepcopy(w.project)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_a, **_k: choice); w.dirty = False
    try:
        w.close(); app.processEvents()
        if choice == QMessageBox.StandardButton.No:
            assert not w.closing and not d._closed_flag
        else: assert w.closing and d.preview_receipt is None
    finally: release.set(); settle(w, app)
    assert w.project == before and not w.editor.can_undo
    if choice == QMessageBox.StandardButton.No:
        assert d.preview_receipt is not None and d.apply_button.isEnabled()
    else:
        end = time.monotonic() + 2
        while w.isVisible() and time.monotonic() < end: app.processEvents(); time.sleep(.005)
        assert not w.isVisible() and d._closed_flag and w.source_audio_reattach_dialog is None


def test_custom_shortcut_text_modal_autorepeat_guards(pair_window, app):
    from PySide6.QtWidgets import QDialog
    from test_shortcuts_dispatch import CTRL, SHIFT, press, repeat
    w = pair_window
    w.shortcuts.set_bindings('edit.reattach_audio', ('Ctrl+Shift+J',))
    before = state(w)
    activate(app, w, w.timeline); repeat(app, w.timeline, Qt.Key.Key_J, CTRL | SHIFT)
    assert w.source_audio_reattach_dialog is None and state(w) == before
    edit = QLineEdit(w); edit.show(); activate(app, w, edit)
    press(app, edit, Qt.Key.Key_J, CTRL | SHIFT)
    assert w.source_audio_reattach_dialog is None and state(w) == before
    modal = QDialog(w); modal.setModal(True); modal.show(); activate(app, modal, modal)
    press(app, modal, Qt.Key.Key_J, CTRL | SHIFT)
    assert w.source_audio_reattach_dialog is None and state(w) == before
    modal.reject(); edit.close(); activate(app, w, w.timeline)
    press(app, w.timeline, Qt.Key.Key_J, CTRL | SHIFT)
    assert w.source_audio_reattach_dialog is not None and state(w) == before

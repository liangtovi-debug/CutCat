"""Staged Qt separation: real workers, interruption, identity and selection."""
from copy import deepcopy
from pathlib import Path
import threading
import time

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QLineEdit

from lumen.i18n import set_locale, tr_error
from lumen.model import Clip, Editor, Media, Project
from lumen.shortcuts import COMMANDS, default_bindings
import lumen.ui_source_audio_separation as ui
from test_master_gain_ui import app, window
from test_clip_copy_paste_ui import settle, state
from test_shortcuts_dispatch import activate


@pytest.fixture
def video_window(window, app, tmp_path):
    w = window
    source = tmp_path / 'video.mov'; source.write_bytes(b'original AV source' * 100)
    w.editor = Editor(Project(media=[Media('av', str(source), 'Video', 4, 64, 48, True, True)],
        clips=[Clip('v', 'av', 0, 5, 12, 30, gain_db=-6, fade_in=2, fade_out=3),
               Clip('other', 'av', 1, 5, 12, 30)]))
    w.refresh(); w.select_clip('v'); activate(app, w, w.timeline)
    return w


def dialog(w, app, track=2):
    activate(app, w, w.timeline)
    d = w.open_source_audio_separation()
    assert d is not None and d.isVisible()
    if track is not None: d.track.setCurrentIndex(track - 1)
    app.processEvents()
    return d


def preview(w, app, d):
    assert d.preview_button.isEnabled(), d.impact.text()
    d.preview(); settle(w, app)
    assert d.preview_receipt is not None and d.apply_button.isEnabled(), d.impact.text()


@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_explicit_track_staged_preview_single_commit_select_audio_one_undo(video_window, app, mode):
    w = video_window; w.set_workspace_mode(mode, persist=False)
    before = state(w); d = dialog(w, app, None)
    assert not d.preview_button.isEnabled() and not d.apply_button.isEnabled()
    assert w.open_source_audio_separation() is d
    d.track.setCurrentIndex(2); preview(w, app, d)
    assert state(w) == before and 'not linked' in d.limits.text()
    d.apply(); d.apply(); settle(w, app)
    assert d._closed_flag and w.source_audio_separation_dialog is None
    assert len(w.project.clips) == 3 and not w.editor._clip('v').source_audio_enabled
    assert w.selected_clip_ids == (w.project.clips[-1].id,) and w.project.clips[-1].track == 3
    assert len(w.editor._undo) == len(before[1]) + 1
    assert w.dirty and w.progress_label.text() == 'Ready · local files only'
    assert 'Independent source audio' in w.source_audio_status.text()
    after = deepcopy(w.project)
    w.undo(); assert w.project == before[0] and w.selected_clip_ids == ('v',)
    w.redo(); assert w.project == after and w.selected_clip_ids == (after.clips[-1].id,)
    w.select_clip('v'); w.refresh_inspector()
    assert 'stays silent even when unmuted' in w.source_audio_status.text()
    assert not w.separate_source_audio_button.isEnabled()
    assert not w.test_warnings


@pytest.mark.parametrize('dismiss', ['cancel', 'close', 'escape'])
@pytest.mark.parametrize('stage', ['draft', 'previewed'])
def test_dismissal_never_changes_project(video_window, app, dismiss, stage):
    w = video_window; before = state(w); d = dialog(w, app)
    if stage == 'previewed': preview(w, app, d)
    if dismiss == 'cancel': d.cancel_button.click()
    elif dismiss == 'close': d.close()
    else: QTest.keyClick(d, Qt.Key.Key_Escape)
    app.processEvents()
    assert state(w) == before and d._closed_flag and not d.timer.isActive()
    reopened = dialog(w, app, None)
    assert reopened is not d and reopened.preview_receipt is None


def held(monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = ui.verify_source_audio_separation; owner = threading.get_ident()
    def work(*args, **kwargs):
        assert threading.get_ident() != owner
        started.set(); assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(ui, 'verify_source_audio_separation', work)
    return started, release


def wait_started(app, started):
    deadline = time.monotonic() + 3
    while not started.is_set() and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.005)
    assert started.is_set()


@pytest.mark.parametrize('phase', ['preview', 'apply'])
@pytest.mark.parametrize('race', ['cancel', 'close', 'selection', 'selection_back', 'edit_undo', 'replace', 'unversioned', 'lock', 'target_back'])
def test_running_old_result_cannot_apply_or_revive(video_window, app, monkeypatch, phase, race):
    w = video_window; d = dialog(w, app)
    if phase == 'apply': preview(w, app, d)
    started, release = held(monkeypatch)
    before = deepcopy(w.project)
    getattr(d, phase)(); wait_started(app, started)
    try:
        if race == 'cancel': w.cancel_jobs()
        elif race == 'close': d.reject()
        elif race == 'selection': w.select_clip('other')
        elif race == 'selection_back': w.select_clip('other'); w.select_clip('v')
        elif race == 'edit_undo': w.editor.update_clip('v', gain_db=-3); w.editor.undo(); w.refresh()
        elif race == 'replace': w.editor = Editor(deepcopy(w.project)); w.refresh()
        elif race == 'unversioned': w.editor._clip('v').gain_db = -3
        elif race == 'lock': w.editor.set_track_edit_lock(2, True); w.changed()
        else: d.track.setCurrentIndex(2); d.track.setCurrentIndex(1)
        d.check_current(); expected = deepcopy(w.project)
    finally:
        release.set(); settle(w, app)
    assert w.project == expected and len(w.project.clips) == 2
    assert w.editor._clip('v').source_audio_enabled
    assert d.preview_receipt is None and not d.apply_button.isEnabled()
    assert 'Checking' not in w.progress_label.text()
    assert not w.test_warnings


@pytest.mark.parametrize('failure', ['source_lock', 'target_lock', 'collision', 'group'])
def test_locked_and_unsupported_preview_blocked_atomic(video_window, app, failure):
    w = video_window
    if failure == 'source_lock': w.editor.set_track_edit_lock(0, True)
    elif failure == 'target_lock': w.editor.set_track_edit_lock(2, True)
    elif failure == 'collision': w.project.clips.append(Clip('a', 'av', 2, 5, 0, 30))
    else: w.editor.group_clips(['v', 'other'])
    w.refresh(); before = state(w)
    if failure in ('source_lock', 'group'):
        assert not w.can_separate_source_audio()
        assert w.open_source_audio_separation() is None
    else:
        d = dialog(w, app)
        assert not d.preview_button.isEnabled() and not d.apply_button.isEnabled()
        assert 'Cannot separate' in d.impact.text()
    assert state(w) == before


@pytest.mark.parametrize('phase', ['preview', 'apply'])
@pytest.mark.parametrize('failure', ['missing', 'changed', 'worker'])
def test_fresh_source_failures_preserve_all_state_and_require_preview(video_window, app, monkeypatch, phase, failure):
    w = video_window; d = dialog(w, app)
    if phase == 'apply': preview(w, app, d)
    source = Path(w.project.media[0].path)
    if failure == 'missing': source.unlink()
    elif failure == 'changed' and phase == 'apply': source.write_bytes(b'changed after preview')
    else: monkeypatch.setattr(ui, 'verify_source_audio_separation', lambda *_a, **_k: (_ for _ in ()).throw(ValueError('source check failed')))
    before = state(w); getattr(d, phase)(); settle(w, app)
    assert state(w) == before and d.preview_receipt is None
    assert not d.apply_button.isEnabled() and d.preview_button.isEnabled()
    assert w.test_warnings and w.progress_label.text() == 'Audio separation check failed · project unchanged'


def test_new_project_closes_staged_dialog(video_window, app, monkeypatch):
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *_a, **_k: ('1280 × 720 · 30 fps', True))
    w = video_window; d = dialog(w, app); preview(w, app, d)
    w.dirty = False; w.new_project(); app.processEvents()
    assert d._closed_flag and w.source_audio_separation_dialog is None and not w.project.clips


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_live_localization_narrow_geometry_and_accessibility(video_window, app, locale, mode):
    w = video_window; w.set_workspace_mode(mode, persist=False)
    d = dialog(w, app); preview(w, app, d)
    before = state(w); plan = d.plan; receipt = d.preview_receipt
    set_locale(locale); app.processEvents(); d.fit_available_screen(QRect(0, 0, 420, 600)); app.processEvents()
    assert d.width() <= 388 and d.height() <= 536
    assert d.scroll.horizontalScrollBar().maximum() == 0
    for button in (d.preview_button, d.apply_button, d.cancel_button):
        assert button.isVisible() and button.isEnabled() and button.accessibleName()
        assert button.width() >= button.fontMetrics().horizontalAdvance(button.text()) + 8
    assert d.track.accessibleName() and d.track_label.buddy() is d.track
    assert d.plan is plan and d.preview_receipt is receipt and state(w) == before
    if locale == 'zh-CN':
        assert '独立片段' in d.windowTitle() and '不联动' in d.limits.text()
        assert '分离预览后' in tr_error('Source changed after separation preview; preview again')


def test_command_unbound_and_status_mute_independent(video_window, app):
    w = video_window
    assert COMMANDS['edit.separate_audio'].category == 'Edit'
    assert default_bindings('windows')['edit.separate_audio'] == ()
    assert default_bindings('macos')['edit.separate_audio'] == ()
    w.shortcuts.actions['edit.separate_audio'].trigger()
    d = w.source_audio_separation_dialog; d.track.setCurrentIndex(1)
    preview(w, app, d); d.apply(); settle(w, app)
    w.select_clip('v'); w.refresh_inspector()
    w.mute.setChecked(True); w.mute.setChecked(False)
    assert not w.editor._clip('v').source_audio_enabled


def test_terminal_status_does_not_overwrite_newer_job(video_window):
    from types import SimpleNamespace
    w = video_window; w._job_generation += 1
    w.progress_label.setText('Newer result'); w.progress_bar.setValue(73)
    ui._finish_job(w, SimpleNamespace(cancel=threading.Event()), w._job_generation - 1,
                  {'completed': True, 'failed': False})
    assert w.progress_label.text() == 'Newer result' and w.progress_bar.value() == 73


def test_selection_history_is_exact_edge_and_session_only(video_window, app):
    w = video_window; d = dialog(w, app); preview(w, app, d)
    d.apply(); settle(w, app); audio_id = w.project.clips[-1].id
    w.edit(lambda: w.editor.update_clip(audio_id, gain_db=-12))
    w.select_clip('other'); w.undo()  # Undo gain is not separation.
    assert w.selected_clip_ids == ('other',)
    w.undo(); assert w.selected_clip_ids == ('v',)
    w.redo(); assert w.selected_clip_ids == (audio_id,)
    w.redo(); assert w.selected_clip_ids == (audio_id,)
    assert w._source_audio_selection_history
    w.editor = Editor(deepcopy(w.project)); w.refresh()
    assert not w._source_audio_selection_history

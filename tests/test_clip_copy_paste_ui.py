"""Real Qt whole-clip commands, isolated workers, stale intent and native text."""
from copy import deepcopy
import json
from pathlib import Path
import threading
import time

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QMenu, QSpinBox

from lumen.i18n import set_locale, tr_error
from lumen.model import Editor
from lumen.shortcuts import COMMANDS, PROFILES, SETTINGS_KEY, ShortcutController, default_bindings
import lumen.ui_clip_copy_paste as ui
from test_master_gain_ui import app, window
from test_shortcuts_dispatch import activate, press, repeat, CTRL


def state(w):
    return deepcopy((w.project, w.editor._undo, w.editor._redo, w.editor.revision,
                     w.revision, w.dirty, w.preview_revision, w.preview_request,
                     w.selected_clip_ids, w.active_selection_type))


def settle(w, app):
    deadline = time.monotonic() + 8
    while w.jobs and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.005)
    app.processEvents()
    assert not w.jobs


def copy(w, app):
    activate(app, w, w.timeline)
    assert w.copy_selected_clips() is not None
    settle(w, app)
    assert w._clip_clipboard is not None
    return w._clip_clipboard


def paste_dialog(w, app, destination=90):
    activate(app, w, w.timeline)
    d = w.paste_copied_clips()
    assert d is not None and d.isVisible()
    d.destination_edit.setText(str(destination)); app.processEvents()
    assert d.paste_button.isEnabled(), d.impact.text()
    return d


@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_copy_is_private_committed_snapshot_and_paste_selects_one_undo(window, app, monkeypatch, mode):
    w = window; w.set_workspace_mode(mode, persist=False)
    w.editor.group_clips(('a', 'b')); w.refresh(); w.select_clip('a')
    w.gain.setValue(2)  # Applies normally; capture committed values after it.
    clipboard = QApplication.clipboard(); clipboard.setText('outside sentinel')
    before = state(w); invalidations = []
    original = w.invalidate_preview_frame
    monkeypatch.setattr(w, 'invalidate_preview_frame', lambda *a, **k: (invalidations.append(True), original(*a, **k))[1])
    copied = copy(w, app)
    assert copied.clip_count == 2 and state(w) == before and invalidations == []
    assert clipboard.text() == 'outside sentinel'
    # Editing source settings later never edits the copied parameter snapshot.
    w.editor.update_clip('a', gain_db=-7); w.changed()
    prior = deepcopy(w.project); depth = len(w.editor._undo); rev = w.revision
    d = paste_dialog(w, app)
    d.paste(); settle(w, app)
    assert not d.isVisible() and w.clip_paste_dialog is None
    assert len(w.project.clips) == 4 and len(w.project.clip_groups) == 2
    new_ids = w.selected_clip_ids
    assert len(new_ids) == 2 and not set(new_ids).intersection({'a', 'b'})
    assert all(c.start == 90 for c in w.project.clips if c.id in new_ids)
    assert all(c.gain_db == copied_value.gain_db for c, copied_value in zip(w.project.clips[2:], prior.clips)
               if copied_value.id != 'a')
    assert w.project.clips[2].gain_db != -7
    assert len(w.editor._undo) == depth + 1 and w.revision == rev + 1
    after = deepcopy(w.project)
    w.undo(); assert w.project == prior
    w.redo(); assert w.project == after
    assert [c.id for c in w.project.clips[2:]] == list(new_ids)
    assert clipboard.text() == 'outside sentinel' and w._clip_clipboard is copied
    assert not w.test_warnings


def test_copy_on_locked_source_allowed_but_paste_rejects_without_mutation(window, app):
    w = window; w.editor.set_track_edit_lock(2, True); w.changed()
    copied = copy(w, app); before = state(w)
    activate(app, w, w.timeline); d = w.paste_copied_clips(); d.destination_edit.setText('90')
    assert not d.paste_button.isEnabled() and 'lock' in d.impact.text()
    d.paste(); assert state(w) == before and w._clip_clipboard is copied


@pytest.mark.parametrize('failure', ['missing', 'changed', 'worker'])
def test_copy_failure_preserves_previous_copy_history_selection_and_system_clipboard(window, app, monkeypatch, failure):
    w = window; previous = copy(w, app); before = state(w)
    QApplication.clipboard().setText('native sentinel')
    if failure == 'missing': Path(w.project.media[0].path).unlink()
    elif failure == 'changed':
        # Changed while batch is being verified is a failed Copy, not a new snapshot.
        monkeypatch.setattr(ui, 'verify_clip_copy', lambda *_a, **_k: (_ for _ in ()).throw(ValueError('Clip dependencies changed during verification')))
    else: monkeypatch.setattr(ui, 'verify_clip_copy', lambda *_a, **_k: (_ for _ in ()).throw(ValueError('worker failed')))
    activate(app, w, w.timeline); w.copy_selected_clips(); settle(w, app)
    assert w._clip_clipboard is previous and state(w) == before and w.test_warnings
    assert QApplication.clipboard().text() == 'native sentinel'


def held(monkeypatch, name):
    started, release = threading.Event(), threading.Event()
    original = getattr(ui, name)
    owner = threading.get_ident()
    def work(*args, **kwargs):
        assert threading.get_ident() != owner
        started.set(); assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(ui, name, work)
    return started, release


@pytest.mark.parametrize('race', ['cancel', 'selection', 'edit_undo', 'replacement', 'unversioned'])
def test_pending_copy_is_worker_only_and_stale_completion_keeps_last_copy(window, app, monkeypatch, race):
    w = window; old = copy(w, app)
    started, release = held(monkeypatch, 'verify_clip_copy')
    activate(app, w, w.timeline); w.copy_selected_clips(); assert started.wait(2)
    try:
        if race == 'cancel': w.cancel_jobs()
        elif race == 'selection': w.select_clip('b')
        elif race == 'edit_undo': w.edit(lambda: w.editor.update_clip('a', gain_db=-2)); w.undo()
        elif race == 'replacement': w.editor = Editor(deepcopy(w.project)); w.refresh()
        else: w.project.clips[0].gain_db = -1
        expected = state(w)
        app.processEvents()  # GUI remains responsive while worker is held.
    finally:
        release.set(); settle(w, app)
    assert state(w) == expected
    assert w._clip_clipboard is (None if race == 'replacement' else old)
    assert not w.test_warnings


@pytest.mark.parametrize('race', ['cancel', 'global_cancel', 'target_aba', 'playhead_aba', 'edit_undo', 'lock', 'copy_replaced', 'session', 'unversioned'])
def test_pending_paste_never_commits_after_stale_intent(window, app, monkeypatch, race):
    w = window; old = copy(w, app)
    d = paste_dialog(w, app)
    started, release = held(monkeypatch, 'verify_clip_paste')
    d.paste(); assert started.wait(2)
    try:
        if race == 'cancel': d.reject()
        elif race == 'global_cancel': w.cancel_jobs()
        elif race == 'target_aba': d.destination_edit.setText('100'); d.destination_edit.setText('90')
        elif race == 'playhead_aba': w.seek_frame(1); w.seek_frame(0)
        elif race == 'edit_undo': w.edit(lambda: w.editor.update_clip('a', gain_db=-2)); w.undo()
        elif race == 'lock': w.edit(lambda: w.editor.set_track_edit_lock(2, True))
        elif race == 'copy_replaced': w._clip_clipboard = deepcopy(old)
        elif race == 'session': w.editor = Editor(deepcopy(w.project)); w.refresh()
        else: w.project.clips[0].gain_db = -1
        d.check_current(); expected = state(w)
        app.processEvents()
    finally:
        release.set(); settle(w, app)
    assert state(w) == expected and len(w.project.clips) == 2
    assert not w.test_warnings


@pytest.mark.parametrize('destination', ['', '-1', '1.5', '1e2', '９０', '9007199254740992', '9'*20])
def test_explicit_destination_rejects_invalid_without_clamp(window, app, destination):
    w = window; copied = copy(w, app); d = paste_dialog(w, app)
    before = state(w); d.destination_edit.setText(destination)
    assert not d.paste_button.isEnabled()
    d.paste(); assert state(w) == before and w._clip_clipboard is copied
    assert d.destination_edit.text() == destination


def test_explicit_destination_large_exact_frame_and_capture_reset(window, app):
    w = window; copy(w, app); d = paste_dialog(w, app)
    d.destination_edit.setText('9007199254740000')
    assert d.destination == 9007199254740000 and d.paste_button.isEnabled()
    w.seek_frame(15); assert not d.paste_button.isEnabled()
    d.capture_playhead(); assert d.destination_edit.text() == '15' and d.destination == 15
    assert not d.paste_button.isEnabled()  # Collision, never auto-positioned.
    assert 'overlap' in d.impact.text().lower()


@pytest.mark.parametrize('operation,key', [('copy', Qt.Key.Key_C), ('paste', Qt.Key.Key_V)])
@pytest.mark.parametrize('focus_kind', ['text', 'number', 'media', 'search', 'dialog', 'modal', 'popup', 'ime', 'unrelated', 'nonactive'])
def test_copy_paste_shortcuts_preserve_native_focus_and_input_method(window, app, monkeypatch, operation, key, focus_kind):
    w = window; copy(w, app)
    calls = []
    monkeypatch.setattr(w, 'copy_selected_clips', lambda: calls.append('copy'))
    # QAction callbacks captured the bound method at creation, so replace its
    # connections while preserving the real dispatcher and enabled guard.
    action = w.shortcuts.actions['edit.copy_clips' if operation == 'copy' else 'edit.paste_clips']
    action.triggered.disconnect(); action.triggered.connect(lambda: calls.append(operation))
    control = None
    if focus_kind == 'text': control = QLineEdit(w.centralWidget()); control.show(); activate(app, w, control)
    elif focus_kind == 'number': control = QSpinBox(w.centralWidget()); control.show(); activate(app, w, control)
    elif focus_kind == 'media': control = w.bin; activate(app, w, control)
    elif focus_kind == 'search': control = w.media_search; activate(app, w, control)
    elif focus_kind in ('dialog', 'modal', 'nonactive'):
        control = QDialog(w if focus_kind != 'nonactive' else None)
        control.setModal(focus_kind == 'modal'); control.show(); QApplication.setActiveWindow(control); control.setFocus(); app.processEvents()
    elif focus_kind == 'popup':
        control = QMenu(w); control.addAction('dummy'); control.popup(w.mapToGlobal(w.rect().center())); app.processEvents()
    elif focus_kind == 'unrelated': control = w.preview_button; activate(app, w, control)
    else:
        control = w.timeline; activate(app, w, control); QApplication.sendEvent(control, QInputMethodEvent('拼', []))
    before = state(w)
    press(app, control, key, CTRL)
    assert not calls and state(w) == before
    if focus_kind == 'ime': QApplication.sendEvent(control, QInputMethodEvent('', []))
    if focus_kind in ('dialog', 'modal', 'popup', 'nonactive'): control.close(); control.deleteLater()


def test_native_text_copy_paste_is_not_intercepted(window, app):
    w = window; copy(w, app)
    editor = QLineEdit(w.centralWidget()); editor.show(); activate(app, w, editor)
    editor.setText('native text'); editor.selectAll()
    press(app, editor, Qt.Key.Key_C, CTRL)
    assert QApplication.clipboard().text() == 'native text'
    editor.clear(); press(app, editor, Qt.Key.Key_V, CTRL)
    assert editor.text() == 'native text' and len(w.project.clips) == 2


def test_default_commands_and_old_custom_config_stay_collision_free(window):
    for profile, primary in [('windows', 'Ctrl'), ('macos', 'Cmd')]:
        values = default_bindings(profile)
        assert values['edit.copy_clips'] == (primary + '+C',)
        assert values['edit.paste_clips'] == (primary + '+V',)
    commands = ('edit.copy_clips', 'edit.paste_clips')
    profiles = {p: {k: list(v) for k, v in default_bindings(p).items() if k not in commands} for p in PROFILES}
    profiles['windows']['edit.split'] = ['Ctrl+C']; profiles['windows']['timeline.snapping'] = ['Ctrl+V']
    window.workspace_settings.setValue(SETTINGS_KEY, json.dumps({'version': 1, 'active_profile': 'windows', 'profiles': profiles}))
    controller = ShortcutController(window, window.workspace_settings)
    try:
        assert not controller.load_warning
        assert controller.bindings['edit.split'] == ('Ctrl+C',) and controller.bindings['timeline.snapping'] == ('Ctrl+V',)
        assert all(not controller.bindings[key] for key in commands)
    finally: controller.detach()


@pytest.mark.parametrize('profile', ['windows', 'macos'])
def test_actual_shortcuts_once_no_repeat_no_qaction_bypass(window, app, profile):
    from lumen.shortcuts import sequence_from_binding
    w = window; w.shortcuts.switch_profile(profile); activate(app, w, w.timeline)
    action = w.shortcuts.actions['edit.copy_clips']
    assert not action.shortcuts() and not action.autoRepeat()
    combination = sequence_from_binding(w.shortcuts.bindings['edit.copy_clips'][0])[0]
    press(app, w.timeline, combination.key(), combination.keyboardModifiers())
    repeat(app, w.timeline, combination.key(), combination.keyboardModifiers())
    settle(w, app); assert w._clip_clipboard is not None
    generation = w._clip_copy_generation
    repeat(app, w.timeline, combination.key(), combination.keyboardModifiers())
    assert w._clip_copy_generation == generation


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_language_help_shortcut_search_and_error_boundary(window, app, locale):
    from lumen.i18n_catalog_clip_copy_paste import ERROR_ZH_CN, ZH_CN
    from lumen.ui_help import help_text
    from lumen.ui_shortcuts import ShortcutDialog
    w = window; copy(w, app); d = paste_dialog(w, app); before = state(w)
    set_locale(locale); app.processEvents()
    assert d.windowTitle() == (ZH_CN['Paste clips at playhead (original tracks)'] if locale == 'zh-CN' else 'Paste clips at playhead (original tracks)')
    assert ('系统剪贴板' if locale == 'zh-CN' else 'system clipboard') in d.private_note.text()
    assert (ZH_CN[ui.COPY_HELP] if locale == 'zh-CN' else ui.COPY_HELP) in help_text()
    assert state(w) == before
    if locale == 'zh-CN':
        for message in ERROR_ZH_CN:
            assert str(tr_error(message)) != message and '技术错误' not in str(tr_error(message))
    shortcut_dialog = ShortcutDialog(w.shortcuts, w)
    try:
        for query in ('复制片段', 'Copy clips', 'edit.copy_clips'):
            shortcut_dialog.search_edit.setText(query)
            visible = {cid for cid, row in shortcut_dialog._rows.items() if not shortcut_dialog.commands_table.isRowHidden(row)}
            assert visible == {'edit.copy_clips'}
    finally: shortcut_dialog.close(); shortcut_dialog.deleteLater()


def test_same_session_hide_show_preserves_private_copy(window, app):
    w = window; copied = copy(w, app); before = state(w)
    w.hide(); app.processEvents(); activate(app, w, w.timeline)
    assert w._clip_clipboard is copied and state(w) == before


@pytest.mark.parametrize('operation', ['new', 'open', 'recover'])
def test_explicit_project_switch_clears_copy_without_saving_it(window, app, monkeypatch, tmp_path, operation):
    from PySide6.QtWidgets import QFileDialog, QInputDialog
    from lumen.model import Project
    w = window; copy(w, app)
    monkeypatch.setattr(w, 'confirm_discard', lambda: True)
    if operation == 'new':
        monkeypatch.setattr(QInputDialog, 'getItem', lambda *_a, **_k: ('1280 × 720 · 30 fps', True))
        w.new_project()
    elif operation == 'open':
        path = tmp_path / 'open.lumen'; Project(name='Opened').save(path)
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *_a, **_k: (str(path), ''))
        w.open_project()
    else:
        from test_recovery_ui import orphan
        entry, _ = orphan(tmp_path / 'recovery')
        assert w.restore_recovery(entry)
    assert w._clip_clipboard is None and not w.can_paste_copied_clips()
    assert 'clip_clipboard' not in w.project.to_dict()


def test_enter_while_editing_destination_does_not_paste_and_escape_cancels(window, app):
    from PySide6.QtTest import QTest
    w = window; copy(w, app); d = paste_dialog(w, app)
    before = state(w); d.destination_edit.setFocus()
    QTest.keyClick(d.destination_edit, Qt.Key.Key_Return); app.processEvents()
    assert d.isVisible() and not w.jobs and state(w) == before
    QTest.keyClick(d.destination_edit, Qt.Key.Key_Escape); app.processEvents()
    assert d._closed_flag and state(w) == before


def test_actual_edit_menu_copy_then_open_paste(window, app):
    from PySide6.QtTest import QTest
    w = window; activate(app, w, w.timeline)
    menu = next(a.menu() for a in w.menuBar().actions() if a.text() == 'Edit')
    for command in ('edit.copy_clips', 'edit.paste_clips'):
        activate(app, w, w.timeline)
        QTest.mouseClick(w.menuBar(), Qt.MouseButton.LeftButton,
            pos=w.menuBar().actionGeometry(menu.menuAction()).center()); app.processEvents()
        action = w.shortcuts.actions[command]; assert action.isEnabled()
        QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())
        app.processEvents(); settle(w, app)
    assert w._clip_clipboard is not None and w.clip_paste_dialog is not None


def test_copy_is_not_shared_with_other_window(window, app, tmp_path):
    from PySide6.QtCore import QSettings
    from lumen.ui import MainWindow
    w = window; copied = copy(w, app)
    other = MainWindow(workspace_settings=QSettings(str(tmp_path / 'other.ini'), QSettings.Format.IniFormat),
                       ui_language='en', recovery_root=tmp_path / 'other-recovery')
    try:
        other.editor = Editor(deepcopy(w.project)); other.refresh()
        assert other._clip_clipboard is None and not other.can_paste_copied_clips()
        assert w._clip_clipboard is copied
    finally:
        other.dirty = False; other.closing = True; other.close(); app.processEvents(); other.deleteLater()


def test_repeated_paste_click_during_worker_commits_once(window, app, monkeypatch):
    w = window; copy(w, app); d = paste_dialog(w, app)
    started, release = held(monkeypatch, 'verify_clip_paste')
    before = len(w.editor._undo)
    d.paste(); assert started.wait(2)
    try:
        first_job = d.job
        for _ in range(4): d.paste()
        assert d.job is first_job and len(w.jobs) == 1
    finally:
        release.set(); settle(w, app)
    assert len(w.project.clips) == 3 and len(w.editor._undo) == before + 1


@pytest.mark.parametrize('change', ['media_changed', 'media_missing', 'lut_changed'])
def test_fresh_dependency_failure_preserves_paste_state(window, app, tmp_path, change):
    w = window
    if change == 'lut_changed':
        lut = tmp_path / 'look.cube'; lut.write_text('LUT_3D_SIZE 2\n0 0 0\n')
        w.editor.update_clip('a', grade=deepcopy(w.project.clips[0].grade))
        w.project.clips[0].grade.lut_path = str(lut)
    copied = copy(w, app); d = paste_dialog(w, app)
    if change == 'media_changed': Path(w.project.media[0].path).write_bytes(b'edited local source')
    elif change == 'media_missing': Path(w.project.media[0].path).unlink()
    else: lut.write_text('LUT_3D_SIZE 2\n1 0 0\n')
    before = state(w)
    d.paste(); settle(w, app)
    assert state(w) == before and w._clip_clipboard is copied and w.test_warnings
    assert d.isVisible() and d._last_error


def test_actual_overflow_error_is_chinese_without_english_field_name(window, app):
    w = window; copy(w, app); d = paste_dialog(w, app)
    set_locale('zh-CN'); d.destination_edit.setText('9007199254740991')
    assert not d.paste_button.isEnabled()
    assert '粘贴终点' in d.impact.text() and 'paste end' not in d.impact.text()


@pytest.mark.parametrize('operation', ['copy', 'paste'])
@pytest.mark.parametrize('finish', ['success', 'cancel', 'stale', 'failed'])
def test_clipboard_jobs_always_leave_terminal_global_status(window, app, monkeypatch, operation, finish):
    w = window
    if operation == 'paste':
        copy(w, app); dialog = paste_dialog(w, app)
    function = 'verify_clip_copy' if operation == 'copy' else 'verify_clip_paste'
    if finish == 'failed':
        monkeypatch.setattr(ui, function, lambda *_a, **_k: (_ for _ in ()).throw(ValueError('Clip dependencies changed during verification')))
    started, release = held(monkeypatch, function)
    if operation == 'copy':
        activate(app, w, w.timeline); w.copy_selected_clips()
    else: dialog.paste()
    assert started.wait(2)
    try:
        if finish == 'cancel': w.cancel_jobs()
        elif finish == 'stale':
            # Pure live project mutation invalidates the signature. Let the
            # callback discard it without invoking a separate cancel action.
            w.project.clips[0].gain_db = -2
        before = deepcopy(w.project)
    finally:
        release.set(); settle(w, app)
    assert not w.jobs and not w.cancel_button.isEnabled()
    text = w.progress_label.text()
    if finish == 'success':
        assert text == 'Ready · local files only' and w.progress_bar.value() == 100
    else:
        assert w.project == before and w.progress_bar.value() == 0
        if finish == 'cancel': assert text == 'Canceled · sources unchanged'
        elif finish == 'failed': assert text == 'Clip dependency check failed · project unchanged'
        else: assert text in ('Clip operation expired · project unchanged', 'Canceled · sources unchanged')
    if finish != 'failed': assert not w.test_warnings


@pytest.mark.parametrize('operation', ['copy', 'paste'])
def test_clipboard_terminal_cleanup_cannot_overwrite_newer_job(window, app, monkeypatch, operation):
    w = window
    if operation == 'paste': copy(w, app); dialog = paste_dialog(w, app)
    started, release = held(monkeypatch, 'verify_clip_copy' if operation == 'copy' else 'verify_clip_paste')
    newer_started, newer_release = threading.Event(), threading.Event()
    old_finished = w.job_finished
    created = []
    def finish(job, kind):
        old_finished(job, kind)
        if kind == 'clip_' + operation and not created:
            def newer(cancel, progress):
                newer_started.set(); assert newer_release.wait(5)
                return None
            created.append(w.start_job('Newer task owns this label', newer, lambda _: None, 'terminal_status_test'))
    monkeypatch.setattr(w, 'job_finished', finish)
    if operation == 'copy': activate(app, w, w.timeline); w.copy_selected_clips()
    else: dialog.paste()
    assert started.wait(2)
    release.set()
    try:
        deadline = time.monotonic() + 5
        while not newer_started.is_set() and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.005)
        app.processEvents()
        assert newer_started.is_set() and len(w.jobs) == 1
        assert w.progress_label.text() == 'Newer task owns this label'
        assert w.progress_bar.value() == 0 and w.cancel_button.isEnabled()
    finally:
        newer_release.set(); settle(w, app)
    assert w.progress_label.text() == 'Ready · local files only'


def test_clipboard_terminal_generation_guard_even_when_newer_work_already_finished(window):
    from types import SimpleNamespace
    w = window
    w.jobs.clear(); w._job_generation += 1
    w.progress_label.setText('Newer terminal status'); w.progress_bar.setValue(75)
    ui._finish_clipboard_job(w, SimpleNamespace(cancel=threading.Event()), w._job_generation - 1,
                            {'completed': True, 'failed': False})
    assert w.progress_label.text() == 'Newer terminal status' and w.progress_bar.value() == 75


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('width', [320, 360, 620])
def test_paste_footer_preserves_text_and_geometry_when_font_changes(window, app, locale, width):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QFont
    set_locale(locale)
    w = window
    copy(w, app)
    d = paste_dialog(w, app)
    before = state(w)
    from PySide6.QtTest import QTest
    from lumen.i18n import tr
    # Keep focus and tab order while changing both language and font live.
    other_locale = 'zh-CN' if locale == 'en' else 'en'
    d.paste_button.setFocus()
    for active_locale, point_size in ((locale, 15), (other_locale, 24), (locale, 10)):
        set_locale(active_locale)
        w.setFont(QFont('DejaVu Sans', point_size))
        d.setFont(QFont('DejaVu Sans', point_size))
        d.resize(width, 476)
        d.fit_available_screen(QRect(0, 0, width + 32, 540))
        for _ in range(8): app.processEvents()
        rects = []
        for button in (d.paste_button, d.cancel_button):
            assert button.font().pointSizeF() == point_size
            rect = QRect(button.mapTo(d, button.rect().topLeft()), button.size())
            assert d.rect().contains(rect)
            assert button.fontMetrics().horizontalAdvance(button.text()) <= button.width() - 8
            rects.append(rect)
        assert not rects[0].intersects(rects[1])
        assert d.width() <= width and d.height() <= 476
        assert d.scroll.horizontalScrollBar().maximum() == 0
        assert d.paste_button.isEnabled()
        assert d.paste_button.text() == tr('Paste clips')
        assert d.cancel_button.text() == tr('Cancel')
        assert app.focusWidget() is d.paste_button
        QTest.keyClick(d.paste_button, Qt.Key.Key_Tab)
        assert app.focusWidget() is d.cancel_button
        QTest.keyClick(d.cancel_button, Qt.Key.Key_Backtab)
        assert app.focusWidget() is d.paste_button
        for control in (d.destination_edit, d.capture_button):
            # QLineEdit's inputMethodCursorRectangle is smaller than the
            # widget: ensureWidgetVisible may expose only that rectangle.
            center = control.mapTo(d.scroll.widget(), control.rect().center())
            d.scroll.ensureVisible(center.x(), center.y(), control.width() // 2 + 1,
                                   control.height() // 2 + 1)
            app.processEvents()
            visible = QRect(control.mapTo(d.scroll.viewport(), control.rect().topLeft()), control.size())
            assert d.scroll.viewport().rect().contains(visible)
        assert state(w) == before
    d.reject()
    set_locale('en')

"""Transactional, selection-independent manual master gain interactions."""
from copy import deepcopy
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from lumen.audio_analysis import audio_context_fingerprint
from lumen.i18n import set_locale
from lumen.i18n_catalog_shortcuts import EN, ZH_CN
from lumen.model import Clip, Editor, Media, Project
from lumen.shortcuts import COMMANDS, SETTINGS_KEY, default_bindings
from lumen.ui import MainWindow
from lumen.ui_audio_analysis import open_audio_analysis
from lumen.ui_master_gain import open_master_gain
from lumen.ui_shortcuts import ShortcutDialog


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(str(args[2])))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    w = MainWindow(workspace_settings=QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat),
                   ui_language='en', recovery_root=tmp_path / 'recovery')
    source = tmp_path / 'audio.wav'; source.write_bytes(b'local test source')
    w.editor = Editor(Project(media=[Media('m', str(source), 'Audio', 3, has_video=False, has_audio=True)],
        clips=[Clip('a', 'm', 2, 0, 0, 60), Clip('b', 'm', 3, 0, 0, 60)]))
    w.refresh(); w.select_clip('a'); w.show(); app.processEvents()
    w.test_warnings = warnings
    yield w
    w.cancel_jobs()
    for job in list(w.jobs): assert job.wait(5000)
    app.processEvents()
    w.dirty = False; w.closing = True; w.close(); app.processEvents()


def test_menu_opens_one_staged_dialog_without_job_or_toolbar_change(window):
    before = deepcopy(window.project)
    revision, undo, height = window.revision, len(window.editor._undo), window.timeline.height()
    assert COMMANDS['timeline.master_gain'].category == 'Timeline'
    assert window.shortcuts.bindings['timeline.master_gain'] == ()
    window.shortcuts.actions['timeline.master_gain'].trigger()
    dialog = window.master_gain_dialog
    assert dialog.isVisible() and open_master_gain(window) is dialog
    dialog.gain.setValue(-6.5)
    assert window.project == before and window.revision == revision and not window.jobs
    assert len(window.editor._undo) == undo and window.timeline.height() == height
    assert 'entire mix' in dialog.warning.text() and 'does not prevent clipping' in dialog.warning.text()
    assert 'Build preview and listen' in dialog.workflow.text()


@pytest.mark.parametrize('dismiss', ['cancel', 'escape', 'close'])
def test_dismissal_discards_draft_and_reopening_loads_project(window, dismiss):
    before = deepcopy(window.project)
    dialog = open_master_gain(window); dialog.gain.setValue(-8)
    if dismiss == 'cancel': dialog.cancel_button.click()
    elif dismiss == 'escape': QTest.keyClick(dialog, Qt.Key.Key_Escape)
    else: dialog.close()
    assert dialog._closed_flag and not dialog.timer.isActive()
    assert window.project == before and not window.editor.can_undo
    reopened = open_master_gain(window)
    assert reopened is not dialog and reopened.gain.value() == 0


@pytest.mark.parametrize('value', [-96, -6.5, 24])
def test_apply_is_one_undo_preserves_clip_controls_and_invalidates_preview(window, value):
    clips = deepcopy(window.project.clips)
    window.preview_revision = window.revision
    dialog = open_master_gain(window); dialog.gain.setValue(value)
    dialog.apply_button.click(); dialog.apply_settings()
    assert dialog._closed_flag and not dialog.isVisible()
    assert window.project.master_gain_db == value and window.project.clips == clips
    assert len(window.editor._undo) == 1 and window.dirty
    assert window.preview_revision != window.revision
    assert not window.jobs and 'Build preview' in window.preview_label.text()
    window.undo(); assert window.project.master_gain_db == 0
    window.redo(); assert window.project.master_gain_db == value


def test_unchanged_apply_preserves_exact_loaded_precision_and_redo(window):
    original = -1.234567890123
    window.editor.set_master_gain(original); window.editor.set_master_gain(-3); window.undo()
    before = deepcopy(window.project)
    revision, undo, redo = window.revision, len(window.editor._undo), len(window.editor._redo)
    dialog = open_master_gain(window)
    assert dialog.gain.value() == -1.23
    dialog.apply_settings()
    assert dialog._closed_flag and window.project == before
    assert window.revision == revision and len(window.editor._undo) == undo and len(window.editor._redo) == redo


def test_pending_keyboard_input_applies_only_on_explicit_apply(window):
    dialog = open_master_gain(window)
    editor = dialog.gain.lineEdit(); editor.setFocus(); editor.selectAll()
    QTest.keyClicks(editor, '-7.25')
    assert window.project.master_gain_db == 0 and not window.editor.can_undo
    dialog.apply_settings()
    assert window.project.master_gain_db == -7.25 and len(window.editor._undo) == 1


def test_reset_rounding_edge_commits_exact_zero(window):
    window.editor.set_master_gain(.004); window.changed()
    undo = len(window.editor._undo)
    dialog = open_master_gain(window)
    assert dialog.gain.value() == 0 and dialog._draft_value == .004
    dialog.reset_button.click()
    assert dialog._draft_value == 0 and window.project.master_gain_db == .004
    dialog.apply_settings()
    assert window.project.master_gain_db == 0 and len(window.editor._undo) == undo + 1


def test_reset_is_staged_and_cancel_restores_nothing_because_nothing_changed(window):
    window.editor.set_master_gain(-8); window.changed()
    before = deepcopy(window.project); undo = len(window.editor._undo)
    dialog = open_master_gain(window); dialog.reset_button.click()
    assert dialog.gain.value() == 0 and window.project == before
    dialog.reject()
    assert window.project == before and len(window.editor._undo) == undo


@pytest.mark.parametrize('change', ['selection', 'clear', 'caption', 'seek'])
def test_clip_selection_and_playhead_do_not_invalidate_project_control(window, change):
    dialog = open_master_gain(window); dialog.gain.setValue(-4)
    if change == 'selection': window.select_clip('b')
    elif change == 'clear': window.clear_clip_selection()
    elif change == 'caption': window.active_selection_type = 'caption'
    else: window.seek_frame(18)
    dialog.check_current()
    assert dialog.apply_button.isEnabled() and not dialog._stale
    dialog.apply_settings(); assert window.project.master_gain_db == -4


@pytest.mark.parametrize('change', ['editor', 'session', 'project', 'editor_revision', 'window_revision', 'master', 'undo'])
def test_stale_context_never_applies_and_cannot_be_revived(window, change):
    dialog = open_master_gain(window); dialog.gain.setValue(-4)
    if change == 'editor': window.editor = Editor(deepcopy(window.project))
    elif change == 'session': window.editor.session_id = 'different-session'
    elif change == 'project': window.editor.project = deepcopy(window.project)
    elif change == 'editor_revision': window.editor.revision += 1
    elif change == 'window_revision': window.revision += 1
    elif change == 'master': window.project.master_gain_db = -2
    else:
        window.editor.set_master_gain(-1); window.undo()
    before = deepcopy(window.project); undo = len(window.editor._undo)
    dialog.check_current(); dialog.apply_settings()
    assert dialog._stale and not dialog.apply_button.isEnabled()
    assert 'Close and reopen' in dialog.status.text()
    assert window.project == before and len(window.editor._undo) == undo
    dialog.context = dialog._context(); dialog.check_current()
    assert not dialog.apply_button.isEnabled()


def test_busy_job_blocks_apply_without_dropping_draft(window):
    dialog = open_master_gain(window); dialog.gain.setValue(-4)
    job = object(); window.jobs.add(job)
    try:
        dialog.check_current(); dialog.apply_settings()
        assert window.project.master_gain_db == 0 and dialog._draft_value == -4
        assert not dialog.apply_button.isEnabled() and 'current task' in dialog.status.text()
    finally:
        window.jobs.remove(job)
    dialog.check_current(); dialog.apply_settings()
    assert window.project.master_gain_db == -4


def test_empty_project_can_be_configured_without_selection(window):
    window.editor = Editor(Project()); window.refresh()
    window.shortcuts.update_enabled()
    assert window.shortcuts.actions['timeline.master_gain'].isEnabled()
    dialog = open_master_gain(window); dialog.gain.setValue(-2); dialog.apply_settings()
    assert window.project.master_gain_db == -2 and not window.project.clips


def test_new_project_closes_unapplied_draft(window, monkeypatch):
    dialog = open_master_gain(window); dialog.gain.setValue(-4)
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *args: ('1280 × 720 · 30 fps', True))
    window.new_project()
    assert dialog._closed_flag and window.project.master_gain_db == 0
    assert not window.editor.can_undo


def test_closing_window_disables_dialog_without_apply(window):
    dialog = open_master_gain(window); dialog.gain.setValue(-4)
    window.closing = True; dialog.check_current(); dialog.apply_settings()
    assert dialog._closed_flag and not dialog.timer.isActive()
    assert window.project.master_gain_db == 0 and open_master_gain(window) is None


def test_open_stage_check_and_cancel_do_not_read_sources(window, monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('GUI draft attempted source IO')
    monkeypatch.setattr(Path, 'stat', forbidden)
    dialog = open_master_gain(window); dialog.gain.setValue(-6)
    dialog.check_current(); dialog.reject()
    assert not window.editor.can_undo


def test_locale_change_preserves_draft_and_bilingual_search(window):
    dialog = open_master_gain(window); dialog.gain.setValue(-8.25)
    before = deepcopy(window.project)
    set_locale('zh-CN'); QApplication.processEvents()
    assert dialog.windowTitle() == '主输出增益' and '整个混音' in dialog.warning.text()
    assert '再次分析' in dialog.workflow.text() and dialog.gain.value() == -8.25
    assert window.project == before and window.shortcuts.actions['timeline.master_gain'].text() == '主输出增益…'
    for locale, term in [('en', '主输出'), ('zh-CN', 'Master output gain')]:
        set_locale(locale)
        preferences = ShortcutDialog(window.shortcuts, window)
        preferences.search_edit.setText(term)
        assert preferences.selected_command_id == 'timeline.master_gain'
        preferences.reject()
    assert EN['Master output gain…'] == 'Master output gain…'
    assert ZH_CN['Master output gain…'] == '主输出增益…'
    assert dialog._draft_value == -8.25 and not window.editor.can_undo


@pytest.mark.parametrize('missing', [('timeline.master_gain',), ('timeline.analyze_audio',),
                                    ('timeline.master_gain', 'timeline.analyze_audio')])
def test_shortcut_migration_preserves_older_custom_bindings(app, tmp_path, missing):
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat)
    profiles = {key: default_bindings(key) for key in ('windows', 'macos')}
    for bindings in profiles.values():
        for key in missing: del bindings[key]
        bindings['timeline.snapping'] = ('Alt+S',)
    settings.setValue(SETTINGS_KEY, json.dumps(dict(version=1, active_profile='windows', profiles=profiles)))
    w = MainWindow(workspace_settings=settings, ui_language='en', recovery_root=tmp_path / 'recovery')
    try:
        assert not w.shortcuts.load_warning and w.shortcuts.bindings['timeline.snapping'] == ('Alt+S',)
        assert all(w.shortcuts.bindings[key] == () for key in missing)
    finally:
        w.dirty = False; w.closing = True; w.close(); app.processEvents()


def test_master_apply_clears_existing_analysis_without_reanalysis(window, monkeypatch):
    import lumen.ui_audio_analysis as analysis
    calls = []
    value = SimpleNamespace(integrated_lufs=-12.3, loudness_status='measured',
        sample_peak_dbfs=(-6., -8.), estimated_true_peak_dbtp=(-5., -7.),
        overload_counts=(0, 0), overload_count=0, first_overload_seconds=None,
        sample_count=96000, sample_rate=48000, fingerprint='report',
        context_fingerprint=audio_context_fingerprint(window.project))
    def analyze(*args, **kwargs): calls.append(True); return value
    monkeypatch.setattr(analysis, 'analyze_audio', analyze)
    monkeypatch.setattr(analysis, 'result_is_current', lambda *args: True)
    report = open_audio_analysis(window); report.analyze()
    deadline = time.monotonic() + 5
    while (window.jobs or report.state == 'checking') and time.monotonic() < deadline: QTest.qWait(10)
    assert report.state == 'current' and report.result is value
    assert 'master output gain' in report.stage.text()
    assert 'Master output gain' in report.next_step.text() and 'build preview and listen' in report.next_step.text()
    dialog = open_master_gain(window); dialog.gain.setValue(-6); dialog.apply_settings()
    assert report.state == 'stale' and report.result is None
    assert all(label.text() == 'Not analyzed' for label in report.values.values())
    assert len(calls) == 1 and not window.jobs


def test_rejected_edit_leaves_draft_open_without_project_mutation(window, monkeypatch):
    before = deepcopy(window.project)
    dialog = open_master_gain(window); dialog.gain.setValue(-6)
    def fail(value): raise ValueError('Invalid master output gain')
    monkeypatch.setattr(window.editor, 'set_master_gain', fail)
    dialog.apply_settings()
    assert not dialog._closed_flag and dialog.isVisible()
    assert dialog._draft_value == -6 and window.project == before and not window.editor.can_undo
    assert window.test_warnings and dialog.apply_button.isEnabled()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_help_explains_master_gain_scope_and_order(window, monkeypatch, locale):
    messages = []
    from lumen.ui_help import HelpDialog
    monkeypatch.setattr(HelpDialog, 'exec', lambda dialog: messages.append(dialog.body.toPlainText()))
    set_locale(locale); window.show_help()
    assert len(messages) == 1
    if locale == 'en':
        assert 'after master output gain, before encoding' in messages[0]
        assert 'Timeline → Master output gain' in messages[0]
        assert 'build preview and listen, then analyze again' in messages[0]
        assert 'Gain does not prevent clipping' in messages[0]
    else:
        assert '主输出增益之后、编码前' in messages[0]
        assert '时间线 → 主输出增益' in messages[0]
        assert '构建预览并试听，再重新分析' in messages[0]
        assert '增益不能防止削波' in messages[0]

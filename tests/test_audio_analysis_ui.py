"""Explicit, cancellable stereo analysis UI; never an edit or live meter."""
from copy import deepcopy
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from lumen.i18n import set_locale
from lumen.audio_analysis import audio_context_fingerprint
from lumen.model import Clip, Editor, Media, Project
from lumen.shortcuts import COMMANDS, SETTINGS_KEY, default_bindings
from lumen.ui import MainWindow
from lumen.ui_audio_analysis import open_audio_analysis
import lumen.ui_audio_analysis as module


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(str(args[2])))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat)
    window = MainWindow(workspace_settings=settings, ui_language='en', recovery_root=tmp_path / 'recovery')
    path = tmp_path / 'audio.wav'
    path.write_bytes(b'local audio test fixture')
    window.editor = Editor(Project(media=[Media('m', str(path), 'Audio source', 3, has_video=False, has_audio=True)],
                                   clips=[Clip('c', 'm', 2, 0, 0, 60)]))
    window.refresh(); window.select_clip('c')
    window.test_warnings = warnings
    window.show(); app.processEvents()
    yield window
    if window.audio_analysis_dialog is not None:
        window.audio_analysis_dialog.reject()
    window.cancel_jobs()
    for job in list(window.jobs): assert job.wait(5000)
    app.processEvents()
    window.dirty = False; window.closing = True; window.close()
    app.processEvents()


def report(**changes):
    values = dict(integrated_lufs=-12.3, loudness_status='measured',
                  sample_peaks=(2.0, 1.5), sample_peak_dbfs=(6.0205999, 3.5218252),
                  estimated_true_peaks=(2.1, 1.6), estimated_true_peak_dbtp=(6.4443859, 4.0823997),
                  overload_counts=(12, 8), overload_count=20,
                  first_overload_seconds=.25, sample_count=96000, sample_rate=48000,
                  fingerprint='test-report', context_fingerprint='test-context')
    values.update(changes)
    return SimpleNamespace(**values)


def mock_report(monkeypatch, window, value=None):
    value = value or report()
    value.context_fingerprint = audio_context_fingerprint(window.project)
    monkeypatch.setattr(module, 'analyze_audio', lambda *args, **kwargs: value)
    return value


def settle(window, timeout=5):
    until = time.monotonic() + timeout
    while (window.jobs or (window.audio_analysis_dialog is not None
            and window.audio_analysis_dialog.state == 'checking')) and time.monotonic() < until:
        QTest.qWait(10)
    QApplication.processEvents()
    assert not window.jobs


def completed(window, monkeypatch, value=None):
    value = mock_report(monkeypatch, window, value)
    dialog = open_audio_analysis(window)
    dialog.analyze(); settle(window)
    assert dialog.state == 'current' and dialog.result is value
    return dialog


def held_analysis(monkeypatch, window):
    started, release = threading.Event(), threading.Event()
    value = report(context_fingerprint=audio_context_fingerprint(window.project))
    def analyze(*args, **kwargs):
        started.set()
        assert release.wait(4)
        return value
    monkeypatch.setattr(module, 'analyze_audio', analyze)
    return started, release


def test_menu_is_explicit_and_adds_no_timeline_row_or_project_fields(window, monkeypatch):
    calls = []
    monkeypatch.setattr(module, 'analyze_audio', lambda *args, **kwargs: calls.append(True))
    before = deepcopy(window.project)
    revision, undo, height = window.revision, len(window.editor._undo), window.timeline.height()
    assert 'timeline.analyze_audio' in COMMANDS
    assert window.shortcuts.bindings['timeline.analyze_audio'] == ()
    window.shortcuts.actions['timeline.analyze_audio'].trigger()
    dialog = window.audio_analysis_dialog
    assert dialog.isVisible() and dialog.state == 'idle' and dialog.result is None
    assert not calls and not window.jobs
    assert window.project == before and window.revision == revision
    assert len(window.editor._undo) == undo and window.timeline.height() == height
    assert open_audio_analysis(window) is dialog


def test_complete_report_is_session_only_and_retains_unclipped_stereo_peaks(window, monkeypatch):
    before, revision = deepcopy(window.project), window.revision
    dialog = completed(window, monkeypatch)
    assert dialog.values['lufs'].text() == '-12.3 LUFS'
    assert dialog.values['left'].text() == '6.02 dBFS'
    assert dialog.values['right'].text() == '3.52 dBFS'
    assert dialog.values['true_left'].text() == '6.44 dBTP'
    assert dialog.values['true_right'].text() == '4.08 dBTP'
    assert 'estimated' in dialog.metric_labels['true_left'].text()
    assert dialog.values['overloads'].text() == '20 (L: 12; R: 8)'
    assert dialog.values['first'].text() == '0.250000 s'
    assert 'AAC' in dialog.caveat.text() and 'sample-peak lower bound' in dialog.caveat.text()
    assert dialog.progress.value() == 100 and window.progress_bar.value() == 100
    assert dialog.analyze_button.isEnabled()
    assert window.project == before and window.revision == revision and not window.editor.can_undo


@pytest.mark.parametrize('status,text', [('silence', 'silence'), ('too_short', '400 ms'), ('below_gate', 'loudness gate')])
def test_unmeasurable_loudness_is_never_a_numeric_lufs(window, monkeypatch, status, text):
    dialog = completed(window, monkeypatch, report(integrated_lufs=None, loudness_status=status,
        sample_peak_dbfs=(None, None), estimated_true_peak_dbtp=(None, None), overload_counts=(0, 0), overload_count=0, first_overload_seconds=None))
    assert 'No measurable LUFS' in dialog.values['lufs'].text()
    assert text in dialog.values['lufs'].text()
    assert dialog.values['left'].text() == 'Silence (−∞ dBFS)'
    assert dialog.values['true_left'].text() == 'Silence (−∞ dBTP)'
    assert dialog.values['true_right'].text() == 'Silence (−∞ dBTP)'
    assert dialog.values['first'].text() == 'None'


@pytest.mark.parametrize('cancel', ['dialog', 'global', 'close'])
def test_canceled_worker_cannot_publish_even_when_it_returns_a_report(window, monkeypatch, cancel):
    started, release = held_analysis(monkeypatch, window)
    dialog = open_audio_analysis(window); dialog.analyze()
    try:
        assert started.wait(2)
        assert window.audio_analysis_job in window.jobs
        if cancel == 'dialog': dialog.cancel_analysis()
        elif cancel == 'global': window.cancel_jobs()
        else: dialog.reject()
        assert dialog.result is None
    finally:
        release.set(); settle(window)
    assert dialog.result is None and dialog.state in ('canceled', 'closed')
    assert window.progress_bar.value() != 100
    assert all(label.text() == 'Not analyzed' for label in dialog.values.values())
    assert not window.test_warnings


@pytest.mark.parametrize('change', ['gain', 'timing', 'source', 'session'])
def test_relevant_change_invalidates_running_request_and_discards_late_result(window, monkeypatch, change):
    started, release = held_analysis(monkeypatch, window)
    dialog = open_audio_analysis(window); dialog.analyze()
    try:
        assert started.wait(2)
        if change == 'gain':
            window.editor.update_clip('c', gain_db=-6); window.changed()
        elif change == 'timing':
            window.editor.update_clip('c', start=5); window.changed()
        elif change == 'source':
            Path(window.project.media[0].path).write_bytes(b'changed source audio bytes')
        else:
            window.editor = Editor(deepcopy(window.project))
        dialog.check_current()
        if change != 'source':
            assert dialog.state == 'stale' and dialog.job.cancel.is_set()
    finally:
        release.set(); settle(window)
    assert dialog.result is None and not window.test_warnings


def test_visual_edit_preserves_report_but_gain_edit_clears_it_and_reanalysis_is_explicit(window, monkeypatch):
    dialog = completed(window, monkeypatch)
    original = dialog.result
    grade = deepcopy(window.project.clips[0].grade); grade.exposure = .5
    window.editor.update_clip('c', grade=grade); window.changed()
    assert dialog.state == 'current' and dialog.result is original
    window.gain.setValue(-3)
    assert dialog.state == 'stale' and dialog.result is None and not window.jobs
    mock_report(monkeypatch, window)
    dialog.analyze(); settle(window)
    assert dialog.state == 'current' and window.project.clips[0].gain_db == -3


def test_source_file_replacement_invalidates_completed_result(window, monkeypatch):
    dialog = completed(window, monkeypatch)
    path = Path(window.project.media[0].path)
    replacement = path.with_suffix('.replacement')
    replacement.write_bytes(path.read_bytes()); replacement.replace(path)
    dialog._last_verified = 0
    dialog.check_current(); settle(window)
    assert dialog.state == 'stale' and dialog.result is None


@pytest.mark.parametrize('boundary', ['new', 'open', 'close'])
def test_completed_result_does_not_cross_project_boundaries(window, monkeypatch, tmp_path, boundary):
    dialog = completed(window, monkeypatch)
    window.dirty = False
    if boundary == 'new':
        monkeypatch.setattr(QInputDialog, 'getItem', lambda *args: ('1280 × 720 · 30 fps', True))
        window.new_project()
    elif boundary == 'open':
        target = tmp_path / 'new.lumen'; Project().save(target)
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(target), ''))
        window.open_project()
    else:
        window.close()
    assert dialog.result is None and dialog.state == 'closed'


def test_current_errors_localize_and_language_switch_preserves_data(window, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError('The timeline is empty.')
    monkeypatch.setattr(module, 'analyze_audio', fail)
    dialog = open_audio_analysis(window); dialog.analyze(); settle(window)
    assert dialog.state == 'failed' and 'The timeline is empty.' in dialog.status.text()
    assert window.progress_bar.value() != 100
    set_locale('zh-CN'); QApplication.processEvents()
    assert dialog.windowTitle() == '分析时间线音频'
    assert '时间轴为空' in dialog.status.text() and 'The timeline is empty.' not in dialog.status.text()
    assert dialog.analyze_button.text() == '开始分析'
    assert window.shortcuts.actions['timeline.analyze_audio'].text() == '分析时间线音频…'
    assert not window.test_warnings


def test_chinese_metrics_and_retranslation_are_state_preserving(window, monkeypatch):
    dialog = completed(window, monkeypatch)
    before = deepcopy(window.project); result = dialog.result
    set_locale('zh-CN'); QApplication.processEvents()
    assert dialog.values['overloads'].text() == '20（左：12；右：8）'
    assert '综合响度' == dialog.metric_labels['lufs'].text()
    assert '真峰值' in dialog.caveat.text() and 'AAC' in dialog.caveat.text()
    assert dialog.result is result and window.project == before
    set_locale('en'); QApplication.processEvents()
    assert dialog.values['overloads'].text() == '20 (L: 12; R: 8)'


def test_old_custom_shortcuts_are_migrated_without_reset(app, tmp_path):
    settings = QSettings(str(tmp_path / 'shortcuts.ini'), QSettings.Format.IniFormat)
    profiles = {key: default_bindings(key) for key in ('windows', 'macos')}
    for bindings in profiles.values():
        del bindings['timeline.analyze_audio']
        bindings['timeline.snapping'] = ('Alt+S',)
    settings.setValue(SETTINGS_KEY, json.dumps(dict(version=1, active_profile='windows', profiles=profiles)))
    window = MainWindow(workspace_settings=settings, ui_language='en', recovery_root=tmp_path / 'recovery')
    try:
        assert not window.shortcuts.load_warning
        assert window.shortcuts.bindings['timeline.snapping'] == ('Alt+S',)
        assert window.shortcuts.bindings['timeline.analyze_audio'] == ()
    finally:
        window.dirty = False; window.closing = True; window.close(); app.processEvents()


def test_command_is_in_direct_bilingual_shortcut_search_catalog():
    from lumen.i18n_catalog_shortcuts import EN, ZH_CN
    assert EN['Analyze timeline audio…'] == 'Analyze timeline audio…'
    assert ZH_CN['Analyze timeline audio…'] == '分析时间线音频…'


def test_audio_backend_errors_have_explicit_localized_boundary():
    import ast
    from lumen.i18n import tr_error
    from lumen.i18n_catalog_audio_analysis import ERROR_ZH_CN
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'lumen/audio_analysis.py').read_text())
    static = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            name = getattr(node.exc.func, 'id', '')
            if name in ('AudioAnalysisError', 'AudioAnalysisCancelled') and node.exc.args:
                arg = node.exc.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    static.append(arg.value)
    assert static and all(text in ERROR_ZH_CN for text in static)
    set_locale('zh-CN')
    try:
        assert '安全上限' in tr_error('The timeline exceeds the safe audio analysis clip limit.')
        assert 'secret/path.wav' in tr_error('Cannot safely analyze the final audio mix: secret/path.wav')
    finally:
        set_locale('en')


def test_preview_and_export_cannot_overlap_analysis_but_edits_remain_available(window, monkeypatch):
    started, release = held_analysis(monkeypatch, window)
    import lumen.ui as core
    monkeypatch.setattr(core, 'choose_export_profile', lambda *args: pytest.fail('Export dialog must not open'))
    dialog = open_audio_analysis(window); dialog.analyze()
    try:
        assert started.wait(2)
        assert not window.preview_button.isEnabled() and not window.export_button.isEnabled()
        window.build_preview(); window.export_dialog()
        assert len(window.jobs) == 1 and window.preview_job is None and window.export_job is None
        assert 'audio analysis' in window.statusBar().currentMessage()
        window.gain.setValue(-6)
        assert window.project.clips[0].gain_db == -6
        assert dialog.state == 'stale' and dialog.job.cancel.is_set()
        # Canceling does not release the shared work slot before cleanup.
        assert not window.preview_button.isEnabled() and not window.export_button.isEnabled()
    finally:
        release.set(); settle(window)
    assert window.preview_button.isEnabled() and window.export_button.isEnabled()
    assert dialog.result is None


def test_source_change_is_detected_by_visible_dialog_timer_without_user_action(window, monkeypatch):
    dialog = completed(window, monkeypatch)
    Path(window.project.media[0].path).write_bytes(b'updated source contents')
    QTest.qWait(750)
    assert dialog.state == 'stale' and dialog.result is None


def test_source_change_between_worker_and_acceptance_cannot_publish(window, monkeypatch):
    started, release = held_analysis(monkeypatch, window)
    dialog = open_audio_analysis(window); dialog.analyze()
    try:
        assert started.wait(2)
        # Do not call refresh/check_current; the acceptance boundary must check.
        dialog.timer.stop()
        Path(window.project.media[0].path).write_bytes(b'new source before accepted callback')
    finally:
        release.set(); settle(window)
    assert dialog.state == 'stale' and dialog.result is None


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_help_explains_explicit_preencoding_scope_and_manual_gain_workflow(window, monkeypatch, locale):
    messages = []
    from lumen.ui_help import HelpDialog
    monkeypatch.setattr(HelpDialog, 'exec', lambda dialog: messages.append(dialog.body.toPlainText()))
    set_locale(locale)
    window.show_help()
    assert len(messages) == 1
    if locale == 'en':
        assert 'Timeline → Analyze timeline audio' in messages[0]
        assert 'before encoding' in messages[0] and 'edit clip gain' in messages[0]
        assert 'No automatic gain, limiter or encoded-file certification' in messages[0]
    else:
        assert '时间线 → 分析时间线音频' in messages[0]
        assert '编码前' in messages[0] and '调整片段增益后重新分析' in messages[0]
        assert '不包含自动增益、限幅器或编码文件达标认证' in messages[0]

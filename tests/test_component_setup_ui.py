"""Real Qt dialog integration; diagnostics never imply native inference works."""
import threading
from types import SimpleNamespace

from test_ui import app, window, pump_until
from lumen.i18n import get_locale, set_locale
from lumen.ui_component_setup import (ComponentSetupDialog, operational_settings,
                                      restore_component_preferences)


def report(message='fixture metadata'):
    return SimpleNamespace(checks=(SimpleNamespace(component='ASR', key='asr_model',
        status='metadata_available', message=message, recommendation='', path='', version='', sha256=''),))


def dialog(window, tmp_path, diagnose=None):
    return ComponentSetupDialog(window, path=tmp_path / 'setup.json', diagnose=diagnose)


def test_save_restore_operational_fields_without_project_changes(window, tmp_path):
    w = window; revision = w.revision
    d = dialog(w, tmp_path)
    for key, field in d.fields.items(): field.setText(str(tmp_path / key))
    d.pack.setCurrentIndex(d.pack.findData('v1.1'))
    d.apply_paths()
    assert operational_settings(w) == d.values()
    assert w.revision == revision and not w.project.media and not w.project.captions
    assert not w.ai_records and w._tts_verified_pack is None
    saved = d.path.read_text()
    assert 'audition' not in saved and 'caption' not in saved and 'fixture metadata' not in saved
    w.asr_runtime.clear()
    restore_component_preferences(w, d.path)
    assert w.asr_runtime.text() == str(tmp_path / 'asr_runtime')
    d.reject()


def test_absent_preferences_preserve_existing_paths(window, tmp_path):
    window.ai_fields['semantic-search']['model'].setText('/existing/registered/model')
    before = operational_settings(window)
    restore_component_preferences(window, tmp_path / 'absent.json')
    assert operational_settings(window) == before


def test_diagnostics_async_cancel_stale_retry_and_no_inference(window, app, tmp_path):
    started = threading.Event(); release = threading.Event(); calls = []
    def slow(values, *, verify_hashes, cancel):
        calls.append(values.copy()); started.set(); release.wait(3)
        return report('stale result')
    d = dialog(window, tmp_path, slow)
    d.start_diagnostics(); assert started.wait(2)
    assert window.jobs and d.cancel_button.isEnabled()
    d.fields['asr_model'].setText('/changed')
    d.start_diagnostics()  # Cannot start overlapping hash scans.
    assert len(calls) == 1
    release.set(); pump_until(app, lambda: not window.jobs)
    assert 'stale result' not in d.results.toPlainText()
    d.diagnose = lambda *a, **k: report('new result')
    d.start_diagnostics(); pump_until(app, lambda: not window.jobs)
    assert 'new result' in d.results.toPlainText()
    assert 'asr_model' in d.results.toPlainText() or '语音识别模型' in d.results.toPlainText()
    assert not window.ai_records and window._tts_verified_pack is None
    d.reject()


def test_global_cancel_and_dialog_close_cancel_owned_job(window, app, tmp_path):
    started = threading.Event()
    def slow(values, *, verify_hashes, cancel):
        started.set(); cancel.wait(3); return report('must not display')
    d = dialog(window, tmp_path, slow)
    d.start_diagnostics(); assert started.wait(2)
    window.cancel_jobs(); pump_until(app, lambda: not window.jobs)
    assert not d.cancel_button.isEnabled() and not d.results.toPlainText()
    started.clear(); d.start_diagnostics(); assert started.wait(2)
    d.reject(); pump_until(app, lambda: not window.jobs)
    assert not d.results.toPlainText()


def test_errors_retry_locale_switch_keeps_user_paths(window, app, tmp_path):
    locale = get_locale()
    try:
        set_locale('en')
        def broken(*a, **k): raise ValueError('fixture failure')
        d = dialog(window, tmp_path, broken)
        d.fields['asr_model'].setText('/用户/Model English.bin')
        d.start_diagnostics(); pump_until(app, lambda: not window.jobs)
        assert 'fixture failure' in d.status.text() and d.check_button.isEnabled()
        d.diagnose = lambda *a, **k: report()
        d.start_diagnostics(); pump_until(app, lambda: not window.jobs)
        assert 'Metadata available' in d.results.toPlainText()
        set_locale('zh-CN')
        assert d.windowTitle() == '本地 AI 组件设置'
        assert '元数据可用' in d.results.toPlainText()
        assert d.fields['asr_model'].text() == '/用户/Model English.bin'
        set_locale('en'); assert 'Metadata available' in d.results.toPlainText()
        d.reject()
    finally: set_locale(locale)


def test_apply_rejects_busy_and_stale_operational_fields(window, app, tmp_path):
    started = threading.Event()
    def slow(values, *, verify_hashes, cancel):
        started.set(); cancel.wait(3); return report()
    d = dialog(window, tmp_path, slow)
    d.fields['asr_model'].setText('/new/model')
    before = operational_settings(window)
    d.start_diagnostics(); assert started.wait(2)
    d.apply_paths(); assert not d.path.exists() and operational_settings(window) == before
    d.cancel_diagnostics(); pump_until(app, lambda: not window.jobs)
    window.asr_model.setText('/other/model')
    d.apply_paths(); assert not d.path.exists() and window.asr_model.text() == '/other/model'
    d.reject()


def test_delete_dialog_and_reopen_while_job_unwinds(window, app, tmp_path, monkeypatch):
    from lumen import ui_component_setup as ui
    from PySide6.QtCore import QCoreApplication, QEvent
    started = threading.Event(); release = threading.Event()
    def slow(*args, **kwargs):
        started.set(); release.wait(3); return report('discarded')
    monkeypatch.setattr(ui, 'preferences_path', lambda: tmp_path / 'setup.json')
    d = ui.open_component_setup(window); d.diagnose = slow
    d.start_diagnostics(); assert started.wait(2)
    d.reject(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    reopened = ui.open_component_setup(window)
    reopened.start_diagnostics()
    assert len(window.jobs) == 1
    release.set(); pump_until(app, lambda: not window.jobs)
    assert not reopened.results.toPlainText()
    reopened.reject()


def test_file_and_directory_choosers_are_local_only(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    d = dialog(window, tmp_path)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: ('/local/model.gguf', ''))
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a: '/local/kokoro-pack')
    d.choose_path('assistant_model'); d.choose_path('tts_model_dir')
    assert d.fields['assistant_model'].text() == '/local/model.gguf'
    assert d.fields['tts_model_dir'].text() == '/local/kokoro-pack'
    assert not d.path.exists() and window.ai_fields['subtitle-assistant']['model'].text() != '/local/model.gguf'
    d.reject()

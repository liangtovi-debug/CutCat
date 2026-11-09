"""Startup, menu/panel entrypoints and project invariance for local setup."""
from PySide6.QtCore import QSettings
from test_ui import app, window, pump_until
from lumen.component_setup import save_preferences
from lumen.i18n import set_locale
from lumen.ui import MainWindow
from lumen import ui_component_setup as setup


def test_both_entrypoints_share_dialog_and_translate(window, app, tmp_path, monkeypatch):
    monkeypatch.setattr(setup, 'preferences_path', lambda: tmp_path / 'setup.json')
    project = window.project.to_dict()
    revision = window.revision
    window.component_setup_action.trigger()
    first = window._component_setup_dialog
    assert first is not None and first.isVisible()
    window.component_setup_button.click()
    assert window._component_setup_dialog is first
    set_locale('zh-CN'); app.processEvents()
    assert window.component_setup_action.text() == '本地 AI 组件设置'
    assert window.component_setup_button.text() == '本地 AI 组件设置'
    assert first.windowTitle() == '本地 AI 组件设置'
    first.start_diagnostics()
    pump_until(app, lambda: not window.jobs)
    assert first._report is not None and not first._report.inference_tested
    assert window.project.to_dict() == project and window.revision == revision
    assert not window.ai_records and window._tts_verified_pack is None
    first.reject()


def test_startup_restores_paths_but_does_not_verify_models(app, tmp_path, monkeypatch):
    pref = tmp_path / 'component-setup.json'
    monkeypatch.setattr(setup, 'preferences_path', lambda: pref)
    save_preferences(pref, {'asr_runtime': '/local/whisper', 'asr_model': '/local/model.bin',
                           'tts_pack_version': 'v1.1', 'search_model': '/用户/model.onnx'})
    settings = QSettings(str(tmp_path / 'ui.ini'), QSettings.Format.IniFormat)
    w = MainWindow(workspace_settings=settings)
    try:
        assert w.asr_runtime.text() == '/local/whisper'
        assert w.asr_model.text() == '/local/model.bin'
        assert w.ai_fields['semantic-search']['model'].text() == '/用户/model.onnx'
        assert not w.jobs and not w.ai_records and w._tts_verified_pack is None
        assert not w.project.media and not w.project.captions and not w.dirty
    finally:
        w.closing = True; w.close(); app.processEvents()


def test_corrupt_startup_preferences_are_actionable_not_fatal(app, tmp_path, monkeypatch):
    pref = tmp_path / 'component-setup.json'; pref.write_text('{broken json')
    monkeypatch.setattr(setup, 'preferences_path', lambda: pref)
    settings = QSettings(str(tmp_path / 'ui.ini'), QSettings.Format.IniFormat)
    w = MainWindow(workspace_settings=settings)
    try:
        assert 'Local component preferences need attention:' in w.ai_model_status.text()
        assert not w.jobs and not w.dirty
    finally:
        w.closing = True; w.close(); app.processEvents()

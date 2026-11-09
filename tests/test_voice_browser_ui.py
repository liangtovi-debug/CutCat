"""Headless browser workflows with fake synthesis; real speech is tested separately."""
from test_ui import app, window, pump_until
from PySide6.QtCore import QSettings, QUrl
from PySide6.QtWidgets import QMessageBox
from pathlib import Path
from types import SimpleNamespace
import threading
import pytest


@pytest.fixture(autouse=True)
def settings(tmp_path, monkeypatch):
    import lumen.ui_captions as module
    settings = QSettings(str(tmp_path / 'favorites.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(module, 'QSettings', lambda *a: settings)
    return settings


def verify(w, app, tmp_path, monkeypatch):
    import lumen.tts as tts
    w.tts_model.setText(str(tmp_path))
    monkeypatch.setattr(tts, 'validate_pack', lambda path, version, cancel: (str(Path(path).resolve()), version))
    w.verify_tts_pack(); pump_until(app, lambda: not w.jobs)
    assert w.tts_voice.isEnabled() and w.tts_preview_button.isEnabled()


def synth_fixture(monkeypatch):
    import lumen.tts as tts
    calls = []
    def synth(text, config, output, **kwargs):
        calls.append((text, config, output))
        Path(output).write_bytes(b'owned preview fixture')
        return SimpleNamespace(path=output, duration=7.125)
    monkeypatch.setattr(tts, 'synthesize', synth)
    return calls


def test_unverified_pack_disabled_and_changes_invalidate(window, app, tmp_path, monkeypatch):
    w = window
    assert not w.tts_voice.isEnabled() and not w.tts_preview_button.isEnabled() and not w.tts_button.isEnabled()
    verify(w, app, tmp_path, monkeypatch)
    w.tts_pack.setCurrentIndex(w.tts_pack.findData('v1.1'))
    assert w._tts_verified_pack is None and not w.tts_voice.isEnabled()
    assert w.tts_voice.count() == 103 and w.tts_voice.value() == 0
    assert 'af_maple' in w.tts_voice.currentText()
    w.verify_tts_pack(); pump_until(app, lambda: not w.jobs)
    assert w._tts_config().pack_version == 'v1.1'
    w.tts_model.setText(str(tmp_path / 'different'))
    assert not w.tts_preview_button.isEnabled() and w._tts_verified_pack is None


def test_filters_and_persistent_favorites_never_edit_project(window, app, tmp_path, monkeypatch, settings):
    w = window; verify(w, app, tmp_path, monkeypatch)
    revision = w.revision
    w.tts_voice.setValue(3); w.toggle_tts_favorite()
    assert settings.value('favorites') == '["v1.0:3"]'
    w.tts_favorites_only.setChecked(True)
    assert w.tts_voice.count() == 1 and w.tts_voice.value() == 3
    w.tts_language.setCurrentIndex(w.tts_language.findData('zh'))
    assert w.tts_voice.count() == 1
    w.tts_pack.setCurrentIndex(w.tts_pack.findData('v1.1'))
    assert w.tts_voice.count() == 0  # Old ID 3 never becomes a new-pack favorite.
    w.tts_favorites_only.setChecked(False)
    w.tts_origin.setCurrentIndex(w.tts_origin.findData('Chinese'))
    w.tts_language.setCurrentIndex(w.tts_language.findData('en'))
    assert w.tts_voice.count() == 100
    assert not w.project.media and not w.project.clips and w.revision == revision


def test_full_audition_is_owned_temporary_and_never_inserted(window, app, tmp_path, monkeypatch):
    w = window; verify(w, app, tmp_path, monkeypatch); calls = synth_fixture(monkeypatch)
    played = []; monkeypatch.setattr(w, 'play_tts_preview', lambda: played.append(w.tts_preview_path))
    text = 'Read every word. ' * 30 + '读完全部文字。'
    w.tts_preview_text.setPlainText(text); w.tts_voice.setValue(45)
    revision = w.revision; w.preview_tts_voice(); w.preview_tts_voice()
    pump_until(app, lambda: not w.jobs)
    assert len(calls) == 1 and calls[0][0] == text.strip()
    assert calls[0][1].voice_id == 45 and played == [w.tts_preview_path]
    path = w.tts_preview_path
    assert path.is_file() and path.parent == w.cache / 'voice-auditions'
    assert not w.project.media and not w.project.clips and w.pending_speech is None and w.revision == revision
    assert w.tts_preview_play_button.isEnabled()
    w.tts_voice.setValue(46)
    assert not path.exists() and w.tts_preview_path is None
    assert not w.tts_preview_play_button.isEnabled()


def test_audition_cancel_error_retry_and_close(window, app, tmp_path, monkeypatch):
    import lumen.tts as tts
    w = window; verify(w, app, tmp_path, monkeypatch)
    started = threading.Event()
    def slow(*args, cancel, **kwargs):
        started.set(); cancel.wait(3); raise RuntimeError('Canceled')
    monkeypatch.setattr(tts, 'synthesize', slow)
    w.preview_tts_voice(); assert started.wait(2); w.stop_tts_preview()
    pump_until(app, lambda: not w.jobs)
    assert 'canceled' in w.tts_preview_status.text() and not w.test_warnings
    assert not list((w.cache / 'voice-auditions').glob('*.wav'))
    monkeypatch.setattr(tts, 'synthesize', lambda *a, **k: (_ for _ in ()).throw(ValueError('bad local pack')))
    w.preview_tts_voice(); pump_until(app, lambda: not w.jobs)
    assert 'bad local pack' in w.test_warnings[-1] and w.tts_preview_button.isEnabled()
    synth_fixture(monkeypatch); monkeypatch.setattr(w, 'play_tts_preview', lambda: None)
    w.preview_tts_voice(); pump_until(app, lambda: not w.jobs)
    path = w.tts_preview_path; assert path.exists()
    cache = w.cache; w.close(); app.processEvents()
    assert not path.exists() and not cache.exists()


def test_failed_or_canceled_verification_keeps_voices_disabled(window, app, tmp_path, monkeypatch):
    import lumen.tts as tts
    w = window; w.tts_model.setText(str(tmp_path))
    monkeypatch.setattr(tts, 'validate_pack', lambda *a, **k: (_ for _ in ()).throw(ValueError('wrong fingerprint')))
    w.verify_tts_pack(); pump_until(app, lambda: not w.jobs)
    assert not w.tts_voice.isEnabled() and not w.tts_preview_button.isEnabled()
    assert 'wrong fingerprint' in w.test_warnings[-1]


def test_other_local_inference_disables_audition_and_generation(window, app, tmp_path, monkeypatch):
    w = window; verify(w, app, tmp_path, monkeypatch)
    for key in ('assistant_job', 'semantic_job', 'asr_job'):
        setattr(w, key, object()); w.update_tts_controls()
        assert not w.tts_preview_button.isEnabled() and not w.tts_button.isEnabled()
        setattr(w, key, None); w.update_tts_controls()
        assert w.tts_preview_button.isEnabled()


def test_changed_pack_while_auditioning_cannot_play_stale_result(window, app, tmp_path, monkeypatch):
    import lumen.tts as tts
    w=window; verify(w,app,tmp_path,monkeypatch)
    gate=threading.Event(); played=[]
    monkeypatch.setattr(w,'play_tts_preview',lambda:played.append(True))
    def slow(text,config,output,**kwargs):
        gate.wait(3);Path(output).write_bytes(b'fixture')
        return SimpleNamespace(path=output,duration=1)
    monkeypatch.setattr(tts,'synthesize',slow)
    w.preview_tts_voice();w.tts_pack.setCurrentIndex(w.tts_pack.findData('v1.1'));gate.set()
    pump_until(app,lambda:not w.jobs)
    assert not played and w.tts_preview_path is None
    assert not list((w.cache/'voice-auditions').glob('*.wav'))
    assert not w.tts_preview_button.isEnabled()


def test_favorites_reload_from_local_settings(window, app, tmp_path, monkeypatch):
    from lumen.ui import MainWindow
    w=window;verify(w,app,tmp_path,monkeypatch);w.tts_voice.setValue(45);w.toggle_tts_favorite()
    other=MainWindow()
    try:
        assert 'v1.0:45' in other.tts_favorites
        assert 'v1.1:45' not in other.tts_favorites
        assert not other.tts_voice.isEnabled()  # Favorites never imply installed/verified assets.
    finally:other.dirty=False;other.close();app.processEvents()


def test_audition_text_and_speed_changes_discard_owned_cache(window,app,tmp_path,monkeypatch):
    w=window;verify(w,app,tmp_path,monkeypatch);synth_fixture(monkeypatch)
    monkeypatch.setattr(w,'play_tts_preview',lambda:None)
    for change in (lambda:w.tts_speed.setValue(1.25),lambda:w.tts_preview_text.setPlainText('New complete preview text')):
        w.preview_tts_voice();pump_until(app,lambda:not w.jobs)
        path=w.tts_preview_path;assert path.exists()
        change();assert not path.exists() and w.tts_preview_path is None
        assert not w.tts_preview_play_button.isEnabled()


def test_missing_audio_device_is_explicit_and_does_not_insert(window,app,tmp_path,monkeypatch):
    import lumen.ui_captions as module
    w=window;verify(w,app,tmp_path,monkeypatch);synth_fixture(monkeypatch)
    monkeypatch.setattr(module.QMediaDevices,'audioOutputs',lambda:[])
    w.preview_tts_voice();pump_until(app,lambda:not w.jobs)
    assert w.tts_preview_path.exists() and 'no local audio output device' in w.tts_preview_status.text()
    assert not w.project.media and not w.project.clips

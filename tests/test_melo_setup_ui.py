"""Optional Melo selection/setup regressions; native inference is tested elsewhere."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFileDialog

from test_ui import app, window, pump_until
from lumen import component_setup as setup
from lumen.i18n import set_locale, tr_error
from lumen.ui_component_setup import ComponentSetupDialog, restore_component_preferences
from lumen.voice_catalog import get_pack

MELO = 'melo-zh_en-v1'


@pytest.fixture(autouse=True)
def isolated_preferences(tmp_path, monkeypatch):
    import lumen.ui_captions as captions
    import lumen.ui_component_setup as components
    settings = QSettings(str(tmp_path / 'favorites.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(captions, 'QSettings', lambda *a: settings)
    monkeypatch.setattr(components, 'preferences_path', lambda: tmp_path / 'no-saved-setup.json')
    return settings


def select_melo(w):
    w.tts_pack.setCurrentIndex(w.tts_pack.findData(MELO))


def verify(w, app, tmp_path, monkeypatch):
    import lumen.tts as tts
    w.tts_model.setText(str(tmp_path))
    monkeypatch.setattr(tts, 'validate_pack', lambda path, version, cancel: (str(Path(path).resolve()), version))
    w.verify_tts_pack(); pump_until(app, lambda: not w.jobs)
    assert w._tts_verified_pack == w._tts_pack_key()


def synth_fixture(monkeypatch):
    import lumen.tts as tts
    calls = []
    def synth(text, config, output, **kwargs):
        calls.append((text, config, output))
        Path(output).write_bytes(b'owned mock audition')
        return SimpleNamespace(path=output, duration=1)
    monkeypatch.setattr(tts, 'synthesize', synth)
    return calls


def test_optional_pack_defaults_and_localized_notices(window, app):
    w = window
    assert w.tts_pack.currentData() == 'v1.0'
    assert 'Kokoro v1.0' in w.tts_pack.currentText()
    assert w.tts_pack.findData('v1.1') >= 0
    select_melo(w)
    assert 'Melo Chinese-English' in w.tts_pack.currentText()
    assert 'experimental' in w.tts_pack.currentText()
    assert w.tts_voice.count() == 1 and w.tts_voice.currentData() == 0
    assert 'Melo Chinese-English' in w.tts_voice.currentText()
    assert '300' in w.tts_pack_info.text() and '44.1' in w.tts_pack_info.text()
    assert 'GiB' in w.tts_pack_info.text() and 'split long' in w.tts_pack_info.text()
    assert 'not certified' in w.tts_pack_info.text()
    assert not w.tts_voice.isEnabled() and not w.tts_preview_button.isEnabled()
    text = w.tts_preview_text.toPlainText()
    set_locale('zh-CN'); app.processEvents()
    assert '实验性' in w.tts_pack.currentText() and '300' in w.tts_pack_info.text()
    assert 'GiB' in w.tts_pack_info.text() and '建议分段' in w.tts_pack_info.text()
    assert '不会自动改写文本' in w.tts_pack_info.text()
    assert w.tts_pack.currentData() == MELO and w.tts_preview_text.toPlainText() == text
    assert '中文 + 英文' in w.tts_voice.currentText()


def test_melo_switch_clears_verified_audition_and_uses_actual_catalog(window, app, tmp_path, monkeypatch):
    w = window; verify(w, app, tmp_path, monkeypatch)
    synth_fixture(monkeypatch); monkeypatch.setattr(w, 'play_tts_preview', lambda: None)
    w.tts_voice.setValue(45)
    w.preview_tts_voice(); pump_until(app, lambda: not w.jobs)
    path = w.tts_preview_path; assert path.exists()
    select_melo(w)
    assert not path.exists() and w.tts_preview_path is None and w._tts_verified_pack is None
    assert w.tts_voice.currentData() == 0 and w.tts_voice.count() == 1
    assert not w.tts_preview_play_button.isEnabled()
    verify(w, app, tmp_path, monkeypatch)
    assert w._tts_config().pack_version == MELO and w._tts_config().voice_id == 0
    assert w._tts_config().threads == 2
    assert w.tts_pause.isEnabled()
    assert 'Melo Chinese-English' in w.tts_pack_status.text() and 'Kokoro' not in w.tts_pack_status.text()
    for language in ('en', 'zh'):
        w.tts_language.setCurrentIndex(w.tts_language.findData(language))
        assert w.tts_voice.count() == 1 and w.tts_voice.currentData() == 0


def test_melo_favorites_are_namespaced_and_preserve_legacy(window, app, tmp_path, monkeypatch, isolated_preferences):
    w = window; verify(w, app, tmp_path, monkeypatch)
    w.tts_voice.setValue(45); w.toggle_tts_favorite()
    select_melo(w); verify(w, app, tmp_path, monkeypatch)
    w.toggle_tts_favorite()
    assert set(json.loads(isolated_preferences.value('favorites'))) == {'v1.0:45', MELO + ':0'}
    w.tts_favorites_only.setChecked(True)
    assert w.tts_voice.count() == 1 and w.tts_voice.currentData() == 0
    from lumen.ui import MainWindow
    other = MainWindow()
    try:
        assert other.tts_favorites == {'v1.0:45', MELO + ':0'}
        assert other.tts_pack.currentData() == 'v1.0'  # Favorites never select a backend.
        select_melo(other); other.tts_favorites_only.setChecked(True)
        assert other.tts_voice.count() == 1 and not other.tts_voice.isEnabled()
    finally:
        other.dirty = False; other.close()
    w.tts_pack.setCurrentIndex(w.tts_pack.findData('v1.1'))
    assert w.tts_voice.count() == 0


def test_melo_limit_blocks_audition_and_save_without_rewriting(window, app, tmp_path, monkeypatch):
    w = window; select_melo(w); verify(w, app, tmp_path, monkeypatch)
    calls = synth_fixture(monkeypatch)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: pytest.fail('opened save before text validation'))
    text = '你' * 301
    w.tts_preview_text.setPlainText(text)
    w.preview_tts_voice()
    assert not calls and not w.jobs and w.tts_preview_text.toPlainText() == text
    assert '300' in w.test_warnings[-1] and 'Nothing was truncated' in w.test_warnings[-1]
    cue = w.editor.add_caption(0, 30, text); w.changed(); w.select_caption(cue.id)
    w.tts_track.setCurrentIndex(1); revision = w.revision
    set_locale('zh-CN')
    w.speak_selected()
    assert not calls and not w.jobs and w.revision == revision
    assert '300' in w.test_warnings[-1] and '没有截断' in w.test_warnings[-1]
    assert w.caption_text.toPlainText() == text and w.project.captions[0].text == text
    assert not w.project.media and not w.project.clips


def test_melo_300_character_audition_preserves_full_text_and_pause(window, app, tmp_path, monkeypatch):
    w = window; select_melo(w); verify(w, app, tmp_path, monkeypatch)
    calls = synth_fixture(monkeypatch); monkeypatch.setattr(w, 'play_tts_preview', lambda: None)
    text = '你' * 300
    w.tts_preview_text.setPlainText(text); w.tts_pause.setValue(80)
    revision = w.revision
    w.preview_tts_voice(); w.preview_tts_voice(); pump_until(app, lambda: not w.jobs)
    assert len(calls) == 1 and calls[0][0] == text and calls[0][1].silence_scale == .8
    assert not w.project.media and not w.project.clips and w.revision == revision
    path = w.tts_preview_path; assert path.exists()
    w.tts_pause.setValue(60)
    assert not path.exists() and w.tts_preview_path is None


def test_pack_switch_away_and_back_rejects_stale_verification(window, app, tmp_path, monkeypatch):
    import lumen.tts as tts
    w = window; select_melo(w); w.tts_model.setText(str(tmp_path))
    started = threading.Event(); release = threading.Event()
    def slow(path, version, cancel):
        started.set(); release.wait(3)
        return str(Path(path).resolve()), version
    monkeypatch.setattr(tts, 'validate_pack', slow)
    w.verify_tts_pack(); assert started.wait(2)
    w.tts_pack.setCurrentIndex(w.tts_pack.findData('v1.0')); select_melo(w)
    release.set(); pump_until(app, lambda: not w.jobs)
    assert w._tts_verified_pack is None and not w.tts_voice.isEnabled()
    assert 'Select a folder' in w.tts_pack_status.text()
    verify(w, app, tmp_path, monkeypatch)
    assert w.tts_voice.isEnabled()


def test_melo_cancel_and_retry_share_existing_audition_controls(window, app, tmp_path, monkeypatch):
    import lumen.tts as tts
    w = window; select_melo(w); verify(w, app, tmp_path, monkeypatch)
    started = threading.Event()
    def slow(*args, cancel, **kwargs):
        started.set(); cancel.wait(3); raise RuntimeError('Canceled')
    monkeypatch.setattr(tts, 'synthesize', slow)
    w.preview_tts_voice(); assert started.wait(2); w.stop_tts_preview()
    pump_until(app, lambda: not w.jobs)
    assert not w.test_warnings and w.tts_preview_path is None
    assert 'canceled' in w.tts_preview_status.text()
    calls = synth_fixture(monkeypatch); monkeypatch.setattr(w, 'play_tts_preview', lambda: None)
    w.preview_tts_voice(); pump_until(app, lambda: not w.jobs)
    assert len(calls) == 1 and w.tts_preview_path.exists()
    assert not w.project.media and not w.project.clips


def test_setup_melo_save_restore_preserves_schema_and_requires_verification(window, app, tmp_path, monkeypatch):
    w = window; verify(w, app, tmp_path, monkeypatch)
    revision = w.revision
    d = ComponentSetupDialog(w, path=tmp_path / 'components.json')
    d.pack.setCurrentIndex(d.pack.findData(MELO)); d.fields['tts_model_dir'].setText(str(tmp_path / 'Melo 中文'))
    assert 'Melo Chinese-English' in d.pack.currentText() and '300' in d.pack_info.text()
    d.apply_paths()
    assert w.tts_pack.currentData() == MELO and w._tts_verified_pack is None
    assert w.revision == revision and not w.project.media
    data = json.loads(d.path.read_text())
    assert data['version'] == 1 and data['settings']['tts_pack_version'] == MELO
    assert 'Melo 中文' in setup.load_preferences(d.path)['tts_model_dir']
    w.tts_pack.setCurrentIndex(w.tts_pack.findData('v1.0'))
    restore_component_preferences(w, d.path)
    assert w.tts_pack.currentData() == MELO and not w.tts_voice.isEnabled()
    set_locale('zh-CN'); app.processEvents()
    assert '实验性' in d.pack_info.text()
    d.reject()


@pytest.mark.parametrize('saved', [{}, {'tts_pack_version': 'v1.0'}, {'tts_pack_version': 'v1.1'}])
def test_legacy_preferences_keep_kokoro_selection(window, tmp_path, saved):
    path = tmp_path / 'legacy.json'
    path.write_text(json.dumps({'version': 1, 'settings': saved}))
    restore_component_preferences(window, path)
    assert window.tts_pack.currentData() == saved.get('tts_pack_version', 'v1.0')
    assert not window.tts_voice.isEnabled()


def test_melo_metadata_checks_only_actual_assets_and_all_pinned_hashes(tmp_path, monkeypatch):
    pack = get_pack(MELO)
    for name in pack.required_files:
        (tmp_path / name).write_bytes(('fixture ' + name).encode())
    hashes = {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in pack.required_files}
    mock_pack = replace(pack, model_sha256=hashes['model.onnx'], voices_sha256=hashes['model.onnx'], asset_sha256=tuple(hashes.items()))
    monkeypatch.setattr(setup, 'get_pack', lambda version: mock_pack)
    result = setup.diagnose_components({'tts_model_dir': str(tmp_path), 'tts_pack_version': MELO}, verify_hashes=True)
    checks = {c.key: c for c in result.checks}
    assert not {'voices.bin', 'espeak-ng-data', 'dict'} & checks.keys()
    assert all(checks[name].status == 'hash_verified' for name in pack.required_files)
    assert result.inference_tested is False
    (tmp_path / 'tokens.txt').write_bytes(b'changed tokens')
    checks = {c.key: c for c in setup.diagnose_components({'tts_model_dir': str(tmp_path), 'tts_pack_version': MELO}, verify_hashes=True).checks}
    assert checks['tokens.txt'].status == 'invalid'


def test_melo_missing_file_and_symlink_are_diagnostics_not_readiness(tmp_path):
    settings = {'tts_model_dir': str(tmp_path), 'tts_pack_version': MELO}
    checks = {c.key: c for c in setup.diagnose_components(settings).checks}
    assert all(checks[name].status == 'missing' for name in get_pack(MELO).required_files)
    original = tmp_path / 'original'; original.write_bytes(b'fixture')
    try:
        (tmp_path / 'lexicon.txt').symlink_to(original)
    except OSError:
        pytest.skip('symlinks unavailable')
    checks = {c.key: c for c in setup.diagnose_components(settings).checks}
    assert checks['lexicon.txt'].status == 'invalid'


def test_melo_setup_switch_discards_pending_diagnostic_report(window, app, tmp_path):
    started = threading.Event(); release = threading.Event()
    def slow(*args, **kwargs):
        started.set(); release.wait(3)
        return SimpleNamespace(checks=())
    d = ComponentSetupDialog(window, path=tmp_path / 'setup.json', diagnose=slow)
    d.start_diagnostics(); assert started.wait(2)
    cancel = d._cancel
    d.pack.setCurrentIndex(d.pack.findData(MELO))
    assert cancel.is_set()
    release.set(); pump_until(app, lambda: not window.jobs)
    assert d._report is None and not d.results.toPlainText()
    assert window.tts_pack.currentData() == 'v1.0'
    d.reject(); assert not d.path.exists()


def test_melo_errors_translate_without_changing_pack_identifiers():
    set_locale('zh-CN')
    examples = [
        ('Melo Chinese-English accepts at most 300 characters per take. Split the reading text explicitly before generating.', '300'),
        ('Incomplete Melo Chinese-English local model pack. See docs/LOCAL_TTS.md; no automatic download is performed.', 'Melo Chinese-English'),
        ('Engine returned invalid mono 44100 Hz PCM audio.', '44100'),
        ('Choose an explicitly supported local speech pack version: v1.0, v1.1, melo-zh_en-v1.', MELO),
    ]
    for source, untouched in examples:
        rendered = str(tr_error(source))
        assert untouched in rendered and '技术错误详情' not in rendered and rendered != source


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_melo_notices_fit_small_setup_and_caption_resize_history(window, app, tmp_path, locale):
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtWidgets import QScrollArea
    from test_component_setup_layout import settle, assert_no_top_level_overlap
    set_locale(locale)
    d = ComponentSetupDialog(window, path=tmp_path / 'setup.json')
    d.pack.setCurrentIndex(d.pack.findData(MELO))
    d.show(); settle(app)
    d._fit_available_screen(QRect(0, 0, 683, 384)); settle(app)
    assert d.width() <= 683 and d.height() <= 384
    assert_no_top_level_overlap(d)
    scroll = d.findChild(QScrollArea)
    assert scroll.horizontalScrollBar().maximum() == 0
    select_melo(window)
    note = window.tts_pack_info
    for version in (MELO, 'v1.0', MELO):
        window.tts_pack.setCurrentIndex(window.tts_pack.findData(version))
        for width in (650, 280, 510):
            note.resize(width, note.height()); note._fit_wrapped_height(); app.processEvents()
            flags = int(note.alignment()) | int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs)
            needed = note.fontMetrics().boundingRect(QRect(0, 0, note.contentsRect().width(), 100000), flags, note.text()).height()
            assert note.minimumHeight() >= needed
    d.reject()

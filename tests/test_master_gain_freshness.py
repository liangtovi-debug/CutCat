"""Master changes invalidate complete-mix evidence and actual preview keys."""
from pathlib import Path
from copy import deepcopy

import pytest

from lumen import audio_analysis as aa
from lumen.model import Editor, Project, Clip, Media
from lumen.render import RenderEngine


def test_master_settings_fingerprint_is_pure_and_undo_redo_stable(monkeypatch):
    editor = Editor(Project())
    def no_io(*args, **kwargs):
        pytest.fail('Master settings fingerprint performed I/O')
    with monkeypatch.context() as patch:
        for target, name in ((aa, '_local_path'), (aa, '_state'), (aa, 'ffmpeg_binary'),
                             (aa.shutil, 'which'), (aa.os, 'open'), (Path, 'stat'),
                             (Path, 'lstat'), (Path, 'resolve')):
            patch.setattr(target, name, no_io)
        original = aa.audio_settings_fingerprint(editor.project)
        editor.set_master_gain(-6)
        gained = aa.audio_settings_fingerprint(editor.project)
        assert gained != original
        editor.undo()
        assert aa.audio_settings_fingerprint(editor.project) == original
        editor.redo()
        assert aa.audio_settings_fingerprint(editor.project) == gained
        editor.set_master_gain(0)
        assert aa.audio_settings_fingerprint(editor.project) == original
        editor.set_master_gain(-0.0)
        assert aa.audio_settings_fingerprint(editor.project) == original


def test_actual_preview_cache_keys_change_and_restore_with_master_gain(tmp_path, monkeypatch):
    # Exercise the public cache method, replacing only its renderer.
    source = tmp_path / 'source.wav'
    source.write_bytes(b'owned cache-key fixture')
    editor = Editor(Project(media=[Media('m', str(source), 'audio', 1,
                                         has_video=False, has_audio=True)],
                            clips=[Clip('c', 'm', 2, 0, 0, 30)]))
    engine = RenderEngine()
    calls = []
    def render(project, target, **kwargs):
        calls.append(deepcopy(project))
        target.write_bytes(b'cache artifact')
    monkeypatch.setattr(engine, 'render', render)
    try:
        original = engine.preview(editor.project)
        assert engine.preview(editor.project) == original and len(calls) == 1
        editor.set_master_gain(-6)
        gained = engine.preview(editor.project)
        assert gained != original and len(calls) == 2
        assert calls[-1].master_gain_db == -6
        editor.undo()
        assert engine.preview(editor.project) == original and len(calls) == 2
        editor.redo()
        assert engine.preview(editor.project) == gained and len(calls) == 2
    finally:
        engine.close()


def test_analysis_reports_post_master_gain_and_rejects_previous_result(tmp_path):
    import math
    import numpy as np
    from test_audio_analysis import wav, tone, project
    source = wav(tmp_path / 'post-master.wav', tone(seconds=2, amplitude=.2))
    editor = Editor(project(source, 2))
    original = aa.analyze_audio(editor.project)
    editor.set_master_gain(-6)
    assert not aa.result_is_current(editor.project, original)
    gained = aa.analyze_audio(editor.project)
    ratio = 10 ** (-6 / 20)
    assert gained.sample_count == original.sample_count == 96000
    assert gained.sample_peaks == pytest.approx(np.array(original.sample_peaks) * ratio, abs=3e-8)
    assert gained.estimated_true_peaks == pytest.approx(np.array(original.estimated_true_peaks) * ratio, abs=1e-7)
    assert gained.integrated_lufs == pytest.approx(original.integrated_lufs - 6, abs=.11)
    assert gained.fingerprint != original.fingerprint
    assert gained.context_fingerprint != original.context_fingerprint
    assert gained.source_hashes == original.source_hashes
    assert aa.result_is_current(editor.project, gained)
    editor.undo()
    assert not aa.result_is_current(editor.project, gained)
    assert aa.result_is_current(editor.project, original)
    editor.redo()
    assert aa.result_is_current(editor.project, gained)


@pytest.mark.parametrize('text', [
    'master output gain must be finite and in -96..24',
    'Version 34 projects require master output gain',
    'Legacy projects cannot contain master output gain',
])
def test_master_gain_backend_errors_translate_without_raw_english(text):
    from lumen.i18n import get_locale, set_locale
    from lumen.i18n_errors import tr_error
    old = get_locale()
    try:
        set_locale('zh-CN')
        assert tr_error(text) != text
        assert '主输出增益' in tr_error(text)
        set_locale('en')
        assert tr_error(text) == text
    finally:
        set_locale(old)

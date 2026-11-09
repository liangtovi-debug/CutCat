"""Pause controls preserve transcript and invalidate only the audition cache."""
from test_ui import app, window, pump_until
from test_voice_browser_ui import verify, synth_fixture
from lumen.i18n import tr,set_locale


def test_pause_control_passed_without_text_or_project_mutation(window, app, tmp_path, monkeypatch):
    w=window;verify(w,app,tmp_path,monkeypatch);calls=synth_fixture(monkeypatch)
    monkeypatch.setattr(w,'play_tts_preview',lambda:None)
    text='2026年10月18日，价格3.14元。AI 与 U.S.A. 保持原文。\n第二句！'
    w.tts_preview_text.setPlainText(text);revision=w.revision
    assert w.tts_pause.value()==20 and w._tts_config().silence_scale==.2
    w.preview_tts_voice();pump_until(app,lambda:not w.jobs)
    old=w.tts_preview_path;assert old.is_file()
    w.tts_pause.setValue(65)
    assert not old.exists() and w.tts_preview_path is None
    assert w.tts_preview_text.toPlainText()==text and w.revision==revision
    assert w._tts_config().silence_scale==.65
    w.preview_tts_voice();pump_until(app,lambda:not w.jobs)
    assert calls[-1][0]==text and calls[-1][1].silence_scale==.65
    assert not w.project.media and not w.project.clips


def test_pause_bounds_and_chinese_label(window):
    w=window
    assert (w.tts_pause.minimum(),w.tts_pause.maximum())==(20,100)
    w.tts_pause.setValue(150);assert w.tts_pause.value()==100
    set_locale('zh_CN')
    try:
        assert tr('Pause preservation')=='停顿保留'
    finally:set_locale('en')

from dataclasses import asdict
from PySide6.QtWidgets import QMessageBox
from test_ui import app,window,pump_until
from test_voice_browser_ui import verify,synth_fixture
from lumen.i18n import set_locale


def test_review_cancel_preserves_audition_and_project(window,app,tmp_path,monkeypatch):
    w=window;verify(w,app,tmp_path,monkeypatch);calls=synth_fixture(monkeypatch)
    text='他说：“导出MP4！”';w.tts_preview_text.setPlainText(text)
    original=asdict(w.project);revision=w.revision;seen=[]
    def question(*args):seen.append(args);return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox,'question',question)
    w.preview_tts_voice()
    assert seen and not calls and not w.jobs
    assert asdict(w.project)==original and w.revision==revision and w.tts_preview_text.toPlainText()==text
    assert seen[0][-1]==QMessageBox.StandardButton.No


def test_review_accept_sends_original_text(window,app,tmp_path,monkeypatch):
    w=window;verify(w,app,tmp_path,monkeypatch);calls=synth_fixture(monkeypatch)
    monkeypatch.setattr(w,'play_tts_preview',lambda:None)
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Yes)
    text='他说：“导出MP4！”';w.tts_preview_text.setPlainText(text);original=asdict(w.project)
    w.preview_tts_voice();pump_until(app,lambda:not w.jobs)
    assert calls[-1][0]==text and w.tts_preview_path.is_file()
    assert asdict(w.project)==original


def test_review_warning_localized_without_text_change(window,monkeypatch):
    seen=[];monkeypatch.setattr(QMessageBox,'question',lambda *a:seen.append(a) or QMessageBox.StandardButton.No)
    set_locale('zh-CN')
    try:
        assert not window.review_tts_text('v1.2.3“测试”')
        assert '检查朗读发音'==seen[0][1] and '不会自动修改文本或字幕' in seen[0][2]
    finally:set_locale('en')

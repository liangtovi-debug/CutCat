"""Exact retiming controls and independent timeline anchors (headless Qt)."""
from copy import deepcopy
from fractions import Fraction
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from lumen.i18n import set_locale
from lumen.model import Caption, Clip, Editor, Media, Overlay, OverlayKeyframe, Project
from lumen.timing import ClipTimeMap
from lumen.ui import MainWindow


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(str(args[2])))
    monkeypatch.setattr(QMessageBox, 'information', lambda *_: None)
    settings = QSettings(str(tmp_path / 'workspace.ini'), QSettings.Format.IniFormat)
    settings.setValue('mode', 'professional')
    w = MainWindow(workspace_settings=settings)
    source = tmp_path / 'source.mp4'; source.write_bytes(b'mocked media')
    media = Media('m', str(source), 'Media', 10, 640, 360, True, True)
    clip = Clip('c', 'm', 0, 10, 0, 31)
    w.editor = Editor(Project(media=[media], clips=[clip]))
    w.refresh(); w.select_clip('c'); w.test_warnings = warnings
    yield w
    w.cancel_jobs()
    for job in list(w.jobs): assert job.wait(5000)
    w.dirty = False; w.closing = True; w.close(); app.processEvents()


def test_speed_is_explicit_exact_and_undoable(window):
    w = window; before = deepcopy(w.project); revision = w.revision
    w.speed_edit.setText('1.25')
    assert w.project == before and w.revision == revision
    assert '31 to 25 frames' in w.speed_duration_note.text()
    assert w.apply_speed_button.isEnabled()
    w.apply_speed()
    c = w.selected_clip()
    assert (c.speed, c.source_in, c.source_out, c.duration, c.start) == (Fraction(5, 4), 0, 31, 25, 10)
    after = deepcopy(w.project)
    assert w.revision == revision + 1
    w.undo(); assert w.project == before
    w.redo(); assert w.project == after


def test_fractional_source_refresh_move_and_grade_preserve_partial_tail(window):
    w = window
    w.editor.trim_clip_source('c', Fraction(1, 2), 31)
    w.editor.set_clip_speed('c', Fraction(3, 2)); w.changed()
    exact = w.selected_clip().timing; revision = w.revision
    w.refresh_inspector()
    assert w.in_spin.text() == '1/2' and w.in_spin.value() == Fraction(1, 2)
    assert w.revision == revision
    w.start_spin.setValue(50); w.apply_timing()
    assert w.selected_clip().timing == exact and w.selected_clip().start == 50
    w.grade_spins['contrast'].setValue(1.1)
    assert w.selected_clip().timing == exact
    state = deepcopy(w.project); revision = w.revision
    w.apply_timing()
    assert w.project == state and w.revision == revision
    w.undo(); assert w.selected_clip().timing == exact
    w.undo(); assert w.selected_clip().start == 10 and w.selected_clip().timing == exact


def test_fractional_source_manual_duration_consumes_exact_source_units(window):
    w = window; w.editor.set_clip_speed('c', Fraction(3, 2)); w.changed()
    w.in_spin.setText('0.5'); w.duration_spin.setValue(10); w.apply_timing()
    assert w.selected_clip().timing == ClipTimeMap(Fraction(1, 2), Fraction(31, 2), Fraction(3, 2))
    before = deepcopy(w.project); revision = w.revision
    w.in_spin.setText('1/1000001'); w.apply_timing()
    assert w.project == before and w.revision == revision
    assert w.in_spin.text() == '1/2' and w.test_warnings


def test_slowdown_overlap_is_atomic_and_never_ripples(window):
    w = window
    w.project.clips.append(Clip('next', 'm', 0, 45, 40, 15)); w.project.validate(); w.refresh()
    before = deepcopy(w.project); undo = deepcopy(w.editor._undo); revision = w.revision
    w.speed_edit.setText('0.5'); assert '31 to 62 frames' in w.speed_duration_note.text()
    w.apply_speed()
    assert w.project == before and w.editor._undo == undo and w.revision == revision
    assert 'overlap' in w.test_warnings[-1]
    assert w.project.clips[1].start == 45


@pytest.mark.parametrize('speed', ['0', '0.249', '4.01', 'NaN', '1/0', '0.3333333333333'])
def test_invalid_speed_does_not_mutate(window, speed):
    before = deepcopy(window.project); revision = window.revision
    window.speed_edit.setText(speed)
    assert not window.apply_speed_button.isEnabled()
    window.apply_speed()
    assert window.project == before and window.revision == revision
    assert window.test_warnings


def test_anchors_and_audition_are_independent(window, tmp_path):
    w = window
    w.project.captions = [Caption('caption', 12, 10, 'Keep this caption')]
    w.project.overlays = [Overlay('sticker', str(tmp_path / 'sticker.png'), 'a' * 64, 11, 25,
                                 keyframes=[OverlayKeyframe(0), OverlayKeyframe(20, 12, 15)])]
    w.project.media.append(Media('voice', str(tmp_path / 'voice.wav'), 'Voice', 1, has_video=False, has_audio=True))
    w.project.clips.append(Clip('voice', 'voice', 2, 12, 0, 30)); w.project.validate(); w.changed()
    anchors = deepcopy((w.project.captions, w.project.overlays, w.project.clips[1]))
    synthesis_speed = w.tts_speed.value(); audition_request = w._tts_preview_request
    audition_text = w.tts_preview_text.toPlainText()
    w.speed_edit.setText('2'); w.apply_speed()
    assert (w.project.captions, w.project.overlays, w.project.clips[1]) == anchors
    assert w.tts_speed.value() == synthesis_speed and w._tts_preview_request == audition_request
    assert w.tts_preview_text.toPlainText() == audition_text


def test_locale_switch_preserves_exact_unsaved_speed_and_timing(window):
    w = window
    w.editor.trim_clip_source('c', Fraction(1, 2), 31); w.changed()
    w.speed_edit.setText('1.25'); w.in_spin.setText('1/4')
    before = deepcopy(w.project); revision = w.revision
    set_locale('zh-CN')
    assert w.speed_edit.text() == '1.25' and w.in_spin.text() == '1/4'
    assert w.apply_speed_button.text() == '应用速度'
    assert '25' in w.speed_duration_note.text() and '时长' in w.speed_duration_note.text()
    assert '不联动移动' in w.speed_anchor_note.text()
    assert w.project == before and w.revision == revision
    set_locale('en')
    assert w.apply_speed_button.text() == 'Apply speed' and w.speed_edit.text() == '1.25'


def configure_asr(w, tmp_path, monkeypatch):
    import lumen.asr as asr
    runtime = tmp_path / 'whisper-cli'; runtime.write_bytes(b'test runtime')
    model = tmp_path / 'model.bin'; model.write_bytes(b'test model')
    w.asr_runtime.setText(str(runtime)); w.asr_model.setText(str(model))
    captured = {}
    w.start_job = lambda label, operation, success, kind: captured.update(operation=operation, success=success)
    def transcribe(source, config, **kwargs):
        captured.update(source=source, kwargs=kwargs)
        return [asr.TranscriptSegment(Fraction(1), Fraction(2), 'Retimed source cue')]
    monkeypatch.setattr(asr, 'transcribe', transcribe)
    return captured


def test_selected_asr_extracts_source_span_then_inverse_maps(window, tmp_path, monkeypatch):
    w = window; captured = configure_asr(w, tmp_path, monkeypatch)
    w.editor.trim_clip_source('c', 30, 150); w.editor.set_clip_speed('c', 2); w.changed()
    before = deepcopy(w.project)
    w.transcribe_selected(); result = captured['operation'](None, lambda _: None)
    assert captured['kwargs']['source_start'] == 1
    assert captured['kwargs']['source_end'] == 5  # Four source seconds, two output seconds.
    captured['success'](result)
    assert [(c.start, c.duration) for c in w.project.captions] == [(10, 15)]
    w.undo(); assert w.project == before


@pytest.mark.parametrize('change', ['speed', 'settings'])
def test_selected_asr_rejects_stale_speed_or_settings(window, tmp_path, monkeypatch, change):
    w = window; captured = configure_asr(w, tmp_path, monkeypatch)
    w.transcribe_selected(); result = captured['operation'](None, lambda _: None)
    if change == 'speed':
        w.editor.set_clip_speed('c', 2); w.changed()
    else:
        w.asr_language.setText('zh')
    captured['success'](result)
    assert not w.project.captions and w.test_warnings


def test_media_bin_asr_uses_whole_media_one_x(window, tmp_path, monkeypatch):
    w = window; captured = configure_asr(w, tmp_path, monkeypatch)
    w.editor.set_clip_speed('c', 2); w.changed()
    w.asr_source.setCurrentIndex(1); w.bin.setCurrentRow(0)
    w.transcribe_selected(); result = captured['operation'](None, lambda _: None)
    assert 'source_start' not in captured['kwargs'] and 'source_end' not in captured['kwargs']
    captured['success'](result)
    assert [(c.start, c.duration) for c in w.project.captions] == [(30, 30)]


@pytest.mark.parametrize('value,expected', [('3/2', '1.5'), ('5/4', '1.25'), ('1/4', '0.25'), ('4', '4'), ('2/3', '2/3'), ('999999/1000000', '0.999999')])
def test_exact_speed_formatting(value, expected):
    from lumen.ui import exact_speed_text
    assert exact_speed_text(Fraction(value)) == expected
    assert Fraction(exact_speed_text(Fraction(value))) == Fraction(value)


def test_current_speed_note_and_unchanged_apply_are_non_mutating(window):
    w = window; w.editor.set_clip_speed('c', Fraction(3, 2)); w.changed()
    assert w.speed_edit.text() == '1.5'
    assert 'Current speed: 1.5×' in w.speed_duration_note.text()
    revision = w.revision; before = deepcopy(w.project); undo = deepcopy(w.editor._undo)
    assert not w.apply_speed_button.isEnabled()
    w.apply_speed()
    assert w.project == before and w.revision == revision and w.editor._undo == undo


def test_generated_speech_playback_preserves_immutable_synthesis_snapshot(window, tmp_path):
    from lumen.model import SpeechProvenance
    w = window
    provenance = SpeechProvenance(text='Recorded words', engine='kokoro-onnx', engine_version='1.0',
        model_sha256='a' * 64, voice_sha256='b' * 64, assets_sha256='c' * 64,
        voice_id='af_heart', speed=1.1, sample_rate=24000, created_utc='2026-10-15T20:00:00Z',
        source_caption_id='caption', source_start=12, source_duration=30, output_sha256='d' * 64)
    w.project.captions = [Caption('caption', 12, 30, 'Recorded words')]
    w.project.media.append(Media('speech', str(tmp_path / 'speech.wav'), 'Generated voice', 1.01,
                                has_video=False, has_audio=True, speech_provenance=provenance))
    w.project.clips.append(Clip('speech', 'speech', 2, 12, 0, 31)); w.project.validate()
    w.changed(); w.select_clip('speech'); before = deepcopy(w.project)
    synthesis_control = w.tts_speed.value(); audition_request = w._tts_preview_request
    w.speed_edit.setText('2'); w.apply_speed()
    c = w.selected_clip()
    assert (c.source_in, c.source_out, c.duration, c.start) == (0, 31, 16, 12)
    assert w.project.media[-1].speech_provenance == provenance
    assert w.project.media[-1].speech_provenance.speed == 1.1
    assert w.tts_speed.value() == synthesis_control and w._tts_preview_request == audition_request
    assert w.project.captions == before.captions
    w.undo(); assert w.project == before


def test_invalid_staged_speed_error_retranslates_without_state_changes(window):
    w = window
    w.speed_edit.setText('0.1'); w.in_spin.setText('1/2'); w.start_spin.setValue(123)
    assert w.speed_duration_note.text() == 'Speed must be between 1/4 and 4'
    before = deepcopy(w.project); undo = deepcopy(w.editor._undo); revision = w.revision
    for locale, note in [('zh-CN', '速度必须在 1/4 至 4 之间'), ('en', 'Speed must be between 1/4 and 4')]:
        set_locale(locale)
        assert w.speed_duration_note.text() == note
        assert w.speed_edit.text() == '0.1' and w.in_spin.text() == '1/2' and w.start_spin.value() == 123
        assert w.project == before and w.editor._undo == undo and w.revision == revision
        assert not w.apply_speed_button.isEnabled()

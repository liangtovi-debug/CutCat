"""Headless Qt integration checks; no claim of Windows/macOS native validation."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import time
from pathlib import Path

import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from PySide6.QtCore import Qt, QSettings
from lumen.model import Media
from lumen.ui import MainWindow


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch, tmp_path):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    # The existing advanced-control suite explicitly requests its workspace;
    # fresh-user defaults and transitions are covered in test_workspace_ui.
    settings = QSettings(str(tmp_path / 'workspace.ini'), QSettings.Format.IniFormat)
    settings.setValue('mode', 'professional')
    w = MainWindow(workspace_settings=settings)
    w.test_warnings = warnings
    yield w
    w.cancel_jobs()
    for job in list(w.jobs):
        assert job.wait(5000), 'Background work failed to stop'
    app.processEvents()
    w.dirty = False
    w.closing = True
    w.close()
    app.processEvents()


def populate(w, tmp_path):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'fixture media; mocked preview only')
    w.editor.add_media(Media('source', str(source), 'Sample scene', 10, 640, 360, True, True))
    w.refresh()
    w.bin.setCurrentRow(0)
    w.add_selected_media()
    return w.selected_clip()


def pump_until(app, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert condition()


def test_edit_split_color_audio_undo_and_redo(window, tmp_path):
    w = window
    clip = populate(w, tmp_path)
    assert clip.duration == 300 and w.inspector.isEnabled()
    revision = w.revision
    w.refresh_inspector()
    assert w.revision == revision  # Loading controls must never create edits.
    w.duration_spin.setValue(120)
    w.in_spin.setValue(30)
    w.apply_timing()
    assert (w.selected_clip().source_in, w.selected_clip().duration) == (30, 120)
    w.grade_spins['exposure'].setValue(.7)
    assert w.selected_clip().grade.exposure == .7
    w.gain.setValue(-6)
    w.fade_in.setValue(10)
    assert (w.selected_clip().gain_db, w.selected_clip().fade_in) == (-6, 10)
    w.seek_frame(60)
    w.split_selected()
    assert len(w.project.clips) == 2
    assert [c.duration for c in w.project.clips] == [60, 60]
    w.undo()
    assert len(w.project.clips) == 1
    w.redo()
    assert len(w.project.clips) == 2
    assert not w.test_warnings


def test_delete_clears_selection_and_clamps_playhead(window, tmp_path):
    w = window
    populate(w, tmp_path)
    w.seek_frame(299)
    w.duration_spin.setValue(30)
    w.apply_timing()
    assert w.timeline.playhead == 29
    w.delete_selected()
    assert w.selection == '' and w.selected_clip() is None
    assert w.timeline.playhead == 0
    assert not w.inspector.isEnabled()


def test_save_source_protection_and_roundtrip(window, tmp_path, monkeypatch):
    w = window
    populate(w, tmp_path)
    source = Path(w.project.media[0].path)
    w.project_path = source
    assert w.save_project() is False
    assert source.read_bytes().startswith(b'fixture media')
    path = tmp_path / 'edit.lumen'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(path), ''))
    assert w.save_project(True)
    assert path.exists() and not w.dirty
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(path), ''))
    w.open_project()
    assert len(w.project.clips) == 1 and w.project_path == path


def test_import_background_and_duplicate_filter(window, app, tmp_path, monkeypatch):
    import lumen.ui as ui
    source = tmp_path / 'clip.mp4'
    source.write_bytes(b'fixture')
    monkeypatch.setattr(ui, 'probe', lambda p: Media('m', str(source), 'Clip', 2, 640, 360, True, False))
    window.import_paths([str(source)])
    pump_until(app, lambda: not window.jobs)
    assert window.bin.count() == 1 and window.dirty
    window.import_paths([str(source)])
    assert not window.jobs and len(window.project.media) == 1


def test_preview_success_then_stale_result(window, app, tmp_path, monkeypatch):
    import lumen.ui as ui
    w = window
    populate(w, tmp_path)
    gate = threading.Event()
    def render(self, snapshot, target, **kwargs):
        assert gate.wait(3)
        return target
    monkeypatch.setattr(ui.RenderEngine, 'render', render)
    w.build_preview()
    gate.set()
    pump_until(app, lambda: not w.jobs)
    assert w.preview_revision == w.revision and w.play_button.isEnabled()
    gate.clear()
    w.build_preview()
    w.grade_spins['exposure'].setValue(1)
    gate.set()
    pump_until(app, lambda: not w.jobs)
    assert w.preview_revision != w.revision and not w.play_button.isEnabled()
    assert 'changed while rendering' in w.preview_label.text()


def test_cancel_and_close_wait_for_worker(window, app, monkeypatch):
    w = window
    started = threading.Event()
    results = []
    def operation(cancel, progress):
        started.set()
        cancel.wait(3)
        return 'must not deliver canceled result'
    w.start_job('Cancelable test', operation, results.append, 'preview')
    assert started.wait(2)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Yes)
    cache = w.cache
    w.close()
    assert w.closing
    pump_until(app, lambda: not w.jobs and not cache.exists())
    assert results == []


def test_relink_validates_replacement_and_resets_history(window, tmp_path, monkeypatch):
    import lumen.ui as ui
    w = window
    populate(w, tmp_path)
    Path(w.project.media[0].path).unlink()
    new = tmp_path / 'replacement.mp4'
    new.write_bytes(b'fixture')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(new), ''))
    monkeypatch.setattr(ui, 'probe', lambda p: Media('other', str(new), 'Replacement', 1, 640, 360, True, True))
    w.relink_dialog()
    assert w.project.media[0].path != str(new) and w.test_warnings
    monkeypatch.setattr(ui, 'probe', lambda p: Media('other', str(new), 'Replacement', 10, 640, 360, True, True))
    w.relink_dialog()
    assert w.project.media[0].path == str(new)
    assert not w.editor.can_undo


def test_invalid_timing_rolls_back_controls(window, tmp_path):
    w = window
    populate(w, tmp_path)
    revision = w.revision
    before = w.project.to_dict()
    w.in_spin.setValue(200)
    w.apply_timing()
    assert w.project.to_dict() == before
    assert w.in_spin.value() == 0 and w.duration_spin.value() == 300
    assert w.revision == revision and w.test_warnings


def test_export_refuses_existing_file(window, tmp_path, monkeypatch):
    monkeypatch.setattr('lumen.ui.choose_export_profile', lambda *_: 'h264_8bit')
    w = window
    populate(w, tmp_path)
    target = tmp_path / 'export.mp4'
    target.write_bytes(b'preserve existing export')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(target), ''))
    w.export_dialog()
    assert target.read_bytes() == b'preserve existing export'
    assert not w.jobs and w.test_warnings


def test_job_failure_restores_controls(window, app, tmp_path):
    w = window
    populate(w, tmp_path)
    def operation(cancel, progress):
        raise RuntimeError('Expected test failure')
    w.start_job('Test failure', operation, lambda _: None, 'preview')
    pump_until(app, lambda: not w.jobs)
    assert w.preview_job is None and w.preview_button.isEnabled()
    assert w.test_warnings == ['Expected test failure']


def test_software_viewer_paints_decoded_frame(app):
    from PySide6.QtGui import QImage, QColor
    from PySide6.QtMultimedia import QVideoFrame
    from lumen.ui import VideoViewer
    viewer = VideoViewer()
    viewer.resize(320, 240)
    image = QImage(160, 90, QImage.Format.Format_RGBA8888)
    image.fill(QColor('#12ab34'))
    viewer.set_frame(QVideoFrame(image))
    viewer.show()
    app.processEvents()
    rendered = viewer.grab().toImage()
    assert rendered.pixelColor(160, 120) == QColor('#12ab34')
    assert rendered.pixelColor(160, 5) == QColor('#0d1117')
    viewer.close()


def test_linear_wheel_and_curve_controls_are_real_undoable_edits(window, tmp_path):
    w = window
    populate(w, tmp_path)
    w.grade_spins['linear_exposure'].setValue(1.25)
    assert w.selected_clip().grade.linear_exposure == 1.25
    w.wheel_controls['gamma'].spins[1].setValue(1.2)
    assert w.selected_clip().grade.gamma == [1., 1.2, 1.]
    w.undo()
    assert w.selected_clip().grade.gamma == [1., 1., 1.]
    w.curve_choice.setCurrentIndex(1)
    assert w.curve_editor.add_point(.4, .7)
    assert w.selected_clip().grade.red_curve == [[0.,0.],[.4,.7],[1.,1.]]
    w.undo()
    assert w.selected_clip().grade.red_curve == [[0.,0.],[1.,1.]]
    assert not w.test_warnings


def test_wheel_gesture_stages_until_release_then_one_undo(window, app, tmp_path):
    from PySide6.QtTest import QTest
    from PySide6.QtCore import QPoint
    w=window; populate(w,tmp_path); w.show(); w.color_tabs.setCurrentIndex(1); app.processEvents()
    wheel=w.wheel_controls['lift'].wheel
    revision=w.revision; before=w.project.to_dict()
    QTest.mousePress(wheel, Qt.MouseButton.LeftButton, pos=QPoint(58,58))
    QTest.mouseMove(wheel, QPoint(90,40)); QTest.mouseMove(wheel,QPoint(95,45))
    assert w.project.to_dict()==before and w.revision==revision
    QTest.mouseRelease(wheel,Qt.MouseButton.LeftButton,pos=QPoint(95,45))
    assert w.revision==revision+1 and w.selected_clip().grade.lift!=[0,0,0]
    w.undo(); assert w.project.to_dict()==before


def test_curve_gesture_constrains_x_and_stages_undo(window, app, tmp_path):
    from PySide6.QtTest import QTest
    w=window; populate(w,tmp_path); w.show(); w.color_tabs.setCurrentIndex(2); app.processEvents()
    curve=w.curve_editor
    assert curve.add_point(.4,.5)
    assert not curve.add_point(.4,.8)
    assert not curve.remove_point(0)
    before=w.project.to_dict(); revision=w.revision
    QTest.mousePress(curve,Qt.MouseButton.LeftButton,pos=curve.to_screen([.4,.5]).toPoint())
    QTest.mouseMove(curve,curve.to_screen([1.2,3.]).toPoint())
    assert w.project.to_dict()==before
    QTest.mouseRelease(curve,Qt.MouseButton.LeftButton,pos=curve.to_screen([1.2,3.]).toPoint())
    points=w.selected_clip().grade.master_curve
    assert 0<points[1][0]<1 and points[1][1]==2
    assert w.revision==revision+1
    w.undo(); assert w.project.to_dict()==before


def test_before_after_renders_snapshot_and_preserves_history(window, app, tmp_path, monkeypatch):
    import lumen.ui as ui
    snapshots=[]
    def render(self,snapshot,target,**kwargs):
        snapshots.append(snapshot.to_dict()); target.write_bytes(b'cache fixture'); return target
    monkeypatch.setattr(ui.RenderEngine,'render',render)
    w=window; populate(w,tmp_path); w.grade_spins['linear_exposure'].setValue(1)
    before=w.project.to_dict(); revision=w.revision
    w.build_preview(); pump_until(app,lambda:not w.jobs)
    w.preview_mode_choice.setCurrentIndex(1); pump_until(app,lambda:not w.jobs)
    assert w.project.to_dict()==before and w.revision==revision
    assert all(not c['grade']['enabled'] for c in snapshots[-1]['clips'])
    assert w.loaded_preview_mode=='bypass' and w.play_button.isEnabled()
    w.preview_mode_choice.setCurrentIndex(0)
    assert w.loaded_preview_mode=='graded' and w.play_button.isEnabled()
    assert len(snapshots)==2  # Return uses the verified same-revision After cache.
    w.undo(); assert w.selected_clip().grade.linear_exposure==0


def test_preview_mode_switch_rejects_inflight_result(window, app, tmp_path, monkeypatch):
    import lumen.ui as ui
    gate=threading.Event(); calls=[]
    def render(self,snapshot,target,**kwargs):
        calls.append(all(not c.grade.enabled for c in snapshot.clips))
        if len(calls)==1: assert gate.wait(3)
        target.write_bytes(b'cache fixture'); return target
    monkeypatch.setattr(ui.RenderEngine,'render',render)
    w=window; populate(w,tmp_path); w.build_preview()
    pump_until(app,lambda:len(calls)==1)
    w.preview_mode_choice.setCurrentIndex(1)
    gate.set()
    pump_until(app,lambda:len(calls)==2 and not w.jobs)
    assert calls==[False,True]
    assert w.loaded_preview_mode=='bypass' and 'ALL clip grades' in w.preview_label.text()
    assert w.selected_clip().grade.enabled


def test_delete_key_removes_curve_point_not_timeline_clip(window, app, tmp_path):
    from PySide6.QtTest import QTest
    w=window; populate(w,tmp_path); w.show(); w.color_tabs.setCurrentIndex(2); app.processEvents()
    curve=w.curve_editor; curve.add_point(.4,.6)
    QTest.mouseClick(curve,Qt.MouseButton.LeftButton,pos=curve.to_screen([.4,.6]).toPoint())
    QTest.keyClick(curve,Qt.Key.Key_Delete)
    assert len(w.project.clips)==1
    assert w.selected_clip().grade.master_curve==[[0.,0.],[1.,1.]]
    w.undo(); assert len(w.selected_clip().grade.master_curve)==3


def test_caption_crud_undo_and_stale_preview(window, tmp_path):
    w = window; populate(w, tmp_path)
    w.preview_revision = w.revision; w.loaded_preview_mode = 'graded'
    w.caption_start.setValue(12); w.caption_duration.setValue(40)
    w.caption_text.setPlainText('Hello\n字幕'); w.caption_language.setText('zh')
    w.add_caption()
    caption = w.project.captions[0]
    assert (caption.start, caption.duration, caption.text, caption.language) == (12, 40, 'Hello\n字幕', 'zh')
    assert w.dirty and w.preview_revision != w.revision
    assert w.caption_list.count() == 1 and w.selected_caption().id == caption.id
    w.caption_text.setPlainText('Updated'); w.update_caption()
    assert w.project.captions[0].text == 'Updated'
    w.undo(); assert w.project.captions[0].text == 'Hello\n字幕'
    w.delete_caption(); assert not w.project.captions
    w.undo(); assert len(w.project.captions) == 1


def test_caption_import_conflict_explicit_replacement_and_undo(window, monkeypatch):
    from lumen.model import Caption
    w = window
    w.import_caption_batch([Caption('old', 0, 60, 'Original')])
    revision = w.revision
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.No)
    w.import_caption_batch([Caption('new', 10, 10, 'Replacement')])
    assert w.revision == revision and w.project.captions[0].id == 'old'
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Yes)
    w.import_caption_batch([Caption('new', 10, 10, 'Replacement'), Caption('second', 20, 10, 'Second')])
    assert w.revision == revision + 1 and len(w.project.captions) == 2
    w.undo(); assert [c.id for c in w.project.captions] == ['old']


def test_caption_srt_dialogs_protect_existing_file(window, tmp_path, monkeypatch):
    w = window
    source = tmp_path / 'subtitles.srt'
    source.write_text('1\n00:00:00,100 --> 00:00:01,000\nHello\n', encoding='utf-8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(source), ''))
    w.import_srt(); assert len(w.project.captions) == 1
    before = source.read_bytes()
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(source), ''))
    w.export_srt(); assert source.read_bytes() == before and w.test_warnings
    target = tmp_path / 'new.srt'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(target), ''))
    w.export_srt(); assert 'Hello' in target.read_text()


def test_caption_timeline_row_selection(window, app, tmp_path):
    from PySide6.QtTest import QTest
    w = window; populate(w, tmp_path)
    caption = w.editor.add_caption(5, 30, 'Timeline cue'); w.changed(); w.show(); app.processEvents()
    QTest.mouseClick(w.timeline, Qt.MouseButton.LeftButton, pos=w.timeline.caption_rect(caption).center().toPoint())
    assert w.selected_caption().id == caption.id and w.side_tabs.currentIndex() == 1
    assert w.timeline.caption_selection == caption.id


def configure_mock_asr(w, tmp_path):
    runtime = tmp_path / 'runtime'; runtime.write_bytes(b'existing executable fixture')
    model = tmp_path / 'model.bin'; model.write_bytes(b'existing model fixture')
    w.asr_runtime.setText(str(runtime)); w.asr_model.setText(str(model))


def test_asr_maps_trimmed_source_and_imports_one_undo(window, app, tmp_path, monkeypatch):
    import lumen.asr as asr
    w = window; clip = populate(w, tmp_path); configure_mock_asr(w, tmp_path)
    w.editor.update_clip(clip.id, source_in=30, duration=60, start=90); w.changed()
    segments = [asr.TranscriptSegment(0, 1.5, 'clipped start', 'en'), asr.TranscriptSegment(1.5, 2, 'middle', 'en'), asr.TranscriptSegment(2, 4, 'clipped end', 'en'), asr.TranscriptSegment(4, 5, 'outside', 'en')]
    monkeypatch.setattr(asr, 'transcribe', lambda *a, **kw: segments)
    revision = w.revision; w.transcribe_selected()
    pump_until(app, lambda: not w.jobs)
    assert [(c.start, c.duration) for c in w.project.captions] == [(90, 15), (105, 15), (120, 30)]
    assert w.revision == revision + 1 and not w.test_warnings
    w.undo(); assert not w.project.captions


def test_asr_rejects_stale_result(window, app, tmp_path, monkeypatch):
    import lumen.asr as asr
    w = window; populate(w, tmp_path); configure_mock_asr(w, tmp_path)
    gate = threading.Event()
    def transcribe(*a, **kw):
        assert gate.wait(3)
        return [asr.TranscriptSegment(0, 1, 'Stale', 'en')]
    monkeypatch.setattr(asr, 'transcribe', transcribe)
    w.transcribe_selected(); w.grade_spins['contrast'].setValue(1.2); gate.set()
    pump_until(app, lambda: not w.jobs)
    assert not w.project.captions and 'project changed' in w.test_warnings[-1]
    assert w.asr_job is None and w.asr_button.isEnabled()


def test_asr_no_audio_missing_runtime_and_cancel(window, app, tmp_path, monkeypatch):
    import lumen.asr as asr
    w = window; populate(w, tmp_path)
    w.transcribe_selected(); assert 'existing' in w.test_warnings[-1] and not w.jobs
    configure_mock_asr(w, tmp_path); w.project.media[0].has_audio = False
    w.transcribe_selected(); assert 'no audio' in w.test_warnings[-1] and not w.jobs
    w.project.media[0].has_audio = True
    def transcribe(*a, cancel=None, **kw):
        cancel.wait(3)
        return [asr.TranscriptSegment(0, 1, 'Cancelled', 'en')]
    monkeypatch.setattr(asr, 'transcribe', transcribe)
    w.transcribe_selected(); w.cancel_jobs(); pump_until(app, lambda: not w.jobs)
    assert not w.project.captions and w.asr_job is None


def test_advanced_color_details_collapsed_by_default(window, app, tmp_path):
    w = window; populate(w, tmp_path); w.show(); app.processEvents()
    assert not w.color_details_toggle.isChecked() and not w.color_details.isVisible()
    w.color_details_toggle.setChecked(True); app.processEvents()
    assert w.color_details.isVisible() and 'FIXED CORRECTION ORDER' in w.stack_label.text()
    assert w.wheel_choice.itemText(0).startswith('Lift')


def test_asr_bin_source_starts_at_zero_and_empty_result(window, app, tmp_path, monkeypatch):
    import lumen.asr as asr
    w = window; populate(w, tmp_path); configure_mock_asr(w, tmp_path)
    w.asr_source.setCurrentIndex(1); w.bin.setCurrentRow(0)
    monkeypatch.setattr(asr, 'transcribe', lambda *a, **kw: [asr.TranscriptSegment(1, 2, 'Source cue', 'en')])
    w.transcribe_selected(); pump_until(app, lambda: not w.jobs)
    assert w.project.captions[0].start == 30
    w.undo(); revision = w.revision
    messages = []; monkeypatch.setattr(QMessageBox, 'information', lambda *a: messages.append(a[2]))
    monkeypatch.setattr(asr, 'transcribe', lambda *a, **kw: [])
    w.transcribe_selected(); pump_until(app, lambda: not w.jobs)
    assert w.revision == revision and not w.project.captions and 'No caption cues' in messages[-1]


def test_caption_style_undo_and_frame_quantization(window):
    from fractions import Fraction
    from lumen.asr import TranscriptSegment
    from lumen.ui_captions import segments_to_captions
    w = window
    w.caption_size.setValue(60); w.caption_margin.setValue(30); w.apply_caption_style()
    assert w.project.caption_style.font_size == 60
    w.undo(); assert w.project.caption_style.font_size == 42
    cues = segments_to_captions([TranscriptSegment(0, .5, 'a'), TranscriptSegment(.5, 1, 'b')], Fraction(30000, 1001))
    assert cues[0].end == cues[1].start == 15


def test_delete_key_targets_caption_not_retained_asr_clip(window, app, tmp_path):
    from PySide6.QtTest import QTest
    w=window; clip=populate(w,tmp_path)
    cue=w.editor.add_caption(0,30,'Caption'); w.changed(); w.show(); app.processEvents()
    w.select_caption(cue.id); w.caption_list.setFocus(); app.processEvents()
    assert w.timeline.selection == '' and w.selected_clip() is None and w.selected_clip_ids == ()
    QTest.keyClick(w.caption_list,Qt.Key.Key_Delete)
    assert len(w.project.clips)==1 and not w.project.captions
    w.undo(); w.select_caption(cue.id)
    QTest.mouseClick(w.timeline,Qt.MouseButton.LeftButton,pos=w.timeline.caption_rect(w.project.captions[0]).center().toPoint())
    QTest.keyClick(w.timeline,Qt.Key.Key_Delete)
    assert len(w.project.clips)==1 and not w.project.captions


def test_caption_text_delete_and_space_are_text_editing(window, app, tmp_path):
    from PySide6.QtTest import QTest
    w=window; populate(w,tmp_path);cue=w.editor.add_caption(0,30,'Caption');w.changed()
    w.show();w.select_caption(cue.id);w.caption_text.setFocus();app.processEvents()
    w.caption_text.setPlainText('AB');cursor=w.caption_text.textCursor();cursor.setPosition(0);w.caption_text.setTextCursor(cursor)
    QTest.keyClick(w.caption_text,Qt.Key.Key_Delete);QTest.keyClick(w.caption_text,Qt.Key.Key_Space)
    assert w.caption_text.toPlainText()==' B'
    assert len(w.project.clips)==1 and len(w.project.captions)==1


def test_asr_bin_source_clamps_recognition_tail_to_full_media_frames(window, app, tmp_path, monkeypatch):
    import lumen.asr as asr
    w = window; populate(w, tmp_path); configure_mock_asr(w, tmp_path)
    w.project.media[0].duration = 6.486
    w.asr_source.setCurrentIndex(1); w.bin.setCurrentRow(0)
    # Keep the project itself valid after shortening this fixture's source.
    w.editor.update_clip(w.selection, duration=w.project.media_frames(w.project.media[0]))
    monkeypatch.setattr(asr, 'transcribe', lambda *a, **kw: [asr.TranscriptSegment(6, 6.50, 'Overlong recognition tail', 'en')])
    w.transcribe_selected(); pump_until(app, lambda: not w.jobs)
    assert len(w.project.captions) == 1
    assert w.project.captions[0].end == w.project.media_frames(w.project.media[0]) == 194
    assert not w.test_warnings


def test_asr_bin_source_shorter_than_one_frame_fails_clearly(window, tmp_path, monkeypatch):
    import lumen.asr as asr
    w = window; populate(w, tmp_path); configure_mock_asr(w, tmp_path)
    w.project.media[0].duration = .01
    w.asr_source.setCurrentIndex(1); w.bin.setCurrentRow(0)
    monkeypatch.setattr(asr, 'transcribe', lambda *a, **kw: pytest.fail('Must reject before launching ASR'))
    w.transcribe_selected()
    assert not w.jobs and 'shorter than one full project frame' in w.test_warnings[-1]

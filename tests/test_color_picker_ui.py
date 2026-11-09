"""Actual Qt picker gestures with immutable real RGB16 source snapshots."""
from copy import deepcopy
import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from lumen.model import Clip, Editor
from lumen.color_picker_source import capture_picker_snapshot
from lumen.ui import MainWindow
from lumen.ui_color_picker import ColorPickerDialog, PickerCanvas
from test_selective_color_render import source_project

@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])

@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: pytest.fail(str(a[2])))
    w = MainWindow(workspace_settings=QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat),
                   recovery_root=tmp_path / 'recovery', ui_language='en')
    project = source_project(tmp_path)
    project.clips.append(Clip('other', project.media[0].id, 0, 2, 0, 1))
    w.editor = Editor(project); w.refresh(); w.select_clip('picture')
    w.inspector_tabs.setCurrentIndex(1); w.color_tabs.setCurrentIndex(2); w.curve_choice.setCurrentIndex(4)
    w.resize(1280, 800); w.show(); app.processEvents()
    yield w
    for d in w.findChildren(ColorPickerDialog): d.reject()
    w.cancel_jobs()
    for job in list(w.jobs): assert job.wait(5000)
    w.dirty = False; w.closing = True; w.close(); w.deleteLater(); app.processEvents()


def loaded(w):
    d = ColorPickerDialog(w); d.show()
    d.accept_snapshot(capture_picker_snapshot(w.project, w.selection, w.timeline.playhead))
    assert d.snapshot is not None
    return d


def test_cancel_sampling_does_not_mutate_grade_or_history(window):
    before = deepcopy(window.project)
    d = loaded(window); d.sample_pixel(32, 32)
    assert d.apply_button.isEnabled()
    QTest.mouseClick(d.close_button, Qt.MouseButton.LeftButton)
    assert window.project == before and not window.editor.can_undo

@pytest.mark.parametrize('choice,key', [(4, 'hue_hue_curve'), (5, 'hue_sat_curve')])
def test_insert_repeat_reopen_single_undo_transaction(window, choice, key):
    w = window; w.curve_choice.setCurrentIndex(choice)
    before = deepcopy(w.project); revision = w.revision
    d = loaded(w); d.sample_pixel(32, 32); d.apply_sample()
    after = deepcopy(w.project)
    assert len(getattr(w.selected_clip().grade, key)) == 4
    assert w.revision == revision + 1 and len(w.editor._undo) == 1
    again = loaded(w); again.sample_pixel(32, 32); again.apply_sample()
    assert w.project == after and len(w.editor._undo) == 1 and w.revision == revision + 1
    w.undo(); assert w.project == before
    w.redo(); assert w.project == after

@pytest.mark.parametrize('change', ['selection', 'revision', 'frame', 'curve', 'source', 'grade'])
def test_stale_context_never_commits(window, change):
    w = window; d = loaded(w); d.sample_pixel(32, 32)
    if change == 'selection': w.select_clip('other')
    elif change == 'revision': w.revision += 1
    elif change == 'frame': w.timeline.playhead += 1
    elif change == 'curve': w.curve_choice.setCurrentIndex(5)
    elif change == 'source': os.utime(w.project.media[0].path, None)
    else: w.selected_clip().grade.exposure = .25
    before = deepcopy(w.project)
    d.apply_sample()
    assert w.project == before and not w.editor.can_undo
    assert not d.apply_button.isEnabled() and not d.canvas.isEnabled()
    assert 'stale' in d.status.text().lower()


def test_gray_padding_and_outside_rejected(window):
    w = window; w.project.height = 128
    d = loaded(w)
    for x, y in [(32, 0), (32, 127), (-1, -1), (w.project.width, 64), (11 * 64 + 32, 64)]:
        d.sample_pixel(x, y)
        assert d.hue is None and not d.apply_button.isEnabled()
    d.sample_pixel(32, 64); assert d.apply_button.isEnabled()


def test_canvas_letterbox_coordinate_mapping_and_right_edge(app):
    canvas = PickerCanvas(); canvas.resize(400, 400)
    canvas.set_frame(np.zeros((10, 20, 3), dtype=np.float32)); canvas.show(); app.processEvents()
    received = []; canvas.sampled.connect(lambda x, y: received.append((x, y)))
    for point, expected in [(QPoint(10, 50), (-1, -1)), (QPoint(210, 210), (10, 5)),
                            (QPoint(399, 299), (19, 9)), (QPoint(200, 300), (-1, -1))]:
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=point)
        assert received[-1] == expected
    canvas.close()


def test_real_async_open_cancel_reopen_and_new_clip(window, app):
    w = window
    first = w.open_color_picker()
    assert w.open_color_picker() is first
    first.reject()
    second = w.open_color_picker(); assert second is not first
    deadline = time.monotonic() + 15
    while second.snapshot is None and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.01)
    assert second.snapshot is not None and first.snapshot is None
    assert 'source frame' in second.metadata.text()
    second.reject(); w.select_clip('other'); w.timeline.playhead = 2
    third = w.open_color_picker()
    while third.snapshot is None and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.01)
    assert third.snapshot.identity.clip_id == 'other'


def test_out_of_range_sample_never_enables_commit(window):
    from dataclasses import replace
    d = loaded(window)
    pixels = d.snapshot.frame.copy(); pixels[32, 32] = [1.01, .2, .3]
    d.snapshot = replace(d.snapshot, frame=pixels)
    d.sample_pixel(32, 32)
    assert d.hue is None and not d.apply_button.isEnabled()


def test_late_snapshot_after_cancel_cannot_repopulate_dialog(window):
    d = ColorPickerDialog(window); d.show(); d.reject()
    snapshot = capture_picker_snapshot(window.project, window.selection, window.timeline.playhead)
    d.accept_snapshot(snapshot)
    assert d.snapshot is None and not d.apply_button.isEnabled()


def test_canceled_old_job_finishes_after_reopened_new_clip(window, app, monkeypatch):
    import threading
    import lumen.ui_color_picker as picker
    w = window
    old_snapshot = capture_picker_snapshot(w.project, 'picture', 0)
    new_snapshot = capture_picker_snapshot(w.project, 'other', 2)
    entered = threading.Event(); release_old = threading.Event()
    def controlled_capture(project, clip_id, frame, **kwargs):
        if clip_id == 'picture':
            entered.set()
            assert release_old.wait(10)
            return old_snapshot  # Deliberately simulate an uncancellable late result.
        return new_snapshot
    monkeypatch.setattr(picker, 'capture_picker_snapshot', controlled_capture)
    first = w.open_color_picker()
    try:
        assert entered.wait(2)
        first.reject(); w.select_clip('other'); w.timeline.playhead = 2
        second = w.open_color_picker()
        deadline = time.monotonic() + 5
        while second.snapshot is None and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.01)
        assert second.snapshot is new_snapshot
        release_old.set()
        deadline = time.monotonic() + 5
        while w.jobs and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.01)
        assert not w.jobs
        assert first.snapshot is None and second.snapshot is new_snapshot
        assert second.current() and not w.editor.can_undo
    finally:
        release_old.set()


@pytest.mark.parametrize('finish', ['reject', 'accept'])
def test_repeated_closed_dialogs_release_large_pixel_buffers(window, finish):
    for _ in range(4):
        d = loaded(window)
        snapshot = d.snapshot
        d.sample_pixel(32, 32)
        assert d.hue is not None and not d.canvas.image.isNull()
        if finish == 'accept':
            d.apply_sample()
        else:
            d.reject()
        assert d.snapshot is None
        assert d.hue is None and d.canvas.marker is None
        assert d.canvas.image.isNull() and not d.apply_button.isEnabled()
        # A queued decode callback arriving after close must not restore buffers.
        d.accept_snapshot(snapshot)
        assert d.snapshot is None and d.canvas.image.isNull()


@pytest.mark.parametrize('active', [False, True])
def test_restoration_stage_label_and_unchanged_sampling_undo(window, active):
    w = window
    w.selected_clip().restoration.enabled = active
    w.selected_clip().restoration.denoise = .5
    before = deepcopy(w.project)
    d = loaded(w)
    assert d.snapshot.restoration_applied is active
    assert ('applied to active picture' if active else 'bypassed') in d.stage.text()
    assert 'before hue curves and LUT' in d.stage.text()
    assert 'clipped 8-bit' in d.stage.text()
    d.sample_pixel(32, 32); d.apply_sample()
    assert w.selected_clip().restoration == before.clips[0].restoration
    assert len(w.editor._undo) == 1
    w.undo(); assert w.project == before


def test_changed_restoration_settings_invalidate_loaded_frame(window):
    w = window; w.selected_clip().restoration.enabled = True
    w.selected_clip().restoration.denoise = .4
    d = loaded(w); d.sample_pixel(32, 32)
    w.selected_clip().restoration.sharpen = .3
    d.apply_sample()
    assert not d.apply_button.isEnabled() and not d.canvas.isEnabled()
    assert not w.editor.can_undo and 'stale' in d.status.text().lower()


def test_restoration_pending_close_reopen_change_stays_safe(window, app):
    w = window; w.selected_clip().restoration.enabled = True
    w.selected_clip().restoration.denoise = .6
    first = w.open_color_picker(); first.reject()
    second = w.open_color_picker()
    w.selected_clip().restoration.denoise = .2
    deadline = time.monotonic() + 15
    while w.jobs and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.01)
    second.check_current()
    assert first.snapshot is None
    assert not second.apply_button.isEnabled() and not second.canvas.isEnabled()
    assert not w.editor.can_undo

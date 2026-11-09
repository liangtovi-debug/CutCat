"""Actual Qt qualifier transactions, source identity and rendered matte pixels."""
from copy import deepcopy
from dataclasses import replace
import os
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from lumen.color_picker_source import capture_picker_snapshot
from lumen.i18n import set_locale
from lumen.model import Clip, Editor
from lumen.qualifier import QualifierSettings
from lumen.ui import MainWindow
from lumen.ui_qualifier import QualifierDialog, open_qualifier
from test_selective_color_render import source_project


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: pytest.fail(str(args[2])))
    window = MainWindow(workspace_settings=QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat),
                        recovery_root=tmp_path / 'recovery', ui_language='en')
    project = source_project(tmp_path)
    project.clips.append(Clip('other', project.media[0].id, 0, 2, 0, 1))
    window.editor = Editor(project); window.refresh(); window.select_clip('picture')
    window.resize(1280, 800); window.show(); app.processEvents()
    yield window
    for dialog in window.findChildren(QualifierDialog):
        dialog.reject()
    window.cancel_jobs()
    for job in list(window.jobs):
        assert job.wait(5000)
    window.dirty = False; window.closing = True
    window.close(); window.deleteLater(); app.processEvents()
    set_locale('en')


def loaded(window):
    dialog = QualifierDialog(window); dialog.show()
    dialog.accept_snapshot(capture_picker_snapshot(window.project, window.selection,
                                                   window.timeline.playhead, stage='qualifier'))
    assert dialog.snapshot is not None
    assert dialog.apply_button.isEnabled()
    return dialog


def wait_until(app, predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.01)
    assert predicate()


def test_cancel_changes_neither_grade_nor_undo_history(window):
    before = deepcopy(window.project)
    dialog = loaded(window)
    dialog.enabled_check.setChecked(True)
    dialog.controls['exposure'].setValue(.4)
    dialog.controls['hue_width'].setValue(45)
    dialog.render_preview()
    assert dialog.settings != window.selected_clip().grade.qualifier
    QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)
    assert window.project == before and not window.editor.can_undo
    assert dialog.snapshot is None and dialog.matte is None and dialog.canvas.image.isNull()


def test_apply_reopen_repeat_undo_redo_and_exact_untouched_fields(window):
    window.selected_clip().grade.qualifier = QualifierSettings(hue_center=.123456789,
                                                              saturation_low=.123456789)
    before = deepcopy(window.project); revision = window.revision
    dialog = loaded(window)
    dialog.enabled_check.setChecked(True)
    dialog.controls['exposure'].setValue(.25)
    settings = deepcopy(dialog.settings)
    dialog.apply_settings()
    assert window.selected_clip().grade.qualifier == settings
    assert settings.hue_center == .123456789 and settings.saturation_low == .123456789
    assert len(window.editor._undo) == 1 and window.revision == revision + 1
    after = deepcopy(window.project)
    again = loaded(window)
    assert again.settings == settings
    again.apply_settings()
    assert len(window.editor._undo) == 1 and window.revision == revision + 1
    window.undo(); assert window.project == before
    window.redo(); assert window.project == after


def test_neutral_enabled_qualifier_can_be_saved_without_a_sample_click(window):
    dialog = loaded(window)
    dialog.enabled_check.setChecked(True)
    assert dialog.apply_button.isEnabled()
    dialog.apply_settings()
    assert window.selected_clip().grade.qualifier.enabled
    assert window.selected_clip().grade.qualifier.exposure == 0
    assert len(window.editor._undo) == 1


@pytest.mark.parametrize('change', ['selection', 'revision', 'frame', 'curve', 'source',
                                    'grade', 'restoration', 'project', 'closing'])
def test_stale_context_never_commits(window, change):
    dialog = loaded(window); dialog.enabled_check.setChecked(True)
    if change == 'selection': window.select_clip('other')
    elif change == 'revision': window.revision += 1
    elif change == 'frame': window.timeline.playhead += 1
    elif change == 'curve': window.curve_choice.setCurrentIndex((window.curve_choice.currentIndex() + 1) % 4)
    elif change == 'source':
        path = window.project.media[0].path
        info = os.stat(path)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000))
    elif change == 'grade': window.selected_clip().grade.exposure = .25
    elif change == 'restoration': window.selected_clip().restoration.sharpen = .2
    elif change == 'project': window.editor = Editor(deepcopy(window.project))
    elif change == 'closing': window.closing = True
    before = deepcopy(window.project)
    dialog.apply_settings()
    assert window.project == before and not window.editor.can_undo
    assert not dialog.apply_button.isEnabled() and not dialog.canvas.isEnabled()
    assert 'stale' in dialog.status.text().lower()


def test_no_snapshot_wrong_stage_or_invalid_ranges_cannot_apply(window):
    dialog = QualifierDialog(window); dialog.show()
    dialog.enabled_check.setChecked(True)
    assert not dialog.apply_button.isEnabled()
    dialog.apply_settings(); assert not window.editor.can_undo
    snapshot = capture_picker_snapshot(window.project, window.selection, window.timeline.playhead)
    dialog.accept_snapshot(snapshot)
    assert dialog.snapshot is None and not dialog.apply_button.isEnabled()
    assert 'different color stage' in dialog.status.text()
    dialog.accept_snapshot(capture_picker_snapshot(window.project, window.selection,
                                                   window.timeline.playhead, stage='qualifier'))
    dialog.controls['saturation_low'].setValue(80)
    dialog.controls['saturation_high'].setValue(20)
    assert not dialog.apply_button.isEnabled()
    assert 'minimum' in dialog.status.text()
    dialog.apply_settings(); assert not window.editor.can_undo
    dialog.controls['saturation_high'].setValue(90)
    assert dialog.apply_button.isEnabled()
    dialog.controls['luma_high'].setValue(20)
    dialog.controls['luma_low'].setValue(80)
    assert not dialog.apply_button.isEnabled()


def test_real_snapshot_matte_white_black_and_padding_pixels(window):
    window.project.height = 128
    window.selected_clip().grade.qualifier = QualifierSettings(hue_center=0, hue_width=.05,
                                                              hue_softness=0)
    dialog = loaded(window)
    # The real source is a 16-bit synthetic chart. Red is selected, green is not.
    stride = dialog.preview_stride
    assert dialog.matte[64 // stride, 32 // stride] == 1
    assert dialog.matte[64 // stride, (5 * 64 + 32) // stride] == 0
    assert dialog.matte[0, 32 // stride] == 0 and dialog.matte[-1, 32 // stride] == 0
    assert dialog.canvas.image.pixelColor(32 // stride, 64 // stride).getRgb()[:3] == (255, 255, 255)
    assert dialog.canvas.image.pixelColor((5 * 64 + 32) // stride, 64 // stride).getRgb()[:3] == (0, 0, 0)
    assert not dialog.settings.enabled  # Inspection did not enable the effect.
    assert 'even when correction disabled' in dialog.preview_choice.currentText()
    assert 'before qualifier and LUT' in dialog.stage.text()
    assert any('not HSL lightness' in label.text() for label in dialog.findChildren(type(dialog.stage)))


def test_float_matte_feather_and_source_adjusted_views(window):
    dialog = loaded(window)
    # Independent boundary values: red H=0; green H=1/3; orange H=.125.
    pixels = np.array([[[.5, 0, 0], [0, .5, 0], [.5, .375, 0]]], dtype=np.float32)
    valid = np.array([[True, True, True]])
    dialog.snapshot = replace(dialog.snapshot, frame=pixels, valid_mask=valid)
    dialog.settings = QualifierSettings(enabled=True, hue_center=0, hue_width=.1,
                                        hue_softness=.05, exposure=1)
    dialog.render_preview()
    np.testing.assert_allclose(dialog.matte, [[1, 0, .5]], atol=1e-6)
    assert dialog.canvas.image.pixelColor(2, 0).getRgb()[:3] == (128, 128, 128)
    dialog.preview_choice.setCurrentIndex(1)
    assert dialog.canvas.image.pixelColor(0, 0).getRgb()[:3] == (128, 0, 0)
    dialog.preview_choice.setCurrentIndex(2)
    assert dialog.canvas.image.pixelColor(0, 0).getRgb()[:3] == (255, 0, 0)
    assert dialog.canvas.image.pixelColor(1, 0).getRgb()[:3] == (0, 128, 0)
    np.testing.assert_array_equal(dialog.snapshot.frame, pixels)
    assert not window.editor.can_undo


def test_grade_bypass_does_not_pretend_correction_will_render(window):
    window.selected_clip().grade.enabled = False
    window.selected_clip().grade.qualifier = QualifierSettings(enabled=True, exposure=1)
    dialog = loaded(window)
    dialog.preview_choice.setCurrentIndex(1); source_image = dialog.canvas.image.copy()
    dialog.preview_choice.setCurrentIndex(2)
    assert dialog.canvas.image == source_image
    assert 'grading is disabled' in dialog.status.text()
    assert 'render bypass' in dialog.status.text()
    assert dialog.apply_button.isEnabled()


def test_real_async_cancel_reopen_and_single_dialog_ownership(window, app):
    first = open_qualifier(window)
    assert open_qualifier(window) is first
    first.reject()
    second = open_qualifier(window); assert second is not first
    wait_until(app, lambda: second.snapshot is not None)
    assert second.snapshot.stage == 'qualifier' and first.snapshot is None
    second.reject(); window.select_clip('other'); window.timeline.playhead = 2
    third = open_qualifier(window)
    wait_until(app, lambda: third.snapshot is not None)
    assert third.snapshot.identity.clip_id == 'other'
    assert 'source frame' in third.metadata.text()


def test_canceled_job_late_result_cannot_overwrite_reopened_clip(window, app, monkeypatch):
    import lumen.ui_qualifier as qualifier_ui
    old = capture_picker_snapshot(window.project, 'picture', 0, stage='qualifier')
    new = capture_picker_snapshot(window.project, 'other', 2, stage='qualifier')
    entered = threading.Event(); release = threading.Event()

    def capture(project, clip_id, frame, **kwargs):
        assert kwargs['stage'] == 'qualifier'
        if clip_id == 'picture':
            entered.set(); assert release.wait(10)
            return old
        return new

    monkeypatch.setattr(qualifier_ui, 'capture_picker_snapshot', capture)
    first = open_qualifier(window)
    try:
        assert entered.wait(2)
        first.reject(); window.select_clip('other'); window.timeline.playhead = 2
        second = open_qualifier(window)
        wait_until(app, lambda: second.snapshot is not None, 5)
        assert second.snapshot is new
        release.set(); wait_until(app, lambda: not window.jobs, 5)
        assert first.snapshot is None and second.snapshot is new
        assert second.current() and not window.editor.can_undo
    finally:
        release.set()


@pytest.mark.parametrize('finish', ['cancel', 'close', 'apply'])
def test_repeated_closed_dialogs_release_buffers_and_ignore_late_callback(window, finish):
    for _ in range(3):
        dialog = loaded(window); snapshot = dialog.snapshot
        if finish == 'cancel': dialog.reject()
        elif finish == 'close': dialog.close()
        else: dialog.apply_settings()
        assert dialog.snapshot is None and dialog.matte is None and dialog.canvas.image.isNull()
        assert not dialog.apply_button.isEnabled() and not dialog.preview_timer.isActive()
        dialog.accept_snapshot(snapshot)
        dialog.render_preview()
        assert dialog.snapshot is None and dialog.canvas.image.isNull()


def test_narrow_dialog_scroll_accessibility_focus_and_live_language(window, app):
    dialog = loaded(window)
    dialog.resize(360, 540); app.processEvents()
    assert dialog.width() <= 360
    assert dialog.scroll.verticalScrollBar().maximum() > 0
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
    assert dialog.apply_button.isVisible() and dialog.close_button.isVisible()
    assert dialog.rect().contains(dialog.apply_button.geometry())
    assert len(dialog.controls) == 23
    for key, spin in dialog.controls.items():
        assert spin.accessibleName() and spin.focusPolicy() != Qt.FocusPolicy.NoFocus
        spin.setFocus(); app.processEvents()
        assert spin.hasFocus()
        assert spin.visibleRegion().boundingRect().height() == spin.height()
    before = deepcopy(dialog.settings)
    dialog.controls['exposure'].setFocus()
    QTest.keyClick(dialog.controls['exposure'], Qt.Key.Key_Return)
    assert dialog.isVisible() and not window.editor.can_undo
    set_locale('zh-CN'); app.processEvents()
    assert '限定器' in dialog.windowTitle()
    assert '色相' in dialog.controls['hue_center'].accessibleName()
    assert '蒙版' in dialog.preview_choice.itemText(0)
    assert dialog.settings == before and not window.editor.can_undo


def test_full_canvas_padding_matches_renderer_instead_of_hidden_roi(window):
    window.project.height = 128
    window.selected_clip().grade.lift = [.1, .1, .1]
    window.selected_clip().grade.qualifier = QualifierSettings(enabled=True, hue_width=.5, exposure=1)
    dialog = loaded(window)
    assert not dialog.snapshot.valid_mask[0, 32]
    x = 32 // dialog.preview_stride
    assert dialog.matte[0, x] == 1
    assert dialog.canvas.image.pixelColor(x, 0).getRgb()[:3] == (255, 255, 255)
    source = dialog.snapshot.frame[0, 32]
    assert np.all(source > 0)
    dialog.preview_choice.setCurrentIndex(1)
    np.testing.assert_array_equal(dialog.canvas.image.pixelColor(x, 0).getRgb()[:3],
                                  np.rint(source * 255).astype(np.uint8))
    dialog.preview_choice.setCurrentIndex(2)
    np.testing.assert_array_equal(dialog.canvas.image.pixelColor(x, 0).getRgb()[:3],
                                  np.rint(np.clip(source * 2, 0, 1) * 255).astype(np.uint8))
    assert any('padding can qualify' in label.text() for label in dialog.findChildren(type(dialog.stage)))


def test_bounded_float_display_is_exact_grid_not_quantized_proxy(window):
    from lumen.qualifier import qualifier_mask, apply_qualifier
    dialog = loaded(window)
    snapshot = dialog.snapshot
    # Full source exceeds the live display budget; non-8-bit values survive.
    rng = np.random.default_rng(123)
    source = rng.uniform(0, 1, (1080, 1920, 3)).astype(np.float32)
    source.setflags(write=False)
    dialog.snapshot = replace(snapshot, frame=source, valid_mask=np.ones(source.shape[:2], bool))
    dialog.settings = QualifierSettings(enabled=True, hue_width=.2, exposure=.25)
    dialog.render_preview()
    assert max(dialog.preview_shape) <= 512 and dialog.preview_stride == 4
    assert dialog.snapshot.frame is source and not source.flags.writeable
    full_mask = qualifier_mask(source, dialog.settings)
    np.testing.assert_array_equal(dialog.matte, full_mask[::4, ::4])
    dialog.preview_choice.setCurrentIndex(2)
    expected = apply_qualifier(source[::4, ::4], dialog.settings)
    for x, y in [(0, 0), (37, 120), (479, 269)]:
        np.testing.assert_array_equal(dialog.canvas.image.pixelColor(x, y).getRgb()[:3],
                                      np.rint(np.clip(expected[y, x], 0, 1) * 255).astype(np.uint8))
    assert '480×270' in dialog.resolution.text() and '1920×1080' in dialog.resolution.text()
    assert 'every pixel' in dialog.resolution.text()
    dialog.reject()
    assert dialog.preview_shape is None and dialog.snapshot is None

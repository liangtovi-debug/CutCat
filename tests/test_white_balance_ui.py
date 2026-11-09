"""Real Qt neutral-reference picking, edit transactions, and stale-frame safety."""
from copy import deepcopy
from dataclasses import replace
import os
import time
import threading
import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from lumen.color_picker_source import capture_picker_snapshot
from lumen.ui_white_balance import WhiteBalanceDialog
from lumen.ui_color_picker import PickerCanvas
from lumen.i18n import set_locale, tr_error
from test_color_picker_ui import app, window


def loaded(w):
    d = WhiteBalanceDialog(w); d.show()
    d.accept_snapshot(capture_picker_snapshot(w.project, w.selection, w.timeline.playhead, stage='white_balance'))
    assert d.snapshot is not None
    return d


def test_apply_repeat_reset_one_undo_and_artistic_independence(window):
    w = window
    w.selected_clip().grade.temperature = .25
    w.selected_clip().grade.tint = -.125
    before = deepcopy(w.project); revision = w.revision
    d = loaded(w); d.sample_pixel(32, 32)
    assert d.gains and d.apply_button.isEnabled()
    assert w.project == before and not w.editor.can_undo
    gains = list(d.gains); d.apply_button.click()
    after = deepcopy(w.project)
    assert w.selected_clip().grade.white_balance_enabled
    assert w.selected_clip().grade.white_balance_gains == gains
    assert w.selected_clip().grade.temperature == .25 and w.selected_clip().grade.tint == -.125
    assert len(w.editor._undo) == 1 and w.revision == revision + 1
    assert w.white_balance_enabled.isChecked()
    again = loaded(w); again.sample_pixel(32, 32); again.apply_sample()
    assert w.project == after and len(w.editor._undo) == 1
    w.undo(); assert w.project == before and not w.white_balance_enabled.isChecked()
    w.redo(); assert w.project == after and w.white_balance_enabled.isChecked()
    w.white_balance_reset.click()
    assert not w.selected_clip().grade.white_balance_enabled
    assert w.selected_clip().grade.white_balance_gains == [1, 1, 1]
    assert w.selected_clip().grade.temperature == .25
    assert len(w.editor._undo) == 2
    w.undo(); assert w.project == after


def test_cancel_and_late_snapshot_release_without_edit(window):
    d = loaded(window); snapshot = d.snapshot; before = deepcopy(window.project)
    d.sample_pixel(32, 32); d.close_button.click(); d.accept_snapshot(snapshot); d.apply_sample()
    assert d.snapshot is None and d.gains is None and d.canvas.image.isNull()
    assert window.project == before and not window.editor.can_undo


@pytest.mark.parametrize('change', ['selection', 'revision', 'frame', 'source', 'grade', 'delete', 'project', 'restoration'])
def test_stale_context_cannot_commit(window, change):
    w = window; d = loaded(w); d.sample_pixel(32, 32)
    assert d.gains is not None
    if change == 'selection': w.select_clip('other')
    elif change == 'revision': w.revision += 1
    elif change == 'frame': w.timeline.playhead += 1
    elif change == 'source': os.utime(w.project.media[0].path, None)
    elif change == 'grade': w.selected_clip().grade.exposure = .25
    elif change == 'delete': w.project.clips = [c for c in w.project.clips if c.id != w.selection]
    elif change == 'project': w.editor.project = deepcopy(w.project)
    else: w.selected_clip().restoration.sharpen = .2
    before = deepcopy(w.project); d.apply_sample()
    assert w.project == before and not w.editor.can_undo
    assert not d.apply_button.isEnabled() and not d.canvas.isEnabled() and d.gains is None


@pytest.mark.parametrize('pixel', [[0, 0, 0], [1, 1, 1], [.01, .02, .01], [.9, .03, .4], [float('nan'), .3, .3]])
def test_bad_pixels_rejected(window, pixel):
    d = loaded(window)
    pixels = d.snapshot.frame.copy(); pixels[32, 32] = pixel
    d.snapshot = replace(d.snapshot, frame=pixels)
    d.sample_pixel(32, 32)
    assert d.gains is None and not d.apply_button.isEnabled()


def test_padding_outside_gray_and_stage(window):
    window.project.height = 128
    d = loaded(window)
    assert 'before white balance and all creative grading' in d.stage.text()
    assert 'neutral-reference' in d.canvas.accessibleName()
    for point in [(32, 0), (32, 127), (-1, -1), (window.project.width, 64)]:
        d.sample_pixel(*point); assert d.gains is None
    d.sample_pixel(11 * 64 + 32, 64)
    assert d.gains == (1, 1, 1) and d.apply_button.isEnabled()
    d.reject()
    wrong = WhiteBalanceDialog(window); wrong.show()
    wrong.accept_snapshot(capture_picker_snapshot(window.project, window.selection, 0))
    assert wrong.snapshot is None and not wrong.canvas.isEnabled()


def test_canvas_logical_coordinate_mapping_independent_of_dpr(app):
    canvas = PickerCanvas(); canvas.resize(400, 400)
    canvas.set_frame(np.zeros((10, 20, 3), np.float32)); canvas.show(); app.processEvents()
    received = []; canvas.sampled.connect(lambda x, y: received.append((x, y)))
    for point, expected in [(QPoint(10, 50), (-1, -1)), (QPoint(210, 210), (10, 5)),
                            (QPoint(399, 299), (19, 9)), (QPoint(200, 300), (-1, -1))]:
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=point)
        assert received[-1] == expected
    canvas.close()


def test_old_async_result_cannot_overwrite_new_clip(window, app, monkeypatch):
    import lumen.ui_white_balance as ui
    w = window
    old = capture_picker_snapshot(w.project, 'picture', 0, stage='white_balance')
    new = capture_picker_snapshot(w.project, 'other', 2, stage='white_balance')
    entered = threading.Event(); release = threading.Event()
    def capture(project, clip_id, frame, **kwargs):
        assert kwargs['stage'] == 'white_balance'
        if clip_id == 'picture':
            entered.set(); assert release.wait(10); return old
        return new
    monkeypatch.setattr(ui, 'capture_picker_snapshot', capture)
    first = w.open_white_balance()
    try:
        assert w.open_white_balance() is first and entered.wait(2)
        first.reject(); w.select_clip('other'); w.timeline.playhead = 2
        second = w.open_white_balance()
        deadline = time.monotonic() + 5
        while second.snapshot is None and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.01)
        assert second.snapshot is new
        release.set(); deadline = time.monotonic() + 5
        while w.jobs and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.01)
        assert not w.jobs and first.snapshot is None and second.snapshot is new
        assert second.current() and not w.editor.can_undo
    finally: release.set()


def test_controls_sync_copy_selection_toggle_and_simple_reset(window):
    w = window
    w.commit_white_balance(True, [.7, 1.2, 1.1]); w.copy_grade_button.click()
    w.select_clip('other'); assert not w.white_balance_enabled.isChecked()
    w.paste_grade_button.click()
    assert w.white_balance_enabled.isChecked() and w.selected_clip().grade.white_balance_gains == [.7, 1.2, 1.1]
    w.white_balance_enabled.click()
    assert not w.selected_clip().grade.white_balance_enabled
    assert w.selected_clip().grade.white_balance_gains == [.7, 1.2, 1.1]
    w.undo(); assert w.white_balance_enabled.isChecked()
    w.workspace_mode = 'simple'; w.reset_grade()
    assert not w.white_balance_enabled.isChecked() and w.selected_clip().grade.white_balance_gains == [1, 1, 1]
    w.undo(); assert w.white_balance_enabled.isChecked()


def test_chinese_labels_and_errors(window):
    from lumen.i18n_catalog_white_balance import ERROR_ZH_CN
    set_locale('zh-CN')
    try:
        assert window.white_balance_group.title() == '中性参照白平衡'
        assert window.white_balance_pick.text() == '吸取已知中性灰／白…'
        d = loaded(window)
        assert '中性参照' in d.windowTitle() and '白平衡' in d.stage.text()
        for source, translated in ERROR_ZH_CN.items(): assert str(tr_error(source)) == translated
    finally: set_locale('en')


@pytest.mark.parametrize('language', ['en', 'zh-CN'])
def test_neutral_controls_fit_narrow_inspector(window, app, language):
    w = window; set_locale(language)
    try:
        w.resize(1280, 900); w.side_tabs.setFixedWidth(320)
        w.color_tabs.setCurrentIndex(0)
        app.processEvents()
        w.inspector.ensureWidgetVisible(w.white_balance_group, 0, 0)
        app.processEvents()
        assert w.inspector.horizontalScrollBar().maximum() == 0
        for control in (w.white_balance_enabled, w.white_balance_pick, w.white_balance_reset):
            assert control.visibleRegion().boundingRect() == control.rect()
            assert control.fontMetrics().horizontalAdvance(control.text()) < control.width() - 10
    finally: set_locale('en')


def test_loaded_picker_live_language_switch_preserves_sample(window):
    d = loaded(window); d.sample_pixel(32, 32)
    snapshot = d.snapshot; gains = d.gains; before = deepcopy(window.project)
    try:
        for lang in ('zh-CN', 'en', 'zh-CN'):
            set_locale(lang)
            assert ('中性参照白平衡' if lang == 'zh-CN' else 'Neutral-reference white balance') in d.windowTitle()
            assert ('空间修复' if lang == 'zh-CN' else 'Spatial restoration') in d.stage.text()
            assert ('创意调色之前' if lang == 'zh-CN' else 'before white balance and all creative grading') in d.stage.text()
            assert ('中性参照' if lang == 'zh-CN' else 'neutral-reference') in d.canvas.accessibleName()
            assert d.snapshot is snapshot and d.gains == gains and d.apply_button.isEnabled()
            assert window.project == before and not window.editor.can_undo
    finally: set_locale('en')

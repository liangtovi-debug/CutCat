"""Native staged-animation transactions, decoded pixels and interrupted ownership."""
from copy import deepcopy
from dataclasses import replace
import os
import threading

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from lumen.color_picker_source import capture_picker_snapshot
from lumen.i18n import set_locale
from lumen.model import Editor, Project
from lumen.qualifier import QualifierSettings, WindowKeyframe, qualifier_mask, apply_qualifier
from lumen.ui_qualifier import QualifierDialog
from lumen.ui_window_animation import (WindowAnimationDialog, open_window_animation, MAX_VISIBLE_FRAMES,
                                       GEOMETRY_FIELDS)
from test_qualifier_ui import app, window, wait_until


@pytest.fixture
def animation_window(window):
    window.selected_clip().duration = 3
    window.project.clips[1].start = 4
    yield window
    for dialog in window.findChildren(WindowAnimationDialog):
        dialog.reject()


def key(frame, x, interpolation='linear'):
    return WindowKeyframe(frame, x, .5, .4, .6, interpolation)


def settings():
    return QualifierSettings(enabled=True, hue_width=.5, exposure=.5, window_shape='ellipse',
                             window_keyframes=[key(0, .25), key(2, .75)])


def loaded(window):
    dialog = WindowAnimationDialog(window); dialog.show()
    capture(dialog)
    return dialog


def capture(dialog):
    dialog.accept_snapshot(capture_picker_snapshot(dialog.window.project, dialog.clip_id,
        dialog.clip_start + dialog.local_frame, stage='qualifier'))
    assert dialog.snapshot is not None


def test_numeric_keys_apply_once_cancel_undo_redo_and_project_roundtrip(animation_window, app, tmp_path):
    window = animation_window; before = deepcopy(window.project)
    dialog = loaded(window)
    assert dialog.local_frame == 0 and dialog.frame_slider.value() == 0
    dialog.window_shape.setCurrentIndex(dialog.window_shape.findData('ellipse'))
    dialog.enabled_check.setChecked(True)
    dialog.controls['window_center_x'].setValue(23)
    assert dialog._draft_dirty and not dialog.apply_button.isEnabled()
    assert not dialog.settings.window_keyframes
    QTest.mouseClick(dialog.add_button, Qt.MouseButton.LeftButton); app.processEvents()
    assert dialog.settings.window_keyframes[0].center_x == .23
    assert dialog.apply_button.isEnabled()
    dialog.frame_spin.setValue(2)
    assert dialog.snapshot is None and window.timeline.playhead == 0
    assert not dialog.apply_button.isEnabled()
    dialog.controls['window_center_x'].setValue(83)
    dialog.interpolation.setCurrentIndex(dialog.interpolation.findData('hold'))
    dialog.add_key(); capture(dialog)
    assert window.project == before and not window.editor.can_undo
    pending = deepcopy(dialog.settings)
    QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton); app.processEvents()
    assert window.selected_clip().grade.qualifier == pending and len(window.editor._undo) == 1
    assert dialog.snapshot is None and dialog.canvas.image.isNull()
    after = deepcopy(window.project)
    path = tmp_path / 'animation.lumen'; window.project.save(path)
    assert Project.load(path).clips[0].grade.qualifier == pending
    window.undo(); assert window.project == before
    window.redo(); assert window.project == after
    cancel = loaded(window); cancel.controls['window_width'].setValue(60); cancel.add_key(); cancel.reject()
    assert window.project == after and len(window.editor._undo) == 1
    again = loaded(window); again.apply_settings()
    assert len(window.editor._undo) == 1


@pytest.mark.parametrize('mode', ['linear', 'hold'])
def test_current_frame_matte_and_adjusted_are_real_interpolated_geometry(animation_window, mode):
    window = animation_window
    window.selected_clip().grade.qualifier = settings()
    window.selected_clip().grade.qualifier.window_keyframes[0] = key(0, .25, mode)
    dialog = loaded(window); dialog.frame_spin.setValue(1); capture(dialog)
    expected = replace(window.selected_clip().grade.qualifier, window_keyframes=[],
                       window_center_x=.5 if mode == 'linear' else .25,
                       window_center_y=.5, window_width=.4, window_height=.6)
    assert dialog.controls['window_center_x'].value() == (50 if mode == 'linear' else 25)
    source = dialog.snapshot.frame
    np.testing.assert_array_equal(dialog.matte, dialog.viewport.extract(qualifier_mask(source, expected)))
    dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData('adjusted'))
    pixels = np.rint(np.clip(dialog.viewport.extract(apply_qualifier(source, expected)), 0, 1) * 255).astype(np.uint8)
    np.testing.assert_array_equal(dialog.canvas._pixels, pixels)
    original = dialog.canvas._pixels.copy()
    dialog.controls['window_center_x'].setValue(10)
    dialog.render_preview()
    np.testing.assert_array_equal(dialog.canvas._pixels, original)  # Explicit Add is required.
    assert not dialog.apply_button.isEnabled()
    dialog.add_key(); dialog.render_preview()
    assert np.any(dialog.canvas._pixels != original)
    assert not window.editor.can_undo


def test_previous_next_hidden_keys_hold_and_remove_last_freezes(animation_window):
    window = animation_window
    window.selected_clip().grade.qualifier = replace(settings(),
        window_keyframes=[key(-10, .1), key(1, .4), key(20, .9)])
    dialog = loaded(window)
    assert '2 outside clip' in dialog.key_status.text()
    assert not dialog.previous_button.isEnabled() and dialog.next_button.isEnabled()
    dialog.jump_key(1); assert dialog.local_frame == 1
    assert not dialog.previous_button.isEnabled() and not dialog.next_button.isEnabled()
    dialog.remove_key()
    assert [entry.frame for entry in dialog.settings.window_keyframes] == [-10, 20]
    assert not dialog.remove_button.isEnabled()
    # Explicit flatten intentionally removes hidden handles; keep current evaluation.
    expected = .1 + (.9 - .1) * 11 / 30
    dialog.make_static()
    assert not dialog.settings.window_keyframes
    assert dialog.settings.window_center_x == pytest.approx(expected)
    dialog.add_key(); dialog.controls['window_center_x'].setValue(72); dialog.add_key()
    dialog.remove_key()
    assert not dialog.settings.window_keyframes and dialog.settings.window_center_x == .72
    assert not window.editor.can_undo


def test_untouched_geometry_precision_and_draft_navigation_not_saved(animation_window):
    window = animation_window
    window.selected_clip().grade.qualifier = replace(settings(), window_keyframes=[
        WindowKeyframe(0, .123456789, .456789123, .555555555, .666666666)])
    dialog = loaded(window)
    dialog.controls['window_width'].setValue(88)
    dialog.add_key()
    actual = dialog.settings.window_keyframes[0]
    assert actual.center_x == .123456789 and actual.center_y == .456789123 and actual.height == .666666666
    assert actual.width == .88
    dialog.controls['window_center_x'].setValue(90)
    dialog.frame_slider.setValue(2)
    assert dialog.local_frame == 2 and not dialog._draft_dirty
    assert dialog.settings.window_keyframes[0] == actual
    capture(dialog); dialog.apply_settings()
    assert window.selected_clip().grade.qualifier.window_keyframes == [actual]


@pytest.mark.parametrize('change', ['selection', 'revision', 'frame', 'source', 'grade', 'project', 'closing'])
def test_stale_animation_context_cannot_commit_or_keep_old_pixels(animation_window, change):
    window = animation_window; dialog = loaded(window)
    dialog.controls['window_width'].setValue(60); dialog.add_key()
    if change == 'selection': window.select_clip('other')
    elif change == 'revision': window.revision += 1
    elif change == 'frame': window.timeline.playhead += 1
    elif change == 'source':
        path = window.project.media[0].path; info = os.stat(path)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000))
    elif change == 'grade': window.selected_clip().grade.exposure = .5
    elif change == 'project': window.editor = Editor(deepcopy(window.project))
    elif change == 'closing': window.closing = True
    before = deepcopy(window.project); dialog.apply_settings()
    assert window.project == before and not window.editor.can_undo
    assert dialog.canvas.image.isNull() and dialog.snapshot is None
    assert not dialog.apply_button.isEnabled() and 'stale' in dialog.status.text().lower()


def test_static_qualifier_disables_keyed_geometry_and_evaluates_captured_frame(animation_window):
    window = animation_window; window.timeline.playhead = 1
    window.selected_clip().grade.qualifier = settings()
    dialog = QualifierDialog(window); dialog.show()
    dialog.accept_snapshot(capture_picker_snapshot(window.project, window.selection, 1, stage='qualifier'))
    assert dialog.animation_note.isVisible() and not dialog.window_edit_button.isEnabled()
    for field in GEOMETRY_FIELDS:
        assert not dialog.controls[field].isEnabled()
    assert dialog.controls['window_center_x'].value() == 50
    assert dialog.controls['window_feather'].isEnabled()
    expected = replace(settings(), window_keyframes=[], window_center_x=.5,
                       window_center_y=.5, window_width=.4, window_height=.6)
    np.testing.assert_array_equal(dialog.matte, dialog.viewport.extract(qualifier_mask(dialog.snapshot.frame, expected)))
    pending = deepcopy(dialog.settings)
    dialog.controls['window_center_x'].setValue(99)
    assert dialog.settings == pending and dialog.controls['window_center_x'].value() == 50
    dialog.controls['window_feather'].setValue(20); dialog.apply_settings()
    assert window.selected_clip().grade.qualifier.window_keyframes == settings().window_keyframes
    assert window.selected_clip().grade.qualifier.window_feather == .2


def test_outgoing_mode_and_disabled_grade_preserve_real_bypass(animation_window):
    window = animation_window; window.selected_clip().grade.enabled = False
    window.selected_clip().grade.qualifier = settings()
    dialog = loaded(window)
    dialog.interpolation.setCurrentIndex(dialog.interpolation.findData('hold'))
    assert not dialog.apply_button.isEnabled()
    dialog.add_key()
    assert dialog.settings.window_keyframes[0].interpolation == 'hold'
    dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData('source')); source = dialog.canvas.image.copy()
    dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData('adjusted'))
    assert dialog.canvas.image == source and 'grading is disabled' in dialog.status.text()
    dialog.reject()


def test_refresh_generation_cancel_reopen_and_late_result_are_bounded(animation_window, app, monkeypatch):
    import lumen.ui_window_animation as ui
    window = animation_window
    old = capture_picker_snapshot(window.project, 'picture', 0, stage='qualifier')
    new = capture_picker_snapshot(window.project, 'picture', 2, stage='qualifier')
    entered, release = threading.Event(), threading.Event()
    calls = []
    def capture(project, clip_id, frame, **kwargs):
        calls.append(frame)
        if frame == 0:
            entered.set(); assert release.wait(8)
            return old
        return new
    monkeypatch.setattr(ui, 'capture_picker_snapshot', capture)
    first = open_window_animation(window)
    try:
        assert open_window_animation(window) is first and entered.wait(2)
        first.frame_spin.setValue(1); first.refresh_frame()
        first.frame_spin.setValue(2); first.refresh_frame()
        assert calls == [0]
        first.reject()
        window.timeline.playhead = 2
        second = open_window_animation(window)
        assert second is not first and second.snapshot is None
        assert calls == [0]
        release.set(); wait_until(app, lambda: second.snapshot is not None)
        assert calls == [0, 2] and second.snapshot is new and first.snapshot is None
        assert not window.editor.can_undo and second.current()
    finally:
        release.set()


def test_global_cancel_drops_queued_refresh_but_keeps_pending_keys(animation_window, app, monkeypatch):
    import lumen.ui_window_animation as ui
    window = animation_window; entered, release = threading.Event(), threading.Event(); calls = []
    original = capture_picker_snapshot(window.project, 'picture', 0, stage='qualifier')
    def capture(*args, **kwargs):
        calls.append(args[2]); entered.set(); assert release.wait(8); return original
    monkeypatch.setattr(ui, 'capture_picker_snapshot', capture)
    dialog = open_window_animation(window)
    try:
        assert entered.wait(2)
        dialog.add_key(); dialog.frame_spin.setValue(2); dialog.refresh_frame()
        pending = deepcopy(dialog.settings)
        window.cancel_jobs(); release.set(); wait_until(app, lambda: not window.jobs)
        assert calls == [0] and dialog.snapshot is None and dialog.settings == pending
        assert not dialog.apply_button.isEnabled()
    finally:
        release.set()


@pytest.mark.parametrize('finish', ['close', 'cancel', 'apply'])
def test_repeated_dialog_release_and_return_key_never_accidentally_apply(animation_window, app, finish):
    window = animation_window
    for _ in range(3):
        dialog = loaded(window); old = dialog.snapshot
        dialog.controls['window_width'].setFocus()
        QTest.keyClick(dialog.controls['window_width'], Qt.Key.Key_Return)
        assert dialog.isVisible() and not window.editor.can_undo
        if finish == 'close': dialog.close()
        elif finish == 'cancel': dialog.reject()
        else: dialog.apply_settings()
        dialog.accept_snapshot(old); dialog.render_preview()
        assert dialog.snapshot is None and dialog.matte is None and dialog.canvas.image.isNull()
        assert not dialog.preview_timer.isActive() and not dialog.timer.isActive()


def test_supported_locales_focus_and_narrow_accessibility(animation_window, app):
    animation_window.selected_clip().grade.qualifier = settings()
    dialog = loaded(animation_window); dialog.resize(360, 600); app.processEvents()
    for locale in ('zh-CN', 'en'):
        before = deepcopy(dialog.settings); set_locale(locale); app.processEvents()
        assert dialog.width() == 360 and dialog.scroll.horizontalScrollBar().maximum() == 0
        assert dialog.apply_button.isVisible() and dialog.close_button.isVisible()
        for control in [*dialog.controls.values(), dialog.interpolation, dialog.add_button, dialog.clear_button]:
            control.setFocus(); app.processEvents()
            assert control.hasFocus()
            assert control.visibleRegion().boundingRect().height() == control.height()
        focused = dialog.controls['window_width']; focused.setFocus(); app.processEvents()
        dialog.render_preview(); dialog.check_current(); app.processEvents()
        assert focused.hasFocus() and dialog.settings == before
        assert all(spin.accessibleName() for spin in dialog.controls.values())


def test_oversized_duration_rejected_before_qt_overflow_and_hidden_keys_stay_supported(animation_window, monkeypatch):
    window = animation_window; clip = window.selected_clip()
    clip.duration = MAX_VISIBLE_FRAMES + 1
    warnings = []; monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(str(args[2])))
    assert open_window_animation(window) is None and '2,147,483,648' in warnings[0]
    with pytest.raises(ValueError, match='supports clips'):
        WindowAnimationDialog(window)
    clip.duration = 3
    clip.grade.qualifier = replace(settings(), window_keyframes=[key(-(2**53 - 1), .2), key(2**53 - 1, .8)])
    dialog = loaded(window)
    assert len(dialog.settings.window_keyframes) == 2 and '2 outside clip' in dialog.key_status.text()
    dialog.add_key()
    assert [entry.frame for entry in dialog.settings.window_keyframes] == [-(2**53 - 1), 0, 2**53 - 1]


def test_animation_panel_button_selection_and_workspace_boundaries(animation_window):
    window = animation_window
    window.refresh_inspector(); window.set_workspace_mode('professional')
    assert window.window_animation_button.isEnabled() and not window.window_animation_button.isHidden()
    window.set_workspace_mode('simple')
    assert window.window_animation_button.isHidden()
    window.set_workspace_mode('professional')
    window.active_selection_type = 'effect'; window.refresh_inspector()
    assert not window.window_animation_button.isEnabled() and open_window_animation(window) is None
    window.active_selection_type = 'clip'; window.selected_clip().track = 2; window.refresh_inspector()
    assert not window.window_animation_button.isEnabled() and open_window_animation(window) is None
    window.selected_clip().track = 0; window.select_clip('')
    assert not window.window_animation_button.isEnabled() and open_window_animation(window) is None


def test_native_copy_paste_warning_and_hidden_timing_without_scaling(animation_window):
    window = animation_window
    window.selected_clip().grade.qualifier = replace(settings(), window_keyframes=[key(-10, .1), key(2, .9)])
    window.refresh_inspector(); assert not window.grade_animation_note.isHidden()
    assert 'no duration or speed scaling' in window.grade_animation_note.text()
    window.copy_selected_grade(); window.select_clip('other')
    assert window.selected_clip().duration == 1
    assert not window.grade_animation_note.isHidden()
    window.paste_selected_grade()
    assert [key.frame for key in window.selected_clip().grade.qualifier.window_keyframes] == [-10, 2]
    assert len(window.editor._undo) == 1
    window.undo(); assert not window.selected_clip().grade.qualifier.window_keyframes


@pytest.mark.parametrize('channel', ['saturation', 'luma'])
def test_animated_invalid_range_then_late_snapshot_stays_disarmed_until_repaired(animation_window, channel):
    window = animation_window
    window.selected_clip().grade.qualifier = settings()
    window.timeline.playhead = 1
    before = deepcopy(window.project)
    dialog = QualifierDialog(window); dialog.show()
    # Hold a real completed decode while UI range edits become temporarily invalid.
    snapshot = capture_picker_snapshot(window.project, window.selection, 1, stage='qualifier')
    displayed_geometry = {field: dialog.controls[field].value() for field in GEOMETRY_FIELDS}
    dialog.controls[channel + '_low'].setValue(80)
    dialog.controls[channel + '_high'].setValue(20)
    assert not dialog.pick_button.isEnabled() and not dialog.apply_button.isEnabled()
    dialog.accept_snapshot(snapshot)
    assert dialog.snapshot is snapshot and dialog.canvas.image.isNull()
    assert not dialog.pick_button.isEnabled() and not dialog.pick_button.isChecked()
    assert not dialog.apply_button.isEnabled() and not dialog.window_edit_button.isEnabled()
    for field, value in displayed_geometry.items():
        assert not dialog.controls[field].isEnabled() and dialog.controls[field].value() == value
    assert 'minimum' in dialog.status.text()
    assert window.project == before and not window.editor.can_undo
    dialog.controls[channel + '_high'].setValue(90)
    dialog.render_preview()
    assert dialog.pick_button.isEnabled() and dialog.apply_button.isEnabled()
    assert not dialog.canvas.image.isNull() and dialog.matte is not None
    assert dialog.settings.window_keyframes == settings().window_keyframes
    assert window.project == before and not window.editor.can_undo


def test_animation_delayed_decode_error_then_retry_clears_pixels_and_preserves_staging(animation_window, app, monkeypatch):
    import lumen.ui_window_animation as ui
    window = animation_window
    dialog = loaded(window)
    valid_snapshot = dialog.snapshot
    before_project = deepcopy(window.project)
    dialog.controls['window_width'].setValue(74); dialog.add_key()
    staged = deepcopy(dialog.settings)
    assert dialog.apply_button.isEnabled() and not dialog.canvas.image.isNull()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def decode(project, clip_id, frame, **kwargs):
        calls.append((clip_id, frame))
        if len(calls) == 1:
            entered.set(); assert release.wait(8)
            raise ValueError('Synthetic animation decode failure')
        return valid_snapshot

    monkeypatch.setattr(ui, 'capture_picker_snapshot', decode)
    try:
        dialog.refresh_frame()
        assert entered.wait(2)
        assert dialog.snapshot is None and dialog.canvas.image.isNull()
        assert not dialog.apply_button.isEnabled() and dialog.settings == staged
        release.set()
        wait_until(app, lambda: not window.jobs and 'Synthetic animation decode failure' in dialog.status.text())
        assert dialog.snapshot is None and dialog.matte is None and dialog.canvas.image.isNull()
        assert not dialog.apply_button.isEnabled() and dialog.settings == staged
        assert window.project == before_project and not window.editor.can_undo
        dialog.refresh_frame()
        wait_until(app, lambda: dialog.snapshot is valid_snapshot and not window.jobs)
        assert len(calls) == 2 and dialog.current()
        assert dialog.apply_button.isEnabled() and not dialog.canvas.image.isNull()
        assert dialog.settings == staged and window.project == before_project
        assert not window.editor.can_undo
    finally:
        release.set()


@pytest.mark.parametrize('mismatch', ['stage', 'frame'])
def test_animation_wrong_snapshot_clears_old_pixels_until_valid_capture(animation_window, mismatch):
    window = animation_window
    dialog = loaded(window)
    valid_snapshot = dialog.snapshot
    before_project = deepcopy(window.project)
    dialog.controls['window_height'].setValue(79); dialog.add_key()
    staged = deepcopy(dialog.settings)
    assert dialog.apply_button.isEnabled() and not dialog.canvas.image.isNull()
    # Change only returned snapshot metadata, preserving the real decoded frame.
    wrong = (replace(valid_snapshot, stage='hue') if mismatch == 'stage' else
             replace(valid_snapshot, identity=replace(valid_snapshot.identity, timeline_frame=1)))
    dialog.accept_snapshot(wrong)
    assert dialog.snapshot is None and dialog.matte is None and dialog.canvas.image.isNull()
    assert not dialog.apply_button.isEnabled() and 'does not match' in dialog.status.text()
    assert dialog.settings == staged and window.project == before_project
    assert not window.editor.can_undo
    dialog.accept_snapshot(valid_snapshot)
    assert dialog.snapshot is valid_snapshot and dialog.current()
    assert dialog.apply_button.isEnabled() and not dialog.canvas.image.isNull()
    assert dialog.settings == staged and window.project == before_project
    assert not window.editor.can_undo


def test_wrapped_enable_label_preserves_mouse_space_and_disabled_semantics(animation_window, app):
    dialog = loaded(animation_window)
    label, check = dialog.enabled_label, dialog.enabled_check
    dialog.scroll.ensureWidgetVisible(label); app.processEvents()
    before = check.isChecked()
    QTest.mouseClick(label, Qt.MouseButton.LeftButton)
    assert check.isChecked() is not before and check.hasFocus()
    QTest.keyClick(check, Qt.Key.Key_Space)
    assert check.isChecked() is before
    check.setEnabled(False)
    QTest.mouseClick(label, Qt.MouseButton.LeftButton)
    assert check.isChecked() is before
    check.setEnabled(True)
    QTest.mousePress(label, Qt.MouseButton.LeftButton)
    QTest.mouseRelease(label, Qt.MouseButton.LeftButton, pos=label.rect().bottomRight() + QPoint(4, 4))
    assert check.isChecked() is before
    dialog.reject()


def test_wrapping_action_keeps_native_keyboard_activation_and_disabled_state(app):
    from lumen.ui_window_animation import _WrappingActionButton
    button = _WrappingActionButton('Make current geometry static')
    button.setAutoDefault(False); button.resize(160, 100); button.show(); button.setFocus()
    calls = []
    button.clicked.connect(lambda: calls.append(True))
    app.processEvents()
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert calls == [True]
    button.setDefault(True)
    QTest.keyClick(button, Qt.Key.Key_Return)
    assert calls == [True, True]
    button.setEnabled(False)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert calls == [True, True]
    button.close()


def test_animation_rows_reflow_in_place_for_locale_and_font(animation_window, app):
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QBoxLayout
    dialog = loaded(animation_window); dialog.resize(320, 640)
    try:
        for locale in ('zh-CN', 'en', 'zh-CN'):
            set_locale(locale)
            for points in (16, 10):
                font = QFont(dialog.font()); font.setPointSize(points); dialog.setFont(font)
                QTest.qWait(80)
                margins = dialog.scroll.widget().layout().contentsMargins()
                available = dialog.scroll.viewport().width() - margins.left() - margins.right()
                for row in (dialog.navigation_row, dialog.actions_row):
                    needed = sum(row.itemAt(i).widget().sizeHint().width() for i in range(row.count())) + row.spacing()
                    expected = (QBoxLayout.Direction.LeftToRight if needed <= available
                                else QBoxLayout.Direction.TopToBottom)
                    assert row.direction() == expected, (locale, points, needed, available, dialog._rows_pending)
                assert dialog.scroll.horizontalScrollBar().maximum() == 0
                assert dialog.size().toTuple() == (320, 640)
    finally:
        dialog.reject(); set_locale('en')


def test_animation_layout_converges_when_idle_across_fonts_and_widths(animation_window, app, monkeypatch):
    from PySide6.QtGui import QFont, QFontInfo
    import time
    from scripts.qt_layout_font import prepare_layout_font
    original_font = QFont(app.font())
    proof = prepare_layout_font(app)
    calls = []
    original = WindowAnimationDialog._fit_rows
    def counted(dialog):
        original(dialog)
        calls.append(1)
    monkeypatch.setattr(WindowAnimationDialog, '_fit_rows', counted)
    dialog = loaded(animation_window)
    try:
        for family in ('Sans Serif', proof['cjk']['resolved_family']):
            for locale in ('en', 'zh-CN'):
                set_locale(locale)
                for points in (10, 13, 16):
                    for width in (320, 360, 740):
                        font = QFont(dialog.font()); font.setFamily(family); font.setPointSize(points)
                        dialog.setFont(font); dialog.resize(width, 760 if width == 740 else 640)
                        if family == proof['cjk']['resolved_family']:
                            assert QFontInfo(dialog.font()).family() == family
                        # Drain queued font/locale/layout work, with a bounded
                        # quiet period; then independently assert zero idle work.
                        deadline = time.monotonic() + 2
                        quiet = 0
                        while quiet < 6 and time.monotonic() < deadline:
                            previous = len(calls)
                            QTest.qWait(50); app.processEvents()
                            quiet = quiet + 1 if len(calls) == previous and not dialog._rows_pending else 0
                        assert quiet == 6, (family, locale, points, width, len(calls))
                        before = len(calls)
                        QTest.qWait(200); app.processEvents()
                        assert len(calls) == before, repr((family, QFontInfo(dialog.font()).family(),
                            locale, points, width, before, len(calls)))
    finally:
        dialog.reject(); set_locale('en'); app.setFont(original_font)


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_long_preview_options_have_visible_keyboard_description(animation_window, app, locale):
    import json
    from pathlib import Path
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QFont
    from scripts.qt_layout_font import prepare_layout_font
    original_font = QFont(app.font())
    proof = prepare_layout_font(app)
    set_locale(locale)
    dialog = loaded(animation_window)
    font = QFont(dialog.font()); font.setFamily(proof['cjk']['resolved_family']); font.setPointSize(16)
    dialog.setFont(font); dialog.resize(320, 640)
    before = deepcopy(animation_window.project)
    snapshot = dialog.snapshot
    try:
        combo = dialog.preview_choice
        dialog.status.setFocus(); QTest.qWait(100)
        status = dialog.status
        if status.height() <= dialog.scroll.viewport().height():
            assert status.visibleRegion().boundingRect() == status.rect()
        else:
            assert status.visibleRegion().contains(QPoint(status.width() // 2, 0))
            assert status.visibleRegion().boundingRect().width() == status.width()
            top = status.mapTo(dialog.scroll.widget(), QPoint()).y()
            dialog.scroll.verticalScrollBar().setValue(top + status.height() - dialog.scroll.viewport().height() + 8)
            app.processEvents()
            assert status.visibleRegion().contains(QPoint(status.width() // 2, status.height() - 1))
            assert status.visibleRegion().boundingRect().width() == status.width()
        status_bounds = dialog.status.fontMetrics().boundingRect(
            QRect(0, 0, dialog.status.contentsRect().width(), 10000),
            int(Qt.TextFlag.TextWordWrap), dialog.status.text())
        assert status_bounds.height() <= dialog.status.contentsRect().height()
        assert status_bounds.width() <= dialog.status.contentsRect().width()
        assert dialog.scroll.geometry().bottom() < dialog.apply_button.mapTo(dialog, QPoint()).y()
        combo.setFocus(); QTest.qWait(100)
        for index in range(combo.count()):
            assert combo.currentIndex() == index
            label = dialog.preview_description
            assert label.text() == combo.currentText() == combo.accessibleDescription()
            assert combo.currentText() in combo.toolTip()
            assert combo.itemData(index, Qt.ItemDataRole.ToolTipRole) == combo.itemText(index)
            assert label.visibleRegion().boundingRect() == label.rect()
            bounds = label.fontMetrics().boundingRect(QRect(0, 0, label.contentsRect().width(), 10000),
                int(Qt.TextFlag.TextWordWrap), label.text())
            assert bounds.height() <= label.contentsRect().height()
            assert bounds.width() <= label.contentsRect().width()
            assert combo.hasFocus() and dialog.snapshot is snapshot
            evidence = os.environ.get('AUTO_FRAME_REVIEW_EVIDENCE')
            if evidence:
                folder = Path(evidence) / 'window-animation-preview-options'
                folder.mkdir(parents=True, exist_ok=True)
                stem = f'{locale}-{index}'
                assert dialog.grab().save(str(folder / f'{stem}.png'))
                (folder / f'{stem}.json').write_text(json.dumps(dict(
                    locale=locale, index=index, mode=combo.currentData(), text=label.text(),
                    dpr=dialog.devicePixelRatioF(), window=dialog.size().toTuple(),
                    rect=label.rect().getRect(), visible=label.visibleRegion().boundingRect().getRect(),
                    text_bounds=bounds.getRect(), font=proof), ensure_ascii=False, indent=2), encoding='utf-8')
            if index + 1 < combo.count():
                QTest.keyClick(combo, Qt.Key.Key_Down); QTest.qWait(100)
        assert animation_window.project == before and not animation_window.editor.can_undo
        set_locale('zh-CN' if locale == 'en' else 'en'); QTest.qWait(100)
        assert dialog.preview_description.text() == combo.currentText() == combo.accessibleDescription()
        assert combo.itemData(combo.currentIndex(), Qt.ItemDataRole.ToolTipRole) == combo.currentText()
        combo.setCurrentIndex(0)
        field = dialog.controls['window_center_x']; field.setFocus(); QTest.qWait(100)
        assert field.hasFocus()
        assert field.visibleRegion().boundingRect() == field.rect()
        label = dialog.control_labels[field]
        assert label.visibleRegion().boundingRect() == label.rect()
    finally:
        dialog.reject(); set_locale('en'); app.setFont(original_font)


def test_scrolled_action_error_is_discoverable_without_stealing_focus(animation_window, app):
    animation_window.selected_clip().grade.qualifier = settings()
    dialog = loaded(animation_window); dialog.resize(320, 640)
    before = deepcopy(animation_window.project); draft = deepcopy(dialog.settings)
    try:
        dialog.select_key(0); dialog.key_time.setFocus(); QTest.qWait(100)
        assert dialog.key_time.hasFocus()
        assert not dialog.move_key(0, 2)
        QTest.qWait(100)
        assert 'Move rejected' in dialog.status.text()
        assert dialog.key_time.hasFocus()
        assert dialog.status.visibleRegion().contains(QPoint(dialog.status.width() // 2, 0))
        assert dialog.settings == draft and animation_window.project == before
        # The full status is also in the ordinary keyboard focus chain.
        for _ in range(40):
            if dialog.status.hasFocus():
                break
            QTest.keyClick(dialog.focusWidget(), Qt.Key.Key_Tab); app.processEvents()
        assert dialog.status.hasFocus()
        assert dialog.status.visibleRegion().contains(QPoint(dialog.status.width() // 2, 0))
        # A subsequent navigation wins over an earlier queued error reveal.
        assert not dialog.move_key(0, 2)
        field = dialog.controls['window_center_x']; field.setFocus(); QTest.qWait(100)
        assert field.hasFocus() and field.visibleRegion().boundingRect() == field.rect()
        assert dialog.settings == draft and animation_window.project == before
    finally:
        dialog.reject()

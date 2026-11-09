"""Static spatial restrictions use original canvas coordinates in every local view."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from uuid import uuid4

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from lumen.i18n import set_locale, tr
from lumen.model import Project
from lumen.project_recovery import RecoveryStore
from lumen.qualifier import QualifierSettings, apply_qualifier, qualifier_mask
from test_qualifier_ui import app, loaded, window
from test_qualifier_picker_ui import click_sample


def prepare(dialog, shape='ellipse'):
    dialog.enabled_check.setChecked(True)
    dialog.controls['hue_width'].setValue(180)
    dialog.controls['exposure'].setValue(.5)
    dialog.window_shape.setCurrentIndex(dialog.window_shape.findData(shape))
    for key, value in [('window_center_x', 29), ('window_center_y', 54),
                       ('window_width', 36), ('window_height', 57), ('window_feather', 34)]:
        dialog.controls[key].setValue(value)
    dialog.render_preview()


@pytest.mark.parametrize('shape', ['ellipse', 'rectangle'])
@pytest.mark.parametrize('invert', [False, True])
def test_static_window_viewport_matches_full_canvas_for_all_views(window, app, shape, invert):
    dialog = loaded(window)
    # Identical colors at different positions must no longer all be corrected.
    source = np.empty((729, 1103, 3), np.float32); source[:] = [.41357, .13179, .21753]
    source.setflags(write=False)
    dialog.accept_snapshot(replace(dialog.snapshot, frame=source,
        valid_mask=np.ones(source.shape[:2], bool)))
    prepare(dialog, shape); dialog.window_invert.setChecked(invert)
    settings = deepcopy(dialog.settings); original = source.copy()
    full_mask = qualifier_mask(source, replace(settings, enabled=True))
    full_adjusted = apply_qualifier(source, settings)
    assert np.any(full_mask == 0) and np.any(full_mask == 1)
    assert np.any((full_mask > 0) & (full_mask < 1))
    assert np.any(full_adjusted != source)
    for zoom, x, y in [(0, 0, 0), (1, 321, 305), (2, 1102, 728), (4, 0, 0), (8, 400, 405)]:
        dialog.zoom_choice.setCurrentIndex(dialog.zoom_choice.findData(zoom))
        dialog.set_detail_center(x, y)
        for mode in ['matte', 'source', 'overlay', 'adjusted']:
            dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData(mode)); dialog.render_preview()
            expected_mask = dialog.viewport.extract(full_mask)
            np.testing.assert_array_equal(dialog.matte, expected_mask)
            sample = dialog.viewport.extract(source)
            if mode == 'matte': expected = np.repeat(expected_mask[..., None], 3, axis=-1)
            elif mode == 'source': expected = sample
            elif mode == 'adjusted': expected = dialog.viewport.extract(full_adjusted)
            else:
                alpha = expected_mask[..., None] * np.float32(.5)
                expected = sample * (np.float32(1) - alpha) + np.array([1, 0, 1], np.float32) * alpha
            np.testing.assert_array_equal(dialog.canvas._pixels, np.rint(np.clip(expected, 0, 1) * 255).astype(np.uint8))
        dialog.pan_view(19, -11)
    assert dialog.settings == settings
    np.testing.assert_array_equal(source, original)
    assert not source.flags.writeable and not window.editor.can_undo


@pytest.mark.parametrize('shape', ['ellipse', 'rectangle'])
def test_static_window_picker_preserves_exact_geometry_even_outside_window(window, app, shape):
    settings = QualifierSettings(enabled=True, window_shape=shape,
        window_center_x=.123456789, window_center_y=.234567891,
        window_width=.111234567, window_height=.112345678,
        window_feather=.345678912, window_invert=True)
    window.selected_clip().grade.qualifier = deepcopy(settings)
    dialog = loaded(window); dialog.preview_choice.setCurrentIndex(3); app.processEvents()
    before = asdict(dialog.settings)
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
    click_sample(dialog, 16, 16)
    assert not dialog.canvas.picking
    for key, value in before.items():
        if key.startswith('window_'): assert getattr(dialog.settings, key) == value
    assert not window.editor.can_undo


def test_static_window_apply_persist_recovery_and_undo_are_single_transaction(window, tmp_path):
    before = deepcopy(window.project); dialog = loaded(window); prepare(dialog)
    pending = deepcopy(dialog.settings); dialog.apply_settings()
    assert window.selected_clip().grade.qualifier == pending and len(window.editor._undo) == 1
    after = deepcopy(window.project)
    path = tmp_path / 'window.lumen'; window.project.save(path)
    saved = json.loads(path.read_text())
    assert saved['clips'][0]['grade']['qualifier'] == asdict(pending)
    assert Project.load(path) == after
    with RecoveryStore(tmp_path / 'window-recovery') as store:
        assert store.load(store.write(window.project, uuid4())) == after
    window.undo(); assert window.project == before
    window.redo(); assert window.project == after
    again = loaded(window)
    assert again.settings == pending
    assert again.window_shape.currentData() == pending.window_shape
    again.apply_settings(); assert len(window.editor._undo) == 1


@pytest.mark.parametrize('action', ['cancel', 'revision', 'frame', 'geometry'])
def test_static_window_stale_or_closed_controls_cannot_change_pending_or_project(window, action):
    before = deepcopy(window.project); dialog = loaded(window); prepare(dialog)
    if action == 'cancel': dialog.reject()
    elif action == 'revision': window.revision += 1
    elif action == 'frame': window.timeline.playhead += 1
    elif action == 'geometry': window.project.width += 2
    dialog.check_current()
    expected = deepcopy(dialog.settings); project = deepcopy(window.project)
    assert not dialog.window_shape.isEnabled() and not dialog.window_invert.isEnabled()
    assert all(not control.isEnabled() for control in dialog.controls.values())
    dialog.change_setting('window_width', .9); dialog.apply_settings()
    assert dialog.settings == expected and window.project == project and not window.editor.can_undo
    if action != 'geometry': assert project == before


def test_static_window_untouched_precision_and_enter_never_accidentally_apply(window, app):
    precise = QualifierSettings(window_center_x=.123456789, window_width=.123456789)
    window.selected_clip().grade.qualifier = deepcopy(precise)
    dialog = loaded(window)
    dialog.controls['window_height'].setFocus()
    QTest.keyClick(dialog.controls['window_height'], Qt.Key.Key_Return)
    assert dialog.isVisible() and not window.editor.can_undo
    dialog.window_shape.setCurrentIndex(dialog.window_shape.findData('rectangle'))
    assert dialog.settings.window_center_x == precise.window_center_x
    assert dialog.settings.window_width == precise.window_width


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_static_window_all_new_visible_strings_localized(locale):
    from lumen.ui_qualifier import _WINDOW_CONTROLS, RANGE_TEXT
    from lumen.i18n_catalog_qualifier import ZH_CN
    keys = [row[1] for row in _WINDOW_CONTROLS]
    keys += ['Static spatial window', 'Window shape', 'Off (color ranges only)',
             'Ellipse', 'Rectangle', 'Invert window only', RANGE_TEXT]
    keys += [key for key in ZH_CN if key.startswith('Window coordinates') or key.startswith('Matte: color-range')]
    set_locale(locale)
    try:
        for key in keys:
            assert key in ZH_CN
            assert str(tr(key)) == (key if locale == 'en' else ZH_CN[key])
            if locale == 'zh-CN': assert str(tr(key)) != key
    finally: set_locale('en')

@pytest.mark.parametrize('message', [
    'Window invert must be a boolean',
    'Window shape must be off, ellipse or rectangle',
    'Spatial windows require a two-dimensional RGB image',
    'Invalid spatial window canvas coordinates',
    'Spatial window samples must lie inside the canvas',
    'Version 18 requires exactly the legacy qualifier fields',
    'Qualifier window_width must be finite and in 0.001..2',
    'Qualifier window_center_x must be finite and in 0..1',
])
def test_static_window_errors_have_localized_messages(message):
    from lumen.i18n import tr_error
    try:
        set_locale('en'); assert str(tr_error(message)) == message
        set_locale('zh-CN'); result = str(tr_error(message))
        assert result != message and '技术错误详情' not in result
        assert any('\u4e00' <= c <= '\u9fff' for c in result)
    finally: set_locale('en')

"""Actual source-pixel feather sampling, pending transactions and bilingual UI."""
from copy import deepcopy
from dataclasses import asdict, replace
import os
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from lumen.i18n import set_locale, tr_error
from lumen.qualifier import QualifierRange, QualifierSettings
from lumen.ui_qualifier import SOFTNESS_HELP_TEXT
from test_qualifier_ui import app, loaded, window
from test_qualifier_picker_ui import click_sample
from test_qualifier_sets_ui_review import choose


def prepare(window, rgb=(.6, .3, .3), valid=True):
    dialog = loaded(window)
    frame = np.tile(np.array(rgb, np.float32), (16, 16, 1))
    frame.setflags(write=False)
    mask = np.full((16, 16), valid, bool); mask.setflags(write=False)
    dialog.accept_snapshot(replace(dialog.snapshot, frame=frame, valid_mask=mask))
    core = dict(hue_center=0, hue_width=.05, saturation_low=.6,
                saturation_high=.9, luma_low=.5, luma_high=.7,
                hue_softness=0, saturation_softness=0, luma_softness=0)
    dialog.settings = QualifierSettings(**core, inclusion_ranges=[QualifierRange(**core)],
                                        exclusion_ranges=[QualifierRange(**core)])
    dialog.refresh_ranges(); dialog.render_preview()
    choose(dialog.sample_mode, 'soften')
    return dialog


def pick(dialog, app):
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert dialog.canvas.picking
    click_sample(dialog, 8, 8)


@pytest.mark.parametrize('target', [('base', 0), ('inclusion_ranges', 0), ('exclusion_ranges', 0)])
@pytest.mark.parametrize('view', ['source', 'overlay'])
def test_exact_channel_range_source_and_cancel(window, app, target, view):
    dialog = prepare(window); project = deepcopy(window.project)
    choose(dialog.range_choice, target); choose(dialog.softness_channel, 'saturation')
    choose(dialog.preview_choice, view)
    before = asdict(dialog.settings); source = dialog.snapshot.frame.copy()
    pick(dialog, app)
    after = asdict(dialog.settings)
    selected = after if target[0] == 'base' else after[target[0]][0]
    # Frozen source HSV-S is .5, not the magenta overlay's saturation.
    assert selected['saturation_softness_low'] == pytest.approx(.2, abs=1e-7)
    selected['saturation_softness_low'] = None
    assert after == before
    assert dialog.range_choice.currentData() == target
    assert dialog.sample_source_position == (8, 8)
    assert dialog.preview_choice.currentData() == ('overlay' if view == 'overlay' else 'matte')
    assert not dialog.canvas.picking and 'increased' in dialog.sample_label.text()
    first = deepcopy(dialog.settings); pick(dialog, app)
    assert dialog.settings == first and 'unchanged' in dialog.sample_label.text()
    np.testing.assert_array_equal(source, dialog.snapshot.frame)
    assert not dialog.snapshot.frame.flags.writeable
    assert window.project == project and not window.editor.can_undo
    dialog.reject(); assert window.project == project and not window.editor.can_undo


def test_softness_apply_is_one_transaction_and_undo_redo(window, app):
    dialog = prepare(window); project = deepcopy(window.project)
    choose(dialog.softness_channel, 'luma'); pick(dialog, app)
    expected = deepcopy(dialog.settings)
    dialog.apply_button.click()
    assert window.selected_clip().grade.qualifier == expected and len(window.editor._undo) == 1
    window.undo(); assert window.project == project
    window.redo(); assert window.selected_clip().grade.qualifier == expected


@pytest.mark.parametrize('channel', ['hue', 'saturation', 'luma'])
def test_gray_explicit_channel_and_hue_gate(window, app, channel):
    dialog = prepare(window, (.4, .4, .4))
    dialog.settings.saturation_low = .2
    dialog.refresh_ranges()
    choose(dialog.softness_channel, channel); before = deepcopy(dialog.settings)
    pick(dialog, app)
    if channel == 'hue':
        assert dialog.settings == before and dialog.canvas.picking
        assert 'hue' in dialog.sample_label.text().lower()
    else:
        assert dialog.settings != before and not dialog.canvas.picking
        assert 'undefined' in dialog.sample_label.toolTip()
        assert not np.any(dialog.matte)  # Narrow hue core still rejects gray.
    assert not window.editor.can_undo


@pytest.mark.parametrize('kind', ['padding', 'overrange', 'impossible', 'stale'])
def test_invalid_sample_preserves_pending_and_history(window, app, kind):
    dialog = prepare(window, (1.1, .3, .3) if kind == 'overrange' else (.6, .3, .3), kind != 'padding')
    choose(dialog.softness_channel, 'saturation')
    if kind == 'impossible':
        dialog.settings.saturation_low = .99; dialog.settings.saturation_high = 1
        dialog.snapshot = replace(dialog.snapshot, frame=np.full((16, 16, 3), .4, np.float32))
        dialog.refresh_ranges(); dialog.render_preview()
    before = deepcopy(dialog.settings); project = deepcopy(window.project)
    if kind == 'stale':
        dialog.pick_button.click(); window.revision += 1; dialog.pick_sample(8, 8)
        assert not dialog.softness_channel.isEnabled()
    else:
        pick(dialog, app); assert dialog.canvas.picking
    assert dialog.settings == before and window.project == project and not window.editor.can_undo


def test_target_channel_mode_change_disarms(window, app):
    dialog = prepare(window)
    for combo, value in [(dialog.softness_channel, 'luma'), (dialog.range_choice, ('inclusion_ranges', 0)),
                         (dialog.sample_mode, 'add')]:
        dialog.pick_button.click(); assert dialog.canvas.picking
        choose(combo, value); assert not dialog.canvas.picking
    assert not dialog.softness_row.isVisible()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('size', [(320, 600), (760, 800)])
def test_softness_bilingual_compact_layout_and_screenshots(window, app, locale, size):
    from lumen.ui_style import apply_ui_layout_tokens
    set_locale(locale); dialog = prepare(window); dialog.resize(*size)
    apply_ui_layout_tokens(dialog)
    choose(dialog.range_choice, ('exclusion_ranges', 0))
    choose(dialog.softness_channel, 'saturation')
    import colorsys
    frame = np.array([[colorsys.hsv_to_rgb(x / 16, .25 + y / 24, .85)
                       for x in range(16)] for y in range(16)], np.float32)
    frame.setflags(write=False)
    dialog.snapshot = replace(dialog.snapshot, frame=frame)
    dialog.controls['saturation_softness_low'].setValue(20)
    choose(dialog.preview_choice, 'overlay'); dialog.render_preview()
    QTest.qWait(120)
    assert dialog.width() == size[0]
    for control in [dialog.softness_channel, dialog.softness_target, dialog.pick_button,
                    dialog.sample_mode, dialog.apply_button, dialog.close_button]:
        assert control.visibleRegion().boundingRect() == control.rect()
    from PySide6.QtCore import QPoint, QRect
    channel_rect = QRect(dialog.softness_channel.mapTo(dialog, QPoint()), dialog.softness_channel.size())
    target_rect = QRect(dialog.softness_target.mapTo(dialog, QPoint()), dialog.softness_target.size())
    assert channel_rect.intersected(target_rect).isEmpty()
    assert dialog.scroll.viewport().height() > 40
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
    assert dialog.range_choice.currentText() in dialog.softness_target.text()
    assert dialog.softness_channel.currentText() in dialog.softness_target.text()
    assert ('50%' in dialog.sample_label.text())
    if locale == 'zh-CN':
        assert dialog.sample_mode.currentText() == '柔化'
        assert '核心边界' in dialog.sample_label.text()
    folder = os.environ.get('QUALIFIER_SOFTNESS_SCREENSHOTS')
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        assert dialog.grab().save(str(Path(folder) / f'softness-{locale}-{size[0]}-dpr{dialog.devicePixelRatioF():g}.png'))


@pytest.mark.parametrize('visible', [False, True])
def test_real_qualifier_owner_attribute_queued_layout_and_close(app, tmp_path, monkeypatch, visible):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QMessageBox, QWidget
    from shiboken6 import isValid
    from lumen.model import Clip, Editor
    from lumen.ui import MainWindow
    from lumen.ui_style import apply_ui_layout_tokens
    from scripts.qt_test_cleanup import cleanup_test_windows
    from test_selective_color_render import source_project

    existing = list(app.topLevelWidgets())
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: pytest.fail(str(args[2])))
    window = MainWindow(workspace_settings=QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat),
                        recovery_root=tmp_path / 'recovery', ui_language='en')
    try:
        project = source_project(tmp_path)
        project.clips.append(Clip('other', project.media[0].id, 0, 2, 0, 1))
        window.editor = Editor(project)
        window.refresh(); window.select_clip('picture')
        window.resize(1280, 800); window.show(); app.processEvents()
        dialog = prepare(window)
        apply_ui_layout_tokens(dialog)
        controller = dialog._lumen_layout_controller
        assert dialog.window is window and not callable(dialog.window)
        assert dialog.isWindow() and QWidget.window(dialog) is dialog
        before_project = deepcopy(window.project)
        before_settings = asdict(dialog.settings)
        before_controls = {key: (spin.value(), spin.accessibleName(), spin.accessibleDescription())
                           for key, spin in dialog.controls.items()}
        changes = []
        for spin in dialog.controls.values():
            spin.valueChanged.connect(lambda *_: changes.append(True))
        dialog.setVisible(visible)
        controller._refresh_scheduled()
        for _ in range(12):
            app.processEvents()
        assert dialog.isVisible() is visible and dialog.window is window
        assert not controller._pending and not controller._refreshing
        assert asdict(dialog.settings) == before_settings
        assert {key: (spin.value(), spin.accessibleName(), spin.accessibleDescription())
                for key, spin in dialog.controls.items()} == before_controls
        assert not changes and window.project == before_project and not window.editor.can_undo
        dialog.reject()
        window.cancel_jobs()
        for job in list(window.jobs):
            assert job.wait(5000)
        window.dirty = False; window.closing = True
        window.close()
        for _ in range(12):
            app.processEvents()
        assert dialog._closed_flag and not dialog.isVisible()
        assert window.project == before_project and not window.editor.can_undo
        assert not controller._pending and not controller._refreshing
        # Leave the real controller connected. Its actual pending callback must
        # run inside cleanup_test_windows, not merely disappear when Qt deletes
        # its owner; the observer calls the unchanged callback and hides nothing.
        phase = {'inside_cleanup': False}
        callbacks = []
        original = controller._refresh_scheduled

        def observed_callback():
            record = dict(inside_cleanup=phase['inside_cleanup'],
                          window_valid=isValid(window), dialog_valid=isValid(dialog),
                          returned=False)
            callbacks.append(record)
            original()
            record['returned'] = True

        controller._refresh_scheduled = observed_callback
        controller.schedule()
        assert controller._pending and isValid(controller)
        phase['inside_cleanup'] = True
        cleanup_test_windows(app, existing)
        phase['inside_cleanup'] = False
        assert callbacks and all(record['inside_cleanup'] and record['returned']
                                 and record['window_valid'] and record['dialog_valid']
                                 for record in callbacks)
        assert not isValid(window) and not isValid(dialog) and not isValid(controller)
    finally:
        if isValid(window):
            window.cancel_jobs()
            for job in list(window.jobs):
                assert job.wait(5000)
            window.dirty = False; window.closing = True
            window.close()
            cleanup_test_windows(app, existing)


@pytest.mark.parametrize('view', ['source', 'overlay'])
def test_hue_feather_detail_reads_exact_frozen_float_pixel(window, app, view):
    import colorsys
    dialog = prepare(window, colorsys.hsv_to_rgb(.13, .75, .8))
    choose(dialog.preview_choice, view)
    choose(dialog.zoom_choice, 8)
    dialog.render_preview()
    before = asdict(dialog.settings)
    pick(dialog, app)
    assert dialog.settings.hue_softness == pytest.approx(.16, abs=1e-6)
    after = asdict(dialog.settings); after['hue_softness'] = 0
    assert after == before
    assert dialog.sample_source_position == dialog.viewport.source_at(8, 8)
    assert not window.editor.can_undo


@pytest.mark.parametrize('message', [
    'Qualifier softness channel must be hue, saturation or luma',
    'Qualifier softness target must identify a valid range',
    'Qualifier base range index must be zero',
    'Qualifier softness target range no longer exists',
    'Qualifier softness sample must be inside visible source content',
    'Qualifier hue softness would exceed 0.5; choose a closer sample or adjust the core range',
    'Qualifier saturation softness would exceed 1; choose a closer sample or adjust the core range',
    'Qualifier luma softness would exceed 1; choose a closer sample or adjust the core range',
])
def test_softness_errors_have_chinese_translation(message):
    set_locale('zh-CN')
    try:
        assert tr_error(ValueError(message)) != message
    finally:
        set_locale('en')

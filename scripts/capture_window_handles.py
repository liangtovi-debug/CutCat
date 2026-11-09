"""Real Qt/RGB16 evidence for static-window mouse handles.

Run in fresh processes with QT_SCALE_FACTOR=1, 1.5 and 2. Only local synthetic
media is used. Linux/offscreen checks are not native Windows/macOS validation.
"""
from copy import deepcopy
from dataclasses import asdict, replace
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from lumen.i18n import set_locale
from lumen.model import Editor
from lumen.qualifier import apply_qualifier, qualifier_mask
from lumen.ui import MainWindow
from lumen.ui_qualifier import open_qualifier
from capture_selective_color import synthetic_project, pump
from capture_qualifier_overlay import wait_frame, geometry_checks
from qt_test_cleanup import cleanup_test_windows


def pixels(widget):
    image = widget.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(image.bits(), np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width()*4].reshape(image.height(), image.width(), 4).copy()


def click_editor(dialog, app):
    pump(app, .08)  # Allow the documented 50 ms pending-preview debounce.
    dialog.window_edit_button.setFocus(); pump(app, .03)
    assert dialog.window_edit_button.visibleRegion().boundingRect() == dialog.window_edit_button.rect()
    QTest.mouseClick(dialog.window_edit_button, Qt.MouseButton.LeftButton)
    pump(app, .03)


def handle_geometry_checks(dialog, field=None):
    result = geometry_checks(dialog, field)
    button = dialog.window_edit_button
    if field is button:
        assert button.isVisible() and button.visibleRegion().boundingRect() == button.rect()
        assert button.fontMetrics().horizontalAdvance(button.text()) + 16 <= button.width(), 'Handle caption clipped'
    return result


def drag(dialog, app, name, delta, release=True):
    events = []
    observer = lambda accepted: events.append(accepted)
    dialog.canvas.window_drag_finished.connect(observer)
    start = dialog.canvas.window_handles()[name].toPoint(); end = start + QPoint(*delta)
    QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=start)
    assert dialog.canvas.window_drag_active
    QTest.mouseMove(dialog.canvas, end, delay=25); pump(app, .06)
    if release:
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=end); pump(app, .06)
        assert not dialog.canvas.window_drag_active
    dialog.canvas.window_drag_finished.disconnect(observer)
    if release:
        assert events == [True], ('Mouse gesture unexpectedly canceled', name, delta, events)
    return end


def expected_delta(dialog, delta):
    rect = dialog.canvas.image_rect(); h, w = dialog.snapshot.frame.shape[:2]
    return (delta[0] * ((dialog.viewport.width + dialog.viewport.stride - 1) // dialog.viewport.stride * dialog.viewport.stride) / rect.width() / w,
            delta[1] * ((dialog.viewport.height + dialog.viewport.stride - 1) // dialog.viewport.stride * dialog.viewport.stride) / rect.height() / h)


def verify_pixels(dialog):
    source = dialog.snapshot.frame
    mask = qualifier_mask(source, replace(dialog.settings, enabled=True))
    np.testing.assert_array_equal(dialog.matte, dialog.viewport.extract(mask))
    mode = dialog.preview_choice.currentData()
    frame = dialog.viewport.extract(source)
    if mode == 'source': expected = frame
    elif mode == 'matte': expected = np.repeat(dialog.matte[..., None], 3, axis=-1)
    elif mode == 'adjusted': expected = dialog.viewport.extract(apply_qualifier(source, dialog.settings))
    else:
        alpha = dialog.matte[..., None] * np.float32(.5)
        expected = np.clip(frame, 0, 1) * (np.float32(1) - alpha) + np.array([1, 0, 1], np.float32) * alpha
    np.testing.assert_array_equal(dialog.canvas._pixels, np.rint(np.clip(expected, 0, 1) * 255).astype(np.uint8))
    return {'full_frame_matte_matches_viewport': True, 'display_pixels_match_without_handle_baking': True,
            'fractional_mask_samples': int(np.count_nonzero((dialog.matte > 0) & (dialog.matte < 1)))}


def capture(output):
    output.mkdir(parents=True, exist_ok=True)
    runtime = output/'runtime'; runtime.mkdir(exist_ok=True)
    project = synthetic_project(runtime)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(workspace_settings=QSettings(str(runtime/'settings.ini'), QSettings.Format.IniFormat),
                        recovery_root=runtime/'recovery', ui_language='en')
    evidence = {'result': 'running', 'platform': 'Linux/offscreen', 'native_validation': False,
        'scale_factor': os.environ.get('QT_SCALE_FACTOR', '1'),
        'source': 'Locally generated RGB16 FFV1 chart, real asynchronous float32 qualifier decode',
        'captures': [], 'checks': []}
    try:
        window.editor = Editor(project); window.refresh(); window.select_clip('synthetic-picture')
        window.resize(1280, 800); window.show(); pump(app)
        before_project = deepcopy(window.project)
        for locale in ['zh-CN', 'en']:
            set_locale(locale); dialog = open_qualifier(window); wait_frame(app, dialog)
            snapshot = dialog.snapshot; digest = hashlib.sha256(snapshot.frame.tobytes()).hexdigest()
            assert not snapshot.frame.flags.writeable
            dialog.enabled_check.setChecked(True)
            dialog.controls['hue_width'].setValue(180); dialog.controls['exposure'].setValue(.5)
            for size in [(760, 800), (360, 600)]:
                dialog.resize(*size); pump(app)
                for shape in ['ellipse', 'rectangle']:
                    dialog.window_shape.setCurrentIndex(dialog.window_shape.findData(shape))
                    for key, value in [('window_center_x', 48), ('window_center_y', 51),
                        ('window_width', 38), ('window_height', 52), ('window_feather', 32)]:
                        dialog.controls[key].setValue(value)
                    dialog.zoom_choice.setCurrentIndex(0)
                    dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData('overlay'))
                    if not dialog.window_edit_button.isChecked():
                        click_editor(dialog, app)
                    pump(app)
                    assert dialog.canvas.window_editing
                    base = deepcopy(dialog.settings)
                    dx, dy = expected_delta(dialog, (18, -9))
                    drag(dialog, app, 'center', (18, -9))
                    assert abs(dialog.settings.window_center_x - (base.window_center_x + dx)) < 1e-12
                    assert abs(dialog.settings.window_center_y - (base.window_center_y + dy)) < 1e-12
                    before_resize = deepcopy(dialog.settings); dx, _ = expected_delta(dialog, (12, 0))
                    drag(dialog, app, 'right', (12, 0))
                    assert abs(dialog.settings.window_width - (before_resize.window_width + 2*dx)) < 1e-12
                    assert dialog.settings.window_center_x == before_resize.window_center_x
                    assert dialog.settings.window_center_y == before_resize.window_center_y
                    pending = deepcopy(dialog.settings)
                    drag(dialog, app, 'bottom', (0, 11), release=False)
                    assert dialog.settings != pending
                    QTest.keyClick(dialog.canvas, Qt.Key.Key_Escape); pump(app)
                    assert dialog.isVisible() and dialog.settings == pending and not dialog.canvas.window_drag_active
                    QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton); pump(app, .03)
                    assert dialog.settings == pending
                    assert window.project == before_project and not window.editor.can_undo
                    for key in ['window_center_x', 'window_center_y', 'window_width', 'window_height']:
                        assert abs(dialog.controls[key].value() - getattr(dialog.settings, key)*100) <= .000501

                    def save(name, field=None):
                        pump(app, .1); dialog.render_preview(); pump(app, .03)
                        field = dialog.window_edit_button if field is None else field
                        field.setFocus(); pump(app, .03)
                        geometry = handle_geometry_checks(dialog, field)
                        checks = verify_pixels(dialog)
                        assert snapshot is dialog.snapshot and hashlib.sha256(snapshot.frame.tobytes()).hexdigest() == digest
                        assert window.project == before_project and not window.editor.can_undo
                        filename = f'{locale}-{size[0]}x{size[1]}-{shape}-{name}.png'
                        shot = dialog.grab(); assert shot.save(str(output/filename))
                        evidence['captures'].append({'file': filename, 'locale': locale, 'shape': shape,
                            'requested_size': size, 'actual_size': [dialog.width(), dialog.height()],
                            'pixel_size': [shot.width(), shot.height()], 'dpr': shot.devicePixelRatio(),
                            'view': dialog.preview_choice.currentData(), 'zoom': dialog.zoom_choice.currentData(),
                            'settings': asdict(dialog.settings), 'control_rects': geometry,
                            'visible_handles': list(dialog.canvas.window_handles()), **checks})

                    for mode in ['source', 'overlay', 'adjusted', 'matte']:
                        dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData(mode)); save(mode)
                    save('numeric-values', dialog.controls['window_center_x'])
                    # Handles must actually be painted and must remain separate from preview/source data.
                    active = pixels(dialog.canvas); rgb = dialog.canvas._pixels.copy()
                    click_editor(dialog, app); pump(app)
                    inactive = pixels(dialog.canvas)
                    np.testing.assert_array_equal(dialog.canvas._pixels, rgb)
                    painted = int(np.count_nonzero(np.any(active != inactive, axis=-1)))
                    assert painted > 30, 'Actual canvas capture has no visible handle drawing'
                    click_editor(dialog, app); pump(app)
                    # Detail edit retains original global canvas units, even when edges are cropped.
                    dialog.zoom_choice.setCurrentIndex(dialog.zoom_choice.findData(2))
                    dialog.set_detail_center(dialog.settings.window_center_x*960, dialog.settings.window_center_y*540)
                    pump(app); detail_before = deepcopy(dialog.settings)
                    dx, dy = expected_delta(dialog, (9, -7)); drag(dialog, app, 'center', (9, -7))
                    assert abs(dialog.settings.window_center_x - (detail_before.window_center_x + dx)) < 1e-12, (locale, size, shape, detail_before.window_center_x, dx, dialog.settings.window_center_x, dialog.canvas.window_mapping, dialog.canvas.image_rect())
                    assert abs(dialog.settings.window_center_y - (detail_before.window_center_y + dy)) < 1e-12
                    dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData('overlay')); save('detail-moved')
                    detail_edited = deepcopy(dialog.settings)
                    for zoom, center in [(8, (0, 0)), (8, (959, 539)), (1, (480, 270)), (0, (480, 270))]:
                        dialog.zoom_choice.setCurrentIndex(dialog.zoom_choice.findData(zoom))
                        dialog.set_detail_center(*center); pump(app, .03)
                        verify_pixels(dialog); assert dialog.settings == detail_edited
                    # Picker wins over editing and never changes geometry, including on a handle center.
                    geometry_before = {k: v for k, v in asdict(dialog.settings).items() if k.startswith('window_')}
                    point = dialog.canvas.window_handles()['center'].toPoint()
                    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton); pump(app, .03)
                    assert dialog.canvas.picking and not dialog.canvas.window_editing
                    QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=point); pump(app, .03)
                    assert not dialog.canvas.picking
                    assert {k: getattr(dialog.settings, k) for k in geometry_before} == geometry_before
                    dialog.controls['hue_width'].setValue(180)
                    click_editor(dialog, app); pump(app)
                    all_fields = []
                    for control in [dialog.enabled_check, dialog.window_shape, dialog.window_edit_button, dialog.window_invert, *dialog.controls.values()]:
                        control.setFocus(); pump(app, .03); handle_geometry_checks(dialog, control)
                        all_fields.append(control.objectName() or 'enabled_check')
                    evidence['checks'].append({'locale': locale, 'size': size, 'shape': shape,
                        'actual_mouse_move_and_symmetric_resize': True, 'escape_restores_active_drag': True,
                        'no_project_change_before_apply': True, 'numeric_parity': True,
                        'detail_and_crop_global_coordinate_parity': True, 'picker_exclusive': True,
                        'painted_handle_difference_pixels': painted, 'all_fields_focus_visible': all_fields})
            pending = deepcopy(dialog.settings)
            QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton); pump(app)
            assert window.selected_clip().grade.qualifier == pending and len(window.editor._undo) == 1
            after = deepcopy(window.project)
            window.undo(); assert window.project == before_project
            window.redo(); assert window.project == after
            window.undo(); assert window.project == before_project and not window.editor.can_undo
            # Cancel after a real drag, then stale invalidation while a drag is active.
            dialog = open_qualifier(window); wait_frame(app, dialog)
            dialog.window_shape.setCurrentIndex(dialog.window_shape.findData('ellipse'))
            click_editor(dialog, app); pump(app)
            start_settings = deepcopy(dialog.settings); drag(dialog, app, 'center', (10, 7), release=False)
            window.revision += 1; dialog.check_current(); pump(app)
            assert not dialog.canvas.window_drag_active and dialog.settings == start_settings
            assert not dialog.window_edit_button.isEnabled()
            dialog.reject(); pump(app)
            assert window.project == before_project and not window.editor.can_undo
            evidence['checks'].append({'locale': locale, 'apply_is_one_undo_transaction': True,
                'undo_redo_exact_project_roundtrip': True, 'stale_active_drag_is_canceled': True,
                'source_sha256_unchanged': digest})
        evidence['result'] = 'passed'
        (output/'capture.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps({'result': 'passed', 'captures': len(evidence['captures']), 'scale_factor': evidence['scale_factor']}), flush=True)
    finally:
        window.dirty = False; cleanup_test_windows(app)


def capture_report(output):
    """A real, taller Chinese view with the complete static-window group."""
    output.mkdir(parents=True, exist_ok=True)
    runtime = output/'runtime'; runtime.mkdir(exist_ok=True)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(workspace_settings=QSettings(str(runtime/'settings.ini'), QSettings.Format.IniFormat),
                        recovery_root=runtime/'recovery', ui_language='zh-CN')
    try:
        window.editor = Editor(synthetic_project(runtime)); window.refresh()
        window.select_clip('synthetic-picture'); window.show(); pump(app)
        before = deepcopy(window.project)
        dialog = open_qualifier(window); wait_frame(app, dialog)
        dialog.resize(760, 960); pump(app)
        dialog.enabled_check.setChecked(True)
        for key, value in [('hue_width', 180), ('exposure', .5),
            ('window_center_x', 45), ('window_center_y', 48),
            ('window_width', 38), ('window_height', 52), ('window_feather', 32)]:
            dialog.controls[key].setValue(value)
        dialog.window_shape.setCurrentIndex(dialog.window_shape.findData('ellipse'))
        dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData('overlay'))
        click_editor(dialog, app)
        drag(dialog, app, 'center', (14, -7)); drag(dialog, app, 'right', (12, 0))
        group = dialog.window_shape.parentWidget()
        top = group.mapTo(dialog.scroll.widget(), QPoint()).y()
        dialog.scroll.verticalScrollBar().setValue(max(0, top - 8)); pump(app)
        for field in [dialog.window_shape, dialog.window_edit_button, dialog.window_invert,
                      *[dialog.controls[key] for key in ('window_center_x', 'window_center_y',
                        'window_width', 'window_height', 'window_feather')]]:
            handle_geometry_checks(dialog, field)
        checks = verify_pixels(dialog)
        assert window.project == before and not window.editor.can_undo
        filename = 'zh-CN-760x960-ellipse-overlay-handles.png'
        image = dialog.grab(); assert image.save(str(output/filename))
        result = {'result': 'passed', 'platform': 'Linux/offscreen', 'native_validation': False,
                  'source': 'Local synthetic RGB16 FFV1, actual async decode and mouse gestures',
                  'file': filename, 'size': [dialog.width(), dialog.height()],
                  'dpr': image.devicePixelRatio(), 'settings': asdict(dialog.settings), **checks}
        (output/'capture.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        window.dirty = False; cleanup_test_windows(app)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', action='store_true', help='Capture one complete Chinese static-window group')
    args = parser.parse_args()
    (capture_report if args.report else capture)(args.output.resolve())

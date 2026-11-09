"""Capture real RGB16 qualifier-overlay Qt UI, without changing a project.

Run once per QT_SCALE_FACTOR (1, 1.5, 2). The locally generated lossless chart
is read by the real asynchronous RGB16 source decoder, not injected into the
UI. Captures are Linux/offscreen evidence, not native Windows/macOS acceptance.
"""
from copy import deepcopy
from dataclasses import asdict
import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from lumen.i18n import set_locale
from lumen.media import ffprobe_binary
from lumen.model import Editor
from lumen.qualifier_picker import initialize_from_sample
from lumen.ui import MainWindow
from lumen.ui_qualifier import open_qualifier
from capture_selective_color import synthetic_project, pump


def wait_frame(app, dialog):
    deadline = time.monotonic() + 30
    while dialog.snapshot is None and time.monotonic() < deadline:
        pump(app, .03)
    assert dialog.snapshot is not None, dialog.status.text()
    assert dialog.snapshot.stage == 'qualifier'
    assert dialog.snapshot.frame.dtype == np.float32
    assert not dialog.snapshot.frame.flags.writeable


def screen_rect(widget):
    return QRect(widget.mapToGlobal(QPoint()), widget.size())


def geometry_checks(dialog, field=None):
    """Check full width AND height, plus non-overlap of visible sibling UI.

    Each scrollable field is checked after production focus/reveal behavior;
    this does not assert all twelve fields fit onscreen at the same time.
    """
    controls = [(name, getattr(dialog, name)) for name in (
        'canvas', 'preview_choice', 'zoom_choice', 'center_x', 'center_y',
        'pick_button', 'sample_label', 'resolution', 'status',
        'apply_button', 'close_button')]
    if field is not None:
        controls.append((field.objectName() or 'enabled_check', field))
        label = dialog.control_labels.get(field)
        if label is not None:
            controls.append(('label_' + field.objectName(), label))
    result = {}
    for name, widget in controls:
        visible = widget.visibleRegion().boundingRect()
        assert widget.isVisible() and visible == widget.rect(), (
            f'Clipped {name}: visible={visible}, full={widget.rect()}')
        if hasattr(widget, 'wordWrap') and widget.wordWrap():
            required_height = widget.heightForWidth(widget.width())
            assert required_height <= widget.height(), (
                f'Text clipped in {name}: need {required_height}, got {widget.height()}')
        rect = screen_rect(widget)
        result[name] = [rect.x(), rect.y(), rect.width(), rect.height()]
    for (first_name, first), (second_name, second) in itertools.combinations(controls, 2):
        overlap = screen_rect(first).intersected(screen_rect(second))
        assert overlap.isEmpty(), f'Overlapping controls: {first_name}, {second_name}: {overlap}'
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
    return result


def check_overlay_pixels(dialog):
    frame = dialog.viewport.extract(dialog.snapshot.frame)
    alpha = dialog.matte[..., None].astype(np.float32) * np.float32(.5)
    expected = np.clip(frame, 0, 1).astype(np.float32) * (np.float32(1) - alpha)
    expected += np.array([1, 0, 1], np.float32) * alpha
    expected_bytes = np.rint(np.clip(expected, 0, 1) * 255).astype(np.uint8)
    np.testing.assert_array_equal(dialog.canvas._pixels, expected_bytes)
    excluded = dialog.matte == 0
    source_bytes = np.rint(np.clip(frame, 0, 1) * 255).astype(np.uint8)
    np.testing.assert_array_equal(dialog.canvas._pixels[excluded], source_bytes[excluded])
    rect = dialog.canvas.image_rect(); dpr = dialog.canvas.devicePixelRatioF()
    width, height = round(rect.width() * dpr), round(rect.height() * dpr)
    columns = np.floor((np.arange(width) + .5) * dialog.matte.shape[1] / width).astype(int)
    rows = np.floor((np.arange(height) + .5) * dialog.matte.shape[0] / height).astype(int)
    visible_matte = dialog.matte[rows[:, None], columns[None, :]]
    return {'independent_50_percent_times_matte_pixel_match': True,
            'visible_physical_fractional_mask_pixels': int(np.count_nonzero((visible_matte > 0) & (visible_matte < 1))),
            'excluded_pixels_unchanged': int(np.count_nonzero(excluded)),
            'fractional_mask_pixels': int(np.count_nonzero((dialog.matte > 0) & (dialog.matte < 1))),
            'fully_selected_pixels': int(np.count_nonzero(dialog.matte == 1))}


def capture(output):
    output.mkdir(parents=True, exist_ok=True)
    runtime = output / 'runtime'; runtime.mkdir(exist_ok=True)
    project = synthetic_project(runtime)
    probe = subprocess.run([ffprobe_binary(), '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=codec_name,pix_fmt,width,height,bits_per_raw_sample,color_space,color_range',
        '-of', 'json', str(runtime / 'synthetic-hue-chart-rgb16.mkv')],
        check=True, capture_output=True, text=True, timeout=30)
    source_stream = json.loads(probe.stdout)['streams'][0]
    assert source_stream['pix_fmt'] == 'gbrp16le' and source_stream['codec_name'] == 'ffv1'
    app = QApplication.instance() or QApplication([])
    window = MainWindow(workspace_settings=QSettings(str(runtime / 'settings.ini'), QSettings.Format.IniFormat),
                        recovery_root=runtime / 'recovery', ui_language='en')
    evidence = {'scale_factor': os.environ.get('QT_SCALE_FACTOR', '1'), 'platform': 'offscreen',
                'not_native_platform_validation': True,
                'source': 'Locally generated lossless RGB16 chart; real async qualifier-stage float32 decode',
                'source_probe': source_stream, 'captures': [], 'checks': []}
    try:
        window.editor = Editor(project); window.refresh(); window.select_clip('synthetic-picture')
        window.set_workspace_mode('professional', persist=False)
        window.resize(1280, 800); window.show(); pump(app)
        before = deepcopy(window.project)
        for locale in ('zh-CN', 'en'):
            set_locale(locale)
            dialog = open_qualifier(window)
            assert open_qualifier(window) is dialog
            wait_frame(app, dialog)
            snapshot = dialog.snapshot
            source_identity = id(snapshot.frame)
            source_hash = hashlib.sha256(snapshot.frame.tobytes()).hexdigest()
            assert snapshot.frame.shape == (540, 960, 3)
            unquantized = int(np.count_nonzero(np.abs(snapshot.frame * 255 - np.rint(snapshot.frame * 255)) > .001))
            assert unquantized > 1000, 'The source appears to have been quantized to display RGB8'
            assert dialog.preview_choice.findData('overlay') == 3
            dialog.enabled_check.setChecked(True)
            for key, value in [('hue_center', 60), ('hue_width', 25), ('hue_softness', 20),
                               ('saturation_softness', 15), ('luma_softness', 15),
                               ('exposure', .6), ('saturation', 70), ('hue_shift', -20)]:
                dialog.controls[key].setValue(value)
            pump(app)
            locale_checks = {'locale': locale, 'source_float_values_off_rgb8_grid': unquantized,
                'overlay_option_index': 3, 'source_sha256': source_hash, 'sizes': []}

            def save(name, requested_size, field=None, require_fractional=False):
                pump(app)
                assert dialog.snapshot is snapshot and id(snapshot.frame) == source_identity
                assert window.project == before and not window.editor.can_undo
                assert hashlib.sha256(snapshot.frame.tobytes()).hexdigest() == source_hash
                geometry = geometry_checks(dialog, field)
                extra = check_overlay_pixels(dialog) if dialog.preview_choice.currentData() == 'overlay' else {}
                if require_fractional:
                    assert extra['fractional_mask_pixels'] > 0
                    assert extra['visible_physical_fractional_mask_pixels'] > 0
                    assert extra['excluded_pixels_unchanged'] > 0
                    assert extra['fully_selected_pixels'] > 0
                filename = f'{locale}-{requested_size[0]}x{requested_size[1]}-{name}.png'
                shot = dialog.grab(); assert shot.save(str(output / filename))
                evidence['captures'].append({'file': filename, 'locale': locale,
                    'mode': dialog.preview_choice.currentData(), 'armed': dialog.pick_button.isChecked(),
                    'requested_size': list(requested_size), 'logical_size': [dialog.width(), dialog.height()],
                    'pixel_size': [shot.width(), shot.height()], 'device_pixel_ratio': shot.devicePixelRatio(),
                    'source_stage': snapshot.stage, 'source_frame': snapshot.source_frame,
                    'grid_shape': list(dialog.preview_shape), 'stride': dialog.preview_stride,
                    'crop_origin': [dialog.viewport.x, dialog.viewport.y],
                    'display_label': dialog.resolution.text(), 'sample_label': dialog.sample_label.text(),
                    'status': dialog.status.text(), 'control_rects': geometry, **extra})

            for size in ((680, 680), (360, 540)):
                dialog.resize(*size); pump(app)
                assert dialog.width() == size[0]
                assert dialog.height() == max(size[1], 600), 'Minimum height must be explicit in evidence'
                # Context views all use exactly the same chart, range, viewport
                # and pending settings. No project changes or recolored mockups.
                dialog.zoom_choice.setCurrentIndex(dialog.zoom_choice.findData(0))
                field = dialog.controls['hue_center']; field.setFocus(); dialog.reveal_control(field)
                context_settings = deepcopy(dialog.settings)
                for mode in ('source', 'matte', 'overlay'):
                    dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData(mode))
                    save('context-' + mode, size, field, require_fractional=mode == 'overlay')
                    assert dialog.settings == context_settings
                # Make a real mouse event through visibly tinted detail, then
                # derive expectation from the exact source pixel Qt maps to.
                dialog.zoom_choice.setCurrentIndex(dialog.zoom_choice.findData(8))
                dialog.set_detail_center(161, 161); pump(app)
                previous_settings = deepcopy(dialog.settings)
                QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton); pump(app)
                assert dialog.pick_button.isChecked() and dialog.canvas.picking
                assert dialog.preview_choice.currentData() == 'overlay'
                assert not dialog.preview_choice.isEnabled()
                assert 'float' in dialog.sample_label.text() or '浮点' in dialog.sample_label.text()
                save('overlay-armed', size, field)
                rect = dialog.canvas.image_rect()
                target_grid = dialog.viewport.grid_at(161, 161)
                pos = QPoint(round(rect.left() + (target_grid[0] + .5) * rect.width() / dialog.canvas.image.width()),
                             round(rect.top() + (target_grid[1] + .5) * rect.height() / dialog.canvas.image.height()))
                actual_grid = dialog.canvas.grid_at(pos)
                actual_source = dialog.viewport.source_at(*actual_grid)
                sx, sy = actual_source
                expected_rgb = snapshot.frame[sy, sx].copy()
                expected_settings, expected_sample = initialize_from_sample(expected_rgb, previous_settings)
                tinted_rgb = dialog.canvas._pixels[actual_grid[1], actual_grid[0]].astype(np.float32) / 255
                assert np.max(np.abs(expected_rgb - tinted_rgb)) > .05, 'Click target must actually be tinted'
                QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=pos); pump(app)
                assert not dialog.pick_button.isChecked() and not dialog.canvas.picking
                assert dialog.preview_choice.currentData() == 'overlay' and dialog.preview_choice.isEnabled()
                assert dialog.sample_source_position == actual_source
                assert dialog.settings == expected_settings, 'Sampling must use exact source, not the overlay tint'
                assert all(f'{float(value):.6f}' in dialog.sample_label.text() for value in expected_rgb)
                assert dialog.matte[actual_grid[1], actual_grid[0]] == 1
                dialog.zoom_choice.setCurrentIndex(dialog.zoom_choice.findData(2)); pump(app)
                save('overlay-picked-detail', size, field, require_fractional=True)
                # Every editable field must be reachable with label and all
                # fixed preview/transaction controls visible and non-overlapping.
                checked_fields = []
                for active in [dialog.enabled_check, *dialog.controls.values()]:
                    active.setFocus(); dialog.reveal_control(active); pump(app, .03)
                    try:
                        geometry_checks(dialog, active)
                    except AssertionError:
                        dialog.grab().save(str(output / f'{locale}-{size[0]}x{size[1]}-FAILED-{active.objectName()}.png'))
                        raise
                    checked_fields.append(active.objectName() or 'enabled_check')
                save('overlay-adjustment-controls', size, dialog.controls['hue_shift'], require_fractional=True)
                locale_checks['sizes'].append({'requested': list(size), 'actual': [dialog.width(), dialog.height()],
                    'all_editable_controls_visible_without_overlap_when_revealed': checked_fields,
                    'qt_click_logical': [pos.x(), pos.y()], 'picked_grid': list(actual_grid),
                    'picked_source': list(actual_source), 'expected_source_rgb': expected_rgb.tolist(),
                    'visible_tinted_rgb8': tinted_rgb.tolist(), 'expected_hsv_luma': asdict(expected_sample),
                    'exact_source_settings_matched': True, 'overlay_preserved_armed_and_after_pick': True})
            picked = dialog.sample_source_position
            dialog.pan_view(7, -11); pump(app)
            assert dialog.sample_source_position == picked
            for zoom in (1, 2, 4, 8, 0, 8):
                dialog.zoom_choice.setCurrentIndex(dialog.zoom_choice.findData(zoom)); pump(app)
                assert dialog.snapshot is snapshot and id(snapshot.frame) == source_identity
            assert hashlib.sha256(snapshot.frame.tobytes()).hexdigest() == source_hash
            dialog.controls['saturation_low'].setValue(80)
            dialog.controls['saturation_high'].setValue(20)
            assert not dialog.apply_button.isEnabled()
            dialog.apply_settings(); assert window.project == before
            QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton); pump(app)
            assert dialog.snapshot is None and dialog.matte is None and dialog.canvas.image.isNull()
            assert dialog.canvas._pixels is None and dialog.canvas._raster_cache is None
            assert not dialog.timer.isActive() and not dialog.preview_timer.isActive()
            assert window.project == before and not window.editor.can_undo
            dialog.accept_snapshot(snapshot); assert dialog.snapshot is None
            reopened = open_qualifier(window); wait_frame(app, reopened)
            assert reopened.settings == before.clips[0].grade.qualifier
            reopened.controls['exposure'].setValue(.5)
            window.revision += 1; reopened.apply_settings()
            assert not reopened.apply_button.isEnabled() and window.project == before
            reopened.reject()
            locale_checks.update({'invalid_pair_rejected': True, 'same_frozen_source_reused': True,
                'cancel_unchanged_and_buffers_cleared': True, 'late_result_rejected': True,
                'reopen_stored_settings': True, 'stale_revision_rejected': True})
            evidence['checks'].append(locale_checks)
        evidence['result'] = 'passed'
        (output / 'capture.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(evidence, ensure_ascii=False), flush=True)
    finally:
        if getattr(window, 'qualifier_dialog', None) is not None:
            window.qualifier_dialog.reject()
        window.cancel_jobs()
        for job in list(window.jobs):
            assert job.wait(5000)
        window.dirty = False; window.closing = True; window.close(); window.deleteLater(); pump(app)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); pump(app)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    capture(parser.parse_args().output.resolve())

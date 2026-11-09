"""Capture real RGB16-derived static windows in both languages at a chosen DPI.

Linux/offscreen evidence only, not native platform certification.
"""
from copy import deepcopy
from dataclasses import replace
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from lumen.i18n import set_locale
from lumen.model import Editor
from lumen.qualifier import apply_qualifier, qualifier_mask
from lumen.ui import MainWindow
from lumen.ui_qualifier import open_qualifier
from capture_selective_color import synthetic_project, pump
from capture_qualifier_overlay import wait_frame, geometry_checks, check_overlay_pixels
from qt_test_cleanup import cleanup_test_windows


def capture(output):
    output.mkdir(parents=True, exist_ok=True)
    runtime = output / 'runtime'; runtime.mkdir(exist_ok=True)
    project = synthetic_project(runtime)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(workspace_settings=QSettings(str(runtime / 'settings.ini'), QSettings.Format.IniFormat),
                        recovery_root=runtime / 'recovery', ui_language='en')
    evidence = {'scale_factor': os.environ.get('QT_SCALE_FACTOR', '1'), 'platform': 'Linux/offscreen',
        'native_validation': False, 'source': 'Real asynchronous RGB16 FFV1 decode of local chart', 'captures': [], 'checks': []}
    try:
        window.editor = Editor(project); window.refresh(); window.select_clip('synthetic-picture')
        window.resize(1280, 800); window.show(); pump(app)
        before = deepcopy(window.project)
        for locale in ['zh-CN', 'en']:
            set_locale(locale); dialog = open_qualifier(window); wait_frame(app, dialog)
            snapshot = dialog.snapshot; source_hash = hashlib.sha256(snapshot.frame.tobytes()).hexdigest()
            assert not snapshot.frame.flags.writeable
            dialog.enabled_check.setChecked(True)
            dialog.controls['hue_width'].setValue(180)
            dialog.controls['exposure'].setValue(.5)
            for key, value in [('window_center_x', 42), ('window_center_y', 54),
                               ('window_width', 37), ('window_height', 59), ('window_feather', 32)]:
                dialog.controls[key].setValue(value)
            for size in [(680, 680), (360, 540)]:
                dialog.resize(*size); pump(app)
                assert (dialog.width(), dialog.height()) == (size[0], max(600, size[1]))
                for shape in ['ellipse', 'rectangle']:
                    dialog.window_shape.setCurrentIndex(dialog.window_shape.findData(shape))
                    dialog.window_invert.setChecked(False)
                    dialog.zoom_choice.setCurrentIndex(0); pump(app)
                    full_mask = qualifier_mask(snapshot.frame, replace(dialog.settings, enabled=True))
                    full_adjusted = apply_qualifier(snapshot.frame, dialog.settings)
                    for mode in ['source', 'matte', 'overlay', 'adjusted']:
                        dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData(mode)); pump(app)
                        field = dialog.controls['window_feather']; field.setFocus(); pump(app, .1)
                        geometry = geometry_checks(dialog, field)
                        np.testing.assert_array_equal(dialog.matte, dialog.viewport.extract(full_mask))
                        if mode == 'adjusted':
                            expected = np.rint(np.clip(dialog.viewport.extract(full_adjusted), 0, 1) * 255).astype(np.uint8)
                            np.testing.assert_array_equal(dialog.canvas._pixels, expected)
                        extra = check_overlay_pixels(dialog) if mode == 'overlay' else {}
                        filename = f'{locale}-{size[0]}x{size[1]}-{shape}-{mode}.png'
                        shot = dialog.grab(); assert shot.save(str(output / filename))
                        evidence['captures'].append({'file': filename, 'mode': mode, 'shape': shape,
                            'requested_size': size, 'actual_size': [dialog.width(), dialog.height()],
                            'dpr': shot.devicePixelRatio(), 'control_rects': geometry, **extra})
                    # All controls reachable through actual FocusIn, not manual scrolling.
                    checked = []
                    for control in [dialog.enabled_check, dialog.window_shape, dialog.window_invert, *dialog.controls.values()]:
                        control.setFocus(); pump(app, .1); geometry_checks(dialog, control)
                        checked.append(control.objectName() or 'enabled_check')
                    for zoom, center in [(1, (450, 280)), (2, (300, 300)), (8, (0, 0)), (8, (959, 539))]:
                        dialog.zoom_choice.setCurrentIndex(dialog.zoom_choice.findData(zoom))
                        dialog.set_detail_center(*center); pump(app)
                        np.testing.assert_array_equal(dialog.matte, dialog.viewport.extract(full_mask))
                        dialog.pan_view(11, -9); pump(app)
                        np.testing.assert_array_equal(dialog.matte, dialog.viewport.extract(full_mask))
                    dialog.window_invert.setChecked(True); dialog.preview_choice.setCurrentIndex(3); pump(app)
                    inv = qualifier_mask(snapshot.frame, replace(dialog.settings, enabled=True))
                    np.testing.assert_array_equal(dialog.matte, dialog.viewport.extract(inv))
                    evidence['checks'].append({'locale': locale, 'size': size, 'shape': shape,
                        'all_fields_focus_visible': checked, 'pan_zoom_global_coordinates_match': True,
                        'inverted_spatial_mask_matches': True})
            assert snapshot is dialog.snapshot and hashlib.sha256(snapshot.frame.tobytes()).hexdigest() == source_hash
            assert before == window.project and not window.editor.can_undo
            dialog.reject(); pump(app)
            assert dialog.snapshot is None and not dialog.window_shape.isEnabled()
            stale = open_qualifier(window); wait_frame(app, stale)
            window.revision += 1; stale.check_current()
            assert not stale.window_shape.isEnabled() and not stale.window_invert.isEnabled()
            assert all(not control.isEnabled() for control in stale.controls.values())
            stale.reject(); pump(app)
        evidence['result'] = 'passed'
        (output / 'capture.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'result': 'passed', 'captures': len(evidence['captures']), 'scale': evidence['scale_factor']}))
    finally:
        window.dirty = False
        cleanup_test_windows(app)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    capture(parser.parse_args().output.resolve())

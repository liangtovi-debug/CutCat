"""Genuine local Qt source-click / range-set evidence; fresh process per DPI.

Linux offscreen evidence only, not native Windows/macOS acceptance.
"""
from copy import deepcopy
from dataclasses import asdict
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
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from lumen.i18n import set_locale
from lumen.model import Editor
from lumen.ui import MainWindow
from lumen.ui_qualifier import open_qualifier
from capture_selective_color import synthetic_project, pump
from capture_qualifier_overlay import wait_frame, geometry_checks, check_overlay_pixels, screen_rect
from qt_test_cleanup import cleanup_test_windows


def choose(combo, data):
    index = next((i for i in range(combo.count()) if combo.itemData(i) == data), -1); assert index >= 0
    combo.setFocus(); QTest.keyClick(combo, Qt.Key.Key_Home)
    for _ in range(index): QTest.keyClick(combo, Qt.Key.Key_Down)
    assert combo.currentData() == data


def sample(dialog, app, mode, fraction):
    choose(dialog.sample_mode, mode)
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton); pump(app, .03)
    assert dialog.canvas.picking
    rect = dialog.canvas.image_rect()
    QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton,
                    pos=QPoint(int(rect.left()+rect.width()*fraction), int(rect.top()+rect.height()*.25)))
    pump(app, .08)
    assert not dialog.canvas.picking, dialog.sample_label.text()


def capture(output):
    output.mkdir(parents=True, exist_ok=True)
    runtime = output/'runtime'; runtime.mkdir(exist_ok=True)
    project = synthetic_project(runtime)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(workspace_settings=QSettings(str(runtime/'settings.ini'), QSettings.Format.IniFormat),
                        recovery_root=runtime/'recovery', ui_language='en')
    evidence = {'platform': 'Linux/offscreen', 'native_validation': False,
                'source': 'Local RGB16 FFV1 chart; real async float32 decoder',
                'scale_factor': os.environ.get('QT_SCALE_FACTOR', '1'), 'captures': []}
    try:
        window.editor = Editor(project); window.refresh(); window.select_clip('synthetic-picture')
        window.resize(1280, 800); window.show(); pump(app)
        before = deepcopy(window.project)
        for locale in ['zh-CN', 'en']:
            set_locale(locale)
            for size in [(320, 600), (360, 600), (760, 800)]:
                dialog = open_qualifier(window); wait_frame(app, dialog)
                dialog.resize(*size); pump(app)
                source = dialog.snapshot.frame
                digest = hashlib.sha256(source.tobytes()).hexdigest()
                assert not source.flags.writeable
                choose(dialog.preview_choice, 'overlay')
                sample(dialog, app, 'replace', .15)
                sample(dialog, app, 'add', .48)
                sample(dialog, app, 'subtract', .15)
                assert len(dialog.settings.inclusion_ranges) == len(dialog.settings.exclusion_ranges) == 1
                for mode, zoom in [('overlay', 0), ('matte', 2), ('overlay', 2)]:
                    choose(dialog.preview_choice, mode); choose(dialog.zoom_choice, zoom)
                    if zoom: dialog.set_detail_center(460, 135)
                    pump(app)
                    dialog.range_choice.setFocus(); pump(app)
                    try:
                        geometry_checks(dialog, dialog.range_choice)
                    except AssertionError:
                        dialog.grab().save(str(output / "layout-failure.png"))
                        print([(n, getattr(dialog, n).geometry().getRect(), getattr(dialog, n).minimumSizeHint().width(), getattr(dialog, n).minimumWidth()) for n in ["zoom_choice", "center_x", "center_y", "sample_mode", "pick_button"]], flush=True)
                        raise
                    assert dialog.width() == size[0]
                    for spin in [dialog.center_x, dialog.center_y]:
                        editor = spin.lineEdit()
                        assert editor.fontMetrics().horizontalAdvance('X: 99999') <= editor.contentsRect().width()
                        assert editor.fontMetrics().horizontalAdvance(editor.text()) <= editor.contentsRect().width(), (editor.text(), editor.contentsRect().width(), editor.fontMetrics().horizontalAdvance(editor.text()))
                    assert dialog.sample_mode.visibleRegion().boundingRect() == dialog.sample_mode.rect()
                    assert dialog.sample_mode.fontMetrics().horizontalAdvance(dialog.sample_mode.currentText()) + 25 <= dialog.sample_mode.width()
                    assert screen_rect(dialog.sample_mode).intersected(screen_rect(dialog.pick_button)).isEmpty(), 'Sample mode overlaps pick button'
                    for control in [dialog.pick_button, dialog.remove_range_button, dialog.reset_ranges_button]:
                        control.setFocus(); pump(app, .05)
                        geometry_checks(dialog, None if control is dialog.pick_button else control)
                        assert control.fontMetrics().horizontalAdvance(control.text()) + 16 <= control.width(), control.text()
                    dialog.range_choice.setFocus(); pump(app, .05)
                    pixel_check = check_overlay_pixels(dialog) if mode == 'overlay' else {}
                    if mode == 'matte':
                        expected = np.repeat(dialog.matte[..., None], 3, axis=-1)
                        np.testing.assert_array_equal(dialog.canvas._pixels, np.rint(expected*255).astype(np.uint8))
                    assert hashlib.sha256(source.tobytes()).hexdigest() == digest
                    assert window.project == before and not window.editor.can_undo
                    name = f'{locale}-{size[0]}x{size[1]}-{mode}-zoom{zoom}.png'
                    image = dialog.grab(); assert image.save(str(output/name))
                    evidence['captures'].append({'file': name, 'locale': locale, 'size': size,
                        'actual_size': [dialog.width(), dialog.height()], 'dpr': image.devicePixelRatio(),
                        'source_sha256': digest, 'source_unchanged': True, 'no_project_mutation': True,
                        'settings': asdict(dialog.settings), **pixel_check})
                QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton); pump(app)
                assert dialog.snapshot is None and window.project == before
        evidence['result'] = 'passed'
        (output/'capture.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps({'result': 'passed', 'captures': len(evidence['captures']), 'scale': evidence['scale_factor']}))
    finally:
        window.dirty = False; cleanup_test_windows(app)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    capture(parser.parse_args().output.resolve())

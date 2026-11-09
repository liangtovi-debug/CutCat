"""Capture actual local RGB16 qualifier UI at independent Qt display scales.

Run once per QT_SCALE_FACTOR (1, 1.5, 2). These are offscreen Qt captures,
not Windows/macOS native-platform acceptance. Every visible frame is decoded
by the real asynchronous source path; no illustration or final proxy is used.
"""
from copy import deepcopy
import argparse
import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from lumen.i18n import set_locale
from lumen.model import Editor
from lumen.ui import MainWindow
from lumen.ui_qualifier import open_qualifier
from capture_selective_color import synthetic_project, pump, visible_fraction


def wait_frame(app, dialog):
    deadline = time.monotonic() + 30
    while dialog.snapshot is None and time.monotonic() < deadline:
        pump(app, .03)
    assert dialog.snapshot is not None, dialog.status.text()
    assert dialog.snapshot.stage == 'qualifier'


def capture(output):
    output.mkdir(parents=True, exist_ok=True)
    runtime = output / 'runtime'; runtime.mkdir(exist_ok=True)
    project = synthetic_project(runtime)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(workspace_settings=QSettings(str(runtime / 'settings.ini'), QSettings.Format.IniFormat),
                        recovery_root=runtime / 'recovery', ui_language='en')
    evidence = {'scale_factor': os.environ.get('QT_SCALE_FACTOR', '1'), 'platform': 'offscreen',
                'not_native_platform_validation': True,
                'source': 'Locally generated lossless RGB16 chart, real async source decode after hue curves',
                'captures': [], 'checks': []}
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
            dialog.enabled_check.setChecked(True)
            dialog.controls['exposure'].setValue(.6)
            dialog.controls['saturation'].setValue(70)
            dialog.controls['hue_shift'].setValue(-20)
            dialog.pick_button.setChecked(True)
            # Actual Qt mouse event, chosen from a chromatic active-picture pixel.
            from PySide6.QtCore import QPoint
            rect = dialog.canvas.image_rect()
            gx, gy = 80, 80
            pos = QPoint(int(rect.left() + (gx + .5) * rect.width() / dialog.canvas.image.width()),
                         int(rect.top() + (gy + .5) * rect.height() / dialog.canvas.image.height()))
            QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=pos)
            assert not dialog.pick_button.isChecked(), dialog.sample_label.text()
            assert 'RGB' in dialog.sample_label.text()
            assert dialog.canvas.sample_position is not None
            assert dialog.matte[dialog.canvas.sample_position[1], dialog.canvas.sample_position[0]] == 1
            for size in ((680, 680), (360, 540)):
                dialog.resize(*size); pump(app)
                for mode in ('matte', 'source', 'adjusted'):
                    index = dialog.preview_choice.findData(mode)
                    dialog.preview_choice.setCurrentIndex(index)
                    field = dialog.controls['hue_center'] if mode != 'adjusted' else dialog.controls['exposure']
                    field.setFocus(); dialog.reveal_control(field); pump(app)
                    assert dialog.snapshot is snapshot and id(dialog.snapshot.frame) == source_identity
                    assert window.project == before and not window.editor.can_undo
                    for required in (dialog.canvas, dialog.preview_choice, dialog.pick_button, dialog.sample_label, dialog.status,
                                     dialog.apply_button, dialog.close_button, field):
                        assert visible_fraction(required) == 1, f'Clipped {required.objectName()}: {visible_fraction(required)}'
                    assert dialog.scroll.horizontalScrollBar().maximum() == 0
                    name = f'{locale}-{size[0]}x{size[1]}-{mode}.png'
                    shot = dialog.grab(); assert shot.save(str(output / name))
                    evidence['captures'].append({'file': name, 'mode': mode, 'locale': locale,
                        'logical_size': [dialog.width(), dialog.height()],
                        'pixel_size': [shot.width(), shot.height()], 'device_pixel_ratio': shot.devicePixelRatio(),
                        'canvas_visible_fraction': visible_fraction(dialog.canvas),
                        'apply_visible_fraction': visible_fraction(dialog.apply_button),
                        'focused_control': field.objectName(), 'field_visible_fraction': visible_fraction(field),
                        'source_stage': snapshot.stage, 'source_frame': snapshot.source_frame,
                        'display_grid_shape': list(dialog.preview_shape), 'display_stride': dialog.preview_stride,
                        'display_label': dialog.resolution.text(), 'picked_sample': dialog.sample_label.text(),
                        'matte_min': float(dialog.matte.min()), 'matte_max': float(dialog.matte.max()),
                        'matte_fractional_pixels': int(np.count_nonzero((dialog.matte > 0) & (dialog.matte < 1))),
                        'status': dialog.status.text()})
            dialog.controls['saturation_low'].setValue(80)
            dialog.controls['saturation_high'].setValue(20)
            assert not dialog.apply_button.isEnabled()
            dialog.apply_settings(); assert window.project == before
            evidence['checks'].append({'locale': locale, 'invalid_pair_rejected': True, 'same_frozen_source_reused': True})
            QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton); pump(app)
            assert dialog.snapshot is None and dialog.matte is None and dialog.canvas.image.isNull()
            assert window.project == before and not window.editor.can_undo
            dialog.accept_snapshot(snapshot)
            assert dialog.snapshot is None
            reopened = open_qualifier(window); wait_frame(app, reopened)
            assert reopened.settings == before.clips[0].grade.qualifier
            reopened.controls['exposure'].setValue(.5)
            window.revision += 1; reopened.apply_settings()
            assert not reopened.apply_button.isEnabled() and window.project == before
            reopened.reject()
            evidence['checks'].append({'locale': locale, 'cancel_unchanged': True, 'late_result_rejected': True,
                                       'reopen_stored_settings': True, 'stale_revision_rejected': True})
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
    args = parser.parse_args()
    capture(args.output.resolve())

"""Real picker UI evidence on local RGB16 chart; run separately at each Qt scale.

Offscreen captures are not native macOS/Windows platform acceptance.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from lumen.i18n import set_locale
from lumen.model import Editor
from lumen.ui import MainWindow
from lumen.render import RenderEngine
from capture_selective_color import synthetic_project, pump, visible_fraction


def capture(output, restoration=False):
    output.mkdir(parents=True, exist_ok=True)
    runtime = output / 'runtime'; runtime.mkdir(exist_ok=True)
    project = synthetic_project(runtime)
    if restoration:
        project.clips[0].restoration.enabled = True
        project.clips[0].restoration.deblock = .8
        project.clips[0].restoration.denoise = .6
        project.clips[0].restoration.sharpen = .5
    project.clips[0].grade.hue_hue_curve = [[0., 0.], [1., 0.]]
    project.clips[0].grade.hue_sat_curve = [[0., 1.], [1., 1.]]
    app = QApplication.instance() or QApplication([])
    window = MainWindow(workspace_settings=QSettings(str(runtime / 'settings.ini'), QSettings.Format.IniFormat),
                        recovery_root=runtime / 'recovery', ui_language='en')
    evidence = {'scale': os.environ.get('QT_SCALE_FACTOR', '1'), 'platform': 'offscreen',
                'source': 'local lossless RGB16 hue chart, actual asynchronous selected-source decoder',
                'captures': [], 'restoration_enabled': restoration, 'not_native_platform_validation': True,
                'compact_layout_note': 'At 1280x800, normal inspector scrolling is required: picked-center shows the button, full plot and exact fields; range-feedback shows the fields and range note.'}
    try:
        window.editor = Editor(project); window.refresh(); window.select_clip('synthetic-picture')
        window.set_workspace_mode('professional', persist=False)
        window.resize(1280, 800); window.show(); window.inspector_tabs.setCurrentIndex(1)
        window.color_tabs.setCurrentIndex(2); window.curve_choice.setCurrentIndex(4); pump(app)
        for locale in ('zh-CN', 'en'):
            set_locale(locale)
            dialog = window.open_color_picker()
            deadline = time.monotonic() + 30
            while dialog.snapshot is None and time.monotonic() < deadline: pump(app, .03)
            assert dialog.snapshot is not None, dialog.status.text()
            assert visible_fraction(dialog.canvas) == 1
            assert visible_fraction(dialog.apply_button) == 1
            for state, pixel in [('gray-rejected', (480, 500)), ('hue-picked', (240, 120))]:
                r = dialog.canvas.image_rect()
                x, y = pixel
                pos = QPoint(round(r.left() + (x + .5) / 960 * r.width()),
                             round(r.top() + (y + .5) / 540 * r.height()))
                QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=pos); pump(app)
                assert dialog.apply_button.isEnabled() == (state == 'hue-picked')
                shot = dialog.grab(); name = f'{locale}-{state}.png'; assert shot.save(str(output / name))
                evidence['captures'].append({'file': name, 'status': dialog.status.text(),
                    'logical_size': [dialog.width(), dialog.height()], 'pixel_size': [shot.width(), shot.height()],
                    'device_pixel_ratio': shot.devicePixelRatio(), 'canvas_visible_fraction': visible_fraction(dialog.canvas),
                    'button_visible_fraction': visible_fraction(dialog.apply_button),
                    'marker': dialog.canvas.marker, 'sample_hue': dialog.hue,
                    'stage': dialog.stage.text(), 'restoration_applied': dialog.snapshot.restoration_applied})
            dialog.apply_sample()
            control = window.selective_curve_control
            control.value_spin.setValue(24.)
            pump(app)
            renderer = RenderEngine(timeout=90)
            try:
                preview_path = runtime / f'{locale}-picked-preview.mp4'
                preview_path.unlink(missing_ok=True)
                preview = renderer.render(window.project, preview_path, preview=True)
                window.activate_preview(preview, window.revision, 'graded')
                window.player.play()
                deadline = time.monotonic() + 10
                while (window.video.image.isNull() or window.scopes.data is None) and time.monotonic() < deadline: pump(app, .03)
                assert not window.video.image.isNull() and window.scopes.data is not None, 'Actual edited preview failed to reach viewer/scopes'
                window.player.pause()
                window.main_split.setSizes([180, 640, 460])
                window.vertical_split.setSizes([565, 164]); pump(app)
                top = control.pick_button.mapTo(window.inspector.widget(), QPoint()).y()
                window.inspector.verticalScrollBar().setValue(max(0, top - 8)); pump(app)
                for view in ('picked-center', 'range-feedback'):
                    if view == 'range-feedback':
                        window.inspector.ensureWidgetVisible(control.range_note, 0, 8); pump(app)
                    required = (control.pick_button, control.editor, control.point_choice, control.value_spin) if view == 'picked-center' else (control.point_choice, control.value_spin, control.range_note)
                    for widget in required:
                        assert visible_fraction(widget) == 1, f'Clipped required {view} control'
                    name = f'{locale}-main-{view}.png'
                    shot = window.grab(); assert shot.save(str(output / name))
                    evidence['captures'].append({'file': name, 'logical_size': [window.width(), window.height()],
                        'pixel_size': [shot.width(), shot.height()], 'device_pixel_ratio': shot.devicePixelRatio(),
                        'selected_point': control.point_choice.currentText(), 'source_hue_degrees': control.hue_spin.value(),
                        'shift_degrees': control.value_spin.value(), 'range_note': control.range_note.text(),
                        'pick_button_visible_fraction': visible_fraction(control.pick_button),
                        'curve_visible_fraction': visible_fraction(control.editor),
                        'range_note_visible_fraction': visible_fraction(control.range_note), 'actual_preview_visible': True})
            finally:
                renderer.close()
            window.reset_current_curve()
        (output / 'capture.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(evidence, ensure_ascii=False))
    finally:
        window.cancel_jobs()
        for job in list(window.jobs): assert job.wait(5000)
        window.dirty = False; window.closing = True; window.close(); window.deleteLater(); pump(app)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); pump(app)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--restoration', action='store_true')
    args = parser.parse_args()
    capture(args.output.resolve(), args.restoration)

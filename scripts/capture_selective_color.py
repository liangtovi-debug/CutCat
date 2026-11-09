"""Capture real selective-curve UI and decoded synthetic preview, entirely local.

Run in a fresh process for each QT_SCALE_FACTOR (1, 1.5, 2). This is portable
offscreen visual evidence, not a claim of native Windows/macOS acceptance.
"""
from __future__ import annotations

import argparse
import colorsys
import faulthandler
import json
import os
from pathlib import Path
import subprocess
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QSettings
from PySide6.QtWidgets import QApplication

from lumen.i18n import set_locale
from lumen.media import ffmpeg_binary, probe
from lumen.model import Clip, Editor, Grade, Project
from lumen.render import RenderEngine
from lumen.ui import MainWindow


def synthetic_project(runtime):
    width, height = 960, 540
    rgb = np.empty((height, width, 3), dtype='<u2')
    for x in range(width):
        rgb[:height // 2, x] = np.rint(np.array(colorsys.hsv_to_rgb(x / width, .75, .8)) * 65535)
        rgb[height // 2:height * 3 // 4, x] = np.rint(
            np.array(colorsys.hsv_to_rgb((x // 80) / 12, .55, .7)) * 65535)
    rgb[height * 3 // 4:] = np.rint(np.linspace(0, 65535, width))[None, :, None]
    path = runtime / 'synthetic-hue-chart-rgb16.mkv'
    tags = ['-colorspace', 'rgb', '-color_range', 'pc', '-color_trc', 'bt709',
            '-color_primaries', 'bt709']
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-threads', '1',
                    '-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-s', f'{width}x{height}',
                    '-r', '30', *tags, '-i', 'pipe:0', '-frames:v', '12',
                    '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'gbrp16le', *tags, str(path)],
                   input=rgb.tobytes() * 12, check=True, capture_output=True, timeout=90)
    grade = Grade(hue_hue_curve=[[0., .06], [1 / 6, 0.], [.5, -.08], [.7, 0.], [1., .06]],
                  hue_sat_curve=[[0., .6], [.15, 1.], [.33, .75], [.5, .6], [.7, 1.], [1., .6]])
    media = probe(path)
    return Project(width=width, height=height, media=[media],
                   clips=[Clip('synthetic-picture', media.id, 0, 0, 0, 12, grade=grade)])


def pump(app, seconds=.15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(.01)


def visible_fraction(widget):
    return widget.visibleRegion().boundingRect().height() / max(1, widget.height())


def capture(output):
    output.mkdir(parents=True, exist_ok=True)
    runtime = output / 'runtime'
    runtime.mkdir(exist_ok=True)
    project = synthetic_project(runtime)
    preview = runtime / 'graded-preview.mp4'
    preview.unlink(missing_ok=True)
    engine = RenderEngine(timeout=120)
    try:
        engine.render(project, preview, preview=True)
    finally:
        engine.close()
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(runtime / 'settings.ini'), QSettings.Format.IniFormat)
    window = MainWindow(workspace_settings=settings, recovery_root=runtime / 'recovery',
                        ui_language='zh-CN')
    evidence = {'captures': [], 'source': 'locally generated lossless RGB16 hue chart and gray ramp',
                'preview': 'actual H.264 decoded by Qt into video viewer and scope traces',
                'platform': os.environ.get('QT_QPA_PLATFORM'),
                'scale_factor': os.environ.get('QT_SCALE_FACTOR', '1'),
                'not_native_platform_validation': True}
    try:
        window.editor = Editor(project)
        window.selection = 'synthetic-picture'
        window.set_workspace_mode('professional', persist=False)
        window.resize(1600, 1120)
        window.refresh()
        window.color_tabs.setCurrentIndex(2)
        window.curve_choice.setCurrentIndex(4)
        window.show()
        pump(app)
        window.activate_preview(preview, window.revision, 'graded')
        window.player.play()
        deadline = time.monotonic() + 15
        while (window.video.image.isNull() or window.scopes.data is None) and time.monotonic() < deadline:
            pump(app, .03)
        if window.video.image.isNull() or window.scopes.data is None:
            raise RuntimeError('Actual preview did not reach both the viewer and scopes')
        window.player.pause()
        assert np.count_nonzero(window.scopes.data.vectorscope) > 8
        assert np.count_nonzero(window.scopes.data.parade) > 8
        revision = window.revision
        for locale in ('zh-CN', 'en'):
            set_locale(locale)
            for size in ((1600, 1120), (1280, 800)):
                window.resize(*size)
                window.main_split.setSizes([200, size[0] - 660, 460])
                window.vertical_split.setSizes([size[1] - 270, 164])
                for choice, label, scope in ((4, 'hue-vs-hue', 'parade'),
                                              (5, 'hue-vs-saturation', 'vectorscope')):
                    window.curve_choice.setCurrentIndex(choice)
                    window.selective_curve_control.point_choice.setCurrentIndex(2)
                    window.scopes.set_mode(scope)
                    pump(app)
                    # Scroll the actual inspector, without hiding/reparenting
                    # production controls, so the active curve is reviewable.
                    top = window.curve_choice.mapTo(window.inspector.widget(), QPoint()).y()
                    window.inspector.verticalScrollBar().setValue(max(0, top - 8))
                    pump(app)
                    editor = window.selective_curve_control.editor
                    assert visible_fraction(editor) == 1, 'Selective curve plot is clipped'
                    assert visible_fraction(window.curve_choice) == 1, 'Curve selector is clipped'
                    assert window.scopes.canvas.height() >= 48
                    assert visible_fraction(window.scopes.canvas) == 1, 'Scope canvas is clipped'
                    assert window.revision == revision, 'Capturing must not edit the project'
                    name = f'{locale}-{size[0]}x{size[1]}-{label}.png'
                    pixmap = window.grab()
                    assert pixmap.save(str(output / name))
                    evidence['captures'].append({
                        'file': name, 'locale': locale, 'curve': window.curve_key(), 'scope': scope,
                        'logical_size': [window.width(), window.height()],
                        'pixel_size': [pixmap.width(), pixmap.height()],
                        'device_pixel_ratio': pixmap.devicePixelRatio(),
                        'curve_plot_size': [editor.width(), editor.height()],
                        'curve_plot_visible_fraction': visible_fraction(editor),
                        'value_field_visible_fraction': visible_fraction(window.selective_curve_control.value_spin),
                        'scope_canvas_size': [window.scopes.canvas.width(), window.scopes.canvas.height()]})
        (output / 'capture.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(evidence, ensure_ascii=False), flush=True)
    finally:
        window.player.stop()
        window.cancel_jobs()
        for job in list(window.jobs):
            assert job.wait(5000)
        window.dirty = False
        window.closing = True
        window.close()
        window.deleteLater()
        pump(app)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        pump(app)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    faulthandler.dump_traceback_later(90)
    try:
        capture(parser.parse_args().output.resolve())
    finally:
        faulthandler.cancel_dump_traceback_later()

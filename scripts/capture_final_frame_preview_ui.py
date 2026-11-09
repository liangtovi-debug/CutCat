"""Actual final-composite Qt captures, worker heartbeat and cancellation.

Run each --scale in a fresh process. Locally generated original media only;
Linux/offscreen evidence is not native Windows/macOS or color-monitor approval.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True, type=Path)
parser.add_argument('--scale', type=float, default=1)
args = parser.parse_args()
os.environ['QT_SCALE_FACTOR'] = str(args.scale)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtGui import QFont, QFontInfo
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionButton
from lumen.model import Editor
from lumen.ui import MainWindow
from scripts.benchmark_final_frame_preview import make_scene

out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
font = QFont('Noto Sans CJK SC', 10)
files = ('lumen/ui.py', 'lumen/ui_final_frame_preview.py', 'lumen/final_frame_preview.py',
         'lumen/ui_frame_grade_preview.py', 'lumen/ui_interval_preview.py',
         'lumen/caption_render.py', 'lumen/caption_layout.py', 'lumen/source_viewer.py',
         'lumen/i18n.py', 'lumen/i18n_catalog_errors.py',
         'lumen/i18n_catalog_final_frame_preview.py', 'scripts/capture_final_frame_preview_ui.py',
         'scripts/benchmark_final_frame_preview.py')
identity = lambda: {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}
source_before = identity()
records = []


def events():
    for _ in range(5): app.processEvents()


def until(condition, timeout=180):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.002)
    events()
    assert condition()


def close(w):
    w.cancel_jobs()
    for job in list(w.jobs): assert job.wait(5000)
    events(); w.dirty = False; w.closing = True; w.close(); events(); w.deleteLater(); events()


def main_controls(w, tag):
    """Actual narrow main-window playback row, including all previous actions."""
    viewer = w.preview_button.parentWidget()
    def find_row(layout):
        if layout.indexOf(w.preview_button) >= 0:
            return layout
        for index in range(layout.count()):
            child = layout.itemAt(index).layout()
            if child is not None:
                result = find_row(child)
                if result is not None:
                    return result
    row = find_row(viewer.layout())
    controls = [row.itemAt(i).widget() for i in range(row.count()) if row.itemAt(i).widget()]
    assert len(controls) == 5 and controls[0] is w.play_button
    assert controls[3] is w.preview_button and controls[4] is w.final_frame_preview_button
    bounds = controls[0].geometry()
    sizes = []
    for button in controls:
        option = QStyleOptionButton(); option.initFrom(button); option.text = button.text()
        contents = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
        text_width = button.fontMetrics().horizontalAdvance(button.text())
        assert text_width <= contents.width(), (button.text(), text_width, contents.width())
        assert button.visibleRegion().boundingRect() == button.rect()
        assert viewer.rect().contains(button.geometry())
        bounds = bounds.united(button.geometry())
        sizes.append(dict(text=button.text(), width=button.width(), text_width=text_width,
                          content_width=contents.width()))
    name = tag + '-main-controls.png'
    assert viewer.grab(bounds.adjusted(-4, -4, 4, 4)).save(str(out / name))
    return dict(main_window_size=[w.width(), w.height()], controls=sizes, path=name)


def shot(d, name):
    assert d.grab().save(str(out / name))
    return name


with tempfile.TemporaryDirectory(prefix='cutcat-final-frame-ui-') as raw:
    temp = Path(raw)
    scene = make_scene(temp / 'scene', Fraction(24), width=320, height=180, length=30)
    for locale in ('en', 'zh-CN'):
        for geometry, requested in (('normal', (760, 650)), ('narrow', (360, 600))):
            tag = f'{locale}-{geometry}-dpi{args.scale:g}'
            w = MainWindow(workspace_settings=QSettings(str(temp / (tag + '.ini')), QSettings.Format.IniFormat),
                recovery_root=temp / tag, ui_language=locale)
            w.setFont(font); w.editor = Editor(deepcopy(scene)); w.refresh(); w.seek_frame(5)
            w.resize(1000, 700); w.show(); events()
            row_receipt = main_controls(w, tag) if geometry == 'narrow' else None
            before = (w.project.to_dict(), len(w.editor._undo), w.revision, dict(w.preview_paths))
            d = w.open_final_frame_preview(); d.setFont(font); d.timer.stop()
            d.resize(*requested); d.fit_available_screen(); events()
            assert not w.jobs and d.snapshot is None
            heartbeat = []; timer = QTimer(); timer.setInterval(16)
            timer.timeout.connect(lambda: heartbeat.append(time.monotonic()))
            timer.start(); started = time.monotonic()
            QApplication.setActiveWindow(d); d.activateWindow(); d.refresh_button.setFocus(Qt.FocusReason.TabFocusReason)
            events(); assert d.refresh_button.hasFocus()
            QTest.keyClick(d.refresh_button, Qt.Key.Key_Space)
            until(lambda: not w.jobs and (d.current() or d.state in ('unsupported', 'stale', 'canceled')))
            assert d.current(), (d.state, d.status.text())
            displayed = time.monotonic(); timer.stop()
            assert d.snapshot.identity.timeline_frame == 5
            assert d.snapshot.stage == 'final_composite_sdr'
            assert d.snapshot.width == 320 and d.snapshot.height == 180
            assert len(d.snapshot.rgb_bytes) == 320 * 180 * 3
            assert d.scroll.horizontalScrollBar().maximum() == 0
            assert d.canvas.visibleRegion().boundingRect() == d.canvas.rect()
            assert d.stage.visibleRegion().boundingRect() == d.stage.rect()
            assert d.stage.height() >= d.stage.heightForWidth(d.stage.width())
            for label in (d.status, d.metadata, d.profile, d.precision, d.freshness):
                assert label.wordWrap() and label.width() <= d.scroll.viewport().width()
                assert label.height() >= label.heightForWidth(label.width()), (label.text(), label.height(), label.heightForWidth(label.width()))
            for button in (d.refresh_button, d.cancel_button, d.close_button, d.timeline_button):
                assert button.accessibleName() and button.visibleRegion().boundingRect() == button.rect()
                assert d.rect().contains(button.mapTo(d, button.rect().bottomRight()))
            d.scroll.verticalScrollBar().setValue(0); events()
            paths = [shot(d, tag + '-top.png')]
            if d.scroll.verticalScrollBar().maximum():
                d.scroll.verticalScrollBar().setValue(d.scroll.verticalScrollBar().maximum()); events()
                paths.append(shot(d, tag + '-bottom.png'))
            assert before == (w.project.to_dict(), len(w.editor._undo), w.revision, dict(w.preview_paths))
            assert w.video.frame_provenance is None and w.scopes.data is None and w.video.reference is None
            intervals = [b-a for a, b in zip([started, *heartbeat], [*heartbeat, displayed])]
            records.append(dict(locale=locale, geometry=geometry, requested_size=list(requested),
                logical_size=[d.width(), d.height()], device_pixel_ratio=d.devicePixelRatioF(),
                actual_canvas=[320, 180], timeline_frame=5, display_stage=d.snapshot.stage,
                time_to_verified_display_seconds=displayed-started,
                heartbeat_ticks=len(heartbeat), heartbeat_max_gap_seconds=max(intervals),
                heartbeat_median_gap_seconds=statistics.median(intervals),
                horizontal_scroll=d.scroll.horizontalScrollBar().maximum(),
                vertical_scroll=d.scroll.verticalScrollBar().maximum(),
                controls_visible=True, main_playback_row=row_receipt,
                source_history_cache_unchanged=True, paths=paths))
            d.close_button.setFocus(Qt.FocusReason.TabFocusReason); events()
            QTest.keyClick(d.close_button, Qt.Key.Key_Space); events()
            assert d._closed_flag and d.snapshot is None and d.canvas.image.isNull()
            close(w)

    # Actual worker cancellation, not a synthetic sleep. A 1080p canvas expands
    # the same tiny source; no extra full-HD source file or full RGB cache.
    large = deepcopy(scene); large.width = 1920; large.height = 1080
    w = MainWindow(workspace_settings=QSettings(str(temp / 'cancel.ini'), QSettings.Format.IniFormat),
        recovery_root=temp / 'cancel', ui_language='en')
    w.editor = Editor(large); w.refresh(); w.seek_frame(5); w.show(); events()
    d = w.open_final_frame_preview(); d.timer.stop()
    cancel_at = []; started = time.monotonic()
    def cancel():
        cancel_at.append(time.monotonic()); d.cancel_refresh()
    d.refresh_frame(); QTimer.singleShot(50, cancel)
    until(lambda: bool(cancel_at) and not w.jobs)
    canceled = time.monotonic()
    cancellation = dict(canvas=[1920, 1080], requested_after_seconds=.05,
        actual_cancel_after_seconds=cancel_at[0]-started,
        cancel_to_idle_seconds=canceled-cancel_at[0],
        no_display=d.snapshot is None and d.canvas.image.isNull(),
        no_queued_request=d._pending is None, state=d.state)
    assert cancellation['no_display'] and cancellation['no_queued_request'] and d.state == 'canceled'
    assert cancellation['cancel_to_idle_seconds'] < 5
    d.reject(); close(w)

source_after = identity()
report = dict(status='passed' if source_before == source_after else 'source_changed_during_run',
    platform='Linux/offscreen', native_windows_macos_validation=False,
    font_family=QFontInfo(font).family(), source_before=source_before, source_after=source_after,
    scale=args.scale, records=records, cancellation=cancellation,
    screenshot_bytes=sum(p.stat().st_size for p in out.glob('*.png')))
(out / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
print(json.dumps({k: report[k] for k in ('status', 'platform', 'scale', 'screenshot_bytes')}, indent=2))
raise SystemExit(0 if report['status'] == 'passed' else 2)

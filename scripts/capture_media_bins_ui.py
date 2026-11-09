"""Repeatable synthetic offscreen media-bin UI geometry and interaction review.

Default launches isolated DPR 1/1.5/2 processes. PNGs require visual review;
JSON geometry assertions do not claim native platform or real-media testing.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--scale', type=float)
args = parser.parse_args()
if args.scale is None:
    for scale in (1, 1.5, 2):
        subprocess.run([sys.executable, __file__, '--output', str(args.output), '--scale', str(scale)], check=True)
    raise SystemExit(0)
os.environ['QT_SCALE_FACTOR'] = str(args.scale)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from lumen.model import Media
from lumen.ui import MainWindow
from lumen.ui_media_bins import ALL_MEDIA, ROLE

args.output.mkdir(parents=True, exist_ok=True)
app = QApplication.instance() or QApplication([])
records = []
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    for locale in ('en', 'zh-CN'):
        for mode in ('simple', 'professional'):
            for font_size in (10, 15):
                app.setFont(QFont('Noto Sans CJK SC', font_size))
                settings = QSettings(str(root / f'{locale}-{mode}-{font_size}.ini'), QSettings.Format.IniFormat)
                settings.setValue('mode', mode)
                window = MainWindow(workspace_settings=settings, ui_language=locale, recovery_root=root / 'recovery')
                parent = window.editor.create_media_bin('旅行 Travel')
                child = window.editor.create_media_bin('海边 Seaside', parent.id)
                window.editor.add_media(Media('video', str(root / 'coast.mov'), '海岸 Coast · synthetic fixture', 8, 1920, 1080, True, True))
                window.editor.add_media(Media('audio', str(root / 'waves.wav'), '海浪 Waves · synthetic fixture', 10, 0, 0, False, True))
                window.editor.move_media_to_bin(['video'], child.id)
                window.refresh(); window.show(); app.processEvents()
                for width in (1000, 1366):
                    window.resize(width, 768); app.processEvents()
                    window.main_split.setSizes([190, width - 550, 360]); app.processEvents()
                    window.media_tree.topLevelItem(2).setExpanded(True)
                    window.media_scope = ALL_MEDIA; window.refresh_media_browser()
                    window.bin.clearSelection(); window.bin.setCurrentRow(1)
                    assert window.selected_media_ids() == ['audio']
                    window.media_search.setText('Coast'); assert not window.selected_media_ids()
                    window.add_selected_media(); assert not window.project.clips
                    window.media_search.clear(); window.bin.setCurrentRow(0)
                    window.refresh_media_browser(); assert window.selected_media_ids() == ['video']
                    deadline = time.monotonic() + 10
                    while window._media_status_job is not None and time.monotonic() < deadline:
                        QTest.qWait(5); app.processEvents()
                    assert window._media_status_job is None
                    assert not window._media_status_waiting
                    stem = f'media-bins-{locale}-{mode}-font{font_size}-width{width}-scale{args.scale:g}'
                    files = []
                    for edge in ('top', 'bottom'):
                        bar = window.media_scroll.verticalScrollBar()
                        bar.setValue(0 if edge == 'top' else bar.maximum()); app.processEvents()
                        filename = f'{stem}-{edge}.png'
                        assert window.grab().save(str(args.output / filename))
                        files.append(filename)
                    browser_file = f'{stem}-browser.png'
                    assert window.media_browser.grab().save(str(args.output / browser_file))
                    files.append(browser_file)
                    assert window.width() == width
                    assert window.media_scroll.horizontalScrollBar().maximum() == 0
                    window.media_scroll.ensureWidgetVisible(window.media_actions); app.processEvents()
                    assert window.media_scroll.viewport().rect().contains(window.media_actions.mapTo(window.media_scroll.viewport(), window.media_actions.rect().center()))
                    # At least scroll reachability is required at large fonts.
                    assert window.media_scroll.widget().height() <= window.media_scroll.viewport().height() + window.media_scroll.verticalScrollBar().maximum() + 2
                    assert window.media_search.width() > 60 and window.bin.width() > 60
                    records.append(dict(locale=locale, mode=mode, font_size=font_size,
                        requested_width=width, actual_width=window.width(), actual_height=window.height(),
                        dpr=window.devicePixelRatioF(), vertical_scroll_max=window.media_scroll.verticalScrollBar().maximum(),
                        horizontal_scroll_max=window.media_scroll.horizontalScrollBar().maximum(),
                        stable_id_and_hidden_selection_checks=True, files=files,
                        evidence='Synthetic metadata; offscreen Qt only. No native desktop, decoding or export claim.'))
                window.dirty = False; window.closing = True; window.close(); app.processEvents()
(args.output / f'manifest-scale{args.scale:g}.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))

#!/usr/bin/env python3
"""Offscreen source-viewer layout evidence, not native playback certification.

Uses explicitly injected synthetic RGB pixels and a genuine immutable model
plan. No media decoder, source file, network, or Apply execution is simulated as
real evidence. Actual MOV decode/proof/apply/render is tested separately.
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scale', choices=('1', '1.5', '2'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.scale is None:
        for scale in ('1', '1.5', '2'):
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR=scale)
            subprocess.run([sys.executable, __file__, '--output', str(args.output), '--scale', scale],
                           env=env, check=True, timeout=120)
        rows = []
        for file in sorted(args.output.glob('metrics-scale-*.json')):
            rows.extend(json.loads(file.read_text()))
        (args.output / 'metrics.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
        print(json.dumps({'variants': len(rows), 'pngs': len(list(args.output.glob('*.png'))),
                          'mode': 'offscreen; injected synthetic pixels; real model impact plan'}, indent=2))
        return
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['QT_SCALE_FACTOR'] = args.scale
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import numpy as np
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication, QPushButton
    from lumen.i18n import set_locale
    from lumen.model import Editor, Media, Project
    from lumen.source_viewer import SourceFrameResult, SourceIdentity
    from lumen.ui import MainWindow
    from lumen import ui_source_viewer as ui

    class SyntheticLayoutBackend:
        def inspect_frame(self, session, cancel=None):
            pixels = np.zeros((180, 320, 3), dtype=np.uint8)
            pixels[:, :, 0] = np.arange(320)[None, :] * 255 // 319
            pixels[:, :, 1] = np.arange(180)[:, None] * 200 // 179
            pixels[:, :, 2] = 100
            identity = SourceIdentity('synthetic-layout-only', (), 'injected', session.signature)
            return SourceFrameResult(320, 180, pixels.tobytes(), session.cursor,
                                     session.generation, identity, session.token)
        def close(self): pass

    ui.SourcePreviewBackend = SyntheticLayoutBackend
    app = QApplication.instance() or QApplication([])
    def drain(condition):
        deadline = time.monotonic() + 15
        while not condition() and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.003)
        for _ in range(3): app.processEvents()
        assert condition()

    rows = []
    with tempfile.TemporaryDirectory(prefix='cutcat-source-layout-') as folder:
        root = Path(folder)
        window = MainWindow(workspace_settings=QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat),
                            recovery_root=root / 'recovery', ui_language='en')
        media = Media('synthetic', str(root / 'layout-only.mov'), 'Synthetic layout fixture / 合成布局样例', 3, 320, 180)
        window.editor = Editor(Project(width=320, height=180, media=[media]))
        window.editor.add_clip(media.id, 0, 0); window.refresh(); window.bin.setCurrentRow(0)
        window.show(); app.processEvents()
        dialog = window.open_source_viewer(); drain(lambda: not window.jobs)
        available = dialog.screen().availableGeometry()
        screen_fit = {'available': [available.x(), available.y(), available.width(), available.height()],
                      'dialog_frame': [dialog.frameGeometry().x(), dialog.frameGeometry().y(),
                                       dialog.frameGeometry().width(), dialog.frameGeometry().height()],
                      'contained': available.contains(dialog.frameGeometry()),
                      'scale': args.scale}
        assert screen_fit['contained'], screen_fit
        (args.output / f'actual-screen-fit-dpr{args.scale}.json').write_text(json.dumps(screen_fit, indent=2))
        dialog.grab().save(str(args.output / f'actual-screen-fit-dpr{args.scale}.png'))
        # Separate layout stress matrix from the actual screen-fit assertion.
        # Offscreen Qt is only 400 logical pixels wide at DPR2; deliberately
        # remove the screen cap here to exercise requested 740px layouts too.
        dialog.setMaximumSize(16777215, 16777215)
        dialog.seek(8); dialog.mark_in(); dialog.seek(29); dialog.mark_out()
        window.timeline.playhead = 20; dialog.capture_playhead()
        dialog.preview_overwrite(); drain(lambda: not window.jobs)
        assert dialog.plan is not None
        for locale in ('en', 'zh-CN'):
            set_locale(locale)
            for font_size in (10, 15):
                family = 'Noto Sans CJK SC' if 'Noto Sans CJK SC' in QFontDatabase.families() else app.font().family()
                font = QFont(family, font_size)
                window.setFont(font); window.resize(1000, 768); dialog.setFont(font)
                app.processEvents()
                entry = window.source_viewer_button
                window.media_scroll.ensureWidgetVisible(entry); app.processEvents()
                entry_width = max(entry.fontMetrics().horizontalAdvance(line) for line in entry.text().splitlines())
                entry_metrics = {'window_width': window.width(), 'font_family': entry.font().family(),
                                 'font_pt': entry.font().pointSizeF(), 'text': entry.text(),
                                 'text_width': entry_width, 'available_width': entry.width() - 24,
                                 'accessible_name': entry.accessibleName(), 'tooltip': entry.toolTip()}
                assert entry_width <= entry.width() - 24, entry_metrics
                entry.grab().save(str(args.output / f'{locale}-{font_size}pt-main-entry-dpr{args.scale}.png'))
                for width in (320, 740):
                    dialog.resize(width, 760); app.processEvents()
                    dialog.scroll.verticalScrollBar().setValue(0); dialog.canvas.setFocus()
                    app.processEvents()
                    prefix = f'{locale}-{font_size}pt-{width}px-dpr{args.scale}'
                    dialog.grab().save(str(args.output / f'{prefix}-source.png'))
                    clipped_buttons = []
                    for button in dialog.findChildren(QPushButton):
                        available = button.width() - 24
                        needed = max(button.fontMetrics().horizontalAdvance(line) for line in button.text().splitlines())
                        if needed > available:
                            clipped_buttons.append({'text': button.text(), 'needed': needed, 'available': available})
                    dialog.scroll.ensureWidgetVisible(dialog.close_button)
                    dialog.close_button.setFocus(); app.processEvents()
                    dialog.grab().save(str(args.output / f'{prefix}-apply-close.png'))
                    viewport = dialog.scroll.viewport()
                    reachable = all(viewport.rect().contains(control.mapTo(viewport, control.rect().center()))
                                    for control in (dialog.apply_button, dialog.close_button))
                    row = {'locale': locale, 'font_pt': font_size, 'width': width, 'scale': args.scale,
                           'actual_width': dialog.width(), 'device_pixel_ratio': dialog.devicePixelRatioF(),
                           'main_entry_button': entry_metrics,
                           'hscroll_maximum': dialog.scroll.horizontalScrollBar().maximum(),
                           'vertical_scroll_maximum': dialog.scroll.verticalScrollBar().maximum(),
                           'apply_and_close_reachable': reachable, 'clipped_buttons': clipped_buttons,
                           'canvas_accessible_name': dialog.canvas.accessibleName(),
                           'cursor_accessible_name': dialog.cursor_spin.accessibleName(),
                           'synthetic_layout_fixture': True,
                           'layout_matrix_screen_cap_removed': True,
                           'actual_screen_fit_verified_separately': screen_fit['contained']}
                    rows.append(row)
        dialog.reject(); drain(lambda: not window.jobs)
        window.dirty = False; window.closing = True; window.close(); app.processEvents()
    target = args.output / f'metrics-scale-{args.scale}.json'
    target.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    failures = [row for row in rows if row['clipped_buttons'] or row['hscroll_maximum'] or not row['apply_and_close_reachable']]
    if failures:
        print(json.dumps(failures, ensure_ascii=False, indent=2))
        raise SystemExit('Source viewer layout checks failed; screenshots saved for inspection.')


if __name__ == '__main__':
    main()

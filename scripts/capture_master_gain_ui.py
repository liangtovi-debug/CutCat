"""Capture staged manual master gain without source IO, rendering or analysis.

Real Qt geometry in English/Chinese, two fonts, widths and DPR settings.
Offscreen evidence does not assert native Windows/macOS desktop validation.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(output, scale):
    os.environ['QT_SCALE_FACTOR'] = str(scale)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'report.json').unlink(missing_ok=True)
    runtime = output / '_runtime'; runtime.mkdir(exist_ok=True)
    for variable, folder in (('XDG_CACHE_HOME', 'cache'), ('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data')):
        os.environ[variable] = str(runtime / folder)
    sys.path.insert(0, str(ROOT))
    from PySide6.QtCore import QSettings, QPoint
    from PySide6.QtGui import QFont, QFontDatabase, QTextLayout
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from lumen.i18n import set_locale
    from lumen.ui import MainWindow
    from lumen.ui_master_gain import open_master_gain

    app = QApplication.instance() or QApplication([])
    fixture_dir = os.environ.get('LUMEN_TEST_CJK_FONT_DIR')
    if fixture_dir:
        from scripts.provision_test_font import verify_fixture
        fixture = verify_fixture(fixture_dir)
        assert QFontDatabase.addApplicationFont(fixture['font_file']) >= 0
    # The capture must shape both languages, not merely count tofu rectangles.
    probe_font = QFont('Noto Sans CJK SC', 10)
    probe = QTextLayout('主输出增益 CutCat 0123456789', probe_font)
    probe.beginLayout(); probe.createLine().setLineWidth(1000); probe.endLayout()
    runs = probe.glyphRuns()
    assert runs and all(run.rawFont().isValid() for run in runs)
    assert all(index != 0 for run in runs for index in run.glyphIndexes())
    records, workspace_records = [], []
    with tempfile.TemporaryDirectory(prefix='master-ui-', dir=runtime) as root:
        root = Path(root)
        w = MainWindow(workspace_settings=QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat),
                       ui_language='en', recovery_root=root / 'recovery')
        w.show(); app.processEvents()
        before = deepcopy(w.project)
        dialog = open_master_gain(w); dialog.gain.setValue(-6)
        try:
            for locale in ('en', 'zh-CN'):
                set_locale(locale)
                for font_size in (10, 13):
                    font = QFont(probe_font); font.setPointSize(font_size); dialog.setFont(font)
                    for width, height in ((560, 440), (320, 360)):
                        dialog.resize(width, height); QTest.qWait(40)
                        assert (dialog.width(), dialog.height()) == (width, height)
                        assert dialog.scroll.horizontalScrollBar().maximum() == 0
                        for button in (dialog.apply_button, dialog.cancel_button):
                            assert button.visibleRegion().boundingRect() == button.rect(), button.text()
                            assert button.width() >= button.sizeHint().width(), button.text()
                        labels = [dialog.scope, dialog.range_note, dialog.warning,
                                  dialog.workflow, dialog.transaction, dialog.status]
                        for label in labels:
                            needed = label.heightForWidth(label.width())
                            assert label.height() + 1 >= needed, (locale, font_size, width, label.text(), label.height(), needed)
                            dialog.scroll.ensureWidgetVisible(label); app.processEvents()
                            assert not label.visibleRegion().isEmpty(), label.text()
                        for widget in (dialog.gain, dialog.reset_button):
                            dialog.scroll.ensureWidgetVisible(widget); app.processEvents()
                            assert widget.visibleRegion().boundingRect() == widget.rect()
                        dialog.scroll.verticalScrollBar().setValue(0); app.processEvents()
                        name = f'{locale}-font{font_size}-{width}x{height}-dpr{scale:g}.png'
                        assert dialog.grab().save(str(output / name))
                        bottom_name = None
                        if dialog.scroll.verticalScrollBar().maximum():
                            dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum())
                            app.processEvents()
                            assert dialog.status.visibleRegion().boundingRect() == dialog.status.rect()
                            bottom_name = f'{locale}-font{font_size}-{width}x{height}-dpr{scale:g}-bottom.png'
                            assert dialog.grab().save(str(output / bottom_name))
                        assert dialog._draft_value == -6 and w.project == before and not w.jobs
                        records.append(dict(locale=locale, font_size=font_size, logical_size=[width, height],
                            device_pixel_ratio=dialog.devicePixelRatioF(), horizontal_scroll=0,
                            screenshot=name, bottom_screenshot=bottom_name, labels_checked=len(labels)))
            dialog.reject()
            for locale in ('en', 'zh-CN'):
                set_locale(locale)
                for width, height in ((1366, 768), (1000, 760)):
                    w.resize(width, height); QTest.qWait(40)
                    assert (w.width(), w.height()) == (width, height)
                    w.shortcuts.update_enabled()
                    action = w.shortcuts.actions['timeline.master_gain']
                    assert action.isEnabled()
                    assert w.timeline_scroll.viewport().height() >= 120
                    name = f'workspace-{locale}-{width}x{height}-dpr{scale:g}.png'
                    assert w.grab().save(str(output / name))
                    menu = next(action_menu.menu() for action_menu in w.menuBar().actions()
                                if action_menu.menu() and action in action_menu.menu().actions())
                    menu.popup(w.menuBar().mapToGlobal(QPoint(240, 20))); app.processEvents()
                    assert menu.actionGeometry(action).height() > 0
                    menu_name = f'menu-{locale}-{width}x{height}-dpr{scale:g}.png'
                    assert menu.grab().save(str(output / menu_name)); menu.close()
                    workspace_records.append(dict(locale=locale, logical_size=[width, height],
                        device_pixel_ratio=w.devicePixelRatioF(), master_action_enabled=True,
                        timeline_viewport_height=w.timeline_scroll.viewport().height(),
                        screenshot=name, menu_screenshot=menu_name))
            assert w.project == before and not w.editor.can_undo and not w.jobs
            report = dict(status='passed', project_unchanged=True, staged_gain_db=-6,
                no_jobs_started=True, native_windows_macos_validation=False,
                records=records, workspace_records=workspace_records)
            (output / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        finally:
            dialog.reject(); w.dirty = False; w.closing = True; w.close(); app.processEvents()
            from scripts.qt_test_cleanup import cleanup_test_windows
            cleanup_test_windows(app)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scale', type=float, default=1)
    args = parser.parse_args()
    print(json.dumps(run(args.output.resolve(), args.scale), indent=2, ensure_ascii=False))

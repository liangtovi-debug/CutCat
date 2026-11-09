"""Measure and capture the actual explicit analysis dialog, using synthetic audio.

Runs a real tracked analysis once, then checks both locales, two font sizes and
normal/narrow layouts. Offscreen Linux evidence is not native Windows/macOS QA.
No network, user media, model download, persistent project edits or gain change.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import wave

ROOT = Path(__file__).resolve().parents[1]


def synchronize_capture_layout(dialog):
    """Drain only layout work for the CURRENT text, never await a shorter state.

    A previous processEvents() can run the freshness timer, change status text,
    and post a LayoutRequest not included in that event-processing turn. Reading
    height immediately then compares the new text against the old geometry.
    Do not pump general events here: keep timers/results and the current text
    unchanged while synchronously applying pending/ancestor layout work. The
    bounded convergence check is geometric, not a wait-until-the-text-fits test;
    the caller's original strict clipping assertions still decide acceptance.
    """
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QLabel, QWidget

    labels = dialog.findChildren(QLabel)
    text = tuple(label.text() for label in labels)
    widgets = (dialog, *dialog.findChildren(QWidget))
    roots = (dialog, dialog.scroll, dialog.scroll.viewport(), dialog.scroll.widget())
    for _ in range(8):
        before = tuple((widget.x(), widget.y(), widget.width(), widget.height()) for widget in widgets)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        activated = False
        for root in roots:
            if root is not None and root.layout() is not None:
                activated = root.layout().activate() or activated
        assert tuple(label.text() for label in labels) == text, 'Text changed during layout-only capture synchronization'
        after = tuple((widget.x(), widget.y(), widget.width(), widget.height()) for widget in widgets)
        if not activated and before == after:
            return
    raise AssertionError('Capture layout did not settle within eight synchronous layout passes')


def run(output, scale):
    os.environ['QT_SCALE_FACTOR'] = str(scale)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    output.mkdir(parents=True, exist_ok=True)
    for name in ('report.json', 'geometry.jsonl'):
        (output / name).unlink(missing_ok=True)
    runtime = output / '_runtime'; runtime.mkdir(exist_ok=True)
    for variable, folder in (('XDG_CACHE_HOME', 'cache'), ('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data')):
        os.environ[variable] = str(runtime / folder)
    sys.path.insert(0, str(ROOT))
    import numpy as np
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QFont, QFontInfo, QTextLayout
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from lumen.i18n import set_locale
    from lumen.model import Clip, Editor, Media, Project
    from lumen.ui import MainWindow
    from lumen.ui_audio_analysis import open_audio_analysis

    app = QApplication.instance() or QApplication([])
    from scripts.qt_layout_font import prepare_layout_font
    font_proof = prepare_layout_font(app)
    (output / 'font.json').write_text(json.dumps(font_proof, indent=2), encoding='utf-8')
    records = []
    workspace_records = []
    with tempfile.TemporaryDirectory(prefix='audio-ui-', dir=runtime) as root:
        root = Path(root)
        source = root / 'synthetic-stereo.wav'
        sample_rate = 48000
        phase = np.arange(sample_rate * 2) / sample_rate
        pcm = np.rint(np.column_stack((.8 * np.sin(2 * np.pi * 997 * phase),
                                       .3 * np.sin(2 * np.pi * 431 * phase))) * 32767).astype('<i2')
        with wave.open(str(source), 'wb') as handle:
            handle.setnchannels(2); handle.setsampwidth(2); handle.setframerate(sample_rate)
            handle.writeframes(pcm.tobytes())
        window = MainWindow(workspace_settings=QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat),
                            ui_language='en', recovery_root=root / 'recovery')
        window.editor = Editor(Project(media=[Media('m', str(source), 'Synthetic stereo', 2,
                                                       has_video=False, has_audio=True)],
            clips=[Clip('a', 'm', 2, 0, 0, 60), Clip('b', 'm', 3, 0, 0, 60)]))
        window.refresh(); window.show(); app.processEvents()
        before = deepcopy(window.project)
        dialog = open_audio_analysis(window)
        try:
            dialog.analyze()
            deadline = time.monotonic() + 30
            while (window.jobs or dialog.state == 'checking') and time.monotonic() < deadline: QTest.qWait(10)
            assert not window.jobs, 'Analysis did not stop before the capture deadline'
            assert dialog.state == 'current', dialog.status.text()
            result = dialog.result
            assert result.sample_peaks[0] > 1 and result.sample_peaks[1] < 1
            assert all(t >= s for t, s in zip(result.estimated_true_peaks, result.sample_peaks))
            assert result.overload_counts[0] > 0 and result.overload_counts[1] == 0
            for locale in ('en', 'zh-CN'):
                set_locale(locale)
                for font_size in (10, 13):
                    font = QFont(dialog.font())
                    font.setFamily(font_proof['cjk']['resolved_family'])
                    font.setPointSize(font_size); dialog.setFont(font)
                    for width, height in ((640, 620), (360, 520)):
                        dialog.resize(width, height); QTest.qWait(40)
                        synchronize_capture_layout(dialog)
                        assert (dialog.width(), dialog.height()) == (width, height)
                        widgets = dict(stage=dialog.stage, status=dialog.status,
                            proposal=dialog.proposal_button, definition=dialog.definition,
                            caveat=dialog.caveat, next_step=dialog.next_step,
                            analyze=dialog.analyze_button, cancel=dialog.cancel_button, close=dialog.close_button,
                            **dialog.metric_labels,
                            **{'value_'+key: value for key, value in dialog.values.items()})
                        geometry = []
                        for key, widget in widgets.items():
                            info = QFontInfo(widget.font())
                            glyphs = QTextLayout(widget.text(), widget.font())
                            glyphs.beginLayout(); glyphs.createLine().setLineWidth(100000); glyphs.endLayout()
                            runs = glyphs.glyphRuns()
                            geometry.append(dict(name=key, text=widget.text(), family=info.family(),
                                pixels=info.pixelSize(), points=widget.font().pointSizeF(),
                                glyph_families=[r.rawFont().familyName() for r in runs],
                                valid_glyphs=bool(runs) and all(r.rawFont().isValid() and all(r.glyphIndexes()) for r in runs),
                                rect=widget.geometry().getRect(), minimum=widget.minimumSize().toTuple(),
                                minimum_hint=widget.minimumSizeHint().toTuple(),
                                visible=widget.visibleRegion().boundingRect().getRect()))
                        diagnostic = dict(locale=locale, font_size=font_size, size=[width, height],
                            dpr=dialog.devicePixelRatioF(), viewport=dialog.scroll.viewport().size().toTuple(),
                            content=dialog.scroll.widget().size().toTuple(),
                            horizontal_maximum=dialog.scroll.horizontalScrollBar().maximum(), widgets=geometry)
                        with (output / 'geometry.jsonl').open('a', encoding='utf-8') as stream:
                            stream.write(json.dumps(diagnostic, ensure_ascii=False)+'\n')
                        assert dialog.grab().save(str(output / f'layout-{locale}-{font_size}-{width}.png'))
                        assert all(row['family'] == font_proof['cjk']['resolved_family'] and row['valid_glyphs']
                                   and row['points'] == font_size
                                   and all(f == font_proof['cjk']['resolved_family'] for f in row['glyph_families'])
                                   for row in geometry), diagnostic
                        assert dialog.scroll.horizontalScrollBar().maximum() == 0, diagnostic
                        for button in (dialog.analyze_button, dialog.cancel_button, dialog.close_button):
                            assert button.visibleRegion().boundingRect() == button.rect(), button.text()
                            assert button.width() >= button.sizeHint().width(), button.text()
                        labels = [dialog.stage, dialog.status, *dialog.values.values(), *dialog.metric_labels.values(),
                                  dialog.definition, dialog.caveat, dialog.next_step]
                        for label in labels:
                            synchronize_capture_layout(dialog)
                            needed = label.heightForWidth(label.width())
                            assert label.height() + 1 >= needed, (locale, font_size, width, label.text(), label.height(), needed)
                            dialog.scroll.ensureWidgetVisible(label); app.processEvents()
                            synchronize_capture_layout(dialog)
                            # A status change may also have moved this label.
                            dialog.scroll.ensureWidgetVisible(label)
                            # Entire prose can exceed a small viewport, but every
                            # label must be reachable without horizontal scroll.
                            visible = label.visibleRegion().boundingRect()
                            assert not visible.isEmpty(), (locale, label.text())
                        dialog.scroll.verticalScrollBar().setValue(0); app.processEvents()
                        synchronize_capture_layout(dialog)
                        name = f'{locale}-font{font_size}-{width}x{height}-dpr{scale:g}.png'
                        assert dialog.grab().save(str(output / name))
                        bottom_name = None
                        if width == 360:
                            dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum())
                            app.processEvents()
                            synchronize_capture_layout(dialog)
                            # Re-anchor to the current, not pre-event, maximum.
                            dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum())
                            assert dialog.next_step.visibleRegion().boundingRect() == dialog.next_step.rect()
                            bottom_name = f'{locale}-font{font_size}-{width}x{height}-dpr{scale:g}-bottom.png'
                            assert dialog.grab().save(str(output / bottom_name))
                        records.append(dict(locale=locale, font_size=font_size,
                            logical_size=[width, height], device_pixel_ratio=dialog.devicePixelRatioF(),
                            screenshot=name, bottom_screenshot=bottom_name, labels_checked=len(labels), horizontal_scroll=0))
            dialog.reject()
            for locale in ('en', 'zh-CN'):
                set_locale(locale)
                for width, height in ((1366, 768), (1000, 760)):
                    window.resize(width, height); QTest.qWait(50)
                    assert (window.width(), window.height()) == (width, height)
                    for widget in (window.timeline_scroll, window.menuBar()):
                        assert widget.visibleRegion().boundingRect() == widget.rect()
                    # The existing 368 px timeline is intentionally vertically
                    # scrollable in compact windows; its viewport must fit.
                    bar = window.timeline_scroll.verticalScrollBar()
                    bar.setValue(bar.maximum()); app.processEvents()
                    assert window.timeline.visibleRegion().boundingRect().bottom() >= window.timeline.height() - 1
                    bar.setValue(0); app.processEvents()
                    name = f'workspace-{locale}-{width}x{height}-dpr{scale:g}.png'
                    assert window.grab().save(str(output / name))
                    action = window.shortcuts.actions['timeline.analyze_audio']
                    menu = next(item.menu() for item in window.menuBar().actions()
                                if item.menu() is not None and action in item.menu().actions())
                    menu_action = next(item for item in window.menuBar().actions() if item.menu() is menu)
                    menu.popup(window.menuBar().mapToGlobal(window.menuBar().actionGeometry(menu_action).bottomLeft()))
                    menu.setActiveAction(action); QTest.qWait(30)
                    assert action.isEnabled() and menu.rect().contains(menu.actionGeometry(action))
                    menu_name = f'timeline-menu-{locale}-{width}x{height}-dpr{scale:g}.png'
                    assert menu.grab().save(str(output / menu_name))
                    menu.close(); app.processEvents()
                    workspace_records.append(dict(locale=locale, logical_size=[width, height],
                        screenshot=name, menu_screenshot=menu_name,
                        timeline_size=[window.timeline.width(), window.timeline.height()],
                        analyze_action_enabled=action.isEnabled()))
            assert window.project == before and not window.editor.can_undo
            report = dict(status='passed', platform=sys.platform, qt_platform=os.environ['QT_QPA_PLATFORM'],
                scale=scale, font_proof=font_proof, synthetic_audio=True, real_tracked_analysis=True, project_unchanged=True,
                native_windows_macos_validation=False, human_visual_review='required separately', records=records, workspace_records=workspace_records,
                measurements=dict(integrated_lufs=result.integrated_lufs, sample_peaks=result.sample_peaks,
                    overload_counts=result.overload_counts, first_overload_seconds=result.first_overload_seconds,
                    estimated_true_peaks=result.estimated_true_peaks, estimated_true_peak_dbtp=result.estimated_true_peak_dbtp))
            (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report, ensure_ascii=False))
        finally:
            dialog.reject(); window.cancel_jobs()
            for job in list(window.jobs): job.wait(5000)
            app.processEvents(); window.dirty = False; window.closing = True; window.close(); app.processEvents()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scale', type=float, default=1)
    args = parser.parse_args()
    run(args.output.resolve(), args.scale)

"""Actual Qt offscreen dialog geometry at all locales, fonts and fractional DPRs."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def _exercise_dialog():
    from copy import deepcopy
    from tempfile import TemporaryDirectory
    from PySide6.QtCore import QPoint, QRect, QSettings, Qt
    from PySide6.QtGui import QFont, QFontInfo, QTextLayout
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton
    from lumen.color_picker_source import capture_picker_snapshot
    from lumen.i18n import set_locale
    from lumen.model import Editor
    from lumen.qualifier import QualifierSettings, WindowKeyframe
    from lumen.ui import MainWindow
    from lumen.ui_window_animation import WindowAnimationDialog
    from test_selective_color_render import source_project
    app = QApplication.instance() or QApplication([])
    from scripts.qt_layout_font import prepare_layout_font
    font_proof = prepare_layout_font(app)
    count = 0
    evidence = os.environ.get('WINDOW_ANIMATION_SCREENSHOTS') or os.environ.get('AUTO_FRAME_REVIEW_EVIDENCE')
    if evidence:
        folder = Path(evidence)/'window-animation'; folder.mkdir(parents=True, exist_ok=True)
        (folder/f'font-{os.environ.get("QT_SCALE_FACTOR", "1")}.json').write_text(
            json.dumps(font_proof, ensure_ascii=False, indent=2), encoding='utf-8')
    def visible(widget, dialog, locale, points, size, *, whole=True):
        info = QFontInfo(widget.font())
        text = widget.text() if hasattr(widget, 'text') else widget.accessibleName()
        glyphs = QTextLayout(text or '0123456789', widget.font())
        glyphs.beginLayout(); glyphs.createLine().setLineWidth(100000); glyphs.endLayout()
        runs = glyphs.glyphRuns()
        area = bounds = None
        if isinstance(widget, QLabel) and widget.text():
            area = widget.contentsRect().adjusted(widget.margin(), widget.margin(), -widget.margin(), -widget.margin())
            flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            if widget.wordWrap():
                flags |= int(Qt.TextFlag.TextWordWrap)
            bounds = widget.fontMetrics().boundingRect(QRect(0, 0, max(1, area.width()), 100000), flags, widget.text())
        elif isinstance(widget, QPushButton):
            area = widget.rect().adjusted(12, 8, -12, -8)
            bounds = widget.fontMetrics().boundingRect(QRect(0, 0, max(1, area.width()), 100000),
                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), widget.text())
        record = dict(locale=locale, points=points, size=size, dpr=dialog.devicePixelRatioF(),
                      name=widget.accessibleName(), text=text, family=info.family(), pixels=info.pixelSize(),
                      glyph_families=[r.rawFont().familyName() for r in runs],
                      rect=widget.rect().getRect(), visible=widget.visibleRegion().boundingRect().getRect(),
                      viewport=dialog.scroll.viewport().size().toTuple(), whole=whole,
                      text_bounds=bounds.getRect() if bounds is not None else None,
                      text_area=area.getRect() if area is not None else None)
        if evidence:
            folder = Path(evidence)/'window-animation'; folder.mkdir(parents=True, exist_ok=True)
            with (folder/f'geometry-{dialog.devicePixelRatioF():g}.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record, ensure_ascii=False)+'\n')
        try:
            assert info.family() and info.pixelSize() > 0, record
            if bounds is not None:
                assert bounds.width() <= area.width() and bounds.height() <= area.height(), record
            if widget is dialog.key_timeline:
                assert widget.height() >= widget.minimumSizeHint().height(), record
                assert widget.track_y() + 13 + widget.fontMetrics().height() <= widget.height() - 8, record
            if widget in dialog.controls.values():
                assert widget.font().pointSizeF() == points, record
                assert info.pixelSize() == QFontInfo(dialog.font()).pixelSize(), record
                if locale == 'zh-CN':
                    assert info.family() == font_proof['cjk']['resolved_family'], record
            assert runs and all(r.rawFont().isValid() and all(i != 0 for i in r.glyphIndexes()) for r in runs), record
            if whole:
                assert widget.visibleRegion().boundingRect() == widget.rect(), record
            else:
                # Only prose taller than the viewport uses this path. Its full
                # text must fit its own widget and both ends must be reachable.
                assert isinstance(widget, QLabel) and widget.height() > dialog.scroll.viewport().height(), record
                top = widget.mapTo(dialog.scroll.widget(), QPoint()).y()
                bar = dialog.scroll.verticalScrollBar()
                bar.setValue(max(0, top - 8)); app.processEvents()
                assert widget.visibleRegion().contains(QPoint(widget.width() // 2, 0)), record
                assert widget.visibleRegion().boundingRect().left() == 0 and widget.visibleRegion().boundingRect().width() == widget.width(), record
                bar.setValue(top + widget.height() - dialog.scroll.viewport().height() + 8); app.processEvents()
                assert widget.visibleRegion().contains(QPoint(widget.width() // 2, widget.height() - 1)), record
                assert widget.visibleRegion().boundingRect().left() == 0 and widget.visibleRegion().boundingRect().width() == widget.width(), record
        except AssertionError:
            if evidence:
                assert dialog.grab().save(str(folder/f'failure-{locale}-{points}-{size[0]}-{dialog.devicePixelRatioF():g}.png'))
            raise
    with TemporaryDirectory() as root:
        root = Path(root)
        window = MainWindow(workspace_settings=QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat),
                            recovery_root=root / 'recovery', ui_language='en')
        project = source_project(root); project.clips[0].duration = 3
        project.width, project.height = 960, 540
        project.clips[0].grade.qualifier = QualifierSettings(enabled=True, hue_width=.5, exposure=.5,
            window_shape='ellipse', window_keyframes=[WindowKeyframe(0, .25, .5, .4, .6),
                                                       WindowKeyframe(2, .75, .5, .6, .5)])
        window.editor = Editor(project); window.refresh(); window.select_clip('picture')
        window.timeline.playhead = 1; window.resize(1280, 800); window.show(); app.processEvents()
        snapshot = capture_picker_snapshot(project, 'picture', 1, stage='qualifier')
        before = deepcopy(project)
        try:
            for locale in ('en', 'zh-CN'):
                set_locale(locale)
                for points in (10, 13, 16):
                    for size in ((740, 760), (360, 640), (320, 640)):
                        dialog = WindowAnimationDialog(window)
                        font = QFont(dialog.font())
                        if locale == 'zh-CN':
                            font.setFamily(font_proof['cjk']['resolved_family'])
                        font.setPointSize(points); dialog.setFont(font)
                        dialog.show(); dialog.accept_snapshot(snapshot); dialog.resize(*size)
                        QTest.qWait(80)
                        assert dialog.size().toTuple() == size, (locale, points, size, dialog.size())
                        fixed = [dialog.canvas, dialog.preview_choice, dialog.refresh_button,
                                 dialog.frame_spin, dialog.frame_slider, dialog.key_timeline,
                                 dialog.apply_button, dialog.close_button]
                        dialog.scroll.ensureWidgetVisible(dialog.resolution, 0, 8); QTest.qWait(30)
                        visible(dialog.resolution, dialog, locale, points, size)
                        if evidence:
                            assert dialog.grab().save(str(Path(evidence)/f'preview-note-{locale}-{points}-{size[0]}-{dialog.devicePixelRatioF():g}.png'))
                        body = dialog.scroll.widget()
                        for label in dialog.findChildren(QLabel):
                            if not label.isVisible() or not label.text():
                                continue
                            inside = body.isAncestorOf(label)
                            if inside:
                                dialog.scroll.ensureWidgetVisible(label, 0, 8); app.processEvents()
                            visible(label, dialog, locale, points, size,
                                    whole=not inside or label.height() <= dialog.scroll.viewport().height())
                        for control in [*dialog.controls.values(), dialog.interpolation, dialog.add_button,
                                        dialog.remove_button, dialog.clear_button, dialog.key_selector,
                                        dialog.key_time, dialog.move_button]:
                            if not control.isEnabled():
                                continue
                            control.setFocus(); QTest.qWait(30)
                            assert control.hasFocus(), (locale, points, size, control.text() if hasattr(control, 'text') else '')
                            shown = fixed + [control]
                            label = dialog.control_labels.get(control)
                            if label is not None:
                                shown.append(label)
                            for widget in shown:
                                visible(widget, dialog, locale, points, size)
                            assert dialog.scroll.horizontalScrollBar().maximum() == 0
                        focus = dialog.controls['window_width']; focus.setFocus(); app.processEvents()
                        dialog.render_preview(); dialog.check_current(); app.processEvents()
                        assert focus.hasFocus() and project == before and not window.editor.can_undo
                        # Native high-DPI drag coordinates and cancellation are
                        # independent of font, language and display scale.
                        dialog.select_key(0); strip = dialog.key_timeline
                        start = QPoint(round(strip.frame_x(0)), round(strip.track_y()))
                        end = QPoint(round(strip.frame_x(1)), round(strip.track_y()))
                        pending = deepcopy(dialog.settings)
                        QTest.mousePress(strip, Qt.MouseButton.LeftButton, pos=start)
                        QTest.mouseMove(strip, end)
                        assert strip.gesture_active and strip.preview_frame == 1
                        QTest.keyClick(strip, Qt.Key.Key_Escape)
                        QTest.mouseRelease(strip, Qt.MouseButton.LeftButton, pos=end)
                        assert not strip.gesture_active and dialog.settings == pending and dialog.isVisible()
                        QTest.mousePress(strip, Qt.MouseButton.LeftButton, pos=start)
                        QTest.mouseMove(strip, end)
                        dialog.key_time.setFocus(); app.processEvents()
                        QTest.mouseRelease(strip, Qt.MouseButton.LeftButton, pos=end)
                        assert not strip.gesture_active and dialog.settings == pending and project == before
                        if evidence and points in (10, 16):
                            path = Path(evidence); path.mkdir(parents=True, exist_ok=True)
                            dialog.frame_spin.setValue(1); dialog.accept_snapshot(snapshot)
                            # Capture with numeric geometry visible, not prose-only end of scroll.
                            dialog.controls['window_center_x'].setFocus(); QTest.qWait(50)
                            name = f'{locale}-font{points}-{size[0]}x{size[1]}-dpr{dialog.devicePixelRatioF():g}.png'
                            assert dialog.grab().save(str(path / name))
                        dialog.reject(); assert dialog.snapshot is None and dialog.canvas.image.isNull()
                        dialog.deleteLater(); app.processEvents(); count += 1
            print(json.dumps({'dpr': window.devicePixelRatioF(), 'layouts': count, 'locales': 2, 'fonts': 3}))
        finally:
            window.dirty = False; window.closing = True; window.close(); window.deleteLater(); app.processEvents()


@pytest.mark.parametrize('scale', ['1', '1.25', '1.5', '2'])
def test_animation_dialog_fractional_dpr_locales_font_sizes_and_focus(scale):
    code = "import runpy, sys; sys.path.insert(0, str(__import__('pathlib').Path(sys.argv[1]).parent)); runpy.run_path(sys.argv[1])['_exercise_dialog']()"
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR=scale)
    result = subprocess.run([sys.executable, '-c', code, str(Path(__file__).resolve())],
        cwd=Path(__file__).resolve().parents[1], env=env, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.strip())
    assert evidence == {'dpr': float(scale), 'layouts': 18, 'locales': 2, 'fonts': 3}

"""Reference-still UI transactions and real fractional-DPI screenshot evidence.

The fixtures are deliberately synthetic decoded Qt frames. They exercise the
actual viewer compositor and controls, not native playback or display accuracy.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest
from PySide6.QtCore import QPoint, QRect, QRectF, QSettings, QUrl, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QStyle, QStyleOptionComboBox

from lumen.i18n import get_locale, set_locale, tr
from lumen.model import Clip, Editor, Media, Project
from lumen.ui import MainWindow


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def synthetic_scene(warm=True, width=640, height=360):
    """A deterministic original scene, never user footage or an edited UI image."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(width / 640, height / 360)
    painter.fillRect(QRectF(0, 0, 640, 360), QColor('#cec0a8' if warm else '#9eb7c5'))
    painter.fillRect(QRectF(0, 260, 640, 100), QColor('#8d5f3e' if warm else '#546c77'))
    painter.fillRect(QRectF(43, 35, 165, 180), QColor('#e8d9b5' if warm else '#d0e2e5'))
    painter.fillRect(QRectF(54, 46, 143, 158), QColor('#8ab2af' if warm else '#8caec1'))
    painter.setPen(QColor('#fff0d3' if warm else '#e8eef1'))
    painter.drawLine(125, 46, 125, 204); painter.drawLine(54, 125, 197, 125)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor('#325c3b' if warm else '#356257'))
    painter.drawEllipse(QRectF(468, 89, 72, 109)); painter.drawEllipse(QRectF(505, 112, 69, 97))
    painter.fillRect(QRectF(490, 180, 56, 78), QColor('#b36b41' if warm else '#a96d58'))
    painter.setBrush(QColor('#5e392d' if warm else '#484c5d'))
    painter.drawRoundedRect(QRectF(270, 86, 102, 127), 40, 40)
    painter.setBrush(QColor('#d7a77e' if warm else '#baaaa1'))
    painter.drawEllipse(QRectF(281, 100, 81, 94))
    painter.setBrush(QColor('#b34c3c' if warm else '#955665'))
    painter.drawRoundedRect(QRectF(251, 183, 141, 122), 45, 45)
    painter.fillRect(QRectF(213, 293, 218, 19), QColor('#4e342a' if warm else '#3c4953'))
    painter.end()
    return image.convertToFormat(QImage.Format.Format_RGBA8888)


def make_window(directory, locale='en-US'):
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / 'synthetic-reference.png'
    assert synthetic_scene().save(str(source))
    target = directory / 'synthetic-current.png'
    assert synthetic_scene(False).save(str(target))
    settings = QSettings(str(directory / 'settings.ini'), QSettings.Format.IniFormat)
    window = MainWindow(workspace_settings=settings, recovery_root=directory / 'recovery',
                        ui_language=locale)
    window.editor = Editor(Project(width=640, height=360, media=[
        Media('source', str(source), 'Synthetic reference · 合成参考镜头', 4, 640, 360, True, False),
        Media('target', str(target), 'Synthetic current · 合成当前镜头', 4, 640, 360, True, False),
    ], clips=[Clip('source-shot', 'source', 0, 0, 0, 120),
              Clip('target-shot', 'target', 0, 120, 0, 120)]))
    window.refresh(); window.select_clip('source-shot')
    window.resize(1280, 900); window.show()
    return window


def close_window(window, app):
    window.cancel_jobs()
    for job in list(window.jobs): assert job.wait(5000)
    window.dirty = False; window.closing = True
    window.close(); window.deleteLater(); app.processEvents()


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    previous = get_locale()
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: pytest.fail(str(args[2])))
    window = make_window(tmp_path); app.processEvents()
    yield window
    close_window(window, app); set_locale(previous)


def choose(combo, value):
    index = combo.findData(value)
    assert index >= 0
    combo.setCurrentIndex(index)
    assert combo.currentData() == value


def decoded_frame(image, number=0, fps=30):
    frame = QVideoFrame(image)
    frame.setStartTime(round(number / fps * 1_000_000))
    frame.setEndTime(round((number + 1) / fps * 1_000_000))
    return frame


class SyntheticPlayer(QMediaPlayer):
    """Keep Qt signals/ownership, substitute only external decoder transport."""
    def setSource(self, url):
        self._synthetic_source = QUrl(url)

    def source(self):
        return getattr(self, '_synthetic_source', QUrl())


def activate_synthetic(window, directory):
    """Use the actual cache-generation path without invoking a video decoder."""
    path = directory / 'synthetic-cache.mp4'
    path.write_bytes(b'synthetic Qt frame transport fixture, not a video')
    with patch('lumen.ui_color_reference.QMediaPlayer', SyntheticPlayer):
        window.activate_preview(path, window.revision, window.preview_mode)
    window.video.videoSink().setVideoFrame(decoded_frame(synthetic_scene(), 0))
    QApplication.processEvents()
    return path


def feed_current(window, number=120, warm=False):
    window.seek_frame(number)
    window.select_clip('target-shot' if number >= 120 else 'source-shot')
    window.video.videoSink().setVideoFrame(decoded_frame(synthetic_scene(warm), number))
    QApplication.processEvents()


def transaction_state(window):
    return (deepcopy(window.project), window.revision, window.dirty,
            deepcopy(window.editor._undo), deepcopy(window.editor._redo))


def test_capture_and_view_controls_never_change_project_or_history(window, app, tmp_path):
    w = window; before = transaction_state(w)
    assert not w.capture_reference_button.isEnabled()
    assert not w.clear_reference_button.isEnabled()
    assert not w.comparison_mode_choice.isEnabled()
    assert not w.capture_reference()
    activate_synthetic(w, tmp_path)
    assert w.capture_reference_button.isEnabled()
    QTest.mouseClick(w.capture_reference_button, Qt.MouseButton.LeftButton)
    assert w.video.reference is not None and w.clear_reference_button.isEnabled()
    assert w.comparison_mode_choice.isEnabled()
    original = w.video.reference.image
    assert original == synthetic_scene()
    for workspace in ('professional', 'simple', 'professional'):
        w.set_workspace_mode(workspace); app.processEvents()
        assert w.video.reference.image == original and transaction_state(w) == before
    # External QImage edits must not mutate the stored comparison still.
    detached = w.video.reference.image; detached.fill(QColor('magenta'))
    assert w.video.reference.image == original
    feed_current(w)
    for mode in ('side_by_side', 'wipe', 'off', 'wipe', 'side_by_side'):
        choose(w.comparison_mode_choice, mode); app.processEvents()
        assert w.video.reference.image == original
        assert transaction_state(w) == before
    choose(w.comparison_mode_choice, 'wipe')
    w.wipe_slider.setFocus()
    QTest.keyClick(w.wipe_slider, Qt.Key.Key_Home)
    assert w.wipe_slider.value() == 0
    QTest.keyClick(w.wipe_slider, Qt.Key.Key_End)
    assert w.wipe_slider.value() == 100
    QTest.keyClick(w.wipe_slider, Qt.Key.Key_Left)
    assert w.wipe_slider.value() == 99
    assert transaction_state(w) == before
    assert w.video.reference.image == original
    QTest.mouseClick(w.clear_reference_button, Qt.MouseButton.LeftButton)
    assert w.video.reference is None
    assert not w.clear_reference_button.isEnabled()
    assert not w.comparison_mode_choice.isEnabled()
    assert w.comparison_mode_choice.currentData() == 'off'
    w.clear_reference(); assert transaction_state(w) == before


def test_repeated_capture_replaces_one_session_still_and_keeps_old_copy(window, app, tmp_path):
    w = window; activate_synthetic(w, tmp_path); before = transaction_state(w)
    assert w.capture_reference(); first = w.video.reference
    feed_current(w)
    for _ in range(3):
        QTest.mouseClick(w.capture_reference_button, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert w.video.reference is not first
        assert w.video.reference.image == synthetic_scene(False)
        assert first.image == synthetic_scene()
    assert transaction_state(w) == before


@pytest.mark.parametrize('locale', ['zh-CN', 'en-US'])
def test_live_translation_preserves_reference_view_and_discloses_limits(window, app, tmp_path, locale):
    w = window; activate_synthetic(w, tmp_path); assert w.capture_reference()
    feed_current(w); choose(w.comparison_mode_choice, 'wipe')
    w.wipe_slider.setValue(37)
    before = transaction_state(w); reference = w.video.reference
    set_locale(locale); app.processEvents()
    assert w.capture_reference_button.text() == tr('Replace still')
    assert w.comparison_mode_choice.currentData() == 'wipe'
    assert w.wipe_slider.value() == 37 and w.video.reference is reference
    assert w.reference_note.text()
    # Explicitly keep proxy/session/display limits discoverable in both languages.
    copy = '\n'.join([w.reference_note.text(), w.reference_note.toolTip(),
                      w.capture_reference_button.toolTip(), w.comparison_mode_choice.toolTip()])
    if locale == 'zh-CN':
        assert '8' in copy and ('会话' in copy or '本次' in copy)
        assert '导出' in copy and ('参考' in copy or '对照' in copy)
        assert '当前' in w.scopes.source_label.text()
        assert any('\u4e00' <= char <= '\u9fff' for char in w.clear_reference_button.text())
    else:
        assert '8-bit' in copy and 'session' in copy.lower()
        assert 'export' in copy.lower() and 'reference' in copy.lower()
        assert 'Current' in w.scopes.source_label.text()
    assert transaction_state(w) == before


def test_stale_current_disables_capture_but_preserves_frozen_reference(window, app, tmp_path):
    w = window; activate_synthetic(w, tmp_path); assert w.capture_reference()
    frozen = w.video.reference; choose(w.comparison_mode_choice, 'side_by_side')
    w.grade_spins['exposure'].setValue(.25); app.processEvents()
    assert w.preview_revision != w.revision
    expected = transaction_state(w)
    assert not w.capture_reference_button.isEnabled()
    assert not w.capture_reference() and w.video.reference is frozen
    for mode in ('wipe', 'off', 'side_by_side'):
        choose(w.comparison_mode_choice, mode); app.processEvents()
        assert w.video.reference is frozen and transaction_state(w) == expected
    activate_synthetic(w, tmp_path)
    assert w.capture_reference_button.isEnabled() and w.video.reference is frozen


@pytest.mark.parametrize('locale', ['en-US', 'zh-CN'])
@pytest.mark.parametrize('mode', ['side_by_side', 'wipe'])
def test_viewer_reference_current_labels_are_complete_and_never_overlap(window, app, tmp_path, monkeypatch, locale, mode):
    import lumen.ui_color_reference as reference_ui
    w = window; activate_synthetic(w, tmp_path); assert w.capture_reference()
    feed_current(w); set_locale(locale)
    viewer = reference_ui.VideoViewer(); viewer.resize(280, 180)
    viewer.reference = w.video.reference
    viewer.set_image(w.video.image, w.video.frame_provenance)
    viewer.set_comparison_mode(mode)
    calls = []
    class RecordingPainter(QPainter):
        def drawText(self, *args):
            calls.append((QRectF(args[0]), str(args[-1])))
            return super().drawText(*args)
    monkeypatch.setattr(reference_ui, 'QPainter', RecordingPainter)
    try:
        viewer.show(); app.processEvents(); calls.clear(); viewer.grab()
        assert [text for _, text in calls] == [str(tr('Reference · f{frame}', frame=0)),
                                               str(tr('Current · f{frame}', frame=120))]
        assert len(calls) == 2 and not calls[0][0].intersects(calls[1][0])
        assert all(QRectF(viewer.rect()).contains(rect) for rect, _ in calls)
    finally:
        viewer.close(); viewer.deleteLater(); app.processEvents()


@pytest.mark.parametrize('mode,position', [('off', .5), ('side_by_side', .5),
                                          ('wipe', 0.), ('wipe', .5), ('wipe', 1.)])
def test_mismatched_aspect_keeps_letterbox_and_wipe_does_not_leak_current_pixels(window, app, tmp_path, mode, position):
    from lumen.ui_color_reference import ReferenceStill, VideoViewer
    w = window; activate_synthetic(w, tmp_path); assert w.capture_reference()
    feed_current(w)
    reference = QImage(80, 160, QImage.Format.Format_RGBA8888); reference.fill(QColor('#e24444'))
    current = QImage(160, 80, QImage.Format.Format_RGBA8888); current.fill(QColor('#3377dd'))
    viewer = VideoViewer(); viewer.resize(400, 240)
    viewer.reference = ReferenceStill.capture(reference, w.video.reference.provenance)
    viewer.set_image(current, w.video.frame_provenance)
    frozen_bytes = viewer.reference.pixels
    viewer.set_comparison_mode(mode); viewer.set_wipe_position(position)
    viewer.show(); app.processEvents()
    try:
        capture = viewer.grab().toImage(); dpr = viewer.devicePixelRatioF()
        def pixel(x, y):
            return capture.pixelColor(round(x * dpr), round(y * dpr)).name()
        if mode == 'off' or (mode == 'wipe' and position == 0):
            assert pixel(50, 120) == '#3377dd' and pixel(300, 120) == '#3377dd'
            assert pixel(200, 230) == '#0d1117'
        elif mode == 'side_by_side':
            # Independent expectations: 198px half-panes with 4px gap;
            # portrait 120x240 centered in left, landscape 198x99 in right.
            assert pixel(20, 120) == '#0d1117' and pixel(70, 120) == '#e24444'
            assert pixel(300, 45) == '#0d1117' and pixel(300, 120) == '#3377dd'
            assert pixel(200, 120) == '#0d1117'
        else:
            # The portrait reference is fitted to x=[140,260), whereas the
            # current landscape fills x=[0,400), y=[20,220). The wipe must
            # clear its left letterbox rather than leak blue current pixels.
            assert pixel(50, 120) == '#0d1117' and pixel(160, 120) == '#e24444'
            if position == .5:
                assert pixel(240, 120) == '#3377dd' and pixel(300, 120) == '#3377dd'
            else:
                assert pixel(240, 120) == '#e24444' and pixel(300, 120) == '#0d1117'
        assert viewer.reference.pixels == frozen_bytes
        assert viewer.reference.image == reference and viewer.image == current
    finally:
        viewer.close(); viewer.deleteLater(); app.processEvents()


@pytest.mark.parametrize('locale', ['en-US', 'zh-CN'])
@pytest.mark.parametrize('scale', [1., 1.25, 1.5, 2.])
def test_narrow_reference_controls_at_real_fractional_dpi(tmp_path, locale, scale):
    output = tmp_path / f'{locale}-{scale:g}'
    requested = os.environ.get('COLOR_REFERENCE_SCREENSHOTS')
    if requested: output = Path(requested) / output.name
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--layout',
                             str(output), locale, str(scale)], cwd=ROOT,
                            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR=str(scale)),
                            text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / 'geometry.json').read_text())
    font_evidence = json.loads((output / 'font.json').read_text())
    assert font_evidence['cjk']['glyph_count'] > 0 and font_evidence['ui']['glyph_count'] > 0
    if os.environ.get('LUMEN_TEST_CJK_FONT_DIR'):
        from scripts.provision_test_font import FONT_SHA256, LICENSE_SHA256, FONT_FAMILY
        assert font_evidence['fixture']['font_sha256'] == FONT_SHA256
        assert font_evidence['fixture']['license_sha256'] == LICENSE_SHA256
        assert font_evidence['cjk']['resolved_family'] == FONT_FAMILY
        assert set(font_evidence['cjk']['glyph_families']) == {FONT_FAMILY}
    assert report['scale'] == scale and report['locale'] == locale
    assert len(report['records']) == 6
    assert len(report['status_records']) == 4
    for record in report['status_records']:
        assert record['video_fully_visible'] and record['scopes_fully_visible']
        assert record['capture_disabled'] and record['reference_preserved']
        assert record['display_only_state_unchanged']
        assert record['status_tooltip_complete']
        assert (output / record['screenshot']).is_file()
    for record in report['records']:
        assert record['dpr'] == pytest.approx(scale)
        assert record['window_size'] in ([1000, 700], [1000, 820])
        assert record['scopes_fully_visible'] and record['scope_canvas_height'] >= 44
        assert record['video_fully_visible']
        assert record['viewer_width'] <= 470
        assert all(row['fully_visible'] and row['text_fits'] for row in record['controls'])
        assert record['controls_overlap'] is False
        assert record['wipe_visible'] == (record['mode'] == 'wipe')
        assert record['wipe_fully_visible']
        assert record['project_and_history_unchanged']
        assert (output / record['screenshot']).is_file()
        assert (output / record['viewer_screenshot']).is_file()


def control_geometry(widget):
    option = QStyleOptionComboBox()
    if hasattr(widget, 'currentText'):
        widget.initStyleOption(option)
        box = widget.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                           QStyle.SubControl.SC_ComboBoxEditField, widget)
        text_width = widget.fontMetrics().horizontalAdvance(widget.currentText())
        usable_width = box.width()
        text = widget.currentText()
    else:
        text = widget.text()
        text_width = widget.fontMetrics().horizontalAdvance(text)
        usable_width = widget.width() - (16 if widget.inherits('QPushButton') else 0)
    return dict(text=text, rect=[widget.x(), widget.y(), widget.width(), widget.height()],
                fully_visible=widget.visibleRegion().boundingRect() == widget.rect(),
                text_fits=text_width <= usable_width,
                text_width=text_width, usable_width=usable_width)


def capture_layout(output, locale, scale):
    """Save untouched native QWidget captures, using only synthetic scene pixels."""
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    from scripts.qt_layout_font import prepare_layout_font
    font_evidence = prepare_layout_font(app)
    (output / 'font.json').write_text(json.dumps(font_evidence, indent=2), encoding='utf-8')
    runtime = output / '_runtime'
    w = make_window(runtime, locale)
    w.set_workspace_mode('professional')
    w.inspector_tabs.setCurrentIndex(1); w.color_tabs.setCurrentIndex(0)
    w.resize(1000, 820); w.side_tabs.setFixedWidth(320)
    w.main_split.setSizes([210, 450, 320])
    activate_synthetic(w, runtime); assert w.capture_reference()
    feed_current(w)
    before = transaction_state(w); records = []
    try:
        for height in (700, 820):
            w.resize(1000, height)
            for mode in ('off', 'side_by_side', 'wipe'):
                choose(w.comparison_mode_choice, mode)
                if mode == 'wipe': w.wipe_slider.setValue(43)
                for _ in range(6): app.processEvents(); QTest.qWait(20)
                controls = [w.capture_reference_button, w.clear_reference_button, w.comparison_mode_choice, w.reference_note]
                visible = controls + ([w.wipe_slider] if w.wipe_slider.isVisible() else [])
                positions = [QRect(widget.mapTo(w, QPoint()), widget.size()) for widget in visible]
                overlap = any(a.intersects(b) for i, a in enumerate(positions) for b in positions[i+1:])
                screenshot = f'reference-{locale}-{mode}-{height}-{scale:g}.png'
                viewer_screenshot = f'viewer-{screenshot}'
                assert w.grab().save(str(output / screenshot))
                assert w.video.parentWidget().grab().save(str(output / viewer_screenshot))
                records.append(dict(mode=mode, window_size=[w.width(), w.height()],
                    viewer_width=w.video.width(), viewer_height=w.video.height(),
                    video_fully_visible=w.video.visibleRegion().boundingRect() == w.video.rect(),
                    scopes_fully_visible=all(child.visibleRegion().boundingRect() == child.rect()
                        for child in (w.scopes.source_label, w.scopes.mode_choice, w.scopes.canvas)),
                    scope_canvas_height=w.scopes.canvas.height(),
                    dpr=w.devicePixelRatioF(), controls=[control_geometry(widget) for widget in controls],
                    controls_overlap=overlap, wipe_visible=w.wipe_slider.isVisible(),
                    wipe_fully_visible=not w.wipe_slider.isVisible() or w.wipe_slider.visibleRegion().boundingRect() == w.wipe_slider.rect(),
                    project_and_history_unchanged=transaction_state(w) == before,
                    screenshot=screenshot, viewer_screenshot=viewer_screenshot))
        choose(w.comparison_mode_choice, 'side_by_side')
        w.resize(1440, 960); w.side_tabs.setFixedWidth(360)
        w.main_split.setSizes([210, 800, 360])
        w.vertical_split.setSizes([700, 180])
        basic_top = w.color_tabs.mapTo(w.inspector.widget(), QPoint(0, 0)).y()
        w.inspector.verticalScrollBar().setValue(basic_top)
        for _ in range(6): app.processEvents(); QTest.qWait(20)
        assert w.grab().save(str(output / 'reference-overview.png'))
        w.side_tabs.setFixedWidth(320); w.main_split.setSizes([210, 450, 320])
        w.grade_spins['contrast'].setValue(1.1)
        reference = w.video.reference; edited = transaction_state(w)
        assert not w.capture_reference_button.isEnabled() and reference is not None
        stale_text = w.preview_label.text()
        status_records = []
        for height in (700, 820):
            w.resize(1000, height)
            for state, text in [('stale_edit', stale_text), ('rendering_text_probe',
                    tr('Rendering AFTER · all corrections… Timeline stays editable.'))]:
                w.preview_label.setText(text)
                choose(w.comparison_mode_choice, 'wipe')
                for _ in range(6): app.processEvents(); QTest.qWait(20)
                name = f'reference-{locale}-{state}-{height}-{scale:g}.png'
                assert w.video.parentWidget().grab().save(str(output / name))
                status_records.append(dict(state=state, height=height,
                    scope_geometry={key:dict(rect=value.geometry().getRect(), visible=value.visibleRegion().boundingRect().getRect(), minimum=value.minimumSize().toTuple(), hint=value.minimumSizeHint().toTuple()) for key,value in dict(panel=w.scopes,source=w.scopes.source_label,selector=w.scopes.mode_choice,canvas=w.scopes.canvas,viewer=w.video.parentWidget(),main_split=w.main_split,vertical_split=w.vertical_split,timeline_panel=w.timeline_scroll.parentWidget(),central=w.centralWidget(),window=w).items()},
                    video_fully_visible=w.video.visibleRegion().boundingRect() == w.video.rect(),
                    scopes_fully_visible=all(child.visibleRegion().boundingRect() == child.rect()
                        for child in (w.scopes.source_label, w.scopes.mode_choice, w.scopes.canvas)),
                    capture_disabled=not w.capture_reference_button.isEnabled(),
                    reference_preserved=w.video.reference is reference,
                    display_only_state_unchanged=transaction_state(w) == edited,
                    status_tooltip_complete=str(w.preview_label.text()) in w.preview_label.toolTip(),
                    screenshot=name))
        (output / 'geometry.json').write_text(json.dumps(dict(locale=locale, scale=scale,
            scope=f'Qt offscreen on {sys.platform}; synthetic decoded-frame UI, not desktop display or color accuracy',
            records=records, status_records=status_records), ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        close_window(w, app)


if __name__ == '__main__':
    assert sys.argv[1] == '--layout'
    capture_layout(Path(sys.argv[2]), sys.argv[3], float(sys.argv[4]))

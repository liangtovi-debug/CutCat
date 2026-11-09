"""Real offscreen Qt layout checks, independent of native platform sign-off."""
import os
import json
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QEvent, QPoint, QSettings, Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QFontMetrics, QHelpEvent, QTextLayout
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel,
    QLineEdit, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from lumen.ui_style import LayoutTokens, STYLE, apply_ui_layout_tokens, _resolve_native_ui_font


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def settle(app):
    # A resize can schedule one font-aware relayout and a viewport adjustment.
    for _ in range(12):
        app.processEvents()


def test_styled_ui_font_shapes_visible_latin_glyphs(app, qt_test_font):
    """Geometry is meaningful only when the test process has a working font."""
    label = QLabel('CutCatABCxyz0123456789')
    label.setStyleSheet(STYLE)
    apply_ui_layout_tokens(label)
    label.show()
    settle(app)
    try:
        info = QFontInfo(label.font())
        assert info.family() and info.pixelSize() > 0, (
            'The Qt test platform has no UI font. Provision the verified test '
            'fixture and set LUMEN_TEST_CJK_FONT_DIR before running pytest.'
        )
        layout = QTextLayout(label.text(), label.font())
        layout.beginLayout()
        layout.createLine().setLineWidth(1000)
        layout.endLayout()
        runs = layout.glyphRuns()
        assert runs
        indexes = []
        for run in runs:
            raw_font = run.rawFont()
            assert raw_font.isValid()
            for index in run.glyphIndexes():
                indexes.append(index)
                assert index != 0, 'A missing-glyph box is not rendered Latin text'
                mask = raw_font.alphaMapForGlyph(index)
                assert not mask.isNull() and any(bytes(mask.constBits()))
        assert len(indexes) == len(label.text())
        if qt_test_font is not None:
            assert QFontDatabase.applicationFontFamilies(qt_test_font['font_id'])
    finally:
        label.close()
        label.deleteLater()
        settle(app)


def test_generic_windows_font_uses_installed_native_family(app, monkeypatch):
    import lumen.ui_style as style

    monkeypatch.setattr(style.sys, 'platform', 'win32')
    monkeypatch.setattr(QFontDatabase, 'families', lambda: ['Arial', 'Segoe UI', 'Noto Sans CJK SC'])
    original = QFont('Sans Serif', 14)
    original.setFamilies(['Sans Serif', 'Noto Sans CJK SC'])
    resolved = _resolve_native_ui_font(original)
    assert resolved.families() == ['Segoe UI', 'Noto Sans CJK SC']
    assert resolved.pointSizeF() == 14
    assert original.families()[0] == 'Sans Serif'
    explicit = QFont('A user-selected family', 12)
    assert _resolve_native_ui_font(explicit).families() == explicit.families()


def test_native_font_resolution_never_uses_an_uninstalled_family(app, monkeypatch):
    import lumen.ui_style as style

    monkeypatch.setattr(style.sys, 'platform', 'win32')
    monkeypatch.setattr(QFontDatabase, 'families', lambda: [])
    font = QFont('Sans Serif', 10)
    assert _resolve_native_ui_font(font).families() == font.families()


@pytest.fixture
def sample(app):
    window = QWidget()
    window.setStyleSheet(STYLE)
    outer = QVBoxLayout(window)
    forms = []
    for title in ('Timing · 时间', 'Appearance · 样式'):
        group = QGroupBox(title)
        form = QFormLayout(group)
        number = QSpinBox()
        number.setRange(0, 100_000_000)
        number.setValue(1234)
        form.addRow('Timeline start', number)
        form.addRow('Linear exposure · stops / 曝光调整', QDoubleSpinBox())
        combo = QComboBox()
        combo.addItems(['Rec.709 encoded RGB', 'A much longer English / 中文字幕 choice that must not widen the form'])
        combo.setToolTip('Original help stays available.')
        # Exercise Qt's stale minimumSizeHint cache before applying the helper.
        combo.minimumSizeHint()
        form.addRow('Source transfer', combo)
        form.addRow('中文 / English subtitle language', QLineEdit('zh'))
        outer.addWidget(group)
        forms.append(form)
    outer.addStretch()
    apply_ui_layout_tokens(window)
    window.resize(520, 900)
    window.show()
    settle(app)
    yield window, forms
    window.close()
    window.deleteLater()
    settle(app)


def row_widgets(form, row):
    return (form.itemAt(row, QFormLayout.ItemRole.LabelRole).widget(),
            form.itemAt(row, QFormLayout.ItemRole.FieldRole).widget())


def root_position(widget, root):
    return widget.mapTo(root, QPoint(0, 0))


@pytest.mark.parametrize('extra_width', [0, 80, 260])
def test_wide_forms_share_label_and_field_columns(sample, app, extra_width):
    root, forms = sample
    # "Wide" depends on actual font metrics.  Noto's taller line spacing in a
    # fontless offscreen backend legitimately stacks a 440-pixel form.  Leave
    # room for outer/group margins above the font-derived column breakpoint.
    width = LayoutTokens.from_font(root.font()).column_breakpoint + 80 + extra_width
    root.resize(width, 900)
    settle(app)
    field_starts, label_starts = [], []
    for form in forms:
        assert form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.DontWrapRows
        previous_bottom = -1
        for row in range(form.rowCount()):
            label, field = row_widgets(form, row)
            field_starts.append(root_position(field, root).x())
            label_starts.append(root_position(label, root).x())
            assert label.height() >= label.heightForWidth(label.width())
            assert field.geometry().right() <= form.contentsRect().right()
            assert label.y() > previous_bottom
            previous_bottom = max(label.geometry().bottom(), field.geometry().bottom())
            assert label.font().pointSizeF() == field.font().pointSizeF() == root.font().pointSizeF()
            assert label.font().weight() == QFont.Weight.Normal
            assert field.font().weight() == QFont.Weight.Normal
    assert len(set(field_starts)) == 1
    assert len(set(label_starts)) == 1


def test_first_line_baseline_matches_spin_editor(sample, app):
    root, forms = sample
    for form in forms:
        for row in (0, 1):
            label, spin = row_widgets(form, row)
            editor = spin.findChild(QLineEdit)
            label_metrics = QFontMetrics(label.font())
            edit_metrics = QFontMetrics(editor.font())
            label_baseline = label.y() + label.contentsRect().y() + label_metrics.ascent()
            edit_baseline = (spin.y() + editor.y() +
                             (editor.height() - edit_metrics.height()) / 2 + edit_metrics.ascent())
            assert abs(label_baseline - edit_baseline) <= 2


@pytest.mark.parametrize('width', [280, 320, 360])
def test_narrow_forms_stack_and_fit_after_wide_resize(sample, app, width):
    root, forms = sample
    # A scroll viewport is the actual inspector parent. It may shrink again
    # after removing a previously wide form's minimum-width contribution.
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(root)
    scroll.resize(width, 480)
    scroll.show()
    settle(app)
    assert scroll.horizontalScrollBar().maximum() == 0
    assert root.width() == scroll.viewport().width()
    for form in forms:
        assert form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapAllRows
        for row in range(form.rowCount()):
            label, field = row_widgets(form, row)
            assert label.x() == field.x()
            assert field.y() > label.geometry().bottom()
            assert label.height() >= label.heightForWidth(label.width())
            assert field.geometry().right() <= form.contentsRect().right()
    scroll.takeWidget()
    scroll.close()


def test_font_size_changes_reflow_without_fixed_text_height(sample, app):
    root, forms = sample
    original_height = row_widgets(forms[0], 0)[1].height()
    font = QFont(root.font())
    font.setPointSizeF(14)
    root.setFont(font)
    settle(app)
    field = row_widgets(forms[0], 0)[1]
    assert field.height() > original_height
    assert field.minimumHeight() == LayoutTokens.from_font(field.font()).control_height
    for form in forms:
        for row in range(form.rowCount()):
            label, field = row_widgets(form, row)
            assert label.height() >= label.heightForWidth(label.width())
            assert field.font().pointSizeF() == 14
    font.setPointSizeF(10)
    root.setFont(font)
    settle(app)
    assert row_widgets(forms[0], 0)[1].height() == original_height


def test_apply_is_idempotent_and_keeps_values_and_help(sample, app):
    root, forms = sample
    controller = root._lumen_layout_controller
    spin = row_widgets(forms[0], 0)[1]
    combo = row_widgets(forms[0], 2)[1]
    changes = []
    spin.valueChanged.connect(changes.append)
    apply_ui_layout_tokens(root)
    apply_ui_layout_tokens(root)
    settle(app)
    assert root._lumen_layout_controller is controller
    assert spin.value() == 1234 and not changes
    combo.setCurrentIndex(1)
    assert combo.currentText() in combo.toolTip()
    assert 'Original help stays available.' in combo.toolTip()


def test_spin_and_combo_disclosures_keep_native_interaction(sample, app):
    root, forms = sample
    spin = row_widgets(forms[0], 0)[1]
    combo = row_widgets(forms[0], 2)[1]
    before = spin.value()
    QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=QPoint(spin.width() - 10, spin.height() // 4))
    assert spin.value() == before + 1
    QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=QPoint(spin.width() - 10, spin.height() * 3 // 4))
    assert spin.value() == before
    QTest.mouseClick(combo, Qt.MouseButton.LeftButton, pos=QPoint(combo.width() - 10, combo.height() // 2))
    settle(app)
    assert combo.view().isVisible()
    combo.hidePopup()
    combo.setFocus()
    QTest.keyClick(combo, Qt.Key.Key_Down)
    assert combo.currentIndex() == 1


def test_combo_tooltip_tracks_signal_blocked_model_refresh(sample, app):
    root, forms = sample
    combo = row_widgets(forms[0], 2)[1]
    changes = []
    combo.currentTextChanged.connect(changes.append)
    combo.blockSignals(True)
    combo.clear()
    combo.addItem('新字幕 / A long bilingual caption added while the combo signals are blocked')
    combo.blockSignals(False)
    settle(app)
    assert combo.currentText() in combo.toolTip()
    assert 'Original help stays available.' in combo.toolTip()
    assert not changes


def test_combo_hover_updates_signal_blocked_index_change(sample, app):
    root, forms = sample
    combo = row_widgets(forms[0], 2)[1]
    combo.blockSignals(True)
    combo.setCurrentIndex(1)
    combo.blockSignals(False)
    pos = QPoint(5, 5)
    event = QHelpEvent(QEvent.Type.ToolTip, pos, combo.mapToGlobal(pos))
    QApplication.sendEvent(combo, event)
    assert combo.currentText() in combo.toolTip()
    assert 'Original help stays available.' in combo.toolTip()


def test_hidden_tab_forms_reflow_against_actual_scroll_viewport(app):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    content = QWidget()
    outer = QVBoxLayout(content)
    tabs = QTabWidget()
    outer.addWidget(tabs)
    first = QWidget()
    QVBoxLayout(first).addWidget(QLabel('Visible tab'))
    tabs.addTab(first, 'First')
    hidden = QWidget()
    form = QFormLayout(hidden)
    field = QLineEdit('A platform-sized field')
    field.setMinimumWidth(220)
    form.addRow('Language / 语言', field)
    tabs.addTab(hidden, 'Never opened')
    scroll.setWidget(content)
    scroll.setStyleSheet(STYLE)
    apply_ui_layout_tokens(scroll)
    scroll.resize(320, 260)
    scroll.show()
    settle(app)
    assert tabs.currentIndex() == 0 and hidden.isHidden()
    assert form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapAllRows
    assert scroll.horizontalScrollBar().maximum() == 0
    tabs.setCurrentIndex(1)
    settle(app)
    assert scroll.horizontalScrollBar().maximum() == 0
    assert field.width() >= 220
    scroll.close()
    scroll.deleteLater()
    settle(app)


def test_column_breakpoint_includes_actual_field_minimum(app):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    content = QWidget()
    form = QFormLayout(content)
    field = QLineEdit('Wide native minimum')
    field.setMinimumWidth(340)
    form.addRow('Project setting', field)
    scroll.setWidget(content)
    scroll.setStyleSheet(STYLE)
    apply_ui_layout_tokens(scroll)
    scroll.resize(440, 260)
    scroll.show()
    settle(app)
    assert form.contentsRect().width() > LayoutTokens.from_font(scroll.font()).column_breakpoint
    assert form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapAllRows
    assert scroll.horizontalScrollBar().maximum() == 0
    assert field.width() >= 340
    scroll.close()
    scroll.deleteLater()
    settle(app)


def inspector_geometry_context(window, scroll, tab):
    """Keep native CI failures actionable without a remote debugger."""
    controls = []
    for widget in scroll.widget().findChildren(QWidget):
        if widget.minimumSizeHint().width() > scroll.viewport().width() - 60:
            name = next((name for name, value in vars(window).items() if value is widget), '')
            text = widget.text() if isinstance(widget, (QLabel, QLineEdit)) else ''
            info = QFontInfo(widget.font())
            controls.append((name, type(widget).__name__, text[:100], widget.isVisible(),
                             widget.geometry().getRect(), widget.minimumSizeHint().toTuple(),
                             widget.minimumSize().toTuple(), info.family(), info.pixelSize(),
                             widget.fontMetrics().height()))
    info = QFontInfo(window.font())
    return {'tab': tab, 'font': window.font().toString(),
            'resolved_font': {'family': info.family(), 'pixels': info.pixelSize(),
                              'height': window.fontMetrics().height(),
                              'sample_width': window.fontMetrics().horizontalAdvance('Enable selected clip correction')},
            'viewport': scroll.viewport().size().toTuple(),
            'content': scroll.widget().size().toTuple(),
            'horizontal_scroll_max': scroll.horizontalScrollBar().maximum(),
            'splitter': window.main_split.sizes(), 'wide_controls': controls}


@pytest.mark.parametrize('mode', ['simple', 'professional'])
@pytest.mark.parametrize('panel_width', [320, 380, 460])
def test_editor_tabs_fit_real_narrow_and_wide_inspectors(app, tmp_path, mode, panel_width):
    from lumen.ui import MainWindow

    settings = QSettings(str(tmp_path / 'workspace.ini'), QSettings.Format.IniFormat)
    settings.setValue('mode', mode)
    window = MainWindow(workspace_settings=settings)
    window.resize(1366, 768)
    window.show()
    window.main_split.setSizes([200, 1126 - panel_width, panel_width])
    try:
        before = (window.project.to_dict(), window.revision, window.dirty)
        apply_ui_layout_tokens(window)
        for index in range(window.side_tabs.count()):
            window.side_tabs.setCurrentIndex(index)
            settle(app)
            page = window.side_tabs.currentWidget()
            scrolls = ([page] if isinstance(page, QScrollArea) else []) + page.findChildren(QScrollArea)
            for scroll in scrolls:
                if scroll.isVisible():
                    context = inspector_geometry_context(window, scroll, window.side_tabs.tabText(index))
                    if (scroll.horizontalScrollBar().maximum() != 0
                            or scroll.widget().width() > scroll.viewport().width()):
                        # Pytest truncates dict assertion representations even
                        # inside JUnit XML. Retain complete native evidence.
                        directory = Path('build/ui-test-failures')
                        directory.mkdir(parents=True, exist_ok=True)
                        stem = f'{mode}-{panel_width}-{index}'
                        detail = json.dumps(context, ensure_ascii=False, indent=2)
                        (directory / f'{stem}.json').write_text(detail, encoding='utf-8')
                        window.grab().save(str(directory / f'{stem}.png'))
                        pytest.fail(detail, pytrace=False)
        assert (window.project.to_dict(), window.revision, window.dirty) == before
    finally:
        window.closing = True
        window.close()
        window.deleteLater()
        settle(app)


def test_adaptive_combo_retains_live_bilingual_tooltip_binding(app):
    from lumen.i18n import set_locale, tr
    from lumen.i18n_widgets import QComboBox as TranslatedCombo
    root = QWidget(); layout = QVBoxLayout(root)
    combo = TranslatedCombo(); combo.addItems([tr('Deblock'), tr('Denoise')])
    combo.setToolTip(tr('Deblock and denoise can soften real detail. Sharpening can amplify noise and halos. Start with low strengths.'))
    layout.addWidget(combo); apply_ui_layout_tokens(root); root.show()
    try:
        for locale, item, warning in [('zh-CN', '去块', '真实细节'),
                                      ('en', 'Deblock', 'real detail'),
                                      ('zh-CN', '去块', '真实细节')]:
            set_locale(locale); settle(app)
            assert 'setToolTip' in combo._translation_bindings
            assert combo.currentText() == item
            root._lumen_layout_controller._refresh_combo_tip(combo)
            assert item in combo.toolTip() and warning in combo.toolTip()
        combo.setCurrentIndex(1); settle(app)
        assert '降噪' in combo.toolTip() and '真实细节' in combo.toolTip()
    finally:
        root.close(); root.deleteLater(); settle(app)


def test_owned_dialog_layout_uses_its_font_and_viewport_after_parent_refresh(app):
    from PySide6.QtWidgets import QDialog
    parent = QWidget(); parent.resize(1200, 800)
    parent.setFont(QFont('Noto Sans CJK SC', 10))
    apply_ui_layout_tokens(parent)
    dialog = QDialog(parent); dialog.setMinimumWidth(320)
    dialog.setFont(QFont(parent.font().family(), 16))
    outer = QVBoxLayout(dialog); scroll = QScrollArea(); scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    body = QWidget(); form = QFormLayout(body)
    field = QDoubleSpinBox(); field.setRange(-1600, 1600); field.setDecimals(3); field.setSuffix('%')
    field.setMinimumWidth(176)
    label = QLabel('Window center X (% canvas width)'); form.addRow(label, field)
    scroll.setWidget(body); outer.addWidget(scroll)
    apply_ui_layout_tokens(dialog); parent.show(); dialog.show()
    try:
        for width in (740, 360, 320, 740):
            dialog.resize(width, 640); settle(app)
            parent._lumen_layout_controller.refresh(); settle(app)
            assert field.font().pointSizeF() == 16
            assert field.visibleRegion().boundingRect() == field.rect()
            assert scroll.horizontalScrollBar().maximum() == 0
            if width <= 360:
                assert form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapAllRows
                assert field.y() > label.geometry().bottom()
    finally:
        dialog.close(); parent.close()


def test_ownership_is_rechecked_after_new_governor_and_reparenting(app):
    root = QWidget(); root.setFont(QFont('Sans Serif', 10))
    child = QWidget(root); field = QLineEdit(child)
    apply_ui_layout_tokens(root)
    parent_controller = root._lumen_layout_controller
    parent_controller.refresh()
    assert field.font().pointSizeF() == 10
    child.setFont(QFont('Sans Serif', 16)); apply_ui_layout_tokens(child)
    parent_controller.refresh()
    assert field.font().pointSizeF() == 16
    field.setParent(root); parent_controller.refresh()
    assert field.font().pointSizeF() == 10
    field.setParent(child); child._lumen_layout_controller.refresh(); parent_controller.refresh()
    assert field.font().pointSizeF() == 16
    root.close(); root.deleteLater(); app.processEvents()


def test_new_governor_during_form_formatting_is_respected_immediately(app, monkeypatch):
    root = QWidget(); root.setFont(QFont('Sans Serif', 10))
    host = QWidget(root); form = QFormLayout(host); field = QLineEdit(); form.addRow('Field', field)
    apply_ui_layout_tokens(root); controller = root._lumen_layout_controller
    original = controller._format_form
    installed = []
    def format_and_install(current, tokens):
        original(current, tokens)
        if current is form and not installed:
            installed.append(True)
            host.setFont(QFont('Sans Serif', 16)); apply_ui_layout_tokens(host)
            assert field.font().pointSizeF() == 16
    monkeypatch.setattr(controller, '_format_form', format_and_install)
    controller.refresh()
    assert installed and host.font().pointSizeF() == field.font().pointSizeF() == 16
    controller.refresh()
    assert host.font().pointSizeF() == field.font().pointSizeF() == 16
    root.close(); root.deleteLater(); app.processEvents()


def test_new_governor_during_font_change_is_respected_immediately(app):
    root = QWidget(); root.setFont(QFont('Sans Serif', 10))
    host = QWidget(root)
    class InstallOnFont(QLineEdit):
        armed = False
        def changeEvent(self, event):
            super().changeEvent(event)
            if event.type() == QEvent.Type.FontChange and self.armed:
                self.armed = False
                host.setFont(QFont('Sans Serif', 16)); apply_ui_layout_tokens(host)
    first = InstallOnFont(host); second = QLineEdit(host)
    apply_ui_layout_tokens(root)
    first.setFont(QFont('Sans Serif', 12)); first.armed = True
    root._lumen_layout_controller.refresh()
    assert host.font().pointSizeF() == first.font().pointSizeF() == second.font().pointSizeF() == 16
    root.close(); root.deleteLater(); app.processEvents()

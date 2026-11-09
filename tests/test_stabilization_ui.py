"""Explicit bilingual stabilization editing through the real Qt inspector."""
from copy import deepcopy
from fractions import Fraction
import ast
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont, QFontDatabase, QFontInfo
from PySide6.QtWidgets import (QApplication, QLabel, QMessageBox, QWidget, QVBoxLayout,
                               QTabWidget, QGroupBox, QFormLayout, QLineEdit, QScrollArea)

from lumen.i18n import set_locale, tr_error
from lumen.model import Clip, Editor, Media, Project
from lumen.stabilization_settings import StabilizationSettings
from lumen.ui import MainWindow
from lumen.ui_style import STYLE, apply_ui_layout_tokens


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(str(args[2])))
    settings = QSettings(str(tmp_path / 'workspace.ini'), QSettings.Format.IniFormat)
    settings.setValue('mode', 'professional')
    w = MainWindow(workspace_settings=settings, ui_language='en')
    source = tmp_path / 'source.mp4'; source.write_bytes(b'mocked media')
    w.editor = Editor(Project(media=[Media('m', str(source), 'Source', 10, 640, 360, True, True)],
                              clips=[Clip('c', 'm', 0, 10, 0, 60)]))
    w.refresh(); w.select_clip('c'); w.inspector_tabs.setCurrentIndex(0)
    w.test_warnings = warnings
    yield w
    w.cancel_jobs()
    for job in list(w.jobs): assert job.wait(5000)
    w.dirty = False; w.closing = True; w.close(); app.processEvents()


def test_disabled_defaults_and_progressive_controls_do_not_edit(window):
    w = window; before = deepcopy(w.project); revision = w.revision
    assert not w.stabilization_enabled.isChecked()
    assert w.stabilization_strength.value() == 1 and not w.stabilization_strength.isEnabled()
    assert w.stabilization_smoothing.value() == 15
    assert w.stabilization_max_zoom.value() == 1.15
    assert w.stabilization_border.currentData() == 'crop'
    assert w.stabilization_advanced.isHidden()
    assert not w.apply_stabilization_button.isEnabled()
    assert not w.reset_stabilization_button.isEnabled()
    w.stabilization_advanced_toggle.setChecked(True)
    assert not w.stabilization_advanced.isHidden()
    assert w.project == before and w.revision == revision


def test_apply_is_one_explicit_undoable_edit_and_invalidates_preview(window):
    w = window; before = deepcopy(w.project); revision = w.revision
    w.preview_revision = revision; w.loaded_preview_mode = w.preview_mode
    w.stabilization_enabled.setChecked(True)
    w.stabilization_strength.setValue(.65)
    w.stabilization_smoothing.setValue(27)
    w.stabilization_max_zoom.setValue(1.24)
    w.stabilization_border.setCurrentIndex(w.stabilization_border.findData('black'))
    assert w.project == before and w.revision == revision
    assert w.apply_stabilization_button.isEnabled()
    assert not w.stabilization_max_zoom.isEnabled()
    w.apply_stabilization_button.click()
    assert w.selected_clip().stabilization == StabilizationSettings(True, .65, 27, 1.24, 'black')
    assert w.revision == revision + 1 and w.preview_revision != w.revision
    assert len(w.editor._undo) == 1 and not w.play_button.isEnabled()
    assert not w.apply_stabilization_button.isEnabled()
    after = deepcopy(w.project)
    w.apply_stabilization(); assert w.revision == revision + 1
    w.undo(); assert w.project == before
    w.redo(); assert w.project == after


def test_reset_is_real_undoable_edit_and_default_reset_only_discards_draft(window):
    w = window; revision = w.revision
    w.stabilization_enabled.setChecked(True); w.stabilization_strength.setValue(.3)
    w.reset_stabilization()
    assert not w.stabilization_enabled.isChecked() and w.stabilization_strength.value() == 1
    assert w.revision == revision and not w.editor.can_undo
    w.stabilization_enabled.setChecked(True); w.stabilization_strength.setValue(.7)
    w.apply_stabilization(); before = deepcopy(w.project)
    w.reset_stabilization_button.click()
    assert w.selected_clip().stabilization == StabilizationSettings()
    assert w.revision == revision + 2 and len(w.editor._undo) == 2
    w.undo(); assert w.project == before and w.stabilization_strength.value() == .7


def test_bypass_preserves_advanced_and_unedited_high_precision_values(window):
    w = window
    settings = StabilizationSettings(True, .123456789, 37, 1.23456789, 'black')
    w.editor.update_clip('c', stabilization=settings); w.changed()
    before = deepcopy(w.selected_clip().stabilization)
    w.stabilization_enabled.setChecked(False); w.apply_stabilization()
    before.enabled = False
    assert w.selected_clip().stabilization == before
    w.stabilization_enabled.setChecked(True); w.apply_stabilization()
    assert w.selected_clip().stabilization == settings


def test_refresh_does_not_edit_and_selection_discards_staged_changes(window):
    w = window
    w.project.clips.append(Clip('next', 'm', 0, 80, 70, 20,
                               stabilization=StabilizationSettings(True, .8, 30, 1.2, 'black')))
    w.project.validate(); w.refresh()
    before = deepcopy(w.project); revision = w.revision
    for _ in range(3): w.refresh_inspector()
    assert w.project == before and w.revision == revision
    w.stabilization_enabled.setChecked(True); w.stabilization_strength.setValue(.2)
    w.select_clip('next')
    assert w.stabilization_enabled.isChecked() and w.stabilization_strength.value() == .8
    assert not w.apply_stabilization_button.isEnabled()
    w.select_clip('c'); assert not w.stabilization_enabled.isChecked()
    assert w.project == before and w.revision == revision


def test_audio_clip_and_no_selection_disable_panel(window):
    w = window
    w.project.media.append(Media('a', '/tmp/audio.wav', 'Audio', 2, has_video=False, has_audio=True))
    w.project.clips.append(Clip('a', 'a', 2, 0, 0, 60)); w.project.validate(); w.refresh()
    w.select_clip('a'); assert not w.stabilization_group.isEnabled()
    before = deepcopy(w.project); revision = w.revision
    w._stabilization_draft.enabled = True; w.apply_stabilization()
    assert w.project == before and w.revision == revision and w.test_warnings
    w.select_clip(''); assert not w.stabilization_group.isEnabled()
    w.apply_stabilization(); w.reset_stabilization()
    assert w.project == before and w.revision == revision


def test_locale_and_workspace_switches_preserve_staged_settings(window):
    w = window; before = deepcopy(w.project); revision = w.revision
    w.stabilization_enabled.setChecked(True); w.stabilization_strength.setValue(.72)
    w.stabilization_smoothing.setValue(29); w.stabilization_max_zoom.setValue(1.26)
    w.stabilization_border.setCurrentIndex(1); w.stabilization_advanced_toggle.setChecked(True)
    draft = deepcopy(w._stabilization_draft)
    for locale in ('zh-CN', 'en', 'zh-CN'):
        set_locale(locale)
        assert w._stabilization_draft == draft
        assert w.stabilization_advanced_toggle.isChecked()
        assert w.stabilization_strength.value() == .72 and w.stabilization_smoothing.value() == 29
        assert w.project == before and w.revision == revision
    assert w.stabilization_group.title() == '画面稳定'
    assert w.stabilization_enabled.text() == '启用画面稳定'
    assert w.apply_stabilization_button.text() == '应用画面稳定'
    assert w.reset_stabilization_button.text() == '重置画面稳定'
    assert '渲染会停止' in w.stabilization_border_note.text()
    assert w.stabilization_border.currentText() == '黑边 · 保留画幅'
    w.set_workspace_mode('simple'); w.set_workspace_mode('professional')
    assert w._stabilization_draft == draft and w.project == before and w.revision == revision
    set_locale('en'); assert w.apply_stabilization_button.text() == 'Apply stabilization'


def test_speed_trim_and_split_preserve_stabilization_through_ui(window):
    w = window
    w.stabilization_enabled.setChecked(True); w.stabilization_strength.setValue(.7); w.apply_stabilization()
    settings = deepcopy(w.selected_clip().stabilization)
    w.speed_edit.setText('1.5'); w.apply_speed()
    w.in_spin.setText('1/2'); w.duration_spin.setValue(20); w.apply_timing()
    assert w.selected_clip().speed == Fraction(3, 2)
    assert w.selected_clip().stabilization == settings
    left, right = w.editor.split_clip('c', 17); w.changed()
    assert left.stabilization == right.stabilization == settings
    w.select_clip(right.id); w.stabilization_strength.setValue(.8); w.apply_stabilization()
    assert left.stabilization.strength == .7
    assert w.selected_clip().stabilization.strength == .8


def test_widget_ranges_match_schema(window):
    w = window
    assert (w.stabilization_strength.minimum(), w.stabilization_strength.maximum()) == (0, 1)
    assert (w.stabilization_smoothing.minimum(), w.stabilization_smoothing.maximum()) == (1, 120)
    assert (w.stabilization_max_zoom.minimum(), w.stabilization_max_zoom.maximum()) == (1, 1.5)
    assert [w.stabilization_border.itemData(i) for i in range(w.stabilization_border.count())] == ['crop', 'black']


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('size', [(1000, 700), (1366, 768)])
def test_expanded_controls_fit_and_are_scroll_reachable(window, app, locale, size):
    w = window; set_locale(locale); w.resize(*size); w.show()
    w.stabilization_enabled.setChecked(True); w.stabilization_advanced_toggle.setChecked(True)
    for _ in range(5): app.processEvents()
    assert (w.width(), w.height()) == size
    for control in (w.stabilization_strength, w.stabilization_smoothing,
                    w.stabilization_max_zoom, w.stabilization_border,
                    w.apply_stabilization_button, w.reset_stabilization_button):
        assert control.width() >= control.minimumSizeHint().width()
        assert control.height() >= control.minimumSizeHint().height()
        assert control.parentWidget().rect().contains(control.geometry())
        w.inspector.ensureWidgetVisible(control)
        for _ in range(2): app.processEvents()
        position = control.mapTo(w.inspector.viewport(), control.rect().center())
        assert w.inspector.viewport().rect().contains(position)
    for label in w.stabilization_group.findChildren(QLabel):
        if label.wordWrap():
            assert label.height() >= label.heightForWidth(label.width())


def test_stabilization_validation_errors_are_localized():
    set_locale('zh-CN')
    assert '视频轨道' in str(tr_error('Stabilization requires a picture clip on a video track'))
    assert '有限数值' in str(tr_error('Stabilization strength must be finite and in 0.0..1.0'))


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_expanded_stabilization_never_adds_horizontal_scroll_at_320px(window, app, locale, mode):
    w = window; set_locale(locale); w.set_workspace_mode(mode)
    w.resize(1366, 768); w.show(); w.main_split.setSizes([200, 806, 320])
    w.stabilization_enabled.setChecked(True); w.stabilization_advanced_toggle.setChecked(True)
    for _ in range(8): app.processEvents()
    assert w.inspector.horizontalScrollBar().maximum() == 0
    assert w.inspector.widget().width() <= w.inspector.viewport().width()
    for control in (w.stabilization_strength, w.stabilization_smoothing,
                    w.stabilization_max_zoom, w.stabilization_border,
                    w.apply_stabilization_button, w.reset_stabilization_button):
        w.inspector.ensureWidgetVisible(control)
        for _ in range(2): app.processEvents()
        assert w.inspector.viewport().rect().contains(
            control.mapTo(w.inspector.viewport(), control.rect().center()))


def test_stabilization_static_backend_errors_have_chinese_translations():
    from lumen.i18n_catalog_errors import ZH_CN
    root = Path(__file__).resolve().parents[1] / 'lumen'
    for module in ('stabilization_settings', 'stabilization_analysis',
                   'stabilization_math', 'stabilization_render'):
        path = root / f'{module}.py'
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                    and node.exc.args and isinstance(node.exc.args[0], ast.Constant)
                    and isinstance(node.exc.args[0].value, str)):
                assert node.exc.args[0].value in ZH_CN, (module, node.exc.args[0].value)


@pytest.fixture
def noto_font(app):
    """Use the actual CI font; a configured fixture must never silently skip."""
    configured = os.environ.get('LUMEN_TEST_CJK_FONT_DIR')
    font_id = None
    if configured:
        path = Path(configured) / 'NotoSansCJKsc-Regular.otf'
        assert path.is_file(), 'Configured native test font is missing'
        font_id = QFontDatabase.addApplicationFont(str(path))
        assert font_id >= 0, 'Configured native test font could not be registered'
        assert 'Noto Sans CJK SC' in QFontDatabase.applicationFontFamilies(font_id)
    elif 'Noto Sans CJK SC' not in QFontDatabase.families():
        pytest.skip('Noto Sans CJK SC is not installed and no native test font was configured')
    previous = QFont(app.font())
    font = QFont('Noto Sans CJK SC', 10)
    app.setFont(font)
    assert QFontInfo(app.font()).family() == 'Noto Sans CJK SC'
    try:
        yield font
    finally:
        app.setFont(previous)
        if font_id is not None:
            QFontDatabase.removeApplicationFont(font_id)


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_noto_font_hidden_and_expanded_edit_minimums_fit_320px(app, tmp_path, noto_font, locale, mode):
    settings = QSettings(str(tmp_path / 'noto-workspace.ini'), QSettings.Format.IniFormat)
    settings.setValue('mode', mode)
    w = MainWindow(workspace_settings=settings, ui_language=locale)
    source = tmp_path / 'source.mp4'; source.write_bytes(b'mocked media')
    w.editor = Editor(Project(media=[Media('m', str(source), 'Source', 10, 640, 360, True, True)],
                              clips=[Clip('c', 'm', 0, 10, 0, 60)]))
    w.refresh(); w.select_clip('c')
    assert w.inspector_tabs.currentIndex() == 1
    w.resize(1366, 768); w.show(); w.main_split.setSizes([200, 806, 320])
    before = deepcopy(w.project); revision = w.revision
    try:
        assert QFontInfo(w.font()).family() == 'Noto Sans CJK SC'
        controller = w._lumen_layout_controller
        refresh = controller.refresh
        refresh_count = []
        def counted_refresh():
            refresh_count.append(1)
            return refresh()
        controller.refresh = counted_refresh
        for state in ('never-opened', 'expanded', 'collapsed', 'hidden-again'):
            if state == 'expanded':
                w.inspector_tabs.setCurrentIndex(0)
                w.stabilization_enabled.setChecked(True)
                w.stabilization_advanced_toggle.setChecked(True)
            elif state == 'collapsed':
                w.stabilization_advanced_toggle.setChecked(False)
            elif state == 'hidden-again':
                w.inspector_tabs.setCurrentIndex(1)
            for _ in range(12): app.processEvents()
            context = (locale, mode, state, w.inspector.widget().width(),
                       w.inspector.viewport().width(), w.inspector_tabs.widget(0).minimumSizeHint().width())
            assert w.inspector.horizontalScrollBar().maximum() == 0, context
            assert w.inspector.widget().width() <= w.inspector.viewport().width(), context
            timing_item = w.inspector_tabs.widget(0).layout().itemAt(0)
            timing = timing_item.widget()
            assert timing_item.minimumSize().width() == timing.minimumSizeHint().width(), context
            if state == 'expanded':
                for control in (w.stabilization_strength, w.stabilization_smoothing,
                                w.stabilization_max_zoom, w.stabilization_border,
                                w.apply_stabilization_button, w.reset_stabilization_button):
                    assert control.width() >= control.minimumSizeHint().width(), context
                    w.inspector.ensureWidgetVisible(control)
                    for _ in range(2): app.processEvents()
                    assert w.inspector.viewport().rect().contains(
                        control.mapTo(w.inspector.viewport(), control.rect().center())), context
        assert w.project == before and w.revision == revision
        settled = len(refresh_count)
        for _ in range(80): app.processEvents()
        assert not controller._pending and len(refresh_count) == settled
    finally:
        w.dirty = False; w.closing = True; w.close(); w.deleteLater(); app.processEvents()


def test_hidden_nested_section_discards_primed_wide_layout_cache(app):
    scroll = QScrollArea(); scroll.setWidgetResizable(True)
    content = QWidget(); layout = QVBoxLayout(content)
    tabs = QTabWidget(); layout.addWidget(tabs)
    first = QWidget(); QVBoxLayout(first).addWidget(QLabel('Visible tab')); tabs.addTab(first, 'First')
    hidden = QWidget(); hidden_box = QVBoxLayout(hidden)
    group = QGroupBox('Timing'); form = QFormLayout(group)
    field = QLineEdit('A native-width field'); field.setMinimumWidth(220)
    form.addRow('A long bilingual label / 语言', field)
    hidden_box.addWidget(group); tabs.addTab(hidden, 'Never opened')
    scroll.setWidget(content); scroll.setStyleSheet(STYLE)
    # Explicitly reproduce the native failure: cache the wide section before
    # the responsive formatter stacks the still-hidden form.
    content.minimumSizeHint(); hidden_box.itemAt(0).minimumSize()
    apply_ui_layout_tokens(scroll); scroll.resize(320, 260); scroll.show()
    try:
        for _ in range(15): app.processEvents()
        assert hidden.isHidden() and tabs.currentIndex() == 0
        assert scroll.horizontalScrollBar().maximum() == 0
        assert content.width() <= scroll.viewport().width()
        assert hidden_box.itemAt(0).minimumSize().width() == group.minimumSizeHint().width()
        tabs.setCurrentIndex(1)
        for _ in range(10): app.processEvents()
        assert scroll.horizontalScrollBar().maximum() == 0 and field.width() >= 220
    finally:
        scroll.close(); scroll.deleteLater(); app.processEvents()


def test_shared_form_host_minimum_signatures_converge(app):
    root = QWidget(); layout = QVBoxLayout(root)
    forms = []
    for text, minimum in [('Small', 80), ('Larger section', 160)]:
        form = QFormLayout(); field = QLineEdit(text); field.setMinimumWidth(minimum)
        form.addRow(text, field); layout.addLayout(form); forms.append(form)
    apply_ui_layout_tokens(root); root.resize(320, 260); root.show()
    try:
        assert forms[0].parentWidget() is forms[1].parentWidget() is root
        for _ in range(15): app.processEvents()
        controller = root._lumen_layout_controller
        refresh = controller.refresh; calls = []
        def counted_refresh():
            calls.append(1)
            return refresh()
        controller.refresh = counted_refresh
        controller.schedule()
        for _ in range(15): app.processEvents()
        settled = len(calls)
        for _ in range(80): app.processEvents()
        assert settled and len(calls) == settled and not controller._pending
    finally:
        root.close(); root.deleteLater(); app.processEvents()

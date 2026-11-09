"""Bilingual, state-preserving local restoration in the actual Qt inspector."""
from copy import deepcopy
from fractions import Fraction
import ast
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont, QFontDatabase, QFontInfo
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from lumen.i18n import set_locale, tr_error
from lumen.model import Clip, Editor, Media, Project
from lumen.restoration_settings import RestorationSettings
from lumen.ui import MainWindow


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
    w.dirty = False; w.closing = True; w.close(); w.deleteLater(); app.processEvents()


def stage(w):
    w.restoration_enabled.setChecked(True)
    w.restoration_deblock.setValue(.25)
    w.restoration_denoise.setValue(.4)
    w.restoration_sharpen.setValue(.15)


def test_defaults_ranges_and_panel_separation(window):
    w = window; before = deepcopy(w.project); revision = w.revision
    assert w.restoration_group.title() == 'Quality restoration'
    assert w.inspector_tabs.widget(0).isAncestorOf(w.restoration_group)
    assert not w.clip_effects_panel.isAncestorOf(w.restoration_group)
    assert not w.stickers_panel.isAncestorOf(w.restoration_group)
    assert not w.restoration_enabled.isChecked()
    for control in w.restoration_spins.values():
        assert control.value() == 0 and not control.isEnabled()
        assert (control.minimum(), control.maximum()) == (0, 1)
    assert not w.apply_restoration_button.isEnabled()
    assert not w.reset_restoration_button.isEnabled()
    assert 'No AI' in w.restoration_limits_note.text()
    assert 'does not analyze motion or guarantee flicker-free' in w.restoration_limits_note.text()
    assert w.project == before and w.revision == revision


def test_apply_is_one_explicit_edit_invalidates_preview_and_undoes(window):
    w = window; before = deepcopy(w.project); revision = w.revision
    w.preview_revision = revision; w.loaded_preview_mode = w.preview_mode
    stage(w)
    assert w.project == before and w.revision == revision
    assert w.apply_restoration_button.isEnabled()
    w.apply_restoration_button.click()
    assert w.selected_clip().restoration == RestorationSettings(True, .25, .4, .15)
    assert w.revision == revision + 1 and w.preview_revision != w.revision
    assert len(w.editor._undo) == 1 and not w.play_button.isEnabled()
    assert not w.apply_restoration_button.isEnabled()
    after = deepcopy(w.project)
    w.apply_restoration(); assert w.revision == revision + 1
    w.undo(); assert w.project == before
    w.redo(); assert w.project == after


def test_reset_is_undoable_and_default_reset_only_discards_draft(window):
    w = window; revision = w.revision
    stage(w); w.reset_restoration()
    assert w._restoration_draft == RestorationSettings()
    assert not w.restoration_enabled.isChecked() and w.restoration_denoise.value() == 0
    assert w.revision == revision and not w.editor.can_undo
    stage(w); w.apply_restoration(); before = deepcopy(w.project)
    w.reset_restoration_button.click()
    assert w.selected_clip().restoration == RestorationSettings()
    assert w.revision == revision + 2 and len(w.editor._undo) == 2
    w.undo(); assert w.project == before and w.restoration_denoise.value() == .4


def test_bypass_preserves_unedited_high_precision_strengths(window):
    w = window
    settings = RestorationSettings(True, .123456789, .234567891, .345678912)
    w.editor.update_clip('c', restoration=settings); w.changed()
    before = deepcopy(settings)
    w.restoration_enabled.setChecked(False); w.apply_restoration()
    before.enabled = False
    assert w.selected_clip().restoration == before
    assert not any(control.isEnabled() for control in w.restoration_spins.values())
    w.restoration_enabled.setChecked(True); w.apply_restoration()
    assert w.selected_clip().restoration == settings


def test_refresh_noop_and_selection_discards_staged_changes(window):
    w = window
    w.project.clips.append(Clip('next', 'm', 0, 80, 70, 20,
                               restoration=RestorationSettings(True, .7, .8, .9)))
    w.project.validate(); w.refresh()
    before = deepcopy(w.project); revision = w.revision
    for _ in range(3): w.refresh_inspector()
    assert w.project == before and w.revision == revision
    stage(w); w.select_clip('next')
    assert w.restoration_enabled.isChecked() and w.restoration_denoise.value() == .8
    assert not w.apply_restoration_button.isEnabled()
    w.select_clip('c'); assert not w.restoration_enabled.isChecked()
    assert w.project == before and w.revision == revision


def test_audio_and_empty_selection_disable_controls(window):
    w = window
    w.project.media.append(Media('a', '/tmp/audio.wav', 'Audio', 2, has_video=False, has_audio=True))
    w.project.clips.append(Clip('a', 'a', 2, 0, 0, 60)); w.project.validate(); w.refresh()
    w.select_clip('a'); assert not w.restoration_group.isEnabled()
    before = deepcopy(w.project); revision = w.revision
    w._restoration_draft.enabled = True; w.apply_restoration()
    assert w.project == before and w.revision == revision and w.test_warnings
    w.select_clip(''); assert not w.restoration_group.isEnabled()
    w.apply_restoration(); w.reset_restoration()
    assert w.project == before and w.revision == revision


def test_language_and_workspace_switches_preserve_drafts_and_committed_settings(window):
    w = window
    w.editor.update_restoration('c', enabled=True, deblock=.123456789, denoise=.4, sharpen=.5)
    w.changed(); before = deepcopy(w.project); revision = w.revision
    w.restoration_denoise.setValue(.72); draft = deepcopy(w._restoration_draft)
    for locale in ('zh-CN', 'en', 'zh-CN'):
        set_locale(locale)
        for mode in ('simple', 'professional', 'simple'):
            w.set_workspace_mode(mode)
            assert w._restoration_draft == draft
            assert w.restoration_denoise.value() == .72
            assert not w.restoration_group.isHidden()
            assert w.project == before and w.revision == revision
    assert w.restoration_group.title() == '画质修复'
    assert w.restoration_enabled.text() == '启用画质修复'
    assert w.apply_restoration_button.text() == '应用画质修复'
    assert w.reset_restoration_button.text() == '重置画质修复'
    assert '不保证消除视频闪烁' in w.restoration_limits_note.text()
    set_locale('en'); assert w.apply_restoration_button.text() == 'Apply restoration'
    w.apply_restoration()
    assert w.selected_clip().restoration == draft
    assert w.selected_clip().restoration.deblock == .123456789


def test_speed_trim_and_split_preserve_restoration_through_ui(window):
    w = window; stage(w); w.apply_restoration()
    settings = deepcopy(w.selected_clip().restoration)
    w.speed_edit.setText('1.5'); w.apply_speed()
    w.in_spin.setText('1/2'); w.duration_spin.setValue(20); w.apply_timing()
    assert w.selected_clip().speed == Fraction(3, 2)
    assert w.selected_clip().restoration == settings
    left, right = w.editor.split_clip('c', 17); w.changed()
    assert left.restoration == right.restoration == settings
    w.select_clip(right.id); w.restoration_denoise.setValue(.8); w.apply_restoration()
    assert left.restoration.denoise == .4 and w.selected_clip().restoration.denoise == .8


def test_validation_errors_are_localized():
    set_locale('zh-CN')
    assert '视频轨道' in str(tr_error('Restoration requires a picture clip on a video track'))
    assert '有限数值' in str(tr_error('Restoration denoise must be finite and in 0.0..1.0'))


def test_static_restoration_errors_have_chinese_translations():
    from lumen.i18n_catalog_errors import ZH_CN
    root = Path(__file__).resolve().parents[1] / 'lumen'
    for path in [root / 'restoration.py', *sorted(root.glob('restoration_*.py'))]:
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                    and node.exc.args and isinstance(node.exc.args[0], ast.Constant)
                    and isinstance(node.exc.args[0].value, str)):
                # Frozen smoke's assertion diagnostics are developer checks.
                if path.stem == 'restoration_smoke': continue
                assert node.exc.args[0].value in ZH_CN, (path.name, node.exc.args[0].value)


@pytest.fixture
def noto_font(app):
    configured = os.environ.get('LUMEN_TEST_CJK_FONT_DIR')
    font_id = None
    if configured:
        path = Path(configured) / 'NotoSansCJKsc-Regular.otf'
        assert path.is_file(), 'Configured native test font is missing'
        font_id = QFontDatabase.addApplicationFont(str(path))
        assert font_id >= 0
        assert 'Noto Sans CJK SC' in QFontDatabase.applicationFontFamilies(font_id)
    elif 'Noto Sans CJK SC' not in QFontDatabase.families():
        pytest.skip('Noto Sans CJK SC is not installed and no test font was configured')
    previous = QFont(app.font()); app.setFont(QFont('Noto Sans CJK SC', 10))
    assert QFontInfo(app.font()).family() == 'Noto Sans CJK SC'
    yield app.font()
    app.setFont(previous)
    if font_id is not None: QFontDatabase.removeApplicationFont(font_id)


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_actual_noto_font_restoration_is_reachable_without_horizontal_scroll_at_320px(
        app, tmp_path, noto_font, locale, mode):
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
        for state in ('never-opened', 'editing', 'bypassed', 'hidden-again'):
            if state == 'editing':
                w.inspector_tabs.setCurrentIndex(0); stage(w)
                w.stabilization_enabled.setChecked(True)
                w.stabilization_advanced_toggle.setChecked(True)
            elif state == 'bypassed':
                w.restoration_enabled.setChecked(False)
            elif state == 'hidden-again':
                w.inspector_tabs.setCurrentIndex(1)
            for _ in range(12): app.processEvents()
            context = (locale, mode, state, w.inspector.widget().width(),
                       w.inspector.viewport().width())
            assert w.inspector.horizontalScrollBar().maximum() == 0, context
            assert w.inspector.widget().width() <= w.inspector.viewport().width(), context
            if state in ('editing', 'bypassed'):
                for control in (*w.restoration_spins.values(), w.restoration_enabled,
                                w.apply_restoration_button, w.reset_restoration_button):
                    assert control.width() >= control.minimumSizeHint().width(), context
                    assert control.height() >= control.minimumSizeHint().height(), context
                    assert control.parentWidget().rect().contains(control.geometry()), context
                    w.inspector.ensureWidgetVisible(control)
                    for _ in range(2): app.processEvents()
                    assert w.inspector.viewport().rect().contains(
                        control.mapTo(w.inspector.viewport(), control.rect().center())), context
                for label in w.restoration_group.findChildren(QLabel):
                    if label.wordWrap():
                        assert label.height() >= label.heightForWidth(label.width()), context
        assert w.project == before and w.revision == revision
        controller = w._lumen_layout_controller
        for _ in range(80): app.processEvents()
        assert not controller._pending
    finally:
        w.dirty = False; w.closing = True; w.close(); w.deleteLater(); app.processEvents()


@pytest.mark.parametrize('mode,restoration_enabled,grade_enabled', [
    ('graded', True, True), ('bypass', False, False),
])
def test_preview_comparison_bypasses_restoration_only_in_snapshot(
        window, monkeypatch, mode, restoration_enabled, grade_enabled):
    import threading
    from lumen.model import Grade, ClipEffect
    from lumen.stabilization_settings import StabilizationSettings
    w = window
    w.editor.update_clip('c', restoration=RestorationSettings(True, .2, .3, .4),
                         stabilization=StabilizationSettings(True, .6, 19, 1.2, 'black'),
                         grade=Grade(enabled=True, input_encoding='srgb', linear_exposure=.7),
                         effects=[ClipEffect('fx', 'vignette', .2)])
    w.changed(); before = deepcopy(w.project); revision = w.revision
    undo = deepcopy(w.editor._undo); captured = []
    def render(engine, snapshot, target, **kwargs):
        captured.append(deepcopy(snapshot))
        return target
    monkeypatch.setattr('lumen.ui.RenderEngine.render', render)
    def execute(_label, operation, _success, _slot):
        operation(threading.Event(), lambda *_: None)
    monkeypatch.setattr(w, 'start_job', execute)
    w.preview_mode = mode; w.build_preview()
    assert len(captured) == 1
    c = captured[0].clips[0]; original = before.clips[0]
    assert c.restoration.enabled is restoration_enabled and c.grade.enabled is grade_enabled
    assert (c.restoration.deblock, c.restoration.denoise, c.restoration.sharpen) == (.2, .3, .4)
    assert c.grade.input_encoding == 'srgb' and c.grade.linear_exposure == .7
    assert c.stabilization == original.stabilization and c.effects == original.effects
    assert w.project == before and w.revision == revision and w.editor._undo == undo
    set_locale('zh-CN')
    assert '关闭修复与调色' in w.preview_mode_choice.itemText(1)
    assert '画面稳定' in w.preview_mode_choice.toolTip()

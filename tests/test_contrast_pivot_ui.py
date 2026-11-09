"""Native basic contrast anchor, immutable source picking, and separate-process DPI QA."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from lumen.color_picker_source import capture_picker_snapshot
from lumen.i18n import get_locale, set_locale, tr_error
from lumen.i18n_catalog_contrast_pivot import ERROR_ZH_CN
from lumen.model import Clip, Editor, Grade, Media, Project
from lumen.ui import MainWindow
from lumen.ui_color_picker import ColorPickerDialog
from lumen.ui_qualifier import QualifierDialog
from lumen.ui_workspace import BASIC_GRADE_FIELDS


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def make_window(directory, locale='en'):
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / 'synthetic-pivot.ppm'
    source.write_bytes(b'P6\n64 32\n255\n' + bytes((154, 91, 51)) * (64 * 32))
    settings = QSettings(str(directory / 'settings.ini'), QSettings.Format.IniFormat)
    window = MainWindow(workspace_settings=settings, recovery_root=directory / 'recovery',
                        ui_language=locale)
    media = Media('media', str(source), 'Synthetic source · 合成素材', 2, 64, 32,
                  has_video=True, has_audio=False)
    project = Project(width=64, height=32, media=[media], clips=[
        Clip('source', media.id, 0, 0, 0, 1),
        Clip('target', media.id, 0, 2, 0, 1, grade=Grade(input_encoding='srgb')),
    ])
    window.editor = Editor(project); window.refresh(); window.select_clip('source')
    window.inspector_tabs.setCurrentIndex(1); window.color_tabs.setCurrentIndex(0)
    window.resize(1280, 800); window.show()
    return window


def close_window(window, app):
    for kind in (ColorPickerDialog, QualifierDialog):
        for dialog in window.findChildren(kind): dialog.reject()
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


def type_pivot(window, text):
    spin = window.grade_spins['contrast_pivot']
    spin.setFocus(); spin.selectAll(); QTest.keyClicks(spin, text)
    return spin


@pytest.mark.parametrize('commit_key', [Qt.Key.Key_Return, Qt.Key.Key_Tab])
def test_keyboard_edit_is_staged_then_one_commit_and_noop_repeat(window, app, commit_key):
    w = window; before = deepcopy(w.project); revision = w.revision
    spin = type_pivot(w, '0.327')
    assert spin.decimals() == 3 and not spin.keyboardTracking()
    assert w.project == before and not w.editor.can_undo
    QTest.keyClick(spin, commit_key); app.processEvents()
    expected = deepcopy(before); expected.clips[0].grade.contrast_pivot = .327
    assert w.project == expected
    assert w.revision == revision + 1 and len(w.editor._undo) == 1
    spin.setFocus(); QTest.keyClick(spin, Qt.Key.Key_Return)
    assert w.revision == revision + 1 and len(w.editor._undo) == 1
    w.undo(); assert w.project == before and spin.value() == .5
    w.redo(); assert w.project == expected and spin.value() == .327


def test_control_is_basic_adjacent_and_bounded_with_exact_default(window):
    w = window; spin = w.grade_spins['contrast_pivot']; form = spin.parentWidget().layout()
    assert 'contrast_pivot' in BASIC_GRADE_FIELDS
    assert form.getWidgetPosition(spin)[0] == form.getWidgetPosition(w.grade_spins['contrast'])[0] + 1
    assert spin.value() == .5 and (spin.minimum(), spin.maximum()) == (0., 1.)
    assert spin.singleStep() == .01
    assert form.labelForField(spin).text() == 'Contrast pivot'
    assert w.contrast_pivot_note.wordWrap()
    assert 'not linear light' in w.contrast_pivot_note.text()
    assert 'legacy code gain' in spin.toolTip() and 'Contrast 1' in spin.toolTip()
    spin.setValue(-1); assert w.selected_clip().grade.contrast_pivot == 0
    spin.stepDown(); assert w.selected_clip().grade.contrast_pivot == 0
    spin.setValue(2); assert w.selected_clip().grade.contrast_pivot == 1
    spin.stepUp(); assert w.selected_clip().grade.contrast_pivot == 1


def test_refresh_unrelated_edit_language_and_workspace_preserve_exact_pivot(window, app):
    w = window
    w.selected_clip().grade = Grade(contrast_pivot=.345678912, exposure=.123456789,
                                    red_curve=[[0., 0.], [.3456789, .4567891], [1., 1.]])
    before = deepcopy(w.project); revision = w.revision
    for language in ('zh-CN', 'en', 'zh-CN', 'en'):
        set_locale(language)
        for mode in ('professional', 'simple'):
            w.set_workspace_mode(mode); w.refresh_inspector(); app.processEvents()
            assert w.grade_spins['contrast_pivot'].value() == .346
            assert w.project == before and w.revision == revision and not w.editor.can_undo
    w.grade_spins['saturation'].setValue(1.25)
    expected = deepcopy(before); expected.clips[0].grade.saturation = 1.25
    assert w.project == expected
    w.grade_enabled.setChecked(False)
    expected.clips[0].grade.enabled = False
    assert w.project == expected and w.selected_clip().grade.contrast_pivot == .345678912


@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_reset_pivot_one_undo_preserves_input_and_simple_advanced_fields(window, mode):
    w = window; w.set_workspace_mode(mode)
    original = Grade(contrast_pivot=.345678912, contrast=1.31, input_encoding='srgb',
                     exposure=.123456789, linear_exposure=.25, enabled=False,
                     red_curve=[[0., 0.], [.3456789, .4567891], [1., 1.]])
    w.selected_clip().grade = deepcopy(original); w.refresh_inspector()
    before = deepcopy(w.project); revision = w.revision
    w.reset_color_button.click()
    expected = deepcopy(original) if mode == 'simple' else Grade(input_encoding='srgb')
    if mode == 'simple':
        for key in BASIC_GRADE_FIELDS: setattr(expected, key, getattr(Grade(), key))
    assert w.selected_clip().grade == expected
    assert w.grade_spins['contrast_pivot'].value() == .5
    assert w.revision == revision + 1 and len(w.editor._undo) == 1
    w.reset_color_button.click(); assert w.revision == revision + 1
    w.undo(); assert w.project == before and w.grade_spins['contrast_pivot'].value() == .346
    w.redo(); assert w.selected_clip().grade == expected


def test_copy_paste_preserves_full_pivot_precision_input_and_one_undo(window):
    w = window; w.selected_clip().grade.contrast_pivot = .123456789
    w.selected_clip().grade.contrast = 1.25; w.refresh_inspector()
    original = deepcopy(w.project); revision = w.revision
    w.copy_grade_button.click()
    assert w.project == original and w.revision == revision and not w.editor.can_undo
    w.select_clip('target'); w.paste_grade_button.click()
    expected = deepcopy(original)
    expected.clips[1].grade = deepcopy(original.clips[0].grade)
    expected.clips[1].grade.input_encoding = 'srgb'
    assert w.project == expected and w.grade_spins['contrast_pivot'].value() == .123
    assert len(w.editor._undo) == 1 and w.revision == revision + 1
    w.paste_selected_grade(); assert len(w.editor._undo) == 1
    w.undo(); assert w.project == original
    w.redo(); assert w.project == expected


def loaded_dialog(window, kind):
    if kind == 'hue':
        window.set_workspace_mode('professional'); window.curve_choice.setCurrentIndex(4)
        dialog = ColorPickerDialog(window)
    else:
        dialog = QualifierDialog(window)
    dialog.show()
    snapshot = capture_picker_snapshot(window.project, window.selection, window.timeline.playhead, stage=kind)
    dialog.accept_snapshot(snapshot)
    assert dialog.snapshot is not None and dialog.current()
    if kind == 'hue':
        dialog.sample_pixel(16, 16); assert dialog.apply_button.isEnabled()
    else:
        dialog.enabled_check.setChecked(True); dialog.controls['exposure'].setValue(.25)
    return dialog, snapshot


@pytest.mark.parametrize('kind', ['hue', 'qualifier'])
@pytest.mark.parametrize('change', ['control_commit', 'model_change'])
def test_pivot_invalidates_frozen_picker_and_never_commits_staged_settings(window, kind, change):
    w = window; w.selected_clip().grade.contrast = 1.25; w.refresh_inspector()
    dialog, old = loaded_dialog(w, kind)
    if change == 'control_commit':
        w.grade_spins['contrast_pivot'].setValue(.35)
    else:
        # Signature guard must work even when no project revision was bumped.
        w.selected_clip().grade.contrast_pivot = .35
    expected = deepcopy(w.project); undo = len(w.editor._undo); revision = w.revision
    assert not old.is_current(w.project)
    if kind == 'hue': dialog.apply_sample()
    else: dialog.apply_settings()
    assert w.project == expected and len(w.editor._undo) == undo and w.revision == revision
    assert not dialog.apply_button.isEnabled() and not dialog.canvas.isEnabled()
    assert 'stale' in dialog.status.text().lower()
    dialog.reject()
    reopened, new = loaded_dialog(w, kind)
    # Independent closed-form difference for this identity-curves fixture.
    np.testing.assert_allclose(new.frame, old.frame + (.35 - .5) * (1 - 1.25), atol=2e-7, rtol=0)
    assert not old.frame.flags.writeable and not new.frame.flags.writeable
    reopened.reject()
    assert w.project == expected and len(w.editor._undo) == undo


@pytest.mark.parametrize('kind', ['hue', 'qualifier'])
def test_uncommitted_pivot_keeps_frozen_source_until_keyboard_commit(window, app, kind):
    w = window
    dialog, snapshot = loaded_dialog(w, kind)
    before = deepcopy(w.project)
    spin = type_pivot(w, '0.327')
    assert w.project == before and not w.editor.can_undo
    assert snapshot.is_current(w.project) and dialog.current()
    QTest.keyClick(spin, Qt.Key.Key_Return); app.processEvents()
    assert w.selected_clip().grade.contrast_pivot == .327 and len(w.editor._undo) == 1
    dialog.check_current()
    assert not dialog.current() and not dialog.apply_button.isEnabled()
    assert not dialog.canvas.isEnabled()


def test_all_pivot_copy_and_schema_errors_localize_live_without_project_edits(window):
    w = window; spin = w.grade_spins['contrast_pivot']; before = deepcopy(w.project)
    for language, label, wording in [('zh-CN', '对比度中心', '而非线性光'),
                                     ('en', 'Contrast pivot', 'not linear light')]:
        set_locale(language)
        assert spin.parentWidget().layout().labelForField(spin).text() == label
        assert wording in w.contrast_pivot_note.text()
        assert ('中心' if language == 'zh-CN' else 'pivot') in spin.toolTip()
        assert ('围绕中心' if language == 'zh-CN' else 'around pivot') in w.stack_label.text()
        assert ('对比度中心' if language == 'zh-CN' else 'contrast pivot') in w.reset_color_button.toolTip()
        for source, translation in ERROR_ZH_CN.items():
            assert tr_error(source) == (translation if language == 'zh-CN' else source)
    assert w.project == before and not w.editor.can_undo


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('scale', [1., 1.25, 1.5, 2.])
def test_narrow_basic_pivot_layout_at_real_dpi(tmp_path, locale, scale):
    output = tmp_path / f'{locale}-{scale:g}'
    requested = os.environ.get('CONTRAST_PIVOT_SCREENSHOTS')
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
    assert len(report['records']) == 2
    for record in report['records']:
        assert record['inspector_width'] <= 322
        assert record['dpr'] == pytest.approx(scale)
        assert record['horizontal_overflow'] == 0
        assert record['pivot_fully_visible'] and record['note_fully_visible']
        assert record['note_text_height'] <= record['note_height']
        assert record['label_fully_visible'] and record['numeric_text_fits']
        assert (output / record['screenshot']).is_file()


def capture_layout(output, locale, scale):
    """Original Qt captures only, never redrawn or annotated UI evidence."""
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QFont, QFontDatabase
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    from scripts.qt_layout_font import prepare_layout_font
    font_evidence = prepare_layout_font(app)
    (output / 'font.json').write_text(json.dumps(font_evidence, indent=2), encoding='utf-8')
    w = make_window(output / '_runtime', locale)
    w.resize(1280, 900); w.side_tabs.setFixedWidth(320)
    w.selected_clip().grade.contrast = 1.25; w.selected_clip().grade.contrast_pivot = .327
    w.refresh_inspector()
    records = []
    try:
        for mode in ('simple', 'professional'):
            w.set_workspace_mode(mode); w.color_tabs.setCurrentIndex(0)
            for _ in range(8): app.processEvents(); QTest.qWait(20)
            spin = w.grade_spins['contrast_pivot']; note = w.contrast_pivot_note
            label = spin.parentWidget().layout().labelForField(spin)
            # Start the visible content at Basic, not halfway through the
            # unrelated grade-transfer explanation above the color tabs.
            basic_top = w.color_tabs.mapTo(w.inspector.widget(), QPoint(0, 0)).y()
            w.inspector.verticalScrollBar().setValue(basic_top)
            for _ in range(4): app.processEvents(); QTest.qWait(20)
            bounds = note.fontMetrics().boundingRect(QRect(0, 0, note.contentsRect().width(), 10000),
                Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs, note.text())
            name = f'basic-pivot-{locale}-{mode}-{scale:g}.png'
            assert w.grab().save(str(output / name))
            assert w.side_tabs.grab().save(str(output / f'inspector-{name}'))
            records.append(dict(mode=mode, inspector_width=w.side_tabs.width(),
                dpr=w.devicePixelRatioF(), horizontal_overflow=w.inspector.horizontalScrollBar().maximum(),
                pivot_fully_visible=spin.visibleRegion().boundingRect() == spin.rect(),
                label_fully_visible=label.visibleRegion().boundingRect() == label.rect(),
                note_fully_visible=note.visibleRegion().boundingRect() == note.rect(),
                note_height=note.contentsRect().height(), note_text_height=bounds.height(),
                numeric_text_fits=spin.lineEdit().fontMetrics().horizontalAdvance(spin.text()) < spin.lineEdit().width(),
                screenshot=name))
        assert not w.editor.can_undo
        (output / 'geometry.json').write_text(json.dumps(dict(locale=locale, scale=scale,
            platform=f'Qt offscreen on {sys.platform}; not desktop display validation', records=records), indent=2), encoding='utf-8')
    finally:
        close_window(w, app)


if __name__ == '__main__':
    assert sys.argv[1] == '--layout'
    capture_layout(Path(sys.argv[2]), sys.argv[3], float(sys.argv[4]))

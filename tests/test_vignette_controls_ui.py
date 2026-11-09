"""Staged, bilingual vignette controls; offscreen checks are not native QA."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lumen.i18n import set_locale, tr_error
from lumen.i18n_catalog_vignette import ERROR_ZH_CN
from lumen.model import Editor, Project
from lumen.ui import MainWindow
from lumen.ui_color_export import ExportProfileDialog
from test_ui import app, window, populate
from test_effects_ui import add_effect


def test_shape_stages_until_single_apply_then_undo_redo_and_noop(window, tmp_path):
    w = window; populate(w, tmp_path); add_effect(w, 'vignette')
    before = deepcopy(w.project); revision = w.revision; steps = len(w.editor._undo)
    w.effect_amount.setValue(.7); w.effect_radius.setValue(.65)
    w.effect_feather.setValue(1.8); w.effect_enabled.setChecked(False)
    assert w.project == before and w.revision == revision
    w.effect_apply_button.click()
    after = deepcopy(w.project); effect = w.selected_effect()
    assert (effect.amount, effect.radius, effect.feather, effect.enabled) == (.7, .65, 1.8, False)
    assert w.revision == revision + 1 and len(w.editor._undo) == steps + 1
    w.effect_apply_button.click(); assert w.revision == revision + 1
    w.undo(); assert w.project == before
    assert (w.effect_radius.value(), w.effect_feather.value()) == (1., 1.)
    w.redo(); assert w.project == after
    assert (w.effect_radius.value(), w.effect_feather.value()) == (.65, 1.8)


def test_shape_ranges_defaults_and_blur_visibility_do_not_leak(window, tmp_path):
    w = window; populate(w, tmp_path); blur = add_effect(w)
    for spin in (w.effect_radius, w.effect_feather):
        assert spin.isHidden() and (spin.minimum(), spin.maximum()) == (.25, 2.)
        assert spin.value() == 1. and spin.decimals() == 3 and not spin.keyboardTracking()
    vignette = add_effect(w, 'vignette')
    assert not w.effect_radius.isHidden() and not w.effect_feather_label.isHidden()
    w.effect_radius.setValue(.25); w.effect_feather.setValue(2.)
    w.select_clip_effect(blur.id)
    assert w.effect_radius.isHidden() and w.effect_feather_label.isHidden()
    w.effect_radius.setValue(.5); w.effect_feather.setValue(.5)  # Hidden controls cannot affect blur.
    w.effect_amount.setValue(4.); w.apply_clip_effect()
    assert (w.selected_effect().radius, w.selected_effect().feather) == (1., 1.)
    w.select_clip_effect(vignette.id)
    assert (w.effect_radius.value(), w.effect_feather.value()) == (1., 1.)
    assert not w.test_warnings


def test_untouched_imported_precision_survives_apply_to_other_field(window, tmp_path):
    w = window; populate(w, tmp_path); add_effect(w, 'vignette')
    effect = w.selected_effect(); effect.amount = .412345678901
    effect.radius = 1.123456789012345; effect.feather = .987654321098765
    precise = (effect.amount, effect.radius, effect.feather)
    w.refresh_clip_effects(); before = deepcopy(w.project); revision = w.revision
    w.effect_apply_button.click(); assert w.project == before and w.revision == revision
    w.effect_enabled.setChecked(False); w.effect_apply_button.click()
    assert (w.selected_effect().amount, w.selected_effect().radius, w.selected_effect().feather) == precise
    w.effect_radius.setValue(.75); w.effect_apply_button.click()
    assert w.selected_effect().radius == .75 and w.selected_effect().feather == precise[2]


def test_keyboard_enter_commits_spin_only_not_project(window, app, tmp_path):
    w = window; populate(w, tmp_path); add_effect(w, 'vignette'); w.show(); app.processEvents()
    before = deepcopy(w.project)
    spin = w.effect_radius; spin.setFocus(); spin.selectAll(); QTest.keyClicks(spin, '0.775')
    assert w.project == before
    QTest.keyClick(spin, Qt.Key.Key_Return); app.processEvents()
    assert spin.value() == .775 and w.project == before
    w.effect_apply_button.click(); assert w.selected_effect().radius == .775


def test_explicitly_retyping_one_resets_rounded_import_to_exact_legacy_shape(window, app, tmp_path):
    w = window; populate(w, tmp_path); add_effect(w, 'vignette')
    w.selected_effect().radius = 1.0000000001; w.refresh_clip_effects()
    assert w.effect_radius.value() == 1.
    revision = w.revision; w.effect_apply_button.click()
    assert w.revision == revision and w.selected_effect().radius == 1.0000000001
    w.show(); app.processEvents(); spin = w.effect_radius
    spin.setFocus(); spin.selectAll(); QTest.keyClicks(spin, '1.000')
    QTest.keyClick(spin, Qt.Key.Key_Return)
    assert w.selected_effect().radius == 1.0000000001
    w.effect_apply_button.click()
    assert w.revision == revision + 1 and w.selected_effect().radius == 1.


def test_language_switch_preserves_staged_shape_and_explains_math_cost(window, app, tmp_path):
    w = window; populate(w, tmp_path); add_effect(w, 'vignette')
    before = deepcopy(w.project); revision = w.revision
    w.effect_radius.setValue(.6); w.effect_feather.setValue(1.4)
    for language in ('zh-CN', 'en', 'zh-CN', 'en'):
        set_locale(language); app.processEvents()
        assert (w.effect_radius.value(), w.effect_feather.value()) == (.6, 1.4)
        assert w.effect_radius_label.text() == ('暗角半径' if language == 'zh-CN' else 'Vignette radius')
        assert w.effect_feather_label.text() == ('暗角羽化' if language == 'zh-CN' else 'Vignette feather')
        assert ('向内' if language == 'zh-CN' else 'inward') in w.effect_feather.toolTip()
        assert 'CPU' in w.effect_vignette_note.toolTip() and 'DaVinci Resolve' in w.effect_vignette_note.toolTip()
        assert ('可能改变颜色' if language == 'zh-CN' else 'can change color') in w.effect_vignette_note.toolTip()
        assert ('实时' if language == 'zh-CN' else 'live preview') in w.effect_vignette_note.toolTip()
        for source, translation in ERROR_ZH_CN.items():
            assert tr_error(source) == (translation if language == 'zh-CN' else source)
        assert w.project == before and w.revision == revision


def test_deleted_or_replaced_selection_clears_shape_controls(window, tmp_path):
    w = window; populate(w, tmp_path); add_effect(w, 'vignette')
    w.effect_radius.setValue(.5); w.delete_clip_effect()
    assert not w.effect_editor.isEnabled() and w.effect_radius.isHidden()
    assert w.selected_clip() is None and w.selected_clip_ids == ()
    w.apply_clip_effect(); assert not w.effect_clip().effects
    w.undo(); assert w.effect_clip().effects[0].radius == 1.
    w.editor = Editor(Project()); w.selection = ''; w.refresh()
    assert w.effect_radius.isHidden() and not w.effects_controls.isEnabled()
    w.apply_clip_effect(); assert not w.project.clips


@pytest.mark.parametrize('language', ['en', 'zh-CN'])
def test_export_explains_default_rgb8_rgb16_and_cpu_without_edit(window, tmp_path, language):
    w = window; populate(w, tmp_path); add_effect(w, 'vignette')
    before = deepcopy(w.project); set_locale(language)
    dialog = ExportProfileDialog(w.project)
    try:
        for profile in (0, 1, 0):
            dialog.profile_choice.setCurrentIndex(profile)
            assert not dialog.vignette_shape_note.isHidden()
            text = dialog.vignette_shape_note.text()
            assert all(term in text for term in ('0.25–2', 'RGB8', 'RGB16', 'CPU'))
        assert w.project == before and dialog.selected_profile is None
    finally: dialog.reject()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('scale', [1., 1.25, 1.5, 2.])
def test_narrow_effect_layout_at_real_dpi(tmp_path, locale, scale):
    output = Path(os.environ.get('VIGNETTE_SCREENSHOTS', tmp_path)) / f'{locale}-{scale:g}'
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--layout',
                             str(output), locale, str(scale)], cwd=ROOT,
                            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR=str(scale)),
                            text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads((output / 'geometry.json').read_text())
    assert data['dpr'] == pytest.approx(scale) and data['inspector_width'] == 320
    assert data['horizontal_overflow'] == 0
    for item in data['controls']:
        assert item['fully_visible'] and item['numeric_text_fits'] and item['label_fully_visible']
    assert (output / data['screenshot']).is_file()


def capture_layout(output, locale, scale):
    """Original Qt captures only, never redrawn or annotated UI evidence."""
    from PySide6.QtGui import QFont, QFontDatabase
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    font_directory = os.environ.get('LUMEN_TEST_CJK_FONT_DIR')
    if font_directory:
        # A separate process cannot inherit the parent pytest font registration.
        from scripts.provision_test_font import verify_fixture
        fixture = verify_fixture(font_directory)
        assert QFontDatabase.addApplicationFont(fixture['font_file']) >= 0
    families = QFontDatabase.families()
    cjk = next((family for family in ('Noto Sans CJK SC', 'Noto Serif CJK SC', 'WenQuanYi Zen Hei') if family in families), None)
    assert cjk, 'Bilingual layout evidence requires an installed CJK font'
    font = QFont(app.font()); font.setFamilies([font.family(), cjk]); app.setFont(font)
    settings = QSettings(str(output / 'workspace.ini'), QSettings.Format.IniFormat)
    settings.setValue('mode', 'professional')
    w = MainWindow(workspace_settings=settings, recovery_root=output / 'recovery', ui_language=locale)
    populate(w, output); add_effect(w, 'vignette')
    w.effect_radius.setValue(.65); w.effect_feather.setValue(1.8)
    w.resize(1280, 1000); w.side_tabs.setFixedWidth(320); w.show()
    w.vertical_split.setSizes([760, 160])
    before = deepcopy(w.project)
    try:
        for _ in range(6): app.processEvents(); QTest.qWait(20)
        w.clip_effects_panel.ensureWidgetVisible(w.effect_editor, 0, 0)
        for _ in range(4): app.processEvents(); QTest.qWait(20)
        controls = []
        for spin, label in ((w.effect_radius, w.effect_radius_label), (w.effect_feather, w.effect_feather_label)):
            controls.append(dict(fully_visible=spin.visibleRegion().boundingRect() == spin.rect(),
                label_fully_visible=label.visibleRegion().boundingRect() == label.rect(),
                numeric_text_fits=spin.lineEdit().fontMetrics().horizontalAdvance(spin.text()) < spin.lineEdit().width()))
        name = f'vignette-{locale}-{scale:g}.png'
        assert w.side_tabs.grab().save(str(output / name))
        assert w.grab().save(str(output / f'window-{name}'))
        (output / 'geometry.json').write_text(json.dumps(dict(locale=locale, scale=scale,
            platform='Qt offscreen Linux, not native Windows/macOS',
            dpr=w.devicePixelRatioF(), inspector_width=w.side_tabs.width(),
            horizontal_overflow=w.clip_effects_panel.horizontalScrollBar().maximum(),
            controls=controls, screenshot=name), indent=2))
        assert w.project == before
    finally:
        w.cancel_jobs()
        for job in list(w.jobs): assert job.wait(5000)
        w.dirty = False; w.closing = True; w.close(); w.deleteLater(); app.processEvents()


if __name__ == '__main__':
    assert sys.argv[1] == '--layout'
    capture_layout(Path(sys.argv[2]), sys.argv[3], float(sys.argv[4]))

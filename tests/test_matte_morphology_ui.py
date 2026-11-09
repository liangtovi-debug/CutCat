"""Real Qt signed matte controls and full-source, two-stage-neighborhood previews."""
from copy import deepcopy
from dataclasses import replace
import ast
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt, QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QGroupBox

from lumen.i18n import set_locale, tr, tr_error
from lumen.model import Project
from lumen.qualifier import QualifierSettings
from test_qualifier_ui import app, loaded, window
from test_qualifier_detail_ui import zoom
from test_qualifier_picker_ui import click_sample, assert_sample_ranges
from test_matte_refinement_ui import focus
from test_matte_smoothing_ui import install_pattern, choose


# The production stages round in float32 and preserve blend endpoints; the
# independent oracle intentionally sums in float64. Two float32 epsilons allow
# the ordered morphology/smoothing/window chain's rounding, not spatial error.
SPATIAL_ATOL = 2 * np.finfo(np.float32).eps


def independent_smooth(mask, amount):
    mask = np.asarray(mask, dtype=np.float64)
    padded = np.pad(mask, 1, mode='edge')
    result = np.zeros_like(mask)
    for dy, wy in enumerate((1, 2, 1)):
        for dx, wx in enumerate((1, 2, 1)):
            result += padded[dy:dy + mask.shape[0], dx:dx + mask.shape[1]] * (wx * wy / 16)
    return mask * (1 - amount) + result * amount


def independent_morphology(mask, amount):
    mask = np.asarray(mask, dtype=np.float64)
    if amount == 0:
        return mask.copy()
    h, w = mask.shape
    padded = np.pad(mask, 1, mode='edge')
    neighbors = np.stack([padded[y:y + h, x:x + w] for y in range(3) for x in range(3)])
    extreme = neighbors.max(axis=0) if amount > 0 else neighbors.min(axis=0)
    return mask * (1 - abs(amount)) + extreme * abs(amount)


@pytest.mark.parametrize('amount,smoothing', [(-.65, 0), (.65, 0), (-.65, .7), (.65, .7)])
@pytest.mark.parametrize('level,center', [(0, (500, 67)), (8, (500, 67)), (2, (0, 0)), (4, (1030, 132))])
@pytest.mark.parametrize('mode', ['matte', 'overlay', 'adjusted'])
def test_morphology_all_views_read_true_source_neighbors_and_combined_halos(window, app, amount, smoothing, level, center, mode):
    dialog = loaded(window); source, raw = install_pattern(dialog)
    before = hashlib.sha256(source.tobytes()).hexdigest()
    dialog.settings = QualifierSettings(enabled=True, hue_width=.01, hue_softness=0,
        matte_black=.2, matte_white=.8, matte_morphology=amount,
        matte_smoothing=smoothing, exposure=.5)
    expected = independent_smooth(independent_morphology(raw, amount), smoothing)
    zoom(dialog, level)
    if level:
        dialog.set_detail_center(*center)
    choose(dialog, mode)
    sample = dialog.viewport.extract(source); matte = dialog.viewport.extract(expected)
    np.testing.assert_allclose(dialog.matte, matte, atol=SPATIAL_ATOL, rtol=0)
    if mode == 'matte':
        pixels = np.repeat(matte[..., None], 3, axis=-1)
    elif mode == 'overlay':
        pixels = sample * (1 - .5 * matte[..., None]) + np.array([1, 0, 1]) * (.5 * matte[..., None])
    else:
        pixels = sample * (1 + (2 ** .5 - 1) * matte[..., None])
    np.testing.assert_allclose(dialog.canvas._pixels.astype(int),
        np.rint(np.clip(pixels, 0, 1) * 255).astype(int), atol=1, rtol=0)
    assert source is dialog.snapshot.frame and not source.flags.writeable
    assert hashlib.sha256(source.tobytes()).hexdigest() == before
    assert not window.editor.can_undo


@pytest.mark.parametrize('amount', [-1, -.67891, 0, .67891, 1])
def test_signed_keyboard_control_cancel_reset_apply_reopen_and_undo(window, app, amount):
    before = deepcopy(window.project)
    dialog = loaded(window); spin = dialog.controls['matte_morphology']
    assert (spin.minimum(), spin.maximum()) == (-100, 100)
    focus(spin); spin.selectAll(); QTest.keyClicks(spin, f'{amount * 100:+.3f}')
    QTest.keyClick(spin, Qt.Key.Key_Return)
    assert dialog.isVisible() and not window.editor.can_undo
    assert dialog.settings.matte_morphology == pytest.approx(amount)
    assert spin.text().startswith('+' if amount > 0 else '-' if amount < 0 else '0')
    dialog.reject(); assert window.project == before and not window.editor.can_undo
    assert dialog.snapshot is None and dialog.matte is None
    dialog = loaded(window)
    dialog.controls['matte_black'].setValue(20); dialog.controls['matte_white'].setValue(80)
    dialog.controls['matte_morphology'].setValue(amount * 100)
    dialog.controls['matte_smoothing'].setValue(70); dialog.controls['exposure'].setValue(.3)
    focus(dialog.reset_matte_button); QTest.keyClick(dialog.reset_matte_button, Qt.Key.Key_Space)
    assert (dialog.settings.matte_black, dialog.settings.matte_white,
            dialog.settings.matte_morphology, dialog.settings.matte_smoothing) == (0, 1, 0, 0)
    assert dialog.controls['matte_morphology'].value() == 0
    assert dialog.controls['matte_smoothing'].value() == 0 and dialog.settings.exposure == .3
    dialog.controls['exposure'].setValue(0); dialog.apply_settings()
    assert window.project == before and not window.editor.can_undo
    dialog = loaded(window); dialog.controls['matte_morphology'].setValue(amount * 100)
    expected = deepcopy(dialog.settings); dialog.apply_settings(); after = deepcopy(window.project)
    assert window.selected_clip().grade.qualifier == expected
    assert len(window.editor._undo) == (amount != 0)
    dialog = loaded(window); assert dialog.controls['matte_morphology'].value() == pytest.approx(amount * 100)
    dialog.apply_settings(); assert len(window.editor._undo) == (amount != 0)
    if amount:
        window.undo(); assert window.project == before
        window.redo(); assert window.project == after


def test_morphology_unrelated_edit_retains_exact_float_and_copy_paste_save(window, tmp_path):
    original = QualifierSettings(matte_black=.123456789, matte_white=.876543219,
        matte_morphology=-.678912345678, matte_smoothing=.321987654321,
        hue_center=.234567891)
    window.selected_clip().grade.qualifier = deepcopy(original)
    dialog = loaded(window); dialog.controls['exposure'].setValue(.3)
    assert dialog.settings == replace(original, exposure=.3)
    dialog.apply_settings(); expected = deepcopy(window.selected_clip().grade.qualifier)
    window.copy_selected_grade(); window.select_clip('other')
    before = deepcopy(window.project); undo_count = len(window.editor._undo)
    window.paste_selected_grade(); after = deepcopy(window.project)
    assert window.selected_clip().grade.qualifier == expected
    assert len(window.editor._undo) == undo_count + 1
    window.paste_selected_grade(); assert len(window.editor._undo) == undo_count + 1
    path = tmp_path / 'morphology-ui.lumen.json'; window.project.save(path)
    assert Project.load(path) == after
    window.undo(); assert window.project == before
    window.redo(); assert window.project == after
    window.project.clips[0].grade.qualifier.matte_morphology = 0
    assert window.selected_clip().grade.qualifier.matte_morphology == original.matte_morphology


@pytest.mark.parametrize('change', ['revision', 'frame', 'grade', 'dimensions'])
def test_morphology_stale_cannot_edit_reset_or_apply(window, change):
    dialog = loaded(window); dialog.controls['matte_morphology'].setValue(-75)
    if change == 'revision': window.revision += 1
    elif change == 'frame': window.timeline.playhead += 1
    elif change == 'grade': window.selected_clip().grade.exposure = .1
    else: window.project.width += 2
    before = deepcopy(window.project); pending = deepcopy(dialog.settings)
    dialog.change_setting('matte_morphology', .5); dialog.reset_matte_levels(); dialog.apply_settings()
    assert window.project == before and dialog.settings == pending
    assert not dialog.controls['matte_morphology'].isEnabled() and not dialog.apply_button.isEnabled()
    assert not window.editor.can_undo


@pytest.mark.parametrize('mode', ['replace', 'add', 'subtract'])
def test_morphology_overlay_pick_uses_source_and_preserves_all_refinement(window, app, mode):
    dialog = loaded(window); source, _ = install_pattern(dialog)
    dialog.controls['matte_morphology'].setValue(-60); dialog.controls['matte_smoothing'].setValue(70)
    dialog.controls['matte_black'].setValue(20); dialog.controls['matte_white'].setValue(80)
    zoom(dialog, 8); dialog.set_detail_center(500, 67); choose(dialog, 'overlay')
    point = (32, 32); sx, sy = dialog.viewport.source_at(*point)
    before = source.copy()
    dialog.sample_mode.setCurrentIndex(dialog.sample_mode.findData(mode))
    dialog.pick_button.click(); click_sample(dialog, *point)
    sampled = dialog.settings if mode == 'replace' else getattr(dialog.settings,
        'inclusion_ranges' if mode == 'add' else 'exclusion_ranges')[-1]
    assert_sample_ranges(sampled, source[sy, sx])
    assert (dialog.settings.matte_black, dialog.settings.matte_white,
            dialog.settings.matte_morphology, dialog.settings.matte_smoothing) == (.2, .8, -.6, .7)
    np.testing.assert_array_equal(source, before)
    assert not window.editor.can_undo


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('width', [320, 360, 680])
def test_morphology_localized_dpi_screenshot(window, app, locale, width):
    set_locale(locale); dialog = loaded(window); dialog.resize(width, 800)
    install_pattern(dialog, (128, 256))
    dialog.controls['matte_morphology'].setValue(35); dialog.controls['matte_smoothing'].setValue(40)
    choose(dialog, 'overlay'); QTest.qWait(100)
    scale = float(os.getenv('QT_SCALE_FACTOR', '1'))
    assert dialog.devicePixelRatioF() == pytest.approx(scale) and dialog.width() == width
    for key in ('matte_black', 'matte_white', 'matte_morphology', 'matte_smoothing'):
        spin = dialog.controls[key]; focus(spin); label = dialog.control_labels[spin]
        assert label.visibleRegion().boundingRect() == label.rect()
        bounds = label.fontMetrics().boundingRect(QRect(0, 0, label.width(), 10000),
            int(Qt.TextFlag.TextWordWrap), label.text())
        assert bounds.height() <= label.height()
        assert spin.lineEdit().fontMetrics().horizontalAdvance(spin.text()) <= spin.lineEdit().width()
    spin = dialog.controls['matte_morphology']
    for value in (-100, 0, 100, 35):
        spin.setValue(value); focus(spin)
        assert spin.lineEdit().fontMetrics().horizontalAdvance(spin.text()) <= spin.lineEdit().width()
        assert spin.text().startswith('+' if value > 0 else '-' if value < 0 else '0')
    assert '3×3' in spin.toolTip() and '1' in spin.toolTip()
    group = dialog.reset_matte_button.parentWidget(); assert isinstance(group, QGroupBox)
    assert group.fontMetrics().horizontalAdvance(group.title()) + 20 <= group.width()
    note = next(label for label in group.findChildren(QLabel) if '3×3' in label.text())
    assert note.hasHeightForWidth() and note.height() >= note.heightForWidth(note.width())
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
    if locale == 'zh-CN':
        assert '收缩' in spin.accessibleName() and '扩张' in spin.accessibleName()
        assert '真实相邻' in spin.accessibleDescription()
    directory = os.getenv('MORPHOLOGY_SCREENSHOT_DIR')
    if directory:
        path = Path(directory); path.mkdir(parents=True, exist_ok=True)
        stem = f'morphology-{locale}-{width}-dpi{os.getenv("QT_SCALE_FACTOR", "1")}'
        pixmap = dialog.grab(); assert pixmap.save(str(path / (stem + '.png')))
        (path / (stem + '.json')).write_text(json.dumps(dict(locale=locale,
            logical_size=[dialog.width(), dialog.height()], device_pixel_ratio=dialog.devicePixelRatioF(),
            physical_size=[pixmap.width(), pixmap.height()], morphology=dialog.settings.matte_morphology,
            smoothing=dialog.settings.matte_smoothing, signed_text=spin.text(),
            mode=dialog.preview_choice.currentData(), horizontal_scroll=dialog.scroll.horizontalScrollBar().maximum()), indent=2))
    focus(dialog.reset_matte_button)
    assert dialog.reset_matte_button.fontMetrics().horizontalAdvance(dialog.reset_matte_button.text()) + 16 <= dialog.reset_matte_button.width()


def test_morphology_live_language_preserves_pending_settings_and_sign(window, app):
    dialog = loaded(window); spin = dialog.controls['matte_morphology']; spin.setValue(-67.891)
    before = deepcopy(dialog.settings)
    for locale in ['zh-CN', 'en', 'zh-CN', 'en']:
        set_locale(locale); app.processEvents()
        assert spin.accessibleName() == tr('Matte shrink / grow (%)')
        assert spin.toolTip() == spin.accessibleDescription()
        assert spin.text().startswith('-') and spin.value() == -67.891
        assert dialog.settings == before and not window.editor.can_undo


def test_morphology_new_visible_strings_and_dynamic_errors_directly_localized():
    import lumen.ui_qualifier as module
    strings = [node.value for node in ast.walk(ast.parse(Path(module.__file__).read_text()))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and any(word in node.value for word in ('Matte shrink / grow', 'Negative shrinks',
            'Remap black/white', 'after matte levels and shrink/grow', 'then matte levels, local shrink/grow'))]
    assert len(set(strings)) == 5
    try:
        set_locale('zh-CN')
        for text in strings:
            translated = str(tr(text)); assert translated != text
            assert any('\u4e00' <= char <= '\u9fff' for char in translated)
        for message in ('Qualifier matte_morphology must be finite and in -1..1',
                        'Version 22 requires exactly the legacy qualifier fields'):
            translated = str(tr_error(message))
            assert translated != message and '技术错误详情' not in translated
    finally:
        set_locale('en')


def test_report_matte_shrink_grow_and_combined_smoothing(window, app):
    set_locale('zh-CN'); dialog = loaded(window); dialog.resize(680, 1000)
    shape = (128, 256); y, x = np.indices(shape)
    red = ((x - 130) ** 2 + (y - 64) ** 2) <= 24 ** 2
    red[51, 102] = True; red[54, 115] = False; red[61, 119] = False
    red[72, 122] = False; red[64, 110] = False
    source = np.empty((*shape, 3), np.float32)
    source[:] = (.12, .36, .18); source[red] = (.75, .15, .12); source.setflags(write=False)
    dialog.accept_snapshot(replace(dialog.snapshot, frame=source, valid_mask=np.ones(shape, bool)))
    dialog.enabled_check.setChecked(True)
    dialog.controls['hue_width'].setValue(15); dialog.controls['hue_softness'].setValue(0)
    dialog.controls['matte_black'].setValue(20); dialog.controls['matte_white'].setValue(80)
    zoom(dialog, 8); dialog.set_detail_center(123, 64)
    before = deepcopy(window.project); directory = os.getenv('MORPHOLOGY_SCREENSHOT_DIR')
    for amount, smoothing, mode in [(0, 0, 'matte'), (-100, 0, 'matte'), (100, 0, 'matte'),
                                  (-60, 70, 'matte'), (60, 70, 'matte'),
                                  (-60, 70, 'overlay'), (60, 70, 'overlay')]:
        dialog.controls['matte_morphology'].setValue(amount)
        dialog.controls['matte_smoothing'].setValue(smoothing); choose(dialog, mode)
        focus(dialog.controls['matte_smoothing']); focus(dialog.controls['matte_morphology']); QTest.qWait(80)
        expected = independent_smooth(independent_morphology(red.astype(np.float32), amount / 100), smoothing / 100)
        np.testing.assert_allclose(dialog.matte, dialog.viewport.extract(expected), atol=SPATIAL_ATOL, rtol=0)
        if directory:
            path = Path(directory); path.mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(path / f'report-zh-CN-detail64-morphology{amount}-smoothing{smoothing}-{mode}.png'))
    assert window.project == before and not window.editor.can_undo


@pytest.mark.parametrize('level', [0, 8])
@pytest.mark.parametrize('amount', [-.7, .7])
@pytest.mark.parametrize('invert', [False, True])
def test_morphology_preview_applies_window_last_without_padding_exclusion(window, level, amount, invert):
    dialog = loaded(window); source, raw = install_pattern(dialog)
    dialog.settings = QualifierSettings(hue_width=.01, hue_softness=0,
        matte_morphology=amount, matte_smoothing=.8,
        window_shape='ellipse', window_center_x=.51, window_center_y=.47,
        window_width=.2, window_height=.7, window_feather=.2, window_invert=invert)
    h, w = raw.shape; y, x = np.indices(raw.shape)
    radius = np.hypot(((x + .5) / w - .51) * 10, ((y + .5) / h - .47) * (2 / .7))
    gate = np.clip(1 - np.maximum(radius - 1, 0) / .2, 0, 1).astype(np.float32)
    if invert: gate = np.float32(1) - gate
    expected = independent_smooth(independent_morphology(raw, amount), .8) * gate
    zoom(dialog, level)
    if level: dialog.set_detail_center(425, 63)
    dialog.render_preview()
    np.testing.assert_allclose(dialog.matte, dialog.viewport.extract(expected), atol=SPATIAL_ATOL, rtol=0)
    dialog.accept_snapshot(replace(dialog.snapshot, valid_mask=np.zeros(raw.shape, bool)))
    np.testing.assert_allclose(dialog.matte, dialog.viewport.extract(expected), atol=SPATIAL_ATOL, rtol=0)


@pytest.mark.parametrize('grade_enabled,qualifier_enabled', [(False, True), (True, False)])
def test_morphology_inspection_does_not_enable_disabled_adjustments(window, grade_enabled, qualifier_enabled):
    window.selected_clip().grade.enabled = grade_enabled
    dialog = loaded(window); source, raw = install_pattern(dialog, (64, 64))
    dialog.settings = QualifierSettings(enabled=qualifier_enabled, hue_width=.01,
        hue_softness=0, matte_morphology=.7, matte_smoothing=.8, exposure=1)
    choose(dialog, 'adjusted')
    np.testing.assert_allclose(dialog.matte, independent_smooth(independent_morphology(raw, .7), .8), atol=SPATIAL_ATOL, rtol=0)
    np.testing.assert_array_equal(dialog.canvas._pixels, np.rint(source * 255).astype(np.uint8))
    assert dialog.settings.enabled is qualifier_enabled and not window.editor.can_undo

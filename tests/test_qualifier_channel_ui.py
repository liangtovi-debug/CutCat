"""Real Qt channel switches: range ownership, sampling and transaction safety."""
from copy import deepcopy
from dataclasses import asdict, replace
import os
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from lumen.i18n import set_locale
from test_qualifier_ui import app, loaded, window
from test_qualifier_softness_ui import prepare, pick
from test_qualifier_sets_ui_review import choose

CHANNELS = ('hue', 'saturation', 'luma')
TARGETS = [('base', 0), ('inclusion_ranges', 0), ('exclusion_ranges', 0)]


def switch(dialog, channel):
    check = dialog.channel_checks[channel]
    check.setFocus()
    QTest.keyClick(check, Qt.Key.Key_Space)


@pytest.mark.parametrize('target', TARGETS)
@pytest.mark.parametrize('channel', CHANNELS)
def test_switch_is_range_local_preserves_values_reenables_and_cancels(window, app, target, channel):
    dialog = prepare(window)
    exact = dict(hue_softness=.0123456789, saturation_softness_low=.123456789,
                 saturation_softness_high=.234567891, luma_softness_low=.345678912,
                 luma_softness_high=.456789123)
    for key, value in exact.items(): setattr(dialog.settings, key, value)
    for kind in ('inclusion_ranges', 'exclusion_ranges'):
        getattr(dialog.settings, kind)[0] = replace(getattr(dialog.settings, kind)[0], **exact)
    dialog.refresh_ranges()
    choose(dialog.range_choice, target)
    before = deepcopy(dialog.settings); project = deepcopy(window.project)
    switch(dialog, channel)
    expected = deepcopy(before)
    selected = expected if target[0] == 'base' else getattr(expected, target[0])[0]
    if target[0] == 'base':
        setattr(selected, channel + '_enabled', False)
    else:
        getattr(expected, target[0])[0] = replace(selected, **{channel + '_enabled': False})
    assert dialog.settings == expected
    for key, control in dialog.controls.items():
        if key.startswith(channel + '_') and key != 'hue_shift':
            assert not control.isEnabled()
    choose(dialog.range_choice, ('base', 0)); choose(dialog.range_choice, target)
    assert not dialog.channel_checks[channel].isChecked()
    switch(dialog, channel)
    assert dialog.settings == before
    assert all(control.isEnabled() for key, control in dialog.controls.items() if key.startswith(channel + '_'))
    dialog.reject()
    assert window.project == project and not window.editor.can_undo


def test_toggle_apply_one_transaction_undo_redo_reopen_noop(window, app):
    before = deepcopy(window.project)
    dialog = loaded(window)
    switch(dialog, 'hue')
    expected = deepcopy(dialog.settings)
    dialog.apply_settings()
    assert len(window.editor._undo) == 1
    assert window.selected_clip().grade.qualifier == expected
    again = loaded(window); assert not again.channel_checks['hue'].isChecked()
    again.apply_settings(); assert len(window.editor._undo) == 1
    window.undo(); assert window.project == before
    window.redo(); assert window.selected_clip().grade.qualifier == expected


def test_stale_switch_and_apply_never_commit(window, app):
    dialog = loaded(window); before = deepcopy(window.project)
    window.revision += 1
    switch(dialog, 'hue'); dialog.apply_settings()
    assert window.project == before and not window.editor.can_undo
    assert all(not check.isEnabled() for check in dialog.channel_checks.values())


@pytest.mark.parametrize('mode', ['replace', 'add', 'subtract'])
@pytest.mark.parametrize('channel', CHANNELS)
def test_picker_uses_base_toggles_preserves_disabled_values(window, app, mode, channel):
    dialog = prepare(window)
    dialog.settings.hue_softness = .0123456789
    dialog.settings.saturation_softness_low = .123456789
    dialog.settings.saturation_softness_high = .234567891
    dialog.settings.luma_softness_low = .345678912
    dialog.settings.luma_softness_high = .456789123
    dialog.refresh_ranges()
    switch(dialog, channel)
    base = asdict(dialog.settings)
    # An unrelated selected range has the opposite toggle; base owns pick policy.
    choose(dialog.range_choice, ('inclusion_ranges', 0))
    assert dialog.channel_checks[channel].isChecked()
    choose(dialog.sample_mode, mode)
    pick(dialog, app)
    assert not dialog.canvas.picking
    selected = dialog.selected_range()
    assert getattr(selected, channel + '_enabled') is False
    for key, value in asdict(selected).items():
        if key.startswith(channel + '_') and key != 'hue_shift':
            assert value == base[key]
    assert not window.editor.can_undo
    dialog.reject()


@pytest.mark.parametrize('mode', ['replace', 'add', 'subtract'])
def test_gray_picker_allowed_with_base_hue_off(window, app, mode):
    dialog = prepare(window, (.4, .4, .4)); switch(dialog, 'hue')
    choose(dialog.sample_mode, mode); pick(dialog, app)
    assert not dialog.canvas.picking
    assert not dialog.selected_range().hue_enabled
    assert not window.editor.can_undo


@pytest.mark.parametrize('target', TARGETS)
@pytest.mark.parametrize('channel', CHANNELS)
def test_soften_disabled_selected_channel_rejected_without_commit(window, app, target, channel):
    dialog = prepare(window)
    choose(dialog.range_choice, target); switch(dialog, channel)
    choose(dialog.softness_channel, channel)
    before = deepcopy(dialog.settings)
    pick(dialog, app)
    assert dialog.settings == before
    assert dialog.canvas.picking and 'enable the selected channel' in dialog.sample_label.text().lower()
    assert not window.editor.can_undo


def test_all_off_exclusion_confirmation_no_repeat_cancel_yes(window, app, monkeypatch):
    before = deepcopy(window.project); questions = []
    def answer(*args):
        questions.append(args)
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', answer)
    for _ in range(2):
        dialog = prepare(window); dialog.enabled_check.setChecked(True)
        choose(dialog.range_choice, ('exclusion_ranges', 0))
        for channel in CHANNELS: switch(dialog, channel)
        dialog.render_preview()
        assert not dialog.matte.any()
        for _ in range(2):
            dialog.apply_settings()
            assert dialog.isVisible() and window.project == before and not window.editor.can_undo
        dialog.reject()
        assert window.project == before and not window.editor.can_undo
    assert len(questions) == 4
    dialog = prepare(window); dialog.enabled_check.setChecked(True)
    choose(dialog.range_choice, ('exclusion_ranges', 0))
    for channel in CHANNELS: switch(dialog, channel)
    expected = deepcopy(dialog.settings)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    dialog.apply_settings()
    assert window.selected_clip().grade.qualifier == expected and len(window.editor._undo) == 1
    window.undo(); assert window.project == before


def test_all_off_base_selects_valid_and_disabled_correction_needs_no_warning(window, app, monkeypatch):
    dialog = prepare(window)
    dialog.settings.inclusion_ranges = []; dialog.settings.exclusion_ranges = []
    dialog.refresh_ranges()
    for channel in CHANNELS: switch(dialog, channel)
    dialog.render_preview()
    assert dialog.matte.all()
    from lumen.qualifier import QualifierRange
    dialog.settings.exclusion_ranges = [QualifierRange(hue_enabled=False, saturation_enabled=False, luma_enabled=False)]
    dialog.refresh_ranges(); dialog.render_preview()
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: pytest.fail('Disabled correction must not prompt'))
    dialog.apply_settings()
    assert len(window.editor._undo) == 1


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('width', [320, 760])
def test_channel_focus_scroll_layout_and_screenshots(window, app, locale, width):
    from lumen.ui_style import apply_ui_layout_tokens
    set_locale(locale)
    dialog = prepare(window); choose(dialog.sample_mode, 'replace')
    dialog.resize(width, 800); apply_ui_layout_tokens(dialog)
    folder = os.environ.get('QUALIFIER_CHANNEL_SCREENSHOTS')
    for target in TARGETS:
        choose(dialog.range_choice, target)
        for channel in CHANNELS:
            check = dialog.channel_checks[channel]
            dialog.controls['exposure'].setFocus(); QTest.qWait(20)
            check.setFocus(); QTest.qWait(40); app.processEvents()
            assert check.isEnabled() and check.accessibleName()
            assert check.visibleRegion().boundingRect() == check.rect()
            assert dialog.scroll.horizontalScrollBar().maximum() == 0
            assert dialog.width() == width
            switch(dialog, channel); assert not check.isChecked()
            if folder:
                Path(folder).mkdir(parents=True, exist_ok=True)
                assert dialog.grab().save(str(Path(folder) / f'channels-{locale}-{width}-{target[0]}-{channel}-dpr{dialog.devicePixelRatioF():g}.png'))
            switch(dialog, channel)
        # Existing numeric features remain reachable after the new switch rows.
        for key, control in dialog.controls.items():
            if not control.isEnabled():
                continue
            control.setFocus(); QTest.qWait(20); app.processEvents()
            assert control.visibleRegion().boundingRect() == control.rect(), key
            label = dialog.control_labels[control]
            assert label.visibleRegion().boundingRect() == label.rect(), key
            assert dialog.scroll.horizontalScrollBar().maximum() == 0
            if folder and key in ('hue_softness', 'saturation_softness_high', 'luma_softness_high'):
                assert dialog.grab().save(str(Path(folder) / f'controls-{locale}-{width}-{target[0]}-{key}-dpr{dialog.devicePixelRatioF():g}.png'))
    assert not window.editor.can_undo


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_real_confirmation_buttons_default_no_then_yes(window, app, locale):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    set_locale(locale)
    before = deepcopy(window.project)
    dialog = prepare(window); dialog.enabled_check.setChecked(True)
    choose(dialog.range_choice, ('exclusion_ranges', 0))
    for channel in CHANNELS: switch(dialog, channel)
    expected = deepcopy(dialog.settings)
    observed = []
    def close_question(answer):
        box = QApplication.activeModalWidget()
        if not isinstance(box, QMessageBox):
            observed.append('missing confirmation'); return
        observed.append((box.defaultButton() is box.button(QMessageBox.StandardButton.No), box.text()))
        box.button(answer).click()
    QTimer.singleShot(30, lambda: close_question(QMessageBox.StandardButton.No))
    dialog.apply_settings()
    assert window.project == before and not window.editor.can_undo and dialog.isVisible()
    QTimer.singleShot(30, lambda: close_question(QMessageBox.StandardButton.Yes))
    dialog.apply_settings()
    assert len(observed) == 2 and all(isinstance(item, tuple) and item[0] for item in observed)
    assert all(('所有' if locale == 'zh-CN' else 'every valid color') in item[1] for item in observed)
    assert window.selected_clip().grade.qualifier == expected and len(window.editor._undo) == 1


@pytest.mark.parametrize('change', ['revision', 'selection'])
def test_confirmation_yes_after_nested_context_change_never_commits(window, app, change):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    before = deepcopy(window.project)
    dialog = prepare(window); dialog.enabled_check.setChecked(True)
    choose(dialog.range_choice, ('exclusion_ranges', 0))
    for channel in CHANNELS: switch(dialog, channel)
    def change_and_accept():
        box = QApplication.activeModalWidget()
        if change == 'revision':
            window.revision += 1
        else:
            window.select_clip('other')
        box.button(QMessageBox.StandardButton.Yes).click()
    QTimer.singleShot(30, change_and_accept)
    dialog.apply_settings()
    assert window.project == before and not window.editor.can_undo
    assert not dialog.apply_button.isEnabled()

"""Independent exterior feathers: actual Qt controls, sampling and transactions."""
from copy import deepcopy
from dataclasses import asdict, replace
import os
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest

from lumen.i18n import set_locale
from lumen.qualifier import QualifierRange, QualifierSettings, range_softness
from test_qualifier_ui import app, loaded, window
from test_qualifier_sets_ui_review import choose
from test_qualifier_softness_ui import prepare, pick

TARGETS = [('base', 0), ('inclusion_ranges', 0), ('exclusion_ranges', 0)]
EDGES = [f'{channel}_softness_{edge}' for channel in ('saturation', 'luma') for edge in ('low', 'high')]


@pytest.mark.parametrize('target', TARGETS)
def test_legacy_values_display_without_quantizing_or_materializing(window, app, target):
    core = dict(saturation_softness=.123456789, luma_softness=.234567891)
    settings = QualifierSettings(**core, inclusion_ranges=[QualifierRange(**core)],
                                exclusion_ranges=[QualifierRange(**core)])
    window.selected_clip().grade.qualifier = settings
    before = deepcopy(window.project)
    dialog = loaded(window)
    for selection in [target, ('base', 0), target]:
        choose(dialog.range_choice, selection)
        for key in EDGES:
            channel, _, edge = key.split('_')
            assert dialog.controls[key].value() == pytest.approx(round(range_softness(dialog.selected_range(), channel, edge) * 100, 3))
            dialog.controls[key].setFocus(); app.processEvents()
    assert dialog.settings == settings
    dialog.apply_settings()
    assert window.project == before and not window.editor.can_undo


@pytest.mark.parametrize('target', TARGETS)
@pytest.mark.parametrize('key', EDGES)
def test_edit_one_edge_keeps_other_values_exact_and_cancel_clean(window, app, target, key):
    dialog = prepare(window)
    choose(dialog.range_choice, target)
    before = asdict(dialog.settings); project = deepcopy(window.project)
    control = dialog.controls[key]
    dialog.activateWindow(); control.setFocus(); app.processEvents()
    control.selectAll(); QTest.keyClicks(control, '31.125')
    QTest.keyClick(control, Qt.Key.Key_Tab); app.processEvents()
    after = asdict(dialog.settings)
    selected = after if target[0] == 'base' else after[target[0]][target[1]]
    assert selected[key] == pytest.approx(.31125)
    selected[key] = None
    assert after == before
    assert dialog.range_choice.currentData() == target
    dialog.reject()
    assert window.project == project and not window.editor.can_undo


@pytest.mark.parametrize('target', TARGETS)
@pytest.mark.parametrize('channel,edge,rgb', [
    ('saturation', 'low', (.6, .3, .3)), ('saturation', 'high', (.6, 0, 0)),
    ('luma', 'low', (.6, .3, .3)), ('luma', 'high', (.9, .9, .9)),
])
def test_source_sample_updates_only_selected_outside_edge(window, app, target, channel, edge, rgb):
    dialog = prepare(window, rgb)
    choose(dialog.range_choice, target); choose(dialog.softness_channel, channel)
    choose(dialog.preview_choice, 'overlay')
    before = asdict(dialog.settings)
    pick(dialog, app)
    after = asdict(dialog.settings)
    item = after if target[0] == 'base' else after[target[0]][0]
    key = f'{channel}_softness_{edge}'
    assert item[key] > 0
    item[key] = None
    assert after == before
    first = deepcopy(dialog.settings)
    pick(dialog, app)
    assert dialog.settings == first and 'unchanged' in dialog.sample_label.text()
    assert not window.editor.can_undo
    dialog.apply_settings()
    assert len(window.editor._undo) == 1
    window.undo(); window.redo()
    assert window.selected_clip().grade.qualifier == first


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('width', [320, 760])
def test_asymmetric_labels_focus_geometry_and_screenshots(window, app, locale, width):
    from lumen.ui_style import apply_ui_layout_tokens
    set_locale(locale)
    dialog = prepare(window)
    # Ordinary mode leaves room to inspect a full pair in the narrow viewport.
    choose(dialog.sample_mode, 'replace')
    choose(dialog.range_choice, ('exclusion_ranges', 0))
    import colorsys
    frame = np.array([[colorsys.hsv_to_rgb(x / 16, .25 + y / 24, .85)
                       for x in range(16)] for y in range(16)], np.float32)
    frame.setflags(write=False)
    dialog.snapshot = replace(dialog.snapshot, frame=frame)
    choose(dialog.preview_choice, 'overlay'); dialog.render_preview()
    dialog.resize(width, 800)
    apply_ui_layout_tokens(dialog)
    for index, key in enumerate(EDGES):
        dialog.controls[key].setValue(10 + 10 * index)
    for key in EDGES:
        control = dialog.controls[key]
        control.setFocus(); QTest.qWait(60); app.processEvents()
        label = dialog.control_labels[control]
        assert label.buddy() is control
        assert control.visibleRegion().boundingRect() == control.rect()
        assert label.visibleRegion().boundingRect() == label.rect()
        a = QRect(control.mapTo(dialog, QPoint()), control.size())
        b = QRect(label.mapTo(dialog, QPoint()), label.size())
        assert a.intersected(b).isEmpty()
        assert dialog.scroll.horizontalScrollBar().maximum() == 0
        assert dialog.width() == width
        assert ('低端' if locale == 'zh-CN' else 'low') in dialog.controls['luma_softness_low'].accessibleName()
        folder = os.environ.get('QUALIFIER_ASYMMETRIC_SCREENSHOTS')
        if folder and key.endswith('_high'):
            Path(folder).mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(Path(folder) / f'asymmetric-{locale}-{width}-{key}-dpr{dialog.devicePixelRatioF():g}.png'))


def test_focused_edge_commits_to_old_range_before_selection_changes(window, app):
    dialog = prepare(window)
    choose(dialog.range_choice, ('inclusion_ranges', 0))
    dialog.activateWindow()
    control = dialog.controls['luma_softness_high']
    control.setFocus(); app.processEvents(); control.selectAll()
    QTest.keyClicks(control, '42.125')
    # A real focus transfer commits the pending editor before selecting a range.
    dialog.range_choice.setFocus(); app.processEvents()
    choose(dialog.range_choice, ('exclusion_ranges', 0))
    assert dialog.settings.inclusion_ranges[0].luma_softness_high == pytest.approx(.42125)
    assert dialog.settings.exclusion_ranges[0].luma_softness_high is None
    assert dialog.controls['luma_softness_high'].value() == 0
    assert not window.editor.can_undo

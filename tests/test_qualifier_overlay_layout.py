"""Overlay focus scrolling keeps wrapped field labels and fixed controls visible."""
from copy import deepcopy
import itertools
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest

from lumen.i18n import set_locale
from test_qualifier_ui import app, loaded, window
from test_qualifier_picker_ui import click_sample


def rect_on_screen(widget):
    return QRect(widget.mapToGlobal(QPoint()), widget.size())


@pytest.mark.parametrize('locale', ['zh-CN', 'en'])
@pytest.mark.parametrize('size', [(680, 680), (360, 540)])
def test_overlay_reveal_includes_wrapped_label_bottom_and_no_overlap(window, app, locale, size):
    set_locale(locale)
    try:
        window.project.height = 540
        before = deepcopy(window.project)
        dialog = loaded(window)
        dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData('overlay'))
        dialog.zoom_choice.setCurrentIndex(dialog.zoom_choice.findData(2))
        dialog.set_detail_center(32, 270)
        QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
        assert dialog.pick_button.isChecked() and dialog.canvas.picking
        click_sample(dialog, *dialog.viewport.grid_at(32, 270))
        assert dialog.preview_choice.currentData() == 'overlay'
        assert dialog.sample_source_position is not None
        assert not dialog.pick_button.isChecked() and not dialog.canvas.picking
        dialog.resize(*size)
        # Allow the production 50 ms preview timer and Qt wrap/layout events
        # to settle before measuring final widget geometry.
        QTest.qWait(100)
        assert dialog.width() == size[0] and dialog.height() == max(600, size[1])
        fixed = [getattr(dialog, name) for name in ('canvas', 'preview_choice', 'zoom_choice',
                 'center_x', 'center_y', 'pick_button', 'sample_label', 'resolution', 'status',
                 'apply_button', 'close_button')]
        for control in [dialog.enabled_check, *dialog.controls.values()]:
            # Exercise production FocusIn scrolling, never manually reveal
            # the field after Qt has handled the focus event.
            control.setFocus(); QTest.qWait(100)
            shown = fixed + [control]
            label = dialog.control_labels.get(control)
            if label is not None:
                shown.append(label)
            for widget in shown:
                assert widget.visibleRegion().boundingRect() == widget.rect(), (
                    locale, size, control.objectName(), widget.accessibleName(),
                    widget.visibleRegion().boundingRect(), widget.rect())
            for first, second in itertools.combinations(shown, 2):
                assert rect_on_screen(first).intersected(rect_on_screen(second)).isEmpty()
            assert dialog.scroll.horizontalScrollBar().maximum() == 0
        assert window.project == before and not window.editor.can_undo
    finally:
        set_locale('en')

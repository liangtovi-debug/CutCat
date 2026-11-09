"""Real Qt small-screen layout contracts; native frame sizes are simulated.

Actual scale-factor screenshots are generated separately by the evidence script.
These tests do not claim native Windows/macOS display-server validation.
"""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QMargins, QPoint, QRect
from PySide6.QtWidgets import QScrollArea, QWidget

from test_ui import app, window
from lumen.i18n import set_locale
from lumen.ui_component_setup import ComponentSetupDialog


def settle(app):
    for _ in range(12):
        app.processEvents()


def assert_no_top_level_overlap(dialog):
    visible = [w for w in dialog.findChildren(QWidget)
               if w.parentWidget() is dialog and w.isVisible()]
    for index, widget in enumerate(visible):
        assert dialog.rect().contains(widget.geometry())
        for other in visible[index + 1:]:
            assert not widget.geometry().intersects(other.geometry()), \
                f'{type(widget).__name__} overlaps {type(other).__name__}'


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('logical_size', [(1366, 768), (911, 512), (683, 384)])
@pytest.mark.parametrize('frame', [(0, 0, 0, 0), (8, 30, 8, 8)])
def test_small_screen_fit_preserves_actions_and_scroll(window, app, tmp_path, monkeypatch,
                                                       locale, logical_size, frame):
    set_locale(locale)
    dialog = ComponentSetupDialog(window, path=tmp_path / 'not-written.json')
    dialog.show(); settle(app)
    # Explicit availableGeometry contract with optional simulated native frame.
    # No claim that Linux/offscreen is a native Windows/macOS window manager.
    margins = QMargins(*frame)
    monkeypatch.setattr(dialog, 'windowHandle', lambda: SimpleNamespace(frameMargins=lambda: margins))
    dialog.resize(760, 730)
    dialog._fit_available_screen(QRect(0, 0, *logical_size)); settle(app)
    assert dialog.width() + margins.left() + margins.right() <= logical_size[0]
    assert dialog.height() + margins.top() + margins.bottom() <= logical_size[1]
    assert dialog.height() >= dialog.layout().minimumHeightForWidth(dialog.width())
    assert_no_top_level_overlap(dialog)
    for button in (dialog.check_button, dialog.cancel_button, dialog.apply_button):
        assert button.isVisible() and button.width() >= button.minimumSizeHint().width()
        assert button.height() >= button.minimumSizeHint().height()
    assert dialog.results.height() >= dialog.results.fontMetrics().lineSpacing() * 3
    scroll = dialog.findChild(QScrollArea)
    assert scroll.horizontalScrollBar().maximum() == 0
    assert scroll.verticalScrollBar().maximum() > 0
    for field in dialog.fields.values():
        # QLineEdit's ensureWidgetVisible uses its cursor rectangle, so it can
        # legitimately leave the outer border partly clipped. Test that the
        # whole control is reachable through the actual scrollbar range.
        field_y = field.mapTo(scroll.widget(), QPoint(0, 0)).y()
        scroll.verticalScrollBar().setValue(field_y - max(0, (scroll.viewport().height() - field.height()) // 2))
        settle(app)
        rect = QRect(field.mapTo(scroll.viewport(), QPoint(0, 0)), field.size())
        assert scroll.viewport().rect().contains(rect)
    assert not window.jobs and not dialog.path.exists()
    dialog.reject()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_normal_size_and_wrapped_note_minimum(window, app, tmp_path, locale):
    set_locale(locale)
    dialog = ComponentSetupDialog(window, path=tmp_path / 'not-written.json')
    dialog.show(); settle(app)
    dialog.resize(760, 730)
    dialog._fit_available_screen(QRect(0, 0, 1920, 1600)); settle(app)
    assert (dialog.width(), dialog.height()) == (760, 730)
    assert dialog.results.minimumHeight() == 150
    dialog.resize(640, 300); settle(app)
    assert dialog.height() >= dialog.layout().minimumHeightForWidth(640)
    assert_no_top_level_overlap(dialog)
    dialog.reject()

"""Publish changed form geometry without draining unrelated Qt work."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer
from PySide6.QtWidgets import (
    QApplication, QDialog, QFormLayout, QGroupBox, QLineEdit,
    QScrollArea, QVBoxLayout, QWidget,
)
from shiboken6 import delete, isValid

from lumen.ui_style import apply_ui_layout_tokens


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def settle(app):
    for _ in range(12):
        app.processEvents()


def add_scroll(parent, *, forms=3):
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    body = QWidget()
    QVBoxLayout(body)
    entries = []
    for index in range(forms):
        host = QGroupBox(f'Section {index}')
        form = QFormLayout(host)
        form.addRow('Visible label', QLineEdit('Unchanged value'))
        body.layout().addWidget(host)
        entries.append(form)
    scroll.setWidget(body)
    parent.layout().addWidget(scroll)
    return scroll, entries


@pytest.fixture
def scene(app):
    root = QWidget()
    QVBoxLayout(root)
    scroll, forms = add_scroll(root)
    apply_ui_layout_tokens(root)
    root.resize(320, 400)
    root.show()
    settle(app)
    yield root, scroll, forms, root._lumen_layout_controller
    if isValid(root):
        delete(root)
    settle(app)


def change_minimums(forms):
    for form in forms:
        margins = form.contentsMargins()
        form.setContentsMargins(margins.left() + 1, margins.top(),
                                margins.right(), margins.bottom())


def record_sends(monkeypatch):
    sent = []
    original = QCoreApplication.sendEvent

    def send(receiver, event):
        if event.type() == QEvent.Type.LayoutRequest:
            sent.append(receiver)
        return original(receiver, event)

    monkeypatch.setattr(QCoreApplication, 'sendEvent', send)
    return sent


def test_changed_forms_publish_once_and_unchanged_refresh_does_not(scene, monkeypatch):
    root, scroll, forms, controller = scene
    sent = record_sends(monkeypatch)
    change_minimums(forms)
    controller.refresh()
    assert sent == [scroll]
    assert scroll.widget().width() <= scroll.viewport().width()
    sent.clear()
    controller.refresh()
    assert sent == []


def test_scroll_commit_preserves_unrelated_posted_events_and_timers(scene, app):
    root, scroll, forms, controller = scene
    delivered = []
    event_type = QEvent.Type(QEvent.registerEventType())

    class Receiver(QObject):
        def event(self, event):
            if event.type() == event_type:
                delivered.append('posted')
                return True
            return super().event(event)

    receiver = Receiver()
    QCoreApplication.postEvent(receiver, QEvent(event_type))
    QTimer.singleShot(0, receiver, lambda: delivered.append('timer'))
    change_minimums(forms)
    controller.refresh()
    assert delivered == []
    app.processEvents()
    assert sorted(delivered) == ['posted', 'timer']


def test_nested_scrolls_publish_inner_before_outer(scene, app, monkeypatch):
    root, outer, outer_forms, controller = scene
    inner, inner_forms = add_scroll(outer.widget())
    controller.refresh()
    settle(app)
    sent = record_sends(monkeypatch)
    change_minimums(outer_forms + inner_forms)
    controller.refresh()
    assert sent == [inner, outer]
    assert inner.horizontalScrollBar().maximum() == 0
    assert outer.horizontalScrollBar().maximum() == 0
    sent.clear()
    settle(app)
    assert len(sent) <= 2  # Native follow-up layout notifications converge.
    sent.clear()
    settle(app)
    assert sent == []


@pytest.mark.parametrize('mutation', ['governor', 'delete'])
def test_nested_commit_rechecks_live_receiver_before_each_send(scene, app, monkeypatch, mutation):
    root, outer, outer_forms, controller = scene
    inner, inner_forms = add_scroll(outer.widget())
    controller.refresh()
    settle(app)
    sent = []
    original = QCoreApplication.sendEvent

    def send(receiver, event):
        sent.append(receiver)
        result = original(receiver, event)
        if receiver is inner:
            if mutation == 'governor':
                outer._lumen_layout_controller = object()
            else:
                delete(outer)
        return result

    monkeypatch.setattr(QCoreApplication, 'sendEvent', send)
    change_minimums(outer_forms + inner_forms)
    controller.refresh()
    assert sent == [inner]


def test_scheduled_commit_respects_visible_child_and_independent_governor(scene, app, monkeypatch):
    root, main_scroll, main_forms, controller = scene
    dialog = QDialog(root)
    QVBoxLayout(dialog)
    child_scroll, child_forms = add_scroll(dialog)
    independent = QWidget(dialog)
    QVBoxLayout(independent)
    separate_scroll, separate_forms = add_scroll(independent)
    dialog.layout().addWidget(independent)
    apply_ui_layout_tokens(independent)
    root.hide()
    dialog.show()
    settle(app)
    sent = record_sends(monkeypatch)
    change_minimums(main_forms + child_forms + separate_forms)
    controller._refresh_scheduled()
    assert sent == [child_scroll]
    assert main_scroll not in sent and separate_scroll not in sent


def test_real_overwide_content_is_not_clipped_or_forced_to_viewport(scene, monkeypatch):
    root, scroll, forms, controller = scene
    forms[0].itemAt(0, QFormLayout.ItemRole.FieldRole).widget().setMinimumWidth(600)
    sent = record_sends(monkeypatch)
    controller.refresh()
    assert sent == [scroll]
    assert scroll.widget().width() >= 600
    assert scroll.horizontalScrollBar().maximum() > 0

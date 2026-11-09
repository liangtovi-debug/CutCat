"""Isolated native timeline gestures: no model, schema or decoding involved."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from lumen.i18n import set_locale
from lumen.ui_keyframe_timeline import KeyframeTimeline, TIMELINE_HELP


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def strip(app):
    host = QWidget(); layout = QVBoxLayout(host)
    widget = KeyframeTimeline(101); widget.set_state((0, 20, 40, 100), 20, 20)
    button = QPushButton('Other control'); layout.addWidget(widget); layout.addWidget(button)
    host.resize(440, 160); host.show(); host.activateWindow(); widget.setFocus(); app.processEvents()
    widget.keySelected.connect(lambda frame: widget.set_state(widget.frames, frame, frame))
    widget.frameRequested.connect(lambda frame: widget.set_state(widget.frames, frame, None))
    yield widget, button
    host.close(); host.deleteLater(); app.processEvents(); set_locale('en')


def point(widget, frame):
    return QPoint(round(widget.frame_x(frame)), round(widget.track_y()))


def press(widget, frame=20):
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=point(widget, frame))
    assert widget.gesture_active


def test_drag_stages_one_release_and_releases_without_repeated_commit(strip):
    widget, _ = strip; moves = []; states = []
    widget.moveRequested.connect(lambda *pair: moves.append(pair)); widget.gestureChanged.connect(states.append)
    press(widget); QTest.mouseMove(widget, point(widget, 60))
    assert widget.frames == (0, 20, 40, 100) and widget.reference_frame == 20
    assert widget.preview_frame == 60 and moves == []
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=point(widget, 60))
    assert moves == [(20, 60)] and states == [True, False] and not widget.gesture_active
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=point(widget, 80))
    assert moves == [(20, 60)]


@pytest.mark.parametrize('destination,expected', [(-1000, 0), (1000, 100)])
def test_drag_snaps_to_bounded_integer(strip, destination, expected):
    widget, _ = strip; moves = []; widget.moveRequested.connect(lambda *pair: moves.append(pair))
    press(widget)
    end = QPoint(destination, round(widget.track_y()))
    QTest.mouseMove(widget, end); assert widget.preview_frame == expected
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=end)
    assert moves == [(20, expected)]


def test_click_key_and_click_reference_do_not_retime(strip):
    widget, _ = strip; moves = []; widget.moveRequested.connect(lambda *pair: moves.append(pair))
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=point(widget, 40))
    assert widget.selected_frame == 40 and widget.reference_frame == 40 and not moves
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=point(widget, 75))
    assert widget.reference_frame == 75 and widget.selected_frame is None and not moves


@pytest.mark.parametrize('cancel', ['escape', 'focus', 'hide', 'deactivate', 'ungrab', 'disable', 'state'])
def test_interrupted_drag_never_commits_on_late_release(strip, app, cancel):
    widget, button = strip; moves = []; widget.moveRequested.connect(lambda *pair: moves.append(pair))
    press(widget); QTest.mouseMove(widget, point(widget, 60))
    if cancel == 'escape': QTest.keyClick(widget, Qt.Key.Key_Escape)
    elif cancel == 'focus': button.setFocus(); app.processEvents()
    elif cancel == 'hide': widget.hide()
    elif cancel == 'deactivate': QApplication.sendEvent(widget, QEvent(QEvent.Type.WindowDeactivate))
    elif cancel == 'ungrab': QApplication.sendEvent(widget, QEvent(QEvent.Type.UngrabMouse))
    elif cancel == 'disable': widget.setEnabled(False)
    else: widget.set_state((0, 20, 40, 100), 40, 40)
    assert not widget.gesture_active and widget.preview_frame is None
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=point(widget, 60))
    assert not moves and widget.frames == (0, 20, 40, 100)


def test_keyboard_press_repeat_release_is_one_staged_move(strip):
    widget, _ = strip; moves = []; widget.moveRequested.connect(lambda *pair: moves.append(pair))
    QTest.keyPress(widget, Qt.Key.Key_Right)
    assert widget.gesture_active and widget.preview_frame == 21 and not moves
    for _ in range(3):
        QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier, '', True))
        QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier, '', True))
    assert widget.preview_frame == 24 and not moves
    QTest.keyRelease(widget, Qt.Key.Key_Right)
    assert moves == [(20, 24)] and not widget.gesture_active


def test_keyboard_shift_escape_focus_and_selection(strip, app):
    widget, button = strip; moves = []; widget.moveRequested.connect(lambda *pair: moves.append(pair))
    QTest.keyPress(widget, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    assert widget.preview_frame == 10
    QTest.keyClick(widget, Qt.Key.Key_Escape); QTest.keyRelease(widget, Qt.Key.Key_Left)
    assert not moves and not widget.gesture_active
    QTest.keyPress(widget, Qt.Key.Key_Right); button.setFocus(); app.processEvents()
    QTest.keyRelease(widget, Qt.Key.Key_Right); assert not moves
    widget.setFocus(); QTest.keyClick(widget, Qt.Key.Key_Down)
    assert widget.selected_frame == 40
    QTest.keyClick(widget, Qt.Key.Key_Up); assert widget.selected_frame == 20
    QTest.keyClick(widget, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    assert moves == [(20, 10)]


def test_state_filters_hidden_keys_without_mutating_input_and_noop_click_long_duration(app):
    widget = KeyframeTimeline(2**31); frames = [-2**53 + 1, 0, 123456789, 123456790, 2**31 - 1, 2**53 - 1]
    widget.resize(300, widget.sizeHint().height()); widget.show(); widget.set_state(frames, 123456789, 123456789)
    widget.keySelected.connect(lambda frame: widget.set_state(widget.frames, frame, frame))
    moves = []; widget.moveRequested.connect(lambda *pair: moves.append(pair))
    assert widget.frames == tuple(frames[1:-1]) and frames[0] == -2**53 + 1
    assert widget.frame_at(-100) == 0 and widget.frame_at(1000) == 2**31 - 1
    # Both keys share one integer screen pixel, and clicking must not retime.
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=point(widget, 123456789))
    assert not moves and widget.selected_frame == 123456789
    widget.set_state(widget.frames, 123456789, 123456789)
    QTest.keyClick(widget, Qt.Key.Key_Down)
    assert widget.selected_frame == 123456790
    widget.close()


def test_one_frame_clip_is_usable_without_divide_by_zero_or_moves(app):
    widget = KeyframeTimeline(1); widget.resize(320, widget.sizeHint().height()); widget.show()
    widget.set_state((0,), 0, 0); widget.keySelected.connect(lambda f: widget.set_state((0,), 0, 0))
    moves = []; widget.moveRequested.connect(lambda *pair: moves.append(pair))
    press(widget, 0); QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(1000, 30))
    QTest.keyClick(widget, Qt.Key.Key_Right); QTest.keyClick(widget, Qt.Key.Key_Left)
    assert widget.frame_at(-10) == widget.frame_at(1000) == 0 and not moves
    assert not widget.grab().isNull(); widget.close()


def test_live_language_accessibility_and_paint_never_mutate_state(strip):
    widget, _ = strip; before = widget.frames
    set_locale('en'); assert 'Reference frame 20' in widget.accessibleDescription()
    assert 'project playhead does not move' in widget.toolTip()
    set_locale('zh-CN')
    assert widget.accessibleName() == '窗口关键帧时间线'
    assert '参考帧 20' in widget.accessibleDescription() and widget.toolTip() != TIMELINE_HELP
    assert not widget.grab().isNull() and widget.frames == before and not widget.gesture_active

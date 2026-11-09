"""Real Qt edge gestures: disposable preview, one commit, stale-context guards."""
from copy import deepcopy
from fractions import Fraction

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QFocusEvent, QHideEvent, QMouseEvent
from PySide6.QtTest import QTest

from lumen.i18n import set_locale
from lumen.model import Caption, Clip, Editor, Media, Overlay, Project
from lumen.timing import ClipTimeMap
from lumen.ui import Timeline
from lumen.ui_recovery import project_fingerprint
from test_speed_ui import app, window


def mouse(widget, kind, point, *, button=Qt.MouseButton.LeftButton,
          buttons=Qt.MouseButton.LeftButton):
    event = QMouseEvent(kind, QPointF(point), QPointF(point), button, buttons,
                        Qt.KeyboardModifier.NoModifier)
    # QApplication delivery exercises event overrides and Qt's focus handling.
    from PySide6.QtWidgets import QApplication
    QApplication.sendEvent(widget, event)


def press(widget, edge='right', clip_id='c'):
    clip = next(c for c in widget.project.clips if c.id == clip_id)
    rect = widget.rect_for(clip)
    point = QPointF(rect.left() if edge == 'left' else rect.right(), rect.center().y())
    mouse(widget, QEvent.Type.MouseButtonPress, point)
    return point


def move(widget, point, delta):
    target = QPointF(point.x() + delta * widget.scale, point.y())
    mouse(widget, QEvent.Type.MouseMove, target, button=Qt.MouseButton.NoButton)
    return target


def release(widget, point):
    mouse(widget, QEvent.Type.MouseButtonRelease, point, buttons=Qt.MouseButton.NoButton)


def state(w):
    return deepcopy((w.project.to_dict(), w.editor._undo, w.editor._redo,
        w.editor.revision, w.revision, w.dirty, w.preview_revision,
        w.preview_request, w.preview_paths, w.saved_project_fingerprint))


@pytest.fixture
def timeline(app):
    widget = Timeline()
    media = Media('m', '/not-decoded.mp4', 'Media', 10, 640, 360, True, False)
    project = Project(media=[media], clips=[Clip('c', 'm', 0, 10, 5, 31)])
    widget.set_project(project, 'c')
    widget.resize(800, 390)
    widget.show()
    app.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


@pytest.mark.parametrize('edge,delta,start,duration', [('left', 3, 13, 28), ('right', -3, 10, 28)])
def test_many_moves_preview_only_then_one_undo(window, monkeypatch, edge, delta, start, duration):
    w = window
    invalidations = []
    monkeypatch.setattr(w, 'invalidate_preview_frame', lambda *a, **k: invalidations.append(True))
    monkeypatch.setattr(w, 'build_preview', lambda: pytest.fail('Drag must not render'))
    w.saved_project_fingerprint = project_fingerprint(w.project)
    before = state(w)
    p = press(w.timeline, edge)
    assert w.timeline.trim_drag is not None and w.timeline.drag is None
    for value in (1, 2, 4, 5, delta):
        target = move(w.timeline, p, value if edge == 'left' else -abs(value))
        assert state(w) == before
    target = move(w.timeline, p, delta)
    candidate = w.timeline.trim_drag.candidate
    assert (candidate.start, candidate.duration) == (start, duration)
    assert candidate is not w.project.clips[0]
    assert f'{delta:+d} frames' in w.timeline.trim_preview_text()
    assert not invalidations
    release(w.timeline, target)
    assert w.timeline.trim_drag is None
    assert (w.project.clips[0].start, w.project.clips[0].duration) == (start, duration)
    assert len(w.editor._undo) == len(before[1]) + 1
    assert w.revision == before[4] + 1 and w.dirty and invalidations == [True]
    after = deepcopy(w.project)
    w.undo(); assert w.project.to_dict() == before[0]
    w.redo(); assert w.project == after


@pytest.mark.parametrize('edge', ['left', 'right'])
@pytest.mark.parametrize('mode', ['click', 'round_zero', 'return_zero', 'invalid', 'escape', 'focus', 'deactivate', 'hide'])
def test_noop_failure_and_cancellation_preserve_entire_state(window, edge, mode):
    w = window
    # Keep a real redo branch to catch hidden history loss on previews/no-ops.
    w.editor.update_clip('c', gain_db=2); w.editor.undo(); w.refresh()
    before = state(w)
    p = press(w.timeline, edge)
    assert w.timeline.trim_drag is not None
    if mode == 'click':
        target = p
    elif mode == 'round_zero':
        target = QPointF(p.x() + .4 * w.timeline.scale, p.y())
    elif mode == 'return_zero':
        move(w.timeline, p, 3 if edge == 'left' else -3)
        target = move(w.timeline, p, 0)
    elif mode == 'invalid':
        target = move(w.timeline, p, 1000)
        assert w.timeline.trim_drag.candidate is None
        assert 'Cannot trim:' in w.timeline.trim_preview_text()
    else:
        target = move(w.timeline, p, 3 if edge == 'left' else -3)
        if mode == 'escape': QTest.keyClick(w.timeline, Qt.Key.Key_Escape)
        elif mode == 'focus': w.timeline.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
        elif mode == 'deactivate': w.timeline.event(QEvent(QEvent.Type.WindowDeactivate))
        else: w.timeline.hideEvent(QHideEvent())
        assert w.timeline.trim_drag is None
    release(w.timeline, target)
    assert state(w) == before
    assert w.timeline.trim_drag is None


@pytest.mark.parametrize('change', ['project', 'delete', 'in_place', 'selection', 'window_selection', 'revision', 'editor_revision', 'editor', 'zoom', 'selection_kind'])
def test_context_changes_reject_stale_release(window, change):
    w = window
    p = press(w.timeline)
    target = move(w.timeline, p, -3)
    assert w.timeline.trim_drag.candidate is not None
    if change == 'project':
        w.editor = Editor(deepcopy(w.project)); w.refresh()
    elif change == 'delete':
        w.project.clips.clear()
    elif change == 'in_place':
        w.project.clips[0].gain_db = 1.5
    elif change == 'selection':
        w.timeline.selection = ''
    elif change == 'window_selection':
        w.selection = ''
    elif change == 'revision':
        w.revision += 1
    elif change == 'editor_revision':
        w.editor.revision += 1
    elif change == 'editor':
        w.editor = Editor(w.project)
    elif change == 'zoom':
        w.timeline.scale = 7
    else:
        w.active_selection_type = 'effect'
    expected = state(w)
    release(w.timeline, target)
    assert state(w) == expected and w.timeline.trim_drag is None


def test_synchronous_selected_refresh_and_waveform_completion_keep_gesture(timeline, app):
    widget = timeline
    widget.selection = ''
    widget.selected.connect(lambda cid: widget.set_project(widget.project, cid))
    emitted = []
    widget.trimmed.connect(lambda *args: emitted.append(args))
    before = deepcopy(widget.project)
    p = press(widget)
    target = move(widget, p, -5)
    gesture = widget.trim_drag
    widget.set_project(widget.project, 'c')
    widget.waveforms.changed()
    widget.grab(); app.processEvents()
    assert widget.trim_drag is gesture and widget.project == before
    release(widget, target)
    assert emitted == [('c', 'right', -5)]
    assert widget.project == before  # Signal is separate from model mutation.


@pytest.mark.parametrize('change', ['replace', 'edit', 'selection_back', 'revision'])
def test_synchronous_selected_context_mutation_never_starts_stale_drag(timeline, change):
    widget = timeline
    editor = Editor(widget.project)
    widget.edit_context = lambda: (editor, 0)
    emitted = []
    widget.trimmed.connect(lambda *args: emitted.append(args))
    def mutate(cid):
        if change == 'replace': widget.set_project(deepcopy(widget.project), cid)
        elif change == 'edit': widget.project.clips[0].gain_db = 2
        elif change == 'selection_back':
            widget.selection = ''; widget.selection = cid
        else: editor.revision += 1
    widget.selected.connect(mutate)
    p = press(widget)
    assert widget.trim_drag is None
    release(widget, QPointF(p.x() - 8, p.y()))
    assert not emitted


@pytest.mark.parametrize('scale', [.25, .5, 1, 4, 20])
@pytest.mark.parametrize('track', [0, 1, 2, 3])
def test_one_frame_clip_has_deterministic_logical_edge_hits(timeline, scale, track):
    widget = timeline
    clip = widget.project.clips[0]
    clip.track = track; clip.duration = 1
    widget.project.media[0].has_audio = track >= 2
    widget.scale = scale
    rect = widget.rect_for(clip)
    assert widget.trim_edge_at(clip, rect.topLeft()) == 'left'
    assert widget.trim_edge_at(clip, rect.topRight()) == 'right'
    expected = 'left' if rect.width() <= 12 else None
    assert widget.trim_edge_at(clip, rect.center()) == expected
    for edge in ('left', 'right'):
        p = press(widget, edge)
        assert widget.trim_drag.edge == edge
        # Extending by one project frame remains one frame at every zoom.
        target = move(widget, p, -1 if edge == 'left' else 1)
        assert widget.trim_drag.candidate.duration == 2
        assert widget.trim_drag.candidate.track == track
        release(widget, target)


def test_body_move_and_ruler_seek_stay_separate(timeline):
    widget = timeline
    widget.snapping_enabled = False
    moves, trims, seeks = [], [], []
    widget.moved.connect(lambda *args: moves.append(args))
    widget.trimmed.connect(lambda *args: trims.append(args))
    widget.seeked.connect(seeks.append)
    p = widget.rect_for(widget.project.clips[0]).center()
    mouse(widget, QEvent.Type.MouseButtonPress, p)
    assert widget.trim_drag is None and widget.drag is not None
    release(widget, QPointF(p.x() + 12, p.y()))
    mouse(widget, QEvent.Type.MouseButtonPress, QPointF(128, 12))
    release(widget, QPointF(128, 12))
    assert moves == [('c', 13, 0)] and trims == [] and seeks == [10]


def test_trim_preserves_retime_opposite_edge_and_independent_anchors(window, tmp_path):
    w = window
    w.editor.trim_clip_source('c', Fraction(1, 2), 31)
    w.editor.set_clip_speed('c', Fraction(3, 2))
    w.project.captions = [Caption('caption', 10, 8, 'Unchanged')]
    w.project.overlays = [Overlay('sticker', str(tmp_path / 'sticker.png'), 'a' * 64, 12, 8)]
    w.project.clips.append(Clip('sound', 'm', 2, 12, 20, 10))
    w.refresh()
    anchors = deepcopy((w.project.captions, w.project.overlays, w.project.clips[1]))
    source_out, end = w.project.clips[0].source_out, w.project.clips[0].end
    p = press(w.timeline, 'left')
    release(w.timeline, move(w.timeline, p, 3))
    c = w.project.clips[0]
    assert c.timing == ClipTimeMap(5, source_out, Fraction(3, 2))
    assert c.end == end
    assert (w.project.captions, w.project.overlays, w.project.clips[1]) == anchors


def test_live_localization_hover_and_no_decode(timeline, monkeypatch):
    widget = timeline
    monkeypatch.setattr(widget.waveforms, 'request', lambda *_: pytest.fail('Gesture must not request decoding'))
    clip = widget.project.clips[0]
    mouse(widget, QEvent.Type.MouseMove, widget.rect_for(clip).topRight(),
          button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.NoButton)
    assert widget.cursor().shape() == Qt.CursorShape.SizeHorCursor
    p = press(widget, 'left')
    move(widget, p, 2)
    before = deepcopy(widget.project)
    set_locale('zh-CN')
    assert '修剪左边缘' in widget.trim_preview_text() and '时长 29 帧' in widget.trim_preview_text()
    assert '左右边缘' in widget.toolTip()
    set_locale('en')
    assert 'Trim left' in widget.trim_preview_text() and 'Duration 29 frames' in widget.trim_preview_text()
    assert 'whole frames' in widget.toolTip() and widget.project == before
    widget.cancel_trim()


def test_direct_noop_handler_and_close_leave_state_unchanged(window):
    w = window
    before = state(w)
    w.trim_clip_edge('c', 'right', 0)
    assert state(w) == before
    p = press(w.timeline)
    target = move(w.timeline, p, -2)
    w.closing = True
    w.close()
    release(w.timeline, target)
    assert state(w) == before and w.timeline.trim_drag is None


@pytest.mark.parametrize('failure', ['source_in', 'source_out', 'collision', 'fade'])
def test_rejected_candidates_never_emit_or_mutate(timeline, failure):
    widget = timeline
    clip = widget.project.clips[0]
    edge, delta = 'right', 300
    if failure == 'source_in': edge, delta = 'left', -6
    elif failure == 'collision':
        widget.project.clips.append(Clip('next', 'm', 0, 43, 0, 20))
        delta = 3
    elif failure == 'fade':
        clip.fade_out = 30
        edge, delta = 'left', 2
    before = deepcopy(widget.project)
    emitted = []
    widget.trimmed.connect(lambda *args: emitted.append(args))
    p = press(widget, edge)
    target = move(widget, p, delta)
    assert widget.trim_drag.error and widget.trim_drag.candidate is None
    release(widget, target)
    assert widget.project == before and emitted == []


@pytest.mark.parametrize('at_selection', [False, True])
def test_invalid_project_at_press_is_rejected_without_qt_exception(timeline, at_selection):
    widget = timeline
    def invalidate(*_): widget.project.clips[0].gain_db = float('nan')
    if at_selection: widget.selected.connect(invalidate)
    else: invalidate()
    p = press(widget)
    assert widget.trim_drag is None
    release(widget, p)

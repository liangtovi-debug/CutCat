"""Multi-clip gestures are disposable, guarded drafts with one release signal."""
from copy import deepcopy

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QFocusEvent, QHideEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lumen.i18n import set_locale
from lumen.model import Clip, Editor, Media, Project
from lumen.ui import Timeline
from test_speed_ui import app, window


def mouse(widget, kind, point, *, shift=False, button=Qt.MouseButton.LeftButton,
          buttons=Qt.MouseButton.LeftButton):
    modifiers = Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
    QApplication.sendEvent(widget, QMouseEvent(kind, QPointF(point), QPointF(point),
                                               button, buttons, modifiers))


def press(widget, clip_id='a', *, edge=None, shift=False):
    clip = next(c for c in widget.project.clips if c.id == clip_id)
    rect = widget.rect_for(clip)
    p = rect.center()
    if edge:
        p.setX(rect.left() if edge == 'left' else rect.right())
    mouse(widget, QEvent.Type.MouseButtonPress, p, shift=shift)
    return p


def move(widget, origin, delta, *, y=None):
    target = QPointF(origin.x() + delta * widget.scale, origin.y() if y is None else y)
    mouse(widget, QEvent.Type.MouseMove, target, button=Qt.MouseButton.NoButton)
    return target


def release(widget, point):
    mouse(widget, QEvent.Type.MouseButtonRelease, point, buttons=Qt.MouseButton.NoButton)


def model_state(widget):
    editor = widget.editor_for_test
    return deepcopy((widget.project.to_dict(), editor._undo, editor._redo,
                     editor.revision, editor.session_id))


@pytest.fixture
def timeline(app, monkeypatch):
    set_locale('en')
    widget = Timeline()
    media = Media('m', '/never-decoded.mp4', 'Media', 20, 640, 360, True, True)
    editor = Editor(Project(media=[media], clips=[
        Clip('a', 'm', 0, 10, 5, 10), Clip('b', 'm', 1, 30, 30, 10),
        Clip('sound', 'm', 2, 15, 0, 10), Clip('blocker', 'm', 0, 55, 80, 10),
    ]))
    # A real redo branch catches accidental preview history changes.
    editor.update_clip('a', gain_db=1); editor.undo()
    widget.editor_for_test = editor
    widget.ui_revision = 0
    widget.ui_selection_generation = 0
    widget.edit_context = lambda: (widget.editor_for_test, widget.ui_revision)
    widget.selection_context = lambda: widget.ui_selection_generation
    widget.set_project(editor.project, 'a', ('a', 'b'))
    widget.snapping_enabled = False
    monkeypatch.setattr(widget.waveforms, 'request', lambda *_: None)
    widget.resize(900, 390)
    widget.show(); app.processEvents()
    widget.group_signals, widget.move_signals, widget.select_signals, widget.toggle_signals = [], [], [], []
    widget.group_moved.connect(lambda *args: widget.group_signals.append(args))
    widget.moved.connect(lambda *args: widget.move_signals.append(args))
    widget.selected.connect(widget.select_signals.append)
    widget.selection_toggled.connect(widget.toggle_signals.append)
    yield widget
    widget.close(); widget.deleteLater(); app.processEvents()


@pytest.mark.parametrize('edge', [None, 'left', 'right'])
@pytest.mark.parametrize('clip_id', ['a', 'sound'])
def test_shift_only_toggles_even_at_clip_edges(timeline, edge, clip_id):
    widget = timeline
    before = model_state(widget)
    p = press(widget, clip_id, edge=edge, shift=True)
    assert widget.toggle_signals == [clip_id]
    assert not widget.select_signals and widget.drag is None and widget.trim_drag is None
    release(widget, move(widget, p, 4))
    assert not widget.group_signals and not widget.move_signals
    assert model_state(widget) == before


def test_selected_body_preserves_group_then_one_horizontal_release(timeline):
    widget = timeline
    before = model_state(widget)
    p = press(widget, 'b')
    assert widget.selected_clip_ids == ('a', 'b') and widget.selection == 'a'
    assert not widget.select_signals and widget.trim_drag is None
    for delta in (1, 3, 5, 7):
        target = move(widget, p, delta, y=200)
        assert [(c.start, c.track) for c in widget.drag.candidates] == [(10 + delta, 0), (30 + delta, 1)]
        assert all(c is not widget.project.clips[i] for i, c in enumerate(widget.drag.candidates))
        assert model_state(widget) == before
    assert '2 clips' in widget.move_preview_text() and '+7 frames' in widget.move_preview_text()
    release(widget, target)
    assert widget.group_signals == [(('a', 'b'), 7)] and not widget.move_signals
    assert not widget.select_signals and widget.selected_clip_ids == ('a', 'b')
    assert model_state(widget) == before and widget.drag is None


def test_group_click_collapses_only_on_release(timeline):
    widget = timeline
    p = press(widget, 'b')
    assert widget.selected_clip_ids == ('a', 'b') and not widget.select_signals
    release(widget, p)
    assert widget.selected_clip_ids == ('b',) and widget.selection == 'b'
    assert widget.select_signals == ['b'] and not widget.group_signals


def test_unselected_body_replaces_group_on_press(timeline):
    widget = timeline
    p = press(widget, 'sound')
    assert widget.selected_clip_ids == ('sound',) and widget.selection == 'sound'
    assert widget.select_signals == ['sound'] and widget.drag.clip_ids == ('sound',)
    release(widget, move(widget, p, 3, y=214))
    assert widget.move_signals == [('sound', 18, 3)] and not widget.group_signals


def test_unselected_no_drag_emits_selection_once(timeline):
    widget = timeline
    p = press(widget, 'sound')
    release(widget, p)
    assert widget.select_signals == ['sound']


def test_window_unselected_body_starts_and_commits_move(window):
    w = window
    w.project.clips.extend([Clip('b', 'm', 1, 50, 0, 20), Clip('other', 'm', 0, 100, 0, 20)])
    w.refresh(); w.set_selected_clip_ids(('c', 'b'), anchor='c')
    w.timeline.snapping_enabled = False
    before = len(w.editor._undo)
    p = press(w.timeline, 'other')
    assert w.selected_clip_ids == ('other',) and w.timeline.drag is not None
    release(w.timeline, move(w.timeline, p, 4))
    assert next(c for c in w.project.clips if c.id == 'other').start == 104
    assert len(w.editor._undo) == before + 1


@pytest.mark.parametrize('edge', ['left', 'right'])
def test_window_group_to_other_edge_starts_and_commits_trim(window, edge):
    w = window
    w.project.clips.extend([Clip('b', 'm', 1, 50, 0, 20), Clip('other', 'm', 0, 100, 0, 20)])
    w.refresh(); w.set_selected_clip_ids(('c', 'b'), anchor='c')
    before = len(w.editor._undo)
    p = press(w.timeline, 'other', edge=edge)
    assert w.selected_clip_ids == ('other',) and w.timeline.trim_drag is not None
    release(w.timeline, move(w.timeline, p, 2 if edge == 'left' else -2))
    assert next(c for c in w.project.clips if c.id == 'other').duration == 18
    assert len(w.editor._undo) == before + 1


def test_window_real_shift_group_drag_is_one_undo_and_one_invalidation(window, monkeypatch):
    from test_trim_edge_ui import state
    w = window
    w.project.clips.append(Clip('b', 'm', 1, 60, 0, 20))
    w.refresh()
    p = press(w.timeline, 'b', shift=True); release(w.timeline, p)
    assert w.selected_clip_ids == ('c', 'b')
    w.editor.update_clip('c', gain_db=2); w.editor.undo(); w.refresh()
    w.timeline.snapping_enabled = False
    invalidations = []
    monkeypatch.setattr(w, 'invalidate_preview_frame', lambda *a, **k: invalidations.append(True))
    monkeypatch.setattr(w, 'build_preview', lambda: pytest.fail('A draft must not render'))
    before = state(w)
    p = press(w.timeline, 'b')
    for delta in (2, 7, 4, 5):
        target = move(w.timeline, p, delta, y=200)
        assert state(w) == before and not invalidations
    release(w.timeline, target)
    assert [(c.start, c.track) for c in w.project.clips] == [(15, 0), (65, 1)]
    assert len(w.editor._undo) == len(before[1]) + 1 and w.revision == before[4] + 1
    assert invalidations == [True] and w.selected_clip_ids == ('c', 'b')
    after = deepcopy(w.project)
    w.undo(); assert w.project.to_dict() == before[0] and w.selected_clip_ids == ('c', 'b')
    w.redo(); assert w.project == after and w.selected_clip_ids == ('c', 'b')


def test_window_close_cancels_hidden_group_draft(window):
    from test_trim_edge_ui import state
    w = window
    w.project.clips.append(Clip('b', 'm', 1, 60, 0, 20))
    w.refresh(); w.set_selected_clip_ids(('c', 'b'), anchor='c')
    w.timeline.snapping_enabled = False
    before = state(w)
    p = press(w.timeline, 'b')
    target = move(w.timeline, p, 5)
    assert w.timeline.drag is not None
    w.closing = True; w.close()
    release(w.timeline, target)
    assert w.timeline.drag is None and state(w) == before


@pytest.mark.parametrize('edge', ['left', 'right'])
def test_unmodified_edge_collapses_group_and_only_trims(timeline, edge):
    widget = timeline
    trims = []
    widget.trimmed.connect(lambda *args: trims.append(args))
    p = press(widget, 'a', edge=edge)
    assert widget.selected_clip_ids == ('a',) and widget.select_signals == ['a']
    assert widget.trim_drag is not None and widget.drag is None
    target = move(widget, p, 2 if edge == 'left' else -2)
    release(widget, target)
    assert trims == [('a', edge, 2 if edge == 'left' else -2)]
    assert not widget.group_signals and not widget.move_signals


def test_mixed_role_group_ignores_vertical_motion(timeline):
    widget = timeline
    widget.selected_clip_ids = ('a', 'sound')
    p = press(widget)
    target = move(widget, p, 12, y=220)
    assert [(c.id, c.start, c.track) for c in widget.drag.candidates] == [('a', 22, 0), ('sound', 27, 2)]
    release(widget, target)
    assert widget.group_signals == [(('a', 'sound'), 12)]


def test_one_snap_delta_applies_to_group(timeline):
    widget = timeline
    widget.snapping_enabled = True
    p = press(widget)
    target = move(widget, p, 34)
    assert widget.drag.delta == 35  # Anchor end snaps from 54 to blocker start 55.
    assert [c.start for c in widget.drag.candidates] == [45, 65]
    release(widget, target)
    assert widget.group_signals == [(('a', 'b'), 35)]


def test_snap_excludes_every_selected_member(timeline):
    widget = timeline
    widget.selected_clip_ids = ('a', 'b', 'sound')
    widget.snapping_enabled = True
    p = press(widget)
    target = move(widget, p, 9)
    # Anchor end 29 must not snap to selected b's old start 30.
    assert widget.drag.delta == 9
    release(widget, target)
    assert widget.group_signals == [(('a', 'b', 'sound'), 9)]


@pytest.mark.parametrize('delta', [-11, 40])
def test_invalid_group_preview_keeps_attempted_geometry(timeline, delta):
    widget = timeline
    before = model_state(widget)
    p = press(widget)
    target = move(widget, p, delta)
    assert widget.drag.error and 'Cannot move:' in widget.move_preview_text()
    assert [c.start for c in widget.drag.candidates] == [10 + delta, 30 + delta]
    widget.grab()  # The rejected candidate must remain paintable, not disappear.
    assert model_state(widget) == before
    release(widget, target)
    assert not widget.group_signals and not widget.move_signals
    assert model_state(widget) == before


def test_invalid_preview_can_recover_to_valid_delta(timeline):
    widget = timeline
    p = press(widget)
    move(widget, p, 40)
    assert widget.drag.error
    target = move(widget, p, 20)
    assert not widget.drag.error
    release(widget, target)
    assert widget.group_signals == [(('a', 'b'), 20)]


@pytest.mark.parametrize('mode', ['round_zero', 'return_zero', 'escape', 'focus', 'deactivate', 'hide', 'no_button'])
def test_noop_and_cancel_never_move_or_touch_history(timeline, mode):
    widget = timeline
    before = model_state(widget)
    p = press(widget)
    target = move(widget, p, 3)
    if mode == 'round_zero':
        widget.cancel_move(); p = press(widget)
        target = move(widget, p, .4)
    elif mode == 'return_zero':
        target = move(widget, p, 0)
    elif mode == 'escape':
        QTest.keyClick(widget, Qt.Key.Key_Escape)
    elif mode == 'focus':
        widget.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    elif mode == 'deactivate':
        widget.event(QEvent(QEvent.Type.WindowDeactivate))
    elif mode == 'hide':
        widget.hideEvent(QHideEvent())
    else:
        mouse(widget, QEvent.Type.MouseMove, target,
              button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.NoButton)
    release(widget, target)
    assert not widget.group_signals and not widget.move_signals
    assert widget.drag is None and model_state(widget) == before


@pytest.mark.parametrize('change', ['project', 'in_place', 'delete_member', 'selection',
    'ids', 'ids_back', 'generation', 'ui_revision', 'editor_revision', 'editor_session',
    'editor', 'zoom', 'zoom_back'])
@pytest.mark.parametrize('single', [False, True])
def test_full_context_guards_reject_stale_releases(timeline, change, single):
    widget = timeline
    if single:
        widget.selected_clip_ids = ('a',)
    p = press(widget)
    target = move(widget, p, 3)
    assert widget.drag is not None
    if change == 'project':
        widget.set_project(deepcopy(widget.project), 'a', widget.selected_clip_ids)
    elif change == 'in_place':
        widget.project.clips[0].gain_db = 2
    elif change == 'delete_member':
        widget.project.clips.pop(1)
    elif change == 'selection':
        widget.selection = ''
    elif change == 'ids':
        widget.selected_clip_ids = ('sound',)
    elif change == 'ids_back':
        ids = widget.selected_clip_ids
        widget.selected_clip_ids = ('sound',); widget.selected_clip_ids = ids
    elif change == 'generation':
        widget.ui_selection_generation += 1
    elif change == 'ui_revision':
        widget.ui_revision += 1
    elif change == 'editor_revision':
        widget.editor_for_test.revision += 1
    elif change == 'editor_session':
        widget.editor_for_test.session_id = 'replacement-session'
    elif change == 'editor':
        widget.editor_for_test = Editor(widget.project)
    elif change == 'zoom':
        widget.scale = 8
    else:
        widget.scale = 8; widget.scale = 4
    expected = model_state(widget)
    release(widget, target)
    assert widget.drag is None and not widget.group_signals and not widget.move_signals
    assert model_state(widget) == expected


@pytest.mark.parametrize('change', ['replace', 'in_place', 'selection_back', 'ids_back', 'revision'])
def test_selected_listener_mutation_does_not_start_stale_move(timeline, change):
    widget = timeline
    widget.selection = ''; widget.selected_clip_ids = ()
    def mutate(cid):
        if change == 'replace': widget.set_project(deepcopy(widget.project), cid)
        elif change == 'in_place': widget.project.clips[0].gain_db = 2
        elif change == 'selection_back': widget.selection = ''; widget.selection = cid
        elif change == 'ids_back': widget.selected_clip_ids = (); widget.selected_clip_ids = (cid,)
        else: widget.ui_revision += 1
    widget.selected.connect(mutate)
    p = press(widget)
    assert widget.drag is None
    release(widget, QPointF(p.x() + 12, p.y()))
    assert not widget.move_signals and not widget.group_signals


def test_refresh_and_waveform_completion_keep_preview_and_cache(timeline, monkeypatch, app):
    widget = timeline
    before = model_state(widget)
    monkeypatch.setattr(widget.waveforms, 'request', lambda *_: pytest.fail('Gesture must not decode waveforms'))
    p = press(widget)
    target = move(widget, p, 5)
    gesture = widget.drag
    widget.waveforms.changed()
    widget.grab(); app.processEvents()
    assert widget.drag is gesture and model_state(widget) == before
    # Normal UI refresh may request cached waveform visibility, but cannot
    # cancel a draft whose exact project, selection and editor stayed current.
    monkeypatch.setattr(widget.waveforms, 'request', lambda *_: None)
    widget.set_project(widget.project, 'a', ('a', 'b'))
    assert widget.drag is gesture
    release(widget, target)
    assert widget.group_signals == [(('a', 'b'), 5)]


def test_live_localization_preserves_group_draft(timeline):
    widget = timeline
    before = model_state(widget)
    p = press(widget)
    target = move(widget, p, 5)
    gesture = widget.drag
    set_locale('zh-CN')
    assert '2 个片段' in widget.move_preview_text() and '+5 帧' in widget.move_preview_text()
    assert '按住 Shift' in widget.toolTip()
    assert widget.drag is gesture and model_state(widget) == before
    set_locale('en')
    assert '2 clips' in widget.move_preview_text()
    release(widget, target)
    assert widget.group_signals == [(('a', 'b'), 5)]


def test_selection_order_does_not_change_project_order_preview(timeline):
    widget = timeline
    widget.set_project(widget.project, 'b', ('b', 'a'))
    p = press(widget, 'b')
    target = move(widget, p, 3)
    assert [(c.id, c.start) for c in widget.drag.candidates] == [('a', 13), ('b', 33)]
    release(widget, target)
    assert widget.group_signals == [(('b', 'a'), 3)]


@pytest.mark.parametrize('track,y,expected', [(0, 110, 1), (0, 190, 0), (2, 214, 3), (2, 60, 2)])
def test_single_move_retains_same_role_track_changes(timeline, track, y, expected):
    widget = timeline
    cid = 'a' if track < 2 else 'sound'
    widget.set_project(widget.project, cid, (cid,))
    clip = next(c for c in widget.project.clips if c.id == cid)
    start = clip.start
    p = press(widget, cid)
    release(widget, move(widget, p, 5, y=y))
    assert widget.move_signals == [(cid, start + 5, expected)]


def test_right_click_never_toggles_or_moves(timeline):
    widget = timeline
    point = widget.rect_for(widget.project.clips[0]).center()
    mouse(widget, QEvent.Type.MouseButtonPress, point, shift=True,
          button=Qt.MouseButton.RightButton, buttons=Qt.MouseButton.RightButton)
    assert not widget.toggle_signals and not widget.select_signals and widget.drag is None

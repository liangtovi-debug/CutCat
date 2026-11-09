"""Joint edge gestures: complete-pair draft, one commit, cancellation and fences."""
from copy import deepcopy

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QFocusEvent, QHideEvent
from PySide6.QtTest import QTest

from lumen.model import AVLink, Clip, Editor
from lumen.ui_linked_av import LINK_TRIM_HELP
from test_linked_av_ui import app, window, pair_window, link
from test_clip_copy_paste_ui import state
from test_clip_multiselection_timeline import mouse, press, move, release


def full_state(w):
    return deepcopy((state(w), w.selection, tuple(w.selected_clip_ids), w.selection_generation))


@pytest.mark.parametrize('member', ['video-id', 'audio-id'])
@pytest.mark.parametrize('edge,delta', [('left', 3), ('left', -3), ('right', 3), ('right', -3)])
def test_both_edges_roles_and_directions_are_pure_drafts_then_one_commit(pair_window, app, monkeypatch, member, edge, delta):
    w = pair_window; link(w, app); w.select_clip(member)
    invalidations = []; monkeypatch.setattr(w, 'invalidate_preview_frame', lambda *a, **k: invalidations.append(True))
    monkeypatch.setattr(w, 'build_preview', lambda: pytest.fail('Trim gesture must not render'))
    monkeypatch.setattr(w.timeline.waveforms, 'request', lambda *_: None)
    before = full_state(w); original = deepcopy(w.project); depth = len(w.editor._undo)
    p = press(w.timeline, member, edge=edge)
    assert w.timeline.trim_drag is not None and w.timeline.drag is None
    assert set(w.timeline.trim_drag.clip_ids) == {'video-id', 'audio-id'}
    for step in (1, 2, delta):
        target = move(w.timeline, p, step)
        assert full_state(w) == before and not invalidations
        assert len(w.timeline.trim_drag.candidates) == len(w.timeline.trim_preview_rects()) == 2
        assert all(c is not live for c in w.timeline.trim_drag.candidates for live in w.project.clips)
    assert 'Linked V+A' in w.timeline.trim_preview_text()
    assert w.timeline.accessibleDescription() == w.timeline.trim_preview_text()
    release(w.timeline, target); release(w.timeline, target)
    a, v = w.editor._clip('audio-id'), w.editor._clip('video-id')
    assert a.start == v.start == (5 + delta if edge == 'left' else 5)
    assert a.duration == v.duration == (30 - delta if edge == 'left' else 30 + delta)
    assert a.timing == v.timing and w.project.av_links == original.av_links
    assert w.editor._clip('other-id') == original.clips[-1]
    assert w.selection == member and set(w.selected_clip_ids) == {'video-id', 'audio-id'}
    assert len(w.editor._undo) == depth + 1 and invalidations == [True]
    after = deepcopy(w.project); w.undo(); assert w.project == original
    w.redo(); assert w.project == after and not w.test_warnings


@pytest.mark.parametrize('mode', ['click', 'round_zero', 'return_zero', 'escape', 'focus', 'deactivate', 'hide', 'button_loss'])
@pytest.mark.parametrize('edge', ['left', 'right'])
def test_noop_and_cancellation_keep_redo_and_all_state(pair_window, app, edge, mode):
    w = pair_window; link(w, app)
    w.editor.update_clip('audio-id', gain_db=-3); w.editor.undo(); w.refresh()
    before = full_state(w); p = press(w.timeline, 'video-id', edge=edge)
    assert w.timeline.trim_drag is not None
    target = p
    if mode == 'round_zero': target = QPointF(p.x() + .4*w.timeline.scale, p.y())
    elif mode != 'click':
        target = move(w.timeline, p, 2)
        if mode == 'return_zero': target = move(w.timeline, p, 0)
        elif mode == 'escape': QTest.keyClick(w.timeline, Qt.Key.Key_Escape)
        elif mode == 'focus': w.timeline.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
        elif mode == 'deactivate': w.timeline.event(QEvent(QEvent.Type.WindowDeactivate))
        elif mode == 'hide': w.timeline.hideEvent(QHideEvent())
        else: mouse(w.timeline, QEvent.Type.MouseMove, target, button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.NoButton)
    release(w.timeline, target)
    assert full_state(w) == before and w.timeline.trim_drag is None and not w.test_warnings


@pytest.mark.parametrize('change', ['role', 'role_aba', 'selection', 'selection_aba', 'editor_aba', 'reload', 'link', 'lock', 'zoom', 'revision', 'window_revision', 'in_place', 'cancel'])
def test_stale_release_cannot_edit_new_context(pair_window, app, change):
    w = pair_window; link(w, app)
    p = press(w.timeline, 'video-id', edge='left'); target = move(w.timeline, p, 2)
    assert w.timeline.trim_drag.candidate is not None
    if change == 'role': w.select_linked_role('audio')
    elif change == 'role_aba': w.select_linked_role('audio'); w.select_linked_role('video')
    elif change == 'selection': w.select_clip('other-id')
    elif change == 'selection_aba': w.select_clip('other-id'); w.select_clip('video-id')
    elif change == 'editor_aba': w.editor.update_clip('audio-id', gain_db=-3); w.editor.undo()
    elif change == 'reload': w.editor = Editor(deepcopy(w.project)); w.refresh()
    elif change == 'link': w.editor.unlink_av_clips(['audio-id'])
    elif change == 'lock': w.editor.set_track_edit_lock(2, True)
    elif change == 'zoom': w.timeline.scale += 1
    elif change == 'revision': w.editor.revision += 1
    elif change == 'window_revision': w.revision += 1
    elif change == 'in_place': w.editor._clip('audio-id').gain_db = -4
    else: w.timeline.cancel_trim()
    expected = full_state(w); release(w.timeline, target)
    assert full_state(w) == expected and w.timeline.trim_drag is None


@pytest.mark.parametrize('selection', [('other-id',), ('video-id', 'audio-id', 'other-id')])
def test_unselected_or_extra_selection_does_not_silently_collapse_to_pair(pair_window, app, selection):
    w = pair_window; link(w, app); w.set_selected_clip_ids(selection, anchor=selection[0]); w.refresh()
    before = full_state(w); p = press(w.timeline, 'video-id', edge='right')
    assert w.timeline.trim_drag is None and w.timeline.drag is None
    release(w.timeline, move(w.timeline, p, 2))
    assert full_state(w) == before and w.statusBar().currentMessage() == LINK_TRIM_HELP


def test_multiple_pairs_and_shift_never_start_trim(pair_window, app):
    w = pair_window; link(w, app)
    w.project.clips.extend([Clip('v2', 'media-id', 1, 50, 12, 20, source_audio_enabled=False),
                            Clip('a2', 'media-id', 3, 50, 12, 20)])
    w.editor.link_av_clips('v2', 'a2'); w.set_selected_clip_ids(('video-id', 'v2'), anchor='video-id'); w.refresh()
    before = full_state(w); p = press(w.timeline, 'audio-id', edge='left')
    release(w.timeline, move(w.timeline, p, 2)); assert full_state(w) == before
    w.select_clip('video-id'); before_project = deepcopy(w.project)
    p = press(w.timeline, 'audio-id', edge='left', shift=True)
    release(w.timeline, move(w.timeline, p, 2))
    assert w.timeline.trim_drag is None and w.project == before_project


@pytest.mark.parametrize('mode', ['partner_collision', 'fade', 'source_end', 'negative_start'])
def test_invalid_attempt_draws_both_outlines_and_rejects_atomically(pair_window, app, mode):
    w = pair_window; link(w, app); edge, delta = 'right', 5
    if mode == 'partner_collision': w.project.clips.append(Clip('block', 'media-id', 2, 36, 60, 10))
    elif mode == 'fade': edge, delta = 'left', 29
    elif mode == 'source_end': delta = 200
    else: edge, delta = 'left', -6
    w.refresh(); before = full_state(w); p = press(w.timeline, 'video-id', edge=edge)
    target = move(w.timeline, p, delta)
    assert w.timeline.trim_drag.candidate is None and w.timeline.trim_drag.error
    assert len(w.timeline.trim_preview_rects()) == 2
    assert f'{delta:+d} frames' in w.timeline.trim_preview_text()
    assert 'Cannot trim linked V+A' in w.timeline.trim_preview_text()
    release(w.timeline, target); assert full_state(w) == before and not w.test_warnings


@pytest.mark.parametrize('track', [0, 2])
def test_either_locked_role_prevents_trim(pair_window, app, track):
    w = pair_window; link(w, app); w.editor.set_track_edit_lock(track, True); w.refresh()
    before = full_state(w); member = 'audio-id' if track == 0 else 'video-id'
    p = press(w.timeline, member, edge='right'); release(w.timeline, move(w.timeline, p, 2))
    assert full_state(w) == before and w.timeline.trim_drag is None


def test_release_revalidates_even_same_pointer_position(pair_window, app, monkeypatch):
    w = pair_window; link(w, app)
    p = press(w.timeline, 'video-id', edge='right'); target = move(w.timeline, p, 2)
    calls = []; preview = w.editor.preview_trim_linked_av
    def counted(*args): calls.append(args); return preview(*args)
    monkeypatch.setattr(w.editor, 'preview_trim_linked_av', counted)
    release(w.timeline, target)
    assert calls and not w.test_warnings


@pytest.mark.parametrize('change', ['cancel', 'role_aba', 'editor_aba', 'reload'])
def test_synchronous_selection_listener_cannot_start_stale_trim(pair_window, app, change):
    w = pair_window; link(w, app)
    def changed(_):
        if change == 'cancel': w.timeline.cancel_gestures()
        elif change == 'role_aba': w.timeline.selection = 'video-id'; w.timeline.selection = 'audio-id'
        elif change == 'editor_aba': w.editor.update_clip('audio-id', gain_db=-4); w.editor.undo()
        else: w.editor = Editor(deepcopy(w.project)); w.refresh()
    w.timeline.selected.connect(changed)
    try:
        p = press(w.timeline, 'audio-id', edge='left'); expected = full_state(w)
        release(w.timeline, move(w.timeline, p, 2))
        assert full_state(w) == expected and w.timeline.trim_drag is None
    finally: w.timeline.selected.disconnect(changed)

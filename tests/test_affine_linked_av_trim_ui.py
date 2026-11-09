"""Affine paired-edge gestures reuse the existing timeline and commit lifecycle."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QFocusEvent, QHideEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from lumen.i18n import set_locale, tr
from lumen.model import AVLink, AV_LINK_AFFINE_ALIGNMENT, Clip, Editor
from lumen.timing import ClipTimeMap
from lumen.ui_linked_av import LINK_HELP, LINK_TRIM_HELP
from test_linked_av_ui import app, window, pair_window
from test_linked_av_trim_ui import full_state
from test_clip_multiselection_timeline import mouse, press, move, release
from test_shortcuts_dispatch import activate


@pytest.fixture
def affine_window(pair_window, app, monkeypatch):
    w = pair_window
    monkeypatch.setattr(w.timeline.waveforms, 'request', lambda *_: None)
    v, a = w.editor._clip('video-id'), w.editor._clip('audio-id')
    v.time_map = ClipTimeMap(Fraction(12), Fraction(167, 4))
    a.start, a.source_in, a.duration = 3, Fraction(10), 34
    a.time_map = ClipTimeMap(Fraction(10), Fraction(87, 2))
    w.project.av_links = [AVLink('affine-pair', v.id, a.id, v.media_id, AV_LINK_AFFINE_ALIGNMENT)]
    w.project.validate(); w.refresh(); w.select_clip(v.id)
    activate(app, w, w.timeline)
    return w


@pytest.mark.parametrize('member', ['video-id', 'audio-id'])
@pytest.mark.parametrize('edge,delta', [('left', 3), ('left', -3), ('right', 3), ('right', -3)])
def test_own_edges_and_tails_pure_draft_one_commit_undo_redo(affine_window, member, edge, delta, monkeypatch):
    w = affine_window; w.select_clip(member)
    invalidations = []; monkeypatch.setattr(w, 'invalidate_preview_frame', lambda *a, **k: invalidations.append(True))
    monkeypatch.setattr(w, 'build_preview', lambda: pytest.fail('Trim gesture must not render'))
    before = full_state(w); old = deepcopy(w.project); depth = len(w.editor._undo)
    p = press(w.timeline, member, edge=edge)
    for step in (1, 2, delta):
        target = move(w.timeline, p, step)
        assert full_state(w) == before and not invalidations
        assert len(w.timeline.trim_drag.candidates) == len(w.timeline.trim_preview_rects()) == 2
        assert all(c is not live for c in w.timeline.trim_drag.candidates for live in w.project.clips)
    amounts = (30-delta, 34-delta) if edge == 'left' else (30+delta, 34+delta)
    text = w.timeline.trim_preview_text()
    assert text.startswith(f'V {amounts[0]}f · A {amounts[1]}f · {delta:+d} frames')
    assert 'J/L offsets preserved' in text and 'audio output may change' in text
    assert text == w.timeline.toolTip() == w.timeline.accessibleDescription()
    release(w.timeline, target); release(w.timeline, target)
    for old_clip in old.clips[:2]:
        current = w.editor._clip(old_clip.id)
        assert current.start == old_clip.start + (delta if edge == 'left' else 0)
        assert current.duration == old_clip.duration + (-delta if edge == 'left' else delta)
        assert current.source_in == old_clip.source_in + (delta if edge == 'left' else 0)
        assert current.source_out == old_clip.source_out + (delta if edge == 'right' else 0)
        assert current.duration-(current.source_out-current.source_in) == old_clip.duration-(old_clip.source_out-old_clip.source_in)
        assert current.audio_mix_anchor == old_clip.audio_mix_anchor
        assert current.source_audio_enabled == old_clip.source_audio_enabled
    v, a = w.editor._clip('video-id'), w.editor._clip('audio-id')
    assert (a.start-v.start, a.end-v.end, a.duration-v.duration) == (-2, 2, 4)
    assert w.project.av_links == old.av_links and w.editor._clip('other-id') == old.clips[-1]
    assert len(w.editor._undo) == depth+1 and invalidations == [True]
    assert w.selection == member and set(w.selected_clip_ids) == {'video-id', 'audio-id'}
    after = deepcopy(w.project); w.undo(); assert w.project == old
    w.redo(); assert w.project == after and not w.test_warnings


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('reverse_order', [False, True])
def test_canonical_summary_ignores_active_role_and_clip_candidate_order(affine_window, locale, reverse_order):
    w = affine_window; set_locale(locale)
    if reverse_order: w.project.clips.reverse(); w.refresh()
    texts = []
    for member in ('video-id', 'audio-id'):
        w.select_clip(member); p = press(w.timeline, member, edge='left'); move(w.timeline, p, 3)
        assert w.timeline.trim_drag.error == ''
        w.timeline.trim_drag.candidates = tuple(reversed(w.timeline.trim_drag.candidates))
        text = w.timeline.trim_preview_text(); texts.append(text)
        assert text.startswith('V 27f · A 31f' if locale == 'en' else 'V 27帧 · A 31帧')
        assert '+3' in text and 'J/L' in text
        w.timeline.cancel_gestures(); release(w.timeline, p)
    assert texts[0] == texts[1]
    help_text = tr(LINK_HELP)
    assert ('paired-edge trim' in help_text and 'Joint roll still requires' in help_text) if locale == 'en' else ('配对边缘修剪' in help_text and '联合滚动仍要求' in help_text)
    set_locale('en')


@pytest.mark.parametrize('mode', ['click', 'round_zero', 'return_zero', 'escape', 'focus', 'deactivate', 'hide', 'button_loss'])
@pytest.mark.parametrize('member', ['video-id', 'audio-id'])
def test_noop_cancel_keeps_redo_and_state(affine_window, mode, member):
    w = affine_window; w.select_clip(member)
    w.editor.update_clip('audio-id', gain_db=-3); w.editor.undo(); w.refresh()
    before = full_state(w); p = press(w.timeline, member, edge='left'); target = p
    assert w.timeline.trim_drag is not None
    if mode == 'round_zero': target = QPointF(p.x()+.4*w.timeline.scale, p.y())
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


@pytest.mark.parametrize('change', ['role', 'role_aba', 'selection', 'selection_aba', 'editor_aba', 'reload', 'link', 'membership', 'lock', 'zoom', 'revision', 'window_revision', 'in_place', 'cancel'])
def test_stale_release_cannot_edit_new_affine_context(affine_window, change):
    w = affine_window; p = press(w.timeline, 'video-id', edge='left'); target = move(w.timeline, p, 2)
    assert w.timeline.trim_drag.candidate is not None
    if change == 'role': w.select_linked_role('audio')
    elif change == 'role_aba': w.select_linked_role('audio'); w.select_linked_role('video')
    elif change == 'selection': w.select_clip('other-id')
    elif change == 'selection_aba': w.select_clip('other-id'); w.select_clip('video-id')
    elif change == 'editor_aba': w.editor.update_clip('audio-id', gain_db=-3); w.editor.undo()
    elif change == 'reload': w.editor = Editor(deepcopy(w.project)); w.refresh()
    elif change == 'link': w.editor.unlink_av_clips(['audio-id'])
    elif change == 'membership': w.project.av_links[0] = replace(w.project.av_links[0], audio_clip_id='other-id')
    elif change == 'lock': w.editor.set_track_edit_lock(2, True)
    elif change == 'zoom': w.timeline.scale += 1
    elif change == 'revision': w.editor.revision += 1
    elif change == 'window_revision': w.revision += 1
    elif change == 'in_place': w.editor._clip('audio-id').gain_db = -4
    else: w.timeline.cancel_trim()
    # Membership corruption is intentionally invalid; don't serialize/normalize it.
    expected = deepcopy((w.project, w.editor._undo, w.editor._redo, w.editor.revision, w.revision))
    release(w.timeline, target)
    assert (w.project, w.editor._undo, w.editor._redo, w.editor.revision, w.revision) == expected
    assert w.timeline.trim_drag is None


@pytest.mark.parametrize('mode', ['partner_collision', 'fade', 'source_end', 'negative_start'])
def test_rejected_delta_draws_both_own_outlines_without_clamping(affine_window, mode):
    w = affine_window; edge, delta = 'right', 5
    if mode == 'partner_collision': w.project.clips.append(Clip('block', 'media-id', 2, 38, 60, 10))
    elif mode == 'fade':
        w.editor._clip('audio-id').fade_out = 6; edge, delta = 'left', 29
    elif mode == 'source_end': delta = 200
    else: edge, delta = 'left', -4
    w.refresh(); before = full_state(w); p = press(w.timeline, 'video-id', edge=edge)
    target = move(w.timeline, p, delta)
    assert w.timeline.trim_drag.error and w.timeline.trim_drag.candidate is None
    rects = w.timeline.trim_preview_rects(); assert len(rects) == 2
    for clip, rect in zip(w.project.clips[:2], rects):
        own = w.timeline.rect_for(clip)
        if edge == 'left': own.setLeft(own.left()+delta*w.timeline.scale)
        else: own.setRight(own.right()+delta*w.timeline.scale)
        assert rect == own.normalized()
    assert f'{delta:+d} frames' in w.timeline.trim_preview_text()
    release(w.timeline, target); assert full_state(w) == before and not w.test_warnings


@pytest.mark.parametrize('track', [0, 2])
def test_either_locked_role_prevents_affine_gesture(affine_window, track):
    w = affine_window; w.editor.set_track_edit_lock(track, True); w.refresh()
    before = full_state(w); member = 'audio-id' if track == 0 else 'video-id'
    p = press(w.timeline, member, edge='right'); release(w.timeline, move(w.timeline, p, 2))
    assert full_state(w) == before and w.timeline.trim_drag is None


@pytest.mark.parametrize('selection', [('other-id',), ('video-id', 'audio-id', 'other-id')])
def test_extra_or_unselected_does_not_collapse_to_affine_pair(affine_window, selection):
    w = affine_window; w.set_selected_clip_ids(selection, anchor=selection[0]); w.refresh()
    before = full_state(w); p = press(w.timeline, 'video-id', edge='right')
    release(w.timeline, move(w.timeline, p, 2))
    assert full_state(w) == before and w.timeline.trim_drag is None
    assert w.statusBar().currentMessage() == LINK_TRIM_HELP


def test_affine_release_revalidates_same_pointer(affine_window, monkeypatch):
    w = affine_window; p = press(w.timeline, 'audio-id', edge='right'); target = move(w.timeline, p, 2)
    calls = []; preview = w.editor.preview_trim_linked_av
    def counted(*args): calls.append(args); return preview(*args)
    monkeypatch.setattr(w.editor, 'preview_trim_linked_av', counted)
    release(w.timeline, target)
    assert calls == [('audio-id', 'right', 2)] and not w.test_warnings


@pytest.mark.parametrize('change', ['cancel', 'role_aba', 'editor_aba', 'reload'])
def test_synchronous_listener_cannot_start_affine_stale_trim(affine_window, change):
    w = affine_window
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


def test_foreign_modal_focus_cancels_existing_affine_gesture(affine_window, app):
    w = affine_window; p = press(w.timeline, 'video-id', edge='left'); target = move(w.timeline, p, 2)
    before = full_state(w); dialog = QDialog(w); dialog.setModal(True)
    try:
        dialog.show(); dialog.activateWindow(); dialog.setFocus(); app.processEvents()
        assert w.timeline.trim_drag is None
        release(w.timeline, target); assert full_state(w) == before
    finally: dialog.close(); dialog.deleteLater(); app.processEvents()


def test_horizontal_scroll_keeps_compact_label_inside_visible_content(affine_window, app):
    w = affine_window; w.timeline.setMinimumWidth(2400); app.processEvents()
    scroll = w.timeline_scroll.horizontalScrollBar(); assert scroll.maximum() > 0
    scroll.setValue(100); app.processEvents()
    p = press(w.timeline, 'video-id', edge='left'); target = move(w.timeline, p, 3)
    text = w.timeline.trim_preview_text(); label = w.timeline.trim_preview_label_rect(text, w.timeline.rect_for(w.timeline.trim_drag.candidate))
    visible = w.timeline.visibleRegion().boundingRect()
    assert label.left() >= visible.left() and label.right() <= visible.right()
    painted = w.timeline.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(label.width()-16))
    assert 'V 27f' in painted and 'A 31f' in painted and '+3' in painted
    before = full_state(w); w.timeline.cancel_gestures(); release(w.timeline, target)
    assert full_state(w) == before

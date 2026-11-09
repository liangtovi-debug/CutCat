"""Joint split command parity, captured intent and cancellable worker lifecycle.

Media decoding is deliberately mocked here; receipt/model application and Qt
job delivery are real. Actual CFR/RGB/PCM cases belong to the media gate.
"""
from copy import deepcopy
from fractions import Fraction
import threading

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

from lumen.i18n import set_locale
from lumen.model import Clip, Editor
from lumen.source_viewer import SourceIdentity
from lumen.timing import ClipTimeMap
import lumen.clip_copy_paste as copy_module
from lumen.clip_copy_paste import ClipDependency
from lumen.linked_av_split import LinkedAVSplitVerification
import lumen.ui_linked_av_split as ui
from lumen.source_viewer import SourcePreviewBackend
from test_linked_av_ui import app, window, pair_window, link
from test_clip_copy_paste_ui import state, settle
from test_shortcuts_dispatch import activate, CTRL, repeat


@pytest.fixture
def split_window(pair_window, app):
    w = pair_window; link(w, app); w.seek_frame(20)
    activate(app, w, w.timeline)
    return w


@pytest.fixture
def verified_worker(monkeypatch):
    calls = []
    identity = SourceIdentity('/test-proof.mov', (), 'test-source-sha', 'dependency-signature')
    monkeypatch.setattr(copy_module, '_dependencies', lambda *args, **kwargs:
        (ClipDependency('media', 'media-id', identity),))
    def proof(self, project, clip_id, frame, **kwargs):
        calls.append((threading.get_ident(), clip_id, frame))
        return SourceIdentity('/test-proof.mov', (), 'test-source-sha', 'test-signature')
    monkeypatch.setattr(SourcePreviewBackend, 'verify_linked_av_split_source', proof)
    return calls


def open_dialog(w, app):
    activate(app, w, w.timeline)
    d = w.open_linked_av_split()
    assert d is not None and d.isVisible()
    return d


def preview(w, app, d):
    d.preview(); settle(w, app)
    assert d.preview_receipt is not None and d.apply_button.isEnabled(), d.impact.text()


def assert_two_off_gui_verifications(calls, role, frame, gui_thread_id):
    # Preview and Apply start separate Jobs. Python may recycle a completed
    # worker's identifier, so neither equality nor inequality is contractual.
    assert [(clip_id, cut) for _, clip_id, cut in calls] == [(role, frame)] * 2
    assert all(thread_id != gui_thread_id for thread_id, _, _ in calls)


def test_verification_assertion_accepts_worker_id_reuse_or_change_not_gui_or_wrong_payload():
    role, frame, gui_thread_id = 'audio-id', 20, 100
    for worker_ids in ((101, 101), (101, 102)):
        assert_two_off_gui_verifications(
            [(thread_id, role, frame) for thread_id in worker_ids], role, frame, gui_thread_id)
    invalid_calls = (
        [],
        [(101, role, frame)],
        [(101, role, frame)] * 3,
        [(gui_thread_id, role, frame), (102, role, frame)],
        [(101, role, frame), (gui_thread_id, role, frame)],
        [(101, 'video-id', frame), (102, role, frame)],
        [(101, role, frame), (102, 'video-id', frame)],
        [(101, role, frame + 1), (102, role, frame)],
        [(101, role, frame), (102, role, frame + 1)],
    )
    for calls in invalid_calls:
        with pytest.raises(AssertionError):
            assert_two_off_gui_verifications(calls, role, frame, gui_thread_id)


@pytest.mark.parametrize('entry', ['button', 'menu', 'shortcut', 'direct'])
@pytest.mark.parametrize('role', ['video-id', 'audio-id'])
def test_common_split_entry_opens_staged_preview_for_either_role(split_window, app, entry, role):
    w = split_window; w.select_clip(role); activate(app, w, w.timeline)
    before = state(w); depth = len(w.editor._undo)
    assert w.can_split_selected() and w.split_clip_button.isEnabled()
    assert w.shortcuts.actions['edit.split'].isEnabled()
    if entry == 'button': w.split_clip_button.click()
    elif entry == 'menu': w.shortcuts.actions['edit.split'].trigger()
    elif entry == 'shortcut': QTest.keyClick(w.timeline, Qt.Key.Key_K, CTRL)
    else: w.split_selected()
    app.processEvents(); d = w.linked_av_split_dialog
    assert d is not None and d.frame == 20 and d.clip_id == role
    assert d.preview_button.isEnabled() and not d.apply_button.isEnabled()
    assert not w.jobs and state(w) == before and len(w.editor._undo) == depth
    assert 'video-id' in d.source.text() and 'audio-id' in d.source.text()
    assert w.open_linked_av_split() is d
    assert not w.test_warnings


@pytest.mark.parametrize('role', ['video-id', 'audio-id'])
def test_verified_split_is_one_history_selects_new_pair_preserves_role(split_window, app, verified_worker, role):
    w = split_window; w.select_clip(role)
    before = deepcopy(w.project); depth = len(w.editor._undo)
    d = open_dialog(w, app); plan = d.plan
    preview(w, app, d)
    assert w.project == before and d.plan is plan
    d.apply(); d.apply(); settle(w, app)
    assert d._closed_flag and w.linked_av_split_dialog is None
    assert len(w.editor._undo) == depth + 1 and len(w.project.av_links) == 2
    assert set(w.selected_clip_ids) == set(plan.new_ids[:2])
    assert w.selection == plan.new_ids[0 if role == 'video-id' else 1]
    assert w.editor._clip('video-id').duration == w.editor._clip('audio-id').duration == 15
    assert_two_off_gui_verifications(verified_worker, role, 20, threading.get_ident())
    assert w.preview_revision != w.revision and not w.test_warnings
    after = deepcopy(w.project); w.undo(); assert w.project == before
    w.redo(); assert w.project == after


@pytest.mark.parametrize('frame', [0, 5, 35, 70])
def test_endpoint_outside_or_no_interior_cut_disabled_everywhere(split_window, app, frame):
    w = split_window
    w.editor._clip('other-id').duration = 85  # Keep endpoint/outside playheads in the project.
    w.refresh(); w.seek_frame(frame); before = state(w)
    assert not w.can_split_selected() and not w.split_clip_button.isEnabled()
    assert not w.shortcuts.actions['edit.split'].isEnabled()
    w.split_selected(); w.shortcuts.actions['edit.split'].trigger()
    assert w.linked_av_split_dialog is None and state(w) == before


@pytest.mark.parametrize('blocked', ['extra', 'multiple', 'locked_video', 'locked_audio', 'retime', 'partial', 'mixed_fps', 'cross_fade'])
def test_complete_pair_scope_and_model_qualification_are_common(split_window, app, blocked):
    w = split_window
    if blocked == 'extra': w.set_selected_clip_ids(('video-id', 'audio-id', 'other-id'))
    elif blocked == 'multiple':
        w.project.clips.extend([Clip('v2','media-id',1,60,0,30,source_audio_enabled=False),
                               Clip('a2','media-id',3,60,0,30)])
        w.editor.link_av_clips('v2','a2'); w.set_selected_clip_ids(('video-id','v2'))
    elif blocked.startswith('locked_'): w.editor.set_track_edit_lock(0 if blocked == 'locked_video' else 2, True)
    elif blocked in ('retime', 'partial'):
        for clip in w.project.clips[:2]:
            clip.time_map = ClipTimeMap(12, 72, 2) if blocked == 'retime' else ClipTimeMap(12, Fraction(167,4))
    elif blocked == 'mixed_fps': w.project.media[0].fps_num = 24
    else: w.editor._clip('audio-id').fade_in = 16
    w.refresh(); before = state(w)
    if blocked == 'partial':
        # Same test role, intentionally changed contract: exact 1x tails now qualify.
        assert w.can_split_selected() and w.split_clip_button.isEnabled()
        assert w.shortcuts.actions['edit.split'].isEnabled()
        w.split_selected(); assert w.linked_av_split_dialog is not None and state(w) == before
        return
    assert not w.can_split_selected() and not w.split_clip_button.isEnabled()
    assert not w.shortcuts.actions['edit.split'].isEnabled()
    w.split_selected(); assert w.linked_av_split_dialog is None and state(w) == before


@pytest.mark.parametrize('change', ['seek', 'seek_aba', 'role', 'role_aba', 'selection_aba', 'revision_aba', 'replace', 'player', 'in_place'])
def test_captured_intent_expires_on_context_change_without_replanning(split_window, app, verified_worker, change):
    w = split_window; d = open_dialog(w, app); plan = d.plan
    preview(w, app, d)
    if change == 'seek': w.seek_frame(21)
    elif change == 'seek_aba': w.seek_frame(21); w.seek_frame(20)
    elif change == 'role': w.select_linked_role('audio')
    elif change == 'role_aba': w.select_linked_role('audio'); w.select_linked_role('video')
    elif change == 'selection_aba': w.select_clip('other-id'); w.select_clip('video-id')
    elif change == 'revision_aba': w.editor.update_clip('audio-id', gain_db=-5); w.editor.undo()
    elif change == 'replace': w.editor = Editor(deepcopy(w.project)); w.refresh()
    elif change == 'player': w.replace_preview_player()
    else: w.editor._clip('audio-id').gain_db = -5
    before = state(w); d.check_current(); d.apply(); d.preview()
    assert d._stale or d._closed_flag
    assert not d.apply_button.isEnabled() or d._closed_flag
    assert d.plan is plan and state(w) == before and not w.jobs


@pytest.mark.parametrize('dismiss', ['cancel', 'escape', 'close'])
def test_dismissal_discards_preview_and_reopen_gets_new_intent(split_window, app, verified_worker, dismiss):
    w = split_window; before = state(w); d = open_dialog(w, app)
    preview(w, app, d); old = d.plan
    if dismiss == 'cancel': d.cancel_button.click()
    elif dismiss == 'escape': QTest.keyClick(d, Qt.Key.Key_Escape)
    else: d.close()
    assert d._closed_flag and not d.timer.isActive() and state(w) == before
    new = open_dialog(w, app)
    assert new.plan is not old and new.plan.new_ids != old.new_ids
    assert new.preview_receipt is None and not new.apply_button.isEnabled()


@pytest.mark.parametrize('focus', ['text', 'view', 'ime', 'modal'])
def test_direct_and_shortcut_split_obey_focus_ime_guards(split_window, app, focus):
    w = split_window; before = state(w); dialog = None
    target = w.timeline
    if focus == 'text':
        target = QLineEdit(w); target.show()
    elif focus == 'view': target = w.bin
    elif focus == 'modal':
        dialog = QDialog(w); dialog.setModal(True); dialog.show(); app.processEvents()
    if focus != 'modal': activate(app, w, target)
    if focus == 'ime':
        QApplication.sendEvent(w.timeline, QInputMethodEvent('composition', [])); app.processEvents()
    w.split_selected(); QTest.keyClick(target, Qt.Key.Key_K, CTRL); app.processEvents()
    assert w.linked_av_split_dialog is None and state(w) == before
    if focus == 'ime': QApplication.sendEvent(w.timeline, QInputMethodEvent('', [])); app.processEvents()
    if dialog is not None: dialog.reject()


@pytest.mark.parametrize('cancel_method', ['dialog', 'global'])
def test_busy_repeated_preview_cancel_and_late_result_cannot_apply(split_window, app, monkeypatch, verified_worker, cancel_method):
    w = split_window; before = state(w); d = open_dialog(w, app); plan = d.plan
    entered, release = threading.Event(), threading.Event()
    original = ui.verify_linked_av_split
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(ui, 'verify_linked_av_split', blocked)
    try:
        d.preview(); assert entered.wait(2)
        d.preview(); d.apply(); w.split_selected()
        assert len(w.jobs) == 1 and not w.can_split_selected() and not w.split_clip_button.isEnabled()
        assert not w.shortcuts.actions['edit.split'].isEnabled()
        if cancel_method == 'global': w.cancel_jobs()
        else: d.cancel_work()
        d.preview(); assert len(w.jobs) == 1 and d.plan is plan
    finally: release.set()
    settle(w, app)
    assert state(w) == before and d.preview_receipt is None
    assert d.preview_button.isEnabled() and not d.apply_button.isEnabled()
    # Retry the same intent, not new UUIDs to rescue a rejected ordering.
    d.preview(); settle(w, app)
    assert d.plan is plan and d.preview_receipt is not None


def test_failure_clears_apply_and_keeps_same_plan(split_window, app, monkeypatch, verified_worker):
    w = split_window; d = open_dialog(w, app); plan = d.plan
    preview(w, app, d); before = state(w)
    def fail(*args, **kwargs): raise ValueError('Linked A/V split dependencies changed; preview again')
    monkeypatch.setattr(ui, 'verify_linked_av_split', fail)
    d.apply(); settle(w, app)
    assert state(w) == before and d.preview_receipt is None and not d.apply_button.isEnabled()
    assert d.plan is plan and 'dependencies changed' in d.impact.text()
    assert w.progress_bar.value() == 0 and len(w.test_warnings) == 1


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('width', [390, 780])
def test_locale_narrow_layout_plain_text_and_accessible_actions(split_window, app, locale, width):
    w = split_window; set_locale(locale); d = open_dialog(w, app)
    d.fit_available_screen(QRect(0,0,width,720)); app.processEvents()
    assert d.width() <= width - 32 and d.height() <= 656
    assert d.source.textFormat() == Qt.TextFormat.PlainText
    assert d.scope.wordWrap() and d.limits.wordWrap() and d.check_note.wordWrap()
    assert d.preview_button.accessibleName() and d.apply_button.accessibleName()
    assert ('30 秒' if locale == 'zh-CN' else '30 seconds') in d.limits.text()
    assert d.plan is not None and not w.jobs


def test_unlinked_independent_split_is_still_immediate(split_window, app):
    w = split_window; w.select_clip('other-id'); activate(app, w, w.timeline)
    before = deepcopy(w.project); depth = len(w.editor._undo)
    w.split_selected()
    assert w.linked_av_split_dialog is None and len(w.editor._undo) == depth + 1
    assert len(w.project.clips) == len(before.clips) + 1
    assert w.project.av_links == before.av_links
    assert not w.test_warnings


@pytest.mark.parametrize('interruption', ['cancel', 'global_cancel', 'close', 'seek_aba', 'role_aba'])
def test_blocked_apply_late_success_cannot_cross_cancel_or_context_boundary(
        split_window, app, monkeypatch, verified_worker, interruption):
    w = split_window; d = open_dialog(w, app); plan = d.plan
    preview(w, app, d)
    receipt = LinkedAVSplitVerification(plan, d.preview_receipt.dependencies,
                                       d.preview_receipt.source_identity, 'apply')
    entered, release = threading.Event(), threading.Event()
    def completed_before_cancel(*args, **kwargs):
        entered.set(); assert release.wait(5)
        # Simulate a successful result already produced before cancellation.
        return receipt
    monkeypatch.setattr(ui, 'verify_linked_av_split', completed_before_cancel)
    try:
        d.apply(); assert entered.wait(2)
        d.apply(); assert len(w.jobs) == 1
        if interruption == 'cancel': d.cancel_work()
        elif interruption == 'global_cancel': w.cancel_jobs()
        elif interruption == 'close': d.reject()
        elif interruption == 'seek_aba': w.seek_frame(21); w.seek_frame(20)
        else: w.select_linked_role('audio'); w.select_linked_role('video')
        before = state(w)
        d.apply(); d.preview(); assert len(w.jobs) == 1
    finally: release.set()
    settle(w, app)
    assert state(w) == before and len(w.project.av_links) == 1
    assert d.preview_receipt is None and d.plan is plan and not w.test_warnings


def test_joint_split_requires_paused_playback_but_independent_split_unchanged(split_window, app, monkeypatch):
    from PySide6.QtMultimedia import QMediaPlayer
    w = split_window; before = state(w)
    playback = [QMediaPlayer.PlaybackState.PlayingState]
    monkeypatch.setattr(w.player, 'playbackState', lambda: playback[0])
    w._preview_receiver.state(playback[0])
    assert not w.can_split_selected() and not w.split_clip_button.isEnabled()
    assert not w.shortcuts.actions['edit.split'].isEnabled()
    assert 'Pause playback' in w.split_clip_button.toolTip()
    w.split_selected(); assert w.linked_av_split_dialog is None and state(w) == before
    w.select_clip('other-id'); assert w.can_split_selected()  # Existing immediate split is not narrowed.
    w.select_clip('video-id'); playback[0] = QMediaPlayer.PlaybackState.PausedState
    w._preview_receiver.state(playback[0]); assert w.can_split_selected()
    d = open_dialog(w, app); assert not d._stale
    playback[0] = QMediaPlayer.PlaybackState.PlayingState; w.player.playbackStateChanged.emit(playback[0])
    playback[0] = QMediaPlayer.PlaybackState.PausedState; w.player.playbackStateChanged.emit(playback[0])
    app.processEvents()
    assert d._stale and not d.apply_button.isEnabled() and not w.jobs


@pytest.mark.parametrize('feedback', ['verified', 'failed', 'stale', 'cancelled'])
@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_new_terminal_feedback_is_visible_once_without_polling_scroll_jump(
        split_window, app, verified_worker, feedback, locale):
    w = split_window; set_locale(locale); d = open_dialog(w, app)
    d.fit_available_screen(QRect(0,0,390,720)); d.resize(d.width(),360); app.processEvents()
    if feedback == 'failed':
        d._failed('Linked A/V split source range is incomplete or variable-rate')
    elif feedback == 'stale': w.seek_frame(21)
    else:
        preview(w, app, d)
        if feedback == 'cancelled': d.cancel_work()
    app.processEvents()
    target = d.impact if feedback == 'failed' else d.status
    assert d._feedback_state[0] == feedback
    top = target.mapTo(d.scroll.viewport(), QPoint(0,0))
    assert d.scroll.viewport().rect().contains(QRect(top.x(),top.y(),target.width(),target.height()))
    assert d._feedback_reveal_pending is None
    bar = d.scroll.verticalScrollBar(); assert bar.maximum() > 0
    user_scroll = bar.maximum(); bar.setValue(user_scroll)
    for _ in range(4): d.check_current(); app.processEvents()
    assert bar.value() == user_scroll  # No recurring 150ms poll can fight the user.
    d.reject(); app.processEvents(); assert d._bound_player is None


@pytest.mark.parametrize('next_action', ['close', 'stale', 'retry'])
def test_queued_feedback_reveal_cannot_scroll_obsolete_state(split_window, app, verified_worker, monkeypatch, next_action):
    w = split_window; d = open_dialog(w, app)
    d.fit_available_screen(QRect(0,0,390,720)); app.processEvents()
    revealed = []; bar = d.scroll.verticalScrollBar(); original = bar.setValue
    monkeypatch.setattr(bar, 'setValue', lambda value:
        (revealed.append((value,d._feedback_state[0])), original(value))[1])
    d._failed('Linked A/V split source range is incomplete or variable-rate')
    if next_action == 'close': d.reject()
    elif next_action == 'stale': w.seek_frame(21)
    else: d.preview()
    app.processEvents(); settle(w, app)
    assert revealed == ([] if next_action == 'close' else [(0,'stale' if next_action == 'stale' else 'verified')])


def test_oversized_error_reveals_first_line_not_hidden_midpoint(split_window, app):
    d = open_dialog(split_window, app)
    d.fit_available_screen(QRect(0,0,390,720)); app.processEvents()
    d._failed('Decoder detail: ' + 'a long diagnostic line ' * 150); app.processEvents()
    assert d.impact.height() > d.scroll.viewport().height()
    top = d.impact.mapTo(d.scroll.viewport(), QPoint(0,0))
    assert d.scroll.viewport().rect().contains(QRect(top.x(),top.y(),d.impact.width(),d.impact.fontMetrics().lineSpacing()))
    assert d._feedback_reveal_pending is None


@pytest.mark.parametrize('user_interrupt', ['none', 'scrollbar', 'key', 'close'])
def test_queued_feedback_top_reveal_respects_user_scroll_and_closed_dialog(
        split_window, app, monkeypatch, user_interrupt):
    from PySide6.QtWidgets import QAbstractSlider
    d = open_dialog(split_window, app); d.fit_available_screen(QRect(0,0,390,720))
    d.resize(d.width(),360); app.processEvents()
    bar = d.scroll.verticalScrollBar(); assert bar.maximum() > 0
    bar.setValue(bar.maximum())
    revealed = []; original = bar.setValue
    monkeypatch.setattr(bar,'setValue',lambda value:(revealed.append(value),original(value))[1])
    d._failed('Linked A/V split source range is incomplete or variable-rate')
    assert d._feedback_reveal_pending is not None
    if user_interrupt == 'scrollbar': bar.triggerAction(QAbstractSlider.SliderAction.SliderToMaximum)
    elif user_interrupt == 'key': QTest.keyClick(bar,Qt.Key.Key_Down)
    elif user_interrupt == 'close': d.reject()
    app.processEvents()
    assert d._feedback_reveal_pending is None
    assert revealed == ([0] if user_interrupt == 'none' else [])
    if user_interrupt == 'none':
        top = d.impact.mapTo(d.scroll.viewport(),QPoint(0,0))
        assert d.scroll.viewport().rect().contains(QRect(top.x(),top.y(),d.impact.width(),d.impact.height()))


def test_stale_status_hides_previous_oversized_error(split_window,app):
    w = split_window; d = open_dialog(w,app)
    d.fit_available_screen(QRect(0,0,390,720)); d.resize(d.width(),360); app.processEvents()
    d._failed('Decoder detail: ' + 'a long diagnostic line ' * 150); app.processEvents()
    w.seek_frame(21); app.processEvents()
    assert d._stale and not d.impact.isVisible() and not d.impact.text()
    top = d.status.mapTo(d.scroll.viewport(),QPoint(0,0))
    assert d.scroll.viewport().rect().contains(QRect(top.x(),top.y(),d.status.width(),d.status.height()))

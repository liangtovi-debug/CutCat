"""Headless staged transition UI safety; decoding is tested by render tests."""
from copy import deepcopy
import time
import threading

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit
from lumen.model import Clip, Editor, Project
from lumen.ui_video_transition import VideoTransitionDialog, selected_pair, transition_rect
from test_speed_ui import app, window


def settle(dialog):
    deadline = time.monotonic() + 5
    while dialog.job is not None and time.monotonic() < deadline:
        QTest.qWait(10)
    assert dialog.job is None, "Transition preflight did not finish"



@pytest.fixture
def pair(window, monkeypatch):
    import lumen.video_transition_render as render
    monkeypatch.setattr(render, 'validate_transition_sources', lambda *_: None)
    import lumen.video_transition_sources as sources
    monkeypatch.setattr(sources, 'prepare_transition_source_certificate', lambda *args, **kwargs: ())
    monkeypatch.setattr(sources, 'verify_transition_source_identities', lambda *_: None)
    w = window
    w.editor = Editor(Project(media=w.project.media, clips=[
        Clip('left', 'm', 0, 0, 20, 30), Clip('right', 'm', 0, 30, 70, 30)]))
    w.refresh(); w.select_clip('left'); w.toggle_clip_selection('right')
    return w


def test_staged_apply_cancel_repeat_undo_redo(pair):
    w = pair; before = deepcopy(w.project); revision = w.revision
    dialog = VideoTransitionDialog(w); dialog.duration.setValue(5)
    assert '[28, 33)' in dialog.window_note.text()
    assert 'left post-roll 3, right pre-roll 2' in dialog.window_note.text()
    assert w.project == before
    dialog.apply_settings(); settle(dialog)
    assert w.revision == revision + 1 and len(w.editor._undo) == 1
    assert w.project.video_transitions[0].duration_frames == 5
    assert w.active_selection_type == 'transition' and not w.selected_clip_ids
    dialog.apply_settings(); settle(dialog); assert len(w.editor._undo) == 1
    after = deepcopy(w.project)
    w.undo(); assert w.project == before
    w.redo(); assert w.project == after
    dialog = VideoTransitionDialog(w, w.project.video_transitions[0])
    dialog.duration.setValue(8); dialog.reject(); dialog.apply_settings(); settle(dialog)
    assert w.project == after


def test_edit_transition_is_separate_and_noop_has_no_undo(pair):
    w = pair; t = w.editor.add_video_transition('left', 'right', 8); w.changed(); w.select_video_transition(t.id)
    before = len(w.editor._undo)
    dialog = VideoTransitionDialog(w, t); dialog.apply_settings(); settle(dialog)
    assert len(w.editor._undo) == before
    dialog = VideoTransitionDialog(w, t); dialog.duration.setValue(9); dialog.apply_settings(); settle(dialog)
    assert len(w.editor._undo) == before + 1
    assert w.project.video_transitions[0].duration_frames == 9


@pytest.mark.parametrize('change', ['selection', 'multi', 'revision', 'project', 'editor', 'session', 'direct', 'closing'])
def test_stale_dialog_cannot_apply(pair, change):
    w = pair; dialog = VideoTransitionDialog(w)
    if change == 'selection': w.select_clip('left')
    elif change == 'multi': w.toggle_clip_selection('right'); w.toggle_clip_selection('right')
    elif change == 'revision': w.revision += 1
    elif change == 'project': w.editor.project = deepcopy(w.project)
    elif change == 'editor': w.editor = Editor(deepcopy(w.project))
    elif change == 'session': w.editor.session_id += '-new'
    elif change == 'direct': w.project.name = 'Different'
    elif change == 'closing': w.closing = True
    before = deepcopy(w.project); undo = deepcopy(w.editor._undo)
    dialog.apply_settings(); settle(dialog)
    assert w.project == before and w.editor._undo == undo
    assert not dialog.apply_button.isEnabled()
    dialog.reject()


def test_pair_selection_never_silently_guesses(pair):
    w = pair
    w.select_clip('left')
    with pytest.raises(ValueError, match='exactly two'): selected_pair(w)
    w.toggle_clip_selection('right'); assert [c.id for c in selected_pair(w)] == ['left', 'right']
    w.project.clips[1].start += 1
    with pytest.raises(ValueError, match='adjacent'): selected_pair(w)


def test_source_handles_validation_and_duplicate_are_staged(pair):
    w = pair; w.project.clips[1].source_in = 0
    d = VideoTransitionDialog(w)
    assert not d.apply_button.isEnabled() and 'source handles' in d.status.text()
    d.reject()
    w.project.clips[1].source_in = 70
    t = w.editor.add_video_transition('left', 'right', 8); w.changed()
    d = VideoTransitionDialog(w)
    assert not d.apply_button.isEnabled() and 'Duplicate' in d.status.text()
    d.reject()


def test_marker_selection_and_delete_remove_relation_only(pair, app):
    w = pair; t = w.editor.add_video_transition('left', 'right', 8); w.changed()
    w.show(); app.processEvents()
    rect = transition_rect(w.timeline, t)
    QTest.mouseClick(w.timeline, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    assert w.selected_video_transition().id == t.id
    assert not w.selected_clip_ids
    before = deepcopy(w.project.clips); undo = len(w.editor._undo)
    w.delete_active_selection()
    assert not w.project.video_transitions and w.project.clips == before
    assert len(w.editor._undo) == undo + 1
    w.undo(); assert w.project.video_transitions


def test_delete_blocked_in_text_bin_and_modal(pair, app):
    w = pair; t = w.editor.add_video_transition('left', 'right', 8); w.changed(); w.select_video_transition(t.id)
    w.show(); app.processEvents()
    field = QLineEdit(w.centralWidget()); field.show(); field.setFocus(); app.processEvents()
    assert field.hasFocus()
    w.delete_active_selection()
    assert w.project.video_transitions
    w.bin.setFocus(); app.processEvents(); w.delete_active_selection()
    assert w.project.video_transitions
    dialog = VideoTransitionDialog(w, t); dialog.setModal(True); dialog.show(); app.processEvents()
    w.delete_active_selection(); assert w.project.video_transitions
    dialog.reject()


def test_new_editor_does_not_inherit_transition_selection(pair):
    w = pair; t = w.editor.add_video_transition('left', 'right', 8); w.changed(); w.select_video_transition(t.id)
    w.editor = Editor(deepcopy(w.project)); w.refresh()
    assert w.selected_video_transition() is None
    assert not w.timeline.transition_selection


def test_delete_keyboard_repeat_and_ime_guards(pair, app):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    w = pair; t = w.editor.add_video_transition('left', 'right', 8); w.changed(); w.select_video_transition(t.id)
    w.show(); w.activateWindow(); w.timeline.setFocus(); app.processEvents()
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier, '', True, 1)
    app.sendEvent(w.timeline, event); app.processEvents()
    assert w.project.video_transitions
    w.shortcuts._composing = w.timeline
    w.delete_active_selection(); assert w.project.video_transitions
    w.shortcuts._composing = None
    QTest.keyClick(w.timeline, Qt.Key.Key_Delete); app.processEvents()
    assert not w.project.video_transitions and len(w.project.clips) == 2


def test_decoded_handle_rejection_has_no_partial_edit(pair, monkeypatch):
    import lumen.video_transition_render as render
    def reject(*args, **kwargs):
        raise ValueError('decoded handle failure')
    import lumen.video_transition_sources as sources
    monkeypatch.setattr(sources, 'prepare_transition_source_certificate', reject)
    w = pair; before = deepcopy(w.project); revision = w.revision
    d = VideoTransitionDialog(w); d.apply_settings(); settle(d)
    assert w.project == before and w.revision == revision and not w.editor._undo
    assert 'decoded handle failure' in d.status.text()
    d.apply_settings(); settle(d); assert w.project == before and not w.editor._undo
    d.reject()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_about_discloses_video_only_transition_limits(pair, monkeypatch, locale):
    from PySide6.QtWidgets import QMessageBox
    from lumen.i18n import set_locale
    set_locale(locale)
    messages = []
    from lumen.ui_help import HelpDialog
    monkeypatch.setattr(HelpDialog, 'exec', lambda dialog: messages.append(dialog.body.toPlainText()))
    pair.show_help()
    text = messages[-1]
    if locale == 'en':
        assert 'Cross Dissolve: video-only' in text and 'verified real source handles' in text
        assert 'CFR sources' in text and 'Audio still cuts at the original boundary' in text
        assert 'Not implemented yet: general transitions' in text
        assert 'Not implemented yet: transitions,' not in text
        assert 'active stabilization are not supported on transition endpoints' in text
    else:
        assert '交叉溶解' in text and '真实源素材余量' in text and '恒定帧率（CFR）' in text
        assert '音频仍在原切点硬切' in text and '尚未实现：通用转场' in text
        assert '非默认画中画合成、已启用的片段特效或防抖' in text
    assert '{brand}' not in text


def test_marker_pick_exits_multiselect_without_clip_mutation(pair, app):
    w = pair; transition = w.editor.add_video_transition('left', 'right', 8); w.changed()
    assert set(w.selected_clip_ids) == {'left', 'right'}
    before = deepcopy(w.project); revision = w.revision
    w.show(); app.processEvents()
    QTest.mouseClick(w.timeline, Qt.MouseButton.LeftButton,
                    pos=transition_rect(w.timeline, transition).center().toPoint())
    assert w.selected_video_transition().id == transition.id and not w.selected_clip_ids
    assert w.project == before and w.revision == revision


def _pump_until(predicate, deadline, message):
    # This is a bounded liveness check, NOT a 70 ms latency benchmark. Qt may
    # coalesce expired timers while initial layout/paint occupies the GUI loop.
    # Check the deadline BEFORE accepting a predicate after a delayed callback.
    while True:
        assert time.monotonic() < deadline, message
        if predicate():
            return
        QTest.qWait(5)


def _exercise_async_preflight(pair, monkeypatch, ending, *, after_entry=None):
    import lumen.video_transition_preflight as preflight
    real = preflight.validate_video_transition_request
    entered = threading.Event(); release = threading.Event()
    observed_cancel = threading.Event(); watchdog_expired = threading.Event()
    gui_thread = threading.get_ident(); validator_threads = []
    def delayed(request, cancel=None):
        validator_threads.append(threading.get_ident())
        entered.set()
        while not release.wait(.005):
            if cancel.is_set():
                observed_cancel.set()
                raise ValueError('canceled test preflight')
        # A broken synchronous dispatch must fail finitely, not deadlock the
        # test process or obtain a valid certificate when the watchdog fires.
        if watchdog_expired.is_set():
            raise ValueError('test preflight watchdog expired')
        return real(request, cancel=cancel)
    def expire():
        watchdog_expired.set(); release.set()
    monkeypatch.setattr(preflight, 'validate_video_transition_request', delayed)
    w = pair; before = deepcopy(w.project); undo = len(w.editor._undo)
    d = VideoTransitionDialog(w); d.show()
    beats = []; premature_mutations = []
    def heartbeat():
        if (entered.is_set() and not release.is_set() and d.job is not None
                and d.job.isRunning()):
            if w.project != before:
                premature_mutations.append(True)
            beats.append(time.monotonic())
    timer = QTimer(d); timer.setInterval(10); timer.timeout.connect(heartbeat); timer.start()
    watchdog = threading.Timer(3, expire); watchdog.daemon = True
    watchdog.start(); deadline = time.monotonic() + 2
    try:
        d.apply_settings(); d.apply_settings()
        # Synchronize actual validator entry, not merely job construction.
        # An already-entered synchronous mutant is diagnosed by thread identity
        # before the deadline check, after its independent watchdog unblocks it.
        if not entered.is_set():
            _pump_until(entered.is_set, deadline, 'Transition worker entry deadline exceeded')
        assert len(validator_threads) == 1 and validator_threads[0] != gui_thread, \
            'Transition validator must run on a non-GUI thread'
        assert d.job is not None and d.job.isRunning() and not d.apply_button.isEnabled()
        baseline = len(beats)
        if after_entry is not None:
            QTimer.singleShot(0, after_entry)
        _pump_until(lambda: len(beats) >= baseline + 2, deadline,
                    'Transition GUI heartbeat deadline exceeded')
        assert not watchdog_expired.is_set() and not release.is_set()
        assert d.job is not None and d.job.isRunning() and w.project == before
        assert not premature_mutations, 'Project mutated while preflight was held'
        if ending == 'cancel': QTest.mouseClick(d.cancel_button, Qt.MouseButton.LeftButton)
        elif ending == 'close': d.close()
        elif ending == 'selection': w.select_clip('left')
        elif ending == 'session': w.editor.session_id += '-new'
        elif ending == 'project': w.editor.project = deepcopy(w.project)
        elif ending == 'revision': w.revision += 1
        elif ending == 'editor': w.editor = Editor(deepcopy(w.project))
        elif ending == 'direct':
            w.project.name = 'Changed while checking'; before = deepcopy(w.project)
        if ending not in ('success', 'cancel', 'close'):
            d.refresh_draft()
        release.set(); settle(d)
        if ending == 'success':
            assert len(w.project.video_transitions) == 1 and len(w.editor._undo) == undo + 1
        else:
            assert w.project == before and len(w.editor._undo) == undo
    finally:
        # Cancel BEFORE releasing a held worker, including assertion failures;
        # a test failure must not accidentally permit a late project commit.
        d.reject(); release.set(); watchdog.cancel(); watchdog.join(timeout=1)
        assert not watchdog.is_alive(), 'Test watchdog did not terminate'
        if d.job is not None:
            assert d.job.wait(5000), 'Transition worker did not terminate'
        settle(d)
        timer.stop()


@pytest.mark.parametrize('ending', ['cancel', 'close', 'selection', 'session', 'project', 'revision', 'editor', 'direct', 'success'])
def test_async_preflight_keeps_qt_responsive_and_discards_stale_results(pair, monkeypatch, ending):
    _exercise_async_preflight(pair, monkeypatch, ending)


def test_async_preflight_liveness_survives_delayed_qt_paint(pair, monkeypatch):
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QWidget
    observed = []
    class SlowPaint(QWidget):
        def paintEvent(self, event):
            started = time.monotonic(); time.sleep(.09)
            observed.append((time.monotonic() - started, threading.get_ident()))
            super().paintEvent(event)
    def paint_delay():
        # A real offscreen QWidget paint event, not merely a sleep labelled as
        # painting. render() dispatches it synchronously inside this GUI event.
        probe = SlowPaint(); probe.resize(16, 16)
        try:
            probe.render(QPixmap(probe.size()))
        finally:
            probe.close()
    _exercise_async_preflight(pair, monkeypatch, 'success', after_entry=paint_delay)
    assert len(observed) == 1 and observed[0][0] >= .09
    assert observed[0][1] == threading.get_ident()


def test_async_preflight_liveness_rejects_gui_deadline_overrun(pair, monkeypatch):
    observed = []
    def stalled_gui():
        started = time.monotonic(); time.sleep(2.1)
        # Permit overdue heartbeats before returning. Checking only the final
        # predicate (rather than the deadline first) would incorrectly pass.
        QTest.qWait(35)
        observed.append(time.monotonic() - started)
    before = deepcopy(pair.project); undo = len(pair.editor._undo)
    with pytest.raises(AssertionError, match='GUI heartbeat deadline exceeded'):
        _exercise_async_preflight(pair, monkeypatch, 'success', after_entry=stalled_gui)
    assert len(observed) == 1 and observed[0] >= 2.1
    assert pair.project == before and len(pair.editor._undo) == undo


def test_async_preflight_liveness_rejects_synchronous_dispatch(pair, monkeypatch):
    from lumen.ui import Job
    real_start = Job.start
    sync_calls = []
    def synchronous_transition(job, *args, **kwargs):
        if getattr(pair, 'video_transition_job', None) is not job:
            return real_start(job, *args, **kwargs)
        sync_calls.append(threading.get_ident())
        try:
            job.run()  # Deliberate regression mutant: execute on the GUI thread.
        finally:
            QTimer.singleShot(0, job.finished.emit)
    monkeypatch.setattr(Job, 'start', synchronous_transition)
    before = deepcopy(pair.project); undo = len(pair.editor._undo)
    started = time.monotonic()
    with pytest.raises(AssertionError, match='non-GUI thread'):
        _exercise_async_preflight(pair, monkeypatch, 'success')
    assert sync_calls == [threading.get_ident()]
    assert time.monotonic() - started < 6, 'Synchronous mutant was not bounded'
    assert pair.project == before and len(pair.editor._undo) == undo


def test_transition_actions_share_original_single_toolbar_row(pair, monkeypatch):
    w = pair
    button = w.video_transition_button
    assert button.menu() is not None
    actions = button.menu().actions()
    assert [a.text() for a in actions] == ['Add Cross Dissolve…', 'Edit transition…', 'Remove transition']
    assert 'audio still cuts' in button.toolTip()
    calls = []
    monkeypatch.setattr(w, '_open_video_transition_dialog', lambda transition=None: calls.append(transition))
    actions[0].trigger()
    assert calls == [None]
    assert button.parentWidget() is w.split_clip_button.parentWidget()

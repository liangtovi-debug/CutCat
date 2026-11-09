"""Slow filesystem identities never block GUI cancellation or project boundaries."""
from copy import deepcopy
from pathlib import Path
import json
import os
import re
import threading
import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog

from lumen.model import Editor
from lumen.ui_audio_analysis import open_audio_analysis
import lumen.audio_analysis as backend
import lumen.ui_audio_analysis as module
from test_audio_analysis_ui import app, window, report, mock_report, settle


def wait_until(predicate, timeout=3):
    until = time.monotonic() + timeout
    while not predicate() and time.monotonic() < until:
        QTest.qWait(10)
    assert predicate()


def blocked_check(monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []
    owner_thread = threading.get_ident()
    def checker(project, result):
        assert threading.get_ident() != owner_thread
        calls.append((threading.get_ident(), result))
        started.set()
        if len(calls) == 1:
            assert release.wait(8)
        return True
    monkeypatch.setattr(module, 'result_is_current', checker)
    return started, release, calls


def test_all_source_fingerprints_run_off_gui_including_analysis_acceptance_and_recheck(window, monkeypatch):
    owner = threading.get_ident()
    original = backend.audio_context_fingerprint
    calls = []
    def checked(project):
        assert threading.get_ident() != owner, 'Filesystem fingerprint reached the GUI thread'
        calls.append(threading.get_ident())
        return original(project)
    monkeypatch.setattr(backend, 'audio_context_fingerprint', checked)
    def analyze(project, **kwargs):
        return report(context_fingerprint=backend.audio_context_fingerprint(project))
    monkeypatch.setattr(module, 'analyze_audio', analyze)
    dialog = open_audio_analysis(window)
    dialog.analyze(); settle(window)
    assert dialog.state == 'current' and len(calls) >= 2
    dialog._last_verified = 0
    dialog.check_current(); settle(window)
    assert dialog.state == 'current' and len(calls) >= 3
    assert all(thread != owner for thread in calls)


def _assert_stalled_acceptance_liveness(window, monkeypatch, *, release=None,
                                       while_held=None, pump=None, telemetry=None):
    """Worker isolation + bounded GUI liveness, not a 120 ms timer-rate promise.

    Qt coalesces timer expirations during ordinary painting. Four fresh callbacks
    must still happen while the actual metadata checker is held, within the
    existing 0.5 s responsiveness budget. Deadline is checked BEFORE accepting
    an overdue predicate. Analyze and Cancel retain their original 0.5 s limits.
    """
    mock_report(monkeypatch, window)
    started = threading.Event()
    release = release if release is not None else threading.Event()
    calls, heartbeats = [], []
    owner = threading.get_ident()
    telemetry = telemetry if telemetry is not None else {}
    from lumen.ui_style import _FormLayoutController
    refreshes, pumps = [], []
    refresh_count = 0
    original_refresh = _FormLayoutController.refresh
    def measured_refresh(controller):
        nonlocal refresh_count
        began, cpu = time.monotonic(), time.thread_time()
        try:
            return original_refresh(controller)
        finally:
            refresh_count += 1
            if len(refreshes) < 128:
                refreshes.append(dict(wall_seconds=time.monotonic()-began,
                    gui_cpu_seconds=time.thread_time()-cpu, root_type=type(controller.root).__name__))
    monkeypatch.setattr(_FormLayoutController, 'refresh', measured_refresh)
    heartbeat_began = None
    before_project, revision = deepcopy(window.project), window.revision
    history = (len(window.editor._undo), len(window.editor._redo))

    def checker(project, result):
        # Record entry BEFORE waiting so a bounded synchronous-dispatch mutant
        # is observable too. Thread ownership is asserted on the GUI below.
        calls.append((threading.get_ident(), result))
        started.set()
        assert release.wait(8)
        return True
    monkeypatch.setattr(module, 'result_is_current', checker)

    def heartbeat():
        if started.is_set() and not release.is_set():
            heartbeats.append((time.monotonic(), threading.get_ident()))
    timer = QTimer(); timer.setInterval(10); timer.timeout.connect(heartbeat); timer.start()
    dialog = open_audio_analysis(window)
    telemetry.update(heartbeats=heartbeats, calls=calls)
    try:
        before = time.monotonic(); dialog.analyze()
        telemetry['analyze_seconds'] = time.monotonic() - before
        assert telemetry['analyze_seconds'] < .5
        wait_until(started.is_set)
        wait_until(lambda: not window.jobs)
        assert len(calls) == 1 and calls[0][0] != owner, 'Metadata checker reached GUI thread'
        assert dialog.state == 'checking' and dialog.result is None
        assert 'not current' in dialog.status.text()
        assert dialog.cancel_button.isEnabled()
        previous = len(heartbeats)
        telemetry['heartbeat_start_count'] = previous
        telemetry['setup_layout_refreshes'] = list(refreshes)
        refreshes.clear(); refresh_count = 0
        began = heartbeat_began = time.monotonic(); deadline = began + .5
        if while_held is not None:
            while_held(dialog)
        while True:
            # An overdue qWait/pump may deliver all four callbacks at once.
            # A now-true predicate must not bypass the wall-clock bound.
            telemetry['heartbeat_seconds'] = time.monotonic() - began
            assert time.monotonic() <= deadline, 'Audio acceptance GUI heartbeat deadline exceeded'
            assert not release.is_set() and window.audio_analysis_freshness.worker.is_alive()
            assert dialog.state == 'checking' and dialog.result is None
            if len(heartbeats) - previous >= 4:
                break
            pump_began, cpu = time.monotonic(), time.thread_time()
            try:
                (pump or (lambda: QTest.qWait(10)))()
            finally:
                if len(pumps) < 128:
                    pumps.append(dict(wall_seconds=time.monotonic()-pump_began,
                                      gui_cpu_seconds=time.thread_time()-cpu))
        telemetry['heartbeat_seconds'] = time.monotonic() - began
        assert all(thread == owner for _, thread in heartbeats[previous:])
        assert not release.is_set()
        before = time.monotonic(); dialog.cancel_button.click()
        telemetry['cancel_seconds'] = time.monotonic() - before
        assert telemetry['cancel_seconds'] < .5
        assert dialog.state == 'canceled' and dialog.result is None
        assert window.audio_analysis_freshness.worker.is_alive() and not release.is_set()
    finally:
        phase_refreshes, phase_refresh_count = list(refreshes), refresh_count
        # Invalidate before releasing even when an expected negative control
        # has raised. Its held result must not publish during test cleanup.
        try:
            dialog.cancel_analysis()
        finally:
            release.set(); timer.stop(); QTest.qWait(60)
        telemetry['cleanup'] = {
            'state': dialog.state,
            'no_result': dialog.result is None,
            'no_complete_progress': window.progress_bar.value() != 100,
            'project_unchanged': window.project == before_project,
            'revision_unchanged': window.revision == revision,
            'history_unchanged': (len(window.editor._undo), len(window.editor._redo)) == history,
        }
        # Synthetic UI diagnostics: durations, ownership and booleans only;
        # never serialize the project, report, media paths or environment.
        evidence = os.environ.get('AUTO_FRAME_REVIEW_EVIDENCE')
        if evidence:
            folder = Path(evidence) / 'audio-acceptance'; folder.mkdir(parents=True, exist_ok=True)
            name = os.environ.get('PYTEST_CURRENT_TEST', 'unknown').split('::')[-1].split(' (')[0]
            name = re.sub(r'[^A-Za-z0-9_-]', '_', name)[:160]
            record = dict(schema=1, platform=os.name, deadline_seconds=.5,
                analyze_seconds=telemetry.get('analyze_seconds'),
                heartbeat_seconds=telemetry.get('heartbeat_seconds'),
                cancel_seconds=telemetry.get('cancel_seconds'),
                paint=telemetry.get('paint'),
                clocks={name: vars(time.get_clock_info(name)) for name in ('monotonic', 'perf_counter')},
                checker_calls=len(calls), checker_off_gui=all(thread != owner for thread, _ in calls),
                heartbeats=[dict(offset_seconds=stamp-heartbeat_began, on_gui=thread == owner)
                    for stamp, thread in heartbeats[telemetry.get('heartbeat_start_count', 0):]] if heartbeat_began is not None else [],
                pumps=pumps, layout_refresh_count=phase_refresh_count, layout_refreshes=phase_refreshes,
                setup_layout_refreshes=telemetry.get('setup_layout_refreshes', []), cleanup=telemetry['cleanup'])
            (folder / (name + '.json')).write_text(json.dumps(record, indent=2), encoding='utf-8')
        assert telemetry['cleanup'] == dict(state='canceled', no_result=True,
            no_complete_progress=True, project_unchanged=True,
            revision_unchanged=True, history_unchanged=True)
    assert len(calls) == 1 and dialog.result is None and dialog.state == 'canceled'
    assert window.progress_bar.value() != 100
    assert window.project == before_project and window.revision == revision
    assert (len(window.editor._undo), len(window.editor._redo)) == history
    return telemetry


def test_stalled_acceptance_keeps_qtimer_and_cancel_responsive_and_never_publishes(window, monkeypatch):
    _assert_stalled_acceptance_liveness(window, monkeypatch)


def test_stalled_acceptance_liveness_allows_short_actual_qt_paint(window, monkeypatch):
    from PySide6.QtWidgets import QWidget
    painted, widgets, telemetry = [], [], {}
    gui_thread = threading.get_ident()
    class DelayedPaint(QWidget):
        def paintEvent(self, event):
            if not painted:
                before = time.perf_counter_ns()
                deadline = before + 80_000_000
                # Measure and enforce the delay on the high-resolution clock;
                # a coarse monotonic tick must not classify an 80ms sleep as 78ms.
                while (remaining := deadline - time.perf_counter_ns()) > 0:
                    time.sleep(remaining / 1_000_000_000)
                elapsed = time.perf_counter_ns() - before
                painted.append(elapsed / 1_000_000_000)
                telemetry['paint'] = dict(elapsed_ns=elapsed, minimum_ns=80_000_000,
                                          on_gui=threading.get_ident() == gui_thread)
            super().paintEvent(event)
    def show_paint(dialog):
        widget = DelayedPaint(dialog); widget.setGeometry(2, 2, 8, 8)
        widgets.append(widget); widget.show(); widget.update()
    try:
        telemetry = _assert_stalled_acceptance_liveness(window, monkeypatch, while_held=show_paint, telemetry=telemetry)
        assert painted and painted[0] >= .08
        assert telemetry['paint']['on_gui']
        assert len(telemetry['heartbeats']) - telemetry['heartbeat_start_count'] >= 4
        assert telemetry['heartbeat_seconds'] <= .5 and telemetry['cancel_seconds'] < .5
    finally:
        for widget in widgets:
            widget.hide(); widget.deleteLater()


def test_stalled_acceptance_liveness_rejects_synchronous_metadata_dispatch(window, monkeypatch):
    from lumen.ui_audio_freshness import AudioFreshnessController
    release = threading.Event(); telemetry = {}
    def synchronous(self, project, result, checker):
        checker(project, result)
        return object()
    monkeypatch.setattr(AudioFreshnessController, 'request', synchronous)
    # Independent finite watchdog prevents this deliberately broken dispatcher
    # from hanging the GUI; it does not supply a successful heartbeat.
    watchdog = threading.Timer(.7, release.set); watchdog.daemon = True; watchdog.start()
    try:
        with pytest.raises(AssertionError, match='Metadata checker reached GUI thread'):
            _assert_stalled_acceptance_liveness(window, monkeypatch, release=release, telemetry=telemetry)
        assert telemetry['cleanup']['no_result'] and telemetry['cleanup']['project_unchanged']
    finally:
        release.set(); watchdog.cancel(); watchdog.join(1)
        assert not watchdog.is_alive()


def test_stalled_acceptance_liveness_rejects_overdue_delivered_heartbeats(window, monkeypatch):
    telemetry = {}
    def overdue_pump():
        time.sleep(.55)
        # Deliver four overdue callbacks before returning. Checking only the
        # now-true count would incorrectly accept this GUI deadline violation.
        until = time.monotonic() + .4
        while (len(telemetry['heartbeats']) - telemetry['heartbeat_start_count'] < 4
               and time.monotonic() < until):
            QTest.qWait(10)
    with pytest.raises(AssertionError, match='Audio acceptance GUI heartbeat deadline exceeded'):
        _assert_stalled_acceptance_liveness(window, monkeypatch, pump=overdue_pump, telemetry=telemetry)
    assert len(telemetry['heartbeats']) - telemetry['heartbeat_start_count'] >= 4
    assert telemetry['cleanup']['no_result'] and telemetry['cleanup']['project_unchanged']


@pytest.mark.parametrize('boundary', ['new', 'open', 'close'])
def test_project_boundaries_do_not_wait_for_stalled_metadata(window, monkeypatch, boundary):
    mock_report(monkeypatch, window)
    started, release, calls = blocked_check(monkeypatch)
    dialog = open_audio_analysis(window); dialog.analyze()
    try:
        wait_until(started.is_set); wait_until(lambda: not window.jobs)
        window.dirty = False
        if boundary == 'new':
            monkeypatch.setattr(QInputDialog, 'getItem', lambda *args: ('1280 × 720 · 30 fps', True))
            action = window.new_project
        elif boundary == 'open':
            from lumen.model import Project
            monkeypatch.setattr(Project, 'load', lambda *args: deepcopy(window.project))
            monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: ('synthetic-project.lumen', ''))
            action = window.open_project
        else:
            action = window.close
        before = time.monotonic(); action()
        assert time.monotonic() - before < .5
        assert dialog.state == 'closed' and dialog.result is None
        assert window.audio_analysis_freshness.worker.is_alive()
    finally:
        release.set(); QTest.qWait(60)
    assert dialog.state == 'closed' and dialog.result is None and len(calls) == 1


def test_repeated_close_reopen_has_one_blocked_worker_and_only_latest_reply_can_publish(window, monkeypatch):
    mock_report(monkeypatch, window)
    started, release, calls = blocked_check(monkeypatch)
    dialogs = []
    try:
        for index in range(6):
            dialog = open_audio_analysis(window); dialogs.append(dialog)
            dialog.analyze()
            wait_until(lambda: not window.jobs and dialog.state == 'checking')
            if index == 0:
                wait_until(started.is_set)
                worker = window.audio_analysis_freshness.worker
            assert window.audio_analysis_freshness.worker is worker
            assert len(calls) == 1 and dialog.result is None
            if index < 5:
                before = time.monotonic(); dialog.reject()
                assert time.monotonic() - before < .5
        last = dialogs[-1]
        release.set(); settle(window)
        assert last.state == 'current' and last.result is not None
        assert len(calls) == 2
        assert all(dialog.result is None for dialog in dialogs[:-1])
    finally:
        release.set(); QTest.qWait(60)


def test_equal_settings_new_editor_session_rejects_old_freshness_reply(window, monkeypatch):
    mock_report(monkeypatch, window)
    started, release, calls = blocked_check(monkeypatch)
    dialog = open_audio_analysis(window); dialog.analyze()
    try:
        wait_until(started.is_set); wait_until(lambda: not window.jobs)
        old_token, old_report = dialog._freshness_token, dialog._candidate
        window.editor = Editor(deepcopy(window.project))
        dialog.check_current()
        assert dialog.state == 'stale' and dialog.result is None
        # Even an already-queued old result with equal audio settings cannot
        # cross the editor/session or generation boundary.
        dialog._freshness_checked(old_token, old_report, True)
        assert dialog.state == 'stale' and dialog.result is None
    finally:
        release.set(); QTest.qWait(60)
    assert dialog.result is None


def test_pending_periodic_check_keeps_numbers_only_under_unverified_status(window, monkeypatch):
    mock_report(monkeypatch, window)
    dialog = open_audio_analysis(window); dialog.analyze(); settle(window)
    assert dialog.state == 'current'
    prior = dialog.values['lufs'].text()
    started, release, calls = blocked_check(monkeypatch)
    try:
        dialog._last_verified = 0; dialog.check_current()
        wait_until(started.is_set)
        assert dialog.state == 'checking' and dialog.result is None
        assert dialog.values['lufs'].text() == prior
        assert 'not current' in dialog.status.text()
        window.editor.update_clip('c', gain_db=-4); window.changed()
        assert dialog.state == 'stale' and dialog.result is None
        assert dialog.values['lufs'].text() == 'Not analyzed'
    finally:
        release.set(); QTest.qWait(60)
    assert dialog.state == 'stale' and dialog.result is None


def test_routine_freshness_does_not_overwrite_later_tracked_job_progress(window, monkeypatch):
    mock_report(monkeypatch, window)
    dialog = open_audio_analysis(window); dialog.analyze(); settle(window)
    assert dialog.state == 'current' and window.progress_bar.value() == 100
    started, release = threading.Event(), threading.Event()
    def other_operation(cancel, progress):
        progress(.37); started.set(); assert release.wait(5)
    window.start_job('Other test task', other_operation, lambda value: None, 'preview')
    try:
        wait_until(started.is_set)
        wait_until(lambda: window.progress_bar.value() == 37)
        dialog._last_verified = 0; dialog.check_current()
        wait_until(lambda: dialog.state == 'current')
        assert window.progress_bar.value() == 37
    finally:
        release.set(); settle(window)


def test_initial_freshness_does_not_overwrite_newer_finished_job_progress(window, monkeypatch):
    mock_report(monkeypatch, window)
    started, release, calls = blocked_check(monkeypatch)
    dialog = open_audio_analysis(window); dialog.analyze()
    try:
        wait_until(started.is_set); wait_until(lambda: not window.jobs)
        window.start_job('Newer test task', lambda cancel, progress: progress(.37), lambda value: None, 'preview')
        wait_until(lambda: not window.jobs)
        assert window.progress_bar.value() == 37
        release.set(); settle(window)
        assert dialog.state == 'current'
        assert window.progress_bar.value() == 37
    finally:
        release.set(); QTest.qWait(60)

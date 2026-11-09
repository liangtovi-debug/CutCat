"""Owned Qt progress for origin-decoded audio; no actual FFmpeg in this suite."""
from copy import deepcopy
import threading

import pytest

from lumen.i18n import set_locale
from lumen import ui_source_viewer as ui
from test_source_viewer_ui import app, window, opened, planned, pump


SCAN = 'Scanning source audio from origin: {scanned} / {target} seconds. Cold preparation is linear; Cancel is safe.'
WARM = 'Reusing exact audio window from this viewer session.'
READY = 'Exact audio windows ready; original media remains the export source.'


def blocked_operation(dialog, message=SCAN, **values):
    started, release = threading.Event(), threading.Event()
    saved = {}
    def operation(cancel, numeric_progress):
        callback = dialog.backend.progress_callback
        saved['callback'] = callback
        saved['worker_thread'] = threading.get_ident()
        callback(message, **values)
        started.set()
        assert release.wait(5)
        return True
    return operation, started, release, saved


def test_exact_cache_limits_and_origin_scan_tradeoff_are_live_localized(window, app):
    dialog = opened(window, app)
    assert 'no longer limited' in dialog.codec_note.text()
    assert 'first late window takes longer' in dialog.codec_note.text()
    assert 'Only requested windows' in dialog.codec_note.text()
    for value in ('Session-only', 'not project media', 'export source', '120 seconds',
                  '30 minutes', '48 MiB', 'at most 30 seconds'):
        assert value in dialog.cache_note.text()
    assert 'No overwrite is applied' in dialog.stop_button.toolTip()
    from lumen.ui_help import help_text
    assert 'linearly from the audio origin without input seeking' in help_text()
    assert 'not constant-time random access' in help_text()
    set_locale('zh-CN'); app.processEvents()
    assert '不再受原点后的 30 秒限制' in dialog.codec_note.text()
    assert '首次准备更晚窗口耗时更长' in dialog.codec_note.text()
    for value in ('不是工程素材', '120 秒', '30 分钟', '48 MiB', '最多 30 秒'):
        assert value in dialog.cache_note.text()
    assert '准备过程中不会应用覆盖' in dialog.stop_button.toolTip()
    assert '不进行输入定位' in help_text() and '并非恒定耗时' in help_text()


def test_progress_is_queued_to_gui_retranslatable_and_cancelable(window, app, monkeypatch):
    dialog = opened(window, app)
    before = deepcopy(window.project)
    gui_thread = threading.get_ident()
    status_threads = []
    original = dialog._set_status
    def status(value):
        status_threads.append(threading.get_ident())
        original(value)
    monkeypatch.setattr(dialog, '_set_status', status)
    operation, started, release, saved = blocked_operation(dialog, scanned='12.5', target='120.0')
    accepted = []
    dialog._submit('Preparing bounded source audition…', operation, lambda *args: accepted.append(args))
    try:
        pump(app, lambda: started.is_set() and '12.5 / 120.0 seconds' in dialog.status.text())
        assert saved['worker_thread'] != gui_thread
        assert set(status_threads) == {gui_thread}
        assert dialog.preparation_status.isVisible() and dialog.stop_button.isEnabled()
        assert not dialog.apply_button.isEnabled()
        set_locale('zh-CN'); app.processEvents()
        assert '12.5／120.0 秒' in dialog.status.text()
        assert '可安全取消' in dialog.preparation_status.text()
        dialog.cancel_work()
        canceled_status = dialog.status.text()
        saved['callback'](WARM)
        app.processEvents()
        assert dialog.status.text() == canceled_status
        assert not dialog.preparation_status.isVisible()
    finally:
        release.set()
        pump(app, lambda: not window.jobs)
    assert not accepted and window.project == before
    assert dialog.backend.progress_callback is None


def test_late_progress_from_old_job_cannot_override_same_context_new_job(window, app):
    dialog = opened(window, app)
    operation, started, release, old = blocked_operation(dialog, scanned='1', target='100')
    dialog._submit('Preparing bounded source audition…', operation, lambda *_: None)
    try:
        pump(app, started.is_set)
    finally:
        release.set(); pump(app, lambda: not window.jobs)
    operation, started, release, current = blocked_operation(dialog, WARM)
    dialog._submit('Preparing bounded source audition…', operation, lambda *_: dialog._set_status('accepted'))
    try:
        pump(app, lambda: started.is_set() and WARM == dialog.status.text())
        old['callback'](SCAN, scanned='99', target='100')
        app.processEvents()
        assert dialog.status.text() == WARM
        assert dialog.preparation_status.text() == WARM
    finally:
        release.set(); pump(app, lambda: not window.jobs)
    assert dialog.status.text() == 'accepted'
    current['callback'](READY); app.processEvents()
    assert dialog.status.text() == 'accepted'
    assert not dialog.preparation_status.isVisible()


@pytest.mark.parametrize('change', ['cancel', 'close', 'selection', 'lock', 'relink', 'undo'])
def test_progress_during_apply_cannot_revive_stale_commit(window, app, monkeypatch, change):
    dialog = planned(window, app)
    started, release = threading.Event(), threading.Event()
    saved = {}
    def verify(session, identity, project_snapshot, plan, cancel=None):
        saved['callback'] = dialog.backend.progress_callback
        saved['callback'](SCAN, scanned='40', target='300')
        started.set()
        assert release.wait(5)
        saved['callback'](READY)
        return True
    monkeypatch.setattr(dialog.backend, 'verify_overwrite_sources', verify)
    dialog.apply_overwrite()
    try:
        pump(app, lambda: started.is_set() and '40 / 300 seconds' in dialog.status.text())
        assert dialog.stop_button.isEnabled() and not dialog.apply_button.isEnabled()
        if change == 'cancel': dialog.cancel_work()
        elif change == 'close': dialog.reject()
        elif change == 'selection': window.bin.setCurrentRow(1)
        elif change == 'lock': window.editor.set_track_edit_lock(0, True)
        elif change == 'relink': window.project.media[0].path += '.relinked'
        else: window.undo()
        dialog.check_current()
        expected = deepcopy(window.project)
        saved['callback'](WARM); app.processEvents()
        assert dialog.preparation_status.isHidden()
        assert WARM != dialog.status.text()
    finally:
        release.set(); pump(app, lambda: not window.jobs)
    assert window.project == expected
    assert dialog.plan is None and not dialog.apply_button.isEnabled()
    assert dialog.backend.progress_callback is None


def test_audio_audition_uses_current_worker_callback_without_numeric_progress_api_change(window, app, monkeypatch):
    dialog = opened(window, app)
    accepted, callbacks = [], []
    def audition(session, cancel=None):
        callback = dialog.backend.progress_callback
        assert callable(callback)
        callbacks.append(callback)
        callback(WARM)
        return 'audition-result'
    monkeypatch.setattr(dialog.backend, 'audition', audition, raising=False)
    monkeypatch.setattr(dialog, '_accept_audition', lambda result, token: accepted.append(result))
    dialog.audition(); pump(app, lambda: not window.jobs)
    assert accepted == ['audition-result'] and len(callbacks) == 1
    assert dialog.backend.progress_callback is None
    assert not dialog.preparation_status.isVisible()


def test_failure_then_late_progress_cannot_replace_error(window, app):
    dialog = opened(window, app)
    saved = {}
    def fail(cancel, progress):
        saved['callback'] = dialog.backend.progress_callback
        saved['callback'](WARM)
        raise ValueError('Source audio window is missing or incomplete.')
    dialog._submit('Preparing bounded source audition…', fail, lambda *_: pytest.fail('Failure accepted'))
    pump(app, lambda: not window.jobs)
    error = dialog.status.text()
    saved['callback'](READY); app.processEvents()
    assert dialog.status.text() == error
    assert 'missing or incomplete' in error
    assert not dialog.preparation_status.isVisible()
    assert dialog.backend.progress_callback is None


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('font_size', [10, 15])
def test_preparation_message_wraps_and_cancel_remains_reachable(window, app, locale, font_size):
    from PySide6.QtGui import QFont
    dialog = opened(window, app)
    font = QFont(dialog.font()); font.setPointSize(font_size)
    window.setFont(font)
    dialog.setFont(font)
    set_locale(locale)
    dialog.resize(320, 380)
    operation, started, release, saved = blocked_operation(dialog, scanned='1799.9', target='1800.0')
    dialog._submit('Preparing bounded source audition…', operation, lambda *_: None)
    try:
        pump(app, lambda: started.is_set() and dialog.preparation_status.isVisible())
        assert dialog.scroll.horizontalScrollBar().maximum() == 0
        assert dialog.preparation_status.font().pointSizeF() == font_size
        assert dialog.stop_button.font().pointSizeF() == font_size
        assert dialog.preparation_status.wordWrap()
        assert '1799.9' in dialog.preparation_status.text()
        assert dialog.preparation_status.accessibleName()
        for control in (dialog.stop_button, dialog.close_button):
            dialog.scroll.ensureWidgetVisible(control); app.processEvents()
            point = control.mapTo(dialog.scroll.viewport(), control.rect().center())
            assert dialog.scroll.viewport().rect().contains(point)
            assert control.isEnabled()
        dialog.cancel_work()
        assert dialog.job.cancel.is_set()
    finally:
        release.set(); pump(app, lambda: not window.jobs)

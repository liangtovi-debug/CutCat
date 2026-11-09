"""Bounded, cached background source availability: no GUI-thread filesystem."""
import threading
from pathlib import Path

import pytest
from lumen.model import Editor, Media, Project
from lumen.i18n import tr
from test_ui import app, window, pump_until


def add(window, tmp_path, count=1):
    media = [Media(f'm{i}', str(tmp_path / f'm{i}.mov'), f'Media {i}', 2, 640, 360, True, True) for i in range(count)]
    window.editor.import_media_batch(media)
    return media


def settled(window, app):
    pump_until(app, lambda: window._media_status_job is None)


def test_missing_badges_check_only_on_worker_and_filters_do_not_rescan(window, tmp_path, app, monkeypatch):
    add(window, tmp_path, 2)
    owner = threading.get_ident(); calls = []
    def exists(path):
        assert threading.get_ident() != owner
        calls.append(str(path)); return path.name == 'm1.mov'
    monkeypatch.setattr(Path, 'exists', exists)
    window.refresh_media_browser(); settled(window, app)
    assert str(tr('MISSING · ')) in window.bin.item(0).text()
    assert str(tr('MISSING · ')) not in window.bin.item(1).text()
    assert str(tr('Checking… · ')) not in window.bin.item(0).text()
    before = list(calls)
    window.media_search.setText('Media'); window.media_type_filter.setCurrentIndex(1)
    app.processEvents(); assert calls == before


def test_scan_coalesces_same_snapshot_and_drains_bounded_batches(window, tmp_path, app, monkeypatch):
    add(window, tmp_path, 300)
    entered = threading.Event(); release = threading.Event(); calls = []
    def exists(path):
        calls.append(path.name)
        if len(calls) == 1: entered.set(); release.wait(5)
        return False
    monkeypatch.setattr(Path, 'exists', exists)
    window.refresh_media_browser(); assert entered.wait(3)
    first_job = window._media_status_job
    for _ in range(20): window.refresh_media_browser()
    assert window._media_status_job is first_job and window._media_status_pending is None
    release.set(); settled(window, app)
    assert len(calls) == 300 and len(set(calls)) == 300
    assert not window._media_status_waiting
    assert len(window._media_status_cache) == 300


@pytest.mark.parametrize('change', ['editor', 'session', 'relink', 'remove', 'close'])
def test_stale_or_canceled_scan_never_applies(window, tmp_path, app, monkeypatch, change):
    media = add(window, tmp_path)[0]
    entered = threading.Event(); release = threading.Event()
    def exists(path): entered.set(); release.wait(5); return False
    monkeypatch.setattr(Path, 'exists', exists)
    window.refresh_media_browser(); assert entered.wait(3)
    key = (window.editor, window.editor.session_id, media.id, media.path)
    if change == 'editor': window.editor = Editor(Project())
    elif change == 'session': window.editor.project = Project()
    elif change == 'relink': window.project.media[0].path = str(tmp_path / 'replacement.mov')
    elif change == 'remove': window.editor.remove_unused_media([media.id])
    else: assert not window.close_media_status()
    release.set(); settled(window, app)
    assert key not in window._media_status_cache


def test_latest_snapshot_wins_and_same_path_name_edits_reuse_status(window, tmp_path, app, monkeypatch):
    media = add(window, tmp_path)[0]
    entered = threading.Event(); release = threading.Event(); calls = []
    def exists(path):
        calls.append(path.name)
        if len(calls) == 1: entered.set(); release.wait(5)
        return path.name == 'latest.mov'
    monkeypatch.setattr(Path, 'exists', exists)
    window.refresh_media_browser(); assert entered.wait(3)
    for filename in ('middle.mov', 'latest.mov'):
        window.project.media[0].path = str(tmp_path / filename); window.refresh_media_browser()
    release.set(); settled(window, app)
    assert calls == ['m0.mov', 'latest.mov']
    assert str(tr('MISSING · ')) not in window.bin.item(0).text()
    key = (window.editor, window.editor.session_id, media.id, window.project.media[0].path)
    assert window._media_status_cache[key] is True
    window.project.media[0].name = 'Renamed'
    window.refresh_media_browser()
    assert window._media_status_cache[key] is True
    settled(window, app)


def test_scan_rejects_network_before_stat_and_marks_unknown(window, tmp_path, app, monkeypatch):
    media = add(window, tmp_path)[0]
    window.project.media[0].path = '//server/share/file.mov'
    def forbidden(*args): raise AssertionError('network stat')
    monkeypatch.setattr(Path, 'exists', forbidden)
    window.refresh_media_browser(); settled(window, app)
    assert str(tr('Unavailable status · ')) in window.bin.item(0).text()


def test_cancel_stops_batch_between_paths(window, tmp_path, app, monkeypatch):
    add(window, tmp_path, 300)
    entered = threading.Event(); release = threading.Event(); calls = []
    def exists(path):
        calls.append(path.name); entered.set(); release.wait(5); return False
    monkeypatch.setattr(Path, 'exists', exists)
    window.refresh_media_browser(); assert entered.wait(3)
    window._media_status_job.cancel.set(); release.set(); settled(window, app)
    assert len(calls) == 1 and not window._media_status_cache
    assert not window._media_status_waiting
    assert str(tr('Unavailable status · ')) in window.bin.item(0).text()


@pytest.mark.parametrize('action', ['new', 'open'])
def test_new_open_not_blocked_by_background_scan(window, tmp_path, app, monkeypatch, action):
    from PySide6.QtWidgets import QInputDialog, QFileDialog
    add(window, tmp_path)
    entered = threading.Event(); release = threading.Event()
    def exists(path): entered.set(); release.wait(5); return False
    monkeypatch.setattr(Path, 'exists', exists)
    window.refresh_media_browser(); assert entered.wait(3)
    old_editor = window.editor
    monkeypatch.setattr(window, 'confirm_discard', lambda: True)
    if action == 'new':
        monkeypatch.setattr(QInputDialog, 'getItem', lambda *a, **k: ('1280 × 720 · 30 fps', True))
        window.new_project()
    else:
        path = tmp_path / 'new.lumen'; Project(name='New source status session').save(path)
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(path), ''))
        window.open_project()
    assert window.editor is not old_editor and not window.jobs
    release.set(); settled(window, app)
    assert not window._media_status_cache and not window.project.media


def test_close_waits_without_blocking_for_background_worker(window, tmp_path, app, monkeypatch):
    add(window, tmp_path)
    entered = threading.Event(); release = threading.Event()
    def exists(path): entered.set(); release.wait(5); return False
    monkeypatch.setattr(Path, 'exists', exists)
    window.show(); window.refresh_media_browser(); assert entered.wait(3)
    monkeypatch.setattr(window, 'confirm_discard', lambda: True)
    window.close()
    assert window.closing and window._media_status_close_pending
    assert window._media_status_job is not None and window._media_status_job.cancel.is_set()
    release.set(); settled(window, app)
    pump_until(app, lambda: not window.isVisible())
    assert not window._media_status_cache


def test_unexpected_worker_failure_marks_unknown_without_infinite_checking(window, tmp_path, app, monkeypatch):
    add(window, tmp_path, 300)
    def broken(path): raise OverflowError('unexpected filesystem failure')
    monkeypatch.setattr(Path, 'exists', broken)
    window.refresh_media_browser(); settled(window, app)
    assert not window._media_status_waiting
    assert str(tr('Unavailable status · ')) in window.bin.item(0).text()
    assert not window._media_status_cache


def test_finished_queued_close_is_synchronous_and_reopen_restarts_cleanly(window, tmp_path, app, monkeypatch):
    add(window, tmp_path, 300)
    calls = []
    monkeypatch.setattr(Path, 'exists', lambda path: (calls.append(path.name) if path.name.startswith('m') and path.suffix == '.mov' else None) or False)
    window.refresh_media_browser()
    old = window._media_status_job
    assert old.wait(2000) and old.isFinished()
    # No event pumping: first batch's completion is still queued.
    window.dirty = False
    assert window.close() is True
    assert window._media_status_job is None
    window.show()
    assert not window.closing and not window._media_status_closed
    assert not window._media_status_close_pending
    assert window.centralWidget().isEnabled()
    settled(window, app)
    assert len(calls) == 428  # old first batch, then one complete fresh scan
    assert len(window._media_status_cache) == 300
    assert window.isVisible()


def test_cancel_work_does_not_retract_authorized_close(window):
    window.closing = True
    window._job_close_timer.start(1000)
    window.cancel_jobs()
    assert window._job_close_timer.isActive()
    window._job_close_timer.stop()

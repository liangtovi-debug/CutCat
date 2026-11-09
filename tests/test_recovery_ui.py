"""Offscreen recovery regressions; these are not native desktop certifications."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import uuid

import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from lumen.model import Caption, Project
from lumen.project_recovery import RecoveryStore
from lumen.ui import MainWindow
from lumen.ui_recovery import project_fingerprint


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch, tmp_path):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: warnings.append(a[2]))
    monkeypatch.setattr(QMessageBox, 'information', lambda *a: None)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Cancel)
    settings = QSettings(str(tmp_path / 'workspace.ini'), QSettings.Format.IniFormat)
    w = MainWindow(workspace_settings=settings, ui_language='en', recovery_root=tmp_path / 'recovery')
    w.test_warnings = warnings
    yield w
    w.jobs.clear()  # Tests below use sentinel jobs, never live workers.
    w.dirty = False
    w.closing = True
    w.close()
    app.processEvents()


def edit_caption(window, text='Unsaved cut'):
    if window.project.captions:
        window.editor.update_caption(window.project.captions[0].id, text=text)
    else:
        window.editor.add_caption(0, 10, text)
    window.changed()


def available(root):
    observer = RecoveryStore(root)
    try:
        entries, warnings = observer.list_entries()
        assert not warnings
        return entries
    finally:
        observer.close()


def release_and_find(window, root, identity=None):
    identity = identity or window.recovery_identity
    window.recovery_store.release(identity)
    return next(e for e in available(root) if e.identity == identity)


def orphan(root, name='Recovered cut', original_path=None):
    project = Project(name=name, captions=[Caption('recover-caption', 2, 8, name)])
    writer = RecoveryStore(root)
    try:
        entry = writer.write(project, str(uuid.uuid4()), original_path)
    finally:
        writer.close()
    return entry, project


def tree_bytes(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_timer_clean_noop_and_repeated_tick_write_only_changed_content(window, tmp_path, monkeypatch):
    w = window
    assert w.recovery_timer.isActive() and w.recovery_timer.interval() == 30_000
    writes = []
    original = w.recovery_store.write
    def record(*args, **kwargs):
        result = original(*args, **kwargs)
        writes.append(result)
        return result
    monkeypatch.setattr(w.recovery_store, 'write', record)
    w.autosave_recovery()
    w.changed()  # A UI notification alone is not a project edit.
    w.autosave_recovery()
    assert not w.dirty and writes == []
    edit_caption(w)
    w.autosave_recovery()
    state = tree_bytes(tmp_path / 'recovery')
    for _ in range(3):
        w.changed()
        w.autosave_recovery()
    assert len(writes) == 1 and tree_bytes(tmp_path / 'recovery') == state
    assert 'saved locally' in w.recovery_button.text()
    assert available(tmp_path / 'recovery') == []  # Active window owns it.
    edit_caption(w, 'A real second edit')
    w.autosave_recovery()
    assert len(writes) == 2 and w.dirty


def test_undo_saved_baseline_removes_snapshot_and_redo_restores_dirty(window, tmp_path, monkeypatch):
    w = window
    edit_caption(w, 'Saved baseline')
    path = tmp_path / 'baseline.lumen'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(path), ''))
    assert w.save_project()
    edit_caption(w, 'Later edit')
    w.autosave_recovery()
    w.undo()
    assert not w.dirty and w.project.captions[0].text == 'Saved baseline'
    w.autosave_recovery()
    assert w.recovery_last_fingerprint is None
    w.recovery_store.release(w.recovery_identity)
    assert available(tmp_path / 'recovery') == []
    w.redo()
    assert w.dirty and w.project.captions[0].text == 'Later edit'
    w.autosave_recovery()
    assert release_and_find(w, tmp_path / 'recovery').identity == w.recovery_identity


def test_save_success_cleans_only_own_snapshot_and_resets_baseline(window, tmp_path, monkeypatch):
    w = window
    root = tmp_path / 'recovery'
    other, _ = orphan(root, 'Unrelated project')
    edit_caption(w)
    w.autosave_recovery()
    old_identity = w.recovery_identity
    path = tmp_path / 'saved.lumen'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(path), ''))
    assert w.save_project()
    assert w.project_path == path and not w.dirty
    assert w.recovery_identity != old_identity
    assert w.saved_project_fingerprint == project_fingerprint(w.project)
    assert Project.load(path).to_dict() == w.project.to_dict()
    w.changed(); w.autosave_recovery()
    assert not w.dirty
    assert [e.identity for e in available(root)] == [other.identity]


@pytest.mark.parametrize('failure', ['cancel', 'write_error'])
def test_failed_or_canceled_save_keeps_current_snapshot(window, tmp_path, monkeypatch, failure):
    w = window
    edit_caption(w)
    w.autosave_recovery()
    identity = w.recovery_identity
    before = tree_bytes(tmp_path / 'recovery')
    target = tmp_path / 'failed.lumen'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: ('' if failure == 'cancel' else str(target), ''))
    if failure == 'write_error':
        def fail(*a):
            raise OSError('simulated full disk')
        monkeypatch.setattr(Project, 'save', fail)
    assert w.save_project() is False
    assert w.dirty and w.project_path is None and w.recovery_identity == identity
    assert tree_bytes(tmp_path / 'recovery') == before
    assert not target.exists()
    assert bool(w.test_warnings) == (failure == 'write_error')
    assert release_and_find(w, tmp_path / 'recovery').identity == identity


def test_autosave_failure_is_visible_retryable_and_never_marks_saved(window, monkeypatch):
    w = window
    edit_caption(w)
    before = w.project.to_dict()
    original = w.recovery_store.write
    def fail(*a):
        raise OSError('recovery disk unavailable')
    monkeypatch.setattr(w.recovery_store, 'write', fail)
    w.autosave_recovery()
    assert w.dirty and w.recovery_last_fingerprint is None
    assert w.project.to_dict() == before
    assert 'attention' in w.recovery_button.text()
    assert 'unavailable' in w.recovery_button.toolTip()
    monkeypatch.setattr(w.recovery_store, 'write', original)
    w.autosave_recovery()
    assert w.dirty and not w.recovery_error
    assert w.recovery_last_fingerprint == project_fingerprint(w.project)


@pytest.mark.parametrize('action', ['open', 'new'])
@pytest.mark.parametrize('cancel_at', ['changes', 'destination'])
def test_canceled_project_replacement_keeps_edits_and_snapshot(window, tmp_path, monkeypatch, action, cancel_at):
    w = window
    edit_caption(w)
    w.autosave_recovery()
    before, identity = w.project.to_dict(), w.recovery_identity
    files = tree_bytes(tmp_path / 'recovery')
    answer = QMessageBox.StandardButton.Cancel if cancel_at == 'changes' else QMessageBox.StandardButton.Discard
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: answer)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: ('', ''))
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a: ('', False))
    getattr(w, action + '_project')()
    assert w.dirty and w.recovery_identity == identity and w.project.to_dict() == before
    assert tree_bytes(tmp_path / 'recovery') == files
    assert release_and_find(w, tmp_path / 'recovery').identity == identity


def test_invalid_open_keeps_current_snapshot_and_undo(window, tmp_path, monkeypatch):
    w = window
    edit_caption(w)
    w.autosave_recovery()
    before, identity = w.project.to_dict(), w.recovery_identity
    invalid = tmp_path / 'broken.lumen'; invalid.write_text('{broken', encoding='utf-8')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(invalid), ''))
    w.open_project()
    assert w.test_warnings and w.dirty and w.editor.can_undo
    assert w.project.to_dict() == before and w.recovery_identity == identity
    assert release_and_find(w, tmp_path / 'recovery').identity == identity


@pytest.mark.parametrize('action', ['open', 'new'])
def test_accepted_project_replacement_starts_independent_recovery(window, tmp_path, monkeypatch, action):
    w = window
    edit_caption(w); w.autosave_recovery()
    old_identity = w.recovery_identity
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Discard)
    path = tmp_path / 'other.lumen'
    Project(name='Opened').save(path)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(path), ''))
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a: ('1280 × 720 · 30 fps', True))
    getattr(w, action + '_project')()
    assert not w.dirty and not w.editor.can_undo
    assert w.recovery_identity != old_identity and available(tmp_path / 'recovery') == []
    edit_caption(w, 'Second session'); w.autosave_recovery()
    entry = release_and_find(w, tmp_path / 'recovery')
    assert entry.identity != old_identity
    assert entry.original_path == (str(path) if action == 'open' else None)


def test_restore_is_explicit_unsaved_copy_with_clean_undo_and_claim(window, tmp_path, monkeypatch):
    w = window
    root = tmp_path / 'recovery'
    original = tmp_path / 'original.lumen'
    Project(name='Original saved version').save(original)
    original_bytes = original.read_bytes()
    entry, snapshot = orphan(root, original_path=original)
    # Listing and showing the manager must not change the current project.
    before = w.project.to_dict()
    w.open_recovery()
    assert w.project.to_dict() == before and not w.dirty
    edit_caption(w, 'To be explicitly replaced')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Discard)
    w.preview_paths['graded'] = tmp_path / 'old-preview.mp4'
    w.timeline.playhead = 5
    assert w.restore_recovery(entry)
    assert w.project.to_dict() == snapshot.to_dict()
    assert w.project_path is None and w.dirty and w.recovery_identity == entry.identity
    assert not w.editor.can_undo and not w.editor.can_redo
    assert w.selection == '' and w.timeline.playhead == 0 and not w.preview_paths
    assert original.read_bytes() == original_bytes
    assert available(root) == []
    before_files = tree_bytes(root)
    w.autosave_recovery()
    assert tree_bytes(root) == before_files  # Restoring is not another revision.
    w.changed()
    assert w.dirty  # A restored copy has no saved baseline yet.


def test_restore_cancel_preserves_both_projects_and_releases_candidate(window, tmp_path):
    w = window
    root = tmp_path / 'recovery'
    entry, _ = orphan(root)
    edit_caption(w); w.autosave_recovery()
    before, identity = w.project.to_dict(), w.recovery_identity
    assert not w.restore_recovery(entry)
    assert w.dirty and w.project.to_dict() == before and w.recovery_identity == identity
    assert [e.identity for e in available(root)] == [entry.identity]
    release_and_find(w, root)
    assert {e.identity for e in available(root)} == {identity, entry.identity}


def test_stale_recovery_selection_never_discards_current_edits(window, tmp_path, monkeypatch):
    w = window
    entry, _ = orphan(tmp_path / 'recovery')
    edit_caption(w); w.autosave_recovery()
    before, identity = w.project.to_dict(), w.recovery_identity
    w.recovery_store.discard(entry.identity)
    w.recovery_store.release(entry.identity)
    confirmations = []
    monkeypatch.setattr(w, 'confirm_discard', lambda: confirmations.append(True) or True)
    assert not w.restore_recovery(entry)
    assert not confirmations and w.recovery_error
    assert w.dirty and w.project.to_dict() == before and w.recovery_identity == identity
    assert release_and_find(w, tmp_path / 'recovery').identity == identity


def test_active_job_prevents_restore_without_claim_or_change(window, tmp_path, monkeypatch):
    w = window
    entry, _ = orphan(tmp_path / 'recovery')
    before = w.project.to_dict()
    def unexpected(*a):
        pytest.fail('An active-job restore must not load or prompt to discard')
    monkeypatch.setattr(w.recovery_store, 'load', unexpected)
    monkeypatch.setattr(w, 'confirm_discard', unexpected)
    sentinel = object(); w.jobs.add(sentinel)
    assert not w.restore_recovery(entry)
    w.open_recovery(); w.recovery_dialog.items.setCurrentRow(0)
    assert not w.recovery_dialog.restore.isEnabled()
    assert w.project.to_dict() == before
    assert [e.identity for e in available(tmp_path / 'recovery')] == [entry.identity]
    w.jobs.remove(sentinel)
    w.recovery_dialog.update_buttons()
    assert w.recovery_dialog.restore.isEnabled()


def test_recovery_dialog_close_and_cancel_discard_keep_all_snapshots(window, tmp_path):
    w = window
    entry, _ = orphan(tmp_path / 'recovery')
    w.open_recovery(); dialog = w.recovery_dialog
    dialog.items.setCurrentRow(0)
    dialog.discard_selected()  # Default response is Cancel.
    assert [e.identity for e in available(tmp_path / 'recovery')] == [entry.identity]
    dialog.close_button.click()
    assert [e.identity for e in available(tmp_path / 'recovery')] == [entry.identity]
    w.open_recovery()
    assert w.recovery_dialog is dialog and dialog.items.count() == 1
    assert not w.dirty


def test_recovery_dialog_discard_targets_only_selected_identity(window, tmp_path, monkeypatch):
    w = window
    first, _ = orphan(tmp_path / 'recovery', 'First')
    second, _ = orphan(tmp_path / 'recovery', 'Second')
    edit_caption(w); w.autosave_recovery()
    current = w.recovery_identity
    w.open_recovery(); dialog = w.recovery_dialog
    index = next(i for i, e in enumerate(dialog.entries) if e.identity == first.identity)
    dialog.items.setCurrentRow(index)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Discard)
    dialog.discard_selected()
    assert [e.identity for e in available(tmp_path / 'recovery')] == [second.identity]
    assert w.dirty and w.recovery_identity == current
    assert release_and_find(w, tmp_path / 'recovery').identity == current


def test_close_cancel_keeps_snapshot_but_explicit_discard_cleans_it(window, tmp_path, monkeypatch):
    w = window
    edit_caption(w); w.autosave_recovery()
    before = tree_bytes(tmp_path / 'recovery')
    assert not w.close()
    assert w.dirty and w.recovery_timer.isActive()
    assert tree_bytes(tmp_path / 'recovery') == before
    other, _ = orphan(tmp_path / 'recovery', 'Another window')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Discard)
    assert w.close()
    assert not w.recovery_timer.isActive()
    assert [e.identity for e in available(tmp_path / 'recovery')] == [other.identity]


def test_corrupt_selected_snapshot_never_discards_current_edits(window, tmp_path, monkeypatch):
    w = window
    entry, _ = orphan(tmp_path / 'recovery')
    edit_caption(w); w.autosave_recovery()
    before, identity = w.project.to_dict(), w.recovery_identity
    entry.path.write_text('{truncated snapshot', encoding='utf-8')
    confirmations = []
    monkeypatch.setattr(w, 'confirm_discard', lambda: confirmations.append(True) or True)
    assert not w.restore_recovery(entry)
    assert not confirmations and w.recovery_error
    assert w.dirty and w.project.to_dict() == before and w.recovery_identity == identity
    assert entry.path.read_text(encoding='utf-8') == '{truncated snapshot'


def test_corrupt_only_dialog_row_requires_explicit_discard(window, tmp_path, monkeypatch):
    w = window
    entry, _ = orphan(tmp_path / 'recovery')
    entry.path.write_text('{truncated snapshot', encoding='utf-8')
    w.open_recovery(); dialog = w.recovery_dialog
    assert dialog.items.count() == 1 and dialog.entries == []
    dialog.items.setCurrentRow(0)
    assert not dialog.restore.isEnabled() and dialog.discard.isEnabled()
    assert 'could not be read' in dialog.notice.text()
    dialog.discard_selected()
    assert entry.path.exists()  # Cancel is not a deletion.
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Discard)
    dialog.discard_selected()
    assert not entry.path.exists() and dialog.items.count() == 0


def test_restored_copy_stays_claimed_until_successful_save_as(window, tmp_path, monkeypatch):
    w = window
    root = tmp_path / 'recovery'
    original = tmp_path / 'original.lumen'
    Project(name='Original saved cut').save(original)
    original_bytes = original.read_bytes()
    entry, project = orphan(root, original_path=original)
    assert w.restore_recovery(entry)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: ('', ''))
    assert not w.save_project() and w.dirty and w.recovery_identity == entry.identity
    assert available(root) == [] and entry.path.exists()
    target = tmp_path / 'recovered-copy.lumen'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(target), ''))
    assert w.save_project()
    assert w.project_path == target and not w.dirty
    assert Project.load(target).to_dict() == project.to_dict()
    assert not entry.path.exists() and available(root) == []
    assert original.read_bytes() == original_bytes


def test_repeated_restore_activation_cannot_delete_claimed_snapshot(window, tmp_path, monkeypatch):
    w = window
    entry, project = orphan(tmp_path / 'recovery')
    assert w.restore_recovery(entry)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Discard)
    before = entry.path.read_bytes()
    assert not w.restore_recovery(entry)
    assert entry.path.read_bytes() == before
    assert w.dirty and w.recovery_identity == entry.identity and w.project.to_dict() == project.to_dict()


def test_unavailable_recovery_root_keeps_editor_and_manager_nonmodal(app, tmp_path, monkeypatch):
    root = tmp_path / 'not-a-directory'
    root.write_bytes(b'Existing unrelated file must not be replaced')
    def unexpected_dialog(*a):
        pytest.fail('Unavailable recovery storage must not open a blocking message box')
    monkeypatch.setattr(QMessageBox, 'warning', unexpected_dialog)
    monkeypatch.setattr(QMessageBox, 'critical', unexpected_dialog)
    monkeypatch.setattr(QMessageBox, 'information', unexpected_dialog)
    settings = QSettings(str(tmp_path / 'unavailable.ini'), QSettings.Format.IniFormat)
    w = MainWindow(workspace_settings=settings, ui_language='en', recovery_root=root)
    try:
        w.show(); app.processEvents()
        assert w.isVisible() and w.centralWidget().isEnabled()
        assert 'attention' in w.recovery_button.text()
        w.open_recovery(); dialog = w.recovery_dialog
        app.processEvents()
        assert dialog.isVisible() and not dialog.isModal()
        assert QApplication.activeModalWidget() is None
        assert 'could not be read' in dialog.notice.text()
        assert not dialog.restore.isEnabled() and not dialog.discard.isEnabled()
        dialog.close()
        edit_caption(w, 'Edits are still possible')
        w.autosave_recovery()
        assert w.dirty and w.recovery_last_fingerprint is None
        assert w.recovery_timer.isActive() and w.recovery_error
        target = tmp_path / 'manual-save-still-works.lumen'
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(target), ''))
        assert w.save_project()
        assert not w.dirty and Project.load(target).captions[0].text == 'Edits are still possible'
        assert root.read_bytes() == b'Existing unrelated file must not be replaced'
    finally:
        w.dirty = False; w.closing = True; w.close(); app.processEvents()


def test_real_qt_timer_dispatch_writes_once_then_skips_unchanged_content(window):
    from PySide6.QtTest import QSignalSpy
    w = window
    assert w.recovery_timer.interval() == 30_000
    edit_caption(w)
    timer = w.recovery_timer
    timer.stop(); timer.setSingleShot(True); timer.setInterval(1)
    spy = QSignalSpy(timer.timeout)
    timer.start()
    assert spy.wait(2000) and spy.count() == 1
    assert w.recovery_last_fingerprint == project_fingerprint(w.project)
    entries, warnings = w.recovery_store.list_entries(include_active=True)
    assert not warnings and len(entries) == 1 and entries[0].generation == 1
    timer.start()
    assert spy.wait(2000) and spy.count() == 2
    repeated, warnings = w.recovery_store.list_entries(include_active=True)
    assert not warnings and repeated == entries


def test_recovery_details_show_selected_full_path_and_utc_time_without_markup(window, tmp_path):
    w = window
    original = tmp_path / ('long-project-name-' * 5 + '.lumen')
    entry, _ = orphan(tmp_path / 'recovery', 'Cut <b>literal</b>', original)
    w.open_recovery(); dialog = w.recovery_dialog
    dialog.items.setCurrentRow(0)
    assert dialog.details.isReadOnly()
    assert dialog.details.toPlainText() == str(original) + '\n' + entry.created_utc
    assert dialog.items.item(0).toolTip() == str(original)
    assert 'Cut <b>literal</b>' in dialog.items.item(0).text()
    assert entry.created_utc not in dialog.items.item(0).text()  # Compact local time in row.
    dialog.items.setCurrentRow(-1)
    assert dialog.details.toPlainText() == ''
    assert not dialog.restore.isEnabled() and not dialog.discard.isEnabled()


def test_unfinished_temp_only_session_is_explicitly_discardable_beside_valid_rows(window, tmp_path, monkeypatch):
    w = window
    root = tmp_path / 'recovery'
    valid, _ = orphan(root, 'Keep this completed recovery')
    identity = str(uuid.uuid4())
    unfinished = root / f'.cutcat-{identity}.{uuid.uuid4().hex}.tmp'
    unfinished.write_bytes(b'Interrupted local snapshot write')
    w.open_recovery(); dialog = w.recovery_dialog
    assert [e.identity for e in dialog.entries] == [valid.identity]
    assert dialog.unreadable == [identity] and dialog.items.count() == 2
    dialog.items.setCurrentRow(0)
    assert dialog.restore.isEnabled() and dialog.details.toPlainText()
    dialog.items.setCurrentRow(1)
    assert not dialog.restore.isEnabled() and dialog.discard.isEnabled()
    assert dialog.details.toPlainText() == ''
    dialog.discard_selected()
    assert unfinished.exists() and valid.path.exists()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Discard)
    dialog.discard_selected()
    assert not unfinished.exists() and valid.path.exists()
    assert dialog.unreadable == [] and dialog.items.count() == 1
    assert [e.identity for e in available(root)] == [valid.identity]


def test_successful_save_clears_old_recovery_error(window, tmp_path, monkeypatch):
    edit_caption(window)
    window.recovery_error = 'Previous temporary write failure'
    target = tmp_path / 'saved.lumen'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(target), ''))
    assert window.save_project()
    assert not window.recovery_error
    assert 'needs attention' not in window.recovery_button.text()

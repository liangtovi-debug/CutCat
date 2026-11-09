"""Named selections are metadata; owned late workers cannot edit stale state."""
from copy import deepcopy
from fractions import Fraction
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest

from lumen.i18n import set_locale
from lumen.model import Editor, Project
from lumen.source_ranges import SourceRangeBinding, range_frames
from lumen import ui_source_ranges as ranges_ui
from test_source_viewer_ui import app, window, opened, pump

BINDING = SourceRangeBinding(1, 100, 'a' * 64, 'b' * 64)


@pytest.fixture
def verification(monkeypatch):
    state = SimpleNamespace(gate=None, started=threading.Event(), changed=False, threads=[], calls=0)
    def verify(backend, session, record=None, cancel=None):
        state.threads.append(threading.get_ident()); state.calls += 1; state.started.set()
        if state.gate is not None: assert state.gate.wait(8)
        if state.changed: raise ValueError('Saved range source binding does not match; original range is preserved')
        start, end = session.selection() if record is None else range_frames(record, session.fps, session.total_frames)
        return SimpleNamespace(binding=BINDING, in_frame=start, out_frame=end,
            is_current=lambda current, project: current.token == session.token
                and current.generation == session.generation and current.matches(project))
    monkeypatch.setattr(ranges_ui, 'verify_source_range', verify)
    return state


def saved(window, app, verification):
    d = opened(window, app)
    d.seek(10); d.mark_in(); d.seek(19); d.mark_out()
    d.saved_range_name.setText('Take α <b>片段</b>')
    d.save_named_range(); pump(app, lambda: not window.jobs)
    assert len(window.project.source_ranges) == 1
    return d


def test_save_recall_rename_delete_one_undo_and_clip_independence(window, app, verification, tmp_path):
    before = deepcopy(window.project); revision = window.editor.revision
    d = saved(window, app, verification)
    after_save = deepcopy(window.project)
    assert window.editor.revision == revision + 1 and window.dirty
    entry = window.project.source_ranges[0]
    assert entry.name == 'Take α <b>片段</b>' and entry.media_id == 'source'
    assert window.project.clips == before.clips
    assert all(thread != threading.get_ident() for thread in verification.threads)
    assert 'Verified now' in d.saved_range_note.text()
    assert 'Saved ranges: 1' in window.bin.item(0).text()
    d.clear_marks(); old_revision = window.editor.revision
    d.recall_named_range(); pump(app, lambda: not window.jobs)
    assert d.session.selection() == (10, 20) and d.session.cursor == 10
    assert window.editor.revision == old_revision and window.project == after_save
    assert d.plan is None and not d.apply_button.isEnabled()
    d.saved_range_name.setText('Renamed 素材')
    d.rename_named_range()
    assert window.editor.revision == old_revision + 1
    assert window.project.source_ranges[0].name == 'Renamed 素材'
    d.rename_named_range(); assert window.editor.revision == old_revision + 1
    renamed = deepcopy(window.project)
    d.delete_named_range()
    assert window.editor.revision == old_revision + 2 and not window.project.source_ranges
    assert window.project.clips == before.clips
    window.undo(); assert window.project == renamed
    window.undo(); assert window.project == after_save
    window.undo(); assert window.project == before
    window.redo(); assert window.project == after_save
    path = tmp_path / 'ranges.lumen'; window.project.save(path)
    d.reject(); pump(app, lambda: not window.jobs)
    window.editor = Editor(Project.load(path)); window.refresh(); window.bin.setCurrentRow(0)
    fresh = opened(window, app)
    assert fresh.session.selection() == (0, 120)
    fresh.saved_range_choice.setCurrentIndex(1)
    assert 'Unverified this session' in fresh.saved_range_note.text()
    fresh.recall_named_range(); pump(app, lambda: not window.jobs)
    assert fresh.session.selection() == (10, 20)


@pytest.mark.parametrize('operation', ['save', 'recall'])
@pytest.mark.parametrize('change', ['cancel', 'close', 'selection', 'undo', 'relink', 'editor', 'marks', 'name', 'range', 'revision'])
def test_delayed_save_and_recall_cannot_commit_after_context_changes(window, app, verification, operation, change):
    d = saved(window, app, verification)
    d.clear_marks()
    verification.gate = threading.Event(); verification.started.clear()
    (d.save_named_range if operation == 'save' else d.recall_named_range)()
    pump(app, verification.started.is_set)
    job = d.job
    try:
        if change == 'cancel': d.cancel_work()
        elif change == 'close': d.reject()
        elif change == 'selection': window.bin.setCurrentRow(1)
        elif change == 'undo': window.undo()
        elif change == 'relink': window.project.media[0].path += '.new'
        elif change == 'editor': window.editor = Editor(deepcopy(window.project))
        elif change == 'marks': d.seek(5); d.mark_in()
        elif change == 'name': d.saved_range_name.setText('New name while worker is busy')
        elif change == 'range': d.saved_range_choice.setCurrentIndex(0)
        else: window.revision += 1
        d.check_current()
        assert job.cancel.is_set()
        expected = deepcopy(window.project); expected_marks = d.session.selection(); revision = window.editor.revision
    finally:
        verification.gate.set(); pump(app, lambda: not window.jobs)
    assert window.project == expected and window.editor.revision == revision
    assert d.session.selection() == expected_marks and d.plan is None


def test_newer_save_replaces_old_pending_only_one_transaction(window, app, verification):
    d = opened(window, app); d.saved_range_name.setText('First')
    verification.gate = threading.Event(); verification.started.clear()
    d.save_named_range(); pump(app, verification.started.is_set)
    before = window.editor.revision
    d.saved_range_name.setText('Second'); d.save_named_range()
    d.saved_range_name.setText('Latest'); d.seek(11); d.mark_in(); d.save_named_range()
    verification.gate.set(); pump(app, lambda: not window.jobs)
    assert len(window.project.source_ranges) == 1 and window.editor.revision == before + 1
    assert window.project.source_ranges[0].name == 'Latest'
    assert Fraction(window.project.source_ranges[0].in_num, window.project.source_ranges[0].in_den) == Fraction(11, 30)


def test_failed_recall_keeps_original_record_and_marks_then_can_retry(window, app, verification):
    d = saved(window, app, verification); d.clear_marks()
    before = deepcopy(window.project); marks = d.session.selection(); revision = window.editor.revision
    verification.changed = True
    d.recall_named_range(); pump(app, lambda: not window.jobs)
    assert window.project == before and window.editor.revision == revision and d.session.selection() == marks
    assert 'Unavailable; original bounds preserved' in d.saved_range_note.text()
    assert 'original range is preserved' in d.status.text()
    assert d.rename_range_button.isEnabled() and d.delete_range_button.isEnabled()
    verification.changed = False
    d.recall_named_range(); pump(app, lambda: not window.jobs)
    assert d.session.selection() == (10, 20) and window.project == before


def test_incompatible_current_grid_fails_without_clamp(window, app, verification):
    d = saved(window, app, verification)
    d.reject(); pump(app, lambda: not window.jobs)
    # No clips to constrain the changed rate. Retain the saved rational bounds.
    project = deepcopy(window.project); project.clips = []; project.fps_num = 25
    window.editor = Editor(project); window.refresh(); window.bin.setCurrentRow(0)
    fresh = opened(window, app); fresh.saved_range_choice.setCurrentIndex(1)
    before = deepcopy(project); marks = fresh.session.selection()
    fresh.recall_named_range(); pump(app, lambda: not window.jobs)
    assert window.project == before and fresh.session.selection() == marks
    assert 'Unavailable' in fresh.saved_range_note.text()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('font_size', [10, 15])
def test_saved_names_plain_text_bilingual_focus_and_narrow_scroll(window, app, verification, locale, font_size):
    d = saved(window, app, verification)
    state = d.session.selection(); project = deepcopy(window.project)
    d.saved_range_name.setText('Staged name <script>中</script>')
    font = QFont(d.font()); font.setPointSize(font_size); window.setFont(font); d.setFont(font)
    set_locale(locale); d.fit_available_screen(QRect(0, 0, 360, 420))
    for _ in range(4): app.processEvents()
    assert window.project == project and d.session.selection() == state
    assert d.saved_range_choice.currentText() == 'Take α <b>片段</b>'
    assert d.saved_range_name.text() == 'Staged name <script>中</script>'
    assert d.saved_range_note.textFormat() == Qt.TextFormat.PlainText
    assert d.scroll.horizontalScrollBar().maximum() == 0
    for control in (d.saved_range_choice, d.saved_range_name, d.save_range_button,
                    d.recall_range_button, d.rename_range_button, d.delete_range_button):
        assert control.accessibleName() and control.toolTip() if isinstance(control, type(d.save_range_button)) else control.accessibleName()
        d.scroll.ensureWidgetVisible(control); app.processEvents()
        assert d.scroll.viewport().rect().contains(control.mapTo(d.scroll.viewport(), control.rect().center()))
        assert control.font().pointSizeF() == font_size
    assert d.save_range_button.text() == ('保存命名选区' if locale == 'zh-CN' else 'Save named range')
    d.activateWindow(); d.saved_range_name.setFocus(); app.processEvents()
    QTest.keyClick(d.saved_range_name, Qt.Key.Key_I)
    QTest.keyClick(d.saved_range_name, Qt.Key.Key_O)
    QTest.keyClick(d.saved_range_name, Qt.Key.Key_Return); app.processEvents()
    assert d.session.selection() == state and window.project == project and not window.jobs


def test_metadata_filter_and_refresh_do_not_read_source(window, app, verification, monkeypatch):
    from lumen.ui_media_bins import visible_media
    from pathlib import Path
    d = saved(window, app, verification)
    def no_io(*a, **k): raise AssertionError('GUI metadata must not read media')
    monkeypatch.setattr(Path, 'stat', no_io); monkeypatch.setattr(Path, 'open', no_io)
    d._refresh_saved_ranges(); d._refresh_text()
    assert window._media_result_text(window.project.media[0]).endswith('Saved ranges: 1')
    # Still a timeline-specific filter; a saved-only source remains Unused.
    project = deepcopy(window.project); project.clips = []
    assert [m.id for m in visible_media(project, usage='used')] == []
    assert 'source' in [m.id for m in visible_media(project, usage='unused')]


def test_shorter_than_one_frame_still_allows_metadata_management(window, app, verification):
    d = saved(window, app, verification)
    d.reject(); pump(app, lambda: not window.jobs)
    project = deepcopy(window.project); project.clips = []
    project.media[0].duration = 0.001
    window.editor = Editor(project); window.refresh(); window.bin.setCurrentRow(0)
    fresh = opened(window, app)
    fresh.saved_range_choice.setCurrentIndex(1)
    assert fresh.session.total_frames == 0
    assert not fresh.save_range_button.isEnabled() and not fresh.recall_range_button.isEnabled()
    assert not fresh.preview_button.isEnabled() and not fresh.inspect_button.isEnabled()
    assert fresh.rename_range_button.isEnabled() and fresh.delete_range_button.isEnabled()
    fresh.saved_range_name.setText('Retained short source'); fresh.rename_named_range()
    assert window.project.source_ranges[0].name == 'Retained short source'
    fresh.delete_named_range(); assert not window.project.source_ranges

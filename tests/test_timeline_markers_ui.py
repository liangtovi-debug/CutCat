"""Fixed-frame annotations: staged edits, stable IDs, narrow UI and cache currency."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtTest import QTest

from lumen.i18n import set_locale, tr
from lumen.model import Editor, Project
from lumen.ui_timeline_markers import (MARKER_SCOPE, MARKER_BOUNDS, MarkerEditDialog,
    marker_tooltip, marker_description)
from lumen.ui_timeline_markers import marker_comment_text
from test_master_gain_ui import app, window


def add(window, name='First <b>片段</b>', start=10, end=None, color='yellow', comment='Comment α\nline two'):
    dialog = window.edit_timeline_marker(range_marker=end is not None)
    dialog.name.setText(name); dialog.comment.setPlainText(comment)
    dialog.start.setText(str(start)); dialog.color.setCurrentIndex(dialog.color.findData(color))
    if end is not None: dialog.end.setText(str(end))
    dialog.apply_marker()
    assert dialog._closed_flag, dialog.status.text()
    return window.project.timeline_markers[-1]


def choose(dialog, marker_id):
    for row in range(dialog.marker_list.count()):
        item = dialog.marker_list.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == marker_id:
            dialog.marker_list.setCurrentItem(item); return
    raise AssertionError('marker not visible')


def test_add_edit_noop_delete_and_metadata_history(window):
    before = deepcopy(window.project); revision = window.revision
    browser = window.open_timeline_markers()
    assert window.open_timeline_markers() is browser
    window.preview_revision = window.revision; window.loaded_preview_mode = window.preview_mode
    marker = add(window); after_add = deepcopy(window.project)
    assert window.dirty and len(window.editor._undo) == 1
    assert window.revision == revision + 1 and window.preview_revision == window.revision
    assert after_add.clips == before.clips and after_add.duration_frames == before.duration_frames
    assert browser.selected_record().id == marker.id
    draft = window.edit_timeline_marker(marker.id); draft.apply_marker(); draft.apply_marker()
    assert len(window.editor._undo) == 1 and window.project == after_add
    draft = window.edit_timeline_marker(marker.id)
    draft.name.setText('Renamed'); draft.comment.setPlainText('New note'); draft.range.setChecked(True)
    draft.end.setText('20'); draft.color.setCurrentIndex(draft.color.findData('purple')); draft.apply_marker()
    after_edit = deepcopy(window.project)
    assert len(window.editor._undo) == 2 and after_edit.timeline_markers[0].id == marker.id
    assert browser.selected_record().id == marker.id
    browser.delete_selected(); browser.delete_selected()
    assert len(window.editor._undo) == 3 and not window.project.timeline_markers
    assert window.preview_revision == window.revision
    window.undo(); assert window.project == after_edit and window.preview_revision == window.revision
    window.undo(); assert window.project == after_add and window.preview_revision == window.revision
    window.undo(); assert window.project == before and window.preview_revision == window.revision
    window.redo(); assert window.project == after_add and window.preview_revision == window.revision


@pytest.mark.parametrize('dismiss', ['cancel', 'escape', 'close'])
def test_cancel_and_reopen_discard_draft(window, dismiss):
    before = deepcopy(window.project)
    dialog = window.add_timeline_marker_at_playhead(); dialog.name.setText('Unsaved')
    if dismiss == 'cancel': dialog.cancel_button.click()
    elif dismiss == 'escape': QTest.keyClick(dialog, Qt.Key.Key_Escape)
    else: dialog.close()
    dialog.apply_marker()
    assert dialog._closed_flag and not dialog.timer.isActive() and window.project == before
    assert not window.editor.can_undo
    reopened = window.add_timeline_marker_at_playhead()
    assert reopened is not dialog and reopened.name.text() == ''


@pytest.mark.parametrize('change', ['edit', 'undo', 'redo', 'editor', 'project', 'revision', 'remove'])
def test_stale_draft_never_rebases_or_revives(window, change):
    marker = add(window); window.editor.add_timeline_marker('Second', 20); window.marker_metadata_changed()
    if change == 'redo': window.undo()
    dialog = window.edit_timeline_marker(marker.id); dialog.name.setText('Stale draft')
    if change == 'edit': window.editor.set_master_gain(-2); window.changed()
    elif change == 'undo': window.undo()
    elif change == 'redo': window.redo()
    elif change == 'editor': window.editor = Editor(deepcopy(window.project)); window.refresh()
    elif change == 'project': window.editor.project = deepcopy(window.project); window.refresh()
    elif change == 'remove': window.editor.delete_timeline_marker(marker.id); window.marker_metadata_changed()
    else: window.revision += 1
    before = deepcopy(window.project); revision = window.editor.revision
    dialog.check_current(); assert dialog._stale and not dialog.apply_button.isEnabled()
    dialog.apply_marker()
    assert window.project == before and window.editor.revision == revision
    assert 'Project changed' in dialog.status.text()


@pytest.mark.parametrize('value', ['-1', '1.5', '1e3', '9007199254740992', '', 'true'])
def test_bad_exact_frame_input_never_clamps_or_commits(window, value):
    dialog = window.add_timeline_marker_at_playhead(); dialog.name.setText('Point'); dialog.start.setText(value)
    before = deepcopy(window.project); dialog.apply_marker()
    assert window.project == before and not window.editor.can_undo and not dialog._closed_flag
    assert 'blocked' in dialog.status.text()
    dialog.start.setText('9007199254740991'); dialog.apply_marker()
    assert dialog._closed_flag and window.project.timeline_markers[0].start_frame == 9007199254740991
    assert window.project.duration_frames == before.duration_frames


@pytest.mark.parametrize('field', ['start', 'end'])
@pytest.mark.parametrize('value', ['10000000000000000', '0' * 70 + '10'],
                         ids=['seventeen-digits', 'long-leading-zeroes'])
def test_long_frame_draft_is_not_truncated_to_different_valid_coordinate(window, field, value):
    dialog = window.edit_timeline_marker(range_marker=True); dialog.name.setText('Range')
    dialog.start.setText('10'); dialog.end.setText('20')
    getattr(dialog, field).setText(value); assert getattr(dialog, field).text() == value
    before = deepcopy(window.project); revision = window.editor.revision
    dialog.apply_marker()
    assert not dialog._closed_flag and window.project == before and window.editor.revision == revision
    getattr(dialog, field).setText('10' if field == 'start' else '20'); dialog.apply_marker()
    assert dialog._closed_flag and window.project.timeline_markers[0].start_frame == 10
    assert window.project.timeline_markers[0].end_frame == 20


def test_invalid_range_and_comment_are_correctable_without_mutation(window):
    dialog = window.edit_timeline_marker(range_marker=True); dialog.name.setText('Range')
    dialog.start.setText('10'); dialog.end.setText('10'); dialog.apply_marker()
    assert not window.editor.can_undo and not dialog._closed_flag
    dialog.end.setText('20'); dialog.comment.setPlainText('x' * 4097); dialog.apply_marker()
    assert not window.editor.can_undo and not dialog._closed_flag
    dialog.comment.setPlainText('Valid'); dialog.apply_marker()
    assert dialog._closed_flag and len(window.editor._undo) == 1


@pytest.mark.parametrize('too_long', ['\U0001f431' * 127 + 'AB', '\U0001f431' * 129, 'x' * 129],
                         ids=['mixed-129-codepoints', 'emoji-129-codepoints', 'ascii-129-codepoints'])
def test_maximum_unicode_name_survives_noop_and_overlimit_is_correctable(window, too_long):
    original = '\U0001f431' * 128
    marker = window.editor.add_timeline_marker(original, 10)
    window.marker_metadata_changed()
    before = deepcopy(window.project); revision = window.editor.revision
    dialog = window.edit_timeline_marker(marker.id)
    assert dialog.name.text() == original
    dialog.apply_marker()
    assert dialog._closed_flag and window.project == before and window.editor.revision == revision
    dialog = window.edit_timeline_marker(marker.id)
    dialog.name.setText(too_long); assert dialog.name.text() == too_long
    dialog.apply_marker()
    assert not dialog._closed_flag and window.project == before and window.editor.revision == revision
    dialog.name.setText(original); dialog.apply_marker()
    assert dialog._closed_flag and window.project == before and window.editor.revision == revision


@pytest.mark.parametrize('comment', ['French\u00a0punctuation\nline', '\ufeffLeading BOM', '\ufeff',
                                    'Interior\ufeffBOM and\u00a0NBSP\n\t\U0001f431'])
def test_unicode_comment_exactness_survives_noop_and_real_text_edits(window, comment):
    marker = window.editor.add_timeline_marker('Unicode comment', 10, comment=comment)
    window.marker_metadata_changed()
    before = deepcopy(window.project); revision = window.editor.revision
    dialog = window.edit_timeline_marker(marker.id)
    assert marker_comment_text(dialog.comment) == comment
    assert not dialog.comment.document().isUndoAvailable()
    dialog.apply_marker()
    assert dialog._closed_flag and window.project == before and window.editor.revision == revision
    dialog = window.edit_timeline_marker(marker.id)
    cursor = dialog.comment.textCursor(); cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText(' appended'); dialog.comment.setTextCursor(cursor)
    assert marker_comment_text(dialog.comment) == comment + ' appended'
    dialog.apply_marker()
    assert dialog._closed_flag and window.project.timeline_markers[0].comment == comment + ' appended'
    assert window.editor.revision == revision + 1
    window.undo(); assert window.project == before


@pytest.mark.parametrize('name', ['\ufeff', '\ufeffLeading BOM', '\ufeff' + 'e\u0301' * 62 + 'x'])
def test_leading_bom_name_survives_noop_and_real_edits(window, name):
    marker = window.editor.add_timeline_marker(name, 10)
    window.marker_metadata_changed(); before = deepcopy(window.project)
    revision = window.editor.revision
    dialog = window.edit_timeline_marker(marker.id)
    assert dialog.name.text() == name and not dialog.name.isUndoAvailable()
    dialog.name.undo(); assert dialog.name.text() == name
    dialog.apply_marker()
    assert dialog._closed_flag and window.project == before and window.editor.revision == revision
    dialog = window.edit_timeline_marker(marker.id)
    dialog.name.setCursorPosition(len(name.encode('utf-16-le')) // 2); dialog.name.insert('X')
    assert dialog.name.text() == name + 'X'
    dialog.apply_marker()
    assert dialog._closed_flag and window.project.timeline_markers[0].name == name + 'X'
    assert window.editor.revision == revision + 1
    window.undo(); assert window.project == before


def test_same_id_record_replacement_without_revision_invalidates_draft(window):
    marker = add(window)
    dialog = window.edit_timeline_marker(marker.id); dialog.name.setText('Stale')
    changed = replace(marker, name='Externally replaced')
    window.project.timeline_markers[0] = changed
    revision = window.editor.revision
    dialog.check_current(); assert dialog._stale
    dialog.apply_marker()
    assert window.project.timeline_markers == [changed] and window.editor.revision == revision
    reopened = window.edit_timeline_marker(marker.id)
    assert reopened.name.text() == 'Externally replaced'
    reopened.name.setText('Current draft'); reopened.apply_marker()
    assert reopened._closed_flag and window.project.timeline_markers[0].name == 'Current draft'


def test_capture_uses_project_playhead_exact_exclusive_end_and_never_source_marks(window):
    window.seek_frame(12); dialog = window.edit_timeline_marker(range_marker=True)
    assert dialog.start.text() == '12' and dialog.end.text() == '13'
    window.seek_frame(23); dialog.capture_end_button.click()
    assert dialog.end.text() == '23' and dialog.start.text() == '12'
    window.seek_frame(15); dialog.capture_start_button.click()
    assert dialog.start.text() == '15' and dialog.end.text() == '23'
    dialog.name.setText('Range'); dialog.apply_marker()
    assert (window.project.timeline_markers[0].start_frame, window.project.timeline_markers[0].end_frame) == (15, 23)


def test_search_filter_stable_id_same_frame_plain_text_and_no_history(window):
    first = add(window, 'One <b>bold?</b>', 10, color='red', comment='<a href="https://x">literal</a>')
    second = add(window, 'Two', 10, 20, color='blue', comment='Needle 中')
    third = add(window, 'Three', 30, color='red')
    browser = window.open_timeline_markers(); before = deepcopy(window.project); revision = window.revision
    browser.search.setText('needle'); assert browser.marker_list.count() == 1
    choose(browser, second.id); assert browser.selected_record().id == second.id
    browser.edit_selected(); draft = window.marker_edit_dialog
    assert draft.marker_id == second.id and draft.name.text() == 'Two'; draft.reject()
    browser.search.clear(); choose(browser, first.id)
    assert browser.details.textFormat() == Qt.TextFormat.PlainText
    assert '<b>bold?</b>' in browser.details.text() and '&lt;b&gt;' in marker_tooltip(window.project, first)
    browser.color_filter.setCurrentIndex(browser.color_filter.findData('red'))
    assert browser.marker_list.count() == 2 and browser.selected_record().id == first.id
    browser.search.setText('Three'); assert browser.selected_record() is None and not browser.delete_button.isEnabled()
    browser.delete_selected(); assert window.project == before and window.revision == revision
    choose(browser, third.id); browser.search.clear()
    assert browser.selected_record().id == third.id and window.project == before


def test_navigation_in_range_start_only_no_wrap_and_out_of_range_never_seeks(window, monkeypatch):
    a = add(window, 'A', 5); b = add(window, 'B', 20, 30); add(window, 'Same frame', 20)
    over = add(window, 'Out', 55, 65)
    calls = []; original = window.seek_frame
    def seek(frame): calls.append(frame); original(frame)
    monkeypatch.setattr(window, 'seek_frame', seek)
    assert not window.navigate_timeline_marker(over.id) and calls == []
    window.timeline.playhead = 0
    assert window.next_timeline_marker() and window.timeline.playhead == 5
    assert window.next_timeline_marker() and window.timeline.playhead == 20
    calls.clear(); assert not window.next_timeline_marker() and calls == []
    assert window.previous_timeline_marker() and window.timeline.playhead == 5
    calls.clear(); assert not window.previous_timeline_marker() and calls == []
    assert window.navigate_timeline_marker(b.id) and window.timeline.playhead == 20
    assert window.selected_timeline_marker_id == b.id


def test_shorten_or_empty_project_keeps_bounds_status_and_fixed_fps_frames(window):
    marker = add(window, 'Long range', 20, 60)
    browser = window.open_timeline_markers(); choose(browser, marker.id)
    for clip in list(window.project.clips): window.editor.delete_clip(clip.id)
    window.changed(); browser.check_current()
    assert window.project.duration_frames == 0 and window.project.timeline_markers == [marker]
    assert 'OUT OF RANGE' in browser.marker_list.item(0).text()
    assert not browser.navigate_button.isEnabled() and browser.edit_button.isEnabled()
    window.editor.project = deepcopy(window.project); window.project.fps_num = 25; window.refresh()
    assert window.project.timeline_markers == [marker]
    assert browser._closed_flag


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('font_size', [10, 15])
@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_plain_text_narrow_dpi_keyboard_accessible_live_language(window, app, locale, font_size, mode):
    marker = add(window, '<b>名称</b>', 12, 22, color='cyan', comment='<script>literal</script>')
    window.set_workspace_mode(mode)
    browser = window.open_timeline_markers(); choose(browser, marker.id)
    dialog = window.edit_timeline_marker(marker.id); dialog.name.setText('Draft <b>值</b>')
    before = deepcopy(window.project)
    font = QFont(dialog.font()); font.setPointSize(font_size)
    window.setFont(font)
    for widget in (browser, dialog): widget.setFont(font)
    set_locale(locale)
    for widget in (browser, dialog): widget.fit_available_screen(QRect(0, 0, 360, 440))
    for _ in range(5): app.processEvents()
    assert dialog.name.text() == 'Draft <b>值</b>' and window.project == before
    assert browser.details.textFormat() == Qt.TextFormat.PlainText and '<script>' in browser.details.text()
    for widget in (browser, dialog):
        assert widget.scroll.horizontalScrollBar().maximum() == 0
        assert widget.width() <= 328 and widget.height() <= 376
    for control in (dialog.name, dialog.comment, dialog.color, dialog.start, dialog.end, dialog.range,
                    dialog.capture_start_button, dialog.capture_end_button):
        assert control.accessibleName()
        dialog.scroll.ensureWidgetVisible(control); app.processEvents()
        assert dialog.scroll.viewport().rect().contains(control.mapTo(dialog.scroll.viewport(), control.rect().center()))
        assert control.font().pointSizeF() == font_size
    assert dialog.apply_button.text() == ('应用' if locale == 'zh-CN' else 'Apply')
    dialog.activateWindow(); dialog.name.setFocus(); QTest.keyClick(dialog.name, Qt.Key.Key_Return)
    assert not dialog._closed_flag and window.project == before
    set_locale('en')


def test_metadata_refresh_preserves_completed_preview_but_inflight_callback_stales(window, monkeypatch):
    callbacks = []; activated = []
    monkeypatch.setattr(window, 'start_job', lambda title, operation, success, kind: callbacks.append(success))
    monkeypatch.setattr(window, 'activate_preview', lambda *args: activated.append(args))
    window.preview_revision = window.revision; window.loaded_preview_mode = window.preview_mode
    window.build_preview(); assert len(callbacks) == 1
    old = window.revision; add(window)
    assert window.revision == old + 1 and window.preview_revision == window.revision
    callbacks[0](Path('/tmp/stale-marker-test.mp4'))
    assert activated == [] and 'Edits changed while rendering' in window.preview_label.text()
    window.preview_revision = -1; add(window, 'Another', 20)
    assert window.preview_revision == -1


def test_metadata_browser_refresh_never_reads_sources(window, monkeypatch):
    marker = add(window); browser = window.open_timeline_markers()
    def forbidden(*args, **kwargs): raise AssertionError('Metadata must not read sources')
    monkeypatch.setattr(Path, 'stat', forbidden); monkeypatch.setattr(Path, 'open', forbidden)
    browser.search.setText('First'); browser.refresh_records(); browser.check_current()
    assert marker.name in marker_description(window.project, marker)
    assert browser.marker_list.count() == 1


def test_validated_large_marker_navigation_and_paint_do_not_rescan_text(window, monkeypatch):
    import lumen.timeline_markers as markers
    project = deepcopy(window.project)
    project.timeline_markers = [markers.TimelineMarker(f'perf{i:05}', 'M', 'x' * 150,
                                'yellow', i % 55) for i in range(10000)]
    window.editor = Editor(project); window.refresh()
    def forbidden(*args, **kwargs): raise AssertionError('UI must not revalidate marker text')
    monkeypatch.setattr(markers, 'validate_timeline_marker', forbidden)
    monkeypatch.setattr(markers, 'timeline_marker_in_bounds', forbidden)
    window.seek_frame(10)
    assert window.adjacent_timeline_marker(1).start_frame == 11
    assert window.adjacent_timeline_marker(-1).start_frame == 9
    assert window.navigate_timeline_marker(project.timeline_markers[20].id)
    assert window.timeline.playhead == 20
    assert not window.timeline.grab().isNull()


def test_browser_navigation_preserves_rows_without_rebuilding_list(window, monkeypatch):
    a = add(window, 'A', 10); b = add(window, 'B', 20)
    browser = window.open_timeline_markers(); browser.check_current()
    def forbidden(*args, **kwargs): raise AssertionError('Selection must not rebuild marker rows')
    monkeypatch.setattr(browser, 'refresh_records', forbidden)
    assert window.navigate_timeline_marker(a.id) and browser.selected_record().id == a.id
    assert window.navigate_timeline_marker(b.id) and browser.selected_record().id == b.id


def test_project_switch_and_window_close_close_both_dialogs(window):
    add(window); browser = window.open_timeline_markers(); draft = window.add_timeline_marker_at_playhead()
    window.editor = Editor(Project()); window.refresh()
    assert browser._closed_flag and draft._stale
    window.close_timeline_markers(); assert draft._closed_flag

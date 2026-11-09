"""Explicit source Insert mode, impact, owned workers and atomic UI lifecycle."""
from copy import deepcopy
from pathlib import Path
import ast
import threading

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest

from lumen.i18n import set_locale, tr_error
from lumen.model import Caption, Overlay, Project
from lumen import ui_source_viewer as ui
from test_source_viewer_ui import app, window, opened, planned, pump


def insert_plan(window, app, monkeypatch, *, downstream=False):
    if downstream:
        window.editor.add_clip('source', 0, 150)
        window.editor.add_clip('other', 1, 5)
        window.project.captions.append(Caption('caption', 22, 10, 'Independent'))
        window.project.overlays.append(Overlay('overlay', str((Path(window.project.media[0].path).parent / 'missing.png').resolve()), 'a' * 64, 23, 8))
        window.refresh(); window.bin.setCurrentRow(0)
    dialog = opened(window, app)
    monkeypatch.setattr(dialog.backend, 'verify_insert_sources',
                        dialog.backend.verify_overwrite_sources, raising=False)
    dialog.mode_choice.setCurrentIndex(dialog.mode_choice.findData('insert'))
    dialog.seek(10); dialog.mark_in(); dialog.seek(19); dialog.mark_out()
    window.timeline.playhead = 20; dialog.capture_playhead()
    dialog.preview_source_edit(); pump(app, lambda: not window.jobs)
    assert dialog.plan is not None, dialog.status.text()
    assert dialog.apply_button.isEnabled()
    return dialog


def test_default_overwrite_is_preserved_and_mode_switch_requires_new_preview(window, app):
    dialog = planned(window, app)
    assert dialog.mode_choice.currentData() == 'overwrite'
    assert dialog.insert_warning.isHidden()
    assert 'Overwrite' in dialog.mode_choice.currentText()
    old = dialog.plan; token = dialog._token(); before = deepcopy(window.project)
    dialog.mode_choice.setCurrentIndex(dialog.mode_choice.findData('insert'))
    assert dialog.plan is None and not dialog.apply_button.isEnabled()
    assert not dialog._current(token)
    assert dialog.insert_warning.isVisible() and dialog.insert_warning.font().bold()
    assert 'may lose synchronization' in dialog.insert_warning.text()
    assert 'not cross-track sync' in dialog.insert_warning.text()
    assert dialog.preview_button.text() == 'Preview insert impact'
    assert dialog.apply_button.text() == 'Apply insert once'
    dialog.apply_source_edit()
    assert window.project == before and not window.jobs
    dialog.mode_choice.setCurrentIndex(dialog.mode_choice.findData('overwrite'))
    assert dialog.plan is None and dialog.plan is not old
    assert dialog.preview_button.text() == 'Preview overwrite impact'


def test_insert_impact_and_apply_once_keep_other_tracks_annotations_and_gaps(window, app, monkeypatch, tmp_path):
    dialog = insert_plan(window, app, monkeypatch, downstream=True)
    before = deepcopy(window.project); revision = window.editor.revision
    assert 'THIS TRACK ONLY' in dialog.impact.text()
    assert 'Affected clips: 2' in dialog.impact.text()
    assert 'Whole downstream clips moved: 1' in dialog.impact.text()
    assert 'Keep ' in dialog.impact.text() and 'Move ' in dialog.impact.text()
    assert '[150, 270) → [160, 280)' in dialog.impact.text()
    assert 'Replace ' not in dialog.impact.text()
    assert all(cid in dialog.impact.accessibleDescription() for cid in dialog.plan.affected_clip_ids)
    dialog.apply_button.click(); dialog.apply_button.click(); dialog.apply_source_edit()
    pump(app, lambda: not window.jobs)
    assert window.editor.revision == revision + 1
    assert dialog.backend.verify_calls == 1 and dialog.plan is None
    assert not dialog.apply_button.isEnabled() and 'Insert applied once' in dialog.status.text()
    assert [(clip.start, clip.duration) for clip in sorted(window.project.clips, key=lambda c: c.start)
            if clip.track == 0] == [(0, 20), (20, 10), (30, 100), (160, 120)]
    assert window.project.captions == before.captions and window.project.overlays == before.overlays
    assert [c for c in window.project.clips if c.track == 1] == [c for c in before.clips if c.track == 1]
    after = deepcopy(window.project)
    path = tmp_path / 'insert.lumen'; window.project.save(path)
    assert Project.load(path) == after
    window.undo(); assert window.project == before
    window.redo(); assert window.project == after
    # The consumed plan cannot revive after Undo/Redo or a repeated click.
    dialog.apply_source_edit(); assert window.project == after and not window.jobs


@pytest.mark.parametrize('change', ['mode', 'cancel', 'close', 'selection', 'lock', 'undo', 'relink', 'editor'])
def test_insert_verification_late_completion_never_commits_stale_plan(window, app, monkeypatch, change):
    dialog = insert_plan(window, app, monkeypatch)
    gate = threading.Event(); dialog.backend.verify_gate = gate; dialog.backend.started.clear()
    dialog.apply_source_edit(); pump(app, dialog.backend.started.is_set)
    old_job = dialog.job
    assert not dialog.mode_choice.isEnabled() and dialog.stop_button.isEnabled()
    try:
        if change == 'mode': dialog.mode_choice.setCurrentIndex(dialog.mode_choice.findData('overwrite'))
        elif change == 'cancel': dialog.cancel_work()
        elif change == 'close': dialog.reject()
        elif change == 'selection': window.bin.setCurrentRow(1)
        elif change == 'lock': window.editor.set_track_edit_lock(0, True)
        elif change == 'undo': window.undo()
        elif change == 'relink': window.project.media[0].path += '.relinked'
        else:
            from lumen.model import Editor
            window.editor = Editor(deepcopy(window.project))
        dialog.check_current(); expected = deepcopy(window.project)
        assert old_job.cancel.is_set()
    finally:
        gate.set(); pump(app, lambda: not window.jobs)
    assert window.project == expected
    assert dialog.plan is None and not dialog.apply_button.isEnabled()


def test_mode_switch_while_impact_is_loading_cannot_display_old_proposal(window, app, monkeypatch):
    dialog = opened(window, app)
    dialog.mode_choice.setCurrentIndex(dialog.mode_choice.findData('insert'))
    dialog.seek(10); dialog.mark_in(); dialog.seek(19); dialog.mark_out()
    gate = threading.Event(); dialog.backend.gate = gate; dialog.backend.started.clear()
    dialog.preview_source_edit(); pump(app, dialog.backend.started.is_set)
    token = dialog._token()
    try:
        dialog.mode_choice.setCurrentIndex(dialog.mode_choice.findData('overwrite'))
        assert not dialog._current(token)
        assert dialog.job.cancel.is_set()
    finally:
        gate.set(); pump(app, lambda: not window.jobs)
    assert dialog.plan is None and not dialog.apply_button.isEnabled()
    assert 'Preview the impact before applying' in dialog.impact.text()


def test_insert_identity_failure_is_atomic_and_recoverable(window, app, monkeypatch):
    dialog = insert_plan(window, app, monkeypatch)
    before = deepcopy((window.project, window.editor._undo, window.editor._redo, window.editor.revision))
    dialog.backend.changed = True; dialog.apply_source_edit()
    pump(app, lambda: not window.jobs)
    assert (window.project, window.editor._undo, window.editor._redo, window.editor.revision) == before
    assert dialog.plan is None and not dialog.apply_button.isEnabled()
    assert 'Source changed' in dialog.status.text()
    dialog.backend.changed = False; dialog.preview_source_edit(); pump(app, lambda: not window.jobs)
    assert dialog.plan is not None
    dialog.apply_source_edit(); pump(app, lambda: not window.jobs)
    assert window.editor.revision == before[-1] + 1


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('font_size', [10, 15])
def test_insert_localization_warning_and_narrow_layout_retain_state(window, app, monkeypatch, locale, font_size):
    from PySide6.QtGui import QFont
    dialog = insert_plan(window, app, monkeypatch, downstream=True)
    old = dialog.plan; token = dialog._token(); selection = dialog.session.selection()
    font = QFont(dialog.font()); font.setPointSize(font_size)
    window.setFont(font); dialog.setFont(font)
    set_locale(locale); dialog.fit_available_screen(QRect(0, 0, 360, 420))
    # Font/style and translated word-wrap updates are queued separately by Qt.
    # Inspect the rendered layout, not a transient immediately after setFont.
    for _ in range(4):
        app.processEvents()
    assert dialog.plan is old and dialog._current(token) and dialog.session.selection() == selection
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
    assert dialog.insert_warning.wordWrap() and dialog.insert_warning.isVisible()
    assert dialog.insert_warning.font().bold()
    assert dialog.insert_warning.font().pointSizeF() == font_size
    assert dialog.mode_choice.font().pointSizeF() == font_size
    assert dialog.apply_button.font().pointSizeF() == font_size
    for control in (dialog.mode_choice, dialog.insert_warning, dialog.apply_button, dialog.close_button):
        dialog.scroll.ensureWidgetVisible(control); app.processEvents()
        point = control.mapTo(dialog.scroll.viewport(), control.rect().center())
        assert dialog.scroll.viewport().rect().contains(point)
    if locale == 'zh-CN':
        assert '插入' in dialog.mode_choice.currentText()
        assert '失去同步' in dialog.insert_warning.text()
        assert '整体顺移的后续片段：1' in dialog.impact.text()
        assert '移动 ' in dialog.impact.text() and '保留 ' in dialog.impact.text()
        assert dialog.apply_button.text() == '应用一次插入'
    else:
        assert 'Insert' in dialog.mode_choice.currentText()
        assert 'Whole downstream clips moved: 1' in dialog.impact.text()
        assert dialog.apply_button.text() == 'Apply insert once'
    dialog.activateWindow(); dialog.cursor_spin.setFocus(); app.processEvents()
    QTest.keyClick(dialog.cursor_spin, Qt.Key.Key_Return); app.processEvents()
    assert dialog.plan is old and not window.jobs


def test_insert_help_describes_scope_and_proof_limits():
    from lumen.ui_help import help_text
    text = help_text()
    assert 'Insert or Overwrite' in text and 'not cross-track sync insert' in text
    assert 'including fades' in text and 'whole-timeline source decoding' in text
    for bound in ('120 seconds', '30 minutes', '48 MiB', 'combined 30 seconds'):
        assert bound in text
    set_locale('zh-CN')
    assert '插入或覆盖' in help_text() and '不是跨轨同步插入' in help_text()
    assert '不因此解码整条时间线' in help_text()


def test_insert_model_errors_are_explicitly_translated():
    from lumen.i18n_catalog_source_overwrite import ERROR_ZH_CN
    tree = ast.parse((Path(ui.__file__).parent / 'source_insert.py').read_text())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                and isinstance(node.exc.func, ast.Name) and node.exc.func.id == 'ValueError'
                and node.exc.args and isinstance(node.exc.args[0], ast.Constant)):
            message = node.exc.args[0].value
            assert message in ERROR_ZH_CN
            assert tr_error(message) == message
            set_locale('zh-CN'); assert tr_error(message) != message; set_locale('en')


def test_shared_insert_guard_errors_name_the_current_operation_live(window, app, monkeypatch):
    dialog = insert_plan(window, app, monkeypatch)
    for message in (
        'Source insert does not support mixed-rate video; use source media at the exact project frame rate',
        'Insert CFR proof is limited to 30 seconds of selected and affected source ranges; source inspection remains available.',
        'Cannot prove insert source timing: diagnostic 123',
    ):
        dialog._set_error(message, wrapped=True)
        assert message in dialog.status.text()
        set_locale('zh-CN'); app.processEvents()
        assert '插入' in dialog.status.text() and '覆盖' not in dialog.status.text()
        if 'diagnostic' in message:
            assert 'diagnostic 123' in dialog.status.text()
        set_locale('en'); app.processEvents()
        assert message in dialog.status.text()


def test_all_shared_guard_variants_have_catalog_entries_without_rewriting_details():
    from lumen.source_insert import insert_error_message
    from lumen.i18n_catalog_source_overwrite import ERROR_ZH_CN
    for message in tuple(ERROR_ZH_CN):
        mapped = insert_error_message(message)
        if mapped != message:
            assert mapped in ERROR_ZH_CN
    detail = '/media/Source overwrite clip.mov; do not rename Overwrite'
    original = 'Cannot prove overwrite source timing: ' + detail
    assert insert_error_message(original) == 'Cannot prove insert source timing: ' + detail

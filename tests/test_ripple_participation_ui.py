"""Explicit immutable ripple preview: choices, history, invalidation and layout."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from string import Formatter
import ast

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QInputDialog

from lumen.i18n import set_locale, tr_error
from lumen.model import Caption, Clip, Editor, Overlay, Project
from lumen.ui_ripple_delete import open_ripple_delete
from test_speed_ui import app, window


def prepared(window, app, *, preview=False):
    set_locale('en')
    p = window.project
    p.clips = [Clip('selected', 'm', 0, 10, 5, 5),
        Clip('before', 'm', 0, 2, 0, 8), Clip('later', 'm', 0, 20, 30, 8),
        Clip('follow-picture', 'm', 1, 15, 40, 12),
        Clip('follow-audio', 'm', 2, 25, 50, 8),
        Clip('fixed-audio', 'm', 3, 10, 0, 70)]
    p.captions = [Caption('early-caption', 0, 10, 'Fixed'), Caption('caption', 15, 8, 'Move')]
    p.overlays = [Overlay('early-sticker', str((Path(window.project.media[0].path).parent / 'missing.png').resolve()), 'a' * 64, 0, 10),
                  Overlay('sticker', str((Path(window.project.media[0].path).parent / 'missing.png').resolve()), 'a' * 64, 20, 8)]
    p.validate(); window.refresh(); window.select_clip('selected'); window.show(); app.processEvents()
    d = open_ripple_delete(window)
    if preview:
        d.preview_delete(); assert d.plan is not None, d.impact.text()
    return d


def test_entry_default_mandatory_target_committed_interval_no_media_work(window, app):
    d = prepared(window, app)
    assert d._participation() == ((0,), False, False)
    assert d.track_checks[0].isChecked() and not d.track_checks[0].isEnabled()
    d.track_checks[0].setChecked(False)
    assert d.track_checks[0].isChecked() and d._participation()[0] == (0,)
    before = deepcopy(window.project)
    window.start_spin.setValue(100); window.duration_spin.setValue(2)
    d.preview_button.click()
    assert (d.plan.start, d.plan.end, d.plan.duration) == (10, 15, 5)
    assert window.project == before and not window.jobs and not window.editor.can_undo
    assert 'staged inspector timing is not used' in d.timing_note.text()
    assert 'can lose synchronization' in d.warning.text()
    assert 'not persistent Sync Lock' in d.transaction.text()
    d.cancel_button.click(); window.ripple_participants_button.click()
    assert window.ripple_delete_dialog is not d


@pytest.mark.parametrize('tracks,captions,stickers', [(t,c,s) for t in (False,True) for c in (False,True) for s in (False,True)])
def test_each_explicit_subset_has_complete_impact_one_undo_reopen(window, app, tmp_path, tracks, captions, stickers):
    d = prepared(window, app)
    d.track_checks[1].setChecked(tracks); d.track_checks[2].setChecked(tracks)
    d.follow_captions_check.setChecked(captions); d.follow_stickers_check.setChecked(stickers)
    before = deepcopy(window.project); rev = window.editor.revision
    d.preview_delete(); plan = d.plan
    assert plan and len(plan.item_impacts) == 10
    assert 'Delete clip: selected' in d.impact.text() and 'Fixed clip: fixed-audio' in d.impact.text()
    assert 'Move clip: later' in d.impact.text()
    d.apply_button.click(); d.apply_delete()
    assert d._closed_flag and window.editor.revision == rev + 1
    assert len(window.editor._undo) == 1 and window.selected_clip() is None
    assert window.editor._clip('later').start == 15
    assert window.editor._clip('follow-picture').start == (10 if tracks else 15)
    assert window.editor._clip('follow-audio').start == (20 if tracks else 25)
    assert window.editor._clip('fixed-audio') == before.clips[-1]
    assert window.project.captions[1].start == (10 if captions else 15)
    assert window.project.overlays[1].start == (15 if stickers else 20)
    assert window.project.duration_frames == 80  # Fixed extent, not old duration - D.
    saved = tmp_path/'ripple.lumen'; window.project.save(saved)
    assert Project.load(saved) == window.project
    after = deepcopy(window.project)
    window.undo(); assert window.project == before
    window.redo(); assert window.project == after


@pytest.mark.parametrize('dismiss', ['cancel','escape','close','newer'])
def test_cancel_and_newer_discard_preview_and_participation(window, app, dismiss):
    d = prepared(window, app, preview=True)
    before = deepcopy(window.project); d.track_checks[2].setChecked(True); d.preview_delete()
    if dismiss == 'cancel': d.cancel_button.click()
    elif dismiss == 'escape': QTest.keyClick(d, Qt.Key.Key_Escape)
    elif dismiss == 'close': d.close()
    else: open_ripple_delete(window)
    assert d._closed_flag and d.plan is None and not d.timer.isActive()
    d.apply_delete(); assert window.project == before and not window.editor.can_undo
    fresh = open_ripple_delete(window)
    assert fresh._participation() == ((0,), False, False) and fresh.plan is None


@pytest.mark.parametrize('change', ['selection','selection_aba','multiselect','caption_selection','edit','lock','relink','undo_aba','editor','session','project','direct_payload'])
def test_stale_context_latches_without_timer_race(window, app, change):
    d = prepared(window, app, preview=True)
    if change == 'selection': window.select_clip('later')
    elif change == 'selection_aba': window.select_clip('later'); window.select_clip('selected')
    elif change == 'multiselect': window.set_selected_clip_ids(('selected','later'))
    elif change == 'caption_selection': window.active_selection_type = 'caption'
    elif change == 'edit': window.editor.update_clip('later', gain_db=-2)
    elif change == 'lock': window.editor.set_track_edit_lock(1, True)
    elif change == 'relink': window.project.media[0].path = '/other-source.mp4'
    elif change == 'undo_aba': window.editor.update_clip('later', gain_db=-2); window.editor.undo()
    elif change == 'editor': window.editor = Editor(deepcopy(window.project))
    elif change == 'session': window.editor.session_id = 'new'
    elif change == 'project': window.editor.project = deepcopy(window.project)
    else: window.project.captions[0].text = 'Uncommitted external mutation'
    before = deepcopy((window.project, window.editor._undo, window.editor._redo, window.editor.revision))
    d.apply_delete()
    assert d._stale and d.plan is None and not d.apply_button.isEnabled()
    assert 'Close and reopen' in d.status.text()
    d.context = d._context(); d.check_current(); d.preview_delete()
    assert not d.preview_button.isEnabled() and d.plan is None
    assert (window.project, window.editor._undo, window.editor._redo, window.editor.revision) == before


@pytest.mark.parametrize('action', ['new','open'])
def test_cancelled_new_open_attempt_still_invalidates_previous_operation(window, app, monkeypatch, action):
    d = prepared(window, app, preview=True); before = deepcopy(window.project)
    if action == 'new':
        monkeypatch.setattr(QInputDialog, 'getItem', lambda *a, **kw: ('',False)); window.new_project()
    else:
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: ('','')); window.open_project()
    assert d._closed_flag and d.plan is None
    d.apply_delete(); assert window.project == before and not window.editor.can_undo


def test_choice_change_invalidates_preview_and_required_track_cannot_be_unchecked(window, app):
    d = prepared(window, app, preview=True); token = d.plan_context
    d.follow_captions_check.setChecked(True)
    assert d.plan is None and not d.apply_button.isEnabled() and d._token() != token
    d.preview_delete(); first = d.plan; d.preview_delete()
    assert d.plan is not first and d.plan_context == d._token()
    d.track_checks[0].setChecked(False)
    assert d.track_checks[0].isChecked() and d.plan is None


def test_busy_then_failed_final_guard_never_reuses_plan(window, app, monkeypatch):
    d = prepared(window, app, preview=True); before = deepcopy(window.project)
    job = object(); window.jobs.add(job)
    d.check_current(); d.apply_delete()
    assert d.plan and not d.apply_button.isEnabled() and not d.preview_button.isEnabled()
    window.jobs.remove(job); d.check_current()
    def reject(_): raise ValueError('Final guard blocked')
    monkeypatch.setattr(window.editor, 'apply_ripple_delete', reject)
    d.apply_delete(); d.apply_delete()
    assert window.project == before and not window.editor.can_undo
    assert d.plan is None and not d.apply_button.isEnabled() and len(window.test_warnings) == 1


@pytest.mark.parametrize('kind', ['clip','caption','sticker'])
@pytest.mark.parametrize('start,duration', [(9,2),(10,1),(11,1),(14,2),(9,7)])
def test_any_intersection_blocks_but_explicit_exclusion_recovers(window, app, kind, start, duration):
    d = prepared(window, app)
    item = (window.editor._clip('follow-picture') if kind == 'clip' else
            window.project.captions[1] if kind == 'caption' else window.project.overlays[1])
    item.start, item.duration = start, duration
    if kind == 'caption': window.project.captions[0].duration = min(8, start)
    # Start a fresh operation after an external fixture mutation.
    d = open_ripple_delete(window)
    check = (d.track_checks[1] if kind == 'clip' else d.follow_captions_check
             if kind == 'caption' else d.follow_stickers_check)
    check.setChecked(True); before = deepcopy(window.project)
    d.preview_delete()
    assert d.plan is None and not d.apply_button.isEnabled()
    assert 'intersects the deleted interval' in d.impact.text()
    set_locale('zh-CN'); app.processEvents()
    assert '与删除区间相交' in d.impact.text()
    check.setChecked(False); d.preview_delete()
    assert d.plan is not None and window.project == before


@pytest.mark.parametrize('locale', ['en','zh-CN'])
@pytest.mark.parametrize('mode', ['simple','professional'])
@pytest.mark.parametrize('size', [10,15])
def test_bilingual_narrow_large_font_keyboard_and_plaintext_ids(window, app, locale, mode, size):
    d = prepared(window, app)
    window.editor._clip('later').id = '<b>Keep ID</b>'
    d = open_ripple_delete(window); d.preview_delete(); plan = d.plan
    window.set_workspace_mode(mode); set_locale(locale)
    font = QFont(d.font()); font.setPointSize(size); window.setFont(font); d.setFont(font)
    d.fit_available_screen(QRect(0,0,360,420))
    for _ in range(5): app.processEvents()
    assert d.plan is plan and not d._stale
    assert '<b>Keep ID</b>' in d.impact.text() and d.impact.textFormat() == Qt.TextFormat.PlainText
    assert d.scroll.horizontalScrollBar().maximum() == 0
    for control in (*d.track_checks, d.follow_captions_check, d.follow_stickers_check):
        d.scroll.ensureWidgetVisible(control); app.processEvents()
        assert control.accessibleName() and control.font().pointSize() == size
        assert d.scroll.viewport().rect().contains(control.mapTo(d.scroll.viewport(), control.rect().center()))
    for control in (d.preview_button,d.apply_button,d.cancel_button):
        assert control.accessibleName() and control.isVisible() and d.rect().contains(control.geometry())
    if locale == 'zh-CN':
        assert '波纹删除' in d.windowTitle() and '同步锁' in d.transaction.text()
        assert d.preview_button.text() == '预览' and d.follow_captions_check.text() == '顺移字幕'
        assert d.follow_stickers_check.text() == '顺移贴纸' and d.follow_captions_check.accessibleName() == '顺移字幕'
        assert '固定' in d.impact.text() and '片段轨道' in d.impact.text()
        assert '整数采样位移' in d.proof_note.text()
    d.scroll.ensureWidgetVisible(d.follow_captions_check); d.activateWindow()
    d.follow_captions_check.setFocus(); QTest.keyClick(d.follow_captions_check,Qt.Key.Key_Space)
    assert d.follow_captions_check.isChecked() and d.plan is None
    QTest.keyClick(d.follow_captions_check,Qt.Key.Key_Return)
    assert d.plan is None and not window.editor.can_undo


def test_multiselection_and_locked_target_have_no_entry(window, app):
    prepared(window, app).reject()
    window.set_selected_clip_ids(('selected','later')); window.refresh_inspector()
    assert not window.ripple_participants_button.isEnabled() and open_ripple_delete(window) is None
    window.select_clip('selected'); window.toggle_track_edit_lock(0,True)
    assert not window.ripple_participants_button.isEnabled() and open_ripple_delete(window) is None


def test_catalog_placeholders_errors_and_help():
    from lumen.i18n_catalog_ripple_delete import ZH_CN, ERROR_ZH_CN
    from lumen.ui_help import help_text
    fields = lambda value: {field for _,field,_,_ in Formatter().parse(value) if field}
    for source,translation in ZH_CN.items(): assert fields(source) == fields(translation)
    tree = ast.parse((Path(__file__).parents[1]/'lumen/ripple_delete.py').read_text())
    for node in ast.walk(tree):
        if isinstance(node,ast.Raise) and isinstance(node.exc,ast.Call) and node.exc.args and isinstance(node.exc.args[0],ast.Constant):
            message = node.exc.args[0].value
            assert message in ERROR_ZH_CN
            set_locale('zh-CN'); assert tr_error(message) != message
    set_locale('en'); assert 'wholly contained items' in help_text()
    set_locale('zh-CN'); assert '任意区间提取' in help_text()


def test_fractional_common_negative_shift_blocker_is_visible_without_decode(window, app):
    prepared(window, app).reject()
    p = window.project; p.fps_num, p.fps_den = 30000, 1001
    p.clips = [Clip('selected','m',0,1,0,1), Clip('first','m',2,2,0,5),
               Clip('second','m',3,3,0,5)]
    p.captions = []; p.overlays = []
    window.refresh(); window.select_clip('selected'); d = open_ripple_delete(window)
    d.track_checks[2].setChecked(True); d.track_checks[3].setChecked(True)
    before = deepcopy(p); d.preview_delete()
    assert d.plan is None and not window.jobs and window.project == before
    assert 'relative audio synchronization' in d.impact.text()
    set_locale('zh-CN'); app.processEvents()
    assert '相对同步' in d.impact.text() and '采样网格相位' in d.impact.text()


def test_preview_context_hash_never_canonicalizes_live_master_gain(window, app):
    prepared(window, app).reject()
    window.project.master_gain_db = 0  # Accepted numeric input, not normalized float.
    assert type(window.project.master_gain_db) is int
    d = open_ripple_delete(window); d.check_current(); d.preview_delete()
    assert d.plan and type(window.project.master_gain_db) is int
    d.reject(); assert type(window.project.master_gain_db) is int

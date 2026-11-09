"""Explicit track + frozen playhead, no implicit timeline-selection ownership."""
from copy import deepcopy
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
from lumen.ui_gap_close import open_gap_close
from test_speed_ui import app, window


def prepared(window, app, *, preview=False):
    set_locale('en')
    p = window.project
    p.clips = [Clip('left', 'm', 0, 2, 0, 8), Clip('right', 'm', 0, 15, 30, 8),
        Clip('later', 'm', 0, 28, 40, 8), Clip('follow-picture', 'm', 1, 15, 40, 12),
        Clip('follow-audio', 'm', 2, 25, 50, 8), Clip('fixed-audio', 'm', 3, 10, 0, 70)]
    p.captions = [Caption('early-caption', 0, 10, 'Fixed'), Caption('caption', 15, 8, 'Move')]
    p.overlays = [Overlay('early-sticker', str((Path(window.project.media[0].path).parent / 'missing.png').resolve()), 'a' * 64, 0, 10),
                  Overlay('sticker', str((Path(window.project.media[0].path).parent / 'missing.png').resolve()), 'a' * 64, 20, 8)]
    p.validate(); window.refresh(); window.seek_frame(12); window.show(); app.processEvents()
    d = open_gap_close(window)
    if preview:
        d.target_track.setCurrentIndex(1); d.capture_button.click(); d.preview_button.click()
        assert d.plan is not None, d.impact.text()
    return d


def capture(d, track=0):
    d.target_track.setCurrentIndex(track + 1); d.capture_button.click()


def test_menu_has_no_selection_or_import_track_inference(window, app):
    d = prepared(window, app); d.reject()
    window.track_choice.setCurrentIndex(3)
    window.select_clip('follow-audio'); window.gap_close_action.trigger()
    d = window.gap_close_dialog
    assert d.target_track.currentData() is None and d.track is None and d.playhead is None
    assert not d.capture_button.isEnabled() and not d.preview_button.isEnabled()
    assert not any(c.isChecked() for c in d.track_checks)
    before = deepcopy(window.project)
    capture(d); assert d.playhead == 12 and d.track == 0
    assert d._participation() == ((0,), False, False)
    window.start_spin.setValue(50); window.duration_spin.setValue(2)
    d.preview_button.click()
    assert (d.plan.track, d.plan.playhead, d.plan.start, d.plan.end) == (0,12,10,15)
    assert window.project == before and not window.jobs and not window.editor.can_undo
    assert 'not used' in d.timing_note.text() and 'can lose synchronization' in d.warning.text()
    assert 'Deleted clips: 0' in d.impact.text()
    assert 'Left clip: left' in d.impact.text() and 'Right clip: right' in d.impact.text()


def test_live_playhead_selection_and_import_destination_do_not_retarget(window, app):
    d = prepared(window, app, preview=True); plan = d.plan
    window.seek_frame(31); window.select_clip('later'); window.select_clip('left')
    window.track_choice.setCurrentIndex(2); d.check_current()
    assert not d._stale and d.plan is plan and d.playhead == 12
    assert 'frame 12' in d.capture_note.text()
    d.apply_button.click()
    assert window.editor._clip('right').start == 10 and window.editor._clip('later').start == 23


@pytest.mark.parametrize('tracks,captions,stickers', [(t,c,s) for t in (False,True) for c in (False,True) for s in (False,True)])
def test_subsets_full_impacts_atomic_undo_reopen(window, app, tmp_path, tracks, captions, stickers):
    d = prepared(window, app); capture(d)
    d.track_checks[1].setChecked(tracks); d.track_checks[2].setChecked(tracks)
    d.follow_captions_check.setChecked(captions); d.follow_stickers_check.setChecked(stickers)
    before = deepcopy(window.project); rev = window.editor.revision
    d.preview_close(); plan = d.plan
    assert plan and len(plan.item_impacts) == 10
    assert 'Fixed clip: fixed-audio' in d.impact.text() and 'Move clip: later' in d.impact.text()
    assert all(i.disposition in ('fixed','move') for i in plan.item_impacts)
    d.apply_button.click(); d.apply_close()
    assert d._closed_flag and window.editor.revision == rev + 1 and len(window.editor._undo) == 1
    assert {c.id for c in window.project.clips} == {c.id for c in before.clips}
    assert window.editor._clip('right').start == 10 and window.editor._clip('later').start == 23
    assert window.editor._clip('follow-picture').start == (10 if tracks else 15)
    assert window.editor._clip('follow-audio').start == (20 if tracks else 25)
    assert window.editor._clip('fixed-audio') == before.clips[-1]
    assert window.project.captions[1].start == (10 if captions else 15)
    assert window.project.overlays[1].start == (15 if stickers else 20)
    assert window.project.duration_frames == 80
    saved=tmp_path/'gap.lumen'; window.project.save(saved); assert Project.load(saved)==window.project
    after=deepcopy(window.project); window.undo(); assert window.project==before
    window.redo(); assert window.project==after


@pytest.mark.parametrize('dismiss', ['cancel','escape','close','newer'])
def test_dismissal_and_newer_dialog_discard_capability(window, app, dismiss):
    d = prepared(window, app, preview=True); before=deepcopy(window.project)
    if dismiss=='cancel':d.cancel_button.click()
    elif dismiss=='escape':QTest.keyClick(d, Qt.Key.Key_Escape)
    elif dismiss=='close':d.close()
    else:open_gap_close(window)
    assert d._closed_flag and d.plan is None and not d.timer.isActive()
    d.apply_close(); assert window.project==before and not window.editor.can_undo
    fresh=open_gap_close(window)
    assert fresh.track is None and fresh.playhead is None and fresh.plan is None


@pytest.mark.parametrize('change', ['edit','lock','relink','undo_aba','editor','session','project','direct_payload','neighbor_id'])
def test_stale_context_latches_before_timer(window, app, change):
    d=prepared(window,app,preview=True)
    if change=='edit':window.editor.update_clip('later',gain_db=-2)
    elif change=='lock':window.editor.set_track_edit_lock(1,True)
    elif change=='relink':window.project.media[0].path='/other-source.mp4'
    elif change=='undo_aba':window.editor.update_clip('later',gain_db=-2);window.editor.undo()
    elif change=='editor':window.editor=Editor(deepcopy(window.project))
    elif change=='session':window.editor.session_id='new'
    elif change=='project':window.editor.project=deepcopy(window.project)
    elif change=='neighbor_id':window.editor._clip('right').id='different-right'
    else:window.project.captions[0].text='direct mutation'
    before=deepcopy((window.project,window.editor._undo,window.editor._redo,window.editor.revision))
    d.apply_close()
    assert d._stale and d.plan is None and not d.apply_button.isEnabled()
    assert 'Close and reopen' in d.status.text()
    assert 'No current preview' in d.impact.text()
    d.context=d._context();d.check_current();d.capture_playhead();d.preview_close()
    assert not d.preview_button.isEnabled() and d.plan is None
    assert (window.project,window.editor._undo,window.editor._redo,window.editor.revision)==before


@pytest.mark.parametrize('action', ['new','open'])
def test_cancelled_new_open_attempt_closes_old_operation(window, app, monkeypatch, action):
    d=prepared(window,app,preview=True); before=deepcopy(window.project)
    if action=='new':
        monkeypatch.setattr(QInputDialog,'getItem',lambda *a,**kw:('',False));window.new_project()
    else:
        monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**kw:('',''));window.open_project()
    assert d._closed_flag and d.plan is None
    d.apply_close();assert window.project==before and not window.editor.can_undo


def test_recapture_track_change_and_participants_invalidate_preview(window, app):
    d=prepared(window,app,preview=True); token=d.plan_context
    d.capture_button.click();assert d.plan is None and d.playhead==12 and d._token()!=token
    d.preview_close();d.follow_captions_check.setChecked(True)
    assert d.plan is None and not d.apply_button.isEnabled()
    d.preview_close();d.track_checks[0].setChecked(False)
    assert d.track_checks[0].isChecked() and d.plan is None
    d.preview_close();d.target_track.setCurrentIndex(2)
    assert d.playhead is None and d.plan is None and d._participation()==((1,),True,False)
    assert not d.preview_button.isEnabled() and not d.track_checks[1].isEnabled()
    d.target_track.setCurrentIndex(1);assert d.playhead is None


def test_busy_and_failed_commit_cannot_reuse_plan(window, app, monkeypatch):
    d=prepared(window,app,preview=True);before=deepcopy(window.project)
    job=object();window.jobs.add(job);d.check_current();d.apply_close()
    assert d.plan and not d.capture_button.isEnabled() and not d.preview_button.isEnabled()
    assert not d.apply_button.isEnabled()
    window.jobs.remove(job);d.check_current()
    def reject(_):raise ValueError('Final guard blocked')
    monkeypatch.setattr(window.editor,'apply_gap_close',reject)
    d.apply_close();d.apply_close()
    assert window.project==before and not window.editor.can_undo
    assert d.plan is None and not d.apply_button.isEnabled() and len(window.test_warnings)==1


@pytest.mark.parametrize('playhead',[0,2,9,15,79])
def test_missing_gap_is_visible_without_mutation(window, app, playhead):
    d=prepared(window,app);window.seek_frame(playhead);capture(d);before=deepcopy(window.project)
    d.preview_close()
    assert d.plan is None and 'No interior gap' in d.impact.text() and not window.jobs
    set_locale('zh-CN');app.processEvents();assert '没有内部空隙' in d.impact.text()
    assert window.project==before and not window.editor.can_undo


@pytest.mark.parametrize('kind',['clip','caption','sticker'])
@pytest.mark.parametrize('start,duration',[(9,2),(10,1),(11,1),(14,2),(9,7)])
def test_intersection_blocking_and_explicit_exclusion(window,app,kind,start,duration):
    d=prepared(window,app)
    item=(window.editor._clip('follow-picture') if kind=='clip' else
          window.project.captions[1] if kind=='caption' else window.project.overlays[1])
    item.start,item.duration=start,duration
    if kind=='caption':window.project.captions[0].duration=min(8,start)
    d=open_gap_close(window);capture(d)
    check=d.track_checks[1] if kind=='clip' else d.follow_captions_check if kind=='caption' else d.follow_stickers_check
    check.setChecked(True);before=deepcopy(window.project);d.preview_close()
    assert d.plan is None and 'intersects the gap' in d.impact.text()
    set_locale('zh-CN');app.processEvents();assert '与空隙相交' in d.impact.text()
    check.setChecked(False);d.preview_close();assert d.plan is not None and window.project==before


@pytest.mark.parametrize('locale',['en','zh-CN'])
@pytest.mark.parametrize('mode',['simple','professional'])
@pytest.mark.parametrize('size',[10,15])
def test_locale_narrow_large_font_keyboard_plaintext(window,app,locale,mode,size):
    d=prepared(window,app);window.editor._clip('right').id='<b>Right ID</b>'
    d=open_gap_close(window);capture(d);d.preview_close();plan=d.plan
    window.set_workspace_mode(mode);set_locale(locale)
    font=QFont(d.font());font.setPointSize(size);window.setFont(font);d.setFont(font)
    d.fit_available_screen(QRect(0,0,360,420))
    for _ in range(5):app.processEvents()
    assert d.plan is plan and not d._stale
    assert '<b>Right ID</b>' in d.impact.text() and d.impact.textFormat()==Qt.TextFormat.PlainText
    assert d.scroll.horizontalScrollBar().maximum()==0
    for control in (d.target_track,d.capture_button,*d.track_checks,d.follow_captions_check,d.follow_stickers_check):
        d.scroll.ensureWidgetVisible(control);app.processEvents()
        assert control.accessibleName() and control.font().pointSize()==size
        assert d.scroll.viewport().rect().contains(control.mapTo(d.scroll.viewport(),control.rect().center()))
    for control in (d.preview_button,d.apply_button,d.cancel_button):
        assert control.accessibleName() and control.isVisible() and d.rect().contains(control.geometry())
    if locale=='zh-CN':
        assert d.windowTitle()=='关闭单个空隙' and d.capture_button.text()=='捕获播放头'
        assert '删除片段：0' in d.impact.text() and '整数采样位移' in d.proof_note.text()
        assert window.gap_close_action.text()=='关闭单个空隙…'
    d.scroll.ensureWidgetVisible(d.follow_captions_check);d.activateWindow()
    d.follow_captions_check.setFocus();QTest.keyClick(d.follow_captions_check,Qt.Key.Key_Space)
    assert d.follow_captions_check.isChecked() and d.plan is None
    QTest.keyClick(d.follow_captions_check,Qt.Key.Key_Return)
    assert d.plan is None and not window.editor.can_undo


def test_single_track_common_sample_mismatch_visible(window, app):
    prepared(window,app).reject();p=window.project;p.fps_num,p.fps_den=30000,1001
    p.clips=[Clip('left','m',0,0,0,1),Clip('right','m',0,2,0,5),Clip('later','m',0,8,0,5)]
    p.captions=[];p.overlays=[];window.refresh();window.seek_frame(1)
    d=open_gap_close(window);capture(d);before=deepcopy(p);d.preview_close()
    assert d.plan is None and 'relative audio synchronization' in d.impact.text()
    set_locale('zh-CN');app.processEvents();assert '相对同步' in d.impact.text()
    assert window.project==before and not window.jobs


def test_catalog_errors_placeholders_help_and_no_live_canonicalization(window,app):
    from lumen.i18n_catalog_gap_close import ZH_CN,ERROR_ZH_CN
    from lumen.ui_help import help_text
    fields=lambda value:{field for _,field,_,_ in Formatter().parse(value) if field}
    for source,translation in ZH_CN.items():assert fields(source)==fields(translation)
    tree=ast.parse((Path(__file__).parents[1]/'lumen/gap_close.py').read_text())
    for node in ast.walk(tree):
        if isinstance(node,ast.Raise) and isinstance(node.exc,ast.Call) and node.exc.args and isinstance(node.exc.args[0],ast.Constant):
            message=node.exc.args[0].value;assert message in ERROR_ZH_CN
            set_locale('zh-CN');assert tr_error(message)!=message
    set_locale('en');assert 'deletes zero clips' in help_text()
    set_locale('zh-CN');assert '一个内部空隙' in help_text()
    prepared(window,app).reject();window.project.master_gain_db=0
    d=open_gap_close(window);capture(d);d.preview_close();d.check_current()
    assert d.plan and type(window.project.master_gain_db) is int
    d.reject();assert type(window.project.master_gain_db) is int


def test_shortcut_scope_does_not_deny_implemented_tools():
    from lumen.ui_shortcuts import _SCOPE
    from lumen.i18n_catalog_shortcuts import ZH_CN
    assert 'general ripple trim' in _SCOPE and 'Source I/O marks and ripple delete exist' in _SCOPE
    assert 'no shortcut mapping' in _SCOPE and '已有源 I/O 标记和波纹删除' in ZH_CN[_SCOPE]
    doc=(Path(__file__).parents[1]/'docs/KEYBOARD_SHORTCUTS.md').read_text()
    assert 'single-gap closure exist' in doc and 'no shortcut mapping' in doc


def test_locked_target_blocks_preview_without_implicit_fallback(window,app):
    prepared(window,app).reject()
    window.toggle_track_edit_lock(0,True)
    d=open_gap_close(window);capture(d);before=deepcopy(window.project)
    d.preview_close()
    assert d.track==0 and d.plan is None and not d.apply_button.isEnabled()
    assert 'locked' in d.impact.text().lower()
    assert window.project==before and len(window.editor._undo)==1

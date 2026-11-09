"""Affine slip widgets: selected anchor, truthful ranges and guarded late jobs."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QDialog, QMenu

from lumen.model import AV_LINK_AFFINE_ALIGNMENT
from lumen.timing import ClipTimeMap
from test_linked_av_ui import app, window, pair_window, link, dialog, preview, held
from test_clip_copy_paste_ui import settle, state
from test_shortcuts_dispatch import activate


@pytest.fixture
def affine_window(pair_window, app):
    w=pair_window; link(w,app)
    v,a=w.editor._clip('video-id'),w.editor._clip('audio-id')
    v.time_map=ClipTimeMap(12,Fraction(167,4))
    a.start,a.duration,a.source_in=3,34,10
    a.time_map=ClipTimeMap(10,Fraction(87,2))
    w.project.av_links[0]=replace(w.project.av_links[0],alignment=AV_LINK_AFFINE_ALIGNMENT)
    w.project.validate();w.refresh();w.select_clip('video-id')
    return w


def expected(old, role, requested):
    result=deepcopy(old); clips={clip.id:clip for clip in old.clips}
    delta=Fraction(requested)-clips[role].source_in
    for clip in result.clips:
        if clip.id not in ('video-id','audio-id'):continue
        mapping=clips[clip.id].timing
        clip.source_in=mapping.source_in+delta
        clip.time_map=ClipTimeMap(clip.source_in,mapping.source_out+delta,mapping.speed)
    return result


@pytest.mark.parametrize('role', ['video','audio'])
@pytest.mark.parametrize('entry', ['direct','menu','inspector'])
def test_v2_entry_anchor_separate_ranges_preview_apply_and_exact_history(affine_window,app,role,entry):
    w=affine_window;w.select_linked_role(role);activate(app,w,w.timeline)
    old=deepcopy(w.project);before=state(w);depth=len(w.editor._undo)
    assert w.can_slip_linked_av() and w.linked_slip_button.isEnabled()
    w.shortcuts.update_enabled();assert w.shortcuts.actions['edit.joint_slip'].isEnabled()
    if entry=='menu':w.shortcuts.actions['edit.joint_slip'].trigger();d=w.linked_av_slip_dialog
    elif entry=='inspector':w.slip_source_edit.setText('20');w.apply_slip();d=w.linked_av_slip_dialog
    else:d=w.open_linked_av_slip()
    assert d is not None
    d.origin.setText('20');d.refresh_text()
    selected='video-id' if role=='video' else 'audio-id';delta=8 if role=='video' else 10
    assert f'anchor: {"V" if role=="video" else "A"}' in d.summary.text()
    assert f'delta: {delta}' in d.summary.text()
    assert f'V source [{12+delta}, {Fraction(167,4)+delta})' in d.impact.text()
    assert f'A source [{10+delta}, {Fraction(87,2)+delta})' in d.impact.text()
    assert 'Fixed timeline [5, 35)' in d.source.text() and 'Fixed timeline [3, 37)' in d.source.text()
    assert state(w)==before
    old_generation=w._job_generation;preview(w,app,d)
    assert w._job_generation==old_generation+1 and not d._stale and state(w)==before
    d.apply();d.apply();settle(w,app)
    assert d._closed_flag and w.project==expected(old,selected,20)
    assert w._job_generation==old_generation+2 and w.selection==selected
    assert len(w.editor._undo)==depth+1 and w.project.av_links==old.av_links
    w.undo();assert w.project==old
    w.redo();assert w.project==expected(old,selected,20) and not w.test_warnings


@pytest.mark.parametrize('guard', ['modal','popup','local-ime','shortcut-ime'])
@pytest.mark.parametrize('phase', ['preview','apply'])
def test_direct_methods_obey_transient_interaction_gates_without_disabling_preedit(affine_window,app,guard,phase):
    w=affine_window;d=dialog(w,app)
    if phase=='apply':preview(w,app,d)
    before=state(w);plan=d.plan;modal=popup=None
    try:
        if guard=='modal':modal=QDialog(w);modal.setModal(True);modal.show();app.processEvents()
        elif guard=='popup':popup=QMenu(d);popup.addAction('Hold');popup.popup(d.mapToGlobal(QPoint(20,20)));app.processEvents()
        elif guard=='shortcut-ime':w.shortcuts._composing=w.timeline
        else:
            activate(app,d,d.origin)
            QApplication.sendEvent(d.origin,QInputMethodEvent('未提交',[]))
            assert d._composing and d.origin.isEnabled()
        d.check_current();getattr(d,phase)()
        assert state(w)==before and not w.jobs and d.plan is plan
        assert not d.preview_button.isEnabled() and not d.apply_button.isEnabled()
        if guard=='local-ime':assert d.origin.isEnabled() and d._composing
    finally:
        if modal is not None:modal.reject()
        if popup is not None:popup.close()
        w.shortcuts._composing=None
        if guard=='local-ime':QApplication.sendEvent(d.origin,QInputMethodEvent('',[]))
        app.processEvents();d.check_current()
    assert d.preview_button.isEnabled() and d.plan is plan and state(w)==before
    assert not w.test_warnings


@pytest.mark.parametrize('guard', ['modal','popup','local-ime','shortcut-ime','playing','foreign-job','job-aba'])
def test_apply_worker_result_cannot_commit_through_new_interaction_or_job_context(affine_window,app,monkeypatch,guard):
    w=affine_window;d=dialog(w,app);preview(w,app,d)
    started,release=held(monkeypatch);before=state(w);modal=popup=None
    d.apply();assert started.wait(2)
    try:
        if guard=='modal':modal=QDialog(w);modal.setModal(True);modal.show();app.processEvents()
        elif guard=='popup':popup=QMenu(d);popup.addAction('Hold');popup.popup(d.mapToGlobal(QPoint(20,20)));app.processEvents()
        elif guard=='local-ime':QApplication.sendEvent(d.origin,QInputMethodEvent('未提交',[]))
        elif guard=='shortcut-ime':w.shortcuts._composing=w.timeline
        elif guard=='playing':
            monkeypatch.setattr(w.player,'playbackState',lambda:QMediaPlayer.PlaybackState.PlayingState)
            w.player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
        elif guard=='foreign-job':w.jobs.add('foreign-job')
        else:w._job_generation+=1
        release.set()
        # settle expects no foreign sentinel; retain it until the real worker's
        # result was processed, then remove without undoing its sticky expiry.
        if guard=='foreign-job':
            import time
            deadline=time.monotonic()+5
            while d.job is not None and time.monotonic()<deadline:app.processEvents();time.sleep(.005)
            assert d.job is None
            w.jobs.discard('foreign-job')
        settle(w,app)
        assert state(w)==before and d.preview_receipt is None and not d._closed_flag
        if guard in ('playing','foreign-job','job-aba'):assert d._stale
    finally:
        release.set();w.jobs.discard('foreign-job');w.shortcuts._composing=None
        if modal is not None:modal.reject()
        if popup is not None:popup.close()
        if guard=='local-ime':QApplication.sendEvent(d.origin,QInputMethodEvent('',[]))
        settle(w,app)
    assert state(w)==before and not w.test_warnings


@pytest.mark.parametrize('change', ['job-aba','role-aba','playing-after'])
def test_context_aba_expires_verified_v2_plan_permanently(affine_window,app,monkeypatch,change):
    w=affine_window;d=dialog(w,app);preview(w,app,d);before=state(w)
    if change=='job-aba':w._job_generation+=1
    elif change=='role-aba':w.select_linked_role('audio');w.select_linked_role('video')
    else:
        monkeypatch.setattr(w.player,'playbackState',lambda:QMediaPlayer.PlaybackState.PlayingState)
        w.player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
    d.check_current();assert d._stale
    d.preview();d.apply();assert state(w)==before and not w.jobs


@pytest.mark.parametrize('guard', ['playing','wrong-role','disabled','central-disabled'])
def test_direct_entry_and_capability_agree_on_global_and_active_role_guards(affine_window,app,monkeypatch,guard):
    w=affine_window
    if guard=='playing':monkeypatch.setattr(w.player,'playbackState',lambda:QMediaPlayer.PlaybackState.PlayingState)
    elif guard=='wrong-role':w.selection='other-id'
    elif guard=='disabled':w.setEnabled(False)
    else:w.centralWidget().setEnabled(False)
    try:
        before=state(w);assert not w.can_slip_linked_av()
        assert w.open_linked_av_slip() is None and state(w)==before
    finally:w.setEnabled(True);w.centralWidget().setEnabled(True)


def test_narrow_initial_input_and_summary_precede_long_pair_details(affine_window,app):
    d=dialog(affine_window,app);d.fit_available_screen(QRect(0,0,420,600));app.processEvents()
    d.scroll.verticalScrollBar().setValue(0)
    rect=QRect(d.origin.mapTo(d.scroll.viewport(),QPoint(0,0)),d.origin.size())
    assert d.scroll.viewport().rect().contains(rect)
    assert d.summary.y()<d.origin.y()<d.impact.y()<d.scope.y()<d.source.y()
    assert d.scroll.horizontalScrollBar().maximum()==0


@pytest.mark.parametrize('dismiss', ['cancel','close','escape'])
@pytest.mark.parametrize('stage', ['draft','previewed'])
def test_v2_cancel_close_escape_and_reopen_preserve_records_and_receipt_boundaries(affine_window,app,dismiss,stage):
    from PySide6.QtTest import QTest
    w=affine_window;before=state(w);identities=tuple(map(id,w.project.clips));d=dialog(w,app)
    if stage=='previewed':preview(w,app,d)
    if dismiss=='cancel':d.cancel_button.click()
    elif dismiss=='close':d.close()
    else:QTest.keyClick(d,Qt.Key.Key_Escape)
    app.processEvents();assert d._closed_flag and not d.timer.isActive()
    assert d.plan is None and d.preview_receipt is None and not d._old_clips
    assert d._playback_player is None
    assert state(w)==before and tuple(map(id,w.project.clips))==identities
    new=dialog(w,app);assert new is not d and new.preview_receipt is None and new.plan is not d.plan
    new.reject();assert state(w)==before


@pytest.mark.parametrize('role', ['video','audio'])
def test_existing_custom_shortcut_opens_affine_pair_for_current_anchor(affine_window,app,role):
    from test_shortcuts_dispatch import CTRL,SHIFT,press as keypress
    w=affine_window;w.select_linked_role(role);w.shortcuts.set_bindings('edit.joint_slip',('Ctrl+Shift+J',))
    activate(app,w,w.timeline);before=state(w)
    keypress(app,w.timeline,Qt.Key.Key_J,CTRL|SHIFT)
    d=w.linked_av_slip_dialog;assert d is not None
    assert d.clip_id==('video-id' if role=='video' else 'audio-id') and state(w)==before
    d.reject()


def test_close_disconnects_locale_and_captured_player_and_releases_display_snapshots(affine_window,app,monkeypatch):
    from lumen.i18n import locale_events
    import lumen.ui_linked_av as module
    w=affine_window;observed=[];target=[]
    old_current=module.LinkedAVSlipDialog.check_current
    old_refresh=module.LinkedAVSlipDialog.refresh_text
    def current(self,*args):
        if target and self is target[0]:observed.append('playback-or-current')
        return old_current(self,*args)
    def refresh(self,*args):
        if target and self is target[0]:observed.append('locale-or-refresh')
        return old_refresh(self,*args)
    monkeypatch.setattr(module.LinkedAVSlipDialog,'check_current',current)
    monkeypatch.setattr(module.LinkedAVSlipDialog,'refresh_text',refresh)
    d=dialog(w,app);target.append(d);player=d._playback_player
    assert set(d._old_clips)=={'video-id','audio-id'} and not hasattr(d,'original')
    d.reject();app.processEvents();observed.clear()
    locale_events.changed.emit('en');player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.StoppedState)
    app.processEvents()
    assert not observed and d.plan is None and d.preview_receipt is None and not d._old_clips
    assert d._playback_player is None and not d.timer.isActive()

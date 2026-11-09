"""Linked pair selection, explicit parameter roles, metadata and fresh workers."""
from copy import deepcopy
from pathlib import Path
import json
import threading
import time

import pytest
from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QLineEdit

from lumen.i18n import set_locale
from lumen.model import AudioMixAnchor, Clip, Editor, Media, Project
from lumen.shortcuts import COMMANDS, SETTINGS_KEY, default_bindings
from lumen.ui_linked_av import LINK_TRIM_HELP, UNLINK_NOTE
import lumen.ui_linked_av as ui
from test_master_gain_ui import app, window
from test_clip_copy_paste_ui import settle, state
from test_clip_multiselection_timeline import press, move, release
from test_shortcuts_dispatch import activate


@pytest.fixture
def pair_window(window, app, tmp_path):
    w = window
    path = tmp_path / 'av.mov'; path.write_bytes(b'original AV identity-only source' * 100)
    w.editor = Editor(Project(media=[Media('media-id', str(path), 'Video', 4, 64, 48, True, True)], clips=[
        Clip('video-id', 'media-id', 0, 5, 12, 30, source_audio_enabled=False),
        Clip('audio-id', 'media-id', 2, 5, 12, 30, gain_db=-6, fade_in=2, fade_out=3,
             audio_mix_anchor=AudioMixAnchor(0, 5, 'video-id')),
        Clip('other-id', 'media-id', 1, 5, 12, 30)]))
    w.refresh(); select_pair(w); activate(app, w, w.timeline)
    return w


def select_pair(w):
    w.active_selection_type = 'clip'
    w.set_selected_clip_ids(('video-id', 'audio-id'), anchor='video-id')
    w.refresh_inspector(); w.update_playback_controls()


def link(w, app):
    activate(app, w, w.timeline); w.link_selected_av()
    assert len(w.project.av_links) == 1
    return w.project.av_links[0]


def dialog(w, app, origin='20'):
    activate(app, w, w.timeline)
    d = w.open_linked_av_slip(); assert d is not None and d.isVisible()
    d.origin.setText(origin); app.processEvents()
    return d


def preview(w, app, d):
    assert d.preview_button.isEnabled(), d.impact.text()
    d.preview(); settle(w, app)
    assert d.preview_receipt is not None and d.apply_button.isEnabled(), d.impact.text()


@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_link_unlink_metadata_and_undo_preserve_activated_playback(pair_window, app, monkeypatch, mode):
    w=pair_window; w.set_workspace_mode(mode, persist=False)
    before=deepcopy(w.project); depth=len(w.editor._undo)
    w.preview_revision=w.revision; w.loaded_preview_mode=w.preview_mode
    invalidations=[]; monkeypatch.setattr(w,'invalidate_preview_frame',lambda:invalidations.append(True))
    link(w,app)
    assert w.project.clips == before.clips and w.project.media == before.media
    assert w.preview_revision == w.revision and not invalidations
    assert len(w.editor._undo) == depth+1 and w.dirty
    assert not w.can_group_selected_clips() and not w.can_reattach_source_audio()
    assert w.shortcuts.actions['edit.unlink_av'].isEnabled()
    assert 'does not restore embedded' in w.shortcuts.actions['edit.unlink_av'].toolTip()
    w.unlink_selected_av()
    assert not w.project.av_links and w.project.clips == before.clips
    assert w.statusBar().currentMessage()==UNLINK_NOTE and w.preview_revision==w.revision
    w.undo(); assert len(w.project.av_links)==1 and w.preview_revision==w.revision
    w.redo(); assert not w.project.av_links and w.preview_revision==w.revision
    assert not invalidations and not w.test_warnings


@pytest.mark.parametrize('member,role', [('video-id','V'), ('audio-id','A')])
def test_member_click_expands_pair_and_explicit_role_edits_only_that_member(pair_window, app, member, role):
    w=pair_window; link(w,app); w.select_clip('other-id')
    p=press(w.timeline,member); release(w.timeline,p)
    assert set(w.selected_clip_ids)=={'video-id','audio-id'} and w.selection==member
    assert w.selected_clip() is None and w.selected_parameter_clip().id==member
    assert w.inspector.isEnabled() and f'role: {role}' in w.linked_av_note.text()
    assert not w.split_clip_button.isEnabled() and not w.apply_timing_button.isEnabled()
    assert not w.apply_speed_button.isEnabled() and not w.apply_roll_button.isEnabled()
    before={c.id:deepcopy(c) for c in w.project.clips}
    w.gain.setValue(-8)
    assert w.editor._clip(member).gain_db==-8
    peer='audio-id' if member=='video-id' else 'video-id'
    assert w.editor._clip(peer)==before[peer] and len(w.project.av_links)==1
    w.select_linked_role('audio' if member=='video-id' else 'video')
    assert w.selection==peer and set(w.selected_clip_ids)=={'video-id','audio-id'}
    assert not w.test_warnings


@pytest.mark.parametrize('member',['video-id','audio-id'])
@pytest.mark.parametrize('edge',['left','right'])
def test_edge_trims_complete_pair_and_keeps_pair_selection(pair_window, app, member, edge):
    w=pair_window; link(w,app); before=deepcopy(w.project); depth=len(w.editor._undo)
    p=press(w.timeline,member,edge=edge); release(w.timeline,move(w.timeline,p,4))
    assert w.project!=before and w.timeline.trim_drag is None
    assert set(w.selected_clip_ids)=={'video-id','audio-id'} and w.selection==member
    assert w.editor._clip('video-id').timing==w.editor._clip('audio-id').timing
    assert w.editor._clip('other-id')==before.clips[-1]
    assert len(w.editor._undo)==depth+1
    assert not w.test_warnings


def test_pair_delete_one_history_entry_and_undo_restore_link(pair_window,app):
    w=pair_window; link(w,app); w.select_clip('audio-id'); before=deepcopy(w.project)
    depth=len(w.editor._undo); w.delete_selected()
    assert [c.id for c in w.project.clips]==['other-id'] and not w.project.av_links
    assert len(w.editor._undo)==depth+1
    w.undo(); assert w.project==before and not w.test_warnings


@pytest.mark.parametrize('member',['video-id','audio-id'])
@pytest.mark.parametrize('muted',[False,True])
def test_pair_mouse_move_is_horizontal_atomic_and_preserves_role(pair_window,app,monkeypatch,member,muted):
    w=pair_window; w.editor._clip('audio-id').muted=muted
    link(w,app); w.timeline.snapping_enabled=False
    before=deepcopy(w.project); depth=len(w.editor._undo); invalidations=[]
    monkeypatch.setattr(w,'invalidate_preview_frame',lambda *a,**k:invalidations.append(True))
    p=press(w.timeline,member)
    for delta in (3,7,5):
        target=move(w.timeline,p,delta,y=200)
        assert w.project==before and len(w.editor._undo)==depth and not invalidations
    release(w.timeline,target)
    assert w.selection==member and set(w.selected_clip_ids)=={'video-id','audio-id'}
    assert [(c.start,c.track) for c in w.project.clips]==[(10,0),(10,2),(5,1)]
    assert w.project.av_links==before.av_links
    for old,current in zip(before.clips,w.project.clips):
        expected=deepcopy(old)
        if old.id in ('video-id','audio-id'): expected.start+=5
        assert current==expected
    assert len(w.editor._undo)==depth+1 and invalidations==[True]
    after=deepcopy(w.project); w.undo(); assert w.project==before
    w.redo(); assert w.project==after and not w.test_warnings


@pytest.mark.parametrize('track',[0,2])
def test_lock_on_either_pair_member_blocks_unlocked_member_drag_and_parameter_edit(pair_window,app,track):
    w=pair_window; link(w,app); w.editor.set_track_edit_lock(track,True); w.refresh()
    member='audio-id' if track==0 else 'video-id'
    w.select_clip(member); before=state(w)
    assert not w.gain.isEnabled() and not w.linked_slip_button.isEnabled()
    p=press(w.timeline,member); release(w.timeline,move(w.timeline,p,5))
    assert state(w)==before and w.timeline.drag is None
    w.editor.set_track_edit_lock(track,False); w.refresh()
    assert w.gain.isEnabled() and not w.apply_timing_button.isEnabled()
    assert not w.apply_speed_button.isEnabled() and not w.apply_roll_button.isEnabled()


@pytest.mark.parametrize('change',['cancel','role_aba','revision_aba','replace'])
def test_role_switch_drag_still_rejects_synchronous_selection_listener_changes(pair_window,app,change):
    w=pair_window; link(w,app); w.timeline.snapping_enabled=False
    def changed(_clip_id):
        if change=='cancel': w.timeline.cancel_gestures()
        elif change=='role_aba':
            w.timeline.selection='video-id'; w.timeline.selection='audio-id'
        elif change=='revision_aba':
            w.editor.update_clip('audio-id',gain_db=-4); w.editor.undo()
        else:
            w.editor=Editor(deepcopy(w.project)); w.refresh()
    w.timeline.selected.connect(changed)
    try:
        p=press(w.timeline,'audio-id'); expected=state(w)
        release(w.timeline,move(w.timeline,p,5))
        assert state(w)==expected and w.timeline.drag is None
        assert w.editor._clip('audio-id').start==w.editor._clip('video-id').start==5
    finally:
        w.timeline.selected.disconnect(changed)


@pytest.mark.parametrize('role',['video','audio'])
def test_joint_slip_preserves_pair_selects_new_exact_source_in_one_undo(pair_window,app,role):
    w=pair_window; link(w,app); w.select_linked_role(role)
    before=deepcopy(w.project); depth=len(w.editor._undo)
    d=dialog(w,app,'41/2'); assert w.open_linked_av_slip() is d
    assert 'video-id' in d.source.text() and 'audio-id' in d.source.text()
    assert not d.apply_button.isEnabled(); preview(w,app,d)
    assert w.project==before
    d.apply(); d.apply(); settle(w,app)
    assert d._closed_flag and w.linked_av_slip_dialog is None
    assert str(w.editor._clip('video-id').source_in)=='41/2'
    assert w.editor._clip('video-id').timing==w.editor._clip('audio-id').timing
    assert w.project.av_links==before.av_links and len(w.editor._undo)==depth+1
    assert set(w.selected_clip_ids)=={'video-id','audio-id'}
    assert w.selection==('video-id' if role=='video' else 'audio-id')
    after=deepcopy(w.project); w.undo(); assert w.project==before
    w.redo(); assert w.project==after and not w.test_warnings


def test_old_slip_button_routes_pair_to_verified_joint_dialog(pair_window,app):
    w=pair_window; link(w,app); before=deepcopy(w.project)
    w.slip_source_edit.setText('22'); w.apply_slip()
    assert w.linked_av_slip_dialog is not None and w.linked_av_slip_dialog.origin.text()=='22'
    assert w.project==before and not w.jobs


@pytest.mark.parametrize('bad',['', '-1', 'bad', '110'])
def test_invalid_or_noop_draft_never_edits(pair_window,app,bad):
    w=pair_window; link(w,app); before=state(w)
    d=dialog(w,app,bad); d.preview(); d.apply()
    assert not d.preview_button.isEnabled() and not d.apply_button.isEnabled()
    assert state(w)==before
    d.origin.setText('12'); assert not d.preview_button.isEnabled()
    assert 'no history' in d.status.text()


@pytest.mark.parametrize('dismiss',['cancel','close','escape'])
@pytest.mark.parametrize('stage',['draft','previewed'])
def test_dismiss_reopen_receipt_not_reused(pair_window,app,dismiss,stage):
    w=pair_window; link(w,app); before=state(w); d=dialog(w,app)
    if stage=='previewed': preview(w,app,d)
    if dismiss=='cancel': d.cancel_button.click()
    elif dismiss=='close': d.close()
    else: QTest.keyClick(d,Qt.Key.Key_Escape)
    app.processEvents(); assert d._closed_flag and state(w)==before and not d.timer.isActive()
    new=dialog(w,app); assert new is not d and new.preview_receipt is None


def held(monkeypatch):
    started,release=threading.Event(),threading.Event(); original=ui.verify_linked_av_slip
    owner=threading.get_ident()
    def verify(*args,**kwargs):
        assert threading.get_ident()!=owner
        started.set(); assert release.wait(5)
        return original(*args,**kwargs)
    monkeypatch.setattr(ui,'verify_linked_av_slip',verify)
    return started,release


@pytest.mark.parametrize('phase',['preview','apply'])
@pytest.mark.parametrize('race',['cancel','close','selection','role_aba','edit_undo','replace','unversioned','lock','relink','unlink'])
def test_worker_stale_cancel_aba_never_commits(pair_window,app,monkeypatch,phase,race):
    w=pair_window; link(w,app); d=dialog(w,app)
    if phase=='apply': preview(w,app,d)
    started,release=held(monkeypatch); getattr(d,phase)()
    assert started.wait(2)
    try:
        if race=='cancel': w.cancel_jobs()
        elif race=='close': d.reject()
        elif race=='selection': w.select_clip('other-id')
        elif race=='role_aba': w.select_linked_role('audio'); w.select_linked_role('video')
        elif race=='edit_undo': w.editor.update_clip('audio-id',gain_db=-3); w.editor.undo(); w.refresh()
        elif race=='replace': w.editor=Editor(deepcopy(w.project)); w.refresh()
        elif race=='unversioned': w.editor._clip('audio-id').gain_db=-3
        elif race=='lock': w.editor.set_track_edit_lock(2,True); w.changed()
        elif race=='relink': w.project.media[0].path += '.relinked'
        else: w.editor.unlink_av_clips(w.selected_clip_ids); w.marker_metadata_changed()
        d.check_current(); expected=state(w)
    finally:
        release.set(); settle(w,app)
    assert state(w)==expected and w.editor._clip('video-id').source_in==12
    assert d.preview_receipt is None and not d.apply_button.isEnabled()
    assert 'Checking' not in w.progress_label.text() and not w.test_warnings


@pytest.mark.parametrize('phase',['preview','apply'])
def test_worker_failure_preserves_state_and_can_repreview(pair_window,app,monkeypatch,phase):
    w=pair_window; link(w,app); d=dialog(w,app)
    if phase=='apply': preview(w,app,d)
    monkeypatch.setattr(ui,'verify_linked_av_slip',lambda *_a,**_k:(_ for _ in ()).throw(ValueError('verification failed')))
    before=state(w); getattr(d,phase)(); settle(w,app)
    assert state(w)==before and d.preview_receipt is None and d.preview_button.isEnabled()
    assert w.test_warnings and w.progress_label.text()=='Joint source slip check failed · project unchanged'


@pytest.mark.parametrize('navigation',['new','open_cancel','new_session'])
def test_navigation_closes_staged_pair(pair_window,app,monkeypatch,navigation):
    w=pair_window; link(w,app); d=dialog(w,app); preview(w,app,d)
    if navigation=='new':
        monkeypatch.setattr(QInputDialog,'getItem',lambda *_a,**_k:('1280 × 720 · 30 fps',True))
        w.dirty=False; w.new_project(); assert not w.project.clips
    elif navigation=='open_cancel':
        w.dirty=False
        before=state(w); monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *_a,**_k:('',''))
        w.open_project(); assert state(w)==before
    else: w.editor=Editor(deepcopy(w.project)); w.refresh()
    app.processEvents(); assert d._closed_flag and w.linked_av_slip_dialog is None


@pytest.mark.parametrize('locale',['en','zh-CN'])
@pytest.mark.parametrize('mode',['simple','professional'])
def test_locale_narrow_dialog_keeps_live_draft_and_explicit_roles(pair_window,app,locale,mode):
    w=pair_window; w.set_workspace_mode(mode,persist=False); link(w,app)
    d=dialog(w,app); preview(w,app,d); plan,receipt=d.plan,d.preview_receipt; before=state(w)
    set_locale(locale); app.processEvents(); d.fit_available_screen(QRect(0,0,420,600)); app.processEvents()
    assert d.width()<=388 and d.height()<=536 and d.scroll.horizontalScrollBar().maximum()==0
    for button in (d.preview_button,d.apply_button,d.cancel_button):
        assert button.isVisible() and button.isEnabled() and button.accessibleName()
        assert button.width()>=button.fontMetrics().horizontalAdvance(button.text())+8
    assert state(w)==before and d.plan is plan and d.preview_receipt is receipt
    assert 'video-id' in d.source.text() and 'audio-id' in d.source.text()
    if locale=='zh-CN':
        assert '联合源滑移' in d.windowTitle() and '不会恢复' in w.linked_av_note.text()


@pytest.mark.parametrize('command',['edit.link_av','edit.unlink_av','edit.joint_slip'])
def test_commands_are_unbound_nonrepeating_and_guarded_for_text_dialog_ime(pair_window,app,command):
    w=pair_window
    if command!='edit.link_av': link(w,app)
    assert command in COMMANDS and not COMMANDS[command].repeat
    assert all(default_bindings(profile)[command]==() for profile in ('windows','macos'))
    assert not w.shortcuts.actions[command].shortcuts()
    assert w.shortcuts.enabled(command)
    text=QLineEdit(w); assert not w.shortcuts._focus_allowed(command,text)
    assert not w.shortcuts._focus_allowed(command,w.bin)
    assert not w.shortcuts._focus_allowed(command,w.gain)
    d=QDialog(w); child=QLineEdit(d); assert not w.shortcuts._focus_allowed(command,child)
    w.shortcuts._composing=w.timeline
    assert not w.shortcuts._focus_allowed(command,w.timeline)
    w.shortcuts._composing=None
    assert w.shortcuts._focus_allowed(command,w.timeline)
    w.editor.set_track_edit_lock(2,True); w.refresh()
    assert not w.shortcuts.enabled(command)


def test_joint_slip_planning_and_metadata_do_no_gui_filesystem_io(pair_window,app,monkeypatch):
    import lumen.clip_copy_paste as core
    w=pair_window; owner=threading.get_ident(); seen=[]; io=[]
    source=Path(w.project.media[0].path); lut=source.with_suffix('.cube')
    lut.write_bytes(b'LUT dependency identity fixture; not decoded')
    w.editor._clip('video-id').grade.lut_path=str(lut)
    before={path:path.read_bytes() for path in (source,lut)}
    # Ordinary media deliberately use sampled identity, not the generated/LUT
    # full-hash path. Exercise both instead of observing an unused helper.
    for name in ('_identity','_full_dependency_identity'):
        original=getattr(core,name)
        def identity(*args,_original=original,_name=name,**kwargs):
            seen.append((_name,threading.get_ident()))
            assert threading.get_ident()!=owner
            return _original(*args,**kwargs)
        monkeypatch.setattr(core,name,identity)
    with monkeypatch.context() as guard:
        for name in ('stat','lstat','open'):
            original=getattr(Path,name)
            def checked(path,*args,_original=original,_name=name,**kwargs):
                if path in before:
                    io.append((_name,threading.get_ident()))
                    assert threading.get_ident()!=owner
                return _original(path,*args,**kwargs)
            guard.setattr(Path,name,checked)
        link(w,app); d=dialog(w,app)
        # Independent media-status workers may already stat the same source;
        # planning must not start dependency hashing or do owner-thread I/O.
        assert not seen and all(thread!=owner for _,thread in io)
        preview(w,app,d); d.apply(); settle(w,app)
    assert {name for name,_ in seen}=={'_identity','_full_dependency_identity'}
    assert len(seen)==8 and all(thread!=owner for _,thread in seen)
    assert io and all(thread!=owner for _,thread in io)
    assert {path:path.read_bytes() for path in before}==before


def test_busy_pair_commands_cannot_mutate_or_launch_a_second_job(pair_window,app,monkeypatch):
    w=pair_window; link(w,app); d=dialog(w,app)
    started,release=held(monkeypatch); before=state(w); d.preview()
    assert started.wait(2)
    try:
        jobs=set(w.jobs)
        for command in ('edit.link_av','edit.unlink_av','edit.joint_slip'):
            assert not w.shortcuts.enabled(command)
            assert not w.shortcuts.actions[command].isEnabled()
            w.shortcuts.actions[command].trigger()
        w.link_selected_av(); w.unlink_selected_av()
        assert w.open_linked_av_slip() is d
        d.preview(); d.apply()
        assert state(w)==before and set(w.jobs)==jobs
        assert not d.preview_button.isEnabled() and not d.apply_button.isEnabled()
    finally:
        release.set(); settle(w,app)
    assert state(w)==before and d.preview_receipt is not None and not w.test_warnings


def test_link_metadata_preserves_playback_but_expires_strict_final_still(pair_window,app,monkeypatch):
    import lumen.ui_final_frame_preview as module
    from test_final_frame_preview_ui import fake_snapshot,dependencies,until
    from lumen.final_frame_preview import project_signature
    w=pair_window; owner=threading.get_ident()
    def capture(project,frame,**kwargs):
        assert threading.get_ident()!=owner
        return fake_snapshot(project,frame)
    def verify(snapshot,project,frame,**kwargs):
        assert threading.get_ident()!=owner
        return snapshot.identity==(project_signature(project),frame,dependencies(project))
    monkeypatch.setattr(module,'capture_final_frame_preview',capture)
    monkeypatch.setattr(module,'verify_final_frame_preview',verify)
    d=w.open_final_frame_preview(); d.timer.stop(); d.refresh_frame()
    until(app,lambda:d.current() and not w.jobs)
    w.preview_revision=w.revision; w.loaded_preview_mode=w.preview_mode
    link(w,app); d.check_current()
    assert w.preview_revision==w.revision
    assert d.state=='stale' and d.snapshot is None and d.canvas.image.isNull()
    assert 'Refresh' in d.status.text()


@pytest.mark.parametrize('role',['video','audio'])
def test_audio_gain_editor_uses_only_explicit_active_role_and_expires_on_role_change(pair_window,app,role):
    from lumen.ui_audio_gain import open_audio_gain
    w=pair_window; link(w,app); w.select_linked_role(role)
    clip_id=w.selection; other='audio-id' if role=='video' else 'video-id'
    before=deepcopy(w.editor._clip(other)); d=open_audio_gain(w)
    assert d is not None and not d._closed_flag and d.clip_id==clip_id
    d.sample_edit.setText('3'); d.offset_edit.setText('-2'); d.add_key()
    assert len(w.editor._clip(clip_id).audio_gain_keyframes)==1
    assert w.editor._clip(other)==before
    w.select_linked_role('audio' if role=='video' else 'video'); d.check_current()
    assert d._closed_flag and not w.test_warnings


def test_future_edit_command_inherits_timeline_focus_boundary(pair_window,app):
    w=pair_window; command='edit.future_guard_probe'
    assert command not in COMMANDS
    for focus in (w.bin,w.caption_list,w.gain,w.apply_timing_button,w.linked_video_button):
        assert not w.shortcuts._focus_allowed(command,focus)
    assert w.shortcuts._focus_allowed(command,w.timeline)


@pytest.mark.parametrize('command',['edit.link_av','edit.unlink_av','edit.joint_slip'])
def test_new_custom_shortcut_repeat_text_modal_and_real_dispatch(pair_window,app,command):
    from test_shortcuts_dispatch import CTRL,SHIFT,press as keypress,repeat
    w=pair_window
    if command!='edit.link_av': link(w,app)
    w.shortcuts.set_bindings(command,('Ctrl+Shift+J',))
    before=state(w)
    activate(app,w,w.timeline); repeat(app,w.timeline,Qt.Key.Key_J,CTRL|SHIFT)
    assert state(w)==before and w.linked_av_slip_dialog is None
    field=QLineEdit(w); field.show(); activate(app,w,field)
    keypress(app,field,Qt.Key.Key_J,CTRL|SHIFT)
    assert state(w)==before and w.linked_av_slip_dialog is None
    modal=QDialog(w); modal.setModal(True); modal.show(); activate(app,modal,modal)
    keypress(app,modal,Qt.Key.Key_J,CTRL|SHIFT)
    assert state(w)==before and w.linked_av_slip_dialog is None
    modal.reject(); field.close(); activate(app,w,w.timeline)
    keypress(app,w.timeline,Qt.Key.Key_J,CTRL|SHIFT)
    if command=='edit.link_av': assert len(w.project.av_links)==1
    elif command=='edit.unlink_av': assert not w.project.av_links
    else: assert w.linked_av_slip_dialog is not None and state(w)==before

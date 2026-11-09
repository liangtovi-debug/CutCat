"""Session In/Out dispatch and reuse of the bounded A/V worker lifecycle."""
from copy import deepcopy
import json
import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QSpinBox, QDialog, QMenu

from lumen.model import Editor
from lumen.shortcuts import PROFILES, SETTINGS_KEY, default_bindings
from lumen.ui_preview_selection import selection, selection_error
from test_range_preview_ui import app, window, until, pump, snapshot
from test_shortcuts_model import controller


def marks(w, start=30, last=59):
    w.seek_frame(start); w.set_preview_mark('in')
    w.seek_frame(last); w.set_preview_mark('out')


@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_selected_build_loops_exact_half_open_range_without_edit(window, app, mode):
    w = window; w.set_workspace_mode(mode, persist=False)
    before = (deepcopy(w.project), w.revision, w.editor.revision, dict(w.preview_paths))
    marks(w)
    assert w.timeline.preview_selection == (30, 60)
    d = w.preview_selection(); d.timer.stop()
    until(app, lambda: d.player is not None and not w.jobs)
    assert w.test_captures == [(30, 60)] and d.loop_checkbox.isChecked()
    assert d._play_intent and '30' in d.selection_label.text() and '59' in d.selection_label.text()
    player = d.player
    d._ended(); until(app, lambda: not w.jobs)
    assert d.player is player and d._play_intent
    assert (w.project, w.revision, w.editor.revision, w.preview_paths) == before
    assert not w.editor.can_undo


@pytest.mark.parametrize('start,last,expected', [(0,0,(0,1)), (599,599,(599,600)), (0,299,(0,300)),
                                                (50,20,None), (0,300,None)])
def test_bounds_one_frame_inclusive_out_and_limit(window, app, start, last, expected):
    w = window; marks(w,start,last); d=w.preview_selection(); d.timer.stop()
    if expected:
        until(app, lambda: d.player is not None and not w.jobs)
        assert w.test_captures == [expected]
    else:
        pump(app)
        assert not w.test_captures and not w.jobs and d.player is None
        assert selection_error(w) in d.status.text() and d.timeline_button.isEnabled()


def test_missing_and_empty_project_fail_before_render(window, app):
    w=window; d=w.preview_selection(); d.timer.stop(); pump(app)
    assert not w.test_captures and selection_error(w) in d.status.text()
    w.project.clips.clear(); w.refresh(); w.set_preview_mark('in'); w.set_preview_mark('out')
    assert selection(w) == (None,None)
    d.build_range(); assert not w.test_captures


@pytest.mark.parametrize('action', ['cancel','clear','remark','replace','undo','redo'])
def test_changed_state_during_render_never_autoplays_old_result(window, app, monkeypatch, action):
    import lumen.ui_range_preview as module
    w=window; marks(w)
    w.editor._commit(lambda: setattr(w.project,'name','Edited'))
    if action == 'redo': w.editor.undo()
    entered, release = threading.Event(), threading.Event()
    def capture(project,start,end,**kwargs):
        entered.set(); assert release.wait(8)
        return snapshot(project,start,end,w)
    monkeypatch.setattr(module,'capture_range_preview',capture)
    d=w.preview_selection(); d.timer.stop()
    try:
        assert entered.wait(2)
        if action == 'cancel': d.cancel_work()
        elif action == 'clear': w.set_preview_mark('clear')
        elif action == 'remark': w.seek_frame(40); w.set_preview_mark('in')
        elif action == 'replace': w.editor=Editor(deepcopy(w.project)); w.refresh()
        elif action == 'undo': w.editor.undo(); w.refresh()
        else: w.editor.redo(); w.refresh()
        d.check_current(); release.set()
        until(app,lambda: bool(w.test_snapshots) and all(s.closed for s in w.test_snapshots) and not w.jobs)
        assert d.player is None and not d._play_intent
        if action == 'replace': assert selection(w)==(None,None) and w.timeline.preview_selection==(None,None)
        if action in ('undo','redo'): assert selection(w)==(30,60)
    finally: release.set()


def test_repeated_build_retains_only_latest_selection(window, app, monkeypatch):
    import lumen.ui_range_preview as module
    w=window; marks(w); entered,release=threading.Event(),threading.Event(); calls=[]
    def capture(project,start,end,**kwargs):
        calls.append((start,end))
        if len(calls)==1: entered.set(); assert release.wait(8)
        return snapshot(project,start,end,w)
    monkeypatch.setattr(module,'capture_range_preview',capture)
    d=w.preview_selection(); d.timer.stop()
    try:
        assert entered.wait(2)
        marks(w,70,89); assert w.preview_selection() is d
        w.preview_selection(); assert len(w.jobs)==1
        release.set(); until(app,lambda: d.player is not None and not w.jobs)
        assert calls==[(30,60),(70,90)] and w.test_snapshots[0].closed
        assert d.context[8:10]==(70,90)
    finally: release.set()


def test_shortened_project_keeps_marks_but_rejects_without_clamping(window, app):
    w=window; marks(w,500,599)
    w.editor._commit(lambda: setattr(w.project.clips[0],'duration',100)); w.refresh()
    assert selection(w)==(500,600) and selection_error(w)
    d=w.preview_selection(); d.timer.stop(); assert not w.test_captures
    w.editor.undo(); w.refresh(); assert selection(w)==(500,600) and not selection_error(w)


@pytest.mark.parametrize('focus_kind',['text','number','media','dialog','popup','ime'])
@pytest.mark.parametrize('key',[Qt.Key.Key_I,Qt.Key.Key_O,Qt.Key.Key_Space])
def test_shortcuts_never_escape_input_or_ime_focus(window, app, focus_kind,key):
    w=window; marks(w); w.seek_frame(100)
    control=w.timeline
    if focus_kind=='text': control=QLineEdit(w)
    elif focus_kind=='number': control=QSpinBox(w)
    elif focus_kind=='media': control=w.bin
    elif focus_kind=='dialog': control=QDialog(w)
    elif focus_kind=='popup': control=QMenu(w); control.addAction('Fixture')
    control.show(); QApplication.setActiveWindow(control if focus_kind in ('dialog','popup') else w)
    control.setFocus(); pump(app,.02)
    if focus_kind=='ime': QApplication.sendEvent(control,QInputMethodEvent('拼',[]))
    modifiers=Qt.KeyboardModifier.ShiftModifier if key==Qt.Key.Key_Space else Qt.KeyboardModifier.NoModifier
    QTest.keyClick(control,key,modifiers); pump(app,.02)
    assert selection(w)==(30,60) and not w.test_captures
    if focus_kind=='ime': QApplication.sendEvent(control,QInputMethodEvent('',[]))
    if control not in (w.timeline,w.bin): control.close()


def test_real_shortcuts_from_timeline_and_no_qaction_bypass(window,app):
    w=window; QApplication.setActiveWindow(w); w.timeline.setFocus(); pump(app,.02)
    w.seek_frame(10); QTest.keyClick(w.timeline,Qt.Key.Key_I)
    w.seek_frame(20); QTest.keyClick(w.timeline,Qt.Key.Key_O)
    assert selection(w)==(10,21)
    QTest.keyClick(w.timeline,Qt.Key.Key_Space,Qt.KeyboardModifier.ShiftModifier)
    d=w.range_preview_dialog; d.timer.stop(); until(app,lambda: d.player is not None and not w.jobs)
    assert w.test_captures==[(10,21)]
    assert not w.shortcuts.actions['timeline.preview_selection'].shortcuts()


def test_old_custom_shortcut_profiles_gain_unbound_selection_commands(controller):
    c=controller; old={p:default_bindings(p) for p in PROFILES}
    for values in old.values():
        for key in tuple(values):
            if key.startswith('timeline.preview_'): del values[key]
        values['timeline.add_marker']=('I',)
    c.settings.setValue(SETTINGS_KEY,json.dumps(dict(version=1,active_profile='windows',profiles=old)))
    c._load()
    assert not c.load_warning and c.bindings['timeline.add_marker']==('I',)
    assert not c.bindings['timeline.preview_in']


@pytest.mark.parametrize('fps_num,fps_den,last,valid', [(24000,1001,238,True),
    (24000,1001,239,False),(120,1,599,True),(120,1,600,False)])
def test_fractional_seconds_and_independent_frame_cap(window,fps_num,fps_den,last,valid):
    w=window; w.project.fps_num=fps_num; w.project.fps_den=fps_den
    w.project.clips[0].duration=1200; w.refresh(); marks(w,0,last)
    assert (selection_error(w) is None)==valid


def test_clear_playing_selection_stops_and_releases_it(window,app):
    w=window; marks(w); d=w.preview_selection(); d.timer.stop()
    until(app,lambda: d.player is not None and not w.jobs); item=d.snapshot
    w.set_preview_mark('clear')
    assert d.player is None and not d._play_intent and w.timeline.preview_selection==(None,None)
    until(app,lambda: item.closed and not w.jobs)


def test_switch_back_to_radius_mode_cannot_reuse_selected_player(window,app):
    w=window; marks(w); d=w.preview_selection(); d.timer.stop()
    until(app,lambda: d.player is not None and not w.jobs); item=d.snapshot
    around=w.open_range_preview(); around.timer.stop()
    assert around is not d and not around.selected and around.player is None
    until(app,lambda: item.closed and not w.jobs)
    assert selection(w)==(30,60)


def test_out_first_can_be_completed_and_project_setter_clears_marks(window):
    w=window; w.seek_frame(50); w.set_preview_mark('out')
    assert selection(w)==(None,51) and selection_error(w)
    w.seek_frame(20); w.set_preview_mark('in')
    assert selection(w)==(20,51) and not selection_error(w)
    w.editor.project=deepcopy(w.project); w.refresh()
    assert selection(w)==(None,None) and w.timeline.preview_selection==(None,None)


@pytest.mark.parametrize('interruption',['none','pause','cancel'])
def test_end_of_media_queues_native_restart_and_drops_obsolete_callbacks(window,app,interruption):
    from PySide6.QtMultimedia import QMediaPlayer
    w=window; marks(w); d=w.preview_selection(); d.timer.stop()
    until(app,lambda: d.player is not None and not w.jobs)
    generation=d._play_generation; checks=len(w.test_checks)
    d.player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
    d.player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
    # No native restart or verification inside the backend's signal stack.
    assert d._play_generation==generation and not w.jobs
    if interruption=='pause': d.toggle_play()
    elif interruption=='cancel': d.cancel_work()
    pump(app,.04); until(app,lambda: not w.jobs)
    if interruption=='none':
        assert d._play_generation==generation+1 and d._play_intent
        assert len(w.test_checks)==checks+1
    else:
        assert not d._play_intent and len(w.test_checks)==checks


@pytest.mark.parametrize('failure_point',['source','position','play'])
def test_synchronous_native_activation_error_retires_player_and_ignores_late_signals(window,app,monkeypatch,failure_point):
    import lumen.ui_range_preview as module
    from test_range_preview_ui import Player, loaded
    w=window; d=loaded(w,app); candidate=d.snapshot; players=[]
    class FailingPlayer(Player):
        def __init__(self,parent=None):
            super().__init__(parent); players.append(self); self.failed=False; self.status_reads=0
        def fail_now(self):
            self.failed=True
            self.errorOccurred.emit()
            # Emissions from an already retired player must be ignored, even
            # before deleteLater has actually destroyed the QObject.
            before=(d.state,d.status.text(),d.position_label.text(),d._play_generation)
            self.errorOccurred.emit()
            self.mediaStatusChanged.emit(self.MediaStatus.EndOfMedia)
            self.positionChanged.emit(999)
            assert (d.state,d.status.text(),d.position_label.text(),d._play_generation)==before
        def setSource(self,source):
            super().setSource(source)
            if not source.isEmpty() and failure_point=='source': self.fail_now()
        def mediaStatus(self):
            assert not self.failed, 'Do not inspect a synchronously retired player'
            self.status_reads+=1
            return super().mediaStatus()
        def setPosition(self,position):
            assert not self.failed
            super().setPosition(position)
            if failure_point=='position': self.fail_now()
        def play(self):
            assert not self.failed, 'Do not play a synchronously retired player'
            super().play()
            if failure_point=='play': self.fail_now()
    monkeypatch.setattr(module,'QMediaPlayer',FailingPlayer)
    d._play_intent=True; d._play_generation+=1
    # Direct activation makes a synchronous exception fail this test directly,
    # rather than merely printing an exception from a Qt callback.
    d._activate_player(candidate,restart=failure_point=='position')
    old=players[0]
    assert old.failed and old.stops==1 and old.audio is None and old.sink is None and old.source.isEmpty()
    assert old.plays==(1 if failure_point=='play' else 0)
    assert d.player is None and d.sink is None and not d._play_intent
    assert d.state=='ready' and 'Synthetic codec unavailable' in d.status.text()
    assert d.snapshot is candidate and not candidate.closed
    # The verified cache remains retryable; only explicit Play may recover.
    monkeypatch.setattr(module,'QMediaPlayer',Player)
    d.toggle_play(); until(app,lambda:d.player is not None and not w.jobs)
    assert d.player is not old and d._play_intent
    d.cancel_work(); until(app,lambda:candidate.closed and not w.jobs)
    assert w.test_closed.count(candidate)==1


@pytest.mark.parametrize('replace_at',['source','position'])
def test_synchronous_activation_cannot_operate_on_replacement_player(window,app,monkeypatch,replace_at):
    import lumen.ui_range_preview as module
    from test_range_preview_ui import Player, loaded
    w=window; d=loaded(w,app); candidate=d.snapshot; players=[]
    replacement=Player(d)
    class ReplacedPlayer(Player):
        def __init__(self,parent=None):
            super().__init__(parent); players.append(self)
        def replace(self):
            d._stop_player()
            d.player=replacement
            # Simulate a reentrant new play request using the same snapshot.
            d._play_intent=True
        def setSource(self,source):
            super().setSource(source)
            if not source.isEmpty() and replace_at=='source': self.replace()
        def setPosition(self,position):
            super().setPosition(position)
            if replace_at=='position': self.replace()
    monkeypatch.setattr(module,'QMediaPlayer',ReplacedPlayer)
    d._play_intent=True; d._play_generation+=1
    d._activate_player(candidate,restart=True)
    assert players[0].plays==0 and replacement.plays==0
    assert d.player is replacement and d.state=='ready'
    d.cancel_work(); until(app,lambda:candidate.closed and not w.jobs)

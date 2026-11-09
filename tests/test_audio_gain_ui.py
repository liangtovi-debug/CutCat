"""Exact-sample hand editing, atomic gestures and interruption-safe UI state."""
from copy import deepcopy

import pytest
from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lumen.audio_gain import AudioGainKeyframe as Key, MAX_AUDIO_GAIN_SAMPLE
from lumen.i18n import set_locale
from lumen.model import Clip, Editor, Media, Project
from lumen.ui_audio_gain import open_audio_gain, can_open_audio_gain
from lumen.ui_audio_gain_timeline import AudioGainTimeline
from test_master_gain_ui import app, window
from test_shortcuts_dispatch import activate


def opened(window, app):
    d = open_audio_gain(window)
    assert d is not None
    app.processEvents()
    assert d.isVisible()
    return d


def add(d, sample, offset=-6, mode='linear'):
    d.sample_edit.setText(str(sample)); d.offset_edit.setText(str(offset))
    d.interpolation.setCurrentIndex(d.interpolation.findData(mode))
    d.add_button.click()


def test_exact_sample_fields_hidden_handles_one_edit_undo_and_no_decoder(window, app):
    w = window; before = deepcopy(w.project); d = opened(w, app)
    assert open_audio_gain(w) is d and not w.jobs
    add(d, 1, -12)
    assert w.selected_clip().audio_gain_keyframes == [Key(1, -12)]
    assert len(w.editor._undo) == 1 and w.dirty and 'Build preview' in w.preview_label.text()
    assert '0.020833 ms' in d.milliseconds.text()
    add(d, 2**33 + 17, 3.125, 'hold')
    add(d, -111, -4)
    assert [key.sample for key in w.selected_clip().audio_gain_keyframes] == [-111, 1, 2**33+17]
    assert '2 hidden' in d.hidden.text() and len(w.editor._undo) == 3
    assert d.key_selector.count() == 4 and d.sample_edit.text() == '-111'
    current = deepcopy(w.project)
    w.undo(); w.undo(); w.undo(); assert w.project == before
    w.redo(); w.redo(); w.redo(); assert w.project == current
    assert not w.jobs and not w.test_warnings


@pytest.mark.parametrize('sample', ['1.5','1e4','NaN','',str(MAX_AUDIO_GAIN_SAMPLE+1),str(-MAX_AUDIO_GAIN_SAMPLE-1)])
def test_invalid_sample_is_nonmutating(window, app, sample):
    d = opened(window, app); before = deepcopy(window.project)
    d.sample_edit.setText(sample); d.add_button.click()
    assert window.project == before and not window.editor.can_undo
    assert 'signed integer' in d.status.text()


@pytest.mark.parametrize('offset', ['inf','nan','-97','25','bad',''])
def test_invalid_offset_is_nonmutating(window, app, offset):
    d = opened(window, app); before = deepcopy(window.project)
    d.offset_edit.setText(offset); d.add_button.click()
    assert window.project == before and not window.editor.can_undo
    assert 'finite envelope offset' in d.status.text()


def test_replace_precision_move_duplicate_delete_clear_are_explicit(window, app):
    d = opened(window, app)
    add(d, 11, -1.2345678901234567, 'hold')
    before = deepcopy(window.project); undo = len(window.editor._undo)
    d.add_button.click()  # untouched full-precision fields are a no-op
    assert window.project == before and len(window.editor._undo) == undo
    add(d, 22, 24)
    d.select_key(11); d.sample_edit.setText('22'); d.move_button.click()
    assert 'already occupies' in d.status.text() and len(window.editor._undo) == 2
    d.sample_edit.setText(str(MAX_AUDIO_GAIN_SAMPLE)); d.move_button.click()
    assert window.selected_clip().audio_gain_keyframes[-1] == Key(MAX_AUDIO_GAIN_SAMPLE, -1.2345678901234567,'hold')
    assert len(window.editor._undo) == 3
    d.delete_button.click(); assert len(window.selected_clip().audio_gain_keyframes) == 1
    d.clear_button.click(); assert window.selected_clip().audio_gain_keyframes == []
    assert len(window.editor._undo) == 5
    window.undo(); assert len(window.selected_clip().audio_gain_keyframes) == 1


def test_maximum_count_rejects_only_new_key(window, app):
    window.editor.update_clip('a',audio_gain_keyframes=[Key(n,-6) for n in range(128)])
    window.changed(); d=opened(window, app); before=deepcopy(window.project); undo=len(window.editor._undo)
    add(d, 128, -9)
    assert window.project == before and len(window.editor._undo) == undo
    assert 'at most 128' in d.status.text()
    add(d,127,-9,'hold'); assert len(window.selected_clip().audio_gain_keyframes)==128
    assert window.selected_clip().audio_gain_keyframes[-1]==Key(127,-9,'hold')


@pytest.mark.parametrize('dismiss',['escape','close','button'])
def test_close_discards_only_uncommitted_fields_and_reopen_is_fresh(window, app, dismiss):
    d=opened(window,app); add(d,21,-6)
    before=deepcopy(window.project); undo=len(window.editor._undo)
    d.sample_edit.setText('2222'); d.offset_edit.setText('12')
    if dismiss=='escape': QTest.keyClick(d,Qt.Key.Key_Escape)
    elif dismiss=='close': d.close()
    else: d.close_button.click()
    assert d._closed_flag and not d.timer.isActive() and window.audio_gain_dialog is None
    assert window.project==before and len(window.editor._undo)==undo
    fresh=opened(window,app); assert fresh is not d and fresh.sample_edit.text()=='0'
    assert fresh.offset_edit.text()=='-6'


def test_mouse_ghost_cancel_and_release_are_one_undo(window, app):
    d=opened(window,app); add(d,24000,-3); add(d,72000,-9)
    d.select_key(24000); timeline=d.timeline
    activate(app,d,timeline)
    before=deepcopy(window.project); undo=len(window.editor._undo)
    origin=QPoint(round(timeline.sample_x(24000)),round(timeline.track_y()))
    target=QPoint(round(timeline.sample_x(48000)),round(timeline.track_y()))
    QTest.mousePress(timeline,Qt.MouseButton.LeftButton,pos=origin)
    QTest.mouseMove(timeline,target)
    assert timeline.gesture_active and timeline.preview_sample is not None
    assert window.project==before and len(window.editor._undo)==undo
    QTest.keyClick(timeline,Qt.Key.Key_Escape)
    QTest.mouseRelease(timeline,Qt.MouseButton.LeftButton,pos=target)
    assert window.project==before and len(window.editor._undo)==undo
    QTest.mousePress(timeline,Qt.MouseButton.LeftButton,pos=origin)
    QTest.mouseMove(timeline,target); candidate=timeline.preview_sample
    QTest.mouseRelease(timeline,Qt.MouseButton.LeftButton,pos=target)
    assert not timeline.gesture_active and len(window.editor._undo)==undo+1
    assert window.selected_clip().audio_gain_keyframes[0].sample==candidate
    window.undo(); assert window.project==before


def test_keyboard_sample_repeat_single_commit_and_focus_cancel(window, app):
    d=opened(window,app); add(d,7,-6); d.select_key(7); timeline=d.timeline
    activate(app,d,timeline); before=deepcopy(window.project); undo=len(window.editor._undo)
    QTest.keyPress(timeline,Qt.Key.Key_Right)
    for _ in range(3):
        QApplication.sendEvent(timeline,QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_Right,Qt.KeyboardModifier.NoModifier,'',True,1))
    assert timeline.preview_sample==11 and window.project==before
    QTest.keyRelease(timeline,Qt.Key.Key_Right)
    assert window.selected_clip().audio_gain_keyframes==[Key(11,-6)]
    assert len(window.editor._undo)==undo+1
    QTest.keyPress(timeline,Qt.Key.Key_Right)
    d.sample_edit.setFocus(); app.processEvents()
    assert not timeline.gesture_active
    QTest.keyRelease(timeline,Qt.Key.Key_Right)
    assert window.selected_clip().audio_gain_keyframes==[Key(11,-6)]


def test_timeline_above_32_bits_uses_python_integer_signals(app):
    timeline=AudioGainTimeline(2**35); timeline.resize(600,100)
    selected=2**33+3; timeline.set_state([selected],selected,selected)
    moves=[]; timeline.moveRequested.connect(lambda old,new:moves.append((old,new)))
    timeline.show(); activate(app,timeline,timeline)
    QTest.keyClick(timeline,Qt.Key.Key_Right)
    assert moves==[(selected,selected+1)]
    timeline.close()


@pytest.mark.parametrize('change',['selection','new_session','removed'])
def test_context_change_closes_and_cannot_edit_old_or_new_clip(window,app,change):
    d=opened(window,app); d.sample_edit.setText('10')
    if change=='selection': window.select_clip('b')
    elif change=='new_session': window.editor=Editor(deepcopy(window.project)); window.refresh()
    else: window.editor.delete_clip('a'); window.changed()
    d.check_current(); before=deepcopy(window.project); d.add_key()
    assert d._closed_flag and window.project==before and window.audio_gain_dialog is None


def test_muted_disabled_source_envelope_does_not_enable_audio(window,app):
    window.editor._clip('a').muted=True; window.editor._clip('a').source_audio_enabled=False
    d=opened(window,app); add(d,1,24)
    clip=window.selected_clip()
    assert clip.muted and not clip.source_audio_enabled
    assert 'do not enable or unmute' in d.status.text()
    window.editor.set_track_edit_lock(2,True); window.changed(); d.check_current()
    before=deepcopy(window.project); assert not d.add_button.isEnabled() and not can_open_audio_gain(window)
    d.add_key(); assert window.project==before


@pytest.mark.parametrize('locale',['en','zh-CN'])
def test_bilingual_narrow_accessibility_and_gain_readout(window,app,locale):
    d=opened(window,app); add(d,1,24); window.editor.update_clip('a',gain_db=24);window.changed()
    set_locale(locale);app.processEvents(); d.fit_available_screen(QRect(0,0,420,600));app.processEvents()
    assert d.width()<=388 and d.height()<=536 and d.scroll.horizontalScrollBar().maximum()==0
    assert '48 dB' in d.summary.text()
    for control in (d.sample_edit,d.offset_edit,d.key_selector,d.interpolation,d.timeline,d.close_button):
        assert control.accessibleName()
    assert d.close_button.isVisible() and d.close_button.fontMetrics().horizontalAdvance(d.close_button.text())<d.close_button.width()
    if locale=='zh-CN':
        assert '片段音量' in d.windowTitle() and '不自动限幅' in d.scope.text()
        assert '视频帧' in d.timeline.toolTip()


def test_rejected_model_edit_reloads_authoritative_selection(window,app,monkeypatch):
    d=opened(window,app);add(d,11,-6);before=deepcopy(window.project);undo=len(window.editor._undo)
    def fail(*_a,**_k):raise ValueError('Invalid audio gain keyframe fields')
    monkeypatch.setattr(window.editor,'update_clip',fail)
    add(d,22,-3)
    assert window.project==before and len(window.editor._undo)==undo
    assert d._selected==11 and d.key_selector.currentData()==11
    assert 'rejected' in d.status.text() and window.test_warnings

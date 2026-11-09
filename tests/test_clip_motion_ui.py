from copy import deepcopy
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from lumen.model import Clip, Editor, Project
from lumen.clip_motion import ClipMotionKeyframe as K
from lumen.clip_transform import ClipTransform
from lumen.clip_composite import ClipComposite
from lumen.ui_clip_motion import ClipMotionDialog
from lumen.i18n import set_locale
from test_speed_ui import app, window


@pytest.fixture
def dialog(window):
    d=ClipMotionDialog(window); d.show()
    yield d
    d.reject()


def test_add_apply_single_undo_cancel_noop(window,dialog,app):
    before=deepcopy(window.project);rev=window.revision
    dialog.controls['x'].setValue(15); assert not dialog.apply_button.isEnabled()
    dialog.add_key(); dialog.frame_spin.setValue(15)
    dialog.controls['x'].setValue(35);dialog.controls['opacity'].setValue(.25)
    dialog.interpolation.setCurrentIndex(dialog.interpolation.findData('hold'));dialog.add_key()
    assert window.project==before
    expected=deepcopy(dialog.keys);dialog.apply_settings()
    assert window.revision==rev+1 and window.selected_clip().motion_keyframes==expected
    assert len(window.editor._undo)==1
    window.undo();assert window.project==before
    window.redo(); assert window.selected_clip().motion_keyframes==expected
    d=ClipMotionDialog(window);d.controls['scale'].setValue(.5);d.add_key();d.reject()
    assert window.selected_clip().motion_keyframes==expected
    rev=window.revision;d=ClipMotionDialog(window);d.apply_settings();assert window.revision==rev


def test_first_add_exact_static_values_and_no_invented_endpoints(window):
    exact=.12345678901234
    window.editor.update_clip('c',transform=ClipTransform(x=exact,scale=.76543219876),composite=ClipComposite(True,exact));window.changed()
    d=ClipMotionDialog(window);d.add_key()
    assert len(d.keys)==1 and d.keys[0].x==exact and d.keys[0].opacity==exact
    assert d.keys[0].scale==window.selected_clip().transform.scale
    d.reject()


def test_hidden_retention_moves_collision_cancel_and_static(window,monkeypatch):
    keys=[K(-8,x=-8),K(0,x=0,opacity=.2),K(10,x=20,opacity=.8),K(60,x=60)]
    window.editor.update_clip('c',motion_keyframes=keys,transform=ClipTransform(rotation=29,mirror_x=True),composite=ClipComposite(True));window.changed()
    d=ClipMotionDialog(window);assert '-8' in d.hidden_keys.text() and '60' in d.hidden_keys.text()
    d.move_key(0,10);assert d.keys==keys
    d.move_key(-8,3);assert d.keys==keys
    d.move_key(0,3);assert [k.frame for k in d.keys]==[-8,3,10,60]
    d.frame_spin.setValue(5);values=d.evaluated()
    monkeypatch.setattr(QMessageBox,'question',lambda *_: QMessageBox.StandardButton.No)
    d.make_static();assert d.keys
    monkeypatch.setattr(QMessageBox,'question',lambda *_: QMessageBox.StandardButton.Yes)
    d.make_static();assert d.keys==[] and window.selected_clip().motion_keyframes==keys
    d.apply_settings();c=window.selected_clip()
    assert (c.transform.x,c.transform.y,c.transform.scale,c.composite.opacity)==values
    assert c.transform.rotation==29 and c.transform.mirror_x and c.composite.transparent_outside_transform
    assert not c.motion_keyframes
    window.undo();assert window.selected_clip().motion_keyframes==keys


@pytest.mark.parametrize('change',['revision','selection','multi','project','editor','session','playhead','close'])
def test_stale_callbacks_do_not_apply(window,dialog,change):
    dialog.add_key();before=deepcopy(window.project)
    if change=='revision': window.editor.update_clip('c',muted=True)
    elif change=='selection': window.selection_generation+=1
    elif change=='multi':
        window.project.clips.append(Clip('other','m',1,20,0,31)); window.set_selected_clip_ids(('c','other'))
    elif change=='project': window.editor.project=deepcopy(window.project)
    elif change=='editor': window.editor=Editor(deepcopy(window.project))
    elif change=='session': window.editor.session_id='new'
    elif change=='playhead': window.timeline.playhead+=1
    elif change=='close': window.closing=True
    current=deepcopy(window.project);rev=window.revision
    dialog.controls['x'].setValue(12);dialog.add_key();dialog.move_key(0,2);dialog.apply_settings()
    assert window.project==current and window.revision==rev and not dialog.apply_button.isEnabled()


def test_main_static_four_disabled_rotation_and_mirrors_preserved(window):
    keys=[K(0,x=20,scale=.5,opacity=.4),K(10,x=30)]
    window.editor.update_clip('c',motion_keyframes=keys,transform=ClipTransform(x=.123456789,scale=.8),composite=ClipComposite(True,.123456789));window.changed()
    for name in ('x','y','scale'): assert not window.transform_spins[name].isEnabled()
    assert not window.composite_opacity.isEnabled() and window.transform_spins['rotation'].isEnabled()
    window.transform_spins['x'].setValue(999);window.transform_spins['rotation'].setValue(30);window.apply_transform()
    assert window.selected_clip().transform.x==.123456789 and window.selected_clip().transform.rotation==30
    window.composite_opacity.setValue(99);window.composite_transparent_outside_transform.setChecked(False);window.apply_composite()
    assert window.selected_clip().composite.opacity==.123456789
    window.reset_transform();window.reset_composite()
    assert window.selected_clip().motion_keyframes==keys and window.selected_clip().transform.x==.123456789
    assert window.selected_clip().composite.opacity==.123456789


def test_frame_change_discards_unadded_values_without_moving_playhead(window,dialog):
    playhead=window.timeline.playhead
    dialog.controls['x'].setValue(99);dialog.frame_spin.setValue(5)
    assert dialog.controls['x'].value()==0 and not dialog.keys and not dialog._dirty
    assert window.timeline.playhead==playhead


def test_timeline_escape_focus_cancel_do_not_commit(window,dialog,app):
    dialog.add_key();dialog.frame_spin.setValue(10);dialog.controls['x'].setValue(10);dialog.add_key()
    timeline=dialog.key_timeline;dialog.select_key(0);before=deepcopy(dialog.keys)
    timeline.setFocus();QTest.keyPress(timeline,Qt.Key.Key_Right)
    QTest.keyClick(timeline,Qt.Key.Key_Escape);QTest.keyRelease(timeline,Qt.Key.Key_Right);app.processEvents()
    assert dialog.keys==before


@pytest.mark.parametrize('locale',['en','zh-CN'])
def test_locale_ownership_and_dialog_controls(window,locale):
    set_locale(locale);d=ClipMotionDialog(window)
    assert ('画面动画' in d.windowTitle()) if locale=='zh-CN' else ('Picture animation' in d.windowTitle())
    d.add_key();snapshot=deepcopy(d.keys);set_locale('en' if locale=='zh-CN' else 'zh-CN')
    assert d.keys==snapshot;d.reject();set_locale('en')


@pytest.mark.parametrize('locale',['en','zh-CN'])
def test_narrow_editor_does_not_clip_horizontal_controls(window,app,locale):
    set_locale(locale);d=ClipMotionDialog(window);d.resize(320,480);d.show();app.processEvents()
    assert d.scroll.widget().width() <= d.scroll.viewport().width()
    assert d.apply_button.geometry().right() < d.width()
    d.reject();set_locale('en')

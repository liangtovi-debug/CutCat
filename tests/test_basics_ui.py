"""Actual Qt controls, repeated editing and stable narrow bilingual forms."""
from copy import deepcopy
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from lumen.i18n import set_locale
from lumen.model import Clip
from lumen.clip_transform import ClipTransform
from test_speed_ui import app, window


def test_staged_transform_apply_reset_undo_and_noop(window):
    w=window; before=deepcopy(w.project); rev=w.revision
    w.transform_spins['x'].setValue(12.5); w.transform_spins['scale'].setValue(.5)
    w.transform_spins['rotation'].setValue(45); w.transform_mirror_x.setChecked(True)
    assert w.project==before and w.revision==rev
    QTest.mouseClick(w.apply_transform_button, Qt.MouseButton.LeftButton)
    assert w.selected_clip().transform==ClipTransform(12.5,0,.5,45,True,False)
    after=deepcopy(w.project)
    assert w.revision==rev+1 and not w.apply_transform_button.isEnabled()
    w.apply_transform(); assert w.revision==rev+1
    w.undo(); assert w.project==before
    w.redo(); assert w.project==after
    QTest.mouseClick(w.reset_transform_button, Qt.MouseButton.LeftButton)
    assert w.selected_clip().transform==ClipTransform()
    w.undo(); assert w.project==after


def test_draft_reset_no_history_selection_and_audio_disabled(window):
    w=window; rev=w.revision
    w.transform_spins['x'].setValue(100); w.reset_transform()
    assert w.transform_spins['x'].value()==0 and w.revision==rev
    w.transform_spins['scale'].setValue(2)
    w.editor.update_clip('c', track=2); w.changed()
    assert not w.transform_group.isEnabled()
    w.apply_transform(); assert w.selected_clip().transform==ClipTransform()
    w.selection=''; w.refresh_inspector()
    assert not w.apply_transform_button.isEnabled() and not w.ripple_delete_button.isEnabled()


def test_ripple_button_independent_of_staged_timing_and_single_undo(window):
    w=window; w.project.clips += [Clip('next','m',0,50,40,20),Clip('audio','m',2,60,0,20)]
    w.changed(); before=deepcopy(w.project); rev=w.revision
    w.start_spin.setValue(100); w.duration_spin.setValue(5)
    QTest.mouseClick(w.ripple_delete_button, Qt.MouseButton.LeftButton)
    assert [(c.id,c.start) for c in w.project.clips]==[('next',19),('audio',60)]
    assert w.revision==rev+1 and w.selected_clip() is None
    w.ripple_delete_selected(); assert w.revision==rev+1
    w.undo(); assert w.project==before
    w.redo(); assert [(c.id,c.start) for c in w.project.clips]==[('next',19),('audio',60)]


@pytest.mark.parametrize('locale', ['en','zh-CN'])
def test_locale_workspace_narrow_controls_keep_staged_values(window,app,locale):
    w=window; w.transform_spins['rotation'].setValue(33); before=deepcopy(w.project)
    set_locale(locale); w.resize(1100,800); w.show(); app.processEvents()
    for mode in ('simple','professional','simple'):
        w.set_workspace_mode(mode); app.processEvents()
        assert w.transform_spins['rotation'].value()==33 and w.project==before
    w.inspector_tabs.setCurrentIndex(0)
    w.inspector.ensureWidgetVisible(w.apply_transform_button); app.processEvents()
    assert w.apply_transform_button.isVisible()
    for spin in w.transform_spins.values():
        assert spin.width() >= 55 and spin.height() >= 22
    assert w.ripple_note.wordWrap() and w.transform_note.wordWrap()
    if locale=='zh-CN':
        assert '仅本轨' in w.ripple_delete_button.text() and '不透明' in w.transform_note.text()
    else:
        assert 'this track' in w.ripple_delete_button.text() and 'not transparent' in w.transform_note.text()


def test_loaded_precision_is_preserved_for_noop_and_unrelated_field(window):
    w=window
    original=ClipTransform(x=1.23456789,y=-2.987654321,scale=.333333333333,rotation=12.3456789)
    w.editor.update_clip('c',transform=original);w.changed();rev=w.revision
    assert not w.apply_transform_button.isEnabled()
    w.apply_transform();assert w.revision==rev and w.selected_clip().transform==original
    w.transform_mirror_x.setChecked(True);w.apply_transform()
    expected=deepcopy(original);expected.mirror_x=True
    assert w.selected_clip().transform==expected and w.revision==rev+1
    w.transform_spins['x'].setValue(9.5);w.apply_transform();expected.x=9.5
    assert w.selected_clip().transform==expected
    w.undo();expected.x=original.x;assert w.selected_clip().transform==expected
    w.redo();assert w.selected_clip().transform.x==9.5

"""Offline Qt checks of real sticker/effect commands and edit boundaries."""
from test_ui import app, window, populate, pump_until
from pathlib import Path
import threading
import pytest

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog
from lumen.model import ClipEffect, Media, Overlay, OverlayKeyframe
from lumen.sticker_assets import load_sticker


def sticker_file(tmp_path, name='sticker.png'):
    path = tmp_path / name
    image = QImage(32, 24, QImage.Format.Format_RGBA8888)
    image.fill(QColor('#80ffbb66'))
    assert image.save(str(path))
    return path


def add_overlay(w, tmp_path, **changes):
    path = sticker_file(tmp_path)
    values = dict(id='sticker', asset_path=str(path), asset_sha256=load_sticker(path).digest,
                  start=5, duration=60, keyframes=[OverlayKeyframe(0, 25, 35, 1, .8)])
    values.update(changes)
    overlay = w.editor.add_overlay(Overlay(**values))
    w.changed(); w.select_overlay(overlay.id)
    return overlay


def add_effect(w, kind='blur'):
    w.effect_kind.setCurrentIndex(0 if kind == 'blur' else 1)
    w.add_clip_effect()
    return w.selected_effect()


def test_sticker_async_import_real_asset_at_playhead_and_one_undo(window, app, tmp_path):
    w=window; populate(w,tmp_path); w.seek_frame(21); path=sticker_file(tmp_path)
    revision=w.revision; w.import_sticker_path(path)
    assert not w.import_sticker_button.isEnabled()
    pump_until(app,lambda:not w.jobs)
    overlay=w.selected_overlay()
    assert overlay.start==21 and overlay.duration==60
    assert overlay.asset_sha256==load_sticker(path).digest
    assert overlay.asset_path==str(path) and overlay.keyframes[0].frame==0
    assert w.revision==revision+1 and w.active_selection_type=='overlay'
    assert w.timeline.overlay_selection==overlay.id and w.timeline.selection==''
    assert w.side_tabs.currentWidget() is w.stickers_panel
    w.undo(); assert not w.project.overlays and len(w.project.clips)==1
    w.redo(); assert w.project.overlays[0]==overlay
    assert path.exists() and w.import_sticker_button.isEnabled() and not w.test_warnings


def test_import_sticker_dialog_uses_local_png_filter(window, app, tmp_path, monkeypatch):
    path=sticker_file(tmp_path); calls=[]
    def choose(*args): calls.append(args); return str(path),''
    monkeypatch.setattr(QFileDialog,'getOpenFileName',choose)
    window.import_sticker_dialog(); pump_until(app,lambda:not window.jobs)
    assert len(window.project.overlays)==1 and '*.png *.json' in calls[0][-1]


def test_import_failed_asset_leaves_project_untouched(window, app, tmp_path):
    w=window; path=tmp_path/'invalid.png'; path.write_bytes(b'not a PNG')
    before=w.project.to_dict(); revision=w.revision
    w.import_sticker_path(path); pump_until(app,lambda:not w.jobs)
    assert w.project.to_dict()==before and w.revision==revision and w.test_warnings
    assert w.sticker_job is None and w.import_sticker_button.isEnabled()


def test_import_does_not_resolve_away_symlink_safety(window, app, tmp_path):
    w=window; real=sticker_file(tmp_path); linked=tmp_path/'linked.png'
    try: linked.symlink_to(real)
    except OSError: pytest.skip('Host does not permit symlink creation')
    w.import_sticker_path(linked); pump_until(app,lambda:not w.jobs)
    assert not w.project.overlays and 'symbolic link' in w.test_warnings[-1]


def test_sticker_stale_import_is_rejected(window, app, tmp_path, monkeypatch):
    import lumen.sticker_assets as assets
    w=window; path=sticker_file(tmp_path); gate=threading.Event(); original=assets.load_sticker
    def slow(*args,**kwargs): assert gate.wait(3); return original(*args,**kwargs)
    monkeypatch.setattr(assets,'load_sticker',slow)
    w.import_sticker_path(path); w.editor.add_caption(0,10,'New edit'); w.changed(); gate.set()
    pump_until(app,lambda:not w.jobs)
    assert not w.project.overlays and 'project changed' in w.test_warnings[-1]


def test_sticker_cancel_cleans_worker_without_edit(window, app, tmp_path, monkeypatch):
    import lumen.sticker_assets as assets
    def slow(*args,cancel=None,**kwargs):
        cancel.wait(3); raise assets.StickerCancelled('Canceled')
    monkeypatch.setattr(assets,'load_sticker',slow)
    window.import_sticker_path(tmp_path/'anything.png'); window.cancel_jobs()
    pump_until(app,lambda:not window.jobs)
    assert not window.project.overlays and not window.test_warnings
    assert window.sticker_job is None and window.import_sticker_button.isEnabled()


def test_sticker_timing_layer_and_bypass_stage_to_single_undo(window, tmp_path):
    w=window; original=add_overlay(w,tmp_path); before=w.project.to_dict(); revision=w.revision
    w.overlay_start.setValue(12); w.overlay_duration.setValue(90)
    w.overlay_z.setValue(9); w.overlay_enabled.setChecked(False)
    assert w.project.to_dict()==before and w.revision==revision
    w.apply_overlay_timing(); changed=w.selected_overlay()
    assert (changed.start,changed.duration,changed.z_order,changed.enabled)==(12,90,9,False)
    assert w.revision==revision+1
    w.undo(); assert w.project.to_dict()==before
    assert (w.overlay_start.value(),w.overlay_duration.value(),w.overlay_z.value(),w.overlay_enabled.isChecked())==(5,60,0,True)
    w.apply_overlay_timing(); assert w.revision==revision+2  # No-op Apply does not add an edit.


def test_sticker_transform_preserves_animation_and_one_undo(window, tmp_path):
    w=window; add_overlay(w,tmp_path,keyframes=[OverlayKeyframe(),OverlayKeyframe(30,100,40,.5,.3)])
    before=w.project.to_dict(); revision=w.revision
    w.overlay_transform['x'].setValue(75); w.overlay_transform['scale'].setValue(.6)
    w.overlay_transform['opacity'].setValue(.25)
    assert w.project.to_dict()==before
    w.apply_overlay_transform(); overlay=w.selected_overlay()
    assert (overlay.keyframes[0].x,overlay.keyframes[0].scale,overlay.keyframes[0].opacity)==(75,.6,.25)
    assert overlay.keyframes[1]==OverlayKeyframe(30,100,40,.5,.3) and w.revision==revision+1
    w.undo(); assert w.project.to_dict()==before


def test_base_transform_inserts_zero_without_rewriting_imported_leading_key(window, tmp_path):
    w=window; add_overlay(w,tmp_path,keyframes=[OverlayKeyframe(10,20,30,1,1)])
    w.overlay_transform['x'].setValue(99); w.apply_overlay_transform()
    assert w.selected_overlay().keyframes==[OverlayKeyframe(0,99,30,1,1),OverlayKeyframe(10,20,30,1,1)]


def test_keyframe_add_replace_delete_each_single_undo(window, tmp_path):
    w=window; add_overlay(w,tmp_path); original=w.project.to_dict(); revision=w.revision
    assert not w.keyframe_toggle.isChecked() and w.keyframe_panel.isHidden()
    w.keyframe_toggle.setChecked(True)
    w.overlay_keyframe_frame.setValue(20); w.overlay_keyframe_values['x'].setValue(300)
    w.overlay_keyframe_values['y'].setValue(90); w.overlay_keyframe_values['scale'].setValue(2)
    w.overlay_keyframe_values['opacity'].setValue(.25)
    assert w.project.to_dict()==original
    w.apply_overlay_keyframe()
    assert w.selected_overlay().keyframes[-1]==OverlayKeyframe(20,300,90,2,.25)
    assert w.revision==revision+1 and w.overlay_keyframes.currentRow()==1
    w.overlay_keyframe_values['x'].setValue(400); w.apply_overlay_keyframe()
    assert len(w.selected_overlay().keyframes)==2 and w.selected_overlay().keyframes[-1].x==400
    w.undo(); assert w.selected_overlay().keyframes[-1].x==300
    w.overlay_keyframes.selectRow(1); w.delete_overlay_keyframe()
    assert len(w.selected_overlay().keyframes)==1
    w.undo(); assert len(w.selected_overlay().keyframes)==2
    w.overlay_keyframes.selectRow(0); revision=w.revision; w.delete_overlay_keyframe()
    assert w.revision==revision and len(w.selected_overlay().keyframes)==2


def test_shortening_sticker_never_discards_keys_silently(window, tmp_path):
    w=window; add_overlay(w,tmp_path,keyframes=[OverlayKeyframe(),OverlayKeyframe(45,50,0,1,1)])
    before=w.project.to_dict(); revision=w.revision
    w.overlay_duration.setValue(30); w.apply_overlay_timing()
    assert w.project.to_dict()==before and w.revision==revision
    assert w.overlay_duration.value()==60 and 'preserved' in w.test_warnings[-1]


def test_refresh_and_selection_are_not_edits(window, tmp_path):
    w=window; populate(w,tmp_path); add_overlay(w,tmp_path); before=w.project.to_dict(); revision=w.revision
    w.refresh(); w.refresh_effects(); w.select_overlay('sticker'); w.load_overlay_keyframe()
    assert w.project.to_dict()==before and w.revision==revision


def test_timeline_sticker_selection_and_delete_do_not_delete_retained_clip(window, app, tmp_path):
    w=window; clip=populate(w,tmp_path); overlay=add_overlay(w,tmp_path)
    w.show(); app.processEvents()
    QTest.mouseClick(w.timeline,Qt.MouseButton.LeftButton,pos=w.timeline.overlay_rect(overlay).center().toPoint())
    assert w.selected_overlay().id==overlay.id and w.selected_clip() is None
    assert w.selected_clip_ids == () and w.selection == ''
    assert w.active_selection_type=='overlay'
    w.overlay_list.setFocus(); QTest.keyClick(w.overlay_list,Qt.Key.Key_Delete)
    assert not w.project.overlays and len(w.project.clips)==1
    w.undo(); assert len(w.project.overlays)==1


def test_caption_selection_clears_sticker_highlight(window, tmp_path):
    w=window; add_overlay(w,tmp_path); caption=w.editor.add_caption(0,10,'Caption'); w.changed()
    w.select_caption(caption.id)
    assert w.timeline.overlay_selection=='' and w.timeline.caption_selection==caption.id
    w.select_overlay('sticker')
    assert w.timeline.caption_selection=='' and w.selected_caption() is None


def test_delete_in_bin_and_numeric_fields_never_deletes_clip(window, app, tmp_path):
    w=window; clip=populate(w,tmp_path); w.show(); app.processEvents()
    w.bin.setCurrentRow(0); w.bin.setFocus(); app.processEvents()
    QTest.keyClick(w.bin,Qt.Key.Key_Delete); assert len(w.project.clips)==1
    w.start_spin.setFocus(); app.processEvents(); w.delete_active_selection()
    assert len(w.project.clips)==1 and w.selected_clip().id==clip.id


def test_delete_keyframe_focus_never_deletes_sticker_or_clip(window, app, tmp_path):
    w=window; populate(w,tmp_path)
    add_overlay(w,tmp_path,keyframes=[OverlayKeyframe(),OverlayKeyframe(25,50,0,1,1)])
    w.show(); w.keyframe_toggle.setChecked(True); app.processEvents()
    w.overlay_keyframes.selectRow(1); w.overlay_keyframes.setFocus(); app.processEvents()
    QTest.keyClick(w.overlay_keyframes,Qt.Key.Key_Delete)
    assert len(w.project.clips)==1 and len(w.project.overlays)==1 and len(w.selected_overlay().keyframes)==1


def test_clip_effect_add_settings_bypass_reorder_and_delete(window, tmp_path):
    w=window; populate(w,tmp_path); blur=add_effect(w); vignette=add_effect(w,'vignette')
    assert [e.kind for e in w.effect_clip().effects]==['blur','vignette']
    assert w.selected_effect().id==vignette.id and w.side_tabs.currentWidget() is w.clip_effects_panel
    before=w.project.to_dict(); revision=w.revision
    w.effect_amount.setValue(.65); w.effect_enabled.setChecked(False)
    assert w.project.to_dict()==before
    w.apply_clip_effect(); assert w.revision==revision+1
    assert w.selected_effect().amount==.65 and not w.selected_effect().enabled
    w.undo(); assert w.project.to_dict()==before
    w.move_clip_effect(-1); assert [e.id for e in w.effect_clip().effects]==[vignette.id,blur.id]
    w.undo(); assert [e.id for e in w.effect_clip().effects]==[blur.id,vignette.id]
    w.delete_clip_effect(); assert [e.id for e in w.effect_clip().effects]==[blur.id]
    w.undo(); assert len(w.effect_clip().effects)==2
    assert not w.test_warnings


def test_effect_parameter_ranges_and_noop_apply(window, tmp_path):
    w=window; populate(w,tmp_path); blur=add_effect(w)
    assert w.effect_amount.maximum()==30 and w.effect_amount.suffix()==' px'
    revision=w.revision; w.apply_clip_effect(); assert w.revision==revision
    vignette=add_effect(w,'vignette'); assert w.effect_amount.maximum()==1
    w.select_clip_effect(blur.id); assert w.effect_amount.maximum()==30
    assert not w.effect_up_button.isEnabled() and w.effect_down_button.isEnabled()


def test_effect_controls_disabled_for_no_clip_and_audio(window, tmp_path):
    w=window; assert not w.effects_controls.isEnabled(); w.add_clip_effect(); assert not w.project.clips
    source=tmp_path/'audio.wav'; source.write_bytes(b'audio fixture')
    w.editor.add_media(Media('audio',str(source),'Audio',5,has_video=False,has_audio=True))
    clip=w.editor.add_clip('audio',2); w.changed(); w.select_clip(clip.id)
    assert not w.effects_controls.isEnabled(); w.add_clip_effect()
    assert not w.effect_clip().effects


def test_effect_delete_key_does_not_delete_clip(window, app, tmp_path):
    w=window; populate(w,tmp_path); add_effect(w); w.show(); app.processEvents()
    w.effect_list.setFocus(); app.processEvents(); QTest.keyClick(w.effect_list,Qt.Key.Key_Delete)
    assert len(w.project.clips)==1 and not w.effect_clip().effects
    w.undo(); assert len(w.effect_clip().effects)==1


def test_deleted_selection_never_falls_through_to_retained_clip(window, tmp_path):
    w=window; clip=populate(w,tmp_path); add_overlay(w,tmp_path); w.delete_overlay()
    assert w.selected_overlay() is None and not w.overlay_controls.isEnabled()
    w.delete_active_selection(); assert len(w.project.clips)==1
    w.select_clip(clip.id); add_effect(w); w.delete_clip_effect()
    w.delete_active_selection(); assert len(w.project.clips)==1


def test_project_replacement_clears_stale_overlay_effect_controls(window, tmp_path):
    from lumen.model import Editor,Project
    w=window; populate(w,tmp_path); add_effect(w); add_overlay(w,tmp_path)
    w.editor=Editor(Project()); w.selection=''; w.refresh()
    assert not w.overlay_selection and not w.effect_selection
    assert w.overlay_list.count()==0 and w.effect_list.count()==0
    assert not w.overlay_controls.isEnabled() and not w.effects_controls.isEnabled()


def test_original_gallery_imports_the_real_bundled_animation(window, app):
    w=window; assert w.sticker_choice.count()==3
    index=w.sticker_choice.findText('Pulse ring'); assert index>=0
    w.sticker_choice.setCurrentIndex(index); w.add_original_sticker()
    pump_until(app,lambda:not w.jobs)
    overlay=w.selected_overlay(); asset=load_sticker(overlay.asset_path)
    assert len(asset.frames)==8 and asset.frame_duration==3 and asset.loop
    assert w.sticker_display_name(overlay.asset_path)=='Pulse ring'
    assert overlay.keyframes[0].opacity==1 and not w.test_warnings


def test_effect_and_sticker_selection_highlights_survive_edit_and_zoom(window,tmp_path):
    w=window; clip=populate(w,tmp_path); add_effect(w)
    w.effect_amount.setValue(4); w.apply_clip_effect()
    assert w.timeline.selection==clip.id
    add_overlay(w,tmp_path); w.zoom_timeline(5)
    assert w.timeline.selection=='' and w.timeline.overlay_selection=='sticker'

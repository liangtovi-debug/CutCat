"""Effects and stickers are independent, state-preserving inspector panels."""
from __future__ import annotations

from copy import deepcopy
import pytest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTabWidget

from lumen.model import Editor
from test_ui import app, window, populate
from test_effects_ui import add_effect, add_overlay
from test_workspace_ui import state


def row_ids(widget):
    return [widget.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(widget.count())]


@pytest.mark.parametrize('language,labels', [
    ('en', ('Effects', 'Stickers')),
    ('zh-CN', ('特效', '贴纸')),
])
@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_independent_top_level_panels_and_workspace_visibility(
        window, app, language, labels, mode):
    w = window
    w.set_ui_language(language, persist=False)
    w.set_workspace_mode(mode, persist=False)
    w.show(); app.processEvents()
    effects_index = w.side_tabs.indexOf(w.clip_effects_panel)
    stickers_index = w.side_tabs.indexOf(w.stickers_panel)
    assert effects_index >= 0 and stickers_index >= 0
    assert effects_index != stickers_index
    assert w.side_tabs.tabText(effects_index) == labels[0]
    assert w.side_tabs.tabText(stickers_index) == labels[1]
    assert w.side_tabs.isTabVisible(effects_index) == (mode == 'professional')
    assert w.side_tabs.isTabVisible(stickers_index)
    assert w.clip_effects_panel.objectName() == 'clipEffectsPanel'
    assert w.stickers_panel.objectName() == 'stickersPanel'
    assert not hasattr(w, 'effects_tabs')
    assert not w.clip_effects_panel.findChildren(QTabWidget)
    assert not w.stickers_panel.findChildren(QTabWidget)
    assert w.clip_effects_panel.isAncestorOf(w.effect_list)
    assert not w.clip_effects_panel.isAncestorOf(w.overlay_list)
    assert w.stickers_panel.isAncestorOf(w.overlay_list)
    assert not w.stickers_panel.isAncestorOf(w.effect_list)
    w.side_tabs.setCurrentWidget(w.stickers_panel); app.processEvents()
    assert w.sticker_choice.isVisible()
    assert not w.effect_kind.isVisible()
    if mode == 'professional':
        w.side_tabs.setCurrentWidget(w.clip_effects_panel); app.processEvents()
        assert w.effect_kind.isVisible()
        assert not w.sticker_choice.isVisible()


def test_selecting_each_kind_routes_to_its_panel_without_merging_lists(window, tmp_path):
    w = window
    clip = populate(w, tmp_path)
    blur = add_effect(w)
    vignette = add_effect(w, 'vignette')
    first = add_overlay(w, tmp_path)
    second = add_overlay(w, tmp_path, id='second-sticker', start=12)
    project = w.project.to_dict()
    revision = w.revision
    w.select_clip_effect(blur.id)
    assert w.side_tabs.currentWidget() is w.clip_effects_panel
    assert w.selected_overlay().id == second.id
    assert w.active_selection_type == 'effect'
    w.select_overlay(first.id)
    assert w.side_tabs.currentWidget() is w.stickers_panel
    assert w.selected_effect().id == blur.id
    assert w.selected_clip() is None and w.selected_clip_ids == ()
    assert w.active_selection_type == 'overlay'
    assert row_ids(w.effect_list) == [blur.id, vignette.id]
    assert row_ids(w.overlay_list) == [first.id, second.id]
    assert set(row_ids(w.effect_list)).isdisjoint(row_ids(w.overlay_list))
    assert w.project.to_dict() == project and w.revision == revision


def test_panel_navigation_locale_and_workspace_retain_both_staged_editors(
        window, app, tmp_path):
    w = window
    clip = populate(w, tmp_path)
    overlay = add_overlay(w, tmp_path)
    w.select_clip(clip.id)
    effect = add_effect(w)
    w.overlay_start.setValue(41)
    w.overlay_transform['x'].setValue(123.4)
    w.keyframe_toggle.setChecked(True)
    w.overlay_keyframe_frame.setValue(17)
    w.overlay_keyframe_values['opacity'].setValue(.321)
    w.effect_amount.setValue(7.5)
    w.effect_enabled.setChecked(False)
    widgets = (w.stickers_panel, w.clip_effects_panel, w.overlay_list, w.effect_list)
    snapshot = state(w)
    for language in ('zh-CN', 'en', 'zh-CN', 'en'):
        w.set_ui_language(language, persist=False)
        w.side_tabs.setCurrentWidget(w.stickers_panel)
        w.side_tabs.setCurrentWidget(w.clip_effects_panel)
        w.set_workspace_mode('simple', persist=False)
        assert w.side_tabs.currentWidget() is w.stickers_panel
        w.set_ui_language('en' if language == 'zh-CN' else 'zh-CN', persist=False)
        w.set_workspace_mode('professional', persist=False)
        app.processEvents()
        assert w.side_tabs.currentWidget() is w.clip_effects_panel
        assert (w.stickers_panel, w.clip_effects_panel, w.overlay_list, w.effect_list) == widgets
        assert w.selected_overlay().id == overlay.id
        assert w.selected_effect().id == effect.id
        assert w.overlay_list.currentItem().data(Qt.ItemDataRole.UserRole) == overlay.id
        assert w.effect_list.currentItem().data(Qt.ItemDataRole.UserRole) == effect.id
        assert w.overlay_start.value() == 41
        assert w.overlay_transform['x'].value() == 123.4
        assert w.overlay_keyframe_frame.value() == 17
        assert w.overlay_keyframe_values['opacity'].value() == .321
        assert w.keyframe_toggle.isChecked()
        assert w.effect_amount.value() == 7.5
        assert not w.effect_enabled.isChecked()
        assert state(w) == snapshot


@pytest.mark.parametrize('panel_name', ['stickers_panel', 'ai_panel', 'inspector'])
def test_visible_panels_keep_their_destination_across_modes(window, panel_name):
    panel = getattr(window, panel_name)
    window.side_tabs.setCurrentWidget(panel)
    for _ in range(3):
        window.set_workspace_mode('simple', persist=False)
        assert window.side_tabs.currentWidget() is panel
        window.set_workspace_mode('professional', persist=False)
        assert window.side_tabs.currentWidget() is panel


def test_retained_effect_selection_cannot_open_hidden_panel_in_simple(window, tmp_path):
    w = window
    populate(w, tmp_path)
    effect = add_effect(w)
    w.set_workspace_mode('simple', persist=False)
    before = w.project.to_dict()
    w.select_clip_effect(effect.id)
    assert w.side_tabs.currentWidget() is w.stickers_panel
    assert not w.side_tabs.isTabVisible(w.side_tabs.indexOf(w.clip_effects_panel))
    assert w.project.to_dict() == before


def test_separate_panels_apply_only_their_own_domain_and_keep_single_undo(window, tmp_path):
    w = window
    clip = populate(w, tmp_path)
    overlay = add_overlay(w, tmp_path)
    w.select_clip(clip.id)
    effect = add_effect(w)
    w.select_overlay(overlay.id)
    before = w.project.to_dict()
    revision = w.revision
    w.overlay_start.setValue(22)
    w.apply_overlay_timing()
    assert w.selected_effect().amount == effect.amount
    assert w.selected_overlay().start == 22
    assert w.revision == revision + 1
    w.undo()
    assert w.project.to_dict() == before
    w.redo()
    assert w.selected_overlay().start == 22
    w.select_clip_effect(effect.id)
    before = w.project.to_dict()
    revision = w.revision
    w.effect_amount.setValue(8)
    w.apply_clip_effect()
    assert w.selected_effect().amount == 8
    assert w.selected_overlay().start == 22
    assert w.revision == revision + 1
    w.undo()
    assert w.project.to_dict() == before
    w.redo()
    assert w.selected_effect().amount == 8


@pytest.mark.parametrize('kind', ['caption', 'overlay'])
def test_inactive_effect_panel_retains_draft_but_requires_explicit_row_activation(
        window, app, tmp_path, kind):
    w = window; clip = populate(w, tmp_path)
    blur = add_effect(w); vignette = add_effect(w, 'vignette')
    overlay = add_overlay(w, tmp_path)
    caption = w.editor.add_caption(0, 30, 'Caption'); w.changed()
    w.select_clip_effect(blur.id)
    w.effect_amount.setValue(7.5); w.effect_enabled.setChecked(False)
    before = state(w); undo = len(w.editor._undo)
    getattr(w, 'select_' + kind)((caption if kind == 'caption' else overlay).id)
    assert w.project.to_dict() == before[0] and w.revision == before[1]
    assert w.selected_clip_ids == () and w.selection == '' and w.effect_clip() is None
    assert w.selected_effect().id == blur.id
    assert row_ids(w.effect_list) == [blur.id, vignette.id]
    assert w.effect_list.isEnabled() and not w.effect_editor.isEnabled()
    assert not w.effect_add_button.isEnabled() and not w.effect_kind.isEnabled()
    assert w.effect_amount.value() == 7.5 and not w.effect_enabled.isChecked()
    inactive = state(w)
    w.apply_clip_effect(); w.move_clip_effect(1); w.delete_clip_effect(); w.add_clip_effect()
    assert state(w) == inactive and len(w.editor._undo) == undo
    # A real click on the already-selected row must reactivate it too; merely
    # changing visible panels must not create an implicit edit target.
    w.side_tabs.setCurrentWidget(w.clip_effects_panel); w.show(); app.processEvents()
    assert w.active_selection_type == kind and w.effect_clip() is None
    QTest.mouseClick(w.effect_list.viewport(), Qt.MouseButton.LeftButton,
                    pos=w.effect_list.visualItemRect(w.effect_list.currentItem()).center())
    assert w.active_selection_type == 'effect' and w.effect_clip().id == clip.id
    assert w.selected_clip() is None and w.selected_clip_ids == ()
    assert w.effect_amount.value() == 7.5 and not w.effect_enabled.isChecked()
    assert w.effect_editor.isEnabled() and w.effect_add_button.isEnabled()
    w.apply_clip_effect()
    assert w.selected_effect().amount == 7.5 and not w.selected_effect().enabled
    assert w.revision == before[1] + 1 and len(w.editor._undo) == undo + 1
    assert w.project.to_dict()['overlays'] == before[0]['overlays']
    assert w.project.to_dict()['captions'] == before[0]['captions']


def test_multiselection_keeps_effect_draft_disabled_and_never_reactivates_anchor(window, tmp_path):
    w = window; clip = populate(w, tmp_path)
    other = w.editor.add_clip(clip.media_id, 1, 10); w.changed()
    blur = add_effect(w); vignette = add_effect(w, 'vignette'); w.select_clip_effect(blur.id)
    w.effect_amount.setValue(7.5)
    w.select_clip(clip.id); w.toggle_clip_selection(other.id)
    before = state(w); undo = len(w.editor._undo)
    assert row_ids(w.effect_list) == [blur.id, vignette.id]
    assert w.effect_amount.value() == 7.5 and not w.effects_controls.isEnabled()
    assert w.effect_clip() is None and w.selected_effect() is None
    w.select_clip_effect(blur.id); w.load_clip_effect()
    w.apply_clip_effect(); w.move_clip_effect(1); w.delete_clip_effect(); w.add_clip_effect()
    assert state(w) == before and len(w.editor._undo) == undo
    assert w.selected_clip_ids == (clip.id, other.id) and w.active_selection_type == 'clip'
    w.select_clip(clip.id); w.select_clip_effect(blur.id)
    assert w.effect_amount.value() == 7.5


@pytest.mark.parametrize('active_kind', ['clip', 'effect', 'overlay'])
def test_replacement_editor_same_ids_cannot_reactivate_retained_effect_draft(window, tmp_path, active_kind):
    w = window; clip = populate(w, tmp_path)
    effect = add_effect(w); overlay = add_overlay(w, tmp_path)
    w.select_clip_effect(effect.id); w.effect_amount.setValue(7.5)
    if active_kind == 'clip':
        w.select_clip(clip.id)
    elif active_kind == 'overlay':
        w.select_overlay(overlay.id)
    w.editor = Editor(deepcopy(w.project))
    before = state(w)
    assert w.effect_clip() is None and w.selected_effect() is None
    w.select_clip_effect(effect.id); w.load_clip_effect()
    w.apply_clip_effect(); w.move_clip_effect(1); w.delete_clip_effect(); w.add_clip_effect()
    assert state(w) == before and not w.editor.can_undo
    w.refresh()
    assert w.effect_list.count() == 0 and not w.effects_controls.isEnabled()
    assert w.selected_clip_ids == () and w.selected_effect() is None
    w.select_clip(clip.id); w.select_clip_effect(effect.id)
    assert w.effect_amount.value() == effect.amount  # The old draft cannot cross sessions.
    w.effect_amount.setValue(8); w.apply_clip_effect()
    assert w.selected_effect().amount == 8 and len(w.editor._undo) == 1


def test_changed_effect_payload_replaces_inactive_draft_and_deleted_owner_clears_panel(window, tmp_path):
    w = window; clip = populate(w, tmp_path)
    effect = add_effect(w); overlay = add_overlay(w, tmp_path)
    w.select_clip_effect(effect.id); w.effect_amount.setValue(7.5)
    w.select_overlay(overlay.id)
    w.editor.update_effect(clip.id, effect.id, amount=4.); w.changed()
    assert w.selected_effect().amount == 4. and w.effect_amount.value() == 4.
    assert not w.effect_editor.isEnabled() and w.effect_clip() is None
    w.select_clip_effect(effect.id); revision = w.revision
    w.apply_clip_effect(); assert w.revision == revision
    w.select_overlay(overlay.id)
    w.editor.delete_clip(clip.id); w.changed()
    assert w.effect_list.count() == 0 and w.selected_effect() is None and w.effect_clip() is None
    assert not w.effects_controls.isEnabled()


def test_inactive_effect_mutation_guard_ignores_inconsistent_single_clip_tuple(window, tmp_path):
    w = window; clip = populate(w, tmp_path); effect = add_effect(w)
    w.select_clip(clip.id); w.active_selection_type = 'overlay'
    assert w.selected_clip().id == clip.id and w.selected_effect().id == effect.id
    assert w.effect_clip() is None
    before = state(w)
    w.effect_amount.setValue(7.5)
    w.apply_clip_effect(); w.move_clip_effect(1); w.delete_clip_effect(); w.add_clip_effect()
    assert state(w) == before


def test_reordering_same_effect_preserves_draft_and_updates_boundary_buttons(window, tmp_path):
    w = window; populate(w, tmp_path)
    blur = add_effect(w); vignette = add_effect(w, 'vignette'); w.select_clip_effect(blur.id)
    w.effect_amount.setValue(7.5)
    assert not w.effect_up_button.isEnabled() and w.effect_down_button.isEnabled()
    w.move_clip_effect(1)
    assert row_ids(w.effect_list) == [vignette.id, blur.id]
    assert w.effect_amount.value() == 7.5 and w.selected_effect().amount == blur.amount
    assert w.effect_up_button.isEnabled() and not w.effect_down_button.isEnabled()
    w.undo()
    assert row_ids(w.effect_list) == [blur.id, vignette.id]
    assert w.effect_amount.value() == 7.5
    assert not w.effect_up_button.isEnabled() and w.effect_down_button.isEnabled()


def test_effect_changed_while_multiselected_reloads_disabled_controls_before_return(window, tmp_path):
    w = window; clip = populate(w, tmp_path)
    other = w.editor.add_clip(clip.media_id, 1, 10); w.changed()
    effect = add_effect(w, 'vignette'); w.effect_radius.setValue(.5)
    w.select_clip(clip.id); w.toggle_clip_selection(other.id)
    w.editor.update_effect(clip.id, effect.id, radius=1.5); w.changed()
    assert not w.effects_controls.isEnabled() and w.effect_radius.value() == 1.5
    w.select_clip(clip.id); w.select_clip_effect(effect.id)
    assert w.effect_radius.value() == 1.5 and not w.effect_radius.isHidden()
    revision = w.revision; w.apply_clip_effect()
    assert w.revision == revision

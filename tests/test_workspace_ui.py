"""Workspace layout is an app preference, never a project edit or render mode."""
from copy import deepcopy
from pathlib import Path
import threading

import pytest

from test_ui import app, populate, pump_until
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from lumen.model import ClipEffect, Grade, Media, Overlay, OverlayKeyframe, Project
from lumen.sticker_assets import load_sticker
from lumen.ui import MainWindow
from lumen.ui_workspace import BASIC_GRADE_FIELDS, hidden_settings_summary


@pytest.fixture
def mode_window(app, monkeypatch, tmp_path):
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: pytest.fail(str(args[2])))
    settings = QSettings(str(tmp_path / 'workspace.ini'), QSettings.Format.IniFormat)
    window = MainWindow(workspace_settings=settings)
    yield window
    window.cancel_jobs()
    for job in list(window.jobs):
        assert job.wait(5000)
    app.processEvents()
    window.dirty = False
    window.closing = True
    window.close()
    app.processEvents()


def add_advanced_settings(window, tmp_path):
    clip = populate(window, tmp_path)
    grade = Grade(
        exposure=.123456, linear_exposure=.4, lift=[.018123, -.01, .02],
        gamma=[1.1, .97, 1.04], gain=[1.03, .98, 1.02],
        master_curve=[[0., 0.], [.42, .51], [1., 1.]],
        red_curve=[[0., 0.], [.5, .6], [1., 1.]],
    )
    window.editor.update_clip(clip.id, grade=grade, effects=[
        ClipEffect('soften', 'blur', 2.5), ClipEffect('edge', 'vignette', .25, False),
    ])
    asset = tmp_path / 'sticker.png'
    image = QImage(16, 16, QImage.Format.Format_RGBA8888)
    image.fill(QColor('#d8a653'))
    assert image.save(str(asset))
    window.editor.add_overlay(Overlay(
        'animated', str(asset), load_sticker(asset).digest, 0, 80,
        keyframes=[OverlayKeyframe(), OverlayKeyframe(50, 80, 45, .7, .5)],
    ))
    window.changed()
    window.select_clip(clip.id)
    return window.selected_clip()


def state(window):
    return (
        window.project.to_dict(), window.revision, window.dirty,
        [p.to_dict() for p in window.editor._undo],
        [p.to_dict() for p in window.editor._redo],
        window.selection, window.active_selection_type, window.timeline.playhead,
        window.preview_revision, window.preview_mode, window.loaded_preview_mode,
        deepcopy(window.preview_paths), window.preview_request,
        window.preview_pending, window.player.source(),
    )


def test_fresh_simple_default_exposes_core_workflows(mode_window, app):
    window = mode_window
    window.show(); app.processEvents()
    assert window.workspace_mode == 'simple'
    assert window.workspace_buttons['simple'].isChecked()
    assert window.color_tabs.isTabVisible(0)
    assert not window.color_tabs.isTabVisible(1)
    assert not window.color_tabs.isTabVisible(2)
    assert not window.side_tabs.isTabVisible(window.side_tabs.indexOf(window.clip_effects_panel))
    assert window.scopes.isHidden() and window.lut_controls.isHidden()
    assert window.input_color_group.isHidden()
    assert not window.input_color_status.isHidden()
    assert window.keyframe_toggle.isHidden() and window.keyframe_panel.isHidden()
    assert window.color_details_toggle.isHidden() and window.color_details.isHidden()
    assert window.reset_color_button.text() == 'Reset basic color'
    assert [window.side_tabs.tabText(i) for i in range(window.side_tabs.count())] == [
        'Clip', 'Captions', 'Effects', 'Stickers', 'Local AI', 'Sounds',
    ]
    window.side_tabs.setCurrentIndex(1); app.processEvents()
    assert not window.caption_text.isHidden()
    assert not window.tts_voice.isHidden()
    assert not window.tts_button.isHidden()
    assert not window.tts_model.isHidden()
    window.side_tabs.setCurrentWidget(window.ai_panel); app.processEvents()
    assert window.ai_query.isVisible()
    assert not window.advanced_settings_notice.isVisible()


def test_one_click_professional_exposes_only_implemented_controls(mode_window, app):
    window = mode_window
    window.show(); app.processEvents()
    before = state(window)
    QTest.mouseClick(window.workspace_buttons['professional'], Qt.MouseButton.LeftButton)
    assert window.workspace_mode == 'professional'
    assert state(window) == before
    assert window.color_tabs.isTabVisible(1) and window.color_tabs.isTabVisible(2)
    assert window.side_tabs.isTabVisible(window.side_tabs.indexOf(window.clip_effects_panel))
    assert window.scopes.isVisible()
    assert not window.input_color_group.isHidden()
    assert not window.lut_controls.isHidden()
    assert not window.keyframe_toggle.isHidden()
    assert not window.color_details_toggle.isHidden()
    assert window.keyframe_panel.isHidden()  # Optional detail stays collapsed.
    assert window.reset_color_button.text() == 'Reset all color'
    assert 'Same project and render engine' in window.workspace_note.text()


def test_repeated_switches_preserve_project_history_cache_and_staged_values(mode_window, app, tmp_path):
    window = mode_window
    clip = add_advanced_settings(window, tmp_path)
    window.editor.update_clip(clip.id, gain_db=-4)
    window.changed(); window.undo()
    assert window.editor.can_undo and window.editor.can_redo
    window.seek_frame(35)
    window.preview_revision = window.revision
    window.loaded_preview_mode = 'graded'
    window.preview_paths['graded'] = (window.revision, tmp_path / 'cache.mp4')
    window.set_workspace_mode('professional')
    window.color_tabs.setCurrentIndex(2)
    window.side_tabs.setCurrentWidget(window.clip_effects_panel)
    window.color_details_toggle.setChecked(True)
    window.keyframe_toggle.setChecked(True)
    window.overlay_transform['x'].setValue(123.4)
    window.overlay_keyframe_values['opacity'].setValue(.321)
    before = state(window)
    identities = (window.editor, window.project, window.curve_editor, window.stickers_panel, window.clip_effects_panel)
    for _ in range(12):
        window.set_workspace_mode('simple')
        app.processEvents()
        assert window.color_details.isHidden() and window.keyframe_panel.isHidden()
        assert window.color_tabs.currentIndex() == 0
        assert window.side_tabs.currentWidget() is window.stickers_panel
        assert 'Still used in After preview and export' in window.advanced_settings_notice.text()
        assert '1 enabled clip effect' in window.advanced_settings_notice.text()
        assert '1 disabled clip effect' in window.advanced_settings_notice.text()
        assert state(window) == before
        window.set_workspace_mode('professional')
        app.processEvents()
        assert window.color_tabs.currentIndex() == 2
        assert window.side_tabs.currentWidget() is window.clip_effects_panel
        assert window.color_details_toggle.isChecked() and window.keyframe_toggle.isChecked()
        assert not window.color_details.isHidden() and not window.keyframe_panel.isHidden()
        assert window.overlay_transform['x'].value() == 123.4
        assert window.overlay_keyframe_values['opacity'].value() == .321
        assert state(window) == before
    assert identities == (window.editor, window.project, window.curve_editor, window.stickers_panel, window.clip_effects_panel)
    window.redo()
    assert window.selected_clip().gain_db == -4


def test_basic_edit_and_reset_preserve_hidden_advanced_precision(mode_window, tmp_path):
    window = mode_window
    add_advanced_settings(window, tmp_path)
    original = deepcopy(window.selected_clip().grade)
    window.grade_spins['saturation'].setValue(1.35)
    expected = deepcopy(original); expected.saturation = 1.35
    assert window.selected_clip().grade == expected
    window.grade_enabled.setChecked(False)
    expected.enabled = False
    assert window.selected_clip().grade == expected
    assert 'bypassed advanced grade' in window.advanced_settings_notice.text()
    revision = window.revision
    window.reset_grade()
    for field in BASIC_GRADE_FIELDS:
        setattr(expected, field, getattr(Grade(), field))
    assert window.selected_clip().grade == expected
    assert window.revision == revision + 1
    assert window.selected_clip().grade.exposure == .123456
    window.undo()
    expected.saturation = 1.35; expected.linear_exposure = .4
    assert window.selected_clip().grade == expected


def test_preference_survives_reopen_without_entering_project(mode_window, app, tmp_path):
    window = mode_window
    before = state(window)
    window.set_workspace_mode('professional')
    assert state(window) == before
    assert 'workspace' not in str(window.project.to_dict()).lower()
    settings = QSettings(window.workspace_settings.fileName(), QSettings.Format.IniFormat)
    reopened = MainWindow(workspace_settings=settings)
    try:
        assert reopened.workspace_mode == 'professional'
        reopened.set_workspace_mode('simple')
        assert not reopened.dirty and reopened.revision == 0
    finally:
        reopened.dirty = False; reopened.closing = True; reopened.close(); app.processEvents()
    settings = QSettings(window.workspace_settings.fileName(), QSettings.Format.IniFormat)
    assert settings.value('mode') == 'simple'


def test_invalid_saved_preference_falls_back_without_project_changes(app, tmp_path):
    settings = QSettings(str(tmp_path / 'invalid-workspace.ini'), QSettings.Format.IniFormat)
    settings.setValue('mode', 'unavailable-mode')
    window = MainWindow(workspace_settings=settings)
    try:
        assert window.workspace_mode == 'simple'
        before = state(window)
        with pytest.raises(ValueError):
            window.set_workspace_mode('cinema')
        assert state(window) == before
    finally:
        window.dirty = False; window.closing = True; window.close(); app.processEvents()


def test_loading_advanced_project_in_simple_keeps_data_and_warns(mode_window, tmp_path, monkeypatch):
    window = mode_window
    add_advanced_settings(window, tmp_path)
    path = tmp_path / 'advanced.lumen'
    window.project.save(path)
    expected = Project.load(path).to_dict()
    window.dirty = False
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(path), ''))
    window.open_project()
    assert window.workspace_mode == 'simple'
    assert window.project.to_dict() == expected
    assert not window.dirty and not window.editor.can_undo
    assert 'advanced color / LUT' in window.advanced_settings_notice.text()
    assert 'animated sticker' in window.advanced_settings_notice.text()
    window.project_path = tmp_path / 'resaved.lumen'
    assert window.save_project()
    assert Project.load(window.project_path).to_dict() == expected


def test_switch_while_preview_runs_neither_cancels_nor_changes_snapshot(mode_window, app, tmp_path, monkeypatch):
    window = mode_window
    add_advanced_settings(window, tmp_path)
    gate = threading.Event()
    snapshots = []
    def render(engine, snapshot, target, **kwargs):
        snapshots.append(snapshot.to_dict())
        assert gate.wait(4)
        target.write_bytes(b'fixture preview')
        return target
    monkeypatch.setattr('lumen.ui.RenderEngine.render', render)
    expected = window.project.to_dict()
    window.build_preview()
    pump_until(app, lambda: bool(snapshots))
    job = window.preview_job
    before = state(window)
    for mode in ('professional', 'simple', 'professional', 'simple'):
        window.set_workspace_mode(mode)
        assert window.preview_job is job and not job.cancel.is_set()
        assert state(window) == before
    gate.set()
    pump_until(app, lambda: not window.jobs)
    assert snapshots == [expected]
    assert window.preview_revision == window.revision
    assert window.loaded_preview_mode == 'graded'


def test_hidden_effect_selection_cannot_be_deleted_by_global_shortcut(mode_window, tmp_path, monkeypatch):
    window = mode_window
    add_advanced_settings(window, tmp_path)
    window.set_workspace_mode('professional')
    window.select_clip_effect('soften')
    assert window.active_selection_type == 'effect'
    window.set_workspace_mode('simple')
    before = state(window)
    window.delete_active_selection()
    assert state(window) == before
    assert 'Professional' in window.statusBar().currentMessage()
    # Also protect stale focus from a hidden advanced table/list.
    for hidden_focus in (window.effect_list, window.overlay_keyframes):
        monkeypatch.setattr(QApplication, 'focusWidget', lambda: hidden_focus)
        window.delete_active_selection()
        assert state(window) == before


def test_stored_bypassed_settings_are_not_reported_as_active(mode_window, tmp_path):
    window = mode_window
    clip = add_advanced_settings(window, tmp_path)
    clip.grade.enabled = False
    for effect in clip.effects:
        effect.enabled = False
    window.project.overlays[0].enabled = False
    summary = hidden_settings_summary(window.project)
    assert 'Still used' not in summary
    assert 'bypassed advanced grade' in summary
    assert '2 disabled clip effect' in summary
    assert 'hidden sticker animation' in summary


def test_bypassed_source_normalization_remains_visible_and_survives_all_reset(mode_window, tmp_path):
    window = mode_window
    add_advanced_settings(window, tmp_path)
    grade = deepcopy(window.selected_clip().grade)
    grade.input_encoding = 'srgb'
    grade.enabled = False
    window.editor.update_clip(window.selection, grade=grade)
    window.changed()
    assert 'input normalization on 1 clip' in window.advanced_settings_notice.text()
    assert 'bypassed advanced grade' in window.advanced_settings_notice.text()
    window.set_workspace_mode('professional')
    window.reset_grade()
    expected = Grade(input_encoding='srgb')
    assert window.selected_clip().grade == expected
    window.undo()
    assert window.selected_clip().grade == grade


def test_simple_keeps_detail_toggles_hidden_without_losing_expansion(mode_window):
    window = mode_window
    before = state(window)
    window.color_details_toggle.setChecked(True)
    window.keyframe_toggle.setChecked(True)
    assert window.color_details.isHidden() and window.keyframe_panel.isHidden()
    window.set_workspace_mode('professional')
    assert not window.color_details.isHidden() and not window.keyframe_panel.isHidden()
    assert state(window) == before


def test_audio_color_values_are_preserved_but_not_reported_as_rendered(mode_window, tmp_path):
    window = mode_window
    source = tmp_path / 'music.wav'
    source.write_bytes(b'mocked audio fixture')
    window.editor.add_media(Media('audio', str(source), 'Music', 5, has_video=False, has_audio=True))
    clip = window.editor.add_clip('audio', 2)
    grade = Grade(exposure=.123456, lift=[.1, 0., 0.], input_encoding='srgb')
    window.editor.update_clip(clip.id, grade=grade)
    window.changed(); window.select_clip(clip.id)
    before = state(window)
    assert not window.inspector_tabs.isTabEnabled(1)
    assert hidden_settings_summary(window.project) == ''
    assert window.advanced_settings_notice.isHidden()
    for mode in ('professional', 'simple'):
        window.set_workspace_mode(mode)
        assert state(window) == before
        assert not window.inspector_tabs.isTabEnabled(1)
    assert window.selected_clip().grade == grade

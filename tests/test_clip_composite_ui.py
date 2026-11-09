"""Native static PIP controls: explicit apply, stale guards and exact opacity."""
from copy import deepcopy
import os
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from lumen.clip_composite import CLIP_COMPOSITE_ERROR, ClipComposite
from lumen.clip_transform import ClipTransform
from lumen.i18n import set_locale, tr_error
from lumen.i18n_catalog_clip_composite import ERROR_ZH_CN
from lumen.model import Clip, Editor, Project
from test_speed_ui import app, window


def test_staged_apply_reset_single_history_and_preview_invalidation(window):
    w = window; before = deepcopy(w.project); revision = w.revision
    w.preview_revision = revision; w.loaded_preview_mode = w.preview_mode
    w.composite_transparent_outside_transform.setChecked(True)
    w.composite_opacity.setValue(37.5)
    assert w.project == before and w.revision == revision
    assert w.apply_composite_button.isEnabled()
    QTest.mouseClick(w.apply_composite_button, Qt.MouseButton.LeftButton)
    assert w.selected_clip().composite == ClipComposite(True, .375)
    assert w.revision == revision + 1 and len(w.editor._undo) == 1
    assert w.preview_revision != w.revision and not w.play_button.isEnabled()
    assert not w.apply_composite_button.isEnabled()
    after = deepcopy(w.project)
    w.apply_composite(); assert w.revision == revision + 1
    w.undo(); assert w.project == before
    w.redo(); assert w.project == after
    QTest.mouseClick(w.reset_composite_button, Qt.MouseButton.LeftButton)
    assert w.selected_clip().composite == ClipComposite()
    assert w.selected_clip().transform == before.clips[0].transform
    w.undo(); assert w.project == after
    w.redo(); assert w.selected_clip().composite == ClipComposite()
    assert not w.test_warnings


def test_loaded_precision_preserved_when_only_toggle_changes(window):
    w = window; exact = .123456789012345
    w.editor.update_clip('c', composite=ClipComposite(False, exact)); w.changed()
    revision = w.revision; undo = deepcopy(w.editor._undo)
    assert w.composite_opacity.value() == 12.3 and '%' in w.composite_opacity.suffix()
    assert not w.apply_composite_button.isEnabled()
    w.apply_composite(); assert w.revision == revision and w.editor._undo == undo
    w.composite_transparent_outside_transform.setChecked(True); w.apply_composite()
    assert w.selected_clip().composite == ClipComposite(True, exact)
    w.composite_opacity.setValue(22.2); w.apply_composite()
    assert w.selected_clip().composite == ClipComposite(True, 22.2 / 100)
    w.undo(); assert w.selected_clip().composite == ClipComposite(True, exact)
    w.redo(); assert w.composite_opacity.value() == 22.2


def test_explicit_opacity_edit_back_to_rounded_display_counts_as_an_edit(window):
    w = window; exact = .123456789012345
    w.editor.update_clip('c', composite=ClipComposite(False, exact)); w.changed()
    revision = w.revision
    w.composite_opacity.setValue(12.4); w.composite_opacity.setValue(12.3)
    assert w.apply_composite_button.isEnabled()
    w.apply_composite()
    assert w.selected_clip().composite.opacity == 12.3 / 100 and w.revision == revision + 1
    w.undo(); assert w.selected_clip().composite.opacity == exact


def test_zero_percent_retains_audio_timing_and_separate_transform_apply(window):
    w = window; before = deepcopy(w.selected_clip())
    w.transform_spins['scale'].setValue(.5); w.transform_spins['x'].setValue(100)
    w.composite_opacity.setValue(0); w.apply_composite()
    clip = w.selected_clip()
    assert clip.composite == ClipComposite(False, 0)
    assert clip.transform == before.transform and clip.timing == before.timing
    assert (clip.gain_db, clip.muted, clip.fade_in, clip.fade_out) == (before.gain_db, before.muted, before.fade_in, before.fade_out)
    assert w.transform_spins['scale'].value() == 1 and w.transform_spins['x'].value() == 0
    w.composite_opacity.setValue(77.7); w.composite_transparent_outside_transform.setChecked(True)
    w.transform_spins['scale'].setValue(.5); w.apply_transform()
    assert w.selected_clip().transform == ClipTransform(scale=.5)
    assert w.selected_clip().composite == ClipComposite(False, 0)
    assert w.composite_opacity.value() == 0 and not w.composite_transparent_outside_transform.isChecked()
    w.reset_transform(); assert w.selected_clip().composite == ClipComposite(False, 0)


def test_draft_reset_and_identical_apply_never_start_transaction(window, monkeypatch):
    w = window; before = deepcopy(w.project); revision = w.revision
    monkeypatch.setattr(w, 'edit', lambda *_: pytest.fail('No-op must not create an edit'))
    w.apply_composite(); w.reset_composite()
    w.composite_opacity.setValue(45); w.composite_transparent_outside_transform.setChecked(True)
    assert w.reset_composite_button.isEnabled()
    w.reset_composite()
    assert w.composite_opacity.value() == 100 and not w.composite_transparent_outside_transform.isChecked()
    assert not w.apply_composite_button.isEnabled() and not w.reset_composite_button.isEnabled()
    assert w.project == before and w.revision == revision


@pytest.mark.parametrize('selection', ['empty', 'missing', 'audio', 'multiple', 'caption', 'overlay', 'effect'])
def test_invalid_target_disables_controls_and_direct_calls_are_safe(window, selection):
    w = window; w.project.clips += [Clip('other', 'm', 1, 20, 0, 31), Clip('audio', 'm', 2, 10, 0, 31)]
    w.refresh(); w.select_clip('c')
    w.composite_opacity.setValue(20)
    if selection == 'empty': w.selection = ''
    elif selection == 'missing': w.selection = 'missing'
    elif selection == 'audio': w.select_clip('audio')
    elif selection == 'multiple': w.set_selected_clip_ids(('c', 'other'))
    else: w.active_selection_type = selection
    w.refresh_inspector(); before = deepcopy(w.project); revision = w.revision
    assert not w.transform_group.isEnabled()
    assert not w.apply_composite_button.isEnabled() and not w.reset_composite_button.isEnabled()
    assert not w.apply_transform_button.isEnabled() and not w.reset_transform_button.isEnabled()
    w.apply_composite(); w.reset_composite(); w.apply_transform(); w.reset_transform()
    assert w.project == before and w.revision == revision


@pytest.mark.parametrize('change', ['selection', 'selection_back', 'editor', 'project', 'revision', 'undo'])
@pytest.mark.parametrize('action', ['apply_composite', 'reset_composite', 'apply_transform', 'reset_transform'])
def test_stale_draft_never_applies_to_replaced_target(window, change, action):
    w = window
    w.project.clips.append(Clip('other', 'm', 1, 20, 0, 31))
    w.editor.update_clip('c', composite=ClipComposite(True, .333333333333), transform=ClipTransform(scale=.5))
    w.changed(); w.composite_opacity.setValue(80); w.transform_spins['x'].setValue(60)
    if change == 'selection': w.selection = 'other'
    elif change == 'selection_back': w.selection = 'other'; w.selection = 'c'
    elif change == 'editor': w.editor = Editor(deepcopy(w.project))
    elif change == 'project': w.editor.project = deepcopy(w.project)
    elif change == 'revision': w.editor.update_clip('c', gain_db=-3)
    elif change == 'undo': w.editor.undo()
    before = deepcopy(w.project); history = deepcopy(w.editor._undo); revision = w.editor.revision
    getattr(w, action)()
    assert w.project == before and w.editor._undo == history and w.editor.revision == revision
    assert not w.apply_composite_button.isEnabled() and not w.apply_transform_button.isEnabled()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_bilingual_workspace_narrow_controls_keep_draft_and_are_visible(window, app, locale, mode):
    w = window; set_locale(locale); w.set_workspace_mode(mode)
    w.composite_transparent_outside_transform.setChecked(True); w.composite_opacity.setValue(34.5)
    before = deepcopy(w.project); revision = w.revision
    w.resize(1100, 800); w.show(); app.processEvents()
    for next_mode in ('simple', 'professional', mode):
        w.set_workspace_mode(next_mode); app.processEvents()
        assert w.composite_opacity.value() == 34.5 and w.composite_transparent_outside_transform.isChecked()
        assert w.project == before and w.revision == revision
    w.inspector_tabs.setCurrentIndex(0)
    w.inspector.ensureWidgetVisible(w.composite_opacity); app.processEvents()
    assert w.composite_opacity.isVisible() and w.composite_opacity.width() >= 55
    assert w.composite_note.wordWrap() and w.composite_apply_note.wordWrap()
    if locale == 'zh-CN':
        assert w.apply_composite_button.text() == '应用画面合成'
        assert '音频' in w.composite_note.text() and '素材自身' in w.composite_note.text()
    else:
        assert w.apply_composite_button.text() == 'Apply compositing'
        assert 'source alpha' in w.composite_note.text() and 'audio stays unchanged' in w.composite_note.text()
    w.inspector.ensureWidgetVisible(w.apply_composite_button); app.processEvents()
    assert w.apply_composite_button.isVisible() and w.apply_composite_button.width() >= 80
    output = os.environ.get('CUTCAT_PIP_SCREENSHOTS')
    if output:
        path = Path(output); path.mkdir(parents=True, exist_ok=True)
        assert w.grab().save(str(path / f'composite-{locale}-{mode}.png'))


def test_language_changes_do_not_mutate_exact_values_or_staged_values(window):
    assert CLIP_COMPOSITE_ERROR in ERROR_ZH_CN
    w = window; original = ClipComposite(True, .987654321098765)
    w.editor.update_clip('c', composite=original); w.changed()
    w.composite_opacity.setValue(56.7); before = deepcopy(w.project); revision = w.revision
    for locale in ('zh-CN', 'en', 'zh-CN', 'en'):
        set_locale(locale)
        assert w.composite_opacity.value() == 56.7 and w.project == before and w.revision == revision
        for source, translated in ERROR_ZH_CN.items():
            assert str(tr_error(ValueError(source))) == (translated if locale == 'zh-CN' else source)



def test_relink_preserves_exact_composite_and_discards_stale_drafts(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    w = window; exact = ClipComposite(True, .123456789012345)
    w.editor.update_clip('c', composite=exact, transform=ClipTransform(scale=.4)); w.changed()
    original_clips = deepcopy(w.project.clips)
    old = Path(w.project.media[0].path); moved = tmp_path / 'relocated.mp4'; old.rename(moved)
    replacement = deepcopy(w.project.media[0]); replacement.path = str(moved)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(moved), ''))
    monkeypatch.setattr('lumen.ui.probe', lambda *_args, **_kwargs: deepcopy(replacement))
    w.composite_opacity.setValue(90)
    w.relink_dialog()
    assert w.project.clips == original_clips and w.project.media[0].path == str(moved)
    assert not w.editor.can_undo and not w.test_warnings
    w.apply_composite()
    assert w.project.clips == original_clips and not w.editor.can_undo
    w.select_clip('c')
    assert w.composite_opacity.value() == 12.3 and w.selected_clip().composite == exact

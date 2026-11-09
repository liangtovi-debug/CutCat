"""Multi-clip selection never leaks its primary anchor into single-clip tools."""
from copy import deepcopy
from types import SimpleNamespace
import threading

import pytest
from test_ui import app, window, populate, configure_mock_asr, pump_until
from test_effects_ui import add_overlay

from lumen.model import ClipEffect
from lumen.ui_color_picker import ColorPickerDialog
from lumen.ui_white_balance import WhiteBalanceDialog
from lumen.ui_qualifier import QualifierDialog
from lumen.ui_window_animation import WindowAnimationDialog
from lumen.ui_frame_grade_preview import FrameGradePreviewDialog


def two_clips(window, tmp_path):
    first = populate(window, tmp_path)
    second = window.editor.add_clip(first.media_id, 1, start=15)
    window.changed(); window.select_clip(first.id)
    return first, second


def select_both(window, first, second):
    window.select_clip(first.id)
    window.toggle_clip_selection(second.id)
    assert window.selected_clip_ids == (first.id, second.id)
    assert window.selection == first.id and window.selected_clip() is None


def test_multiple_clips_disable_single_target_tools_and_keep_delete(window, tmp_path, monkeypatch):
    w = window; first, second = two_clips(w, tmp_path)
    select_both(w, first, second)
    w.seek_frame(30)
    assert not w.inspector.isEnabled() and not w.effects_controls.isEnabled()
    assert not w.white_balance_group.isEnabled() and not w.window_animation_button.isEnabled()
    assert not w.ripple_delete_button.isEnabled() and not w.asr_button.isEnabled()
    assert w.shortcuts.enabled('edit.delete') and not w.shortcuts.enabled('edit.split')
    monkeypatch.setattr(w, 'start_job', lambda *a, **kw: pytest.fail('No implicit primary-clip worker'))
    for name in ('open_color_picker', 'open_white_balance', 'open_qualifier', 'open_window_animation'):
        assert getattr(w, name)() is None
    configure_mock_asr(w, tmp_path)
    w.transcribe_selected()
    assert not w.jobs and len(w.test_warnings) == 1
    before = deepcopy(w.project); revision = w.revision; undo = len(w.editor._undo)
    for name in ('apply_timing', 'apply_slip', 'apply_roll', 'apply_speed', 'apply_grade',
                 'apply_audio', 'apply_transform', 'reset_transform', 'apply_restoration',
                 'reset_restoration', 'apply_stabilization', 'reset_stabilization',
                 'reset_grade', 'reset_current_curve', 'add_clip_effect', 'apply_clip_effect',
                 'delete_clip_effect', 'inspect_speech', 'split_selected', 'ripple_delete_selected'):
        getattr(w, name)()
    w.move_clip_effect(1)
    assert w.project == before and w.revision == revision and len(w.editor._undo) == undo
    w.asr_source.setCurrentIndex(1)
    assert w.asr_button.isEnabled()  # An explicit media-bin target is independent.
    w.asr_source.setCurrentIndex(0)
    assert not w.asr_button.isEnabled()


@pytest.mark.parametrize('kind', ['caption', 'overlay'])
def test_nonclip_selection_clears_multi_clip_targets(window, tmp_path, kind):
    w = window; first, second = two_clips(w, tmp_path)
    if kind == 'caption':
        item = w.editor.add_caption(0, 30, 'Saved caption'); w.changed()
    else:
        item = add_overlay(w, tmp_path)
    select_both(w, first, second)
    generation = w.selection_generation; before = deepcopy(w.project); revision = w.revision
    getattr(w, 'select_' + kind)(item.id)
    assert w.active_selection_type == kind and w.selected_clip_ids == ()
    assert w.selection == '' and w.selected_clip() is None and w.effect_clip() is None
    assert w.timeline.selected_clip_ids == () and w.timeline.selection == ''
    assert w.selection_generation > generation
    assert not w.inspector.isEnabled() and not w.asr_button.isEnabled()
    assert w.project == before and w.revision == revision
    getattr(w, 'delete_' + kind)()
    assert {c.id for c in w.project.clips} == {first.id, second.id}


def test_effect_has_explicit_anchor_but_never_uses_multi_clip_anchor(window, tmp_path):
    w = window; first, second = two_clips(w, tmp_path)
    w.editor.add_effect(first.id, ClipEffect('blur', 'blur', 3.)); w.changed()
    select_both(w, first, second)
    before = deepcopy(w.project); revision = w.revision
    w.select_clip_effect('blur'); w.delete_clip_effect(); w.add_clip_effect()
    assert w.selected_effect() is None and w.effect_clip() is None
    assert w.project == before and w.revision == revision
    w.select_clip(first.id); generation = w.selection_generation
    w.select_clip_effect('blur')
    assert w.active_selection_type == 'effect' and w.selected_clip_ids == ()
    assert w.selected_clip() is None and w.effect_clip().id == first.id
    assert w.selection == first.id and w.selection_generation > generation
    assert w.timeline.selected_clip_ids == ()
    assert not w.inspector.isEnabled() and not w.asr_button.isEnabled()
    assert w.shortcuts.enabled('edit.delete') and not w.shortcuts.enabled('edit.split')
    w.delete_clip_effect()
    assert len(w.project.clips) == 2 and not w.effect_clip().effects
    w.delete_clip_effect()
    assert len(w.project.clips) == 2


@pytest.mark.parametrize('dialog_type', [ColorPickerDialog, WhiteBalanceDialog,
                                      QualifierDialog, WindowAnimationDialog])
@pytest.mark.parametrize('return_to_original', [False, True])
def test_staged_single_clip_dialog_rejects_multi_selection_even_after_return(
        window, tmp_path, dialog_type, return_to_original, monkeypatch):
    w = window; first, second = two_clips(w, tmp_path)
    d = dialog_type(w); d.show()
    d.snapshot = SimpleNamespace(is_current=lambda *a: True)
    d.hue = .2; d.gains = [1.1, 1., .9]
    assert d.current()
    context = d.context; generation = w.selection_generation
    w.toggle_clip_selection(second.id)
    if return_to_original:
        w.select_clip(first.id)
    assert w.selection == first.id and w.selection_generation > generation
    assert d.context == context and not d.context_current()
    d.snapshot = None
    d.accept_snapshot(object())  # Stale workers are rejected before inspecting their pixels.
    assert d.snapshot is None
    monkeypatch.setattr(w, 'apply_color_field', lambda *a: pytest.fail('Stale qualifier committed'))
    monkeypatch.setattr(w, 'apply_selective_curve', lambda *a: pytest.fail('Stale hue committed'))
    monkeypatch.setattr(w, 'commit_white_balance', lambda *a: pytest.fail('Stale neutral committed'))
    before = deepcopy(w.project); revision = w.revision; undo = len(w.editor._undo)
    if isinstance(d, (QualifierDialog, WindowAnimationDialog)):
        d.apply_settings()
    else:
        d.apply_sample()
    assert not d.apply_button.isEnabled()
    assert w.project == before and w.revision == revision and len(w.editor._undo) == undo
    d.reject()


@pytest.mark.parametrize('kind', ['caption', 'overlay', 'effect'])
def test_switching_selection_kind_invalidates_single_clip_dialog(window, tmp_path, kind):
    w = window; first, second = two_clips(w, tmp_path)
    if kind == 'caption':
        item = w.editor.add_caption(0, 30, 'Saved caption'); w.changed()
    elif kind == 'overlay':
        item = add_overlay(w, tmp_path)
    else:
        item = w.editor.add_effect(first.id, ClipEffect('blur', 'blur', 3.)); w.changed()
    w.select_clip(first.id)
    d = ColorPickerDialog(w); d.show()
    assert d.context_current()
    getattr(w, 'select_clip_effect' if kind == 'effect' else 'select_' + kind)(item.id)
    assert not d.context_current()
    w.select_clip(first.id)
    assert not d.context_current()  # Returning before the timer runs cannot revive it.
    d.reject()


def test_frame_preview_blocks_anchor_capture(window, tmp_path, monkeypatch):
    import lumen.ui_frame_grade_preview as preview
    w = window; first, second = two_clips(w, tmp_path)
    d = FrameGradePreviewDialog(w); d.show()
    w.toggle_clip_selection(second.id)
    monkeypatch.setattr(preview, '_selected', lambda *a: pytest.fail('Multi-selection anchor read'))
    assert d._change_token()[-1] == 'no_single_clip'
    monkeypatch.setattr(w, 'start_job', lambda *a, **kw: pytest.fail('Multi-selection anchor decoded'))
    d.refresh_frame()
    assert d.state == 'unsupported' and d.snapshot is None
    d.reject()


@pytest.mark.parametrize('return_to_original', [False, True])
def test_frame_preview_rejects_late_result_after_selection_change(window, tmp_path, return_to_original):
    w = window; first, second = two_clips(w, tmp_path)
    d = FrameGradePreviewDialog(w); d.show(); d.context = d._context()
    context = d.context; generation = d._generation
    assert d.context_current(context)
    w.toggle_clip_selection(second.id)
    if return_to_original:
        w.select_clip(first.id)
    assert not d.context_current(context)
    assert d._generation == generation  # No polling/invalidation timer has run yet.
    d._accept_result(generation, context, (SimpleNamespace(is_current=lambda *a: True),
                                        (1, 1, b'\x80\x80\x80'), None))
    assert d.snapshot is None and d.canvas.image.isNull()
    d.reject()


@pytest.mark.parametrize('return_to_original', [False, True])
def test_transcription_result_rejects_changed_clip_selection_without_project_edit(
        window, app, tmp_path, monkeypatch, return_to_original):
    import lumen.asr as asr
    w = window; first, second = two_clips(w, tmp_path); configure_mock_asr(w, tmp_path)
    gate = threading.Event()
    def transcribe(*a, **kw):
        assert gate.wait(5)
        return [asr.TranscriptSegment(0, 1, 'Do not add a stale caption', 'en')]
    monkeypatch.setattr(asr, 'transcribe', transcribe)
    before = deepcopy(w.project); revision = w.revision; undo = len(w.editor._undo)
    w.transcribe_selected()
    w.toggle_clip_selection(second.id)
    if return_to_original:
        w.select_clip(first.id)
    gate.set(); pump_until(app, lambda: not w.jobs)
    assert w.project == before and w.revision == revision and len(w.editor._undo) == undo
    assert not w.project.captions and len(w.test_warnings) == 1


def test_clip_selection_change_invalidates_controlled_ai_review(window, tmp_path):
    w = window; first, second = two_clips(w, tmp_path)
    w.controlled_proposal = object(); w.ai_review_proposal = object(); w.ai_review_request = object()
    generation = w.ai_generation
    w.toggle_clip_selection(second.id)
    assert w.ai_generation > generation
    assert w.controlled_proposal is None and w.ai_review_proposal is None and w.ai_review_request is None
    assert not w.controlled_apply_button.isEnabled() and not w.ai_apply_button.isEnabled()

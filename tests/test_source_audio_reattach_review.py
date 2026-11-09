"""Independent adversarial review of strict two-record audio reattachment."""
from copy import deepcopy
from dataclasses import fields, replace
from fractions import Fraction
import json
from pathlib import Path
from threading import Thread

import pytest

from lumen.audio_gain import AudioGainKeyframe
from lumen.model import (AudioMixAnchor, Caption, Clip, ClipGroup, Editor, Media,
                         Overlay, Project, VideoTransition, MAX_CAPTION_FRAME)
from lumen.source_audio_reattach import (plan_source_audio_reattach,
    verify_source_audio_reattach, apply_source_audio_reattach)
from lumen.timing import ClipTimeMap
from test_track_edit_locks_model import snapshot


def editor(tmp_path):
    file = tmp_path / 'review.mov'; file.write_bytes(b'review-shared-source' * 100)
    return Editor(Project(media=[Media('m', str(file), 'Review', 60, 64, 48, True, True)],
        clips=[Clip('video', 'm', 1, 30, 45, 12, muted=True, source_audio_enabled=False),
               Clip('audio', 'm', 3, 30, 45, 12, gain_db=-13, muted=False,
                    fade_in=2, fade_out=4, audio_mix_anchor=AudioMixAnchor(0, 99, 'historical-deleted')),
               Clip('peer', 'm', 0, 10, 8, 5)],
        captions=[Caption('c', 4, 8, 'fixed')],
        overlays=[Overlay('o', '/unchanged.png', 'a'*64, 3, 5)]))


def verified(editor):
    plan = plan_source_audio_reattach(editor, 'video', 'audio')
    preview = verify_source_audio_reattach(plan)
    return plan, verify_source_audio_reattach(plan, preview)


def test_all_current_video_fields_are_exact_except_six_approved_audio_fields(tmp_path):
    # Retain the historical test ID; schema41 adds the seventh approved field.
    e = editor(tmp_path)
    e._clip('video').audio_gain_keyframes = [AudioGainKeyframe(-7, -12, 'hold'), AudioGainKeyframe(27000, -3)]
    e._clip('audio').audio_gain_keyframes = [AudioGainKeyframe(-100, 2), AudioGainKeyframe(500, -20, 'hold'), AudioGainKeyframe(1000000, -5)]
    before = deepcopy(e.project); original_v = e._clip('video')
    original_audio_keys = e._clip('audio').audio_gain_keyframes
    e._commit_source_audio_reattach('video', 'audio')
    expected_changes = {'source_audio_enabled', 'audio_mix_anchor', 'gain_db', 'muted', 'fade_in', 'fade_out', 'audio_gain_keyframes'}
    for field in fields(Clip):
        if field.name not in expected_changes:
            assert getattr(e._clip('video'), field.name) == getattr(before.clips[0], field.name)
    for name in ('gain_db', 'muted', 'fade_in', 'fade_out', 'audio_gain_keyframes'):
        assert getattr(e._clip('video'), name) == getattr(before.clips[1], name)
    assert e._clip('video').audio_gain_keyframes != before.clips[0].audio_gain_keyframes
    assert e._clip('video').audio_gain_keyframes is not original_audio_keys
    assert e._clip('video') is original_v
    assert e.project.media == before.media and e.project.captions == before.captions
    assert e.project.overlays == before.overlays and e._clip('peer') == before.clips[2]
    assert e._clip('video').audio_mix_anchor == AudioMixAnchor(0, 99, 'historical-deleted')
    assert set(c.id for c in e.project.clips) == {'video', 'peer'}


@pytest.mark.parametrize('field,value', [
    ('source_audio_enabled', True), ('track', 2), ('start', 31), ('duration', 11),
    ('source_in', 44), ('media_id', 'unavailable')])
def test_direct_narrow_primitive_rechecks_roles_and_timing(tmp_path, field, value):
    e = editor(tmp_path); setattr(e._clip('video'), field, value); before = snapshot(e)
    with pytest.raises(ValueError): e._commit_source_audio_reattach('video', 'audio')
    assert snapshot(e) == before


@pytest.mark.parametrize('selected', ['video', 'audio'])
def test_direct_primitive_rechecks_explicit_track_locks_and_group_membership(tmp_path, selected):
    e = editor(tmp_path); c = e._clip(selected)
    e.set_track_edit_lock(c.track, True); before = snapshot(e)
    with pytest.raises(ValueError, match='locked'): e._commit_source_audio_reattach('video', 'audio')
    assert snapshot(e) == before
    e.set_track_edit_lock(c.track, False); e.group_clips([selected, 'peer']); before = snapshot(e)
    with pytest.raises(ValueError, match='Ungroup'): e._commit_source_audio_reattach('video', 'audio')
    assert snapshot(e) == before


def test_valid_video_transition_endpoint_rejected_without_removing_transition(tmp_path):
    e = editor(tmp_path); e.project.clips.append(Clip('right', 'm', 1, 42, 70, 12))
    e.project.video_transitions = [VideoTransition('t', 'video', 'right', 4)]
    e.project.validate(); before = snapshot(e)
    with pytest.raises(ValueError, match='transitions'): plan_source_audio_reattach(e, 'video', 'audio')
    with pytest.raises(ValueError, match='transitions'): e._commit_source_audio_reattach('video', 'audio')
    assert snapshot(e) == before


@pytest.mark.parametrize('muted', [False, True])
def test_enabled_video_rejects_even_if_its_user_mute_is_true(tmp_path, muted):
    e = editor(tmp_path); e._clip('video').source_audio_enabled = True; e._clip('video').muted = muted
    before = snapshot(e)
    with pytest.raises(ValueError, match='already emits'): plan_source_audio_reattach(e, 'video', 'audio')
    assert snapshot(e) == before


def test_complete_rational_endpoints_not_just_same_rounded_duration(tmp_path):
    e = editor(tmp_path)
    a, v = e._clip('audio'), e._clip('video')
    v.time_map = ClipTimeMap(Fraction(45), Fraction(225, 4), Fraction(1))
    a.time_map = ClipTimeMap(Fraction(45), Fraction(113, 2), Fraction(1))
    assert v.duration == a.duration == 12
    e.project.validate(); before = snapshot(e)
    with pytest.raises(ValueError, match='complete rational'): plan_source_audio_reattach(e, v.id, a.id)
    assert snapshot(e) == before


def test_valid_historical_max_bound_and_different_current_starts_are_allowed(tmp_path):
    from lumen.audio_components import audio_mix_order_key
    e = editor(tmp_path); anchor = AudioMixAnchor(3, MAX_CAPTION_FRAME, 'unreferenced')
    e._clip('audio').audio_mix_anchor = anchor; e._clip('peer').audio_mix_anchor = anchor
    before = [c.id for c in sorted(e.project.clips, key=audio_mix_order_key) if c.source_audio_enabled]
    e._commit_source_audio_reattach('video', 'audio')
    after = [c.id for c in sorted(e.project.clips, key=audio_mix_order_key) if c.source_audio_enabled]
    assert after == ['video' if item == 'audio' else item for item in before]
    assert e._clip('video').audio_mix_anchor == anchor


@pytest.mark.parametrize('silent_state', ['audible', 'muted', 'structurally_suppressed'])
def test_ambiguous_other_slot_rejected_without_future_unmute_loophole(tmp_path, silent_state):
    e = editor(tmp_path); peer = e._clip('peer'); peer.start = 30
    peer.audio_mix_anchor = e._clip('audio').audio_mix_anchor
    if silent_state == 'muted': peer.muted = True
    if silent_state == 'structurally_suppressed': peer.source_audio_enabled = False
    before = snapshot(e)
    with pytest.raises(ValueError, match='ambiguous'): e._commit_source_audio_reattach('video', 'audio')
    assert snapshot(e) == before


def test_transaction_refuses_nested_generic_callback_attempt(tmp_path):
    e = editor(tmp_path); before = snapshot(e)
    with pytest.raises(ValueError, match='Nested'):
        e._commit(lambda: e._commit_source_audio_reattach('video', 'audio'))
    assert snapshot(e) == before


def test_generic_transaction_cannot_delete_audio_and_change_structural_state(tmp_path):
    e = editor(tmp_path); before = snapshot(e)
    def change():
        c = e._clip('video'); c.source_audio_enabled = True
        c.audio_mix_anchor = e._clip('audio').audio_mix_anchor
        e.project.clips = [c for c in e.project.clips if c.id != 'audio']
    with pytest.raises(ValueError, match='structural'): e._commit(change)
    assert snapshot(e) == before


def test_planning_and_direct_commit_reject_non_owner_threads(tmp_path):
    e = editor(tmp_path); before = snapshot(e); errors = []
    def worker():
        for action in (lambda: plan_source_audio_reattach(e, 'video', 'audio'),
                       lambda: e._commit_source_audio_reattach('video', 'audio')):
            try: action()
            except ValueError as error: errors.append(str(error))
    t = Thread(target=worker); t.start(); t.join(3)
    assert not t.is_alive() and len(errors) == 2
    assert all('owning thread' in item for item in errors) and snapshot(e) == before


@pytest.mark.parametrize('tampering', ['wrong_phase', 'foreign_plan', 'edited_metadata', 'different_pair', 'new_session'])
def test_worker_receipt_bound_to_exact_plan_metadata_and_session(tmp_path, tampering):
    e = editor(tmp_path); plan, receipt = verified(e)
    if tampering == 'wrong_phase': receipt = replace(receipt, phase='preview')
    elif tampering == 'foreign_plan': receipt = replace(receipt, plan=replace(plan, token='different'))
    elif tampering == 'edited_metadata': e.project.media[0].audio_offset = .1
    elif tampering == 'different_pair': plan = replace(plan, audio_clip_id='peer')
    else: e.project = deepcopy(e.project)
    before = snapshot(e)
    with pytest.raises(ValueError): apply_source_audio_reattach(e, plan, receipt)
    assert snapshot(e) == before


@pytest.mark.parametrize('tampering', ['extra_clip', 'one_clip', 'extra_media', 'annotation', 'enabled_video', 'reversed_roles'])
def test_worker_rejects_tampered_snapshots_before_file_read(tmp_path, monkeypatch, tampering):
    e = editor(tmp_path); plan = plan_source_audio_reattach(e, 'video', 'audio')
    value = json.loads(plan.source_snapshot)
    if tampering == 'extra_clip':
        c = deepcopy(value['clips'][0]); c['id'] = 'extra'; c['track'] = 0; value['clips'].append(c)
    elif tampering == 'one_clip': value['clips'].pop()
    elif tampering == 'extra_media':
        m = deepcopy(value['media'][0]); m['id'] = 'extra'; value['media'].append(m)
    elif tampering == 'annotation': value['captions'] = e.project.to_dict()['captions']
    elif tampering == 'enabled_video': value['clips'][0]['source_audio_enabled'] = True
    else: plan = replace(plan, video_clip_id='audio', audio_clip_id='video')
    plan = replace(plan, source_snapshot=json.dumps(value))
    def forbidden(*_args, **_kwargs): raise AssertionError('invalid snapshot reached file read')
    monkeypatch.setattr(Path, 'open', forbidden)
    with pytest.raises(ValueError): verify_source_audio_reattach(plan)


def test_reattached_enabled_video_copy_resets_only_new_contribution_slot(tmp_path):
    from lumen.clip_copy_paste import (prepare_clip_copy, verify_clip_copy, accept_clip_copy,
        plan_clip_paste, verify_clip_paste, apply_clip_paste)
    e = editor(tmp_path); e._commit_source_audio_reattach('video', 'audio')
    original = deepcopy(e._clip('video'))
    request = prepare_clip_copy(e, ('video',))
    copied = accept_clip_copy(e, request, verify_clip_copy(request))
    plan = plan_clip_paste(e, copied, 100)
    clone, = apply_clip_paste(e, copied, plan, verify_clip_paste(plan, copied))
    assert e._clip('video') == original
    expected = deepcopy(original); expected.id = clone.id; expected.start = 100
    expected.audio_mix_anchor = None
    assert clone == expected and clone.source_audio_enabled


@pytest.mark.parametrize('operation', ['split', 'insert_remnants', 'overwrite_remnants', 'overwrite_entire'])
def test_reattached_enabled_video_still_obeys_conservative_source_cut_policies(tmp_path, operation):
    e = editor(tmp_path); e._commit_source_audio_reattach('video', 'audio')
    before = snapshot(e)
    with pytest.raises(ValueError, match='separated'):
        if operation == 'split': e.split_clip('video', 35)
        elif operation == 'insert_remnants': e.plan_source_insert('m', 1, 35, 0, 4)
        elif operation == 'overwrite_entire': e.plan_source_overwrite('m', 1, 30, 0, 12)
        else: e.plan_source_overwrite('m', 1, 35, 0, 4)
    assert snapshot(e) == before


def test_whole_source_insert_translation_keeps_reattached_anchor_and_audio_settings(tmp_path):
    e = editor(tmp_path); e._commit_source_audio_reattach('video', 'audio')
    original = deepcopy(e._clip('video'))
    plan = e.plan_source_insert('m', 1, 0, 0, 6)
    assert not plan.remnants
    e.apply_source_insert(plan)
    original.start += 6
    assert e._clip('video') == original


# Independent offscreen lifecycle probes complement the pure model review.
from test_source_audio_reattach_ui import (app, window, pair_window, dialog, preview,
    select_pair, held, wait_started)
from test_clip_copy_paste_ui import settle, state
from test_shortcuts_dispatch import activate, press, repeat, CTRL
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLineEdit


@pytest.mark.parametrize('phase', ['preview', 'apply'])
def test_late_cancelled_worker_cannot_populate_new_dialog(pair_window, app, monkeypatch, phase):
    w = pair_window; old = dialog(w, app)
    if phase == 'apply': preview(w, app, old)
    started, release = held(monkeypatch); getattr(old, phase)(); wait_started(app, started)
    before = state(w)
    try:
        old.reject()
        # Work remains busy, so there is no unsafe simultaneous fresh preview.
        assert w.open_source_audio_reattach() is None
    finally:
        release.set(); settle(w, app)
    fresh = dialog(w, app)
    assert fresh is not old and fresh.preview_receipt is None
    assert not fresh.apply_button.isEnabled() and old._closed_flag
    assert state(w) == before


def test_actual_recovery_replacement_closes_staged_dialog_and_has_no_old_history(pair_window, app, tmp_path):
    from test_recovery_ui import orphan
    w = pair_window; old = dialog(w, app); preview(w, app, old)
    entry, recovered = orphan(tmp_path / 'recovery', 'Explicit restored copy')
    old_session = w.editor.session_id; w.dirty = False
    assert w.restore_recovery(entry)
    app.processEvents()
    assert old._closed_flag and w.source_audio_reattach_dialog is None
    assert w.editor.session_id != old_session and w.project == recovered
    assert not w.editor.can_undo and not w.selected_clip_ids
    old.apply(); assert w.project == recovered


def test_explicit_reattach_keyboard_binding_obeys_repeat_text_focus_and_modal_guards(pair_window, app):
    w = pair_window; w.shortcuts.set_bindings('edit.reattach_audio', ['Ctrl+Alt+J'])
    activate(app, w, w.timeline)
    modifiers = CTRL | Qt.KeyboardModifier.AltModifier
    before = state(w)
    repeat(app, w.timeline, Qt.Key.Key_J, modifiers)
    assert w.source_audio_reattach_dialog is None
    text = QLineEdit(w.centralWidget()); text.setGeometry(10, 10, 150, 30)
    text.show(); activate(app, w, text)
    press(app, text, Qt.Key.Key_J, modifiers)
    assert w.source_audio_reattach_dialog is None
    modal = QDialog(w); modal.setModal(True); modal.show(); app.processEvents()
    press(app, modal, Qt.Key.Key_J, modifiers)
    assert w.source_audio_reattach_dialog is None
    modal.reject(); app.processEvents(); activate(app, w, w.timeline)
    press(app, w.timeline, Qt.Key.Key_J, modifiers)
    assert w.source_audio_reattach_dialog is not None
    assert state(w) == before

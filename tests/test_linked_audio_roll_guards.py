"""Audio-only linked roll: conservative qualification, fences and stale plans.

No media files or renderer are used. The externally loaded v2 fixture below is
built from explicit old-map algebra, not an audio-roll production candidate.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from threading import Thread
from unittest.mock import Mock

import pytest

from lumen.audio_gain import AudioGainKeyframe, MAX_AUDIO_GAIN_SAMPLE
from lumen.clip_motion import ClipMotionKeyframe, MAX_MOTION_FRAME
from lumen.linked_av_audio_roll import (
    apply_linked_audio_roll, linked_audio_roll_candidate,
    plan_linked_audio_align, plan_linked_audio_roll)
from lumen.model import (AV_LINK_AFFINE_ALIGNMENT, AudioMixAnchor, Clip,
                         MAX_CAPTION_FRAME, Project)
from lumen.qualifier import MAX_WINDOW_FRAME, WindowKeyframe
from lumen.timing import ClipTimeMap, MAX_SOURCE_NUMERATOR
from test_linked_av_model import state
from test_linked_av_roll_model import IDS, editor_for, order_fixture


def affine_editor(cut=28):
    editor = editor_for()
    delta = cut - 27
    left, right = editor._clip('la'), editor._clip('ra')
    left.time_map = ClipTimeMap(left.source_in, left.source_out + delta * left.speed, left.speed)
    left.duration += delta
    right.time_map = ClipTimeMap(right.source_in + delta * right.speed, right.source_out, right.speed)
    right.source_in = right.time_map.source_in
    right.start, right.duration = cut, right.duration - delta
    editor.project.av_links = [replace(link, alignment=AV_LINK_AFFINE_ALIGNMENT)
                               for link in editor.project.av_links]
    editor.project.validate()
    return editor


def reject_call(editor, action, match=None):
    before = state(editor)
    project, records, links = editor.project, tuple(editor.project.clips), tuple(editor.project.av_links)
    with pytest.raises(ValueError, match=match):
        action()
    assert state(editor) == before
    assert editor.project is project
    assert len(editor.project.clips) == len(records)
    assert all(old is current for old, current in zip(records, editor.project.clips))
    assert all(old is current for old, current in zip(links, editor.project.av_links))


def rejected(editor, cut, ids=IDS, match=None):
    for action in (lambda: editor.preview_roll_linked_audio(ids, cut),
                   lambda: editor.roll_linked_audio(ids, cut),
                   lambda: plan_linked_audio_roll(editor, ids, cut)):
        reject_call(editor, action, match)


@pytest.mark.parametrize('ids', [(), ('lv',), ('lv', 'la'), ('lv', 'la', 'rv'),
    (*IDS, 'other'), (*IDS, 'lv'), ('lv', 'la', 'rv', 'other'),
    ('lv', 'la', 'rv', 'missing'), 'lv', None, (True, 'la', 'rv', 'ra')])
def test_exact_complete_selection_rejects_before_any_edit(ids):
    rejected(editor_for(), 28, ids)


@pytest.mark.parametrize('cut', [True, False, 28.0, Fraction(28), '28', None,
    float('nan'), float('inf'), -1, 2**53, -2**53])
def test_absolute_audio_cut_requires_bounded_integer(cut):
    rejected(editor_for(), cut)


@pytest.mark.parametrize('cut', [0, 7, 47, 48, 1000])
def test_audio_cut_must_leave_both_roles_positive(cut):
    rejected(editor_for(), cut)


@pytest.mark.parametrize('kind', ['video_gap', 'audio_gap', 'left_outer', 'right_outer',
    'video_track', 'audio_track', 'missing_link', 'missing_role', 'enabled_video',
    'suppressed_audio', 'wrong_media', 'wrong_intercept', 'collision', 'audio_motion'])
def test_invalid_or_ineligible_loaded_v2_is_never_repaired(kind):
    editor = affine_editor()
    if kind in ('video_gap', 'audio_gap', 'left_outer'):
        clip = editor._clip({'video_gap': 'rv', 'audio_gap': 'ra', 'left_outer': 'la'}[kind])
        old = clip.timing
        clip.start += 1
        clip.source_in = old.source_in + old.speed
        clip.time_map = ClipTimeMap(clip.source_in, old.source_out, old.speed)
        clip.duration -= 1
    elif kind == 'right_outer':
        clip = editor._clip('ra'); old = clip.timing
        clip.time_map = ClipTimeMap(old.source_in, old.source_out-old.speed, old.speed)
        clip.duration -= 1
    elif kind == 'video_track': editor._clip('rv').track = 1
    elif kind == 'audio_track': editor._clip('ra').track = 3
    elif kind == 'missing_link': editor.project.av_links.pop()
    elif kind == 'missing_role': editor.project.clips.remove(editor._clip('ra'))
    elif kind == 'enabled_video': editor._clip('lv').source_audio_enabled = True
    elif kind == 'suppressed_audio': editor._clip('ra').source_audio_enabled = False
    elif kind == 'wrong_media': editor._clip('ra').media_id = 'm'
    elif kind == 'wrong_intercept':
        clip = editor._clip('ra'); old = clip.timing
        clip.source_in = old.source_in + 1
        clip.time_map = ClipTimeMap(old.source_in+1, old.source_out+1, old.speed)
    elif kind == 'collision': editor.project.clips.append(Clip('collision', 'm', 2, 27, 0, 2))
    else: editor._clip('ra').motion_keyframes = [ClipMotionKeyframe(-5)]
    rejected(editor, 29)


@pytest.mark.parametrize('mode', ['v1_promotion', 'v2_continuation', 'v2_align', 'metadata_align'])
@pytest.mark.parametrize('role', IDS)
def test_every_involved_track_lock_including_unchanged_picture_rejects(mode, role):
    editor = editor_for() if mode == 'v1_promotion' else affine_editor(27 if mode == 'metadata_align' else 28)
    editor.set_track_edit_lock(editor._clip(role).track, True)
    if mode in ('v1_promotion', 'v2_continuation'):
        rejected(editor, 28 if mode == 'v1_promotion' else 29, match='[Ll]ock')
    else:
        for action in (lambda: editor.preview_align_linked_audio(IDS),
                       lambda: editor.align_linked_audio(IDS),
                       lambda: plan_linked_audio_align(editor, IDS)):
            reject_call(editor, action, '[Ll]ock')


@pytest.mark.parametrize('affine', [False, True])
def test_unchanged_regular_request_allowed_with_both_tracks_locked_and_redo(affine):
    editor = affine_editor() if affine else editor_for()
    editor.update_clip('other', gain_db=-8); assert editor.undo()
    editor.project.track_edit_locks = [True, False, True, False]
    cut = editor._clip('la').end
    before, records = state(editor), tuple(editor._clip(key) for key in IDS)
    editor.preview_roll_linked_audio(IDS, cut)
    result = editor.roll_linked_audio(IDS, cut)
    assert state(editor) == before and editor.can_redo
    assert all(old is current for old, current in zip(records, result))


@pytest.mark.parametrize('track', [1, 3])
def test_unrelated_track_lock_does_not_block_audio_roll(track):
    editor = affine_editor(); editor.set_track_edit_lock(track, True)
    editor.roll_linked_audio(IDS, 29)
    assert editor._clip('la').end == editor._clip('ra').start == 29
    assert editor._clip('lv').end == editor._clip('rv').start == 27


@pytest.mark.parametrize('role', ['la', 'ra'])
@pytest.mark.parametrize('fade', ['fade_in', 'fade_out'])
def test_audio_fade_fit_rejects_without_clamping(role, fade):
    editor = editor_for(); setattr(editor._clip(role), fade, 20)
    rejected(editor, 26 if role == 'la' else 28, match='fade')


def test_video_fades_are_not_revalidated_against_audio_duration():
    editor = editor_for()
    editor._clip('rv').fade_in = editor._clip('rv').fade_out = 20
    editor.roll_linked_audio(IDS, 28)
    assert editor._clip('rv').fade_in == editor._clip('rv').fade_out == 20


@pytest.mark.parametrize('kind', ['window', 'gain'])
@pytest.mark.parametrize('delta', [-1, 1])
def test_right_audio_hidden_key_overflow_is_atomic(kind, delta):
    editor = editor_for(); clip = editor._clip('ra')
    if kind == 'window':
        clip.grade.qualifier.window_keyframes = [WindowKeyframe(-MAX_WINDOW_FRAME if delta > 0 else MAX_WINDOW_FRAME)]
    else:
        clip.audio_gain_keyframes = [AudioGainKeyframe(-MAX_AUDIO_GAIN_SAMPLE if delta > 0 else MAX_AUDIO_GAIN_SAMPLE)]
    rejected(editor, 27+delta)


@pytest.mark.parametrize('kind', ['motion', 'window', 'gain'])
def test_extreme_video_keys_are_not_shifted_by_audio_roll(kind):
    editor = editor_for(); video = editor._clip('rv')
    if kind == 'motion': video.motion_keyframes = [ClipMotionKeyframe(-MAX_MOTION_FRAME)]
    elif kind == 'window': video.grade.qualifier.window_keyframes = [WindowKeyframe(-MAX_WINDOW_FRAME)]
    else: video.audio_gain_keyframes = [AudioGainKeyframe(-MAX_AUDIO_GAIN_SAMPLE)]
    old = deepcopy(video); editor.roll_linked_audio(IDS, 28)
    assert video == old and editor._clip('rv') is video


def test_source_zero_and_eof_are_rejected_without_clamping():
    editor = editor_for(right_speed=Fraction(4))
    for key in IDS[2:]:
        clip = editor._clip(key)
        clip.time_map, clip.source_in = ClipTimeMap(4, 83, 4), Fraction(4)
    rejected(editor, 25, match='rational bounds')
    editor.roll_linked_audio(IDS, 26)
    assert editor._clip('ra').source_in == 0 and editor._clip('rv').source_in == 4
    editor = editor_for(left_speed=Fraction(4))
    limit = editor.project.media_frames(editor.project.media[0])
    for key in IDS[:2]:
        clip = editor._clip(key)
        clip.time_map, clip.source_in = ClipTimeMap(limit-83, limit-4, 4), Fraction(limit-83)
    editor.roll_linked_audio(IDS, 28)
    assert editor._clip('la').source_out == limit and editor._clip('lv').source_out == limit-4
    rejected(editor, 29, match='source media')


def test_source_rational_numerator_overflow_is_atomic():
    editor = editor_for(left_speed=Fraction(4), fps=Fraction(1000))
    endpoint = Fraction(MAX_SOURCE_NUMERATOR, 1000000)
    editor.project.media[0].duration = 100000000
    for key in IDS[:2]:
        clip = editor._clip(key)
        clip.time_map, clip.source_in = ClipTimeMap(endpoint-79, endpoint, 4), endpoint-79
    rejected(editor, 28, match='rational bounds')


def test_output_envelope_budget_is_not_silently_clamped():
    editor = editor_for(Fraction(1), Fraction(1), fps=Fraction(1000))
    length = MAX_AUDIO_GAIN_SAMPLE // 48
    editor.project.media[0].duration = 1e10
    for key in IDS[:2]:
        clip = editor._clip(key)
        clip.source_in, clip.duration, clip.time_map = 0, length, None
    for key in IDS[2:]: editor._clip(key).start = 7+length
    editor.project.validate()
    rejected(editor, 8+length, match='sample budget')


def test_outer_endpoint_bound_is_checked_not_only_requested_cut():
    editor = editor_for()
    for key in IDS: editor._clip(key).start += MAX_CAPTION_FRAME-46
    rejected(editor, editor._clip('la').end-1)


@pytest.mark.parametrize('muted', [False, True])
@pytest.mark.parametrize('anchor', [False, True])
def test_full_historical_audio_order_including_muted_contributors_is_guarded(muted, anchor):
    rejected(order_fixture(muted=muted, anchor=anchor), 29, match='historical audio mixing order')


@pytest.mark.parametrize('tie_id', ['000-other', 'zzz-other'])
def test_equal_start_id_tiebreaker_is_not_repaired(tie_id):
    editor = order_fixture(tie_id=tie_id)
    if tie_id < 'ra': rejected(editor, 28, match='historical audio mixing order')
    else:
        editor.roll_linked_audio(IDS, 28)
        assert editor._clip('ra').start == 28


def test_suppressed_contributor_does_not_false_block_order():
    editor = order_fixture(suppressed=True)
    editor.roll_linked_audio(IDS, 29)
    assert editor._clip('ra').start == 29


@pytest.mark.parametrize('existing_v2', [False, True])
def test_generic_transaction_cannot_smuggle_identical_audio_only_candidate(existing_v2):
    editor = affine_editor() if existing_v2 else editor_for()
    candidate = linked_audio_roll_candidate(editor.project, IDS, 29)
    before = state(editor)
    with pytest.raises(ValueError, match='[Ll]ink|Unlink'):
        editor._commit(lambda: setattr(editor, '_project', deepcopy(candidate)))
    assert state(editor) == before


def test_exact_candidate_fence_rejects_unrelated_valid_payload_change(monkeypatch):
    editor = editor_for(); before = state(editor)
    private = editor._Editor__commit
    def injected(change, **kwargs):
        def malicious():
            result = change(); editor.project.captions[0].text = 'not approved'; return result
        return private(malicious, **kwargs)
    monkeypatch.setattr(editor, '_Editor__commit', injected)
    with pytest.raises(ValueError): editor.roll_linked_audio(IDS, 28)
    assert state(editor) == before


@pytest.mark.parametrize('kind', ['revision', 'undo_aba', 'redo_aba', 'lock', 'new_session',
    'identity_only', 'reload', 'direct_audio_payload', 'direct_video_payload', 'direct_media', 'unlink'])
@pytest.mark.parametrize('reset', [False, True])
def test_captured_plan_never_renews_stale_context(kind, reset):
    editor = affine_editor()
    plan = plan_linked_audio_align(editor, IDS) if reset else plan_linked_audio_roll(editor, IDS, 29)
    if kind in ('revision', 'undo_aba', 'redo_aba'):
        editor.update_clip('other', gain_db=-8)
        if kind != 'revision': editor.undo()
        if kind == 'redo_aba': editor.redo()
    elif kind == 'lock': editor.set_track_edit_lock(0, True)
    elif kind == 'new_session': editor.project = deepcopy(editor.project)
    elif kind == 'identity_only': editor._project = deepcopy(editor.project)
    elif kind == 'reload': editor.project = Project.from_dict(editor.project.to_dict())
    elif kind == 'direct_audio_payload': editor._clip('ra').gain_db = -9
    elif kind == 'direct_video_payload': editor._clip('rv').grade.exposure = .9
    elif kind == 'direct_media': editor.project.media[0].path = '/changed-but-never-opened.mov'
    else: editor.unlink_av_clips(['ra'])
    reject_call(editor, lambda: apply_linked_audio_roll(editor, plan), 'stale')


@pytest.mark.parametrize('field,value', [('session_id', 'wrong'), ('revision', True),
    ('project_identity', 0), ('project_signature', '0'*64), ('candidate_signature', '0'*64),
    ('clip_ids', ('lv', 'la')), ('clip_ids', list(IDS)), ('cut', True), ('cut', 30),
    ('reset', 1), ('reset', True), ('token', ''), ('token', None)])
def test_tampered_plan_is_not_a_candidate_authority(field, value):
    editor = affine_editor(); plan = plan_linked_audio_roll(editor, IDS, 29)
    reject_call(editor, lambda: apply_linked_audio_roll(editor, replace(plan, **{field: value})))


@pytest.mark.parametrize('cut', [27, 28])
def test_changed_and_noop_plan_each_applies_at_most_once(cut):
    editor = editor_for(); editor.update_clip('other', gain_db=-8); editor.undo()
    before = state(editor)
    plan = plan_linked_audio_roll(editor, IDS, cut)
    apply_linked_audio_roll(editor, plan)
    if cut == 27: assert state(editor) == before and editor.can_redo
    else: assert editor.revision == before[3]+1
    reject_call(editor, lambda: apply_linked_audio_roll(editor, plan), 'stale|already applied')


def test_noop_receipt_budget_is_bounded_and_never_changes_project():
    editor = editor_for(); before = state(editor)
    # Exercise the threshold without spending 1,024 full candidate derivations.
    key = (editor.session_id, editor.revision)
    editor._linked_audio_roll_consumed_plans = (key, {f'used-{i}' for i in range(1023)})
    apply_linked_audio_roll(editor, plan_linked_audio_roll(editor, IDS, 27))
    assert len(editor._linked_audio_roll_consumed_plans[1]) == 1024
    reject_call(editor, lambda: apply_linked_audio_roll(editor,
        plan_linked_audio_roll(editor, IDS, 27)), 'budget')
    assert state(editor) == before


def test_plan_preview_and_apply_have_no_source_observation_or_media_process(monkeypatch):
    import builtins
    import subprocess
    editor = affine_editor()
    def forbidden(*args, **kwargs): raise AssertionError('audio roll must be pure owner-thread work')
    with monkeypatch.context() as patch:
        for name in ('open', 'stat', 'lstat'): patch.setattr(Path, name, forbidden)
        patch.setattr(builtins, 'open', forbidden)
        patch.setattr(subprocess, 'run', forbidden); patch.setattr(subprocess, 'Popen', forbidden)
        editor.preview_roll_linked_audio(IDS, 29)
        plan = plan_linked_audio_roll(editor, IDS, 29)
        apply_linked_audio_roll(editor, plan)
        align = plan_linked_audio_align(editor, IDS)
        apply_linked_audio_roll(editor, align)


def test_editor_preview_plan_apply_and_noop_commands_require_owner_thread():
    editor = affine_editor(); before = state(editor)
    plan = plan_linked_audio_roll(editor, IDS, 29); errors = []
    actions = [lambda: editor.preview_roll_linked_audio(IDS, 29),
               lambda: editor.roll_linked_audio(IDS, 29),
               lambda: editor.roll_linked_audio(IDS, 28),
               lambda: editor.preview_align_linked_audio(IDS),
               lambda: editor.align_linked_audio(IDS),
               lambda: plan_linked_audio_roll(editor, IDS, 29),
               lambda: plan_linked_audio_align(editor, IDS),
               lambda: apply_linked_audio_roll(editor, plan)]
    def worker():
        for action in actions:
            try: action()
            except ValueError as exc: errors.append(str(exc))
    thread = Thread(target=worker); thread.start(); thread.join()
    assert len(errors) == len(actions) and all('owning thread' in error for error in errors)
    assert state(editor) == before


@pytest.mark.parametrize('reset', [False, True])
def test_one_private_exact_candidate_not_sequential_trims_or_conservation(monkeypatch, reset):
    import lumen.clip_audio_translation as translation
    editor = affine_editor() if reset else editor_for()
    def forbidden(*args, **kwargs):
        pytest.fail('Audio-only roll must not compose trims, unlink, or waveform-conservation proofs')
    for name in ('trim_edge', 'preview_trim_edge', 'trim_linked_av', 'preview_trim_linked_av',
                 'roll_cut', 'preview_roll_cut', 'roll_linked_av', 'preview_roll_linked_av',
                 'unlink_av_clips', 'link_av_clips', '_set_clip_timing', '_commit'):
        monkeypatch.setattr(editor, name, forbidden)
    monkeypatch.setattr(translation, 'guard_local_audio_translation', forbidden)
    monkeypatch.setattr(translation, 'guard_common_audio_translation', forbidden)
    private = Mock(wraps=editor._Editor__commit)
    monkeypatch.setattr(editor, '_Editor__commit', private)
    if reset: editor.align_linked_audio(IDS)
    else: editor.roll_linked_audio(IDS, 28)
    assert private.call_count == 1
    assert set(private.call_args.kwargs) == {'linked_av_candidate'}
    assert isinstance(private.call_args.kwargs['linked_av_candidate'], Project)


@pytest.mark.parametrize('mutation', ['ownership', 'anchor', 'new_special_clip'])
def test_existing_private_candidate_fence_does_not_gain_structural_audio_authority(mutation):
    editor = editor_for(); before = state(editor)
    candidate = linked_audio_roll_candidate(editor.project, IDS, 28)
    unrelated = next(clip for clip in candidate.clips if clip.id == 'other')
    if mutation == 'ownership': unrelated.source_audio_enabled = False
    elif mutation == 'anchor': unrelated.audio_mix_anchor = AudioMixAnchor(1, 80, 'forged-slot')
    else:
        appended = deepcopy(candidate.clips[0]); appended.id, appended.start = 'forged-video', 100
        candidate.clips.append(appended)
    # Even an exact approved snapshot never bypasses the unchanged structural
    # audio fence. This is not the separate split/separation/copy authority.
    with pytest.raises(ValueError, match='structural audio state|Special source audio state'):
        editor._Editor__commit(lambda: setattr(editor, '_project', deepcopy(candidate)),
                               linked_av_candidate=candidate)
    assert state(editor) == before

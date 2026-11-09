"""Four-record joint roll: independent exact oracle, purity and edit fences."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from itertools import permutations
from threading import Thread
from unittest.mock import Mock

import pytest

from lumen.audio_gain import AudioGainKeyframe, MAX_AUDIO_GAIN_SAMPLE
from lumen.clip_motion import ClipMotionKeyframe, MAX_MOTION_FRAME
from lumen.model import (AVLink, AudioMixAnchor, Clip, ClipGroup, Editor,
                         MAX_CAPTION_FRAME, MediaBin, Project, VideoTransition)
from lumen.qualifier import MAX_WINDOW_FRAME, WindowKeyframe
from lumen.source_ranges import SourceRange, SourceRangeBinding
from lumen.timing import ClipTimeMap, MAX_SOURCE_NUMERATOR
from test_linked_av_model import sample, state


IDS = ('lv', 'la', 'rv', 'ra')


def editor_for(left_speed=Fraction(3, 2), right_speed=Fraction(2, 3),
               fps=Fraction(30000, 1001), start=7):
    """No real media, receipts or production roll/trim helper in this fixture."""
    editor = sample(fps, ClipTimeMap(80, 80 + Fraction(79, 4) * left_speed, left_speed))
    project = editor.project
    left = project.clips[:2]
    for clip, clip_id in zip(left, IDS[:2]):
        clip.id, clip.start = clip_id, start
    source = deepcopy(project.media[0])
    source.id, source.path, source.name = 'right-source', '/never-opened-right.mov', 'Right source'
    project.media.append(source)
    right = deepcopy(left)
    for clip, clip_id in zip(right, IDS[2:]):
        clip.id, clip.media_id, clip.start = clip_id, source.id, start + 20
        clip.time_map = ClipTimeMap(120, 120 + Fraction(79, 4) * right_speed, right_speed)
        clip.source_in = clip.time_map.source_in
        clip.audio_mix_anchor = AudioMixAnchor(0, 4, 'right-owner')
    project.clips[2:2] = right
    for clip in project.clips[:4]:
        clip.motion_keyframes = ([ClipMotionKeyframe(-8, x=.3),
            ClipMotionKeyframe(5, x=.8, interpolation='hold'), ClipMotionKeyframe(70, x=1.4)]
            if clip.track < 2 else [])
        clip.grade.qualifier.enabled = False
        clip.grade.qualifier.window_keyframes = [WindowKeyframe(-5),
            WindowKeyframe(7, center_x=.7, interpolation='hold'), WindowKeyframe(60)]
        clip.audio_gain_keyframes = [AudioGainKeyframe(-200, -7),
            AudioGainKeyframe(20000, 3, 'hold'), AudioGainKeyframe(800000, -2)]
    project.av_links = [AVLink('left-link', *IDS[:2], 'm'),
                        AVLink('right-link', *IDS[2:], source.id)]
    project.clips.append(Clip('group-other', 'm', 3, 80, 20, 7))
    project.clip_groups = [ClipGroup('unrelated-group', ['other', 'group-other'])]
    project.media_bins = [MediaBin('bin', 'Unrelated bin')]
    project.media_bin_ids = {'m': 'bin', source.id: 'bin'}
    project.source_ranges = [SourceRange('range', 'Keep range', 'm', 0, 1, 1, 1,
        24, 1, SourceRangeBinding(1, 91, 'a' * 64, 'b' * 64))]
    project.master_gain_db = -2.5
    project.validate()
    return editor


def boundary(frame, fps):
    value = Fraction(frame * 48000, 1) / fps
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def expected_project(old, cut):
    """Old-project/request algebra, not the production candidate/rebase helpers."""
    expected = deepcopy(old)
    originals = {clip.id: clip for clip in old.clips}
    delta = cut - originals['lv'].end
    for clip in expected.clips:
        if clip.id not in IDS:
            continue
        previous = originals[clip.id]
        if clip.id in IDS[:2]:
            origin, endpoint = previous.source_in, previous.source_out + delta * previous.speed
            clip.duration += delta
        else:
            origin, endpoint = previous.source_in + delta * previous.speed, previous.source_out
            clip.start, clip.duration = cut, previous.duration - delta
            sample_delta = boundary(cut, old.fps) - boundary(previous.start, old.fps)
            clip.motion_keyframes = [replace(key, frame=key.frame-delta) for key in previous.motion_keyframes]
            clip.grade.qualifier.window_keyframes = [replace(key, frame=key.frame-delta)
                                                     for key in previous.grade.qualifier.window_keyframes]
            clip.audio_gain_keyframes = [replace(key, sample=key.sample-sample_delta)
                                        for key in previous.audio_gain_keyframes]
        clip.source_in = origin
        clip.time_map = ClipTimeMap(origin, endpoint, previous.speed)
        if previous.speed == 1 and origin.denominator == 1 and endpoint-origin == clip.duration:
            clip.source_in, clip.time_map = int(origin), None
    return expected


def rejected(editor, cut, ids=IDS, match=None):
    before, project, identities = state(editor), editor.project, tuple(editor.project.clips)
    for method in (editor.preview_roll_linked_av, editor.roll_linked_av):
        with pytest.raises(ValueError, match=match):
            method(ids, cut)
        assert state(editor) == before
        assert editor.project is project
        assert all(a is b for a, b in zip(editor.project.clips, identities))


@pytest.mark.parametrize('left_speed', [Fraction(1, 4), Fraction(1), Fraction(3, 2), Fraction(4)])
@pytest.mark.parametrize('right_speed', [Fraction(1, 4), Fraction(1), Fraction(2, 3), Fraction(4)])
@pytest.mark.parametrize('fps', [Fraction(24), Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001)])
@pytest.mark.parametrize('delta', [-3, 2])
def test_joint_exact_maps_partial_tails_hidden_keys_all_records_one_history(left_speed, right_speed, fps, delta):
    editor = editor_for(left_speed, right_speed, fps)
    old, before = deepcopy(editor.project), state(editor)
    cut = editor._clip('lv').end + delta
    expected = expected_project(old, cut)
    identities = {clip.id: clip for clip in editor.project.clips}
    preview = editor.preview_roll_linked_av(IDS, cut)
    assert tuple(clip.id for clip in preview) == IDS
    assert preview == tuple(clip for clip in expected.clips if clip.id in IDS)
    assert state(editor) == before
    assert all(clip is not identities[clip.id] for clip in preview)
    result = editor.roll_linked_av(tuple(reversed(IDS)), cut)
    assert tuple(clip.id for clip in result) == IDS
    assert editor.project == expected
    assert all(clip is identities[clip.id] for clip in editor.project.clips)
    assert all(clip is identities[clip.id] for clip in result)
    for previous, current in zip(old.clips[:4], result):
        assert (current.duration*current.speed-(current.source_out-current.source_in)
                == previous.duration*previous.speed-(previous.source_out-previous.source_in)
                == previous.speed/4)
    assert editor.project.duration_frames == old.duration_frames
    assert len(editor._undo) == len(before[1])+1 and editor.revision == before[3]+1
    assert Project.from_dict(editor.project.to_dict()) == expected
    assert editor.undo() and editor.project == old
    assert editor.redo() and editor.project == expected


@pytest.mark.parametrize('ids', list(permutations(IDS)))
def test_selection_order_and_unsorted_project_never_change_role_order(ids):
    editor = editor_for()
    editor.project.clips.reverse()
    editor.project.av_links.reverse()
    result = editor.roll_linked_av(ids, 28)
    assert tuple(clip.id for clip in result) == IDS
    assert (result[0].end, result[1].end, result[2].start, result[3].start) == (28,)*4


@pytest.mark.parametrize('ids', [(), ('lv',), ('lv', 'la'), ('lv', 'la', 'rv'),
    (*IDS, 'other'), (*IDS, 'lv'), ('lv', 'la', 'rv', 'other'),
    ('lv', 'la', 'rv', 'missing'), 'lv', None, (True, 'la', 'rv', 'ra')])
def test_exactly_four_existing_unique_complete_pair_selection(ids):
    rejected(editor_for(), 28, ids)


@pytest.mark.parametrize('cut', [True, False, 28.0, Fraction(28), '28', None,
    float('nan'), float('inf'), -1, 2**53, -2**53])
def test_invalid_absolute_cut_types_and_bounds(cut):
    rejected(editor_for(), cut)


@pytest.mark.parametrize('cut', [0, 7, 47, 48, 1000])
def test_each_side_requires_one_timeline_frame(cut):
    rejected(editor_for(), cut)


@pytest.mark.parametrize('delta', [-19, 19])
def test_one_frame_partial_tail_retains_residue(delta):
    editor = editor_for()
    for clip in editor.project.clips[:4]:
        clip.fade_in = clip.fade_out = 0
    old = deepcopy(editor.project)
    result = editor.roll_linked_av(IDS, 27+delta)
    shortened = result[:2] if delta < 0 else result[2:]
    for clip in shortened:
        assert clip.duration == 1
        assert clip.source_out-clip.source_in == clip.speed*Fraction(3, 4)
    assert editor.project == expected_project(old, 27+delta)


@pytest.mark.parametrize('locked', [False, True])
def test_noop_detached_preview_and_live_return_preserve_identities_redo_revision(locked):
    editor = editor_for()
    editor.update_clip('other', gain_db=-4)
    editor.undo()
    editor.project.track_edit_locks = [locked, False, locked, False]
    original = tuple(editor._clip(clip_id) for clip_id in IDS)
    before, project = state(editor), editor.project
    preview = editor.preview_roll_linked_av(IDS, 27)
    preview[0].grade.qualifier.window_keyframes.clear()
    preview[3].audio_gain_keyframes.clear()
    assert state(editor) == before
    result = editor.roll_linked_av(IDS, 27)
    assert all(a is b for a, b in zip(result, original))
    assert editor.project is project and state(editor) == before and editor.can_redo


@pytest.mark.parametrize('operation', ['preview', 'noop', 'invalid'])
def test_live_validation_canonicalization_never_mutates_rejected_or_noop_inputs(operation):
    editor = editor_for()
    editor.project.master_gain_db = -2  # Valid but Project.validate would change its type.
    before = state(editor)
    if operation == 'preview':
        editor.preview_roll_linked_av(IDS, 28)
    elif operation == 'noop':
        editor.roll_linked_av(IDS, 27)
    else:
        rejected(editor, 7)
    assert type(editor.project.master_gain_db) is int and state(editor) == before


@pytest.mark.parametrize('role', IDS)
@pytest.mark.parametrize('fade', ['fade_in', 'fade_out'])
def test_fade_on_either_role_rejects_whole_candidate_without_clamping(role, fade):
    editor = editor_for()
    setattr(editor._clip(role), fade, 20)
    cut = 26 if role in IDS[:2] else 28
    rejected(editor, cut, match='fade')
    setattr(editor._clip(role), fade, 19)
    editor.roll_linked_av(IDS, cut)
    assert getattr(editor._clip(role), fade) == 19


@pytest.mark.parametrize('track', [0, 2])
@pytest.mark.parametrize('delta', [-1, 1])
def test_either_track_lock_rejects_all_four(track, delta):
    editor = editor_for()
    editor.set_track_edit_lock(track, True)
    rejected(editor, 27+delta, match='[Ll]ock')


@pytest.mark.parametrize('kind', ['gap', 'different_video_track', 'different_audio_track',
    'missing_link', 'missing_role', 'wrong_pair_media', 'different_pair_map',
    'enabled_video', 'suppressed_audio', 'grouped_member', 'transition_endpoint', 'collision'])
def test_incomplete_nonadjacent_or_invalid_project_is_not_repaired(kind):
    editor = editor_for()
    if kind == 'gap':
        for clip_id in IDS[2:]: editor._clip(clip_id).start += 1
    elif kind == 'different_video_track': editor._clip('rv').track = 1
    elif kind == 'different_audio_track': editor._clip('ra').track = 3
    elif kind == 'missing_link': editor.project.av_links.pop()
    elif kind == 'missing_role': editor.project.clips.remove(editor._clip('ra'))
    elif kind == 'wrong_pair_media': editor._clip('ra').media_id = 'm'
    elif kind == 'different_pair_map':
        editor._clip('ra').time_map = ClipTimeMap(121, 121+Fraction(79, 6), Fraction(2, 3))
        editor._clip('ra').source_in = 121
    elif kind == 'enabled_video': editor._clip('lv').source_audio_enabled = True
    elif kind == 'suppressed_audio': editor._clip('ra').source_audio_enabled = False
    elif kind == 'grouped_member': editor.project.clip_groups.append(ClipGroup('bad-group', ['lv', 'la']))
    elif kind == 'transition_endpoint':
        editor.project.video_transitions.append(VideoTransition('bad-transition', 'lv', 'rv', 2))
    else: editor.project.clips.append(Clip('collision', 'm', 2, 26, 0, 2))
    rejected(editor, 28)


def test_source_zero_and_eof_handles_are_exact_not_clamped():
    editor = editor_for(right_speed=Fraction(4))
    for clip_id in IDS[2:]:
        clip = editor._clip(clip_id)
        clip.time_map, clip.source_in = ClipTimeMap(4, 83, 4), Fraction(4)
    rejected(editor, 25, match='rational bounds')
    editor.roll_linked_av(IDS, 26)
    assert editor._clip('ra').source_in == 0
    editor = editor_for(left_speed=Fraction(4))
    bound = editor.project.media_frames(editor.project.media[0])
    for clip_id in IDS[:2]:
        clip = editor._clip(clip_id)
        clip.time_map = ClipTimeMap(bound-83, bound-4, 4)
        clip.source_in = bound-83
    editor.roll_linked_av(IDS, 28)
    assert editor._clip('lv').source_out == bound
    rejected(editor, 29, match='source media')


@pytest.mark.parametrize('side', ['left', 'right'])
def test_coprime_rational_overflow_is_atomic(side):
    editor = editor_for()
    speed = Fraction(999978, 999979)
    origin = Fraction(9999831, 999983) if side == 'right' else Fraction(10)
    endpoint = Fraction(49999151, 999983) if side == 'left' else Fraction(50)
    mapping = ClipTimeMap(origin, endpoint, speed)
    length = -(-((endpoint-origin)/speed).numerator // ((endpoint-origin)/speed).denominator)
    for clip_id in IDS[:2] if side == 'left' else IDS[2:]:
        clip = editor._clip(clip_id)
        clip.time_map, clip.source_in, clip.duration = mapping, origin, length
    if side == 'left':
        for clip_id in IDS[2:]: editor._clip(clip_id).start = 7+length
    editor.project.validate()
    rejected(editor, editor._clip('lv').end+1, match='rational bounds')


def test_source_numerator_overflow():
    editor = editor_for(left_speed=Fraction(4), fps=Fraction(1000))
    endpoint = Fraction(MAX_SOURCE_NUMERATOR, 1000000)
    editor.project.media[0].duration = 100000000
    for clip_id in IDS[:2]:
        clip = editor._clip(clip_id)
        clip.time_map = ClipTimeMap(endpoint-79, endpoint, 4)
        clip.source_in = endpoint-79
    rejected(editor, 28, match='rational bounds')


@pytest.mark.parametrize('kind,role', [('motion', 'rv'), ('window', 'rv'), ('window', 'ra'),
    ('gain', 'rv'), ('gain', 'ra')])
@pytest.mark.parametrize('delta', [-1, 1])
def test_all_hidden_right_key_overflows_reject_atomically(kind, role, delta):
    editor = editor_for()
    clip = editor._clip(role)
    if kind == 'motion':
        clip.motion_keyframes = [ClipMotionKeyframe(-MAX_MOTION_FRAME if delta > 0 else MAX_MOTION_FRAME)]
    elif kind == 'window':
        clip.grade.qualifier.window_keyframes = [WindowKeyframe(-MAX_WINDOW_FRAME if delta > 0 else MAX_WINDOW_FRAME)]
    else:
        clip.audio_gain_keyframes = [AudioGainKeyframe(-MAX_AUDIO_GAIN_SAMPLE if delta > 0 else MAX_AUDIO_GAIN_SAMPLE)]
    rejected(editor, 27+delta)


@pytest.mark.parametrize('kind', ['motion', 'window', 'gain'])
def test_left_extreme_hidden_keys_do_not_shift(kind):
    editor = editor_for()
    clip = editor._clip('lv')
    if kind == 'motion': clip.motion_keyframes = [ClipMotionKeyframe(-MAX_MOTION_FRAME)]
    elif kind == 'window': clip.grade.qualifier.window_keyframes = [WindowKeyframe(-MAX_WINDOW_FRAME)]
    else: clip.audio_gain_keyframes = [AudioGainKeyframe(-MAX_AUDIO_GAIN_SAMPLE)]
    before = deepcopy(clip)
    editor.roll_linked_av(IDS, 28)
    after = editor._clip('lv')
    assert after.motion_keyframes == before.motion_keyframes
    assert after.grade.qualifier.window_keyframes == before.grade.qualifier.window_keyframes
    assert after.audio_gain_keyframes == before.audio_gain_keyframes


def test_global_sample_boundary_difference_not_displacement_rounding():
    fps = Fraction(30000, 1001)
    editor = editor_for(fps=fps, start=6)  # C0=26; next frame advances 1601, not1602 samples.
    old = deepcopy(editor.project)
    sample_delta = boundary(27, fps)-boundary(26, fps)
    assert sample_delta != boundary(1, fps)
    editor.roll_linked_av(IDS, 27)
    for previous, current in zip(old.clips[2:4], editor.project.clips[2:4]):
        assert current.audio_gain_keyframes == [replace(key, sample=key.sample-sample_delta)
                                                for key in previous.audio_gain_keyframes]


def test_new_audio_envelope_output_budget_rejects_without_overflow_clamp():
    editor = editor_for(Fraction(1), Fraction(1), fps=Fraction(1000))
    length = MAX_AUDIO_GAIN_SAMPLE // 48
    editor.project.media[0].duration = 1e10
    for clip_id in IDS[:2]:
        clip = editor._clip(clip_id)
        clip.source_in, clip.duration, clip.time_map = 0, length, None
    for clip_id in IDS[2:]: editor._clip(clip_id).start = 7+length
    editor.project.validate()
    rejected(editor, 8+length, match='sample budget')


@pytest.mark.parametrize('delta', [-1, 1])
def test_bounded_outer_timeline_endpoint_is_checked(delta):
    editor = editor_for()
    for clip_id in IDS:
        editor._clip(clip_id).start += MAX_CAPTION_FRAME-46
    rejected(editor, editor._clip('lv').end+delta)


def order_fixture(*, muted=False, suppressed=False, anchor=True, tie_id='other'):
    editor = editor_for()
    other = editor._clip('other')
    # On another video track: overlap is intentional, no clip collision hides order proof.
    other.start = 28
    other.muted, other.source_audio_enabled = muted, not suppressed
    editor._clip('ra').muted = muted
    if anchor:
        other.audio_mix_anchor = deepcopy(editor._clip('ra').audio_mix_anchor)
    else:
        editor._clip('ra').audio_mix_anchor = None
        other.audio_mix_anchor = AudioMixAnchor(2, 28, 'ra')
    if tie_id != 'other':
        other.id = tie_id
        editor.project.clip_groups[0].clip_ids[0] = tie_id
    editor.project.validate()
    return editor


@pytest.mark.parametrize('muted', [False, True])
@pytest.mark.parametrize('anchor', [False, True])
def test_full_audio_mix_sort_order_crossing_rejects_muted_and_unanchored_cases(muted, anchor):
    editor = order_fixture(muted=muted, anchor=anchor)
    rejected(editor, 29, match='historical audio mixing order')


@pytest.mark.parametrize('other_id', ['000-other', 'zzz-other'])
def test_equal_start_id_tiebreaker_not_just_historical_anchor(other_id):
    editor = order_fixture(tie_id=other_id)
    if other_id < 'ra':
        rejected(editor, 28, match='historical audio mixing order')
    else:
        editor.roll_linked_av(IDS, 28)
        assert editor._clip('ra').start == 28


def test_shared_anchor_legal_order_and_suppressed_video_do_not_false_reject():
    editor = order_fixture(suppressed=True)
    old = deepcopy(editor.project)
    editor.roll_linked_av(IDS, 29)
    assert editor.project == expected_project(old, 29)
    editor = editor_for()
    editor._clip('ra').audio_mix_anchor = deepcopy(editor._clip('la').audio_mix_anchor)
    old = deepcopy(editor.project)
    editor.roll_linked_av(IDS, 28)
    assert editor.project == expected_project(old, 28)


def test_one_private_candidate_not_sequential_trims_or_generic_audio_conservation(monkeypatch):
    import lumen.clip_audio_translation as translation
    editor = editor_for()
    def forbidden(*args, **kwargs):
        pytest.fail('Joint roll must not compose independent trims, clamp fades or claim PCM conservation')
    for name in ('trim_edge', 'preview_trim_edge', 'trim_linked_av', 'preview_trim_linked_av',
                 'roll_cut', 'preview_roll_cut', 'unlink_av_clips', 'link_av_clips', '_set_clip_timing', '_commit'):
        monkeypatch.setattr(editor, name, forbidden)
    monkeypatch.setattr(translation, 'guard_local_audio_translation', forbidden)
    monkeypatch.setattr(translation, 'guard_common_audio_translation', forbidden)
    private = Mock(wraps=editor._Editor__commit)
    monkeypatch.setattr(editor, '_Editor__commit', private)
    editor.roll_linked_av(IDS, 28)
    assert private.call_count == 1
    assert set(private.call_args.kwargs) == {'linked_av_candidate'}
    assert isinstance(private.call_args.kwargs['linked_av_candidate'], Project)


def test_generic_transaction_cannot_smuggle_same_four_record_roll():
    editor = editor_for()
    expected = expected_project(editor.project, 28)
    before = state(editor)
    def change(): editor._project = deepcopy(expected)
    with pytest.raises(ValueError, match='Unlink'):
        editor._commit(change)
    assert state(editor) == before


def test_exact_candidate_fence_rejects_unrelated_change(monkeypatch):
    editor = editor_for()
    before = state(editor)
    private = editor._Editor__commit
    def injected(change, **kwargs):
        def malicious():
            result = change()
            editor.project.captions[0].text = 'unapproved'
            return result
        return private(malicious, **kwargs)
    monkeypatch.setattr(editor, '_Editor__commit', injected)
    with pytest.raises(ValueError, match='approved pair'):
        editor.roll_linked_av(IDS, 28)
    assert state(editor) == before


def test_existing_linked_authority_does_not_gain_split_special_audio_permission():
    editor = editor_for()
    candidate = expected_project(editor.project, 28)
    appended = deepcopy(candidate.clips[0])
    appended.id, appended.start = 'forged-special-video', 100
    candidate.clips.append(appended)
    before = state(editor)
    def change(): editor._project = deepcopy(candidate)
    with pytest.raises(ValueError, match='Special source audio state'):
        editor._Editor__commit(change, linked_av_candidate=candidate)
    assert state(editor) == before


def test_commit_owner_thread_noop_and_nonzero_both_reject():
    editor = editor_for()
    before, errors = state(editor), []
    def worker():
        for cut in (27, 28):
            try: editor.roll_linked_av(IDS, cut)
            except ValueError as exc: errors.append(str(exc))
    thread = Thread(target=worker)
    thread.start(); thread.join()
    assert len(errors) == 2 and all('owning thread' in error for error in errors)
    assert state(editor) == before


@pytest.mark.parametrize('video_track,audio_track', [(0, 2), (0, 3), (1, 2), (1, 3)])
def test_supported_track_pairs_and_later_touching_neighbors_stay_fixed(video_track, audio_track):
    editor = editor_for()
    for clip_id in ('lv', 'rv'): editor._clip(clip_id).track = video_track
    for clip_id in ('la', 'ra'): editor._clip(clip_id).track = audio_track
    editor.project.clips.extend([Clip('later-video', 'm', video_track, 47, 10, 5),
                                 Clip('later-audio', 'm', audio_track, 47, 10, 5)])
    editor.project.validate()
    old = deepcopy(editor.project)
    editor.roll_linked_av(IDS, 29)
    assert editor.project == expected_project(old, 29)


def test_unselected_third_complete_pair_is_not_expanded_or_changed():
    editor = editor_for()
    editor.project.clips.extend([Clip('third-v', 'm', 1, 130, 20, 5, source_audio_enabled=False),
                                 Clip('third-a', 'm', 3, 130, 20, 5)])
    editor.project.av_links.append(AVLink('third-link', 'third-v', 'third-a', 'm'))
    editor.project.validate()
    old = deepcopy(editor.project)
    editor.roll_linked_av(IDS, 28)
    assert editor.project == expected_project(old, 28)
    rejected(editor, 29, (*IDS, 'third-v', 'third-a'))


def test_planning_and_commit_do_not_observe_source_files_or_run_media_tools(monkeypatch):
    import builtins
    import pathlib
    import subprocess
    import lumen.linked_av_roll  # Resolve imports before denying file observation.
    import lumen.media
    editor = editor_for()
    def forbidden(*args, **kwargs):
        pytest.fail('Joint roll is a pure model edit, not source verification or media rendering')
    with monkeypatch.context() as patch:
        patch.setattr(builtins, 'open', forbidden)
        patch.setattr(pathlib.Path, 'open', forbidden)
        patch.setattr(pathlib.Path, 'stat', forbidden)
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(lumen.media, 'probe', forbidden)
        editor.preview_roll_linked_av(IDS, 28)
        editor.roll_linked_av(IDS, 28)

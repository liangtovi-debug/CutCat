"""Independent OLD/request paired-edge trim oracles; no media or GUI fixtures.

Expected endpoints and keys are scalar arithmetic on immutable old records.
No production trim/shift, duration evaluator or affine-validation helper builds
the expected project. In particular each role has its own sample-grid phase.
"""
from copy import deepcopy
from dataclasses import asdict, fields, is_dataclass, replace
from fractions import Fraction
import json
from threading import Thread

import pytest

from lumen.audio_gain import AudioGainKeyframe, MAX_AUDIO_GAIN_SAMPLE
from lumen.clip_motion import ClipMotionKeyframe, MAX_MOTION_FRAME
from lumen.linked_av_trim import linked_av_trim_candidate
from lumen.linked_av_split import qualify_linked_av_split
from lumen.model import (AVLink, AV_LINK_AFFINE_ALIGNMENT, AudioMixAnchor, Clip,
                         ClipGroup, Editor, MAX_CAPTION_FRAME, Project, VERSION)
from lumen.qualifier import MAX_WINDOW_FRAME, WindowKeyframe
from lumen.timing import ClipTimeMap, MAX_SOURCE_NUMERATOR
from test_linked_av_model import sample, state


def wire(project):
    return json.dumps(asdict(project), default=str, sort_keys=True, separators=(',', ':'))


def identities(value, prefix=()):
    """Include all mutable containers and nested record identities, not equality."""
    result = {}
    if is_dataclass(value):
        result[prefix] = id(value)
        for field in fields(value):
            result.update(identities(getattr(value, field.name), (*prefix, field.name)))
    elif isinstance(value, (list, dict)):
        result[prefix] = id(value)
        for key, item in (enumerate(value) if isinstance(value, list) else value.items()):
            result.update(identities(item, (*prefix, key)))
    return result


def snapshot(editor):
    return (state(editor), wire(editor.project), identities(editor.project),
            identities(editor._undo), identities(editor._redo))


def rounded_sample(frame, fps):
    numerator = Fraction(frame) * 48000 / fps
    return (2*numerator.numerator + numerator.denominator) // (2*numerator.denominator)


def affine_editor(speed=Fraction(1), fps=Fraction(30000, 1001), geometry='leading'):
    origin = Fraction(161, 4)
    editor = sample(fps, ClipTimeMap(origin, origin+10*speed-speed/4, speed))
    video, audio = editor._clip('v'), editor._clip('a')
    audio.start, audio.duration = {'leading': (3, 15), 'trailing': (7, 12),
        'equal-edges': (5, 10), 'equal-maps': (5, 10), 'disjoint': (30, 12)}[geometry]
    audio_origin = origin+(audio.start-video.start)*speed
    tail = speed/4 if geometry == 'equal-maps' else speed/2
    audio.source_in = audio_origin
    audio.time_map = ClipTimeMap(audio_origin, audio_origin+audio.duration*speed-tail, speed)
    video.motion_keyframes = [ClipMotionKeyframe(-8, x=.3),
        ClipMotionKeyframe(5, x=.8, interpolation='hold'), ClipMotionKeyframe(70, x=1.4)]
    for clip in (video, audio):
        clip.grade.qualifier.enabled = False
        clip.grade.qualifier.window_keyframes = [WindowKeyframe(-5),
            WindowKeyframe(7, center_x=.7, interpolation='hold'), WindowKeyframe(60)]
        clip.audio_gain_keyframes = [AudioGainKeyframe(-200, -7),
            AudioGainKeyframe(20000, 3, 'hold'), AudioGainKeyframe(800000, -2)]
    editor.project.av_links = [AVLink('pair', 'v', 'a', 'm', AV_LINK_AFFINE_ALIGNMENT)]
    editor.project.clips.extend([Clip('uv', 'm', 1, 120, 20, 5, source_audio_enabled=False),
                                Clip('ua', 'm', 3, 120, 20, 5)])
    editor.project.av_links.append(AVLink('unrelated', 'uv', 'ua', 'm'))
    editor.project.validate()
    return editor


def expected_project(old, edge, delta):
    expected = deepcopy(old)
    previous = {clip.id: clip for clip in old.clips}
    for clip in expected.clips:
        if clip.id not in ('v', 'a'):
            continue
        before = previous[clip.id]
        origin, endpoint, speed = before.source_in, before.source_out, before.speed
        origin += delta*speed if edge == 'left' else 0
        endpoint += delta*speed if edge == 'right' else 0
        clip.source_in, clip.time_map = origin, ClipTimeMap(origin, endpoint, speed)
        clip.duration += -delta if edge == 'left' else delta
        if edge == 'left':
            q = rounded_sample(before.start+delta, old.fps)-rounded_sample(before.start, old.fps)
            clip.start += delta
            clip.motion_keyframes = [replace(key, frame=key.frame-delta) for key in before.motion_keyframes]
            clip.grade.qualifier.window_keyframes = [replace(key, frame=key.frame-delta)
                for key in before.grade.qualifier.window_keyframes]
            clip.audio_gain_keyframes = [replace(key, sample=key.sample-q) for key in before.audio_gain_keyframes]
        # Independent legacy serialization rule; do not call normalization.
        if speed == 1 and Fraction(origin).denominator == 1 and endpoint-origin == clip.duration:
            clip.source_in, clip.time_map = int(origin), None
    return expected


def rejected(editor, edge, delta, *, role='v', match=None):
    before = snapshot(editor)
    for action in (lambda: linked_av_trim_candidate(editor.project, role, edge, delta),
                   lambda: editor.preview_trim_linked_av(role, edge, delta),
                   lambda: editor.trim_linked_av(role, edge, delta)):
        with pytest.raises(ValueError, match=match):
            action()
        assert snapshot(editor) == before


@pytest.mark.parametrize('speed', [Fraction(1, 4), Fraction(2, 3), Fraction(1), Fraction(3, 2), Fraction(4)])
@pytest.mark.parametrize('fps', [Fraction(24), Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001)])
@pytest.mark.parametrize('geometry', ['leading', 'trailing', 'equal-edges'])
@pytest.mark.parametrize('role', ['v', 'a'])
@pytest.mark.parametrize('edge,delta', [('left', -2), ('left', 2), ('right', -2), ('right', 2)])
def test_each_own_exact_map_tail_keys_and_one_atomic_history(speed, fps, geometry, role, edge, delta):
    editor = affine_editor(speed, fps, geometry)
    editor.update_clip('other', gain_db=-6); editor.undo()
    before, old = snapshot(editor), deepcopy(editor.project)
    expected = expected_project(old, edge, delta)
    clip_objects, link_objects = tuple(editor.project.clips), tuple(editor.project.av_links)
    candidate = linked_av_trim_candidate(editor.project, role, edge, delta)
    assert candidate == expected and wire(candidate) == wire(expected) and snapshot(editor) == before
    preview = editor.preview_trim_linked_av(role, edge, delta)
    assert tuple(clip.id for clip in preview) == ('v', 'a')
    assert preview == tuple(clip for clip in expected.clips if clip.id in ('v', 'a'))
    assert all(clip is not editor._clip(clip.id) for clip in preview) and snapshot(editor) == before
    result = editor.trim_linked_av(role, edge, delta)
    assert result is editor._clip(role) and editor.project == expected and wire(editor.project) == wire(expected)
    assert all(a is b for a, b in zip(clip_objects, editor.project.clips))
    assert all(a is b for a, b in zip(link_objects, editor.project.av_links))
    old_pair = [clip for clip in old.clips if clip.id in ('v', 'a')]
    new_pair = [editor._clip(key) for key in ('v', 'a')]
    for previous, current in zip(old_pair, new_pair):
        assert current.source_in-current.start*speed == previous.source_in-previous.start*speed
        assert current.duration*speed-(current.source_out-current.source_in) == previous.duration*speed-(previous.source_out-previous.source_in)
        if edge == 'left':
            assert (current.end, current.source_out) == (previous.end, previous.source_out)
        else:
            assert (current.start, current.source_in) == (previous.start, previous.source_in)
    assert new_pair[0].source_in-new_pair[0].start*speed == new_pair[1].source_in-new_pair[1].start*speed
    for attribute in ('start', 'end', 'duration'):
        assert getattr(new_pair[1], attribute)-getattr(new_pair[0], attribute) == getattr(old_pair[1], attribute)-getattr(old_pair[0], attribute)
    assert editor.project.av_links[0].alignment == AV_LINK_AFFINE_ALIGNMENT and VERSION == 43
    assert editor.revision == before[0][3]+1 and len(editor._undo) == len(before[0][1])+1 and not editor.can_redo
    assert Project.from_dict(editor.project.to_dict()) == expected
    assert editor.undo() and editor.project == old
    assert editor.redo() and editor.project == expected


@pytest.mark.parametrize('geometry', ['equal-maps', 'disjoint'])
@pytest.mark.parametrize('edge,delta', [('left', -1), ('left', 1), ('right', -1), ('right', 1)])
@pytest.mark.parametrize('role', ['v', 'a'])
def test_no_overlap_requirement_or_implicit_downgrade(geometry, edge, delta, role):
    editor = affine_editor(geometry=geometry); old = deepcopy(editor.project)
    editor.trim_linked_av(role, edge, delta)
    assert editor.project == expected_project(old, edge, delta)
    assert editor.project.av_links[0].alignment == AV_LINK_AFFINE_ALIGNMENT


def test_distinct_global_sample_origins_1602_1601_with_unequal_tails():
    editor = affine_editor(); video, audio = editor._clip('v'), editor._clip('a')
    video.source_in, video.duration, video.time_map = 12, 30, ClipTimeMap(12, Fraction(167, 4))
    audio.source_in, audio.duration, audio.time_map = 10, 34, ClipTimeMap(10, Fraction(87, 2))
    editor.project.validate(); old = deepcopy(editor.project)
    assert rounded_sample(6, old.fps)-rounded_sample(5, old.fps) == 1602
    assert rounded_sample(4, old.fps)-rounded_sample(3, old.fps) == 1601
    assert rounded_sample(1, old.fps) == 1602
    editor.trim_linked_av('a', 'left', 1)
    assert editor.project == expected_project(old, 'left', 1)
    assert video.time_map == ClipTimeMap(13, Fraction(167, 4)) and audio.time_map == ClipTimeMap(11, Fraction(87, 2))
    assert [key.sample for key in video.audio_gain_keyframes] == [-1802, 18398, 798398]
    assert [key.sample for key in audio.audio_gain_keyframes] == [-1801, 18399, 798399]
    assert (video.start, video.duration, audio.start, audio.duration) == (6, 29, 4, 33)


@pytest.mark.parametrize('edge', ['left', 'right'])
@pytest.mark.parametrize('role', ['v', 'a'])
@pytest.mark.parametrize('geometry', ['leading', 'equal-maps'])
def test_zero_delta_and_detached_preview_preserve_raw_scalars_all_objects_redo_locked(edge, role, geometry):
    editor = affine_editor(geometry=geometry); editor.update_clip('other', gain_db=-4); editor.undo()
    editor.project.track_edit_locks = [True, False, True, False]
    editor.project.master_gain_db = -0.0; editor.project.media[0].duration = 1000
    editor._clip('v').gain_db = 7
    before = snapshot(editor); original = editor._clip(role)
    candidate = linked_av_trim_candidate(editor.project, role, edge, 0)
    candidate.clips[0].grade.qualifier.window_keyframes.clear()
    preview = editor.preview_trim_linked_av(role, edge, 0)
    preview[0].motion_keyframes.clear(); preview[1].audio_gain_keyframes.clear()
    assert snapshot(editor) == before and editor.trim_linked_av(role, edge, 0) is original
    assert snapshot(editor) == before and editor.can_redo


@pytest.mark.parametrize('edge,delta', [('left', 9), ('right', -9)])
@pytest.mark.parametrize('geometry', ['leading', 'trailing'])
def test_one_frame_shorter_role_retains_both_independent_partial_tails(edge, delta, geometry):
    editor = affine_editor(Fraction(3, 2), geometry=geometry)
    for clip in editor.project.clips[:2]: clip.fade_in = clip.fade_out = 0
    old = deepcopy(editor.project); editor.trim_linked_av('a', edge, delta)
    assert editor.project == expected_project(old, edge, delta) and editor._clip('v').duration == 1
    assert editor._clip('v').source_out-editor._clip('v').source_in == Fraction(9, 8)
    assert editor._clip('a').duration > 1


@pytest.mark.parametrize('role', ['v', 'a'])
@pytest.mark.parametrize('edge', ['left', 'right'])
@pytest.mark.parametrize('track', [0, 2])
def test_either_role_lock_refuses_nonzero_without_raw_normalization(role, edge, track):
    editor = affine_editor(); editor.set_track_edit_lock(track, True)
    editor.update_clip('other', gain_db=-3); editor.undo(); assert editor.can_redo
    editor.project.master_gain_db = -0.0
    rejected(editor, edge, 1, role=role, match='[Ll]ock')


def test_unrelated_locked_tracks_do_not_refuse_pair_trim():
    editor = affine_editor(); editor.project.track_edit_locks = [False, True, False, True]
    old = deepcopy(editor.project); editor.trim_linked_av('v', 'right', 1)
    assert editor.project == expected_project(old, 'right', 1)


@pytest.mark.parametrize('edge', ['left', 'right'])
@pytest.mark.parametrize('delta', [True, False, 1.0, Fraction(1), '1', None, float('nan'), float('inf'), 2**53, -2**53])
def test_invalid_delta_is_atomic(edge, delta):
    editor = affine_editor(); editor.project.master_gain_db = -0.0
    rejected(editor, edge, delta, match='trim delta')


@pytest.mark.parametrize('edge', ['', 'Left', 'out', None, True, 0, []])
def test_invalid_edge_is_atomic(edge): rejected(affine_editor(), edge, 1, match='edge')


@pytest.mark.parametrize('role', ['v', 'a'])
@pytest.mark.parametrize('edge,delta', [('left', 1), ('right', -1)])
@pytest.mark.parametrize('fade', ['fade_in', 'fade_out'])
def test_each_own_fade_refuses_without_silent_clamp(role, edge, delta, fade):
    editor = affine_editor(); clip = editor._clip(role); setattr(clip, fade, clip.duration)
    rejected(editor, edge, delta, match='fade')
    setattr(clip, fade, clip.duration-1); old = deepcopy(editor.project)
    editor.trim_linked_av(role, edge, delta); assert editor.project == expected_project(old, edge, delta)


@pytest.mark.parametrize('role', ['v', 'a'])
@pytest.mark.parametrize('edge,delta', [('left', -2), ('right', 2)])
def test_each_partner_collision_and_exact_adjacency(role, edge, delta):
    editor = affine_editor(); clip = editor._clip(role)
    neighbor = Clip('neighbor', 'm', clip.track, 0 if edge == 'left' else clip.end+1,
                    0, clip.start-1 if edge == 'left' else 2)
    editor.project.clips.append(neighbor); editor.project.clips.reverse(); editor.project.validate()
    rejected(editor, edge, delta, match='overlap')
    old = deepcopy(editor.project); editor.trim_linked_av('a', edge, -1 if edge == 'left' else 1)
    assert editor.project == expected_project(old, edge, -1 if edge == 'left' else 1)


@pytest.mark.parametrize('kind', ['mode', 'intercept', 'speed', 'missing_media', 'missing_role', 'group', 'enabled_video'])
def test_malformed_or_stale_inplace_project_is_never_repaired(kind):
    editor = affine_editor(); editor.preview_trim_linked_av('a', 'left', 1)
    if kind == 'mode': editor.project.av_links[0] = replace(editor.project.av_links[0], alignment='future-mode')
    elif kind == 'intercept': editor._clip('a').start += 1
    elif kind == 'speed':
        clip = editor._clip('a'); clip.time_map = ClipTimeMap(clip.source_in, clip.source_out, 2)
    elif kind == 'missing_media': editor.project.media.clear()
    elif kind == 'missing_role': editor.project.clips.remove(editor._clip('a'))
    elif kind == 'group': editor.project.clip_groups = [ClipGroup('g', ['v', 'a'])]
    else: editor._clip('v').source_audio_enabled = True
    editor.project.master_gain_db = -0.0; rejected(editor, 'left', 1)
    rejected(editor, 'right', 0)


def test_commit_requalifies_current_lock_and_preserves_unrelated_new_edit():
    editor = affine_editor(); editor.preview_trim_linked_av('v', 'right', 1)
    editor.set_track_edit_lock(2, True); rejected(editor, 'right', 1, match='[Ll]ock')
    editor.set_track_edit_lock(2, False); editor.update_clip('other', gain_db=-9)
    old = deepcopy(editor.project); editor.trim_linked_av('v', 'right', 1)
    assert editor.project == expected_project(old, 'right', 1) and editor._clip('other').gain_db == -9


@pytest.mark.parametrize('muted', [False, True])
def test_potential_audio_tie_order_refuses_crossing_even_muted(muted):
    editor = affine_editor(); audio = editor._clip('a'); other = editor._clip('other')
    audio.muted = muted; other.muted = muted; other.start = audio.start+2
    other.audio_mix_anchor = deepcopy(audio.audio_mix_anchor); editor.project.validate()
    rejected(editor, 'left', 3, match='historical audio mixing order')
    for edge in ('left', 'right'):
        old = deepcopy(editor.project); editor.trim_linked_av('a', edge, 1)
        assert editor.project == expected_project(old, edge, 1)
    assert editor._clip('v').source_audio_enabled is False


def test_suppressed_tied_picture_does_not_invent_an_audible_order_constraint():
    editor = affine_editor(); other = editor._clip('other'); other.start = 5
    other.audio_mix_anchor = deepcopy(editor._clip('a').audio_mix_anchor)
    other.source_audio_enabled = False; editor.project.validate(); old = deepcopy(editor.project)
    editor.trim_linked_av('v', 'left', 3); assert editor.project == expected_project(old, 'left', 3)


@pytest.mark.parametrize('kind,role', [('motion', 'v'), ('window', 'v'), ('window', 'a'), ('gain', 'v'), ('gain', 'a')])
@pytest.mark.parametrize('delta', [-1, 1])
def test_every_hidden_key_overflow_is_atomic_but_right_does_not_rebase(kind, role, delta):
    editor = affine_editor(); clip = editor._clip(role)
    if kind == 'motion': clip.motion_keyframes = [ClipMotionKeyframe(-MAX_MOTION_FRAME if delta > 0 else MAX_MOTION_FRAME)]
    elif kind == 'window': clip.grade.qualifier.window_keyframes = [WindowKeyframe(-MAX_WINDOW_FRAME if delta > 0 else MAX_WINDOW_FRAME)]
    else: clip.audio_gain_keyframes = [AudioGainKeyframe(-MAX_AUDIO_GAIN_SAMPLE if delta > 0 else MAX_AUDIO_GAIN_SAMPLE, 0)]
    rejected(editor, 'left', delta)
    old = deepcopy(editor.project); editor.trim_linked_av(role, 'right', delta)
    assert editor.project == expected_project(old, 'right', delta)


@pytest.mark.parametrize('edge', ['left', 'right'])
def test_wrong_thread_preview_and_commit_refuse_before_any_candidate_even_noop(edge, monkeypatch):
    editor = affine_editor(); before = snapshot(editor); errors = []
    import lumen.linked_av_trim as module
    original = module.linked_av_trim_candidate
    calls = []
    def observed(*args): calls.append(args); return original(*args)
    monkeypatch.setattr(module, 'linked_av_trim_candidate', observed)
    def worker():
        for action in (editor.preview_trim_linked_av, editor.trim_linked_av):
            for delta in (0, 1):
                try: action('a', edge, delta)
                except ValueError as error: errors.append(str(error))
    thread = Thread(target=worker); thread.start(); thread.join()
    assert len(errors) == 4 and all('owning thread' in error for error in errors)
    assert not calls and snapshot(editor) == before
    detached = []
    thread = Thread(target=lambda: detached.append(original(editor.project, 'a', edge, 1)))
    thread.start(); thread.join()
    assert detached == [expected_project(editor.project, edge, 1)] and snapshot(editor) == before


def test_generic_fence_and_v2_split_joint_roll_refusals_stay_in_force():
    editor = affine_editor(); before = state(editor)
    def forged():
        expected = expected_project(editor.project, 'left', 1)
        for current, proposed in zip(editor.project.clips, expected.clips):
            for name in ('source_in', 'time_map', 'start', 'duration', 'grade', 'motion_keyframes', 'audio_gain_keyframes'):
                setattr(current, name, getattr(proposed, name))
    with pytest.raises(ValueError, match='Unlink'): editor._commit(forged)
    assert state(editor) == before
    for role in ('v', 'a'):
        for action in (lambda: editor.trim_edge(role, 'left', 1),
                       lambda: editor.split_clip(role, editor._clip(role).start+2)):
            with pytest.raises(ValueError): action()
            assert state(editor) == before
    # Dedicated 1x affine split is now separately qualified, never generic split.
    # This fixture has enabled effects, so retain its independent picture refusal.
    with pytest.raises(ValueError, match='active effects'):
        qualify_linked_av_split(editor.project, 'v', 10)
    assert state(editor) == before
    with pytest.raises(ValueError): editor.roll_linked_av(('v', 'a', 'uv', 'ua'), 15)
    assert state(editor) == before


@pytest.mark.parametrize('short_role', ['v', 'a'])
@pytest.mark.parametrize('edge', ['left', 'right'])
def test_either_role_cannot_become_empty(short_role, edge):
    editor = affine_editor()
    if short_role == 'a':
        audio = editor._clip('a'); audio.duration = 6
        audio.time_map = ClipTimeMap(audio.source_in, audio.source_in+Fraction(11, 2))
    length = editor._clip(short_role).duration
    rejected(editor, edge, length if edge == 'left' else -length, match='one timeline frame')


def test_own_timeline_zero_source_zero_and_eof_boundaries():
    editor = affine_editor(); rejected(editor, 'left', -4, match='trim start')
    for clip in editor.project.clips[:2]:
        origin = Fraction(clip.start-2)
        clip.source_in = origin
        clip.time_map = ClipTimeMap(origin, origin+clip.duration-(Fraction(1, 4) if clip.id == 'v' else Fraction(1, 2)))
    editor.project.validate(); old = deepcopy(editor.project)
    editor.trim_linked_av('a', 'left', -1)
    assert editor.project == expected_project(old, 'left', -1) and editor._clip('a').source_in == 0
    rejected(editor, 'left', -1, match='rational bounds')
    editor = affine_editor(); bound = editor.project.media_frames(editor.project.media[0])
    shift = bound-max(editor._clip(role).source_out for role in ('v', 'a'))
    for clip in editor.project.clips[:2]:
        clip.time_map = ClipTimeMap(clip.source_in+shift, clip.source_out+shift)
        clip.source_in = clip.time_map.source_in
    editor.project.validate()
    assert editor._clip('a').source_out == bound and editor._clip('v').source_out < bound
    rejected(editor, 'right', 1, match='source media')


@pytest.mark.parametrize('edge', ['left', 'right'])
def test_exact_fraction_denominator_overflow_is_refused_for_v2(edge):
    speed = Fraction(999978, 999979)
    origin = Fraction(9999831, 999983) if edge == 'left' else Fraction(10)
    endpoint = Fraction(49999151, 999983) if edge == 'right' else Fraction(50)
    editor = sample(mapping=ClipTimeMap(origin, endpoint, speed))
    editor.project.av_links = [AVLink('pair', 'v', 'a', 'm', AV_LINK_AFFINE_ALIGNMENT)]
    editor.project.validate(); rejected(editor, edge, 1, match='rational bounds')


def test_source_numerator_and_own_timeline_end_overflow_are_atomic():
    endpoint = Fraction(MAX_SOURCE_NUMERATOR, 1000000)
    editor = affine_editor(Fraction(4), Fraction(1000), 'equal-maps')
    editor.project.media[0].duration = 100000000
    for clip in editor.project.clips[:2]:
        clip.time_map, clip.source_in, clip.duration = ClipTimeMap(endpoint-76, endpoint, 4), endpoint-76, 19
    editor.project.validate(); rejected(editor, 'right', 1, match='rational bounds')
    editor = affine_editor(); shift = MAX_CAPTION_FRAME-max(editor._clip(role).end for role in ('v', 'a'))
    for clip in editor.project.clips[:2]: clip.start += shift
    editor.project.validate(); rejected(editor, 'right', 1, match='trim end')


@pytest.mark.parametrize('role', ['unknown', 'other'])
def test_unknown_or_unlinked_role_refuses_without_raw_redo_changes(role):
    editor = affine_editor(); editor.update_clip('other', gain_db=-3); editor.undo()
    editor.project.master_gain_db = -0.0; editor._clip('a').gain_db = -4
    rejected(editor, 'right', 1, role=role, match='existing linked A/V pair')
    assert editor.can_redo


def test_independent_trim_unlink_timing_clamp_and_audio_translation_are_not_bypasses(monkeypatch):
    import lumen.clip_audio_translation as translation
    editor = affine_editor(); old = deepcopy(editor.project)
    def forbidden(*args, **kwargs): pytest.fail('Paired trim must use only its existing exact-candidate transaction')
    for name in ('trim_edge', 'preview_trim_edge', 'trim_clip_left', 'unlink_av_clips', 'link_av_clips', '_set_clip_timing'):
        monkeypatch.setattr(editor, name, forbidden)
    monkeypatch.setattr(translation, 'guard_local_audio_translation', forbidden)
    monkeypatch.setattr(translation, 'guard_common_audio_translation', forbidden)
    editor.trim_linked_av('v', 'left', 1)
    assert editor.project == expected_project(old, 'left', 1)

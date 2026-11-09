"""Affine source slip: independent OLD + selected request oracle, no renderer.

Only the two exact source maps move. These assertions do not use candidate,
normalization, affine-relation or timing-rebase helpers to build expectations.
"""
from native_path_fixture import bind_missing_project_paths
from copy import deepcopy
from dataclasses import asdict, replace
from fractions import Fraction
import json

import pytest

from lumen.linked_av import linked_av_slip_candidate, plan_linked_av_slip
from lumen.model import AVLink, AV_LINK_AFFINE_ALIGNMENT, AV_LINK_ALIGNMENT, Clip, Project, VERSION
from lumen.timing import ClipTimeMap
from test_linked_av_model import sample, state


def wire(value):
    return json.dumps(asdict(value), default=str, sort_keys=True, separators=(',', ':'))


def affine_editor(speed=Fraction(1), fps=Fraction(24), geometry='leading'):
    origin = Fraction(161, 4)
    editor = sample(fps, ClipTimeMap(origin, origin + 10*speed-speed/4, speed))
    video, audio = editor._clip('v'), editor._clip('a')
    video.start = 10
    audio.start, audio.duration = {'leading': (8, 15), 'trailing': (12, 12),
        'equal-edges': (10, 10), 'equal-maps': (10, 10), 'disjoint': (30, 12)}[geometry]
    audio_origin = origin + (audio.start-video.start)*speed
    audio_tail = speed/4 if geometry == 'equal-maps' else speed/2
    audio.time_map = ClipTimeMap(audio_origin, audio_origin+audio.duration*speed-audio_tail, speed)
    audio.source_in = audio_origin
    audio.muted = True
    editor.project.av_links = [AVLink('pair', 'v', 'a', 'm', AV_LINK_AFFINE_ALIGNMENT)]
    # Unrelated strict pair must remain byte-for-byte and in the same list slot.
    editor.project.clips.extend([Clip('uv', 'm', 1, 120, 20, 5, source_audio_enabled=False),
                                 Clip('ua', 'm', 3, 120, 20, 5)])
    editor.project.av_links.append(AVLink('unrelated', 'uv', 'ua', 'm'))
    editor.project.validate()
    return editor


def expected_project(old, role, requested):
    expected = deepcopy(old)
    previous = {clip.id: clip for clip in old.clips}
    delta = Fraction(requested) - previous[role].timing.source_in
    if not delta:
        return expected
    for clip in expected.clips:
        if clip.id not in ('v', 'a'):
            continue
        mapping = previous[clip.id].timing
        origin, endpoint = mapping.source_in+delta, mapping.source_out+delta
        clip.source_in, clip.time_map = origin, ClipTimeMap(origin, endpoint, mapping.speed)
        # Independent legacy wire encoding rule; never call normalization.
        if mapping.speed == 1 and origin.denominator == 1 and endpoint-origin == clip.duration:
            clip.source_in, clip.time_map = int(origin), None
    return expected


def assert_atomic_rejection(editor, role, request):
    before, raw = state(editor), wire(editor.project)
    project, clips, links = editor.project, tuple(editor.project.clips), tuple(editor.project.av_links)
    for action in (lambda: linked_av_slip_candidate(editor.project, role, request),
                   lambda: plan_linked_av_slip(editor, role, request),
                   lambda: editor.slip_linked_av(role, request)):
        with pytest.raises(ValueError):
            action()
        assert state(editor) == before and wire(editor.project) == raw
        assert editor.project is project
        assert all(a is b for a, b in zip(clips, editor.project.clips))
        assert all(a is b for a, b in zip(links, editor.project.av_links))


@pytest.mark.parametrize('speed', [Fraction(1,4), Fraction(2,3), Fraction(1), Fraction(3,2), Fraction(4)])
@pytest.mark.parametrize('fps', [Fraction(24), Fraction(24000,1001), Fraction(30000,1001), Fraction(60000,1001)])
@pytest.mark.parametrize('geometry', ['leading', 'trailing', 'equal-edges'])
@pytest.mark.parametrize('role', ['v', 'a'])
@pytest.mark.parametrize('delta', [Fraction(-3,2), Fraction(0), Fraction(7,3)])
def test_common_exact_source_delta_keeps_each_tail_and_every_other_field(speed, fps, geometry, role, delta):
    editor = affine_editor(speed, fps, geometry)
    editor.update_clip('other', gain_db=-6); editor.undo()
    old, before = deepcopy(editor.project), state(editor)
    requested = editor._clip(role).timing.source_in + delta
    expected = expected_project(old, role, requested)
    identities = tuple(editor.project.clips), tuple(editor.project.av_links)
    candidate = linked_av_slip_candidate(editor.project, role, str(requested))
    assert candidate == expected and state(editor) == before
    assert all(a is not b for a, b in zip(candidate.clips, editor.project.clips))
    result = editor.slip_linked_av(role, str(requested))
    assert result is editor._clip(role) and editor.project == expected
    assert wire(editor.project) == wire(expected)
    assert all(a is b for a, b in zip(identities[0], editor.project.clips))
    assert all(a is b for a, b in zip(identities[1], editor.project.av_links))
    old_clips = {clip.id: clip for clip in old.clips}
    intercepts = []
    for clip in editor.project.clips[:2]:
        previous, mapping = old_clips[clip.id], clip.timing
        assert (clip.start, clip.duration, clip.end) == (previous.start, previous.duration, previous.end)
        assert mapping.source_in == previous.timing.source_in+delta
        assert mapping.source_out == previous.source_out+delta
        assert mapping.source_out-mapping.source_in == previous.source_out-previous.source_in
        assert clip.duration*speed-(mapping.source_out-mapping.source_in) == previous.duration*speed-(previous.source_out-previous.source_in)
        assert mapping.source_in-clip.start*speed == previous.source_in-previous.start*speed+delta
        intercepts.append(mapping.source_in-clip.start*speed)
        stripped_old, stripped_new = asdict(previous), asdict(clip)
        for field in ('source_in', 'time_map'):
            stripped_old.pop(field); stripped_new.pop(field)
        assert stripped_new == stripped_old
    assert intercepts[0] == intercepts[1]
    assert editor.project.duration_frames == old.duration_frames
    assert editor.project.av_links[0].alignment == AV_LINK_AFFINE_ALIGNMENT
    assert Project.from_dict(editor.project.to_dict()) == expected and VERSION == 43
    if delta:
        assert editor.revision == before[3]+1 and len(editor._undo) == len(before[1])+1
        assert not editor.can_redo
        assert editor.undo() and editor.project == old
        assert editor.redo() and editor.project == expected
    else:
        assert state(editor) == before and editor.can_redo


@pytest.mark.parametrize('geometry', ['equal-maps', 'disjoint'])
@pytest.mark.parametrize('role', ['v', 'a'])
def test_no_overlap_requirement_or_implicit_v2_downgrade(geometry, role):
    editor = affine_editor(Fraction(3,2), geometry=geometry)
    old = deepcopy(editor.project)
    requested = editor._clip(role).source_in+1
    editor.slip_linked_av(role, requested)
    assert editor.project == expected_project(old, role, requested)
    assert editor.project.av_links[0].alignment == AV_LINK_AFFINE_ALIGNMENT


@pytest.mark.parametrize('requested', [50, Fraction(101,2), '50.125', '100/2'])
@pytest.mark.parametrize('role', ['v', 'a'])
def test_request_is_selected_role_origin_not_shared_origin_or_timeline_delta(requested, role):
    editor = affine_editor(Fraction(3,2))
    old = deepcopy(editor.project)
    editor.slip_linked_av(role, requested)
    assert editor.project == expected_project(old, role, requested)
    assert editor._clip(role).source_in == Fraction(requested)
    assert editor._clip('v').source_in-editor._clip('a').source_in == 3


def test_documented_independent_tail_counterexample():
    editor = affine_editor(Fraction(3,2))
    video, audio = editor._clip('v'), editor._clip('a')
    video.source_in, video.time_map = Fraction(20), ClipTimeMap(20, Fraction(139,4), Fraction(3,2))
    audio.duration = 12
    audio.source_in, audio.time_map = Fraction(17), ClipTimeMap(17, 35, Fraction(3,2))
    editor.project.validate()
    editor.slip_linked_av('a', '37/2')
    assert video.timing == ClipTimeMap(Fraction(43,2), Fraction(145,4), Fraction(3,2))
    assert audio.timing == ClipTimeMap(Fraction(37,2), Fraction(73,2), Fraction(3,2))


@pytest.mark.parametrize('requested', [-1, True, 1.25, '1/1000001', '999999999999999999999999999', None, {}, '999999'])
@pytest.mark.parametrize('role', ['v', 'a'])
def test_invalid_exact_requests_reject_atomically(requested, role):
    editor = affine_editor(); editor.update_clip('other', gain_db=-3); editor.undo()
    editor.project.master_gain_db = -0.0
    assert_atomic_rejection(editor, role, requested)


@pytest.mark.parametrize('case', ['partner-negative', 'partner-eof', 'selected-eof', 'partner-denominator'])
def test_individually_legal_request_does_not_override_both_role_bounds(case):
    editor = affine_editor()
    role = 'v'
    if case == 'partner-negative':
        request = 0
    elif case == 'partner-eof':
        video = editor._clip('v')
        request = editor.project.media_frames(editor.project.media[0])-(video.source_out-video.source_in)
        assert request+video.source_out-video.source_in == editor.project.media_frames(editor.project.media[0])
    elif case == 'selected-eof':
        request = editor.project.media_frames(editor.project.media[0])
    else:
        # Both old maps are bounded, and N is bounded, but N + partner offset
        # has denominator 1009*1013 > 1,000,000. No rounding/clamping is legal.
        speed = Fraction(1009,1013)
        editor = affine_editor(speed)
        for role, origin, span in [('v', Fraction(40), 9), ('a', 40-2*speed, 14)]:
            clip = editor._clip(role)
            clip.time_map, clip.source_in = ClipTimeMap(origin, origin+span, speed), origin
        editor.project.validate()
        role, request = 'v', Fraction(50000,1009)
        assert (request+9).denominator <= 1_000_000
        assert (request-2*speed).denominator > 1_000_000
    assert_atomic_rejection(editor, role, request)


@pytest.mark.parametrize('mutation', ['unknown-mode', 'missing-role', 'bad-intercept', 'bad-duration', 'mirror', 'ownership'])
def test_malformed_original_state_is_rejected_without_live_normalization(mutation):
    editor = affine_editor(); editor.update_clip('other', gain_db=-3); editor.undo()
    editor.project.master_gain_db = -0.0
    if mutation == 'unknown-mode': editor.project.av_links[0] = replace(editor.project.av_links[0], alignment='unknown')
    elif mutation == 'missing-role': editor.project.av_links[0] = replace(editor.project.av_links[0], audio_clip_id='missing')
    elif mutation == 'bad-intercept': editor._clip('a').start += 1
    elif mutation == 'bad-duration': editor._clip('a').duration += 1
    elif mutation == 'mirror': editor._clip('a').source_in += 1
    else: editor._clip('v').source_audio_enabled = True
    assert_atomic_rejection(editor, 'v', 50)


@pytest.mark.parametrize('track', [0, 1, 2, 3])
def test_only_changed_pair_tracks_need_be_unlocked(track):
    editor = affine_editor(); editor.set_track_edit_lock(track, True)
    old = deepcopy(editor.project)
    if track in (0, 2):
        assert_atomic_rejection(editor, 'a', 50)
    else:
        editor.slip_linked_av('a', 50)
        assert editor.project == expected_project(old, 'a', 50)


@pytest.mark.parametrize('mode', [AV_LINK_ALIGNMENT, AV_LINK_AFFINE_ALIGNMENT])
@pytest.mark.parametrize('role', ['v', 'a'])
def test_true_noop_preserves_raw_scalars_record_identity_locks_history_and_redo(mode, role):
    editor = affine_editor(geometry='equal-maps')
    editor.project.av_links[0] = replace(editor.project.av_links[0], alignment=mode)
    editor.set_track_edit_lock(0, True); editor.set_track_edit_lock(2, True)
    editor.update_clip('other', gain_db=-3); editor.undo()
    editor.project.master_gain_db = -0.0
    editor._clip('a').gain_db = -4  # Accepted integer, not normalized to float.
    before, raw = state(editor), wire(editor.project)
    project, clips, links = editor.project, tuple(editor.project.clips), tuple(editor.project.av_links)
    result = editor.slip_linked_av(role, str(editor._clip(role).source_in))
    assert result is editor._clip(role) and editor.project is project
    assert state(editor) == before and wire(editor.project) == raw and editor.can_redo
    assert all(a is b for a, b in zip(clips, editor.project.clips))
    assert all(a is b for a, b in zip(links, editor.project.av_links))


def test_save_load_preserves_affine_mode_and_both_independent_tails(tmp_path):
    editor = affine_editor(Fraction(2,3), Fraction(30000,1001)); bind_missing_project_paths(editor.project, tmp_path)
    old = deepcopy(editor.project)
    editor.slip_linked_av('a', '51.125')
    path = tmp_path/'affine-slip.lumen'
    editor.project.save(path)
    assert Project.load(path) == expected_project(old, 'a', '51.125')


@pytest.mark.parametrize('mode', [AV_LINK_ALIGNMENT, AV_LINK_AFFINE_ALIGNMENT])
def test_equivalent_exact_request_strings_are_canonical_noops_without_live_normalization(mode):
    editor = affine_editor(geometry='equal-maps')
    editor.project.av_links[0] = replace(editor.project.av_links[0], alignment=mode)
    for clip in editor.project.clips[:2]:
        clip.source_in = Fraction(1,2)
        clip.time_map = ClipTimeMap(Fraction(1,2), Fraction(41,4))
    editor.project.validate()
    editor.project.master_gain_db = -0.0
    editor._clip('a').gain_db = -4
    before, raw = state(editor), wire(editor.project)
    first = plan_linked_av_slip(editor, 'a', '2/4')
    second = plan_linked_av_slip(editor, 'a', '1/2')
    assert first.source_in == second.source_in == '1/2'
    assert first.candidate_signature == second.candidate_signature
    assert state(editor) == before and wire(editor.project) == raw
    editor.slip_linked_av('a', '2/4')
    assert state(editor) == before and wire(editor.project) == raw

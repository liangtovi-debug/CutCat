"""Headless J/L audio-only roll: OLD/request oracle and exact atomic fences.

The expected project is constructed directly from the prior records. No
production candidate, timing-rebase, synchronization or downgrade helper is
used to derive the oracle. Real PCM/video rendering is a separate gate.
"""
from native_path_fixture import bind_missing_project_paths
from copy import deepcopy
from dataclasses import asdict, replace
from fractions import Fraction
from itertools import permutations
import json

import pytest

from lumen.audio_gain import AudioGainKeyframe, MAX_AUDIO_GAIN_SAMPLE
from lumen.clip_motion import ClipMotionKeyframe, MAX_MOTION_FRAME
from lumen.qualifier import MAX_WINDOW_FRAME, WindowKeyframe
from lumen.linked_av_audio_roll import (linked_audio_align_candidate,
    linked_audio_roll_candidate, resolve_linked_audio_roll)
from lumen.model import AV_LINK_AFFINE_ALIGNMENT, AV_LINK_ALIGNMENT, Project
from lumen.timing import ClipTimeMap
from test_linked_av_model import state
from test_linked_av_roll_model import IDS, boundary, editor_for


def wire(value):
    return json.dumps(asdict(value), default=str, sort_keys=True, separators=(',', ':'))


def expected_project(old, cut, *, align=False):
    """Hand-derived two-A-record algebra, preserving all V/unrelated payload."""
    expected = deepcopy(old)
    originals = {clip.id: clip for clip in old.clips}
    delta = cut - originals['la'].end
    if delta == 0 and not align:
        return expected
    if delta:
        for clip in expected.clips:
            if clip.id not in ('la', 'ra'):
                continue
            previous = originals[clip.id]
            origin, endpoint, speed = previous.timing.source_in, previous.source_out, previous.speed
            if clip.id == 'la':
                endpoint += delta * speed
                clip.duration += delta
            else:
                origin += delta * speed
                clip.start, clip.duration = cut, previous.duration - delta
                sample_delta = boundary(cut, old.fps) - boundary(previous.start, old.fps)
                clip.motion_keyframes = [replace(key, frame=key.frame-delta)
                                        for key in previous.motion_keyframes]
                clip.grade.qualifier.window_keyframes = [replace(key, frame=key.frame-delta)
                    for key in previous.grade.qualifier.window_keyframes]
                clip.audio_gain_keyframes = [replace(key, sample=key.sample-sample_delta)
                                             for key in previous.audio_gain_keyframes]
            clip.source_in, clip.time_map = origin, ClipTimeMap(origin, endpoint, speed)
            if speed == 1 and origin.denominator == 1 and endpoint-origin == clip.duration:
                clip.source_in, clip.time_map = int(origin), None
    clips = {clip.id: clip for clip in expected.clips}
    for index, link in enumerate(expected.av_links):
        if link.id not in ('left-link', 'right-link'):
            continue
        video, audio = (clips[key] for key in link.clip_ids)
        strict = ((video.start, video.duration, video.timing)
                  == (audio.start, audio.duration, audio.timing))
        mode = AV_LINK_ALIGNMENT if align and strict else AV_LINK_AFFINE_ALIGNMENT
        expected.av_links[index] = replace(link, alignment=mode)
    return expected


def promote(project):
    project.av_links = [replace(link, alignment=AV_LINK_AFFINE_ALIGNMENT)
                        for link in project.av_links]


def rejected(editor, cut, ids=IDS, *, align=False, match=None):
    before, original_wire = state(editor), wire(editor.project)
    project, clips, links = editor.project, tuple(editor.project.clips), tuple(editor.project.av_links)
    actions = ((lambda: linked_audio_align_candidate(editor.project, ids),
                lambda: editor.preview_align_linked_audio(ids),
                lambda: editor.align_linked_audio(ids)) if align else
               (lambda: linked_audio_roll_candidate(editor.project, ids, cut),
                lambda: editor.preview_roll_linked_audio(ids, cut),
                lambda: editor.roll_linked_audio(ids, cut)))
    for action in actions:
        with pytest.raises(ValueError, match=match):
            action()
        assert state(editor) == before and wire(editor.project) == original_wire
        assert editor.project is project
        assert all(a is b for a, b in zip(editor.project.clips, clips))
        assert all(a is b for a, b in zip(editor.project.av_links, links))


@pytest.mark.parametrize('left_speed', [Fraction(1, 4), Fraction(1), Fraction(3, 2), Fraction(4)])
@pytest.mark.parametrize('right_speed', [Fraction(2, 3), Fraction(1)])
@pytest.mark.parametrize('fps', [Fraction(24), Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001)])
@pytest.mark.parametrize('delta', [-3, 2])
def test_exact_audio_maps_rational_rates_hidden_keys_and_unmodified_picture(left_speed, right_speed, fps, delta):
    editor = editor_for(left_speed, right_speed, fps)
    # Fractional origins as well as partial tails: do not only test integer O.
    for key in IDS:
        clip = editor._clip(key); mapping = clip.timing
        offset = Fraction(1, 3) if key in ('lv', 'la') else Fraction(2, 5)
        clip.time_map = ClipTimeMap(mapping.source_in+offset, mapping.source_out+offset, mapping.speed)
        clip.source_in = clip.time_map.source_in
    editor.project.validate()
    old, before = deepcopy(editor.project), state(editor)
    cut = editor._clip('la').end + delta
    expected = expected_project(old, cut)
    identities = {clip.id: clip for clip in editor.project.clips}
    picture_bytes = {key: wire(identities[key]) for key in ('lv', 'rv')}
    preview = editor.preview_roll_linked_audio(IDS, cut)
    assert tuple(clip.id for clip in preview) == IDS
    assert preview == tuple(next(c for c in expected.clips if c.id == key) for key in IDS)
    assert state(editor) == before and all(clip is not identities[clip.id] for clip in preview)
    result = editor.roll_linked_audio(tuple(reversed(IDS)), cut)
    assert tuple(clip.id for clip in result) == IDS and editor.project == expected
    assert all(clip is editor._clip(clip.id) for clip in result)
    for clip in editor.project.clips:
        if clip.id not in ('la', 'ra'):
            assert clip is identities[clip.id]
    for key in ('lv', 'rv'):
        assert wire(editor._clip(key)) == picture_bytes[key]
    for key in ('la', 'ra'):
        previous, current = next(c for c in old.clips if c.id == key), editor._clip(key)
        assert (current.duration*current.speed-(current.source_out-current.source_in)
                == previous.duration*previous.speed-(previous.source_out-previous.source_in)
                == previous.speed/4)
        assert current.audio_mix_anchor == previous.audio_mix_anchor
        assert (current.muted, current.gain_db, current.source_audio_enabled,
                current.fade_in, current.fade_out) == (previous.muted, previous.gain_db,
                previous.source_audio_enabled, previous.fade_in, previous.fade_out)
    assert editor.project.duration_frames == old.duration_frames
    assert all(link.alignment == AV_LINK_AFFINE_ALIGNMENT for link in editor.project.av_links)
    assert editor.revision == before[3]+1 and len(editor._undo) == len(before[1])+1
    assert Project.from_dict(editor.project.to_dict()) == expected
    assert editor.undo() and editor.project == old
    assert editor.redo() and editor.project == expected


@pytest.mark.parametrize('ids', list(permutations(IDS)))
def test_selection_permutations_and_unsorted_arrays_resolve_by_picture_order(ids):
    editor = editor_for()
    editor.project.clips.reverse(); editor.project.av_links.reverse()
    old = deepcopy(editor.project)
    pairs = resolve_linked_audio_roll(editor.project, ids)
    assert tuple(link.id for link in pairs) == ('left-link', 'right-link')
    result = editor.roll_linked_audio(ids, 28)
    assert tuple(clip.id for clip in result) == IDS
    assert (result[0].end, result[2].start) == (27, 27)
    assert (result[1].end, result[3].start) == (28, 28)
    assert editor.project == expected_project(old, 28)


@pytest.mark.parametrize('existing_v2', [False, True])
def test_zero_delta_preserves_mode_all_objects_history_and_redo(existing_v2):
    editor = editor_for()
    if existing_v2:
        editor.roll_linked_audio(IDS, 29)
    editor.update_clip('other', gain_db=-4); editor.undo()
    original = tuple(editor._clip(key) for key in IDS)
    links = tuple(editor.project.av_links)
    before, project = state(editor), editor.project
    cut = editor._clip('la').end
    preview = editor.preview_roll_linked_audio(IDS, cut)
    preview[0].grade.qualifier.window_keyframes.clear()
    preview[3].audio_gain_keyframes.clear()
    assert state(editor) == before
    result = editor.roll_linked_audio(IDS, cut)
    assert all(a is b for a, b in zip(result, original))
    assert all(a is b for a, b in zip(editor.project.av_links, links))
    assert editor.project is project and state(editor) == before and editor.can_redo


@pytest.mark.parametrize('operation', ['preview', 'noop', 'invalid', 'align-preview', 'align-noop'])
def test_pure_preview_and_rejected_or_noop_requests_do_not_normalize_live_scalars(operation):
    editor = editor_for()
    editor.project.master_gain_db = -2
    before = state(editor)
    if operation == 'preview': editor.preview_roll_linked_audio(IDS, 28)
    elif operation == 'noop': editor.roll_linked_audio(IDS, 27)
    elif operation == 'invalid': rejected(editor, 7)
    elif operation == 'align-preview': editor.preview_align_linked_audio(IDS)
    else: editor.align_linked_audio(IDS)
    assert type(editor.project.master_gain_db) is int and state(editor) == before


def test_repeated_j_l_j_then_align_one_history_each_and_exact_multi_undo_redo(tmp_path):
    editor = editor_for(); bind_missing_project_paths(editor.project, tmp_path)
    original = deepcopy(editor.project)
    frames = (24, 30, 24)
    snapshots = [original]
    for cut in frames:
        expected = expected_project(snapshots[-1], cut)
        revision, undo_size = editor.revision, len(editor._undo)
        editor.roll_linked_audio(IDS, cut)
        assert editor.project == expected
        assert editor._clip('lv').end == editor._clip('rv').start == 27
        assert editor.revision == revision+1 and len(editor._undo) == undo_size+1
        snapshots.append(deepcopy(editor.project))
    assert snapshots[1] == snapshots[3]
    expected = expected_project(snapshots[-1], 27, align=True)
    before = state(editor)
    preview = editor.preview_align_linked_audio(IDS)
    assert state(editor) == before
    assert preview == tuple(next(c for c in expected.clips if c.id == key) for key in IDS)
    editor.align_linked_audio(IDS)
    assert editor.project == original == expected
    snapshots.append(deepcopy(editor.project))
    path = tmp_path/'audio-roll-schema43.lumen'
    editor.project.save(path); assert Project.load(path) == expected
    for snapshot in reversed(snapshots[:-1]):
        assert editor.undo() and editor.project == snapshot
    for snapshot in snapshots[1:]:
        assert editor.redo() and editor.project == snapshot


def test_regular_return_to_picture_cut_stays_v2_until_explicit_metadata_reset():
    editor = editor_for()
    original = deepcopy(editor.project)
    editor.roll_linked_audio(IDS, 24); editor.roll_linked_audio(IDS, 27)
    aligned = deepcopy(editor.project)
    assert aligned.clips == original.clips
    assert all(link.alignment == AV_LINK_AFFINE_ALIGNMENT for link in aligned.av_links)
    before, identities = state(editor), tuple(editor.project.clips)
    editor.roll_linked_audio(IDS, 27)
    assert state(editor) == before
    editor.align_linked_audio(IDS)
    assert editor.project == original and editor.revision == before[3]+1
    assert all(a is b for a, b in zip(editor.project.clips, identities))
    assert editor.undo() and editor.project == aligned
    assert editor.redo() and editor.project == original


def test_aligned_external_v2_unequal_source_tail_downgrades_only_strict_pair():
    editor = editor_for()
    promote(editor.project)
    audio = editor._clip('la'); mapping = audio.timing
    audio.time_map = ClipTimeMap(mapping.source_in, mapping.source_out-mapping.speed/4, mapping.speed)
    audio.source_in = audio.time_map.source_in
    editor.project.validate()
    original = deepcopy(editor.project)
    assert (editor._clip('lv').start, editor._clip('lv').duration) == (audio.start, audio.duration)
    assert editor._clip('lv').timing != audio.timing
    expected = expected_project(original, 27, align=True)
    identities = tuple(editor.project.clips)
    editor.align_linked_audio(IDS)
    assert editor.project == expected and editor.project.clips == original.clips
    assert [link.alignment for link in editor.project.av_links] == [AV_LINK_AFFINE_ALIGNMENT, AV_LINK_ALIGNMENT]
    assert all(a is b for a, b in zip(editor.project.clips, identities))
    before = state(editor)
    editor.align_linked_audio(IDS)  # Already maximally aligned; no metadata coercion.
    assert state(editor) == before
    assert editor.undo() and editor.project == original


def test_gain_rebase_uses_global_boundary_difference_not_rounded_delta():
    fps = Fraction(30000, 1001)
    editor = editor_for(fps=fps, start=6)
    old = deepcopy(editor.project)
    sample_delta = boundary(27, fps)-boundary(26, fps)
    assert sample_delta != boundary(1, fps)
    editor.roll_linked_audio(IDS, 27)
    previous = next(c for c in old.clips if c.id == 'ra')
    assert editor._clip('ra').audio_gain_keyframes == [replace(key, sample=key.sample-sample_delta)
                                                     for key in previous.audio_gain_keyframes]
    assert editor._clip('rv').audio_gain_keyframes == next(c for c in old.clips if c.id == 'rv').audio_gain_keyframes
    assert editor.project == expected_project(old, 27)


@pytest.mark.parametrize('delta', [-3, 3])
def test_unchanged_picture_fades_and_extreme_hidden_keys_do_not_constrain_audio_cut(delta):
    editor = editor_for()
    editor._clip('lv').fade_out = editor._clip('rv').fade_in = 20
    # A joint roll would shorten a full-length V fade and overflow these RV
    # hidden handles. Audio-only roll must not inspect/rebase unchanged V keys.
    sign = -1 if delta > 0 else 1
    video = editor._clip('rv')
    video.motion_keyframes = [ClipMotionKeyframe(sign*MAX_MOTION_FRAME)]
    video.grade.qualifier.window_keyframes = [WindowKeyframe(sign*MAX_WINDOW_FRAME)]
    video.audio_gain_keyframes = [AudioGainKeyframe(sign*MAX_AUDIO_GAIN_SAMPLE)]
    editor.project.validate()
    old = deepcopy(editor.project)
    identities = {key: editor._clip(key) for key in ('lv', 'rv')}
    picture_bytes = {key: wire(clip) for key, clip in identities.items()}
    editor.roll_linked_audio(IDS, 27+delta)
    assert editor.project == expected_project(old, 27+delta)
    for key in ('lv', 'rv'):
        assert editor._clip(key) is identities[key] and wire(editor._clip(key)) == picture_bytes[key]


@pytest.mark.parametrize('delta', [-19, 19])
def test_one_frame_audio_endpoint_preserves_its_exact_partial_tail(delta):
    editor = editor_for()
    for key in ('la', 'ra'):
        editor._clip(key).fade_in = editor._clip(key).fade_out = 0
    old = deepcopy(editor.project)
    editor.roll_linked_audio(IDS, 27 + delta)
    short = editor._clip('la' if delta < 0 else 'ra')
    assert short.duration == 1 and short.source_out - short.source_in == short.speed * Fraction(3, 4)
    assert editor.project == expected_project(old, 27 + delta)


def test_unrelated_link_records_and_live_clips_keep_identity():
    from lumen.model import AVLink, Clip
    editor = editor_for()
    video = Clip('third-v', 'm', 1, 100, 20, 5, source_audio_enabled=False)
    audio = Clip('third-a', 'm', 3, 100, 20, 5)
    link = AVLink('third-link', video.id, audio.id, 'm')
    editor.project.clips.extend((video, audio)); editor.project.av_links.insert(1, link)
    editor.project.validate(); old = deepcopy(editor.project)
    editor.roll_linked_audio(IDS, 28)
    assert editor.project == expected_project(old, 28)
    assert editor._clip(video.id) is video and editor._clip(audio.id) is audio
    assert editor.project.av_links[1] is link

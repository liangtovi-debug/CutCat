"""Pure, atomic audio-only shared-cut roll and strict alignment reset.

The picture edit never moves. Both A maps and relationship modes are derived
in one detached complete project; no sequential trims, unlink/relink or audio
ownership changes. No source observation, decoding or media worker is involved.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from uuid import uuid4

from .audio_gain import shift_audio_gain_keyframes
from .clip_motion import shift_clip_motion
from .linked_av_roll import _guard_audio_order
from .source_overwrite import _signature
from .timing import ClipTimeMap, duration_frames, sample_boundary
from .window_animation import shift_window_animation


def _resolve(project, clip_ids):
    """Resolve exactly four roles in fixed picture order after validation."""
    from .model import _selected_clip_ids
    selected = set(_selected_clip_ids(project, clip_ids))
    pairs = [link for link in project.av_links if selected.intersection(link.clip_ids)]
    if (len(selected) != 4 or len(pairs) != 2
            or {key for link in pairs for key in link.clip_ids} != selected):
        raise ValueError('Audio-only roll requires exactly two complete linked A/V pairs and no extra clips')
    clips = {clip.id: clip for clip in project.clips}
    pairs.sort(key=lambda link: clips[link.video_clip_id].start)
    lv, la, rv, ra = (clips[key] for link in pairs for key in link.clip_ids)
    if lv.track != rv.track or la.track != ra.track:
        raise ValueError('Audio-only roll requires both video roles on one track and both audio roles on one track')
    if lv.end != rv.start or la.end != ra.start:
        raise ValueError('Audio-only roll requires adjacent picture and audio cuts')
    if lv.start != la.start or rv.end != ra.end:
        raise ValueError('Audio-only roll requires matching outer picture and audio endpoints')
    return tuple(pairs)


def resolve_linked_audio_roll(project, clip_ids):
    candidate = deepcopy(project)
    candidate.validate()
    return _resolve(candidate, clip_ids)


def _candidate(project, clip_ids, cut, *, reset):
    from .model import (AV_LINK_ALIGNMENT, AV_LINK_AFFINE_ALIGNMENT, Editor,
                        MAX_CAPTION_FRAME, _integer, av_link_timing_matches)
    if not reset:
        _integer(cut, 'audio roll cut', 0, MAX_CAPTION_FRAME)
    candidate = deepcopy(project)
    candidate.validate()
    left, right = _resolve(candidate, clip_ids)
    clips = {clip.id: clip for clip in candidate.clips}
    lv, la, rv, ra = (clips[key] for pair in (left, right) for key in pair.clip_ids)
    if reset:
        cut = lv.end
        _integer(cut, 'audio roll cut', 0, MAX_CAPTION_FRAME)
    if not la.start < cut < ra.end:
        raise ValueError('Audio-only roll must leave at least one timeline frame in each audio clip')
    delta = cut - la.end
    if delta == 0 and not reset:
        return candidate  # A regular no-op never normalizes link metadata.
    # Outer endpoints are fixed and must fit the same supported frame bounds.
    _integer(la.start, 'audio roll start', 0, MAX_CAPTION_FRAME)
    _integer(ra.end, 'audio roll end', 1, MAX_CAPTION_FRAME)
    originals = {la.id: deepcopy(la), ra.id: deepcopy(ra)}
    media = {item.id: item for item in candidate.media}
    sample_delta = sample_boundary(cut, candidate.fps) - sample_boundary(la.end, candidate.fps)
    if delta:
        for index, clip in enumerate(candidate.clips):
            if clip.id not in originals:
                continue
            old = originals[clip.id]
            is_right = clip.id == ra.id
            length = old.duration - delta if is_right else old.duration + delta
            if max(old.fade_in, old.fade_out) > length:
                raise ValueError('Audio-only roll would shorten an existing fade; reduce the fade first')
            mapping = old.timing
            rolled = ClipTimeMap(
                mapping.source_in + delta * mapping.speed if is_right else mapping.source_in,
                mapping.source_out if is_right else mapping.source_out + delta * mapping.speed,
                mapping.speed)
            if duration_frames(rolled) != length:
                raise ValueError('Audio-only roll did not preserve the exact timeline boundary')
            if rolled.source_out > candidate.media_frames(media[old.media_id]):
                raise ValueError('Audio-only roll exceeds the available source media range')
            clip.time_map, clip.source_in, clip.duration = rolled, rolled.source_in, length
            clip.start = cut if is_right else old.start
            clip._normalize_legacy_timing()
            if is_right:
                clip.audio_gain_keyframes = shift_audio_gain_keyframes(old.audio_gain_keyframes, sample_delta)
                clip = shift_clip_motion(clip, delta)
                clip.grade = shift_window_animation(clip.grade, delta)
                candidate.clips[index] = clip
    # Reset may downgrade an aligned v2 only when every strict map/tail matches;
    # never copy V over A, which would erase independent gain/mute/anchor history.
    updated = {clip.id: clip for clip in candidate.clips}
    replacements = {}
    for pair in (left, right):
        strict = reset and av_link_timing_matches(updated[pair.video_clip_id], updated[pair.audio_clip_id], AV_LINK_ALIGNMENT)
        replacements[pair.id] = replace(pair, alignment=AV_LINK_ALIGNMENT if strict else AV_LINK_AFFINE_ALIGNMENT)
    candidate.av_links = [replacements.get(link.id, link) for link in candidate.av_links]
    if delta == 0 and candidate.av_links == project.av_links:
        return candidate
    # Central diff-based locks cannot see unchanged V in later v2→v2 rolls.
    # Explicitly require editability of all four involved members, including a
    # metadata-only reset. Locks on unrelated tracks are not relevant.
    if any(project.is_track_edit_locked(clips[key].track)
           for pair in (left, right) for key in pair.clip_ids):
        raise ValueError('Track is edit-locked. Unlock the track before editing it.')
    Editor._guard_track_edit_locks(project, candidate)
    candidate.validate()
    _guard_audio_order(project, candidate, (left.audio_clip_id, right.audio_clip_id))
    return candidate


def linked_audio_roll_candidate(project, clip_ids, cut):
    return _candidate(project, clip_ids, cut, reset=False)


def linked_audio_align_candidate(project, clip_ids):
    return _candidate(project, clip_ids, None, reset=True)


@dataclass(frozen=True)
class LinkedAudioRollPlan:
    session_id: str
    revision: int
    project_identity: int
    project_signature: str
    clip_ids: tuple[str, ...]
    cut: int
    reset: bool
    candidate_signature: str
    token: str


def _plan(editor, clip_ids, cut, *, reset):
    from .model import _selected_clip_ids
    editor._assert_owner()
    selected = tuple(_selected_clip_ids(editor.project, clip_ids))
    candidate = _candidate(editor.project, selected, cut, reset=reset)
    if reset:
        left, _ = _resolve(candidate, selected)
        cut = next(clip.end for clip in candidate.clips if clip.id == left.video_clip_id)
    return LinkedAudioRollPlan(editor.session_id, editor.revision, id(editor.project),
        _signature(editor.project), selected, cut, reset, _signature(candidate), uuid4().hex)


def plan_linked_audio_roll(editor, clip_ids, cut):
    """Pure captured candidate; changing project/session/revision expires Apply."""
    return _plan(editor, clip_ids, cut, reset=False)


def plan_linked_audio_align(editor, clip_ids):
    return _plan(editor, clip_ids, None, reset=True)


def apply_linked_audio_roll(editor, plan):
    """Requalify a captured request, then one exact transaction; never renew it."""
    from .model import MAX_CAPTION_FRAME, _integer, _text
    editor._assert_owner()
    if type(plan) is not LinkedAudioRollPlan or type(plan.reset) is not bool or type(plan.clip_ids) is not tuple:
        raise ValueError('Invalid audio-only roll plan')
    _integer(plan.revision, 'audio roll revision', 0)
    _integer(plan.project_identity, 'audio roll project identity', 1)
    _integer(plan.cut, 'audio roll cut', 0, MAX_CAPTION_FRAME)
    for value in (plan.session_id, plan.project_signature, plan.candidate_signature, plan.token):
        _text(value, 'audio roll plan identity', True)
    key = (editor.session_id, editor.revision)
    if (plan.session_id, plan.revision, plan.project_identity, plan.project_signature) != (
            *key, id(editor.project), _signature(editor.project)):
        raise ValueError('Audio-only roll plan is stale; preview again')
    consumed_key, consumed = getattr(editor, '_linked_audio_roll_consumed_plans', (None, set()))
    if consumed_key != key:
        consumed = set()
    if plan.token in consumed:
        raise ValueError('Audio-only roll plan was already applied; preview again')
    if len(consumed) >= 1024:
        raise ValueError('Audio-only roll no-op receipt budget reached; reopen the project')
    candidate = _candidate(editor.project, plan.clip_ids, plan.cut, reset=plan.reset)
    if plan.reset:
        left, _ = _resolve(candidate, plan.clip_ids)
        if plan.cut != next(clip.end for clip in candidate.clips if clip.id == left.video_clip_id):
            raise ValueError('Audio-only roll plan was changed; preview again')
    if _signature(candidate) != plan.candidate_signature:
        raise ValueError('Audio-only roll candidate changed; preview again')
    result = (editor.align_linked_audio(plan.clip_ids) if plan.reset
              else editor.roll_linked_audio(plan.clip_ids, plan.cut))
    consumed.add(plan.token)
    editor._linked_audio_roll_consumed_plans = (key, consumed)
    return result

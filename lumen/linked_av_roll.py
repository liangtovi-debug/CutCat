"""Atomic non-ripple roll of two existing strict linked A/V pairs.

Only one detached final Project is validated: the old shared cut can otherwise
make a sequential edge trim collide with the other pair. No source inspection,
rendering, new identities, ownership handoff or waveform-conservation claim.
"""
from __future__ import annotations

from copy import deepcopy

from .audio_components import audio_mix_order_key
from .audio_gain import shift_audio_gain_keyframes
from .clip_motion import shift_clip_motion
from .timing import ClipTimeMap, duration_frames, sample_boundary
from .window_animation import shift_window_animation


def _resolve(project, clip_ids):
    """Resolve exact selection against an already validated detached project."""
    from .model import _selected_clip_ids, require_av_link_alignment
    selected = set(_selected_clip_ids(project, clip_ids))
    pairs = [link for link in project.av_links if selected.intersection(link.clip_ids)]
    if (len(selected) != 4 or len(pairs) != 2
            or set(clip_id for link in pairs for clip_id in link.clip_ids) != selected):
        raise ValueError('Joint roll requires exactly two complete linked A/V pairs and no extra clips')
    for link in pairs:
        require_av_link_alignment(link, operation='Joint roll')
    clips = {clip.id: clip for clip in project.clips}
    pairs.sort(key=lambda link: clips[link.video_clip_id].start)
    left, right = pairs
    lv, la, rv, ra = (clips[key] for link in pairs for key in link.clip_ids)
    if lv.track != rv.track or la.track != ra.track:
        raise ValueError('Joint roll requires both video roles on one track and both audio roles on one track')
    if lv.end != rv.start or la.end != ra.start or lv.end != la.end:
        raise ValueError('Joint roll requires one shared adjacent cut on both tracks')
    return left, right


def resolve_linked_av_roll(project, clip_ids):
    """Pure qualification, including exact selection; never canonicalize live data."""
    candidate = deepcopy(project)
    candidate.validate()
    return _resolve(candidate, clip_ids)


def _guard_audio_order(before, after, audio_ids):
    """Full order against every potential contributor, including muted audio.

    Retaining historical anchors is not enough: current start and ID break ties.
    Reject crossings instead of changing anchors or IDs to rescue the request.
    Structurally suppressed video and media without audio are not contributors.
    """
    media = {item.id: item for item in before.media}
    old = {clip.id: clip for clip in before.clips}
    new = {clip.id: clip for clip in after.clips}
    for audio_id in audio_ids:
        old_key, new_key = audio_mix_order_key(old[audio_id]), audio_mix_order_key(new[audio_id])
        for other in before.clips:
            if (other.id == audio_id or not other.source_audio_enabled
                    or not media[other.media_id].has_audio):
                continue
            if ((old_key < audio_mix_order_key(other))
                    != (new_key < audio_mix_order_key(new[other.id]))):
                raise ValueError('Linked A/V roll would change historical audio mixing order; choose another cut')


def linked_av_roll_candidate(project, clip_ids, cut):
    """Derive the final four records together without touching live state.

    For delta = cut - old_cut, the left map becomes [O, E + delta*r),
    the right map [O + delta*r, E). Exact partial-tail residues are retained.
    Right local keys follow the new origin; fades keep edge-anchored lengths.
    """
    from .model import Editor, MAX_CAPTION_FRAME, _integer
    _integer(cut, 'roll cut', 0, MAX_CAPTION_FRAME)
    candidate = deepcopy(project)
    candidate.validate()
    left, right = _resolve(candidate, clip_ids)
    clips = {clip.id: clip for clip in candidate.clips}
    lv, rv = clips[left.video_clip_id], clips[right.video_clip_id]
    if not lv.start < cut < rv.end:
        raise ValueError('Roll must leave at least one timeline frame in each clip.')
    delta = cut - lv.end
    if delta == 0:
        return candidate
    # Check the unchanged outer endpoints too; do not accept an already extreme
    # project merely because the requested inner cut itself fits the bound.
    _integer(lv.start, 'roll start', 0, MAX_CAPTION_FRAME)
    _integer(rv.end, 'roll end', 1, MAX_CAPTION_FRAME)
    originals = {key: deepcopy(clips[key]) for pair in (left, right) for key in pair.clip_ids}
    media = {item.id: item for item in candidate.media}
    right_ids = set(right.clip_ids)
    sample_delta = sample_boundary(cut, candidate.fps) - sample_boundary(lv.end, candidate.fps)
    for index, clip in enumerate(candidate.clips):
        if clip.id not in originals:
            continue
        old = originals[clip.id]
        is_right = clip.id in right_ids
        length = old.duration - delta if is_right else old.duration + delta
        if max(old.fade_in, old.fade_out) > length:
            raise ValueError('Roll would shorten an existing fade. Reduce the fade first.')
        mapping = old.timing
        rolled = ClipTimeMap(
            mapping.source_in + delta * mapping.speed if is_right else mapping.source_in,
            mapping.source_out if is_right else mapping.source_out + delta * mapping.speed,
            mapping.speed)
        if duration_frames(rolled) != length:
            raise ValueError('Roll did not preserve the exact timeline boundary.')
        if rolled.source_out > candidate.media_frames(media[old.media_id]):
            raise ValueError('Roll exceeds the available source media range.')
        # The duration helper clamps fades; assigning only approved timing keeps
        # every payload and fade intact before complete final validation.
        clip.time_map, clip.source_in, clip.duration = rolled, rolled.source_in, length
        clip.start = cut if is_right else old.start
        clip._normalize_legacy_timing()
        if is_right:
            clip.audio_gain_keyframes = shift_audio_gain_keyframes(old.audio_gain_keyframes, sample_delta)
            clip = shift_clip_motion(clip, delta)
            clip.grade = shift_window_animation(clip.grade, delta)
            candidate.clips[index] = clip
    Editor._guard_track_edit_locks(project, candidate)
    candidate.validate()
    _guard_audio_order(project, candidate, (left.audio_clip_id, right.audio_clip_id))
    return candidate

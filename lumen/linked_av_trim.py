"""Exact joint, non-ripple edge trim of one existing v1/v2 same-source A/V pair.

Both roles are derived in a detached project before validation. This changes
source selection and edge-anchored fades, not just timeline placement: neither
retained waveform identity nor retime transient continuity is promised. This
module does no file observation, decoding, rendering or ownership handoff.
"""
from __future__ import annotations

from copy import deepcopy

from .audio_components import audio_mix_order_key
from .audio_gain import shift_audio_gain_keyframes
from .clip_motion import shift_clip_motion
from .timing import ClipTimeMap, duration_frames, sample_boundary
from .window_animation import shift_window_animation


def _guard_audio_order(before, after, audio_id):
    """Preserve the A role's relative potentially audible summation order.

    Muted contributions are checked as unmuted; structural suppression and
    sources without audio are excluded. Historical descendants can share an
    anchor, so merely retaining the anchor is not an ordering proof. Do not
    rewrite anchors or IDs to rescue a trim that changes a tie's order.
    """
    media = {item.id: item for item in before.media}
    old = {clip.id: clip for clip in before.clips}
    new = {clip.id: clip for clip in after.clips}
    old_key = audio_mix_order_key(old[audio_id])
    new_key = audio_mix_order_key(new[audio_id])
    for other in before.clips:
        if (other.id == audio_id or not other.source_audio_enabled
                or not media[other.media_id].has_audio):
            continue
        if ((old_key < audio_mix_order_key(other))
                != (new_key < audio_mix_order_key(new[other.id]))):
            raise ValueError('Linked A/V trim would change historical audio mixing order; choose another edge position')


def linked_av_trim_candidate(project, clip_id, edge, delta):
    """Return a complete candidate without mutating live records or history.

    For map [O,E), speed r and old timeline [t,t+D), left produces
    [O+d*r,E), [t+d,t+D); right produces [O,E+d*r), [t,t+D+d).
    Never rebuild a source endpoint from the rounded output duration.
    """
    from .model import Editor, MAX_CAPTION_FRAME, _integer, _text
    if edge not in ('left', 'right'):
        raise ValueError('Trim edge must be left or right.')
    _integer(delta, 'trim delta', -MAX_CAPTION_FRAME, MAX_CAPTION_FRAME)
    _text(clip_id, 'linked A/V clip id', True)
    # Qualification must not canonicalize values in the live project.
    candidate = deepcopy(project)
    candidate.validate()
    link = next((item for item in candidate.av_links if clip_id in item.clip_ids), None)
    if link is None:
        raise ValueError('Joint edge trim requires an existing linked A/V pair')
    # Detached validation admits only the known strict-v1 / affine-sync-v2
    # relations. This own-map algebra preserves each role's independent tail;
    # do not broaden the legacy alignment guard used by split or joint roll.
    original = {clip.id: deepcopy(clip) for clip in candidate.clips if clip.id in link.clip_ids}
    if delta == 0:
        return candidate
    for index, clip in enumerate(candidate.clips):
        if clip.id not in original:
            continue
        current = original[clip.id]
        length = current.duration - delta if edge == 'left' else current.duration + delta
        if length < 1:
            raise ValueError('Trim must leave at least one timeline frame.')
        start = current.start + delta if edge == 'left' else current.start
        _integer(start, 'trim start', 0, MAX_CAPTION_FRAME)
        _integer(start + length, 'trim end', 1, MAX_CAPTION_FRAME)
        if max(current.fade_in, current.fade_out) > length:
            raise ValueError('Trim would shorten an existing fade. Reduce the fade first.')
        mapping = current.timing
        trimmed = ClipTimeMap(
            mapping.source_in + delta * mapping.speed if edge == 'left' else mapping.source_in,
            mapping.source_out + delta * mapping.speed if edge == 'right' else mapping.source_out,
            mapping.speed,
        )
        if duration_frames(trimmed) != length:
            raise ValueError('Trim did not preserve the exact timeline boundary.')
        source = next(item for item in project.media if item.id == current.media_id)
        if trimmed.source_out > project.media_frames(source):
            raise ValueError('Trim exceeds the available source media range.')
        # Do not call the duration-setting helper: it may silently clamp fades.
        clip.time_map, clip.source_in, clip.duration = trimmed, trimmed.source_in, length
        clip.start = start
        clip._normalize_legacy_timing()
        if edge == 'left' and delta:
            clip.audio_gain_keyframes = shift_audio_gain_keyframes(current.audio_gain_keyframes,
                sample_boundary(start, project.fps) - sample_boundary(current.start, project.fps))
            clip = shift_clip_motion(clip, delta)
            clip.grade = shift_window_animation(clip.grade, delta)
            candidate.clips[index] = clip
    Editor._guard_track_edit_locks(project, candidate)
    candidate.validate()
    if edge == 'left' and delta:
        _guard_audio_order(project, candidate, link.audio_clip_id)
    return candidate

"""Pure, structural cross-dissolve planning; never proof of decoded handles.

Source windows are in project-frame units. The renderer must independently
verify actual decoded video coverage, not merely container duration metadata.
"""
from dataclasses import dataclass, fields
from fractions import Fraction
from typing import Any


@dataclass(frozen=True)
class VideoTransition:
    id: str
    left_clip_id: str
    right_clip_id: str
    duration_frames: int
    kind: str = 'cross_dissolve'

    def __post_init__(self):
        validate_video_transition(self)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) != {f.name for f in fields(cls)}:
            raise ValueError('Video transition requires exactly its schema fields')
        return cls(**data)


def validate_video_transition(value):
    if not isinstance(value, VideoTransition):
        raise ValueError('Invalid video transition')
    for name in ('id', 'left_clip_id', 'right_clip_id'):
        text = getattr(value, name)
        if not isinstance(text, str) or not text.strip() or '\x00' in text:
            raise ValueError('Invalid video transition identifier')
    if value.kind != 'cross_dissolve':
        raise ValueError('Unsupported video transition kind')
    if type(value.duration_frames) is not int or not 2 <= value.duration_frames <= 2**53 - 1:
        raise ValueError('Video transition duration must be an integer of at least 2 frames')
    if value.left_clip_id == value.right_clip_id:
        raise ValueError('Video transition requires two distinct clips')


@dataclass(frozen=True)
class VideoTransitionPlan:
    transition: VideoTransition
    left: Any
    right: Any
    start: int
    end: int
    duration: int
    left_source_start: int
    right_source_start: int
    track: int
    cut: int


def plan_video_transition(project, transition):
    """Check structural constraints only; do not mutate or extend live clips."""
    validate_video_transition(transition)
    clips = {c.id: c for c in project.clips}
    if transition.left_clip_id not in clips or transition.right_clip_id not in clips:
        raise ValueError('Video transition references a missing clip; remove the transition first')
    left, right = clips[transition.left_clip_id], clips[transition.right_clip_id]
    sources = {m.id: m for m in project.media}
    if left.track != right.track or left.track not in (0, 1) or left.end != right.start:
        raise ValueError('Video transition requires adjacent clips on the same video track')
    ordered = sorted((c for c in project.clips if c.track == left.track), key=lambda c: c.start)
    if ordered.index(right) != ordered.index(left) + 1:
        raise ValueError('Video transition requires adjacent clips on the same video track')
    for clip in (left, right):
        if clip.media_id not in sources or not sources[clip.media_id].has_video:
            raise ValueError('Video transition requires video media')
        mapping = clip.timing
        if (mapping.speed != 1 or Fraction(mapping.source_in).denominator != 1
                or Fraction(mapping.source_out).denominator != 1
                or mapping.source_out - mapping.source_in != clip.duration):
            raise ValueError('Video transition requires 1x clips with complete integer source frames')
        if clip.motion_keyframes or clip.grade.qualifier.window_keyframes:
            raise ValueError('Remove clip motion and window animation before adding a video transition')
        if not clip.composite.is_legacy():
            raise ValueError('Video transition requires default opaque compositing')
        if any(effect.enabled and effect.amount > 0 for effect in clip.effects):
            raise ValueError('Disable clip effects before adding a video transition')
        if clip.stabilization.enabled:
            raise ValueError('Disable stabilization before adding a video transition')
    duration = transition.duration_frames
    before = duration // 2
    after = duration - before
    cut = left.end
    start, end = cut - before, cut + after
    if start < left.start or end > right.end:
        raise ValueError('Video transition window must fit inside its two clips')
    left_source = int(left.source_out) - before
    right_source = int(right.source_in) - before
    for clip, origin in ((left, left_source), (right, right_source)):
        if origin < 0 or origin + duration > project.media_frames(sources[clip.media_id]):
            raise ValueError('Video transition needs additional source handles')
    return VideoTransitionPlan(transition, left, right, start, end, duration,
                               left_source, right_source, left.track, cut)


def plan_video_transitions(project):
    entries = project.video_transitions
    if not isinstance(entries, list):
        raise ValueError('Video transitions must be a list')
    plans, ids, pairs = [], set(), set()
    for transition in entries:
        validate_video_transition(transition)
        pair = (transition.left_clip_id, transition.right_clip_id)
        if transition.id in ids or pair in pairs:
            raise ValueError('Duplicate video transition')
        ids.add(transition.id)
        pairs.add(pair)
        plan = plan_video_transition(project, transition)
        if any(p.track == plan.track and p.start < plan.end and plan.start < p.end for p in plans):
            raise ValueError('Video transition windows may not overlap')
        plans.append(plan)
    return plans


def validate_video_transitions(project):
    plan_video_transitions(project)

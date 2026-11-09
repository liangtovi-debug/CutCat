"""Close one captured interior gap between real clips, without deleting items.

No gap clip, arbitrary range deletion, decoding, source-cut budget or persistent
sync state. Every audible whole translation has the same exact sample shift,
including destination-only edits; legacy ripple policy remains independent.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import TYPE_CHECKING

from .clip_audio_translation import guard_common_audio_translation, guard_local_audio_translation
from .source_overwrite import _signature
from .timeline_participation import (TimelineItemImpact as GapItemImpact,
    commit_item_collections, interval_disposition, participation)
from .timing import sample_boundary

if TYPE_CHECKING:
    from .model import Editor


@dataclass(frozen=True)
class GapIdentity:
    track: int
    playhead: int
    left_clip_id: str
    right_clip_id: str
    start: int
    end: int


@dataclass(frozen=True)
class GapClosePlan:
    session_id: str
    revision: int
    project_signature: str
    fps_num: int
    fps_den: int
    track: int
    playhead: int
    left_clip_id: str
    right_clip_id: str
    start: int
    end: int
    participating_tracks: tuple[int, ...]
    follow_captions: bool
    follow_stickers: bool
    item_impacts: tuple[GapItemImpact, ...]
    candidate_signature: str

    @property
    def duration(self) -> int:
        return self.end - self.start

    @property
    def cross_track(self) -> bool:
        return len(self.participating_tracks) > 1 or self.follow_captions or self.follow_stickers


def _discover(project, track, playhead):
    from .model import MAX_CAPTION_FRAME, _integer
    _integer(track, 'gap target track', 0, 3)
    _integer(playhead, 'captured gap playhead', 0, MAX_CAPTION_FRAME)
    ordered = sorted((clip for clip in project.clips if clip.track == track),
                     key=lambda clip: (clip.start, clip.id))
    for left, right in zip(ordered, ordered[1:]):
        if left.end < right.start and left.end <= playhead < right.start:
            return GapIdentity(track, playhead, left.id, right.id, left.end, right.start)
    raise ValueError('No interior gap at the captured playhead on this track; choose the space between two clips')


def _candidate(editor, track, playhead, *, participating_tracks=None,
               follow_captions=False, follow_stickers=False):
    project = deepcopy(editor.project)
    project.validate()
    gap = _discover(project, track, playhead)
    tracks = participation(track, participating_tracks, follow_captions, follow_stickers,
        operation='Gap close', field_prefix='gap', target_label='the target track')
    duration = gap.end - gap.start
    delta = sample_boundary(gap.start, project.fps) - sample_boundary(gap.end, project.fps)
    impacts = []
    for category, name, follows in (('clip', 'clips', None),
            ('caption', 'captions', follow_captions), ('sticker', 'overlays', follow_stickers)):
        for item in getattr(project, name):
            participates = item.track in tracks if category == 'clip' else follows
            action = interval_disposition(item, participates, gap.start, gap.end,
                intersection_message='Gap close participant intersects the gap; leave it fixed or choose another gap')
            start = item.start - duration if action == 'move' else item.start
            impacts.append(GapItemImpact(category, item.id,
                item.track if category == 'clip' else None, item.start, start, item.duration, action))
            if action != 'move':
                continue
            if category == 'clip':
                editor.guard_track_editable(item.track)
                editor._guard_linked_clip(item.id)
                editor._guard_grouped_clip(item.id)
                editor._guard_transition_endpoint(item.id)
                translated = deepcopy(item)
                translated.start = start
                guard_local_audio_translation(item, translated, project, operation='Gap close')
                # Always required, even for two followers on the same track.
                guard_common_audio_translation(item, translated, project, delta,
                    message='Gap close would change relative audio synchronization at this sample-grid phase; choose another gap or project frame')
            item.start = start
    editor._guard_track_edit_locks(editor.project, project)
    project.validate()
    return project, gap, tracks, tuple(impacts)


def _candidate_signature(project, gap, tracks, captions, stickers):
    value = (_signature(project), asdict(gap), tracks, captions, stickers)
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')).hexdigest()


def plan_gap_close(editor: Editor, track: int, playhead: int, *, participating_tracks=None,
                   follow_captions=False, follow_stickers=False) -> GapClosePlan:
    editor._assert_owner()
    signature = _signature(editor.project)
    candidate, gap, tracks, impacts = _candidate(editor, track, playhead,
        participating_tracks=participating_tracks, follow_captions=follow_captions,
        follow_stickers=follow_stickers)
    return GapClosePlan(editor.session_id, editor.revision, signature,
        editor.project.fps_num, editor.project.fps_den, gap.track, gap.playhead,
        gap.left_clip_id, gap.right_clip_id, gap.start, gap.end, tracks,
        follow_captions, follow_stickers, impacts,
        _candidate_signature(candidate, gap, tracks, follow_captions, follow_stickers))


def apply_gap_close(editor: Editor, plan: GapClosePlan) -> None:
    editor._assert_owner()
    if (type(plan) is not GapClosePlan
            or any(type(getattr(plan, field)) is not int for field in
                ('revision', 'fps_num', 'fps_den', 'track', 'playhead', 'start', 'end'))
            or any(type(getattr(plan, field)) is not str for field in
                ('session_id', 'project_signature', 'candidate_signature', 'left_clip_id', 'right_clip_id'))
            or type(plan.participating_tracks) is not tuple
            or type(plan.follow_captions) is not bool or type(plan.follow_stickers) is not bool
            or type(plan.item_impacts) is not tuple
            or any(type(item) is not GapItemImpact for item in plan.item_impacts)):
        raise ValueError('Invalid gap close plan')
    if ((plan.session_id, plan.revision, plan.fps_num, plan.fps_den)
            != (editor.session_id, editor.revision, editor.project.fps_num, editor.project.fps_den)
            or plan.project_signature != _signature(editor.project)):
        raise ValueError('Gap close plan is stale; capture and preview the current gap again')
    candidate, gap, tracks, impacts = _candidate(editor, plan.track, plan.playhead,
        participating_tracks=plan.participating_tracks, follow_captions=plan.follow_captions,
        follow_stickers=plan.follow_stickers)
    if (gap != GapIdentity(plan.track, plan.playhead, plan.left_clip_id, plan.right_clip_id, plan.start, plan.end)
            or tracks != plan.participating_tracks
            or json.dumps([asdict(item) for item in impacts], sort_keys=True)
               != json.dumps([asdict(item) for item in plan.item_impacts], sort_keys=True)
            or _candidate_signature(candidate, gap, tracks, plan.follow_captions,
                                    plan.follow_stickers) != plan.candidate_signature):
        raise ValueError('Gap close plan was changed; capture and preview the current gap again')
    commit_item_collections(editor, candidate, impacts)

"""Explicit single-clip ripple-delete participation, never range extraction.

No decoder, source probing, renderer change or persisted synchronization state.
Every surviving participating interval is fixed, wholly translated, or rejects.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import TYPE_CHECKING

from .clip_audio_translation import guard_local_audio_translation, guard_common_audio_translation
from .timeline_participation import (TimelineItemImpact as RippleItemImpact,
    participation, interval_disposition, commit_item_collections)
from .source_overwrite import _signature

if TYPE_CHECKING:
    from .model import Editor


@dataclass(frozen=True)
class RippleDeletePlan:
    session_id: str
    revision: int
    project_signature: str
    fps_num: int
    fps_den: int
    selected_clip_id: str
    track: int
    start: int
    end: int
    participating_tracks: tuple[int, ...]
    follow_captions: bool
    follow_stickers: bool
    item_impacts: tuple[RippleItemImpact, ...]
    candidate_signature: str

    @property
    def duration(self) -> int:
        return self.end - self.start

    @property
    def cross_track(self) -> bool:
        return len(self.participating_tracks) > 1 or self.follow_captions or self.follow_stickers


def _participation(track, participating_tracks, follow_captions, follow_stickers):
    return participation(track, participating_tracks, follow_captions, follow_stickers,
        operation='Ripple delete', field_prefix='ripple', target_label='the selected track')


def _guard_common_pcm_shift(original, translated, project, start, end):
    from .timing import sample_boundary
    expected = sample_boundary(start, project.fps) - sample_boundary(end, project.fps)
    guard_common_audio_translation(original, translated, project, expected,
        message='Ripple delete participation would change relative audio synchronization at this sample-grid phase; choose another edit interval or project frame')


def _candidate(editor, clip_id, *, participating_tracks=None,
               follow_captions=False, follow_stickers=False):
    from .model import _text
    _text(clip_id, 'selected ripple clip id', True)
    project = deepcopy(editor.project)
    project.validate()
    selected = next((clip for clip in project.clips if clip.id == clip_id), None)
    if selected is None:
        raise ValueError('Unknown ripple delete clip')
    tracks = _participation(selected.track, participating_tracks, follow_captions, follow_stickers)
    cross_track = len(tracks) > 1 or follow_captions or follow_stickers
    editor.guard_track_editable(selected.track)
    start, end, duration = selected.start, selected.end, selected.duration
    impacts, output = [], []

    def impact(category, item, track, disposition, moved_start=None):
        impacts.append(RippleItemImpact(category, item.id, track, item.start,
            item.start if moved_start is None else moved_start, item.duration, disposition))

    def disposition(item, participates):
        return interval_disposition(item, participates, start, end,
            intersection_message='Ripple delete participant intersects the deleted interval; leave it fixed or choose another clip')

    for clip in project.clips:
        action = 'delete' if clip.id == clip_id else disposition(clip, clip.track in tracks)
        if action != 'fixed':
            editor.guard_track_editable(clip.track)
            editor._guard_linked_clip(clip.id)
            editor._guard_grouped_clip(clip.id)
            # Explicit participation is intentionally conservative. The legacy
            # single-track operation keeps its valid pair-translation support.
            editor._guard_transition_endpoint(clip.id)
        if action == 'delete':
            impact('clip', clip, clip.track, action)
            continue
        if action == 'move':
            translated = deepcopy(clip)
            translated.start -= duration
            guard_local_audio_translation(clip, translated, project, operation='Ripple delete')
            if cross_track:
                _guard_common_pcm_shift(clip, translated, project, start, end)
            impact('clip', clip, clip.track, action, translated.start)
            output.append(translated)
        else:
            impact('clip', clip, clip.track, action)
            output.append(clip)
    project.clips = output
    for category, collection, follows in (('caption', project.captions, follow_captions),
                                          ('sticker', project.overlays, follow_stickers)):
        for item in collection:
            action = disposition(item, follows)
            moved_start = item.start - duration if action == 'move' else item.start
            impact(category, item, None, action, moved_start)
            item.start = moved_start
    editor._guard_track_edit_locks(editor.project, project)
    project.validate()
    return project, selected, tracks, tuple(impacts)


def _candidate_signature(project, selected, tracks, captions, stickers):
    # Bind choices even when a participant collection currently has no items.
    value = (_signature(project), selected.id, selected.track, selected.start,
             selected.end, tracks, captions, stickers)
    return sha256(json.dumps(value, separators=(',', ':')).encode('ascii')).hexdigest()


def plan_ripple_delete(editor: Editor, clip_id: str, *, participating_tracks=None,
                       follow_captions=False, follow_stickers=False) -> RippleDeletePlan:
    editor._assert_owner()
    signature = _signature(editor.project)
    candidate, selected, tracks, impacts = _candidate(editor, clip_id,
        participating_tracks=participating_tracks, follow_captions=follow_captions,
        follow_stickers=follow_stickers)
    return RippleDeletePlan(editor.session_id, editor.revision, signature,
        editor.project.fps_num, editor.project.fps_den, selected.id, selected.track,
        selected.start, selected.end, tracks, follow_captions, follow_stickers,
        impacts, _candidate_signature(candidate, selected, tracks, follow_captions, follow_stickers))


def apply_ripple_delete(editor: Editor, plan: RippleDeletePlan) -> None:
    editor._assert_owner()
    if (type(plan) is not RippleDeletePlan
            or any(type(getattr(plan, field)) is not int for field in
                   ('revision', 'fps_num', 'fps_den', 'track', 'start', 'end'))
            or type(plan.participating_tracks) is not tuple
            or type(plan.follow_captions) is not bool or type(plan.follow_stickers) is not bool
            or type(plan.item_impacts) is not tuple
            or any(type(item) is not RippleItemImpact for item in plan.item_impacts)):
        raise ValueError('Invalid ripple delete plan')
    if ((plan.session_id, plan.revision, plan.fps_num, plan.fps_den)
            != (editor.session_id, editor.revision, editor.project.fps_num, editor.project.fps_den)
            or plan.project_signature != _signature(editor.project)):
        raise ValueError('Ripple delete plan is stale; preview the current selection again')
    candidate, selected, tracks, impacts = _candidate(editor, plan.selected_clip_id,
        participating_tracks=plan.participating_tracks, follow_captions=plan.follow_captions,
        follow_stickers=plan.follow_stickers)
    # JSON distinguishes bool from int in forged impact fields, unlike Python
    # dataclass equality. Full impact records are part of the preview contract.
    from dataclasses import asdict
    if ((selected.track, selected.start, selected.end) != (plan.track, plan.start, plan.end)
            or tracks != plan.participating_tracks
            or json.dumps([asdict(x) for x in impacts], sort_keys=True)
               != json.dumps([asdict(x) for x in plan.item_impacts], sort_keys=True)
            or _candidate_signature(candidate, selected, tracks, plan.follow_captions,
                                    plan.follow_stickers) != plan.candidate_signature):
        raise ValueError('Ripple delete plan was changed; preview the current selection again')

    commit_item_collections(editor, candidate, impacts)

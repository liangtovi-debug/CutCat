"""Persistent same-source A/V pair editing, without audio ownership changes.

Link/Unlink are metadata only. Joint source slip translates both exact source intervals;
its audio is expected to change. Local keys, hidden handles, gains, fades and
mix anchors do not move. File observations belong only in verify_* workers.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from uuid import uuid4

from .timing import ClipTimeMap, parse_source_position, sample_boundary
from .source_overwrite import _signature


def linked_av_slip_candidate(project, clip_id, source_in):
    """Translate each v1/v2 role by the selected role's exact source delta.

    Timeline edges and each independent partial tail stay fixed. A source
    coordinate delta is not a timeline delta: never multiply it by speed.
    """
    from .model import _text
    # Qualification must not canonicalize values in the live project.
    candidate = deepcopy(project)
    candidate.validate()
    _text(clip_id, 'linked A/V clip id', True)
    link = next((item for item in candidate.av_links if clip_id in item.clip_ids), None)
    if link is None:
        raise ValueError('Joint source slip requires a linked A/V pair')
    # Full detached validation above admits only the known v1/v2 relations.
    selected = next(clip for clip in candidate.clips if clip.id == clip_id)
    delta = parse_source_position(source_in) - selected.timing.source_in
    for clip in candidate.clips:
        if clip.id not in link.clip_ids or not delta:
            continue
        mapping = clip.timing
        clip.time_map = ClipTimeMap(mapping.source_in + delta, mapping.source_out + delta,
                                    mapping.speed)
        clip.source_in = clip.time_map.source_in
        clip._normalize_legacy_timing()
    # No duration setter: it can clamp fades and lose the partial source tail.
    from .model import Editor
    Editor._guard_track_edit_locks(project, candidate)
    candidate.validate()
    return candidate


def guard_linked_av_move(before, after, selected):
    """Prove the full potentially audible selection moves by one sample delta.

    A muted A role is checked as unmuted, without changing its stored mute.
    Suppressed video stays suppressed; there is no second emitted contribution.
    """
    if not any(set(link.clip_ids).intersection(selected) for link in before.av_links):
        return
    from .clip_audio_translation import guard_local_audio_translation, guard_common_audio_translation
    previous = {clip.id: clip for clip in before.clips if clip.id in selected}
    current = {clip.id: clip for clip in after.clips if clip.id in selected}
    shifts = {current[key].start - clip.start for key, clip in previous.items()}
    if len(shifts) != 1:
        raise ValueError('Linked A/V move requires one common timeline translation')
    anchor = min(clip.start for clip in previous.values())
    delta = shifts.pop()
    expected = sample_boundary(anchor + delta, before.fps) - sample_boundary(anchor, before.fps)
    for key, original in previous.items():
        translated = current[key]
        potential_old, potential_new = deepcopy(original), deepcopy(translated)
        potential_old.muted = potential_new.muted = False
        guard_local_audio_translation(potential_old, potential_new, before, operation='Linked A/V move')
        guard_common_audio_translation(potential_old, potential_new, before, expected,
            message='Linked A/V move would change relative audio sample alignment; choose another project frame')


@dataclass(frozen=True)
class LinkedAVSlipPlan:
    session_id: str
    revision: int
    project_signature: str
    clip_id: str
    source_in: str
    source_request: object
    candidate_signature: str
    token: str


@dataclass(frozen=True)
class LinkedAVSlipVerification:
    plan: LinkedAVSlipPlan
    dependencies: tuple
    phase: str


class LinkedAVSlipCancelled(ValueError):
    pass


def _slip_copy_request(editor, clip_id):
    """Reuse copy capture without its legacy live-project normalization."""
    from .clip_copy_paste import prepare_clip_copy
    from .model import Editor
    editor._assert_owner()
    session_id, revision, signature = editor.session_id, editor.revision, _signature(editor.project)
    request = prepare_clip_copy(Editor(deepcopy(editor.project)), [clip_id])
    return replace(request, session_id=session_id, revision=revision,
                   project_signature=signature)


def plan_linked_av_slip(editor, clip_id, source_in):
    """Pure GUI-side capture: no stat/hash/decode and no project mutation."""
    editor._assert_owner()
    candidate = linked_av_slip_candidate(editor.project, clip_id, source_in)
    request = _slip_copy_request(editor, clip_id)
    return LinkedAVSlipPlan(editor.session_id, editor.revision, _signature(editor.project),
        clip_id, str(parse_source_position(source_in)), request, _signature(candidate), uuid4().hex)


def _validate_plan(plan):
    from .clip_copy_paste import ClipCopyRequest, _snapshot
    if (type(plan) is not LinkedAVSlipPlan or type(plan.source_request) is not ClipCopyRequest
            or type(plan.source_in) is not str or type(plan.token) is not str or not plan.token
            or (plan.session_id, plan.revision, plan.project_signature)
            != (plan.source_request.session_id, plan.source_request.revision,
                plan.source_request.project_signature)):
        raise ValueError('Invalid linked A/V source-slip plan')
    project = _snapshot(plan.source_request)
    if (len(project.clips) != 2 or len(project.av_links) != 1
            or plan.clip_id not in project.av_links[0].clip_ids or project.clip_groups):
        raise ValueError('Invalid linked A/V source-slip snapshot')
    linked_av_slip_candidate(project, plan.clip_id, plan.source_in)
    return project


def verify_linked_av_slip(plan, previous=None, cancel=None):
    """Worker Preview + Apply; reuse accepted source/generated/LUT identities.

    Ordinary media use the existing bounded sampled identity, generated sources
    and LUTs use full hashes. Each batch is observed twice. This is freshness,
    not a filesystem lock, adversarial seal, extraction, or media-write step.
    """
    from .clip_copy_paste import _dependencies, ClipCopyCancelled
    _validate_plan(plan)
    if previous is not None and (type(previous) is not LinkedAVSlipVerification
            or previous.plan != plan or previous.phase != 'preview'):
        raise ValueError('Linked A/V source-slip preview is stale; preview again')
    try:
        dependencies = _dependencies(plan.source_request, cancel)
    except ClipCopyCancelled as exc:
        raise LinkedAVSlipCancelled('Linked A/V source-slip check cancelled') from exc
    if previous is not None and previous.dependencies != dependencies:
        raise ValueError('Linked A/V source-slip dependencies changed; preview again')
    return LinkedAVSlipVerification(plan, dependencies, 'apply' if previous is not None else 'preview')


def apply_linked_av_slip(editor, plan, verification):
    """Consume one verified plan without I/O; stale/ABA/no-op replay reject."""
    editor._assert_owner()
    _validate_plan(plan)
    if (type(verification) is not LinkedAVSlipVerification or verification.plan != plan
            or verification.phase != 'apply'):
        raise ValueError('Joint source slip needs a fresh verified preview')
    key = (editor.session_id, editor.revision)
    if ((plan.session_id, plan.revision, plan.project_signature)
            != (*key, _signature(editor.project))):
        raise ValueError('Linked A/V source-slip plan is stale; preview again')
    consumed_key, consumed = getattr(editor, '_linked_av_consumed_plans', (None, set()))
    if consumed_key != key:
        consumed = set()
    if plan.token in consumed:
        raise ValueError('Linked A/V source-slip plan was already applied; preview again')
    if len(consumed) >= 1024:
        raise ValueError('Linked A/V source-slip no-op receipt budget reached; reopen the project')
    if _slip_copy_request(editor, plan.clip_id).payload != plan.source_request.payload:
        raise ValueError('Linked A/V source-slip snapshot changed; preview again')
    candidate = linked_av_slip_candidate(editor.project, plan.clip_id, plan.source_in)
    if _signature(candidate) != plan.candidate_signature:
        raise ValueError('Linked A/V source-slip candidate changed; preview again')
    result = editor.slip_linked_av(plan.clip_id, plan.source_in)
    consumed.add(plan.token)
    editor._linked_av_consumed_plans = (key, consumed)
    return result

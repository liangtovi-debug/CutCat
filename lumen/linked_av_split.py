"""Bounded through-split of one strict or affine same-source linked A/V pair.

Pure candidates prove timing, keys, PCM lattices and historical summation order.
The worker separately proves actual CFR and the renderer's legacy trim phase;
only its fresh Preview + Apply receipts can enter the dedicated transaction.
No renderer, source file, schema, or generic editor permission is changed.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import time
from uuid import uuid4

from .audio_components import audio_mix_order_key, effective_audio_mix_anchor
from .audio_gain import shift_audio_gain_keyframes
from .audio_timing import compile_audio_timing
from .clip_motion import shift_clip_motion
from .source_overwrite import _signature
from .timing import ClipTimeMap, sample_boundary
from .window_animation import shift_window_animation

MAX_SPLIT_PROOF_SECONDS = 30
MAX_SPLIT_VERIFY_SECONDS = 120


def _guard_pcm(original, halves, project, media):
    """Prove every emitted source-sample index and every silence interval.

    This proof is only valid at 1x. It deliberately ignores user mute, so a
    later unmute cannot reveal a one-sample source/leading/padding discontinuity.
    Finite source EOF truncates the same source-index lattice on both sides.
    """
    old = compile_audio_timing(original, project.fps, media.audio_offset)
    plans = [compile_audio_timing(half, project.fps, media.audio_offset) for half in halves]
    if (plans[0].start_sample != old.start_sample
            or plans[0].start_sample + plans[0].sample_count != plans[1].start_sample
            or sum(plan.sample_count for plan in plans) != old.sample_count):
        raise ValueError('Linked A/V split cannot preserve the complete audio sample budget')

    def active(plan, lo, hi):
        if plan.silent:
            return None
        onset = plan.start_sample + plan.leading_samples
        first = max(lo, onset)
        last = min(hi, plan.start_sample + plan.sample_count,
                   onset + plan.source_end_sample - plan.source_start_sample)
        return (first, last, plan.source_start_sample - onset) if first < last else None

    for plan in plans:
        lo, hi = plan.start_sample, plan.start_sample + plan.sample_count
        if active(old, lo, hi) != active(plan, lo, hi):
            raise ValueError('Linked A/V split would change retained audio at this sample-grid boundary; choose another project frame')


def _guard_audio_order(project, original, halves):
    """Both halves must occupy the original's relative potential mix position.

    Anchors alone are insufficient: start/id tie breakers also matter. Reject
    ambiguous new ties independently of the generated UUID, then compare the
    complete ordering key. Never retry UUIDs or rewrite anchors to rescue an edit.
    """
    media = {item.id: item for item in project.media}
    old_key = audio_mix_order_key(original)
    for other in project.clips:
        if (other.id == original.id or not other.source_audio_enabled
                or not media[other.media_id].has_audio):
            continue
        other_key = audio_mix_order_key(other)
        for half in halves:
            half_anchor, other_anchor = effective_audio_mix_anchor(half), effective_audio_mix_anchor(other)
            ambiguous = ((half_anchor, half.start) == (other_anchor, other.start)
                if half.audio_mix_anchor is not None
                else half_anchor[:2] == other_anchor[:2])
            if half.id != original.id and ambiguous:
                raise ValueError('Linked A/V split cannot preserve an ambiguous historical audio mix slot')
            if (old_key < other_key) != (audio_mix_order_key(half) < other_key):
                raise ValueError('Linked A/V split would change historical audio mixing order')


def linked_av_split_candidate(project, clip_id, frame, new_ids):
    """Derive exactly two left records, two new right records and one new link.

    Each role independently divides [O,E) at O+(c-t), retaining its own E.
    The common affine intercept makes both source cuts equal, not their outer
    edges or residual tails. Only 1x has the complete PCM conservation proof.
    """
    from .model import AVLink, Editor, MAX_CAPTION_FRAME, _integer, _text
    _text(clip_id, 'linked A/V clip id', True)
    _integer(frame, 'split frame', 0, MAX_CAPTION_FRAME)
    if type(new_ids) is not tuple or len(new_ids) != 3:
        raise ValueError('Linked A/V split requires exactly three fresh identities')
    for value in new_ids:
        _text(value, 'linked A/V split identity', True)
    existing = {clip.id for clip in project.clips} | {link.id for link in project.av_links}
    if len(set(new_ids)) != 3 or existing.intersection(new_ids):
        raise ValueError('Linked A/V split identities must be distinct and unused')
    # Validate a detached value: Project.validate canonicalizes some scalar
    # fields and a rejected qualification must never mutate live records.
    candidate = deepcopy(project)
    candidate.validate()
    link = next((item for item in candidate.av_links if clip_id in item.clip_ids), None)
    if link is None:
        raise ValueError('Joint split requires one existing linked A/V pair')
    originals = {clip.id: deepcopy(clip) for clip in candidate.clips if clip.id in link.clip_ids}
    video, audio = (originals[key] for key in link.clip_ids)
    if not all(clip.start < frame < clip.end for clip in (video, audio)):
        raise ValueError('Split must be strictly inside both linked A/V members')
    if video.speed != 1:
        raise ValueError('Linked A/V split requires 1x source timing')
    media = next(item for item in candidate.media if item.id == link.media_id)
    if Fraction(media.fps_num, media.fps_den) != candidate.fps:
        raise ValueError('Linked A/V split requires source video at the exact project frame rate')
    # Charge the complete selected source envelope, including an audio-only
    # J/L extension. Fractional origins can require the final sampled frame.
    source_start = min(clip.source_in.__floor__() for clip in (video, audio))
    source_end = max(clip.source_in.__floor__() + clip.duration for clip in (video, audio))
    if source_end - source_start > MAX_SPLIT_PROOF_SECONDS * candidate.fps:
        raise ValueError('Linked A/V split source proof is limited to 30 seconds')
    if (video.stabilization.enabled and video.stabilization.strength > 0
            or video.restoration.enabled and any((video.restoration.deblock,
                video.restoration.denoise, video.restoration.sharpen))
            or any(effect.enabled and effect.amount > 0 for effect in video.effects)):
        raise ValueError('Linked A/V split does not support active effects, stabilization or restoration')
    output, pairs = [], {}
    right_ids = dict(zip(link.clip_ids, new_ids[:2]))
    for clip in candidate.clips:
        if clip.id not in originals:
            output.append(clip)
            continue
        original = originals[clip.id]
        mapping = original.timing
        k = frame - original.start
        boundary = mapping.source_in + k * mapping.speed
        sample_delta = sample_boundary(frame, candidate.fps) - sample_boundary(original.start, candidate.fps)
        if original.fade_in > k or original.fade_out > original.duration - k:
            raise ValueError('Linked A/V split would cross an existing fade; reduce the fade first')
        right = deepcopy(original)
        right.id, right.start = right_ids[clip.id], frame
        right.audio_gain_keyframes = shift_audio_gain_keyframes(original.audio_gain_keyframes, sample_delta)
        right = shift_clip_motion(right, k)
        right.grade = shift_window_animation(right.grade, k)
        right.time_map = ClipTimeMap(boundary, mapping.source_out, mapping.speed)
        right.source_in, right.duration = boundary, original.duration - k
        right.fade_in = 0
        right._normalize_legacy_timing()
        clip.time_map = ClipTimeMap(mapping.source_in, boundary, mapping.speed)
        clip.source_in, clip.duration, clip.fade_out = mapping.source_in, k, 0
        clip._normalize_legacy_timing()
        output.extend((clip, right))
        pairs[clip.id] = (clip, right)
    candidate.clips = output
    candidate.av_links.append(AVLink(new_ids[2], new_ids[0], new_ids[1], link.media_id, link.alignment))
    Editor._guard_track_edit_locks(project, candidate)
    candidate.validate()
    _guard_pcm(audio, pairs[audio.id], candidate, media)
    _guard_audio_order(project, audio, pairs[audio.id])
    return candidate


def qualify_linked_av_split(project, clip_id, frame):
    """Pure UUID-independent eligibility; no file reads or saved plan receipt."""
    used = {clip.id for clip in project.clips} | {link.id for link in project.av_links}
    # Deterministic noncolliding qualification names; the order guard rejects
    # every new historical tie before its ID can influence qualification.
    names = []
    for role in ('video', 'audio', 'link'):
        name = '__linked_split_qualification_' + role
        while name in used:
            name += '_'
        names.append(name)
    linked_av_split_candidate(project, clip_id, frame, tuple(names))


@dataclass(frozen=True)
class LinkedAVSplitPlan:
    session_id: str
    revision: int
    project_signature: str
    clip_id: str
    frame: int
    new_ids: tuple[str, str, str]
    source_request: object
    candidate_signature: str
    token: str


@dataclass(frozen=True)
class LinkedAVSplitVerification:
    plan: LinkedAVSplitPlan
    dependencies: tuple
    source_identity: object
    phase: str


class LinkedAVSplitCancelled(ValueError):
    pass


def plan_linked_av_split(editor, clip_id, frame):
    from .clip_copy_paste import prepare_clip_copy
    editor._assert_owner()
    qualify_linked_av_split(editor.project, clip_id, frame)
    new_ids = tuple(uuid4().hex for _ in range(3))
    candidate = linked_av_split_candidate(editor.project, clip_id, frame, new_ids)
    return LinkedAVSplitPlan(editor.session_id, editor.revision, _signature(editor.project),
        clip_id, frame, new_ids, prepare_clip_copy(editor, [clip_id]),
        _signature(candidate), uuid4().hex)


def _validate_plan(plan):
    from .clip_copy_paste import ClipCopyRequest, _snapshot
    if (type(plan) is not LinkedAVSplitPlan or type(plan.source_request) is not ClipCopyRequest
            or type(plan.token) is not str or not plan.token
            or (plan.session_id, plan.revision, plan.project_signature)
            != (plan.source_request.session_id, plan.source_request.revision,
                plan.source_request.project_signature)):
        raise ValueError('Invalid linked A/V split plan')
    snapshot = _snapshot(plan.source_request)
    if (len(snapshot.clips) != 2 or len(snapshot.av_links) != 1
            or plan.clip_id not in snapshot.av_links[0].clip_ids or snapshot.clip_groups):
        raise ValueError('Invalid linked A/V split snapshot')
    linked_av_split_candidate(snapshot, plan.clip_id, plan.frame, plan.new_ids)
    return snapshot


def verify_linked_av_split(plan, previous=None, cancel=None):
    """Cancellable worker: each Preview/Apply repeats the complete media proof.

    One shared 120s deadline includes dependencies, CFR/RGB16 proof and final
    identity observations. Ordinary media identity is sampled, not a file lock.
    """
    from .clip_copy_paste import _dependencies, ClipCopyCancelled
    from .source_viewer import SourcePreviewBackend, SourceViewerCancelled, _check
    deadline = time.monotonic() + MAX_SPLIT_VERIFY_SECONDS
    snapshot = _validate_plan(plan)
    if previous is not None and (type(previous) is not LinkedAVSplitVerification
            or previous.plan != plan or previous.phase != 'preview'):
        raise ValueError('Linked A/V split preview is stale; preview again')
    backend = SourcePreviewBackend(timeout=MAX_SPLIT_VERIFY_SECONDS)
    try:
        dependencies = _dependencies(plan.source_request, cancel, deadline=deadline)
        source_dependencies = [item for item in dependencies if item.kind == 'media'
            and item.key == snapshot.av_links[0].media_id]
        if len(source_dependencies) != 1:
            raise ValueError('Linked A/V split source identity is unavailable')
        expected_identity = source_dependencies[0].identity
        identity = backend.verify_linked_av_split_source(snapshot, plan.clip_id,
            plan.frame, cancel=cancel, deadline=deadline, expected_identity=expected_identity)
        # The source viewer's signature additionally includes canvas/fps. Its
        # underlying sampled observation must still describe the same file as
        # the dependency batch, not a path that switched A -> B -> A mid-proof.
        if ((identity.path, identity.state, identity.sample_sha256)
                != (expected_identity.path, expected_identity.state, expected_identity.sample_sha256)):
            raise ValueError('Linked A/V split source identity changed during qualification')
        if dependencies != _dependencies(plan.source_request, cancel, deadline=deadline):
            raise ValueError('Linked A/V split dependencies changed; preview again')
        _check(cancel, deadline)
        if previous is not None and (previous.dependencies != dependencies
                or previous.source_identity != identity):
            raise ValueError('Linked A/V split dependencies changed; preview again')
        return LinkedAVSplitVerification(plan, dependencies, identity,
            'apply' if previous is not None else 'preview')
    except (ClipCopyCancelled, SourceViewerCancelled) as exc:
        raise LinkedAVSplitCancelled('Linked A/V split check cancelled') from exc
    finally:
        backend.close()


def validated_split_candidate(editor, plan, verification):
    """Pure current-context/receipt check before the private editor transaction."""
    from .clip_copy_paste import prepare_clip_copy
    editor._assert_owner()
    _validate_plan(plan)
    if (type(verification) is not LinkedAVSplitVerification or verification.plan != plan
            or verification.phase != 'apply'):
        raise ValueError('Linked A/V split needs a fresh verified preview')
    if ((plan.session_id, plan.revision, plan.project_signature)
            != (editor.session_id, editor.revision, _signature(editor.project))):
        raise ValueError('Linked A/V split plan is stale; preview again')
    if prepare_clip_copy(editor, [plan.clip_id]).payload != plan.source_request.payload:
        raise ValueError('Linked A/V split snapshot changed; preview again')
    candidate = linked_av_split_candidate(editor.project, plan.clip_id, plan.frame, plan.new_ids)
    if _signature(candidate) != plan.candidate_signature:
        raise ValueError('Linked A/V split candidate changed; preview again')
    return candidate


def apply_linked_av_split(editor, plan, verification):
    return editor.split_linked_av(plan, verification)

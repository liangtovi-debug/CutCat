"""Explicit, output-preserving recombination of two independent source clips.

No candidate search or AV link is inferred. Planning and commit are pure model
operations; two bounded worker observations verify the shared source separately.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import time
from types import SimpleNamespace
from uuid import uuid4

from .source_overwrite import _signature
from .source_viewer import SourceIdentity, _identity

MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_VERIFY_SECONDS = 60.0
MAX_IDENTITY_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class SourceAudioReattachPlan:
    session_id: str
    revision: int
    project_signature: str
    video_clip_id: str
    audio_clip_id: str
    source_snapshot: str
    candidate_signature: str
    token: str


@dataclass(frozen=True)
class SourceAudioReattachVerification:
    plan: SourceAudioReattachPlan
    identity: SourceIdentity
    phase: str


class SourceAudioReattachCancelled(ValueError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False)


def _pair(project, video_id, audio_id):
    """Strict explicit roles, semantic timing equality, never source rebasing."""
    from .model import _text
    _text(video_id, 'reattach video clip id', True)
    _text(audio_id, 'reattach audio clip id', True)
    project.validate()
    video = next((c for c in project.clips if c.id == video_id), None)
    audio = next((c for c in project.clips if c.id == audio_id), None)
    if (video is None or audio is None or video.id == audio.id
            or video.track not in (0, 1) or audio.track not in (2, 3)):
        raise ValueError('Reattach requires one explicit video-track clip and one explicit audio-track clip')
    if video.source_audio_enabled:
        raise ValueError('The selected video already emits source audio; reattach would duplicate its contribution')
    if not audio.source_audio_enabled:
        raise ValueError('The selected audio clip must have its source audio enabled; user mute is allowed')
    if video.media_id != audio.media_id:
        raise ValueError('Reattach requires the same Media record; matching filenames are not sufficient')
    media = next(m for m in project.media if m.id == video.media_id)
    if not media.has_video or not media.has_audio:
        raise ValueError('Reattach requires a shared source containing both video and audio')
    if (video.start, video.duration, video.timing) != (audio.start, audio.duration, audio.timing):
        raise ValueError('Reattach requires identical timeline start, duration and complete rational source timing')
    return video, audio, media


def _potential_order(project):
    # Muted components still participate in the proof: later unmute must not
    # expose an ID-dependent change of float32 addition order. No decoder work.
    from .audio_components import audio_mix_order_key
    audible_media = {m.id for m in project.media if m.has_audio}
    return [c.id for c in sorted(project.clips, key=audio_mix_order_key)
            if c.source_audio_enabled and c.media_id in audible_media]


def _candidate(project, video_id, audio_id):
    """Build only the approved replacement; also used by the narrow primitive."""
    from .audio_components import effective_audio_mix_anchor
    from .model import AudioMixAnchor
    video, audio, _ = _pair(project, video_id, audio_id)
    ids = {video.id, audio.id}
    if any(ids.intersection(link.clip_ids) for link in project.av_links):
        raise ValueError("Unlink A/V clips before reattaching source audio")
    if any(project.is_track_edit_locked(c.track) for c in (video, audio)):
        raise ValueError('Track is edit-locked. Unlock the track before editing it.')
    if any(ids.intersection(group.clip_ids) for group in project.clip_groups):
        raise ValueError('Ungroup both selected clips before reattaching source audio')
    if any(ids.intersection((t.left_clip_id, t.right_clip_id)) for t in project.video_transitions):
        raise ValueError('Remove transitions from both selected clips before reattaching source audio')
    anchor = AudioMixAnchor(*effective_audio_mix_anchor(audio))
    try:
        anchor.validate()
    except ValueError as exc:
        raise ValueError('Reattach cannot represent the audio clip historical mix slot') from exc
    slot = effective_audio_mix_anchor(audio), audio.start
    # The suppressed retained V and removed A are the intended replacement,
    # not a tie. Every other tie rejects, even if muted/suppressed today. Shared
    # historical anchors at DIFFERENT current starts remain safe and valid.
    if any(c.id not in ids and (effective_audio_mix_anchor(c), c.start) == slot
           for c in project.clips):
        raise ValueError('Reattach cannot preserve an ambiguous historical audio mix slot')
    replacement = deepcopy(video)
    replacement.source_audio_enabled = True
    replacement.audio_mix_anchor = anchor
    for name in ('gain_db', 'muted', 'fade_in', 'fade_out', 'audio_gain_keyframes'):
        setattr(replacement, name, deepcopy(getattr(audio, name)))
    candidate = deepcopy(project)
    candidate.clips = [deepcopy(replacement) if c.id == video.id else c
                       for c in candidate.clips if c.id != audio.id]
    candidate.validate()
    expected_order = [video.id if item == audio.id else item for item in _potential_order(project)]
    if _potential_order(candidate) != expected_order:
        raise ValueError('Reattach cannot preserve the complete historical audio mixing order')
    return candidate, replacement


def _snapshot(project, video_id, audio_id):
    selected = deepcopy(project)
    selected.clips = [c for c in selected.clips if c.id in (video_id, audio_id)]
    media_ids = {c.media_id for c in selected.clips}
    selected.media = [m for m in selected.media if m.id in media_ids]
    selected.media_bins = []; selected.media_bin_ids = {}
    selected.clip_groups = []; selected.video_transitions = []; selected.av_links = []
    selected.captions = []; selected.overlays = []
    selected.source_ranges = []; selected.timeline_markers = []
    payload = _json(selected.to_dict())
    if len(payload) > MAX_SNAPSHOT_BYTES:
        raise ValueError('Source audio reattach exceeds its 8 MiB parameter budget')
    return payload


def plan_source_audio_reattach(editor, video_clip_id, audio_clip_id):
    """Capture exactly two supplied IDs; no searching, file I/O or mutation."""
    editor._assert_owner()
    candidate, _ = _candidate(editor.project, video_clip_id, audio_clip_id)
    return SourceAudioReattachPlan(editor.session_id, editor.revision,
        _signature(editor.project), video_clip_id, audio_clip_id,
        _snapshot(editor.project, video_clip_id, audio_clip_id),
        _signature(candidate), uuid4().hex)


def _source_snapshot(plan):
    from .model import Project
    if (type(plan) is not SourceAudioReattachPlan
            or type(plan.source_snapshot) is not str
            or len(plan.source_snapshot) > MAX_SNAPSHOT_BYTES):
        raise ValueError('Invalid source audio reattach plan')
    project = Project.from_dict(json.loads(plan.source_snapshot))
    if (len(project.clips) != 2 or len(project.media) != 1
            or project.av_links or project.clip_groups or project.video_transitions or project.captions
            or project.overlays or project.source_ranges or project.timeline_markers
            or project.media_bins or project.media_bin_ids):
        raise ValueError('Invalid source audio reattach snapshot')
    _, _, media = _pair(project, plan.video_clip_id, plan.audio_clip_id)
    return media


def verify_source_audio_reattach(plan, previous=None, cancel=None):
    """Worker Preview then Apply: twice observe one source, never extract it."""
    from .clip_copy_paste import _full_dependency_identity
    media = _source_snapshot(plan)
    if previous is not None and (type(previous) is not SourceAudioReattachVerification
            or previous.plan != plan or previous.phase != 'preview'):
        raise ValueError('Source audio reattach preview is stale; preview again')
    deadline = time.monotonic() + MAX_VERIFY_SECONDS
    count = 0
    def check():
        if cancel is not None and cancel.is_set():
            raise SourceAudioReattachCancelled('Source audio reattach check cancelled')
        if time.monotonic() >= deadline:
            raise ValueError('Source audio reattach check exceeded its 60-second budget')
    def reserve(amount):
        nonlocal count
        check()
        if count + amount > MAX_IDENTITY_BYTES:
            raise ValueError('Source audio reattach exceeds its 128 MiB read budget')
        count += amount
    signature = hashlib.sha256(_json(asdict(media)).encode('ascii')).hexdigest()
    def inspect():
        check()
        provenance = media.sound_provenance or media.speech_provenance
        if provenance is None:
            reserve(3 * 65536)
            identity = _identity(SimpleNamespace(media=media, signature=signature), cancel, deadline)
        else:
            identity = _full_dependency_identity(media.path, signature, cancel,
                deadline, reserve, generated_sha256=provenance.output_sha256)
        check()
        return identity
    first = inspect()
    if first != inspect():
        raise ValueError('Source changed during reattach verification; preview again')
    if previous is not None and first != previous.identity:
        raise ValueError('Source changed after reattach preview; preview again')
    return SourceAudioReattachVerification(plan, first,
        'apply' if previous is not None else 'preview')


def apply_source_audio_reattach(editor, plan, verification):
    """Consume a fresh explicit receipt in one owner-thread atomic history step."""
    editor._assert_owner()
    if (type(plan) is not SourceAudioReattachPlan
            or type(verification) is not SourceAudioReattachVerification
            or verification.plan != plan or verification.phase != 'apply'):
        raise ValueError('Source audio reattach needs a fresh verified preview')
    if ((plan.session_id, plan.revision, plan.project_signature)
            != (editor.session_id, editor.revision, _signature(editor.project))):
        raise ValueError('Source audio reattach plan is stale; preview again')
    if _snapshot(editor.project, plan.video_clip_id, plan.audio_clip_id) != plan.source_snapshot:
        raise ValueError('Source audio reattach snapshot changed; preview again')
    candidate, _ = _candidate(editor.project, plan.video_clip_id, plan.audio_clip_id)
    if _signature(candidate) != plan.candidate_signature:
        raise ValueError('Source audio reattach plan changed; preview again')
    return editor._commit_source_audio_reattach(plan.video_clip_id, plan.audio_clip_id)

"""Independent source-audio separation; no extraction and no persistent link.

Planning/commit never inspect a file. Preview and Apply each observe the shared
source on a cancellable worker. Identity is bounded sampled freshness for normal
media, and full provenance verification for generated media; not a file lock.
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
class SourceAudioSeparationPlan:
    session_id: str
    revision: int
    project_signature: str
    source_clip_id: str
    track: int
    new_clip_id: str
    source_snapshot: str
    candidate_signature: str
    token: str


@dataclass(frozen=True)
class SourceAudioSeparationVerification:
    plan: SourceAudioSeparationPlan
    identity: SourceIdentity
    phase: str


class SourceAudioSeparationCancelled(ValueError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False)


def _guard_source(editor, clip_id, track):
    from .model import _integer, _text
    _text(clip_id, 'source clip id', True)
    _integer(track, 'separate audio track', 2, 3)
    project = deepcopy(editor.project)
    project.validate()
    source = next((c for c in project.clips if c.id == clip_id), None)
    if source is None or source.track not in (0, 1):
        raise ValueError('Separate source audio requires one committed video-track clip')
    editor._guard_linked_clip(clip_id)
    if not source.source_audio_enabled:
        raise ValueError('Source audio is already separated; Undo restores the previous state')
    media = next(m for m in project.media if m.id == source.media_id)
    if not media.has_video or not media.has_audio:
        raise ValueError('The selected source must contain both video and audio')
    editor.guard_track_editable(source.track)
    editor.guard_track_editable(track)
    editor._guard_grouped_clip(clip_id)
    editor._guard_transition_endpoint(clip_id)
    from .audio_components import guard_audio_handoff_slot
    guard_audio_handoff_slot(project, source)
    if any(c.track == track and c.start < source.end and source.start < c.end
           for c in project.clips):
        raise ValueError('The chosen audio track is occupied during this clip; choose a free track')
    return project, source, media


def _candidate(editor, clip_id, track, new_id):
    from .audio_components import effective_audio_mix_anchor
    from .model import AudioMixAnchor, Clip, _text
    _text(new_id, 'new separated audio clip id', True)
    project, source, media = _guard_source(editor, clip_id, track)
    if any(c.id == new_id for c in project.clips):
        raise ValueError('Separated audio clip identity must be new')
    # A deliberately fresh audio-only Clip. No picture grade/effect/transform,
    # source-wide processing or hidden motion key may leak from the V record.
    audio = Clip(new_id, source.media_id, track, source.start, source.source_in,
        source.duration, gain_db=source.gain_db, muted=source.muted,
        fade_in=source.fade_in, fade_out=source.fade_out,
        time_map=deepcopy(source.time_map), source_audio_enabled=True,
        audio_gain_keyframes=deepcopy(source.audio_gain_keyframes),
        audio_mix_anchor=AudioMixAnchor(*effective_audio_mix_anchor(source)))
    source.source_audio_enabled = False
    project.clips.append(audio)
    editor._guard_track_edit_locks(editor.project, project)
    project.validate()
    return project, audio


def _snapshot(project, clip_id):
    selected = deepcopy(project)
    selected.clips = [c for c in selected.clips if c.id == clip_id]
    ids = {c.media_id for c in selected.clips}
    selected.media = [m for m in selected.media if m.id in ids]
    selected.media_bins = []; selected.media_bin_ids = {}
    selected.clip_groups = []; selected.video_transitions = []; selected.av_links = []
    selected.captions = []; selected.overlays = []
    selected.source_ranges = []; selected.timeline_markers = []
    payload = _json(selected.to_dict())
    if len(payload) > MAX_SNAPSHOT_BYTES:
        raise ValueError('Source audio separation exceeds its 8 MiB parameter budget')
    return payload


def plan_source_audio_separation(editor, clip_id, track) -> SourceAudioSeparationPlan:
    """Capture an explicit free A0/A1 destination; no file or project mutation."""
    editor._assert_owner()
    new_id = uuid4().hex
    candidate, _ = _candidate(editor, clip_id, track, new_id)
    return SourceAudioSeparationPlan(editor.session_id, editor.revision,
        _signature(editor.project), clip_id, track, new_id,
        _snapshot(editor.project, clip_id), _signature(candidate), uuid4().hex)


def _source_snapshot(plan):
    from .model import Project
    if (type(plan) is not SourceAudioSeparationPlan
            or type(plan.source_snapshot) is not str
            or len(plan.source_snapshot) > MAX_SNAPSHOT_BYTES):
        raise ValueError('Invalid source audio separation plan')
    project = Project.from_dict(json.loads(plan.source_snapshot))
    if (len(project.clips) != 1 or len(project.media) != 1
            or project.clips[0].id != plan.source_clip_id
            or project.clips[0].track not in (0, 1)
            or not project.clips[0].source_audio_enabled
            or not project.media[0].has_video or not project.media[0].has_audio
            or project.clip_groups or project.video_transitions or project.captions
            or project.overlays or project.source_ranges or project.timeline_markers):
        raise ValueError('Invalid source audio separation snapshot')
    return project.media[0]


def verify_source_audio_separation(plan, previous=None, cancel=None):
    """Worker-only Preview, then fresh Apply verification; no media extraction.

    Each worker reads at most 128 MiB, has a 60-second cooperative deadline and
    inspects a single local file twice. There is no 30-second source-cut limit.
    A receipt records observation, not a lock against later external changes.
    """
    from .clip_copy_paste import _full_dependency_identity
    media = _source_snapshot(plan)
    if previous is not None and (type(previous) is not SourceAudioSeparationVerification
            or previous.plan != plan or previous.phase != 'preview'):
        raise ValueError('Source audio separation preview is stale; preview again')
    deadline = time.monotonic() + MAX_VERIFY_SECONDS
    count = 0
    def check():
        if cancel is not None and cancel.is_set():
            raise SourceAudioSeparationCancelled('Source audio separation check cancelled')
        if time.monotonic() >= deadline:
            raise ValueError('Source audio separation check exceeded its 60-second budget')
    def reserve(amount):
        nonlocal count
        check()
        if count + amount > MAX_IDENTITY_BYTES:
            raise ValueError('Source audio separation exceeds its 128 MiB read budget')
        count += amount
    signature = hashlib.sha256(_json(asdict(media)).encode('ascii')).hexdigest()
    def inspect():
        check()
        provenance = media.sound_provenance or media.speech_provenance
        if provenance is None:
            reserve(3 * 65536)
            result = _identity(SimpleNamespace(media=media, signature=signature), cancel, deadline)
        else:
            result = _full_dependency_identity(media.path, signature, cancel,
                deadline, reserve, generated_sha256=provenance.output_sha256)
        check()
        return result
    first = inspect()
    if first != inspect():
        raise ValueError('Source changed during separation verification; preview again')
    if previous is not None and first != previous.identity:
        raise ValueError('Source changed after separation preview; preview again')
    return SourceAudioSeparationVerification(plan, first,
        'apply' if previous is not None else 'preview')


def apply_source_audio_separation(editor, plan, verification):
    """Owner-thread, once-only commit; all rejection paths preserve history."""
    editor._assert_owner()
    if (type(plan) is not SourceAudioSeparationPlan
            or type(verification) is not SourceAudioSeparationVerification
            or verification.plan != plan or verification.phase != 'apply'):
        raise ValueError('Source audio separation needs a fresh verified preview')
    if ((plan.session_id, plan.revision, plan.project_signature)
            != (editor.session_id, editor.revision, _signature(editor.project))):
        raise ValueError('Source audio separation plan is stale; preview again')
    if _snapshot(editor.project, plan.source_clip_id) != plan.source_snapshot:
        raise ValueError('Source audio separation snapshot changed; preview again')
    candidate, audio = _candidate(editor, plan.source_clip_id, plan.track, plan.new_clip_id)
    if _signature(candidate) != plan.candidate_signature:
        raise ValueError('Source audio separation plan changed; preview again')
    return editor._commit_source_audio_separation(plan.source_clip_id, audio)

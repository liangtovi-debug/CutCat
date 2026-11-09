"""Window-private whole-clip snapshots and atomic, original-track Paste.

Planning and commit do no file I/O. Only verify_* belongs on a cancellable
worker. Ordinary media use the existing bounded sampled identity; generated
sources and LUTs require full hashes. No source-cut/decode duration limit applies.
A receipt is a fresh observation, not a filesystem lock or adversarial seal.
"""
from __future__ import annotations
from .audio_components import clip_emits_source_audio

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4

from .file_identity import is_plain_file, path_matches_descriptor, same_file_state
from .source_overwrite import _signature
from .source_viewer import SourceIdentity, _identity, _state

MAX_COPY_CLIPS = 2000
MAX_COPY_DEPENDENCIES = 128
MAX_COPY_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_DEPENDENCY_BYTES = 256 * 1024 * 1024
MAX_LUT_BYTES = 64 * 1024 * 1024
MAX_VERIFY_SECONDS = 60.0


class ClipCopyCancelled(ValueError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('ascii')).hexdigest()


@dataclass(frozen=True)
class ClipCopyRequest:
    session_id: str
    revision: int
    project_signature: str
    token: str
    payload: str
    anchor: int
    fps_num: int
    fps_den: int
    clip_count: int


@dataclass(frozen=True)
class ClipDependency:
    kind: str
    key: str
    identity: SourceIdentity


@dataclass(frozen=True)
class ClipClipboard:
    request: ClipCopyRequest
    dependencies: tuple[ClipDependency, ...]

    @property
    def session_id(self): return self.request.session_id
    @property
    def token(self): return self.request.token
    @property
    def fps_num(self): return self.request.fps_num
    @property
    def fps_den(self): return self.request.fps_den
    @property
    def anchor(self): return self.request.anchor
    @property
    def clip_count(self): return self.request.clip_count


@dataclass(frozen=True)
class ClipPastePlan:
    session_id: str
    revision: int
    project_signature: str
    clipboard_signature: str
    copy_token: str
    token: str
    destination: int
    new_clip_ids: tuple[str, ...]
    new_group_ids: tuple[str, ...]
    new_effect_ids: tuple[tuple[str, ...], ...]
    candidate_signature: str
    # Appended for callers that construct the pre-link plan positionally.
    new_link_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClipPasteVerification:
    plan: ClipPastePlan
    clipboard_signature: str


def _snapshot(request):
    from .model import Project
    if (type(request) is not ClipCopyRequest or type(request.payload) is not str
            or len(request.payload) > MAX_COPY_PAYLOAD_BYTES):
        raise ValueError('Invalid clip copy snapshot')
    project = Project.from_dict(json.loads(request.payload))
    if (not 1 <= len(project.clips) <= MAX_COPY_CLIPS
            or request.clip_count != len(project.clips)
            or request.anchor != min(clip.start for clip in project.clips)
            or (request.fps_num, request.fps_den) != (project.fps_num, project.fps_den)
            or project.video_transitions or project.captions or project.overlays
            or project.source_ranges or project.timeline_markers):
        raise ValueError('Invalid clip copy snapshot')
    return project


def prepare_clip_copy(editor, selected_ids) -> ClipCopyRequest:
    """Capture committed values only. Reading an edit-locked source is allowed."""
    editor._assert_owner()
    editor.project.validate()
    ids = editor.expanded_clip_ids(selected_ids)
    if not 1 <= len(ids) <= MAX_COPY_CLIPS:
        raise ValueError('Copy clips requires 1 to 2000 selected timeline clips')
    selected = set(ids)
    if any(selected.intersection((t.left_clip_id, t.right_clip_id))
           for t in editor.project.video_transitions):
        raise ValueError('Copy clips cannot include a video transition endpoint; remove the transition first')
    project = deepcopy(editor.project)
    project.clips = [clip for clip in project.clips if clip.id in selected]
    project.clip_groups = [group for group in project.clip_groups if selected.intersection(group.clip_ids)]
    project.av_links = [link for link in project.av_links if selected.intersection(link.clip_ids)]
    media_ids = {clip.media_id for clip in project.clips}
    project.media = [media for media in project.media if media.id in media_ids]
    project.media_bins = []; project.media_bin_ids = {}
    project.video_transitions = []; project.captions = []; project.overlays = []
    project.source_ranges = []; project.timeline_markers = []
    payload = _json(project.to_dict())
    if len(payload) > MAX_COPY_PAYLOAD_BYTES:
        raise ValueError('Clip copy snapshot exceeds its 8 MiB parameter budget')
    return ClipCopyRequest(editor.session_id, editor.revision, _signature(editor.project),
        uuid4().hex, payload, min(clip.start for clip in project.clips),
        project.fps_num, project.fps_den, len(project.clips))


def _check(cancel, deadline):
    if cancel is not None and cancel.is_set():
        raise ClipCopyCancelled('Clip dependency verification cancelled')
    if time.monotonic() >= deadline:
        raise ValueError('Clip dependency verification exceeded its 60-second budget')


def _full_dependency_identity(raw_path, signature, cancel, deadline, reserve,
                              *, generated_sha256=None):
    """Full bounded hash; reserve actual loop reads, not a racy earlier stat."""
    _check(cancel, deadline)
    requested = Path(raw_path).expanduser().absolute()
    requested_before = requested.lstat()
    label = 'Generated source' if generated_sha256 is not None else 'LUT'
    if not is_plain_file(requested_before):
        raise ValueError(f'Clip copy requires a plain local {label} file')
    path = requested.resolve(strict=True)
    before = path.lstat()
    if not is_plain_file(before) or not same_file_state(requested_before, before):
        raise ValueError(f'{label} changed while resolving its local path')
    if before.st_size > MAX_LUT_BYTES:
        raise ValueError(f'{label} exceeds the 64 MiB identity-check budget')
    digest = hashlib.sha256(); count = 0
    with path.open('rb') as stream:
        descriptor = os.fstat(stream.fileno())
        if not path_matches_descriptor(before, descriptor):
            raise ValueError(f'{label} changed while being opened')
        while count < before.st_size:
            _check(cancel, deadline)
            wanted = min(1024 * 1024, before.st_size - count)
            reserve(wanted)  # Shared hard budget, charged before the read.
            block = stream.read(wanted)
            if not block:
                raise ValueError(f'{label} changed during inspection')
            count += len(block); digest.update(block)
        after = path.lstat(); descriptor_after = os.fstat(stream.fileno())
        if (not same_file_state(before, after)
                or not same_file_state(requested_before, requested.lstat())
                or not same_file_state(descriptor, descriptor_after)
                or not path_matches_descriptor(after, descriptor_after)):
            raise ValueError(f'{label} changed during inspection')
    if generated_sha256 is not None and digest.hexdigest() != generated_sha256.lower():
        raise ValueError('Generated source has changed; restore or regenerate the original audio.')
    return SourceIdentity(str(path), (_state(after), _state(descriptor_after)),
                          digest.hexdigest(), signature)


def _dependencies(request, cancel, *, deadline=None):
    project = _snapshot(request)
    entries = [('media', media.id, media.path, media) for media in project.media]
    entries += [('lut', path, path, None) for path in sorted({
        clip.grade.lut_path for clip in project.clips if clip.grade.lut_path})]
    if len(entries) > MAX_COPY_DEPENDENCIES:
        raise ValueError('Clip copy exceeds its 128-dependency budget')
    local_deadline = time.monotonic() + MAX_VERIFY_SECONDS
    deadline = local_deadline if deadline is None else min(deadline, local_deadline)
    read_budget = 0
    def reserve(count):
        nonlocal read_budget
        if read_budget + count > MAX_DEPENDENCY_BYTES:
            raise ValueError('Clip dependency verification exceeds its 256 MiB read budget')
        read_budget += count
    def inspect(entry):
        kind, key, path, media = entry
        _check(cancel, deadline)
        if kind == 'lut':
            identity = _full_dependency_identity(path, _digest(('lut', path)),
                                                 cancel, deadline, reserve)
        else:
            signature = _digest(asdict(media))
            provenance = media.sound_provenance or media.speech_provenance
            if provenance is not None:
                identity = _full_dependency_identity(path, signature, cancel, deadline,
                    reserve, generated_sha256=provenance.output_sha256)
            else:
                # Existing ordinary-source identity reads exactly three bounded
                # samples at most, regardless of concurrent file-size changes.
                reserve(3 * 65536)
                session = SimpleNamespace(media=media, signature=signature)
                identity = _identity(session, cancel, deadline)
        _check(cancel, deadline)
        return ClipDependency(kind, key, identity)
    first = tuple(inspect(entry) for entry in entries)
    # Earlier dependencies must still match when the whole batch completes.
    if first != tuple(inspect(entry) for entry in entries):
        raise ValueError('Clip dependencies changed during verification')
    return first


def verify_clip_copy(request, cancel=None) -> ClipClipboard:
    """Worker-only I/O. Failure never replaces the window's last good copy."""
    return ClipClipboard(request, _dependencies(request, cancel))


def accept_clip_copy(editor, request, clipboard) -> ClipClipboard:
    editor._assert_owner()
    if (type(clipboard) is not ClipClipboard or clipboard.request != request
            or (request.session_id, request.revision, request.project_signature)
            != (editor.session_id, editor.revision, _signature(editor.project))):
        raise ValueError('Clip copy request is stale; copy the committed selection again')
    _snapshot(request)
    return clipboard


def _clipboard_signature(clipboard):
    if type(clipboard) is not ClipClipboard:
        raise ValueError('No verified clip copy is available')
    return _digest(asdict(clipboard))


def _candidate(editor, clipboard, destination, clip_ids, group_ids, effect_ids, link_ids=()):
    from .model import MAX_CAPTION_FRAME, _integer, _text
    from .clip_audio_translation import guard_local_audio_translation, guard_common_audio_translation
    from .timing import sample_boundary
    from .audio_timing import compile_audio_timing
    if type(clipboard) is not ClipClipboard:
        raise ValueError('No verified clip copy is available')
    original = _snapshot(clipboard.request)
    project = editor.project
    if clipboard.session_id != editor.session_id:
        raise ValueError('Clip copy belongs to another project session; copy again')
    if (clipboard.fps_num, clipboard.fps_den) != (project.fps_num, project.fps_den):
        raise ValueError('Project frame rate changed after Copy; copy again')
    _integer(destination, 'paste destination', 0, MAX_CAPTION_FRAME)
    current_media = {media.id: media for media in project.media}
    if any(current_media.get(media.id) != media for media in original.media):
        raise ValueError('Copied source media is missing, relinked or reinterpreted; copy again')
    if (type(clip_ids) is not tuple or type(group_ids) is not tuple or type(effect_ids) is not tuple
            or type(link_ids) is not tuple
            or len(clip_ids) != len(original.clips) or len(group_ids) != len(original.clip_groups)
            or len(effect_ids) != len(original.clips) or len(link_ids) != len(original.av_links)):
        raise ValueError('Invalid clip paste identities')
    all_ids = [*clip_ids, *group_ids, *link_ids]
    for values, clip in zip(effect_ids, original.clips):
        if type(values) is not tuple or len(values) != len(clip.effects):
            raise ValueError('Invalid clip paste effect identities')
        all_ids.extend(values)
    for value in all_ids: _text(value, 'new paste identity', True)
    existing = {item.id for item in project.clips + project.clip_groups + project.av_links}
    existing.update(effect.id for clip in project.clips for effect in clip.effects)
    if len(set(all_ids)) != len(all_ids) or existing.intersection(all_ids):
        raise ValueError('Clip paste identities must be new and distinct')
    mapping = dict(zip((clip.id for clip in original.clips), clip_ids))
    candidate = deepcopy(project)
    clones = []
    linked_ids = {clip_id for link in original.av_links for clip_id in link.clip_ids}
    expected = sample_boundary(destination, project.fps) - sample_boundary(clipboard.anchor, project.fps)
    for clip, new_id, effects in zip(original.clips, clip_ids, effect_ids):
        editor.guard_track_editable(clip.track)
        clone = deepcopy(clip)
        clone.id = new_id
        # Copy creates an independent contribution, not another handoff into
        # the original summation slot. Disabled picture stays disabled.
        clone.audio_mix_anchor = None
        clone.start = destination + clip.start - clipboard.anchor
        _integer(clone.start, 'paste start', 0, MAX_CAPTION_FRAME)
        _integer(clone.end, 'paste end', 1, MAX_CAPTION_FRAME)
        for effect, new_effect_id in zip(clone.effects, effects): effect.id = new_effect_id
        media = current_media[clip.media_id]
        # Muting is reversible. A linked copy must keep the complete potential
        # audio contract even when its A member is currently muted. These
        # detached proof records never change the user's saved mute state.
        proof_clip = replace(clip, muted=False) if clip.id in linked_ids else clip
        proof_clone = replace(clone, muted=False) if clip.id in linked_ids else clone
        silent_pair = (clip_emits_source_audio(proof_clip, media)
            and compile_audio_timing(proof_clip, project.fps, media.audio_offset).silent
            and compile_audio_timing(proof_clone, project.fps, media.audio_offset).silent)
        # AudioMixBuilder emits no contribution for a fully silent source
        # interval. Its unrendered sample budget adds no phase constraint.
        if not silent_pair:
            guard_local_audio_translation(proof_clip, proof_clone, project, operation='Clip paste')
        guard_common_audio_translation(proof_clip, proof_clone, project, expected_delta=expected,
            message='Clip paste would change relative audio synchronization at this sample-grid phase; choose another project frame')
        clones.append(clone)
    groups = deepcopy(original.clip_groups)
    for group, new_id in zip(groups, group_ids):
        group.id = new_id; group.clip_ids = [mapping[value] for value in group.clip_ids]
    links = [replace(link, id=new_id,
                     video_clip_id=mapping[link.video_clip_id],
                     audio_clip_id=mapping[link.audio_clip_id])
             for link, new_id in zip(original.av_links, link_ids)]
    candidate.clips.extend(clones); candidate.clip_groups.extend(groups)
    candidate.av_links.extend(links)
    candidate.validate()
    return candidate, clones, groups, links


def plan_clip_paste(editor, clipboard, destination) -> ClipPastePlan:
    editor._assert_owner()
    signature = _clipboard_signature(clipboard)
    original = _snapshot(clipboard.request)
    clip_ids = tuple(uuid4().hex for _ in original.clips)
    group_ids = tuple(uuid4().hex for _ in original.clip_groups)
    link_ids = tuple(uuid4().hex for _ in original.av_links)
    effect_ids = tuple(tuple(uuid4().hex for _ in clip.effects) for clip in original.clips)
    candidate, _, _, _ = _candidate(editor, clipboard, destination, clip_ids, group_ids, effect_ids, link_ids)
    return ClipPastePlan(editor.session_id, editor.revision, _signature(editor.project),
        signature, clipboard.token, uuid4().hex, destination, clip_ids, group_ids,
        effect_ids, _signature(candidate), link_ids)


def verify_clip_paste(plan, clipboard, cancel=None) -> ClipPasteVerification:
    """Worker-only freshness verification; no live Editor is accessed."""
    signature = _clipboard_signature(clipboard)
    if (type(plan) is not ClipPastePlan or plan.clipboard_signature != signature
            or plan.copy_token != clipboard.token or plan.session_id != clipboard.session_id):
        raise ValueError('Clip paste plan does not match the current copy')
    if _dependencies(clipboard.request, cancel) != clipboard.dependencies:
        raise ValueError('Copied media or LUT content changed after Copy; copy again')
    return ClipPasteVerification(plan, signature)


def apply_clip_paste(editor, clipboard, plan, verification):
    editor._assert_owner()
    signature = _clipboard_signature(clipboard)
    if (type(plan) is not ClipPastePlan or type(verification) is not ClipPasteVerification
            or verification.plan != plan or verification.clipboard_signature != signature
            or plan.clipboard_signature != signature or plan.copy_token != clipboard.token):
        raise ValueError('Clip paste verification is stale; paste again')
    if ((plan.session_id, plan.revision, plan.project_signature)
            != (editor.session_id, editor.revision, _signature(editor.project))):
        raise ValueError('Clip paste plan is stale; paste again')
    candidate, clips, groups, links = _candidate(editor, clipboard, plan.destination,
        plan.new_clip_ids, plan.new_group_ids, plan.new_effect_ids, plan.new_link_ids)
    if _signature(candidate) != plan.candidate_signature:
        raise ValueError('Clip paste plan was changed; paste again')
    return editor._commit_clip_paste(clips, groups, av_links=links)

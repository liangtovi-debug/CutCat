"""Atomic source Insert with explicit per-operation downstream participation.

Whole downstream clips retain their source ranges. Their complete local audio
compiler output must be identical before/after translation; only the final
placement delay may differ. Cross-track participation additionally requires one
common integer sample displacement. No decoding or live project mutation occurs.
"""
from __future__ import annotations
from .audio_components import clip_emits_source_audio

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import TYPE_CHECKING
from uuid import uuid4

from .source_overwrite import (OverwriteRemnant, _guard_pcm_remnant,
                               _guard_picture_rate, _guard_safe_clip, _signature,
                               complete_source_frames)

if TYPE_CHECKING:
    from .model import Clip, Editor, Project


@dataclass(frozen=True)
class InsertTranslation:
    original_clip_id: str
    previous_start: int
    start: int
    duration: int


@dataclass(frozen=True)
class InsertItemImpact:
    """Every pre-existing item is explicitly fixed, moved, or destination-split.

    Split pieces have their exact new identities/ranges in ``remnants``; this
    record identifies the complete original item rather than inventing a trim.
    """
    category: str
    item_id: str
    track: int | None
    previous_start: int
    start: int
    duration: int
    disposition: str


@dataclass(frozen=True)
class SourceInsertPlan:
    session_id: str
    revision: int
    project_signature: str
    fps_num: int
    fps_den: int
    media_id: str
    track: int
    start: int
    source_in: int
    source_out: int
    new_clip_id: str
    affected_clip_ids: tuple[str, ...]
    remnants: tuple[OverwriteRemnant, ...]
    translations: tuple[InsertTranslation, ...]
    candidate_signature: str
    participating_tracks: tuple[int, ...]
    follow_captions: bool
    follow_stickers: bool
    item_impacts: tuple[InsertItemImpact, ...]

    @property
    def cross_track(self) -> bool:
        return (len(self.participating_tracks) > 1
                or self.follow_captions or self.follow_stickers)

    @property
    def duration(self) -> int:
        return self.source_out - self.source_in

    @property
    def end(self) -> int:
        return self.start + self.duration

    @property
    def proof_clip_ids(self) -> tuple[str, ...]:
        # Pure translations preserve source trim/cadence and require no decode.
        return tuple(dict.fromkeys(item.original_clip_id for item in self.remnants))


def _local_audio(clip: Clip, project: Project):
    from .clip_audio_translation import local_audio_contract
    return local_audio_contract(clip, project, operation='Source insert')


def _guard_pcm_translation(original: Clip, translated: Clip, project: Project) -> None:
    if _local_audio(original, project) != _local_audio(translated, project):
        raise ValueError('Source insert would change downstream audio at this sample-grid phase; choose another insertion length or project frame')


def _guard_common_pcm_shift(original: Clip, translated: Clip, project: Project,
                            cut: int, duration: int) -> None:
    """Local equivalence alone does not prove relative cross-track sync.

    At 30000/1001 fps, moving five-frame clips at frames 1 and 2 by one
    frame leaves both local lengths 8008, but shifts them by 1601/1602 samples.
    Preserve the insertion boundary's one exact sample displacement instead
    of independently rounding followers or changing the historical compiler.
    """
    from .audio_timing import compile_audio_timing
    from .timing import sample_boundary

    media = next(item for item in project.media if item.id == original.media_id)
    if not clip_emits_source_audio(original, media):
        return
    old = compile_audio_timing(original, project.fps, media.audio_offset)
    new = compile_audio_timing(translated, project.fps, media.audio_offset)
    if old.silent and new.silent:
        return
    expected = sample_boundary(cut + duration, project.fps) - sample_boundary(cut, project.fps)
    if new.start_sample - old.start_sample != expected:
        raise ValueError('Source insert participation would change relative audio synchronization at this sample-grid phase; choose another insertion length or project frame')


def _participation(track, participating_tracks, follow_captions, follow_stickers):
    from .model import _boolean, _integer

    if participating_tracks is None:
        participating_tracks = (track,)
    if type(participating_tracks) is not tuple or not participating_tracks:
        raise ValueError('Source insert participating tracks must be a nonempty tuple')
    for value in participating_tracks:
        _integer(value, 'insert participating track', 0, 3)
    if len(set(participating_tracks)) != len(participating_tracks) or track not in participating_tracks:
        raise ValueError('Source insert participating tracks must be unique and include the destination')
    _boolean(follow_captions, 'insert follow captions')
    _boolean(follow_stickers, 'insert follow stickers')
    return tuple(sorted(participating_tracks))


def insert_error_message(message: str) -> str:
    """Name the requested edit without rewriting a source path/detail suffix."""
    for old, new in (('Source overwrite ', 'Source insert '),
                     ('Overwrite ', 'Insert '), ('Audio overwrite ', 'Audio insert '),
                     ('Picture overwrite ', 'Picture insert '),
                     ('Cannot prove overwrite source timing:', 'Cannot prove insert source timing:')):
        if message.startswith(old):
            return new + message[len(old):]
    return {
        'Affected source timing is unsupported for overwrite proof.':
            'Affected source timing is unsupported for insert proof.',
        'Source range is incomplete or variable-rate; overwrite cannot be proven.':
            'Source range is incomplete or variable-rate; insert cannot be proven.',
    }.get(message, message)


def _candidate(*args, **kwargs):
    try:
        return _build_candidate(*args, **kwargs)
    except ValueError as exc:
        message = insert_error_message(str(exc))
        if message == str(exc):
            raise
        raise ValueError(message) from exc


def _build_candidate(editor: Editor, media_id: str, track: int, start: int,
               source_in: int, source_out: int, new_clip_id: str,
               right_ids: dict[str, str] | None = None, *,
               participating_tracks: tuple[int, ...] | None = None,
               follow_captions: bool = False, follow_stickers: bool = False):
    from .model import Clip, MAX_CAPTION_FRAME, _integer, _text

    _text(media_id, 'source media id', True)
    _text(new_clip_id, 'new insert clip id', True)
    _integer(track, 'insert track', 0, 3)
    tracks = _participation(track, participating_tracks, follow_captions, follow_stickers)
    cross_track = len(tracks) > 1 or follow_captions or follow_stickers
    _integer(start, 'insert start', 0, MAX_CAPTION_FRAME)
    _integer(source_in, 'source In', 0, MAX_CAPTION_FRAME)
    _integer(source_out, 'source Out', 1, MAX_CAPTION_FRAME)
    if source_out <= source_in:
        raise ValueError('Source Out must be after In (exclusive)')
    duration = source_out - source_in
    _integer(start + duration, 'insert end', 1, MAX_CAPTION_FRAME)
    project = deepcopy(editor.project)
    project.validate()
    editor.guard_track_editable(track)
    media = next((item for item in project.media if item.id == media_id), None)
    if media is None:
        raise ValueError('Unknown source media')
    if source_out > complete_source_frames(media, project.fps):
        raise ValueError('Source Out exceeds the complete source media range')
    if (track < 2 and not media.has_video) or (track >= 2 and not media.has_audio):
        raise ValueError('Source media is incompatible with the target track')
    _guard_picture_rate(media, track, project.fps)
    if any(clip.id == new_clip_id for clip in project.clips):
        raise ValueError('New insert clip identity already exists')
    affected, remnants, translations, output, impacts = [], [], [], [], []
    def impact(category, item, item_track, moved_start=None, disposition='fixed'):
        impacts.append(InsertItemImpact(category, item.id, item_track, item.start,
            item.start if moved_start is None else moved_start, item.duration, disposition))
    for clip in project.clips:
        if clip.track not in tracks or clip.end <= start:
            output.append(clip)
            impact('clip', clip, clip.track)
            continue
        if clip.track != track and clip.start < start:
            raise ValueError(f'Source insert follower clip {clip.id} spans the insertion point; leave its track fixed or choose another point')
        editor.guard_track_editable(clip.track)
        affected.append(clip.id)
        editor._guard_linked_clip(clip.id)
        editor._guard_grouped_clip(clip.id)
        editor._guard_transition_endpoint(clip.id)
        if clip.start >= start:
            # Whole-clip fades may be retained ONLY when their actual compiled
            # local sample boundaries remain byte-for-byte equivalent.
            _guard_safe_clip(clip, project, allow_fades=True, allow_special_audio_state=True)
            translated = deepcopy(clip)
            translated.start += duration
            _integer(translated.end, 'translated clip end', 1, MAX_CAPTION_FRAME)
            _guard_pcm_translation(clip, translated, project)
            if cross_track:
                _guard_common_pcm_shift(clip, translated, project, start, duration)
            impact('clip', clip, clip.track, translated.start, 'move')
            output.append(translated)
            translations.append(InsertTranslation(clip.id, clip.start,
                                                   translated.start, clip.duration))
            continue
        _guard_safe_clip(clip, project)
        left = deepcopy(clip)
        left.duration = start - clip.start
        left.source_in = int(clip.source_in)
        left.time_map = None
        _guard_pcm_remnant(clip, left, project)
        right = deepcopy(clip)
        if right_ids is None:
            right.id = uuid4().hex
        else:
            try:
                right.id = right_ids[clip.id]
            except KeyError as exc:
                raise ValueError('Invalid insert remnant plan') from exc
        right.start = start
        right.source_in = int(clip.source_in) + start - clip.start
        right.duration = clip.end - start
        right.time_map = None
        _guard_pcm_remnant(clip, right, project)
        shifted_right = deepcopy(right)
        shifted_right.start += duration
        _integer(shifted_right.end, 'insert remnant end', 1, MAX_CAPTION_FRAME)
        _guard_pcm_translation(right, shifted_right, project)
        if cross_track:
            _guard_common_pcm_shift(right, shifted_right, project, start, duration)
        impact('clip', clip, clip.track, disposition='split')
        output.extend((left, shifted_right))
        for surviving in (left, shifted_right):
            remnants.append(OverwriteRemnant(clip.id, surviving.id, surviving.start,
                                             surviving.source_in, surviving.duration))
    inserted = Clip(new_clip_id, media_id, track, start, source_in, duration)
    if media.input_encoding == 'apple_log_bt2020':
        inserted.grade.input_encoding = media.input_encoding
    output.append(inserted)
    project.clips = output
    for category, collection, follows in (('caption', project.captions, follow_captions),
                                           ('sticker', project.overlays, follow_stickers)):
        for item in collection:
            if not follows or item.end <= start:
                impact(category, item, None)
            elif item.start < start:
                raise ValueError(f'Source insert follower {category} {item.id} spans the insertion point; leave this category fixed or choose another point')
            else:
                _integer(item.end + duration, f'translated {category} end', 1, MAX_CAPTION_FRAME)
                impact(category, item, None, item.start + duration, 'move')
                item.start += duration
    editor._guard_track_edit_locks(editor.project, project)
    project.validate()
    return project, tuple(affected), tuple(remnants), tuple(translations), tuple(impacts), tracks


def _candidate_signature(project, tracks, follow_captions, follow_stickers):
    # Bind choices even when a selected track/category is currently empty.
    # They are session operation intent, deliberately not saved project fields.
    value = (_signature(project), tracks, follow_captions, follow_stickers)
    return sha256(json.dumps(value, separators=(',', ':')).encode('ascii')).hexdigest()


def plan_source_insert(editor: Editor, media_id: str, track: int, start: int,
                       source_in: int, source_out: int, *,
                       participating_tracks: tuple[int, ...] | None = None,
                       follow_captions: bool = False,
                       follow_stickers: bool = False) -> SourceInsertPlan:
    editor._assert_owner()
    signature = _signature(editor.project)
    new_id = uuid4().hex
    candidate, affected, remnants, translations, impacts, tracks = _candidate(
        editor, media_id, track, start, source_in, source_out, new_id,
        participating_tracks=participating_tracks, follow_captions=follow_captions,
        follow_stickers=follow_stickers)
    return SourceInsertPlan(editor.session_id, editor.revision, signature,
        editor.project.fps_num, editor.project.fps_den, media_id, track, start,
        source_in, source_out, new_id, affected, remnants, translations,
        _candidate_signature(candidate, tracks, follow_captions, follow_stickers),
        tracks, follow_captions, follow_stickers, impacts)


def apply_source_insert(editor: Editor, plan: SourceInsertPlan) -> Clip:
    editor._assert_owner()
    if type(plan) is not SourceInsertPlan:
        raise ValueError('Invalid source insert plan')
    if ((plan.session_id, plan.revision, plan.fps_num, plan.fps_den)
            != (editor.session_id, editor.revision, editor.project.fps_num, editor.project.fps_den)
            or plan.project_signature != _signature(editor.project)):
        raise ValueError('Source insert plan is stale; preview the source selection again')
    if (type(plan.affected_clip_ids) is not tuple or type(plan.remnants) is not tuple
            or type(plan.translations) is not tuple or type(plan.item_impacts) is not tuple
            or type(plan.participating_tracks) is not tuple
            or type(plan.follow_captions) is not bool or type(plan.follow_stickers) is not bool
            or any(type(item) is not InsertItemImpact for item in plan.item_impacts)
            or any(type(item) is not OverwriteRemnant for item in plan.remnants)
            or any(type(item) is not InsertTranslation for item in plan.translations)):
        raise ValueError('Invalid source insert plan')
    right_ids = {item.original_clip_id: item.clip_id for item in plan.remnants
                 if item.original_clip_id != item.clip_id}
    candidate, affected, remnants, translations, impacts, tracks = _candidate(editor, plan.media_id,
        plan.track, plan.start, plan.source_in, plan.source_out, plan.new_clip_id, right_ids,
        participating_tracks=plan.participating_tracks, follow_captions=plan.follow_captions,
        follow_stickers=plan.follow_stickers)
    if (affected != plan.affected_clip_ids or remnants != plan.remnants
            or translations != plan.translations or impacts != plan.item_impacts
            or tracks != plan.participating_tracks
            or _candidate_signature(candidate, tracks, plan.follow_captions,
                                    plan.follow_stickers) != plan.candidate_signature):
        raise ValueError('Source insert plan was changed; preview the source selection again')

    def change():
        unaffected = {clip.id: clip for clip in editor.project.clips if clip.id not in affected}
        editor.project.clips[:] = [unaffected.get(clip.id, clip) for clip in candidate.clips]
        # All changed collections belong to this same central transaction.
        # Preserve live identities and byte-equivalent records for fixed items.
        for category, name in (('caption', 'captions'), ('sticker', 'overlays')):
            moved = {item.item_id for item in impacts
                     if item.category == category and item.disposition == 'move'}
            if moved:
                collection = getattr(editor.project, name)
                fixed = {item.id: item for item in collection if item.id not in moved}
                collection[:] = [fixed.get(item.id, item) for item in getattr(candidate, name)]
        return next(clip for clip in editor.project.clips if clip.id == plan.new_clip_id)
    return editor._commit(change)

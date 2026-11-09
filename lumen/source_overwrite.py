"""Pure, immutable plans for a conservative single-track source overwrite.

All positions are whole *project* frames, never native source-frame indices.
The source interval is half-open and plays at 1x. No media I/O occurs here:
the source viewer must separately verify its captured file identity before Apply.
"""
from __future__ import annotations
from .audio_components import clip_emits_source_audio

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
import json
import math
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from .model import Clip, Editor, Media, Project


@dataclass(frozen=True)
class OverwriteRemnant:
    original_clip_id: str
    clip_id: str
    start: int
    source_in: int
    duration: int


@dataclass(frozen=True)
class SourceOverwritePlan:
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
    candidate_signature: str

    @property
    def duration(self) -> int:
        return self.source_out - self.source_in

    @property
    def end(self) -> int:
        return self.start + self.duration


def complete_source_frames(media: Media, project_fps: Fraction) -> int:
    """Count only complete project-frame units, excluding a padded source tail.

    Exact PCM sample counts and decoded video endpoints supersede rounded float
    duration. In particular, video-only imports store float(decoded_endpoint)
    as display metadata; taking their minimum can lose the last complete frame.
    Floor the exact endpoint itself, never round a genuine partial frame up.
    Longer embedded audio does not extend the decoded picture endpoint.
    This deliberately differs from Project.media_frames for partial final PCM
    frames, which remain supported by existing non-overwrite editing paths.
    """
    from .model import Media

    if not isinstance(media, Media):
        raise ValueError("Source overwrite requires valid media")
    if (type(project_fps) is not int and not isinstance(project_fps, Fraction)) or project_fps <= 0:
        raise ValueError("Source overwrite requires an exact positive project frame rate")
    if (isinstance(media.duration, bool) or not isinstance(media.duration, (int, float))
            or not math.isfinite(media.duration) or media.duration < 0):
        raise ValueError("Source overwrite requires a finite nonnegative source duration")
    provenance = media.sound_provenance or media.speech_provenance
    if provenance is not None and provenance.sample_count is not None:
        duration = Fraction(provenance.sample_count, provenance.sample_rate)
    elif media.has_video and media.decoded_video_end_num is not None:
        duration = Fraction(media.decoded_video_end_num, media.decoded_video_end_den)
    else:
        duration = Fraction(str(media.duration))
    return math.floor(duration * project_fps)


def _signature(project: Project) -> str:
    # Project.to_dict validates/canonicalizes master gain. Do that on a detached
    # snapshot so even rejected previews never mutate the live project.
    data = deepcopy(project).to_dict()
    return sha256(json.dumps(data, sort_keys=True, ensure_ascii=True,
                             allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _guard_safe_clip(clip: Clip, project: Project, *, allow_fades: bool = False,
                     allow_special_audio_state: bool = False) -> None:
    from .clip_transform import is_identity
    from .restoration_settings import RestorationSettings
    from .stabilization_settings import StabilizationSettings

    if not allow_special_audio_state and (not clip.source_audio_enabled or clip.audio_mix_anchor is not None):
        raise ValueError("Source overwrite cannot cut separated source audio clips yet")
    if not allow_special_audio_state and clip.audio_gain_keyframes:
        raise ValueError("Source overwrite cannot cut clips with audio gain keyframes yet")
    mapping = clip.timing
    media = next(item for item in project.media if item.id == clip.media_id)
    _guard_picture_rate(media, clip.track, project.fps)
    if (mapping.speed != 1 or mapping.source_in.denominator != 1
            or mapping.source_out.denominator != 1
            or mapping.source_out - mapping.source_in != clip.duration
            or mapping.source_out > complete_source_frames(media, project.fps)):
        raise ValueError("Source overwrite requires affected clips with whole-frame 1x timing, without a partial source tail")
    if ((not allow_fades and (clip.fade_in or clip.fade_out)) or clip.motion_keyframes
            or clip.grade.qualifier.window_keyframes or clip.effects
            or clip.stabilization != StabilizationSettings()
            or clip.restoration != RestorationSettings()
            or not is_identity(clip.transform) or not clip.composite.is_legacy()):
        raise ValueError("Source overwrite cannot cut affected fades, animation, effects, stabilization, restoration, transforms or transparent compositing")


def _guard_picture_rate(media: Media, track: int, project_fps: Fraction) -> None:
    # Existing schema-37 1x rendering trims before native-to-project frame-rate
    # normalization. Cutting mixed-rate video can change cadence beyond the
    # overwritten span. Do not alter historical rendering or guess a rebase.
    # The viewer additionally verifies actual CFR/PTS in its I/O preflight.
    if track < 2 and Fraction(media.fps_num, media.fps_den) != project_fps:
        raise ValueError("Source overwrite does not support mixed-rate video; use source media at the exact project frame rate")


def _guard_pcm_remnant(original: Clip, remnant: Clip, project: Project) -> None:
    """Keep exactly the original 48 kHz samples outside the overwritten span.

    At fractional frame rates, independently rounded new source and timeline
    boundaries need not agree. Reject those cuts, rather than silently moving
    the retained audio by a sample or changing its trailing silence.
    """
    from .audio_timing import compile_audio_timing

    media = next(item for item in project.media if item.id == original.media_id)
    if not clip_emits_source_audio(original, media):
        return
    old = compile_audio_timing(original, project.fps, media.audio_offset)
    new = compile_audio_timing(remnant, project.fps, media.audio_offset)

    def active(plan, lo, hi):
        if plan.silent:
            return None
        onset = plan.start_sample + plan.leading_samples
        first = max(lo, onset)
        last = min(hi, plan.start_sample + plan.sample_count,
                   onset + plan.source_end_sample - plan.source_start_sample)
        return (first, last, plan.source_start_sample - onset) if first < last else None

    lo, hi = new.start_sample, new.start_sample + new.sample_count
    if active(old, lo, hi) != active(new, lo, hi):
        raise ValueError("Source overwrite would change retained audio at this sample-grid boundary; choose another project frame")


def _candidate(editor: Editor, media_id: str, track: int, start: int,
               source_in: int, source_out: int, new_clip_id: str,
               second_remnant_ids: dict[str, str] | None = None
               ) -> tuple[Project, tuple[str, ...], tuple[OverwriteRemnant, ...]]:
    from .model import Clip, MAX_CAPTION_FRAME, _integer, _text

    _text(media_id, "source media id", True)
    _text(new_clip_id, "new overwrite clip id", True)
    _integer(track, "overwrite track", 0, 3)
    _integer(start, "overwrite start", 0, MAX_CAPTION_FRAME)
    _integer(source_in, "source In", 0, MAX_CAPTION_FRAME)
    _integer(source_out, "source Out", 1, MAX_CAPTION_FRAME)
    if source_out <= source_in:
        raise ValueError("Source Out must be after In (exclusive)")
    end = start + source_out - source_in
    _integer(end, "overwrite end", 1, MAX_CAPTION_FRAME)
    project = deepcopy(editor.project)
    project.validate()
    editor.guard_track_editable(track)
    media = next((item for item in project.media if item.id == media_id), None)
    if media is None:
        raise ValueError("Unknown source media")
    if source_out > complete_source_frames(media, project.fps):
        raise ValueError("Source Out exceeds the complete source media range")
    if (track < 2 and not media.has_video) or (track >= 2 and not media.has_audio):
        raise ValueError("Source media is incompatible with the target track")
    _guard_picture_rate(media, track, project.fps)
    if any(clip.id == new_clip_id for clip in project.clips):
        raise ValueError("New overwrite clip identity already exists")
    affected, remnants, output = [], [], []
    for clip in project.clips:
        if clip.track != track or clip.end <= start or clip.start >= end:
            output.append(clip)
            continue
        affected.append(clip.id)
        editor._guard_linked_clip(clip.id)
        editor._guard_grouped_clip(clip.id)
        editor._guard_transition_endpoint(clip.id)
        _guard_safe_clip(clip, project)
        left = clip.start < start
        right = end < clip.end
        if left:
            surviving = deepcopy(clip)
            surviving.duration = start - clip.start
            surviving.time_map = None
            surviving.source_in = int(clip.source_in)
            _guard_pcm_remnant(clip, surviving, project)
            output.append(surviving)
            remnants.append(OverwriteRemnant(clip.id, surviving.id, surviving.start,
                                             surviving.source_in, surviving.duration))
        if right:
            surviving = deepcopy(clip)
            if left:
                if second_remnant_ids is None:
                    surviving.id = uuid4().hex
                else:
                    try:
                        surviving.id = second_remnant_ids[clip.id]
                    except KeyError as exc:
                        raise ValueError("Invalid overwrite remnant plan") from exc
            surviving.start = end
            surviving.source_in = int(clip.source_in) + end - clip.start
            surviving.duration = clip.end - end
            surviving.time_map = None
            _guard_pcm_remnant(clip, surviving, project)
            output.append(surviving)
            remnants.append(OverwriteRemnant(clip.id, surviving.id, surviving.start,
                                             surviving.source_in, surviving.duration))
    inserted = Clip(new_clip_id, media_id, track, start, source_in, source_out - source_in)
    # Match ordinary insertion's explicit Apple Log interpretation. No creative
    # grade, mute, group or other timeline-clip settings leak into a source take.
    if media.input_encoding == "apple_log_bt2020":
        inserted.grade.input_encoding = media.input_encoding
    output.append(inserted)
    project.clips = output
    editor._guard_track_edit_locks(editor.project, project)
    project.validate()
    return project, tuple(affected), tuple(remnants)


def plan_source_overwrite(editor: Editor, media_id: str, track: int, start: int,
                          source_in: int, source_out: int) -> SourceOverwritePlan:
    """Validate and describe all remnants without changing objects or history."""
    editor._assert_owner()
    signature = _signature(editor.project)
    new_id = uuid4().hex
    candidate, affected, remnants = _candidate(editor, media_id, track, start,
                                               source_in, source_out, new_id)
    return SourceOverwritePlan(editor.session_id, editor.revision, signature,
        editor.project.fps_num, editor.project.fps_den, media_id, track, start,
        source_in, source_out, new_id, affected, remnants, _signature(candidate))


def apply_source_overwrite(editor: Editor, plan: SourceOverwritePlan) -> Clip:
    """Reject stale/tampered plans; validate every remnant before one commit."""
    editor._assert_owner()
    if type(plan) is not SourceOverwritePlan:
        raise ValueError("Invalid source overwrite plan")
    if ((plan.session_id, plan.revision, plan.fps_num, plan.fps_den)
            != (editor.session_id, editor.revision, editor.project.fps_num, editor.project.fps_den)
            or plan.project_signature != _signature(editor.project)):
        raise ValueError("Source overwrite plan is stale; preview the source selection again")
    if (type(plan.remnants) is not tuple or type(plan.affected_clip_ids) is not tuple
            or any(type(item) is not OverwriteRemnant for item in plan.remnants)):
        raise ValueError("Invalid source overwrite plan")
    second_ids = {item.original_clip_id: item.clip_id for item in plan.remnants
                  if item.original_clip_id != item.clip_id}
    candidate, affected, remnants = _candidate(editor, plan.media_id, plan.track,
        plan.start, plan.source_in, plan.source_out, plan.new_clip_id, second_ids)
    if (affected != plan.affected_clip_ids or remnants != plan.remnants
            or _signature(candidate) != plan.candidate_signature):
        raise ValueError("Source overwrite plan was changed; preview the source selection again")

    def change():
        # Only the clip collection changes. Existing unaffected objects retain
        # their identities, and central lock/group/transition guards still run.
        unaffected = {clip.id: clip for clip in editor.project.clips if clip.id not in affected}
        editor.project.clips[:] = [unaffected.get(clip.id, clip) for clip in candidate.clips]
        return next(clip for clip in editor.project.clips if clip.id == plan.new_clip_id)

    return editor._commit(change)

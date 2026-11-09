"""Frame-exact timeline model, transactional editing and portable JSON projects.

Timeline positions and fades are integer *project* frames. Source positions
are exact rational project-frame units. Audio gain keyframes use 48 kHz output
clip-local sample coordinates. No Qt or media backend is required.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime
from fractions import Fraction
import json
import math
import os
import re
from pathlib import Path
import tempfile
import threading
from typing import Any
from uuid import uuid4

from .timing import (ClipTimeMap, duration_frames, parse_source_position, parse_speed,
                     source_position, validate_time_map, round_half_up, sample_boundary)
from .stabilization_settings import StabilizationSettings, validate_stabilization_settings
from .restoration_settings import RestorationSettings, validate_restoration_settings
from .sound_sources import SOURCE_FIELDS, validate_source_metadata
from .clip_transform import ClipTransform, validate_transform, is_identity
from .clip_composite import ClipComposite, validate_composite
from .clip_motion import (ClipMotionKeyframe, MAX_MOTION_KEYFRAMES,
                          evaluate_clip_motion, shift_clip_motion, validate_motion_keyframes)
from .qualifier import (QualifierSettings, QualifierRange, validate_qualifier,
                        ASYMMETRIC_SOFTNESS_FIELDS, CHANNEL_ENABLE_FIELDS, MAX_QUALIFIER_RANGES)

from .audio_gain import (AudioGainKeyframe, MAX_AUDIO_GAIN_KEYFRAMES, MAX_AUDIO_GAIN_SAMPLE,
                         validate_audio_gain_keyframes, shift_audio_gain_keyframes)
from .white_balance import validate_white_balance
from .window_animation import shift_window_animation
from .video_transition import VideoTransition, validate_video_transitions
from .source_ranges import (SourceRange, SourceRangeBinding, source_ranges_from_dict,
                            validate_source_ranges, range_frames, MAX_SOURCE_RANGE_FRAME)
from .timeline_markers import (TimelineMarker, timeline_markers_from_dict,
                               validate_timeline_markers)

FORMAT = "lumen-project"
# Version 2 adds the float-color controls. Version 1 keeps its original
# code-value exposure interpretation; missing new controls migrate to identity.
# Version 3 adds an independent, non-rippling subtitle track and caption style.
# Version 4 adds immutable local-speech provenance to generated audio media.
# Version 5 adds independent local sticker overlays and ordered clip effects.
# Version 6 adds explicit SDR input encoding and retained source color tags.
# Version 7 stores one exact rational source interval and constant playback speed.
# Version 8 adds bounded nondestructive source-wide picture stabilization.
# Version 9 adds local per-frame spatial quality restoration.
# Version 10 adds render-only caption wrapping limits. Earlier readers reject
# these new CaptionStyle fields, so new saves must advertise the newer schema.
# Version 11 records the native pause-preservation setting in speech provenance.
# Earlier readers reject this new field; missing legacy values default to 0.2.
# Version 12 records the exact generated PCM sample count. Legacy takes retain
# their original floating-duration timing; new takes use exact rational sizing.
# Earlier readers reject the new provenance field before decoding the project.
# Version 13 adds immutable sound-library provenance and portable WAV assets.
# Version 14 retains author/source/license links for external CC0 sound assets.
# Earlier readers must reject rather than silently discard their provenance.
# Version 15 records optional decoded video-tail evidence for frame sizing.
# Version 16 adds static main-video transforms; older readers must reject.
# Version 17 adds periodic selective hue curves; older readers must reject.
# Version 18 adds a joint hue / HSV saturation / encoded luma qualifier.
# Version 19 adds a static geometric window multiplied with the color key.
# Version 20 adds bounded independent additive/subtractive qualifier ranges.
# Version 21 adds pointwise black/white matte levels before the window.
# Version 22 adds bounded project-pixel spatial smoothing of the color matte.
# Version 23 adds signed fixed-radius matte shrink/grow before smoothing.
# Version 24 adds independently inherited low/high S/Y exterior feathers.
# Version 25 adds independent H/S/Y qualifier channel enable switches.
# Version 26 adds the encoded Rec.709 contrast pivot; earlier readers reject.
# Version 27 adds bounded linear neutral-reference white balance.
# Version 28 adds bounded vignette radius and feather; earlier readers reject.
# Version 29 persists explicit experimental Apple Log 1 source interpretation.
# Version 30 adds bounded clip-local window geometry keyframes.
# Version 31 adds explicit static picture compositing and whole-picture opacity.
# Version 32 adds absolute clip-local position, scale and opacity keyframes.
# Version 33 adds explicit non-rippling video cross-dissolve relationships.
# Version 34 adds project master output gain.
# Version 35 adds nondestructive media bins and explicit media membership.
# Version 36 adds persistent, non-nested timeline clip groups.
# Version 37 adds strict, persisted timeline track edit locks.
# Version 38 adds named reusable source-relative ranges with portable bindings.
# Version 39 adds fixed project-frame point/range annotation metadata.
# Version 40 adds independent source-audio emission and historical mix ordering.
# Version 41 adds sample-local manual clip gain-offset keyframes.
# Version 42 adds explicit same-source 1:1 A/V links; no legacy inference.
# Version 43 adds a separately versioned affine source-time A/V relationship.
# The strict schema42 relationship remains unchanged; loading never promotes it.
VERSION = 43
MAX_CLIP_GROUPS = 10000
MAX_CLIP_GROUP_MEMBERS = 20000
MAX_MEDIA_BINS = 1024
MAX_MEDIA_BIN_DEPTH = 16
MAX_MEDIA_BIN_NAME = 128
MAX_CAPTIONS = 20000
MAX_CAPTION_TEXT = 10000
MAX_CAPTION_FRAME = 2**53 - 1
MAX_OVERLAYS = 256
MAX_OVERLAY_KEYFRAMES = 128
MAX_CLIP_EFFECTS = 16


def _integer(value: Any, name: str, low: int = 0, high: int | None = None) -> None:
    if type(value) is not int or value < low or (high is not None and value > high):
        raise ValueError(f"{name} must be an integer in {low}..{high or 'unbounded'}")


def _number(value: Any, name: str, low: float, high: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high or not math.isfinite(value):
        raise ValueError(f"{name} must be finite and in {low}..{high}")


def _text(value: Any, name: str, nonempty: bool = False) -> None:
    if not isinstance(value, str) or '\x00' in value or (nonempty and not value.strip()):
        raise ValueError(f"Invalid {name}")


def _boolean(value: Any, name: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")


def _vector(value: Any, name: str, low: float, high: float) -> None:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{name} must contain three RGB numbers")
    for component in value:
        _number(component, name, low, high)


def _curve(value: Any, name: str, low: float = -1, high: float = 2) -> None:
    if not isinstance(value, list) or not 2 <= len(value) <= 32:
        raise ValueError(f"{name} must contain 2..32 points")
    previous = -1.0
    for point in value:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"{name} points must be [x, y] pairs")
        _number(point[0], f"{name} x", 0, 1)
        _number(point[1], f"{name} y", low, high)
        if point[0] <= previous:
            raise ValueError(f"{name} x coordinates must be strictly increasing")
        previous = point[0]
    if value[0][0] != 0 or value[-1][0] != 1:
        raise ValueError(f"{name} x endpoints must be 0 and 1")


def _identity_curve() -> list[list[float]]:
    return [[0.0, 0.0], [1.0, 1.0]]


def _hue_curve(value: Any, name: str, low: float, high: float) -> None:
    """Validate a bounded, explicitly closed periodic piecewise-linear curve."""
    _curve(value, name, low, high)
    if value[0][1] != value[-1][1]:
        raise ValueError(f"{name} y endpoints must match for a periodic hue curve")


@dataclass(frozen=True)
class SpeechProvenance:
    """Immutable record of the actual synthesis input, independent of later edits."""

    text: str
    engine: str
    engine_version: str
    model_sha256: str
    voice_sha256: str
    assets_sha256: str
    voice_id: str
    speed: float
    sample_rate: int
    created_utc: str
    source_caption_id: str
    source_start: int
    source_duration: int
    output_sha256: str
    silence_scale: float = 0.2
    sample_count: int | None = None

    def __post_init__(self):
        validate_speech_provenance(self)


def validate_speech_provenance(value: SpeechProvenance) -> None:
    if not isinstance(value, SpeechProvenance):
        raise ValueError("Invalid speech provenance")
    normalize_caption_text(value.text)
    for name in ("engine", "engine_version", "voice_id", "source_caption_id"):
        item = getattr(value, name)
        _text(item, name, True)
        if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in item):
            raise ValueError(f"Invalid {name}")
    for name in ("model_sha256", "voice_sha256", "assets_sha256", "output_sha256"):
        if not isinstance(getattr(value, name), str) or not re.fullmatch(r"[0-9a-fA-F]{64}", getattr(value, name)):
            raise ValueError(f"{name} must be a SHA-256 hexadecimal digest")
    _number(value.speed, "speech speed", 0.5, 2.0)
    _number(value.silence_scale, "speech pause preservation", 0.2, 1.0)
    _integer(value.sample_rate, "speech sample rate", 1, 384000)
    if value.sample_count is not None:
        _integer(value.sample_count, "speech sample count", 1, MAX_CAPTION_FRAME)
    _integer(value.source_start, "speech source start", 0, MAX_CAPTION_FRAME)
    _integer(value.source_duration, "speech source duration", 1, MAX_CAPTION_FRAME)
    if value.source_start + value.source_duration > MAX_CAPTION_FRAME:
        raise ValueError("Speech source end exceeds supported frame range")
    if not isinstance(value.created_utc, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value.created_utc
    ):
        raise ValueError("Speech creation time must be an ISO-8601 UTC timestamp")
    try:
        datetime.fromisoformat(value.created_utc.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid speech creation time") from exc


@dataclass(frozen=True)
class SoundProvenance:
    """Exact PCM identity for packaged or user-imported local sound effects."""
    sound_id: str
    origin: str
    license: str
    source_sha256: str
    output_sha256: str
    sample_rate: int
    sample_count: int
    author: str = ''
    source_name: str = ''
    source_url: str = ''
    license_url: str = ''
    source_file: str = ''

    def __post_init__(self):
        validate_sound_provenance(self)


def validate_sound_provenance(value):
    if not isinstance(value, SoundProvenance):
        raise ValueError("Invalid sound provenance")
    for name in ("sound_id", "license"):
        _text(getattr(value, name), name, True)
    if value.origin not in ("procedural", "user-imported", "external-cc0"):
        raise ValueError("Invalid sound origin")
    validate_source_metadata(value.origin, value.license,
                             {name: getattr(value, name) for name in SOURCE_FIELDS})
    for name in ("source_sha256", "output_sha256"):
        if not isinstance(getattr(value, name), str) or not re.fullmatch(r"[0-9a-f]{64}", getattr(value, name)):
            raise ValueError("Sound identity must be a SHA-256 digest")
    _integer(value.sample_rate, "sound sample rate", 8000, 192000)
    _integer(value.sample_count, "sound sample count", 1, 60 * value.sample_rate)


@dataclass
class Media:
    id: str
    path: str
    name: str
    duration: float
    width: int = 0
    height: int = 0
    has_video: bool = True
    has_audio: bool = False
    fps_num: int = 30
    fps_den: int = 1
    color_space: str = "unknown"
    audio_offset: float = 0.0
    speech_provenance: SpeechProvenance | None = None
    color_transfer: str = "unknown"
    color_primaries: str = "unknown"
    color_range: str = "unknown"
    sound_provenance: SoundProvenance | None = None
    decoded_video_end_num: int | None = None
    decoded_video_end_den: int | None = None
    # Explicit import interpretation; never inferred from camera or filename.
    input_encoding: str = "rec709"


@dataclass
class MediaBin:
    """Virtual organization only: never a filesystem directory."""

    id: str
    name: str
    parent_id: str | None = None


def validate_media_bins(bins: list[MediaBin], membership: dict[str, str], media_ids: set[str]) -> None:
    if not isinstance(bins, list) or len(bins) > MAX_MEDIA_BINS:
        raise ValueError("Media bins must be a list of at most 1024 entries")
    by_id = {}
    for item in bins:
        if not isinstance(item, MediaBin):
            raise ValueError("Invalid media bin")
        _text(item.id, "media bin id", True)
        _text(item.name, "media bin name", True)
        if len(item.name) > MAX_MEDIA_BIN_NAME:
            raise ValueError("Media bin names must be at most 128 characters")
        if item.parent_id is not None:
            _text(item.parent_id, "media bin parent id", True)
        if item.id in by_id:
            raise ValueError("Duplicate media bin id")
        by_id[item.id] = item
    for item in bins:
        visited = set()
        current = item.id
        while current is not None:
            if current not in by_id:
                raise ValueError("Media bin references unknown parent")
            if current in visited:
                raise ValueError("Media bins cannot contain cycles")
            visited.add(current)
            if len(visited) > MAX_MEDIA_BIN_DEPTH:
                raise ValueError("Media bin depth exceeds 16")
            current = by_id[current].parent_id
    if not isinstance(membership, dict):
        raise ValueError("Media bin membership must be an object")
    for media_id, bin_id in membership.items():
        _text(media_id, "media membership id", True)
        _text(bin_id, "media membership bin id", True)
        if media_id not in media_ids or bin_id not in by_id:
            raise ValueError("Media bin membership references unknown media or bin")


@dataclass
class Grade:
    # Legacy code-value exposure; linear-light exposure is a separate control.
    exposure: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    temperature: float = 0.0
    tint: float = 0.0
    shadows: float = 0.0
    highlights: float = 0.0
    enabled: bool = True
    lut_path: str = ""
    linear_exposure: float = 0.0
    lift: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    gamma: list[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    gain: list[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    master_curve: list[list[float]] = field(default_factory=_identity_curve)
    red_curve: list[list[float]] = field(default_factory=_identity_curve)
    green_curve: list[list[float]] = field(default_factory=_identity_curve)
    blue_curve: list[list[float]] = field(default_factory=_identity_curve)
    # Input normalization is independent of the creative grade enable/bypass.
    input_encoding: str = "rec709"
    # Missing fields in older projects retain their original full LUT effect.
    lut_strength: float = 1.0
    # Appended for compatibility with earlier positional Grade construction.
    # Hue offsets are turns; saturation values scale encoded-luma deviations.
    hue_hue_curve: list[list[float]] = field(default_factory=lambda: [[0.0, 0.0], [1.0, 0.0]])
    hue_sat_curve: list[list[float]] = field(default_factory=lambda: [[0.0, 1.0], [1.0, 1.0]])
    qualifier: QualifierSettings = field(default_factory=QualifierSettings)
    # Encoded Rec.709 RGB code value, not linear-light middle gray. Appended
    # to preserve every earlier positional Grade constructor.
    contrast_pivot: float = 0.5
    white_balance_enabled: bool = False
    white_balance_gains: list[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])

    def __post_init__(self):
        if isinstance(self.qualifier, dict):
            self.qualifier = QualifierSettings.from_dict(self.qualifier)


@dataclass
class ClipEffect:
    """Ordered post-grade effect; vignette shape defaults preserve legacy output.

    Blur amount is sigma in pixels and its shape fields must remain at 1.
    Vignette radius and feather are dimensionless, not camera/lens parameters.
    Appended fields preserve the earlier positional constructor.
    """

    id: str
    kind: str
    amount: float
    enabled: bool = True
    radius: float = 1.0
    feather: float = 1.0


@dataclass(frozen=True)
class AudioMixAnchor:
    """Historical summation slot, not a live clip reference or an A/V link."""

    track: int
    start: int
    clip_id: str

    def validate(self) -> None:
        _integer(self.track, "audio mix anchor track", 0, 3)
        _integer(self.start, "audio mix anchor start", 0, MAX_CAPTION_FRAME)
        _text(self.clip_id, "audio mix anchor clip id", True)

    @classmethod
    def from_dict(cls, value: Any) -> AudioMixAnchor:
        if not isinstance(value, dict) or set(value) != {"track", "start", "clip_id"}:
            raise ValueError("Invalid audio mix anchor fields")
        anchor = cls(**value)
        anchor.validate()
        return anchor


@dataclass
class Clip:
    id: str
    media_id: str
    track: int
    start: int
    source_in: int | Fraction
    duration: int
    grade: Grade = field(default_factory=Grade)
    gain_db: float = 0.0
    muted: bool = False
    fade_in: int = 0
    fade_out: int = 0
    effects: list[ClipEffect] = field(default_factory=list)
    # Legacy positional construction remains supported. For ordinary integral
    # 1x clips the effective map derives from source_in/duration. Every retimed
    # clip has a complete immutable map; source_in is only a checked mirror.
    time_map: ClipTimeMap | None = None
    # Appended so every earlier positional Clip constructor remains compatible.
    stabilization: StabilizationSettings = field(default_factory=StabilizationSettings)
    restoration: RestorationSettings = field(default_factory=RestorationSettings)
    transform: ClipTransform = field(default_factory=ClipTransform)
    composite: ClipComposite = field(default_factory=ClipComposite)
    motion_keyframes: list[ClipMotionKeyframe] = field(default_factory=list)
    # Structural emission is separate from the user mute control.
    source_audio_enabled: bool = True
    audio_mix_anchor: AudioMixAnchor | None = None
    # Appended: older positional Clip construction remains unchanged.
    audio_gain_keyframes: list[AudioGainKeyframe] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate_audio_gain_keyframes(self.audio_gain_keyframes)
        self.audio_gain_keyframes = list(self.audio_gain_keyframes)
        validate_motion_keyframes(self.motion_keyframes)
        self.motion_keyframes = list(self.motion_keyframes)
        if self.time_map is not None:
            validate_time_map(self.time_map)
            self.source_in = self.time_map.source_in
            self._normalize_legacy_timing()

    def _normalize_legacy_timing(self) -> None:
        mapping = self.time_map
        if (isinstance(mapping, ClipTimeMap) and mapping.speed == 1
                and mapping.source_in.denominator == 1
                and mapping.source_out - mapping.source_in == self.duration):
            self.source_in = int(mapping.source_in)
            self.time_map = None

    @property
    def timing(self) -> ClipTimeMap:
        if self.time_map is not None:
            return self.time_map
        return ClipTimeMap(self.source_in, self.source_in + self.duration)

    @property
    def source_out(self) -> Fraction:
        return self.timing.source_out

    @property
    def speed(self) -> Fraction:
        return self.timing.speed

    @property
    def end(self) -> int:
        return self.start + self.duration


def _audio_gain_keyframes_from_dict(value: Any) -> list[AudioGainKeyframe]:
    """Strict wire records; missing/defaulted or unknown point fields reject."""
    if type(value) is not list or len(value) > MAX_AUDIO_GAIN_KEYFRAMES:
        raise ValueError("Audio gain keyframes must be a list of at most 128 entries")
    keys = []
    for item in value:
        if type(item) is not dict or set(item) != {"sample", "offset_db", "interpolation"}:
            raise ValueError("Invalid audio gain keyframe fields")
        keys.append(AudioGainKeyframe(**item))
    validate_audio_gain_keyframes(keys)
    return keys


AV_LINK_ALIGNMENT = "same-source-zero-offset-v1"
AV_LINK_AFFINE_ALIGNMENT = "same-source-affine-sync-v2"
MAX_AV_LINKS = 10000


@dataclass(frozen=True)
class AVLink:
    """Metadata-only same-source pair; relative alignment, not a frozen range."""

    id: str
    video_clip_id: str
    audio_clip_id: str
    media_id: str
    alignment: str = AV_LINK_ALIGNMENT

    @property
    def clip_ids(self) -> tuple[str, str]:
        return self.video_clip_id, self.audio_clip_id


def av_link_timing_matches(video: Clip, audio: Clip, alignment: str) -> bool:
    """Pure timing relation for already validated clips; never normalize values.

    Affine synchronization compares exact source-time intercepts, not rounded
    visible edges. Each clip's duration and partial tail are validated separately.
    Strict v1 retains equality of the complete maps, including both source tails.
    """
    if alignment == AV_LINK_ALIGNMENT:
        return (video.start, video.duration, video.timing) == (audio.start, audio.duration, audio.timing)
    if alignment == AV_LINK_AFFINE_ALIGNMENT:
        vmap, amap = video.timing, audio.timing
        return (vmap.speed == amap.speed
            and vmap.source_in - video.start * vmap.speed
            == amap.source_in - audio.start * amap.speed)
    return False


def require_av_link_alignment(link: AVLink, *, operation: str) -> None:
    """Fail closed for legacy commands whose algebra only supports strict v1."""
    if link.alignment != AV_LINK_ALIGNMENT:
        raise ValueError(f"{operation} requires same-source-zero-offset-v1 alignment; affine-sync pairs are not supported yet")


def validate_av_links(project: Project) -> None:
    if type(project.av_links) is not list or len(project.av_links) > MAX_AV_LINKS:
        raise ValueError("A/V links must be a list of at most 10000 entries")
    ids, members = set(), set()
    clips = {clip.id: clip for clip in project.clips}
    media = {item.id: item for item in project.media}
    grouped = {clip_id for group in project.clip_groups for clip_id in group.clip_ids}
    endpoints = {clip_id for item in project.video_transitions
                 for clip_id in (item.left_clip_id, item.right_clip_id)}
    for link in project.av_links:
        if type(link) is not AVLink:
            raise ValueError("Invalid A/V link")
        for value in (link.id, link.video_clip_id, link.audio_clip_id, link.media_id):
            _text(value, "A/V link identity", True)
        if type(link.alignment) is not str or link.alignment not in (AV_LINK_ALIGNMENT, AV_LINK_AFFINE_ALIGNMENT):
            raise ValueError("Unknown A/V link alignment mode")
        if link.id in ids:
            raise ValueError("Duplicate A/V link id")
        ids.add(link.id)
        if link.video_clip_id == link.audio_clip_id or members.intersection(link.clip_ids):
            raise ValueError("A clip may belong to only one A/V link")
        members.update(link.clip_ids)
        if not set(link.clip_ids) <= clips.keys():
            raise ValueError("A/V links may reference only existing timeline clips")
        video, audio = (clips[clip_id] for clip_id in link.clip_ids)
        if video.track not in (0, 1) or audio.track not in (2, 3):
            raise ValueError("A/V links require one explicit video role and one explicit audio role")
        if video.source_audio_enabled or not audio.source_audio_enabled:
            raise ValueError("A/V links require disabled video source audio and enabled audio source audio")
        source = media.get(link.media_id)
        if (video.media_id != link.media_id or audio.media_id != link.media_id
                or source is None or not source.has_video or not source.has_audio):
            raise ValueError("A/V links require the same Media record containing both video and audio")
        if not av_link_timing_matches(video, audio, link.alignment):
            if link.alignment == AV_LINK_ALIGNMENT:
                raise ValueError("A/V links require identical start, duration and complete rational source timing")
            raise ValueError("Affine A/V links require equal speed and exact source-time intercept")
        if set(link.clip_ids).intersection(grouped | endpoints):
            raise ValueError("Unlink A/V clips before grouping them or adding transitions")


def av_links_from_dict(value: Any, *, version: int = VERSION) -> list[AVLink]:
    if type(version) is not int or version not in (42, 43):
        raise ValueError("Unsupported A/V link schema version")
    if type(value) is not list or len(value) > MAX_AV_LINKS:
        raise ValueError("A/V links must be a list of at most 10000 entries")
    result = []
    for item in value:
        if type(item) is not dict or set(item) != {"id", "video_clip_id", "audio_clip_id", "media_id", "alignment"}:
            raise ValueError("Invalid A/V link fields")
        if (version == 42 and item["alignment"] != AV_LINK_ALIGNMENT):
            raise ValueError("Version 42 A/V links require same-source-zero-offset-v1 alignment")
        result.append(AVLink(**item))
    return result


@dataclass
class ClipGroup:
    """Persistent membership only; clips retain their independent media and tracks."""

    id: str
    clip_ids: list[str]


def validate_clip_groups(groups: list[ClipGroup], clip_ids: set[str]) -> None:
    if not isinstance(groups, list) or len(groups) > MAX_CLIP_GROUPS:
        raise ValueError("Clip groups must be a list of at most 10000 entries")
    group_ids, members = set(), set()
    for group in groups:
        if not isinstance(group, ClipGroup):
            raise ValueError("Invalid clip group")
        _text(group.id, "clip group id", True)
        if group.id in group_ids:
            raise ValueError("Duplicate clip group id")
        group_ids.add(group.id)
        if (not isinstance(group.clip_ids, list)
                or not 2 <= len(group.clip_ids) <= MAX_CLIP_GROUP_MEMBERS):
            raise ValueError("Clip groups require 2..20000 unique clips")
        local = set()
        for clip_id in group.clip_ids:
            _text(clip_id, "group member clip id", True)
            if clip_id not in clip_ids:
                raise ValueError("Clip groups may contain only existing timeline clips")
            if clip_id in local:
                raise ValueError("Duplicate clip group member")
            if clip_id in members:
                raise ValueError("A clip may belong to only one clip group")
            local.add(clip_id)
        members.update(local)


def _selected_clip_ids(project: Project, clip_ids: Iterable[str], *,
                       allow_empty: bool = False) -> list[str]:
    """Resolve exact, unique clip identities without mutating the project."""
    if isinstance(clip_ids, (str, bytes, bytearray, Mapping)):
        raise ValueError("Clip IDs must be a collection of unique strings")
    try:
        ids = tuple(clip_ids)
    except TypeError as exc:
        raise ValueError("Clip IDs must be a collection of unique strings") from exc
    if not ids and not allow_empty:
        raise ValueError("Clip IDs must be a nonempty collection of unique strings")
    selected = set()
    available = {clip.id for clip in project.clips}
    for clip_id in ids:
        _text(clip_id, "clip id", True)
        if clip_id in selected:
            raise ValueError(f"Duplicate selected clip: {clip_id}")
        if clip_id not in available:
            raise ValueError(f"Unknown clip: {clip_id}")
        selected.add(clip_id)
    return [clip.id for clip in project.clips if clip.id in selected]


def expanded_clip_ids(project: Project, clip_ids: Iterable[str]) -> list[str]:
    """Return selected clips and complete touched groups in project order.

    This is a pure selection helper, never an edit or an implicit model-level
    move/delete expansion. Empty selections are supported; stale IDs are not.
    """
    selected = set(_selected_clip_ids(project, clip_ids, allow_empty=True))
    validate_clip_groups(project.clip_groups, {clip.id for clip in project.clips})
    for group in project.clip_groups:
        if selected.intersection(group.clip_ids):
            selected.update(group.clip_ids)
    for link in project.av_links:
        if selected.intersection(link.clip_ids):
            selected.update(link.clip_ids)
    return [clip.id for clip in project.clips if clip.id in selected]


@dataclass
class OverlayKeyframe:
    """Absolute canvas position and transform at an overlay-relative frame."""

    frame: int = 0
    x: float = 0.0
    y: float = 0.0
    scale: float = 1.0
    opacity: float = 1.0


@dataclass
class Overlay:
    """Independent local PNG or frame-pack sticker; it never ripples clips."""

    id: str
    asset_path: str
    asset_sha256: str
    start: int
    duration: int
    z_order: int = 0
    enabled: bool = True
    keyframes: list[OverlayKeyframe] = field(default_factory=lambda: [OverlayKeyframe()])

    @property
    def end(self) -> int:
        return self.start + self.duration


def validate_clip_effects(effects: list[ClipEffect], track: int) -> None:
    if not isinstance(effects, list) or len(effects) > MAX_CLIP_EFFECTS:
        raise ValueError("Clip effects must be a list of at most 16 entries")
    if effects and track >= 2:
        raise ValueError("Clip effects are supported on video tracks only")
    ids = set()
    for effect in effects:
        if not isinstance(effect, ClipEffect):
            raise ValueError("Invalid clip effect")
        _text(effect.id, "effect id", True)
        if effect.id in ids:
            raise ValueError("Duplicate effect id within clip")
        ids.add(effect.id)
        _text(effect.kind, "effect kind", True)
        if effect.kind not in ("blur", "vignette"):
            raise ValueError("Unsupported clip effect kind")
        _number(effect.amount, "effect amount", 0, 30 if effect.kind == "blur" else 1)
        _boolean(effect.enabled, "effect enabled")
        _number(effect.radius, "vignette radius", .25, 2)
        _number(effect.feather, "vignette feather", .25, 2)
        if effect.kind == "blur" and (effect.radius != 1 or effect.feather != 1):
            raise ValueError("Blur effects require default vignette radius and feather")


def validate_overlay(overlay: Overlay) -> None:
    if not isinstance(overlay, Overlay):
        raise ValueError("Invalid overlay entry")
    _text(overlay.id, "overlay id", True)
    _text(overlay.asset_path, "overlay asset path", True)
    # URI protocols and network shares are not local asset inputs. Permit an
    # absolute Windows drive path so cross-platform projects remain readable.
    if (any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in overlay.asset_path)
            or overlay.asset_path.startswith(("//", "\\\\"))
            or (re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", overlay.asset_path)
                and not re.match(r"^[A-Za-z]:[\\/]", overlay.asset_path))
            or Path(overlay.asset_path).suffix.lower() not in (".png", ".json")):
        raise ValueError("Overlay assets must be local PNG files or JSON frame packs")
    if not isinstance(overlay.asset_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", overlay.asset_sha256):
        raise ValueError("Overlay asset_sha256 must be a SHA-256 hexadecimal digest")
    _integer(overlay.start, "overlay start", 0, MAX_CAPTION_FRAME)
    _integer(overlay.duration, "overlay duration", 1, MAX_CAPTION_FRAME)
    if overlay.end > MAX_CAPTION_FRAME:
        raise ValueError("Overlay end exceeds supported frame range")
    _integer(overlay.z_order, "overlay z_order", -(2**31), 2**31 - 1)
    _boolean(overlay.enabled, "overlay enabled")
    if not isinstance(overlay.keyframes, list) or not 1 <= len(overlay.keyframes) <= MAX_OVERLAY_KEYFRAMES:
        raise ValueError("Overlay keyframes must be a list of 1..128 entries")
    previous = -1
    for keyframe in overlay.keyframes:
        if not isinstance(keyframe, OverlayKeyframe):
            raise ValueError("Invalid overlay keyframe")
        _integer(keyframe.frame, "overlay keyframe frame", 0, overlay.duration - 1)
        if keyframe.frame <= previous:
            raise ValueError("Overlay keyframe frames must be strictly increasing")
        previous = keyframe.frame
        _number(keyframe.x, "overlay x", -16384, 16384)
        _number(keyframe.y, "overlay y", -16384, 16384)
        _number(keyframe.scale, "overlay scale", 0.01, 16)
        _number(keyframe.opacity, "overlay opacity", 0, 1)


def validate_overlays(overlays: list[Overlay]) -> None:
    if not isinstance(overlays, list) or len(overlays) > MAX_OVERLAYS:
        raise ValueError("Overlays must be a list of at most 256 entries")
    ids = set()
    for overlay in overlays:
        validate_overlay(overlay)
        if overlay.id in ids:
            raise ValueError("Duplicate overlay id")
        ids.add(overlay.id)


def normalize_caption_text(value: str) -> str:
    _text(value, "caption text", True)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if len(value) > MAX_CAPTION_TEXT or any(
        (ord(char) < 32 and char not in "\n\t") or 0xD800 <= ord(char) <= 0xDFFF
        for char in value
    ):
        raise ValueError("Caption text contains invalid characters or exceeds 10000 characters")
    return value


@dataclass
class Caption:
    id: str
    start: int
    duration: int
    text: str
    language: str = "und"

    def __post_init__(self):
        self.text = normalize_caption_text(self.text)

    @property
    def end(self) -> int:
        return self.start + self.duration


@dataclass
class CaptionStyle:
    font_family: str = "DejaVu Sans"
    font_size: int = 42
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_width: int = 2
    margin_bottom: int = 40
    font_directory: str = ""
    # Render-only layout constraints. Original caption/SRT text is unchanged;
    # older project styles receive these defaults on load.
    safe_margin_percent: int = 5
    max_lines: int = 6


def validate_captions(captions: list[Caption]) -> None:
    if not isinstance(captions, list) or len(captions) > MAX_CAPTIONS:
        raise ValueError("Captions must be a list of at most 20000 entries")
    ids = set()
    for caption in captions:
        if not isinstance(caption, Caption):
            raise ValueError("Invalid caption entry")
        _text(caption.id, "caption id", True)
        if caption.id in ids:
            raise ValueError("Duplicate caption id")
        ids.add(caption.id)
        _integer(caption.start, "caption start", 0, MAX_CAPTION_FRAME)
        _integer(caption.duration, "caption duration", 1, MAX_CAPTION_FRAME)
        if caption.end > MAX_CAPTION_FRAME:
            raise ValueError("Caption end exceeds supported frame range")
        if normalize_caption_text(caption.text) != caption.text:
            raise ValueError("Caption text must use normalized line breaks")
        _text(caption.language, "caption language", True)
        if not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", caption.language):
            raise ValueError("Caption language must be a language tag such as und or en-US")
    ordered = sorted(captions, key=lambda c: c.start)
    if any(a.end > b.start for a, b in zip(ordered, ordered[1:])):
        raise ValueError("Captions on the subtitle track may not overlap")


def validate_caption_style(style: CaptionStyle) -> None:
    if not isinstance(style, CaptionStyle):
        raise ValueError("Invalid caption style")
    _text(style.font_family, "caption font family", True)
    if len(style.font_family) > 200 or any(ord(c) < 32 for c in style.font_family):
        raise ValueError("Invalid caption font family")
    _integer(style.font_size, "caption font size", 1, 1000)
    _integer(style.outline_width, "caption outline width", 0, 100)
    _integer(style.margin_bottom, "caption bottom margin", 0, 16384)
    _integer(style.safe_margin_percent, "caption safe horizontal margin", 0, 20)
    _integer(style.max_lines, "caption maximum lines", 1, 20)
    for value in (style.color, style.outline_color):
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("Caption colors must be #RRGGBB")
    _text(style.font_directory, "caption font directory")


@dataclass
class Project:
    name: str = "Untitled"
    width: int = 1280
    height: int = 720
    fps_num: int = 30
    fps_den: int = 1
    media: list[Media] = field(default_factory=list)
    clips: list[Clip] = field(default_factory=list)
    captions: list[Caption] = field(default_factory=list)
    caption_style: CaptionStyle = field(default_factory=CaptionStyle)
    overlays: list[Overlay] = field(default_factory=list)
    video_transitions: list[VideoTransition] = field(default_factory=list)
    master_gain_db: float = 0.0
    media_bins: list[MediaBin] = field(default_factory=list)
    media_bin_ids: dict[str, str] = field(default_factory=dict)
    clip_groups: list[ClipGroup] = field(default_factory=list)
    track_edit_locks: list[bool] = field(default_factory=lambda: [False] * 4)
    source_ranges: list[SourceRange] = field(default_factory=list)
    timeline_markers: list[TimelineMarker] = field(default_factory=list)
    av_links: list[AVLink] = field(default_factory=list)

    def is_track_edit_locked(self, track: int) -> bool:
        _integer(track, "track", 0, 3)
        return self.track_edit_locks[track]

    @property
    def fps(self) -> Fraction:
        return Fraction(self.fps_num, self.fps_den)

    @property
    def duration_frames(self) -> int:
        # Bypassing a visual/audio item must not change the timeline's extent.
        return max((item.end for item in [*self.clips, *self.captions, *self.overlays]), default=0)

    def media_frames(self, media: Media) -> int:
        provenance = media.sound_provenance or media.speech_provenance
        if provenance is not None and provenance.sample_count is not None:
            # A float such as 56000 / 24000 rounds slightly above 70 / 30.
            # Reinterpreting its decimal spelling then ceiling adds a false
            # frame. Keep the verified PCM duration rational all the way through.
            duration = Fraction(provenance.sample_count, provenance.sample_rate) * self.fps
        else:
            # Do not resize legacy saved clips or infer unrecorded PCM counts.
            duration = Fraction(str(media.duration)) * self.fps
        # Generated speech keeps its last partial frame. Playback/export pads
        # the remainder with silence rather than dropping source samples.
        if provenance is not None:
            return math.ceil(duration)
        frames = int(duration)
        if media.decoded_video_end_num is not None and media.decoded_video_end_den is not None:
            # Match the normalized FFmpeg fps filter's nearest EOF boundary.
            # Keep longer audio and legacy metadata-based ranges unchanged.
            end = Fraction(media.decoded_video_end_num, media.decoded_video_end_den)
            frames = max(frames, round_half_up(end * self.fps))
        return frames

    def validate(self) -> None:
        if (type(self.track_edit_locks) is not list or len(self.track_edit_locks) != 4
                or any(type(locked) is not bool for locked in self.track_edit_locks)):
            raise ValueError("Track edit locks must be a list of exactly four booleans")
        _text(self.name, "project name")
        _number(self.master_gain_db, "master output gain", -96, 24)
        # Canonical float and positive zero keep saved state and cache keys stable.
        self.master_gain_db = float(self.master_gain_db) if self.master_gain_db else 0.0
        _integer(self.width, "width", 1, 16384)
        _integer(self.height, "height", 1, 16384)
        _integer(self.fps_num, "fps_num", 1, 1000000)
        _integer(self.fps_den, "fps_den", 1, 1000000)
        if not Fraction(1, 1000) <= self.fps <= 1000:
            raise ValueError("Unsupported project frame rate")
        if not isinstance(self.media, list) or not isinstance(self.clips, list):
            raise ValueError("media and clips must be lists")
        validate_timeline_markers(self.timeline_markers)
        validate_captions(self.captions)
        validate_caption_style(self.caption_style)
        validate_overlays(self.overlays)
        sources = {}
        for media in self.media:
            if not isinstance(media, Media):
                raise ValueError("Invalid media entry")
            _text(media.id, "media id", True)
            if media.id in sources:
                raise ValueError("Duplicate media id")
            sources[media.id] = media
            _text(media.path, "media path", True)
            _text(media.name, "media name")
            _number(media.duration, "media duration", 0, 1e10)
            if media.decoded_video_end_num is not None or media.decoded_video_end_den is not None:
                if not media.has_video:
                    raise ValueError("Decoded video endpoint requires video")
                _integer(media.decoded_video_end_num, "decoded video endpoint numerator", 1, 2**53 - 1)
                _integer(media.decoded_video_end_den, "decoded video endpoint denominator", 1, 1000000000)
                end = Fraction(media.decoded_video_end_num, media.decoded_video_end_den)
                if (end.numerator != media.decoded_video_end_num or end.denominator != media.decoded_video_end_den
                        or end > Fraction(str(media.duration)) + 1):
                    raise ValueError("Invalid decoded video endpoint")
            _integer(media.width, "media width", 0, 1000000)
            _integer(media.height, "media height", 0, 1000000)
            _integer(media.fps_num, "media fps_num", 1, 1000000)
            _integer(media.fps_den, "media fps_den", 1, 1000000)
            _boolean(media.has_video, "has_video")
            _boolean(media.has_audio, "has_audio")
            if media.input_encoding not in ("rec709", "apple_log_bt2020"):
                raise ValueError("Unsupported media input encoding")
            if media.input_encoding == "apple_log_bt2020" and not media.has_video:
                raise ValueError("Apple Log input requires video")
            _text(media.color_space, "color_space")
            for name in ("color_transfer", "color_primaries", "color_range"):
                _text(getattr(media, name), name)
            _number(media.audio_offset, "audio_offset", -1e10, 1e10)
            if media.speech_provenance is not None:
                validate_speech_provenance(media.speech_provenance)
                if media.has_video or not media.has_audio or media.duration <= 0 or media.audio_offset != 0:
                    raise ValueError("Generated speech must be positive-duration, zero-offset audio-only media")
                if media.speech_provenance.sample_count is not None:
                    expected_duration = (media.speech_provenance.sample_count
                                         / media.speech_provenance.sample_rate)
                    # Permit only one representable floating-point step around
                    # the exact PCM quotient, never a duration-relative epsilon.
                    # This keeps display metadata consistent with exact timing.
                    if abs(media.duration - expected_duration) > math.ulp(expected_duration):
                        raise ValueError("Generated speech duration does not match its recorded PCM sample count")
            if media.sound_provenance is not None:
                validate_sound_provenance(media.sound_provenance)
                if media.speech_provenance is not None or media.has_video or not media.has_audio or media.audio_offset != 0:
                    raise ValueError("Sound effects must be zero-offset audio-only media")
                duration = media.sound_provenance.sample_count / media.sound_provenance.sample_rate
                if abs(media.duration - duration) > math.ulp(duration):
                    raise ValueError("Sound duration does not match its recorded PCM sample count")
            if not media.has_video and not media.has_audio:
                raise ValueError("Media must contain video or audio")
        validate_media_bins(self.media_bins, self.media_bin_ids, set(sources))
        validate_source_ranges(self.source_ranges, set(sources))
        ids = set()
        tracks: dict[int, list[Clip]] = {i: [] for i in range(4)}
        for clip in self.clips:
            if not isinstance(clip, Clip):
                raise ValueError("Invalid clip entry")
            _text(clip.id, "clip id", True)
            _text(clip.media_id, "clip media_id", True)
            if clip.id in ids:
                raise ValueError("Duplicate clip id")
            ids.add(clip.id)
            if clip.media_id not in sources:
                raise ValueError("Clip references unknown media")
            source = sources[clip.media_id]
            _integer(clip.track, "track", 0, 3)
            validate_clip_effects(clip.effects, clip.track)
            validate_stabilization_settings(clip.stabilization)
            if clip.stabilization.enabled and (clip.track >= 2 or not source.has_video):
                raise ValueError("Stabilization requires a picture clip on a video track")
            validate_transform(clip.transform)
            if not is_identity(clip.transform) and (clip.track >= 2 or not source.has_video):
                raise ValueError("Transform requires a picture clip on a video track")
            validate_composite(clip.composite)
            if not clip.composite.is_legacy() and (clip.track >= 2 or not source.has_video):
                raise ValueError("Compositing requires a picture clip on a video track")
            validate_motion_keyframes(clip.motion_keyframes)
            if clip.motion_keyframes and (clip.track >= 2 or not source.has_video):
                raise ValueError("Motion requires a picture clip on a video track")
            validate_restoration_settings(clip.restoration)
            if clip.restoration.enabled and (clip.track >= 2 or not source.has_video):
                raise ValueError("Restoration requires a picture clip on a video track")
            _integer(clip.start, "start")
            _integer(clip.duration, "duration", 1)
            if clip.time_map is None:
                _integer(clip.source_in, "source_in")
            elif (type(clip.source_in) is not int and not isinstance(clip.source_in, Fraction)):
                raise ValueError("Source position must be an exact integer or fraction")
            mapping = clip.timing
            validate_time_map(mapping)
            if mapping.source_in != clip.source_in:
                raise ValueError("Clip source origin does not match its time map")
            if duration_frames(mapping) != clip.duration:
                raise ValueError("Clip duration does not match its time map")
            if mapping.source_out > self.media_frames(source):
                raise ValueError("Clip exceeds source duration")
            if clip.track < 2 and not source.has_video:
                raise ValueError("Video tracks require video media")
            if clip.track >= 2 and not source.has_audio:
                raise ValueError("Audio tracks require audio media")
            validate_audio_gain_keyframes(clip.audio_gain_keyframes)
            if (clip.audio_gain_keyframes
                    and sample_boundary(clip.end, self.fps) - sample_boundary(clip.start, self.fps)
                    > MAX_AUDIO_GAIN_SAMPLE):
                raise ValueError("Audio gain envelope output sample budget exceeds the supported bound")
            _number(clip.gain_db, "gain_db", -96, 24)
            _boolean(clip.muted, "muted")
            _boolean(clip.source_audio_enabled, "source_audio_enabled")
            if clip.audio_mix_anchor is not None:
                if type(clip.audio_mix_anchor) is not AudioMixAnchor:
                    raise ValueError("Invalid audio mix anchor")
                clip.audio_mix_anchor.validate()
            _integer(clip.fade_in, "fade_in", 0, clip.duration)
            _integer(clip.fade_out, "fade_out", 0, clip.duration)
            if not isinstance(clip.grade, Grade):
                raise ValueError("Invalid grade")
            for name, low, high in (("exposure", -10, 10), ("linear_exposure", -10, 10), ("contrast", 0, 4), ("contrast_pivot", 0, 1), ("saturation", 0, 4), ("temperature", -1, 1), ("tint", -1, 1), ("shadows", -1, 1), ("highlights", -1, 1)):
                _number(getattr(clip.grade, name), name, low, high)
            for name, low, high in (("lift", -1, 1), ("gamma", 0.1, 4), ("gain", 0, 4)):
                _vector(getattr(clip.grade, name), name, low, high)
            for name in ("master_curve", "red_curve", "green_curve", "blue_curve"):
                _curve(getattr(clip.grade, name), name)
            _hue_curve(clip.grade.hue_hue_curve, "hue_hue_curve", -0.5, 0.5)
            _hue_curve(clip.grade.hue_sat_curve, "hue_sat_curve", 0, 2)
            validate_qualifier(clip.grade.qualifier)
            validate_white_balance(clip.grade.white_balance_enabled, clip.grade.white_balance_gains)
            _boolean(clip.grade.enabled, "grade enabled")
            _text(clip.grade.lut_path, "lut_path")
            _number(clip.grade.lut_strength, "lut_strength", 0, 1)
            if clip.grade.input_encoding not in ("rec709", "srgb", "linear_rec709", "apple_log_bt2020"):
                raise ValueError("Input encoding must be rec709, srgb, linear_rec709 or apple_log_bt2020")
            tracks[clip.track].append(clip)
        for clips in tracks.values():
            ordered = sorted(clips, key=lambda c: c.start)
            if any(a.end > b.start for a, b in zip(ordered, ordered[1:])):
                raise ValueError("Clips on the same track may not overlap")

        validate_clip_groups(self.clip_groups, ids)
        validate_video_transitions(self)
        validate_av_links(self)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data = asdict(self)
        for clip, serialized in zip(self.clips, data["clips"]):
            serialized.pop("source_in")
            serialized["time_map"] = clip.timing.to_dict()
        return {"format": FORMAT, "version": VERSION, **data}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        if not isinstance(data, dict):
            raise ValueError("Project must be a JSON object")
        data = deepcopy(data)
        version = data.pop("version", None)
        if data.pop("format", None) != FORMAT or type(version) is not int or version not in range(1, VERSION + 1):
            raise ValueError("Unsupported project format or version")
        try:
            if version >= 42:
                if "av_links" not in data:
                    raise ValueError("Version 42 and later projects require an A/V links list")
                data["av_links"] = av_links_from_dict(data["av_links"], version=version)
            else:
                if "av_links" in data:
                    raise ValueError("Legacy projects cannot contain A/V links")
                data["av_links"] = []
            if version >= 39:
                if "timeline_markers" not in data:
                    raise ValueError("Version 39 projects require timeline markers")
                data["timeline_markers"] = timeline_markers_from_dict(data["timeline_markers"])
            else:
                if "timeline_markers" in data:
                    raise ValueError("Legacy projects cannot contain timeline markers")
                data["timeline_markers"] = []
            if version >= 38:
                if "source_ranges" not in data:
                    raise ValueError("Version 38 projects require saved source ranges")
                data["source_ranges"] = source_ranges_from_dict(data["source_ranges"])
            else:
                if "source_ranges" in data:
                    raise ValueError("Legacy projects cannot contain saved source ranges")
                data["source_ranges"] = []
            if version >= 37:
                if "track_edit_locks" not in data:
                    raise ValueError("Version 37 projects require track edit locks")
            else:
                if "track_edit_locks" in data:
                    raise ValueError("Legacy projects cannot contain track edit locks")
                data["track_edit_locks"] = [False] * 4
            if version >= 36:
                if "clip_groups" not in data or not isinstance(data["clip_groups"], list):
                    raise ValueError("Version 36 projects require a clip groups list")
                if len(data["clip_groups"]) > MAX_CLIP_GROUPS:
                    raise ValueError("Clip groups must be a list of at most 10000 entries")
                groups = []
                for item in data["clip_groups"]:
                    if not isinstance(item, dict) or set(item) != {"id", "clip_ids"}:
                        raise ValueError("Invalid clip group fields")
                    if (not isinstance(item["clip_ids"], list)
                            or not 2 <= len(item["clip_ids"]) <= MAX_CLIP_GROUP_MEMBERS):
                        raise ValueError("Clip groups require 2..20000 unique clips")
                    groups.append(ClipGroup(**item))
                data["clip_groups"] = groups
            else:
                if "clip_groups" in data:
                    raise ValueError("Legacy projects cannot contain clip groups")
                data["clip_groups"] = []
            if version >= 35:
                if "media_bins" not in data or "media_bin_ids" not in data:
                    raise ValueError("Version 35 projects require media bins and membership")
                if not isinstance(data["media_bins"], list) or len(data["media_bins"]) > MAX_MEDIA_BINS:
                    raise ValueError("Media bins must be a list of at most 1024 entries")
                bins = []
                for item in data["media_bins"]:
                    if not isinstance(item, dict) or set(item) != {"id", "name", "parent_id"}:
                        raise ValueError("Invalid media bin fields")
                    bins.append(MediaBin(**item))
                data["media_bins"] = bins
            else:
                if "media_bins" in data or "media_bin_ids" in data:
                    raise ValueError("Legacy projects cannot contain media bin fields")
                data["media_bins"] = []
                data["media_bin_ids"] = {}
            if version >= 34:
                if "master_gain_db" not in data:
                    raise ValueError("Version 34 projects require master output gain")
            else:
                if "master_gain_db" in data:
                    raise ValueError("Legacy projects cannot contain master output gain")
                data["master_gain_db"] = 0.0
            if version >= 33:
                if "video_transitions" not in data or not isinstance(data["video_transitions"], list):
                    raise ValueError("Version 33 projects require a video transitions list")
                data["video_transitions"] = [VideoTransition.from_dict(item) for item in data["video_transitions"]]
            else:
                if "video_transitions" in data and data["video_transitions"] != []:
                    raise ValueError("Legacy projects cannot contain video transitions")
                data["video_transitions"] = []
            if not isinstance(data.get("media", []), list) or not isinstance(data.get("clips", []), list):
                raise ValueError("media and clips must be lists")
            if not isinstance(data.get("captions", []), list) or len(data.get("captions", [])) > MAX_CAPTIONS:
                raise ValueError("Captions must be a list of at most 20000 entries")
            data["captions"] = [Caption(**item) for item in data.get("captions", [])]
            data["caption_style"] = CaptionStyle(**data.get("caption_style", {}))
            if not isinstance(data.get("overlays", []), list) or len(data.get("overlays", [])) > MAX_OVERLAYS:
                raise ValueError("Overlays must be a list of at most 256 entries")
            overlays = []
            for item in data.get("overlays", []):
                if not isinstance(item, dict):
                    raise ValueError("Invalid overlay entry")
                item = dict(item)
                keyframes = item.get("keyframes", [{}])
                if not isinstance(keyframes, list) or not 1 <= len(keyframes) <= MAX_OVERLAY_KEYFRAMES:
                    raise ValueError("Overlay keyframes must be a list of 1..128 entries")
                item["keyframes"] = [OverlayKeyframe(**keyframe) for keyframe in keyframes]
                overlays.append(Overlay(**item))
            data["overlays"] = overlays
            media = []
            for item in data.get("media", []):
                item = dict(item)
                if item.get("speech_provenance") is not None:
                    item["speech_provenance"] = SpeechProvenance(**item["speech_provenance"])
                if item.get("sound_provenance") is not None:
                    if not isinstance(item['sound_provenance'], dict):
                        raise ValueError("Invalid sound provenance")
                    if version < 13:
                        raise ValueError("Legacy projects cannot contain sound provenance")
                    if version < 14 and (item['sound_provenance'].get('origin') == 'external-cc0'
                            or any(name in item['sound_provenance'] for name in SOURCE_FIELDS)):
                        raise ValueError("Legacy projects cannot contain external sound source fields")
                    item["sound_provenance"] = SoundProvenance(**item["sound_provenance"])
                if version < 15 and any(item.get(name) is not None for name in
                                        ("decoded_video_end_num", "decoded_video_end_den")):
                    raise ValueError("Legacy projects cannot contain decoded video endpoint")
                if version < 29 and item.get("input_encoding", "rec709") != "rec709":
                    raise ValueError("Legacy projects cannot contain Apple Log input encoding")
                media.append(Media(**item))
            data["media"] = media
            clips = []
            for item in data.get("clips", []):
                item = dict(item)
                if version >= 41:
                    if "audio_gain_keyframes" not in item:
                        raise ValueError("Version 41 clips require audio_gain_keyframes")
                    item["audio_gain_keyframes"] = _audio_gain_keyframes_from_dict(item["audio_gain_keyframes"])
                elif "audio_gain_keyframes" in item:
                    raise ValueError("Legacy clips cannot contain audio_gain_keyframes")
                audio_fields = {"source_audio_enabled", "audio_mix_anchor"}
                if version >= 40:
                    if not audio_fields <= item.keys():
                        raise ValueError("Version 40 clips require source audio state and mix anchor")
                    if item["audio_mix_anchor"] is not None:
                        item["audio_mix_anchor"] = AudioMixAnchor.from_dict(item["audio_mix_anchor"])
                elif audio_fields & item.keys():
                    raise ValueError("Legacy clips cannot contain source audio separation fields")
                if version >= 7:
                    if "source_in" in item or "time_map" not in item:
                        raise ValueError("Version 7 and later clips require only canonical time_map timing")
                    item["time_map"] = ClipTimeMap.from_dict(item["time_map"])
                    item["source_in"] = item["time_map"].source_in
                elif "time_map" in item:
                    raise ValueError("Legacy clips cannot contain a time map")
                if version >= 8:
                    if "stabilization" not in item:
                        raise ValueError("Version 8 clips require stabilization settings")
                    item["stabilization"] = StabilizationSettings.from_dict(item["stabilization"])
                elif "stabilization" in item:
                    raise ValueError("Legacy clips cannot contain stabilization settings")
                if version >= 9:
                    if "restoration" not in item:
                        raise ValueError("Version 9 clips require restoration settings")
                    item["restoration"] = RestorationSettings.from_dict(item["restoration"])
                elif "restoration" in item:
                    raise ValueError("Legacy clips cannot contain restoration settings")
                if version >= 16:
                    if "transform" not in item:
                        raise ValueError("Version 16 clips require transform settings")
                    item["transform"] = ClipTransform.from_dict(item["transform"])
                elif "transform" in item:
                    raise ValueError("Legacy clips cannot contain transform settings")
                if version >= 31:
                    if "composite" not in item:
                        raise ValueError("Version 31 clips require compositing settings")
                    item["composite"] = ClipComposite.from_dict(item["composite"])
                elif "composite" in item:
                    raise ValueError("Legacy clips cannot contain compositing settings")
                if version >= 32:
                    if "motion_keyframes" not in item:
                        raise ValueError("Version 32 clips require motion_keyframes")
                    entries = item["motion_keyframes"]
                    if not isinstance(entries, list) or len(entries) > MAX_MOTION_KEYFRAMES:
                        raise ValueError("Motion keyframes must be a list of at most 128 entries")
                    item["motion_keyframes"] = [ClipMotionKeyframe.from_dict(key) for key in entries]
                elif "motion_keyframes" in item:
                    raise ValueError("Legacy clips cannot contain motion_keyframes")
                grade = item.get("grade", {})
                if not isinstance(grade, dict):
                    raise ValueError("Invalid grade")
                if version < 29 and grade.get("input_encoding") == "apple_log_bt2020":
                    raise ValueError("Legacy projects cannot contain Apple Log input encoding")
                wb_fields = {"white_balance_enabled", "white_balance_gains"}
                if version >= 27 and not wb_fields <= grade.keys():
                    raise ValueError("Version 27 clips require white balance settings")
                if version < 27 and wb_fields & grade.keys():
                    raise ValueError("Legacy projects cannot contain white balance settings")
                if version >= 26 and "contrast_pivot" not in grade:
                    raise ValueError("Version 26 clips require contrast_pivot")
                if version < 26 and "contrast_pivot" in grade:
                    raise ValueError("Legacy projects cannot contain contrast_pivot")
                hue_fields = {"hue_hue_curve", "hue_sat_curve"}
                if version >= 17 and not hue_fields <= grade.keys():
                    raise ValueError("Version 17 clips require both selective hue curves")
                if version < 17 and hue_fields & grade.keys():
                    raise ValueError("Legacy projects cannot contain selective hue curves")
                if version >= 18 and "qualifier" not in grade:
                    raise ValueError("Version 18 clips require qualifier settings")
                if version < 18 and "qualifier" in grade:
                    raise ValueError("Legacy projects cannot contain qualifier settings")
                if 18 <= version < 30:
                    qualifier = grade["qualifier"]
                    defaults = QualifierSettings().to_dict()
                    old_fields = set(defaults) - {'window_keyframes'}
                    if version < 25:
                        old_fields -= CHANNEL_ENABLE_FIELDS
                    if version < 24:
                        old_fields -= ASYMMETRIC_SOFTNESS_FIELDS
                    if version < 23:
                        old_fields -= {'matte_morphology'}
                    if version < 22:
                        old_fields -= {'matte_smoothing'}
                    if version < 21:
                        old_fields -= {'matte_black', 'matte_white'}
                    if version < 20:
                        old_fields -= {'inclusion_ranges', 'exclusion_ranges'}
                    if version == 18:
                        old_fields = {key for key in old_fields if not key.startswith('window_')}
                    if not isinstance(qualifier, dict) or set(qualifier) != old_fields:
                        raise ValueError(f"Version {version} requires exactly the legacy qualifier fields")
                    migrated = {**defaults, **qualifier}
                    range_defaults = QualifierRange().to_dict()
                    legacy_range_fields = set(range_defaults)
                    if version < 25:
                        legacy_range_fields -= CHANNEL_ENABLE_FIELDS
                    if version < 24:
                        legacy_range_fields -= ASYMMETRIC_SOFTNESS_FIELDS
                    for name in ('inclusion_ranges', 'exclusion_ranges'):
                        entries = migrated[name]
                        if not isinstance(entries, list) or len(entries) > MAX_QUALIFIER_RANGES:
                            raise ValueError('Legacy qualifier range sets must be bounded lists')
                        upgraded = []
                        for entry in entries:
                            if not isinstance(entry, dict) or set(entry) != legacy_range_fields:
                                raise ValueError('Legacy qualifier range requires exactly its documented fields')
                            upgraded.append({**range_defaults, **entry})
                        migrated[name] = upgraded
                    grade["qualifier"] = migrated
                item["grade"] = Grade(**grade)
                effects = item.get("effects", [])
                if not isinstance(effects, list) or len(effects) > MAX_CLIP_EFFECTS:
                    raise ValueError("Clip effects must be a list of at most 16 entries")
                shape_fields = {"radius", "feather"}
                for effect in effects:
                    if not isinstance(effect, dict):
                        raise ValueError("Invalid clip effect")
                    if version >= 28 and not shape_fields <= effect.keys():
                        raise ValueError("Version 28 effects require vignette radius and feather")
                    if version < 28 and shape_fields & effect.keys():
                        raise ValueError("Legacy projects cannot contain vignette radius or feather")
                item["effects"] = [ClipEffect(**effect) for effect in effects]
                clips.append(Clip(**item))
            data["clips"] = clips
            project = cls(**data)
            project.validate()
            return project
        except (TypeError, KeyError, AttributeError) as exc:
            raise ValueError(f"Malformed project: {exc}") from exc

    def save(self, path: str | Path) -> None:
        destination = Path(path).expanduser().resolve()
        data = self.to_dict()
        # Save-as must never destroy an input, including a symlink or hardlink
        # alias. Compare missing inputs too: restoring one must remain possible.
        protected = [m.path for m in self.media]
        protected.extend(c.grade.lut_path for c in self.clips if c.grade.lut_path)
        protected.extend(overlay.asset_path for overlay in self.overlays)
        for overlay in self.overlays:
            if Path(overlay.asset_path).suffix.lower() != ".json":
                continue
            # This dependency reader is bounded and does not decode PNG data.
            # Keep it lazy so ordinary model edits have no asset/backend cost.
            from .sticker_assets import StickerAssetError, sticker_input_paths
            try:
                protected.extend(sticker_input_paths(overlay.asset_path))
            except StickerAssetError:
                # Broken or temporarily missing packs must remain saveable,
                # without risking destruction of a recoverable frame. Their
                # contained inputs cannot be enumerated reliably, so preserve
                # the whole pack directory until it can be read again.
                pack_root = Path(overlay.asset_path).expanduser().resolve().parent
                # A hardlink outside that directory may still alias a frame
                # whose name we could not discover. Do not replace such an
                # alias while the pack is unreadable either.
                hardlink = destination.exists() and destination.stat().st_nlink > 1
                if destination == pack_root or pack_root in destination.parents or hardlink:
                    raise ValueError("A project cannot overwrite a possible input of an unreadable overlay frame pack")
        if self.caption_style.font_directory:
            font_root = Path(self.caption_style.font_directory).expanduser().resolve()
            if destination == font_root or font_root in destination.parents:
                raise ValueError("A project cannot overwrite a file in its caption font directory")
        for source in protected:
            source_path = Path(source).expanduser().resolve()
            aliases = source_path == destination
            if not aliases and destination.exists() and source_path.exists():
                aliases = os.path.samefile(destination, source_path)
            if aliases:
                raise ValueError("A project cannot overwrite an input media, LUT, or overlay asset file")
        # Sound-library files travel with a saved project. No source is changed,
        # and unrelated imported video/audio retain the existing relink behavior.
        for media in data["media"]:
            provenance = media.get("sound_provenance")
            if provenance:
                from .sound_library import copy_sound_asset, verify_sound_file
                target = destination.parent / (destination.stem + ".assets") / "sounds" / (provenance["output_sha256"] + ".wav")
                source = Path(media["path"])
                if source.is_file() or target.is_file():
                    # A previous successful save remains portable even when the
                    # library original later disappears in this live session.
                    source = source if source.is_file() else target
                    verify_sound_file(source, provenance["output_sha256"],
                                      provenance["sample_rate"], provenance["sample_count"])
                    media["path"] = str(copy_sound_asset(source, provenance["output_sha256"], target))
            # Missing sources remain saveable for subsequent explicit relinking.
            media["path"] = _relative_path(media["path"], destination.parent)
        for clip in data["clips"]:
            if clip["grade"]["lut_path"]:
                clip["grade"]["lut_path"] = _relative_path(clip["grade"]["lut_path"], destination.parent)
        for overlay in data["overlays"]:
            overlay["asset_path"] = _relative_path(overlay["asset_path"], destination.parent)
        if data["caption_style"]["font_directory"]:
            data["caption_style"]["font_directory"] = _relative_path(data["caption_style"]["font_directory"], destination.parent)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent, delete=False) as stream:
                temporary = stream.name
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    @classmethod
    def load(cls, path: str | Path) -> Project:
        path = Path(path).expanduser().resolve()
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Project file exceeds 32 MiB")
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"Duplicate JSON key: {key}")
                result[key] = value
            return result
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream, object_pairs_hook=unique_object)
        project = cls.from_dict(data)
        for media in project.media:
            media.path = str((path.parent / media.path).resolve())
        for clip in project.clips:
            if clip.grade.lut_path:
                clip.grade.lut_path = str((path.parent / clip.grade.lut_path).resolve())
        for overlay in project.overlays:
            overlay.asset_path = str((path.parent / overlay.asset_path).resolve())
        if project.caption_style.font_directory:
            project.caption_style.font_directory = str((path.parent / project.caption_style.font_directory).resolve())
        return project

    def missing_media(self) -> list[Media]:
        return [media for media in self.media if not Path(media.path).is_file()]


def _relative_path(path: str, base: Path) -> str:
    absolute = Path(path).expanduser().resolve()
    try:
        return Path(os.path.relpath(absolute, base)).as_posix()
    except ValueError:  # Different Windows drives cannot be made relative.
        return str(absolute)


class Editor:
    """Validated all-or-nothing edits with independent undo/redo snapshots."""

    def __init__(self, project: Project | None = None):
        self._owner_thread = threading.get_ident()
        self._lock = threading.RLock()
        self.__transaction_active = False
        self.session_id = uuid4().hex
        self.revision = 0
        self._project = project if project is not None else Project()
        self.project.validate()
        self._undo: list[Project] = []
        self._redo: list[Project] = []

    def __deepcopy__(self, memo):
        """Independent editor snapshots must not copy a lock or session token."""
        self._assert_owner()
        clone = type(self)(deepcopy(self.project, memo))
        memo[id(self)] = clone
        clone._undo = deepcopy(self._undo, memo)
        clone._redo = deepcopy(self._redo, memo)
        clone.revision = self.revision
        return clone

    @property
    def project(self) -> Project:
        return self._project

    @project.setter
    def project(self, value: Project) -> None:
        """Replacing a project is a new session, never a proposal rebase."""
        self._assert_owner()
        if self.__transaction_active:
            raise ValueError("Nested editor transactions are not supported")
        value.validate()
        with self._lock:
            self._project = value
            self.session_id = uuid4().hex
            self.revision += 1
            self._undo.clear()
            self._redo.clear()

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner_thread:
            raise ValueError("Editor changes must run on its owning thread")

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def _commit(self, change):
        # Generic callbacks cannot authorize membership edits. Only the private
        # membership-only transaction below supplies an exact expected snapshot.
        return self.__commit(change)

    def __commit(self, change, *, group_membership: list[ClipGroup] | None = None,
                 track_edit_locks: list[bool] | None = None,
                 timeline_markers: list[TimelineMarker] | None = None,
                 clip_additions: tuple[list[Clip], list[ClipGroup], list[AVLink]] | None = None,
                 av_link_membership: list[AVLink] | None = None,
                 linked_av_candidate: Project | None = None,
                 linked_av_split_candidate: Project | None = None,
                 source_audio_separation: tuple[str, Clip] | None = None,
                 source_audio_reattach: tuple[str, Clip] | None = None):
        self._assert_owner()
        with self._lock:
            if self.__transaction_active:
                raise ValueError("Nested editor transactions are not supported")
            before = deepcopy(self.project)
            self.__transaction_active = True
            try:
                result = change()
                if timeline_markers is not None:
                    expected = deepcopy(before)
                    expected.timeline_markers = timeline_markers
                    if self.project != expected:
                        raise ValueError("Marker changes may change only timeline marker metadata")
                elif self.project.timeline_markers != before.timeline_markers:
                    raise ValueError("Use timeline marker commands to change timeline markers")
                if track_edit_locks is not None:
                    expected = deepcopy(before)
                    expected.track_edit_locks = track_edit_locks
                    if self.project != expected:
                        raise ValueError("Track lock changes may change only the requested lock state")
                elif self.project.track_edit_locks != before.track_edit_locks:
                    raise ValueError("Use set_track_edit_lock to change track edit locks")
                self._guard_track_edit_locks(before, self.project)
                if clip_additions is not None:
                    expected = deepcopy(before)
                    expected.clips.extend(deepcopy(clip_additions[0]))
                    expected.clip_groups.extend(deepcopy(clip_additions[1]))
                    expected.av_links.extend(deepcopy(clip_additions[2]))
                    if self.project != expected:
                        raise ValueError("Clip paste may only append its approved clips and groups")
                elif group_membership is not None:
                    expected = deepcopy(before)
                    expected.clip_groups = deepcopy(group_membership)
                    if self.project != expected:
                        raise ValueError("Group/Ungroup may change only clip group membership")
                else:
                    self._guard_clip_group_edit(before)
                if av_link_membership is not None:
                    expected = deepcopy(before)
                    expected.av_links = deepcopy(av_link_membership)
                    if self.project != expected:
                        raise ValueError("Link/Unlink may change only A/V link metadata")
                elif linked_av_split_candidate is not None:
                    if self.project != linked_av_split_candidate:
                        raise ValueError("Linked A/V split may change only its approved pair candidate")
                elif linked_av_candidate is not None:
                    if self.project != linked_av_candidate:
                        raise ValueError("Linked A/V commands may change only their approved pair edit")
                else:
                    self._guard_av_link_edit(before, self.project,
                        appended_links=clip_additions[2] if clip_additions is not None else ())
                # Endpoints cannot be silently retimed, replaced or removed by
                # generic edits. Pair translations are validated in final state.
                previous = {c.id: c for c in before.clips}
                current = {c.id: c for c in self.project.clips}
                if linked_av_split_candidate is not None:
                    # New right V/A records inherit special structural emission
                    # and historical anchors only through this exact snapshot.
                    # The generic/link-slip/trim paths retain the old fence.
                    if self.project != linked_av_split_candidate:
                        raise ValueError("Linked A/V split may change only its approved pair candidate")
                elif source_audio_reattach is not None:
                    removed_id, retained = source_audio_reattach
                    expected = deepcopy(before)
                    expected.clips = [deepcopy(retained) if c.id == retained.id else c
                                      for c in expected.clips if c.id != removed_id]
                    if self.project != expected:
                        raise ValueError("Source audio reattach may only restore its video audio and remove the selected audio clip")
                elif source_audio_separation is not None:
                    source_id, detached = source_audio_separation
                    expected = deepcopy(before)
                    next(c for c in expected.clips if c.id == source_id).source_audio_enabled = False
                    expected.clips.append(deepcopy(detached))
                    if self.project != expected:
                        raise ValueError("Source audio separation may only disable its source and append approved audio")
                else:
                    for clip_id in previous.keys() & current.keys():
                        a, b = previous[clip_id], current[clip_id]
                        if (a.source_audio_enabled, a.audio_mix_anchor) != (b.source_audio_enabled, b.audio_mix_anchor):
                            raise ValueError("Use source audio separation to change structural audio state")
                    if clip_additions is None and any(
                            not current[clip_id].source_audio_enabled or current[clip_id].audio_mix_anchor is not None
                            for clip_id in current.keys() - previous.keys()):
                        raise ValueError("Special source audio state requires an approved separation or copy")
                for transition in before.video_transitions:
                    if transition not in self.project.video_transitions:
                        continue
                    for clip_id in (transition.left_clip_id, transition.right_clip_id):
                        a, b = previous[clip_id], current.get(clip_id)
                        if b is None or (a.media_id, a.timing, a.duration) != (b.media_id, b.timing, b.duration):
                            raise ValueError("Remove the video transition before changing its clip timing or media")
                self.project.validate()
            except Exception:
                self._project = before
                raise
            finally:
                self.__transaction_active = False
            if self.project != before:
                self._undo.append(before)
                self._redo.clear()
                self.revision += 1
            return result

    def is_track_edit_locked(self, track: int) -> bool:
        return self.project.is_track_edit_locked(track)

    def guard_track_editable(self, track: int) -> None:
        if self.is_track_edit_locked(track):
            raise ValueError("Track is edit-locked. Unlock the track before editing it.")

    def set_track_edit_lock(self, track: int, locked: bool) -> None:
        """Change exactly one track lock, atomically and on the owner thread."""
        self._assert_owner()
        _integer(track, "track", 0, 3)
        if type(locked) is not bool:
            raise ValueError("Track edit lock must be a boolean")
        desired = list(self.project.track_edit_locks)
        desired[track] = locked
        def change():
            self.project.track_edit_locks = list(desired)
        self.__commit(change, track_edit_locks=desired)

    @staticmethod
    def _guard_track_edit_locks(before: Project, after: Project) -> None:
        """Compare complete records, not a whitelist of current edit controls.

        Original locks govern the whole transaction, including batches, generic
        callbacks, group operations, media relinks, and destination-track edits.
        Undo/redo deliberately restore historical snapshots outside this guard.
        """
        locked = {track for track, value in enumerate(before.track_edit_locks) if value}
        if not locked:
            return
        previous = {clip.id: clip for clip in before.clips if clip.track in locked}
        current = {clip.id: clip for clip in after.clips if clip.track in locked}
        if previous != current:
            raise ValueError("Track is edit-locked. Unlock the track before editing it.")
        ids = set(previous)
        for name, references in (
                ("clip_groups", lambda value: set(value.clip_ids)),
                ("av_links", lambda value: set(value.clip_ids)),
                ("video_transitions", lambda value: {value.left_clip_id, value.right_clip_id})):
            old = [value for value in getattr(before, name) if ids & references(value)]
            new = [value for value in getattr(after, name) if ids & references(value)]
            if old != new:
                raise ValueError("Unlock the track before changing its groups or transitions")
        media_ids = {clip.media_id for clip in previous.values()}
        old_media = {media.id: media for media in before.media if media.id in media_ids}
        new_media = {media.id: media for media in after.media if media.id in media_ids}
        if old_media != new_media:
            raise ValueError("Unlock the track before changing its source media")
        if ids and any(getattr(before, name) != getattr(after, name)
                       for name in ("width", "height", "fps_num", "fps_den")):
            raise ValueError("Unlock populated tracks before changing project dimensions or frame rate")

    def _guard_clip_group_edit(self, before: Project) -> None:
        """Keep generic edits from bypassing persistent group invariants."""
        previous = {clip.id: clip for clip in before.clips}
        current = {clip.id: clip for clip in self.project.clips}
        expected_groups = []
        for group in before.clip_groups:
            surviving = [clip_id for clip_id in group.clip_ids if clip_id in current]
            if not surviving:
                # Complete explicit deletion must remove its membership record
                # in this same transaction; no stale records or auto-cascade.
                continue
            if len(surviving) != len(group.clip_ids):
                raise ValueError("Ungroup clips before deleting only part of a group")
            expected_groups.append(group)
            shifts = set()
            for clip_id in group.clip_ids:
                a, b = previous[clip_id], current[clip_id]
                if ((a.media_id, a.track, a.source_in, a.timing, a.duration)
                        != (b.media_id, b.track, b.source_in, b.timing, b.duration)):
                    raise ValueError("Ungroup clips before changing their source, timing or track")
                shifts.add(b.start - a.start)
            if len(shifts) != 1:
                raise ValueError("Ungroup clips before moving only part of a group")
        if self.project.clip_groups != expected_groups:
            raise ValueError("Use Group/Ungroup to change clip group membership")

    def _commit_clip_groups(self, groups: list[ClipGroup]):
        """Apply an exact membership-only edit, not an authorization flag."""
        desired = deepcopy(groups)
        def change():
            self.project.clip_groups = deepcopy(desired)
        return self.__commit(change, group_membership=desired)

    def _commit_clip_paste(self, clips: list[Clip], groups: list[ClipGroup], av_links=None):
        """Narrow append-only transaction, never a general membership bypass.

        Every old record remains exact; new groups may reference only the new
        clips. The central lock/validation/history guards still apply.
        """
        self._assert_owner()
        additions, memberships = deepcopy(clips), deepcopy(groups)
        links = deepcopy(av_links) if av_links is not None else []
        if not additions:
            raise ValueError("Clip paste requires new clips")
        if any(clip.audio_mix_anchor is not None for clip in additions):
            raise ValueError("Clip paste creates new contributions without historical mix anchors")
        ids = {clip.id for clip in additions}
        if (len(ids) != len(additions)
                or ids.intersection(clip.id for clip in self.project.clips)
                or any(not set(group.clip_ids) <= ids for group in memberships)
                or any(not set(link.clip_ids) <= ids for link in links)):
            raise ValueError("Clip paste groups may reference only new clips")
        def change():
            self.project.clips.extend(additions)
            self.project.clip_groups.extend(memberships)
            self.project.av_links.extend(links)
            return tuple(additions)
        return self.__commit(change, clip_additions=(additions, memberships, links))

    def _commit_source_audio_separation(self, source_clip_id: str, detached_clip: Clip) -> Clip:
        """Commit exactly one independent audio handoff, never a generic bypass.

        This primitive performs no filesystem work. The caller additionally
        consumes a fresh source-inspection receipt before entering it.
        """
        self._assert_owner()
        self.project.validate()
        source = self._clip(source_clip_id)
        self._guard_linked_clip(source_clip_id)
        media = next(item for item in self.project.media if item.id == source.media_id)
        if source.track not in (0, 1) or not media.has_video or not media.has_audio:
            raise ValueError("Source audio separation requires a video clip with source audio")
        if not source.source_audio_enabled:
            raise ValueError("Source audio is already separated")
        if type(detached_clip) is not Clip or detached_clip.track not in (2, 3):
            raise ValueError("Choose audio track A0 or A1 for independent source audio")
        _text(detached_clip.id, "new audio clip id", True)
        if any(clip.id == detached_clip.id for clip in self.project.clips):
            raise ValueError("Separated audio requires a new clip identity")
        self.guard_track_editable(source.track)
        self.guard_track_editable(detached_clip.track)
        self._guard_grouped_clip(source.id)
        self._guard_transition_endpoint(source.id)
        from .audio_components import effective_audio_mix_anchor, guard_audio_handoff_slot
        guard_audio_handoff_slot(self.project, source)
        expected = Clip(detached_clip.id, source.media_id, detached_clip.track,
                        source.start, source.source_in, source.duration,
                        gain_db=source.gain_db, muted=source.muted,
                        fade_in=source.fade_in, fade_out=source.fade_out,
                        time_map=deepcopy(source.time_map),
                        audio_gain_keyframes=deepcopy(source.audio_gain_keyframes),
                        audio_mix_anchor=AudioMixAnchor(*effective_audio_mix_anchor(source)))
        if detached_clip != expected:
            raise ValueError("Separated audio must preserve the exact source audio contract without picture settings")
        candidate = deepcopy(self.project)
        next(c for c in candidate.clips if c.id == source.id).source_audio_enabled = False
        candidate.clips.append(deepcopy(expected))
        candidate.validate()
        def change():
            self._clip(source.id).source_audio_enabled = False
            added = deepcopy(expected)
            self.project.clips.append(added)
            return added
        return self.__commit(change, source_audio_separation=(source.id, expected))

    def _commit_source_audio_reattach(self, video_clip_id: str, audio_clip_id: str) -> Clip:
        """Exact two-record recombination, not a general structural-state bypass.

        Recompute the complete approved replacement here, independently of the
        caller's preview. All other records must remain byte-for-byte equal in
        model terms. The caller additionally verifies source freshness.
        """
        self._assert_owner()
        from .source_audio_reattach import _candidate
        _, retained = _candidate(self.project, video_clip_id, audio_clip_id)
        def change():
            video = self._clip(video_clip_id)
            for name in ('source_audio_enabled', 'audio_mix_anchor', 'gain_db',
                         'muted', 'fade_in', 'fade_out', 'audio_gain_keyframes'):
                setattr(video, name, deepcopy(getattr(retained, name)))
            self.project.clips = [c for c in self.project.clips if c.id != audio_clip_id]
            return video
        return self.__commit(change, source_audio_reattach=(audio_clip_id, retained))

    @staticmethod
    def _guard_av_link_edit(before: Project, after: Project, *, appended_links=()) -> None:
        """Future generic/batch commands cannot silently edit linked timing.

        Pair move, delete, slip and trim enter separate exact-candidate transactions;
        two matching generic edits are still not an authorization to ripple.
        Parameter edits on an explicit role remain independent.
        """
        if after.av_links != [*before.av_links, *appended_links]:
            raise ValueError("Use Link/Unlink to change A/V link metadata")
        previous = {clip.id: clip for clip in before.clips}
        current = {clip.id: clip for clip in after.clips}
        for link in before.av_links:
            for clip_id in link.clip_ids:
                old, new = previous[clip_id], current.get(clip_id)
                if new is None:
                    raise ValueError("Unlink A/V clips first, or explicitly delete the whole pair")
                if ((old.media_id, old.track, old.start, old.duration, old.timing)
                        != (new.media_id, new.track, new.start, new.duration, new.timing)):
                    raise ValueError("Unlink A/V clips before independent timing, track or source edits")
        if before.av_links and (before.fps_num, before.fps_den) != (after.fps_num, after.fps_den):
            raise ValueError("Unlink A/V clips before changing the project frame rate")

    def av_link_for_clip(self, clip_id: str) -> AVLink | None:
        self._clip(clip_id)
        return next((link for link in self.project.av_links if clip_id in link.clip_ids), None)

    def _guard_linked_clip(self, clip_id: str) -> None:
        if any(clip_id in link.clip_ids for link in self.project.av_links):
            raise ValueError("Unlink A/V clips before independent timing, track or source edits")

    def _guard_complete_av_links(self, selected: set[str]) -> None:
        for link in self.project.av_links:
            if selected.intersection(link.clip_ids) and not set(link.clip_ids) <= selected:
                raise ValueError("Unlink A/V clips first, or select both members of the pair")

    def link_av_clips(self, video_clip_id: str, audio_clip_id: str) -> AVLink:
        """Associate explicit roles without touching audio emission or payload."""
        self._assert_owner()
        self.project.validate()
        video, audio = self._clip(video_clip_id), self._clip(audio_clip_id)
        for link in self.project.av_links:
            if link.clip_ids == (video_clip_id, audio_clip_id):
                return link
        candidate = deepcopy(self.project)
        link = AVLink(uuid4().hex, video_clip_id, audio_clip_id, video.media_id)
        candidate.av_links.append(link)
        candidate.validate()
        self._guard_track_edit_locks(self.project, candidate)
        def change():
            self.project.av_links.append(link)
            return link
        return self.__commit(change, av_link_membership=candidate.av_links)

    def unlink_av_clips(self, clip_ids: Iterable[str]) -> None:
        """Remove touched relations only; embedded video audio stays disabled."""
        self._assert_owner()
        self.project.validate()
        selected = set(_selected_clip_ids(self.project, clip_ids))
        desired = [link for link in self.project.av_links if not selected.intersection(link.clip_ids)]
        def change():
            self.project.av_links = deepcopy(desired)
        self.__commit(change, av_link_membership=desired)

    def slip_linked_av(self, clip_id: str, source_in: int | Fraction | str) -> Clip:
        """Atomic common-source-delta slip; no timeline-local keys/fades rebase.

        The UI additionally uses two worker dependency receipts. This pure
        model primitive is deliberately free of file I/O like other edits.
        """
        self._assert_owner()
        from .linked_av import linked_av_slip_candidate
        candidate = linked_av_slip_candidate(self.project, clip_id, source_in)
        self._guard_track_edit_locks(self.project, candidate)
        ids = set(self.av_link_for_clip(clip_id).clip_ids)
        replacements = {clip.id: clip for clip in candidate.clips if clip.id in ids}
        if replacements[clip_id].timing == self._clip(clip_id).timing:
            # A validated zero delta must not canonicalize live records, touch
            # history/redo, or manufacture an edit on an otherwise locked pair.
            return self._clip(clip_id)
        def change():
            for item in self.project.clips:
                if item.id in replacements:
                    item.time_map = replacements[item.id].time_map
                    item.source_in = replacements[item.id].source_in
            return self._clip(clip_id)
        return self.__commit(change, linked_av_candidate=candidate)

    def expanded_clip_ids(self, clip_ids: Iterable[str]) -> list[str]:
        return expanded_clip_ids(self.project, clip_ids)

    def group_clips(self, clip_ids: Iterable[str]) -> ClipGroup:
        """Group two or more ungrouped clips, without implicit merges or nesting."""
        self._assert_owner()
        self.project.validate()
        selected = _selected_clip_ids(self.project, clip_ids)
        if len(selected) < 2:
            raise ValueError("Select at least two ungrouped clips to group")
        selected_set = set(selected)
        for clip_id in selected:
            self._guard_linked_clip(clip_id)
        touched = [group for group in self.project.clip_groups
                   if selected_set.intersection(group.clip_ids)]
        if len(touched) == 1 and selected_set == set(touched[0].clip_ids):
            return touched[0]
        if touched:
            raise ValueError("Ungroup existing groups before creating a new group")
        group = ClipGroup(uuid4().hex, selected)
        self._commit_clip_groups([*self.project.clip_groups, group])
        return self.project.clip_groups[-1]

    def ungroup_clips(self, clip_ids: Iterable[str]) -> None:
        """Dissolve every touched group in one undo step; clips never change."""
        self._assert_owner()
        self.project.validate()
        selected = set(_selected_clip_ids(self.project, clip_ids))
        groups = [group for group in self.project.clip_groups
                  if not selected.intersection(group.clip_ids)]
        if groups == self.project.clip_groups:
            return
        self._commit_clip_groups(groups)

    def _guard_grouped_clip(self, clip_id: str) -> None:
        self._guard_linked_clip(clip_id)
        if any(clip_id in group.clip_ids for group in self.project.clip_groups):
            raise ValueError("Ungroup clips before changing their source, timing or track")

    def _guard_complete_clip_groups(self, selected: set[str]) -> None:
        for group in self.project.clip_groups:
            members = set(group.clip_ids)
            if members.intersection(selected) and not members <= selected:
                raise ValueError("Ungroup clips first, or select every member of the group")

    def set_master_gain(self, gain_db: float) -> None:
        """Set the entire mix gain atomically, without touching clip controls."""
        def change():
            _number(gain_db, "master output gain", -96, 24)
            self.project.master_gain_db = float(gain_db) if gain_db else 0.0
        return self._commit(change)

    def _clip(self, clip_id: str) -> Clip:
        for clip in self.project.clips:
            if clip.id == clip_id:
                return clip
        raise ValueError(f"Unknown clip: {clip_id}")

    def _video_transition(self, transition_id: str) -> VideoTransition:
        for transition in self.project.video_transitions:
            if transition.id == transition_id:
                return transition
        raise ValueError("Unknown video transition")

    def _guard_transition_endpoint(self, clip_id: str) -> None:
        if any(clip_id in (t.left_clip_id, t.right_clip_id) for t in self.project.video_transitions):
            raise ValueError("Remove the video transition before changing its clip timing or media")

    def prepare_video_transition(self, left_clip_id: str, right_clip_id: str,
                                 duration_frames: int, transition_id: str | None = None):
        """Capture an immutable worker request without I/O or editor mutation."""
        self._assert_owner()
        from .video_transition_preflight import VideoTransitionRequest, project_json, fingerprint
        if transition_id is None:
            transition = VideoTransition(uuid4().hex, left_clip_id, right_clip_id, duration_frames)
            operation = "add"
        else:
            current = self._video_transition(transition_id)
            if (left_clip_id, right_clip_id) != (current.left_clip_id, current.right_clip_id):
                raise ValueError("Video transition validation is stale; validate this edit again")
            transition = replace(current, duration_frames=duration_frames)
            operation = "update"
        candidate = self._video_transition_candidate(operation, transition)
        return VideoTransitionRequest(self.session_id, self.revision,
            fingerprint(project_json(self.project)), project_json(candidate), transition, operation)

    def _video_transition_candidate(self, operation, transition):
        candidate = deepcopy(self.project)
        if operation == "add":
            candidate.video_transitions.append(transition)
        elif operation == "update":
            current = self._video_transition(transition.id)
            if (transition.left_clip_id, transition.right_clip_id) != (current.left_clip_id, current.right_clip_id):
                raise ValueError("Video transition validation is stale; validate this edit again")
            candidate.video_transitions[candidate.video_transitions.index(current)] = transition
        else:
            raise ValueError("Video transition validation certificate is invalid or already used")
        self._guard_track_edit_locks(self.project, candidate)
        candidate.validate()
        return candidate

    def commit_video_transition(self, certificate) -> VideoTransition:
        """Consume a worker proof and commit on the owner thread without decode."""
        self._assert_owner()
        from .video_transition_preflight import (VideoTransitionCertificate,
                                                consume_video_transition_certificate)
        if type(certificate) is not VideoTransitionCertificate:
            raise ValueError("Video transition validation certificate is invalid or already used")
        request = certificate.request
        candidate = self._video_transition_candidate(request.operation, request.transition)
        consume_video_transition_certificate(certificate, self, candidate)
        def change():
            self.project.video_transitions = candidate.video_transitions
            return request.transition
        return self._commit(change)

    def add_video_transition(self, left_clip_id: str, right_clip_id: str,
                             duration_frames: int) -> VideoTransition:
        transition = VideoTransition(uuid4().hex, left_clip_id, right_clip_id, duration_frames)
        self.guard_track_editable(self._clip(left_clip_id).track)
        self.guard_track_editable(self._clip(right_clip_id).track)
        def change():
            self.project.video_transitions.append(transition)
            self.project.validate()
            from .video_transition_render import validate_transition_sources
            validate_transition_sources(self.project)
            return transition
        return self._commit(change)

    def update_video_transition(self, transition_id: str, duration_frames: int) -> VideoTransition:
        self._assert_owner()
        current = self._video_transition(transition_id)
        candidate = replace(current, duration_frames=duration_frames)
        if candidate == current:
            return current
        self.guard_track_editable(self._clip(current.left_clip_id).track)
        self.guard_track_editable(self._clip(current.right_clip_id).track)
        def change():
            self.project.video_transitions[self.project.video_transitions.index(current)] = candidate
            self.project.validate()
            from .video_transition_render import validate_transition_sources
            validate_transition_sources(self.project)
            return candidate
        return self._commit(change)

    def delete_video_transition(self, transition_id: str) -> None:
        def change():
            self.project.video_transitions.remove(self._video_transition(transition_id))
        self._commit(change)

    def _timeline_marker(self, marker_id: str) -> TimelineMarker:
        _text(marker_id, "timeline marker id", True)
        item = next((item for item in self.project.timeline_markers if item.id == marker_id), None)
        if item is None:
            raise ValueError("Unknown timeline marker")
        return item

    def _commit_timeline_markers(self, markers: list[TimelineMarker]):
        """Commit only the exact detached marker collection, never render data."""
        desired = deepcopy(markers)
        validate_timeline_markers(desired)
        def change():
            self.project.timeline_markers = deepcopy(desired)
        self.__commit(change, timeline_markers=desired)

    def add_timeline_marker(self, name: str, start_frame: int,
                            end_frame: int | None = None, *, comment: str = "",
                            color: str = "yellow") -> TimelineMarker:
        """Add fixed project-frame metadata, even beyond the current duration."""
        self._assert_owner()
        item = TimelineMarker(uuid4().hex, name, comment, color, start_frame, end_frame)
        self._commit_timeline_markers([*self.project.timeline_markers, item])
        return item

    def update_timeline_marker(self, marker_id: str, *, name: str, comment: str,
                               color: str, start_frame: int,
                               end_frame: int | None) -> TimelineMarker:
        """Replace explicit values while retaining identity; equal values are no-op."""
        self._assert_owner()
        old = self._timeline_marker(marker_id)
        item = replace(old, name=name, comment=comment, color=color,
                       start_frame=start_frame, end_frame=end_frame)
        desired = list(self.project.timeline_markers)
        desired[desired.index(old)] = item
        self._commit_timeline_markers(desired)
        return item

    def delete_timeline_marker(self, marker_id: str) -> None:
        self._assert_owner()
        old = self._timeline_marker(marker_id)
        self._commit_timeline_markers([item for item in self.project.timeline_markers if item.id != old.id])

    def _source_range(self, range_id: str) -> SourceRange:
        _text(range_id, "saved source range id", True)
        item = next((item for item in self.project.source_ranges if item.id == range_id), None)
        if item is None:
            raise ValueError("Unknown saved source range")
        return item

    def save_source_range(self, media_id: str, name: str, in_frame: int,
                          out_frame: int, binding: SourceRangeBinding) -> SourceRange:
        """Save worker-verified marks as metadata in one owner-thread edit.

        Binding freshness is established by the source worker, not by model I/O.
        Only creation checks current metadata bounds; later source replacement
        must not discard, shorten or rebind an existing saved selection.
        """
        def change():
            from .source_overwrite import complete_source_frames
            _text(media_id, "saved source range media id", True)
            media = next((item for item in self.project.media if item.id == media_id), None)
            if media is None:
                raise ValueError("Saved source range references unknown media")
            _integer(in_frame, "saved source range In", 0, MAX_SOURCE_RANGE_FRAME)
            _integer(out_frame, "saved source range Out", 1, MAX_SOURCE_RANGE_FRAME)
            start, end = Fraction(in_frame, 1) / self.project.fps, Fraction(out_frame, 1) / self.project.fps
            fps = self.project.fps
            item = SourceRange(uuid4().hex, name, media_id, start.numerator, start.denominator,
                               end.numerator, end.denominator, fps.numerator, fps.denominator,
                               deepcopy(binding))
            range_frames(item, fps, complete_source_frames(media, fps))
            self.project.source_ranges.append(item)
            return item
        return self._commit(change)

    def rename_source_range(self, range_id: str, name: str) -> SourceRange:
        def change():
            old = self._source_range(range_id)
            item = replace(old, name=name)
            self.project.source_ranges[self.project.source_ranges.index(old)] = item
            return item
        return self._commit(change)

    def delete_source_range(self, range_id: str) -> None:
        def change():
            self.project.source_ranges.remove(self._source_range(range_id))
        self._commit(change)

    def _media_bin(self, bin_id: str) -> MediaBin:
        _text(bin_id, "media bin id", True)
        item = next((item for item in self.project.media_bins if item.id == bin_id), None)
        if item is None:
            raise ValueError("Unknown media bin")
        return item

    def _media_bin_target(self, bin_id: str | None) -> None:
        if bin_id is not None:
            self._media_bin(bin_id)

    def _media_batch_ids(self, media_ids: Iterable[str]) -> set[str]:
        if not isinstance(media_ids, Iterable) or isinstance(media_ids, (str, bytes)):
            raise ValueError("Media IDs must be a collection")
        selected = set()
        for media_id in media_ids:
            _text(media_id, "media id", True)
            selected.add(media_id)
        if not selected <= {item.id for item in self.project.media}:
            raise ValueError("Unknown media in batch")
        return selected

    def create_media_bin(self, name: str, parent_id: str | None = None) -> MediaBin:
        def change():
            self._media_bin_target(parent_id)
            item = MediaBin(uuid4().hex, name, parent_id)
            self.project.media_bins.append(item)
            return item
        return self._commit(change)

    def rename_media_bin(self, bin_id: str, name: str) -> MediaBin:
        def change():
            item = self._media_bin(bin_id)
            item.name = name
            return item
        return self._commit(change)

    def reparent_media_bin(self, bin_id: str, parent_id: str | None = None) -> MediaBin:
        def change():
            self._media_bin_target(parent_id)
            item = self._media_bin(bin_id)
            item.parent_id = parent_id
            return item
        return self._commit(change)

    def move_media_to_bin(self, media_ids: Iterable[str], bin_id: str | None = None) -> None:
        def change():
            self._media_bin_target(bin_id)
            for media_id in self._media_batch_ids(media_ids):
                if bin_id is None:
                    self.project.media_bin_ids.pop(media_id, None)
                else:
                    self.project.media_bin_ids[media_id] = bin_id
        self._commit(change)

    def delete_media_bin(self, bin_id: str) -> None:
        """Delete only an empty virtual bin, never its children or source files."""
        def change():
            item = self._media_bin(bin_id)
            if (bin_id in self.project.media_bin_ids.values()
                    or any(child.parent_id == bin_id for child in self.project.media_bins)):
                raise ValueError("Only empty media bins can be deleted")
            self.project.media_bins.remove(item)
        self._commit(change)

    def remove_unused_media(self, media_ids: Iterable[str]) -> None:
        """Remove project references only; even muted timeline clips count as use."""
        def change():
            selected = self._media_batch_ids(media_ids)
            if any(clip.media_id in selected for clip in self.project.clips):
                raise ValueError("Cannot remove media used by timeline clips")
            if any(item.media_id in selected for item in self.project.source_ranges):
                raise ValueError("Cannot remove media referenced by saved source ranges")
            self.project.media = [item for item in self.project.media if item.id not in selected]
            for media_id in selected:
                self.project.media_bin_ids.pop(media_id, None)
        self._commit(change)

    def import_media_batch(self, media: Iterable[Media], bin_id: str | None = None) -> list[Media]:
        """Import prepared metadata in one transaction without filesystem access.

        The preparation worker supplies canonical paths. Platform-normalized paths, and
        repeated paths within the batch, are skipped without moving media.
        """
        def change():
            self._media_bin_target(bin_id)
            if not isinstance(media, Iterable) or isinstance(media, (str, bytes, Mapping)):
                raise ValueError("Invalid prepared media entry")
            paths = {os.path.normcase(item.path) for item in self.project.media}
            ids = {item.id for item in self.project.media}
            added = []
            for source in media:
                if not isinstance(source, Media):
                    raise ValueError("Invalid prepared media entry")
                _text(source.path, "media path", True)
                path_key = os.path.normcase(source.path)
                if path_key in paths:
                    continue
                _text(source.id, "media id", True)
                if source.id in ids:
                    raise ValueError("Duplicate media id")
                item = deepcopy(source)
                self.project.media.append(item)
                paths.add(path_key)
                ids.add(item.id)
                if bin_id is not None:
                    self.project.media_bin_ids[item.id] = bin_id
                added.append(item)
            return added
        return self._commit(change)

    def add_media(self, media: Media) -> Media:
        def change():
            item = deepcopy(media)
            self.project.media.append(item)
            return item
        return self._commit(change)

    def add_clip(self, media_id: str, track: int, start: int | None = None) -> Clip:
        def change():
            source = next((m for m in self.project.media if m.id == media_id), None)
            if source is None:
                raise ValueError(f"Unknown media: {media_id}")
            position = max((c.end for c in self.project.clips if c.track == track), default=0) if start is None else start
            clip = Clip(uuid4().hex, media_id, track, position, 0, self.project.media_frames(source))
            if source.input_encoding == "apple_log_bt2020":
                clip.grade.input_encoding = source.input_encoding
            self.project.clips.append(clip)
            return clip
        return self._commit(change)

    def plan_source_overwrite(self, media_id: str, track: int, start: int,
                              source_in: int, source_out: int):
        """Capture a pure whole-project-frame, single-track overwrite preview."""
        from .source_overwrite import plan_source_overwrite
        return plan_source_overwrite(self, media_id, track, start, source_in, source_out)

    def apply_source_overwrite(self, plan) -> Clip:
        """Apply a current validated overwrite plan as exactly one undo step."""
        from .source_overwrite import apply_source_overwrite
        return apply_source_overwrite(self, plan)

    def plan_source_insert(self, media_id: str, track: int, start: int,
                           source_in: int, source_out: int, *,
                           participating_tracks: tuple[int, ...] | None = None,
                           follow_captions: bool = False, follow_stickers: bool = False):
        """Preview source Insert; optional participation belongs only to this edit."""
        from .source_insert import plan_source_insert
        return plan_source_insert(self, media_id, track, start, source_in, source_out,
            participating_tracks=participating_tracks, follow_captions=follow_captions,
            follow_stickers=follow_stickers)

    def apply_source_insert(self, plan) -> Clip:
        """Apply one current Insert plan as exactly one undo step."""
        from .source_insert import apply_source_insert
        return apply_source_insert(self, plan)

    def add_sound_clip(self, media: Media, track: int, start: int) -> Clip:
        """Insert complete sound at an exact frame, atomically and without ripple."""
        def change():
            _integer(track, "sound track", 2, 3)
            _integer(start, "sound start", 0)
            if not isinstance(media, Media) or media.sound_provenance is None:
                raise ValueError("Sound insertion requires provenance")
            source = next((m for m in self.project.media if m.id == media.id), None)
            if source is None:
                source = deepcopy(media)
                self.project.media.append(source)
            elif source != media:
                raise ValueError("Sound media identity conflict")
            clip = Clip(uuid4().hex, source.id, track, start, 0, self.project.media_frames(source))
            self.project.clips.append(clip)
            return clip
        return self._commit(change)

    def add_speech_clip(self, media: Media, caption_id: str, track: int, start: int | None = None) -> Clip:
        """Insert generated audio and its complete clip as one undoable change.

        Conflicts fail atomically; no caption or existing clip is moved or cut.
        The original synthesis snapshot remains valid after caption edits/deletion.
        """
        def change():
            _integer(track, "speech track", 2, 3)
            caption = self._caption(caption_id)
            if not isinstance(media, Media) or media.speech_provenance is None:
                raise ValueError("Generated speech requires provenance")
            if media.speech_provenance.source_caption_id != caption.id:
                raise ValueError("Speech provenance references a different caption")
            source = deepcopy(media)
            self.project.media.append(source)
            clip = Clip(uuid4().hex, source.id, track, caption.start if start is None else start,
                        0, self.project.media_frames(source))
            self.project.clips.append(clip)
            return clip
        return self._commit(change)

    @staticmethod
    def _set_clip_timing(clip: Clip, mapping: ClipTimeMap) -> None:
        validate_time_map(mapping)
        clip.time_map = mapping
        clip.source_in = mapping.source_in
        clip.duration = duration_frames(mapping)
        clip.fade_in = min(clip.fade_in, clip.duration)
        clip.fade_out = min(clip.fade_out, clip.duration)
        clip._normalize_legacy_timing()

    def set_clip_speed(self, clip_id: str, speed: int | Fraction | str) -> Clip:
        """Preserve source span, start and local animation times; never ripple."""
        def change():
            clip = self._clip(clip_id)
            mapping = clip.timing
            self._set_clip_timing(clip, ClipTimeMap(mapping.source_in, mapping.source_out, parse_speed(speed)))
            return clip
        return self._commit(change)

    def split_clip(self, clip_id: str, frame: int) -> tuple[Clip, Clip]:
        def change():
            clip = self._clip(clip_id)
            if not clip.source_audio_enabled or clip.audio_mix_anchor is not None:
                raise ValueError("Splitting separated source audio clips is not supported yet")
            _integer(frame, "split frame")
            if not clip.start < frame < clip.end:
                raise ValueError("Split must be strictly inside the clip")
            left_length = frame - clip.start
            mapping = clip.timing
            boundary = source_position(mapping, left_length)
            right = deepcopy(clip)
            right.id = uuid4().hex
            right.start = frame
            right.audio_gain_keyframes = shift_audio_gain_keyframes(clip.audio_gain_keyframes,
                sample_boundary(frame, self.project.fps) - sample_boundary(clip.start, self.project.fps))
            right = shift_clip_motion(right, left_length)
            right.grade = shift_window_animation(right.grade, left_length)
            self._set_clip_timing(right, ClipTimeMap(boundary, mapping.source_out, mapping.speed))
            right.fade_in = 0
            self._set_clip_timing(clip, ClipTimeMap(mapping.source_in, boundary, mapping.speed))
            clip.fade_out = 0
            self.project.clips.insert(self.project.clips.index(clip) + 1, right)
            return clip, right
        return self._commit(change)

    def split_linked_av(self, plan, verification) -> tuple[Clip, Clip]:
        """Apply one worker-qualified pair split and return its new right pair.

        Unlike independent split this needs fresh CFR/legacy-phase evidence.
        Existing left objects/IDs and link ID remain; other project records are
        untouched, and all four records commit in one exact-candidate Undo.
        """
        from .linked_av_split import validated_split_candidate
        candidate = validated_split_candidate(self, plan, verification)
        link = self.av_link_for_clip(plan.clip_id)
        approved = {clip.id: clip for clip in candidate.clips}
        def change():
            current = {clip.id: clip for clip in self.project.clips}
            for clip_id in link.clip_ids:
                current[clip_id].__dict__.update(deepcopy(approved[clip_id].__dict__))
            self.project.clips = [current[clip.id] if clip.id in current else deepcopy(clip)
                                 for clip in candidate.clips]
            self.project.av_links = deepcopy(candidate.av_links)
            return tuple(self._clip(clip_id) for clip_id in plan.new_ids[:2])
        return self.__commit(change, linked_av_split_candidate=candidate)

    def trim_clip(self, clip_id: str, source_in: int | Fraction | str, duration: int) -> Clip:
        """Choose E = O + duration * speed; retain local animation times."""
        def change():
            clip = self._clip(clip_id)
            origin = parse_source_position(source_in)
            _integer(duration, "duration", 1)
            self._set_clip_timing(clip, ClipTimeMap(origin, origin + duration * clip.speed, clip.speed))
            return clip
        return self._commit(change)

    def trim_clip_source(self, clip_id: str, source_in: int | Fraction | str,
                         source_out: int | Fraction | str) -> Clip:
        """Select source endpoints; preserve speed and local animation times."""
        def change():
            clip = self._clip(clip_id)
            self._set_clip_timing(clip, ClipTimeMap(parse_source_position(source_in),
                                                  parse_source_position(source_out), clip.speed))
            return clip
        return self._commit(change)

    def slip_clip(self, clip_id: str, source_in: int | Fraction | str) -> Clip:
        """Shift the exact selected source span without changing timeline timing.

        source_in is an absolute position in project-frame units, not a delta
        or a native source-frame index. Preserve the partial final interval;
        reconstructing an endpoint from rounded output duration would grow it.
        Full project validation rejects out-of-range edits atomically.
        """
        origin = parse_source_position(source_in)
        current = self._clip(clip_id)
        if origin == current.timing.source_in:
            return current
        def change():
            clip = self._clip(clip_id)
            mapping = clip.timing
            endpoint = origin + mapping.source_out - mapping.source_in
            self._set_clip_timing(clip, ClipTimeMap(origin, endpoint, mapping.speed))
            return clip
        return self._commit(change)

    def trim_clip_left(self, clip_id: str, frames: int) -> Clip:
        """Trim k output frames from the left; preserve source/timeline tail."""
        def change():
            clip = self._clip(clip_id)
            _integer(frames, "left trim frames")
            if frames >= clip.duration:
                raise ValueError("Left trim must leave at least one output frame")
            mapping = clip.timing
            self._set_clip_timing(clip, ClipTimeMap(source_position(mapping, frames),
                                                  mapping.source_out, mapping.speed))
            clip.audio_gain_keyframes = shift_audio_gain_keyframes(clip.audio_gain_keyframes,
                sample_boundary(clip.start + frames, self.project.fps) - sample_boundary(clip.start, self.project.fps))
            clip.start += frames
            clip.motion_keyframes = shift_clip_motion(clip, frames).motion_keyframes
            clip.grade = shift_window_animation(clip.grade, frames)
            return clip
        return self._commit(change)

    def preview_trim_edge(self, clip_id: str, edge: str, delta: int) -> Clip:
        """Return one validated, detached non-ripple edge-trim candidate.

        Delta is a signed integer in timeline frames: positive moves the chosen
        edge later. Advance the chosen source endpoint by delta * speed, rather
        than rebuilding it from the rounded duration, to retain partial tails.
        Left-edge edits rebase every window and motion key, including hidden
        handles; right-edge edits retain local key times. Existing fades must
        still fit, and same-track neighbors and independent anchors never move.
        """
        if edge not in ("left", "right"):
            raise ValueError("Trim edge must be left or right.")
        _integer(delta, "trim delta", -MAX_CAPTION_FRAME, MAX_CAPTION_FRAME)
        # Reject corrupt/stale model state before deriving a candidate. Preview
        # must provide the same validation guarantees as the eventual commit.
        self.project.validate()
        current = self._clip(clip_id)
        if delta:
            self.guard_track_editable(current.track)
            self._guard_grouped_clip(clip_id)
            self._guard_transition_endpoint(clip_id)
        length = current.duration - delta if edge == "left" else current.duration + delta
        if length < 1:
            raise ValueError("Trim must leave at least one timeline frame.")
        start = current.start + delta if edge == "left" else current.start
        _integer(start, "trim start", 0, MAX_CAPTION_FRAME)
        _integer(start + length, "trim end", 1, MAX_CAPTION_FRAME)
        if max(current.fade_in, current.fade_out) > length:
            raise ValueError("Trim would shorten an existing fade. Reduce the fade first.")
        mapping = current.timing
        trimmed = ClipTimeMap(
            mapping.source_in + delta * mapping.speed if edge == "left" else mapping.source_in,
            mapping.source_out + delta * mapping.speed if edge == "right" else mapping.source_out,
            mapping.speed,
        )
        if duration_frames(trimmed) != length:
            raise ValueError("Trim did not preserve the exact timeline boundary.")
        source = next(m for m in self.project.media if m.id == current.media_id)
        if trimmed.source_out > self.project.media_frames(source):
            raise ValueError("Trim exceeds the available source media range.")
        candidate = deepcopy(current)
        self._set_clip_timing(candidate, trimmed)
        candidate.start = start
        if edge == "left" and delta:
            candidate.audio_gain_keyframes = shift_audio_gain_keyframes(current.audio_gain_keyframes,
                sample_boundary(start, self.project.fps) - sample_boundary(current.start, self.project.fps))
            candidate = shift_clip_motion(candidate, delta)
            candidate.grade = shift_window_animation(candidate.grade, delta)
        # Full candidate validation also checks keyframe overflow, media/track
        # compatibility, and collisions without replacing any live objects.
        project = deepcopy(self.project)
        project.clips[self.project.clips.index(current)] = candidate
        project.validate()
        return candidate

    def trim_edge(self, clip_id: str, edge: str, delta: int) -> Clip:
        """Commit one edge trim as one undoable edit, or preserve a true no-op."""
        self._assert_owner()
        candidate = self.preview_trim_edge(clip_id, edge, delta)
        current = self._clip(clip_id)
        if delta == 0:
            return current
        def change():
            self._set_clip_timing(current, candidate.timing)
            current.start = candidate.start
            if edge == "left":
                current.grade = candidate.grade
                current.motion_keyframes = candidate.motion_keyframes
                current.audio_gain_keyframes = candidate.audio_gain_keyframes
            return current
        return self._commit(change)

    def preview_trim_linked_av(self, clip_id: str, edge: str, delta: int) -> tuple[Clip, Clip]:
        """Detached V/A candidates for one exact, non-ripple pair edge trim."""
        self._assert_owner()
        from .linked_av_trim import linked_av_trim_candidate
        candidate = linked_av_trim_candidate(self.project, clip_id, edge, delta)
        link = self.av_link_for_clip(clip_id)
        clips = {clip.id: clip for clip in candidate.clips}
        return tuple(clips[key] for key in link.clip_ids)

    def trim_linked_av(self, clip_id: str, edge: str, delta: int) -> Clip:
        """Commit both existing roles together, never independent edits/unlink.

        Left keys rebase by the project-frame/global-sample displacement;
        right keys remain fixed. Fades retain their edge-anchored frame lengths.
        A zero delta retains object identity, redo history and revision.
        """
        self._assert_owner()
        from .linked_av_trim import linked_av_trim_candidate
        candidate = linked_av_trim_candidate(self.project, clip_id, edge, delta)
        if delta == 0:
            return self._clip(clip_id)
        ids = set(self.av_link_for_clip(clip_id).clip_ids)
        replacements = {clip.id: clip for clip in candidate.clips if clip.id in ids}
        def change():
            for item in self.project.clips:
                if item.id in replacements:
                    approved = replacements[item.id]
                    for name in ('time_map', 'source_in', 'duration', 'start',
                                 'grade', 'motion_keyframes', 'audio_gain_keyframes'):
                        setattr(item, name, getattr(approved, name))
            return self._clip(clip_id)
        return self.__commit(change, linked_av_candidate=candidate)

    def preview_roll_linked_av(self, clip_ids: Iterable[str], cut: int) -> tuple[Clip, Clip, Clip, Clip]:
        """Detached four-role preview of one shared cut; selection stays exact."""
        from .linked_av_roll import linked_av_roll_candidate, _resolve
        selected = _selected_clip_ids(self.project, clip_ids)
        candidate = linked_av_roll_candidate(self.project, selected, cut)
        pairs = _resolve(candidate, selected)
        clips = {clip.id: clip for clip in candidate.clips}
        return tuple(clips[key] for pair in pairs for key in pair.clip_ids)

    def roll_linked_av(self, clip_ids: Iterable[str], cut: int) -> tuple[Clip, Clip, Clip, Clip]:
        """Commit both pairs once through the existing exact-candidate fence.

        No temporary unlink or two edge trims. No new records or structural
        audio authority. Zero displacement preserves live objects and redo.
        """
        self._assert_owner()
        from .linked_av_roll import linked_av_roll_candidate, _resolve
        selected = _selected_clip_ids(self.project, clip_ids)
        candidate = linked_av_roll_candidate(self.project, selected, cut)
        pairs = _resolve(candidate, selected)
        ids = tuple(key for pair in pairs for key in pair.clip_ids)
        if cut == self._clip(ids[0]).end:
            return tuple(self._clip(key) for key in ids)
        replacements = {clip.id: clip for clip in candidate.clips if clip.id in ids}
        def change():
            for item in self.project.clips:
                if item.id in replacements:
                    approved = replacements[item.id]
                    for name in ('time_map', 'source_in', 'duration', 'start',
                                 'grade', 'motion_keyframes', 'audio_gain_keyframes'):
                        setattr(item, name, getattr(approved, name))
            return tuple(self._clip(key) for key in ids)
        return self.__commit(change, linked_av_candidate=candidate)

    def preview_roll_linked_audio(self, clip_ids: Iterable[str], cut: int) -> tuple[Clip, Clip, Clip, Clip]:
        """Detached audio-only cut preview, ordered LV/LA/RV/RA."""
        self._assert_owner()
        from .linked_av_audio_roll import linked_audio_roll_candidate, _resolve
        selected = _selected_clip_ids(self.project, clip_ids)
        candidate = linked_audio_roll_candidate(self.project, selected, cut)
        clips = {clip.id: clip for clip in candidate.clips}
        return tuple(clips[key] for pair in _resolve(candidate, selected) for key in pair.clip_ids)

    def preview_align_linked_audio(self, clip_ids: Iterable[str]) -> tuple[Clip, Clip, Clip, Clip]:
        self._assert_owner()
        from .linked_av_audio_roll import linked_audio_align_candidate, _resolve
        selected = _selected_clip_ids(self.project, clip_ids)
        candidate = linked_audio_align_candidate(self.project, selected)
        clips = {clip.id: clip for clip in candidate.clips}
        return tuple(clips[key] for pair in _resolve(candidate, selected) for key in pair.clip_ids)

    def __edit_linked_audio(self, clip_ids, cut, *, reset):
        from .linked_av_audio_roll import linked_audio_roll_candidate, linked_audio_align_candidate, _resolve
        selected = _selected_clip_ids(self.project, clip_ids)
        candidate = (linked_audio_align_candidate(self.project, selected) if reset
                     else linked_audio_roll_candidate(self.project, selected, cut))
        pairs = _resolve(candidate, selected)
        ids = tuple(key for pair in pairs for key in pair.clip_ids)
        audio_ids = {pair.audio_clip_id for pair in pairs}
        replacements = {clip.id: clip for clip in candidate.clips if clip.id in audio_ids}
        if (candidate.av_links == self.project.av_links
                and all(self._clip(key) == value for key, value in replacements.items())):
            return tuple(self._clip(key) for key in ids)
        def change():
            for item in self.project.clips:
                if item.id in replacements:
                    approved = replacements[item.id]
                    for name in ('time_map', 'source_in', 'duration', 'start',
                                 'grade', 'motion_keyframes', 'audio_gain_keyframes'):
                        setattr(item, name, getattr(approved, name))
            desired = {link.id: link for link in candidate.av_links}
            self.project.av_links[:] = [desired[link.id] if desired[link.id] != link else link
                                       for link in self.project.av_links]
            return tuple(self._clip(key) for key in ids)
        return self.__commit(change, linked_av_candidate=candidate)

    def roll_linked_audio(self, clip_ids: Iterable[str], cut: int) -> tuple[Clip, Clip, Clip, Clip]:
        """Move only the shared audio cut; preserve both live V objects exactly."""
        self._assert_owner()
        return self.__edit_linked_audio(clip_ids, cut, reset=False)

    def align_linked_audio(self, clip_ids: Iterable[str]) -> tuple[Clip, Clip, Clip, Clip]:
        """Align to picture and downgrade only genuinely strict map-equal pairs."""
        self._assert_owner()
        return self.__edit_linked_audio(clip_ids, None, reset=True)

    def preview_roll_cut(self, left_id: str, cut: int) -> tuple[Clip, Clip]:
        """Calculate a same-track outgoing roll without changing live state.

        Move both sides by the integer timeline delta in their own exact source
        speed. Preserve outer endpoints and each clip's partial-tail residue.
        Fades are not silently shortened. Independent anchors never move.
        """
        _integer(cut, "roll cut", 0)
        left = self._clip(left_id)
        adjacent = [c for c in self.project.clips
                    if c.id != left.id and c.track == left.track and c.start == left.end]
        if len(adjacent) != 1:
            raise ValueError("Roll requires one immediately adjacent right clip on the same track.")
        right = adjacent[0]
        if not left.start < cut < right.end:
            raise ValueError("Roll must leave at least one timeline frame in each clip.")
        delta = cut - left.end
        if delta:
            self.guard_track_editable(left.track)
            self._guard_grouped_clip(left.id)
            self._guard_grouped_clip(right.id)
            self._guard_transition_endpoint(left.id)
            self._guard_transition_endpoint(right.id)
        left_map = ClipTimeMap(left.source_in, left.source_out + delta * left.speed, left.speed)
        right_map = ClipTimeMap(right.source_in + delta * right.speed, right.source_out, right.speed)
        sources = {m.id: m for m in self.project.media}
        for clip, mapping, length in ((left, left_map, cut - left.start),
                                      (right, right_map, right.end - cut)):
            if mapping.source_out > self.project.media_frames(sources[clip.media_id]):
                raise ValueError("Roll exceeds the available source media range.")
            if max(clip.fade_in, clip.fade_out) > length:
                raise ValueError("Roll would shorten an existing fade. Reduce the fade first.")
            if duration_frames(mapping) != length:
                raise ValueError("Roll did not preserve the exact timeline boundary.")
        a, b = deepcopy(left), deepcopy(right)
        self._set_clip_timing(a, left_map)
        self._set_clip_timing(b, right_map)
        b.start = cut
        b.audio_gain_keyframes = shift_audio_gain_keyframes(right.audio_gain_keyframes,
            sample_boundary(cut, self.project.fps) - sample_boundary(right.start, self.project.fps))
        b = shift_clip_motion(b, delta)
        b.grade = shift_window_animation(b.grade, delta)
        # New envelopes have an independent exact-sample compiler budget.
        # Check the detached preview too, not only the eventual transaction.
        for candidate in (a, b):
            if (candidate.audio_gain_keyframes
                    and sample_boundary(candidate.end, self.project.fps)
                    - sample_boundary(candidate.start, self.project.fps) > MAX_AUDIO_GAIN_SAMPLE):
                raise ValueError("Audio gain envelope output sample budget exceeds the supported bound")
        return a, b

    def roll_cut(self, left_id: str, cut: int) -> tuple[Clip, Clip]:
        """Move one shared cut atomically; total extent and other objects stay put."""
        a, b = self.preview_roll_cut(left_id, cut)
        left, right = self._clip(a.id), self._clip(b.id)
        if cut == left.end:
            return left, right
        def change():
            self._set_clip_timing(left, a.timing)
            self._set_clip_timing(right, b.timing)
            right.start = b.start
            right.grade = b.grade
            right.motion_keyframes = b.motion_keyframes
            right.audio_gain_keyframes = b.audio_gain_keyframes
            return left, right
        return self._commit(change)

    def move_clip(self, clip_id: str, start: int, track: int | None = None) -> Clip:
        return self.update_clip(clip_id, start=start, **({"track": track} if track is not None else {}))

    def _selected_clips(self, clip_ids: Iterable[str]) -> list[Clip]:
        """Resolve a nonempty, unique ID collection in existing project order."""
        selected = set(_selected_clip_ids(self.project, clip_ids))
        return [clip for clip in self.project.clips if clip.id in selected]

    def preview_move_clips(self, clip_ids: Iterable[str], delta: int) -> list[Clip]:
        """Return detached candidates for one non-rippling group translation.

        Delta is a signed integer in project frames. Only starts change; tracks,
        exact source ranges, local animation keys and all other payload remain
        intact. Validate the complete final placement, never intermediate moves
        against selected clips' old positions. Return clips in project order.
        """
        if type(delta) is not int:
            raise ValueError("Move delta must be an integer")
        self.project.validate()
        selected = {clip.id for clip in self._selected_clips(clip_ids)}
        self._guard_complete_clip_groups(selected)
        self._guard_complete_av_links(selected)
        candidate = deepcopy(self.project)
        moved = []
        for clip in candidate.clips:
            if clip.id in selected:
                if clip.start + delta < 0:
                    raise ValueError("Selected clips cannot start before frame zero")
                clip.start += delta
                moved.append(clip)
        self._guard_track_edit_locks(self.project, candidate)
        candidate.validate()
        from .linked_av import guard_linked_av_move
        guard_linked_av_move(self.project, candidate, selected)
        return moved

    def move_clips(self, clip_ids: Iterable[str], delta: int) -> list[Clip]:
        """Move the whole selection as one undo step, preserving true no-ops."""
        self._assert_owner()
        candidates = self.preview_move_clips(clip_ids, delta)
        live = {clip.id: clip for clip in self.project.clips}
        current = [live[clip.id] for clip in candidates]
        if delta == 0:
            return current
        def change():
            for clip, candidate in zip(current, candidates):
                clip.start = candidate.start
            return current
        if any(set(link.clip_ids).intersection(clip.id for clip in candidates)
               for link in self.project.av_links):
            approved = deepcopy(self.project)
            starts = {clip.id: clip.start for clip in candidates}
            for clip in approved.clips:
                if clip.id in starts:
                    clip.start = starts[clip.id]
            return self.__commit(change, linked_av_candidate=approved)
        return self._commit(change)

    def delete_clips(self, clip_ids: Iterable[str]) -> None:
        """Delete one validated selection atomically, without ripple or asset removal."""
        self._assert_owner()
        self.project.validate()
        selected = {clip.id for clip in self._selected_clips(clip_ids)}
        self._guard_complete_clip_groups(selected)
        self._guard_complete_av_links(selected)
        candidate = deepcopy(self.project)
        candidate.clips = [clip for clip in candidate.clips if clip.id not in selected]
        candidate.clip_groups = [group for group in candidate.clip_groups
                                 if not selected.intersection(group.clip_ids)]
        candidate.av_links = [link for link in candidate.av_links
                              if not selected.intersection(link.clip_ids)]
        candidate.validate()
        def change():
            self.project.clips[:] = [clip for clip in self.project.clips if clip.id not in selected]
            self.project.clip_groups[:] = [group for group in self.project.clip_groups
                                           if not selected.intersection(group.clip_ids)]
            self.project.av_links[:] = [link for link in self.project.av_links
                                       if not selected.intersection(link.clip_ids)]
        if any(selected.intersection(link.clip_ids) for link in self.project.av_links):
            self.__commit(change, linked_av_candidate=candidate)
        else:
            self._commit(change)

    def delete_clip(self, clip_id: str) -> None:
        def change():
            self.project.clips.remove(self._clip(clip_id))
        self._commit(change)

    def plan_gap_close(self, track: int, playhead: int, *, participating_tracks=None,
                       follow_captions: bool = False, follow_stickers: bool = False):
        """Preview one real-neighbor interior gap at an explicit captured target."""
        from .gap_close import plan_gap_close
        return plan_gap_close(self, track, playhead, participating_tracks=participating_tracks,
                              follow_captions=follow_captions, follow_stickers=follow_stickers)

    def apply_gap_close(self, plan) -> None:
        from .gap_close import apply_gap_close
        apply_gap_close(self, plan)

    def plan_ripple_delete(self, clip_id: str, *, participating_tracks=None,
                           follow_captions: bool = False, follow_stickers: bool = False):
        """Preview one committed clip's deletion with per-operation followers."""
        from .ripple_delete import plan_ripple_delete
        return plan_ripple_delete(self, clip_id, participating_tracks=participating_tracks,
            follow_captions=follow_captions, follow_stickers=follow_stickers)

    def apply_ripple_delete(self, plan) -> None:
        from .ripple_delete import apply_ripple_delete
        apply_ripple_delete(self, plan)

    def ripple_delete_clip(self, clip_id: str) -> None:
        """Remove exactly the selected span from its track, as one transaction.

        Existing gaps are retained. Other tracks, captions and stickers are
        anchors and never move. Source timing and all surviving settings remain
        unchanged. Invalid/overlapping input is rejected before any mutation.
        """
        def change():
            self.project.validate()
            selected = self._clip(clip_id)
            track, end, duration = selected.track, selected.end, selected.duration
            affected = {clip.id for clip in self.project.clips
                        if clip.id == selected.id or (clip.track == track and clip.start >= end)}
            if any(affected.intersection(group.clip_ids) for group in self.project.clip_groups):
                raise ValueError("Ungroup affected clips before ripple deletion")
            # Whole-clip source timing alone does not preserve sound at a
            # fractional frame rate: budgets, leading silence and fade sample
            # boundaries can change. Prove the full local production contract
            # before deleting anything; do not impose source-cut restrictions.
            from .clip_audio_translation import guard_local_audio_translation
            for clip in self.project.clips:
                if clip.id != selected.id and clip.track == track and clip.start >= end:
                    translated = deepcopy(clip)
                    translated.start -= duration
                    guard_local_audio_translation(clip, translated, self.project,
                                                  operation='Ripple delete')
            self.project.clips.remove(selected)
            for clip in self.project.clips:
                if clip.track == track and clip.start >= end:
                    clip.start -= duration
        self._commit(change)

    def update_clip(self, clip_id: str, **kwargs) -> Clip:
        allowed = {f.name for f in fields(Clip)} - {"id", "time_map", "source_audio_enabled", "audio_mix_anchor"}
        if not set(kwargs) <= allowed:
            raise ValueError("Unknown or immutable clip property")
        def change():
            clip = self._clip(clip_id)
            if "source_in" in kwargs or "duration" in kwargs:
                mapping = clip.timing
                origin = parse_source_position(kwargs.get("source_in", mapping.source_in))
                endpoint = mapping.source_out
                if "duration" in kwargs:
                    _integer(kwargs["duration"], "duration", 1)
                    endpoint = origin + kwargs["duration"] * mapping.speed
                self._set_clip_timing(clip, ClipTimeMap(origin, endpoint, mapping.speed))
            for key, value in kwargs.items():
                if key in ("source_in", "duration"):
                    continue
                if key == "grade" and isinstance(value, dict):
                    try:
                        value = Grade(**value)
                    except TypeError as exc:
                        raise ValueError(f"Invalid grade: {exc}") from exc
                if key == "stabilization" and isinstance(value, dict):
                    value = StabilizationSettings.from_dict(value)
                if key == "transform" and isinstance(value, dict):
                    value = ClipTransform.from_dict(value)
                if key == "composite" and isinstance(value, dict):
                    value = ClipComposite.from_dict(value)
                if key == "restoration" and isinstance(value, dict):
                    value = RestorationSettings.from_dict(value)
                if key == "motion_keyframes" and isinstance(value, list):
                    value = [ClipMotionKeyframe.from_dict(item) if isinstance(item, dict) else item
                             for item in value]
                if key == "audio_gain_keyframes" and isinstance(value, list):
                    value = [(_audio_gain_keyframes_from_dict([item])[0]
                              if isinstance(item, dict) else item) for item in value]
                setattr(clip, key, deepcopy(value))
            return clip
        return self._commit(change)

    def convert_clip_motion_to_static(self, clip_id: str, frame: int | Fraction) -> Clip:
        """Freeze evaluated local-frame geometry/opacity and clear keys in one undo.

        Rotation, mirrors, transparent exterior and all non-picture settings are
        retained. A static clip is a genuine no-op and preserves redo history.
        """
        self._assert_owner()
        if type(frame) is not int and not isinstance(frame, Fraction):
            raise ValueError('Motion evaluation frame must be an integer or Fraction')
        self.project.validate()
        clip = self._clip(clip_id)
        if not clip.motion_keyframes:
            return clip
        x, y, scale, opacity = evaluate_clip_motion(clip.motion_keyframes, frame)
        return self.update_clip(clip_id,
            transform=replace(clip.transform, x=x, y=y, scale=scale),
            composite=replace(clip.composite, opacity=opacity), motion_keyframes=[])

    def update_stabilization(self, clip_id: str, **changes) -> Clip:
        """Apply bounded settings as one independent, undoable project edit."""
        if not set(changes) <= {f.name for f in fields(StabilizationSettings)}:
            raise ValueError("Unknown stabilization property")
        settings = deepcopy(self._clip(clip_id).stabilization)
        for key, value in changes.items():
            setattr(settings, key, deepcopy(value))
        return self.update_clip(clip_id, stabilization=settings)

    def update_restoration(self, clip_id: str, **changes) -> Clip:
        """Apply local quality-restoration settings as one undoable edit."""
        if not set(changes) <= {f.name for f in fields(RestorationSettings)}:
            raise ValueError("Unknown restoration property")
        settings = deepcopy(self._clip(clip_id).restoration)
        for key, value in changes.items():
            setattr(settings, key, deepcopy(value))
        return self.update_clip(clip_id, restoration=settings)

    def _overlay(self, overlay_id: str) -> Overlay:
        for overlay in self.project.overlays:
            if overlay.id == overlay_id:
                return overlay
        raise ValueError(f"Unknown overlay: {overlay_id}")

    def add_overlay(self, overlay: Overlay) -> Overlay:
        def change():
            item = deepcopy(overlay)
            self.project.overlays.append(item)
            return item
        return self._commit(change)

    def update_overlay(self, overlay_id: str, **changes) -> Overlay:
        if not set(changes) <= {f.name for f in fields(Overlay)} - {"id"}:
            raise ValueError("Unknown or immutable overlay property")
        def change():
            overlay = self._overlay(overlay_id)
            for key, value in changes.items():
                setattr(overlay, key, deepcopy(value))
            return overlay
        return self._commit(change)

    def delete_overlay(self, overlay_id: str) -> None:
        self._commit(lambda: self.project.overlays.remove(self._overlay(overlay_id)))

    def _effect(self, clip_id: str, effect_id: str) -> ClipEffect:
        for effect in self._clip(clip_id).effects:
            if effect.id == effect_id:
                return effect
        raise ValueError(f"Unknown effect: {effect_id}")

    def add_effect(self, clip_id: str, effect: ClipEffect) -> ClipEffect:
        def change():
            item = deepcopy(effect)
            self._clip(clip_id).effects.append(item)
            return item
        return self._commit(change)

    def update_effect(self, clip_id: str, effect_id: str, **changes) -> ClipEffect:
        if not set(changes) <= {f.name for f in fields(ClipEffect)} - {"id"}:
            raise ValueError("Unknown or immutable effect property")
        def change():
            effect = self._effect(clip_id, effect_id)
            for key, value in changes.items():
                setattr(effect, key, deepcopy(value))
            return effect
        return self._commit(change)

    def delete_effect(self, clip_id: str, effect_id: str) -> None:
        self._commit(lambda: self._clip(clip_id).effects.remove(self._effect(clip_id, effect_id)))

    def move_effect(self, clip_id: str, effect_id: str, index: int) -> ClipEffect:
        """Move an effect to a zero-based index in the final ordered stack."""
        def change():
            effects = self._clip(clip_id).effects
            effect = self._effect(clip_id, effect_id)
            _integer(index, "effect index", 0, len(effects) - 1)
            effects.remove(effect)
            effects.insert(index, effect)
            return effect
        return self._commit(change)

    def _caption(self, caption_id: str) -> Caption:
        for caption in self.project.captions:
            if caption.id == caption_id:
                return caption
        raise ValueError(f"Unknown caption: {caption_id}")

    def add_caption(self, start: int, duration: int, text: str, language: str = "und") -> Caption:
        def change():
            caption = Caption(uuid4().hex, start, duration, text, language)
            self.project.captions.append(caption)
            return caption
        return self._commit(change)

    def update_caption(self, caption_id: str, **kwargs) -> Caption:
        if not set(kwargs) <= {f.name for f in fields(Caption)} - {"id"}:
            raise ValueError("Unknown or immutable caption property")
        def change():
            caption = self._caption(caption_id)
            for key, value in kwargs.items():
                setattr(caption, key, normalize_caption_text(value) if key == "text" else deepcopy(value))
            return caption
        return self._commit(change)

    def delete_caption(self, caption_id: str) -> None:
        self._commit(lambda: self.project.captions.remove(self._caption(caption_id)))

    def import_captions(self, captions: list[Caption], replace: bool = False) -> None:
        _boolean(replace, "replace captions")
        validate_captions(captions)
        def change():
            if replace:
                self.project.captions = deepcopy(captions)
            else:
                self.project.captions.extend(deepcopy(captions))
        self._commit(change)

    def update_caption_style(self, **kwargs) -> CaptionStyle:
        if not set(kwargs) <= {f.name for f in fields(CaptionStyle)}:
            raise ValueError("Unknown caption style property")
        def change():
            for key, value in kwargs.items():
                setattr(self.project.caption_style, key, deepcopy(value))
            return self.project.caption_style
        return self._commit(change)

    def undo(self) -> bool:
        self._assert_owner()
        with self._lock:
            if self.__transaction_active:
                raise ValueError("Nested editor transactions are not supported")
            if not self._undo:
                return False
            self._redo.append(deepcopy(self.project))
            self._project = self._undo.pop()
            self.revision += 1
            return True

    def redo(self) -> bool:
        self._assert_owner()
        with self._lock:
            if self.__transaction_active:
                raise ValueError("Nested editor transactions are not supported")
            if not self._redo:
                return False
            self._undo.append(deepcopy(self.project))
            self._project = self._redo.pop()
            self.revision += 1
            return True

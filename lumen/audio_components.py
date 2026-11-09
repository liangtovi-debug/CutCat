"""Pure structural source-audio semantics shared by rendering and edit proofs."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model import Clip, Media

AUDIO_MIX_ORDER_ALGORITHM = 'historical-slot-then-current-start-id-v1'


def clip_emits_source_audio(clip: Clip, media: Media) -> bool:
    """Potential contribution; timing may still compile to complete silence."""
    return media.has_audio and clip.source_audio_enabled and not clip.muted


def effective_audio_mix_anchor(clip: Clip) -> tuple[int, int, str]:
    anchor = clip.audio_mix_anchor
    return ((anchor.track, anchor.start, anchor.clip_id) if anchor is not None
            else (clip.track, clip.start, clip.id))


def audio_mix_order_key(clip: Clip) -> tuple[tuple[int, int, str], int, str]:
    return effective_audio_mix_anchor(clip), clip.start, clip.id


def guard_audio_handoff_slot(project, source):
    """Replacing an ID must not reorder tied historical descendants.

    The first version cannot create tied descendants, but strict valid saved
    projects may contain them. Reject this ambiguous handoff deterministically
    instead of letting a random new UUID change float32 summation order.
    """
    slot = effective_audio_mix_anchor(source), source.start
    if any(clip.id != source.id and
           (effective_audio_mix_anchor(clip), clip.start) == slot
           for clip in project.clips):
        raise ValueError('Source audio separation cannot preserve an ambiguous historical mix slot')

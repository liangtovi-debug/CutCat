"""Whole-clip audio preservation proof; no decoding and no source-cut limits.

This compares the production compiler, not a parallel approximation. It does
not change timing, mixing or rendering. Retiming and complex picture settings
remain eligible whenever the complete local audio contribution is equivalent.
"""
from __future__ import annotations
from .audio_components import clip_emits_source_audio
from copy import copy
from dataclasses import replace
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .model import Clip, Project


def local_audio_contract(clip: Clip, project: Project, *, operation: str):
    """The *entire* local renderer audio contract, not just source endpoints.

    AudioMixBuilder owns gain and global-grid fade compilation. Comparing its
    actual fragment avoids a parallel hand-written approximation drifting from
    the renderer. Use the same fixed input/output labels in both snapshots;
    strip ONLY the final placement delay, leaving any leading-silence delay.
    """
    from .audio_mix import AudioMixBuilder
    from .audio_timing import compile_audio_timing

    media = next(item for item in project.media if item.id == clip.media_id)
    if not clip_emits_source_audio(clip, media):
        return None
    plan = compile_audio_timing(clip, project.fps, media.audio_offset)
    context = copy(project)
    context.clips = [clip]
    fragment = AudioMixBuilder(context).clip_fragment(0, media, input_index=0)
    local = None
    if fragment is not None:
        suffix = f',adelay={plan.start_sample}S:all=1[a0]'
        if not fragment.fragment.endswith(suffix):
            raise ValueError(f'{operation} cannot prove the local audio compiler contract')
        local = fragment.fragment[:-len(suffix)] + '[a0]'
    return replace(plan, start_sample=0), local



def guard_local_audio_translation(original: Clip, translated: Clip,
                                  project: Project, *, operation: str) -> None:
    if (local_audio_contract(original, project, operation=operation)
            != local_audio_contract(translated, project, operation=operation)):
        raise ValueError(f'{operation} would change downstream audio at this sample-grid phase; choose another edit interval or project frame')


def guard_common_audio_translation(original: Clip, translated: Clip,
                                   project: Project, expected_delta: int, *, message: str) -> None:
    """Require one exact placement shift for every audible contribution.

    Callers choose when this promise applies and supply the difference of the
    actual edit boundaries, never a rounded duration. Silent contributions do
    not invent a synchronization constraint. No decode or source cut occurs.
    """
    from .audio_timing import compile_audio_timing
    media = next(item for item in project.media if item.id == original.media_id)
    if not clip_emits_source_audio(original, media):
        return
    old = compile_audio_timing(original, project.fps, media.audio_offset)
    new = compile_audio_timing(translated, project.fps, media.audio_offset)
    if old.silent and new.silent:
        return
    if new.start_sample - old.start_sample != expected_delta:
        raise ValueError(message)

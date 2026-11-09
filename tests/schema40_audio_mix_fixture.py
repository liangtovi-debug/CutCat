# Frozen accepted 540178990dae51c69c3eaefa7c08feca36956d86 schema40 compiler.
# Original audio_mix.py SHA256: 37e6f3040f3444a96640eff491350de72122a3a49540cfe952837ddb47038ddf
# Only imports made absolute; structural/order helpers inlined verbatim semantically.
"""The renderer's exact, pre-encoder 48 kHz stereo timeline mix.

This module only compiles filters. Callers validate the project and fresh source
metadata, verify generated-media provenance and, when necessary, verify the
fixed Rubber Band implementation before running FFmpeg. In particular, building
an audio-only graph never probes, decodes, grades or prepares picture sources.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
from typing import Mapping, TYPE_CHECKING

from lumen.audio_timing import AudioTiming, SAMPLE_RATE, compile_audio_timing
from lumen.timing import sample_boundary
# Frozen accepted schema40 component semantics, not the candidate helpers.
def clip_emits_source_audio(clip, media):
    return media.has_audio and clip.source_audio_enabled and not clip.muted


def audio_mix_order_key(clip):
    anchor = clip.audio_mix_anchor
    slot = ((anchor.track, anchor.start, anchor.clip_id) if anchor is not None
            else (clip.track, clip.start, clip.id))
    return slot, clip.start, clip.id

if TYPE_CHECKING:
    from lumen.model import Clip, Media, Project


def ordered_clips(project: Project) -> tuple[Clip, ...]:
    """Legacy picture/input traversal. Never reorder this list for audio handoff."""
    return tuple(sorted(project.clips, key=lambda clip: (clip.track, clip.start, clip.id)))


def _number(value: float) -> str:
    # Identical to the renderer's numeric serialization. Do not increase its
    # precision here: the filter text and floating-point gain must stay exact.
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Non-finite timeline or grade value.')
    return format(value, '.12g')


@dataclass(frozen=True)
class AudioMixClip:
    fragment: str
    label: str
    timing: AudioTiming


@dataclass(frozen=True)
class CompiledAudioMix:
    inputs: tuple[str, ...]
    graph: str
    end_sample: int
    # Unique actually audible media IDs, in first-input order.
    source_ids: tuple[str, ...]


class AudioMixBuilder:
    """Composable fragments preserve the full renderer's graph insertion order.

    Export interleaves picture and sound per clip. Moving sound into one block
    would change the compiled graph even if its PCM happened to match. Expose
    the three existing fragments instead: silence, each clip, and final mix.
    ``index`` always addresses ``clips`` and determines its stable output label;
    ``input_index`` optionally remaps only the FFmpeg input for audio-only work.
    """

    def __init__(self, project: Project):
        self.master_gain_db = project.master_gain_db
        self.clips = ordered_clips(project)
        self.fps = Fraction(project.fps_num, project.fps_den)
        self.end_sample = sample_boundary(project.duration_frames, self.fps, SAMPLE_RATE)

    @property
    def silence_fragment(self) -> str:
        return f'anullsrc=r=48000:cl=stereo,atrim=end_sample={self.end_sample}[silence]'

    def clip_fragment(self, index: int, media: Media, *,
                      input_index: int | None = None) -> AudioMixClip | None:
        clip = self.clips[index]
        if not clip_emits_source_audio(clip, media):
            return None
        plan = compile_audio_timing(clip, self.fps, media.audio_offset)
        if plan.silent:
            return None
        filters = [*plan.filters, f'volume={_number(clip.gain_db)}dB']
        if clip.fade_in:
            fade_samples = sample_boundary(clip.start + clip.fade_in, self.fps, SAMPLE_RATE) - plan.start_sample
            filters += [f'afade=t=in:ss=0:ns={fade_samples}']
        if clip.fade_out:
            fade_start = sample_boundary(clip.end - clip.fade_out, self.fps, SAMPLE_RATE) - plan.start_sample
            filters += [f'afade=t=out:ss={fade_start}:ns={plan.sample_count - fade_start}']
        filters += [f'adelay={plan.start_sample}S:all=1']
        source_index = index if input_index is None else input_index
        label = f'a{index}'
        return AudioMixClip(f'[{source_index}:a:0]' + ','.join(filters) + f'[{label}]', label, plan)

    def final_fragment(self, labels: list[str] | tuple[str, ...]) -> str:
        """Mix the supplied clip labels after the duration-defining silence bed."""
        # Keep graph input/picture traversal unchanged. Only the summation
        # labels move: separated audio occupies the source's historical slot.
        # No historical slots means the exact legacy supplied-label API:
        # callers may use this final-stage helper with externally named inputs.
        # Production labels already arrive in legacy clip traversal order.
        ordered_labels = tuple(labels)
        if any(clip.audio_mix_anchor is not None for clip in self.clips):
            order = {f'a{index}': audio_mix_order_key(clip)
                     for index, clip in enumerate(self.clips)}
            if (any(type(label) is not str or label not in order for label in ordered_labels)
                    or len(set(ordered_labels)) != len(ordered_labels)):
                raise ValueError('Anchored audio mixing requires distinct known clip labels')
            ordered_labels = tuple(sorted(ordered_labels, key=order.__getitem__))
        audio = ('silence', *ordered_labels)
        # Preserve legacy graphs exactly at unity. Float precision retains
        # overload headroom until this post-sum, post-duration gain stage.
        gain = (f',volume={_number(self.master_gain_db)}dB:precision=float'
                if self.master_gain_db else '')
        return (''.join(f'[{label}]' for label in audio)
                + f'amix=inputs={len(audio)}:normalize=0:duration=first,'
                + f'atrim=end_sample={self.end_sample}{gain}[aout]')


def compile_audio_mix(project: Project, checked_media: Mapping[str, Media]) -> CompiledAudioMix:
    """Compile only audible inputs, using the caller's checked local paths.

    The source path, audio presence and audio offset must describe the same
    verified source snapshot. No file inspection or picture work occurs here.
    Each audible clip keeps its own FFmpeg input, as it does during export.
    Explicit input video/subtitle/data disabling prevents incidental decoding.
    The caller maps ``[aout]`` to a 48 kHz, two-channel PCM output, not AAC.
    """
    builder = AudioMixBuilder(project)
    inputs, fragments, labels, source_ids = [], [builder.silence_fragment], [], []
    input_index = 0
    for index, clip in enumerate(builder.clips):
        media = checked_media[clip.media_id]
        fragment = builder.clip_fragment(index, media, input_index=input_index)
        if fragment is None:
            continue
        inputs += ['-protocol_whitelist', 'file,pipe', '-vn', '-sn', '-dn', '-i', str(media.path)]
        fragments.append(fragment.fragment)
        labels.append(fragment.label)
        source_ids.append(clip.media_id)
        input_index += 1
    fragments.append(builder.final_fragment(labels))
    return CompiledAudioMix(tuple(inputs), ';'.join(fragments), builder.end_sample,
                            tuple(dict.fromkeys(source_ids)))

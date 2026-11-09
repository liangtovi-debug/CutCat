"""Non-mutating, sample-exact windows of the existing 48 kHz stereo mix.

A range is not a shortened project or a newly trimmed clip. Each contributing
clip keeps its original source selection, retimer state, output-sample gain
clock and fades. Only the final local signal is sliced, before its range-local
placement. Decoder/resampler history before the selected source may still be
traversed; this is explicitly not random-access audio decoding.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Mapping, TYPE_CHECKING

from .audio_mix import AudioMixBuilder, AudioMixClip, CompiledAudioMix, ordered_clips
from .timing import sample_boundary

if TYPE_CHECKING:
    from .model import Media, Project

MAX_RANGE_SECONDS = 10
MAX_AUDIO_PREFIX_SECONDS = 30
MAX_SOURCE_AUDIO_SECONDS = 300
FALLBACK = 'Use Build full timeline preview for this project/range.'


class RangeAudioMixError(ValueError):
    pass


@dataclass(frozen=True)
class RangeAudioWork:
    clip_id: str
    original_local_start_sample: int
    original_local_end_sample: int
    range_delay_samples: int
    source_start_sample: int
    source_end_sample: int
    # The original timing filters retain their complete source endpoint. Their
    # downstream consumer stops at original_local_end_sample, so no unrelated
    # clip tail is emitted; stateful codecs/retimers may require lookahead.
    source_decode_may_traverse_earlier_samples: bool = True


class RangeAudioMixBuilder(AudioMixBuilder):
    """AudioMixBuilder-compatible fragments with original clocks and a window.

    ``clips`` includes only records intersecting the requested frame interval,
    in the legacy picture/input order. Their values and the project are never
    modified. Input labels remain indexed by this tuple; callers may remap only
    the actual FFmpeg input with ``input_index``. ``end_sample`` is the RANGE
    output length, whereas range_start/end_sample are global boundaries.

    At most 30 seconds of clip-local history precede the range, followed by at
    most 10 seconds of range. Excessive history refuses rather than restarting
    stateful filters with a different sound. The original selected source end
    is additionally capped at 300 seconds from source origin. Decoder traversal
    still needs the outer worker's cancellation/deadline guard.
    """

    def __init__(self, project: Project, start_frame: int, end_frame: int):
        if (type(start_frame) is not int or type(end_frame) is not int
                or not 0 <= start_frame < end_frame <= project.duration_frames):
            raise RangeAudioMixError('Choose a non-empty integer timeline range. ' + FALLBACK)
        fps = Fraction(project.fps_num, project.fps_den)
        if Fraction(end_frame - start_frame, 1) / fps > MAX_RANGE_SECONDS:
            raise RangeAudioMixError('Range preview supports at most 10 seconds. ' + FALLBACK)
        super().__init__(project)
        self.clips = tuple(clip for clip in ordered_clips(project)
                           if clip.start < end_frame and clip.end > start_frame)
        self.start_frame, self.end_frame = start_frame, end_frame
        self.range_start_sample = sample_boundary(start_frame, fps, 48000)
        self.range_end_sample = sample_boundary(end_frame, fps, 48000)
        self.end_sample = self.range_end_sample - self.range_start_sample
        self.work: dict[str, RangeAudioWork] = {}

    def clip_fragment(self, index: int, media: Media, *,
                      input_index: int | None = None) -> AudioMixClip | None:
        original = super().clip_fragment(index, media, input_index=input_index)
        if original is None:
            return None
        clip, plan = self.clips[index], original.timing
        first = max(0, self.range_start_sample - plan.start_sample)
        last = min(plan.sample_count, self.range_end_sample - plan.start_sample)
        if last <= first or last <= plan.leading_samples:
            return None
        if first > MAX_AUDIO_PREFIX_SECONDS * 48000:
            raise RangeAudioMixError('Range audio requires more than 30 seconds of '
                                     'clip history. ' + FALLBACK)
        # Conservatively bound ALL source samples admitted to the original
        # stateful chain, including source_in. Do not claim that downstream
        # trimming enables codec/resampler random access.
        if plan.source_end_sample > MAX_SOURCE_AUDIO_SECONDS * 48000:
            raise RangeAudioMixError('Range audio source history exceeds 300 seconds. ' + FALLBACK)
        delay = max(0, plan.start_sample - self.range_start_sample)
        suffix = f',adelay={plan.start_sample}S:all=1[{original.label}]'
        if not original.fragment.endswith(suffix):
            # Fail visibly if the shared renderer's placement contract evolves.
            raise RangeAudioMixError('The full audio compiler placement changed. ' + FALLBACK)
        fragment = (original.fragment[:-len(suffix)]
                    + f',atrim=start_sample={first}:end_sample={last},asetpts=PTS-STARTPTS'
                    + f',adelay={delay}S:all=1[{original.label}]')
        self.work[clip.id] = RangeAudioWork(
            clip.id, first, last, delay, plan.source_start_sample, plan.source_end_sample)
        return AudioMixClip(fragment, original.label, plan)


def compile_range_audio_mix(project: Project, checked_media: Mapping[str, Media],
                            start_frame: int, end_frame: int) -> CompiledAudioMix:
    """Compile audible range inputs only; source inspection belongs to caller.

    Disabled video/subtitle/data input streams prevent incidental picture work.
    The returned graph maps ``[aout]`` to exact 48 kHz stereo float PCM before
    any playback encoding. No input seeking, model mutation or file I/O occurs.
    """
    builder = RangeAudioMixBuilder(project, start_frame, end_frame)
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

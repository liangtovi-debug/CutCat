"""Bounded clip-local, output-sample gain automation.

The clock is 48 kHz AFTER source selection/retiming/leading silence/padding.
Keys are offsets from the existing base gain, with linear-dB (not amplitude)
or outgoing hold interpolation. Hidden keys are retained as interpolation
handles. The first/last value holds outside the key range.

The sample and difference bounds stay exactly representable in the double
expression evaluator. A balanced selection tree bounds each sample to at most
eight comparisons for 128 keys; its leaves contain only trusted numbers.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace
import math

AUDIO_GAIN_ALGORITHM_ID = 'clip-local-48k-linear-db-hold-aeval-f32-balanced-v1'
MAX_AUDIO_GAIN_KEYFRAMES = 128
MAX_AUDIO_GAIN_SAMPLE = 1 << 40
MIN_AUDIO_GAIN_DB = -96.0
MAX_AUDIO_GAIN_DB = 24.0
# Both channel expressions share a single expression via val(ch). The cap is
# defensive, independent of the structural key-count and numeric bounds.
MAX_AUDIO_GAIN_EXPRESSION_LENGTH = 32768


@dataclass(frozen=True)
class AudioGainKeyframe:
    sample: int
    offset_db: float = 0.0
    interpolation: str = 'linear'

    def __post_init__(self):
        _validate_key(self)
        # Stable JSON/cache identity: accepted integer dB and signed zero have
        # exactly one serialized representation; coordinates remain integers.
        object.__setattr__(self, 'offset_db', float(self.offset_db) if self.offset_db else 0.0)


def _validate_key(key):
    if type(key) is not AudioGainKeyframe:
        raise ValueError('Audio gain keys must be AudioGainKeyframe values.')
    if type(key.sample) is not int or not -MAX_AUDIO_GAIN_SAMPLE <= key.sample <= MAX_AUDIO_GAIN_SAMPLE:
        raise ValueError('Audio gain key sample must be an integer within the supported range.')
    if (type(key.offset_db) not in (int, float)
            or not MIN_AUDIO_GAIN_DB <= key.offset_db <= MAX_AUDIO_GAIN_DB
            or not math.isfinite(key.offset_db)):
        raise ValueError('Audio gain offset must be finite and between -96 and 24 dB.')
    if type(key.interpolation) is not str or key.interpolation not in ('linear', 'hold'):
        raise ValueError('Audio gain interpolation must be linear or hold.')


def validate_audio_gain_keyframes(keys) -> None:
    if type(keys) is not list or len(keys) > MAX_AUDIO_GAIN_KEYFRAMES:
        raise ValueError('Audio gain automation must contain at most 128 keys in a list.')
    previous = None
    for key in keys:
        _validate_key(key)
        if previous is not None and key.sample <= previous:
            raise ValueError('Audio gain key samples must be unique and increasing.')
        previous = key.sample


def shift_audio_gain_keyframes(keys, delta_samples: int) -> list[AudioGainKeyframe]:
    """Rebase all handles by old sample minus the global-boundary delta."""
    validate_audio_gain_keyframes(keys)
    if type(delta_samples) is not int:
        raise ValueError('Audio gain key shift must be an integer sample count.')
    return [replace(key, sample=key.sample - delta_samples) for key in keys]


def evaluate_audio_gain_offset(keys, sample: int) -> float:
    """Inspector-only scalar evaluation; production audio uses the compiler."""
    validate_audio_gain_keyframes(keys)
    if type(sample) is not int or not -MAX_AUDIO_GAIN_SAMPLE <= sample <= MAX_AUDIO_GAIN_SAMPLE:
        raise ValueError('Audio gain sample must be an integer within the supported range.')
    if not keys:
        return 0.0
    index = bisect_right([key.sample for key in keys], sample) - 1
    if index < 0:
        return keys[0].offset_db
    left = keys[index]
    if index == len(keys) - 1 or left.interpolation == 'hold':
        return left.offset_db
    right = keys[index + 1]
    return left.offset_db + (right.offset_db - left.offset_db) * ((sample - left.sample) / (right.sample - left.sample))


def _number(value: float) -> str:
    # Unlike the unchanged legacy scalar volume serialization, new envelope
    # coefficients retain round-trip double precision explicitly.
    return format(float(value), '.17g')


def compile_audio_gain_filters(keys, sample_count: int) -> tuple[str, ...]:
    """No keys means exactly no filters; never change legacy negotiation.

    aeval is per sample (n begins at zero), NOT volume's per-audio-frame eval.
    Explicit float32 stereo on either side prevents aeval's double format
    requirement from changing legacy base-gain/fade/amix negotiation.
    """
    validate_audio_gain_keyframes(keys)
    if not keys:
        return ()
    if type(sample_count) is not int or not 0 < sample_count <= MAX_AUDIO_GAIN_SAMPLE:
        raise ValueError('Audio gain automation exceeds the supported output sample budget.')
    # Each leaf is (-infinity, first), [first, second), ..., [last, infinity).
    leaves = [_number(keys[0].offset_db)]
    for left, right in zip(keys, keys[1:]):
        value = _number(left.offset_db)
        if left.interpolation == 'linear' and left.offset_db != right.offset_db:
            value = (f'({value}+({_number(right.offset_db - left.offset_db)})'
                     f'*((n-({left.sample}))/({right.sample - left.sample})))')
        leaves.append(value)
    leaves.append(_number(keys[-1].offset_db))

    def tree(lo, hi):
        if hi - lo == 1:
            return leaves[lo]
        mid = (lo + hi) // 2
        return f'if(lt(n,{keys[mid - 1].sample}),{tree(lo, mid)},{tree(mid, hi)})'

    expression = f'val(ch)*pow(10,({tree(0, len(leaves))})/20)'
    if len(expression) > MAX_AUDIO_GAIN_EXPRESSION_LENGTH:
        raise ValueError('Audio gain expression exceeds the supported complexity limit.')
    boundary = 'aformat=sample_fmts=fltp:channel_layouts=stereo'
    return (boundary, f"aeval=exprs='{expression}':channel_layout=stereo", boundary)

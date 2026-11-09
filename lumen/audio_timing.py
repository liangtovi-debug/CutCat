"""Exact PCM budgets and pitch-preserving constant-speed audio compilation.

The time map selects source audio independently of the rounded-up output frame
count. All clip edges use global 48 kHz half-up sample boundaries. The fixed Rubber Band R2 short-window pipeline preserves pitch, with documented
startup padding and delay compensation at every bounded stage. Independent
instances at a split can still reshape transients; this is not lossless PCM or
a promise of phase continuity across edits.
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass
from functools import lru_cache
from fractions import Fraction
import math
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import TYPE_CHECKING

from .timing import atempo_factors, round_half_up, sample_boundary

if TYPE_CHECKING:
    from .model import Clip

SAMPLE_RATE = 48000
AUDIO_TIMING_ALGORITHM = 'rubberband-r2-short-mixed-together-48k-512pad-v1'
RUBBERBAND_START_PAD = 512
# Feed bounded *silence*, never extra source content, through the tempo filters
# so their processing windows can flush. Only the exact output budget is emitted.
TEMPO_FLUSH_SAMPLES = SAMPLE_RATE // 4


@dataclass(frozen=True)
class AudioTiming:
    """Filters before gain/output-time fades/timeline placement.

    A silent plan has no filters and must not add an input to the mix. For an
    audible plan the caller appends gain/fades, then an ``adelay`` of
    ``start_sample`` samples. ``sample_count`` is clip-local, not an end index.
    """

    filters: tuple[str, ...]
    start_sample: int
    sample_count: int
    leading_samples: int
    source_start_sample: int
    source_end_sample: int
    silent: bool


def _offset(value: int | float | Fraction) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float, Fraction)):
        raise ValueError('Audio offset must be a finite number.')
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('Audio offset must be a finite number.')
        # Probe timestamps are decimal seconds; avoid the binary float fraction.
        return Fraction(str(value))
    return Fraction(value)


def _tempo_decimal(value: Fraction) -> str:
    # The external filter accepts a double, rather than a rational expression. Conversion is
    # confined to this codec boundary; all endpoint/sample arithmetic stays exact.
    return format(float(value), '.17g')


def compile_audio_timing(clip: Clip, project_fps: Fraction,
                         media_offset: int | float | Fraction = 0) -> AudioTiming:
    """Compile selected source audio, retiming, late offset and exact PCM budget.

    Normalizing the stream to 48 kHz before sample-index selection makes both
    source edges explicit. No source sample beyond the exclusive endpoint is
    fed to the tempo processor. The silence flush does not extend that selected interval.
    The final rounded-up picture frame, an actual short audio stream and time-stretcher
    output shortages are padded with silence, never by repeating source audio.
    """
    if isinstance(project_fps, bool) or not isinstance(project_fps, (int, Fraction)) or project_fps <= 0:
        raise ValueError('Project rate must be a positive rational number.')
    fps = Fraction(project_fps)
    mapping = clip.timing
    factors = atempo_factors(mapping.speed)
    start = sample_boundary(clip.start, fps, SAMPLE_RATE)
    count = sample_boundary(clip.start + clip.duration, fps, SAMPLE_RATE) - start
    origin, endpoint = mapping.source_in / fps, mapping.source_out / fps
    offset = _offset(media_offset)
    lo, hi = max(Fraction(0), origin - offset), endpoint - offset
    source_start = round_half_up(lo * SAMPLE_RATE)
    source_end = max(0, round_half_up(max(Fraction(0), hi) * SAMPLE_RATE))
    late = max(Fraction(0), offset - origin) / mapping.speed
    # Quantize the actual global onset once. Independently rounding a duration
    # can differ by one sample from the boundary implied by the timeline start.
    leading = max(0, round_half_up((Fraction(clip.start, 1) / fps + late) * SAMPLE_RATE) - start)
    silent = hi <= lo or source_end <= source_start or leading >= count
    if silent:
        return AudioTiming((), start, count, min(leading, count), source_start, source_end, True)
    filters = ['asetpts=PTS-STARTPTS',
               'aresample=48000:async=1:first_pts=0',
               'aformat=sample_fmts=fltp:channel_layouts=stereo',
               f'atrim=start_sample={source_start}:end_sample={source_end}',
               'asetpts=PTS-STARTPTS']
    if factors:
        filters += rubberband_tempo_filters(mapping.speed)
    if leading:
        filters += [f'adelay={leading}S:all=1']
    filters += [f'apad=whole_len={count}', f'atrim=end_sample={count}',
                'asetpts=N/SR/TB']
    return AudioTiming(tuple(filters), start, count, leading, source_start, source_end, False)


def rubberband_tempo_filters(speed: Fraction) -> list[str]:
    """A fixed, explicit algorithm. Missing/incompatible support never falls back.

    FFmpeg exposes Rubber Band's R2 real-time engine. At 48 kHz, short windows
    and factors in [0.5,2], the documented R2 start pad/delay is 512 samples.
    The official notes call this value *typical*, not universal. Consequently
    ``ensure_retime_audio_capability`` requires a real regression probe of the
    exact executable before rendering. Native CI also exercises this graph.

    https://breakfastquay.com/rubberband/integration.html
    https://ffmpeg.org/doxygen/7.0/af__rubberband_8c_source.html
    """
    filters = []
    for factor in atempo_factors(speed):
        filters += [f'adelay={RUBBERBAND_START_PAD}S:all=1',
                    f'apad=pad_len={TEMPO_FLUSH_SAMPLES}',
                    f'rubberband=tempo={_tempo_decimal(factor)}:pitch=1:transients=mixed:window=short:channels=together',
                    f'atrim=start_sample={RUBBERBAND_START_PAD}', 'asetpts=N/SR/TB']
    return filters


def _probe_audio_compensation(binary: str, timeout: float, *, run_process=None) -> dict:
    """Real PCM pitch, transient alignment and endpoint gate; no network/media.

    This is a compatibility test for a fixed algorithm, not an empirical delay
    estimator. No measured offset is injected into the user's audio. A failure
    rejects this executable instead of choosing a different processing path.
    ``run_process`` optionally supplies a subprocess.run-compatible bounded,
    cancellable runner; default export behavior and numerical checks are unchanged.
    """
    import numpy as np

    began = time.monotonic()
    def run(arguments, data=None):
        remaining = timeout - (time.monotonic() - began)
        if remaining <= 0:
            raise ValueError('Rubber Band audio compatibility check timed out.')
        try:
            result = (run_process or subprocess.run)([binary, *arguments], input=data, capture_output=True,
                                    timeout=remaining, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f'Cannot verify required Rubber Band audio support: {exc}') from exc
        if result.returncode:
            reason = result.stderr.decode('utf-8', errors='replace')[-1500:]
            raise ValueError(f'Required Rubber Band audio compatibility check failed: {reason}')
        return result

    help_result = run(['-hide_banner', '-h', 'filter=rubberband'])
    help_text = (help_result.stdout + help_result.stderr).decode('utf-8', errors='replace')
    if not re.search(r'^Filter rubberband\s*$', help_text, re.MULTILINE):
        raise ValueError('Pitch-preserving speed requires FFmpeg with Rubber Band (librubberband). '
                         'This FFmpeg does not provide it; no lower-quality fallback is used.')
    source = array('f', [0]) * (SAMPLE_RATE * 2)
    for index in range(round(.8 * SAMPLE_RATE)):
        source[index] = .3 * math.sin(2 * math.pi * 440 * index / SAMPLE_RATE)
    centers = (Fraction(6, 5), Fraction(8, 5), Fraction(399, 200))
    for center in centers:
        first = round_half_up((center - Fraction(1, 200)) * SAMPLE_RATE)
        for index in range(480):
            source[first + index] = .5 * math.sin(2 * math.pi * 1000 * index / SAMPLE_RATE)
    if sys.byteorder != 'little':
        source.byteswap()
    raw = source.tobytes()
    measurements = []
    for speed in (Fraction(1, 4), Fraction(1, 2), Fraction(2, 3),
                  Fraction(3, 2), Fraction(2), Fraction(4)):
        count = round_half_up(Fraction(2 * SAMPLE_RATE) / speed)
        filters = ['aformat=sample_fmts=fltp:channel_layouts=stereo',
                   *rubberband_tempo_filters(speed), f'apad=whole_len={count}',
                   f'atrim=end_sample={count}']
        result = run(['-hide_banner', '-v', 'error', '-nostdin', '-f', 'f32le',
                      '-ar', str(SAMPLE_RATE), '-ac', '1', '-i', 'pipe:0',
                      '-af', ','.join(filters), '-ar', str(SAMPLE_RATE), '-ac', '1',
                      '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1'], raw)
        if len(result.stdout) != count * 4:
            raise ValueError(f'Rubber Band {float(speed):g}x failed the exact PCM sample-budget check.')
        samples = array('f')
        samples.frombytes(result.stdout)
        if sys.byteorder != 'little':
            samples.byteswap()
        body = samples[round_half_up(Fraction(SAMPLE_RATE, 5) / speed):
                       round_half_up(Fraction(2 * SAMPLE_RATE, 5) / speed)]
        waveform = np.asarray(body, dtype=np.float64)
        if len(waveform) < 3 or np.max(np.abs(waveform), initial=0) < .01:
            raise ValueError(f'Rubber Band {float(speed):g}x produced no measurable tone.')
        spectrum = np.abs(np.fft.rfft(waveform * np.hanning(len(waveform))))
        frequency = int(np.argmax(spectrum)) * SAMPLE_RATE / len(waveform)
        if abs(frequency-440) > 5:
            raise ValueError(f'Rubber Band {float(speed):g}x failed pitch preservation ({frequency:.2f} Hz).')
        errors = []
        for center in centers:
            expected = float(center / speed) * SAMPLE_RATE
            lo, hi = max(0, round(expected-.06*SAMPLE_RATE)), min(count, round(expected+.06*SAMPLE_RATE))
            energy = [float(value) ** 2 for value in samples[lo:hi]]
            total = sum(energy)
            if total < .05 or max(energy, default=0) < .000625:
                raise ValueError(f'Rubber Band {float(speed):g}x lost a selected audio transient or source tail.')
            error = (sum((lo+i)*value for i, value in enumerate(energy)) / total - expected) / SAMPLE_RATE
            if abs(error) > 1 / 30:
                raise ValueError(f'Rubber Band {float(speed):g}x is incompatible with the fixed start-delay '
                                 f'compensation ({error*1000:.1f} ms marker error).')
            errors.append(round(error * 1000, 3))
        measurements.append({'speed': str(speed), 'sample_count': count,
                             'measured_hz': round(frequency, 3), 'marker_errors_ms': errors})
    return {'algorithm': AUDIO_TIMING_ALGORITHM, 'sample_rate': SAMPLE_RATE,
            'max_marker_error_ms': 1000 / 30, 'measurements': measurements}


@lru_cache(maxsize=8)
def _binary_sha256(binary: str, identity: tuple[int, ...]) -> str:
    with open(binary, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


@lru_cache(maxsize=8)
def _cached_audio_capability(binary: str, digest: str, algorithm: str, timeout: float) -> dict:
    result = _probe_audio_compensation(binary, timeout)
    result['ffmpeg_sha256'] = digest
    return result


def ensure_retime_audio_capability(ffmpeg_binary: str, timeout: float = 30) -> dict:
    """Fail before video work if fixed Rubber Band support is incompatible.

    Cache only successful checks, keyed by the executable's resolved path,
    SHA-256 and algorithm identity. Binary hashing is cached by device/inode,
    byte size, modification and metadata-change timestamps.
    """
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Audio capability timeout must be positive and finite.')
    located = shutil.which(str(ffmpeg_binary))
    if not located:
        raise ValueError('Cannot find FFmpeg to verify required Rubber Band audio support.')
    try:
        path = Path(located).resolve(strict=True)
        stat = path.stat()
    except OSError as exc:
        raise ValueError(f'Cannot inspect FFmpeg for required Rubber Band audio support: {exc}') from exc
    identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    # The returned report is diagnostic, not user-editable algorithm settings.
    from copy import deepcopy
    try:
        digest = _binary_sha256(str(path), identity)
    except OSError as exc:
        raise ValueError(f'Cannot verify FFmpeg identity for audio support: {exc}') from exc
    return deepcopy(_cached_audio_capability(str(path), digest, AUDIO_TIMING_ALGORITHM, float(timeout)))

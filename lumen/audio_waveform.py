"""Bounded, session-only source audio envelopes for timeline display.

These are first-audio-stream, pre-gain/pre-fade max-absolute peaks, not rendered
PCM, loudness, or a preview of Rubber Band's transient processing. Channels are
never mixed together: opposite-phase stereo must remain visible. Extraction
only reads local media and never modifies the project or source.

All timeline selection uses the existing rational audio/sample plan. Peak bins
have finite resolution (initially 128 samples, adaptively coarsened for long
sources). At a trim edge, a partial base bin that could contain *unselected*
samples is omitted rather than showing out-of-range audio. This can omit a very
short edge transient; final picture-frame padding and known EOF remain silent.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import closing
from dataclasses import dataclass
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Callable, Hashable

import numpy as np

from .audio_timing import SAMPLE_RATE, compile_audio_timing
from .file_identity import is_plain_file
from .media import ffmpeg_binary, ffprobe_binary

if TYPE_CHECKING:
    from .model import Clip

BASE_BLOCK_SAMPLES = 128
MAX_BASE_BINS = 262_144
MAX_DISPLAY_PIXELS = 16_384
CACHE_MAX_BYTES = 32 * 1024**2
CACHE_MAX_ENTRIES = 16
_READ_BYTES = 64 * 1024


class WaveformError(ValueError):
    """An unavailable/unsafe source must leave the editor usable without peaks."""


class WaveformCancelled(WaveformError):
    """Cancelled extraction never publishes a partial envelope."""


@dataclass(frozen=True)
class FileIdentity:
    path: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


def identity(path: str | os.PathLike) -> FileIdentity:
    """Snapshot a regular local file, including replacement and metadata edits."""
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
        state = resolved.stat()
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        raise WaveformError('Cannot inspect waveform source.') from exc
    if not is_plain_file(state):
        raise WaveformError('Waveforms require a regular local media file.')
    return FileIdentity(str(resolved), state.st_dev, state.st_ino, state.st_size,
                        state.st_mtime_ns, state.st_ctime_ns)


@dataclass(frozen=True)
class Peaks:
    identity: FileIdentity | None
    levels: tuple[np.ndarray, ...]
    block_size: int
    sample_count: int
    sample_rate: int = SAMPLE_RATE

    @property
    def nbytes(self) -> int:
        return sum(level.nbytes for level in self.levels)


@dataclass(frozen=True)
class ExtractionLimits:
    timeout: float = 120.0
    max_pcm_bytes: int = 8 * 1024**3
    max_samples: int = SAMPLE_RATE * 4 * 60 * 60
    max_channels: int = 32
    max_bins: int = MAX_BASE_BINS
    block_size: int = BASE_BLOCK_SAMPLES
    probe_bytes: int = 64 * 1024

    def __post_init__(self):
        if (isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float))
                or not math.isfinite(self.timeout) or not 0 < self.timeout <= 120):
            raise ValueError('Waveform timeout must be positive and at most 120 seconds.')
        bounds = {'max_pcm_bytes': 8 * 1024**3, 'max_samples': SAMPLE_RATE * 4 * 60 * 60,
                  'max_channels': 32, 'max_bins': MAX_BASE_BINS, 'block_size': 65_536,
                  'probe_bytes': 64 * 1024}
        for name, maximum in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f'Invalid waveform extraction limit: {name}.')
        if self.max_bins < 2 or self.max_bins & (self.max_bins - 1):
            raise ValueError('Waveform bin limit must be a power of two, at least two.')
        if self.block_size & (self.block_size - 1):
            raise ValueError('Waveform block size must be a power of two.')


Cancel = Callable[[], bool] | threading.Event | None


class _Budget:
    def __init__(self, timeout: float, cancel: Cancel):
        self.deadline = time.monotonic() + timeout
        self.cancel = cancel

    def check(self):
        cancelled = (self.cancel() if callable(self.cancel)
                     else self.cancel.is_set() if self.cancel is not None else False)
        if cancelled:
            raise WaveformCancelled('Waveform extraction was cancelled.')
        if time.monotonic() >= self.deadline:
            raise WaveformError('Waveform extraction timed out.')


def _stream_process(command: list[str], budget: _Budget, max_bytes: int):
    """Drain bounded chunks on all desktop platforms, including stalled pipes.

    A bounded queue provides backpressure. The reader cannot retain an entire
    decode; the owner can still observe cancellation while stdout is blocked.
    No diagnostics or PCM are written to disk. Every path kills/reaps the child.
    """
    budget.check()
    process = None
    reader = None
    stopped = threading.Event()
    chunks: queue.Queue = queue.Queue(maxsize=2)

    def deliver(value):
        while not stopped.is_set():
            try:
                chunks.put(value, timeout=.05)
                return
            except queue.Full:
                pass

    def read():
        try:
            while not stopped.is_set():
                data = process.stdout.read(_READ_BYTES)
                if not data:
                    break
                deliver(data)
        except (OSError, ValueError) as exc:
            deliver(exc)
        finally:
            deliver(None)

    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   bufsize=0,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        reader = threading.Thread(target=read, name='CutCat waveform pipe', daemon=True)
        reader.start()
        count = 0
        while True:
            budget.check()
            try:
                chunk = chunks.get(timeout=.05)
            except queue.Empty:
                continue
            if chunk is None:
                break
            if isinstance(chunk, Exception):
                raise WaveformError('Cannot read waveform process output.') from chunk
            count += len(chunk)
            if count > max_bytes:
                raise WaveformError('Waveform process exceeded its output limit.')
            yield chunk
        # A child can close stdout before exiting. Do not turn that into an
        # unbounded wait or lose cancellation after the final byte.
        while process.poll() is None:
            budget.check()
            time.sleep(.01)
        budget.check()
        if process.returncode:
            raise WaveformError('The local audio decoder could not read this source.')
    except OSError as exc:
        raise WaveformError('Cannot run local FFmpeg tools for waveforms.') from exc
    finally:
        stopped.set()
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            if process.stdout is not None:
                process.stdout.close()
        if reader is not None:
            reader.join(timeout=1)


class _PeakBuilder:
    """Streaming max reduction with bounded adaptive base resolution."""
    def __init__(self, block_size=BASE_BLOCK_SAMPLES, max_bins=MAX_BASE_BINS):
        if (type(block_size) is not int or block_size < 1 or block_size & (block_size - 1)
                or type(max_bins) is not int or not 2 <= max_bins <= MAX_BASE_BINS
                or max_bins & (max_bins - 1)):
            raise ValueError('Peak storage needs bounded power-of-two block/bin sizes.')
        self.block_size = block_size
        self._values = np.empty(max_bins, dtype=np.float32)
        self._size = 0
        self._pending_count = 0
        self._pending_max = 0.0
        self.sample_count = 0

    def _compact(self):
        if self._size == len(self._values):
            half = self._size // 2
            self._values[:half] = np.maximum(self._values[0:self._size:2],
                                             self._values[1:self._size:2])
            self._size = half
            self.block_size *= 2

    def feed(self, samples: np.ndarray):
        values = np.asarray(samples, dtype=np.float32)
        if values.ndim == 2:
            if not 1 <= values.shape[1] <= 32:
                raise WaveformError('Unsupported waveform channel count.')
            values = np.max(np.abs(values), axis=1)
        elif values.ndim == 1:
            values = np.abs(values)
        else:
            raise ValueError('Audio samples must be frames or frames by channels.')
        values = np.nan_to_num(values, nan=0.0, posinf=1.0, neginf=1.0)
        np.clip(values, 0, 1, out=values)
        self.sample_count += len(values)
        offset = 0
        while offset < len(values):
            if self._pending_count:
                count = min(self.block_size - self._pending_count, len(values) - offset)
                self._pending_max = max(self._pending_max, float(values[offset:offset+count].max()))
                self._pending_count += count
                offset += count
                if self._pending_count == self.block_size:
                    self._values[self._size] = self._pending_max
                    self._size += 1
                    self._pending_count = 0
                    self._pending_max = 0.0
                    self._compact()
                continue
            blocks = min((len(values) - offset) // self.block_size,
                         len(self._values) - self._size)
            if blocks:
                count = blocks * self.block_size
                self._values[self._size:self._size+blocks] = (
                    values[offset:offset+count].reshape(blocks, self.block_size).max(axis=1))
                self._size += blocks
                offset += count
                self._compact()
            else:
                self._pending_count = len(values) - offset
                self._pending_max = float(values[offset:].max())
                break

    def finish(self, file_identity=None) -> Peaks:
        if self._pending_count:
            self._values[self._size] = self._pending_max
            self._size += 1
            self._pending_count = 0
            self._compact()
        levels = []
        level = self._values[:self._size]
        while True:
            # Bytes backing prevents a consumer from re-enabling writes and
            # corrupting another clip's cached peaks.
            level = np.frombuffer(level.tobytes(), dtype=np.float32)
            levels.append(level)
            if len(level) <= 1:
                break
            paired = np.maximum(level[0:len(level)-1:2], level[1::2])
            level = np.concatenate((paired, level[-1:])) if len(level) % 2 else paired
        return Peaks(file_identity, tuple(levels), self.block_size, self.sample_count)


def peaks_from_samples(samples, *, file_identity=None, block_size=BASE_BLOCK_SAMPLES,
                       max_bins=MAX_BASE_BINS) -> Peaks:
    """Build the same envelope from already-decoded PCM (also useful for tests)."""
    builder = _PeakBuilder(block_size, max_bins)
    values = np.asarray(samples)
    if values.ndim not in (1, 2):
        raise ValueError('Audio samples must be frames or frames by channels.')
    for start in range(0, len(values), 16_384):
        builder.feed(values[start:start+16_384])
    return builder.finish(file_identity)


def extract_peaks(path: str | os.PathLike, cancel: Cancel = None, *,
                  limits: ExtractionLimits | None = None) -> Peaks:
    """Decode the first audio stream under wall/output/memory/cancel budgets."""
    limits = limits or ExtractionLimits()
    budget = _Budget(limits.timeout, cancel)
    budget.check()
    before = identity(path)
    probe_command = [ffprobe_binary(), '-v', 'error', '-protocol_whitelist', 'file,pipe',
                     '-select_streams', 'a:0', '-show_entries', 'stream=channels',
                     '-of', 'json', before.path]
    with closing(_stream_process(probe_command, budget, limits.probe_bytes)) as output:
        raw_probe = b''.join(output)
    try:
        streams = json.loads(raw_probe).get('streams', [])
        channels = streams[0]['channels']
        if type(channels) is not int or not 1 <= channels <= limits.max_channels:
            raise ValueError('Unsupported channel count.')
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise WaveformError('No supported first audio stream for waveforms.') from exc
    builder = _PeakBuilder(limits.block_size, limits.max_bins)
    command = [ffmpeg_binary(), '-v', 'error', '-nostdin', '-threads', '1',
               '-filter_threads', '1', '-protocol_whitelist', 'file,pipe',
               '-i', before.path, '-map', '0:a:0', '-vn', '-sn', '-dn',
               '-af', 'asetpts=PTS-STARTPTS,aresample=48000:async=1:first_pts=0',
               '-ar', str(SAMPLE_RATE), '-ac', str(channels),
               '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1']
    remaining = b''
    frame_bytes = channels * 4
    with closing(_stream_process(command, budget, limits.max_pcm_bytes)) as output:
        for chunk in output:
            budget.check()
            data = remaining + chunk
            complete = len(data) // frame_bytes * frame_bytes
            remaining = data[complete:]
            if complete:
                frames = np.frombuffer(data[:complete], dtype='<f4').reshape(-1, channels)
                if builder.sample_count + len(frames) > limits.max_samples:
                    raise WaveformError('Waveform source exceeds its decoded duration limit.')
                builder.feed(frames)
    if remaining:
        raise WaveformError('The local decoder returned an incomplete audio sample.')
    budget.check()
    if identity(path) != before:
        raise WaveformError('Waveform source changed during extraction.')
    result = builder.finish(before)
    budget.check()
    return result


def _frame_fraction(value, name) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float, Fraction)):
        raise ValueError(f'{name} must be a finite frame position.')
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f'{name} must be a finite frame position.')
        return Fraction(str(value))
    return Fraction(value)


def _range_maxima(peaks: Peaks, left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """Vectorized dyadic range maxima, never scanning a whole source at paint."""
    result = np.zeros(len(left), dtype=np.float32)
    left, right = left.copy(), right.copy()
    for level in peaks.levels:
        active = left < right
        if not np.any(active):
            break
        take_left = active & ((left & 1) != 0)
        if np.any(take_left):
            result[take_left] = np.maximum(result[take_left], level[left[take_left]])
            left[take_left] += 1
        take_right = (left < right) & ((right & 1) != 0)
        if np.any(take_right):
            right[take_right] -= 1
            result[take_right] = np.maximum(result[take_right], level[right[take_right]])
        left //= 2
        right //= 2
    return result


def map_peaks(peaks: Peaks, clip: Clip, fps: Fraction, offset=0, local_start=0,
              local_end=None, pixels: int = 1) -> np.ndarray:
    """Map a clip-local project-frame window to independent display columns.

    Uses compile_audio_timing for source endpoints, signed stream offset,
    globally rounded leading silence and the exact output sample budget. The
    source envelope is simply remapped for speed, not pitch-shifted or mixed.
    Mute/gain/fades intentionally do not change these source peaks.
    """
    if type(pixels) is not int or not 0 <= pixels <= MAX_DISPLAY_PIXELS:
        raise ValueError('Waveform display width is out of bounds.')
    start = _frame_fraction(local_start, 'Waveform start')
    end = _frame_fraction(clip.duration if local_end is None else local_end, 'Waveform end')
    if end < start:
        raise ValueError('Waveform end must not precede its start.')
    result = np.zeros(pixels, dtype=np.float32)
    if not pixels or start == end or not peaks.sample_count:
        return result
    if peaks.sample_rate != SAMPLE_RATE:
        raise ValueError('Waveform sample rate must match the audio timing plan.')
    plan = compile_audio_timing(clip, fps, offset)
    source_start = plan.source_start_sample
    source_end = min(plan.source_end_sample, peaks.sample_count)
    if plan.silent or source_start >= source_end:
        return result
    speed = clip.timing.speed
    rate = Fraction(SAMPLE_RATE) / fps
    # Integer progression keeps all fractional-fps/subframe pixel boundaries
    # exact without constructing a Fraction for every display column.
    origin = (Fraction(clip.start) + start) * rate
    step = (end - start) * rate / pixels
    denominator = math.lcm(origin.denominator, step.denominator)
    numerator = origin.numerator * (denominator // origin.denominator)
    increment = step.numerator * (denominator // step.denominator)
    low_num, high_num = source_start * speed.denominator, source_end * speed.denominator

    def source_edges():
        for i in range(pixels + 1):
            sample = (2 * (numerator + i * increment) + denominator) // (2 * denominator)
            output = min(plan.sample_count, max(0, sample - plan.start_sample))
            mapped = low_num + (output - plan.leading_samples) * speed.numerator
            yield min(high_num, max(low_num, mapped))

    edges = np.fromiter(source_edges(), dtype=np.int64, count=pixels+1)
    width = peaks.block_size * speed.denominator
    left = edges[:-1] // width
    right = (edges[1:] + width - 1) // width
    # No base bucket that straddles the selected source interval is allowed
    # to contribute, since its maximum may belong to an excluded impulse.
    allowed_left = (source_start + peaks.block_size - 1) // peaks.block_size
    allowed_right = (source_end // peaks.block_size if source_end < peaks.sample_count
                     else len(peaks.levels[0]))
    left = np.maximum(left, allowed_left)
    right = np.minimum(right, allowed_right)
    right = np.where(edges[1:] > edges[:-1], right, left)
    return _range_maxima(peaks, left, right)


class PeakCache:
    """Memory- and entry-bounded session LRU; no persistent waveform artifacts."""
    def __init__(self, max_bytes=CACHE_MAX_BYTES, max_entries=CACHE_MAX_ENTRIES):
        if type(max_bytes) is not int or max_bytes < 0 or type(max_entries) is not int or max_entries < 0:
            raise ValueError('Waveform cache limits must be nonnegative integers.')
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self._items: OrderedDict[Hashable, Peaks] = OrderedDict()
        self._nbytes = 0

    @property
    def nbytes(self):
        return self._nbytes

    def __len__(self):
        return len(self._items)

    def keys(self) -> tuple:
        return tuple(self._items)

    def remove(self, key):
        previous = self._items.pop(key, None)
        if previous is not None:
            self._nbytes -= previous.nbytes

    def get(self, key):
        value = self._items.get(key)
        if value is not None:
            self._items.move_to_end(key)
        return value

    def put(self, key, peaks: Peaks) -> bool:
        if peaks.identity is not None and peaks.identity != key:
            return False
        if not self.max_entries or peaks.nbytes > self.max_bytes:
            return False
        previous = self._items.pop(key, None)
        if previous is not None:
            self._nbytes -= previous.nbytes
        self._items[key] = peaks
        self._nbytes += peaks.nbytes
        while self._nbytes > self.max_bytes or len(self._items) > self.max_entries:
            _, evicted = self._items.popitem(last=False)
            self._nbytes -= evicted.nbytes
        return True

    def clear(self):
        self._items.clear()
        self._nbytes = 0

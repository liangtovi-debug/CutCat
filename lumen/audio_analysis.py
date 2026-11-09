"""Bounded, read-only analysis of the renderer's pre-AAC stereo timeline mix.

Integrated loudness uses FFmpeg's BS.1770 / EBU R128 absolute and relative gate
(400 ms blocks), reported to 0.1 LU. Sample peaks and bounded four-times FIR estimated true peaks use unclipped
48 kHz float PCM, not encoded AAC peaks or a loudness-normalization pass.
Only scalar reductions and bounded pipe chunks are retained. All media is copied
and hashed into private, read-only snapshots, then the original is reverified.
Nothing is written to the project, media, Library, or a persistent cache.
"""
from __future__ import annotations
from .audio_components import clip_emits_source_audio, AUDIO_MIX_ORDER_ALGORITHM
from .audio_gain import AUDIO_GAIN_ALGORITHM_ID

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import queue
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from typing import Callable
import wave
from types import SimpleNamespace

import numpy as np

from .audio_true_peak import TruePeakEstimator
from .audio_mix import compile_audio_mix, ordered_clips
from .audio_timing import AUDIO_TIMING_ALGORITHM, SAMPLE_RATE, compile_audio_timing
from .file_identity import is_plain_file, path_matches_descriptor, same_file_state
from .media import ffmpeg_binary, ffprobe_binary

ANALYSIS_ALGORITHM = 'post-master-stereo-f32-ebur128-fir4-true-peak-v3'
# No playlist, concatenation, image sequence, external-reference, or raw demuxers.
CONTAINERS = 'wav,mp3,flac,ogg,mov,matroska,webm,aac,aiff,asf,caf,avi,mpeg,mpegts,nut,flv'
_READ_BYTES = 64 * 1024
_SNAPSHOT_LOCK = threading.Lock()
_WINDOWS = os.name == 'nt'
Cancel = Callable[[], bool] | threading.Event | None


class AudioAnalysisError(ValueError):
    """No result is published when source, decoder or measurement is unsafe."""


class AudioAnalysisCancelled(AudioAnalysisError):
    pass


@dataclass(frozen=True)
class AnalysisLimits:
    timeout: float = 600.0
    max_source_bytes: int = 8 * 1024**3
    max_total_source_bytes: int = 16 * 1024**3
    min_free_bytes: int = 128 * 1024**2
    max_duration_seconds: float = 2 * 60 * 60
    max_pcm_bytes: int = 2 * 60 * 60 * SAMPLE_RATE * 8
    max_log_bytes: int = 1024 * 1024
    max_probe_bytes: int = 256 * 1024
    max_sources: int = 256
    max_clips: int = 1024

    def __post_init__(self):
        maxima = {'timeout': 600.0, 'max_duration_seconds': 7200.0}
        for key, maximum in maxima.items():
            value = getattr(self, key)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 < value <= maximum):
                raise ValueError(f'Invalid audio analysis limit: {key}.')
        maxima = {'max_source_bytes': 8 * 1024**3, 'max_total_source_bytes': 16 * 1024**3,
                  'min_free_bytes': 1024**3, 'max_pcm_bytes': 7200 * SAMPLE_RATE * 8,
                  'max_log_bytes': 1024 * 1024, 'max_probe_bytes': 256 * 1024,
                  'max_sources': 256, 'max_clips': 1024}
        for key, maximum in maxima.items():
            if type(getattr(self, key)) is not int or not 1 <= getattr(self, key) <= maximum:
                raise ValueError(f'Invalid audio analysis limit: {key}.')


@dataclass(frozen=True)
class AudioAnalysisResult:
    integrated_lufs: float | None
    loudness_status: str
    sample_peaks: tuple[float, float]
    sample_peak_dbfs: tuple[float | None, float | None]
    overload_counts: tuple[int, int]
    overload_count: int
    first_overload_seconds: float | None
    sample_count: int
    sample_rate: int
    fingerprint: str
    context_fingerprint: str
    source_hashes: tuple[tuple[str, str], ...] = ()
    decoder_version: str = ''
    algorithm: str = ANALYSIS_ALGORITHM
    estimated_true_peaks: tuple[float, float] = (0.0, 0.0)
    estimated_true_peak_dbtp: tuple[float | None, float | None] = (None, None)


class _Budget:
    def __init__(self, limits, cancel):
        self.deadline = time.monotonic() + limits.timeout
        self.cancel = cancel

    def check(self):
        if self.cancel is not None and (self.cancel() if callable(self.cancel) else self.cancel.is_set()):
            raise AudioAnalysisCancelled('Audio analysis was cancelled.')
        if time.monotonic() >= self.deadline:
            raise AudioAnalysisError('Audio analysis timed out.')


def _local_path(value) -> Path:
    try:
        text = os.fspath(value)
        if not isinstance(text, str) or not text or any(ord(char) < 32 for char in text):
            raise ValueError
        drive = PureWindowsPath(text).drive if os.name == 'nt' else ''
        if text.startswith(('//', '\\\\')) or ':' in text[len(drive):] or (os.name != 'nt' and '\\' in text):
            raise ValueError
        path = Path(text).expanduser().absolute()
        # Do not resolve links and thereby hide an untrusted redirect.
        for item in (*reversed(path.parents), path):
            info = item.lstat()
            if item.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError
        if not is_plain_file(path.lstat()):
            raise ValueError
        return path
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        raise AudioAnalysisError('Audio analysis requires plain local media files, not links or network paths.') from exc


def _state(path) -> tuple:
    info = path.lstat()
    descriptor_change_time = None
    if _WINDOWS:
        # Python 3.12 path-stat ctime can be creation time on Windows while
        # fstat exposes metadata change time. Inspect the descriptor without
        # reading source bytes, so restoring size/mtime cannot revive a result.
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if not is_plain_file(opened) or not path_matches_descriptor(info, opened):
                raise AudioAnalysisError('An audio source changed while it was being read.')
            descriptor_change_time = opened.st_ctime_ns
        finally:
            os.close(fd)
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            getattr(info, 'st_birthtime_ns', None), descriptor_change_time)


def _settings(project):
    media = {item.id: item for item in project.media}
    clips = ordered_clips(project)
    active = {}
    rows = []
    for clip in clips:
        item = media[clip.media_id]
        rows.append((clip.id, clip.media_id, clip.track, clip.start, clip.duration,
                     clip.timing.to_dict(), float(clip.gain_db), clip.muted, clip.fade_in, clip.fade_out,
                     clip.source_audio_enabled, asdict(clip.audio_mix_anchor) if clip.audio_mix_anchor else None,
                     [asdict(key) for key in clip.audio_gain_keyframes]))
        if clip.source_audio_enabled and not clip.muted:
            active[item.id] = item
        # Generated provenance is verified even for muted generated clips, as
        # in export. Unmuted video-only files are re-probed too: a replacement
        # may now contain audio, and export would otherwise diverge.
        if item.speech_provenance is not None or item.sound_provenance is not None:
            active[item.id] = item
    entries = [(key, item.path, item.has_audio, float(item.audio_offset),
                asdict(item.speech_provenance) if item.speech_provenance else None,
                asdict(item.sound_provenance) if item.sound_provenance else None)
               for key, item in sorted(media.items()) if key in {clip.media_id for clip in clips}]
    return {'fps': [project.fps_num, project.fps_den], 'end': project.duration_frames,
            'master_gain_db': float(project.master_gain_db),
            'clips': rows, 'media': entries, 'timing_algorithm': AUDIO_TIMING_ALGORITHM,
            'analysis_algorithm': ANALYSIS_ALGORITHM,
            'mix_order_algorithm': AUDIO_MIX_ORDER_ALGORITHM,
            'audio_gain_algorithm': AUDIO_GAIN_ALGORITHM_ID}, active


def _hash_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def audio_settings_fingerprint(project) -> str:
    """Pure in-memory key for GUI edits. No paths, tools or filesystem calls.

    Source-content/identity freshness must still be checked off the GUI thread
    with ``audio_context_fingerprint`` or ``result_is_current``.
    """
    try:
        settings, _active = _settings(project)
        return _hash_json(settings)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise AudioAnalysisError('Cannot inspect the current audio analysis context.') from exc


def audio_context_fingerprint(project) -> str:
    """Worker-only metadata freshness key; never call it on the GUI thread.

    No source bytes are read, but path/descriptor metadata and decoder lookup
    may block on disconnected or slow storage. GUI edits use the pure settings
    fingerprint; workers own this stronger source-identity check.
    """
    try:
        settings, active = _settings(project)
        states = [(key, str(path := _local_path(item.path)), _state(path))
                  for key, item in sorted(active.items())]
        decoder = shutil.which(ffmpeg_binary())
        decoder_state = (str(Path(decoder).resolve(strict=True)), _state(Path(decoder).resolve(strict=True))) if decoder else None
        return _hash_json([settings, states, ffmpeg_binary(), decoder_state])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        if isinstance(exc, AudioAnalysisError):
            raise
        raise AudioAnalysisError('Cannot inspect the current audio analysis context.') from exc


def result_is_current(project, result: AudioAnalysisResult) -> bool:
    try:
        return audio_context_fingerprint(project) == result.context_fingerprint
    except AudioAnalysisError:
        return False


def _copy_hash(source, output, budget, max_bytes, *, disk_dir=None, reserve=0):
    budget.check()
    before = source.lstat()
    if not is_plain_file(before) or before.st_size > max_bytes:
        raise AudioAnalysisError('An audio source exceeds the safe file-size limit.')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(source, flags), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not is_plain_file(opened) or not path_matches_descriptor(before, opened):
            raise AudioAnalysisError('An audio source changed while it was being read.')
        count, digest = 0, hashlib.sha256()
        while True:
            budget.check()
            block = stream.read(1024 * 1024)
            if not block:
                break
            count += len(block)
            if count > max_bytes:
                raise AudioAnalysisError('An audio source exceeds the safe file-size limit.')
            digest.update(block)
            if output is not None:
                if disk_dir is not None and shutil.disk_usage(disk_dir).free < len(block) + reserve:
                    raise AudioAnalysisError('Not enough free disk space for audio source snapshots.')
                output.write(block)
        after_fd, after_path = os.fstat(stream.fileno()), source.lstat()
        if (count != before.st_size or not same_file_state(opened, after_fd)
                or not same_file_state(before, after_path) or not path_matches_descriptor(after_path, after_fd)):
            raise AudioAnalysisError('An audio source changed while it was being read.')
    budget.check()
    return digest.hexdigest()


def _terminate(process):
    if os.name != 'nt':
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        # Compatibility probes can own a decoder child. Reap that tree too.
        try:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=3, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.SubprocessError):
            process.kill()
    process.wait(timeout=3)


def _decoder_failure_note(command, returncode, diagnostics, totals):
    """Bounded evidence without media paths, graph contents or arbitrary stderr.

    Unknown lines are counted, never copied. Even option/filter names come from
    fixed literals, not captured text: stderr can echo private input metadata.
    """
    known_options = ('-version', '-show_entries', '-filter_complex_script',
                     '-/filter_complex', '-filter_complex', '-xerror',
                     '-format_whitelist', '-protocol_whitelist', '-err_detect')
    evidence = {
        "Unrecognized option 'filter_complex_script'.": 'unrecognized_filter_complex_script',
        "Unrecognized option '/filter_complex'.": 'unrecognized_file_filter_complex',
        'Error splitting the argument list: Option not found': 'option_not_found',
        "No such filter: 'ebur128'": 'missing_ebur128',
        "No such filter: ebur128": 'missing_ebur128',
        'Error initializing filters': 'filter_initialization_failed',
    }
    scanned = bytes(diagnostics[:16384]).decode('utf-8', errors='replace')
    recognized, redacted = set(), 0
    for line in scanned.splitlines():
        line = re.sub(r'^\[(?:error|fatal|warning|info)\]\s*', '', line).strip()
        code = evidence.get(line)
        if code:
            recognized.add(code)
        elif line:
            redacted += 1
    return 'Audio decoder diagnostic: ' + json.dumps({
        'returncode': int(returncode),
        'stdout_bytes': int(totals[0]), 'stderr_bytes': int(totals[1]),
        'recognized_options': [option for option in known_options if option in command],
        'stderr_evidence': sorted(recognized),
        'redacted_line_count': redacted,
        'stderr_scan_truncated': len(diagnostics) > 16384,
    }, sort_keys=True)


def _run(command, budget, stdout_limit, stderr_limit, consume=None, *, input_data=None):
    """Bound both pipes, total bytes, wall clock and queued memory on all OSes."""
    budget.check()
    process = None
    stopped = threading.Event()
    chunks = queue.Queue(maxsize=4)
    readers = []

    def deliver(value):
        while not stopped.is_set():
            try:
                chunks.put(value, timeout=.05)
                return
            except queue.Full:
                pass

    def read(index, pipe):
        try:
            while not stopped.is_set():
                block = pipe.read(_READ_BYTES)
                if not block:
                    break
                deliver((index, block))
        except (OSError, ValueError) as exc:
            deliver((index, exc))
        finally:
            deliver((index, None))

    output, diagnostics = bytearray(), bytearray()
    totals = [0, 0]
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, bufsize=0, start_new_session=os.name != 'nt',
                                   creationflags=(getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                                                  | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)))
        for index, pipe in enumerate((process.stdout, process.stderr)):
            thread = threading.Thread(target=read, args=(index, pipe), daemon=True,
                                      name='CutCat audio analysis pipe')
            thread.start()
            readers.append(thread)
        if input_data is not None:
            def write():
                try:
                    data = memoryview(input_data)
                    while data and not stopped.is_set():
                        written = process.stdin.write(data[:_READ_BYTES])
                        if not written:
                            raise OSError('Cannot feed compatibility probe.')
                        data = data[written:]
                    process.stdin.close()
                except (OSError, ValueError) as exc:
                    deliver((0, exc))
            writer = threading.Thread(target=write, daemon=True, name='CutCat audio probe input')
            writer.start()
            readers.append(writer)
        ended = 0
        while ended != 2:
            budget.check()
            try:
                index, block = chunks.get(timeout=.05)
            except queue.Empty:
                continue
            if block is None:
                ended += 1
                continue
            if isinstance(block, Exception):
                raise AudioAnalysisError('Cannot read audio decoder output.') from block
            totals[index] += len(block)
            if totals[index] > (stdout_limit if index == 0 else stderr_limit):
                raise AudioAnalysisError('Audio decoder exceeded its bounded output limit.')
            if index == 1:
                diagnostics.extend(block)
            elif consume is not None:
                consume(block)
            else:
                output.extend(block)
        while process.poll() is None:
            budget.check()
            time.sleep(.01)
        budget.check()
        if process.returncode:
            error = AudioAnalysisError('The local audio decoder or required analysis filter failed.')
            error.add_note(_decoder_failure_note(command, process.returncode, diagnostics, totals))
            raise error
        return bytes(output), bytes(diagnostics)
    except OSError as exc:
        raise AudioAnalysisError('Cannot run the local audio analysis tools.') from exc
    finally:
        stopped.set()
        if process is not None:
            _terminate(process)
            for pipe in (process.stdout, process.stderr, process.stdin):
                if pipe is not None:
                    pipe.close()
        for thread in readers:
            thread.join(timeout=1)


def _probe(path, item, budget, limits):
    raw, error = _run([ffprobe_binary(), '-v', 'error', '-protocol_whitelist', 'file',
                       '-format_whitelist', CONTAINERS, '-show_entries',
                       'stream=codec_type,channels,channel_layout,start_time,duration:stream_disposition=attached_pic:format=format_name,duration,start_time',
                       '-of', 'json', str(path)], budget, limits.max_probe_bytes, limits.max_log_bytes)
    try:
        if error.strip():
            raise ValueError
        data = json.loads(raw)
        streams = data['streams']
        audios = [stream for stream in streams if stream.get('codec_type') == 'audio']
        videos = [stream for stream in streams if stream.get('codec_type') == 'video'
                  and not stream.get('disposition', {}).get('attached_pic')]
        if bool(audios) != item.has_audio:
            raise ValueError
        if not audios:
            return
        first = audios[0]
        channels = first['channels']
        layout = first.get('channel_layout', '')
        # Missing layout is normal for a plain mono/stereo WAV. Other declared
        # 2-channel layouts are ambiguous and are not silently remapped.
        if channels not in (1, 2) or type(channels) is not int or layout not in ('', 'mono' if channels == 1 else 'stereo'):
            raise ValueError
        anchor = float(videos[0].get('start_time', first.get('start_time', 0))) if videos else float(first.get('start_time', 0))
        offset = float(first.get('start_time', anchor)) - anchor
        if not math.isfinite(offset) or abs(offset - item.audio_offset) > 1e-9:
            raise ValueError
        # Reproduce media._probe's source-duration rule without decoding any
        # picture frames. Valid short audio within a longer video remains
        # padded exactly as export; a replacement shorter *source* is stale.
        durations = [float(stream['duration']) + float(stream.get('start_time', anchor)) - anchor
                     for stream in videos[:1] + audios[:1] if stream.get('duration') not in (None, 'N/A')]
        fmt = data.get('format', {})
        duration = max(durations, default=float(fmt.get('duration', 0)) + float(fmt.get('start_time', anchor)) - anchor)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError
        provenance = item.sound_provenance or item.speech_provenance
        if provenance is None:
            if duration != item.duration:
                raise AudioAnalysisError('Audio source duration changed; re-import or relink it before analysis.')
        elif abs(duration - item.duration) > 1 / provenance.sample_rate:
            raise AudioAnalysisError('Audio source duration changed; re-import or relink it before analysis.')
    except AudioAnalysisError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        raise AudioAnalysisError('Audio analysis supports only unchanged first-stream mono/stereo media and timestamps.') from exc


def _verify_provenance(item, path, digest, budget):
    provenance = item.sound_provenance or item.speech_provenance
    if provenance is None:
        return
    if provenance.output_sha256.lower() != digest:
        raise AudioAnalysisError('Generated audio has changed since it was created or imported.')
    if item.sound_provenance is not None or provenance.sample_count is not None:
        # Preserve sound-library constraints and newer exact speech sample
        # provenance, reading a bounded PCM16 chunk rather than a whole WAV.
        try:
            if item.sound_provenance is not None and path.stat().st_size > 64 * 1024**2:
                raise ValueError
            with wave.open(str(path), 'rb') as audio:
                channels, count = audio.getnchannels(), audio.getnframes()
                if (channels not in (1, 2) or audio.getsampwidth() != 2 or audio.getcomptype() != 'NONE'
                        or audio.getframerate() != provenance.sample_rate or count != provenance.sample_count):
                    raise ValueError
                if item.sound_provenance is not None and (audio.getframerate() != 48000 or not 0 < count <= 48000*60):
                    raise ValueError
                remaining = count
                while remaining:
                    budget.check()
                    frames = audio.readframes(min(remaining, 16384))
                    if not frames or len(frames) % (channels * 2):
                        raise ValueError
                    remaining -= len(frames) // (channels * 2)
                    if (item.sound_provenance is not None
                            and np.abs(np.frombuffer(frames, dtype='<i2').astype(np.int32)).max() / 32768 > .501):
                        raise ValueError
                if audio.readframes(1):
                    raise ValueError
        except (OSError, EOFError, wave.Error, ValueError) as exc:
            raise AudioAnalysisError('Generated audio metadata or safe peak does not match its WAV samples.') from exc


class _PCMReduction:
    def __init__(self, expected_samples):
        self.expected_samples = expected_samples
        self.sample_count = 0
        self.peaks = np.zeros(2, dtype=np.float64)
        self.counts = np.zeros(2, dtype=np.int64)
        self.first = None
        self.pending = b''
        self.true_peak = TruePeakEstimator()
        self.estimated_true_peaks = None

    def feed(self, chunk):
        data = self.pending + chunk
        complete = len(data) // 8 * 8
        self.pending = data[complete:]
        if not complete:
            return
        pcm = np.frombuffer(data[:complete], dtype='<f4').reshape(-1, 2)
        if not np.isfinite(pcm).all():
            raise AudioAnalysisError('The final audio mix contains non-finite samples.')
        if self.sample_count + len(pcm) > self.expected_samples:
            raise AudioAnalysisError('The final audio mix has an unexpected sample count.')
        self.true_peak.feed(pcm)
        absolute = np.abs(pcm)
        self.peaks = np.maximum(self.peaks, absolute.max(axis=0))
        above = absolute > 1.0
        self.counts += above.sum(axis=0)
        if self.first is None and above.any():
            self.first = self.sample_count + int(np.flatnonzero(above.any(axis=1))[0])
        self.sample_count += len(pcm)

    def finish(self):
        if self.pending or self.sample_count != self.expected_samples:
            raise AudioAnalysisError('The final audio mix is truncated or has an unexpected sample count.')
        self.estimated_true_peaks = self.true_peak.finish()


def _loudness(log, reduction):
    text = log.decode('utf-8', errors='replace')
    if re.search(r'\[(?:error|fatal|panic)\]', text, re.IGNORECASE):
        raise AudioAnalysisError('The final audio mix could not be decoded without errors.')
    # Require the terminal ebur128 report, never a progress-line placeholder.
    reports = re.findall(r'Summary:\s+Integrated loudness:\s+I:\s*([-+\d.]+) LUFS\s+Threshold:\s*([-+\d.]+) LUFS', text)
    if not reports:
        raise AudioAnalysisError('The required EBU R128 loudness report is missing.')
    measured, threshold = map(float, reports[-1])
    if not math.isfinite(measured) or not math.isfinite(threshold):
        raise AudioAnalysisError('The loudness measurement is non-finite.')
    if not reduction.peaks.any():
        return None, 'silence'
    if reduction.sample_count < SAMPLE_RATE * 2 // 5:
        return None, 'too_short'
    # FFmpeg emits I=-70 / threshold=0 only when no 400 ms block
    # survives the absolute gate. A real level just above -70 can round to
    # -70.0, but has a negative relative-gate threshold: retain that result.
    if measured == -70.0 and threshold == 0.0:
        return None, 'below_gate'
    if measured < -70.0 or threshold > measured:
        raise AudioAnalysisError('The loudness gate report is inconsistent.')
    return measured, 'measured'


def _decoder_identity(binary, budget, limits):
    located = shutil.which(binary)
    if not located:
        raise AudioAnalysisError('Cannot find the local FFmpeg audio decoder.')
    path = Path(located).resolve(strict=True)
    digest = _copy_hash(path, None, budget, 512 * 1024**2)
    output, error = _run([binary, '-version'], budget, limits.max_probe_bytes, limits.max_log_bytes)
    if not output.startswith(b'ffmpeg version ') or error.strip():
        raise AudioAnalysisError('Cannot verify the local audio decoder version.')
    version = output.decode('utf-8', errors='replace').splitlines()[0]
    return digest, version


def _check_retime(binary, budget, limits):
    # Reuse the exact existing numerical authority, but inject bounded,
    # cancellable pipes. Never launch sys.executable: in a packaged desktop
    # application that is the GUI executable, not a Python interpreter.
    from .audio_timing import _probe_audio_compensation
    remaining = max(.01, min(30, budget.deadline - time.monotonic()))
    def run_process(command, *, input=None, **_kwargs):
        probe_budget = _Budget(limits, budget.cancel)
        probe_budget.deadline = min(budget.deadline, time.monotonic() + _kwargs.get('timeout', remaining))
        output, error = _run(command, probe_budget, 4 * 1024**2, limits.max_log_bytes, input_data=input)
        return SimpleNamespace(stdout=output, stderr=error, returncode=0)
    _probe_audio_compensation(binary, remaining, run_process=run_process)


def analyze_audio(project, cancel: Cancel = None, *, limits: AnalysisLimits | None = None,
                  progress: Callable[[str], None] | None = None) -> AudioAnalysisResult:
    """Measure one immutable final mix, or fail without publishing any result.

    Progress reports ``snapshot``, ``decode`` and ``verify``. Supported inputs
    are plain local files, safe container demuxers, and an unambiguous first
    mono/stereo audio stream. Missing filters, stale media and malformed PCM
    fail closed. Cancellation includes copy, probe, decode and final rehash.
    """
    limits = limits or AnalysisLimits()
    budget = _Budget(limits, cancel)
    try:
        budget.check()
        owned = deepcopy(project)
        owned.validate()
        if len(owned.clips) > limits.max_clips:
            raise AudioAnalysisError('The timeline exceeds the safe audio analysis clip limit.')
        duration = owned.duration_frames / owned.fps
        if not 0 < duration <= limits.max_duration_seconds:
            raise AudioAnalysisError('The timeline is empty or exceeds the safe audio analysis duration limit.')
        from .timing import sample_boundary
        expected_samples = sample_boundary(owned.duration_frames, owned.fps, SAMPLE_RATE)
        if expected_samples * 8 > limits.max_pcm_bytes:
            raise AudioAnalysisError('The final audio mix exceeds its PCM size limit.')
        context = audio_context_fingerprint(owned)
        settings, active = _settings(owned)
        if len(active) > limits.max_sources:
            raise AudioAnalysisError('The timeline exceeds the safe audio analysis source limit.')
        binary = ffmpeg_binary()
        decoder_digest, decoder_version = _decoder_identity(binary, budget, limits)
        checked = {item.id: item for item in owned.media}
        original = {}
        hashes = {}
        if progress:
            progress('snapshot')
        with tempfile.TemporaryDirectory(prefix='cutcat-audio-analysis-') as directory:
            root = Path(directory)
            # Reserve/check concurrent copying serially; no cache survives this run.
            while not _SNAPSHOT_LOCK.acquire(timeout=.05):
                budget.check()
            try:
                paths = {key: _local_path(item.path) for key, item in active.items()}
                unique = dict.fromkeys(paths.values())
                planned_sizes = {path: path.lstat().st_size for path in unique}
                if any(size > limits.max_source_bytes for size in planned_sizes.values()):
                    raise AudioAnalysisError('An audio source exceeds the safe file-size limit.')
                total = sum(planned_sizes.values())
                if total > limits.max_total_source_bytes:
                    raise AudioAnalysisError('Audio source snapshots exceed their total size limit.')
                if shutil.disk_usage(root).free < total + limits.min_free_bytes:
                    raise AudioAnalysisError('Not enough free disk space for audio source snapshots.')
                snapshots = {}
                for index, source in enumerate(unique):
                    budget.check()
                    target = root / f'source-{index}.bin'
                    with target.open('xb') as output:
                        digest = _copy_hash(source, output, budget, planned_sizes[source],
                                            disk_dir=root, reserve=limits.min_free_bytes)
                    target.chmod(0o444)
                    snapshots[source] = (target, digest)
                for key, item in active.items():
                    source = paths[key]
                    target, digest = snapshots[source]
                    original[source] = digest
                    hashes[key] = digest
                    _verify_provenance(item, target, digest, budget)
                    _probe(target, item, budget, limits)
                    checked[key] = replace(item, path=str(target))
            finally:
                _SNAPSHOT_LOCK.release()
            plan = compile_audio_mix(owned, checked)
            if plan.end_sample * 8 > limits.max_pcm_bytes:
                raise AudioAnalysisError('The final audio mix exceeds its PCM size limit.')
            if any(clip.speed != 1 and clip_emits_source_audio(clip, checked[clip.media_id])
                   and not compile_audio_timing(clip, owned.fps, checked[clip.media_id].audio_offset).silent
                   for clip in owned.clips):
                _check_retime(binary, budget, limits)
            reduction = _PCMReduction(plan.end_sample)
            # A real null muxer output avoids an FFmpeg 7.1 EOF scheduler
            # assertion triggered by anullsink after some retimed selections.
            # It writes no media; the independent PCM branch is byte-identical
            # to the renderer's pre-encoder stream.
            graph = (plan.graph + ';[aout]asplit=2[pcm][meter];'
                     '[meter]ebur128=framelog=verbose[measured]')
            graph_path = root / 'mix.ffgraph'
            graph_path.write_text(graph, encoding='utf-8')
            # Restrict *every* input, not merely the first. Mov external data
            # references are disabled by the decoder default; no playlists are
            # admitted. Float output deliberately preserves values beyond 1.
            inputs = []
            for argument in plan.inputs:
                if argument == '-i':
                    inputs += ['-format_whitelist', CONTAINERS, '-err_detect', 'explode']
                inputs.append(argument)
            # FFmpeg 7+ loads an option value from a file with -/. Keep the
            # graph off argv and pass its path as one unescaped argument; the
            # legacy -filter_complex_script spelling is removed in FFmpeg 9.
            command = [binary, '-hide_banner', '-nostdin', '-nostats', '-y', '-loglevel', 'level+info',
                       '-xerror', '-threads', '1', '-filter_complex_threads', '1',
                       *inputs, '-/filter_complex', str(graph_path), '-map', '[pcm]',
                       '-vn', '-sn', '-dn', '-ar', str(SAMPLE_RATE), '-ac', '2',
                       '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1',
                       '-map', '[measured]', '-vn', '-sn', '-dn', '-f', 'null', os.devnull]
            if progress:
                progress('decode')
            _, log = _run(command, budget, limits.max_pcm_bytes, limits.max_log_bytes, reduction.feed)
            reduction.finish()
            integrated, status = _loudness(log, reduction)
            if progress:
                progress('verify')
            for source, expected in original.items():
                if _copy_hash(source, None, budget, planned_sizes[source]) != expected:
                    raise AudioAnalysisError('An audio source changed during analysis.')
            if audio_context_fingerprint(owned) != context or audio_context_fingerprint(project) != context:
                raise AudioAnalysisError('The audio timeline or source changed during analysis.')
            budget.check()
            peak_values = tuple(float(value) for value in reduction.peaks)
            counts = tuple(int(value) for value in reduction.counts)
            fingerprint = _hash_json([settings, sorted(hashes.items()), decoder_digest, decoder_version])
            return AudioAnalysisResult(integrated, status, peak_values,
                                       tuple(20 * math.log10(value) if value else None for value in peak_values),
                                       counts, sum(counts),
                                       reduction.first / SAMPLE_RATE if reduction.first is not None else None,
                                       reduction.sample_count, SAMPLE_RATE, fingerprint, context,
                                       tuple(sorted(hashes.items())), decoder_version,
                                       estimated_true_peaks=reduction.estimated_true_peaks,
                                       estimated_true_peak_dbtp=tuple(20 * math.log10(value) if value else None
                                                                     for value in reduction.estimated_true_peaks))
    except AudioAnalysisError:
        raise
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        raise AudioAnalysisError(f'Cannot safely analyze the final audio mix: {exc}') from exc

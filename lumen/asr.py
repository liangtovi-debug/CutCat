"""Offline whisper.cpp CLI adapter. No downloads or network clients are used.

Runtime/model paths are explicit. Media is decoded into a disposable PCM WAV;
all generated files live in a private temporary directory, never beside media.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
from typing import Callable

from .media import ffmpeg_binary, ffprobe_binary


class ASRError(ValueError):
    """Invalid configuration, input, runtime failure, or malformed output."""


class ASRCancelled(ASRError):
    """The user cancelled transcription."""


@dataclass(frozen=True)
class ASRConfig:
    runtime_path: str | Path
    model_path: str | Path
    language: str = 'auto'
    threads: int = 4


@dataclass(frozen=True)
class TranscriptSegment:
    start_seconds: float | Fraction
    end_seconds: float | Fraction
    text: str
    language: str = 'und'


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise ASRCancelled('Transcription cancelled.')


def _file(value, label):
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, ValueError, TypeError) as exc:
        raise ASRError(f'Select an existing local {label} file.') from exc
    if not path.is_file():
        raise ASRError(f'Select an existing local {label} file.')
    return path


def _runtime_environment(threads):
    # whisper.cpp discovers optional backends from GGML_BACKEND_PATH and its
    # current directory. Use explicit CLI arguments and a private working
    # directory instead of inherited application/model-download settings.
    # This does not sandbox an executable or libraries in its trusted directory.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(('GGML_', 'WHISPER_', 'LLAMA_', 'HF_', 'HUGGING_FACE_'))}
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               OMP_NUM_THREADS=str(threads), OPENBLAS_NUM_THREADS=str(threads),
               MKL_NUM_THREADS=str(threads))
    return env


def _run(command, cancel, label, output_path=None, *, cwd=None, env=None):
    # File-backed diagnostics avoid both unbounded RAM and pipe deadlocks.
    with tempfile.TemporaryFile() as log, (open(output_path, "wb") if output_path else tempfile.TemporaryFile()) as output:
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=output if output_path else log, stderr=log, shell=False,
                                       cwd=cwd, env=env)
        except OSError as exc:
            raise ASRError(f'Cannot start {label}: {exc}') from exc
        try:
            while True:
                _cancelled(cancel)
                try:
                    code = process.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
            _cancelled(cancel)
            if code:
                log.seek(0, os.SEEK_END)
                log.seek(max(0, log.tell() - 8192))
                detail = log.read().decode('utf-8', errors='replace').strip()
                raise ASRError(f'{label} failed (exit {code}): {detail}')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def _read_json(path):
    # A corrupt or incompatible runtime must not allocate unbounded JSON memory.
    with Path(path).open('rb') as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ASRError('Speech recognition JSON exceeds the 16 MiB safety limit.')
    return json.loads(raw.decode('utf-8'))


def _audio_offset(source, directory, cancel, exact=False):
    output = Path(directory) / 'probe.json'
    _run([ffprobe_binary(), '-v', 'error', '-protocol_whitelist', 'file,pipe',
          '-show_entries', 'stream=codec_type,start_time:stream_disposition=attached_pic',
          '-of', 'json', str(source)], cancel, 'Audio timing inspection', output)
    try:
        streams = _read_json(output)['streams']
        audio = next(s for s in streams if s.get('codec_type') == 'audio')
        video = next((s for s in streams if s.get('codec_type') == 'video'
                      and not s.get('disposition', {}).get('attached_pic')), None)
        anchor = video if video is not None else audio
        if exact:
            # Subtract probe's decimal timestamps before any binary-float
            # conversion; an ulp at a sample boundary must not trim one extra
            # sample from a selected source window.
            return (Fraction(str(audio.get('start_time', 0))) -
                    Fraction(str(anchor.get('start_time', audio.get('start_time', 0)))))
        offset = float(audio.get('start_time', 0)) - float(
            anchor.get('start_time', audio.get('start_time', 0)))
        if not math.isfinite(offset):
            raise ValueError('nonfinite stream timestamp')
        return offset
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError, StopIteration) as exc:
        raise ASRError('Cannot determine source audio timing; ensure it contains audio.') from exc


def _parse_transcript(data):
    """Parse whisper.cpp v1.8.2 -oj JSON: offsets are MILLISECONDS.

    This is distinct from internal whisper token timestamps (10ms ticks).
    Missing offsets fail closed rather than guessing timestamps or units.
    """
    try:
        if not isinstance(data, dict) or not isinstance(data['transcription'], list):
            raise ValueError('expected a transcription list')
        if len(data['transcription']) > 100000:
            raise ValueError('too many segments (maximum 100000)')
        language = data.get('result', {}).get('language', 'und')
        if not isinstance(language, str) or not language:
            raise ValueError('invalid language')
        segments = []
        previous_start = -1.0
        for item in data['transcription']:
            offsets = item['offsets']
            start, end = offsets['from'], offsets['to']
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (start, end)):
                raise ValueError('non-numeric offsets')
            start, end = start / 1000.0, end / 1000.0
            if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end):
                raise ValueError('invalid time range')
            if start < previous_start:
                raise ValueError('out-of-order segments')
            previous_start = start
            text = item['text']
            if not isinstance(text, str):
                raise ValueError('invalid text')
            if text.strip():
                segments.append(TranscriptSegment(start, end, text.strip(), language))
        return segments
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        raise ASRError(f'Invalid whisper.cpp JSON output: {exc}') from exc


def _source_window(source_start, source_end):
    """Keep requested source bounds rational until the PCM sampling boundary."""
    if source_start is None and source_end is None:
        return None
    try:
        if any(type(value) is not int and not isinstance(value, Fraction)
               for value in (source_start, source_end)):
            raise ValueError
        left, right = Fraction(source_start), Fraction(source_end)
        if left < 0 or right <= left:
            raise ValueError
        return left, right
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
        raise ASRError('Transcription source bounds must be exact nonnegative times with end after start.') from exc


def _transcribe_impl(media_path: str | Path, config: ASRConfig,
               progress: Callable[[float], None] | None = None,
               cancel: threading.Event | None = None, *,
               source_start=None, source_end=None) -> list[TranscriptSegment]:
    """Transcribe locally with original-language output and source-relative times.

    Progress is monotonic stage progress (0, .15, .95, 1), not an ETA. Cancellation
    kills and reaps the current process within its 100ms polling interval. Runtime
    is executable code: select only a trusted whisper.cpp CLI binary.
    """
    _cancelled(cancel)
    window = _source_window(source_start, source_end)
    source = _file(media_path, 'media')
    runtime = _file(config.runtime_path, 'whisper.cpp runtime')
    model = _file(config.model_path, 'Whisper model')
    if os.name != 'nt' and not os.access(runtime, os.X_OK):
        raise ASRError('The selected whisper.cpp runtime is not executable.')
    if isinstance(config.threads, bool) or not isinstance(config.threads, int) or not 1 <= config.threads <= 256:
        raise ASRError('Threads must be an integer between 1 and 256.')
    if not isinstance(config.language, str) or not re.fullmatch(r'auto|[a-z]{2,3}', config.language):
        raise ASRError('Language must be auto or a lowercase Whisper language code (en, zh, etc.).')
    def report(value):
        _cancelled(cancel)
        if progress is not None:
            progress(float(value))
    report(0)
    try:
        with tempfile.TemporaryDirectory(prefix='lumen-asr-') as directory:
            wav = Path(directory) / 'audio.wav'
            output = Path(directory) / 'transcript'
            offset = (_audio_offset(source, directory, cancel, True) if window is not None else
                      _audio_offset(source, directory, cancel))
            filters = ['asetpts=PTS-STARTPTS']
            transcript_origin = Fraction(0)
            if window is not None:
                # Decode the selected source interval at original speed. Audio
                # samples have a discrete 16 kHz boundary: exclude every sample
                # before O or at/after E, then retain the exact sample origin
                # when translating Whisper's relative cue timestamps back.
                left, right = window
                audio_origin = Fraction(str(offset))
                first = max(0, math.ceil((left - audio_origin) * 16000))
                last = max(0, math.ceil((right - audio_origin) * 16000))
                if last <= first:
                    report(1)
                    return []
                transcript_origin = audio_origin + Fraction(first, 16000)
                filters += [f'atrim=start_sample={first}:end_sample={last}',
                            'asetpts=PTS-STARTPTS']
            else:
                # Keep the established whole-media origin and 1x extraction.
                if offset > 0:
                    filters += [f'adelay={round(offset * 16000)}S:all=1']
                elif offset < 0:
                    filters += [f'atrim=start={-offset:.12f}', 'asetpts=PTS-STARTPTS']
            _run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-n',
                  '-protocol_whitelist', 'file,pipe', '-i', str(source),
                  '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000',
                  '-af', 'aresample=16000,' + ','.join(filters),
                  '-c:a', 'pcm_s16le', '-f', 'wav', str(wav)], cancel, 'Audio extraction')
            report(.15)
            _run([str(runtime), '-m', str(model), '-f', str(wav),
                  '-l', config.language, '-t', str(config.threads),
                  '-oj', '-of', str(output)], cancel, 'Local speech recognition',
                 cwd=directory, env=_runtime_environment(config.threads))
            report(.95)
            result = _parse_transcript(_read_json(output.with_suffix('.json')))
            if window is not None:
                result = [TranscriptSegment(transcript_origin + Fraction(str(cue.start_seconds)),
                                            transcript_origin + Fraction(str(cue.end_seconds)),
                                            cue.text, cue.language) for cue in result]
            report(1)
            return result
    except ASRError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise ASRError(f'Cannot transcribe media: {exc}') from exc


def transcribe(media_path: str | Path, config: ASRConfig,
               progress: Callable[[float], None] | None = None,
               cancel: threading.Event | None = None, *,
               source_start=None, source_end=None) -> list[TranscriptSegment]:
    """Run local ASR; optional bounds are exact int/Fraction source seconds.

    Selected windows are extracted at original speed. Returned timestamps keep
    the source's video-relative origin for one later inverse playback mapping.
    """
    from .local_ai import inference_slot, LocalAIError, InferenceCancelled
    _cancelled(cancel)
    if type(config.threads) is not int or not 1 <= config.threads <= 4:
        raise ASRError('Threads must be an integer between1 and4 (shared local AI resource limit).')
    try:
        with inference_slot('asr', cancel=cancel):
            return _transcribe_impl(media_path, config, progress, cancel,
                                    source_start=source_start, source_end=source_end)
    except InferenceCancelled as exc:
        raise ASRCancelled(str(exc)) from exc
    except LocalAIError as exc:
        raise ASRError(str(exc)) from exc

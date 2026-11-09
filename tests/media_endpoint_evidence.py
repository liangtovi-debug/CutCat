"""Bounded, test-only evidence for explicitly named synthetic media probes.

Observe original calls once. Never retry, alter returned media, serialize command
arguments/raw stderr, or read sources outside the fixture allowlist.
Content hashing adds bounded I/O; stderr classes are text matches, not root causes.
"""
from contextlib import contextmanager
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time

LIMIT = 1024 * 1024
NUMERIC = re.compile(r'[+-]?\d{1,20}(?:[./][+-]?\d{1,20})?\Z', re.ASCII)
STDERR_CLASSES = {
    'resource_unavailable': b'resource temporarily unavailable',
    'memory': b'cannot allocate memory', 'permission': b'permission denied',
    'missing_file': b'no such file', 'invalid_data': b'invalid data',
    'decoder_error': b'error while decoding', 'thread_mention': b'thread',
}


def numeric(value):
    if type(value) in (int, float):
        try:
            return value if math.isfinite(value) else None
        except OverflowError:
            return None
    if isinstance(value, str) and NUMERIC.fullmatch(value):
        return value
    return None


def identity(path):
    try:
        with path.open('rb') as handle:
            data = handle.read(LIMIT + 1)
        if len(data) > LIMIT:
            return {'status': 'too_large'}
        return dict(status='ok', bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    except OSError as exc:
        return dict(status=type(exc).__name__)


def payload(data):
    if isinstance(data, str):
        data = data.encode('utf-8')
    if len(data) > LIMIT:
        return None
    try:
        value = json.loads(data)
        return value if isinstance(value, dict) else None
    except (ValueError, UnicodeError):
        return None


def rejection(row, stream, frames):
    """Classify the existing guard outcome, never decide the returned endpoint."""
    if row.get('endpoint') is not None:
        return 'accepted'
    metadata = next((p for p in row['processes'] if p['kind'] == 'metadata'), None)
    if metadata:
        if 'exception' in metadata: return 'metadata_' + metadata['exception']
        if metadata.get('returncode'): return 'metadata_nonzero_exit'
        if not metadata.get('json_valid'): return 'metadata_invalid_json'
    tail = next((p for p in row['processes'] if p['kind'] == 'tail'), None)
    if tail:
        if 'exception' in tail: return 'tail_' + tail['exception']
        if 'diagnostic_error' in tail: return 'diagnostic_capture_error'
        if tail['returncode']: return 'tail_nonzero_exit'
        if tail['stderr_bytes']: return 'tail_stderr'
        if tail.get('output_position', 0) > LIMIT: return 'tail_output_limit'
        if not tail.get('json_valid'): return 'tail_invalid_json'
        if not frames: return 'tail_empty_frames'
    try:
        base = Fraction(stream['time_base'])
        if base <= 0: return 'nonpositive_time_base'
        if tail is None: return 'tail_not_started'
        timestamps = [int(f['best_effort_timestamp']) for f in frames]
        if any(b <= a for a, b in zip(timestamps, timestamps[1:])):
            return 'nonincreasing_timestamps'
        ticks = int(frames[-1].get('duration', frames[-1].get('pkt_duration', 0)))
        if ticks <= 0: return 'nonpositive_frame_duration'
        nominal = 1 / Fraction(stream['avg_frame_rate'])
        if (nominal <= 0 or abs(ticks * base - nominal) > base
                or any(abs((b-a) * base - nominal) > base for a,b in zip(timestamps,timestamps[1:]))):
            return 'non_cfr_tail'
        origin = (Fraction(stream['start_pts']) * base if 'start_pts' in stream
                  else Fraction(str(float(stream.get('start_time', 0)))))
        end = (timestamps[-1] + ticks) * base - origin
        if end <= 0: return 'nonpositive_endpoint'
        if end > Fraction(str(row['duration'])) + 1: return 'endpoint_exceeds_metadata_bound'
        return 'unclassified_guard_rejection'
    except (ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError):
        return 'invalid_or_missing_timing'


@contextmanager
def trace_synthetic_probes(monkeypatch, sources, output):
    import lumen.media as media
    allowed = {Path(path).resolve(): alias for alias, path in sources.items()}
    original = media._probe
    rows = []
    counts = dict(unrecorded_sources=0, dropped_records=0)

    def probe(path, *args, **kwargs):
        resolved = Path(path).resolve()
        if resolved not in allowed or len(rows) >= 32:
            counts['unrecorded_sources' if resolved not in allowed else 'dropped_records'] += 1
            return original(path, *args, **kwargs)
        row = dict(call=len(rows)+1, source=allowed[resolved], before=identity(resolved), processes=[])
        rows.append(row)
        stream, frames = {}, None
        runner = kwargs.get('run_process') or subprocess.run
        def run(command, **options):
            nonlocal stream, frames
            tail = '-show_frames' in command
            process = dict(kind='tail' if tail else 'metadata', timeout_seconds=options.get('timeout'))
            row['processes'].append(process)
            began = time.perf_counter()
            try:
                result = runner(command, **options)
            except Exception as exc:
                process['exception'] = type(exc).__name__
                raise
            finally:
                process['seconds'] = time.perf_counter() - began
            try:
                stderr = result.stderr or b''
                if isinstance(stderr, str): stderr = stderr.encode('utf-8')
                sample = stderr[:8192].lower()
                process.update(returncode=result.returncode, stderr_bytes=len(stderr),
                               stderr_classes=[key for key, term in STDERR_CLASSES.items() if term in sample],
                               stderr_sample_bytes=len(sample))
                if tail:
                    handle = options['stdout']; position = handle.tell()
                    process['output_position'] = position
                    try:
                        handle.seek(0); data = handle.read(LIMIT+1)
                    finally:
                        handle.seek(position)
                else:
                    data = result.stdout
                process['captured_bytes'] = len(data.encode('utf-8') if isinstance(data, str) else data)
                process['capture_truncated'] = process['captured_bytes'] > LIMIT
                decoded = payload(data)
                process['json_valid'] = decoded is not None
                if decoded is not None and tail:
                    frames = decoded.get('frames', [])
                    if isinstance(frames, list):
                        process['frame_count'] = len(frames)
                        process['frames'] = [{key: numeric(f.get(key)) for key in
                            ('best_effort_timestamp', 'duration', 'pkt_duration')} for f in frames[:64] if isinstance(f, dict)]
                        process['frames_truncated'] = len(frames) > 64
                elif decoded is not None:
                    videos = [s for s in decoded.get('streams', []) if isinstance(s, dict)
                              and s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
                    stream = videos[0] if videos else {}
                    process['stream'] = {key: numeric(stream.get(key)) for key in
                        ('time_base', 'avg_frame_rate', 'start_pts', 'start_time', 'duration', 'duration_ts')}
                    process['format_duration'] = numeric(decoded.get('format', {}).get('duration'))
            except Exception as exc:
                process['diagnostic_error'] = type(exc).__name__
            return result
        try:
            result = original(path, *args, **dict(kwargs, run_process=run))
            row['duration'] = result.duration
            row['endpoint'] = ([result.decoded_video_end_num, result.decoded_video_end_den]
                               if result.decoded_video_end_num is not None else None)
            return result
        except Exception as exc:
            row['exception'] = type(exc).__name__
            raise
        finally:
            row['after'] = identity(resolved)
            row['content_unchanged'] = row['before'].get('status') == 'ok' and row['before'] == row['after']
            row['endpoint_reason'] = rejection(row, stream, frames)
    with monkeypatch.context() as patch:
        patch.setattr(media, '_probe', probe)
        try:
            yield
        finally:
            active = sys.exception()
            try:
                output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(dict(schema=1, synthetic_only=True, probes=rows, **counts), indent=2), encoding='utf-8')
            except OSError as exc:
                if active is None: raise
                active.add_note('Synthetic endpoint evidence write failed: ' + type(exc).__name__)

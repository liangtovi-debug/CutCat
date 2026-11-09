"""Local media inspection for the explicitly assigned SDR Rec.709 timeline."""
from __future__ import annotations
import json
import os
import math
from pathlib import Path
import shutil
import subprocess
import uuid
import tempfile
from fractions import Fraction
from .model import Media
from .color_source import source_color_from_stream

class MediaError(ValueError):
    pass

def ffmpeg_binary() -> str:
    return os.environ.get('LUMEN_FFMPEG', 'ffmpeg')

def ffprobe_binary() -> str:
    return os.environ.get('LUMEN_FFPROBE', 'ffprobe')

def ffmpeg_available() -> bool:
    return bool(shutil.which(ffmpeg_binary()) and shutil.which(ffprobe_binary()))

def ffmpeg_report() -> str:
    if not ffmpeg_available():
        return 'FFmpeg and ffprobe must be installed and available on PATH.'
    return subprocess.run([ffmpeg_binary(), '-version'], capture_output=True, text=True, timeout=10, check=True).stdout.splitlines()[0]

def _decoded_video_end(path, stream, anchor, duration, *, run_process=None):
    """Best-effort tail evidence, never a metadata-only duration ceiling.

    Read short files sequentially; seek near EOF only for longer files
    (keyframe preroll is decoder-dependent). Cap wall time and
    parsed output. Unsupported seeks, corrupt tails and incomplete timestamps
    preserve legacy sizing. No extra work for audio-only imports.
    """
    try:
        time_base = Fraction(stream['time_base'])
        if time_base <= 0:
            return None
        start = max(0.0, anchor + duration - 2.0)
        # Even a seek to zero invokes demuxer seeking, which is unnecessary
        # when the whole file fits in the tail window. Preserve the initial
        # demux/decode state for short files, including nonzero stream origins.
        # This is one probe, not a retry or a metadata-only endpoint estimate.
        interval = ['-read_intervals', f'{start:.9f}%'] if duration > 2.0 else []
        with tempfile.TemporaryFile(mode='w+b') as output:
            result = (run_process or subprocess.run)([
                ffprobe_binary(), '-v', 'error', '-select_streams', str(stream['index']),
                *interval, '-show_frames',
                '-show_entries', 'frame=best_effort_timestamp,duration,pkt_duration',
                '-of', 'json', '-protocol_whitelist', 'file,pipe', str(path),
            ], stdout=output, stderr=subprocess.PIPE, timeout=10)
            if result.returncode or result.stderr or output.tell() > 1024 * 1024:
                return None
            output.seek(0)
            frames = json.load(output).get('frames', [])
        if not frames:
            return None
        timestamps = [int(frame['best_effort_timestamp']) for frame in frames]
        if any(b <= a for a, b in zip(timestamps, timestamps[1:])):
            return None
        last = frames[-1]
        ticks = int(last.get('duration', last.get('pkt_duration', 0)))
        if ticks <= 0:
            return None
        nominal = 1 / Fraction(stream['avg_frame_rate'])
        # FFmpeg extrapolates VFR EOF from observed timing, not necessarily
        # the final packet's nominal duration. This bounded repair accepts
        # only a decoded CFR tail, allowing container timestamp quantization.
        if (nominal <= 0 or abs(ticks * time_base - nominal) > time_base
                or any(abs((b - a) * time_base - nominal) > time_base
                       for a, b in zip(timestamps, timestamps[1:]))):
            return None
        origin = Fraction(stream['start_pts']) * time_base if 'start_pts' in stream else Fraction(str(anchor))
        end = (timestamps[-1] + ticks) * time_base - origin
        # Do not repair severely inconsistent/missing duration metadata. This
        # correction addresses sub-frame rounding, not arbitrary damaged files.
        if end <= 0 or end > Fraction(str(duration)) + 1:
            return None
        return end
    except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError, OSError, subprocess.SubprocessError):
        return None


def _probe(path, input_encoding="rec709", *, run_process=None) -> Media:
    if input_encoding not in ("rec709", "apple_log_bt2020"):
        raise MediaError("Unsupported media input encoding")
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise MediaError('Select a media file.')
    if not ffmpeg_available():
        raise MediaError(ffmpeg_report())
    p = (run_process or subprocess.run)([ffprobe_binary(), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', '-protocol_whitelist', 'file,pipe', str(path)], capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise MediaError(p.stderr.strip() or 'Cannot inspect media.')
    data = json.loads(p.stdout)
    videos = [s for s in data.get('streams', []) if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
    audios = [s for s in data.get('streams', []) if s.get('codec_type') == 'audio']
    if not videos and not audios:
        raise MediaError('No supported audio or video streams.')
    v = videos[0] if videos else {}
    if input_encoding == "apple_log_bt2020" and not videos:
        raise MediaError("Apple Log input requires video")
    if videos:
        if input_encoding == "rec709":
            source_color_from_stream(v)
        else:
            source_color_from_stream(v, input_encoding=input_encoding)
    fmt = data.get('format', {})
    anchor = float(v.get('start_time', audios[0].get('start_time', 0) if audios else 0))
    durations = [float(s['duration']) + float(s.get('start_time', anchor)) - anchor for s in videos[:1] + audios[:1] if s.get('duration') not in (None, 'N/A')]
    duration = max(durations, default=float(fmt.get('duration', 0)) + float(fmt.get('start_time', anchor)) - anchor)
    if not math.isfinite(duration) or duration <= 0:
        raise MediaError('Media must have a finite, positive duration.')
    try:
        fps = Fraction(v.get('avg_frame_rate', '30/1'))
        if fps <= 0: fps = Fraction(30)
    except (ValueError, ZeroDivisionError):
        fps = Fraction(30)
    audio_offset = float(audios[0].get('start_time', anchor)) - anchor if audios else 0.0
    if not math.isfinite(audio_offset):
        raise MediaError('Invalid audio timestamp offset.')
    video_end = (_decoded_video_end(path, v, anchor, duration,
                     **({'run_process': run_process} if run_process is not None else {})) if videos else None)
    if video_end is not None and not audios:
        # Some containers report an absolute EOF in format.duration for shifted
        # timestamps. A verified video-only endpoint is relative to its origin.
        duration = float(video_end)
    return Media(id=uuid.uuid4().hex, path=str(path), name=path.name, duration=duration,
                 width=int(v.get('width', 0)), height=int(v.get('height', 0)), has_video=bool(videos),
                 has_audio=bool(audios), fps_num=fps.numerator, fps_den=fps.denominator,
                 decoded_video_end_num=video_end.numerator if video_end is not None else None,
                 decoded_video_end_den=video_end.denominator if video_end is not None else None,
                 color_space=v.get('color_space', 'unknown'),
                 audio_offset=audio_offset,
                 color_transfer=v.get('color_transfer', 'unknown'),
                 color_primaries=v.get('color_primaries', 'unknown'),
                 color_range=v.get('color_range', 'unknown'),
                 input_encoding=input_encoding)


def probe(path, input_encoding="rec709", *, run_process=None) -> Media:
    try:
        if run_process is not None:
            return _probe(path, input_encoding=input_encoding, run_process=run_process)
        return _probe(path) if input_encoding == "rec709" else _probe(path, input_encoding=input_encoding)
    except MediaError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, OSError, subprocess.SubprocessError) as exc:
        raise MediaError(f'Cannot inspect media: {exc}') from exc

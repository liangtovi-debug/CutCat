"""Session-only bounded final SDR audio/video playback, never a timeline edit.

The final compositor receives an explicit window. Original clip maps, local
animation and sample clocks stay intact. Decoders may traverse earlier source
history: this is reduced emitted work, not random-access or realtime playback.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from contextlib import ExitStack
from fractions import Fraction
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from .color_picker_source import _source_identity, _grade_lut_identity, _run
from .final_frame_preview import project_signature, _self_contained_source
from .media import ffmpeg_binary, ffprobe_binary
from .render import RenderEngine, RenderError, RenderCancelled, _n
from .timing import sample_boundary

MAX_SECONDS = 10
MAX_FRAMES = 600
MAX_TIMEOUT = 120.0
MAX_SOURCE_SECONDS = 300
MAX_CACHE_BYTES = 1024 * 1024 * 1024
_AUDIO_CAPABILITIES = {}
_AUDIO_CAPABILITY_LOCK = threading.Lock()
FALLBACK = 'Use Build full timeline preview for this project/range.'


class RangePreviewError(RenderError):
    pass


class RangePreviewCancelled(RangePreviewError):
    pass


@dataclass(frozen=True)
class RangePreviewIdentity:
    project_signature: str
    start_frame: int
    end_frame: int
    dependencies: tuple
    output_identity: tuple


@dataclass(frozen=True)
class RangePreviewWork:
    active_clip_ids: tuple[str, ...]
    graded_frames: int
    sticker_frames: int
    composite_frames: int
    mixed_samples: int
    intermediate_bytes: int
    source_decode_may_traverse_earlier_frames: bool = True


@dataclass(frozen=True)
class RangePreviewSnapshot:
    path: Path
    identity: RangePreviewIdentity
    work: RangePreviewWork
    _directory: object = field(repr=False, compare=False)
    stage: str = 'rendered_range_rec709_sdr_h264_aac'

    def close(self):
        """Idempotent owned-cache cleanup. UI calls this only on a worker."""
        self._directory.cleanup()


class _Guard:
    """Boolean cancellation contract for legacy watcher threads; no exceptions."""
    def __init__(self, cancel, deadline):
        self.cancel, self.deadline = cancel, deadline
        self.resource_error = None

    def is_set(self):
        return (bool(self.cancel is not None and self.cancel.is_set())
                or time.monotonic() >= self.deadline or self.resource_error is not None)

    def check(self):
        if self.cancel is not None and self.cancel.is_set():
            raise RangePreviewCancelled('Range preview canceled.')
        if self.resource_error is not None:
            raise RangePreviewError(self.resource_error + ' ' + FALLBACK)
        if time.monotonic() >= self.deadline:
            raise RangePreviewError('Range preview timed out. ' + FALLBACK)


def _guard(cancel, timeout):
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
        raise RangePreviewError('Range preview timeout must be positive and finite.')
    return _Guard(cancel, time.monotonic() + min(timeout, MAX_TIMEOUT))


def validate_range(project, start_frame, end_frame):
    """Pure admission checks; callers validate their detached snapshot first."""
    if (type(start_frame) is not int or type(end_frame) is not int
            or not 0 <= start_frame < end_frame <= project.duration_frames):
        raise RangePreviewError('Choose a nonempty existing integer frame range. ' + FALLBACK)
    if (end_frame - start_frame > MAX_FRAMES
            or Fraction(end_frame - start_frame, 1) / project.fps > MAX_SECONDS):
        raise RangePreviewError('Range preview supports at most 10 seconds and 600 frames. ' + FALLBACK)
    if (project.width > 1920 or project.height > 1080 or project.width % 2 or project.height % 2):
        raise RangePreviewError('Range preview needs an even SDR canvas up to 1920 x 1080. ' + FALLBACK)
    from .video_transition import plan_video_transitions
    if any(p.start < end_frame and p.end > start_frame for p in plan_video_transitions(project)):
        raise RangePreviewError('Range preview does not support an active video transition. ' + FALLBACK)
    clips = tuple(c for c in project.clips if c.start < end_frame and c.end > start_frame)
    if len(clips) > 32:
        raise RangePreviewError('Range preview supports at most 32 intersecting clips. ' + FALLBACK)
    media = {m.id: m for m in project.media}
    for clip in clips:
        if clip.track >= 2:
            continue
        if clip.stabilization.enabled and clip.stabilization.strength > 0:
            raise RangePreviewError('Range preview does not support active stabilization. ' + FALLBACK)
        if (clip.grade.input_encoding == 'apple_log_bt2020'
                or media[clip.media_id].input_encoding == 'apple_log_bt2020'):
            raise RangePreviewError('Range preview supports original SDR input, not Apple Log. ' + FALLBACK)
        last = min(clip.duration, end_frame - clip.start) - 1
        source_end = (clip.timing.source_in + last * clip.timing.speed + 1) / project.fps
        if source_end > MAX_SOURCE_SECONDS:
            raise RangePreviewError('Range picture decoding requires more than 300 seconds of source history. ' + FALLBACK)
    return clips


def _dependencies(project, start, end, guard):
    guard.check()
    clips = validate_range(project, start, end)
    media = {m.id: m for m in project.media}
    dependencies, seen = [], {}
    for clip in clips:
        guard.check()
        identity = _source_identity(media[clip.media_id].path)
        if identity not in seen:
            seen[identity] = _self_contained_source(identity[0], guard, guard.deadline)
        dependencies.append(('source', clip.id, identity, seen[identity]))
        if clip.track < 2:
            dependencies.append(('lut', clip.id, _grade_lut_identity(clip.grade, guard, guard.deadline)))
    from .sticker_assets import verify_sticker
    overlays = [o for o in project.overlays if o.enabled and o.start < end and o.end > start]
    for overlay in overlays:
        guard.check()
        asset = verify_sticker(overlay.asset_path, overlay.asset_sha256, cancel=guard)
        dependencies.append(('sticker', overlay.id, _source_identity(overlay.asset_path),
                             asset.digest, tuple(asset.frame_sha256),
                             tuple(_source_identity(p) for p in asset.frames)))
    if any(c.start < end and c.end > start for c in project.captions):
        from .caption_render import font_identity
        identity = font_identity(project.caption_style, check=guard.check,
            run_process=lambda args, **kwargs: _checked_run(guard, args, **kwargs))
        dependencies.append(('font', identity, _source_identity(identity[0])))
    from .color_media import decoder_identity
    dependencies.append(('decoder', decoder_identity(guard, RangePreviewError, RangePreviewCancelled)))
    # Generated-file provenance is content checked even during playback checks.
    owner = object.__new__(RenderEngine)
    owner._verify_generated_sources(replace(project, clips=list(clips)), guard)
    guard.check()
    return tuple(dependencies)


def _size(directory):
    total = 0
    for root, _, files in os.walk(directory):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except FileNotFoundError:
                pass
    return total


def _checked_run(guard, args, *, input=None, timeout=None, capture_output=False,
                 stdout=None, stderr=None, text=False, check=False):
    """Small run-compatible adapter for existing metadata/capability probes.

    Keeps their parsers and exact algorithms, but owns every process and bounds
    capture, cancellation and the entire range's shared deadline. No pipe read
    can block the GUI or an uninterruptible subprocess.run timeout.
    """
    guard.check()
    deadline = min(guard.deadline, time.monotonic() + timeout) if timeout else guard.deadline
    process = None
    with ExitStack() as stack:
        out = stdout if stdout not in (None, subprocess.PIPE) else stack.enter_context(tempfile.TemporaryFile())
        err = stack.enter_context(tempfile.TemporaryFile())
        source = subprocess.DEVNULL
        if input is not None:
            source = stack.enter_context(tempfile.TemporaryFile())
            source.write(input.encode() if isinstance(input, str) else input)
            source.seek(0)
        try:
            process = subprocess.Popen(args, stdin=source, stdout=out, stderr=err)
            while process.poll() is None:
                guard.check()
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(args, timeout)
                if os.fstat(out.fileno()).st_size > 8 * 1024 * 1024 or os.fstat(err.fileno()).st_size > 65536:
                    raise RangePreviewError('Range source/capability probe exceeded its output budget. ' + FALLBACK)
                time.sleep(.025)
            guard.check()
            if os.fstat(out.fileno()).st_size > 8 * 1024 * 1024 or os.fstat(err.fileno()).st_size > 65536:
                raise RangePreviewError('Range source/capability probe exceeded its output budget. ' + FALLBACK)
            out.seek(0); err.seek(0)
            data, errors = out.read(), err.read()
            if text:
                data, errors = data.decode(errors='replace'), errors.decode(errors='replace')
            result = subprocess.CompletedProcess(args, process.returncode,
                data if capture_output or stdout == subprocess.PIPE else None,
                errors if capture_output or stderr == subprocess.PIPE else None)
            if check:
                result.check_returncode()
            return result
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()


class _RangeEngine(RenderEngine):
    def __init__(self, guard, decoder):
        super().__init__(timeout=max(.001, guard.deadline - time.monotonic()))
        self._range_check = guard.check
        self._range_run_process = lambda args, **kwargs: _checked_run(guard, args, **kwargs)
        def capability():
            from .audio_timing import _probe_audio_compensation, AUDIO_TIMING_ALGORITHM
            key = (decoder, AUDIO_TIMING_ALGORITHM)
            guard.check()
            with _AUDIO_CAPABILITY_LOCK:
                known = key in _AUDIO_CAPABILITIES
            if not known:
                _probe_audio_compensation(ffmpeg_binary(), min(30, max(.001, guard.deadline - time.monotonic())),
                                          run_process=self._range_run_process)
                guard.check()
                with _AUDIO_CAPABILITY_LOCK:
                    if len(_AUDIO_CAPABILITIES) >= 8:
                        _AUDIO_CAPABILITIES.pop(next(iter(_AUDIO_CAPABILITIES)))
                    _AUDIO_CAPABILITIES[key] = True
        self._range_audio_capability = capability


def _encode(engine, project, start, end, output, guard):
    guard.check()
    inputs, graph, duration = engine._compile(project, cancel=guard, frame_window=(start, end),
                                               filter_complex_threads=1)
    guard.check()
    graph_path = Path(engine._cache_dir.name) / 'range.ffgraph'
    graph_path.write_text(graph, encoding='utf-8')
    command = [ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-filter_complex_threads', '1',
               *inputs, '-/filter_complex', str(graph_path), '-map', '[vout]', '-map', '[aout]',
               '-r', f'{project.fps_num}/{project.fps_den}', '-fps_mode', 'cfr',
               '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '20', '-pix_fmt', 'yuv420p',
               '-threads', '1', '-color_primaries', 'bt709', '-color_trc', 'bt709',
               '-colorspace', 'bt709', '-color_range', 'tv', '-c:a', 'aac', '-b:a', '192k',
               '-ar', '48000', '-ac', '2', '-t', _n(duration), '-movflags', '+faststart', str(output)]
    _run(command, 65536, guard, guard.deadline)
    guard.check()
    import json
    result = json.loads(_run([ffprobe_binary(), '-v', 'error', '-count_frames',
        '-show_entries', 'stream=codec_type,codec_name,width,height,pix_fmt,nb_read_frames,sample_rate,channels',
        '-of', 'json', '-protocol_whitelist', 'file,pipe', str(output)], 65536, guard, guard.deadline))
    streams = result.get('streams', [])
    videos = [s for s in streams if s.get('codec_type') == 'video']
    audios = [s for s in streams if s.get('codec_type') == 'audio']
    if (len(videos) != 1 or len(audios) != 1
            or videos[0].get('codec_name') != 'h264' or videos[0].get('pix_fmt') != 'yuv420p'
            or (videos[0].get('width'), videos[0].get('height')) != (project.width, project.height)
            or int(videos[0].get('nb_read_frames', -1)) != end - start
            or audios[0].get('sample_rate') != '48000' or audios[0].get('channels') != 2):
        raise RangePreviewError('Range playback output did not match its frame/audio contract. ' + FALLBACK)


def capture_range_preview(project, start_frame, end_frame, cancel=None, timeout=120):
    """Worker-only capture. Caller owns returned snapshot and must close it."""
    guard = _guard(cancel, timeout)
    directory = None
    engine = None
    finished = threading.Event()
    watcher = None
    try:
        guard.check()
        signature = project_signature(project)
        detached = deepcopy(project)
        detached.validate()
        if project_signature(detached) != signature:
            raise RangePreviewError('Project changed before range preparation.')
        clips = validate_range(detached, start_frame, end_frame)
        before = _dependencies(detached, start_frame, end_frame, guard)
        directory = tempfile.TemporaryDirectory(prefix='cutcat-range-')
        decoder = next(entry[1] for entry in before if entry[0] == 'decoder')
        engine = _RangeEngine(guard, decoder)
        output_directory = directory.name
        def watch_budget():
            while not finished.wait(.1):
                if _size(engine._cache_dir.name) + _size(output_directory) > MAX_CACHE_BYTES:
                    guard.resource_error = 'Range preview exceeded its 1 GiB session-cache budget.'
                    return
        watcher = threading.Thread(target=watch_budget, daemon=True)
        watcher.start()
        output = Path(directory.name) / 'range.mp4'
        _encode(engine, detached, start_frame, end_frame, output, guard)
        after = _dependencies(detached, start_frame, end_frame, guard)
        guard.check()
        if before != after or project_signature(project) != signature:
            raise RangePreviewError('Project or source dependencies changed during range preparation.')
        bytes_used = _size(engine._cache_dir.name) + _size(directory.name)
        if bytes_used > MAX_CACHE_BYTES:
            raise RangePreviewError('Range preview exceeded its 1 GiB session-cache budget. ' + FALLBACK)
        frames = end_frame - start_frame
        work = RangePreviewWork(tuple(c.id for c in clips),
            sum(min(c.end, end_frame) - max(c.start, start_frame) for c in clips
                if c.track < 2 and next(m for m in detached.media if m.id == c.media_id).has_video),
            frames if any(o.enabled and o.start < end_frame and o.end > start_frame for o in detached.overlays) else 0,
            frames, sample_boundary(end_frame, detached.fps, 48000) - sample_boundary(start_frame, detached.fps, 48000),
            bytes_used)
        snapshot = RangePreviewSnapshot(output, RangePreviewIdentity(signature, start_frame, end_frame,
            after, _source_identity(output)), work, directory)
        directory = None  # ownership moves to snapshot only on successful publication
        return snapshot
    except Exception as exc:
        guard.check()
        if isinstance(exc, RangePreviewError):
            raise
        if isinstance(exc, RenderCancelled):
            raise RangePreviewCancelled('Range preview canceled.') from exc
        raise RangePreviewError(f'Cannot prepare range preview: {exc} {FALLBACK}') from exc
    finally:
        finished.set()
        if watcher is not None:
            watcher.join(timeout=2)
        if engine is not None:
            engine.close()
        if directory is not None:
            directory.cleanup()


def verify_range_preview(snapshot, project, cancel=None, timeout=30):
    """Worker-only source/output verification before playback and loop restart."""
    guard = _guard(cancel, timeout)
    try:
        guard.check()
        if (not isinstance(snapshot, RangePreviewSnapshot)
                or project_signature(project) != snapshot.identity.project_signature):
            return False
        detached = deepcopy(project)
        if project_signature(detached) != snapshot.identity.project_signature:
            return False
        current = _dependencies(detached, snapshot.identity.start_frame, snapshot.identity.end_frame, guard)
        return (current == snapshot.identity.dependencies
                and _source_identity(snapshot.path) == snapshot.identity.output_identity
                and project_signature(project) == snapshot.identity.project_signature)
    except RangePreviewCancelled:
        raise
    except Exception:
        guard.check()
        return False

"""Session-only source inspection, never a timeline preview or an edit.

All file access belongs to SourcePreviewBackend worker methods. Construction,
marks, cursor changes and result currency checks do no I/O. Requests are whole
project-frame intervals at 1x. Video decode seeks with preroll and retains the global source timestamp/fps
phase. Exact audio decodes from origin, discards prefixes and caches only requested
normalized f32 sample windows. Only one frame is buffered between decode and encode;
private output, stderr, wall time, dimensions, cache and audition duration are
bounded. No source file or real Project is ever written.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
import json
import math
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import uuid

from .file_identity import is_plain_file, same_file_state, path_matches_descriptor
from .media import ffmpeg_binary, ffprobe_binary

MAX_AUDITION_SECONDS = 30
MAX_AUDIO_SCAN_SAMPLES = 30 * 60 * 48000
MAX_PCM_CACHE_ENTRIES = 32
PCM_FRAME_BYTES = 8
PCM_ALGORITHM = 'origin-asetpts-aresample48000-async1-firstpts0-stereo-fltp-f32le-v1'
MAX_TIMEOUT = 120.0
MAX_OUTPUT_BYTES = 24 * 1024 * 1024
MAX_CACHE_BYTES = 48 * 1024 * 1024
MAX_CACHE_ENTRIES = 4
MAX_PROBE_BYTES = 256 * 1024
MAX_LOG_BYTES = 64 * 1024
MAX_SOURCE_PIXELS = 33_554_432
MAX_PROVENANCE_BYTES = 64 * 1024 * 1024
MAX_FPS = 120
# No manifests, playlists, image sequences, network or external dependencies.
SAFE_INPUT_FORMATS = ('mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi,wav,aiff,flac,'
                      'mp3,ogg,aac,caf,mpeg,mpegts,asf,gif,apng,png_pipe,jpeg_pipe,'
                      'webp_pipe,bmp_pipe')


class SourceViewerError(ValueError):
    """A faithful, bounded source inspection is unavailable."""


class SourceViewerCancelled(SourceViewerError):
    pass


def _signature(project, media_id):
    selected = [media for media in project.media if media.id == media_id]
    if len(selected) != 1:
        raise SourceViewerError('Select one existing source media item.')
    value = (project.width, project.height, project.fps_num, project.fps_den,
             asdict(selected[0]))
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _complete_frames(media, fps):
    # Shared with the edit planner; unlike Project.media_frames this never
    # rounds a fractional final frame up or extends video with longer audio.
    from .source_overwrite import complete_source_frames
    return complete_source_frames(media, fps)


class SourceViewerSession:
    """In-memory selection and marks, independent of the timeline playhead."""
    def __init__(self, project, media_id, *, allow_empty=False):
        self.signature = _signature(project, media_id)
        self.media = deepcopy(next(m for m in project.media if m.id == media_id))
        self.media_id = media_id
        if (any(type(v) is not int or not 1 <= v <= 1_000_000
                for v in (project.fps_num, project.fps_den))
                or not Fraction(1, 1000) <= project.fps <= MAX_FPS):
            raise SourceViewerError('Source viewer project frame rate must be at most 120 fps.')
        if any(type(v) is not int or not 1 <= v <= 16384
               for v in (project.width, project.height)):
            raise SourceViewerError('Invalid source viewer canvas.')
        self.fps = Fraction(project.fps)
        scale = min(1, 640 / project.width, 360 / project.height)
        self.width = max(2, int(project.width * scale) // 2 * 2)
        self.height = max(2, int(project.height * scale) // 2 * 2)
        try:
            self.total_frames = _complete_frames(self.media, self.fps)
        except (ValueError, TypeError, OverflowError) as exc:
            raise SourceViewerError(str(exc)) from exc
        if self.total_frames < 1 and not allow_empty:
            raise SourceViewerError('Source has no complete project-frame interval.')
        self.generation = 0
        self.cursor = self.in_frame = 0
        self.out_frame = self.total_frames
        self.token = uuid.uuid4().hex

    def matches(self, project, media_id=None):
        try:
            return ((media_id is None or media_id == self.media_id)
                    and _signature(project, self.media_id) == self.signature)
        except (ValueError, TypeError, AttributeError):
            return False

    def seek(self, frame):
        if type(frame) is not int or not 0 <= frame < self.total_frames:
            raise SourceViewerError('Source cursor is outside complete source frames.')
        if frame != self.cursor:
            self.cursor = frame
            self.generation += 1

    def mark_in(self):
        self.in_frame = self.cursor
        if self.out_frame <= self.in_frame:
            self.out_frame = self.in_frame + 1
        self.generation += 1

    def mark_out(self):
        self.out_frame = self.cursor + 1
        if self.in_frame >= self.out_frame:
            self.in_frame = self.cursor
        self.generation += 1

    def clear_marks(self):
        self.in_frame, self.out_frame = 0, self.total_frames
        self.generation += 1

    def selection(self):
        if (type(self.in_frame) is not int or type(self.out_frame) is not int
                or not 0 <= self.in_frame < self.out_frame <= self.total_frames):
            raise SourceViewerError('Source marks must enclose complete project frames.')
        return self.in_frame, self.out_frame


@dataclass(frozen=True)
class SourceIdentity:
    path: str
    state: tuple
    sample_sha256: str
    signature: str


@dataclass(frozen=True)
class SourceFrameResult:
    width: int
    height: int
    pixels: bytes
    source_frame: int
    generation: int
    identity: SourceIdentity
    session_token: str

    def is_current(self, session, project=None):
        return _current(self, session, project)


@dataclass(frozen=True)
class SourceAuditionResult:
    path: Path
    start_frame: int
    end_frame: int
    generation: int
    identity: SourceIdentity
    session_token: str
    width: int
    height: int

    def is_current(self, session, project=None):
        return _current(self, session, project)


def _current(result, session, project):
    # Deliberately no stat or cache read here: callers invoke verify_identity
    # on a worker before applying edits. This is a model/generation guard only.
    return (result.session_token == session.token
            and result.generation == session.generation
            and result.identity.signature == session.signature
            and (project is None or session.matches(project)))


def _check(cancel, deadline):
    if cancel is not None and cancel.is_set():
        raise SourceViewerCancelled('Source preview cancelled.')
    if time.monotonic() >= deadline:
        raise SourceViewerError('Source preview exceeded its runtime budget.')


def _state(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, getattr(info, 'st_birthtime_ns', None))


def _identity(session, cancel, deadline):
    """Bounded sampled identity, with portable path/fd before-after checks.

    This detects ordinary replacements/edits; it is not a lock or a whole-file
    cryptographic proof against storage that rewrites bytes and all metadata.
    """
    _check(cancel, deadline)
    requested = Path(session.media.path).expanduser().absolute()
    requested_before = requested.lstat()
    if not is_plain_file(requested_before):
        raise SourceViewerError('Source viewer requires a plain local media file.')
    # Pin decoder commands to the resolved path, so a parent-directory symlink
    # cannot be retargeted A→B→A between our source checks.
    path = requested.resolve(strict=True)
    before = path.lstat()
    if not is_plain_file(before) or not same_file_state(requested_before, before):
        raise SourceViewerError('Source changed while resolving its local path.')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        descriptor = os.fstat(stream.fileno())
        if not path_matches_descriptor(before, descriptor):
            raise SourceViewerError('Source changed while being opened.')
        for offset in sorted({0, max(0, before.st_size // 2 - 32768),
                              max(0, before.st_size - 65536)}):
            _check(cancel, deadline)
            stream.seek(offset)
            digest.update(str(offset).encode() + b':' + stream.read(65536))
        provenance = session.media.sound_provenance or session.media.speech_provenance
        if provenance is not None:
            if before.st_size > MAX_PROVENANCE_BYTES:
                raise SourceViewerError('Generated source exceeds the 64 MiB identity-check budget.')
            stream.seek(0)
            content = hashlib.sha256()
            count = 0
            while block := stream.read(1024 * 1024):
                _check(cancel, deadline)
                count += len(block)
                if count > MAX_PROVENANCE_BYTES:
                    raise SourceViewerError('Generated source exceeds the 64 MiB identity-check budget.')
                content.update(block)
            if content.hexdigest() != provenance.output_sha256.lower():
                raise SourceViewerError('Generated source has changed; restore or regenerate the original audio.')
            digest.update(content.digest())
        after = path.lstat()
        descriptor_after = os.fstat(stream.fileno())
        if (not same_file_state(before, after)
                or not same_file_state(requested_before, requested.lstat())
                or not same_file_state(descriptor, descriptor_after)
                or not path_matches_descriptor(after, descriptor_after)):
            raise SourceViewerError('Source changed during inspection.')
    # Retain both same-API snapshots: on Windows path ctime may be creation
    # time while fstat ctime is the stronger metadata-change timestamp.
    return SourceIdentity(str(path), (_state(after), _state(descriptor_after)),
                          digest.hexdigest(), session.signature)


class _Pipeline:
    """Own subprocesses and cap every collected byte before writing it.

    The watcher kills blocked pipe reads/writes on cancellation/deadline. Reader
    threads only hold bounded chunks. Every child is killed/reaped on failure.
    """
    def __init__(self, cancel, deadline):
        self.cancel, self.deadline = cancel, deadline
        self.processes, self.threads, self.failures = [], [], []
        self.finished = threading.Event()
        self.watcher = threading.Thread(target=self._watch, daemon=True)

    def __enter__(self):
        _check(self.cancel, self.deadline)
        self.watcher.start()
        return self

    def kill(self):
        for process in self.processes:
            if process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass

    def fail(self, error):
        self.failures.append(error)
        self.kill()

    def check(self):
        _check(self.cancel, self.deadline)
        if self.failures:
            raise self.failures[0]

    def _watch(self):
        while not self.finished.wait(.01):
            try:
                self.check()
            except Exception as exc:
                self.fail(exc)
                return

    def capture(self, pipe, budget, sink):
        def read():
            count = 0
            try:
                while block := pipe.read(65536):
                    count += len(block)
                    if count > budget:
                        raise SourceViewerError('Source preview exceeded its output byte budget.')
                    sink(block)
            except Exception as exc:
                self.fail(exc)
            finally:
                pipe.close()
        thread = threading.Thread(target=read, daemon=True)
        self.threads.append(thread)
        thread.start()
        return thread

    def spawn(self, args, stdin=None):
        self.check()
        process = subprocess.Popen(args, stdin=stdin, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        self.processes.append(process)
        errors = bytearray()
        process._source_error_reader = self.capture(process.stderr, MAX_LOG_BYTES, errors.extend)
        return process, errors

    def finish(self, process, errors):
        process.wait()
        process._source_error_reader.join()
        self.check()
        if process.returncode:
            if b'Format not on whitelist' in errors:
                raise SourceViewerError('Source viewer requires a self-contained media container; manifests and external-reference formats are unsupported.')
            raise SourceViewerError('Cannot decode or encode this source preview.')

    def __exit__(self, typ, value, traceback):
        self.finished.set()
        self.kill()
        for process in self.processes:
            process.wait()
            for pipe in (process.stdin, process.stdout):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        # Encoder stdin may still have buffered bytes after a
                        # cancellation killed its child. Never mask the cause.
                        pass
        for thread in self.threads:
            thread.join()
        self.watcher.join()
        if typ is None:
            self.check()


def _run(args, budget, cancel, deadline):
    data = bytearray()
    with _Pipeline(cancel, deadline) as pipe:
        process, errors = pipe.spawn(args, stdin=subprocess.DEVNULL)
        reader = pipe.capture(process.stdout, budget, data.extend)
        pipe.finish(process, errors)
        reader.join()
        pipe.check()
    return bytes(data)


def _number(value):
    return format(float(value), '.15g')


def _probe(session, identity, cancel, deadline):
    raw = _run([ffprobe_binary(), '-v', 'error', '-show_streams', '-show_format',
                '-of', 'json', '-protocol_whitelist', 'file,pipe',
                '-format_whitelist', SAFE_INPUT_FORMATS, identity.path],
               MAX_PROBE_BYTES, cancel, deadline)
    data = json.loads(raw)
    formats = set(data.get('format', {}).get('format_name', '').split(','))
    if not formats or not formats <= set(SAFE_INPUT_FORMATS.split(',')):
        raise SourceViewerError('Source viewer requires a self-contained media container; manifests and external-reference formats are unsupported.')
    videos = [s for s in data.get('streams', []) if s.get('codec_type') == 'video'
              and not s.get('disposition', {}).get('attached_pic')]
    audios = [s for s in data.get('streams', []) if s.get('codec_type') == 'audio']
    if bool(videos) != session.media.has_video or bool(audios) != session.media.has_audio:
        raise SourceViewerError('Source stream layout no longer matches imported media.')
    primary = videos[0] if videos else audios[0] if audios else None
    if primary is None:
        raise SourceViewerError('Source has no usable media stream.')
    origin = (Fraction(primary['start_pts']) * Fraction(primary['time_base'])
              if 'start_pts' in primary else Fraction(primary.get('start_time', '0')))
    format_start = Fraction(data.get('format', {}).get('start_time', '0'))
    capacity = session.total_frames
    capacity_proven = False
    if primary.get('duration_ts') is not None and primary.get('time_base'):
        duration = Fraction(primary['duration_ts']) * Fraction(primary['time_base'])
        capacity = min(capacity, math.floor(duration * session.fps))
        capacity_proven = True
    elif primary.get('duration') not in (None, 'N/A'):
        capacity = min(capacity, math.floor(Fraction(primary['duration']) * session.fps))
        capacity_proven = True
    audio_capacity = None
    audio_duration = None
    if audios:
        audio = audios[0]
        audio_origin = (Fraction(audio['start_pts']) * Fraction(audio['time_base'])
                        if 'start_pts' in audio else Fraction(audio.get('start_time', str(float(origin)))))
        # Import retains ffprobe's decimal start_time (microsecond precision),
        # while this worker can read the exact native tick rational.
        if abs((audio_origin-origin) - Fraction(str(session.media.audio_offset))) > Fraction(1, 1_000_000):
            raise SourceViewerError('Source audio timestamp offset changed since import; re-import the source.')
        audio_duration = None
        if audio.get('duration_ts') is not None and audio.get('time_base'):
            audio_duration = Fraction(audio['duration_ts']) * Fraction(audio['time_base'])
        elif audio.get('duration') not in (None, 'N/A'):
            audio_duration = Fraction(audio['duration'])
        if audio_duration is not None:
            audio_capacity = math.floor((audio_origin-origin+audio_duration) * session.fps)
    color = None
    if videos:
        from .color_source import source_color_from_stream, validate_input_assignment
        from .color_picker_source import _has_alpha
        video = videos[0]
        if (any(type(video.get(k)) is not int or not 1 <= video[k] <= 16384
                for k in ('width', 'height'))
                or video['width'] * video['height'] > MAX_SOURCE_PIXELS):
            raise SourceViewerError('Source dimensions exceed the preview memory budget.')
        if (video['width'] != session.media.width or video['height'] != session.media.height
                or Fraction(video.get('avg_frame_rate', '0')) != Fraction(session.media.fps_num, session.media.fps_den)):
            raise SourceViewerError('Source video dimensions or native frame rate changed since import; re-import the source.')
        color = source_color_from_stream(video, input_encoding=session.media.input_encoding)
        validate_input_assignment(color, session.media.input_encoding)
        if _has_alpha(color.pixel_format):
            raise SourceViewerError('Transparent source preview is not supported.')
    return {'video': videos[0] if videos else None,
            'audio': audios[0] if audios else None, 'origin': origin,
            'format_start': format_start, 'capacity': capacity, 'capacity_proven': capacity_proven, 'audio_capacity': audio_capacity, 'audio_duration': audio_duration, 'color': color,
            # Worker-only raw headers for path-free saved-range semantics;
            # existing preview/proof consumers keep their historical fields.
            'source_streams': data.get('streams', []), 'source_format': data.get('format', {})}


def _input_args(identity, info, session, start):
    # Accurate input seek discards preroll before this point, not a whole-file
    # transcode. copyts and absolute normalization preserve the global fps grid.
    seek = max(Fraction(0), info['origin'] - info['format_start']
               + Fraction(start, 1) / session.fps - 1)
    return ['-copyts', '-ss', _number(seek), '-protocol_whitelist', 'file,pipe',
            '-format_whitelist', SAFE_INPUT_FORMATS, '-i', identity.path]


def _video_args(identity, info, session, start, end):
    from .color_media import source_rgb_filters
    filters = [f"setpts=PTS-({_number(info['origin'])})/TB",
               f'fps=fps={session.fps.numerator}/{session.fps.denominator}:round=near:eof_action=pass',
               f'trim=start_pts={start}:end_pts={end}', 'setpts=PTS-STARTPTS',
               *source_rgb_filters(identity.path, MAX_TIMEOUT, SourceViewerError, info['color']),
               'scale=w=iw*sar:h=ih:in_color_matrix=bt709:out_color_matrix=bt709:out_range=full',
               'format=rgb48le', 'setsar=1',
               f'scale={session.width}:{session.height}:force_original_aspect_ratio=decrease:in_color_matrix=bt709:out_color_matrix=bt709:out_range=full',
               'format=rgb48le',
               f'pad={session.width}:{session.height}:(ow-iw)/2:(oh-ih)/2:color=black',
               'setsar=1']
    return [ffmpeg_binary(), '-v', 'error', '-xerror', '-nostdin', '-threads', '1',
            '-filter_threads', '1', *_input_args(identity, info, session, start),
            '-map', f"0:{info['video']['index']}", '-an', '-vf', ','.join(filters),
            '-frames:v', str(end-start), '-fps_mode', 'passthrough', '-threads', '1',
            '-pix_fmt', 'rgb48le', '-f', 'rawvideo', 'pipe:1']


def _pixels(raw, session, cancel, deadline):
    import numpy as np
    from .color import normalize_input
    source = np.frombuffer(raw, dtype='<u2').reshape(-1, 3)
    output = np.empty(source.shape, dtype=np.uint8)
    for start in range(0, len(source), 32768):
        _check(cancel, deadline)
        normalized = normalize_input(source[start:start + 32768].astype(np.float32)
                                     / np.float32(65535), session.media.input_encoding)
        output[start:start + 32768] = np.rint(np.clip(normalized, 0, 1) * 255).astype(np.uint8)
    return output.tobytes()


class SourcePreviewBackend:
    """Serial worker with sparse exact PCM and audition LRUs sharing 48 MiB.

    A whole operation is limited to 120s and 30 minutes cumulative normalized
    origin scan. Selected/proof intervals remain at most 30s. PCM cache files
    never enter a Project or the renderer. Optional max_cache_bytes still bounds
    audition proxies independently; max_storage_bytes caps all staged/LRU files.

    Constructing it performs no I/O. close() is worker-only and must follow all
    active work; paths are temporary and can expire on the next cache eviction.
    """
    def __init__(self, timeout=120, *, max_output_bytes=MAX_OUTPUT_BYTES,
                 max_cache_bytes=MAX_CACHE_BYTES, max_cache_entries=MAX_CACHE_ENTRIES,
                 max_storage_bytes=MAX_CACHE_BYTES, max_scan_samples=MAX_AUDIO_SCAN_SAMPLES):
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT):
            raise SourceViewerError('Source preview timeout must be within 120 seconds.')
        if (any(type(v) is not int or v < 1 for v in
                (max_output_bytes, max_cache_bytes, max_cache_entries))
                or max_output_bytes > MAX_OUTPUT_BYTES or max_cache_bytes > MAX_CACHE_BYTES
                or max_cache_entries > MAX_CACHE_ENTRIES):
            raise SourceViewerError('Invalid source preview cache/output budget.')
        if (type(max_storage_bytes) is not int or not 1 <= max_storage_bytes <= MAX_CACHE_BYTES
                or type(max_scan_samples) is not int or not 1 <= max_scan_samples <= MAX_AUDIO_SCAN_SAMPLES):
            raise SourceViewerError('Invalid exact audio storage/scan budget.')
        self.max_storage_bytes = max_storage_bytes
        self.max_scan_samples = max_scan_samples
        self.progress_callback = None
        self._audio_temporary = None
        self._audio_cache = OrderedDict()
        self._audio_eof = OrderedDict()
        self._cache_age = {}
        self._clock = 0
        self._pending_bytes = 0
        self._scan_samples = 0
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes
        self.max_cache_bytes = max_cache_bytes
        self.max_cache_entries = max_cache_entries
        self._temporary = None
        self._cache = OrderedDict()
        self._lock = threading.Lock()
        self._closed = False

    def _begin(self, cancel, *, deadline=None):
        local_deadline = time.monotonic() + self.timeout
        deadline = local_deadline if deadline is None else min(deadline, local_deadline)
        while not self._lock.acquire(timeout=.025):
            _check(cancel, deadline)
        try:
            _check(cancel, deadline)
            if self._closed:
                raise SourceViewerError('Source viewer backend is closed.')
            self._scan_samples = 0
            return deadline
        except BaseException:
            self._lock.release()
            raise

    def _directory(self):
        if self._temporary is None:
            self._temporary = tempfile.TemporaryDirectory(prefix='cutcat-source-viewer-')
        return Path(self._temporary.name)

    def _progress(self, message, **values):
        if self.progress_callback is not None:
            self.progress_callback(message, **values)

    def _touch(self, kind, key):
        self._clock += 1
        self._cache_age[kind, key] = self._clock

    def _drop(self, kind, key):
        cache = self._cache if kind == 'proxy' else self._audio_cache
        entry = cache.pop(key, None)
        if entry is not None:
            entry[0].unlink(missing_ok=True)
        self._cache_age.pop((kind, key), None)

    def _storage_bytes(self):
        return self._pending_bytes + sum(v[1] for cache in (self._cache, self._audio_cache)
                                         for v in cache.values())

    def _reserve(self, count, protected=()):
        # Both LRUs and ALL pending files share one hard session storage cap.
        if count < 0 or count + self._pending_bytes > self.max_storage_bytes:
            raise SourceViewerError('Exact audio preparation exceeded its shared storage byte budget.')
        protected = set(protected)
        while self._storage_bytes() + count > self.max_storage_bytes:
            choices = [(age, kind, key) for (kind, key), age in self._cache_age.items()
                       if (kind, key) not in protected]
            if not choices:
                raise SourceViewerError('Exact audio preparation exceeded its shared storage byte budget.')
            _, kind, key = min(choices, key=lambda value: value[0])
            self._drop(kind, key)
        self._pending_bytes += count

    def _purge_source(self, identity, *, all_entries=False):
        for key in list(self._audio_eof):
            if key[0].path == identity.path and (all_entries or key[0] != identity):
                del self._audio_eof[key]
        for kind, cache in (('proxy', self._cache), ('pcm', self._audio_cache)):
            for key in list(cache):
                if key[0].path == identity.path and (all_entries or key[0] != identity):
                    self._drop(kind, key)

    def _probe_source(self, session, identity, cancel, deadline):
        # Never serve a cached signal before checking the current source and
        # its self-contained container. Cache files are not replacement media.
        self._purge_source(identity)
        try:
            return _probe(session, identity, cancel, deadline)
        except SourceViewerCancelled:
            raise
        except SourceViewerError:
            self._purge_source(identity, all_entries=True)
            raise

    def _revalidate(self, session, identity, cancel, deadline):
        if _identity(session, cancel, deadline) != identity:
            raise SourceViewerError('Source changed; refresh the source viewer before continuing.')

    def verify_identity(self, session, expected_identity, cancel=None):
        deadline = self._begin(cancel)
        try:
            self._revalidate(deepcopy(session), expected_identity, cancel, deadline)
            return True
        except (OSError, TypeError) as exc:
            raise SourceViewerError('Cannot revalidate the selected source.') from exc
        finally:
            self._lock.release()

    def verify_linked_av_split_source(self, project_snapshot, clip_id, frame,
                                      cancel=None, *, deadline=None, expected_identity=None):
        """Narrow worker-only 1x linked split proof, not an overwrite plan.

        Prove all timestamps and complete native RGB16 legacy/global hashes
        for the original and both halves. The source union is charged once
        against 30s; the caller's common deadline includes surrounding identity
        and LUT observations. No source, project or renderer is modified.
        """
        from .linked_av_split import qualify_linked_av_split
        project = deepcopy(project_snapshot)
        qualify_linked_av_split(project, clip_id, frame)
        link = next(item for item in project.av_links if clip_id in item.clip_ids)
        video = next(item for item in project.clips if item.id == link.video_clip_id)
        session = SourceViewerSession(project, link.media_id)
        deadline = self._begin(cancel, deadline=deadline)
        try:
            _check(cancel, deadline)
            identity = _identity(session, cancel, deadline)
            if expected_identity is not None and (
                    (identity.path, identity.state, identity.sample_sha256)
                    != (expected_identity.path, expected_identity.state, expected_identity.sample_sha256)):
                raise SourceViewerError('Linked A/V split source identity changed during qualification')
            info = self._probe_source(session, identity, cancel, deadline)
            if not info['video'] or not info['audio']:
                raise SourceViewerError('Linked A/V split requires current video and audio streams')
            # Match media.probe, which supplies the renderer's actual offset.
            # The old source-viewer tick tolerance is not a PCM lattice proof.
            anchor = float(info['video'].get('start_time', 0))
            offset = float(info['audio'].get('start_time', anchor)) - anchor
            if (not math.isfinite(offset)
                    or Fraction(str(offset)) != Fraction(str(session.media.audio_offset))):
                raise SourceViewerError('Linked A/V split source audio offset changed; re-import the source')
            # At 1x the exact-map renderer samples floor(O)+k, including a
            # final partial source tail. int(E) can drop that last picture.
            start = video.source_in.__floor__()
            end = start + video.duration
            cut = start + frame - video.start
            ranges = [(start, end), (start, cut), (cut, end)]
            boundary = video.source_in + frame - video.start
            maps = [(video.source_in, video.source_out, video.duration),
                    (video.source_in, boundary, frame - video.start),
                    (boundary, video.source_out, video.end - frame)]
            legacy_ranges = [interval for interval, (origin, endpoint, duration)
                in zip(ranges, maps)
                if origin.denominator == 1 and endpoint - origin == duration]
            if end > min(session.total_frames, info['capacity']):
                raise SourceViewerError('Linked A/V split exceeds complete source-frame intervals')
            try:
                self._prove_cfr(session, identity, info, [(start, end)],
                    legacy_ranges, cancel, deadline)
                if len(legacy_ranges) != 3:
                    # Only actually used legacy paths require legacy/global
                    # equivalence. A fractional-origin exact-map clip must not
                    # be rejected for an unused trim-before-fps discrepancy.
                    keys = [self._timing_keys(session, identity, info, lo, hi,
                                             False, cancel, deadline)
                            for lo, hi in ranges]
                    if (any(len(value) != hi-lo for value, (lo, hi) in zip(keys, ranges))
                            or keys[0] != keys[1] + keys[2]):
                        raise SourceViewerError('Linked A/V split exact source frames do not partition at the cut')
            except SourceViewerCancelled:
                raise
            except SourceViewerError as exc:
                if str(exc) == 'Source overwrite requires actual video FPS to equal project FPS; inspection remains available.':
                    raise SourceViewerError('Linked A/V split requires actual video FPS to equal project FPS') from exc
                if str(exc) == 'Source range is incomplete or variable-rate; overwrite cannot be proven.':
                    raise SourceViewerError('Linked A/V split source range is incomplete or variable-rate') from exc
                raise
            self._revalidate(session, identity, cancel, deadline)
            _check(cancel, deadline)
            return identity
        except SourceViewerError:
            raise
        except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError, StopIteration) as exc:
            _check(cancel, deadline)
            raise SourceViewerError(f'Cannot prove linked A/V split source timing: {exc}') from exc
        finally:
            self._lock.release()

    def verify_overwrite_sources(self, session, expected_identity, project_snapshot,
                                 plan, cancel=None):
        return self._verify_edit_sources(session, expected_identity, project_snapshot,
                                         plan, cancel=cancel)

    def verify_insert_sources(self, session, expected_identity, project_snapshot,
                              plan, cancel=None):
        """Prove selected/split ranges, never decode unchanged downstream trims.

        Downstream source identities/containers are still freshly checked and
        revalidated within the same deadline. The immutable model plan guards
        their full local audio compiler equivalence before/after translation.
        """
        from .source_insert import SourceInsertPlan, insert_error_message
        if type(plan) is not SourceInsertPlan:
            raise SourceViewerError('Invalid source insert plan')
        try:
            return self._verify_edit_sources(session, expected_identity, project_snapshot,
                                             plan, cancel=cancel, insert=True)
        except SourceViewerError as exc:
            message = insert_error_message(str(exc))
            if message == str(exc):
                raise
            raise type(exc)(message) from exc

    def _verify_edit_sources(self, session, expected_identity, project_snapshot,
                             plan, cancel=None, *, insert=False):
        """Fail-closed schema-37 overwrite proof, performed entirely on a worker.

        All selected and affected picture-source intervals are decoded as frame
        timestamps, not inferred from average FPS or a couple of endpoints.
        Every selected/affected audio stream, including embedded picture audio, is
        decoded to exact f32 PCM. Known absent embedded tails remain silent;
        missing samples inside claimed ranges are never padded.
        In either case the merged aggregate extent is at most 30 seconds. Missing evidence,
        VFR, mixed rates, excessive preroll, partial tails and legacy trim phase
        ambiguity reject Apply without limiting independent source inspection.
        """
        request = deepcopy(session)
        project = deepcopy(project_snapshot)
        deadline = self._begin(cancel)
        try:
            if not request.matches(project) or plan.media_id != request.media_id:
                raise SourceViewerError('Source overwrite context changed.')
            selected = _identity(request, cancel, deadline)
            if selected != expected_identity:
                raise SourceViewerError('Source changed; refresh the source viewer before continuing.')
            sources = {request.media_id: request}
            intervals = {request.media_id: [(plan.source_in, plan.source_out)]}
            trim_ranges = {request.media_id: [(plan.source_in, plan.source_out)]}
            affected = set(plan.proof_clip_ids if insert else plan.affected_clip_ids)
            if affected:
                for clip in project.clips:
                    if clip.id not in affected:
                        continue
                    if clip.track != plan.track or clip.speed != 1 or clip.timing.source_in.denominator != 1:
                        raise SourceViewerError('Affected source timing is unsupported for overwrite proof.')
                    if clip.media_id not in sources:
                        sources[clip.media_id] = SourceViewerSession(project, clip.media_id)
                    start = int(clip.timing.source_in)
                    intervals.setdefault(clip.media_id, []).append((start, start + clip.duration))
                    trim_ranges.setdefault(clip.media_id, []).append((start, start + clip.duration))
                for remnant in plan.remnants:
                    original = next((c for c in project.clips if c.id == remnant.original_clip_id), None)
                    if original is None:
                        raise SourceViewerError('Affected source context changed.')
                    trim_ranges[original.media_id].append((remnant.source_in, remnant.source_in + remnant.duration))
            if insert:
                # No new source selection is made by a whole-clip translation.
                # Do fresh bounded identity/container checks, not a full media
                # decode that would consume the 30s selected/split proof cap.
                moved = {item.original_clip_id for item in plan.translations}
                for clip in project.clips:
                    if clip.id not in moved:
                        continue
                    if clip.media_id not in sources:
                        sources[clip.media_id] = SourceViewerSession(project, clip.media_id)
                    intervals.setdefault(clip.media_id, [])
                    trim_ranges.setdefault(clip.media_id, [])
            merged = {}
            for media_id, values in intervals.items():
                merged[media_id] = []
                for start, end in sorted(values):
                    if (type(start) is not int or type(end) is not int or not 0 <= start < end):
                        raise SourceViewerError('Overwrite source proof requires whole project frames.')
                    if merged[media_id] and start <= merged[media_id][-1][1]:
                        merged[media_id][-1] = (merged[media_id][-1][0], max(end, merged[media_id][-1][1]))
                    else:
                        merged[media_id].append((start, end))
            if plan.track < 2 and sum(end-start for values in merged.values()
                                     for start, end in values) > MAX_AUDITION_SECONDS * request.fps:
                raise SourceViewerError('Overwrite CFR proof is limited to 30 seconds of selected and affected source ranges; source inspection remains available.')
            if plan.track >= 2 and sum(end-start for values in merged.values()
                                      for start, end in values) > MAX_AUDITION_SECONDS * request.fps:
                raise SourceViewerError('Overwrite audio proof is limited to 30 seconds of selected and affected source ranges; source inspection remains available.')
            self._progress('Verifying current source identity and exact audio windows…')
            identities, inspected = {}, {}
            for media_id, source in sources.items():
                identity = selected if media_id == request.media_id else _identity(source, cancel, deadline)
                identities[media_id] = identity
                info = self._probe_source(source, identity, cancel, deadline)
                inspected[media_id] = info
                for start, end in merged[media_id]:
                    # Complete handles are checked again using current stream
                    # evidence before bounded video or PCM decoding.
                    if end > min(source.total_frames, info['capacity']):
                        raise SourceViewerError('Overwrite exceeds complete source-frame intervals.')
            # Estimate the SUM of missing from-origin scans before any proof.
            # A warm interval needs no decoder, but still needs identity/hash
            # checks. This budget is independent of the retained 30s range cap.
            requests = {}
            total_scan = 0
            for media_id, source in sources.items():
                info, identity = inspected[media_id], identities[media_id]
                windows = []
                if info['audio']:
                    if plan.track >= 2 and info['audio_capacity'] is not None and any(
                            end > info['audio_capacity'] for start, end in merged[media_id]):
                        raise SourceViewerError('Overwrite exceeds complete source-frame intervals.')
                    for start, end in merged[media_id]:
                        lo, hi, _, _, _ = self._audio_bounds(info, source, start, end,
                                                            pad=plan.track < 2)
                        if hi > lo:
                            windows.append((lo, hi))
                elif plan.track >= 2 and merged[media_id]:
                    raise SourceViewerError('Audio overwrite requires an audio source.')
                requests[media_id] = self._merge_windows(windows)
                missing = self._missing_windows(identity, info, requests[media_id], cancel, deadline,
                                                allow_absent=plan.track < 2)
                total_scan += max((hi for lo, hi in missing), default=0)
            if total_scan > self.max_scan_samples:
                raise SourceViewerError('Exact audio preparation exceeded its cumulative 30-minute origin-scan sample budget.')
            # Only after ALL source and resource preflights may proof begin.
            # Embedded audio on picture tracks gets the same exact PCM proof.
            for media_id, source in sources.items():
                identity, info = identities[media_id], inspected[media_id]
                if plan.track < 2 and merged[media_id]:
                    if not info['video']:
                        raise SourceViewerError('Picture overwrite requires a video source.')
                    self._prove_cfr(source, identity, info, merged[media_id],
                                    trim_ranges[media_id], cancel, deadline)
                if info['audio'] and requests[media_id]:
                    self._prepare_pcm(identity, info, source, requests[media_id], cancel, deadline,
                                      allow_absent=plan.track < 2)
            for media_id, source in sources.items():
                self._revalidate(source, identities[media_id], cancel, deadline)
            return True
        except SourceViewerError:
            raise
        except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError, StopIteration) as exc:
            _check(cancel, deadline)
            raise SourceViewerError(f'Cannot prove overwrite source timing: {exc}') from exc
        finally:
            self._lock.release()

    @staticmethod
    def _prove_cfr(session, identity, info, intervals, trim_ranges, cancel, deadline):
        from .timing import round_half_up
        stream = info['video']
        if any(Fraction(stream.get(key, '0')) != session.fps
               for key in ('avg_frame_rate', 'r_frame_rate')):
            raise SourceViewerError('Source overwrite requires actual video FPS to equal project FPS; inspection remains available.')
        time_base = Fraction(stream['time_base'])
        if not 0 < time_base < Fraction(1, 2) / session.fps:
            raise SourceViewerError('Source timestamp precision cannot prove project-frame boundaries.')

        def decoded(interval):
            raw = _run([ffprobe_binary(), '-v', 'error', '-select_streams', 'V:0',
                        '-read_intervals', interval, '-show_frames', '-show_entries',
                        'frame=best_effort_timestamp,duration,pkt_duration', '-of', 'json',
                        '-protocol_whitelist', 'file,pipe', '-format_whitelist', SAFE_INPUT_FORMATS, identity.path],
                       512 * 1024, cancel, deadline)
            frames = json.loads(raw).get('frames', [])
            if not isinstance(frames, list) or len(frames) > 3900:
                raise SourceViewerError('Source timestamp proof exceeded its frame budget.')
            return frames

        origin_frames = decoded('%+#1')
        if len(origin_frames) != 1 or int(origin_frames[0]['best_effort_timestamp']) * time_base != info['origin']:
            raise SourceViewerError('Source timestamp origin differs from its declared project-grid origin.')
        for start, end in intervals:
            begin_seconds = info['origin'] + max(Fraction(0), Fraction(start, 1) / session.fps - 1)
            end_seconds = info['origin'] + Fraction(end + 1, 1) / session.fps
            frames = decoded(f'{_number(begin_seconds)}%{_number(end_seconds)}')
            found, previous = {}, None
            for frame in frames:
                _check(cancel, deadline)
                stamp = int(frame['best_effort_timestamp']) * time_base - info['origin']
                index = round_half_up(stamp * session.fps)
                if previous is not None and stamp <= previous:
                    raise SourceViewerError('Source timestamps are not strictly increasing CFR frames.')
                previous = stamp
                if not start <= index < end:
                    continue
                if index in found or abs(stamp - Fraction(index, 1) / session.fps) > time_base:
                    raise SourceViewerError('Source frames do not follow the absolute project-frame grid.')
                ticks = int(frame.get('duration', frame.get('pkt_duration', 0)))
                if ticks <= 0 or abs(ticks * time_base - 1 / session.fps) > time_base:
                    raise SourceViewerError('Source frame durations do not prove constant-rate complete frames.')
                found[index] = stamp
            if set(found) != set(range(start, end)):
                raise SourceViewerError('Source range is incomplete or variable-rate; overwrite cannot be proven.')
        for start, end in sorted(set(trim_ranges)):
            # A matched CFR declaration alone does not guarantee the persisted
            # schema-37 trim-before-fps path selects the same endpoints. Ask
            # the real decoder/filter graph, including its timebase rounding,
            # and compare the complete output sequence of native-resolution
            # RGB16 SHA-256 hashes. No pixel files or scaled surrogate proof.
            global_frames = SourcePreviewBackend._timing_keys(
                session, identity, info, start, end, False, cancel, deadline)
            legacy_frames = SourcePreviewBackend._timing_keys(
                session, identity, info, start, end, True, cancel, deadline)
            if len(global_frames) != end-start or legacy_frames != global_frames:
                raise SourceViewerError('Legacy source trim phase cannot exactly represent this boundary; source inspection remains available.')

    @staticmethod
    def _timing_keys(session, identity, info, start, end, legacy, cancel, deadline):
        rate = f'{session.fps.numerator}/{session.fps.denominator}'
        temporal = [f"setpts=PTS-({_number(info['origin'])})/TB"]
        if legacy:
            # Deliberately match render._n and its original integral-1x branch.
            number = lambda value: format(float(value), '.12g')
            temporal += [f'trim=start={number(start / float(session.fps))}:duration={number((end-start) / float(session.fps))}',
                         'setpts=PTS-STARTPTS', f'fps=fps={rate}:start_time=0']
        else:
            temporal += [f'fps=fps={rate}:round=near:eof_action=pass',
                         f'trim=start_pts={start}:end_pts={end}', 'setpts=PTS-STARTPTS']
        filters = [*temporal, 'format=rgb48le']
        raw = _run([ffmpeg_binary(), '-v', 'error', '-xerror', '-nostdin', '-threads', '1',
                    '-filter_threads', '1', *_input_args(identity, info, session, start),
                    '-map', f"0:{info['video']['index']}", '-an', '-vf', ','.join(filters),
                    '-frames:v', str(end-start+1), '-fps_mode', 'passthrough', '-threads', '1',
                    '-pix_fmt', 'rgb48le', '-f', 'framehash', '-hash', 'sha256', 'pipe:1'],
                   512 * 1024, cancel, deadline)
        records = [line.split(',') for line in raw.decode('ascii').splitlines()
                   if line and not line.startswith('#')]
        if len(records) > 3601 or any(len(row) != 6 for row in records):
            raise SourceViewerError('Source temporal proof exceeded its frame budget.')
        return tuple((int(row[3]), int(row[4]), row[5].strip()) for row in records)

    def inspect_frame(self, session, cancel=None):
        request = deepcopy(session)
        deadline = self._begin(cancel)
        try:
            identity = _identity(request, cancel, deadline)
            info = self._probe_source(request, identity, cancel, deadline)
            start = request.cursor
            self._range(request, info, start, start + 1)
            if info['video']:
                raw = _run(_video_args(identity, info, request, start, start + 1),
                           request.width * request.height * 6, cancel, deadline)
                if len(raw) != request.width * request.height * 6:
                    raise SourceViewerError('Source frame is missing or incomplete.')
                pixels = _pixels(raw, request, cancel, deadline)
            else:
                pixels = bytes(request.width * request.height * 3)
            self._revalidate(request, identity, cancel, deadline)
            return SourceFrameResult(request.width, request.height, pixels, start,
                                     request.generation, identity, request.token)
        except SourceViewerError:
            raise
        except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError) as exc:
            raise SourceViewerError(f'Cannot inspect source: {exc}') from exc
        finally:
            self._lock.release()

    @staticmethod
    def _range(session, info, start, end):
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= min(session.total_frames, info['capacity'])):
            raise SourceViewerError('Source window exceeds complete source-frame intervals.')
        if Fraction(end - start, 1) / session.fps > MAX_AUDITION_SECONDS:
            raise SourceViewerError('Source audition is limited to 30 seconds.')

    @staticmethod
    def _merge_windows(windows):
        result = []
        for lo, hi in sorted(windows):
            if type(lo) is not int or type(hi) is not int or not 0 <= lo < hi:
                raise SourceViewerError('Exact audio windows require complete sample intervals.')
            if result and lo <= result[-1][1]:
                result[-1] = (result[-1][0], max(hi, result[-1][1]))
            else:
                result.append((lo, hi))
        if len(result) > MAX_PCM_CACHE_ENTRIES:
            raise SourceViewerError('Exact audio proof is limited to 32 sparse sample windows per source.')
        if sum(hi-lo for lo, hi in result) > MAX_AUDITION_SECONDS * 48000 + 1:
            raise SourceViewerError('Exact audio windows exceed the 30-second retained sample budget.')
        return result

    @staticmethod
    def _audio_bounds(info, session, start, end, *, pad=True):
        from .timing import sample_boundary, round_half_up
        count = sample_boundary(end, session.fps) - sample_boundary(start, session.fps)
        offset = Fraction(str(session.media.audio_offset))
        lo = round_half_up(max(Fraction(0), Fraction(start, 1) / session.fps - offset) * 48000)
        hi = round_half_up(max(Fraction(0), Fraction(end, 1) / session.fps - offset) * 48000)
        leading = min(count, max(0, round_half_up((offset - Fraction(start, 1) / session.fps) * 48000)))
        # A stream/container duration is a lower completeness claim, NOT its
        # decoded EOF. AAC can have real normalized tail samples beyond it.
        # Never use duration metadata to trim requested audio or invent silence.
        return lo, hi, leading, 0, count

    @staticmethod
    def _decoder_identity():
        located = shutil.which(ffmpeg_binary())
        if not located:
            raise SourceViewerError('Cannot locate the exact audio decoder.')
        path = Path(located).resolve(strict=True)
        return str(path), _state(path.stat())

    def _pcm_key(self, identity, info, lo, hi):
        return identity, (PCM_ALGORITHM, self._decoder_identity()), info['audio']['index'], lo, hi

    def _cached_window(self, identity, info, lo, hi, cancel, deadline):
        for key, entry in list(self._audio_cache.items()):
            if key[:3] != self._pcm_key(identity, info, lo, hi)[:3] or not key[3] <= lo < hi <= key[4]:
                continue
            path, size, digest = entry
            try:
                before = path.lstat()
                if not is_plain_file(before) or before.st_size != size:
                    raise OSError('invalid cached audio window')
                actual = hashlib.sha256()
                with path.open('rb') as stream:
                    descriptor = os.fstat(stream.fileno())
                    if not path_matches_descriptor(before, descriptor):
                        raise OSError('changed cached audio window')
                    while block := stream.read(65536):
                        _check(cancel, deadline)
                        actual.update(block)
                    if (not same_file_state(before, path.lstat())
                            or not same_file_state(descriptor, os.fstat(stream.fileno()))):
                        raise OSError('changed cached audio window')
                if actual.hexdigest() != digest:
                    raise OSError('corrupt cached audio window')
            except OSError:
                self._drop('pcm', key)
                continue
            self._audio_cache.move_to_end(key)
            self._touch('pcm', key)
            return key, entry
        return None

    def _known_eof(self, identity, info):
        key = self._pcm_key(identity, info, 0, 1)[:3]
        eof = self._audio_eof.get(key)
        if eof is not None:
            self._audio_eof.move_to_end(key)
        return eof

    def _effective_windows(self, identity, info, windows, *, allow_absent=False):
        if not windows:
            return []
        eof = self._known_eof(identity, info)
        if eof is None:
            return windows
        if any(hi > eof for lo, hi in windows):
            declared = info['audio_duration']
            if (not allow_absent or not info['video'] or declared is None
                    or eof < math.ceil(declared * 48000)):
                raise SourceViewerError('Source audio window is missing or incomplete.')
        return [(lo, min(hi, eof)) for lo, hi in windows if lo < eof]

    def _missing_windows(self, identity, info, windows, cancel, deadline, *, allow_absent=False):
        return [(lo, hi) for lo, hi in self._effective_windows(
                    identity, info, windows, allow_absent=allow_absent)
                if self._cached_window(identity, info, lo, hi, cancel, deadline) is None]

    def _prepare_pcm(self, identity, info, session, windows, cancel, deadline, *, allow_absent=False):
        """Decode normalized f32 from ORIGIN; discard prefix, retain sparse spans.

        No input -ss (including zero), decoder restart or approximate waveform
        is used. The three normalization filters exactly match audio_timing.py.
        End trim bounds normalized samples even if input timestamps have gaps.
        A complete worker operation shares one deadline and cumulative scan cap.
        """
        requested = self._merge_windows(windows)
        windows = self._effective_windows(identity, info, requested, allow_absent=allow_absent)
        missing, protected = [], []
        for lo, hi in windows:
            hit = self._cached_window(identity, info, lo, hi, cancel, deadline)
            if hit is None:
                missing.append((lo, hi))
            else:
                protected.append(('pcm', hit[0]))
        if not missing:
            if requested:
                self._progress('Reusing exact audio window from this viewer session.')
            return
        decoder_identity = self._decoder_identity()
        endpoint = max(hi for lo, hi in missing)
        if self._scan_samples + endpoint > self.max_scan_samples:
            raise SourceViewerError('Exact audio preparation exceeded its cumulative 30-minute origin-scan sample budget.')
        size = sum((hi-lo) * PCM_FRAME_BYTES for lo, hi in missing)
        self._reserve(size, protected)
        pending = []
        try:
            root = Path(self._audio_temporary.name) if self._audio_temporary else Path(tempfile.gettempdir())
            if shutil.disk_usage(root).free < size + 64 * 1024 * 1024:
                raise SourceViewerError('Insufficient temporary disk space for exact audio preparation.')
            if self._audio_temporary is None:
                self._audio_temporary = tempfile.TemporaryDirectory(prefix='cutcat-source-pcm-')
            directory = Path(self._audio_temporary.name)
            for lo, hi in missing:
                path = directory / (uuid.uuid4().hex + '.f32')
                pending.append((lo, hi, path, path.open('xb'), hashlib.sha256()))
            filters = ['asetpts=PTS-STARTPTS', 'aresample=48000:async=1:first_pts=0',
                       'aformat=sample_fmts=fltp:channel_layouts=stereo',
                       f'atrim=end_sample={endpoint}']
            command = [decoder_identity[0], '-v', 'error', '-xerror', '-nostdin', '-threads', '1',
                       '-filter_threads', '1', '-protocol_whitelist', 'file,pipe',
                       '-format_whitelist', SAFE_INPUT_FORMATS, '-i', identity.path,
                       '-map', f"0:{info['audio']['index']}", '-vn', '-af', ','.join(filters),
                       '-ac', '2', '-ar', '48000', '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1']
            scanned = 0
            next_progress = 0.0
            self._progress('Scanning source audio from origin: {scanned} / {target} seconds. Cold preparation is linear; Cancel is safe.',
                           scanned='0.0', target=f'{endpoint/48000:.1f}')
            with _Pipeline(cancel, deadline) as pipe:
                process, errors = pipe.spawn(command, stdin=subprocess.DEVNULL)
                while block := process.stdout.read(65536):
                    pipe.check()
                    old, scanned = scanned, scanned + len(block)
                    if scanned > endpoint * PCM_FRAME_BYTES or scanned % PCM_FRAME_BYTES:
                        raise SourceViewerError('Exact audio decoder exceeded its normalized sample budget.')
                    for lo, hi, path, stream, digest in pending:
                        first, last = max(old, lo*PCM_FRAME_BYTES), min(scanned, hi*PCM_FRAME_BYTES)
                        if first < last:
                            selected = block[first-old:last-old]
                            import numpy as np
                            if not np.isfinite(np.frombuffer(selected, dtype='<f4')).all():
                                raise SourceViewerError('Source audio contains non-finite samples.')
                            stream.write(selected)
                            digest.update(selected)
                    now = time.monotonic()
                    if now >= next_progress:
                        self._progress('Scanning source audio from origin: {scanned} / {target} seconds. Cold preparation is linear; Cancel is safe.',
                                       scanned=f'{scanned/PCM_FRAME_BYTES/48000:.1f}', target=f'{endpoint/48000:.1f}')
                        next_progress = now + .1
                pipe.finish(process, errors)
                pipe.check()
            decoded_end = scanned // PCM_FRAME_BYTES
            self._scan_samples += decoded_end
            # Fewer normalized samples than atrim's endpoint, with a clean
            # process exit, establishes actual decoder EOF. A duration header
            # alone never does: retain codec tail samples even beyond it.
            reached_eof = decoded_end < endpoint
            if reached_eof:
                declared = info['audio_duration']
                if (not allow_absent or not info['video'] or declared is None
                        or decoded_end < math.ceil(declared * 48000)):
                    raise SourceViewerError('Source audio window is missing or incomplete.')
            self._revalidate(session, identity, cancel, deadline)
            if self._decoder_identity() != decoder_identity:
                raise SourceViewerError('Exact audio decoder changed during preparation.')
            for lo, hi, path, stream, digest in pending:
                stream.close()
                expected = max(0, min(hi, decoded_end)-lo)*PCM_FRAME_BYTES
                if path.stat().st_size != expected:
                    raise SourceViewerError('Source audio window is missing or incomplete.')
            # Publish only complete, verified windows. Pending reservation is
            # transferred, never doubled or left outside the shared accounting.
            self._pending_bytes -= size
            size = 0
            for lo, hi, path, stream, digest in pending:
                hi = min(hi, decoded_end)
                if hi <= lo:
                    path.unlink()
                    continue
                key = identity, (PCM_ALGORITHM, decoder_identity), info['audio']['index'], lo, hi
                while len(self._audio_cache) >= MAX_PCM_CACHE_ENTRIES:
                    self._drop('pcm', next(iter(self._audio_cache)))
                self._audio_cache[key] = path, (hi-lo)*PCM_FRAME_BYTES, digest.hexdigest()
                self._touch('pcm', key)
            pending.clear()
            if reached_eof:
                eof_key = identity, (PCM_ALGORITHM, decoder_identity), info['audio']['index']
                self._audio_eof[eof_key] = decoded_end
                self._audio_eof.move_to_end(eof_key)
                while len(self._audio_eof) > MAX_PCM_CACHE_ENTRIES:
                    self._audio_eof.popitem(last=False)
            self._progress('Exact audio windows ready; original media remains the export source.')
        finally:
            self._pending_bytes -= size
            for lo, hi, path, stream, digest in pending:
                stream.close()
                path.unlink(missing_ok=True)

    def _audio_f32(self, identity, info, session, start, end, cancel, deadline, *, pad=True):
        lo, hi, leading, absent, count = self._audio_bounds(info, session, start, end, pad=pad)
        if not info['audio'] or hi <= lo or leading >= count:
            return bytes(count * PCM_FRAME_BYTES)
        self._prepare_pcm(identity, info, session, [(lo, hi)], cancel, deadline, allow_absent=pad)
        eof = self._known_eof(identity, info)
        if eof is not None and hi > eof:
            # _prepare_pcm has checked genuine EOF and all declared coverage.
            absent = max(0, hi - max(lo, eof))
            hi = max(lo, min(hi, eof))
            if hi <= lo:
                return bytes(count * PCM_FRAME_BYTES)
        hit = self._cached_window(identity, info, lo, hi, cancel, deadline)
        if hit is None:
            raise SourceViewerError('Exact audio window changed before use.')
        key, (path, size, digest) = hit
        before = path.lstat()
        with path.open('rb') as stream:
            descriptor = os.fstat(stream.fileno())
            if not is_plain_file(before) or not path_matches_descriptor(before, descriptor):
                raise SourceViewerError('Exact audio window changed before use.')
            retained = stream.read(size + 1)
            if (not same_file_state(before, path.lstat())
                    or not same_file_state(descriptor, os.fstat(stream.fileno()))
                    or len(retained) != size or hashlib.sha256(retained).hexdigest() != digest):
                self._drop('pcm', key)
                raise SourceViewerError('Exact audio window changed before use.')
        data = retained[(lo-key[3])*PCM_FRAME_BYTES:(hi-key[3])*PCM_FRAME_BYTES]
        if len(data) != (hi-lo)*PCM_FRAME_BYTES:
            raise SourceViewerError('Source audio window is missing or incomplete.')
        output = bytes(leading*PCM_FRAME_BYTES) + data + bytes(absent*PCM_FRAME_BYTES)
        missing = count*PCM_FRAME_BYTES-len(output)
        if missing > PCM_FRAME_BYTES:
            raise SourceViewerError('Source audio sample phase cannot be represented exactly.')
        return (output + bytes(max(0, missing)))[:count*PCM_FRAME_BYTES]

    def _audio(self, identity, info, session, start, end, cancel, deadline, *, pad=True):
        # Audition alone is int16/AAC. The cached/proven signal stays exact f32;
        # neither this conversion nor the lossy audition ever feeds the project.
        import numpy as np
        raw = self._audio_f32(identity, info, session, start, end, cancel, deadline, pad=pad)
        values = np.frombuffer(raw, dtype='<f4')
        if not np.isfinite(values).all():
            raise SourceViewerError('Source audio contains non-finite samples.')
        return np.clip(np.rint(values * np.float32(32768)), -32768, 32767).astype('<i2').tobytes()

    def audition(self, session, start_frame=None, end_frame=None, cancel=None):
        request = deepcopy(session)
        start = request.cursor if start_frame is None else start_frame
        end = (min(request.total_frames, start + math.floor(MAX_AUDITION_SECONDS * request.fps))
               if end_frame is None else end_frame)
        deadline = self._begin(cancel)
        pending = audio_path = None
        reservation = 0
        try:
            identity = _identity(request, cancel, deadline)
            info = self._probe_source(request, identity, cancel, deadline)
            self._range(request, info, start, end)
            key = (identity, request.fps, request.width, request.height, start, end)
            entry = self._cache.get(key)
            if entry and entry[0].is_file():
                path, size, digest = entry
                data = path.read_bytes() if path.stat().st_size <= self.max_output_bytes else b''
                if len(data) == size and hashlib.sha256(data).hexdigest() == digest:
                    self._revalidate(request, identity, cancel, deadline)
                    self._cache.move_to_end(key)
                    self._touch('proxy', key)
                    return SourceAuditionResult(path, start, end, request.generation,
                                                identity, request.token, request.width, request.height)
                self._drop('proxy', key)
            # Leave room for one maximally sized pending output. Audio staging is
            # separately bounded to 30s * 48000 * stereo * int16 = 5.76 MB.
            while self._cache and (len(self._cache) >= self.max_cache_entries
                    or sum(v[1] for v in self._cache.values()) + self.max_output_bytes > self.max_cache_bytes):
                self._drop('proxy', next(iter(self._cache)))
            from .timing import sample_boundary
            required = (min(self.max_output_bytes, self.max_cache_bytes)
                        + sample_boundary(end-start, request.fps) * 4 + 64 * 1024 * 1024)
            temporary_root = Path(self._temporary.name) if self._temporary else Path(tempfile.gettempdir())
            if shutil.disk_usage(temporary_root).free < required:
                raise SourceViewerError('Insufficient temporary disk space for the bounded source audition; no preview was allocated.')
            directory = self._directory()
            pending = directory / (uuid.uuid4().hex + '.mp4')
            audio_path = directory / (uuid.uuid4().hex + '.pcm')
            staged_size = min(self.max_output_bytes, self.max_cache_bytes) + (sample_boundary(end, request.fps)-sample_boundary(start, request.fps))*4
            self._reserve(staged_size)
            reservation = staged_size
            audio_path.write_bytes(self._audio(identity, info, request, start, end, cancel, deadline))
            self._encode(identity, info, request, start, end, audio_path, pending, cancel, deadline)
            self._revalidate(request, identity, cancel, deadline)
            size = pending.stat().st_size
            if size <= 0 or size > min(self.max_output_bytes, self.max_cache_bytes):
                raise SourceViewerError('Source preview exceeded its cache byte budget.')
            digest = hashlib.sha256(pending.read_bytes()).hexdigest()
            audio_path.unlink()
            audio_path = None
            self._pending_bytes -= reservation
            reservation = 0
            self._cache[key] = (pending, size, digest)
            self._touch('proxy', key)
            result = SourceAuditionResult(pending, start, end, request.generation,
                                         identity, request.token, request.width, request.height)
            pending = None
            return result
        except SourceViewerError:
            raise
        except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError) as exc:
            _check(cancel, deadline)
            raise SourceViewerError(f'Cannot audition source: {exc}') from exc
        finally:
            self._pending_bytes -= reservation
            if pending is not None:
                pending.unlink(missing_ok=True)
            if audio_path is not None:
                audio_path.unlink(missing_ok=True)
            self._lock.release()

    def _encode(self, identity, info, session, start, end, audio_path, output, cancel, deadline):
        rate = f'{session.fps.numerator}/{session.fps.denominator}'
        command = [ffmpeg_binary(), '-v', 'error', '-nostdin', '-threads', '1',
                   '-filter_threads', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                   '-s', f'{session.width}x{session.height}', '-r', rate,
                   '-color_range', 'pc', '-color_primaries', 'bt709', '-color_trc', 'bt709',
                   '-colorspace', 'rgb', '-protocol_whitelist', 'file,pipe', '-i', 'pipe:0',
                   '-f', 's16le', '-ar', '48000', '-ac', '2', '-i', str(audio_path),
                   '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-preset', 'ultrafast',
                   '-crf', '18', '-threads', '1', '-vf',
                   'scale=in_range=full:out_range=limited:out_color_matrix=bt709,format=yuv420p',
                   '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
                   '-color_range', 'tv', '-c:a', 'aac', '-b:a', '128k',
                   '-t', _number(Fraction(end-start, 1) / session.fps),
                   '-avoid_negative_ts', 'disabled',
                   '-movflags', '+frag_keyframe+empty_moov+default_base_moof', '-f', 'mp4', 'pipe:1']
        with output.open('xb') as target, _Pipeline(cancel, deadline) as pipe:
            encoder, encoder_errors = pipe.spawn(command, stdin=subprocess.PIPE)
            reader = pipe.capture(encoder.stdout, min(self.max_output_bytes, self.max_cache_bytes), target.write)
            decoder = None
            if info['video']:
                decoder, decoder_errors = pipe.spawn(
                    _video_args(identity, info, session, start, end), stdin=subprocess.DEVNULL)
            frame_size = session.width * session.height * 6
            blank = bytes(session.width * session.height * 3)
            for _ in range(end-start):
                pipe.check()
                if decoder:
                    raw = decoder.stdout.read(frame_size)
                    if len(raw) != frame_size:
                        pipe.check()
                        raise SourceViewerError('Source window has missing or incomplete video frames.')
                    pixels = _pixels(raw, session, cancel, deadline)
                else:
                    pixels = blank
                try:
                    encoder.stdin.write(pixels)
                except BrokenPipeError:
                    pipe.check()
                    raise
            encoder.stdin.close()
            if decoder:
                if decoder.stdout.read(1):
                    raise SourceViewerError('Source decoder exceeded its frame budget.')
                pipe.finish(decoder, decoder_errors)
            pipe.finish(encoder, encoder_errors)
            reader.join()
            pipe.check()

    def close(self):
        with self._lock:
            self._closed = True
            self._cache.clear()
            self._audio_cache.clear()
            self._audio_eof.clear()
            self._cache_age.clear()
            if self._audio_temporary is not None:
                self._audio_temporary.cleanup()
                self._audio_temporary = None
            if self._temporary is not None:
                self._temporary.cleanup()
                self._temporary = None

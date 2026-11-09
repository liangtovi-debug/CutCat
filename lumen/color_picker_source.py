"""Selected-source RGB16 snapshots at explicit grading pipeline stages.

Pickers never read a preview, proxy, composite, or LUT. Coordinates are those of the
fitted, padded pre-grade canvas, not the post-effect/transform viewer. Alpha and
active stabilization are deliberately unsupported rather than silently sampling
a different image. Spatial restoration runs on the exact normalized active
picture, before primary grading. Source indices use project-rate units.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time

import numpy as np

from .color import apply_grade, normalize_input
from .color_media import active_picture_size, source_rgb_filters, normalized_restoration
from .color_source import source_color_from_stream, validate_input_assignment
from .media import ffmpeg_binary, ffprobe_binary

MAX_PIXELS = 8_388_608
MAX_TIMEOUT = 120.0


def _has_alpha(pixel_format):
    # Cover packed YUV alpha as well as RGB/planar alpha. In particular vuya
    # passes source_color's vuy family, so the generic color validator alone
    # does not protect the picker from discarding its alpha plane.
    return pixel_format.startswith((
        'rgba', 'bgra', 'argb', 'abgr', 'yuva', 'gbrap', 'ya', 'pal', 'vuya', 'ayuv'))


class PickerSourceError(ValueError):
    """No faithful, current picker snapshot is available."""


class PickerSourceCancelled(PickerSourceError):
    """The pending snapshot was cancelled."""


def _selected(project, clip_id):
    clips = [c for c in project.clips if c.id == clip_id]
    if len(clips) != 1:
        raise PickerSourceError('Select exactly one existing clip.')
    clip = clips[0]
    media = [m for m in project.media if m.id == clip.media_id]
    if len(media) != 1 or not media[0].has_video:
        raise PickerSourceError('Color picking requires a video source.')
    return clip, media[0]


def _signature(project, clip_id):
    clip, media = _selected(project, clip_id)
    payload = [project.width, project.height, project.fps_num, project.fps_den,
               asdict(clip), asdict(media)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _source_identity(path):
    source = Path(path).expanduser().resolve(strict=True)
    stat = source.stat()
    if not source.is_file():
        raise PickerSourceError('Color picking requires a local media file.')
    return (str(source), stat.st_dev, stat.st_ino, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns)


@dataclass(frozen=True)
class PickerIdentity:
    clip_id: str
    timeline_frame: int
    project_signature: str
    source_identity: tuple
    # Populated only by the full-grade preview. Picker stages do not read LUTs.
    lut_identity: tuple | None = None


@dataclass(frozen=True)
class PickerSnapshot:
    frame: np.ndarray
    valid_mask: np.ndarray
    active_rect: tuple[int, int, int, int]
    identity: PickerIdentity
    source_frame: int
    source_time: Fraction
    restoration_applied: bool = False
    stage: str = "hue"

    def is_current(self, project, clip_id=None, timeline_frame=None):
        """Revalidate before displaying a stage or applying a picked color."""
        try:
            if clip_id is not None and clip_id != self.identity.clip_id:
                return False
            if timeline_frame is not None and timeline_frame != self.identity.timeline_frame:
                return False
            clip, media = _selected(project, self.identity.clip_id)
            return (_signature(project, self.identity.clip_id) == self.identity.project_signature
                    and _source_identity(media.path) == self.identity.source_identity
                    and (self.stage != 'grade_preview'
                         or _grade_lut_stat_identity(clip.grade) ==
                         (self.identity.lut_identity[:6] if self.identity.lut_identity else None)))
        except (OSError, ValueError, TypeError, AttributeError):
            return False


def _check(cancel, deadline):
    if cancel is not None and cancel.is_set():
        raise PickerSourceCancelled('Color picking cancelled.')
    if time.monotonic() >= deadline:
        raise PickerSourceError('Color picking timed out.')


def _grade_lut_stat_identity(grade):
    """Cheap GUI guard; content is fingerprinted on the capture worker only.

    Normal replacements and edits change inode/size/mtime/ctime. A filesystem
    that can secretly rewrite bytes while preserving every stat field cannot
    be detected by this lightweight display guard; recapturing hashes bytes.
    """
    if not (grade.enabled and grade.lut_path and grade.lut_strength > 0):
        return None
    return _source_identity(grade.lut_path)


def _grade_lut_identity(grade, cancel=None, deadline=float('inf')):
    """Bounded content/file identity of the LUT that the evaluator will read.

    A disabled grade or zero-strength LUT never reads a file, matching apply_grade.
    Check the file before and after hashing so replacing it cannot yield a
    falsely current mixed-version identity. This does not cache by timestamps.
    """
    if not (grade.enabled and grade.lut_path and grade.lut_strength > 0):
        return None
    _check(cancel, deadline)
    before = _grade_lut_stat_identity(grade)
    if Path(before[0]).suffix.lower() != '.cube':
        raise PickerSourceError('Only .cube 3D LUT files are supported')
    limit = 64 * 1024 * 1024
    if before[3] > limit:
        raise PickerSourceError('LUT exceeds 64 MiB')
    digest, size = hashlib.sha256(), 0
    with Path(before[0]).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            _check(cancel, deadline)
            size += len(block)
            if size > limit:
                raise PickerSourceError('LUT exceeds 64 MiB')
            digest.update(block)
    _check(cancel, deadline)
    if _source_identity(grade.lut_path) != before:
        raise PickerSourceError('Selected LUT changed during color preview.')
    return (*before, digest.hexdigest())


def _run(args, budget, cancel, deadline):
    """Bound output and lifetime; cancellation always kills and reaps children."""
    _check(cancel, deadline)
    process = None
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            process = subprocess.Popen(args, stdout=out, stderr=err)
            while process.poll() is None:
                _check(cancel, deadline)
                if os.fstat(out.fileno()).st_size > budget or os.fstat(err.fileno()).st_size > 65536:
                    raise PickerSourceError('Color picker decoder exceeded its output budget.')
                time.sleep(.025)
            _check(cancel, deadline)
            if os.fstat(out.fileno()).st_size > budget or os.fstat(err.fileno()).st_size > 65536:
                raise PickerSourceError('Color picker decoder exceeded its output budget.')
            if process.returncode:
                err.seek(0)
                raise PickerSourceError(err.read(65536)[-4000:].decode(errors='replace')
                                        or 'Cannot decode selected source frame.')
            out.seek(0)
            return out.read(budget)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()


def capture_picker_snapshot(project, clip_id, timeline_frame, cancel=None, timeout=30, *, stage="hue"):
    """Decode the selected clip at an integer timeline frame, without composites.

    Timeout is a total deadline (maximum 120 seconds), not a per-process limit.
    Returns immutable arrays and a state/source identity for UI stale-result
    rejection. At most one RGB16 frame is collected; grading uses bounded blocks.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
        raise PickerSourceError('Color picker timeout must be positive and finite.')
    if stage not in ("hue", "qualifier", "white_balance"):
        raise PickerSourceError("Unknown color sampling stage.")
    return _capture_snapshot(project, clip_id, timeline_frame, cancel, timeout, stage=stage)


def _capture_snapshot(project, clip_id, timeline_frame, cancel=None, timeout=30, *, stage):
    """Shared faithful ingress; only the grade-preview stage retains the full grade."""
    if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
        raise PickerSourceError('Color picker timeout must be positive and finite.')
    if stage not in ("hue", "qualifier", "white_balance", "grade_preview"):
        raise PickerSourceError("Unknown color sampling stage.")
    deadline = time.monotonic() + min(timeout, MAX_TIMEOUT)
    _check(cancel, deadline)
    try:
        signature = _signature(project, clip_id)
        live_clip, live_media = _selected(project, clip_id)
        clip, media = deepcopy(live_clip), deepcopy(live_media)
        width, height = project.width, project.height
        if (any(type(v) is not int or not 1 <= v <= 16384 for v in (width, height))
                or width * height > MAX_PIXELS):
            raise PickerSourceError('Color picker canvas exceeds its memory budget.')
        if (any(type(v) is not int or not 1 <= v <= 1_000_000 for v in (project.fps_num, project.fps_den))
                or not Fraction(1, 1000) <= project.fps <= 1000):
            raise PickerSourceError('Invalid project frame rate.')
        rate = f'{project.fps_num}/{project.fps_den}'
        fps = project.fps
        if type(timeline_frame) is not int or not clip.start <= timeline_frame < clip.end:
            raise PickerSourceError('Playhead must be inside the selected clip.')
        if clip.stabilization.enabled and clip.stabilization.strength > 0:
            raise PickerSourceError('Color picking is unavailable with active stabilization.')
        restoration_active = clip.restoration.enabled and any((
            clip.restoration.deblock, clip.restoration.denoise, clip.restoration.sharpen))
        identity = PickerIdentity(clip_id, timeline_frame, signature, _source_identity(media.path),
                                  _grade_lut_identity(clip.grade, cancel, deadline)
                                  if stage == 'grade_preview' else None)
        if clip.grade.input_encoding == 'apple_log_bt2020' or media.input_encoding == 'apple_log_bt2020':
            validate_input_assignment(media, clip.grade.input_encoding)
        source = identity.source_identity[0]
        probe = _run([ffprobe_binary(), '-v', 'error', '-select_streams', 'V:0',
                      '-show_entries', 'stream=width,height,pix_fmt,color_range,color_space,color_transfer,color_primaries:stream_side_data',
                      '-of', 'json', '-protocol_whitelist', 'file,pipe', source], 65536, cancel, deadline)
        data = json.loads(probe)
        if not isinstance(data, dict) or len(data.get('streams', [])) != 1:
            raise PickerSourceError('Color picking requires one decodable video stream.')
        stream = data['streams'][0]
        if (any(type(stream.get(key)) is not int or not 1 <= stream[key] <= 16384
                for key in ('width', 'height'))
                or stream['width'] * stream['height'] > 33_554_432):
            raise PickerSourceError('Color picker source exceeds its memory budget.')
        info = (source_color_from_stream(stream, input_encoding=clip.grade.input_encoding)
                if clip.grade.input_encoding == 'apple_log_bt2020' else source_color_from_stream(stream))
        # pal8 can carry transparency even without a named alpha plane.
        if _has_alpha(info.pixel_format):
            raise PickerSourceError('Color picking does not support transparent source formats.')
        validate_input_assignment(info, clip.grade.input_encoding)
        local_frame = timeline_frame - clip.start
        mapping = clip.timing
        source_frame = (mapping.source_in + local_frame * mapping.speed).__floor__()
        legacy = (mapping.speed == 1 and mapping.source_in.denominator == 1
                  and mapping.source_out - mapping.source_in == clip.duration)
        if legacy:
            # Preserve render's legacy timestamp phase and decimal formatting.
            number = lambda value: format(float(value), '.12g')
            temporal = ['setpts=PTS-STARTPTS',
                        f'trim=start={number(clip.source_in / float(fps))}:duration={number(clip.duration / float(fps))}',
                        'setpts=PTS-STARTPTS', f'fps=fps={rate}:start_time=0',
                        f'trim=start_frame={local_frame}:end_frame={local_frame + 1}']
        else:
            temporal = ['setpts=PTS-STARTPTS', f'fps=fps={rate}:start_time=0',
                        f'trim=start_frame={source_frame}:end_frame={source_frame + 1}']
        spatial = [*source_rgb_filters(source, timeout, PickerSourceError, info),
                   'scale=w=iw*sar:h=ih:in_color_matrix=bt709:out_color_matrix=bt709:out_range=full',
                   'format=rgb48le', 'setsar=1',
                   f'scale={width}:{height}:force_original_aspect_ratio=decrease:in_color_matrix=bt709:out_color_matrix=bt709:out_range=full',
                   'format=rgb48le']
        _check(cancel, deadline)
        # Measure the requested frame too: a source may change dimensions midstream.
        aw, ah = active_picture_size(source, [*temporal, *spatial], width, height, deadline - time.monotonic(),
                                     cancel, PickerSourceError, PickerSourceCancelled)
        if restoration_active:
            # The renderer measures restoration geometry at source origin.
            # Refuse changing geometry rather than sample a different kernel ROI.
            origin_size = active_picture_size(source, spatial, width, height,
                deadline - time.monotonic(), cancel, PickerSourceError, PickerSourceCancelled)
            if origin_size != (aw, ah):
                raise PickerSourceError('Color picking cannot match restoration with changing source geometry.')
        filters = [*temporal, *spatial, f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black', 'setsar=1']
        raw = _run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-threads', '1',
                    '-protocol_whitelist', 'file,pipe', '-i', source, '-map', '0:V:0', '-an',
                    '-vf', ','.join(filters), '-frames:v', '1', '-threads', '1',
                    '-pix_fmt', 'rgb48le', '-f', 'rawvideo', 'pipe:1'], width * height * 6, cancel, deadline)
        if len(raw) != width * height * 6:
            raise PickerSourceError('Selected source frame is missing or incomplete.')
        rgb16 = np.frombuffer(raw, dtype='<u2').reshape(-1, 3)
        frame = np.empty((height * width, 3), dtype=np.float32)
        # The picker source coordinate may repeat under slow motion while
        # the window continues to move on the output timeline frame grid.
        from .window_animation import evaluate_grade
        grade = deepcopy(evaluate_grade(clip.grade, local_frame))
        # Disable all creative grading without disabling input normalization
        # or restoration. WB sampling must never feed its own output back.
        if stage == "white_balance":
            grade.enabled = False
        if stage == "hue":
            grade.hue_hue_curve = [[0., 0.], [1., 0.]]
            grade.hue_sat_curve = [[0., 1.], [1., 1.]]
        if stage != 'grade_preview':
            grade.qualifier.enabled = False
            grade.lut_path, grade.lut_strength = '', 0.
        if stage == 'grade_preview':
            if identity.lut_identity is not None:
                # Pin evaluation to the resolved file whose identity was
                # captured. Following the user's symlink again would permit
                # A -> B -> A retargeting to render B with a current A identity.
                # Final checks still use clip.grade's original user path.
                grade.lut_path = identity.lut_identity[0]
            # Keep full native HWC topology for spatial qualifier windows and
            # morphology/smoothing. Flattened grade blocks would invent new
            # neighbors or discard the canvas coordinate system. Ingress
            # temporaries are bounded; the creative evaluator is called once.
            for start in range(0, len(frame), 65536):
                _check(cancel, deadline)
                frame[start:start + 65536] = (
                    rgb16[start:start + 65536].astype(np.float32) / np.float32(65535))

            class Guard:
                def is_set(self):
                    _check(cancel, deadline)
                    return False

            restore = (normalized_restoration(clip.restoration, (aw, ah), width,
                       height, Guard(), PickerSourceCancelled)
                       if restoration_active else None)

            def after_normalization(normalized):
                _check(cancel, deadline)
                result = restore(normalized) if restore is not None else normalized
                _check(cancel, deadline)
                return result

            _check(cancel, deadline)
            frame = apply_grade(frame.reshape(height, width, 3), grade,
                                normalized_transform=after_normalization,
                                cancel=lambda: _check(cancel, deadline))
            _check(cancel, deadline)
        elif restoration_active:
            # Spatial kernels must see the whole HWC active picture, not the
            # flat grading chunks. Normalize exactly once; never round-trip
            # through RGB8 or the quantized graded-segment delivery boundary.
            class Guard:
                def is_set(self):
                    _check(cancel, deadline)
                    return False
            guard = Guard()
            # Reuse the output buffer for normalization, with bounded transfer
            # temporaries and cancellation even for large sRGB/linear frames.
            for start in range(0, len(frame), 65536):
                _check(cancel, deadline)
                frame[start:start + 65536] = normalize_input(
                    rgb16[start:start + 65536].astype(np.float32) / np.float32(65535),
                    grade.input_encoding)
            normalized = frame.reshape(height, width, 3)
            _check(cancel, deadline)
            restored = normalized_restoration(clip.restoration, (aw, ah), width,
                height, guard, PickerSourceCancelled)(normalized)
            grade.input_encoding = 'rec709'
            samples = restored.reshape(-1, 3)
            for start in range(0, len(frame), 65536):
                _check(cancel, deadline)
                frame[start:start + 65536] = apply_grade(samples[start:start + 65536], grade)
            del normalized, restored, samples
        else:
            for start in range(0, len(frame), 65536):
                _check(cancel, deadline)
                frame[start:start + 65536] = apply_grade(rgb16[start:start + 65536].astype(np.float32) / np.float32(65535), grade)
        left, top = (width - aw) // 2, (height - ah) // 2
        mask = np.zeros((height, width), dtype=np.bool_)
        mask[top:top + ah, left:left + aw] = True
        # Bytes-backed arrays cannot be made writeable by a caller.
        frame = np.frombuffer(frame.tobytes(), dtype=np.float32).reshape(height, width, 3)
        mask = np.frombuffer(mask.tobytes(), dtype=np.bool_).reshape(height, width)
        snapshot = PickerSnapshot(frame, mask, (left, top, aw, ah), identity, source_frame, Fraction(source_frame, 1) / fps, restoration_active, stage)
        _check(cancel, deadline)
        if stage == 'grade_preview' and _grade_lut_identity(clip.grade, cancel, deadline) != identity.lut_identity:
            raise PickerSourceError('Selected LUT changed during color preview.')
        if not snapshot.is_current(project):
            raise PickerSourceError('Selected source or clip changed during color picking.')
        _check(cancel, deadline)
        return snapshot
    except PickerSourceError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise PickerSourceError(f'Cannot prepare color picker source: {exc}') from exc

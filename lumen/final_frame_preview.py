"""Bounded final H.264/preview-composition SDR still, before video encoding.

This is deliberately separate from RenderEngine: no playback cache, complete
timeline render, audio graph, export or project-schema changes. Only pictures
active at the requested frame are decoded/graded. A decoder may still have to
traverse earlier source frames to preserve the renderer's timestamp phase.

The legacy renderer remains the independent reference. Its final 8-bit YUV420
composition order/precision is reproduced here, not replaced by an RGB blend.
Input normalization and full-canvas grading use the existing one-frame ingress;
its float output crosses the SAME round/clamp RGB16 boundary as grade_segment.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import time

import numpy as np

from .color_picker_source import (
    _grade_lut_identity, _source_identity, _run, PickerSourceError,
    PickerSourceCancelled,
)
from .frame_grade_preview import capture_grade_preview_snapshot
from .media import ffmpeg_binary, ffprobe_binary

MAX_WIDTH = 1920
MAX_HEIGHT = 1080
MAX_TIMEOUT = 120.0
FALLBACK = 'Use the existing Build preview for this project/frame.'
SELF_CONTAINED_ERROR = ('Final-frame preview requires verified self-contained sources; '
                        'manifests and external-reference formats are unsupported.')


class FinalFramePreviewError(ValueError):
    pass


class FinalFramePreviewCancelled(FinalFramePreviewError):
    pass


@dataclass(frozen=True)
class FinalFrameIdentity:
    project_signature: str
    timeline_frame: int
    dependencies: tuple


@dataclass(frozen=True)
class FinalFrameWork:
    """Emitted picture work, NOT a claim of random-access source decoding."""
    active_clip_ids: tuple[str, ...]
    source_frames: tuple[int, ...]
    graded_frames: int
    sticker_frames: int
    composite_frames: int
    intermediate_bytes: int
    source_decode_may_traverse_earlier_frames: bool = True


@dataclass(frozen=True)
class FinalFrameSnapshot:
    width: int
    height: int
    rgb_bytes: bytes
    yuv420p: bytes
    identity: FinalFrameIdentity
    work: FinalFrameWork
    stage: str = 'final_composite_sdr'


def project_signature(project):
    """Pure in-memory identity; safe for UI generation guards (no filesystem)."""
    return hashlib.sha256(json.dumps(asdict(project), sort_keys=True,
                                    default=str).encode()).hexdigest()


def _check(cancel, deadline):
    if cancel is not None and cancel.is_set():
        raise FinalFramePreviewCancelled('Final-frame preview cancelled.')
    if time.monotonic() >= deadline:
        raise FinalFramePreviewError('Final-frame preview timed out. ' + FALLBACK)


class _Guard:
    def __init__(self, cancel, deadline):
        self.cancel, self.deadline = cancel, deadline

    def is_set(self):
        _check(self.cancel, self.deadline)
        return False


def _deadline(timeout):
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0):
        raise FinalFramePreviewError('Final-frame timeout must be positive and finite.')
    return time.monotonic() + min(timeout, MAX_TIMEOUT)


def _plan(project, frame):
    # validate may canonicalize master gain; callers supply a detached project.
    project.validate()
    if (project.width > MAX_WIDTH or project.height > MAX_HEIGHT
            or project.width % 2 or project.height % 2):
        raise FinalFramePreviewError('Final-frame preview requires an even SDR canvas '
                                     'no larger than 1920 x 1080. ' + FALLBACK)
    if type(frame) is not int or not 0 <= frame < project.duration_frames:
        raise FinalFramePreviewError('Choose an existing integer timeline frame. ' + FALLBACK)
    from .video_transition import plan_video_transitions
    if any(p.start <= frame < p.end for p in plan_video_transitions(project)):
        raise FinalFramePreviewError('Final-frame preview does not support an active '
                                     'video transition. ' + FALLBACK)
    clips = tuple(sorted((c for c in project.clips if c.track < 2 and c.start <= frame < c.end),
                         key=lambda c: (c.track, c.start, c.id)))
    media = {m.id: m for m in project.media}
    for clip in clips:
        if clip.stabilization.enabled and clip.stabilization.strength > 0:
            raise FinalFramePreviewError('Final-frame preview does not support active '
                                         'stabilization. ' + FALLBACK)
        if (clip.grade.input_encoding == 'apple_log_bt2020'
                or media[clip.media_id].input_encoding == 'apple_log_bt2020'):
            raise FinalFramePreviewError('Final-frame preview currently supports original '
                                         'SDR input, not Apple Log. ' + FALLBACK)
    overlays = tuple(o for o in project.overlays if o.enabled and o.start <= frame < o.end)
    captions = tuple(c for c in project.captions if c.start <= frame < c.end)
    return clips, overlays, captions


def _dependencies(project, frame, guard, deadline):
    """Worker-only content verification of active LUTs/assets and source identity.

    Media stat identities follow the existing strict one-frame ingress; LUTs,
    sticker manifests and ALL pack frames are content-verified on every pass.
    No audio file is opened. Unrelated tail media/assets are not inspected.
    """
    clips, overlays, captions = _plan(project, frame)
    media = {m.id: m for m in project.media}
    dependencies, assets, checked_formats = [], {}, {}
    for clip in clips:
        guard.is_set()
        identity = _source_identity(media[clip.media_id].path)
        if identity not in checked_formats:
            checked_formats[identity] = _self_contained_source(identity[0], guard, deadline)
        dependencies.append(('source', clip.id, identity, checked_formats[identity]))
        dependencies.append(('lut', clip.id, _grade_lut_identity(clip.grade, guard, deadline)))
    from .sticker_assets import verify_sticker
    for overlay in overlays:
        guard.is_set()
        asset = verify_sticker(overlay.asset_path, overlay.asset_sha256, cancel=guard)
        assets[overlay.id] = asset
        dependencies.append(('sticker', overlay.id, _source_identity(overlay.asset_path),
                             asset.digest, tuple(asset.frame_sha256),
                             tuple(_source_identity(p) for p in asset.frames)))
    if captions:
        from .caption_render import font_identity
        # font selection and stat checks happen off the GUI thread; copied font
        # bytes are immutable for this capture and rechecked before publication.
        identity = font_identity(project.caption_style, check=guard.is_set)
        dependencies.append(('font', identity, _source_identity(identity[0])))
    guard.is_set()
    return tuple(dependencies), assets


def _self_contained_source(path, guard, deadline):
    """Reject indirect demuxers BEFORE they can follow untracked media inputs.

    The shared source-viewer allowlist admits plain containers/images, never
    concat/HLS/DASH/image sequences. Protocol limits alone would still permit
    local playlists whose children can change without touching the manifest.
    This is metadata inspection only; no audio stream is decoded or processed.
    """
    from .source_viewer import SAFE_INPUT_FORMATS
    try:
        raw = _run([ffprobe_binary(), '-v', 'error', '-show_entries', 'format=format_name',
                    '-of', 'json', '-protocol_whitelist', 'file,pipe',
                    '-format_whitelist', SAFE_INPUT_FORMATS, str(path)], 65536, guard, deadline)
        data = json.loads(raw)
        formats = tuple(sorted(set(data.get('format', {}).get('format_name', '').split(','))))
        if not formats or not set(formats) <= set(SAFE_INPUT_FORMATS.split(',')):
            raise ValueError('Unsupported source container')
        guard.is_set()
        return formats
    except (PickerSourceError, ValueError, TypeError, AttributeError) as exc:
        guard.is_set()
        raise FinalFramePreviewError(SELF_CONTAINED_ERROR + ' ' + FALLBACK) from exc


def verify_final_frame_preview(snapshot, project, timeline_frame, cancel=None, timeout=30):
    """Worker-only revalidation. Never call on the GUI thread."""
    deadline = _deadline(timeout)
    guard = _Guard(cancel, deadline)
    try:
        guard.is_set()
        if (not isinstance(snapshot, FinalFrameSnapshot)
                or timeline_frame != snapshot.identity.timeline_frame
                or project_signature(project) != snapshot.identity.project_signature):
            return False
        detached = deepcopy(project)
        if project_signature(detached) != snapshot.identity.project_signature:
            return False
        dependencies, _ = _dependencies(detached, timeline_frame, guard, deadline)
        return (dependencies == snapshot.identity.dependencies
                and project_signature(project) == snapshot.identity.project_signature)
    except FinalFramePreviewCancelled:
        raise
    except Exception:
        guard.is_set()
        return False


def _raw_input(path, project, pixel_format):
    return ['-f', 'rawvideo', '-pixel_format', pixel_format,
            '-video_size', f'{project.width}x{project.height}',
            '-framerate', f'{project.fps_num}/{project.fps_den}',
            '-color_range', 'pc', '-color_primaries', 'bt709',
            '-color_trc', 'bt709', '-colorspace', 'rgb',
            '-protocol_whitelist', 'file,pipe', '-i', str(path)]


def _picture_graph(project, frame, clips, inputs, directory, guard, source_frames):
    """Mirror only the frozen renderer's H.264 visual branches, at one frame."""
    rate = f'{project.fps_num}/{project.fps_den}'
    width, height = project.width, project.height
    graph = [f'color=c=black:s={width}x{height}:r={rate},trim=end_frame=1,'
             'setsar=1,format=yuv420p[base]']
    video = 'base'
    written = 0
    from .overlay_render import effect_filters
    from .vignette_shape import has_active_shaped_vignette
    from .clip_transform import transform_filters
    from .clip_composite import clip_composite_filters
    for index, clip in enumerate(clips):
        guard.is_set()
        snapshot = capture_grade_preview_snapshot(project, clip.id, frame, guard,
                                                 max(.001, guard.deadline - time.monotonic()))
        source_frames.append(snapshot.source_frame)
        # EXACT grade_segment boundary, including full HWC animation/restoration.
        encoded = np.rint(np.clip(snapshot.frame, 0, 1) * np.float32(65535)).astype('<u2')
        # FFV1 grade caches expose planar GBR16. Feeding the identical planar
        # bytes pins the same input format negotiation without an encoder/cache.
        raw = encoded[..., [1, 2, 0]].transpose(2, 0, 1).tobytes()
        path = directory / f'grade-{index}.gbrp16'
        path.write_bytes(raw)
        written += len(raw)
        del snapshot, encoded, raw
        inputs += _raw_input(path, project, 'gbrp16le')
        shaped = has_active_shaped_vignette(clip.effects)
        composite = bool(clip.motion_keyframes) or not clip.composite.is_legacy()
        if composite and not any(e.enabled and e.amount > 0 for e in clip.effects):
            vf = ['setpts=PTS-STARTPTS', f'fps=fps={rate}:start_time=0', 'format=gbrp16le', 'setsar=1']
        elif shaped or composite:
            vf = ['setpts=PTS-STARTPTS', f'fps=fps={rate}:start_time=0', 'format=gbrp', 'setsar=1']
        else:
            vf = ['setpts=PTS-STARTPTS', f'fps=fps={rate}:start_time=0',
                  f'scale={width}:{height}:force_original_aspect_ratio=decrease:in_color_matrix=bt709:out_color_matrix=bt709:out_range=tv',
                  f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black', 'setsar=1']
        filters = effect_filters(clip.effects)
        if composite and not shaped:
            ordered = []
            for item in filters:
                ordered.extend(['format=rgb24', item, 'format=gbrp']
                               if item.startswith('vignette=') else [item])
            filters = ordered
        vf += filters
        next_video = f'over{index}'
        if composite:
            main, layer, over = clip_composite_filters(clip.composite, clip.transform, width, height,
                                                      high_bit=False, motion_keys=clip.motion_keyframes)
            # Keep the actual expression and its floating arithmetic. Replacing
            # only the trusted N token preserves clip-local time at arbitrary
            # seeks without generating all preceding compositor frames.
            layer = re.sub(r'\bN\b', str(frame - clip.start), layer)
            vf += [layer, 'setpts=PTS+0']
            graph += [f'[{index}:V:0]' + ','.join(vf) + f'[v{index}]',
                      f'[{video}]{main}[compositebase{index}]']
            first, comma, tail = over.partition(',')
            graph += [f'[compositebase{index}][v{index}]' + first + ":enable='1'" + comma + tail + f'[{next_video}]']
        else:
            vf += transform_filters(clip.transform, width, height)
            if shaped:
                vf += ['scale=out_color_matrix=bt709:in_range=full:out_range=tv']
            vf += ['format=yuv420p', 'setpts=PTS+0']
            graph += [f'[{index}:V:0]' + ','.join(vf) + f'[v{index}]',
                      f"[{video}][v{index}]overlay=eof_action=pass:repeatlast=0:shortest=0:enable='1'[{next_video}]"]
        video = next_video
    return graph, video, written


def capture_final_frame_preview(project, timeline_frame, cancel=None, timeout=120):
    """Return one immutable original-canvas final SDR/no-audio snapshot.

    Uses at most two graded frames and one sticker composite; no MP4 or full
    clip/timeline intermediates. All temporary data is request-owned and removed
    on success, cancellation, deadline or failure. UI publication needs its own
    generation guard and worker revalidation, because edits can follow capture.
    """
    deadline = _deadline(timeout)
    guard = _Guard(cancel, deadline)
    guard.is_set()
    try:
        signature = project_signature(project)
        project = deepcopy(project)
        if project_signature(project) != signature:
            raise FinalFramePreviewError('The project changed while detaching the requested frame. Refresh again.')
        clips, overlays, captions = _plan(project, timeline_frame)
        dependencies, assets = _dependencies(project, timeline_frame, guard, deadline)
        from .clip_composite import (verify_clip_composite_support,
                                     verify_clip_motion_support,
                                     ensure_composite_rgb8_blur_support)
        composite = any(c.motion_keyframes or not c.composite.is_legacy() for c in clips)
        if composite:
            verify_clip_composite_support(False, max(.001, deadline-time.monotonic()), guard,
                                          FinalFramePreviewError, FinalFramePreviewCancelled)
            if any(c.motion_keyframes for c in clips):
                verify_clip_motion_support(False, max(.001, deadline-time.monotonic()), guard,
                                           FinalFramePreviewError, FinalFramePreviewCancelled)
        # The full compiler pins this process-global SIMD choice from the
        # whole project's metadata. Keep it even for an inactive tail clip:
        # otherwise RGB sticker conversion can differ by one chroma code.
        scalar_blur = any((c.motion_keyframes or not c.composite.is_legacy())
                          and any(e.enabled and e.kind == 'blur' and e.amount > 0 for e in c.effects)
                          for c in project.clips if c.track < 2)
        if scalar_blur:
            ensure_composite_rgb8_blur_support(max(.001, deadline-time.monotonic()), guard,
                                               FinalFramePreviewError, FinalFramePreviewCancelled)
        with tempfile.TemporaryDirectory(prefix='cutcat-final-frame-') as name:
            directory = Path(name)
            inputs, source_frames = [], []
            graph, video, written = _picture_graph(project, timeline_frame, clips, inputs,
                                                   directory, guard, source_frames)
            if overlays:
                from .overlay_render import compose_frame, FrameImageCache
                limited = deepcopy(project)
                limited.overlays = list(overlays)
                image = compose_frame(limited, timeline_frame, assets, FrameImageCache(cancel=guard))
                path = directory / 'stickers.rgba'
                raw = bytes(image.constBits())
                path.write_bytes(raw)
                written += len(raw)
                del image, raw
                inputs += _raw_input(path, project, 'rgba')
                rate = f'{project.fps_num}/{project.fps_den}'
                graph += [f'[{len(clips)}:V:0]setpts=PTS-STARTPTS,fps=fps={rate}:start_time=0,'
                          'format=rgba,scale=in_color_matrix=bt709:out_color_matrix=bt709:in_range=full:out_range=tv,'
                          'format=yuva420p[stickers]',
                          f'[{video}][stickers]overlay=eof_action=pass:repeatlast=0:shortest=0:alpha=straight[stickered]']
                video = 'stickered'
            if captions:
                from .caption_render import caption_filters
                limited = deepcopy(project)
                limited.captions = list(captions)
                filters = caption_filters(limited, directory, check=guard.is_set)
                filters = [f.replace('gte(n,', f'gte({timeline_frame},').replace('lt(n,', f'lt({timeline_frame},')
                           for f in filters]
                graph += [f'[{video}]' + ','.join(filters) + '[captioned]']
                video = 'captioned'
            graph += [f'[{video}]trim=end_frame=1,setpts=PTS-STARTPTS,format=yuv420p,'
                      'setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709[vout]']
            graph_path = directory / 'frame.ffgraph'
            graph_path.write_text(';'.join(graph), encoding='utf-8')
            guard.is_set()
            yuv_size = project.width * project.height * 3 // 2
            yuv = _run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-threads', '1',
                        '-filter_complex_threads', '1', *(['-cpuflags', '0'] if scalar_blur else []),
                        *inputs, '-/filter_complex', str(graph_path), '-map', '[vout]', '-an',
                        '-frames:v', '1', '-threads', '1', '-pix_fmt', 'yuv420p',
                        '-f', 'rawvideo', 'pipe:1'], yuv_size, guard, deadline)
            if len(yuv) != yuv_size:
                raise FinalFramePreviewError('Final composition returned an incomplete frame. ' + FALLBACK)
            # Display conversion is explicitly after the authoritative final
            # pre-encoding YUV boundary, so it cannot change graph negotiation.
            display_path = directory / 'final.yuv'
            display_path.write_bytes(yuv)
            written += len(yuv)
            rgb = _run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-threads', '1',
                        '-f', 'rawvideo', '-pixel_format', 'yuv420p',
                        '-video_size', f'{project.width}x{project.height}',
                        '-color_range', 'tv', '-colorspace', 'bt709',
                        '-protocol_whitelist', 'file,pipe', '-i', str(display_path), '-an',
                        '-vf', 'scale=in_color_matrix=bt709:out_color_matrix=bt709:in_range=tv:out_range=full,format=rgb24',
                        '-frames:v', '1', '-threads', '1', '-pix_fmt', 'rgb24',
                        '-f', 'rawvideo', 'pipe:1'], project.width * project.height * 3, guard, deadline)
            if len(rgb) != project.width * project.height * 3:
                raise FinalFramePreviewError('Final display conversion returned an incomplete frame.')
            after, _ = _dependencies(project, timeline_frame, guard, deadline)
            if after != dependencies:
                raise FinalFramePreviewError('A final-frame dependency changed during capture. Refresh again.')
            guard.is_set()
            return FinalFrameSnapshot(project.width, project.height, rgb, yuv,
                FinalFrameIdentity(signature, timeline_frame, dependencies),
                FinalFrameWork(tuple(c.id for c in clips), tuple(source_frames), len(clips),
                               int(bool(overlays)), 1, written))
    except FinalFramePreviewError:
        raise
    except PickerSourceCancelled as exc:
        raise FinalFramePreviewCancelled('Final-frame preview cancelled.') from exc
    except Exception as exc:
        guard.is_set()
        raise FinalFramePreviewError(f'Cannot prepare final-frame preview: {exc} {FALLBACK}') from exc


capture_final_frame_snapshot = capture_final_frame_preview

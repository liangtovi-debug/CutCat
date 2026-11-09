"""One FFmpeg timeline compiler for playback, still previews, and delivery."""
from __future__ import annotations
from .audio_components import clip_emits_source_audio
from dataclasses import asdict, replace
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from .media import ffmpeg_available, ffmpeg_binary, ffprobe_binary, probe

class RenderError(RuntimeError):
    pass

class RenderCancelled(RenderError):
    pass


def _cancel_check(cancel):
    def check():
        if cancel is not None and cancel.is_set():
            raise RenderCancelled('Render cancelled.')
    return check

def _n(value):
    value = float(value)
    if not math.isfinite(value):
        raise RenderError('Non-finite timeline or grade value.')
    return format(value, '.12g')


def _composite_filter_threads(project):
    """Bound only the verified animated graph; complex/legacy paths stay scalar.

    This is a per-render value, never mutable engine state. Effects and extra
    layers retain their established filter scheduling until independently
    verified. Decoder/encoder threading is deliberately unchanged.
    """
    clips = [clip for clip in project.clips if clip.track < 2]
    if (not any(clip.motion_keyframes for clip in clips)
            or project.captions or project.overlays or getattr(project, 'video_transitions', [])
            or any(clip.effects or clip.grade.qualifier.window_keyframes for clip in clips)):
        return 1
    available = os.cpu_count() or 1
    return 4 if available >= 4 else 2 if available >= 2 else 1


def _lut_fingerprint(path):
    """Bounded content identity: same-size edits can share Windows timestamps."""
    path = Path(path).expanduser().resolve(strict=True)
    stat = path.stat()
    limit = 64 * 1024 * 1024
    if stat.st_size > limit:
        raise RenderError('LUT exceeds 64 MiB')
    digest = hashlib.sha256()
    count = 0
    with path.open('rb') as stream:
        while block := stream.read(min(1024 * 1024, limit + 1 - count)):
            count += len(block)
            if count > limit:
                raise RenderError('LUT exceeds 64 MiB')
            digest.update(block)
    return (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, digest.hexdigest())

class RenderEngine:
    def __init__(self, timeout=3600):
        self.timeout = timeout
        self._cache_dir = tempfile.TemporaryDirectory(prefix='lumen-preview-')
        self._cache_lock = threading.RLock()

    def close(self):
        self._cache_dir.cleanup()

    def _graded_segment(self, media, clip, project, cancel, prepared_stabilization=None, *, exact_video_frames=False,
                        frame_window=None):
        from .color_media import grade_segment, source_rgb_filters, read_source_color
        from .color_source import validate_input_assignment
        from .restoration import RESTORATION_ALGORITHM
        restoration_active = (clip.restoration.enabled and any((clip.restoration.deblock,
                              clip.restoration.denoise, clip.restoration.sharpen)))
        if clip.grade.input_encoding == 'apple_log_bt2020' or media.input_encoding == 'apple_log_bt2020':
            try:
                validate_input_assignment(media, clip.grade.input_encoding)
            except ValueError as exc:
                raise RenderError(str(exc)) from exc
        source = Path(media.path).expanduser().resolve(strict=True)
        stat = source.stat()
        stabilization = prepared_stabilization
        if (clip.grade.input_encoding == 'apple_log_bt2020' and
                clip.stabilization.enabled and clip.stabilization.strength > 0):
            raise RenderError('Experimental Apple Log input does not support active stabilization.')
        if clip.stabilization.enabled and clip.stabilization.strength > 0:
            if stabilization is None:
                from .stabilization_render import prepare_stabilization
                stabilization = prepare_stabilization(self, source, clip, project, cancel)
            source = stabilization.source
        color_info = (read_source_color(source, self.timeout, RenderError,
                                       input_encoding=clip.grade.input_encoding)
                      if clip.grade.input_encoding == 'apple_log_bt2020'
                      else read_source_color(source, self.timeout, RenderError,
                          **({'run_process': self._range_run_process}
                             if frame_window is not None and hasattr(self, '_range_run_process') else {})))
        try:
            validate_input_assignment(color_info, clip.grade.input_encoding)
        except ValueError as exc:
            raise RenderError(str(exc)) from exc
        fingerprint = [str(source), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
                       clip.timing.to_dict(), clip.duration, project.fps_num, project.fps_den, project.width, project.height,
                       asdict(clip.grade), asdict(color_info), 'sdr-input-retime-window-output-v2',
                       RESTORATION_ALGORITHM, asdict(clip.restoration)]
        if exact_video_frames:
            from .color_media import decoder_identity
            fingerprint.append(('strict-transition-decoded-frame-index-v1',
                                decoder_identity(cancel, RenderError, RenderCancelled)))
        first, end = frame_window if frame_window is not None else (0, clip.duration)
        if frame_window is not None:
            if (type(first) is not int or type(end) is not int
                    or not 0 <= first < end <= clip.duration
                    or exact_video_frames or stabilization is not None):
                raise RenderError('Unsupported bounded grade frame window.')
            fingerprint.append(('bounded-original-local-clock-v1', first, end))
        restoration_decoder = None
        if restoration_active:
            from .color_media import decoder_identity
            restoration_decoder = decoder_identity(cancel, RenderError, RenderCancelled)
            fingerprint.append(restoration_decoder)
        if stabilization is not None:
            fingerprint.append(stabilization.fingerprint)
        if clip.grade.enabled and clip.grade.lut_path and clip.grade.lut_strength > 0:
            fingerprint += list(_lut_fingerprint(clip.grade.lut_path))
        digest = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
        target = Path(self._cache_dir.name) / (digest + '.mkv')
        with self._cache_lock:
            if not target.exists():
                temporary = target.with_suffix('.partial.mkv')
                fps = project.fps_num / project.fps_den
                rate = f'{project.fps_num}/{project.fps_den}'
                mapping = clip.timing
                legacy = (stabilization is None and mapping.speed == 1 and mapping.source_in.denominator == 1
                          and mapping.source_out - mapping.source_in == clip.duration)
                sample_indices = None
                if exact_video_frames:
                    temporal = [f'trim=start_frame={int(mapping.source_in)}:end_frame={int(mapping.source_out)}',
                                f'settb=expr={project.fps_den}/{project.fps_num}', 'setpts=N']
                elif legacy:
                    temporal = ['setpts=PTS-STARTPTS',
                                f'trim=start={_n(clip.source_in / fps)}:duration={_n(clip.duration / fps)}',
                                'setpts=PTS-STARTPTS', f'fps=fps={rate}:start_time=0']
                    if frame_window is not None:
                        temporal += [f'trim=start_frame={first}:end_frame={end}', 'setpts=PTS-STARTPTS']
                else:
                    # Normalize once from the source origin, then sample exact
                    # floor(O+kR). Decode bounded streaming context, no full clip
                    # array, no timestamp-phase reset at fractional trim/split.
                    last = (mapping.source_in + (end - 1) * mapping.speed).__floor__()
                    sample_indices = ((mapping.source_in + k * mapping.speed).__floor__()
                                      for k in range(first, end))
                    temporal = ['setpts=PTS-STARTPTS', f'fps=fps={rate}:start_time=0',
                                f'trim=end_frame={last + 1}']
                spatial = [*source_rgb_filters(source, self.timeout, RenderError, color_info),
                           'scale=w=iw*sar:h=ih:in_color_matrix=bt709:out_color_matrix=bt709:out_range=full',
                           'format=rgb48le', 'setsar=1',
                           f'scale={project.width}:{project.height}:force_original_aspect_ratio=decrease:in_color_matrix=bt709:out_color_matrix=bt709:out_range=full',
                           'format=rgb48le']
                filters = [*temporal, *spatial,
                           f'pad={project.width}:{project.height}:(ow-iw)/2:(oh-ih)/2:color=black', 'setsar=1']
                bridge_options = {'preserve_decoded_frames': True} if exact_video_frames else {}
                if first:
                    bridge_options['output_frame_offset'] = first
                if stabilization is not None:
                    from .stabilization_render import float_picture_transform
                    geometry = stabilization.geometry
                    filters = [*temporal,
                               *source_rgb_filters(source, self.timeout, RenderError, color_info),
                               f'scale={geometry.active_width}:{geometry.active_height}',
                               'format=rgb48le', 'setsar=1']
                    bridge_options = {
                        'decode_size': (geometry.active_width, geometry.active_height),
                        'pre_grade_transform': float_picture_transform(
                            stabilization, project.width, project.height, cancel),
                    }
                if restoration_active:
                    from .color_media import active_picture_size, normalized_restoration
                    if stabilization is not None:
                        active_size = (geometry.active_width, geometry.active_height)
                    else:
                        # Probe the actual existing FFmpeg chain once per source
                        # identity/canvas. Never infer padding from black pixels,
                        # metadata-only aspect guesses, or stabilization proxies.
                        sizes = getattr(self, '_active_picture_sizes', None)
                        if sizes is None:
                            sizes = self._active_picture_sizes = {}
                        geometry_key = (str(source), stat.st_size, stat.st_mtime_ns,
                                        stat.st_ctime_ns, tuple(spatial), restoration_decoder)
                        active_size = sizes.get(geometry_key)
                        if active_size is None:
                            active_size = active_picture_size(
                                source, spatial, project.width, project.height,
                                self.timeout, cancel, RenderError, RenderCancelled)
                            if len(sizes) >= 64:
                                sizes.pop(next(iter(sizes)))
                            sizes[geometry_key] = active_size
                    bridge_options['normalized_transform'] = normalized_restoration(
                        clip.restoration, active_size, project.width, project.height,
                        cancel, RenderCancelled)
                try:
                    grade_segment(source, temporary, clip.grade, project.width, project.height,
                                  filters, rate, self.timeout, cancel, RenderError, RenderCancelled,
                                  sample_indices=sample_indices, **bridge_options)
                    if exact_video_frames:
                        from .video_transition_sources import decoded_frame_count
                        if decoded_frame_count(temporary, project.fps, timeout=self.timeout, cancel=cancel) != clip.duration:
                            raise RenderError('Video transition output has an unexpected frame count.')
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
        return target

    def _verify_overlay_sources(self, project, cancel=None):
        """Hash the manifest and every PNG, including before cached playback."""
        overlays = [o for o in getattr(project, 'overlays', []) if o.enabled]
        if not overlays:
            return {}
        from .sticker_assets import verify_sticker
        from .overlay_render import validate_canvas, OverlayRenderError
        try:
            validate_canvas(project)
        except OverlayRenderError as exc:
            raise RenderError(str(exc)) from exc
        verified, assets = {}, {}
        for overlay in overlays:
            if cancel is not None and cancel.is_set():
                raise RenderCancelled('Render cancelled.')
            identity = (overlay.asset_path, overlay.asset_sha256.lower())
            try:
                if identity not in verified:
                    verified[identity] = verify_sticker(overlay.asset_path, overlay.asset_sha256, cancel=cancel)
                assets[overlay.id] = verified[identity]
            except Exception as exc:
                if cancel is not None and cancel.is_set():
                    raise RenderCancelled('Render cancelled.') from exc
                raise RenderError(f'Cannot verify sticker asset: {exc}') from exc
        return assets

    def _overlay_segment(self, project, assets, cancel=None, progress=None, *, frame_window=None):
        from .overlay_render import render_overlay_segment, OverlayRenderError, OverlayRenderCancelled
        fingerprint = {
            'compositor_version': 1,
            'width': project.width, 'height': project.height,
            'fps_num': project.fps_num, 'fps_den': project.fps_den,
            'duration_frames': project.duration_frames,
            'overlays': [asdict(overlay) for overlay in project.overlays],
        }
        if frame_window is not None:
            fingerprint['frame_window'] = frame_window
        digest = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
        target = Path(self._cache_dir.name) / ('stickers-' + digest + '.mkv')
        with self._cache_lock:
            if not target.exists():
                fd, name = tempfile.mkstemp(prefix='stickers-', suffix='.partial.mkv', dir=self._cache_dir.name)
                os.close(fd)
                temporary = Path(name)
                try:
                    options = {'frame_window': frame_window} if frame_window is not None else {}
                    render_overlay_segment(project, assets, temporary, self.timeout, cancel, progress, **options)
                    # Detect edits between verification and image decoding before
                    # publishing a reusable cache entry. The FFmpeg input after
                    # this point is the immutable owned intermediate.
                    self._verify_overlay_sources(project, cancel)
                    temporary.replace(target)
                except OverlayRenderCancelled as exc:
                    raise RenderCancelled(str(exc)) from exc
                except OverlayRenderError as exc:
                    raise RenderError(str(exc)) from exc
                finally:
                    temporary.unlink(missing_ok=True)
        return target

    def _verify_generated_sources(self, project, cancel=None):
        """Check original audio identity, including when returning a cached preview."""
        used = {clip.media_id for clip in project.clips}
        digests = {}
        for media in project.media:
            provenance = media.sound_provenance or media.speech_provenance
            if media.id not in used or provenance is None:
                continue
            if media.sound_provenance is not None:
                from .sound_library import verify_sound_file
                try:
                    verify_sound_file(media.path, provenance.output_sha256,
                                      provenance.sample_rate, provenance.sample_count, cancel)
                except InterruptedError as exc:
                    raise RenderCancelled('Render cancelled.') from exc
                except (ValueError, OSError) as exc:
                    raise RenderError(str(exc)) from exc
                continue
            try:
                source = Path(media.path).expanduser().resolve(strict=True)
                if source not in digests:
                    digest = hashlib.sha256()
                    with source.open('rb') as stream:
                        while True:
                            if cancel is not None and cancel.is_set():
                                raise RenderCancelled('Render cancelled.')
                            block = stream.read(1024 * 1024)
                            if not block:
                                break
                            digest.update(block)
                    digests[source] = digest.hexdigest()
            except OSError as exc:
                raise RenderError(f'Cannot verify sound source: {media.name}' if media.sound_provenance else f'Cannot verify generated speech source: {media.name}') from exc
            if digests[source] != provenance.output_sha256.lower():
                if media.sound_provenance is not None:
                    raise RenderError(f'Sound source has changed: {media.name}. Restore the original WAV or import it again.')
                raise RenderError(f'Generated speech source has changed: {media.name}. '
                                  'Restore the original audio or generate speech again.')

    def _compile(self, project, cancel=None, progress=None, export_profile='h264_8bit', *,
                 filter_complex_threads=1, frame_window=None):
        if type(filter_complex_threads) is not int or filter_complex_threads not in (1, 2, 4):
            raise ValueError('Composite filter threads must be 1, 2, or 4')
        project.validate()
        from .color_output import validate_export_profile
        try:
            profile = validate_export_profile(project, export_profile)
        except ValueError as exc:
            raise RenderError(str(exc)) from exc
        pix_fmt = profile.pixel_format
        # Range rendering keeps the original project/clip records and clocks.
        # This view selects dependencies only; it is never saved or validated
        # as an edited/cropped timeline (linked records may be outside it).
        participating = project
        range_start = 0
        if frame_window is not None:
            from .range_preview import validate_range
            validate_range(project, *frame_window)
            if profile.high_bit:
                raise RenderError('Range playback supports Rec.709 SDR H.264 only.')
            from .range_audio_mix import RangeAudioMixBuilder
            audio_mix = RangeAudioMixBuilder(project, *frame_window)
            range_start, range_end = frame_window
            participating = replace(project, clips=list(audio_mix.clips),
                overlays=[o for o in project.overlays if o.enabled and o.start < range_end and o.end > range_start],
                captions=[c for c in project.captions if c.start < range_end and c.end > range_start])
        else:
            from .audio_mix import AudioMixBuilder
            audio_mix = AudioMixBuilder(project)
        self._verify_generated_sources(participating, cancel)
        assets = self._verify_overlay_sources(participating, cancel)
        if project.width % 2 or project.height % 2:
            raise RenderError('SDR export requires even width and height.' if profile.high_bit else 'H.264 4:2:0 export requires even width and height.')
        if project.duration_frames <= 0:
            raise RenderError('The timeline is empty.')
        if profile.high_bit:
            from .highbit_effects import ensure_highbit_effects
            with self._cache_lock:
                ensure_highbit_effects(project, self.timeout, cancel, RenderError, RenderCancelled)
        # Only opted-in clips probe the new path; default projects retain the
        # exact legacy transform and overlay graph without this capability cost.
        if any(c.track < 2 and (c.motion_keyframes or not c.composite.is_legacy()) for c in participating.clips):
            from .clip_composite import verify_clip_composite_support
            with self._cache_lock:
                try:
                    verify_clip_composite_support(profile.high_bit, self.timeout, cancel,
                                                  RenderError, RenderCancelled, filter_complex_threads=filter_complex_threads)
                except RenderError as exc:
                    from .clip_composite import CLIP_COMPOSITE_ERROR, CLIP_MOTION_ERROR
                    if str(exc) == CLIP_COMPOSITE_ERROR and any(c.motion_keyframes for c in participating.clips):
                        raise RenderError(CLIP_MOTION_ERROR) from exc
                    raise
                if any(c.track < 2 and c.motion_keyframes for c in participating.clips):
                    from .clip_composite import verify_clip_motion_support
                    verify_clip_motion_support(profile.high_bit, self.timeout, cancel,
                                               RenderError, RenderCancelled, filter_complex_threads=filter_complex_threads)
                if not profile.high_bit and any(
                        c.track < 2 and (c.motion_keyframes or not c.composite.is_legacy()) and
                        any(e.enabled and e.kind == 'blur' and e.amount > 0 for e in c.effects)
                        for c in participating.clips):
                    from .clip_composite import ensure_composite_rgb8_blur_support
                    ensure_composite_rgb8_blur_support(self.timeout, cancel, RenderError, RenderCancelled)
        highbit_stickers = None
        if profile.high_bit and assets:
            from .highbit_stickers import highbit_sticker_filters
            with self._cache_lock:
                highbit_stickers = highbit_sticker_filters(
                    project, assets, self.timeout, cancel, RenderError, RenderCancelled)
        highbit_captions = None
        if profile.high_bit and getattr(project, 'captions', []):
            from .highbit_captions import highbit_caption_filters
            from .caption_render import CaptionRenderError
            try:
                with self._cache_lock:
                    highbit_captions = highbit_caption_filters(
                        project, self._cache_dir.name, self.timeout, cancel, RenderError, RenderCancelled)
            except CaptionRenderError as exc:
                raise RenderError(str(exc)) from exc
        from .audio_timing import ensure_retime_audio_capability
        fps = project.fps_num / project.fps_den
        rate = f'{project.fps_num}/{project.fps_den}'
        duration = ((range_end - range_start) if frame_window is not None else project.duration_frames) / fps
        media = {m.id: m for m in project.media}
        clips = audio_mix.clips
        inputs, graph = [], []
        if profile.high_bit:
            from .highbit_effects import highbit_effect_input_options
            inputs += highbit_effect_input_options(project)
        elif any(c.track < 2 and (c.motion_keyframes or not c.composite.is_legacy()) and
                 any(e.enabled and e.kind == 'blur' and e.amount > 0 for e in c.effects)
                 for c in (project.clips if frame_window is not None else clips)):
            # Optimized gblur corrupts narrow RGB8 as well as RGB16 frames.
            # New opt-in compositing keeps explicit RGB8 effects and uses the
            # verified scalar implementation; legacy default flags stay exact.
            inputs += ['-cpuflags', '0']
        checked = {}
        stabilized_clips, source_snapshots = {}, {}
        prepared_transitions = []
        if frame_window is None and getattr(project, 'video_transitions', []):
            from .video_transition_render import prepare_video_transitions
            prepared_transitions, source_snapshots = prepare_video_transitions(
                self, project, profile.high_bit, cancel)
        stabilized_source_snapshots = {}
        for clip in clips:
            if (clip.track < 2 and clip.grade.input_encoding == 'apple_log_bt2020' and
                    clip.stabilization.enabled and clip.stabilization.strength > 0):
                raise RenderError('Experimental Apple Log input does not support active stabilization.')
            if clip.track < 2 and clip.stabilization.enabled and clip.stabilization.strength > 0:
                from .stabilization_render import prepare_stabilization
                prepared = prepare_stabilization(self, source_snapshots.get(clip.media_id, Path(media[clip.media_id].path)), clip, project, cancel)
                previous = stabilized_source_snapshots.get(clip.media_id)
                if previous is not None and previous != prepared.source:
                    raise RenderError('Source changed while preparing stabilization.')
                stabilized_source_snapshots[clip.media_id] = prepared.source
                source_snapshots.setdefault(clip.media_id, prepared.source)
                stabilized_clips[clip.id] = prepared
        for clip in clips:
            if frame_window is not None and cancel is not None and cancel.is_set():
                raise RenderCancelled('Render cancelled.')
            m = media[clip.media_id]
            source_path = source_snapshots.get(m.id, Path(m.path).expanduser().resolve(strict=True))
            if m.id not in checked:
                checked[m.id] = (probe(source_path, run_process=self._range_run_process)
                                 if frame_window is not None and hasattr(self, '_range_run_process') else
                                 probe(source_path, input_encoding=m.input_encoding)
                                 if m.input_encoding == 'apple_log_bt2020' else probe(source_path))
            inputs += ['-protocol_whitelist', 'file,pipe', '-i', str(source_path)]
        refreshed = replace(project, media=[
            replace(checked[m.id], id=m.id, speech_provenance=m.speech_provenance, sound_provenance=m.sound_provenance,
                    # The original duration was validated against the recorded
                    # PCM count and source hash. ffprobe rounds its display
                    # duration to six decimals, losing exact sample boundaries.
                    duration=(m.duration if m.sound_provenance is not None
                              or (m.speech_provenance is not None and m.speech_provenance.sample_count is not None)
                              else checked[m.id].duration))
            if m.id in checked else m for m in project.media
        ])
        refreshed.validate()
        if frame_window is not None:
            # Reject known audio history bounds before expensive video grading.
            for index, clip in enumerate(clips):
                audio_mix.clip_fragment(index, checked[clip.media_id])
        if any(c.speed != 1 and clip_emits_source_audio(c, checked[c.media_id]) for c in clips):
            try:
                if frame_window is not None and hasattr(self, '_range_audio_capability'):
                    self._range_audio_capability()
                else:
                    ensure_retime_audio_capability(ffmpeg_binary(), min(self.timeout, 30))
            except ValueError as exc:
                raise RenderError(str(exc)) from exc
        graph += [f'color=c=black:s={project.width}x{project.height}:r={rate}:d={_n(duration)},setsar=1,format={pix_fmt}[base]',
                  audio_mix.silence_fragment]
        graded_inputs = {}
        for index, clip in enumerate(clips):
            if frame_window is not None and cancel is not None and cancel.is_set():
                raise RenderCancelled('Render cancelled.')
            m = checked[clip.media_id]
            if clip.track < 2 and m.has_video:
                graded_inputs[index] = len(clips) + len(graded_inputs)
                prepared = stabilized_clips.get(clip.id)
                segment = (self._graded_segment(m, clip, project, cancel,
                               frame_window=(max(0, range_start - clip.start), min(clip.duration, range_end - clip.start)))
                           if frame_window is not None else
                           self._graded_segment(m, clip, project, cancel, prepared_stabilization=prepared)
                           if prepared is not None else self._graded_segment(m, clip, project, cancel))
                inputs += ['-protocol_whitelist', 'file,pipe', '-i', str(segment)]
            if progress:
                progress((.4 if assets else .6) * (index + 1) / len(clips))
        transition_inputs = {}
        for prepared in prepared_transitions:
            transition_inputs[prepared.plan.transition.id] = len(clips) + len(graded_inputs) + len(transition_inputs)
            inputs += ['-protocol_whitelist', 'file,pipe', '-i', str(prepared.path)]
        video = 'base'
        audio = []
        for index, clip in enumerate(clips):
            m = checked[clip.media_id]
            start, source, length = clip.start / fps, clip.source_in / fps, clip.duration / fps
            if clip.track < 2 and m.has_video:
                gate = f'gte(n,{clip.start - range_start})*lt(n,{clip.start + clip.duration - range_start})'
                local_start = max(0, range_start - clip.start)
                placement = max(clip.start, range_start) - range_start
                for prepared in prepared_transitions:
                    plan = prepared.plan
                    if clip.id in (plan.left.id, plan.right.id):
                        gate += f'*not(gte(n,{plan.start})*lt(n,{plan.end}))'
                from .vignette_shape import has_active_shaped_vignette
                shaped_rgb8 = not profile.high_bit and has_active_shaped_vignette(getattr(clip, 'effects', []))
                composite_active = bool(clip.motion_keyframes) or not clip.composite.is_legacy()
                if profile.high_bit or (composite_active and not any(
                        e.enabled and e.amount > 0 for e in getattr(clip, 'effects', []))):
                    # _graded_segment already fits/pads the project canvas in RGB16.
                    # Do not round-trip through the legacy 8-bit YUV composition path.
                    vf = ['setpts=PTS-STARTPTS', f'fps=fps={rate}:start_time=0',
                          'format=gbrp16le', 'setsar=1']
                elif shaped_rgb8 or composite_active:
                    # The grade cache already fits/pads the full project canvas
                    # in RGB16. Reusing the legacy YUV matrix/range scale while
                    # it negotiates RGB output changes saturated colors. Cross
                    # only the intended explicit RGB16 -> RGB8 precision boundary.
                    vf = ['setpts=PTS-STARTPTS', f'fps=fps={rate}:start_time=0',
                          'format=gbrp', 'setsar=1']
                else:
                    vf = ['setpts=PTS-STARTPTS', f'fps=fps={rate}:start_time=0',
                          f'scale={project.width}:{project.height}:force_original_aspect_ratio=decrease:in_color_matrix=bt709:out_color_matrix=bt709:out_range=tv',
                          f'pad={project.width}:{project.height}:(ow-iw)/2:(oh-ih)/2:color=black', 'setsar=1']
                if getattr(clip, 'effects', []):
                    from .overlay_render import effect_filters, OverlayRenderError
                    try:
                        if profile.high_bit:
                            from .highbit_effects import highbit_effect_filters
                            vf += highbit_effect_filters(clip.effects)
                        else:
                            filters = effect_filters(clip.effects)
                            if composite_active and not shaped_rgb8:
                                # The new H.264 source-over helper consumes RGB16.
                                # Keep H.264 effects explicitly RGB8 first: letting
                                # gblur negotiate RGB16 here can hit SIMD defects.
                                # Legacy vignette accepts packed, not planar RGB.
                                ordered = []
                                for item in filters:
                                    ordered.extend(['format=rgb24', item, 'format=gbrp']
                                                   if item.startswith('vignette=') else [item])
                                filters = ordered
                            vf += filters
                    except OverlayRenderError as exc:
                        raise RenderError(str(exc)) from exc
                next_video = f'over{index}'
                if composite_active:
                    from .clip_composite import clip_composite_filters
                    main_filter, layer_filter, over_filter = clip_composite_filters(
                        clip.composite, clip.transform, project.width, project.height,
                        high_bit=profile.high_bit, motion_keys=clip.motion_keyframes,
                        **({'motion_frame_offset': local_start} if frame_window is not None else {}))
                    # Source timing is unchanged. Geometry and alpha operate on
                    # the already fitted, graded/effected canvas together.
                    vf += [layer_filter, f'setpts=PTS+{placement}']
                    graph += [f'[{graded_inputs[index]}:V:0]' + ','.join(vf) + f'[v{index}]',
                              f'[{video}]{main_filter}[compositebase{index}]']
                    over, comma, tail = over_filter.partition(',')
                    over += f":enable='{gate}'"
                    graph += [f'[compositebase{index}][v{index}]' + over + comma + tail + f'[{next_video}]']
                else:
                    from .clip_transform import transform_filters
                    vf += transform_filters(clip.transform, project.width, project.height)
                    if profile.high_bit:
                        vf += ['format=gbrp16le',
                               'zscale=matrixin=gbr:rangein=full:matrix=709:range=limited:dither=none']
                    elif shaped_rgb8:
                        # Pin Rec.709/full RGB -> limited YUV explicitly after
                        # effects and geometry. A bare format request may silently
                        # use a 601 conversion despite the final 709 output tags.
                        vf += ['scale=out_color_matrix=bt709:in_range=full:out_range=tv']
                    vf += [f'format={pix_fmt}', f'setpts=PTS+{placement}']
                    graph += [f'[{graded_inputs[index]}:V:0]' + ','.join(vf) + f'[v{index}]']
                    next_video = f'over{index}'
                    # Zero-based integer frame gates avoid fractional-time rounding at cuts.
                    overlay_format = 'format=yuv422p10:' if profile.high_bit else ''
                    graph += [f'[{video}][v{index}]overlay={overlay_format}eof_action=pass:repeatlast=0:shortest=0:enable=\'{gate}\'[{next_video}]']
                video = next_video
            fragment = audio_mix.clip_fragment(index, m)
            if fragment is not None:
                graph += [fragment.fragment]
                audio += [fragment.label]
            # Finish this track before higher tracks, stickers and captions.
            if prepared_transitions and (index + 1 == len(clips) or clips[index + 1].track != clip.track):
                from .video_transition_render import transition_overlay_graph
                for transition_index, prepared in enumerate(prepared_transitions):
                    if prepared.plan.track == clip.track:
                        name = f'transition{transition_index}'
                        graph += transition_overlay_graph(prepared, transition_inputs[prepared.plan.transition.id],
                                                          video, name, project, profile.high_bit)
                        video = name
        if assets:
            sticker_index = len(clips) + len(graded_inputs) + len(prepared_transitions)
            sticker_progress = (lambda value: progress(.4 + .2 * value)) if progress else None
            sticker_video = self._overlay_segment(participating, assets, cancel, sticker_progress,
                **({'frame_window': frame_window} if frame_window is not None else {}))
            if progress:
                progress(.6)
            inputs += ['-protocol_whitelist', 'file,pipe', '-i', str(sticker_video)]
            if highbit_stickers is not None:
                main_filter, layer_filter, overlay_filter = highbit_stickers
                graph += [f'[{video}]{main_filter}[stickerbase]',
                          f'[{sticker_index}:V:0]setpts=PTS-STARTPTS,fps=fps={rate}:start_time=0,'
                          f'{layer_filter}[stickers]',
                          f'[stickerbase][stickers]{overlay_filter}[stickered]']
            else:
                graph += [f'[{sticker_index}:V:0]setpts=PTS-STARTPTS,fps=fps={rate}:start_time=0,'
                          'format=rgba,scale=in_color_matrix=bt709:out_color_matrix=bt709:in_range=full:out_range=tv,'
                          'format=yuva420p[stickers]',
                          f'[{video}][stickers]overlay=eof_action=pass:repeatlast=0:shortest=0:alpha=straight[stickered]']
            video = 'stickered'
        if getattr(participating, 'captions', []):
            from .caption_render import caption_filters, CaptionRenderError
            try:
                with self._cache_lock:
                    filters = (highbit_captions if highbit_captions is not None else
                               caption_filters(participating, self._cache_dir.name,
                                   **({'frame_offset': range_start, 'check': self._range_check,
                                       'run_process': self._range_run_process}
                                      if frame_window is not None and hasattr(self, '_range_check') else
                                      {'frame_offset': range_start, 'check': _cancel_check(cancel)}
                                      if frame_window is not None and cancel is not None else
                                      {'frame_offset': range_start} if frame_window is not None else {})))
            except CaptionRenderError as exc:
                raise RenderError(str(exc)) from exc
            graph += [f'[{video}]' + ','.join(filters) + '[captioned]']
            video = 'captioned'
        graph += [f'[{video}]trim=duration={_n(duration)},setpts=PTS-STARTPTS,format={pix_fmt},'
                  'setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709[vout]',
                  audio_mix.final_fragment(audio)]
        return inputs, ';'.join(graph), duration

    def render(self, project, output_path, progress=None, cancel=None, preview=False,
               export_profile='h264_8bit') -> Path:
        from .color_output import validate_export_profile
        try:
            profile = validate_export_profile(project, export_profile)
        except ValueError as exc:
            raise RenderError(str(exc)) from exc
        if preview and profile.high_bit:
            raise RenderError('Preview is an H.264 8-bit proxy; ProRes 10-bit is an export profile only.')
        output = Path(output_path).expanduser().absolute()
        if output.suffix.lower() not in profile.suffixes:
            raise RenderError('ProRes 10-bit SDR export filename must end in .mov.' if profile.high_bit
                              else 'Export filename must end in .mp4 or .mov.')
        if output.exists() or output.is_symlink():
            raise RenderError('Output already exists. Choose a new filename; overwriting is disabled.')
        if any(output.resolve() == Path(m.path).expanduser().resolve() for m in project.media):
            raise RenderError('An input media file cannot be replaced.')
        if any(c.grade.lut_path and output.resolve() == Path(c.grade.lut_path).expanduser().resolve() for c in project.clips):
            raise RenderError('A LUT input file cannot be replaced.')
        for overlay in getattr(project, 'overlays', []):
            if output.resolve() == Path(overlay.asset_path).expanduser().resolve():
                raise RenderError('A sticker input file cannot be replaced.')
        if not output.parent.is_dir():
            raise RenderError('Output folder does not exist.')
        if cancel is not None and cancel.is_set():
            raise RenderCancelled('Render cancelled.')
        if not ffmpeg_available():
            raise RenderError('Install FFmpeg and ffprobe and add them to PATH.')
        filter_complex_threads = _composite_filter_threads(project)
        if profile.high_bit:
            self._ensure_highbit_encoder()
            if cancel is not None and cancel.is_set():
                raise RenderCancelled('Render cancelled.')
            inputs, graph, duration = self._compile(project, cancel=cancel, progress=progress,
                                                   export_profile=profile.key, filter_complex_threads=filter_complex_threads)
        else:
            inputs, graph, duration = self._compile(project, cancel=cancel, progress=progress,
                                                   filter_complex_threads=filter_complex_threads)
        if getattr(project, 'overlays', []):
            from .sticker_assets import sticker_input_paths, StickerAssetError
            try:
                for overlay in project.overlays:
                    if overlay.enabled and any(output.resolve() == Path(path).resolve()
                                               for path in sticker_input_paths(overlay.asset_path, cancel=cancel)):
                        raise RenderError('A sticker input file cannot be replaced.')
            except StickerAssetError as exc:
                if cancel is not None and cancel.is_set():
                    raise RenderCancelled('Render cancelled.') from exc
                raise RenderError(f'Cannot verify sticker input paths: {exc}') from exc
        graph_args = ['-filter_complex', graph]
        if (getattr(project, 'captions', [])
                or any(c.motion_keyframes or c.audio_gain_keyframes for c in project.clips)):
            # A cue-rich timeline can exceed the OS single-argument size limit.
            # FFmpeg 7+ supports loading an option value from a file via -/;
            # the older -filter_complex_script spelling was removed in 9.
            # Pass the path as one argv entry, without shell/filter escaping.
            graph_path = Path(self._cache_dir.name) / (hashlib.sha256(graph.encode()).hexdigest() + '.ffgraph')
            with self._cache_lock:
                graph_path.write_text(graph, encoding='utf-8')
            graph_args = ['-/filter_complex', str(graph_path.resolve())]
        fd, temporary = tempfile.mkstemp(prefix='.lumen-render-', suffix=output.suffix, dir=output.parent)
        os.close(fd)
        process = None
        try:
            with tempfile.NamedTemporaryFile(mode='w+b', delete=False) as pf:
                progress_path = pf.name
            try:
                with tempfile.TemporaryFile(mode='w+b') as log:
                    video_codec = (['-c:v', 'prores_ks', '-profile:v', '3', '-vendor', 'apl0',
                                    '-pix_fmt', 'yuv422p10le'] if profile.high_bit else
                                   ['-c:v', 'libx264', '-preset', 'ultrafast' if preview else 'medium',
                                    '-crf', '20' if preview else '18', '-pix_fmt', 'yuv420p'])
                    command = [ffmpeg_binary(), '-hide_banner', '-nostdin', '-y', '-filter_complex_threads', str(filter_complex_threads), *inputs,
                               *graph_args, '-map', '[vout]', '-map', '[aout]',
                               '-r', f'{project.fps_num}/{project.fps_den}', '-fps_mode', 'cfr', *video_codec, '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', '-color_range', 'tv',
                               '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-t', _n(duration), '-movflags', '+faststart',
                               '-progress', progress_path, '-nostats', temporary]
                    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=log)
                    began = time.monotonic()
                    while process.poll() is None:
                        if cancel is not None and cancel.is_set():
                            raise RenderCancelled('Render cancelled.')
                        if time.monotonic() - began > self.timeout:
                            raise RenderError('Render timed out.')
                        if progress:
                            lines = Path(progress_path).read_text(errors='replace').splitlines()
                            stamps = [x.split('=', 1)[1] for x in lines if x.startswith('out_time_us=')]
                            if stamps and stamps[-1].isdigit():
                                progress(min(.99, .6 + .39 * int(stamps[-1]) / 1e6 / duration))
                        time.sleep(.05)
                    if process.returncode:
                        log.seek(0)
                        raise RenderError(log.read().decode(errors='replace')[-6000:])
                if cancel is not None and cancel.is_set():
                    raise RenderCancelled('Render cancelled.')
                if profile.high_bit:
                    self._verify_highbit_output(temporary)
                    if cancel is not None and cancel.is_set():
                        raise RenderCancelled('Render cancelled.')
                # Atomic creation without a race that could overwrite a new user file.
                os.link(temporary, output)
                if progress:
                    progress(1.0)
                return output
            finally:
                # Windows cannot unlink the progress file while FFmpeg holds it.
                # Reap the encoder before removing either of its output files.
                if process is not None and process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                Path(progress_path).unlink(missing_ok=True)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _ensure_highbit_encoder(self):
        """Check the explicit encoder; never silently choose an 8-bit alternative."""
        try:
            result = subprocess.run([ffmpeg_binary(), '-hide_banner', '-h', 'encoder=prores_ks'],
                                    capture_output=True, text=True, timeout=min(self.timeout, 30))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RenderError('Cannot check the ProRes 10-bit encoder. Install an FFmpeg build with prores_ks and zscale support.') from exc
        if result.returncode or 'yuv422p10le' not in result.stdout or 'prores_ks' not in result.stdout:
            raise RenderError('This FFmpeg build lacks the required prores_ks 10-bit encoder. Choose H.264 8-bit or install a compatible FFmpeg build.')

    def _verify_highbit_output(self, path):
        """Fail before publication if an external encoder violates the delivery contract."""
        try:
            result = subprocess.run([ffprobe_binary(), '-v', 'error', '-select_streams', 'v:0',
                                     '-read_intervals', '%+#1', '-show_frames',
                                     '-show_entries', 'stream=codec_name,profile,pix_fmt,color_space,color_transfer,color_primaries:frame=pix_fmt,color_range',
                                     '-of', 'json', '-protocol_whitelist', 'file,pipe', str(path)],
                                    capture_output=True, text=True, timeout=min(self.timeout, 30))
            data = json.loads(result.stdout)
            stream = data['streams'][0]
            expected = {'codec_name': 'prores', 'profile': 'HQ', 'pix_fmt': 'yuv422p10le',
                        'color_space': 'bt709',
                        'color_transfer': 'bt709', 'color_primaries': 'bt709'}
            # MOV's nclc does not encode range. ProRes decoding defines video
            # range; verify a real decoded frame rather than inventing a tag.
            frames = data.get('frames', [])
            if (result.returncode or not isinstance(stream, dict)
                    or any(stream.get(k) != v for k, v in expected.items())
                    or not isinstance(frames, list) or not frames or not isinstance(frames[0], dict)
                    or frames[0].get('color_range') != 'tv'
                    or frames[0].get('pix_fmt') != 'yuv422p10le'):
                raise ValueError('Unexpected encoded pixel format, codec profile or SDR color tags')
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, IndexError, TypeError) as exc:
            raise RenderError(f'ProRes 10-bit SDR output verification failed; no export was published: {exc}') from exc

    def preview(self, project, progress=None, cancel=None) -> Path:
        """Cached timeline playback, using precisely the export compiler."""
        project.validate()
        if cancel is not None and cancel.is_set():
            raise RenderCancelled('Render cancelled.')
        # Stat metadata is insufficient for imported assets: always re-hash the
        # manifest and its PNG dependencies before returning even a cache hit.
        self._verify_overlay_sources(project, cancel)
        from .audio_timing import AUDIO_TIMING_ALGORITHM
        from .audio_gain import AUDIO_GAIN_ALGORITHM_ID
        from .restoration import RESTORATION_ALGORITHM
        from .clip_composite import COMPOSITE_ALGORITHM
        fingerprint = {'project': project.to_dict(), 'retime_sampler': 'project-grid-floor-v1',
                       'audio_retime': AUDIO_TIMING_ALGORITHM,
                       'audio_gain': AUDIO_GAIN_ALGORITHM_ID,
                       'restoration': RESTORATION_ALGORITHM, 'sources': []}
        if getattr(project, 'video_transitions', []):
            from .video_transition_render import TRANSITION_ALGORITHM
            from .video_transition_sources import prepare_transition_sources
            try:
                proofs = prepare_transition_sources(project, Path(self._cache_dir.name) / 'video-transitions',
                                                    timeout=self.timeout, cancel=cancel)
            except Exception as exc:
                if cancel is not None and cancel.is_set():
                    raise RenderCancelled('Render cancelled.') from exc
                raise RenderError(str(exc)) from exc
            from .color_media import decoder_identity
            fingerprint['video_transition'] = (TRANSITION_ALGORITHM,
                {key: proof.sha256 for key, proof in proofs.items()},
                decoder_identity(cancel, RenderError, RenderCancelled))
        if any(c.motion_keyframes or not c.composite.is_legacy() for c in project.clips):
            fingerprint['clip_composite'] = COMPOSITE_ALGORITHM
        if any(c.motion_keyframes for c in project.clips):
            from .clip_motion_render import MOTION_ALGORITHM
            fingerprint['clip_motion'] = MOTION_ALGORITHM
        from .color_media import read_source_color, decoder_identity
        if any(c.track < 2 and c.restoration.enabled and
               any((c.restoration.deblock, c.restoration.denoise, c.restoration.sharpen)) for c in project.clips):
            fingerprint['restoration_decoder'] = decoder_identity(cancel, RenderError, RenderCancelled)
        from .color_source import validate_input_assignment
        used_video = {c.media_id for c in project.clips if c.track < 2}
        color_sources = {}
        for m in project.media:
            p = Path(m.path)
            stat = p.stat()
            fingerprint['sources'].append((str(p.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
            if m.id in used_video:
                # Re-probe using the persisted explicit import interpretation.
                # Each clip must agree below; no per-file camera guessing.
                color_sources[m.id] = (read_source_color(p, self.timeout, RenderError,
                    input_encoding=m.input_encoding) if m.input_encoding == 'apple_log_bt2020'
                    else read_source_color(p, self.timeout, RenderError))
        fingerprint['source_color'] = {key: asdict(value) for key, value in color_sources.items()}
        for c in project.clips:
            if (c.track < 2 and c.grade.input_encoding == 'apple_log_bt2020' and
                    c.stabilization.enabled and c.stabilization.strength > 0):
                raise RenderError('Experimental Apple Log input does not support active stabilization.')
            if c.track < 2 and c.stabilization.enabled and c.stabilization.strength > 0:
                from .stabilization_render import prepare_stabilization
                media = next(m for m in project.media if m.id == c.media_id)
                prepared = prepare_stabilization(self, Path(media.path), c, project, cancel)
                fingerprint.setdefault('stabilization', []).append((c.id, prepared.fingerprint))
            if c.media_id in color_sources and c.track < 2:
                try:
                    validate_input_assignment(color_sources[c.media_id], c.grade.input_encoding)
                except ValueError as exc:
                    raise RenderError(str(exc)) from exc
            if c.grade.enabled and c.grade.lut_path and c.grade.lut_strength > 0:
                fingerprint['sources'].append(_lut_fingerprint(c.grade.lut_path))
        if getattr(project, 'captions', []):
            from .caption_render import font_identity, CaptionRenderError
            try:
                fingerprint['caption_font'] = font_identity(project.caption_style)
            except CaptionRenderError as exc:
                raise RenderError(str(exc)) from exc
        digest = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, default=str).encode()).hexdigest()
        target = Path(self._cache_dir.name) / (digest + '.mp4')
        with self._cache_lock:
            if not target.exists():
                self.render(project, target, progress=progress, cancel=cancel, preview=True)
            else:
                self._verify_generated_sources(project, cancel)
        return target

    def frame(self, project, frame_index, width=640) -> bytes:
        if not 0 <= frame_index < project.duration_frames:
            raise RenderError('Frame is outside the timeline.')
        if width < 2:
            raise RenderError('Preview width must be at least 2 pixels.')
        path = self.preview(project)
        result = subprocess.run([ffmpeg_binary(), '-v', 'error', '-protocol_whitelist', 'file,pipe', '-i', str(path), '-vf',
                                 f'select=eq(n\\,{int(frame_index)}),scale={int(width)}:-1',
                                 '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', 'pipe:1'],
                                capture_output=True, timeout=self.timeout)
        if result.returncode or not result.stdout:
            raise RenderError(result.stderr.decode(errors='replace') or 'No preview frame.')
        return result.stdout

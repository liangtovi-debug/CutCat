"""Video-only two-source transition bridge; never extends live clips or audio.

Both immutable source windows are graded independently, then effected and
transformed before full-chroma Rec.709 encoded-domain RGB16 mixing. The exact
integer project clock is essential: independently trimmed millisecond clocks
can cause framesync to silently repeat a neighboring frame.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

TRANSITION_ALGORITHM = 'decoded-cfr-two-source-rgb16-dissolve-v1'


def validate_transition_sources(project, timeout=30, cancel=None):
    from .video_transition_sources import validate_transition_sources as validate
    return validate(project, timeout=timeout, cancel=cancel)


def _run(command, timeout, cancel):
    from .render import RenderError, RenderCancelled
    process = None
    started = time.monotonic()
    with tempfile.TemporaryFile() as log:
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=log)
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    raise RenderCancelled('Render cancelled.')
                if time.monotonic() - started > timeout:
                    raise RenderError('Video transition rendering timed out.')
                if log.tell() > 8 * 1024**2:
                    raise RenderError('Video transition diagnostics exceeded the safe limit.')
                time.sleep(.025)
            if cancel is not None and cancel.is_set():
                raise RenderCancelled('Render cancelled.')
            log.seek(0)
            detail = log.read(8192).decode(errors='replace').strip()
            if process.returncode or detail:
                raise RenderError('Video transition rendering failed: ' + detail)
        except OSError as exc:
            raise RenderError('Cannot run the video transition renderer.') from exc
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()


def picture_filters(clip, project, high_bit):
    from .clip_transform import transform_filters
    # Effects retain their explicit profile precision boundary. There is never
    # YUV/chroma subsampling between the two sources and the dissolve operation.
    filters = ['format=gbrp16le']
    if high_bit:
        from .highbit_effects import highbit_effect_filters
        filters += highbit_effect_filters(clip.effects)
    elif any(e.enabled and e.amount > 0 for e in clip.effects):
        from .overlay_render import effect_filters
        filters += ['format=gbrp']
        for item in effect_filters(clip.effects):
            filters += ['format=rgb24', item, 'format=gbrp'] if item.startswith('vignette=') else [item]
    filters += transform_filters(clip.transform, project.width, project.height)
    return filters + ['format=gbrp16le']


def blend_filter(duration):
    if type(duration) is not int or duration < 2:
        raise ValueError('Video transition duration must be at least two frames.')
    return (f"blend=all_expr='A*(1-(N-1)/{duration-1})+B*((N-1)/{duration-1})'"
            ':shortest=1:repeatlast=0')


@dataclass(frozen=True)
class PreparedTransition:
    plan: object
    path: Path
    fingerprint: str


def verify_transition_support(engine, decoder, cancel=None):
    """Probe actual low-bit blend arithmetic before publishing a new cache."""
    import numpy as np
    from .media import ffmpeg_binary
    from .render import RenderError
    key = (TRANSITION_ALGORITHM, tuple(decoder))
    if key in getattr(engine, '_transition_support', set()):
        return
    width, height, duration = 1024, 2, 3
    ramp = np.arange(width, dtype=np.uint16) * 64
    left = np.broadcast_to(ramp, (duration, 3, height, width)).copy()
    right = left + 1
    with tempfile.TemporaryDirectory(prefix='cutcat-blend-probe-', dir=engine._cache_dir.name) as directory:
        root = Path(directory)
        (root / 'left.raw').write_bytes(left.astype('<u2').tobytes())
        (root / 'right.raw').write_bytes(right.astype('<u2').tobytes())
        output = root / 'out.raw'
        inputs = []
        for side in ('left', 'right'):
            inputs += ['-f', 'rawvideo', '-pix_fmt', 'gbrp16le', '-s', '1024x2', '-r', '30',
                       '-protocol_whitelist', 'file,pipe', '-i', str(root / (side + '.raw'))]
        _run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-noauto_conversion_filters',
              '-filter_complex_threads', '1', *inputs,
              '-filter_complex', '[0:v][1:v]' + blend_filter(duration) + '[out]',
              '-map', '[out]', '-an', '-fps_mode', 'passthrough', '-threads', '1',
              '-pix_fmt', 'gbrp16le', '-f', 'rawvideo', str(output)], engine.timeout, cancel)
        raw = output.read_bytes()
        if len(raw) != left.nbytes:
            raise RenderError('This FFmpeg build cannot preserve precise RGB16 video transitions.')
        got = np.frombuffer(raw, dtype='<u2').reshape(left.shape)
        if (not np.array_equal(got[0], left[0]) or not np.array_equal(got[-1], right[-1])
                or np.max(np.abs(got[1].astype(np.float64) - (left[1].astype(np.float64) + .5))) > .5):
            raise RenderError('This FFmpeg build cannot preserve precise RGB16 video transitions.')
    if not hasattr(engine, '_transition_support'):
        engine._transition_support = set()
    engine._transition_support.add(key)


def prepare_video_transitions(engine, project, high_bit=False, cancel=None):
    from .video_transition import plan_video_transitions
    from .video_transition_sources import prepare_transition_sources
    from .media import ffmpeg_binary
    from .color_media import decoder_identity
    from .render import RenderError, RenderCancelled, _lut_fingerprint
    plans = plan_video_transitions(project)
    if not plans:
        return [], {}
    cache = Path(engine._cache_dir.name) / 'video-transitions'
    cache.mkdir(exist_ok=True)
    try:
        proofs = prepare_transition_sources(project, cache, timeout=engine.timeout, cancel=cancel)
    except Exception as exc:
        if cancel is not None and cancel.is_set():
            raise RenderCancelled('Render cancelled.') from exc
        raise RenderError(str(exc)) from exc
    decoder = decoder_identity(cancel, RenderError, RenderCancelled)
    verify_transition_support(engine, decoder, cancel)
    media = {m.id: m for m in project.media}
    prepared = []
    for plan in plans:
        if cancel is not None and cancel.is_set():
            raise RenderCancelled('Render cancelled.')
        windows, identities = [], []
        for clip, start in ((plan.left, plan.left_source_start), (plan.right, plan.right_source_start)):
            proof = proofs[clip.media_id]
            source = replace(media[clip.media_id], path=str(proof.path))
            virtual = replace(clip, start=0, source_in=int(start), duration=plan.duration,
                              time_map=None, muted=True, fade_in=0, fade_out=0)
            windows.append(engine._graded_segment(source, virtual, project, cancel, exact_video_frames=True))
            identity = dict(clip=asdict(virtual), source=proof.sha256)
            if clip.grade.enabled and clip.grade.lut_path and clip.grade.lut_strength > 0:
                identity['lut'] = _lut_fingerprint(clip.grade.lut_path)
            identities.append(identity)
        fingerprint = hashlib.sha256(json.dumps(dict(
            algorithm=TRANSITION_ALGORITHM, decoder=decoder, sources=identities,
            width=project.width, height=project.height, fps_num=project.fps_num,
            fps_den=project.fps_den, high_bit=high_bit, duration=plan.duration),
            sort_keys=True, default=str).encode()).hexdigest()
        target = cache / (fingerprint + '.mkv')
        with engine._cache_lock:
            if not target.exists():
                fd, name = tempfile.mkstemp(dir=cache, suffix='.partial.mkv')
                os.close(fd)
                temporary = Path(name)
                try:
                    graph = []
                    for index, clip in enumerate((plan.left, plan.right)):
                        filters = [f'settb=expr={project.fps_den}/{project.fps_num}', 'setpts=N',
                                   *picture_filters(clip, project, high_bit)]
                        graph.append(f'[{index}:V:0]' + ','.join(filters) + f'[side{index}]')
                    graph.append('[side0][side1]' + blend_filter(plan.duration) + '[out]')
                    _run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-cpuflags', '0',
                          '-filter_complex_threads', '1',
                          '-protocol_whitelist', 'file,pipe', '-i', str(windows[0]),
                          '-protocol_whitelist', 'file,pipe', '-i', str(windows[1]),
                          '-filter_complex', ';'.join(graph), '-map', '[out]', '-an',
                          '-fps_mode', 'passthrough',
                          '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'gbrp16le', '-threads', '1',
                          '-color_range', 'pc', '-color_primaries', 'bt709', '-color_trc', 'bt709',
                          '-colorspace', 'rgb', str(temporary)], engine.timeout, cancel)
                    # Decode the completed immutable bridge and verify its exact
                    # frame count; no encoder timestamp/fps padding is accepted.
                    from .video_transition_sources import decoded_frame_count
                    count = decoded_frame_count(temporary, project.fps, timeout=engine.timeout, cancel=cancel)
                    if count != plan.duration:
                        raise RenderError('Video transition output has an unexpected frame count.')
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
        prepared.append(PreparedTransition(plan, target, fingerprint))
    return prepared, {key: proof.path for key, proof in proofs.items()}


def transition_overlay_graph(prepared, input_index, video, name, project, high_bit):
    plan = prepared.plan
    pix_fmt = 'yuv422p10le' if high_bit else 'yuv420p'
    conversion = ('zscale=matrixin=gbr:rangein=full:matrix=709:range=limited:dither=none'
                  if high_bit else 'scale=in_color_matrix=bt709:out_color_matrix=bt709:in_range=full:out_range=tv')
    filters = [f'settb=expr={project.fps_den}/{project.fps_num}', 'setpts=N', 'format=gbrp16le',
               conversion, f'format={pix_fmt}', f'setpts=PTS+{plan.start}']
    overlay_format = 'format=yuv422p10:' if high_bit else ''
    return [f'[{input_index}:V:0]' + ','.join(filters) + f'[{name}layer]',
            f'[{video}][{name}layer]overlay={overlay_format}eof_action=pass:repeatlast=0:shortest=0:'
            f"enable='gte(n,{plan.start})*lt(n,{plan.end})'[{name}]"]

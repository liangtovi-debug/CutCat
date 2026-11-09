"""Bounded-memory 16-bit media bridge to the owned float32 grading evaluator.

Only this codec boundary quantizes/clamps. Arithmetic inside apply_grade stays float.
"""
from __future__ import annotations
import subprocess
import json
import re
import hashlib
import os
import shutil
import tempfile
import threading
import time
import numpy as np
from . import color
from .media import ffmpeg_binary, ffprobe_binary
from .color_source import source_color_from_stream


def read_source_color(source, timeout, error_type, input_encoding="rec709"):
    """Re-read and validate actual source metadata, including cached renders."""
    try:
        result = subprocess.run([
            ffprobe_binary(), '-v', 'error', '-select_streams', 'V:0',
            '-show_entries', 'stream=pix_fmt,color_range,color_space,color_transfer,color_primaries:stream_side_data',
            '-of', 'json', '-protocol_whitelist', 'file,pipe', str(source)],
            capture_output=True, text=True, timeout=min(timeout, 30))
    except subprocess.TimeoutExpired as exc:
        raise error_type('Render timed out inspecting source color format.') from exc
    if result.returncode:
        raise error_type(result.stderr or 'Cannot inspect source color format.')
    try:
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or not isinstance(data.get('streams'), list) or len(data['streams']) != 1:
            raise ValueError('Expected one video stream')
        return (source_color_from_stream(data['streams'][0], input_encoding=input_encoding)
                if input_encoding == 'apple_log_bt2020' else source_color_from_stream(data['streams'][0]))
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise error_type(f'Cannot interpret source color metadata: {exc}') from exc


def source_rgb_filters(source, timeout, error_type, source_color=None):
    """Use declared YUV matrix/range; transfer normalization is owned float math."""
    info = source_color or read_source_color(source, timeout, error_type)
    if info.is_rgb:
        return ['format=rgb48le']
    return [f'zscale=matrixin={info.matrix}:matrix=gbr:rangein={info.range}:range=full',
            'format=gbrp16le', 'format=rgb48le']


def decoder_identity(cancel, error_type, cancelled_type):
    """Content identity for geometry/render caches when the external binary changes."""
    executable = shutil.which(ffmpeg_binary())
    if executable is None:
        raise error_type('Cannot locate the external FFmpeg executable.')
    from pathlib import Path
    path = Path(executable).resolve(strict=True)
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            if cancel is not None and cancel.is_set():
                raise cancelled_type('Render cancelled.')
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise error_type('FFmpeg changed while its restoration cache identity was being checked.')
    return str(path), digest.hexdigest()


def active_picture_size(source, spatial_filters, width, height, timeout, cancel,
                        error_type, cancelled_type):
    """Measure the actual unpadded decoder geometry without guessing SAR/rotation.

    FFmpeg resolves its own autorotation, scale-expression truncation and aspect
    fit rounding. A one-frame framecrc header exposes those exact dimensions;
    its pixel payload is discarded and is never used as a restoration input.
    Both the probe and the delivered decoder use the identical spatial chain.
    """
    began = time.monotonic()
    process = None
    with tempfile.TemporaryFile() as result, tempfile.TemporaryFile() as log:
        try:
            if cancel is not None and cancel.is_set():
                raise cancelled_type('Render cancelled.')
            process = subprocess.Popen([
                ffmpeg_binary(), '-v', 'error', '-nostdin', '-threads', '1',
                '-protocol_whitelist', 'file,pipe', '-i', str(source), '-map', '0:V:0', '-an',
                '-vf', ','.join(spatial_filters), '-frames:v', '1', '-c:v', 'rawvideo',
                '-threads', '1', '-pix_fmt', 'rgb48le', '-f', 'framecrc', 'pipe:1'],
                stdout=result, stderr=log)
            while process.poll() is None:
                if max(os.fstat(result.fileno()).st_size, os.fstat(log.fileno()).st_size) > 65536:
                    raise error_type('Active picture probe exceeded its output budget.')
                if cancel is not None and cancel.is_set():
                    raise cancelled_type('Render cancelled.')
                if time.monotonic() - began > timeout:
                    raise error_type('Render timed out inspecting active picture geometry.')
                time.sleep(.025)
            if cancel is not None and cancel.is_set():
                raise cancelled_type('Render cancelled.')
            if max(os.fstat(result.fileno()).st_size, os.fstat(log.fileno()).st_size) > 65536:
                raise error_type('Active picture probe exceeded its output budget.')
            if process.returncode:
                log.seek(0)
                raise error_type(log.read(65536)[-6000:].decode(errors='replace')
                                 or 'Cannot inspect active picture geometry.')
            result.seek(0)
            header = result.read(16384).decode(errors='replace')
            matches = re.findall(r'^#dimensions 0: (\d+)x(\d+)\s*$', header, re.MULTILINE)
            if len(matches) != 1:
                raise error_type('Decoder did not report active picture dimensions.')
            active_width, active_height = map(int, matches[0])
            if not (1 <= active_width <= width and 1 <= active_height <= height):
                raise error_type('Active picture geometry exceeds the project canvas.')
            return active_width, active_height
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()


def normalized_restoration(settings, active_size, width, height, cancel, cancelled_type):
    """Restore active picture only; synthetic letterbox pixels never enter kernels."""
    from .restoration import apply_restoration
    active_width, active_height = active_size
    left, top = (width - active_width) // 2, (height - active_height) // 2
    if min(active_width, active_height) < 1 or min(left, top) < 0:
        raise ValueError('Invalid restoration active picture geometry')

    def check():
        if cancel is not None and cancel.is_set():
            raise cancelled_type('Render cancelled.')

    def transform(rgb):
        check()
        picture = rgb[top:top + active_height, left:left + active_width]
        restored = apply_restoration(picture, settings, cancel=check)
        check()
        if (not isinstance(restored, np.ndarray) or restored.shape != picture.shape
                or restored.dtype != np.float32 or not np.isfinite(restored).all()):
            raise ValueError('Invalid float restoration output')
        if (active_width, active_height) == (width, height):
            return restored
        canvas = rgb.copy()
        canvas[top:top + active_height, left:left + active_width] = restored
        return canvas
    return transform


def grade_segment(source, target, grade, width, height, trim_filters, rate,
                  timeout, cancel, error_type, cancelled_type, sample_indices=None,
                  decode_size=None, pre_grade_transform=None, normalized_transform=None,
                  preserve_decoded_frames=False):
    """Preserve compiler frame selection, grading one RGB48 frame at a time."""
    processes = []
    finished = threading.Event()
    failures = []
    began = time.monotonic()

    def stop():
        for process in processes:
            if process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass

    def check_grade():
        # Spatial matte processing checks this between bounded tiles rather
        # than waiting for the entire frame after the watcher stops FFmpeg.
        if cancel is not None and cancel.is_set():
            raise cancelled_type('Render cancelled.')
        if failures:
            raise failures[0]
        if time.monotonic() - began > timeout:
            raise error_type('Render timed out during float grading.')

    def watch():
        while not finished.wait(.025):
            if cancel is not None and cancel.is_set():
                failures.append(cancelled_type('Render cancelled.'))
                stop()
                return
            if time.monotonic() - began > timeout:
                failures.append(error_type('Render timed out during float grading.'))
                stop()
                return

    with tempfile.TemporaryFile() as decode_log, tempfile.TemporaryFile() as encode_log:
        watcher = None
        try:
            decoder = subprocess.Popen([
                ffmpeg_binary(), '-v', 'error', '-nostdin', '-threads', '1', '-protocol_whitelist', 'file,pipe', '-i', str(source),
                '-map', '0:V:0', '-an', '-vf', ','.join(trim_filters + ['format=rgb48le']),
                '-threads', '1', *(['-fps_mode', 'passthrough'] if preserve_decoded_frames else []),
                '-f', 'rawvideo', '-pix_fmt', 'rgb48le', 'pipe:1'],
                stdout=subprocess.PIPE, stderr=decode_log)
            processes.append(decoder)
            encoder = subprocess.Popen([
                ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-threads', '1',
                '-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-s', f'{width}x{height}',
                '-r', rate, '-color_range', 'pc', '-color_primaries', 'bt709',
                '-color_trc', 'bt709', '-colorspace', 'rgb',
                '-protocol_whitelist', 'file,pipe', '-i', 'pipe:0', '-an', '-c:v', 'ffv1', '-level', '3',
                '-pix_fmt', 'gbrp16le', '-threads', '1', '-color_range', 'pc',
                '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'rgb', str(target)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=encode_log)
            processes.append(encoder)
            watcher = threading.Thread(target=watch, daemon=True)
            watcher.start()
            decode_width, decode_height = decode_size or (width, height)
            frame_bytes = decode_width * decode_height * 6
            # Retime samples a globally normalized source-frame grid. Never
            # restart the sampling phase at a split or fractional source trim.
            requested = iter(sample_indices) if sample_indices is not None else None
            wanted = next(requested, None) if requested is not None else None
            decoded_index = 0
            emitted = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise cancelled_type('Render cancelled.')
                data = decoder.stdout.read(frame_bytes)
                if not data:
                    break
                if len(data) != frame_bytes:
                    raise error_type('Incomplete RGB48 frame from decoder.')
                source_frame_index = decoded_index
                decoded_index += 1
                repeats = 1
                if requested is not None:
                    repeats = 0
                    while wanted == source_frame_index:
                        repeats += 1
                        wanted = next(requested, None)
                    if not repeats:
                        continue
                rgb = np.frombuffer(data, dtype='<u2').reshape(decode_height, decode_width, 3).astype(np.float32)
                rgb /= np.float32(65535)
                if pre_grade_transform is not None:
                    rgb = pre_grade_transform(rgb, source_frame_index)
                    if rgb.shape != (height, width, 3) or rgb.dtype != np.float32 or not np.isfinite(rgb).all():
                        raise error_type('Invalid float picture transform output.')
                options = {}
                if normalized_transform is not None:
                    options['normalized_transform'] = normalized_transform
                if grade.qualifier.enabled and (grade.qualifier.matte_smoothing
                                                or grade.qualifier.matte_morphology):
                    options['cancel'] = check_grade
                # Animation belongs to each OUTPUT timeline frame, not the
                # decoded source frame. Slow motion may repeat one source
                # picture but must evaluate a different window on every repeat.
                # Preserve the historical grade-once path exactly for static
                # settings, including disabled creative corrections.
                animated = bool(grade.qualifier.window_keyframes)
                if not animated:
                    graded = color.apply_grade(rgb, grade, **options)
                    if cancel is not None and cancel.is_set():
                        raise cancelled_type('Render cancelled.')
                    encoded = np.rint(np.clip(graded, 0, 1) * np.float32(65535)).astype('<u2')
                for _ in range(repeats):
                    if animated:
                        from .window_animation import evaluate_grade
                        check_grade()
                        frame_grade = evaluate_grade(grade, emitted)
                        graded = color.apply_grade(rgb, frame_grade, **options)
                        check_grade()
                        # The sole post-restoration/grade clamp remains the
                        # SDR intermediate boundary, never a working clamp.
                        encoded = np.rint(np.clip(graded, 0, 1) * np.float32(65535)).astype('<u2')
                    encoder.stdin.write(encoded.tobytes())
                    emitted += 1
            if requested is not None and wanted is not None:
                raise error_type('Source ended before the requested retime frame.')
            if emitted == 0:
                raise error_type('No picture frames were decoded for this clip.')
            encoder.stdin.close()
            decoder.wait()
            encoder.wait()
            if failures:
                raise failures[0]
            if decoder.returncode or encoder.returncode:
                decode_log.seek(0); encode_log.seek(0)
                detail = (decode_log.read() + encode_log.read()).decode(errors='replace')[-6000:]
                if 'No such filter' in detail and 'zscale' in detail:
                    detail = 'This FFmpeg build needs the zscale (libzimg) filter for precise SDR color conversion. ' + detail
                raise error_type(detail)
        except Exception as exc:
            if failures:
                raise failures[0] from exc
            if isinstance(exc, error_type):
                raise
            raise error_type(f'Float grading failed: {exc}') from exc
        finally:
            finished.set()
            stop()
            if watcher is not None:
                watcher.join(timeout=2)
            for process in processes:
                process.wait()
                if process.stdout:
                    process.stdout.close()
                if process.stdin and not process.stdin.closed:
                    try:
                        process.stdin.close()
                    except BrokenPipeError:
                        pass

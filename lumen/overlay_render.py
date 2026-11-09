"""Deterministic, frame-indexed sticker composition shared by preview and export.

Transforms are evaluated in project frames. QPainter composites premultiplied
8-bit RGBA in source-over order; the boundary exports straight RGBA to a lossless
FFV1/BGRA stream. This is an SDR code-value compositor, not a linear-light HDR
renderer. No asset name or user-authored expression enters an FFmpeg filter.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import hmac
import math
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter

from .media import ffmpeg_binary
from .vignette_shape import shaped_vignette_filter, validated_vignette_shape

MAX_CANVAS_PIXELS = 16_777_216


class OverlayRenderError(RuntimeError):
    pass


class OverlayRenderCancelled(OverlayRenderError):
    pass


def validate_canvas(project):
    """Refuse an excessive full-frame allocation before creating Qt/codec data."""
    if (type(project.width) is not int or type(project.height) is not int
            or project.width <= 0 or project.height <= 0
            or project.width * project.height > MAX_CANVAS_PIXELS):
        raise OverlayRenderError(
            'Sticker composition supports at most 16,777,216 canvas pixels. '
            'Choose a smaller project resolution, such as 3840 x 2160 UHD.')


@dataclass(frozen=True)
class OverlayPose:
    x: float
    y: float
    scale: float
    opacity: float
    local_frame: int


def evaluate_overlay(overlay, timeline_frame: int) -> OverlayPose | None:
    """Evaluate the half-open active interval and linearly interpolate keyframes.

    X/Y are the asset's top-left corner in project pixels, scale is uniform, and
    keyframe frame numbers are relative to overlay.start. Outside the keyframe
    interval the nearest endpoint is held. Integer frame selection avoids drift
    at 24, 30, and 30000/1001 fps alike.
    """
    if type(timeline_frame) is not int:
        raise OverlayRenderError('Overlay evaluation requires an integer project frame.')
    local = timeline_frame - overlay.start
    if not overlay.enabled or not 0 <= local < overlay.duration:
        return None
    points = overlay.keyframes
    if not points:
        raise OverlayRenderError('An overlay needs at least one keyframe.')
    right = bisect_right([point.frame for point in points], local)
    if right == 0:
        left = after = points[0]
    elif right == len(points):
        left = after = points[-1]
    else:
        left, after = points[right - 1], points[right]
    ratio = 0.0 if left.frame == after.frame else (local - left.frame) / (after.frame - left.frame)
    values = [float(getattr(left, name)) + ratio * (float(getattr(after, name)) - float(getattr(left, name)))
              for name in ('x', 'y', 'scale', 'opacity')]
    return OverlayPose(*values, local_frame=local)


def animation_frame(asset, local_frame: int) -> int:
    """Select animation frame; non-looping animations hold their final image."""
    index = local_frame // asset.frame_duration
    return index % len(asset.frames) if asset.loop else min(index, len(asset.frames) - 1)


class FrameImageCache:
    """Bound decoded image storage, even for a long sequence or many overlays."""
    def __init__(self, max_bytes=64 * 1024 * 1024, cancel=None):
        self.max_bytes = max_bytes
        self.cancel = cancel
        self._images = OrderedDict()
        self._bytes = 0

    def get(self, path, expected_sha256=None):
        key = (str(path), expected_sha256 or '')
        if key in self._images:
            image = self._images.pop(key)
            self._images[key] = image
            return image
        from .sticker_assets import read_sticker_frame
        data = read_sticker_frame(Path(path), cancel=self.cancel)
        if expected_sha256 is not None and not hmac.compare_digest(hashlib.sha256(data).hexdigest(), expected_sha256):
            raise OverlayRenderError('Sticker frame changed after verification. Import the sticker again to approve new content.')
        image = QImage.fromData(data, 'PNG')
        if image.isNull():
            raise OverlayRenderError(f'Cannot decode verified sticker frame: {Path(path).name}')
        image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        size = image.sizeInBytes()
        while self._images and self._bytes + size > self.max_bytes:
            _, removed = self._images.popitem(last=False)
            self._bytes -= removed.sizeInBytes()
        if size <= self.max_bytes:
            self._images[key] = image
            self._bytes += size
        return image


def compose_frame(project, timeline_frame: int, assets, image_cache=None) -> QImage:
    """Return one project-sized straight RGBA image, transparent outside stickers.

    ``assets`` maps overlay IDs to already verified StickerAsset records.
    Callers must not reuse a decoded-image cache across asset verification runs.
    """
    validate_canvas(project)
    image_cache = image_cache if image_cache is not None else FrameImageCache()
    canvas = QImage(project.width, project.height, QImage.Format.Format_ARGB32_Premultiplied)
    if canvas.isNull():
        raise OverlayRenderError('Cannot allocate the overlay frame.')
    canvas.fill(0)
    painter = QPainter(canvas)
    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        # Python's sort is stable: equal z_order values preserve project order.
        for overlay in sorted(project.overlays, key=lambda item: item.z_order):
            pose = evaluate_overlay(overlay, timeline_frame)
            if pose is None or pose.opacity <= 0:
                continue
            asset = assets[overlay.id]
            width, height = asset.width * pose.scale, asset.height * pose.scale
            if pose.x >= project.width or pose.y >= project.height or pose.x + width <= 0 or pose.y + height <= 0:
                continue
            index = animation_frame(asset, pose.local_frame)
            if len(asset.frame_sha256) != len(asset.frames):
                raise OverlayRenderError('Sticker frame identity is missing. Verify the asset before rendering.')
            source = image_cache.get(asset.frames[index], asset.frame_sha256[index])
            if source.width() != asset.width or source.height() != asset.height:
                raise OverlayRenderError('Sticker frame dimensions changed after verification.')
            painter.setOpacity(pose.opacity)
            painter.drawImage(QRectF(pose.x, pose.y, width, height), source)
    finally:
        painter.end()
    return canvas.convertToFormat(QImage.Format.Format_RGBA8888)


def effect_filters(effects) -> list[str]:
    """Build ordered real FFmpeg effects from a fixed, numeric-only vocabulary.

    Zero amount and disabled effects are exact graph bypasses. Blur amount is
    Gaussian sigma in project pixels; vignette amount maps to 0..pi/2 radians.
    """
    result = []
    shaped = False
    for effect in effects:
        limit = {'blur': 30, 'vignette': 1}.get(effect.kind)
        amount = effect.amount
        if limit is None or isinstance(amount, bool) or not isinstance(amount, (int, float)) or not 0 <= amount <= limit or not math.isfinite(amount):
            raise OverlayRenderError('Invalid clip effect kind or amount.')
        try:
            radius, feather = validated_vignette_shape(effect)
        except ValueError as exc:
            raise OverlayRenderError(str(exc)) from exc
        if not effect.enabled or amount == 0:
            continue
        if effect.kind == 'blur':
            result.append(f'gblur=sigma={format(float(amount), ".12g")}:steps=3')
        elif radius != 1 or feather != 1:
            shaped = True
            # Preview/H.264 intentionally uses the same new RGB falloff as
            # native RGB16, but at its explicit eight-bit effect boundary.
            result.extend(['format=gbrp', shaped_vignette_filter(amount, radius, feather),
                           'format=gbrp'])
        else:
            angle = amount * math.pi / 2
            result.append(f'vignette=angle={format(angle, ".12g")}:mode=forward:eval=init:dither=0')
    if shaped:
        # A mixed legacy/new chain must not negotiate an implicit RGB/YUV
        # conversion between effects. vf_vignette supports packed RGB8, not
        # planar GBR8. Pin only these newly opted-in chains to RGB throughout;
        # pure legacy/default projects retain their exact previous filters.
        ordered = []
        for item in result:
            ordered.extend(['format=rgb24', item, 'format=gbrp']
                           if item.startswith('vignette=') else [item])
        if ordered[0] != 'format=gbrp':
            ordered.insert(0, 'format=gbrp')
        if ordered[-1] != 'format=gbrp':
            ordered.append('format=gbrp')
        result = ordered
    return result


def render_overlay_segment(project, assets, target, timeout, cancel=None, progress=None, *, frame_window=None):
    """Stream bounded-memory frames into FFV1; reap on failure/cancel/timeout.

    The caller supplies a unique temporary target and owns atomic publication.
    This function deletes an incomplete target, never publishes cache entries.
    """
    validate_canvas(project)
    target = Path(target)
    if cancel is not None and cancel.is_set():
        raise OverlayRenderCancelled('Render cancelled.')
    process = None
    watcher = None
    finished = threading.Event()
    failures = []
    began = time.monotonic()

    def stop():
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass

    def check():
        if cancel is not None and cancel.is_set():
            raise OverlayRenderCancelled('Render cancelled.')
        if time.monotonic() - began > timeout:
            raise OverlayRenderError('Render timed out composing stickers.')

    def watch():
        while not finished.wait(.025):
            try:
                check()
            except OverlayRenderError as exc:
                failures.append(exc)
                stop()
                return

    succeeded = False
    with tempfile.TemporaryFile() as log:
        try:
            process = subprocess.Popen([
                ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-threads', '1',
                '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', f'{project.width}x{project.height}',
                '-r', f'{project.fps_num}/{project.fps_den}', '-protocol_whitelist', 'file,pipe',
                '-i', 'pipe:0', '-an', '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'bgra', '-threads', '1',
                '-color_range', 'pc', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'rgb',
                str(target)], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
            watcher = threading.Thread(target=watch, daemon=True)
            watcher.start()
            cache = FrameImageCache(cancel=cancel)
            first, end = frame_window if frame_window is not None else (0, project.duration_frames)
            for frame in range(first, end):
                check()
                image = compose_frame(project, frame, assets, cache)
                check()
                # RGBA rows are width * 4 bytes and therefore need no Qt padding.
                process.stdin.write(image.constBits())
                if progress:
                    progress((frame - first + 1) / (end - first))
            process.stdin.close()
            process.wait()
            if failures:
                raise failures[0]
            check()
            if process.returncode:
                log.seek(0)
                raise OverlayRenderError(log.read().decode(errors='replace')[-6000:] or 'Sticker encoding failed.')
            succeeded = True
        except Exception as exc:
            if failures:
                raise failures[0] from exc
            if cancel is not None and cancel.is_set():
                raise OverlayRenderCancelled('Render cancelled.') from exc
            if isinstance(exc, OverlayRenderError):
                raise
            raise OverlayRenderError(f'Sticker composition failed: {exc}') from exc
        finally:
            finished.set()
            stop()
            if watcher is not None:
                watcher.join(timeout=2)
            if process is not None:
                process.wait()
                if process.stdin and not process.stdin.closed:
                    try:
                        process.stdin.close()
                    except (BrokenPipeError, OSError):
                        pass
            if not succeeded:
                target.unlink(missing_ok=True)

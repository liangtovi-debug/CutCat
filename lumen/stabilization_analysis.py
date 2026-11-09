"""Local, bounded motion analysis. Proxy pixels never become exported pixels.

The external FFmpeg must provide libvidstab. Two offline passes analyze an
owned snapshot on the *whole source-origin project-FPS grid*. The debug file
is raw current-to-previous motion, before smoothing; column zero is unused,
column four is ignored zoom, and column five is an error flag, NOT confidence.

The confidence/cut tests are conservative heuristics, not object tracking or
proof of camera motion. Moving subjects, repeated textures, flashes, rolling
shutter, and gradual transitions can still confuse them. Analysis estimates
are not promised bit-identical across FFmpeg/libvidstab builds or platforms.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from typing import Callable

import numpy as np

from .file_identity import is_plain_file, path_matches_descriptor, same_file_state
from .media import ffmpeg_binary, ffprobe_binary

MAX_FRAMES = 18_000
MAX_SECONDS = 600.0
MAX_SOURCE_BYTES = 16 * 1024**3
MAX_CACHE_BYTES = 4 * 1024**2
ALGORITHM_VERSION = 'vidstab-proxy-raw-v1'
# Deliberately exclude playlists, concat scripts, devices, and network demuxers.
CONTAINERS = 'matroska,webm,mov,avi,mpeg,mpegts,ogg,asf,nut,flv'
DETECT = 'vidstabdetect=result=detect.trf:shakiness=5:accuracy=15:stepsize=6:mincontrast=0.3:show=0'
TRANSFORM = ('vidstabtransform=input=detect.trf:smoothing=0:optzoom=0:zoom=0:'
             'relative=0:debug=1')
ALGORITHM_SETTINGS = {'version': ALGORITHM_VERSION, 'detector': DETECT,
                      'raw_transform': TRANSFORM, 'proxy_max': [640, 480],
                      'quality_max': [96, 72], 'texture_floor': 0.012,
                      'residual_limit': 0.12, 'cut_residual': 0.24,
                      'cut_histogram': 0.28, 'confidence_threshold': 0.5,
                      'max_frames': MAX_FRAMES, 'max_seconds': MAX_SECONDS}


class StabilizationAnalysisError(RuntimeError):
    pass


class StabilizationAnalysisCancelled(StabilizationAnalysisError):
    pass


@dataclass(frozen=True)
class SourceGeometry:
    coded_width: int
    coded_height: int
    sar_num: int
    sar_den: int
    rotation: int
    displayed_width: float
    displayed_height: float
    active_width: int
    active_height: int
    proxy_width: int
    proxy_height: int
    stream_index: int
    duration: float
    field_order: str

    @property
    def normalization_filters(self) -> list[str]:
        """For a decoder retaining FFmpeg's default metadata autorotation.

        Scale explicit dimensions, then assign square pixels. This folds the
        actual SAR into the active-picture aspect; never pad before warping.
        """
        return [f'scale={self.active_width}:{self.active_height}', 'setsar=1']

    def proxy_filter_chain(self, fps_num: int, fps_den: int) -> list[str]:
        rate = _rate(fps_num, fps_den)
        return ['setpts=PTS-STARTPTS', f'fps=fps={rate}:start_time=0',
                f'scale={self.proxy_width}:{self.proxy_height}', 'setsar=1',
                'format=yuv420p']

    @property
    def proxy_to_active(self) -> np.ndarray:
        sx = self.active_width / self.proxy_width
        sy = self.active_height / self.proxy_height
        return np.array([[sx, 0, .5 * (sx - 1)], [0, sy, .5 * (sy - 1)],
                         [0, 0, 1]], dtype=np.float64)


@dataclass(frozen=True)
class MotionAnalysis:
    raw_rows: np.ndarray
    geometry: SourceGeometry
    cut_flags: np.ndarray
    confidence: np.ndarray
    frame_count: int
    provenance: dict
    statistics: dict
    cache_key: str
    snapshot_path: Path
    cache_hit: bool = False
    _snapshot_owner: object = field(default=None, repr=False, compare=False)

    @property
    def shot_starts(self) -> np.ndarray:
        return self.cut_flags


class _Budget:
    def __init__(self, cancel=None, timeout=300.0):
        if not math.isfinite(timeout) or timeout <= 0:
            raise StabilizationAnalysisError('Analysis timeout must be finite and positive.')
        self.cancel, self.deadline = cancel, time.monotonic() + timeout

    def check(self):
        if self.cancel is not None and self.cancel.is_set():
            raise StabilizationAnalysisCancelled('Stabilization analysis cancelled.')
        if time.monotonic() >= self.deadline:
            raise StabilizationAnalysisError('Stabilization analysis timed out.')


def _run(args, *, cwd: Path, budget: _Budget, on_tick: Callable | None = None) -> bytes:
    """No shell, bounded captured output, cancellation and unconditional reap."""
    budget.check()
    process = None
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            process = subprocess.Popen(args, cwd=str(cwd), stdin=subprocess.DEVNULL,
                                       stdout=out, stderr=err)
            while process.poll() is None:
                budget.check()
                if out.tell() > 2 * 1024**2 or err.tell() > 8 * 1024**2:
                    raise StabilizationAnalysisError('Unexpectedly large analysis process diagnostic.')
                if on_tick is not None:
                    on_tick()
                time.sleep(.025)
            budget.check()
            # Fast children can exit before the polling loop executes once.
            # Enforce both output limits again before interpreting any result.
            if out.tell() > 2 * 1024**2 or err.tell() > 8 * 1024**2:
                raise StabilizationAnalysisError('Unexpectedly large analysis process diagnostic.')
            if on_tick is not None:
                on_tick()
            if process.returncode:
                err.seek(max(0, err.tell() - 6000))
                detail = err.read(6000).decode(errors='replace').strip()
                raise StabilizationAnalysisError('Local stabilization process failed: ' + detail)
            out.seek(0, os.SEEK_END)
            if out.tell() > 2 * 1024**2:
                raise StabilizationAnalysisError('Unexpectedly large analysis process response.')
            out.seek(0)
            return out.read()
        except OSError as exc:
            raise StabilizationAnalysisError(f'Cannot run local stabilization tools: {exc}') from exc
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()


def _rate(num, den) -> str:
    if (type(num) is not int or type(den) is not int or den <= 0 or
            num <= 0 or not 1 <= Fraction(num, den) <= 240):
        raise StabilizationAnalysisError('Analysis needs a project frame rate from 1 to 240 fps.')
    return str(Fraction(num, den)) if den != 1 else str(num)


def _fit_even(width: float, height: float, max_width: int, max_height: int,
              *, upscale=True) -> tuple[int, int]:
    factor = min(max_width / width, max_height / height, float('inf') if upscale else 1)
    return (max(2, int(math.floor(width * factor + 1e-8)) // 2 * 2),
            max(2, int(math.floor(height * factor + 1e-8)) // 2 * 2))


def _geometry_from_probe(data, project_width, project_height) -> SourceGeometry:
    if (type(project_width) is not int or type(project_height) is not int or
            not 2 <= project_width <= 8192 or not 2 <= project_height <= 8192):
        raise StabilizationAnalysisError('Unsupported stabilization canvas dimensions.')
    try:
        videos = [v for v in data['streams'] if v.get('codec_type') == 'video'
                  and not v.get('disposition', {}).get('attached_pic')]
        if not videos:
            raise ValueError('No picture video stream')
        v = videos[0]
        width, height = int(v['width']), int(v['height'])
        if not 2 <= width <= 16384 or not 2 <= height <= 16384:
            raise ValueError('Unsupported source dimensions')
        sar_string = v.get('sample_aspect_ratio', '1:1')
        sar = Fraction(sar_string.replace(':', '/')) if sar_string not in ('N/A', '0:1') else Fraction(1)
        if not Fraction(1, 16) <= sar <= 16:
            raise ValueError('Unsupported source sample aspect ratio')
        field_order = v.get('field_order', 'unknown')
        if field_order not in ('progressive', 'unknown'):
            raise ValueError('Interlaced sources need deinterlacing before stabilization')
        rotation = float(v.get('tags', {}).get('rotate', 0))
        for side in v.get('side_data_list', []):
            if side.get('side_data_type') == 'Display Matrix':
                rotation = float(side.get('rotation', rotation))
                # FFprobe's display matrix includes row-offset prefixes.
                matrix = side.get('displaymatrix', '')
                if matrix:
                    entries = [int(x) for line in matrix.splitlines() if ':' in line
                               for x in line.split(':', 1)[1].split()]
                    if len(entries) != 9:
                        raise ValueError('Unsupported display matrix')
                    a, b, u, c, d, vv, x, y, ww = entries
                    if (a * d - b * c <= 0 or u or vv or x or y or ww != 1073741824 or
                            abs(a * a + b * b - 65536**2) > 2**18 or
                            abs(c * c + d * d - 65536**2) > 2**18):
                        raise ValueError('Reflected, translated or scaled display matrices are unsupported')
        if not math.isfinite(rotation) or abs(rotation / 90 - round(rotation / 90)) > 1e-6:
            raise ValueError('Only right-angle display rotation is supported')
        rotation = int(round(rotation)) % 360
        display_w, display_h = width * float(sar), float(height)
        if rotation in (90, 270):
            display_w, display_h = display_h, display_w
        active_w, active_h = _fit_even(display_w, display_h, project_width, project_height)
        proxy_w, proxy_h = _fit_even(active_w, active_h, 640, 480, upscale=False)
        if min(proxy_w, proxy_h) < 64:
            raise ValueError('Stabilization needs an active picture at least 64 pixels in each dimension')
        fmt = data.get('format', {})
        duration_value = v.get('duration', fmt.get('duration'))
        duration = float(duration_value)
        if not math.isfinite(duration) or duration <= 0 or duration > MAX_SECONDS + 1e-6:
            raise ValueError(f'Stabilization supports sources up to {MAX_SECONDS:g} seconds')
        return SourceGeometry(width, height, sar.numerator, sar.denominator, rotation,
                              display_w, display_h, active_w, active_h, proxy_w, proxy_h,
                              int(v['index']), duration, field_order)
    except (TypeError, KeyError, ValueError, OverflowError, ZeroDivisionError) as exc:
        raise StabilizationAnalysisError(f'Cannot use source geometry: {exc}') from exc


def _probe(path: Path, project_width: int, project_height: int, budget: _Budget,
           ffprobe: str) -> SourceGeometry:
    output = _run([ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
                   '-format_whitelist', CONTAINERS, '-show_streams', '-show_format',
                   '-of', 'json', str(path)], cwd=path.parent, budget=budget)
    try:
        data = json.loads(output)
    except (ValueError, TypeError) as exc:
        raise StabilizationAnalysisError('Invalid source probe response.') from exc
    return _geometry_from_probe(data, project_width, project_height)


def probe_source_geometry(path, *, project_width: int, project_height: int,
                          cancel=None, timeout=30.0) -> SourceGeometry:
    budget = _Budget(cancel, timeout)
    budget.check()
    try:
        source = Path(path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise StabilizationAnalysisError('Stabilization source must be a local media file.')
        return _probe(source, project_width, project_height, budget, _binary(ffprobe_binary()))
    except OSError as exc:
        raise StabilizationAnalysisError(f'Cannot inspect stabilization source: {exc}') from exc


def _binary(value: str) -> str:
    resolved = shutil.which(value)
    if not resolved:
        raise StabilizationAnalysisError('Install an external FFmpeg/ffprobe build with libvidstab for stabilization.')
    return str(Path(resolved).resolve(strict=True))


def _hash_file(path: Path, budget: _Budget) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024**2):
            budget.check()
            digest.update(block)
    return digest.hexdigest()


def _tool_identity(binary: str, cwd: Path, budget: _Budget) -> dict:
    info = Path(binary).stat()
    digest = _hash_file(Path(binary), budget)
    version = _run([binary, '-version'], cwd=cwd, budget=budget).decode(errors='replace')
    if not same_file_state(info, Path(binary).stat()):
        raise StabilizationAnalysisError('FFmpeg changed while it was being identified.')
    # Full configuration/version output includes the linked libav* ABI versions.
    return {'sha256': digest, 'version': version.strip()}


def _snapshot(source: Path, destination: Path, budget: _Budget) -> tuple[str, os.stat_result]:
    """Hash and copy together; both external passes see the exact same owned bytes."""
    before = source.lstat()
    if not is_plain_file(before) or before.st_size <= 0 or before.st_size > MAX_SOURCE_BYTES:
        raise StabilizationAnalysisError('Stabilization needs a regular source file no larger than 16 GiB.')
    digest = hashlib.sha256()
    copied = 0
    with source.open('rb') as incoming, destination.open('xb') as outgoing:
        opened = os.fstat(incoming.fileno())
        if not path_matches_descriptor(before, opened):
            raise StabilizationAnalysisError('Source changed while opening stabilization snapshot.')
        while block := incoming.read(1024**2):
            budget.check()
            copied += len(block)
            if copied > MAX_SOURCE_BYTES:
                raise StabilizationAnalysisError('Stabilization source exceeds 16 GiB.')
            digest.update(block)
            outgoing.write(block)
        if (copied != before.st_size or not same_file_state(opened, os.fstat(incoming.fileno())) or
                not same_file_state(before, source.lstat())):
            raise StabilizationAnalysisError('Source changed while taking stabilization snapshot.')
    return digest.hexdigest(), before


def _parse_debug(path: Path, *, expected_frames=None) -> np.ndarray:
    from .stabilization_math import parse_vidstab_debug
    if not path.is_file() or path.stat().st_size > MAX_CACHE_BYTES:
        raise StabilizationAnalysisError('Missing or oversized raw vidstab debug output.')
    try:
        return parse_vidstab_debug(path.read_text(encoding='utf-8'),
                                   expected_frames=expected_frames, max_rows=MAX_FRAMES)
    except (OSError, ValueError, UnicodeError) as exc:
        raise StabilizationAnalysisError(f'Unsupported raw vidstab debug format: {exc}') from exc


def _ffmpeg_prefix(binary: str) -> list[str]:
    return [binary, '-v', 'error', '-nostdin', '-y', '-threads', '1',
            '-filter_threads', '1', '-filter_complex_threads', '1']


def _input(source: str) -> list[str]:
    return ['-protocol_whitelist', 'file,pipe', '-format_whitelist', CONTAINERS, '-i', source]


def _two_passes(binary: str, source: str, geometry: SourceGeometry, fps_num: int,
                fps_den: int, cwd: Path, budget: _Budget) -> tuple[np.ndarray, Path, tuple[int, int]]:
    canonical = ','.join(geometry.proxy_filter_chain(fps_num, fps_den))
    common = _ffmpeg_prefix(binary) + _input(source)
    _run(common + ['-map', '0:V:0', '-an', '-vf', canonical + ',' + DETECT,
                   '-frames:v', str(MAX_FRAMES + 1), '-f', 'null', 'discard'],
         cwd=cwd, budget=budget)
    quality_w, quality_h = _fit_even(geometry.proxy_width, geometry.proxy_height, 96, 72, upscale=False)
    quality_file = cwd / 'quality.gray'
    graph = (f'[0:V:0]{canonical},split=2[motion][quality];'
             f'[motion]{TRANSFORM}[discard];'
             f'[quality]scale={quality_w}:{quality_h},format=gray[gray]')
    def bound_output():
        if quality_file.exists() and quality_file.stat().st_size > (MAX_FRAMES + 1) * quality_w * quality_h:
            raise StabilizationAnalysisError('Stabilization exceeds the frame analysis bound.')
    _run(common + ['-filter_complex', graph, '-map', '[discard]', '-an',
                   '-frames:v', str(MAX_FRAMES + 1), '-f', 'null', 'discard',
                   '-map', '[gray]', '-an', '-frames:v', str(MAX_FRAMES + 1),
                   '-threads', '1', '-f', 'rawvideo', '-pix_fmt', 'gray', 'quality.gray'],
         cwd=cwd, budget=budget, on_tick=bound_output)
    size = quality_file.stat().st_size
    frame_bytes = quality_w * quality_h
    if size % frame_bytes or not 0 < size // frame_bytes <= MAX_FRAMES:
        raise StabilizationAnalysisError('Incomplete or excessive source-origin proxy frame count.')
    rows = _parse_debug(cwd / 'global_motions.trf', expected_frames=size // frame_bytes)
    if len(rows) / float(Fraction(fps_num, fps_den)) > MAX_SECONDS + 1 / float(Fraction(fps_num, fps_den)):
        raise StabilizationAnalysisError('Stabilization exceeds the source duration bound.')
    return rows, quality_file, (quality_w, quality_h)


_CAPABILITIES = set()
_CAPABILITY_LOCK = threading.Lock()


def _capability_gate(binary: str, identity: dict, budget: _Budget) -> None:
    """Execute a known-translation/count test, once per tool identity per process.

    Filter names alone cannot establish a compatible raw debug convention.
    No fixture download, compilation, network, or bundled GPL executable.
    """
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    with _CAPABILITY_LOCK:
        if key in _CAPABILITIES:
            return
    with tempfile.TemporaryDirectory(prefix='lumen-motion-gate-') as directory:
        cwd = Path(directory)
        rng = np.random.default_rng(421)
        base = np.repeat(np.repeat(rng.integers(0, 256, (20, 32, 3), dtype=np.uint8), 12, 0), 10, 1)
        for _ in range(2):
            base = ((base.astype(np.uint16) + np.roll(base, 1, 0) + np.roll(base, 1, 1)) // 3).astype(np.uint8)
        shifts = [(0, 0), (4, -2), (1, 1), (-3, 3), (-1, -1), (2, 2), (0, -2), (3, 0)]
        with (cwd / 'fixture.rgb').open('wb') as stream:
            for x, y in shifts:
                budget.check()
                stream.write(np.roll(base, (y, x), (0, 1)).tobytes())
        input_args = ['-protocol_whitelist', 'file,pipe', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                      '-s', '320x240', '-r', '30', '-i', 'fixture.rgb']
        for vf in ('format=yuv420p,' + DETECT, 'format=yuv420p,' + TRANSFORM):
            _run(_ffmpeg_prefix(binary) + input_args + ['-an', '-vf', vf, '-f', 'null', 'discard'],
                 cwd=cwd, budget=budget)
        rows = _parse_debug(cwd / 'global_motions.trf', expected_frames=len(shifts))
        expected = -np.diff(np.asarray(shifts, dtype=float), axis=0)
        if (rows[0, 5] != 1 or np.any(rows[1:, 5] != 0) or
                np.max(np.abs(rows[1:, 1:3] - expected)) > 0.65 or
                np.max(np.abs(rows[:, 3])) > 0.008):
            raise StabilizationAnalysisError('This FFmpeg/libvidstab build failed the offline raw-motion compatibility test.')
    with _CAPABILITY_LOCK:
        _CAPABILITIES.add(key)


def _quality_metrics(path: Path, shape: tuple[int, int], rows: np.ndarray,
                     geometry: SourceGeometry, budget: _Budget):
    """At most two small grayscale frames in memory; no video-sized array."""
    width, height = shape
    yy, xx = np.indices((height, width), dtype=np.float64)
    # Matching libvidstab rotation convention is opposite image-space SE(2).
    sx, sy = width / geometry.proxy_width, height / geometry.proxy_height
    confidence = np.ones(len(rows), dtype=np.float64)
    cuts = np.zeros(len(rows), dtype=bool)
    cuts[0] = True
    confidence[0] = 0
    textures, residuals, histograms = [], [], []
    previous = None
    with path.open('rb') as stream:
        for i, row in enumerate(rows):
            budget.check()
            raw = stream.read(width * height)
            if len(raw) != width * height:
                raise StabilizationAnalysisError('Incomplete confidence proxy frame.')
            current = np.frombuffer(raw, dtype=np.uint8).reshape(height, width).astype(np.float64) / 255
            texture = float((np.mean(np.abs(np.diff(current, axis=0))) + np.mean(np.abs(np.diff(current, axis=1)))) / 2)
            textures.append(texture)
            residual = hist_distance = 0.0
            if previous is not None:
                # Evaluate current -> previous displacement at each current pixel.
                # Center convention equals libvidstab width/2, height/2.
                px = (xx + .5) / sx - .5 - geometry.proxy_width / 2
                py = (yy + .5) / sy - .5 - geometry.proxy_height / 2
                co, si = math.cos(-row[3]), math.sin(-row[3])
                mapped_x = (co * px - si * py + geometry.proxy_width / 2 + row[1] + .5) * sx - .5
                mapped_y = (si * px + co * py + geometry.proxy_height / 2 + row[2] + .5) * sy - .5
                x0, y0 = np.floor(mapped_x).astype(int), np.floor(mapped_y).astype(int)
                valid = (x0 >= 0) & (x0 < width - 1) & (y0 >= 0) & (y0 < height - 1)
                if valid.mean() < .60:
                    residual = 1.0
                else:
                    xv, yv = x0[valid], y0[valid]
                    fx, fy = mapped_x[valid] - xv, mapped_y[valid] - yv
                    reference = (previous[yv, xv] * (1-fx)*(1-fy) + previous[yv, xv+1]*fx*(1-fy) +
                                 previous[yv+1, xv]*(1-fx)*fy + previous[yv+1, xv+1]*fx*fy)
                    residual = float(np.mean(np.abs(current[valid] - reference)))
                h1 = np.histogram(current, bins=16, range=(0, 1))[0] / current.size
                h0 = np.histogram(previous, bins=16, range=(0, 1))[0] / previous.size
                hist_distance = float(np.abs(h1-h0).sum() / 2)
                # Similar-histogram cuts are caught by high aligned residual;
                # histogram change helps distinguish sharp exposure/scene cuts.
                cuts[i] = residual > .24 or (hist_distance > .28 and residual > .10)
                if (row[5] != 0 or min(texture, textures[i-1]) < .012 or
                        residual > .12 or cuts[i]):
                    confidence[i] = 0
            residuals.append(residual)
            histograms.append(hist_distance)
            previous = current
    statistics = {'low_confidence_frames': int(np.count_nonzero(confidence < .5)),
                  'cut_count': int(np.count_nonzero(cuts[1:])),
                  'no_fields_frames': int(np.count_nonzero(rows[:, 5])),
                  'texture_min': float(min(textures)),
                  'residual_max': float(max(residuals)),
                  'histogram_distance_max': float(max(histograms)),
                  'confidence_kind': 'binary conservative heuristic; 1 usable, 0 reset',
                  'confidence_threshold': .5}
    return cuts, confidence, statistics


def _cache_load(path: Path, key: str, geometry: SourceGeometry, provenance: dict, snapshot_path: Path):
    try:
        if not path.is_file() or path.stat().st_size > MAX_CACHE_BYTES:
            return None
        with zipfile.ZipFile(path) as archive:
            if sum(member.file_size for member in archive.infolist()) > MAX_CACHE_BYTES:
                return None
        with np.load(path, allow_pickle=False) as saved:
            if set(saved.files) != {'rows', 'cuts', 'confidence', 'metadata'}:
                return None
            metadata = json.loads(str(saved['metadata'].item()))
            rows = np.asarray(saved['rows'], dtype=np.float64)
            cuts = np.asarray(saved['cuts'])
            confidence = np.asarray(saved['confidence'], dtype=np.float64)
        n = len(rows)
        if (metadata['cache_key'] != key or metadata['provenance'] != provenance or
                rows.shape != (n, 6) or not 0 < n <= MAX_FRAMES or
                cuts.shape != (n,) or cuts.dtype != np.bool_ or confidence.shape != (n,) or
                not np.isfinite(rows).all() or not np.isfinite(confidence).all() or
                np.any(rows[:, 0] != 0) or np.any(rows[:, 5] != np.floor(rows[:, 5])) or
                np.any((confidence != 0) & (confidence != 1)) or
                not cuts[0] or confidence[0] != 0):
            return None
        # All files in this application-owned cache are regenerable artifacts.
        # Corrupt entries fail closed to recomputation; never load project paths.
        return MotionAnalysis(rows, geometry, cuts, confidence, n, provenance,
                              metadata['statistics'], key, snapshot_path, True)
    except (OSError, ValueError, KeyError, TypeError, EOFError, zipfile.BadZipFile):
        return None


def _cache_save(path: Path, result: MotionAnalysis, budget: _Budget):
    metadata = json.dumps({'cache_key': result.cache_key, 'provenance': result.provenance,
                           'statistics': result.statistics}, sort_keys=True)
    fd, temporary = tempfile.mkstemp(prefix='.motion-', suffix='.partial', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            np.savez_compressed(stream, rows=result.raw_rows, cuts=result.cut_flags,
                                confidence=result.confidence, metadata=metadata)
            stream.flush()
            os.fsync(stream.fileno())
        budget.check()
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _persist_snapshot(owned: Path, cache: Path, digest: str, budget: _Budget) -> Path:
    """Atomic content-addressed owned copy, rehashed rather than trusted by name."""
    destination = cache / (digest + '.source.media')
    if destination.exists() and not destination.is_symlink():
        before = destination.stat()
        if (is_plain_file(before) and before.st_size == owned.stat().st_size and
                _hash_file(destination, budget) == digest and same_file_state(before, destination.stat())):
            return destination
    fd, name = tempfile.mkstemp(prefix='.source-', suffix='.partial', dir=str(cache))
    try:
        with os.fdopen(fd, 'wb') as output, owned.open('rb') as incoming:
            while block := incoming.read(1024**2):
                budget.check()
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        budget.check()
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)
    return destination


def analyze_motion(path, *, fps_num: int, fps_den: int, project_width: int,
                   project_height: int, cache_dir=None, cancel=None,
                   timeout=300.0) -> MotionAnalysis:
    """Analyze a complete source; trim/speed/frame-floor mapping belongs to caller.

    Limits are 600 seconds, 18,000 canonical frames and a 16 GiB local source.
    `cache_dir` must be an application-owned directory, never a serialized
    project analysis path. A full content hash is computed even on cache hits.
    Cancellation/timeout does not publish a partial cache entry.
    """
    _rate(fps_num, fps_den)
    budget = _Budget(cancel, timeout)
    budget.check()
    binary, ffprobe = _binary(ffmpeg_binary()), _binary(ffprobe_binary())
    try:
        # Resolve once; reject special files when taking the actual snapshot.
        source = Path(path).expanduser().resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix='lumen-motion-') as directory:
            cwd = Path(directory)
            owned = cwd / 'source.media'
            source_hash, source_state = _snapshot(source, owned, budget)
            geometry = _probe(owned, project_width, project_height, budget, ffprobe)
            fps = Fraction(fps_num, fps_den)
            if geometry.duration * float(fps) > MAX_FRAMES + 1:
                raise StabilizationAnalysisError('Source exceeds 18,000 project-FPS analysis frames.')
            tool_identity = _tool_identity(binary, cwd, budget)
            probe_identity = _tool_identity(ffprobe, cwd, budget)
            _capability_gate(binary, tool_identity, budget)
            provenance = {'algorithm': ALGORITHM_SETTINGS, 'source_sha256': source_hash,
                          'source_bytes': source_state.st_size, 'ffmpeg': tool_identity,
                          'ffprobe': probe_identity, 'fps': [fps.numerator, fps.denominator],
                          'geometry': asdict(geometry),
                          'sampling': 'setpts=PTS-STARTPTS,fps=start_time=0; whole source; before floor(O+kR)'}
            key = hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()
            cache_file = None
            snapshot_owner = None
            if cache_dir is not None:
                cache = Path(cache_dir)
                cache.mkdir(parents=True, exist_ok=True)
                snapshot_path = _persist_snapshot(owned, cache, source_hash, budget)
                cache_file = cache / (key + '.npz')
                cached = _cache_load(cache_file, key, geometry, provenance, snapshot_path)
                if cached is not None:
                    budget.check()
                    if not same_file_state(source_state, source.lstat()):
                        raise StabilizationAnalysisError('Source changed during cached stabilization analysis.')
                    return cached
            rows, quality_path, quality_shape = _two_passes(binary, str(owned), geometry,
                                                            fps_num, fps_den, cwd, budget)
            cuts, confidence, statistics = _quality_metrics(quality_path, quality_shape, rows, geometry, budget)
            statistics['frame_count'] = len(rows)
            if not same_file_state(source_state, source.lstat()):
                raise StabilizationAnalysisError('Source changed during stabilization analysis.')
            budget.check()
            if cache_dir is None:
                snapshot_owner = tempfile.TemporaryDirectory(prefix='lumen-motion-source-')
                try:
                    snapshot_path = _persist_snapshot(owned, Path(snapshot_owner.name), source_hash, budget)
                except Exception:
                    snapshot_owner.cleanup()
                    raise
            result = MotionAnalysis(rows, geometry, cuts, confidence, len(rows), provenance, statistics, key,
                                    snapshot_path, False, snapshot_owner)
            if cache_file is not None:
                _cache_save(cache_file, result, budget)
            budget.check()
            return result
    except StabilizationAnalysisError:
        raise
    except (OSError, ValueError, TypeError, OverflowError, subprocess.SubprocessError) as exc:
        raise StabilizationAnalysisError(f'Cannot analyze stabilization: {exc}') from exc

"""Production-runtime integration oracle: complete graph, then exact raw slice.

This is independent of the range compile path, not an independent application
implementation. The immutable-code oracle supplies that complementary check.
No LUMEN_REFERENCE_* variable is read and no runtime is redirected here.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
from scripts.runtime_timing import new_timing_token, timed_stage


def full_av(project, directory):
    from lumen.media import ffmpeg_binary, ffprobe_binary
    from lumen.render import RenderEngine, _composite_filter_threads
    from lumen.timing import sample_boundary
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    binaries = {}
    for name, requested in [('ffmpeg', ffmpeg_binary()), ('ffprobe', ffprobe_binary())]:
        resolved = shutil.which(str(requested))
        if not resolved:
            raise RuntimeError('Production media executable unavailable: ' + name)
        binaries[name] = str(Path(resolved).resolve(strict=True))
    versions = {name: subprocess.run([path, '-version'], capture_output=True,
                text=True, check=True, timeout=10).stdout.splitlines()[0]
                for name, path in binaries.items()}
    if sys.platform == 'win32':
        assert all(version.startswith(name + ' version 9.') for name, version in versions.items()), 'Windows production oracle must exercise FFmpeg 9'
    token = new_timing_token()
    engine = RenderEngine(timeout=180)
    try:
        threads = _composite_filter_threads(project)
        # No frame_window argument, no range helper and no range cache reuse.
        with timed_stage('production-full', 'compile', token):
            inputs, graph, _ = engine._compile(project, filter_complex_threads=threads)
        graphpath = directory / 'full.ffgraph'
        graphpath.write_text(graph)
        command = [binaries['ffmpeg'], '-v', 'error', '-nostdin', '-y',
                   '-filter_complex_threads', str(threads), *inputs,
                   '-/filter_complex', str(graphpath), '-map', '[vout]',
                   '-r', f'{project.fps_num}/{project.fps_den}', '-fps_mode', 'cfr',
                   '-c:v', 'rawvideo', '-threads', '1', '-pix_fmt', 'yuv420p',
                   '-f', 'rawvideo', str(directory / 'full.yuv'), '-map', '[aout]',
                   '-ar', '48000', '-ac', '2', '-c:a', 'pcm_f32le', '-f', 'f32le',
                   str(directory / 'full.f32')]
        with timed_stage('production-full', 'ffmpeg', token):
            result = subprocess.run(command, capture_output=True, timeout=180)
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors='replace'))
        yuv = (directory / 'full.yuv').read_bytes()
        pcm = (directory / 'full.f32').read_bytes()
        assert len(yuv) == project.duration_frames * project.width * project.height * 3 // 2
        assert len(pcm) == sample_boundary(project.duration_frames, project.fps, 48000) * 8
        (directory / 'runtime.json').write_text(json.dumps({
            'scope': 'current production code and runtime, full graph without frame_window',
            'versions': versions, 'filter_complex_threads': threads,
            'graph_sha256': hashlib.sha256(graph.encode()).hexdigest(),
            'binaries': {name: {'path': str(path), 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}
                         for name, path in binaries.items()}}, indent=2))
        return yuv, pcm
    finally:
        engine.close()


def compare_ranges(project, directory, windows=((1, 2), (3, 9), (9, 13), (12, 13))):
    from copy import deepcopy
    from scripts.benchmark_range_preview import _candidate_range_av_in_process
    from lumen.timing import sample_boundary
    directory = Path(directory)
    before = deepcopy(project.to_dict())
    environment = dict(os.environ)
    full = full_av(project, directory / 'full')
    frame_bytes = project.width * project.height * 3 // 2
    for start, end in windows:
        actual = _candidate_range_av_in_process(project, start, end, directory / f'range-{start}-{end}')
        # Slice the already-completed full output; never compile it as a range.
        expected_yuv = full[0][start * frame_bytes:end * frame_bytes]
        expected_pcm = full[1][sample_boundary(start, project.fps, 48000) * 8:
                               sample_boundary(end, project.fps, 48000) * 8]
        assert actual[0] == expected_yuv, f'production YUV mismatch [{start},{end})'
        assert actual[1] == expected_pcm, f'production PCM mismatch [{start},{end})'
    assert project.to_dict() == before
    assert dict(os.environ) == environment

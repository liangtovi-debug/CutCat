"""Independent accepted-full-render oracle and bounded serial range benchmark.

Only synthetic, owned fixture media is produced. The reference process imports
all application Python from a SHA-pinned accepted-source ZIP, never the candidate
compositor. No dependency installation, network or hosted runner is used.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from fractions import Fraction
import hashlib
import inspect
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.runtime_timing import new_timing_token, record_stage_event, timed_stage
FROZEN = ROOT / 'tests/frozen_range_preview'
BASELINE_COMMIT = 'f91f80ac101ea8fed59fa44497a019faa375f50f'
BASELINE_TREE = '0034e800278d018bf3ea935b47edcd244102f272'
ARCHIVE_SHA256 = '04574e31ff660df39e1c09d7239a2b97e5d093ef249adf1ab99f31531d5c7784'
CAPTION_BOOTSTRAP = ROOT / 'tests/frozen_range_runtime/caption_runtime.py'
CAPTION_BOOTSTRAP_SHA256 = 'c6789493649b544bcc87b7256142d8124a3e2a7804c590dae3d57077ef443e3d'


def frozen_archive():
    if not __debug__:
        raise RuntimeError('Frozen oracle integrity checks require non-optimized Python.')
    archive = FROZEN / 'accepted-lumen-python.zip'
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == ARCHIVE_SHA256, 'Accepted oracle ZIP changed'
    manifest = json.loads((FROZEN / 'manifest.json').read_text())
    assert manifest['baseline_commit'] == BASELINE_COMMIT and manifest['baseline_tree'] == BASELINE_TREE
    assert manifest['archive_sha256'] == ARCHIVE_SHA256
    with zipfile.ZipFile(archive) as source:
        assert source.namelist() == [entry['path'] for entry in manifest['entries']]
        for entry in manifest['entries']:
            data = source.read(entry['path'])
            assert len(data) == entry['size'] and hashlib.sha256(data).hexdigest() == entry['sha256']
    return archive


def verify_frozen_imports(archive, modules):
    """Require each application module's real ZIP loader and exact member path."""
    import os
    from pathlib import Path
    import zipimport

    if not __debug__:
        raise RuntimeError('Frozen oracle integrity checks require non-optimized Python.')

    archive = Path(archive).resolve(strict=True)
    normalized = lambda value: os.path.normcase(os.path.normpath(os.fspath(value)))
    imports = {}
    for name, module in modules.items():
        if name != 'lumen' and not name.startswith('lumen.'):
            continue
        loader = getattr(module, '__loader__', None)
        assert isinstance(loader, zipimport.zipimporter), 'Reference module has no ZIP loader: ' + name
        assert Path(loader.archive).resolve(strict=True) == archive, 'Reference module uses another archive: ' + name
        member = name.replace('.', '/')
        member += '/__init__.py' if loader.is_package(name) else '.py'
        expected = os.path.join(str(archive), *member.split('/'))
        origin = getattr(getattr(module, '__spec__', None), 'origin', None)
        filename = getattr(module, '__file__', None)
        assert all(value and normalized(value) == normalized(expected)
                   for value in (filename, origin, loader.get_filename(name))), 'Reference module origin mismatch: ' + name
        imports[name] = filename
    assert imports and 'lumen' in imports, 'Reference application modules are absent'
    return imports


def reference_tool_lease(executable):
    """Keep Windows executable bytes locked against writes/deletion while used."""
    import os
    from pathlib import Path
    import sys

    path = Path(executable).resolve(strict=True)
    if sys.platform != 'win32':
        return path.open('rb')
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # GENERIC_READ, FILE_SHARE_READ only, OPEN_EXISTING. Do not grant write/delete.
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    try:
        return os.fdopen(descriptor, 'rb')
    except BaseException:
        os.close(descriptor)
        raise


def verify_reference_tool_leases(leases):
    import hashlib
    import os
    for path, stream, digest, before in leases:
        stream.seek(0)
        current = os.fstat(stream.fileno())
        signature = lambda value: (value.st_dev, value.st_ino, value.st_size,
                                   value.st_mtime_ns, value.st_ctime_ns)
        if (signature(current) != signature(before)
                or not os.path.samestat(current, os.stat(path))
                or hashlib.file_digest(stream, 'sha256').hexdigest() != digest):
            raise RuntimeError('Reference executable changed while in use')


def initialize_reference_runtime(bootstrap_path, bootstrap_sha256, windows_tool_hashes):
    """Load only pinned dependency setup, before any reference/Pillow import."""
    import hashlib
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import sys
    from contextlib import ExitStack

    if not __debug__:
        raise RuntimeError('Frozen oracle integrity checks require non-optimized Python.')
    assert not any(name == 'lumen' or name.startswith('lumen.')
                   or name == 'PIL' or name.startswith('PIL.') for name in sys.modules), 'Reference dependencies were imported before bootstrap'
    bootstrap = Path(bootstrap_path).read_bytes()
    assert hashlib.sha256(bootstrap).hexdigest() == bootstrap_sha256, 'Reference caption bootstrap changed'
    tools, leases = {}, []
    with ExitStack() as cleanup:
        for key, name in [('LUMEN_FFMPEG', 'ffmpeg'), ('LUMEN_FFPROBE', 'ffprobe')]:
            executable = shutil.which(os.environ.get(key, name))
            assert executable, 'Reference media executable is unavailable'
            executable = str(Path(executable).resolve(strict=True))
            os.environ[key] = executable
            stream = cleanup.enter_context(reference_tool_lease(executable))
            before = os.fstat(stream.fileno())
            if not os.path.samestat(before, os.stat(executable)):
                raise RuntimeError('Reference executable path changed before verification')
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            leases.append((executable, stream, digest, before))
            if sys.platform == 'win32':
                assert digest == windows_tool_hashes[name + '.exe'], 'Reference media executable is not the pinned binary'
            version = subprocess.run([executable, '-version'], capture_output=True,
                                     check=True, text=True, timeout=10).stdout.splitlines()[0]
            verify_reference_tool_leases(leases)
            assert version.startswith(name + ' version 7.'), 'Frozen oracle requires an explicit compatible FFmpeg 7 runtime'
            tools[name] = {'sha256': digest, 'version': version}
        # Retain provider/DLL/search handles and executable leases through render.
        provider = {'__name__': '_accepted_caption_runtime', '__file__': str(bootstrap_path)}
        exec(compile(bootstrap, str(bootstrap_path), 'exec'), provider)
        shaping = provider['require_capability']()
        provider['_tool_leases'] = leases
        provider['_cleanup'] = cleanup.pop_all()
        return provider, {'tools': tools, 'shaping': shaping,
                          'caption_bootstrap_sha256': bootstrap_sha256}


REFERENCE = r'''
import sys
sys.path[0]=sys.argv[2]  # No imports from the writable evidence directory.
import hashlib,json,pathlib,subprocess,time
archive=pathlib.Path(sys.argv[2])
assert hashlib.sha256(archive.read_bytes()).hexdigest()==sys.argv[4], 'Accepted oracle ZIP changed in child'
token=new_timing_token()
with timed_stage('reference','runtime',token):
 _provider,runtime=initialize_reference_runtime(sys.argv[5],sys.argv[6],json.loads(sys.argv[7]))
from lumen.model import Project
from lumen.render import RenderEngine, _composite_filter_threads
from lumen.media import ffmpeg_binary
verify_frozen_imports(archive,sys.modules)
root=pathlib.Path(sys.argv[1]);p=Project.from_dict(json.loads((root/'project.json').read_text()))
e=RenderEngine(timeout=180)
began=time.monotonic()
try:
 threads=_composite_filter_threads(p) if sys.argv[3]=='production' else 1
 with timed_stage('reference','compile',token):
  inputs,graph,_=e._compile(p,filter_complex_threads=threads)
 graphpath=root/'full.ffgraph';graphpath.write_text(graph)
 command=[ffmpeg_binary(),'-v','error','-nostdin','-y','-filter_complex_threads',str(threads),*inputs,
          '-/filter_complex',str(graphpath),'-map','[vout]','-r',f'{p.fps_num}/{p.fps_den}',
          '-fps_mode','cfr','-c:v','rawvideo','-threads','1','-pix_fmt','yuv420p','-f','rawvideo',str(root/'full.yuv'),
          '-map','[aout]','-ar','48000','-ac','2','-c:a','pcm_f32le','-f','f32le',str(root/'full.f32')]
 with timed_stage('reference','ffmpeg',token):
  subprocess.run(command,capture_output=True,check=True,timeout=180)
 imports=verify_frozen_imports(sys.argv[2],sys.modules)
 verify_reference_tool_leases(_provider['_tool_leases'])
 assert hashlib.sha256(archive.read_bytes()).hexdigest()==sys.argv[4], 'Accepted oracle ZIP changed during render'
 (root/'reference.json').write_text(json.dumps({'duration_seconds':time.monotonic()-began,'imports':imports,
       'graph_sha256':hashlib.sha256(graph.encode()).hexdigest(),'picture_frames':p.duration_frames,
       'filter_complex_threads':threads,'runtime':runtime},indent=2))
finally:
 e.close()
 _provider['_cleanup'].close()
'''


def oracle_child_environment(directory, pythonpath):
    """Select the same external tools for both code oracles, never the parent."""
    env = dict(os.environ, PYTHONPATH=str(pythonpath), PYTHONDONTWRITEBYTECODE='1',
               QT_QPA_PLATFORM='offscreen', TMPDIR=str(directory))
    env.pop('CUTCAT_FROZEN_MODULES', None)
    env.pop('PYTHONOPTIMIZE', None)
    for name in ('FFMPEG', 'FFPROBE'):
        reference_tool = env.get('LUMEN_REFERENCE_' + name)
        if sys.platform == 'win32' and not reference_tool:
            raise RuntimeError('Provision the independently pinned reference runtime before running the frozen oracle.')
        if reference_tool:
            env['LUMEN_' + name] = reference_tool
    env.setdefault('LUMEN_FRIBIDI_DIR', str(ROOT / 'build/runtime/fribidi'))
    return env


def runtime_bootstrap_source():
    return (inspect.getsource(new_timing_token) + '\n'
            + inspect.getsource(record_stage_event) + '\n'
            + inspect.getsource(timed_stage) + '\n'
            + inspect.getsource(reference_tool_lease) + '\n'
            + inspect.getsource(verify_reference_tool_leases) + '\n'
            + inspect.getsource(initialize_reference_runtime) + '\n')


def frozen_full_av(project, directory, *, production_threads=False):
    """Whole accepted final graph, raw YUV + PCM. No schema lowering or crop."""
    from lumen.timing import sample_boundary
    archive = frozen_archive()
    directory = Path(directory).resolve(); directory.mkdir(parents=True, exist_ok=True)
    (directory / 'project.json').write_text(json.dumps(project.to_dict()), encoding='utf-8')
    env = oracle_child_environment(directory, archive)
    from scripts.provision_frozen_reference import TOOL_HASHES, verify_manifest
    verify_manifest()
    # -c puts cwd first, so use the new evidence directory, not candidate ROOT.
    reference = (inspect.getsource(verify_frozen_imports) + '\n'
                 + runtime_bootstrap_source() + REFERENCE)
    result = subprocess.run([sys.executable, '-c', reference, str(directory), str(archive),
                             'production' if production_threads else 'scalar', ARCHIVE_SHA256,
                             str(CAPTION_BOOTSTRAP), CAPTION_BOOTSTRAP_SHA256, json.dumps(TOOL_HASHES)],
                            cwd=directory, env=env, capture_output=True, timeout=240)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace'))
    yuv, pcm = (directory / 'full.yuv').read_bytes(), (directory / 'full.f32').read_bytes()
    assert len(yuv) == project.duration_frames * project.width * project.height * 3 // 2
    assert len(pcm) == sample_boundary(project.duration_frames, project.fps, 48000) * 2 * 4
    return yuv, pcm


CANDIDATE = r"""
import sys
sys.path[0]=sys.argv[2]  # No stdlib/helper/package shadowing by evidence files.
import json,pathlib
root=pathlib.Path(sys.argv[2]).resolve()
token=new_timing_token()
with timed_stage('candidate','runtime',token):
 _provider,runtime=initialize_reference_runtime(sys.argv[3],sys.argv[4],json.loads(sys.argv[5]))
try:
 from lumen.model import Project
 import scripts.benchmark_range_preview as helper
 assert pathlib.Path(helper.__file__).resolve()==root/'scripts/benchmark_range_preview.py', 'Candidate helper origin mismatch'
 assert pathlib.Path(helper._candidate_range_av_in_process.__code__.co_filename).resolve()==root/'scripts/benchmark_range_preview.py', 'Candidate function origin mismatch'
 import lumen.render
 assert pathlib.Path(lumen.render.__file__).resolve()==root/'lumen/render.py', 'Candidate renderer origin mismatch'
 directory=pathlib.Path(sys.argv[1])
 request=json.loads((directory/'candidate-request.json').read_text())
 project=Project.from_dict(request['project'])
 helper._candidate_range_av_in_process(project,request['start'],request['end'],directory)
 verify_reference_tool_leases(_provider['_tool_leases'])
 imports={}
 for name,module in sys.modules.items():
  if name=='lumen' or name.startswith('lumen.'):
   origin=pathlib.Path(module.__file__).resolve()
   assert origin.is_relative_to(root/'lumen'), 'Candidate imported noncandidate application module'
   imports[name]=str(origin)
 (directory/'candidate.json').write_text(json.dumps({'runtime':runtime,'imports':imports,
      'scope':'candidate code on common FFmpeg 7 oracle runtime; not production FFmpeg 9'},indent=2))
finally:
 _provider['_cleanup'].close()
"""


def candidate_range_av(project, start, end, directory):
    """Strict code oracle in a separate child using the reference tool bytes.

    Production integration/capture tests continue using the parent's FFmpeg 9.
    This helper establishes code equivalence, not cross-version equivalence.
    """
    from scripts.provision_frozen_reference import TOOL_HASHES, verify_manifest
    from lumen.timing import sample_boundary
    verify_manifest()
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'candidate-request.json').write_text(json.dumps({
        'project': project.to_dict(), 'start': start, 'end': end}), encoding='utf-8')
    result = subprocess.run([sys.executable, '-c', runtime_bootstrap_source() + CANDIDATE,
                             str(directory), str(ROOT), str(CAPTION_BOOTSTRAP),
                             CAPTION_BOOTSTRAP_SHA256, json.dumps(TOOL_HASHES)],
                            cwd=directory, env=oracle_child_environment(directory, ROOT),
                            capture_output=True, timeout=240)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace'))
    yuv, pcm = (directory / 'range.yuv').read_bytes(), (directory / 'range.f32').read_bytes()
    assert len(yuv) == (end-start)*project.width*project.height*3//2
    assert len(pcm) == (sample_boundary(end,project.fps,48000)-sample_boundary(start,project.fps,48000))*8
    return yuv, pcm


def _candidate_range_av_in_process(project, start, end, directory):
    """Candidate final graph before H.264/AAC; preserves exact 48 kHz count."""
    from lumen.media import ffmpeg_binary
    from lumen.render import RenderEngine
    from lumen.timing import sample_boundary
    directory = Path(directory).resolve(); directory.mkdir(parents=True, exist_ok=True)
    engine = RenderEngine(timeout=180)
    token = new_timing_token()
    try:
        with timed_stage('range', 'compile', token):
            inputs, graph, _ = engine._compile(project, filter_complex_threads=1, frame_window=(start, end))
        graphpath = directory / 'range.ffgraph'; graphpath.write_text(graph)
        command = [ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-filter_complex_threads', '1', *inputs,
                   '-/filter_complex', str(graphpath), '-map', '[vout]', '-r', f'{project.fps_num}/{project.fps_den}',
                   '-fps_mode', 'cfr', '-c:v', 'rawvideo', '-threads', '1', '-pix_fmt', 'yuv420p',
                   '-f', 'rawvideo', str(directory / 'range.yuv'), '-map', '[aout]', '-ar', '48000',
                   '-ac', '2', '-c:a', 'pcm_f32le', '-f', 'f32le', str(directory / 'range.f32')]
        with timed_stage('range', 'ffmpeg', token):
            result = subprocess.run(command, capture_output=True, timeout=180)
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors='replace'))
        yuv, pcm = (directory / 'range.yuv').read_bytes(), (directory / 'range.f32').read_bytes()
        assert len(yuv) == (end - start) * project.width * project.height * 3 // 2
        assert len(pcm) == (sample_boundary(end, project.fps, 48000) - sample_boundary(start, project.fps, 48000)) * 2 * 4
        return yuv, pcm
    finally:
        engine.close()


def expected_range(project, full, start, end):
    from lumen.timing import sample_boundary
    yuv, pcm = full
    picture_size = project.width * project.height * 3 // 2
    return (yuv[start * picture_size:end * picture_size],
            pcm[sample_boundary(start, project.fps, 48000) * 8:sample_boundary(end, project.fps, 48000) * 8])


def make_av_source(directory, rate=Fraction(24), *, count=64, sample_rate=48000,
                   width=64, height=48):
    """Numbered SDR pictures and asymmetric stereo impulses/tones, same file."""
    import numpy as np
    from lumen.media import ffmpeg_binary, probe
    from scripts.benchmark_final_frame_preview import make_source
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    picture = make_source(directory / 'picture', rate, width=width, height=height, count=count)
    # Audio extends one frame beyond picture to avoid metadata floor disputes.
    samples = math_ceil(Fraction(count + 1, 1) / rate * sample_rate)
    t = np.arange(samples, dtype=np.float64)
    pcm = np.column_stack((.25 * np.sin(t * .073) + .07, .22 * np.cos(t * .131) - .04)).astype('<f4')
    pcm[::997, 0] = .9; pcm[123::997, 1] = -.8
    audio = directory / 'source.wav'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-f', 'f32le',
        '-ar', str(sample_rate), '-ac', '2', '-i', 'pipe:0', '-c:a', 'pcm_f32le', str(audio)],
        input=pcm.tobytes(), capture_output=True, check=True, timeout=30)
    target = directory / 'source-av.mkv'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-i', picture.path, '-i', str(audio),
        '-map', '0:V:0', '-map', '1:a:0', '-c', 'copy', str(target)],
        capture_output=True, check=True, timeout=30)
    return probe(target)


def math_ceil(value):
    return -(-value.numerator // value.denominator)


def make_av_scene(directory, rate=Fraction(24), *, variant='complex', source=None, length=13):
    """Existing non-degenerate final picture fixture plus affine J/L ownership."""
    from lumen.audio_gain import AudioGainKeyframe as K
    from lumen.model import AVLink, AudioMixAnchor, Clip, AV_LINK_AFFINE_ALIGNMENT
    from scripts.benchmark_final_frame_preview import make_scene
    root = Path(directory); root.mkdir(parents=True, exist_ok=True)
    source = source or make_av_source(root / 'av', rate)
    project = make_scene(root, rate, variant=variant, source=source, length=length,
                         width=source.width, height=source.height)
    video = project.clips[0]
    video.source_audio_enabled = False
    # The audio starts a frame before its V role and ends a frame later.
    audio = Clip('j-l-audio', source.id, 2, 0, 0, length, gain_db=1.75,
                 fade_in=2, fade_out=3, audio_mix_anchor=AudioMixAnchor(0, video.start, video.id),
                 audio_gain_keyframes=[K(-101, -4), K(7001, 6), K(15003, -7, 'hold'), K(28001, 2)])
    project.clips.append(audio)
    project.av_links = [AVLink('j-l-pair', video.id, audio.id, source.id, AV_LINK_AFFINE_ALIGNMENT)]
    project.master_gain_db = -2.125
    # Explicit licensed test font keeps this oracle portable to native CI.
    font = os.environ.get('LUMEN_TEST_CJK_FONT_DIR')
    if font and project.captions:
        project.caption_style.font_directory = font
        project.caption_style.font_family = 'Noto Sans CJK SC'
    project.validate()
    return project
def compare_scene_ranges(root, rate, media, variant):
    """Every original matrix assertion, shared without importing pytest tests."""
    directory = Path(root) / variant
    project = make_av_scene(directory / 'scene', rate, source=media, variant=variant)
    original = deepcopy(project.to_dict())
    full = frozen_full_av(project, directory / 'full')
    # Full composition spans motion/window keys, caption and animated sticker
    # boundaries; the last range crosses a picture end with sound continuing.
    for start, end in ((1, 2), (3, 9), (9, 13), (12, 13)):
        actual = candidate_range_av(project, start, end, directory / f'range-{start}-{end}')
        expected = expected_range(project, full, start, end)
        assert actual[0] == expected[0], f'final picture mismatch {variant}/{rate}/[{start},{end})'
        assert actual[1] == expected[1], f'pre-AAC stereo mismatch {variant}/{rate}/[{start},{end})'
        candidate_receipt = json.loads((directory / f'range-{start}-{end}/candidate.json').read_text())
        reference_receipt = json.loads((directory / 'full/reference.json').read_text())
        assert candidate_receipt['runtime'] == reference_receipt['runtime'], 'Code oracle runtimes differ'
    assert project.to_dict() == original
    receipt = json.loads((directory / 'full/reference.json').read_text())
    assert receipt['imports']['lumen.render'].startswith(str(frozen_archive()))
    if rate == Fraction(30000, 1001):
        assert len(expected_range(project, full, 1, 2)[1]) == 1601 * 8
def source_identity():
    entries = []
    for folder in ('lumen', 'scripts', 'tests'):
        for path in sorted((ROOT / folder).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                entries.append((path.relative_to(ROOT).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    return hashlib.sha256(json.dumps(entries, separators=(',', ':')).encode()).hexdigest()


def benchmark(output, repeats=3):
    """Cold serial 320x180 measurements. Not a realtime/native performance claim."""
    from lumen.range_preview import capture_range_preview
    from lumen.render import RenderEngine
    from lumen.model import Clip
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=False)
    identity = source_identity()
    source = make_av_source(output / 'source', count=160, width=320, height=180)
    project = make_av_scene(output / 'scene', source=source, variant='legacy', length=73)
    # Put the affine J/L edge in the interior so the default +/-2 second
    # request is a true four-second window rather than an endpoint clamp.
    for clip in project.clips:
        clip.start += 48
    for overlay in project.overlays:
        overlay.start += 48
    # Unrelated tail is a separate long clip, not a large random array.
    project.clips.append(Clip('unrelated-tail', source.id, 0, 150, 0, 120, source_audio_enabled=False))
    project.validate()
    records = []
    for index in range(repeats):
        before = time.monotonic(); snapshot = capture_range_preview(project, 30, 126)
        elapsed = time.monotonic() - before
        records.append({'kind': 'range', 'seconds': elapsed, 'work': snapshot.work.__dict__, 'output_bytes': snapshot.path.stat().st_size})
        snapshot.close()
        engine = RenderEngine(timeout=180)
        try:
            before = time.monotonic(); path = engine.preview(project)
            records.append({'kind': 'full', 'seconds': time.monotonic() - before, 'output_bytes': path.stat().st_size,
                            'picture_frames': project.duration_frames,
                            'graded_frames': sum(c.duration for c in project.clips if c.track < 2)})
        finally: engine.close()
    result = {'baseline_commit': BASELINE_COMMIT, 'frozen_archive_sha256': ARCHIVE_SHA256,
              'candidate_code_tree_before': identity, 'candidate_code_tree_after': source_identity(),
              'candidate_code_tree_scope': 'all non-__pycache__ files in lumen, scripts and tests',
              'full_runtime': 'candidate existing full-preview path; frozen ZIP is used separately for byte oracles',
              'serial_cold_measurements': records, 'canvas': [project.width, project.height],
              'range_frames': [30, 126], 'playhead_frame': 78, 'requested_radius_seconds': 2,
              'timeline_frames': project.duration_frames,
              'fixture': '320x180 legacy transformed/vignette picture, affine J/L stereo audio, unrelated picture tail',
              'fixture_video_interval': [49, 120], 'fixture_audio_interval': [48, 121],
              'unrelated_tail_interval': [150, 270],
              'range_median_seconds': statistics.median(r['seconds'] for r in records if r['kind'] == 'range'),
              'full_median_seconds': statistics.median(r['seconds'] for r in records if r['kind'] == 'full'),
              'native_or_realtime_claim': False}
    result['source_unchanged'] = result['candidate_code_tree_before'] == result['candidate_code_tree_after']
    result['measurement_status'] = ('SAME_SOURCE_MEASURED' if result['source_unchanged']
                                    else 'SOURCE_CHANGED_UNACCEPTED')
    result['cache_policy'] = 'fresh renderer-owned caches per trial; OS filesystem caches are not flushed'
    (output / 'benchmark.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3, choices=range(1, 6))
    args = parser.parse_args()
    print(json.dumps(benchmark(args.output, args.repeats), indent=2))

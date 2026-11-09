"""Exact final-frame code oracle on a shared, verified FFmpeg 7 runtime.

The immutable full renderer and current still backend run in separate children.
The parent keeps its production runtime (FFmpeg 9 on Windows). This entry point
is deliberately separate from the performance benchmark: it does not change
benchmark frozen_environment(), timing scope, frozen bytes or schema transport.
Production-runtime integration is covered separately by production_range_oracle.
"""
from dataclasses import dataclass
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys

from scripts import benchmark_final_frame_preview as fixture
from scripts.benchmark_range_preview import (
    CAPTION_BOOTSTRAP, CAPTION_BOOTSTRAP_SHA256,
    oracle_child_environment, runtime_bootstrap_source,
)
from scripts.provision_frozen_reference import TOOL_HASHES, verify_manifest

FROZEN_LEAVES = ('clip_composite', 'model', 'caption_layout', 'caption_render',
                 'media', 'color_media', 'overlay_render', 'render')


def verify_application_imports(root, candidate_root, frozen, modules):
    """Reject mixed applications and any candidate still import in the old side."""
    from pathlib import Path
    if not __debug__:
        raise RuntimeError('Frozen oracle integrity checks require non-optimized Python.')
    root, candidate_root = Path(root).resolve(), Path(candidate_root).resolve()
    imports = {}
    for name, module in list(modules.items()):
        if name != 'lumen' and not name.startswith('lumen.'):
            continue
        relative = name.replace('.', '/') + (('/__init__' if name == 'lumen' else '') + '.py')
        expected = root / relative
        leaf = name.removeprefix('lumen.')
        if frozen and root == candidate_root and leaf in FROZEN_LEAVES:
            expected = root / 'tests/frozen_final_frame' / (leaf + '.py')
        actual = Path(getattr(module, '__file__', '')).resolve()
        origin = Path(getattr(getattr(module, '__spec__', None), 'origin', '') or '').resolve()
        assert actual == expected and origin == expected, f'Application origin mismatch: {name}'
        assert not (frozen and name in ('lumen.final_frame_preview', 'lumen.range_preview')), 'Candidate backend imported by frozen oracle'
        imports[name] = str(actual)
    required = 'lumen.render' if frozen else 'lumen.final_frame_preview'
    assert required in imports, 'Application imports absent'
    return imports


def font_receipt(project, verified=None):
    from pathlib import Path
    import hashlib
    if not project.captions:
        return None
    from lumen.caption_render import font_identity
    path, size, _mtime, _ctime, face = font_identity(project.caption_style)
    receipt = {'path': str(Path(path).resolve()), 'size': size, 'face': face,
               'family': project.caption_style.font_family,
               'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}
    if verified is not None:
        assert receipt['path'] == verified['font_file'] and receipt['sha256'] == verified['font_sha256'], 'Verified fixture font changed'
        assert hashlib.sha256(Path(verified['license_file']).read_bytes()).hexdigest() == verified['license_sha256'], 'Verified fixture license changed'
    return receipt


REFERENCE = r'''
from lumen.model import Project
from lumen.render import RenderEngine, _composite_filter_threads
from lumen.media import ffmpeg_binary
p=Project.from_dict(json.loads((directory/'project.json').read_text(encoding='utf-8')))
before_font=font_receipt(p,request['verified_font'])
e=RenderEngine(timeout=180)
try:
 threads=_composite_filter_threads(p) if request['production_threads'] else 1
 inputs,graph,_=e._compile(p,filter_complex_threads=threads)
 assert graph.endswith('[aout]')
 graph=graph[:-len('[aout]')]+',anullsink'
 graphpath=directory/'full.ffgraph';graphpath.write_text(graph,encoding='utf-8')
 command=[ffmpeg_binary(),'-v','error','-nostdin','-y','-filter_complex_threads',str(threads),*inputs,
          '-/filter_complex',str(graphpath),'-map','[vout]','-frames:v',str(p.duration_frames),
          '-r',f'{p.fps_num}/{p.fps_den}','-fps_mode','cfr','-c:v','rawvideo','-threads','1',
          '-pix_fmt','yuv420p','-f','rawvideo',str(directory/'full.yuv')]
 result=subprocess.run(command,capture_output=True,timeout=180)
 (directory/'ffmpeg.stderr').write_bytes(result.stderr)
 assert result.returncode==0, result.stderr.decode(errors='replace')
 imports=verify_application_imports(root,ROOT,True,sys.modules)
 frozen_baseline(root)
 verify_reference_tool_leases(_provider['_tool_leases'])
 assert font_receipt(p,request['verified_font'])==before_font, 'Font changed during render'
 (directory/'reference.json').write_text(json.dumps(dict(
     scope='immutable full renderer, shared FFmpeg 7 code comparison, not production 9',
     runtime=runtime,font=before_font,imports=imports,filter_complex_threads=threads,
     graph_sha256=hashlib.sha256(graph.encode()).hexdigest()),indent=2),encoding='utf-8')
finally:
 e.close()
'''

CANDIDATE = r'''
from lumen.model import Project
from lumen.final_frame_preview import capture_final_frame_snapshot
assert Path(capture_final_frame_snapshot.__code__.co_filename).resolve()==ROOT/'lumen/final_frame_preview.py'
p=Project.from_dict(json.loads((directory/'project.json').read_text(encoding='utf-8')))
before_font=font_receipt(p,request['verified_font'])
sizes=[]
with (directory/'frames.yuv').open('wb') as yuv, (directory/'frames.rgb').open('wb') as rgb:
 for frame in request['frames']:
  result=capture_final_frame_snapshot(p,frame,timeout=120)
  assert result.width==p.width and result.height==p.height
  assert len(result.yuv420p)==p.width*p.height*3//2
  assert len(result.rgb_bytes)==p.width*p.height*3
  yuv.write(result.yuv420p);rgb.write(result.rgb_bytes)
  sizes.append([result.width,result.height])
imports=verify_application_imports(root,ROOT,False,sys.modules)
verify_reference_tool_leases(_provider['_tool_leases'])
assert font_receipt(p,request['verified_font'])==before_font, 'Font changed during render'
(directory/'candidate.json').write_text(json.dumps(dict(
    scope='current still backend, shared FFmpeg 7 code comparison, not production 9',
    runtime=runtime,font=before_font,imports=imports,frames=request['frames'],sizes=sizes),indent=2),encoding='utf-8')
'''


def child_source(frozen):
    # No lumen or PIL import may precede initialize_reference_runtime.
    prefix = ('import sys\nsys.path[0]=sys.argv[2]\n'
              'import hashlib,json,os,subprocess\nfrom pathlib import Path\n'
              f'ROOT=Path({str(fixture.ROOT)!r})\n'
              f'BASELINE_RENDER_SHA256={fixture.BASELINE_RENDER_SHA256!r}\n'
              f'BASELINE_VISUAL_DEPENDENCIES={fixture.BASELINE_VISUAL_DEPENDENCIES!r}\n'
              f'FROZEN_LEAVES={FROZEN_LEAVES!r}\n')
    prefix += runtime_bootstrap_source()
    prefix += '\n' + inspect.getsource(fixture.frozen_baseline)
    prefix += '\n' + inspect.getsource(verify_application_imports)
    prefix += '\n' + inspect.getsource(font_receipt)
    prefix += "\ndirectory=Path(sys.argv[1]);root=Path(sys.argv[2])\n"
    prefix += "request=json.loads((directory/'request.json').read_text(encoding='utf-8'))\n"
    prefix += '_provider,runtime=initialize_reference_runtime(sys.argv[3],sys.argv[4],json.loads(sys.argv[5]))\n'
    body = ('frozen_baseline(root)\n' + fixture.FROZEN_BOOTSTRAP + REFERENCE) if frozen else CANDIDATE
    # Leases also close on import, compiler or snapshot failure.
    return prefix + '\ntry:\n' + '\n'.join(' ' + line for line in body.splitlines()) + "\nfinally:\n _provider['_cleanup'].close()\n"


POSIX_SUPERVISOR = r'''
import os,signal,subprocess,sys
parent=int(sys.argv[1])
def check_parent():
 if os.getppid()!=parent:
  # A suite/pytest crash must not strand our newly isolated process group.
  os.killpg(os.getpgrp(),signal.SIGKILL)
check_parent()
child=subprocess.Popen(sys.argv[2:])
while True:
 check_parent()
 try:
  result=child.wait(timeout=.2)
  break
 except subprocess.TimeoutExpired:
  pass
sys.exit(result)
'''


def run_isolated_process(command, directory, environment, *, timeout=300):
    """Bound the whole worker tree, not just its Python leader, on timeout."""
    from scripts.run_bounded_tests import terminate_tree
    options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt'
               else {'start_new_session': True})
    if os.name != 'nt':
        command = [sys.executable, '-c', POSIX_SUPERVISOR, str(os.getpid()), *command]
    with (directory/'stdout.log').open('xb') as stdout, (directory/'stderr.log').open('xb') as stderr:
        process = subprocess.Popen(command, cwd=directory, env=environment,
                                   stdout=stdout, stderr=stderr, **options)
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            # In particular TimeoutExpired does not first kill/reap the leader:
            # Windows taskkill /T can still discover and stop its descendants.
            terminate_tree(process)
            raise
    if code:
        raise RuntimeError((directory/'stderr.log').read_text(encoding='utf-8', errors='replace'))


def run_child(project, directory, *, frozen, baseline=None, frames=(), production_threads=False):
    verify_manifest()  # Also refuses optimized Python before any assert-based check.
    root = fixture.frozen_baseline(baseline) if frozen else fixture.ROOT
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    if frozen:
        fixture.write_frozen_project(project, directory / 'project.json')
    else:
        fixture.write_project(project, directory / 'project.json')
    verified_font = None
    font_directory = os.environ.get('LUMEN_TEST_CJK_FONT_DIR')
    if (font_directory and project.captions and project.caption_style.font_directory
            and Path(project.caption_style.font_directory).resolve() == Path(font_directory).resolve()):
        from scripts.provision_test_font import verify_fixture
        verified_font = verify_fixture(font_directory)
    (directory / 'request.json').write_text(json.dumps(dict(frames=list(frames),
        production_threads=production_threads, verified_font=verified_font)), encoding='utf-8')
    env = oracle_child_environment(directory, root)
    if frozen and root == fixture.ROOT:
        env['CUTCAT_FROZEN_MODULES'] = str(root / 'tests/frozen_final_frame')
    try:
        run_isolated_process([sys.executable, '-c', child_source(frozen), str(directory), str(root),
                              str(CAPTION_BOOTSTRAP), CAPTION_BOOTSTRAP_SHA256, json.dumps(TOOL_HASHES)],
                             directory, env)
    finally:
        # Bounded pytest excludes its temporary media from artifact uploads.
        # Retain only these explicit runtime/import receipts and process logs.
        evidence = os.environ.get('RANGE_ORACLE_EVIDENCE')
        if evidence:
            import shutil
            import uuid
            destination = Path(evidence)/'final-frame-code'/uuid.uuid4().hex
            destination.mkdir(parents=True)
            for name in ('reference.json', 'candidate.json', 'stdout.log', 'stderr.log', 'ffmpeg.stderr'):
                if (directory/name).is_file():
                    shutil.copyfile(directory/name, destination/(name + '.log' if name == 'ffmpeg.stderr' else name))
    return directory


def frozen_full_pixels(project, directory, baseline=None, *, production_threads=False):
    directory = run_child(project, directory, frozen=True, baseline=baseline,
                          production_threads=production_threads)
    raw = (directory / 'full.yuv').read_bytes()
    assert len(raw) == project.duration_frames * project.width * project.height * 3 // 2
    return raw


@dataclass(frozen=True)
class FramePixels:
    width: int
    height: int
    yuv420p: bytes
    rgb_bytes: bytes


def require_same_runtime(reference, candidate):
    assert reference['runtime'] == candidate['runtime'], 'Code oracle runtime mismatch (no cross-runtime equality claim)'
    assert reference['font'] == candidate['font'], 'Code oracle font mismatch'


def candidate_snapshots(project, frames, directory, *, reference_directory):
    frames = list(frames)
    assert frames and len(set(frames)) == len(frames)
    assert all(type(frame) is int and 0 <= frame < project.duration_frames for frame in frames)
    reference = json.loads((Path(reference_directory) / 'reference.json').read_text(encoding='utf-8'))
    directory = run_child(project, directory, frozen=False, frames=frames)
    candidate = json.loads((directory / 'candidate.json').read_text(encoding='utf-8'))
    require_same_runtime(reference, candidate)
    assert candidate['frames'] == frames
    assert candidate['sizes'] == [[project.width, project.height]] * len(frames)
    yuv, rgb = (directory / 'frames.yuv').read_bytes(), (directory / 'frames.rgb').read_bytes()
    ysize, rsize = project.width * project.height * 3 // 2, project.width * project.height * 3
    assert len(yuv) == ysize * len(frames) and len(rgb) == rsize * len(frames)
    return [FramePixels(project.width, project.height, yuv[n*ysize:(n+1)*ysize], rgb[n*rsize:(n+1)*rsize])
            for n in range(len(frames))]

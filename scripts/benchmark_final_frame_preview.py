"""Cold, serial final-still / full-preview measurements and frozen pixel oracle.

Synthetic owned inputs only. No dependency install or network. The baseline root
is a frozen accepted checkout; its full render.py hash is checked before use.
The pixel oracle renders *all* frames through that full pre-encoding graph, not
through the candidate's frame selector or H.264. Timing of whole preview uses
actual RenderEngine.preview, including audio and MP4 encoding, as users do.

Example: python scripts/benchmark_final_frame_preview.py --output /tmp/new-run \
  --baseline ../cutcat-audio-gain-keyframes --width 320 --height 180 --repeats 3
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
BASELINE_RENDER_SHA256 = '30c3e231583cb12cc262be35bdb7cf70580ff7bbd42c4d14c846653b23a400e0'
BASELINE_COMMIT = '2c70ebb1e31733ee4d95fe20a43152ae0d38f5df'
BASELINE_VISUAL_DEPENDENCIES = {'lumen/audio_components.py': '2ce06aca3ca83fa5aa5f5da6161367227662d7acb9ef1ce11ea1687adca94554',
 'lumen/audio_gain.py': 'd6c6856a19c4759aaa7847dae0f9a0031e4086284f728eae0f2752d4c37f77a0',
 'lumen/audio_mix.py': '899712de16a203aa932821b8f2ba701fe8bc08c8a681ec563f3ea833fa39c847',
 'lumen/audio_timing.py': '33f47da3a449fb2a914ca8cb0d3bc95a05e5ccb0ea410f6351c0a9f0e6197c5b',
 'lumen/caption_layout.py': '7faf13ac9a5f8f4bb28d7da98eef13921c30bd662b118a58a1136fed8ef52cc8',
 'lumen/caption_render.py': 'ddb3f00ceeb1ba5c7da4a0fc10e3d743992fc0b59d78674fe6fbbb1b3a91dabc',
 'lumen/clip_composite.py': 'b0719904446bba16ebe16f578c2c0fff4b908f31be5c4d446da393d86793540b',
 'lumen/clip_motion.py': 'e6d0dc0319a98f3ead3243aaa3ca1090f91f1302433ab1e56fc9a3e34505ac47',
 'lumen/clip_motion_render.py': '2db1e6d88efdd8dd510d9688eb3529b000568f1b47a864778b0145e93824d996',
 'lumen/clip_transform.py': 'bf2a4332cfce114cbee50de30ee781f6d8a8f6741808ed212892e4880b3fcee7',
 'lumen/color.py': '5fb4fe5b9258990a62c683ad7f06e190086138f96e2027b4ea82312e07589c52',
 'lumen/color_media.py': 'bb29d198edcd6e04e94df9db67fc780e856a68100c2de206c501093385de8477',
 'lumen/color_output.py': 'f26ac316f7c8070697ca203c1042521057b33de0ab89fab576a500cae01324cb',
 'lumen/color_source.py': '00b597d8ef997afe170cc76fab12801de9ee9ef0618e586cac86c69772e4a7a3',
 'lumen/file_identity.py': 'bd5c1d024368a8d3be34fc06afeabc781527ee49666fe8639cbd2a0d24b2fc2e',
 'lumen/media.py': 'b8ae8a379d646e3065baf4af0e1957de230c591be190edd62dc11defd4ada1e3',
 'lumen/model.py': 'f0e98c832dbae630d5b21699ac09899a07c6d0281ab83ae1f48ea27179bf355a',
 'lumen/overlay_render.py': 'c8843121921746a74a03efa06ab4284edc397bed3ac183e9860ce013806be706',
 'lumen/qualifier.py': 'd4e2837f3acc7595a0f37b9f80016ccbb78f23a61b789095a0e4bdb2a52b81ad',
 'lumen/restoration.py': '68a46f4939ec403ffb65286ccc05dbe2c99db52f55710ba10739f825ed21e995',
 'lumen/restoration_settings.py': '7609ef3901f1140793dced6331011f625424a087c24f1c0bbd4c09cf107363e9',
 'lumen/sticker_assets.py': '4eee4600fe9b6fddbf6701f1c898acb0179ebd377cf06b8d81555d77aaa1710e',
 'lumen/timing.py': '62dfad1e8f4552ad8510eeab9bd19dd0c8f07d9045dddf168496ad9d3dcd27ae',
 'lumen/vignette_shape.py': '5903a8686379bef2e885a2fe79a0b33288114b7e5c2cc80a5fbe77b13671c61e',
 'lumen/window_animation.py': 'bccf59011b76c296f39c790028ed64120fbe16568a656fc53ea5c6d18bb4c9f8'}


def frozen_baseline(path=None):
    value = Path(path or os.environ.get('CUTCAT_FINAL_FRAME_BASELINE',
                    ROOT.parent / 'cutcat-audio-gain-keyframes')).resolve()
    # Fresh extraction can use its byte-identical legacy compiler. An explicit
    # root is never silently substituted, and the hash guards against drift.
    if not value.exists() and path is None and 'CUTCAT_FINAL_FRAME_BASELINE' not in os.environ:
        value = ROOT
    renderer = (value / 'tests/frozen_final_frame/render.py' if value == ROOT else value / 'lumen/render.py')
    actual = hashlib.sha256(renderer.read_bytes()).hexdigest()
    if actual != BASELINE_RENDER_SHA256:
        raise AssertionError(f'Frozen full renderer changed: {value}: {actual}')
    for name, expected in BASELINE_VISUAL_DEPENDENCIES.items():
        dependency = value / name
        if value == ROOT and Path(name).name in ('model.py', 'caption_render.py', 'caption_layout.py',
                                                'render.py', 'color_media.py', 'clip_composite.py', 'overlay_render.py', 'media.py'):
            dependency = ROOT / 'tests/frozen_final_frame' / Path(name).name
        if hashlib.sha256(dependency.read_bytes()).hexdigest() != expected:
            raise AssertionError(f'Frozen full-render dependency changed: {name}')
    return value


def ff(*args, data=None, timeout=120):
    from lumen.media import ffmpeg_binary
    return subprocess.run([ffmpeg_binary(), '-hide_banner', '-v', 'error', '-nostdin', '-y',
                           '-threads', '1', *map(str, args)], input=data,
                          capture_output=True, check=True, timeout=timeout).stdout


def make_source(directory, rate, width=64, height=48, count=32, transfer='bt709'):
    """Numbered, non-flat RGB16 frames whose temporal order is visibly distinct."""
    import numpy as np
    from lumen.media import probe
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'numbered-{width}x{height}-{rate.numerator}-{rate.denominator}-{count}-{transfer}.mkv'
    # Stream source creation to avoid a count*canvas array (1080p memory check).
    from lumen.media import ffmpeg_binary
    cmd = [ffmpeg_binary(), '-v', 'error', '-y', '-threads', '1', '-f', 'rawvideo',
           '-pix_fmt', 'gbrp16le', '-s', f'{width}x{height}', '-r', str(rate),
           '-colorspace', 'rgb', '-color_range', 'pc', '-color_trc', transfer,
           '-color_primaries', 'bt709', '-i', 'pipe:0', '-an', '-c:v', 'ffv1',
           '-level', '3', '-pix_fmt', 'gbrp16le', '-threads', '1',
           '-colorspace', 'rgb', '-color_range', 'pc', '-color_trc', transfer,
           '-color_primaries', 'bt709', str(path)]
    yy, xx = np.mgrid[:height, :width]
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
        try:
            for n in range(count):
                rgb = np.empty((height, width, 3), '<u2')
                rgb[..., 0] = ((xx * 257 + yy * 43 + n * 991) % 50000) + 7000
                rgb[..., 1] = ((yy * 431 + xx * 31 + n * 617) % 43000) + 12000
                rgb[..., 2] = ((xx * 73 + yy * 53 + n * 1379) % 48000) + 8000
                # Binary frame number tiles survive fitting and 4:2:0 conversion.
                tile = max(2, min(width // 8, height // 6))
                for bit in range(5):
                    rgb[:tile, bit * tile:(bit + 1) * tile] = 58000 if n & (1 << bit) else 4000
                proc.stdin.write(np.moveaxis(rgb[..., [1, 2, 0]], -1, 0).astype('<u2').tobytes())
            proc.stdin.close(); proc.wait(timeout=120)
            if proc.returncode:
                log.seek(0); raise RuntimeError(log.read().decode(errors='replace'))
        finally:
            if proc.poll() is None: proc.kill(); proc.wait()
    return probe(path)


def make_stickers(directory, length=13):
    from PySide6.QtGui import QColor, QImage
    from lumen.sticker_assets import load_sticker
    from lumen.model import Overlay, OverlayKeyframe
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    names = []
    for index, rgba in enumerate(((248, 30, 70, 192), (30, 220, 90, 128), (45, 75, 245, 224))):
        image = QImage(14, 12, QImage.Format.Format_RGBA8888)
        image.fill(QColor(*rgba))
        name = f'frame-{index}.png'; assert image.save(str(directory / name), 'PNG'); names.append(name)
    manifest = directory / 'animation.json'
    manifest.write_text(json.dumps(dict(version=1, frames=names, frame_duration=2, loop=True)))
    asset = load_sticker(manifest)
    animated = Overlay('animated-sticker', str(manifest), asset.digest, 2, max(1, length-4),
                       keyframes=[OverlayKeyframe(0, 2, 4, 1, .7),
                                  OverlayKeyframe(5, 17, 7, 1.3, .9)])
    static_asset = load_sticker(directory / names[0])
    static = Overlay('static-sticker', str(directory / names[0]), static_asset.digest, 0, length,
                     z_order=-1, keyframes=[OverlayKeyframe(0, 36, 4, .75, .65)])
    return [static, animated]


def make_scene(directory, rate=Fraction(24), *, width=64, height=48,
               length=13, variant='complex', source=None):
    """All time-varying edits are pinned before frame 10, independent of tail."""
    from lumen.clip_composite import ClipComposite
    from lumen.clip_motion import ClipMotionKeyframe as K
    from lumen.clip_transform import ClipTransform
    from lumen.model import Caption, CaptionStyle, Clip, ClipEffect, Grade, Project
    from lumen.qualifier import QualifierSettings, WindowKeyframe
    from lumen.timing import ClipTimeMap
    root = Path(directory); root.mkdir(parents=True, exist_ok=True)
    source = source or make_source(root, rate, width, height, max(length+3, 32))
    media = deepcopy(source)
    # First and final holes expose black-base global timing, including a higher
    # track ending before the lower one. Audio is deliberately absent.
    bottom = Clip('bottom', media.id, 0, 1, 1, length-2)
    project = Project(width=width, height=height, fps_num=rate.numerator, fps_den=rate.denominator,
                      media=[media], clips=[bottom])
    if variant == 'legacy':
        bottom.transform = ClipTransform(x=3, y=-2, scale=.85, rotation=90, mirror_x=True)
        bottom.effects = [ClipEffect('vignette', 'vignette', .18)]
    elif variant in ('complex', 'fractional', 'fractional_slow', 'fractional_fast', 'static'):
        top = Clip('top', media.id, 1, 3, 2, min(7, length-4),
                   composite=ClipComposite(True, .65),
                   transform=ClipTransform(x=3, y=-2, scale=.65, rotation=17, mirror_x=True))
        if variant != 'static':
            top.motion_keyframes = [K(-2, -7, 2, .45, .15), K(2, 4, -3, .7, .7, 'hold'),
                                    K(4, 6, 3, .8, 0), K(6, -3, 1, .6, 1)]
        if variant.startswith('fractional'):
            start = Fraction(5, 2)
            speed = {'fractional': Fraction(3, 2), 'fractional_slow': Fraction(1, 4),
                     'fractional_fast': Fraction(4)}[variant]
            top.source_in = start
            top.time_map = ClipTimeMap(start, start+top.duration*speed, speed)
        top.effects = [ClipEffect('blur', 'blur', .8),
                       ClipEffect('vignette', 'vignette', .22, radius=.75, feather=.9)]
        top.grade = Grade(exposure=.12, saturation=.8,
                          red_curve=[[0., 0.], [.5, .55], [1., 1.]])
        top.grade.qualifier = QualifierSettings(enabled=True, hue_enabled=False,
            saturation_enabled=False, luma_enabled=False, exposure=.4, saturation=.7,
            window_shape='ellipse', window_feather=.2,
            window_keyframes=[WindowKeyframe(-2, .2, .5, .4, .6),
                              WindowKeyframe(6, .8, .5, .7, .6)])
        lut = root / 'cross.cube'
        lut.write_text('LUT_3D_SIZE 2\n' + ''.join(
            f'{.05+.8*b+.05*r*g} {.03+.85*r} {.02+.75*g+.08*b*r}\n'
            for b in (0, 1) for g in (0, 1) for r in (0, 1)))
        top.grade.lut_path, top.grade.lut_strength = str(lut), .4
        project.clips.append(top)
        project.overlays = make_stickers(root / 'stickers', length)
        project.captions = [Caption('first', 2, 2, 'A'), Caption('second', 4, 3, 'B')]
        project.caption_style = CaptionStyle(font_size=max(8, height//5), margin_bottom=3,
                                            outline_width=0, safe_margin_percent=5)
    # Keep a real explicit gap at the last project frame (metadata duration is
    # defined by a disabled overlay without adding any visible pixels).
    from lumen.model import Overlay
    project.overlays.append(Overlay('duration-only', '/not-read-disabled.png', '0'*64,
                                    length-1, 1, enabled=False))
    # Synthetic acceptance fixtures use the provisioned, hash-verified asset.
    # Do not change the product's default font or silently accept a broken cache.
    font_directory = os.environ.get('LUMEN_TEST_CJK_FONT_DIR')
    if font_directory and project.captions:
        from scripts.provision_test_font import verify_fixture
        verified = verify_fixture(font_directory)
        project.caption_style.font_directory = verified['font_directory']
        project.caption_style.font_family = 'Noto Sans CJK SC'
    project.validate()
    return project


def write_project(project, path):
    # Exact asdict serialization; no save-as rewriting or source copies.
    Path(path).write_text(json.dumps(project.to_dict()), encoding='utf-8')


def write_frozen_project(project, path):
    """Explicit schema43 -> pinned schema41 transport for historical scenes only.

    Linked scenes must use their own current-schema oracle, never silently lose
    relations. All old fields and the caller's project remain exactly intact.
    """
    data = project.to_dict()
    assert type(data['version']) is int and data['version'] == 43
    assert type(data['av_links']) is list and data['av_links'] == []
    assert data.pop('av_links') == []
    data['version'] = 41
    Path(path).write_text(json.dumps(data), encoding='utf-8')


FROZEN_BOOTSTRAP = r'''
import importlib.util,os,pathlib,sys
if os.environ.get('CUTCAT_FROZEN_MODULES'):
 import lumen
 for leaf in ('clip_composite','model','caption_layout','caption_render','media','color_media','overlay_render','render'):
  name='lumen.'+leaf
  spec=importlib.util.spec_from_file_location(name,pathlib.Path(os.environ['CUTCAT_FROZEN_MODULES'])/(leaf+'.py'))
  module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
  setattr(lumen,leaf,module)
'''


def frozen_environment(root, directory):
    """Only isolated frozen workers receive the pinned fallback modules."""
    root = frozen_baseline(root)
    env = dict(os.environ, PYTHONPATH=str(root), QT_QPA_PLATFORM='offscreen', TMPDIR=str(directory))
    env.pop('CUTCAT_FROZEN_CAPTIONS', None)
    env.pop('CUTCAT_FROZEN_MODULES', None)
    if root == ROOT:
        env['CUTCAT_FROZEN_MODULES'] = str(ROOT / 'tests/frozen_final_frame')
    return env


def frozen_full_pixels(project, directory, baseline=None):
    """All frames, raw final vout bytes from the frozen full compiler process."""
    root = frozen_baseline(baseline)
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    project_path, target = directory / 'project.json', directory / 'full.yuv'
    write_frozen_project(project, project_path)
    code = FROZEN_BOOTSTRAP + r'''
import hashlib,json,pathlib,subprocess,sys
from lumen.model import Project
from lumen.render import RenderEngine
from lumen.media import ffmpeg_binary
p=Project.from_dict(json.loads(pathlib.Path(sys.argv[1]).read_text()))
e=RenderEngine(timeout=180)
try:
 inputs,graph,_=e._compile(p,filter_complex_threads=1)
 assert graph.endswith('[aout]')
 graph=graph[:-len('[aout]')]+',anullsink'
 graphpath=pathlib.Path(sys.argv[2]).with_suffix('.ffgraph');graphpath.write_text(graph)
 cmd=[ffmpeg_binary(),'-v','error','-nostdin','-y','-filter_complex_threads','1',*inputs,
      '-/filter_complex',str(graphpath),'-map','[vout]','-frames:v',str(p.duration_frames),
      '-r',f'{p.fps_num}/{p.fps_den}','-fps_mode','cfr','-c:v','rawvideo','-threads','1',
      '-pix_fmt','yuv420p','-f','rawvideo',sys.argv[2]]
 subprocess.run(cmd,capture_output=True,check=True,timeout=180)
finally:e.close()
'''
    env = frozen_environment(root, directory)
    result = subprocess.run([sys.executable, '-c', code, str(project_path), str(target)],
                            cwd=root, env=env, capture_output=True, timeout=240)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace'))
    raw = target.read_bytes()
    assert len(raw) == project.duration_frames * project.width * project.height * 3 // 2
    return raw


def _tree_stats(pid):
    """Linux sampled simultaneous RSS, not sum of each child's separate maxRSS."""
    records = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit(): continue
        try:
            status = (entry / 'status').read_text()
            values = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
            records[int(entry.name)] = (int(values['PPid'].strip()), int(values.get('VmRSS', '0 kB').split()[0])*1024)
        except (OSError, ValueError, KeyError): pass
    tree = {pid}
    while True:
        next_tree = tree | {p for p, (parent, _) in records.items() if parent in tree}
        if next_tree == tree: break
        tree = next_tree
    anonymous = {}
    for process in tree:
        try:
            for descriptor in (Path('/proc') / str(process) / 'fd').iterdir():
                try:
                    target = os.readlink(descriptor)
                    if target.endswith(' (deleted)') and target.startswith('/tmp/'):
                        stat = descriptor.stat()
                        anonymous[(stat.st_dev, stat.st_ino)] = stat.st_size
                except OSError:
                    pass
        except OSError:
            pass
    return sum(records[p][1] for p in tree if p in records), len(tree), sum(anonymous.values())


def _temporary_stats(root):
    total, extensions, files = 0, set(), 0
    for directory, _, names in os.walk(root):
        for name in names:
            path = Path(directory) / name
            try: total += path.stat().st_size; files += 1; extensions.add(path.suffix)
            except OSError: pass
    return total, files, extensions


def _worker(args):
    sys.path.insert(0, str(args.runtime_root))
    if args.worker == 'preview':
        # Direct --worker invocations must not trust inherited fallback paths.
        pinned = frozen_environment(args.runtime_root, os.environ.get('TMPDIR', tempfile.gettempdir()))
        for key in ('CUTCAT_FROZEN_CAPTIONS', 'CUTCAT_FROZEN_MODULES'):
            if key in pinned:
                os.environ[key] = pinned[key]
            else:
                os.environ.pop(key, None)
        exec(FROZEN_BOOTSTRAP, {})
    from lumen.model import Project
    from lumen.media import ffmpeg_binary
    from lumen.render import RenderEngine
    p = Project.from_dict(json.loads(args.project.read_text()))
    began = time.perf_counter(); details = {}
    if args.worker == 'still':
        # Observed subprocess/grade work, independent of backend self-reports.
        commands, grade_shapes = [], []
        original_popen = subprocess.Popen
        def traced_popen(command, *positional, **keywords):
            commands.append([str(item) for item in command])
            return original_popen(command, *positional, **keywords)
        subprocess.Popen = traced_popen
        import lumen.color_picker_source as ingress
        original_grade = ingress.apply_grade
        def traced_grade(pixels, *positional, **keywords):
            grade_shapes.append(list(pixels.shape))
            return original_grade(pixels, *positional, **keywords)
        ingress.apply_grade = traced_grade
        from lumen.final_frame_preview import capture_final_frame_snapshot
        value = capture_final_frame_snapshot(p, args.frame, timeout=120)
        raw = value.yuv420p
        from dataclasses import asdict
        details['work'] = asdict(value.work)
        details['observed_commands'] = commands
        details['observed_grade_shapes'] = grade_shapes
    elif args.worker == 'cancel':
        from lumen.final_frame_preview import capture_final_frame_snapshot
        cancel = threading.Event(); fired = []
        def stop(): fired.append(time.perf_counter()); cancel.set()
        timer = threading.Timer(.2, stop); timer.start()
        try:
            value = capture_final_frame_snapshot(p, args.frame, cancel=cancel, timeout=120)
            details['cancel_result'] = 'completed_before_cancel'; raw = value.yuv420p
        except Exception as exc:
            details['cancel_result'] = type(exc).__name__; details['cancel_message'] = str(exc); raw = b''
        finally: timer.cancel(); timer.join()
        details['cancel_to_return_seconds'] = time.perf_counter()-fired[0] if fired else None
    else:
        engine = RenderEngine(timeout=900)
        try:
            path = engine.preview(p)
            raw = ff('-i', path, '-vf', f'select=eq(n\\,{args.frame})', '-frames:v', 1,
                     '-pix_fmt', 'yuv420p', '-f', 'rawvideo', 'pipe:1', timeout=120)
            details['preview_bytes'] = path.stat().st_size
            details['cache_bytes_before_cleanup'] = _temporary_stats(engine._cache_dir.name)[0]
        finally: engine.close()
    details.update(seconds=time.perf_counter()-began, sha256=hashlib.sha256(raw).hexdigest(),
                   bytes=len(raw), mode=args.worker)
    print(json.dumps(details), flush=True)


def measure(mode, root, project, directory, frame=5):
    directory.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', mode,
               '--runtime-root', str(root), '--project', str(project), '--frame', str(frame)]
    env = dict(os.environ, TMPDIR=str(directory), QT_QPA_PLATFORM='offscreen',
               PYTHONPATH=str(root), OPENBLAS_NUM_THREADS='1')
    env.pop('CUTCAT_FROZEN_CAPTIONS', None)
    env.pop('CUTCAT_FROZEN_MODULES', None)
    if mode == 'preview':
        env.update(frozen_environment(root, directory))
    env['OPENBLAS_NUM_THREADS'] = '1'
    began = time.perf_counter(); peak_rss=peak_files=peak_temp=peak_processes=0; suffixes=set()
    with (directory / 'stdout.log').open('w') as out, (directory / 'stderr.log').open('w') as err:
        child = subprocess.Popen(command, cwd=root, env=env, stdout=out, stderr=err)
        while child.poll() is None:
            rss, processes, anonymous = _tree_stats(child.pid)
            size, files, extensions = _temporary_stats(directory)
            size += anonymous
            peak_rss=max(peak_rss,rss); peak_processes=max(peak_processes,processes)
            peak_temp=max(peak_temp,size); peak_files=max(peak_files,files); suffixes.update(extensions)
            if time.perf_counter()-began > 1200:
                child.kill(); child.wait(); raise TimeoutError('Cold worker exceeded 20 minutes')
            time.sleep(.025)
    stderr = (directory / 'stderr.log').read_text()
    stdout = (directory / 'stdout.log').read_text()
    if child.returncode:
        raise RuntimeError(f'{mode} failed: {stderr[-10000:]}\n{stdout}')
    row = json.loads(stdout.strip().splitlines()[-1])
    row.update(end_to_end_seconds=time.perf_counter()-began, peak_tree_rss_bytes=peak_rss,
               peak_temporary_bytes=peak_temp, peak_files=peak_files, peak_processes=peak_processes,
               observed_extensions=sorted(suffixes), sample_interval_seconds=.025,
               load_after=os.getloadavg())
    return row


def benchmark(args):
    sys.path.insert(0,str(ROOT))
    baseline=frozen_baseline(args.baseline)
    out=args.output.resolve(); out.mkdir(parents=True,exist_ok=False)
    rate=Fraction(24); fixtures=out/'fixtures'; fixtures.mkdir()
    source=make_source(fixtures,rate,args.width,args.height,303)
    projects={}
    expected={}
    for length in (30,300):
        p=make_scene(fixtures/f'scene-{length}',rate,width=args.width,height=args.height,
                     length=length,source=source)
        # A sustained two-track composition, not a mostly empty long timeline.
        # The held motion/window endpoints and all target-frame inputs stay
        # identical; only the unrelated rendered tail becomes longer.
        p.clips[1].duration = length-4
        p.validate()
        path=fixtures/f'scene-{length}.json';write_project(p,path)
        frozen_path=fixtures/f'scene-{length}-schema41.json';write_frozen_project(p,frozen_path)
        projects[length]={'still':path,'preview':frozen_path}
        # The short scene oracle proves target correctness. Target source and
        # all active settings are identical in the long scene; only tails vary.
        if length==30:
            raw=frozen_full_pixels(p,fixtures/'oracle',baseline)
            size=p.width*p.height*3//2
            expected[30]=hashlib.sha256(raw[5*size:6*size]).hexdigest()
            (fixtures/'oracle'/'full.yuv').unlink()
    expected[300]=expected[30]
    from lumen.media import ffmpeg_binary
    ffmpeg_version = subprocess.run([ffmpeg_binary(), '-version'], capture_output=True,
                                    text=True, check=True, timeout=30).stdout.splitlines()[0]
    report=dict(baseline_commit=BASELINE_COMMIT,baseline_render_sha256=BASELINE_RENDER_SHA256,
                python_version=sys.version,ffmpeg_version=ffmpeg_version,load_before=os.getloadavg(),
                candidate_backend_sha256=hashlib.sha256((ROOT/'lumen/final_frame_preview.py').read_bytes()).hexdigest(),
                candidate_capture_scope_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                    for name in sorted(set(BASELINE_VISUAL_DEPENDENCIES) |
                        {'lumen/final_frame_preview.py','lumen/color_picker_source.py',
                         'lumen/frame_grade_preview.py','lumen/source_viewer.py'})},
                benchmark_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                width=args.width,height=args.height,target_frame=5,rate='24/1',repeats=args.repeats,
                method='Fresh Python process and fresh renderer/cache; OS page cache not forcibly dropped. '
                       '25 ms simultaneous process-tree RSS and temporary-file footprint sampling '
                       '(named files plus unique anonymous/deleted /tmp FDs). '
                       'Full preview includes real MP4/audio then target decode; still exactness uses frozen '
                       'full pre-encoding YUV420 oracle, never H.264 tolerance.',measurements=[],summaries={})
    def save(): (out/'results.json').write_text(json.dumps(report,indent=2))
    save()
    for repeat in range(args.repeats):
        for length in ((30,300) if repeat%2==0 else (300,30)):
            for mode in (('still','preview') if repeat%2==0 else ('preview','still')):
                row=measure(mode,ROOT if mode=='still' else baseline,projects[length][mode],
                            out/f'{mode}-{length}-{repeat}')
                row.update(length=length,repeat=repeat)
                assert row['bytes'] == args.width*args.height*3//2, row
                if mode=='still':
                    row['exact_frozen_oracle']=row['sha256']==expected[length]
                    assert row['exact_frozen_oracle'], row
                    assert not {'.mp4','.mkv'} & set(row['observed_extensions']), row
                    assert row['work']['graded_frames'] == 2 and row['work']['composite_frames'] == 1
                    assert row['work']['source_frames'] == [5,4]
                    assert row['observed_grade_shapes'] == [[args.height,args.width,3]]*2
                report['measurements'].append(row);save();print(json.dumps(row),flush=True)
    med={(mode,length):statistics.median(row['end_to_end_seconds'] for row in report['measurements']
         if row['mode']==mode and row['length']==length) for mode in ('still','preview') for length in (30,300)}
    report['summaries']={f'{mode}_{length}_median_seconds':value for (mode,length),value in med.items()}
    report['summaries']['time_basis'] = 'Fresh process launch to verified returned target bytes, including startup'
    report['summaries']['operation_only_median_seconds'] = {
        f'{mode}_{length}': statistics.median(row['seconds'] for row in report['measurements']
            if row['mode']==mode and row['length']==length)
        for mode in ('still','preview') for length in (30,300)}
    report['summaries'].update(still_300_reduction_fraction=1-med['still',300]/med['preview',300],
        still_tail_ratio=med['still',300]/med['still',30],
        meets_50percent_target=med['still',300]<=.5*med['preview',300])
    if args.fullhd:
        hd=fixtures/'1080p';source_hd=make_source(hd,rate,1920,1080,12)
        p=make_scene(hd,rate,width=1920,height=1080,length=10,source=source_hd)
        path=hd/'scene.json';write_project(p,path)
        for mode in ('still','cancel'):
            row=measure(mode,ROOT,path,out/f'1080p-{mode}');row.update(width=1920,height=1080)
            row['within_768_mib_tree_rss'] = row['peak_tree_rss_bytes'] <= 768*1024*1024
            report.setdefault('fullhd',[]).append(row);save();print(json.dumps(row),flush=True)
    save();print(json.dumps(report['summaries']),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--baseline',type=Path)
    parser.add_argument('--width',type=int,default=320);parser.add_argument('--height',type=int,default=180)
    parser.add_argument('--repeats',type=int,default=3);parser.add_argument('--fullhd',action='store_true')
    parser.add_argument('--worker',choices=('still','preview','cancel'))
    parser.add_argument('--runtime-root',type=Path);parser.add_argument('--project',type=Path)
    parser.add_argument('--frame',type=int,default=5)
    args=parser.parse_args()
    if args.worker:_worker(args)
    else:
        if args.output is None:parser.error('--output is required')
        benchmark(args)

if __name__=='__main__':main()

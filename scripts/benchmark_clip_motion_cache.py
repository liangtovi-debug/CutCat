"""Sequential synthetic exact-output baseline/candidate benchmark, not playback FPS.

Run from the source root: python scripts/benchmark_clip_motion_cache.py --output DIR
No user media, network, dependencies, or performance assertions. Includes startup,
raw I/O and three animated frames; excludes decode, grade, probe, audio and codecs.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from lumen.clip_composite import ClipComposite, clip_composite_filters
from lumen.clip_transform import ClipTransform
from lumen.clip_motion import ClipMotionKeyframe as K
from lumen.media import ffmpeg_binary


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(output, rounds=2):
    output.mkdir(parents=True, exist_ok=False)
    old_path = ROOT / 'tests/clip_composite_uncached_fixture.py'
    old = types.ModuleType('_benchmark_uncached');old.__package__ = 'lumen'
    sys.modules[old.__name__] = old
    exec(compile(old_path.read_text(), str(old_path), 'exec'), old.__dict__)
    builders = {'baseline': old.clip_composite_filters, 'candidate': clip_composite_filters}
    binary = ffmpeg_binary()
    startup = []
    for _ in range(5):
        began = time.perf_counter()
        version = subprocess.run([binary, '-version'], capture_output=True, check=True, timeout=10)
        startup.append(time.perf_counter() - began)
    report = dict(scope=__doc__, utc_started=datetime.now(timezone.utc).isoformat(),
        platform=platform.platform(), cpu_count=os.cpu_count(),
        ffmpeg=version.stdout.decode().splitlines()[0],
        source_sha256={p: digest((ROOT / p).read_bytes()) for p in (
            'lumen/clip_composite.py', 'lumen/clip_motion_render.py',
            'tests/clip_composite_uncached_fixture.py')},
        ffmpeg_version_startup_seconds=startup, measurements=[], summaries=[])
    def save():
        (output/'results.json').write_text(json.dumps(report, indent=2)+'\n')
    save()
    for width,height in ((1920,1080),(3840,2160)):
        for high in (False, True):
            pixfmt = 'yuv422p10le' if high else 'yuv420p'
            native = b''.join(np.full((height if i == 0 or high else height//2,
                width if i == 0 else width//2),v,dtype='<u2' if high else 'u1').tobytes()
                for i,v in enumerate((288,512,512) if high else (72,128,128)))
            foreground = b''.join(np.full((height,width),v,dtype='<u2').tobytes()
                for v in (1532,50000,32123))
            # Same deterministic three frames as the accepted animation benchmark.
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'foreground.gbrp16le';path.write_bytes(foreground*3)
                for repeat in range(rounds):
                    # Reverse order on alternating repetitions to expose drift.
                    for name in (('baseline','candidate') if repeat % 2 == 0 else ('candidate','baseline')):
                        main,layer,overlay = builders[name](ClipComposite(True,.5),
                            ClipTransform(scale=.5,rotation=17),width,height,high_bit=high,
                            motion_keys=[K(0,x=-40,scale=.4,opacity=.25),K(2,x=40,scale=.6,opacity=.75)])
                        graph=f'[0:v]{main}[main];[1:v]{layer}[layer];[main][layer]{overlay}[out]'
                        graph_file=output/f'{width}-{height}-{int(high)}-{name}.ffgraph'
                        graph_file.write_text(graph)
                        command=[binary,'-hide_banner','-v','info','-benchmark','-nostdin',
                            '-noauto_conversion_filters','-filter_complex_threads','1',
                            '-f','rawvideo','-pix_fmt',pixfmt,'-s',f'{width}x{height}','-r','1','-i','pipe:0',
                            '-f','rawvideo','-pix_fmt','gbrp16le','-s',f'{width}x{height}','-r','1','-i',str(path),
                            '-/filter_complex',str(graph_file),'-map','[out]','-frames:v','3','-an',
                            '-c:v','rawvideo','-threads','1','-pix_fmt',pixfmt,'-f','rawvideo','pipe:1']
                        load=os.getloadavg();utc=datetime.now(timezone.utc).isoformat();began=time.perf_counter()
                        result=subprocess.run(command,input=native*3,capture_output=True,timeout=240)
                        elapsed=time.perf_counter()-began
                        stderr=result.stderr.decode(errors='replace')
                        (output/f'{width}-{height}-{int(high)}-{repeat}-{name}.log').write_text(stderr)
                        row=dict(variant=name,repeat=repeat,width=width,height=height,high_bit=high,
                            frames=3,seconds=elapsed,amortized_seconds_per_frame=elapsed/3,
                            utc_started=utc,load_before=load,load_after=os.getloadavg(),
                            ffmpeg_benchmark=re.findall(r'bench:.*',stderr),
                            main_input_sha256=digest(native*3),foreground_input_sha256=digest(foreground*3),
                            filter_graph_sha256=digest(graph.encode()),output_sha256=digest(result.stdout),
                            output_bytes=len(result.stdout),returncode=result.returncode)
                        report['measurements'].append(row);save();print(json.dumps(row),flush=True)
                        assert result.returncode==0 and len(result.stdout)==len(native)*3,row
                rows=[r for r in report['measurements'] if r['width']==width and r['high_bit']==high]
                assert len({r['output_sha256'] for r in rows})==1,'Exact output mismatch'
                baseline=statistics.median(r['seconds'] for r in rows if r['variant']=='baseline')
                candidate=statistics.median(r['seconds'] for r in rows if r['variant']=='candidate')
                report['summaries'].append(dict(width=width,height=height,high_bit=high,
                    baseline_median_seconds=baseline,candidate_median_seconds=candidate,
                    speedup=baseline/candidate,reduction_percent=100*(1-candidate/baseline),
                    exact_output=True,output_sha256=rows[0]['output_sha256']))
                save()
    report['utc_finished']=datetime.now(timezone.utc).isoformat();save()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--rounds',type=int,default=2,choices=range(2,5))
    args=parser.parse_args();run(args.output.resolve(),args.rounds)

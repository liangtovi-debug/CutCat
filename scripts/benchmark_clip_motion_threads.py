"""Serial alternating exact-pixel 1/2/4-thread synthetic benchmark.

Run from source root with --output NEW_DIRECTORY. Three repetitions per
size/precision/stage; no timing assertions, network, or user media. Includes
process startup and raw I/O, not grade/decode/audio/delivery encode. FFmpeg
maxrss can include pre-exec inherited memory; do not attribute it solely to
filter workers. Use the separate full-export benchmark for end-to-end costs.
"""
import sys,os,json,time,hashlib,subprocess,tempfile,statistics,platform
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from lumen.clip_composite import ClipComposite,clip_composite_filters
from lumen.clip_transform import ClipTransform
from lumen.clip_motion import ClipMotionKeyframe as K
from lumen.media import ffmpeg_binary
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,required=True)
out=parser.parse_args().output.resolve();out.mkdir(parents=True,exist_ok=False)
report=dict(cpu_count=os.cpu_count(),cpu_quota='not measured; read host/container quota separately',platform=platform.platform(),measurements=[],summaries=[])
startup=[]
for _ in range(5):
 began=time.perf_counter();version=subprocess.run([ffmpeg_binary(),'-version'],capture_output=True,check=True,timeout=10);startup.append(time.perf_counter()-began)
report['ffmpeg_version']=version.stdout.decode().splitlines()[0]
report['ffmpeg_version_startup_seconds']=startup
report['source_sha256']={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('lumen/clip_composite.py','lumen/clip_motion_render.py','lumen/render.py')}
def save(): (out/'results.json').write_text(json.dumps(report,indent=2))
for w,h in ((1920,1080),(3840,2160)):
 for high in (False,True):
  pix='yuv422p10le' if high else 'yuv420p'
  main=b''.join(np.full((h if i==0 or high else h//2,w if i==0 else w//2),v,dtype='<u2' if high else 'u1').tobytes() for i,v in enumerate((288,512,512) if high else (72,128,128)))
  fg=b''.join(np.full((h,w),v,dtype='<u2').tobytes() for v in (1532,50000,32123))
  a,b,c=clip_composite_filters(ClipComposite(True,.5),ClipTransform(scale=.5,rotation=17),w,h,high_bit=high,motion_keys=[K(0,x=-40,scale=.4,opacity=.25),K(2,x=40,scale=.6,opacity=.75)])
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'fg.raw';p.write_bytes(fg*3)
   for stage in ('raw_rgb16_alpha','full_composite'):
    graph=f'[1:v]{b.split(",zscale=",1)[0]}[out]' if stage=='raw_rgb16_alpha' else f'[0:v]{a}[main];[1:v]{b}[layer];[main][layer]{c}[out]'
    path=out/f'{w}-{high}-{stage}.ffgraph';path.write_text(graph);fmt='gbrap16le' if stage=='raw_rgb16_alpha' else pix
    for rep in range(3):
     for n in ((1,2,4) if rep%2==0 else (4,2,1)):
      cmd=[ffmpeg_binary(),'-hide_banner','-v','info','-benchmark','-nostdin','-noauto_conversion_filters','-filter_complex_threads',str(n),'-f','rawvideo','-pix_fmt',pix,'-s',f'{w}x{h}','-r','1','-i','pipe:0','-f','rawvideo','-pix_fmt','gbrp16le','-s',f'{w}x{h}','-r','1','-i',str(p),'-/filter_complex',str(path),'-map','[out]','-frames:v','3','-an','-c:v','rawvideo','-threads','1','-pix_fmt',fmt,'-f','rawvideo','pipe:1']
      load=os.getloadavg();t=time.perf_counter();r=subprocess.run(cmd,input=main*3,capture_output=True,timeout=240);secs=time.perf_counter()-t
      stderr=r.stderr.decode(errors='replace');(out/f'{w}-{high}-{stage}-{rep}-{n}.log').write_text(stderr)
      row=dict(width=w,height=h,high=high,stage=stage,repeat=rep,threads=n,seconds=secs,load_before=load,load_after=os.getloadavg(),returncode=r.returncode,sha256=hashlib.sha256(r.stdout).hexdigest(),bytes=len(r.stdout),bench=[s for s in stderr.splitlines() if s.startswith('bench:')]);report['measurements'].append(row);save();print(json.dumps(row),flush=True);assert r.returncode==0 and len(r.stdout)==3*w*h*(8 if stage=='raw_rgb16_alpha' else 4 if high else 1.5)
    rows=[r for r in report['measurements'] if (r['width'],r['high'],r['stage'])==(w,high,stage)]
    med={n:statistics.median(r['seconds'] for r in rows if r['threads']==n) for n in (1,2,4)}
    report['summaries'].append(dict(width=w,high=high,stage=stage,median=med,speedups={n:med[1]/med[n] for n in (2,4)},exact=len({r['sha256'] for r in rows})==1));save()
assert all(row['exact'] for row in report['summaries']), 'Cross-thread output mismatch'
print('DONE',flush=True)

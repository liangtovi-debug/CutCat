"""Cold single-frame grade review versus cold full-timeline proxy build.
Synthetic RGB16 input only; these are different deliverables, not equivalent engines.
"""
from pathlib import Path
import sys, os, json, time, resource, platform, subprocess
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
BASE=Path(os.environ.get('LUMEN_FRAME_BENCHMARK_OUTPUT', Path.cwd()/'.frame-grade-benchmark')).resolve()
(BASE/'evidence').mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(ROOT))
from lumen.model import Grade, Clip, Project
from lumen.media import ffmpeg_binary, probe
from lumen.render import RenderEngine

def ff(*args,data=None):
 return subprocess.run([ffmpeg_binary(),'-hide_banner','-v','error','-nostdin','-y','-threads','1',*map(str,args)],input=data,capture_output=True,check=True,timeout=300).stdout

def fixtures():
 for w,h,n in [(1920,1080,30),(3840,2160,8)]:
  source=BASE/'evidence'/f'seed-{w}.mkv'
  target=BASE/'evidence'/f'fixture-{w}.mkv'
  yy,xx=np.mgrid[:h,:w]
  rgb=np.stack([((xx*59+yy*17)%65536),((xx*13+yy*47)%65536),((xx*31+yy*7)%65536)],axis=2).astype('<u2')
  planar=np.moveaxis(rgb[..., [1,2,0]],-1,0).copy()
  tags=['-colorspace','rgb','-color_range','pc','-color_trc','bt709','-color_primaries','bt709']
  ff('-f','rawvideo','-pix_fmt','gbrp16le','-s',f'{w}x{h}','-r',30,*tags,'-i','pipe:0','-frames:v',1,'-c:v','ffv1','-level',3,'-pix_fmt','gbrp16le',*tags,source,data=planar.tobytes())
  assert ff('-i',source,'-frames:v',1,'-pix_fmt','gbrp16le','-f','rawvideo','pipe:1')==planar.tobytes()
  ff('-stream_loop','-1','-i',source,'-frames:v',n,'-c:v','copy',target)

def worker(kind,w,h,n,r):
 from lumen.frame_grade_preview import capture_grade_preview_snapshot
 media=probe(BASE/'evidence'/f'fixture-{w}.mkv')
 grade=Grade(linear_exposure=.1, contrast=1.04,
  hue_hue_curve=[[0,.08],[.12,-.13],[.37,.22],[.69,-.3],[1,.08]],
  hue_sat_curve=[[0,.6],[.14,1.3],[.42,0],[.8,2],[1,.6]])
 project=Project(width=w,height=h,media=[media],clips=[Clip('c',media.id,0,0,0,n,grade=grade)])
 engine=RenderEngine(timeout=600)
 try:
  start=time.perf_counter()
  if kind=='frame':
   snap=capture_grade_preview_snapshot(project,'c',n//2,timeout=120)
   elapsed=time.perf_counter()-start
   assert snap.is_current(project,'c',n//2) and snap.frame.shape==(h,w,3)
   boundary='full-resolution float32 selected clip, before effects/composite; display conversion not timed'
  else:
   out=engine.preview(project)
   elapsed=time.perf_counter()-start
   assert out.is_file()
   boundary='complete playable 8-bit timeline proxy, decode+grade+composite+encode'
  print(json.dumps(dict(kind=kind,width=w,height=h,timeline_frames=n,requested_frame=n//2,repeat=r,total_seconds=elapsed,python_peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,boundary=boundary)),flush=True)
 finally:engine.close()

if __name__=='__main__':
 if len(sys.argv)>1 and sys.argv[1]=='--fixtures':fixtures();sys.exit()
 if len(sys.argv)>1:worker(sys.argv[1],*map(int,sys.argv[2:]));sys.exit()
 subprocess.run([sys.executable,__file__,'--fixtures'],check=True)
 meta=dict(platform=platform.platform(),python=sys.version,numpy=np.__version__,cpu=next((x.split(':',1)[1].strip() for x in Path('/proc/cpuinfo').read_text().splitlines() if x.startswith('model name')),'unknown'),rounds=3,order='alternating frame/whole first by round',warning='Different deliverables; no Resolve timing, realtime claim or export speedup inference. OS file cache is uncontrolled; each process/render cache is fresh.')
 (BASE/'evidence/benchmark-environment.json').write_text(json.dumps(meta,indent=2))
 with (BASE/'evidence/frame-benchmark.jsonl').open('w') as log:
  for w,h,n in [(1920,1080,30),(3840,2160,8)]:
   for r in range(3):
    for kind in (['frame','whole'] if r%2==0 else ['whole','frame']):
     p=subprocess.run([sys.executable,__file__,kind,str(w),str(h),str(n),str(r)],capture_output=True,text=True,timeout=600)
     if p.returncode:raise RuntimeError(p.stdout+p.stderr)
     obj=json.loads(p.stdout);log.write(json.dumps(obj)+'\n');log.flush();print(json.dumps(obj),flush=True)

"""Reproducible cold RGB16 render benchmark. No user media/network needed."""
from pathlib import Path
import sys,os,json,time,hashlib,resource,platform,subprocess,threading,importlib.util
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
BASE=Path(os.environ.get('LUMEN_COLOR_BENCHMARK_OUTPUT', Path.cwd()/'.color-performance-benchmark')).resolve()
(BASE/'evidence').mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(ROOT))
from lumen import color
from lumen.model import Grade,Clip,Project
from lumen.media import ffmpeg_binary,ffprobe_binary,probe
from lumen.render import RenderEngine,RenderCancelled
G=Grade(hue_hue_curve=[[0,.08],[.12,-.13],[.37,.22],[.69,-.3],[1,.08]],hue_sat_curve=[[0,.6],[.14,1.3],[.42,0],[.8,2],[1,.6]])
def ff(*args,data=None):
 return subprocess.run([ffmpeg_binary(),'-hide_banner','-v','error','-nostdin','-y','-threads','1',*map(str,args)],input=data,capture_output=True,check=True,timeout=300).stdout
def fixture(w,h,frames):
 path=BASE/'evidence'/f'fixture-{w}.mkv'
 if path.exists():return path
 yy,xx=np.mgrid[:h,:w]
 rgb=np.stack([((xx*59+yy*17)%65536),((xx*13+yy*47)%65536),((xx*31+yy*7)%65536)],axis=2).astype('<u2')
 planar=np.moveaxis(rgb[..., [1,2,0]],-1,0).copy()
 tags=['-colorspace','rgb','-color_range','pc','-color_trc','bt709','-color_primaries','bt709']
 ff('-f','rawvideo','-pix_fmt','gbrp16le','-s',f'{w}x{h}','-r',30,*tags,'-i','pipe:0','-frames:v',frames,'-c:v','ffv1','-level',3,'-pix_fmt','gbrp16le',*tags,path,data=planar.tobytes()*frames)
 assert ff('-i',path,'-frames:v',1,'-pix_fmt','gbrp16le','-f','rawvideo','pipe:1')==planar.tobytes()
 return path
def stats(xs):return dict(samples_seconds=xs,median_seconds=float(np.median(xs)),p95_seconds=float(np.percentile(xs,95)))
def worker(mode,w,h,frames,kind,repeat):
 if mode=='baseline':
  spec=importlib.util.spec_from_file_location('lumen.legacy_selective',ROOT/'tests/selective_color_v20_fixture.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);color.apply_selective_color=m.apply_selective_color
 media=probe(BASE/'evidence'/f'fixture-{w}.mkv')
 project=Project(width=w,height=h,media=[media],clips=[Clip('c',media.id,0,0,0,frames,grade=G)])
 grade_times=[]; fn=color.apply_grade;cancel=threading.Event();started=[]
 def measured(*a,**kw):
  t=time.perf_counter()
  if kind=='cancel':started.append(t);cancel.set()
  result=fn(*a,**kw);grade_times.append(time.perf_counter()-t);return result
 color.apply_grade=measured
 engine=RenderEngine(timeout=600)
 try:
  t=time.perf_counter();output=None
  try:
   output=engine.preview(project,cancel=cancel) if kind in ('preview','cancel') else engine.render(project,BASE/'evidence'/f'temp-{mode}-{w}-{repeat}.mov',export_profile='prores_422_hq_10bit')
   if kind=='cancel':raise AssertionError('Cancellation not honored')
  except RenderCancelled:
   assert kind=='cancel' and started
  elapsed=time.perf_counter()-t
  result=dict(mode=mode,width=w,height=h,frames=frames,kind=kind,repeat=repeat,total_seconds=elapsed,grade=stats(grade_times),rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024)
  if kind=='cancel':
   result['cancel_latency_seconds']=time.perf_counter()-started[0]
   result['cancel_cache_files']=[str(p.name) for p in Path(engine._cache_dir.name).rglob('*') if p.is_file()]
  else:
   result['decoded_sha256']=hashlib.sha256(ff('-i',output,'-pix_fmt','rgb48le','-f','rawvideo','pipe:1')).hexdigest()
   stage=engine._graded_segment(media,project.clips[0],project,None)
   result['rgb16_stage_sha256']=hashlib.sha256(ff('-i',stage,'-pix_fmt','rgb48le','-f','rawvideo','pipe:1')).hexdigest()
   result['output_probe']=json.loads(subprocess.run([ffprobe_binary(),'-v','error','-select_streams','v:0','-show_entries','stream=width,height,pix_fmt,color_space,color_transfer,color_primaries,color_range','-of','json',str(output)],capture_output=True,text=True,check=True).stdout)
   if kind=='preview':
    t=time.perf_counter();assert engine.preview(project)==output;result['warm_cache_seconds']=time.perf_counter()-t
   else:output.unlink()
  print(json.dumps(result),flush=True)
 finally:engine.close()
if __name__=='__main__':
 if len(sys.argv)>1 and sys.argv[1]=='--fixtures':
  for w,h,n in [(1920,1080,8),(3840,2160,4)]:fixture(w,h,n)
  sys.exit()
 if len(sys.argv)>1 and sys.argv[1]!='--run':worker(sys.argv[1],*map(int,sys.argv[2:5]),sys.argv[5],int(sys.argv[6]));sys.exit()
 meta=dict(platform=platform.platform(),python=sys.version,numpy=np.__version__,cpu=next((x.split(':',1)[1].strip() for x in Path('/proc/cpuinfo').read_text().splitlines() if x.startswith('model name')),'unknown') if Path('/proc/cpuinfo').exists() else platform.processor(),affinity=list(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else [],fixture='deterministic RGB16 modular gradients, no downsampling',rounds=5,order='alternating baseline/optimized first by round')
 (BASE/'evidence/render-environment.json').write_text(json.dumps(meta,indent=2))
 if len(sys.argv)==1:
  # Generate large RGB16 fixtures in a child. exec() alone does not reset
  # ru_maxrss, so the lightweight orchestrator must never allocate them.
  subprocess.run([sys.executable,__file__,'--fixtures'],check=True,timeout=600)
 out=(BASE/'evidence/render-benchmark.jsonl').open('w')
 for w,h,n in [(1920,1080,8),(3840,2160,4)]:
  for kind in ['preview','prores','cancel']:
   for r in range(5):
    for mode in (['baseline','optimized'] if r%2==0 else ['optimized','baseline']):
     proc=subprocess.run([sys.executable,__file__,mode,str(w),str(h),str(n),kind,str(r)],capture_output=True,text=True,timeout=600)
     if proc.returncode:raise RuntimeError(proc.stdout+proc.stderr)
     obj=json.loads(proc.stdout);out.write(json.dumps(obj)+'\n');out.flush();print(json.dumps(obj),flush=True)
 out.close()

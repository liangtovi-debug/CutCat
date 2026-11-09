"""Serial cold-export benchmark including cache, probes, audio and encoding.

Use --output NEW_DIRECTORY; --worker is internal. Three repeats per size,
precision and forced1/2/4 compositor count. Each worker has a fresh engine/cache;
source synthesis/import and result decoding/hashing are outside the measured
render call. Synthetic input only; never changes shipped scheduling policy.
Peak child RSS covers the whole pipeline, not just the compositor. Raw stage
measurements live in benchmark_clip_motion_threads.py. POSIX benchmark harness,
not native Windows/macOS acceptance or real-time playback evidence.
"""
import sys,os,time,json,hashlib,subprocess,resource,statistics,platform
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from lumen.media import ffmpeg_binary,probe
from lumen.model import Project,Clip
from lumen.clip_motion import ClipMotionKeyframe as K
from lumen.clip_composite import ClipComposite
from lumen.clip_transform import ClipTransform
from lumen.render import RenderEngine
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--worker',type=int,nargs=5)
args=parser.parse_args()
OUT=args.output.resolve()
if not args.worker:OUT.mkdir(parents=True,exist_ok=False)
def sha(b):return hashlib.sha256(b).hexdigest()
def worker(w,h,high,n,rep):
 source=OUT/'synthetic.mkv'; media=probe(source)
 p=Project(width=w,height=h,fps_num=1,fps_den=1,media=[media],clips=[Clip('base',media.id,0,0,0,3),Clip('top',media.id,1,0,0,3,composite=ClipComposite(True,.5),transform=ClipTransform(rotation=17),motion_keyframes=[K(0,x=-40,scale=.4,opacity=.25),K(2,x=40,scale=.6,opacity=.75)])])
 target=OUT/f'{w}-{high}-{n}-{rep}.{ "mov" if high else "mp4"}'
 commands=[];original=subprocess.Popen
 def changed(cmd,*a,**kw):
  cmd=list(cmd)
  if '-filter_complex_threads' in cmd:
   cmd[cmd.index('-filter_complex_threads')+1]=str(n)
   commands.append(dict(threads=n,probe='-v' in cmd,cpuflags=cmd[cmd.index('-cpuflags')+1] if '-cpuflags' in cmd else None))
  return original(cmd,*a,**kw)
 subprocess.Popen=changed
 engine=RenderEngine(timeout=240);before=resource.getrusage(resource.RUSAGE_CHILDREN);load=os.getloadavg();start=time.perf_counter()
 try:engine.render(p,target,export_profile='prores_422_hq_10bit' if high else 'h264_8bit')
 finally:engine.close();subprocess.Popen=original
 elapsed=time.perf_counter()-start;after=resource.getrusage(resource.RUSAGE_CHILDREN)
 base=[ffmpeg_binary(),'-v','error','-i',str(target)]
 raw=subprocess.run(base+['-map','0:v','-c:v','rawvideo','-threads','1','-fps_mode','passthrough','-pix_fmt','yuv422p10le' if high else 'yuv420p','-f','rawvideo','pipe:1'],capture_output=True,check=True).stdout
 assert len(raw)==3*w*h*(4 if high else 1.5)
 audio=subprocess.run(base+['-map','0:a','-f','f32le','pipe:1'],capture_output=True,check=True).stdout
 row=dict(width=w,height=h,high=high,threads=n,repeat=rep,seconds=elapsed,child_user_cpu=after.ru_utime-before.ru_utime,child_system_cpu=after.ru_stime-before.ru_stime,peak_child_rss_kib=after.ru_maxrss/1024 if sys.platform=='darwin' else after.ru_maxrss,load_before=load,load_after=os.getloadavg(),decoded_sha256=sha(raw),audio_pcm_sha256=sha(audio),video_bytes=len(raw),audio_bytes=len(audio),encoded_sha256=sha(target.read_bytes()),commands=commands)
 (OUT/f'{w}-{high}-{n}-{rep}.json').write_text(json.dumps(row,indent=2));target.unlink();print(json.dumps(row),flush=True)
if args.worker:worker(*args.worker);sys.exit()
metadata=dict(platform=platform.platform(),cpu_count=os.cpu_count(),cpu_quota='not measured; read host/container quota separately',ffmpeg=subprocess.check_output([ffmpeg_binary(),'-version'],text=True).splitlines()[0],source_sha256={name:sha((ROOT/name).read_bytes()) for name in ('lumen/render.py','lumen/clip_composite.py','lumen/clip_motion_render.py')},script_sha256=sha(Path(__file__).read_bytes()),fixture='64x48 FFV1 synthetic source, scaled to project canvas; three frames at1fps')
(OUT/'metadata.json').write_text(json.dumps(metadata,indent=2))
source=OUT/'synthetic.mkv'
if not source.exists():subprocess.run([ffmpeg_binary(),'-v','error','-f','lavfi','-i','testsrc2=size=64x48:rate=1:duration=3','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=3','-c:v','ffv1','-c:a','pcm_s16le',str(source)],check=True)
rows=[]
for w,h in ((1920,1080),(3840,2160)):
 for high in (0,1):
  for rep in range(3):
   for n in ((1,2,4) if rep%2==0 else (4,2,1)):
    r=subprocess.run([sys.executable,__file__,'--output',str(OUT),'--worker',str(w),str(h),str(high),str(n),str(rep)],capture_output=True,text=True,timeout=300)
    print(r.stdout,flush=True)
    if r.returncode:print(r.stderr,flush=True);raise RuntimeError(r.returncode)
    rows.append(json.loads(r.stdout));(OUT/'results.json').write_text(json.dumps(rows,indent=2))
for width,high in {(row['width'],row['high']) for row in rows}:
 group=[row for row in rows if (row['width'],row['high'])==(width,high)]
 assert len(group)==9
 for key in ('decoded_sha256','audio_pcm_sha256','encoded_sha256'):
  assert len({row[key] for row in group})==1, (width,high,key,'cross-thread mismatch')
print('DONE',flush=True)

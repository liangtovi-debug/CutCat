"""Reproducible 30-minute 29.97 fps synthetic synchronization reference.

Creates temporary synthetic footage only; requires FFmpeg and NumPy.
This is a bounded reference test, not general production-media certification.
"""
import os,sys,subprocess,time,json,tempfile,math
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lumen.media import probe,ffmpeg_binary,ffprobe_binary
from lumen.model import Project,Clip
from lumen.render import RenderEngine
import numpy as np
root=Path(tempfile.mkdtemp(prefix='lumen-long-reference-'))
start=time.monotonic()
source=root/'reference.mp4'
subprocess.run([ffmpeg_binary(),'-v','error','-f','lavfi','-i','color=gray:s=32x24:r=30000/1001:d=1800','-f','lavfi','-i','aevalsrc=if(between(t\\,1799.5\\,1799.7)\\,0.5*sin(2*PI*440*t)\\,0):s=48000:d=1800','-c:v','libx264','-preset','ultrafast','-c:a','aac',str(source)],check=True)
m=probe(source); frames=53946
p=Project(width=32,height=24,fps_num=30000,fps_den=1001,media=[m],clips=[Clip('long',m.id,0,0,0,frames)])
e=RenderEngine();out=e.render(p,root/'render.mp4');e.close()
def onset(path):
 b=subprocess.run([ffmpeg_binary(),'-v','error','-ss','1798','-i',str(path),'-vn','-ar','48000','-ac','1','-f','f32le','pipe:1'],capture_output=True,check=True).stdout
 a=np.frombuffer(b,dtype='<f4');indices=np.where(np.abs(a)>.05)[0];return float(indices[0])/48000
info=json.loads(subprocess.run([ffprobe_binary(),'-v','error','-select_streams','v:0','-count_frames','-show_entries','stream=nb_read_frames,duration,r_frame_rate','-of','json',str(out)],capture_output=True,check=True).stdout)['streams'][0]
a,b=onset(source),onset(out)
result={'reference_duration_seconds':1800,'fps':'30000/1001','expected_frames':frames,'output':info,'source_last_tone_onset_relative_to_1798':a,'output_last_tone_onset_relative_to_1798':b,'measured_end_audio_offset_seconds':b-a,'within_one_output_frame':abs(b-a)<1001/30000,'elapsed_seconds':round(time.monotonic()-start,2),'fixture_directory':str(root)}
(Path(__file__).resolve().parents[1] / 'artifacts').mkdir(exist_ok=True)
(Path(__file__).resolve().parents[1] / 'artifacts' / 'long-reference-result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
assert int(info['nb_read_frames'])==frames
assert abs(b-a)<1001/30000

"""Small reproducible CPU-only transition timing receipt, not a realtime claim."""
from pathlib import Path
import argparse,json,subprocess,sys,tempfile,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lumen.media import ffmpeg_binary,probe
from lumen.model import Project,Clip
from lumen.video_transition import VideoTransition
from lumen.video_transition_render import prepare_video_transitions
from lumen.render import RenderEngine


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    results=[]
    with tempfile.TemporaryDirectory(prefix='cutcat-transition-bench-') as directory:
        root=Path(directory)
        for w,h in ((640,360),(1920,1080)):
            media=[]
            for side,color in enumerate(('0xc23040','0x2070c0')):
                path=root/f'{w}-{side}.mkv'
                subprocess.run([ffmpeg_binary(),'-v','error','-nostdin','-y','-f','lavfi','-i',
                    f'color=c={color}:s={w}x{h}:r=30','-frames:v','24','-c:v','ffv1','-threads','1',str(path)],check=True)
                media.append(probe(path))
            project=Project(width=w,height=h,media=media,clips=[Clip('l',media[0].id,0,0,2,10),Clip('r',media[1].id,0,10,8,10)],
                            video_transitions=[VideoTransition('t','l','r',5)])
            for profile in ('h264_8bit','prores_422_hq_10bit'):
                engine=RenderEngine();high=profile!='h264_8bit'
                try:
                    start=time.perf_counter();prepare_video_transitions(engine,project,high);cold=time.perf_counter()-start
                    start=time.perf_counter();prepare_video_transitions(engine,project,high);warm=time.perf_counter()-start
                    target=root/f'{w}-{profile}{".mov" if high else ".mp4"}'
                    start=time.perf_counter();engine.render(project,target,export_profile=profile);export=time.perf_counter()-start
                    results.append(dict(width=w,height=h,profile=profile,source_frames=24,transition_frames=5,timeline_frames=20,
                        cold_bridge_seconds=cold,warm_bridge_including_fresh_source_proof_seconds=warm,
                        warm_bridge_full_export_seconds=export,export_bytes=target.stat().st_size))
                finally:engine.close()
    Path(args.output).write_text(json.dumps(dict(scope='CPU offline 5-frame transition and20-frame export on two synthetic CFR clips; not realtime, not4K/native-platform benchmark',results=results),indent=2))

if __name__=='__main__':main()

"""Generate a 16-bit SDR chart and a genuine editable float-color demonstration."""
from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from lumen.media import probe, ffmpeg_binary
from lumen.model import Project, Clip, Grade
from lumen.render import RenderEngine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--render', action='store_true')
    args = parser.parse_args()
    folder = args.directory.resolve(); folder.mkdir(parents=True, exist_ok=False)
    w,h = 960,540
    rgb = np.full((h,w,3), .055, dtype=np.float32)
    x = np.linspace(0,1,w-80,dtype=np.float32)
    rgb[35:155,40:-40] = x[None,:,None]
    for channel in range(3):
        y = 180 + channel * 60
        rgb[y:y+42,40:-40] = .015
        rgb[y:y+42,40:-40,channel] = x
    patches = [[.63,.44,.34],[.85,.64,.49],[.32,.43,.51],[.23,.35,.20], [.49,.33,.46],[.20,.58,.62],[.7,.25,.20],[.18,.25,.65]]
    for i, patch in enumerate(patches):
        left=40+i*110
        rgb[390:505,left:left+96] = patch
    frame = np.rint(rgb * 65535).astype('<u2').tobytes()
    source = folder/'precision-chart.mkv'
    cmd=[ffmpeg_binary(),'-v','error','-nostdin','-n','-f','rawvideo','-pix_fmt','rgb48le','-s',f'{w}x{h}','-r','30','-i','pipe:0','-c:v','ffv1','-pix_fmt','gbrp16le','-color_range','pc','-colorspace','rgb','-color_primaries','bt709','-color_trc','bt709',str(source)]
    process=subprocess.Popen(cmd,stdin=subprocess.PIPE)
    try:
        for _ in range(120): process.stdin.write(frame)
        process.stdin.close()
        if process.wait(): raise RuntimeError('Chart encoding failed')
    finally:
        if process.poll() is None: process.kill();process.wait()
    media=probe(source)
    grade=Grade(linear_exposure=.4,lift=[.005,0,-.005],gamma=[1.03,1, .98],gain=[1.03,1, .98],master_curve=[[0,0],[.2,.17],[.7,.76],[1,1]],blue_curve=[[0,0],[.3,.33],[1,1]])
    project=Project(name='Float color · 16-bit SDR source',width=w,height=h,media=[media],clips=[Clip('color-chart',media.id,0,0,0,120,grade=grade)])
    project.save(folder/'float-color.lumen')
    if args.render:
        engine=RenderEngine()
        try:
            engine.render(project,folder/'graded.mp4')
            project.clips[0].grade.enabled=False
            engine.render(project,folder/'before.mp4')
        finally: engine.close()
    print(folder/'float-color.lumen')


if __name__=='__main__':main()

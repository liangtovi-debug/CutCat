"""Create a genuine offline sticker/keyframe/effect demo from original assets.

The target directory must not exist. No assets or models are downloaded.
"""
from pathlib import Path
from dataclasses import replace
import argparse,json,sys,subprocess
from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage,QPainter,QColor,QLinearGradient,QPolygonF
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lumen.model import Project,Editor,Overlay,OverlayKeyframe,ClipEffect
from lumen.media import probe,ffmpeg_binary
from lumen.sticker_assets import load_sticker
from lumen.render import RenderEngine

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--static',type=Path,required=True)
    parser.add_argument('--animated',type=Path,required=True)
    args=parser.parse_args();folder=args.directory.resolve();folder.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parents[1]
    p=Project.load(root/'artifacts/tts-demo/project.lumen');p.name='Original offline stickers and local narration'
    # Original procedural scene makes movement/alpha readable without external art.
    image=QImage(640,360,QImage.Format.Format_RGB32);painter=QPainter(image)
    gradient=QLinearGradient(0,0,0,360);gradient.setColorAt(0,QColor('#203b5f'));gradient.setColorAt(1,QColor('#d9c3a3'))
    painter.fillRect(image.rect(),gradient);painter.setPen(QColor('#efc986'));painter.setBrush(QColor('#efc986'));painter.drawEllipse(QPointF(470,88),35,35)
    for color,points in [('#506b7d',[(0,230),(120,105),(260,245),(400,150),(640,260),(640,360),(0,360)]),('#274b65',[(0,300),(170,210),(320,295),(490,175),(640,255),(640,360),(0,360)])]:
        painter.setPen(QColor(color));painter.setBrush(QColor(color));painter.drawPolygon(QPolygonF([QPointF(*xy) for xy in points]))
    painter.end();image.save(str(folder/'original-scene.png'))
    source=folder/'original-scene.mp4'
    subprocess.run([ffmpeg_binary(),'-v','error','-nostdin','-n','-loop','1','-framerate','30','-i',str(folder/'original-scene.png'),'-t',str(p.duration_frames/float(p.fps)),'-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True)
    clip=next(c for c in p.clips if c.track==0)
    p.media=[replace(probe(source),id=m.id) if m.id==clip.media_id else m for m in p.media]
    ed=Editor(p)
    ed.add_effect(clip.id,ClipEffect('gentle-blur','blur',.6))
    ed.add_effect(clip.id,ClipEffect('edge-vignette','vignette',.45))
    for index,path in enumerate((args.static,args.animated)):
        asset=load_sticker(path)
        keys=[OverlayKeyframe(0,25+index*150,25,.6,.0),OverlayKeyframe(20,60+index*170,35,.8,1),OverlayKeyframe(150,190+index*130,80,.6,.9),OverlayKeyframe(179,230+index*100,90,.5,0)]
        overlay=Overlay(f'sticker-{index}',str(path.resolve()),asset.digest,15+index*30,180,index,True,keys)
        ed.add_overlay(overlay)
    ed.project.save(folder/'project.lumen')
    engine=RenderEngine()
    try:engine.render(ed.project,folder/'effects-and-stickers.mp4')
    finally:engine.close()
    (folder/'README.md').write_text('Actual original static and animated PNG stickers, linearly keyframed position/scale/opacity, ordered real blur/vignette, bilingual local TTS and captions. Shared graph export. Original assets ship with their procedural source. No commercial asset pack or real-time playback claim.\n')
    print(folder/'project.lumen')
if __name__=='__main__':main()

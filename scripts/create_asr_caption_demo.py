"""Real local recognition -> editable captions -> SRT -> burned-in export.

Explicit runtime/model inputs only. Never downloads files. Uses generated test
speech fixtures from artifacts/asr-validation, not user media.
"""
from pathlib import Path
import argparse,json,sys,subprocess,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lumen.asr import ASRConfig,transcribe
from lumen.media import probe,ffmpeg_binary
from lumen.model import Project,Editor,CaptionStyle
from lumen.subtitles import write_srt
from lumen.ui_captions import segments_to_captions
from lumen.render import RenderEngine
from dataclasses import asdict


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',required=True);parser.add_argument('--model',required=True)
    parser.add_argument('--language',choices=['en','zh'],default='en')
    parser.add_argument('directory',type=Path);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];folder=args.directory.resolve();folder.mkdir(parents=True,exist_ok=False)
    fixture=root/'artifacts/asr-validation'/f'synthetic-{args.language}.wav'
    source=folder/'speech-source.mp4'
    subprocess.run([ffmpeg_binary(),'-v','error','-nostdin','-n','-f','lavfi','-i','testsrc2=s=640x360:r=30', '-protocol_whitelist','file,pipe','-i',str(fixture),'-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(source)],check=True)
    media=probe(source);ed=Editor(Project(name='Actual local speech recognition',width=640,height=360))
    ed.add_media(media);clip=ed.add_clip(media.id,0)
    started=time.monotonic();segments=transcribe(source,ASRConfig(args.runtime,args.model,args.language,4))
    # Match clip bounds and split long Latin text explicitly; no automatic
    # wrapping is implied by the renderer. Preserve original raw results too.
    cues=segments_to_captions(segments,ed.project.fps,duration=clip.duration)
    for cue in cues:
        if args.language=='en':
            words=cue.text.split();lines=[];line=''
            for word in words:
                if len(line)+len(word)+1>42:lines.append(line);line=word
                else:line=(line+' '+word).strip()
            if line:lines.append(line)
            cue.text='\n'.join(lines)
    ed.import_captions(cues)
    ed.update_caption_style(font_family='Noto Sans CJK SC' if args.language=='zh' else 'DejaVu Sans',font_size=24,margin_bottom=24)
    ed.project.save(folder/'recognized.lumen');write_srt(folder/'recognized.srt',ed.project.captions,ed.project.fps)
    engine=RenderEngine()
    try:engine.render(ed.project,folder/'recognized-captioned.mp4')
    finally:engine.close()
    (folder/'actual-recognition.json').write_text(json.dumps({'language':args.language,'segments':[asdict(s) for s in segments],'elapsed_including_render':time.monotonic()-started,'source':'generated test pattern + synthetic test speech'},ensure_ascii=False,indent=2))
    print(folder/'recognized-captioned.mp4')

if __name__=='__main__':main()

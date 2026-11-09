"""Build an actual locally synthesized voice/caption project from saved validation takes.

No downloads or synthesis APIs. Run verify_local_tts first to generate takes.
"""
from pathlib import Path
import json,sys,subprocess,math
from dataclasses import replace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lumen.model import Project,Editor,Caption,SpeechProvenance
from lumen.media import probe,ffmpeg_binary
from lumen.render import RenderEngine
from lumen.subtitles import write_srt

def main():
    root=Path(__file__).resolve().parents[1]
    folder=root/'artifacts/tts-demo';folder.mkdir(exist_ok=False)
    records=json.loads((root/'artifacts/tts-validation/synthesis-results.json').read_text())
    ed=Editor(Project(name='Local bilingual subtitle narration',width=640,height=360))
    cursor=0
    for language,record in zip(('en','zh'),records):
        wav=root/'artifacts/tts-validation'/f'{language}.wav'
        # The generated audio is immutable. Caption wrapping is an independent edit.
        original=record['provenance']['text']
        text=original.replace('. ','.\n').replace('。','。\n').strip()
        duration=math.ceil(record['duration']*30)
        cue=Caption(f'caption-{language}',cursor,duration,text)
        ed.import_captions([cue])
        provenance=SpeechProvenance(**record['provenance'],source_caption_id=cue.id,source_start=cursor,source_duration=duration)
        media=replace(probe(wav),speech_provenance=provenance)
        ed.add_speech_clip(media,cue.id,2)
        cursor+=duration+15
    ed.update_caption_style(font_family='Noto Sans CJK SC',font_size=23,margin_bottom=28)
    source=folder/'pattern.mp4'
    subprocess.run([ffmpeg_binary(),'-v','error','-nostdin','-n','-f','lavfi','-i',f'testsrc2=s=640x360:r=30:d={cursor/30}', '-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True)
    media=probe(source);ed.add_media(media);ed.add_clip(media.id,0)
    ed.project.save(folder/'project.lumen')
    write_srt(folder/'captions.srt',ed.project.captions,ed.project.fps)
    engine=RenderEngine()
    try:engine.render(ed.project,folder/'local-bilingual-narration.mp4')
    finally:engine.close()
    (folder/'README.md').write_text('Actual local Kokoro English and Chinese speech, editable independent audio clips and captions, rendered by the shared graph. All source text is synthetic test content. See ../tts-validation for exact inference provenance and quality limitations. Font: installed Noto Sans CJK SC (not bundled).\n')
    print(folder/'project.lumen')
if __name__=='__main__':main()

"""Run real local English/Chinese synthesis with an explicit existing model pack.

No downloads. Creates a new output directory containing WAVs and provenance.
Install the project's optional [tts] dependencies explicitly first.
"""
import argparse,json,sys,time
from pathlib import Path
from dataclasses import asdict
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lumen.tts import TTSConfig,synthesize

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-pack',required=True,type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    results=[]
    for language,voice,text in [('en',0,'Welcome to the local video editor. This narration was generated entirely on this computer.'),('zh',45,'欢迎使用本地视频编辑器。这段中文语音完全在这台电脑上生成。')]:
        start=time.monotonic()
        result=synthesize(text,TTSConfig(str(args.model_pack.resolve()),voice_id=voice),args.output/f'{language}.wav')
        record=asdict(result);record['elapsed_seconds']=time.monotonic()-start;results.append(record)
    (args.output/'synthesis-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(args.output/'synthesis-results.json')
if __name__=='__main__':main()

"""Actual local inference + reviewed synthetic captions + export. No downloads.

This script approves only exact expected corrections to its own synthetic test
fixtures. The desktop app always requires a human review/apply action.
"""
from pathlib import Path
from dataclasses import asdict
import argparse,copy,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lumen.model import Project,Editor,Caption
from lumen.subtitle_ai import AssistantConfig,suggest_caption
from lumen.semantic_search import SearchConfig,SemanticIndex
from lumen.render import RenderEngine

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',required=True);parser.add_argument('--gguf',required=True)
    parser.add_argument('--embedding',required=True);parser.add_argument('--tokenizer',required=True)
    parser.add_argument('directory',type=Path);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];folder=args.directory.resolve();folder.mkdir(parents=True,exist_ok=False)
    project=Project.load(root/'artifacts/effects-demo/project.lumen')
    project.name='Local subtitle assistant and semantic search'
    project.clips=[c for c in project.clips if c.track<2]
    project.media=[m for m in project.media if m.id in {c.media_id for c in project.clips}]
    project.captions=[Caption('camera',0,75,'teh camera is ready','en'),Caption('weather',75,75,'今天天汽很好，我们去公园吧','zh'),Caption('coffee',150,75,'The quiet café serves fresh coffee.','en'),Caption('train',225,75,'下一班火车十分钟后离开。','zh'),Caption('audio',300,75,'Reduce background noise before export.','en')]
    project.caption_style.font_size=24;project.validate();project.save(folder/'before.lumen')
    editor=Editor(project);records=[]
    for cid,expected in [('camera','The camera is ready'),('weather','今天天气很好，我们去公园吧')]:
        old=copy.deepcopy(next(c for c in editor.project.captions if c.id==cid))
        result=suggest_caption(old.text,AssistantConfig(args.runtime,args.gguf))
        if result.suggested_text!=expected:raise RuntimeError('Demo suggestion needs manual review: '+repr(result.suggested_text))
        editor.update_caption(cid,text=result.suggested_text)
        current=next(c for c in editor.project.captions if c.id==cid)
        assert (current.id,current.start,current.duration)==(old.id,old.start,old.duration)
        records.append({'caption_id':cid,'start':old.start,'duration':old.duration,'suggestion':asdict(result),'demo_approval':'Exact synthetic expected correction; no real user subtitle changed.'})
    editor.project.save(folder/'reviewed.lumen')
    config=SearchConfig(args.embedding,args.tokenizer);index=SemanticIndex.build(editor.project.captions,config)
    searches=[]
    for query,expected in [('喝咖啡的地方','coffee'),('train departure','train')]:
        results=index.search(query)
        assert results[0].id==expected
        searches.append({'query':query,'results':[asdict(r) for r in results]})
    (folder/'actual-ai-results.json').write_text(json.dumps({'suggestions':records,'searches':searches,'search_provenance':asdict(index.provenance)},ensure_ascii=False,indent=2))
    engine=RenderEngine()
    try:engine.render(editor.project,folder/'reviewed-caption-export.mp4')
    finally:engine.close()
    (folder/'README.md').write_text('Actual local Qwen proofreading and MiniLM retrieval on synthetic captions. Exact expected-fixture corrections were approved only by this demo script. The app requires explicit review/apply. Caption IDs/times remain fixed. Source scene/stickers are original; no media/text was uploaded. Optional runtime/model files are external and not bundled.\n')
    print(folder/'reviewed.lumen')
if __name__=='__main__':main()

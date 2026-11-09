"""Capture actual asynchronous local AI controls, results and verified voices.

Uses only synthetic demo captions and explicitly supplied existing test packs.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import argparse,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from lumen.model import Project,Editor
from lumen.ui import MainWindow
from lumen.local_ai import ModelRegistry

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('runtime','gguf','embedding','tokenizer','voice-pack'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1];folder=root/'artifacts/local-ai-demo'
    app=QApplication.instance() or QApplication([])
    w=MainWindow();w.model_registry=ModelRegistry(folder/'test-local-models.json');w.editor=Editor(Project.load(folder/'before.lumen'))
    w.project_path=folder/'before.lumen';w.resize(1760,1440);w.refresh();w.show();app.processEvents();w.main_split.setSizes([190,1000,540])
    def wait_jobs():
        deadline=time.monotonic()+180
        while w.jobs and time.monotonic()<deadline:app.processEvents();time.sleep(.02)
        app.processEvents()
        if w.jobs:raise RuntimeError('Actual local UI job did not finish')
    def preview(path,position=1200):
        w.activate_preview(path,w.revision,'graded');w.player.play();w.player.setPosition(position)
        deadline=time.monotonic()+15
        while (w.video.image.isNull() or w.player.position()<position) and time.monotonic()<deadline:app.processEvents();time.sleep(.02)
        if w.video.image.isNull():raise RuntimeError('Real Qt preview did not decode')
        w.player.pause();w.scopes.set_image(w.video.image);app.processEvents()
    preview(folder/'before-caption-preview.mp4')
    w.side_tabs.setCurrentWidget(w.ai_panel)
    for kind,files in [('subtitle-assistant',{'runtime':args.runtime,'model':args.gguf}),('semantic-search',{'model':args.embedding,'tokenizer':args.tokenizer})]:
        for role,value in files.items():w.ai_fields[kind][role].setText(value)
        w.import_ai_model(kind);wait_jobs();assert kind in w.ai_records
    w.ai_caption_choice.setCurrentIndex(w.ai_caption_choice.findData('camera'));w.suggest_subtitle();wait_jobs()
    assert w.ai_pending is not None and w.ai_proposed.toPlainText()=='The camera is ready'
    scroll=w.ai_panel;scroll.ensureWidgetVisible(w.ai_apply_button);app.processEvents()
    assert w.grab().save(str(folder/'editor-subtitle-assistant.png'))
    # Load the separately validated reviewed state for retrieval/viewer consistency.
    w.editor=Editor(Project.load(folder/'reviewed.lumen'));w.project_path=folder/'reviewed.lumen';w.changed()
    preview(folder/'reviewed-caption-export.mp4',5200);w.side_tabs.setCurrentWidget(w.ai_panel)
    w.build_semantic_index();wait_jobs();assert w.search_index is not None
    w.ai_query.setText('喝咖啡的地方');w.search_subtitles();wait_jobs()
    assert w.ai_results.count()>0 and w.ai_results.item(0).data(256).id=='coffee'
    w.ai_results.setCurrentRow(0);w.jump_to_search_result(w.ai_results.item(0));assert w.timeline.playhead==150
    w.side_tabs.setCurrentWidget(w.ai_panel);scroll.ensureWidgetVisible(w.ai_results);scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum());app.processEvents()
    assert w.grab().save(str(folder/'editor-semantic-search.png'))
    w.side_tabs.setCurrentIndex(1);w.tts_pack.setCurrentIndex(w.tts_pack.findData('v1.1'));w.tts_model.setText(args.voice_pack)
    w.verify_tts_pack();wait_jobs();assert w._tts_verified_pack is not None
    w.tts_voice.setValue(58);w.tts_preview_text.setPlainText('你好，这是完全在本地生成的中文语音试听。')
    # This produces a real WAV. Audible hardware output is not claimed.
    w.preview_tts_voice();wait_jobs();assert w.tts_preview_path is not None and Path(w.tts_preview_path).is_file()
    w.stop_tts_preview();scroll=w.side_tabs.widget(1);scroll.ensureWidgetVisible(w.tts_voice);app.processEvents()
    assert w.grab().save(str(folder/'editor-voice-browser.png'))
    (folder/'ui-checks.json').write_text(json.dumps({'real_async_model_registration':True,'real_qwen_suggestion':True,'real_minilm_query_top_id':'coffee','actual_jump_frame':150,'real_v1_1_voice_audition_id':58,'physical_audio_verified':False},indent=2))
    w.dirty=False;w.close();app.processEvents();print('Actual AI UI screenshots saved')
if __name__=='__main__':main()

"""Render synthetic media and capture the real SDR color UI; no user media/network."""
from __future__ import annotations
import argparse
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import subprocess
import sys
import time
import json
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from lumen.i18n import set_locale
from lumen.media import ffmpeg_binary, probe
from lumen.model import Project, Editor, Clip, Grade, Caption, Overlay
from lumen.render import RenderEngine
from lumen.sticker_assets import load_sticker
from lumen.ui import MainWindow
from lumen.ui_color_export import ExportProfileDialog


def capture(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    runtime = output / 'runtime'; runtime.mkdir(exist_ok=True)
    source = runtime / 'synthetic-rgb16.mkv'
    width, height = 960, 540
    rgb = np.zeros((height, width, 3), dtype='<u2')
    bars = [(1,1,1), (1,1,0), (0,1,1), (0,1,0), (1,0,1), (1,0,0), (0,0,1), (0,0,0)]
    for i, bar in enumerate(bars):
        rgb[:height//2, i*width//8:(i+1)*width//8] = np.array(bar) * .75 * 65535
    ramp = np.linspace(0,65535,width).astype('<u2')
    rgb[height//2:] = ramp[None,:,None]
    tags=['-color_range','pc','-color_primaries','bt709','-color_trc','bt709','-colorspace','rgb']
    subprocess.run([ffmpeg_binary(),'-v','error','-nostdin','-y','-f','rawvideo','-pix_fmt','rgb48le',
                    '-s',f'{width}x{height}','-r','30',*tags,'-i','pipe:0','-frames:v','3',
                    '-c:v','ffv1','-level','3','-pix_fmt','gbrp16le',*tags,str(source)],
                   input=rgb.tobytes()*3,check=True,capture_output=True)
    media=probe(source)
    project=Project(width=width,height=height,media=[media],clips=[Clip('picture',media.id,0,0,0,3,grade=Grade())])
    preview=runtime/'preview.mp4'; master=runtime/'master.mov'
    for path in (preview,master): path.unlink(missing_ok=True)
    engine=RenderEngine(timeout=120)
    try:
        engine.render(project,preview,preview=True)
        engine.render(project,master,export_profile='prores_422_hq_10bit')
    finally: engine.close()
    app=QApplication.instance() or QApplication([])
    settings=QSettings(str(runtime/'settings.ini'),QSettings.Format.IniFormat)
    w=MainWindow(workspace_settings=settings,recovery_root=runtime/'recovery',ui_language='zh-CN')
    w.editor=Editor(project); w.selection='picture'; w.set_workspace_mode('professional',persist=False)
    w.resize(1600,1120);w.refresh();w.show();app.processEvents();w.main_split.setSizes([210,970,420])
    w.activate_preview(preview,w.revision,'graded');w.player.play()
    end=time.monotonic()+10
    while (w.video.image.isNull() or w.scopes.data is None) and time.monotonic()<end:
        app.processEvents();time.sleep(.02)
    if w.video.image.isNull() or w.scopes.data is None:
        raise RuntimeError('Actual Qt preview decode did not reach the video and scopes')
    w.player.pause();w.copy_selected_grade();app.processEvents()
    captures=[]; canvas_heights={}
    profile_project=Project.from_dict(project.to_dict())
    static_png=Path(__file__).resolve().parents[1]/'lumen/assets/stickers/spark_star.png'
    profile_project.overlays=[Overlay('static-png',str(static_png),load_sticker(static_png).digest,0,1)]
    profile_project.captions=[Caption('demo-caption',0,1,'合成字幕示例')]
    for locale in ('zh-CN','en'):
        set_locale(locale);app.processEvents()
        for mode in ('parade','vectorscope'):
            w.scopes.set_mode(mode)
            for _ in range(12):
                app.processEvents();time.sleep(.01)
            assert w.scopes.canvas.height() >= 48, 'Real MainWindow scope canvas collapsed'
            assert w.scopes.canvas.visibleRegion().boundingRect().height() == w.scopes.canvas.height(), 'Scope canvas is clipped'
            canvas_heights[f'{locale}-{mode}'] = w.scopes.canvas.height()
            name=f'{locale}-{mode}.png';assert w.grab().save(str(output/name));captures.append(name)
        dialog=ExportProfileDialog(profile_project,w);dialog.profile_choice.setCurrentIndex(1);dialog.show();app.processEvents()
        name=f'{locale}-highbit-export.png';assert dialog.grab().save(str(output/name));captures.append(name)
        dialog.reject();dialog.deleteLater();app.processEvents()
    set_locale('zh-CN')
    blocked=Project.from_dict(profile_project.to_dict())
    manifest=Path(__file__).resolve().parents[1]/'lumen/assets/stickers/pulse_ring/sticker.json'
    blocked.overlays=[Overlay('animated-pack',str(manifest),load_sticker(manifest).digest,0,1)]
    dialog=ExportProfileDialog(blocked,w);dialog.profile_choice.setCurrentIndex(1);dialog.show();app.processEvents();dialog.accept();app.processEvents()
    assert dialog.selected_profile is None
    name='zh-CN-highbit-blocked.png';assert dialog.grab().save(str(output/name));captures.append(name)
    dialog.reject();dialog.deleteLater();w.dirty=False;w.close();app.processEvents()
    evidence={'captures':captures,'scope_canvas_heights':canvas_heights,'source':'locally generated RGB16 color bars and ramp',
              'preview':'actual H.264 Qt decode','master':'actual verified ProRes HQ 10-bit MOV',
              'not_native_platform_validation':True}
    (output/'capture.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(evidence,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    capture(parser.parse_args().output.resolve())

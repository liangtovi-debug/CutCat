"""Create actual mode-switch evidence and an explicit sRGB normalization demo.
No downloads, AI models or user media. Uses the bundled original scene/assets.
"""
from pathlib import Path
import copy, hashlib, json, os, subprocess, sys, time
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings
from lumen.model import Project, Editor, Clip, Grade
from lumen.media import probe, ffmpeg_binary
from lumen.color import rec709_to_linear, linear_to_srgb, apply_grade
from lumen.render import RenderEngine
from lumen.ui import MainWindow


def decoded_digest(path):
    data = subprocess.check_output([ffmpeg_binary(), '-v', 'error', '-i', str(path), '-map', '0:v:0', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])
    return hashlib.sha256(data).hexdigest()


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root/'artifacts/workspace-color-milestone'
    folder.mkdir(parents=True, exist_ok=False)
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(folder/'workspace-test.ini'), QSettings.Format.IniFormat)
    w = MainWindow(workspace_settings=settings)
    project = Project.load(root/'artifacts/effects-demo/project.lumen')
    project.name = 'Same project · Simple and Professional'
    picture = next(c for c in project.clips if next(m for m in project.media if m.id == c.media_id).has_video)
    picture.grade.lift = [.013, -.005, .002]
    picture.grade.red_curve = [[0., 0.], [.3, .35], [1., 1.]]
    project.save(folder/'shared-project.lumen')
    w.editor = Editor(project); w.project_path = folder/'shared-project.lumen'
    w.refresh(); w.select_clip(picture.id)
    w.resize(1760, 1440); w.show(); app.processEvents()
    w.main_split.setSizes([190, 1000, 540])
    state = copy.deepcopy(w.project); revision = w.revision
    engine = RenderEngine()
    records = {}
    try:
        for mode in ('simple', 'professional'):
            w.set_workspace_mode(mode)
            assert w.project == state and w.revision == revision
            target = folder/f'{mode}.mp4'
            engine.render(w.project, target)
            records[mode] = decoded_digest(target)
            w.activate_preview(target, w.revision, 'graded'); w.player.play()
            deadline = time.monotonic()+15
            while (w.video.image.isNull() or w.player.position()<1200) and time.monotonic()<deadline:
                app.processEvents(); time.sleep(.02)
            if w.video.image.isNull(): raise RuntimeError('Actual Qt decoded preview unavailable')
            w.player.pause(); w.scopes.set_image(w.video.image)
            w.inspector_tabs.setCurrentIndex(1)
            if mode == 'professional': w.color_tabs.setCurrentIndex(1)
            app.processEvents()
            assert w.grab().save(str(folder/f'editor-{mode}.png'))
        assert records['simple'] == records['professional']
        for _ in range(10):
            w.set_workspace_mode('simple'); w.set_workspace_mode('professional')
        assert w.project == state and w.revision == revision
        # A generated 16-bit RGB sRGB source. Some FFV1 containers omit transfer tags;
        # the explicit assignment is validated against the known generated values.
        width, height = 320, 180
        chart = np.broadcast_to(np.linspace(.02, .95, width, dtype=np.float32)[None,:,None], (height,width,3)).copy()
        chart[80:130,20:100] = [.7,.25,.2]; chart[80:130,120:200] = [.2,.6,.25]
        encoded = linear_to_srgb(rec709_to_linear(chart))
        source = folder/'known-srgb-chart.mkv'
        frame = np.rint(encoded*65535).astype('<u2').tobytes()
        subprocess.run([ffmpeg_binary(),'-v','error','-nostdin','-n','-f','rawvideo','-pix_fmt','rgb48le','-s',f'{width}x{height}','-r','30','-color_range','pc','-colorspace','rgb','-color_primaries','bt709','-color_trc','iec61966-2-1','-i','pipe:0','-c:v','ffv1','-pix_fmt','gbrp16le','-color_range','pc','-colorspace','rgb','-color_primaries','bt709','-color_trc','iec61966-2-1',str(source)],input=frame*60,check=True)
        media = probe(source)
        assert media.color_transfer == 'iec61966-2-1' and media.color_primaries == 'bt709'
        normalized = Project(name='Explicit sRGB to Rec.709',width=width,height=height,media=[media],clips=[Clip('srgb',media.id,0,0,0,60,grade=Grade(input_encoding='srgb'))])
        normalized.save(folder/'srgb-normalized.lumen')
        engine.render(normalized,folder/'srgb-normalized.mp4')
        expected = apply_grade(encoded,Grade(input_encoding='srgb'))
        assert np.max(np.abs(expected-chart)) < .0004
        w.editor=Editor(normalized); w.project_path=folder/'srgb-normalized.lumen';w.changed();w.select_clip('srgb');w.set_workspace_mode('professional')
        w.activate_preview(folder/'srgb-normalized.mp4',w.revision,'graded');w.player.play()
        end=time.monotonic()+10
        while w.player.position()<200 and time.monotonic()<end:app.processEvents();time.sleep(.02)
        w.player.pause();w.inspector_tabs.setCurrentIndex(1);w.color_tabs.setCurrentIndex(0);app.processEvents()
        assert w.grab().save(str(folder/'editor-input-normalization.png'))
    finally:
        engine.close(); w.dirty=False;w.close();app.processEvents()
    result={'same_project_after_22_switches':True,'same_revision':True,'decoded_video_sha256':records,'decoded_pixels_identical':True,'actual_qt_preview_decoded':True,'observed_srgb_source_tags':{'transfer':media.color_transfer,'primaries':media.color_primaries,'matrix':media.color_space,'range':media.color_range},'float_roundtrip_max_error':float(np.max(np.abs(expected-chart))),'physical_audio_verified':False,'native_platforms_verified':False}
    result['output_tags'] = {}
    for name in ('simple.mp4', 'professional.mp4', 'srgb-normalized.mp4'):
        tags = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=color_transfer,color_primaries,color_space,color_range', '-of', 'json', str(folder/name)]))['streams'][0]
        assert tags == {'color_range':'tv','color_space':'bt709','color_transfer':'bt709','color_primaries':'bt709'}
        result['output_tags'][name] = tags
    (folder/'validation.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()

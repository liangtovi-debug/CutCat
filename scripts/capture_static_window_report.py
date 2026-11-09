"""User-facing real RGB16 window examples, not composited/mock UI."""
from copy import deepcopy
from dataclasses import asdict
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from lumen.media import ffmpeg_binary, probe
from lumen.model import Clip, Editor, Project
from lumen.ui import MainWindow
from lumen.ui_qualifier import open_qualifier
from capture_selective_color import pump
from capture_qualifier_overlay import wait_frame, geometry_checks
from qt_test_cleanup import cleanup_test_windows


def capture(output):
    output.mkdir(parents=True, exist_ok=True)
    rgb = np.empty((540, 960, 3), '<u2'); rgb[:] = [12001, 12001, 12001]
    rgb[120:420, 100:380] = [36017, 10007, 7019]
    rgb[120:420, 580:860] = [36017, 10007, 7019]
    source = output / 'same-color-rgb16.mkv'
    tags = ['-colorspace', 'rgb', '-color_range', 'pc', '-color_trc', 'bt709', '-color_primaries', 'bt709']
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-threads', '1',
        '-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-s', '960x540', '-r', '30', *tags,
        '-i', 'pipe:0', '-frames:v', '1', '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'gbrp16le', *tags, str(source)],
        input=rgb.tobytes(), check=True, capture_output=True, timeout=60)
    media = probe(source); project = Project(width=960, height=540, media=[media], clips=[Clip('picture', media.id, 0, 0, 0, 1)])
    app = QApplication.instance() or QApplication([])
    window = MainWindow(workspace_settings=QSettings(str(output/'settings.ini'), QSettings.Format.IniFormat),
                        recovery_root=output/'recovery', ui_language='zh-CN')
    try:
        window.editor=Editor(project); window.refresh(); window.select_clip('picture'); window.show(); pump(app)
        before=deepcopy(project); dialog=open_qualifier(window); wait_frame(app, dialog)
        dialog.resize(760, 900); pump(app)
        dialog.enabled_check.setChecked(True)
        for key, value in [('hue_width', 20), ('exposure', .7), ('window_center_x', 25),
            ('window_center_y', 50), ('window_width', 29), ('window_height', 55), ('window_feather', 25)]:
            dialog.controls[key].setValue(value)
        captures=[]
        for shape in ['ellipse', 'rectangle']:
            dialog.window_shape.setCurrentIndex(dialog.window_shape.findData(shape))
            for mode in ['source', 'overlay', 'adjusted', 'matte']:
                dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData(mode)); pump(app)
                # Align the group title and shape selector, showing geometry below.
                group=dialog.window_shape.parentWidget()
                top=group.mapTo(dialog.scroll.widget(), group.rect().topLeft()).y()
                dialog.scroll.verticalScrollBar().setValue(max(0, top-8)); pump(app)
                geometry_checks(dialog, dialog.window_shape)
                assert dialog.window_invert.visibleRegion().boundingRect() == dialog.window_invert.rect()
                assert dialog.controls['window_height'].visibleRegion().boundingRect() == dialog.controls['window_height'].rect()
                filename=f'zh-CN-{shape}-{mode}-same-color.png'
                assert dialog.grab().save(str(output/filename))
                captures.append(filename)
            a=dialog.viewport.grid_at(240,270); b=dialog.viewport.grid_at(720,270)
            assert dialog.matte[a[1],a[0]] == 1 and dialog.matte[b[1],b[0]] == 0
        assert window.project == before and not window.editor.can_undo
        result={'result':'passed','platform':'Linux/offscreen','size':[dialog.width(),dialog.height()],
            'source':'Local RGB16 FFV1: two identical RGB patches, no private media', 'same_color_left_selected_right_excluded':True,
            'settings':asdict(dialog.settings),'captures':captures}
        (output/'capture.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(result,ensure_ascii=False))
    finally:
        window.dirty=False; cleanup_test_windows(app)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',type=Path,required=True)
    capture(parser.parse_args().output.resolve())

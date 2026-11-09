"""Capture real offscreen Qt marker UI across locales/workspaces at one DPI.

Run once per QT_SCALE_FACTOR (1, 1.5, 2) in a fresh process. This is synthetic
native-widget evidence, not Windows/macOS hardware or screen-reader validation.
Usage: QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=1 python scripts/capture_timeline_markers_ui.py OUTPUT_DIRECTORY
"""
from pathlib import Path
import json, os, sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings, QRect
from PySide6.QtWidgets import QApplication
from lumen.ui import MainWindow
from lumen.model import Project, Editor, Clip, Media
from lumen.i18n import set_locale

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
app=QApplication([])
scale=os.environ.get('QT_SCALE_FACTOR','1')
receipts=[]
for locale in ('en','zh-CN'):
  for mode in ('simple','professional'):
    tag=f'{locale}-{mode}-dpi{scale}'
    settings=QSettings(f'/tmp/marker-capture-{tag}.ini',QSettings.Format.IniFormat)
    settings.setValue('mode',mode)
    w=MainWindow(workspace_settings=settings,ui_language=locale,recovery_root=f'/tmp/marker-capture-recovery-{tag}')
    w.editor=Editor(Project(media=[Media('m','/tmp/annotation-test-source.wav','Synthetic audio fixture',3,has_video=False,has_audio=True)],clips=[Clip('c','m',2,0,0,90)]))
    a=w.editor.add_timeline_marker('Opening <b>literal</b> / 开场',10,comment='Same-frame notes remain separate.\n同帧批注独立保存。',color='yellow')
    w.editor.add_timeline_marker('Range 片段复核',10,40,comment='Range [10, 40): end is exclusive.',color='blue')
    w.editor.add_timeline_marker('Review / 复核',45,comment='A visible separate point.',color='purple')
    w.editor.add_timeline_marker('Retained beyond shorter timeline 越界保留',80,140,comment='<a href="example">Plain text, never a link</a>',color='red')
    w.refresh();w.show();app.processEvents()
    band_paths=[]
    if scale == '1':
      w.zoom_timeline(8);w.timeline_scroll.verticalScrollBar().setValue(w.timeline_scroll.verticalScrollBar().maximum())
      for _ in range(5):app.processEvents()
      for label,widget in [('timeline',w.timeline),('timeline-bottom',w.timeline_scroll)]:
        bp=out/f'{tag}-{label}.png';widget.grab().save(str(bp));band_paths.append(bp)
    b=w.open_timeline_markers(); b.selected_id=a.id;b.refresh_records()
    # A narrow available-screen rectangle is explicitly bounded in logical pixels.
    b.fit_available_screen(QRect(0,0,360,700))
    for _ in range(5):app.processEvents()
    path=out/f'{tag}-browser.png';b.grab().save(str(path))
    browser_nohscroll=b.scroll.horizontalScrollBar().maximum()==0
    e=w.edit_timeline_marker(a.id);e.range.setChecked(True);e.end.setText('40');e.fit_available_screen(QRect(0,0,360,700))
    for _ in range(5):app.processEvents()
    path2=out/f'{tag}-editor.png';e.grab().save(str(path2))
    # Details, exact end/capture and footer remain accessible after vertical scrolling.
    e.scroll.ensureWidgetVisible(e.capture_end_button)
    for _ in range(5):app.processEvents()
    path3=out/f'{tag}-editor-bounds.png';e.grab().save(str(path3))
    b.scroll.ensureWidgetVisible(b.navigate_button)
    for _ in range(5):app.processEvents()
    path4=out/f'{tag}-browser-actions.png';b.grab().save(str(path4))
    receipts.append({'tag':tag,'device_pixel_ratio':e.devicePixelRatioF(),'browser_size':[b.width(),b.height()],'editor_size':[e.width(),e.height()],'horizontal_scroll_zero':browser_nohscroll and e.scroll.horizontalScrollBar().maximum()==0,'paths':[str(p) for p in (path,path2,path3,path4,*band_paths)]})
    w.close_timeline_markers();w.closing=True;w.dirty=False;w.close();app.processEvents();w.deleteLater();app.processEvents()
(out/f'geometry-dpi{scale}.json').write_text(json.dumps(receipts,indent=2),encoding='utf-8')

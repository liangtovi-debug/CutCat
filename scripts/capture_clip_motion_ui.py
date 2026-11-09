"""Synthetic offscreen bilingual picture-motion review. Not native UI evidence.
Run separately with QT_SCALE_FACTOR=1 / 1.5 / 2 for device-scale captures.
"""
import argparse,json,os,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from lumen.i18n import set_locale
from lumen.model import Clip,Editor,Media,Project
from lumen.clip_motion import ClipMotionKeyframe as K
from lumen.clip_composite import ClipComposite
from lumen.ui import MainWindow
from lumen.ui_clip_motion import ClipMotionDialog

parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
args.output.mkdir(parents=True,exist_ok=True)
app=QApplication.instance() or QApplication([]);app.setFont(QFont('Noto Sans CJK SC',10))
records=[]
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory);source=root/'synthetic.mp4';source.write_bytes(b'UI metadata only, not decoded media')
 for locale in ('en','zh-CN'):
  for mode in ('simple','professional'):
   set_locale(locale)
   settings=QSettings(str(root/f'{locale}-{mode}.ini'),QSettings.Format.IniFormat);settings.setValue('mode',mode)
   window=MainWindow(workspace_settings=settings);window.resize(1366,850)
   window.editor=Editor(Project(media=[Media('m',str(source),'Synthetic picture',10,640,360,True,True)],clips=[
      Clip('c','m',0,10,0,90,composite=ClipComposite(True),motion_keyframes=[K(-10,x=-40,scale=.5),K(0,x=-20,scale=.5,opacity=.25),K(60,x=60,y=10,scale=.75,opacity=1),K(100,x=80)])]))
   window.refresh();window.select_clip('c');window.show();app.processEvents()
   dialog=ClipMotionDialog(window);dialog.resize(580,740);dialog.show();app.processEvents()
   stem=f'picture-motion-{locale}-{mode}-scale{os.environ.get("QT_SCALE_FACTOR","1")}'
   paths=[]
   for label,widget in [('dialog',dialog),('window',window),('static-controls',window.transform_group)]:
    path=args.output/f'{stem}-{label}.png';assert widget.grab().save(str(path));paths.append(path.name)
   dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum());app.processEvents()
   bottom=args.output/f'{stem}-dialog-bottom.png';assert dialog.grab().save(str(bottom));paths.append(bottom.name)
   dialog.resize(320,480);app.processEvents()
   narrow=args.output/f'{stem}-dialog-narrow.png';assert dialog.grab().save(str(narrow));paths.append(narrow.name)
   assert dialog.width() <= 340, 'Narrow editor must not force a desktop-wide minimum'
   assert dialog.scroll.widget().width() <= dialog.scroll.viewport().width(), 'Narrow content must not be clipped horizontally'
   assert dialog.apply_button.isEnabled() and not window.composite_opacity.isEnabled()
   assert all(not window.transform_spins[n].isEnabled() for n in ('x','y','scale'))
   assert window.transform_spins['rotation'].isEnabled()
   records.append(dict(locale=locale,mode=mode,scale=dialog.devicePixelRatioF(),dialog_width=dialog.width(),dialog_height=dialog.height(),files=paths))
   dialog.reject();window.dirty=False;window.closing=True;window.close();app.processEvents()
(args.output/f'manifest-scale{os.environ.get("QT_SCALE_FACTOR","1")}.json').write_text(json.dumps(records,indent=2))

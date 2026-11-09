"""Capture actual offscreen Qt UI playing the rendered color demonstration."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from lumen.model import Project, Editor
from lumen.ui import MainWindow

app=QApplication.instance() or QApplication([])
root=Path(__file__).resolve().parents[1]
folder=root/'artifacts/color-milestone'
w=MainWindow(); w.editor=Editor(Project.load(folder/'float-color.lumen'))
w.project_path=folder/'float-color.lumen'; w.selection=w.project.clips[0].id
w.resize(1600,1120); w.refresh(); w.show(); app.processEvents()
w.main_split.setSizes([220,940,400])
w.activate_preview(folder/'graded.mp4',w.revision,'graded')
w.player.play()
deadline=time.monotonic()+12
while w.video.image.isNull() and time.monotonic()<deadline:
    app.processEvents(); time.sleep(.02)
if w.video.image.isNull(): raise RuntimeError('Qt did not decode an actual rendered preview frame')
w.player.pause(); w.scopes.set_image(w.video.image)
w.color_tabs.setCurrentIndex(1); w.wheel_choice.setCurrentIndex(1); app.processEvents()
assert w.grab().save(str(root/'artifacts/color-editor-wheels.png'))
w.color_tabs.setCurrentIndex(2); app.processEvents()
assert w.grab().save(str(root/'artifacts/color-editor-curves.png'))
w.preview_paths[(w.revision,'bypass')]=folder/'before.mp4'
w.preview_mode_choice.setCurrentIndex(1); w.player.play()
deadline=time.monotonic()+2
while time.monotonic()<deadline: app.processEvents(); time.sleep(.02)
w.player.pause(); w.scopes.set_image(w.video.image); app.processEvents()
assert w.grab().save(str(root/'artifacts/color-editor-before.png'))
w.dirty=False; w.close(); app.processEvents()
print('Saved three actual Qt captures from the rendered 16-bit chart project.')

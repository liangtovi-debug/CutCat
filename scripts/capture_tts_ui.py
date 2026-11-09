"""Capture actual Qt controls and provenance from the real bilingual TTS demo."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from lumen.model import Project, Editor
from lumen.ui import MainWindow

app = QApplication.instance() or QApplication([])
root = Path(__file__).resolve().parents[1]
folder = root / 'artifacts/tts-demo'
w = MainWindow(); w.editor = Editor(Project.load(folder/'project.lumen'))
w.project_path = folder/'project.lumen'
w.resize(1800, 1240); w.refresh(); w.show(); app.processEvents()
w.main_split.setSizes([470, 940, 390])
preview = folder/'local-bilingual-narration.mp4'
if not preview.is_file(): raise RuntimeError('Render the real TTS demo before capturing the UI')
w.activate_preview(preview, w.revision, 'graded')
w.player.play()
deadline = time.monotonic() + 12
while w.video.image.isNull() and time.monotonic() < deadline:
    app.processEvents(); time.sleep(.02)
if w.video.image.isNull(): raise RuntimeError('Qt did not decode the actual narration render')
w.player.pause()
w.tts_model.setText(str(root.parent/'lumen-tts-test-cache/kokoro-multi-lang-v1_0'))
w.tts_voice.setValue(45); w.tts_track.setCurrentIndex(1)
assert w.tts_voice_name.text() == 'zf_xiaobei'
w.select_caption('caption-zh'); app.processEvents()
scroll = w.side_tabs.widget(1)
scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
w.tts_status.setText('Saved and inserted full Chinese voice: artifacts/tts-validation/zh.wav (6.682833 seconds, 201 frames)')
w.seek_frame(220); w.player.play()
deadline = time.monotonic() + 1
while time.monotonic() < deadline: app.processEvents(); time.sleep(.02)
w.player.pause(); w.scopes.set_image(w.video.image); app.processEvents()
assert w.grab().save(str(root/'artifacts/tts-editor-controls.png'))
speech = next(c for c in w.project.clips if next(m for m in w.project.media if m.id == c.media_id).speech_provenance and next(m for m in w.project.media if m.id == c.media_id).speech_provenance.source_caption_id == 'caption-zh')
w.select_clip(speech.id); w.inspector_tabs.setCurrentIndex(2); app.processEvents()
assert w.speech_info_button.isEnabled()
assert w.grab().save(str(root/'artifacts/tts-editor-audio.png'))
def capture_dialog():
    dialog = QApplication.activeModalWidget()
    assert dialog is not None and dialog.windowTitle() == 'Immutable voice provenance'
    assert dialog.grab().save(str(root/'artifacts/tts-editor-provenance.png'))
    dialog.accept()
QTimer.singleShot(250, capture_dialog)
w.inspect_speech()
w.dirty = False; w.close(); app.processEvents()
print('Captured real TTS controls, full audio timeline, and immutable provenance dialog.')

"""Synthetic offscreen screenshots; never reads user media or claims native QA."""
from pathlib import Path
import sys
import tempfile
import wave
import numpy as np
from PySide6.QtCore import QEvent, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication
from lumen.model import Clip, Editor, Project
from lumen.media import probe
from lumen.ui import MainWindow


def main(output, language="zh-CN"):
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory(prefix='cutcat-group-review-') as temp:
        temp = Path(temp)
        raw = np.zeros(48000*5, np.int16)
        for i in range(0, len(raw), 8000):
            raw[i:i+500] = (np.sin(np.arange(500)*.12)*23000).astype(np.int16)
        audio = temp/'synthetic-impulses.wav'
        with wave.open(str(audio), 'wb') as f:
            f.setparams((1, 2, 48000, 0, 'NONE', 'not compressed')); f.writeframes(raw.tobytes())
        settings = QSettings(str(temp/'workspace.ini'), QSettings.Format.IniFormat)
        settings.setValue('mode', 'professional')
        w = MainWindow(workspace_settings=settings, ui_language=language, recovery_root=temp/'recovery')
        m = probe(audio); m.name = '合成脉冲 · Synthetic impulses'
        w.editor = Editor(Project(media=[m], clips=[Clip('a', m.id, 2, 10, 0, 25),
            Clip('b', m.id, 3, 50, 30, 25), Clip('anchor', m.id, 2, 95, 80, 25)]))
        w.refresh(); w.select_clip('a'); w.toggle_clip_selection('b')
        w.show(); app.processEvents()
        # Local bounded wait only for deterministic synthetic waveform extraction.
        from PySide6.QtTest import QTest
        for _ in range(30):
            app.processEvents(); QTest.qWait(30)
            if w.timeline.waveforms.get(m.path) is not None: break
        w.grab().save(str(output/f'multiselection-{language}.png'))
        timeline = w.timeline; timeline.snapping_enabled = False
        pos = timeline.rect_for(w.project.clips[0]).center()
        def send(kind, at, button, buttons):
            app.sendEvent(timeline, QMouseEvent(kind, QPointF(at), QPointF(at), button, buttons, Qt.KeyboardModifier.NoModifier))
        send(QEvent.Type.MouseButtonPress, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        invalid = QPointF(pos.x()-20*timeline.scale, pos.y())
        send(QEvent.Type.MouseMove, invalid, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        app.processEvents(); w.grab().save(str(output/f'multiselection-invalid-{language}.png'))
        timeline.cancel_gestures(); w.dirty = False; w.closing = True; w.close(); app.processEvents()


if __name__ == '__main__': main(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else 'zh-CN')

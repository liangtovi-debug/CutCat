"""Synthetic offscreen Cross Dissolve UI review, NOT native or media proof.

Run separately with QT_SCALE_FACTOR=1 and 1.5. Creates metadata-only clip and
transition fixtures directly; never invokes Apply, source validation or render.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtTest import QTest
from lumen.i18n import set_locale
from lumen.model import Clip, Editor, Media, Project
from lumen.video_transition import VideoTransition
from lumen.ui import MainWindow
from lumen.ui_video_transition import VideoTransitionDialog, transition_rect

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
app = QApplication.instance() or QApplication([])
app.setFont(QFont('Noto Sans CJK SC', 10))
records = []
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    source = root / 'synthetic.mp4'
    source.write_bytes(b'UI metadata fixture only: not decoded media')
    for locale in ('en', 'zh-CN'):
        set_locale(locale)
        settings = QSettings(str(root / f'{locale}.ini'), QSettings.Format.IniFormat)
        settings.setValue('mode', 'professional')
        window = MainWindow(workspace_settings=settings, ui_language=locale)
        transition = VideoTransition('fixture-transition', 'left', 'right', 9)
        window.editor = Editor(Project(
            media=[Media('m', str(source), 'Synthetic UI fixture', 10, 640, 360, True, False)],
            clips=[Clip('left', 'm', 0, 0, 30, 60), Clip('right', 'm', 0, 60, 120, 60)],
            video_transitions=[transition]))
        window.refresh(); window.select_video_transition(transition.id)
        window.resize(1366, 768); window.show(); app.processEvents()
        scale = os.environ.get('QT_SCALE_FACTOR', '1')
        stem = f'video-transition-{locale}-scale{scale}'
        files = []
        def capture(widget, label):
            path = args.output / f'{stem}-{label}.png'
            assert widget.grab().save(str(path))
            files.append(path.name)
        assert window.timeline_scroll.parentWidget().rect().contains(window.timeline_scroll.geometry()), 'Timeline scroll must fit its parent'
        capture(window, 'window')
        menu = window.video_transition_button.menu()
        menu.popup(window.video_transition_button.mapToGlobal(window.video_transition_button.rect().bottomLeft()))
        app.processEvents(); capture(menu, 'transition-menu'); menu.hide(); app.processEvents()
        capture(window.timeline, 'marker')
        window.resize(1000, 760); app.processEvents(); capture(window, 'window-narrow')
        assert window.timeline_scroll.parentWidget().rect().contains(window.timeline_scroll.geometry()), 'Narrow timeline scroll must fit its parent'
        dialog = VideoTransitionDialog(window, transition)
        dialog.resize(520, 420); dialog.show(); app.processEvents(); capture(dialog, 'dialog')
        dialog.resize(320, 420); QTest.qWait(100); app.processEvents(); capture(dialog, 'dialog-narrow')
        assert dialog.width() <= 340, 'Narrow dialog must stay narrow'
        assert dialog.scroll.horizontalScrollBar().maximum() == 0, 'Instructions must not clip horizontally'
        dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum())
        QTest.qWait(100); dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum())
        app.processEvents(); capture(dialog, 'dialog-narrow-bottom')
        assert all(label.height() >= label.heightForWidth(label.width())
                   for label in dialog.scroll.findChildren(QLabel)), 'Wrapped explanatory text must not be vertically clipped'
        assert dialog.apply_button.isEnabled()
        assert dialog.job is None and window.editor.revision == 0
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        records.append(dict(locale=locale, device_scale=dialog.devicePixelRatioF(),
            evidence='Synthetic metadata-only offscreen UI, not native desktop or source/render validation',
            dialog_width=dialog.width(), dialog_height=dialog.height(),
            window_width=window.width(), window_height=window.height(),
            marker=[transition_rect(window.timeline, transition).x(), transition_rect(window.timeline, transition).y()],
            labels=labels, scroll_maximum=dialog.scroll.verticalScrollBar().maximum(),
            label_geometry=[dict(text=l.text(), width=l.width(), height=l.height(), needed=l.heightForWidth(l.width()), y=l.y()) for l in dialog.scroll.findChildren(QLabel)], files=files))
        dialog.reject(); window.cancel_jobs()
        for job in list(window.jobs):
            assert job.wait(5000)
        window.dirty = False; window.closing = True; window.close(); app.processEvents()
(args.output / f'manifest-scale{os.environ.get("QT_SCALE_FACTOR", "1")}.json').write_text(
    json.dumps(records, indent=2, ensure_ascii=False))

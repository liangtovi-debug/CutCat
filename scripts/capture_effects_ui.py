"""Capture actual Qt controls playing the shared-graph effects/stickers demo.

Run after scripts/create_effects_demo.py. No mock viewer pixels are used.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import argparse
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication, QScrollArea
from lumen.model import Editor, Project
from lumen.ui import MainWindow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, default=Path(__file__).resolve().parents[1] / 'artifacts/effects-demo')
    args = parser.parse_args(); folder = args.folder.resolve()
    project_path = folder / 'project.lumen'; preview_path = folder / 'effects-and-stickers.mp4'
    if not preview_path.is_file(): raise RuntimeError('Render the actual effects demo before capturing its UI.')
    app = QApplication.instance() or QApplication([])
    window = MainWindow(); window.editor = Editor(Project.load(project_path)); window.project_path = project_path
    clip = next(c for c in window.project.clips if c.track < 2 and c.effects)
    window.selection = clip.id; window.resize(1680, 1400); window.refresh(); window.show(); app.processEvents()
    window.main_split.setSizes([200, 1000, 480])
    window.activate_preview(preview_path, window.revision, 'graded')
    window.player.play(); window.player.setPosition(2100)
    deadline = time.monotonic() + 12
    while (window.video.image.isNull() or window.player.position() < 2100) and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.02)
    if window.video.image.isNull() or window.player.position() < 2100:
        raise RuntimeError('Qt could not decode and seek the genuine rendered preview.')
    window.player.pause(); window.scopes.set_image(window.video.image)
    overlay = window.project.overlays[-1]; window.select_overlay(overlay.id)
    app.processEvents()
    assert window.overlay_start.value() == overlay.start
    assert window.overlay_transform['opacity'].value() == overlay.keyframes[0].opacity
    assert window.grab().save(str(folder / 'editor-stickers.png'))
    window.keyframe_toggle.setChecked(True); window.overlay_keyframes.selectRow(1)
    scroll = window.stickers_panel
    assert isinstance(scroll, QScrollArea)
    app.processEvents(); scroll.ensureWidgetVisible(window.overlay_keyframe_frame); scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    app.processEvents()
    assert window.overlay_keyframe_frame.value() == overlay.keyframes[1].frame
    assert window.overlay_keyframe_values['x'].value() == overlay.keyframes[1].x
    assert window.grab().save(str(folder / 'editor-keyframes.png'))
    window.select_clip(clip.id); window.select_clip_effect(clip.effects[-1].id)
    app.processEvents()
    assert window.effect_amount.value() == clip.effects[-1].amount
    assert window.grab().save(str(folder / 'editor-effects.png'))
    window.dirty = False; window.close(); app.processEvents()
    print('Saved actual Qt sticker, keyframe and ordered-effect controls with a decoded shared-graph preview.')


if __name__ == '__main__': main()

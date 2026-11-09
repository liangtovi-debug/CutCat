"""Capture actual bilingual Qt export dialogs with synthetic static PNG assets.

This offline script checks UI/profile validation only. It does not claim native
Windows/macOS behavior, FFmpeg capability or color precision from screenshots.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QTextLayout, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QScrollArea
from lumen.i18n import set_locale
from lumen.model import Caption, Clip, Media, Overlay, Project
from lumen.sticker_assets import load_sticker
from lumen.ui_color_export import ExportProfileDialog


def capture(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    # Use installed CJK fallback, without downloading or packaging a font.
    font = QFont(app.font())
    font.setFamilies(['Noto Sans CJK SC', *font.families()])
    app.setFont(font)
    shaping = QTextLayout('Export PNG 静态贴纸 10 位', app.font())
    shaping.beginLayout()
    shaping.createLine().setLineWidth(1000)
    shaping.endLayout()
    runs = shaping.glyphRuns()
    assert runs and all(run.rawFont().isValid() for run in runs)
    assert all(index != 0 for run in runs for index in run.glyphIndexes()), \
        'A real installed font must cover both English and Chinese UI text'

    source = output / 'synthetic-static.png'
    png = QImage(32, 24, QImage.Format.Format_RGBA8888)
    png.fill(QColor('#80ffbb66'))
    assert png.save(str(source))
    source_bytes = source.read_bytes()
    project = Project(media=[Media('media', 'synthetic-source.mp4', 'Synthetic', 1, 64, 48)],
                      clips=[Clip('clip', 'media', 0, 0, 0, 30)],
                      captions=[Caption('caption', 2, 8, 'User text 原文 <unchanged>')],
                      overlays=[Overlay('static', str(source), load_sticker(source).digest, 2, 8)])
    before = copy.deepcopy(project.to_dict())
    captures = []
    checks = []

    def shot(dialog, name):
        app.processEvents()
        assert dialog.grab().save(str(output / name))
        captures.append(name)

    for locale in ('en', 'zh-CN'):
        set_locale(locale)
        dialog = ExportProfileDialog(project)
        dialog.profile_choice.setCurrentIndex(1)
        dialog.resize(720, 740)
        dialog.show()
        app.processEvents()
        assert dialog.graphics_note.isVisible()
        assert dialog.graphics_note.textFormat() == Qt.TextFormat.PlainText
        assert ('静态 PNG 贴纸' if locale == 'zh-CN' else 'static PNG stickers') in dialog.profile_limits.text()
        shot(dialog, f'{locale}-static-png-supported.png')
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.selected_profile == 'prores_422_hq_10bit'
        assert project.to_dict() == before
        checks.append(f'{locale}: static PNG plus captions accepted; project unchanged')

        dialog.show()
        assert dialog.selected_profile is None
        dialog.resize(dialog.minimumSize())
        app.processEvents()
        scroll = dialog.findChild(QScrollArea)
        assert scroll.horizontalScrollBar().maximum() == 0
        assert dialog.rect().contains(dialog.buttons.geometry())
        shot(dialog, f'{locale}-narrow-top.png')
        scroll.ensureWidgetVisible(dialog.graphics_note)
        app.processEvents()
        assert scroll.horizontalScrollBar().maximum() == 0
        assert dialog.graphics_note.width() <= scroll.viewport().width()
        assert dialog.continue_button.isVisible() and dialog.cancel_button.isVisible()
        shot(dialog, f'{locale}-narrow-graphics-boundary.png')
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert not dialog.isVisible() and dialog.selected_profile is None
        checks.append(f'{locale}: narrow dialog scrolls without horizontal clipping; Escape clears selection')

        blocked = Project.from_dict(before)
        manifest = Path(__file__).resolve().parents[1] / 'lumen/assets/stickers/pulse_ring/sticker.json'
        blocked.overlays = [Overlay('animated', str(manifest), load_sticker(manifest).digest, 2, 8)]
        blocked_before = copy.deepcopy(blocked.to_dict())
        dialog = ExportProfileDialog(blocked)
        dialog.profile_choice.setCurrentIndex(1)
        dialog.resize(720, 780)
        dialog.show()
        dialog.accept()
        assert dialog.isVisible() and dialog.selected_profile is None
        assert ('动画贴纸包' if locale == 'zh-CN' else 'Animated frame-pack manifests') in dialog.validation_notice.text()
        shot(dialog, f'{locale}-animated-pack-blocked.png')
        dialog.profile_choice.setCurrentIndex(0)
        assert dialog.validation_notice.isHidden() and dialog.graphics_note.isHidden()
        dialog.accept()
        assert dialog.selected_profile == 'h264_8bit'
        assert blocked.to_dict() == blocked_before
        checks.append(f'{locale}: animated pack blocked; explicit H.264 selection succeeds without edits')

    set_locale('en')
    dialog = ExportProfileDialog(project)
    dialog.profile_choice.setCurrentIndex(1)
    dialog.show()
    for locale in ('zh-CN', 'en', 'zh-CN'):
        set_locale(locale)
        app.processEvents()
        assert dialog.profile_choice.currentData() == 'prores_422_hq_10bit'
        assert ('静态 PNG 贴纸' if locale == 'zh-CN' else 'static PNG stickers') in dialog.profile_limits.text()
        assert ('8 位 RGBA' if locale == 'zh-CN' else '8-bit RGBA') in dialog.graphics_note.text()
    dialog.close()
    assert dialog.selected_profile is None
    assert project.to_dict() == before and source.read_bytes() == source_bytes
    checks.append('Live language switching, window close and repeated open preserve project and source bytes')
    evidence = {'captures': captures, 'checks': checks, 'platform': sys.platform,
                'qt_platform': os.environ.get('QT_QPA_PLATFORM'),
                'content': 'synthetic local PNG and packaged original animation; no user media',
                'not_native_platform_validation': True,
                'not_render_or_color_precision_validation': True}
    (output / 'capture.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(evidence, ensure_ascii=False))
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    capture(parser.parse_args().output.resolve())

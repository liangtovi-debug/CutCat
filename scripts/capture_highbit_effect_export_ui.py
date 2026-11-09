"""Capture actual bilingual Qt Gaussian-blur eligibility and vignette rejection.

Synthetic model data only. Screenshots verify UI, not native desktop builds,
FFmpeg precision, or successful final exports. The chooser is preflight-only.
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
from PySide6.QtGui import QFont, QTextLayout
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QScrollArea
from lumen.i18n import set_locale
from lumen.model import Caption, Clip, ClipEffect, Media, Project
from lumen.ui_color_export import ExportProfileDialog
from lumen.ui_style import STYLE


def capture(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    font = QFont(app.font())
    font.setFamilies(['Noto Sans CJK SC', *font.families()])
    app.setFont(font)
    shaping = QTextLayout('Gaussian blur 高斯模糊 暗角 16 位', app.font())
    shaping.beginLayout()
    shaping.createLine().setLineWidth(1000)
    shaping.endLayout()
    runs = shaping.glyphRuns()
    assert runs and all(run.rawFont().isValid() for run in runs)
    assert all(index != 0 for run in runs for index in run.glyphIndexes()), \
        'A real installed font must cover both English and Chinese UI text'

    project = Project(media=[Media('media', 'synthetic-source.mp4', 'Synthetic', 1, 64, 48)],
                      clips=[Clip('clip', 'media', 0, 0, 0, 30,
                                  effects=[ClipEffect('blur', 'blur', 3)])],
                      captions=[Caption('caption', 2, 8, 'User text 原文 <unchanged>')])
    before = copy.deepcopy(project.to_dict())
    captures, checks = [], []

    def shot(dialog, name):
        app.processEvents()
        assert dialog.grab().save(str(output / name))
        captures.append(name)

    def layout_ok(dialog):
        app.processEvents()
        scroll = dialog.findChild(QScrollArea)
        assert scroll.horizontalScrollBar().maximum() == 0
        assert dialog.rect().contains(dialog.buttons.geometry())
        assert dialog.continue_button.isVisible() and dialog.cancel_button.isVisible()
        assert dialog.effect_note.wordWrap()
        assert dialog.effect_note.width() <= scroll.viewport().width()
        if not dialog.validation_notice.isHidden():
            notice = dialog.validation_notice
            assert dialog.rect().contains(notice.geometry())
            assert notice.geometry().bottom() < dialog.buttons.geometry().top()
            assert notice.height() >= notice.heightForWidth(notice.width())
        return scroll

    for locale in ('en', 'zh-CN'):
        set_locale(locale)
        dialog = ExportProfileDialog(project)
        dialog.profile_choice.setCurrentIndex(1)
        dialog.resize(760, 860)
        dialog.show()
        layout_ok(dialog)
        assert ('高斯模糊支持原生 RGB16' if locale == 'zh-CN' else 'Gaussian blur is supported in native RGB16') in dialog.profile_limits.text()
        assert '0–30' in dialog.effect_note.text()
        shot(dialog, f'{locale}-gaussian-blur-eligible-wide.png')
        dialog.continue_button.click()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.selected_profile == 'prores_422_hq_10bit'
        assert project.to_dict() == before
        checks.append(f'{locale}: Gaussian sigma 3 plus captions accepted by chooser; model unchanged; runtime export check still required')

        dialog.show()
        assert dialog.selected_profile is None
        dialog.resize(dialog.minimumSize())
        scroll = layout_ok(dialog)
        shot(dialog, f'{locale}-gaussian-blur-narrow-top.png')
        scroll.ensureWidgetVisible(dialog.effect_note)
        layout_ok(dialog)
        shot(dialog, f'{locale}-gaussian-blur-narrow-semantics.png')
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert not dialog.isVisible() and dialog.selected_profile is None
        checks.append(f'{locale}: narrow effect note scrolls, no horizontal clipping; Escape clears selection')

        blocked = Project.from_dict(before)
        blocked.clips[0].effects.append(ClipEffect('vignette', 'vignette', .5))
        blocked_before = copy.deepcopy(blocked.to_dict())
        dialog = ExportProfileDialog(blocked)
        dialog.profile_choice.setCurrentIndex(1)
        dialog.resize(760, 940)
        dialog.show()
        for _ in range(2):
            dialog.continue_button.click()
            assert dialog.isVisible() and dialog.selected_profile is None
            assert ('生效中的暗角' if locale == 'zh-CN' else 'Active vignette') in dialog.validation_notice.text()
        layout_ok(dialog)
        shot(dialog, f'{locale}-active-vignette-blocked-wide.png')
        dialog.resize(dialog.minimumSize())
        layout_ok(dialog)
        shot(dialog, f'{locale}-active-vignette-blocked-narrow.png')
        other = 'en' if locale == 'zh-CN' else 'zh-CN'
        set_locale(other)
        layout_ok(dialog)
        assert ('生效中的暗角' if other == 'zh-CN' else 'Active vignette') in dialog.validation_notice.text()
        set_locale(locale)
        layout_ok(dialog)
        assert dialog.profile_choice.currentData() == 'prores_422_hq_10bit'
        dialog.profile_choice.setCurrentIndex(0)
        assert dialog.validation_notice.isHidden() and dialog.effect_note.isHidden()
        dialog.continue_button.click()
        assert dialog.selected_profile == 'h264_8bit'
        assert blocked.to_dict() == blocked_before
        checks.append(f'{locale}: active vignette blocks repeatedly, remains legible at narrow width, live locale switch preserves selection; explicit H.264 succeeds without edits')

    set_locale('en')
    dialog = ExportProfileDialog(project)
    dialog.profile_choice.setCurrentIndex(1)
    dialog.show()
    for locale in ('zh-CN', 'en', 'zh-CN'):
        set_locale(locale)
        app.processEvents()
        assert dialog.profile_choice.currentData() == 'prores_422_hq_10bit'
        assert ('不是线性光或 HDR' if locale == 'zh-CN' else 'not linear light or HDR') in dialog.effect_note.text()
        assert ('暂不支持片段特效关键帧' if locale == 'zh-CN' else 'Clip-effect keyframes are not supported') in dialog.effect_note.text()
    dialog.close()
    assert dialog.selected_profile is None and project.to_dict() == before
    checks.append('Live blur-note language switching, window close and reopening preserve model and selection boundaries')
    evidence = {'captures': captures, 'checks': checks, 'platform': sys.platform,
                'qt_platform': os.environ.get('QT_QPA_PLATFORM'),
                'content': 'synthetic local project model; no user media',
                'not_native_platform_validation': True,
                'not_render_or_color_precision_validation': True}
    (output / 'capture.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(evidence, ensure_ascii=False))
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    capture(parser.parse_args().output.resolve())

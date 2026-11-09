"""Capture real bilingual Qt review widgets and verify apply/reject/undo.

Linux offscreen evidence only, not native Windows/macOS acceptance. Uses a
synthetic saved caption and audio-clip model, not user media or an AI model.
"""
from __future__ import annotations
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QTextLayout
from PySide6.QtWidgets import QApplication, QMessageBox
from lumen.i18n import set_locale
from lumen.model import Caption, Clip, Editor, Media, Project
from lumen.ui import MainWindow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('artifacts/controlled-assistant-ui'))
    args = parser.parse_args()
    folder = args.output.resolve(); folder.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    font_path = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    if font_path.is_file():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        assert font_id >= 0
        app.setFont(QFont('Noto Sans CJK SC', 10))
    shaped = QTextLayout('剪辑猫受控编辑字幕预览 CutCat 15 dB', app.font())
    shaped.beginLayout(); shaped.createLine().setLineWidth(900); shaped.endLayout()
    assert shaped.glyphRuns() and all(g != 0 for run in shaped.glyphRuns() for g in run.glyphIndexes()), 'A real CJK-capable font is required for screenshot evidence.'
    with tempfile.TemporaryDirectory(prefix='cutcat-controlled-ui-') as temporary:
        settings = QSettings(str(Path(temporary) / 'workspace.ini'), QSettings.Format.IniFormat)
        settings.setValue('mode', 'professional')
        window = MainWindow(workspace_settings=settings, ui_language='en')
        window.editor = Editor(Project(name='Controlled review demo',
            media=[Media('voice-source', '/synthetic/demo.wav', 'Interview / 访谈', 10, has_video=False, has_audio=True)],
            clips=[Clip('voice-01', 'voice-source', 2, 0, 0, 180)],
            captions=[Caption('caption-01', 0, 90, '欢迎来到剪辑猫。 Welcome to CutCat.')]))
        window.resize(1740, 1250); window.refresh(); window.show()
        window.main_split.setSizes([230, 850, 660])
        window.side_tabs.setCurrentWidget(window.ai_panel)
        window.controlled_text.setPlainText('欢迎使用剪辑猫！ Welcome to CutCat!')
        window.controlled_gain_enabled.setChecked(True)
        window.controlled_gain.setValue(-6)
        window.preview_controlled_edits()
        assert window.controlled_proposal is not None
        before = deepcopy(window.project)
        proposal = window.controlled_proposal
        captures = []
        for locale in ('en', 'zh-CN'):
            set_locale(locale); app.processEvents()
            window.ai_panel.verticalScrollBar().setValue(0); app.processEvents()
            assert window.controlled_proposal is proposal
            for name, widget in (('review', window.controlled_group), ('editor', window)):
                filename = f'{name}-{locale}.png'
                assert widget.grab().save(str(folder / filename)); captures.append(filename)
            def capture_dialog(locale=locale):
                box = app.activeModalWidget()
                assert isinstance(box, QMessageBox)
                filename = f'confirmation-{locale}.png'
                assert box.grab().save(str(folder / filename)); captures.append(filename)
                box.button(QMessageBox.StandardButton.No).click()
            QTimer.singleShot(100, capture_dialog)
            window.apply_controlled_edits()
            assert window.project == before and not window.dirty
        # Edit invalidates the previous preview and does not mutate the project.
        window.controlled_gain.setValue(-5)
        assert not window.controlled_apply_button.isEnabled()
        app.processEvents()
        assert window.controlled_group.grab().save(str(folder / 'revision-needs-preview-zh-CN.png'))
        captures.append('revision-needs-preview-zh-CN.png')
        window.controlled_gain.setValue(-6); window.preview_controlled_edits()
        def accept_dialog():
            box = app.activeModalWidget(); assert isinstance(box, QMessageBox)
            box.button(QMessageBox.StandardButton.Yes).click()
        QTimer.singleShot(100, accept_dialog)
        window.apply_controlled_edits()
        assert window.project.captions[0].text == '欢迎使用剪辑猫！ Welcome to CutCat!'
        assert window.project.clips[0].gain_db == -6
        assert window.editor.revision == 1
        window.undo(); assert window.project == before
        assert not window.editor.undo()
        evidence = {
            'kind': 'actual Qt widget capture', 'platform': sys.platform,
            'qt_platform': os.environ['QT_QPA_PLATFORM'],
            'claim': 'Linux offscreen only; not Windows/macOS native certification',
            'locales': ['en', 'zh-CN'], 'captures': captures,
            'checks': {'preview_no_mutation': True, 'rejected_confirmation_no_mutation': True,
                       'locale_switch_preserves_preview': True, 'revision_requires_new_preview': True,
                       'combined_apply_single_revision': True, 'one_undo_restores_batch': True,
                       'cjk_glyphs_present': True},
            'uses_model_or_network': False, 'uses_user_media': False,
        }
        (folder / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        window.dirty = False; window.close(); window.deleteLater(); app.processEvents()
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == '__main__':
    main()

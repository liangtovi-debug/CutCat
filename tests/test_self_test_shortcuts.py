"""Keep frozen shortcut acceptance bounded, real-Qt and state-restoring."""
from copy import deepcopy
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from lumen.model import Caption, Clip, Editor, Media, Project
from lumen.self_test import _shortcut_runtime_checks
from lumen.shortcuts import SETTINGS_KEY
from lumen.ui import MainWindow


def test_frozen_shortcut_acceptance_restores_editor_and_preferences(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / 'ui.ini'), QSettings.Format.IniFormat)
    settings.setValue('mode', 'professional')
    window = MainWindow(workspace_settings=settings)
    media = Media('media', str(tmp_path / 'local-fixture.mp4'), 'Fixture', 1, 640, 360, True, True)
    project = Project(width=640, height=360, fps_num=24, media=[media],
                      clips=[Clip('clip', 'media', 0, 0, 0, 24)],
                      captions=[Caption('caption', 2, 12, 'Keep this caption', 'en')])
    window.editor = Editor(project)
    window.editor.set_clip_speed('clip', '3/2')
    window.revision = 1
    window.dirty = True
    window.refresh()
    window.select_clip('clip')
    window.seek_frame(5)
    window.resize(1920, 1080)
    window.show()
    app.processEvents()
    original_editor = window.editor
    original_language = window.ui_language
    before = deepcopy((window.project.to_dict(), window.revision, window.dirty,
                       [item.to_dict() for item in window.editor._undo],
                       [item.to_dict() for item in window.editor._redo],
                       window.selection, window.active_selection_type,
                       window.timeline.playhead, window.shortcuts.profiles))
    screenshots = []
    try:
        report = _shortcut_runtime_checks(window, tmp_path, screenshots)
        assert window.editor is original_editor
        assert window.ui_language == original_language
        assert before == (window.project.to_dict(), window.revision, window.dirty,
                          [item.to_dict() for item in window.editor._undo],
                          [item.to_dict() for item in window.editor._redo],
                          window.selection, window.active_selection_type,
                          window.timeline.playhead, window.shortcuts.profiles)
        assert not settings.contains(SETTINGS_KEY)
        assert report['host_split_binding'] == ('Cmd+K' if sys.platform == 'darwin' else 'Ctrl+K')
        assert report['dialog_record_apply_reload']
        assert report['preferences_preserve_project_undo_preview_cache']
        assert not report['physical_keyboard_tested']
        assert not report['os_ime_integration_tested']
        assert screenshots == ['frozen-ui-zh-CN-shortcuts.png', 'frozen-ui-en-shortcuts.png']
        assert all(not QImage(str(tmp_path / name)).isNull() for name in screenshots)
        assert not window.jobs
    finally:
        window.dirty = False
        window.close()
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

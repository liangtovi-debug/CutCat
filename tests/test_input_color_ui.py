"""Source assignment controls change only their persisted field, never a look."""
import copy
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QSettings
from lumen.ui import MainWindow
from lumen.model import Media, Grade

@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])

@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_: None)
    monkeypatch.setattr(QMessageBox, 'information', lambda *_: None)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path/'settings'))
    w = MainWindow()
    source = tmp_path/'video.mp4'; source.write_bytes(b'mocked fixture; no decoding')
    w.editor.add_media(Media('m', str(source), 'source', 5, 64, 64, True, False))
    w.refresh(); w.bin.setCurrentRow(0); w.add_selected_media()
    yield w
    w.dirty = False; w.close(); app.processEvents()


def test_input_assignment_is_single_undo_without_hidden_grade_loss(window):
    w = window; c = w.selected_clip()
    grade = copy.deepcopy(c.grade)
    grade.lift = [.123456, -.1, 0.]
    grade.red_curve = [[0., .1], [.3, .4], [1., .9]]
    w.editor.update_clip(c.id, grade=grade); w.refresh()
    before = copy.deepcopy(w.project)
    w.input_encoding_choice.setCurrentIndex(w.input_encoding_choice.findData('srgb'))
    current = w.selected_clip().grade
    assert current.input_encoding == 'srgb'
    expected = copy.deepcopy(grade); expected.input_encoding = 'srgb'
    assert current == expected
    w.undo(); assert w.project == before
    w.redo(); assert w.selected_clip().grade == expected


def test_refresh_does_not_mutate_project_or_revision(window):
    before = copy.deepcopy(window.project); revision = window.revision
    for _ in range(4): window.refresh_input_color_controls()
    assert window.project == before and window.revision == revision
    assert '709' in window.input_color_status.text()


def test_tag_mismatch_is_visible_without_silent_reassignment(window):
    window.project.media[0].color_transfer = 'iec61966-2-1'
    before = copy.deepcopy(window.project)
    window.refresh_input_color_controls()
    assert window.project == before
    assert window.input_encoding_choice.currentData() == 'rec709'
    assert 'srgb' in window.input_color_status.text().lower() or 'sRGB' in window.input_color_status.text()


def test_unsupported_hdr_status_is_actionable(window):
    window.project.media[0].color_transfer = 'smpte2084'
    window.refresh_input_color_controls()
    text = window.input_color_status.text().lower()
    assert any(word in text for word in ('hdr','unsupported','pq'))
    assert window.selected_clip().grade.input_encoding == 'rec709'

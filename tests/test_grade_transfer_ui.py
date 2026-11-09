"""Actual native inspector grade-copy/paste, without OS clipboard transport."""
from copy import deepcopy
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from lumen.i18n import get_locale, set_locale, tr_error
from lumen.i18n_catalog_grade_transfer import ERROR_ZH_CN
from lumen.model import Clip, Editor, Grade, Media, Project
from lumen.ui import MainWindow

CUBE = 'LUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n'


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    previous_locale = get_locale()
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(str(args[2])))
    settings = QSettings(str(tmp_path / 'workspace.ini'), QSettings.Format.IniFormat)
    settings.setValue('mode', 'professional')
    w = MainWindow(workspace_settings=settings, ui_language='en')
    source = tmp_path / 'source.mp4'; source.write_bytes(b'not decoded in inspector tests')
    grade = Grade(linear_exposure=.123456789, saturation=1.23456789,
                  enabled=False, lift=[.1, -.2, .3], gamma=[.8, .9, 1.1],
                  red_curve=[[0., 0.], [.21, .31], [1., 1.]],
                  input_encoding='linear_rec709', lut_strength=.23456789)
    w.editor = Editor(Project(media=[Media('m', str(source), 'Source', 10, 640, 360, True, True),
                                    Media('a', str(tmp_path / 'source.wav'), 'Audio', 10,
                                          has_video=False, has_audio=True)],
                              clips=[Clip('source', 'm', 0, 0, 0, 60, grade=grade),
                                     Clip('target', 'm', 0, 90, 60, 60, grade=Grade(input_encoding='srgb')),
                                     Clip('audio', 'a', 2, 0, 0, 60)]))
    w.refresh(); w.select_clip('source'); w.inspector_tabs.setCurrentIndex(1)
    w.test_warnings = warnings
    yield w
    w.cancel_jobs()
    for job in list(w.jobs): assert job.wait(5000)
    w.dirty = False; w.closing = True; w.close(); w.deleteLater(); app.processEvents()
    set_locale(previous_locale)


def test_native_panel_initial_states_and_boundaries(window):
    w = window
    assert w.inspector_tabs.widget(1).isAncestorOf(w.grade_transfer_controls)
    assert w.copy_grade_button.text() == 'Copy grade'
    assert w.paste_grade_button.text() == 'Paste grade'
    assert w.copy_grade_button.isEnabled() and not w.paste_grade_button.isEnabled()
    assert 'not copied' in w.grade_transfer_note.text()
    assert 'input normalization stays unchanged' in w.paste_grade_button.toolTip()
    assert 'system clipboard' in w.copy_grade_button.toolTip()
    assert not w.editor.can_undo


def test_copy_is_not_an_edit_and_never_uses_system_clipboard(window, app):
    w = window
    clipboard = app.clipboard()
    clipboard.setText('unrelated clipboard sentinel')
    before = deepcopy(w.project); revision = w.revision
    w.copy_grade_button.click()
    assert w._copied_grade is not None
    assert w.project == before and w.revision == revision and not w.editor.can_undo
    assert clipboard.text() == 'unrelated clipboard sentinel'
    assert not w.paste_grade_button.isEnabled()
    assert 'already has this creative grade' in w.grade_transfer_status.text()


def test_paste_is_one_editor_update_and_invalidates_preview_undo_redo(window, monkeypatch):
    w = window
    w.copy_grade_button.click(); w.select_clip('target')
    assert w.paste_grade_button.isEnabled()
    before = deepcopy(w.project); revision = w.revision
    w.preview_revision = revision; w.loaded_preview_mode = w.preview_mode
    calls = []; original = w.editor.update_clip
    monkeypatch.setattr(w.editor, 'update_clip', lambda *args, **kwargs: (calls.append((args, kwargs)), original(*args, **kwargs))[1])
    w.paste_grade_button.click()
    after = deepcopy(w.project)
    expected = deepcopy(before)
    expected.clips[1].grade = deepcopy(before.clips[0].grade)
    expected.clips[1].grade.input_encoding = 'srgb'
    assert after == expected
    assert len(calls) == 1 and set(calls[0][1]) == {'grade'}
    assert len(w.editor._undo) == 1 and w.revision == revision + 1
    assert w.preview_revision != w.revision and not w.play_button.isEnabled()
    assert not w.paste_grade_button.isEnabled()
    assert w.input_encoding_choice.currentData() == 'srgb'
    assert w.grade_spins['linear_exposure'].value() == .12
    assert w.selected_clip().grade.linear_exposure == .123456789
    w.undo(); assert w.project == before and w.paste_grade_button.isEnabled()
    w.redo(); assert w.project == after and not w.paste_grade_button.isEnabled()
    assert not w.test_warnings


def test_identical_paste_does_not_bump_revision_or_autosave(window, monkeypatch):
    w = window
    w.copy_selected_grade(); w.select_clip('target'); w.paste_selected_grade()
    before = deepcopy(w.project); revision = w.revision; undo = len(w.editor._undo)
    monkeypatch.setattr(w, 'edit', lambda *_: pytest.fail('No-op must never start an edit'))
    w.paste_selected_grade(); w.copy_selected_grade(); w.refresh_inspector()
    assert w.project == before and w.revision == revision and len(w.editor._undo) == undo


@pytest.mark.parametrize('selection', ['', 'audio', 'missing'])
def test_empty_audio_and_missing_selection_are_safe_and_disabled(window, selection):
    w = window; w.copy_selected_grade(); w.select_clip(selection)
    before = deepcopy(w.project); revision = w.revision
    assert not w.copy_grade_button.isEnabled() and not w.paste_grade_button.isEnabled()
    w.copy_selected_grade(); w.paste_selected_grade()
    assert w.project == before and w.revision == revision and not w.editor.can_undo


def test_effect_selection_is_not_an_implicit_clip_target(window):
    w = window; w.copy_selected_grade(); w.select_clip('target')
    w.active_selection_type = 'effect'; w.refresh_inspector()
    before = deepcopy(w.project); revision = w.revision
    assert not w.copy_grade_button.isEnabled() and not w.paste_grade_button.isEnabled()
    w.copy_selected_grade(); w.paste_selected_grade()
    assert w.project == before and w.revision == revision


def test_lut_failure_preserves_project_revision_and_previous_copy(window, tmp_path):
    w = window
    lut = tmp_path / 'look.cube'; lut.write_text(CUBE)
    w.project.clips[0].grade.lut_path = str(lut); w.refresh_inspector()
    w.copy_selected_grade(); copied = w._copied_grade
    lut.write_text(CUBE.replace('0 0 0', '1 0 0', 1))
    w.select_clip('target')
    before = deepcopy(w.project); revision = w.revision
    w.paste_grade_button.click()
    assert w.project == before and w.revision == revision and not w.editor.can_undo
    assert 'changed or moved' in w.test_warnings[-1]
    lut.unlink(); w.select_clip('source'); w.copy_selected_grade()
    assert w._copied_grade is copied and 'Cannot read the grade LUT' in w.test_warnings[-1]
    assert w.project == before and w.revision == revision


def test_window_snapshot_survives_project_change_but_never_model_mutation(window):
    w = window
    w.copy_selected_grade(); copied = w._copied_grade
    new_project = Project(media=deepcopy(w.project.media), clips=[deepcopy(w.project.clips[1])])
    w.editor = Editor(new_project); w.refresh(); w.select_clip('target')
    assert w._copied_grade is copied and w.paste_grade_button.isEnabled()
    w.paste_selected_grade()
    assert w.selected_clip().grade == copied.grade_for_input('srgb')
    assert len(w.editor._undo) == 1


def test_clipboard_is_not_shared_between_windows(window, app, tmp_path):
    w = window; w.copy_selected_grade()
    settings = QSettings(str(tmp_path / 'second.ini'), QSettings.Format.IniFormat)
    other = MainWindow(workspace_settings=settings, ui_language='en')
    try:
        assert other._copied_grade is None
        assert not other.paste_grade_button.isEnabled()
    finally:
        other.cancel_jobs()
        for job in list(other.jobs): assert job.wait(5000)
        other.dirty = False; other.closing = True; other.close(); other.deleteLater(); app.processEvents()


def test_language_and_workspace_switches_preserve_full_hidden_precision(window):
    w = window
    w.set_workspace_mode('simple'); w.copy_selected_grade(); copied = w._copied_grade
    w.select_clip('target')
    before = deepcopy(w.project); revision = w.revision
    for language in ['zh-CN', 'en', 'zh-CN']:
        set_locale(language)
        for mode in ['professional', 'simple']:
            w.set_workspace_mode(mode)
            assert w._copied_grade is copied and w.project == before and w.revision == revision
            assert not w.grade_transfer_controls.isHidden()
    assert w.copy_grade_button.text() == '复制调色'
    assert w.paste_grade_button.text() == '粘贴调色'
    assert '输入标准化' in w.grade_transfer_note.text()
    w.paste_grade_button.click()
    assert w.selected_clip().grade == copied.grade_for_input('srgb')
    assert w.selected_clip().grade.linear_exposure == .123456789
    assert w.selected_clip().grade.red_curve == [[0., 0.], [.21, .31], [1., 1.]]
    assert w.selected_clip().grade.enabled is False


def test_all_errors_render_from_central_chinese_catalog(window):
    set_locale('zh-CN')
    for source, expected in ERROR_ZH_CN.items():
        assert str(tr_error(ValueError(source))) == expected
    set_locale('en')
    for source in ERROR_ZH_CN:
        assert tr_error(ValueError(source)) == source

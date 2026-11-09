"""Native hue-curve gestures, history isolation, translation and compact geometry."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QKeyEvent, QTextLayout
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from lumen.i18n import set_locale, tr_error
from lumen.model import Clip, Editor, Grade, Media, Project
from lumen.ui import MainWindow
from lumen.ui_selective_color import SelectiveCurveControl, SelectiveCurveEditor
from lumen.ui_workspace import hidden_settings_summary


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(str(args[2])))
    settings = QSettings(str(tmp_path / 'workspace.ini'), QSettings.Format.IniFormat)
    settings.setValue('mode', 'professional')
    w = MainWindow(workspace_settings=settings, ui_language='en')
    source = tmp_path / 'source.mp4'; source.write_bytes(b'UI-only; never decoded')
    w.editor = Editor(Project(media=[Media('m', str(source), 'Source', 10, 640, 360, True, True)],
                              clips=[Clip('source', 'm', 0, 0, 0, 60),
                                     Clip('target', 'm', 0, 90, 60, 60)]))
    w.refresh(); w.select_clip('source'); w.inspector_tabs.setCurrentIndex(1)
    w.color_tabs.setCurrentIndex(2); w.curve_choice.setCurrentIndex(4)
    w.resize(1280, 800); w.show(); app.processEvents()
    w.test_warnings = warnings
    yield w
    w.cancel_jobs()
    for job in list(w.jobs): assert job.wait(5000)
    w.dirty = False; w.closing = True; w.close(); w.deleteLater(); app.processEvents()


@pytest.fixture
def standalone(app):
    widget = SelectiveCurveControl(); widget.resize(264, 450); widget.show(); app.processEvents()
    yield widget
    widget.close(); widget.deleteLater(); app.processEvents()


def drag(editor, start, end, *, release=True):
    QTest.mousePress(editor, Qt.MouseButton.LeftButton, pos=editor.to_screen(start).toPoint())
    QTest.mouseMove(editor, editor.to_screen(end).toPoint())
    if release:
        QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=editor.to_screen(end).toPoint())


@pytest.mark.parametrize('choice,key,neutral,target', [(4, 'hue_hue_curve', 0., .25), (5, 'hue_sat_curve', 1., 1.5)])
def test_drag_stages_then_one_transaction_and_undo_redo(window, choice, key, neutral, target):
    w = window; w.curve_choice.setCurrentIndex(choice)
    editor = w.selective_curve_control.editor
    before = deepcopy(w.project); revision = w.revision
    drag(editor, [0., neutral], [.1, target], release=False)
    assert w.project == before and w.revision == revision
    assert editor.points[0][1] == editor.points[-1][1]
    QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=editor.to_screen([.1, target]).toPoint())
    points = getattr(w.selected_clip().grade, key)
    assert points[0][0] == 0 and points[-1][0] == 1
    assert points[0][1] == points[-1][1] != neutral
    assert w.revision == revision + 1 and len(w.editor._undo) == 1
    after = deepcopy(w.project)
    w.undo(); assert w.project == before
    w.redo(); assert w.project == after
    assert not w.test_warnings


@pytest.mark.parametrize('interruption', ['escape', 'curve', 'selection', 'clear', 'tab', 'window'])
def test_interrupted_drag_never_writes_any_clip(window, app, interruption):
    w = window; editor = w.selective_curve_control.editor
    before = deepcopy(w.project); revision = w.revision
    drag(editor, [0., 0.], [.1, .25], release=False)
    if interruption == 'escape': QTest.keyClick(editor, Qt.Key.Key_Escape)
    elif interruption == 'curve': w.curve_choice.setCurrentIndex(5)
    elif interruption == 'selection': w.select_clip('target')
    elif interruption == 'clear': w.select_clip('')
    elif interruption == 'tab': w.color_tabs.setCurrentIndex(0)
    else: QApplication.sendEvent(editor, QEvent(QEvent.Type.WindowDeactivate))
    QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=editor.to_screen([.1, .25]).toPoint())
    assert not editor.dragging
    assert w.project == before and w.revision == revision and not w.editor.can_undo


def test_double_click_delete_and_selection_changes_are_one_step(window):
    w = window; editor = w.selective_curve_control.editor
    QTest.mouseDClick(editor, Qt.MouseButton.LeftButton, pos=editor.to_screen([.35, .2]).toPoint())
    assert len(w.selected_clip().grade.hue_hue_curve) == 3 and len(w.editor._undo) == 1
    assert editor.selected == 1
    QTest.keyClick(editor, Qt.Key.Key_Delete)
    assert len(w.project.clips) == 2
    assert w.selected_clip().grade.hue_hue_curve == [[0., 0.], [1., 0.]]
    assert len(w.editor._undo) == 2
    w.undo(); assert len(w.selected_clip().grade.hue_hue_curve) == 3
    w.select_clip('target'); assert editor.points == [[0., 0.], [1., 0.]]
    w.select_clip('source'); assert len(editor.points) == 3


def test_exact_fields_preserve_other_coordinate_precision_and_hidden_grade(window):
    w = window; grade = deepcopy(w.selected_clip().grade)
    grade.linear_exposure = .123456789
    grade.red_curve = [[0., .0123456789], [1., .987654321]]
    grade.hue_hue_curve = [[0., .0123456789], [.23456789, .123456789], [1., .0123456789]]
    w.editor.update_clip('source', grade=grade); w.refresh()
    control = w.selective_curve_control
    control.point_choice.setCurrentIndex(1)
    before = deepcopy(w.project); revision = w.revision
    control.hue_spin.setValue(120)
    actual = w.selected_clip().grade
    assert actual.hue_hue_curve[1] == [1 / 3, .123456789]
    assert actual.red_curve == grade.red_curve and actual.linear_exposure == grade.linear_exposure
    assert w.revision == revision + 1
    control.value_spin.setValue(-45)
    assert w.selected_clip().grade.hue_hue_curve[1] == [1 / 3, -.125]
    w.undo(); w.undo(); assert w.project == before


def test_keyboard_entry_only_commits_when_finished(window):
    w = window; control = w.selective_curve_control
    line = control.value_spin.lineEdit()
    line.setFocus(); line.selectAll()
    before = deepcopy(w.project); revision = w.revision
    QTest.keyClicks(line, '72')
    assert w.project == before and w.revision == revision
    QTest.keyClick(line, Qt.Key.Key_Return)
    assert w.selected_clip().grade.hue_hue_curve == [[0., .2], [1., .2]]
    assert w.revision == revision + 1


def test_keyboard_nudge_repeat_is_one_commit_and_escape_cancels(window):
    w = window; editor = w.selective_curve_control.editor
    editor.setFocus()
    before = deepcopy(w.project); revision = w.revision
    QTest.keyPress(editor, Qt.Key.Key_Up)
    for _ in range(3):
        QApplication.sendEvent(editor, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier, '', True))
    assert w.project == before
    QTest.keyRelease(editor, Qt.Key.Key_Up)
    assert len(w.editor._undo) == 1 and w.revision == revision + 1
    assert w.selected_clip().grade.hue_hue_curve[0][1] == pytest.approx(4 / 360)
    after = deepcopy(w.project)
    QTest.keyPress(editor, Qt.Key.Key_Down); QTest.keyClick(editor, Qt.Key.Key_Escape)
    QTest.keyRelease(editor, Qt.Key.Key_Down)
    assert w.project == after and editor.points == w.selected_clip().grade.hue_hue_curve


def test_keyboard_point_navigation_add_remove_and_fixed_endpoints(window):
    w = window; control = w.selective_curve_control; editor = control.editor
    editor.setFocus()
    QTest.keyClick(editor, Qt.Key.Key_Insert)
    assert editor.selected == 1 and len(editor.points) == 3
    assert control.hue_spin.isEnabled() and control.remove_button.isEnabled()
    QTest.keyClick(editor, Qt.Key.Key_PageDown)
    assert editor.selected == 2 and not control.hue_spin.isEnabled()
    count = len(w.editor._undo)
    QTest.keyClick(editor, Qt.Key.Key_Delete); QTest.keyClick(editor, Qt.Key.Key_Right)
    assert len(w.editor._undo) == count
    QTest.keyClick(editor, Qt.Key.Key_PageUp); QTest.keyClick(editor, Qt.Key.Key_Backspace)
    assert len(editor.points) == 2 and len(w.project.clips) == 2


def test_reset_defaults_noop_switches_and_repeated_refresh_preserve_project(window):
    w = window; control = w.selective_curve_control
    before = deepcopy(w.project); revision = w.revision
    for _ in range(3):
        for index in range(6):
            w.curve_choice.setCurrentIndex(index); w.reset_curve_button.click(); w.refresh_curve()
    assert w.project == before and w.revision == revision
    control.value_spin.setValue(1.4)
    changed = deepcopy(w.project)
    w.reset_curve_button.click()
    assert w.selected_clip().grade.hue_sat_curve == [[0., 1.], [1., 1.]]
    w.undo(); assert w.project == changed
    w.reset_curve_button.click(); count = len(w.editor._undo)
    w.reset_curve_button.click(); assert len(w.editor._undo) == count


def test_deleting_selected_clip_cancels_old_gesture_before_new_selection(window):
    w = window; editor = w.selective_curve_control.editor
    drag(editor, [0., 0.], [.1, .2], release=False)
    w.editor.delete_clip('source'); w.refresh(); w.select_clip('target')
    before = deepcopy(w.project); revision = w.revision
    QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=editor.to_screen([.1, .2]).toPoint())
    assert w.project == before and w.revision == revision
    assert editor.points == [[0., 0.], [1., 0.]]


def test_selective_settings_retained_and_disclosed_in_simple_mode(window):
    w = window; w.selective_curve_control.value_spin.setValue(15)
    grade = deepcopy(w.selected_clip().grade)
    w.set_workspace_mode('simple')
    assert 'advanced color' in hidden_settings_summary(w.project)
    w.grade_spins['contrast'].setValue(1.2); w.reset_grade()
    assert w.selected_clip().grade.hue_hue_curve == grade.hue_hue_curve
    w.set_workspace_mode('professional'); w.reset_grade()
    assert w.selected_clip().grade.hue_hue_curve == [[0., 0.], [1., 0.]]


def test_copy_grade_carries_both_selective_curves(window):
    w = window; w.selective_curve_control.value_spin.setValue(20)
    w.curve_choice.setCurrentIndex(5); w.selective_curve_control.value_spin.setValue(1.25)
    expected = deepcopy(w.selected_clip().grade)
    w.copy_selected_grade(); w.select_clip('target'); w.paste_selected_grade()
    assert w.selected_clip().grade == expected
    w.undo(); assert w.selected_clip().grade == Grade()


def test_editor_bounds_32_point_cap_and_noop(standalone):
    editor = standalone.editor; changes = []; editor.committed.connect(changes.append)
    assert not editor.add_point(0, 0) and not editor.add_point(1, 0)
    assert editor.add_point(.5, .2) and not editor.add_point(.5, .3)
    editor.edit_point(1, x=2, y=2)
    assert 0 < editor.points[1][0] < 1 and editor.points[1][1] == .5
    for _ in range(29): assert editor.add_point()
    assert len(editor.points) == 32 and not standalone.add_button.isEnabled()
    assert not editor.add_point()
    count = len(changes); editor.edit_point(0, y=editor.points[0][1]); editor.remove_point(0)
    assert len(changes) == count


@pytest.mark.parametrize('language', ['zh-CN', 'en'])
def test_compact_translation_is_live_and_does_not_change_staged_gesture(standalone, app, language):
    control = standalone; editor = control.editor
    drag(editor, [0., 0.], [.1, .2], release=False)
    points = deepcopy(editor.points)
    set_locale(language); app.processEvents()
    assert editor.dragging and editor.points == points
    expected = '色相偏移' if language == 'zh-CN' else 'Hue shift'
    assert control.value_label.text() == expected
    for widget in (control.point_choice, control.hue_spin, control.value_spin, control.add_button, control.remove_button):
        assert widget.geometry().right() < control.width()
    assert control.description.height() >= control.description.heightForWidth(control.description.width())
    assert control.hint.height() >= control.hint.heightForWidth(control.hint.width())
    QTest.keyClick(editor, Qt.Key.Key_Escape)
    assert editor.points == [[0., 0.], [1., 0.]]


def test_selective_errors_are_translated_without_losing_field_names():
    set_locale('zh-CN')
    assert tr_error(ValueError('Version 17 clips require both selective hue curves')) == '版本 17 的片段必须包含两条选择性色相曲线'
    assert tr_error(ValueError('Legacy projects cannot contain selective hue curves')) == '旧版项目不能包含选择性色相曲线'
    message = tr_error(ValueError('hue_hue_curve y endpoints must match for a periodic hue curve'))
    assert 'hue_hue_curve' in message and '首尾输出值' in message


@pytest.mark.parametrize('scale', ['1', '2'])
def test_compact_dpi_geometry_uses_actual_qt_scale(scale, tmp_path):
    code = '''
import json
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QTextLayout
from lumen.i18n import set_locale
from lumen.ui_selective_color import SelectiveCurveControl
app=QApplication([])
set_locale('zh-CN')
w=SelectiveCurveControl(); w.resize(264,450); w.show(); app.processEvents()
for c in (w.point_choice,w.hue_spin,w.value_spin,w.add_button,w.remove_button):
    assert c.geometry().right() < w.width()
image=w.grab().toImage()
print(json.dumps({'scale':w.devicePixelRatioF(),'width':w.width(),'image_width':image.width()}))
w.close()
'''
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR=scale)
    result = subprocess.run([sys.executable, '-c', code], env=env, cwd=Path(__file__).resolve().parents[1],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout.strip())
    assert evidence['scale'] == float(scale)
    assert evidence['image_width'] == evidence['width'] * int(scale)


def test_loaded_tightly_spaced_points_keep_strict_order_and_exact_y(standalone):
    editor = standalone.editor
    points = [[0., 0.], [.5, .123456789], [.50000001, -.2], [.50000002, .1], [1., 0.]]
    editor.set_points('hue_hue_curve', points)
    editor.edit_point(2, x=0)
    assert .5 < editor.points[2][0] < .50000002
    assert editor.points[2][1] == -.2
    editor.edit_point(2, x=1)
    assert .5 < editor.points[2][0] < .50000002
    assert all(a[0] < b[0] for a, b in zip(editor.points, editor.points[1:]))

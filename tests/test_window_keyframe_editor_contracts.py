"""Independent dialog integration contracts for the UI-only timing strip."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from uuid import uuid4

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lumen.grade_transfer import copy_grade, paste_grade
from lumen.i18n import set_locale
from lumen.model import Project, VERSION
from lumen.project_recovery import RecoveryStore
from lumen.qualifier import MAX_WINDOW_FRAME, MAX_WINDOW_KEYFRAMES, QualifierSettings, WindowKeyframe
from lumen.ui_window_animation import WindowAnimationDialog
from test_qualifier_ui import app, window
from test_window_animation_ui import animation_window, capture, loaded


def rich_settings():
    return QualifierSettings(
        enabled=True, hue_width=.5, exposure=.2345678912345,
        window_shape='ellipse', window_center_x=.1234567891234,
        window_center_y=.9876543219876, window_feather=.2765432198765,
        window_keyframes=[
            WindowKeyframe(-MAX_WINDOW_FRAME, .13, .17, .21, .27, 'hold'),
            WindowKeyframe(0, .2345678912345, .3456789123456, .4567891234567, .5678912345678, 'hold'),
            WindowKeyframe(2, .8765432198765, .7654321987654, .6543219876543, .5432198765432),
            WindowKeyframe(MAX_WINDOW_FRAME, .91, .81, .71, .61, 'hold'),
        ])


def prepare(window):
    window.selected_clip().grade.qualifier = rich_settings()
    return loaded(window)


def send_pointer(widget, kind, frame):
    point = QPointF(widget.frame_x(frame), widget.track_y())
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    held = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
    QApplication.sendEvent(widget, QMouseEvent(kind, point, widget.mapToGlobal(point.toPoint()),
        button, held, Qt.KeyboardModifier.NoModifier))


def test_ui_only_state_never_adds_schema_fields_or_mutates_project(animation_window):
    window = animation_window
    dialog = prepare(window)
    before = window.project.to_dict()
    assert before['version'] == VERSION
    assert set(before['clips'][0]['grade']['qualifier']) == set(QualifierSettings().to_dict())
    assert [dialog.key_selector.itemData(i) for i in range(dialog.key_selector.count())] == [None, 0, 2]
    assert str(-MAX_WINDOW_FRAME) in dialog.hidden_keys.text()
    assert str(MAX_WINDOW_FRAME) in dialog.hidden_keys.text()
    assert dialog.move_key(0, 1)
    assert dialog.local_frame == 1 and window.timeline.playhead == 0
    assert window.project.to_dict() == before and not window.editor.can_undo
    dialog.reject()
    assert window.project.to_dict() == before


@pytest.mark.parametrize('origin,destination', [(0, 1), (2, 1)])
def test_retime_changes_only_exact_frame_keeps_hidden_keys_and_manual_decode(animation_window, monkeypatch, origin, destination):
    import lumen.ui_window_animation as ui
    window = animation_window
    dialog = prepare(window)
    before = deepcopy(dialog.settings)
    project = deepcopy(window.project)
    monkeypatch.setattr(ui, 'capture_picker_snapshot', lambda *a, **k: pytest.fail('Retime launched a decode'))
    assert dialog.move_key(origin, destination)
    expected = replace(before, window_keyframes=sorted(
        [replace(key, frame=destination) if key.frame == origin else key for key in before.window_keyframes],
        key=lambda key: key.frame))
    assert dialog.settings == expected
    assert dialog.snapshot is None and dialog.canvas.image.isNull()
    assert dialog.key_selector.currentData() == destination and dialog.key_time.value() == destination
    assert not dialog.apply_button.isEnabled()
    assert window.project == project and not window.editor.can_undo


@pytest.mark.parametrize('origin,destination', [
    (0, 2), (0, -1), (0, 3), (-MAX_WINDOW_FRAME, 1), (MAX_WINDOW_FRAME, 1),
    (1, 0), (True, 1), (0, True), (0., 1), (0, 1.),
], ids=['collision', 'before-clip', 'after-clip', 'hidden-left', 'hidden-right',
        'missing-origin', 'boolean-origin', 'boolean-target', 'float-origin', 'float-target'])
def test_invalid_retime_is_atomic_and_cannot_replace_or_clamp_a_key(animation_window, origin, destination):
    window = animation_window
    dialog = prepare(window)
    before = deepcopy(dialog.settings)
    project, undo, redo = deepcopy((window.project, window.editor._undo, window.editor._redo))
    snapshot = dialog.snapshot
    assert not dialog.move_key(origin, destination)
    assert dialog.settings == before and dialog.snapshot is snapshot
    assert (window.project, window.editor._undo, window.editor._redo) == (project, undo, redo)


@pytest.mark.parametrize('mode', ['same-frame', 'move-and-return', 'click-without-motion'])
def test_timing_noop_apply_preserves_redo_revision_and_exact_serialized_values(animation_window, mode):
    window = animation_window
    window.selected_clip().grade.qualifier = rich_settings()
    window.editor.update_clip('picture', gain_db=-3)
    assert window.editor.undo() and window.editor.can_redo
    before = deepcopy((window.project, window.editor._undo, window.editor._redo))
    revision, editor_revision, dirty = window.revision, window.editor.revision, window.dirty
    dialog = loaded(window)
    if mode == 'same-frame':
        assert dialog.move_key(0, 0)
    elif mode == 'move-and-return':
        assert dialog.move_key(0, 1) and dialog.move_key(1, 0)
        capture(dialog)
    else:
        send_pointer(dialog.key_timeline, QEvent.Type.MouseButtonPress, 0)
        send_pointer(dialog.key_timeline, QEvent.Type.MouseButtonRelease, 0)
    assert dialog.settings == before[0].clips[0].grade.qualifier
    dialog.apply_settings()
    assert (window.project, window.editor._undo, window.editor._redo) == before
    assert (window.revision, window.editor.revision, window.dirty) == (revision, editor_revision, dirty)


def test_pointer_ghost_cannot_apply_until_release_then_one_transaction(animation_window):
    window = animation_window
    dialog = prepare(window)
    before = deepcopy(window.project)
    strip = dialog.key_timeline
    send_pointer(strip, QEvent.Type.MouseButtonPress, 0)
    send_pointer(strip, QEvent.Type.MouseMove, 1)
    assert strip.gesture_active and strip.preview_frame == 1
    assert dialog.settings == before.clips[0].grade.qualifier
    assert not dialog.apply_button.isEnabled()
    dialog.apply_settings()
    assert dialog.isVisible() and window.project == before
    send_pointer(strip, QEvent.Type.MouseButtonRelease, 1)
    assert not strip.gesture_active and dialog.local_frame == 1
    assert window.project == before and dialog.snapshot is None
    capture(dialog)
    pending = deepcopy(dialog.settings)
    dialog.apply_settings()
    assert window.selected_clip().grade.qualifier == pending and len(window.editor._undo) == 1
    window.undo()
    assert window.project == before
    window.redo()
    assert window.selected_clip().grade.qualifier == pending


@pytest.mark.parametrize('reason', ['escape-strip', 'escape-dialog', 'focus-out', 'deactivate', 'cancel', 'stale'])
def test_interrupted_pointer_preview_and_late_release_never_retime(animation_window, reason):
    window = animation_window
    dialog = prepare(window)
    before = deepcopy(dialog.settings)
    project = deepcopy(window.project)
    strip = dialog.key_timeline
    send_pointer(strip, QEvent.Type.MouseButtonPress, 0)
    send_pointer(strip, QEvent.Type.MouseMove, 1)
    assert strip.gesture_active
    if reason.startswith('escape'):
        QTest.keyClick(strip if reason == 'escape-strip' else dialog, Qt.Key.Key_Escape)
    elif reason == 'focus-out':
        QApplication.sendEvent(strip, QEvent(QEvent.Type.FocusOut))
    elif reason == 'deactivate':
        QApplication.sendEvent(dialog, QEvent(QEvent.Type.WindowDeactivate))
    elif reason == 'cancel':
        dialog.reject()
    else:
        window.revision += 1
        dialog.check_current()
    assert not strip.gesture_active
    send_pointer(strip, QEvent.Type.MouseButtonRelease, 1)
    assert dialog.settings == before and window.project == project and not window.editor.can_undo
    if reason not in ('cancel', 'stale'):
        assert dialog.isVisible()


def test_retimed_keys_save_recover_and_grade_copy_with_no_scaling_or_ui_state(animation_window, tmp_path):
    window = animation_window
    dialog = prepare(window)
    assert dialog.move_key(0, 1)
    capture(dialog)
    pending = deepcopy(dialog.settings)
    dialog.apply_settings()
    path = tmp_path / 'retimed.lumen.json'
    window.project.save(path)
    raw = json.loads(path.read_text())
    assert raw['version'] == VERSION
    assert raw['clips'][0]['grade']['qualifier'] == asdict(pending)
    assert Project.load(path) == window.project
    with RecoveryStore(tmp_path / 'independent-recovery') as store:
        entry = store.write(window.project, uuid4())
        restored = store.load(entry)
        assert restored == window.project
        restored.clips[0].grade.qualifier.window_keyframes.clear()
        assert store.load(entry) == window.project
    copied = copy_grade(window.project, 'picture')
    assert paste_grade(window.editor, 'other', copied)
    source, target = window.project.clips
    assert target.duration == 1 and target.grade.qualifier == pending
    assert target.grade.qualifier.window_keyframes is not source.grade.qualifier.window_keyframes
    assert [key.frame for key in target.grade.qualifier.window_keyframes] == [-MAX_WINDOW_FRAME, 1, 2, MAX_WINDOW_FRAME]


def test_maximum_key_count_can_retime_without_consuming_an_extra_slot(animation_window):
    window = animation_window
    clip = window.selected_clip()
    window.project.media[0].duration = 30
    clip.duration = 256
    window.project.clips[1].start = 300
    clip.grade.qualifier = QualifierSettings(window_keyframes=[WindowKeyframe(i) for i in range(MAX_WINDOW_KEYFRAMES)])
    dialog = WindowAnimationDialog(window)
    before = deepcopy(window.project)
    assert dialog.move_key(0, 255)
    assert len(dialog.settings.window_keyframes) == MAX_WINDOW_KEYFRAMES
    assert [key.frame for key in dialog.settings.window_keyframes] == [*range(1, 128), 255]
    assert window.project == before and not window.editor.can_undo


def test_numeric_destination_survives_preview_and_language_until_explicit_move(animation_window, app):
    window = animation_window
    dialog = prepare(window)
    before = deepcopy(window.project)
    dialog.select_key(0)
    dialog.key_time.setValue(1)
    for locale in ('zh-CN', 'en'):
        set_locale(locale)
        dialog.update_buttons(); dialog.render_preview(); app.processEvents()
        assert dialog.key_time.value() == 1 and dialog.key_selector.currentData() == 0
        assert dialog.settings == before.clips[0].grade.qualifier and window.project == before
    QTest.mouseClick(dialog.move_button, Qt.MouseButton.LeftButton)
    assert dialog.key_selector.currentData() == 1 and dialog.local_frame == 1
    assert [key.frame for key in dialog.settings.window_keyframes] == [-MAX_WINDOW_FRAME, 1, 2, MAX_WINDOW_FRAME]
    assert window.project == before and not window.editor.can_undo


def test_explicit_no_key_selection_does_not_navigate_or_retime(animation_window):
    window = animation_window
    dialog = prepare(window)
    dialog.select_key(2)
    before = deepcopy(dialog.settings)
    dialog.key_selector.setCurrentIndex(0)
    assert dialog.key_selector.currentData() is None
    assert dialog.key_timeline.selected_frame is None
    assert dialog.local_frame == 2 and not dialog.key_time.isEnabled() and not dialog.move_button.isEnabled()
    assert dialog.settings == before and not window.editor.can_undo

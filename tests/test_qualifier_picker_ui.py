"""Real Qt source-click workflow, independent sample math and transactions."""
import colorsys
from copy import deepcopy
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from lumen.grade_transfer import copy_grade, paste_grade
from lumen.model import VERSION, Editor, Project
from lumen.project_recovery import RecoveryStore
from lumen.qualifier import QualifierSettings
from lumen.ui_qualifier import QualifierDialog
from test_qualifier_ui import app, loaded, window


PRESERVED = ('enabled', 'hue_softness', 'saturation_softness', 'luma_softness',
             'exposure', 'saturation', 'hue_shift')


def click_sample(dialog, x, y):
    """Click a displayed pixel center in actual Qt logical coordinates."""
    rect = dialog.canvas.image_rect()
    width, height = dialog.canvas.image.width(), dialog.canvas.image.height()
    point = QPoint(int(rect.left() + (x + .5) * rect.width() / width),
                   int(rect.top() + (y + .5) * rect.height() / height))
    # Observe the real mouse signal rather than echoing the implementation's
    # hit-test formula. In these workflows each cell is larger than a logical
    # pixel, so its logical center addresses this cell unambiguously.
    picking = dialog.canvas.picking
    received = []
    receiver = lambda px, py: received.append((px, py))
    dialog.canvas.sample_clicked.connect(receiver)
    QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=point)
    dialog.canvas.sample_clicked.disconnect(receiver)
    assert received == ([(x, y)] if picking else [])


def arm(dialog):
    assert dialog.pick_button.isEnabled()
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
    assert dialog.pick_button.isChecked() and dialog.canvas.picking
    assert dialog.preview_choice.currentData() == 'source'
    assert not dialog.preview_choice.isEnabled()


def assert_sample_ranges(settings, rgb):
    # Independent colorsys conversion and published encoded BT.709 coefficients.
    hue, saturation, _ = colorsys.rgb_to_hsv(*map(float, rgb))
    luma = sum(float(v) * c for v, c in zip(rgb, (.2126, .7152, .0722)))
    assert settings.hue_center == pytest.approx(hue, abs=2e-7)
    assert settings.hue_width == pytest.approx(10 / 360, abs=1e-12)
    assert settings.saturation_low == pytest.approx(max(0, saturation - .1), abs=2e-7)
    assert settings.saturation_high == pytest.approx(min(1, saturation + .1), abs=2e-7)
    assert settings.luma_low == pytest.approx(max(0, luma - .1), abs=2e-7)
    assert settings.luma_high == pytest.approx(min(1, luma + .1), abs=2e-7)


def test_real_click_arms_source_then_shows_pending_matte_without_committing(window, app):
    original = QualifierSettings(enabled=True, hue_softness=.0123456789,
                                 saturation_softness=.0234567891, luma_softness=.0345678912,
                                 exposure=.2345678912, saturation=.876543219,
                                 hue_shift=.123456789)
    window.selected_clip().grade.qualifier = deepcopy(original)
    before = deepcopy(window.project)
    dialog = loaded(window); app.processEvents()
    assert dialog.pick_button.isCheckable()
    assert dialog.pick_button.accessibleName()
    assert not dialog.canvas.picking
    click_sample(dialog, 16, 16)
    assert dialog.settings == original
    dialog.preview_choice.setCurrentIndex(2)
    arm(dialog)
    rgb = dialog.snapshot.frame[32, 32].copy()
    click_sample(dialog, 16, 16)
    assert_sample_ranges(dialog.settings, rgb)
    for field in PRESERVED:
        assert getattr(dialog.settings, field) == getattr(original, field)
    assert not dialog.pick_button.isChecked() and not dialog.canvas.picking
    assert dialog.preview_choice.isEnabled() and dialog.preview_choice.currentData() == 'matte'
    assert dialog.canvas.sample_position == (16, 16)
    assert dialog.matte[16, 16] == 1
    assert 'Source (32, 32)' in dialog.sample_label.text()
    assert ', '.join(f'{float(v):.6f}' for v in rgb) in dialog.sample_label.text()
    assert all(label in dialog.sample_label.text() for label in ('RGB', 'H ', 'S ', 'Y′'))
    assert 'No project changes until Apply' in dialog.sample_label.text()
    assert window.project == before and not window.editor.can_undo


def test_rearm_replaces_not_adds_and_never_samples_adjustment_feedback(window, app):
    window.selected_clip().grade.qualifier = QualifierSettings(
        enabled=True, hue_shift=.15, exposure=.4, saturation=.35)
    dialog = loaded(window); app.processEvents()
    source = dialog.snapshot.frame.copy()
    arm(dialog); click_sample(dialog, 16, 16)
    first = deepcopy(dialog.settings)
    dialog.preview_choice.setCurrentIndex(2)
    arm(dialog); click_sample(dialog, 16, 16)
    assert dialog.settings == first  # Repeating the same source has no drift.
    arm(dialog); click_sample(dialog, (5 * 64 + 32) // 2, 16)
    assert_sample_ranges(dialog.settings, source[32, 5 * 64 + 32])
    assert dialog.settings.hue_center != first.hue_center
    assert dialog.matte[16, 16] == 0
    assert dialog.matte[16, (5 * 64 + 32) // 2] == 1
    np.testing.assert_array_equal(dialog.snapshot.frame, source)
    assert not window.editor.can_undo


def test_arming_can_be_canceled_and_unarmed_clicks_are_inert(window, app):
    dialog = loaded(window); app.processEvents()
    before = deepcopy(dialog.settings)
    arm(dialog)
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
    assert not dialog.canvas.picking and dialog.preview_choice.isEnabled()
    click_sample(dialog, 16, 16)
    dialog.pick_sample(16, 16)
    assert dialog.settings == before and dialog.canvas.sample_position is None
    assert not window.editor.can_undo


@pytest.mark.parametrize('channel', ['saturation', 'luma'])
@pytest.mark.parametrize('initially_armed', [False, True])
def test_inverted_ranges_disarm_and_clear_old_preview_until_repaired(
        window, app, channel, initially_armed):
    dialog = loaded(window); app.processEvents()
    if initially_armed:
        arm(dialog)
    before = deepcopy(window.project)
    dialog.controls[f'{channel}_low'].setValue(80)
    dialog.controls[f'{channel}_high'].setValue(20)
    invalid = deepcopy(dialog.settings)
    assert not dialog.pick_button.isEnabled() and not dialog.pick_button.isChecked()
    assert not dialog.canvas.picking and dialog.preview_choice.isEnabled()
    assert not dialog.apply_button.isEnabled()
    assert dialog.canvas.image.isNull() and dialog.matte is None
    assert 'minimum' in dialog.status.text().lower()
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
    dialog.pick_sample(16, 16)
    dialog.apply_settings()
    assert dialog.settings == invalid and window.project == before
    assert not window.editor.can_undo
    dialog.controls[f'{channel}_high'].setValue(90)
    dialog.render_preview(); app.processEvents()
    assert dialog.pick_button.isEnabled() and not dialog.canvas.image.isNull()
    arm(dialog); click_sample(dialog, 16, 16)
    assert_sample_ranges(dialog.settings, dialog.snapshot.frame[32, 32])
    assert window.project == before and not window.editor.can_undo


def test_repeat_pick_cancel_releases_state_and_leaves_no_transaction(window, app):
    before = deepcopy(window.project)
    for _ in range(3):
        dialog = loaded(window); app.processEvents()
        arm(dialog); click_sample(dialog, 16, 16)
        assert dialog.settings != window.selected_clip().grade.qualifier
        QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)
        assert not dialog.canvas.picking and not dialog.pick_button.isChecked()
        assert dialog.snapshot is None and dialog.canvas.sample_position is None
        assert dialog.canvas.image.isNull()
        assert window.project == before and not window.editor.can_undo


def test_coarse_display_click_reads_exact_full_float_grid_not_rgb8_or_neighbor(window, app):
    dialog = loaded(window)
    # Give each displayed sample more than one logical screen pixel so QTest's
    # integer QPoint can address this exact chosen location unambiguously.
    dialog.canvas.setMaximumHeight(400); dialog.canvas.setMinimumHeight(300)
    dialog.resize(900, 1000)
    frame = np.zeros((1080, 1920, 3), np.float32)
    frame[:] = [.7, .1, .1]
    x, y = 137, 120
    rgb = np.array([.12345678, .56789123, .31098765], np.float32)
    frame[y * 4, x * 4] = rgb
    frame[y * 4 + 1, x * 4 + 1] = [.1, .1, .9]
    frame.setflags(write=False)
    dialog.snapshot = replace(dialog.snapshot, frame=frame,
                              valid_mask=np.ones(frame.shape[:2], bool))
    dialog.render_preview(); app.processEvents()
    assert dialog.preview_stride == 4 and dialog.preview_shape == (270, 480)
    arm(dialog); click_sample(dialog, x, y)
    assert_sample_ranges(dialog.settings, rgb)
    quantized = np.rint(rgb * 255) / 255
    quantized_hue = colorsys.rgb_to_hsv(*map(float, quantized))[0]
    assert abs(dialog.settings.hue_center - quantized_hue) > 1e-5
    assert 'Source (548, 480)' in dialog.sample_label.text()
    assert dialog.snapshot.frame is frame and not frame.flags.writeable
    assert not window.editor.can_undo


@pytest.mark.parametrize('kind,rgb,valid', [
    ('gray', [.4, .4, .4], True), ('black', [0, 0, 0], True),
    ('negative', [-.001, .4, .8], True), ('overrange', [.2, .4, 1.001], True),
    ('padding', [.7, .1, .2], False),
])
def test_invalid_picture_sample_keeps_ranges_and_picker_armed(window, app, kind, rgb, valid):
    dialog = loaded(window)
    frame = np.array([[rgb, [.7, .1, .2], [.1, .7, .2]]], np.float32)
    dialog.snapshot = replace(dialog.snapshot, frame=frame,
                              valid_mask=np.array([[valid, True, True]]))
    dialog.render_preview(); app.processEvents()
    before = deepcopy(dialog.settings)
    arm(dialog); click_sample(dialog, 0, 0)
    assert dialog.settings == before
    assert dialog.pick_button.isChecked() and dialog.canvas.picking
    assert dialog.canvas.sample_position is None
    if kind == 'padding':
        assert 'padding' in dialog.sample_label.text().lower()
    else:
        assert 'click a displayed' not in dialog.sample_label.text().lower()
    assert not window.editor.can_undo
    # An invalid attempt does not require reopening the frozen source.
    click_sample(dialog, 1, 0)
    assert_sample_ranges(dialog.settings, frame[0, 1])
    assert not dialog.canvas.picking


def test_letterbox_and_out_of_bounds_clicks_do_not_select_clamped_pixels(window, app):
    dialog = loaded(window); app.processEvents()
    before = deepcopy(dialog.settings)
    arm(dialog)
    assert dialog.canvas.image_rect().top() > 1
    QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=QPoint(20, 0))
    h, w = dialog.preview_shape
    for x, y in [(-1, 0), (0, -1), (w, 0), (0, h)]:
        dialog.pick_sample(x, y)
    assert dialog.settings == before and dialog.canvas.picking
    assert dialog.canvas.sample_position is None and not window.editor.can_undo


def test_real_fitted_padding_is_rejected_even_when_primary_lift_makes_it_colored(window, app):
    window.project.height = 128
    window.selected_clip().grade.lift = [.1, .05, 0.]
    dialog = loaded(window); app.processEvents()
    assert not dialog.snapshot.valid_mask[0, 32]
    assert np.ptp(dialog.snapshot.frame[0, 32]) > .01
    before = deepcopy(dialog.settings)
    arm(dialog); click_sample(dialog, 16, 0)
    assert dialog.settings == before and dialog.canvas.picking
    assert 'padding' in dialog.sample_label.text().lower()
    click_sample(dialog, 16, 32)
    assert_sample_ranges(dialog.settings, dialog.snapshot.frame[64, 32])
    assert not dialog.canvas.picking and not window.editor.can_undo


@pytest.mark.parametrize('change', ['selection', 'revision', 'frame', 'curve', 'source',
                                    'grade', 'restoration', 'project', 'dimensions', 'closing'])
def test_stale_armed_click_cannot_replace_ranges_or_commit(window, change):
    dialog = loaded(window); arm(dialog)
    settings_before = deepcopy(dialog.settings)
    if change == 'selection': window.select_clip('other')
    elif change == 'revision': window.revision += 1
    elif change == 'frame': window.timeline.playhead += 1
    elif change == 'curve': window.curve_choice.setCurrentIndex((window.curve_choice.currentIndex() + 1) % 4)
    elif change == 'source':
        path = window.project.media[0].path
        info = os.stat(path)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000))
    elif change == 'grade': window.selected_clip().grade.exposure = .25
    elif change == 'restoration': window.selected_clip().restoration.sharpen = .2
    elif change == 'project': window.editor = Editor(deepcopy(window.project))
    elif change == 'dimensions': window.project.height += 2
    elif change == 'closing': window.closing = True
    before = deepcopy(window.project)
    dialog.pick_sample(16, 16)
    dialog.apply_settings()
    assert dialog.settings == settings_before and window.project == before
    assert not window.editor.can_undo
    assert not dialog.pick_button.isEnabled() and not dialog.canvas.picking
    assert not dialog.apply_button.isEnabled() and not dialog.canvas.isEnabled()
    assert 'stale' in dialog.status.text().lower()


def test_click_apply_is_one_undo_and_survives_save_recovery_and_grade_copy(window, app, tmp_path):
    before = deepcopy(window.project)
    dialog = loaded(window); app.processEvents()
    arm(dialog); click_sample(dialog, 16, 16)
    settings = deepcopy(dialog.settings)
    assert not settings.enabled  # Sampling does not silently activate the correction.
    QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
    after = deepcopy(window.project)
    assert window.selected_clip().grade.qualifier == settings
    assert len(window.editor._undo) == 1
    path = tmp_path / 'sampled.lumen.json'; window.project.save(path)
    saved = json.loads(path.read_text())
    assert saved['version'] == VERSION
    assert saved['clips'][0]['grade']['qualifier'] == asdict(settings)
    assert Project.load(path) == after
    with RecoveryStore(tmp_path / 'sampled-recovery') as store:
        entry = store.write(window.project, uuid4())
        recovered = store.load(entry)
        assert recovered == after
        recovered.clips[0].grade.qualifier.hue_center = .75
        assert store.load(entry) == after and window.project == after
    copied = copy_grade(window.project, 'picture')
    editor = Editor(deepcopy(window.project))
    assert paste_grade(editor, 'other', copied)
    assert editor.project.clips[1].grade.qualifier == settings
    assert len(editor._undo) == 1
    pasted = deepcopy(editor.project)
    assert not paste_grade(editor, 'other', copied)
    assert editor.undo() and editor.project == after
    assert editor.redo() and editor.project == pasted
    window.undo(); assert window.project == before
    window.redo(); assert window.project == after
    again = loaded(window); app.processEvents()
    assert again.settings == settings
    arm(again); click_sample(again, 16, 16)
    again.apply_settings()
    assert len(window.editor._undo) == 1 and window.project == after


@pytest.mark.parametrize('scale', ['1', '1.5', '2'])
def test_real_qt_dpi_click_mapping_uses_logical_not_device_pixels(scale):
    code = '''
import json
import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from lumen.ui_qualifier import QualifierCanvas
app = QApplication([])
canvas = QualifierCanvas(); canvas.resize(320, 220)
canvas.set_frame(np.zeros((4, 8, 3), np.float32))
canvas.picking = True; canvas.show(); app.processEvents()
received = []; canvas.sample_clicked.connect(lambda x, y: received.append([x, y]))
r = canvas.image_rect()
QTest.mouseClick(canvas, Qt.MouseButton.LeftButton,
    pos=QPoint(int(r.left() + 5.5 * r.width() / 8), int(r.top() + 2.5 * r.height() / 4)))
assert received == [[5, 2]], received
QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(10, 0))
assert received == [[5, 2]], received
canvas.picking = False
QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
assert received == [[5, 2]], received
print(json.dumps({'scale': canvas.devicePixelRatioF(), 'width': canvas.width(),
                  'image_width': canvas.grab().toImage().width()}))
canvas.close()
'''
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR=scale)
    result = subprocess.run([sys.executable, '-c', code], env=env,
                            cwd=Path(__file__).resolve().parents[1],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout.strip())
    assert evidence['scale'] == float(scale)
    assert evidence['image_width'] == evidence['width'] * float(scale)

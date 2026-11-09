"""Actual overlay inspection remains view-only and samples frozen source RGB."""
from copy import deepcopy
from dataclasses import asdict, replace
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
import weakref

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from lumen.model import Editor, Project
from lumen.project_recovery import RecoveryStore
from lumen.qualifier import QualifierSettings
from test_qualifier_picker_ui import PRESERVED, assert_sample_ranges, click_sample
from test_qualifier_ui import app, loaded, window


def show_overlay(dialog):
    assert [dialog.preview_choice.itemData(i) for i in range(4)] == [
        'matte', 'source', 'adjusted', 'overlay']
    dialog.preview_choice.setCurrentIndex(3)
    dialog.render_preview()


def arm_overlay(dialog):
    assert dialog.preview_choice.currentData() == 'overlay'
    assert dialog.pick_button.isEnabled()
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
    assert dialog.pick_button.isChecked() and dialog.canvas.picking
    assert dialog.preview_choice.currentData() == 'overlay'
    assert not dialog.preview_choice.isEnabled()


def overlay_reference(rgb, weight):
    """Independent scalar definition, with display quantization only at the end."""
    return tuple(round(255 * (min(1., max(0., float(c))) * (1 - .5 * weight)
                              + tint * .5 * weight))
                 for c, tint in zip(rgb, (1, 0, 1)))


@pytest.mark.parametrize('grade_enabled', [True, False])
def test_overlay_exact_selected_excluded_feather_and_padding_pixels(window, grade_enabled):
    window.selected_clip().grade.enabled = grade_enabled
    dialog = loaded(window)
    source = np.array([[[.5, 0, 0], [0, .5, 0], [.5, .375, 0],
                        [-.125, .3125, .25], [1.125, .25, .125]]], np.float32)
    source.setflags(write=False)
    # Membership deliberately includes fitted-canvas padding. Only picking
    # rejects padding; hiding its membership would disagree with rendering.
    dialog.accept_snapshot(replace(dialog.snapshot, frame=source,
                                   valid_mask=np.array([[False, True, True, True, True]])))
    dialog.settings = QualifierSettings(enabled=False, hue_width=.1,
                                        hue_softness=.05, exposure=1)
    before = deepcopy(dialog.settings); project = deepcopy(window.project)
    show_overlay(dialog)
    np.testing.assert_allclose(dialog.matte, [[1, 0, .5, 0, 0]], atol=1e-6)
    for x, weight in enumerate((1, 0, .5, 0, 0)):
        assert dialog.canvas.image.pixelColor(x, 0).getRgb()[:3] == overlay_reference(source[0, x], weight)
    for index in (0, 1, 2, 3, 0, 3):
        dialog.preview_choice.setCurrentIndex(index)
    assert dialog.settings == before and window.project == project
    assert dialog.snapshot.frame is source and not source.flags.writeable
    assert not window.editor.can_undo and not dialog.settings.enabled


def test_real_overlay_click_preserves_view_and_reads_source_not_magenta_or_adjustment(window, app):
    original = QualifierSettings(enabled=True, hue_width=.5, hue_softness=.0123456789,
                                 saturation_softness=.0234567891, luma_softness=.0345678912,
                                 exposure=.2345678912, saturation=.876543219,
                                 hue_shift=.123456789)
    window.selected_clip().grade.qualifier = deepcopy(original)
    before = deepcopy(window.project)
    dialog = loaded(window); show_overlay(dialog); app.processEvents()
    source = dialog.snapshot.frame.copy()
    rgb = source[32, 32]
    assert dialog.canvas.image.pixelColor(16, 16).getRgb()[:3] == overlay_reference(rgb, 1)
    assert dialog.canvas.image.pixelColor(16, 16).getRgb()[:3] != tuple(np.rint(rgb * 255).astype(int))
    arm_overlay(dialog); click_sample(dialog, 16, 16)
    assert_sample_ranges(dialog.settings, rgb)
    for field in PRESERVED:
        assert getattr(dialog.settings, field) == getattr(original, field)
    assert dialog.preview_choice.currentData() == 'overlay'
    assert dialog.preview_choice.isEnabled() and not dialog.canvas.picking
    assert dialog.sample_source_position == (32, 32) and dialog.matte[16, 16] == 1
    first = deepcopy(dialog.settings)
    arm_overlay(dialog); click_sample(dialog, 16, 16)
    assert dialog.settings == first  # No accumulated tint or adjustment feedback.
    arm_overlay(dialog); click_sample(dialog, 176, 16)
    assert_sample_ranges(dialog.settings, source[32, 352])
    assert dialog.matte[16, 16] == 0 and dialog.matte[16, 176] == 1
    np.testing.assert_array_equal(dialog.snapshot.frame, source)
    assert not dialog.snapshot.frame.flags.writeable
    assert window.project == before and not window.editor.can_undo


@pytest.mark.parametrize('kind,rgb,valid', [
    ('gray', [.4, .4, .4], True), ('black', [0, 0, 0], True),
    ('negative', [-.001, .4, .8], True), ('overrange', [.2, .4, 1.001], True),
    ('padding', [.7, .1, .2], False),
])
def test_overlay_invalid_samples_remain_armed_and_recover_on_valid_neighbor(window, app, kind, rgb, valid):
    dialog = loaded(window)
    source = np.array([[rgb, [.12345678, .56789123, .31098765]]], np.float32)
    source.setflags(write=False)
    dialog.accept_snapshot(replace(dialog.snapshot, frame=source,
                                   valid_mask=np.array([[valid, True]])))
    show_overlay(dialog); app.processEvents(); before = deepcopy(dialog.settings)
    arm_overlay(dialog); click_sample(dialog, 0, 0)
    assert dialog.settings == before and dialog.canvas.picking
    assert dialog.preview_choice.currentData() == 'overlay' and not dialog.preview_choice.isEnabled()
    assert dialog.canvas.sample_position is None
    if kind == 'padding':
        assert 'padding' in dialog.sample_label.text().lower()
    click_sample(dialog, 1, 0)
    assert_sample_ranges(dialog.settings, source[0, 1])
    assert dialog.preview_choice.currentData() == 'overlay'
    assert not dialog.canvas.picking and not window.editor.can_undo


def test_overlay_letterbox_out_of_bounds_and_manual_disarm_are_inert(window, app):
    dialog = loaded(window); show_overlay(dialog); app.processEvents()
    before = deepcopy(dialog.settings); arm_overlay(dialog)
    assert dialog.canvas.image_rect().top() > 1
    QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=QPoint(20, 0))
    height, width = dialog.preview_shape
    for point in ((-1, 0), (0, -1), (width, 0), (0, height)):
        dialog.pick_sample(*point)
    assert dialog.settings == before and dialog.canvas.picking
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
    click_sample(dialog, 16, 16); dialog.pick_sample(16, 16)
    assert dialog.settings == before and dialog.canvas.sample_position is None
    assert dialog.preview_choice.currentData() == 'overlay' and dialog.preview_choice.isEnabled()
    assert not window.editor.can_undo


@pytest.mark.parametrize('change', ['revision', 'selection', 'frame', 'grade', 'source', 'dimensions'])
def test_stale_overlay_cannot_sample_or_apply(window, change):
    dialog = loaded(window); show_overlay(dialog); arm_overlay(dialog)
    settings = deepcopy(dialog.settings)
    if change == 'revision': window.revision += 1
    elif change == 'selection': window.select_clip('other')
    elif change == 'frame': window.timeline.playhead += 1
    elif change == 'grade': window.selected_clip().grade.exposure = .3
    elif change == 'dimensions': window.project.height += 2
    elif change == 'source':
        path = window.project.media[0].path; info = os.stat(path)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000))
    before = deepcopy(window.project)
    dialog.pick_sample(16, 16); dialog.render_preview(); dialog.apply_settings()
    assert dialog.settings == settings and window.project == before
    assert not dialog.canvas.picking and not dialog.canvas.isEnabled()
    assert not dialog.pick_button.isEnabled() and not dialog.apply_button.isEnabled()
    assert not window.editor.can_undo and 'stale' in dialog.status.text().lower()


@pytest.mark.parametrize('channel', ['saturation', 'luma'])
def test_invalid_overlay_ranges_clear_display_and_disarm_until_repaired(window, app, channel):
    dialog = loaded(window); show_overlay(dialog); arm_overlay(dialog)
    before = deepcopy(window.project)
    dialog.controls[channel + '_low'].setValue(80)
    dialog.controls[channel + '_high'].setValue(20)
    assert dialog.canvas.image.isNull() and dialog.matte is None
    assert not dialog.canvas.picking and not dialog.apply_button.isEnabled()
    assert not dialog.pick_button.isEnabled() and dialog.preview_choice.isEnabled()
    invalid = deepcopy(dialog.settings)
    dialog.pick_sample(16, 16); dialog.apply_settings()
    assert dialog.settings == invalid and window.project == before
    dialog.controls[channel + '_high'].setValue(90)
    dialog.render_preview(); app.processEvents()
    arm_overlay(dialog); click_sample(dialog, 16, 16)
    assert_sample_ranges(dialog.settings, dialog.snapshot.frame[32, 32])
    assert not window.editor.can_undo


def test_cancel_overlay_releases_source_display_matte_and_pending_changes(window, app):
    before = deepcopy(window.project)
    dialog = loaded(window); show_overlay(dialog); app.processEvents()
    reference = weakref.ref(dialog.snapshot.frame)
    arm_overlay(dialog); click_sample(dialog, 16, 16)
    dialog.zoom_choice.setCurrentIndex(4); dialog.pan_view(20, 10)
    QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)
    app.processEvents(); gc.collect()
    assert reference() is None
    assert dialog.snapshot is None and dialog.matte is None and dialog.viewport is None
    assert dialog.canvas.image.isNull() and dialog.canvas._pixels is None
    assert dialog.canvas._raster_cache is None and dialog.sample_source_position is None
    assert window.project == before and not window.editor.can_undo


def test_overlay_view_state_never_persists_and_sample_apply_has_one_transaction(window, app, tmp_path):
    before = deepcopy(window.project)
    dialog = loaded(window); show_overlay(dialog); app.processEvents()
    arm_overlay(dialog); click_sample(dialog, 16, 16)
    expected = deepcopy(dialog.settings)
    for mode in (2, 3, 1, 0, 3):
        dialog.preview_choice.setCurrentIndex(mode)
        dialog.zoom_choice.setCurrentIndex(2); dialog.pan_view(11, -7)
        assert dialog.settings == expected
    dialog.apply_settings()
    after = deepcopy(window.project)
    assert len(window.editor._undo) == 1 and window.selected_clip().grade.qualifier == expected
    path = tmp_path / 'overlay-inspected.lumen.json'; window.project.save(path)
    assert json.loads(path.read_text())['clips'][0]['grade']['qualifier'] == asdict(expected)
    assert Project.load(path) == after
    with RecoveryStore(tmp_path / 'overlay-recovery') as store:
        assert store.load(store.write(window.project, uuid4())) == after
    window.undo(); assert window.project == before
    window.redo(); assert window.project == after
    again = loaded(window)
    assert again.settings == expected and again.preview_choice.currentData() == 'matte'
    show_overlay(again); again.zoom_choice.setCurrentIndex(4); again.apply_settings()
    assert window.project == after and len(window.editor._undo) == 1


def test_real_rgb16_single_pixel_in_detail_is_sampled_under_overlay_without_redecode(window, app, tmp_path, monkeypatch):
    from test_qualifier_render import source_project
    height, width = 1080, 1920
    image = np.empty((height, width, 3), '<u2'); image[:] = [13003, 29009, 47017]
    x, y = 997, 541
    target = np.array([48013, 11027, 7019], '<u2'); image[y, x] = target
    project, _ = source_project(tmp_path, frames=image[None], name='overlay-tiny-target')
    window.editor = Editor(project); window.refresh(); window.select_clip('picture')
    before = deepcopy(window.project)
    dialog = loaded(window); show_overlay(dialog)
    frozen = dialog.snapshot
    exact = target.astype(np.float32) / np.float32(65535)
    np.testing.assert_array_equal(frozen.frame[y, x], exact)
    assert dialog.viewport.grid_at(x, y) is None
    assert np.max(abs(exact - np.rint(exact * 255) / 255)) > 1e-4
    monkeypatch.setattr('lumen.ui_qualifier.capture_picker_snapshot',
                        lambda *a, **k: pytest.fail('Overlay detail must reuse its frozen RGB16 snapshot'))
    dialog.zoom_choice.setCurrentIndex(4); dialog.set_detail_center(x, y); app.processEvents()
    grid = dialog.viewport.grid_at(x, y)
    assert grid is not None and dialog.viewport.source_at(*grid) == (x, y)
    arm_overlay(dialog); click_sample(dialog, *grid)
    assert_sample_ranges(dialog.settings, exact)
    assert dialog.matte[grid[1], grid[0]] == 1
    assert dialog.canvas.image.pixelColor(*grid).getRgb()[:3] == overlay_reference(exact, 1)
    assert dialog.sample_source_position == (x, y)
    assert dialog.snapshot is frozen and not frozen.frame.flags.writeable
    assert window.project == before and not window.editor.can_undo


def _exercise_overlay_raster():
    """Fresh Qt scale context, actual dialog slots and physical display pixels."""
    import math
    from tempfile import TemporaryDirectory
    from PySide6.QtCore import QEvent, QPointF, QSettings
    from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QMouseEvent
    from PySide6.QtWidgets import QApplication
    from lumen.ui import MainWindow
    from scripts.qt_test_cleanup import cleanup_test_windows
    from test_selective_color_render import source_project

    application = QApplication.instance() or QApplication([])
    # A subprocess cannot inherit the session fixture's native font handles.
    # Reuse its verified local fixture on fontless Windows offscreen runners.
    if os.environ.get('LUMEN_TEST_CJK_FONT_DIR'):
        from scripts.provision_test_font import verify_fixture
        verified = verify_fixture(os.environ['LUMEN_TEST_CJK_FONT_DIR'])
        font_id = QFontDatabase.addApplicationFont(verified['font_file'])
        assert font_id >= 0
        families = QFontDatabase.applicationFontFamilies(font_id)
        assert families
        info = QFontInfo(application.font())
        if not info.family() or info.pixelSize() <= 0:
            font = QFont(application.font()); font.setFamilies(families)
            application.setFont(font)
    with TemporaryDirectory() as directory:
        path = Path(directory)
        main = MainWindow(workspace_settings=QSettings(str(path / 'settings.ini'), QSettings.Format.IniFormat),
                          recovery_root=path / 'recovery', ui_language='en')
        main.editor = Editor(source_project(path)); main.refresh(); main.select_clip('picture')
        main.show(); application.processEvents()
        dialog = loaded(main)
        height, width = 801, 1027
        yy, xx = np.mgrid[:height, :width]
        source = np.stack((.10231 + .6 * xx / (width - 1),
                           .15317 + .5 * yy / (height - 1),
                           .75113 - .2 * xx / (width - 1)), axis=-1).astype(np.float32)
        source.setflags(write=False)
        dialog.accept_snapshot(replace(dialog.snapshot, frame=source,
                                       valid_mask=np.ones((height, width), bool)))
        base = QualifierSettings(enabled=True, hue_width=.5, exposure=.3, hue_shift=.1)
        before = deepcopy(main.project); received = []
        dialog.canvas.sample_clicked.connect(lambda x, y: received.append((x, y)))
        tested = 0
        try:
            for zoom, center in ((0, (513, 401)), (4, (513, 401)), (4, (0, 0)), (4, (1026, 800))):
                dialog.zoom_choice.setCurrentIndex(zoom); dialog.set_detail_center(*center)
                for size in ((360, 700), (900, 900)):
                    dialog.resize(*size); application.processEvents()
                    rect = dialog.canvas.image_rect(); dpr = dialog.canvas.devicePixelRatioF()
                    left = math.ceil(rect.left() * dpr); top = math.ceil(rect.top() * dpr)
                    right = math.floor(rect.right() * dpr + 1e-8) - 1
                    bottom = math.floor(rect.bottom() * dpr + 1e-8) - 1
                    for fx, fy in ((.1, .2), (.5, .5), (.9, .8)):
                        px = round(left + fx * (right - left)); py = round(top + fy * (bottom - top))
                        for fraction in (.05, .5, .95):
                            dialog.settings = deepcopy(base); dialog.sample_source_position = None
                            show_overlay(dialog); arm_overlay(dialog); application.processEvents()
                            grabbed = dialog.canvas.grab().toImage()
                            received.clear()
                            event = QMouseEvent(QEvent.Type.MouseButtonPress,
                                                QPointF((px + fraction) / dpr, (py + fraction) / dpr),
                                                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                                Qt.KeyboardModifier.NoModifier)
                            QApplication.sendEvent(dialog.canvas, event)
                            assert len(received) == 1, received
                            coordinate = dialog.viewport.source_at(*received[0])
                            sx, sy = coordinate; rgb = source[sy, sx]
                            assert grabbed.pixelColor(px, py).getRgb()[:3] == overlay_reference(rgb, 1), (zoom, size, dpr, coordinate)
                            assert_sample_ranges(dialog.settings, rgb)
                            assert dialog.sample_source_position == coordinate
                            assert dialog.preview_choice.currentData() == 'overlay'
                            assert not dialog.canvas.picking and dialog.preview_choice.isEnabled()
                            tested += 1
            assert main.project == before and not main.editor.can_undo
            assert dialog.snapshot.frame is source and not source.flags.writeable
            print(json.dumps({'dpr': dialog.canvas.devicePixelRatioF(), 'verified_clicks': tested,
                              'view_count': 4}))
        finally:
            dialog.reject(); main.dirty = False
            cleanup_test_windows(application)


@pytest.mark.parametrize('scale', ['1', '1.25', '1.5', '2'])
def test_overlay_physical_pixels_pick_underlying_source_at_fractional_dpr(scale):
    code = "import runpy, sys; sys.path.insert(0, 'tests'); runpy.run_path(sys.argv[1])['_exercise_overlay_raster']()"
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR=scale)
    result = subprocess.run([sys.executable, '-c', code, str(Path(__file__).resolve())],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.strip())
    assert evidence == {'dpr': float(scale), 'verified_clicks': 72, 'view_count': 4}

"""Real handle gestures must match numeric geometry in decoded deliveries.

Only synthetic RGB16 media is used. Expected pixels come from the independent
scalar color and analytic fitted-canvas spatial references, not handle math,
production masks, display pixels, or cached preview files.
"""
from copy import deepcopy
from dataclasses import asdict, replace
import json

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from lumen.clip_transform import ClipTransform
from lumen.media import ffmpeg_available
from lumen.model import Editor, Project
from lumen.render import RenderEngine
from test_color_highbit_reference import video_metadata
from test_color_picker_restoration import primary_reference
from test_color_picker_restoration_render import (decode_yuv, subsample_reference,
                                                  yuv_reference)
from test_qualifier_picker_ui import click_sample
from test_qualifier_render import decode_rgb, ff, mask_reference
from test_qualifier_ui import app, loaded, window
from test_static_window_render import (combined_reference, spatial_reference,
                                       uniform_source, window_settings)

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def attach_project(window, project):
    window.editor = Editor(project)
    window.refresh()
    window.select_clip('picture')


def ready_handles(dialog, app):
    # An exact 2x overview fit gives integer logical mouse displacements and
    # independently known binary-fraction geometry, without consulting the
    # production WindowMapping or WindowDrag calculations.
    dialog.canvas.setFixedSize(320, 192)
    dialog.zoom_choice.setCurrentIndex(dialog.zoom_choice.findData(0))
    dialog.render_preview()
    app.processEvents()
    if not dialog.window_edit_button.isChecked():
        QTest.mouseClick(dialog.window_edit_button, Qt.MouseButton.LeftButton)
    assert dialog.canvas.window_editing
    assert dialog.canvas.image_rect().width() == 320
    assert dialog.canvas.image_rect().height() == 192
    app.processEvents()


def drag(dialog, app, handle, dx, dy, *, cancel=False):
    canvas = dialog.canvas
    start = canvas.window_handles()[handle].toPoint()
    end = start + QPoint(dx, dy)
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
    assert canvas.window_drag_active
    QTest.mouseMove(canvas, end, delay=5)
    app.processEvents()
    if cancel:
        QTest.keyClick(canvas, Qt.Key.Key_Escape)
        assert dialog.isVisible() and not canvas.window_drag_active
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)
    app.processEvents()
    assert not canvas.window_drag_active


def fresh_delivery(project, tmp_path, stem, delivery):
    """New engine and a full raw decode on EVERY call: no cache-only parity."""
    renderer = RenderEngine(timeout=90)
    ten = delivery == 'prores_422_hq_10bit'
    try:
        path = (renderer.preview(project) if not ten else renderer.render(
            project, tmp_path / (stem + '.mov'), export_profile=delivery))
        metadata = video_metadata(path)
        assert metadata['codec_name'] == ('prores' if ten else 'h264')
        assert metadata['pix_fmt'] == ('yuv422p10le' if ten else 'yuv420p')
        raw = ff('-i', path, '-map', '0:V:0', '-an', '-pix_fmt',
                 'yuv422p10le' if ten else 'yuv420p', '-f', 'rawvideo', 'pipe:1')
        planes = decode_yuv(path, project.width, project.height, ten)
        return raw, planes
    finally:
        renderer.close()


def assert_delivery_reference(planes, expected, ten):
    reference = yuv_reference(np.repeat(expected[None], 3, axis=0), ten)
    for channel, plane in enumerate(planes):
        target = (reference[..., channel][..., ::2] if ten and channel
                  else subsample_reference(reference, channel, ten))
        error = abs(plane[:, 4:-4, 4:-4] - target[:, 4:-4, 4:-4])
        assert error.mean() < 1.3, (ten, channel, error.mean())
        assert np.quantile(error, .99) < (3.5 if ten else 4.5), (ten, channel, error.max())


@pytest.mark.parametrize('delivery', ['preview', 'prores_422_hq_10bit'])
def test_handles_views_navigation_cancel_and_noop_are_decode_inert(
        window, app, tmp_path, monkeypatch, delivery):
    project, _ = uniform_source(tmp_path, window_settings())
    attach_project(window, project)
    original_project = deepcopy(project)
    baseline, _ = fresh_delivery(project, tmp_path, 'baseline', delivery)

    dialog = loaded(window)
    dialog.canvas.setFixedSize(320, 192)
    app.processEvents()
    # A real prior picker click gives the paint-only checks a real source sample
    # marker, not merely an untouched None. This change remains dialog-local.
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
    click_sample(dialog, 60, 48)
    pending = deepcopy(dialog.settings)
    source_object = dialog.snapshot.frame
    source = source_object.copy()
    sample = dialog.sample_source_position
    assert sample is not None and not source_object.flags.writeable
    full_matte = (mask_reference(source, replace(pending, enabled=True))
                  * spatial_reference(project.width, project.height, pending))

    for mode in ('matte', 'source', 'adjusted', 'overlay'):
        dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData(mode))
        dialog.render_preview()
        image = dialog.canvas.image.copy()
        pixels = dialog.canvas._pixels.copy()
        matte = dialog.matte.copy()
        marker = dialog.canvas.sample_position
        for _ in range(2):
            QTest.mouseClick(dialog.window_edit_button, Qt.MouseButton.LeftButton)
            app.processEvents()
            # Painting handles must not be baked into the sampled/display frame
            # or range matte, regardless of the selected inspection view.
            assert dialog.canvas.image == image
            np.testing.assert_array_equal(dialog.canvas._pixels, pixels)
            np.testing.assert_array_equal(dialog.matte, matte)
            assert dialog.canvas.sample_position == marker
            assert dialog.sample_source_position == sample
            assert dialog.settings == pending
        assert dialog.snapshot.frame is source_object
        np.testing.assert_array_equal(source_object, source)
        assert window.project == original_project and not window.editor.can_undo

    ready_handles(dialog, app)
    for zoom, x, y in ((2, 64, 44), (4, 100, 58), (8, 30, 30)):
        dialog.zoom_choice.setCurrentIndex(dialog.zoom_choice.findData(zoom))
        dialog.set_detail_center(x, y)
        dialog.pan_view(3, -2)
        dialog.render_preview()
        np.testing.assert_allclose(dialog.matte, dialog.viewport.extract(full_matte),
                                   atol=3e-7, rtol=0)
        assert dialog.settings == pending and dialog.sample_source_position == sample
        assert dialog.snapshot.frame is source_object
        np.testing.assert_array_equal(source_object, source)
    ready_handles(dialog, app)
    drag(dialog, app, 'center', 30, 16, cancel=True)
    assert dialog.settings == pending
    drag(dialog, app, 'right', 20, 0)
    assert dialog.settings.window_width != pending.window_width
    assert window.project == original_project and not window.editor.can_undo
    dialog.reject()  # Completed but unapplied geometry must also be discarded.
    assert window.project == original_project and not window.editor.can_undo

    noop = loaded(window)
    ready_handles(noop, app)
    for handle in ('center', 'left', 'right', 'top', 'bottom'):
        drag(noop, app, handle, 0, 0)
        assert noop.settings == original_project.clips[0].grade.qualifier
    noop.apply_settings()
    assert window.project == original_project and not window.editor.can_undo

    # Delivery must never call display-only overlay/handle routines. Fresh
    # engines prevent a UI leak from being hidden by the earlier render cache.
    def forbidden(*args, **kwargs):
        pytest.fail('Paint-only handles or selection tint entered delivery')

    monkeypatch.setattr('lumen.ui_qualifier.QualifierCanvas.paintEvent', forbidden)
    monkeypatch.setattr('lumen.qualifier_overlay.selection_overlay', forbidden)
    after, _ = fresh_delivery(window.project, tmp_path, 'after-inspection', delivery)
    assert after == baseline
    assert window.project == original_project and not window.editor.can_undo


@pytest.mark.parametrize('shape,invert', [('ellipse', False), ('rectangle', True)])
def test_real_drag_matches_numeric_persistence_rgb16_preview_and_prores_before_mirror(
        window, app, tmp_path, shape, invert):
    settings = window_settings(window_shape=shape, window_invert=invert,
        window_center_x=.25, window_center_y=.5, window_width=.25,
        window_height=.5, window_feather=.375, saturation_low=.7,
        saturation_high=.95, saturation_softness=.3, exposure=.6)
    project, frames = uniform_source(tmp_path, settings, width=160, height=48)
    project.height = 96
    grade = project.clips[0].grade
    grade.red_curve = grade.green_curve = grade.blue_curve = [[0., .1], [1., .9]]
    project.clips[0].transform = ClipTransform(mirror_x=True)
    attach_project(window, project)
    original = deepcopy(project)

    dialog = loaded(window)
    assert dialog.snapshot.active_rect == (0, 24, 160, 48)
    canvas = np.zeros((96, 160, 3), float)
    canvas[24:72] = frames[0].astype(float) / 65535
    incoming = primary_reference(canvas, grade)
    np.testing.assert_allclose(dialog.snapshot.frame, incoming, atol=2e-7, rtol=0)
    frozen = dialog.snapshot.frame.copy()
    ready_handles(dialog, app)
    drag(dialog, app, 'center', 40, -24)
    drag(dialog, app, 'right', 20, 0)
    drag(dialog, app, 'bottom', 0, 12)
    expected_settings = replace(settings, window_center_x=.375,
        window_center_y=.375, window_width=.375, window_height=.625)
    # These exact values are independently calculated from the fixed 320x192
    # fitted canvas. No production mapping/helper supplies the expected result.
    assert dialog.settings == expected_settings
    np.testing.assert_array_equal(dialog.snapshot.frame, frozen)
    color = mask_reference(incoming, expected_settings)
    spatial = spatial_reference(160, 96, expected_settings)
    assert np.any((color > .1) & (color < .9))
    assert np.any((spatial > .1) & (spatial < .9))
    np.testing.assert_allclose(dialog.matte, color * spatial, atol=4e-7, rtol=0)
    assert window.project == original and not window.editor.can_undo
    dialog.apply_settings()
    assert len(window.editor._undo) == 1
    dragged = deepcopy(window.project)
    assert dragged.clips[0].grade.qualifier == expected_settings
    path = tmp_path / 'dragged-window.lumen'
    dragged.save(path)
    assert json.loads(path.read_text())['clips'][0]['grade']['qualifier'] == asdict(expected_settings)
    assert Project.load(path) == dragged

    # An entirely separate numeric-control transaction must persist exactly the
    # same qualifier and project, not merely a visually close frame.
    attach_project(window, deepcopy(original))
    numeric = loaded(window)
    for field, value in (('window_center_x', 37.5), ('window_center_y', 37.5),
                         ('window_width', 37.5), ('window_height', 62.5)):
        numeric.controls[field].setValue(value)
    assert numeric.settings == expected_settings
    numeric.apply_settings()
    assert window.project == dragged and len(window.editor._undo) == 1

    expected_stage = combined_reference(incoming, expected_settings)
    expected_output = expected_stage[:, ::-1]  # Clip mirror follows qualifier.
    baseline_output = combined_reference(incoming, settings)[:, ::-1]
    assert np.max(abs(expected_output - baseline_output)) > .03
    assert np.max(abs(expected_output - expected_stage)) > .03
    stage_renderer = RenderEngine(timeout=90)
    try:
        stage = stage_renderer._graded_segment(dragged.media[0], dragged.clips[0], dragged, None)
        np.testing.assert_allclose(decode_rgb(stage, 160, 96), expected_stage,
                                   atol=3 / 65535, rtol=0)
    finally:
        stage_renderer.close()
    for delivery in ('preview', 'prores_422_hq_10bit'):
        before, _ = fresh_delivery(original, tmp_path, 'before-' + delivery, delivery)
        dragged_bytes, planes = fresh_delivery(dragged, tmp_path, 'drag-' + delivery, delivery)
        numeric_bytes, _ = fresh_delivery(window.project, tmp_path, 'numeric-' + delivery, delivery)
        assert dragged_bytes != before
        assert dragged_bytes == numeric_bytes
        assert_delivery_reference(planes, expected_output, delivery.startswith('prores'))

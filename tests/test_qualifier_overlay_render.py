"""Inspection tint must never enter persisted grades or decoded deliveries."""
from copy import deepcopy

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from lumen.media import ffmpeg_available
from lumen.model import Editor, Grade
from lumen.render import RenderEngine
from test_qualifier_overlay_ui import arm_overlay, show_overlay
from test_qualifier_render import (decode_yuv_patches, engine, ff, patch_values,
                                   qualified_settings, qualifier_reference,
                                   source_project, yuv_reference)
from test_qualifier_ui import app, loaded, window


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


@pytest.mark.parametrize('delivery', ['preview', 'prores_422_hq_10bit'])
def test_overlay_inspection_does_not_change_decoded_preview_or_prores_bytes(
        window, app, tmp_path, engine, delivery, monkeypatch):
    settings = qualified_settings()
    project, _ = source_project(tmp_path, grade=Grade(qualifier=settings), name='overlay-delivery')
    window.editor = Editor(project); window.refresh(); window.select_clip('picture')
    before = deepcopy(window.project)
    ten_bit = delivery == 'prores_422_hq_10bit'

    def deliver(renderer, name):
        return (renderer.preview(window.project) if not ten_bit else renderer.render(
            window.project, tmp_path / (name + '.mov'), export_profile=delivery))

    baseline = deliver(engine, 'before-overlay')
    fmt = 'yuv422p10le' if ten_bit else 'yuv420p'
    before_pixels = ff('-i', baseline, '-pix_fmt', fmt, '-f', 'rawvideo', 'pipe:1')
    incoming = patch_values().astype(float) / 65535
    expected = qualifier_reference(incoming, settings)
    assert np.max(abs(expected - incoming)) > .05  # Real non-neutral correction.
    np.testing.assert_allclose(decode_yuv_patches(baseline, project.width, ten_bit),
                               yuv_reference(expected, ten_bit), atol=2, rtol=0)

    dialog = loaded(window)
    original = deepcopy(dialog.settings)
    for index in (3, 0, 1, 2, 3):
        dialog.preview_choice.setCurrentIndex(index)
        dialog.render_preview()
        dialog.zoom_choice.setCurrentIndex(4)
        dialog.set_detail_center(777, 32)
        dialog.pan_view(-13, 0)
        assert dialog.settings == original
    show_overlay(dialog); app.processEvents(); arm_overlay(dialog)
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
    dialog.apply_settings()  # View-only Apply is a no-op, not a grade transaction.
    assert window.project == before and not window.editor.can_undo

    # A new renderer forces a genuinely independent run rather than reusing the
    # first renderer's cached preview. Rendering must not call the UI tint code.
    def forbidden(*args, **kwargs):
        pytest.fail('Display-only selection tint entered the render pipeline')

    monkeypatch.setattr('lumen.qualifier_overlay.selection_overlay', forbidden)
    with_renderer = RenderEngine(timeout=90)
    try:
        after = deliver(with_renderer, 'after-overlay')
        after_pixels = ff('-i', after, '-pix_fmt', fmt, '-f', 'rawvideo', 'pipe:1')
        assert after_pixels == before_pixels
        np.testing.assert_allclose(decode_yuv_patches(after, project.width, ten_bit),
                                   yuv_reference(expected, ten_bit), atol=2, rtol=0)
    finally:
        with_renderer.close()
    assert window.project == before and not window.editor.can_undo

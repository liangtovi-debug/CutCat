"""Numerical scope contracts and offscreen UI; not physical display validation."""
from __future__ import annotations

import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from lumen.color_scopes import (
    CODE_BINS, SAMPLE_SIDE, VECTOR_BINS, Scopes, analyze_qimage, analyze_rgb,
    rec709_ycbcr, rgb_from_qimage, sample_rgb, vector_targets,
    _rebin_counts,
)
from lumen.i18n import get_locale, set_locale


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def qimage(rgb):
    rgb = np.ascontiguousarray(rgb)
    return QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.shape[1] * 3,
                  QImage.Format.Format_RGB888).copy()


def test_parade_contains_separate_rgb_codes_and_preserves_horizontal_position():
    rgb = np.array([[[255, 0, 16], [0, 128, 235]],
                    [[255, 64, 16], [255, 128, 255]]], dtype=np.uint8)
    data = analyze_rgb(rgb)
    assert data.source_size == data.sampled_size == (2, 2)
    assert data.parade.shape == (3, CODE_BINS, 2)
    assert data.parade[0, 0, 0] == 2
    assert data.parade[0, 255, 1] == 1
    assert data.parade[0, 0, 1] == 1
    assert data.parade[1, 127, 1] == 2
    assert data.parade[1, 191, 0] == 1
    assert data.parade[2, 239, 0] == 2
    assert data.parade[2, 20, 1] == 1
    assert data.parade[2, 0, 1] == 1
    np.testing.assert_array_equal(data.parade.sum(axis=1), [[2, 2]] * 3)
    np.testing.assert_array_equal(data.histogram.sum(axis=1), [4] * 3)
    assert data.waveform.sum() == data.vectorscope.sum() == data.sample_count == 4


@pytest.mark.parametrize('code', [0, 16, 64, 128, 235, 255])
def test_neutral_rgb_stays_centered_and_not_video_range_expanded(code):
    rgb = np.full((3, 7, 3), code, dtype=np.uint8)
    data = analyze_rgb(rgb)
    assert data.vectorscope[VECTOR_BINS // 2, VECTOR_BINS // 2] == 21
    assert np.count_nonzero(data.vectorscope) == 1
    np.testing.assert_array_equal(data.histogram[:, code], [21] * 3)
    np.testing.assert_array_equal(data.parade[:, 255 - code, :], np.full((3, 7), 3))
    np.testing.assert_array_equal(data.waveform[255 - code], np.full(7, 3))
    y, cb, cr = rec709_ycbcr(rgb)
    np.testing.assert_allclose(y, code / 255, atol=1e-15)
    np.testing.assert_allclose(cb, 0., atol=1e-15)
    np.testing.assert_allclose(cr, 0., atol=1e-15)


# Independently calculated from BT.709 Kr=0.2126, Kb=0.0722. In particular,
# pure red must NOT use the BT.601 Cb≈-0.168736 / Y'=0.299 coordinates.
REFERENCE = {
    'R': ((255, 0, 0), .2126, -.11457210605733995, .5),
    'Y': ((255, 255, 0), .9278, -.5, .04584709169418339),
    'G': ((0, 255, 0), .7152, -.38542789394266006, -.4541529083058166),
    'C': ((0, 255, 255), .7874, .11457210605733995, -.5),
    'B': ((0, 0, 255), .0722, .5, -.04584709169418339),
    'M': ((255, 0, 255), .2848, .38542789394266006, .4541529083058166),
}


@pytest.mark.parametrize('name', list(REFERENCE))
def test_rec709_primary_secondary_math_and_vector_bin(name):
    color, expected_y, expected_cb, expected_cr = REFERENCE[name]
    rgb = np.array([[color]], dtype=np.uint8)
    y, cb, cr = rec709_ycbcr(rgb)
    assert y.item() == pytest.approx(expected_y, abs=1e-14)
    assert cb.item() == pytest.approx(expected_cb, abs=1e-14)
    assert cr.item() == pytest.approx(expected_cr, abs=1e-14)
    data = analyze_rgb(rgb)
    expected_x = round((expected_cb + .5) * 256)
    expected_row = round((.5 - expected_cr) * 256)
    assert data.vectorscope[expected_row, expected_x] == 1
    assert data.vectorscope.sum() == 1
    assert data.waveform[255 - round(expected_y * 255), 0] == 1
    assert vector_targets()[name] == pytest.approx((expected_cb, expected_cr), abs=1e-14)
    assert vector_targets(.75)[name] == pytest.approx((expected_cb * .75, expected_cr * .75), abs=1e-14)


def test_quantized_75_percent_color_bars_match_reference_within_half_bin():
    for name, (rgb, _y, _cb, _cr) in REFERENCE.items():
        patch = np.rint(np.array(rgb) * .75).astype(np.uint8).reshape(1, 1, 3)
        _luma, cb, cr = rec709_ycbcr(patch)
        ref_cb, ref_cr = vector_targets(.75)[name]
        assert abs(cb.item() - ref_cb) <= .5 / 256
        assert abs(cr.item() - ref_cr) <= .5 / 256
    assert set(vector_targets(0).values()) == {(0., 0.)}


@pytest.mark.parametrize('invalid', [True, False, float('nan'), float('inf'), -1, 1.1, '0.75', None])
def test_reject_invalid_reference_amplitude(invalid):
    with pytest.raises(ValueError, match='amplitude'):
        vector_targets(invalid)


@pytest.mark.parametrize('shape', [(256, 256), (31, 401), (401, 31), (1, 8192), (8192, 1), (3, 5)])
def test_sampling_is_bounded_non_square_and_density_counts_are_conserved(shape):
    height, width = shape
    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[..., 0] = np.arange(width, dtype=np.uint32) % 256
    rgb[..., 1] = (np.arange(height, dtype=np.uint32) % 256)[:, None]
    rgb[..., 2] = 235
    sampled = sample_rgb(rgb)
    sh, sw = sampled.shape[:2]
    assert 1 <= sw <= SAMPLE_SIDE and 1 <= sh <= SAMPLE_SIDE
    assert sh * sw <= SAMPLE_SIDE ** 2
    assert sw <= width and sh <= height  # No upsampling even for tiny frames.
    xs = ((2 * np.arange(sw) + 1) * width) // (2 * sw)
    ys = ((2 * np.arange(sh) + 1) * height) // (2 * sh)
    np.testing.assert_array_equal(sampled, rgb[ys[:, None], xs[None, :]])
    data = analyze_rgb(rgb)
    assert data.source_size == (width, height)
    assert data.sampled_size == (sw, sh)
    np.testing.assert_array_equal(data.histogram.sum(axis=1), [sw * sh] * 3)
    np.testing.assert_array_equal(data.parade.sum(axis=(1, 2)), [sw * sh] * 3)
    assert data.waveform.sum() == data.vectorscope.sum() == sw * sh


def test_counts_do_not_overflow_or_clip_dense_neutral_patch():
    data = analyze_rgb(np.full((1024, 1024, 3), 255, dtype=np.uint8))
    assert data.vectorscope[128, 128] == 65536
    assert data.histogram[0, 255] == 65536
    assert data.parade[0, 0, 0] == 256
    for array in (data.histogram, data.waveform, data.parade, data.vectorscope):
        assert array.dtype == np.uint32
        assert not array.flags.writeable


def test_all_rgb_cube_corners_and_random_values_stay_visible_without_clamping():
    rng = np.random.default_rng(143)
    rgb = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
    rgb[0, :8] = [[r, g, b] for r in (0, 255) for g in (0, 255) for b in (0, 255)]
    data = analyze_rgb(rgb)
    assert data.vectorscope.sum() == 65536
    # All four boundaries have true legal RGB points, not clipped accumulations.
    assert data.vectorscope[0].sum() > 0  # red
    assert data.vectorscope[-1].sum() > 0  # cyan
    assert data.vectorscope[:, 0].sum() > 0  # yellow
    assert data.vectorscope[:, -1].sum() > 0  # blue


@pytest.mark.parametrize('bad', [
    np.zeros((2, 2, 3), dtype=np.float32),
    np.full((2, 2, 3), np.nan),
    np.full((2, 2, 3), np.inf),
    np.full((2, 2, 3), -1, dtype=np.int16),
    np.full((2, 2, 3), 65535, dtype=np.uint16),
    np.zeros((0, 3, 3), dtype=np.uint8),
    np.zeros((3, 0, 3), dtype=np.uint8),
    np.zeros((2, 2), dtype=np.uint8),
    np.zeros((2, 2, 4), dtype=np.uint8),
    [[[0, 0, 0]]], None,
], ids=['float', 'nan', 'infinity', 'negative', 'high-bit', 'zero-height',
        'zero-width', 'no-channels', 'alpha-array', 'list', 'none'])
def test_reject_invalid_arrays_instead_of_silently_casting_or_clipping(bad):
    for operation in (analyze_rgb, sample_rgb, rec709_ycbcr):
        with pytest.raises(ValueError, match='uint8 RGB'):
            operation(bad)


def test_noncontiguous_rgb_views_are_supported_without_mutating_input():
    original = np.arange(3 * 7 * 3, dtype=np.uint8).reshape(3, 7, 3)
    view = original[::-1, ::-2, :]
    before = original.copy()
    data = analyze_rgb(view)
    assert data.sampled_size == (4, 3)
    np.testing.assert_array_equal(original, before)
    np.testing.assert_array_equal(data.histogram[1], np.bincount(view[..., 1].ravel(), minlength=256))


def test_qimage_scanline_padding_is_not_counted_or_confused_with_pixels():
    # RGB888 width 3 produces padded scanlines. Poison padding independently.
    raw = np.full((2, 12), 254, dtype=np.uint8)
    rgb = np.array([[[255, 0, 0], [0, 255, 0], [0, 0, 255]],
                    [[16, 16, 16], [128, 128, 128], [235, 235, 235]]], dtype=np.uint8)
    raw[:, :9] = rgb.reshape(2, 9)
    image = QImage(raw.data, 3, 2, 12, QImage.Format.Format_RGB888)
    extracted = rgb_from_qimage(image)
    np.testing.assert_array_equal(extracted, rgb)
    raw[:] = 0  # The extractor owns its pixels.
    np.testing.assert_array_equal(extracted, rgb)
    data = analyze_rgb(extracted)
    assert not data.histogram[:, 254].any()
    assert data.sample_count == 6


@pytest.mark.parametrize('image_format', [QImage.Format.Format_RGB32, QImage.Format.Format_BGR888,
                                          QImage.Format.Format_RGBA8888])
def test_qimage_format_conversion_preserves_rgb_channel_order(image_format):
    image = QImage(3, 2, image_format)
    for x, color in enumerate((QColor('red'), QColor('lime'), QColor('blue'))):
        for y in range(2):
            image.setPixelColor(x, y, color)
    rgb = rgb_from_qimage(image)
    np.testing.assert_array_equal(rgb[0], np.eye(3, dtype=np.uint8) * 255)
    np.testing.assert_array_equal(rgb[1], rgb[0])


@pytest.mark.parametrize('size', [(600, 1200), (1200, 600), (1, 6000), (6000, 1)])
def test_qimage_sampling_keeps_original_dimensions_and_never_creates_new_colors(size):
    image = QImage(*size, QImage.Format.Format_RGB32)
    image.fill(QColor(16, 235, 128))
    data = analyze_qimage(image)
    assert data.source_size == size
    assert max(data.sampled_size) == 256
    assert data.sample_count <= 65536
    assert data.histogram[0, 16] == data.sample_count
    assert data.histogram[1, 235] == data.sample_count
    assert data.histogram[2, 128] == data.sample_count


def test_null_qimage_is_rejected_by_computation():
    with pytest.raises(ValueError, match='empty'):
        rgb_from_qimage(QImage())


def test_widget_has_runtime_modes_and_localization_without_losing_data(app):
    widget = Scopes()
    widget.resize(600, 260)
    widget.show()
    widget.set_image(qimage(np.array([[[255, 0, 0], [0, 255, 0], [0, 0, 255]]], dtype=np.uint8)))
    data = widget.data
    for mode, _label in widget.MODES:
        widget.set_mode(mode)
        app.processEvents()
        assert widget.mode == mode
        assert not widget.grab().isNull()
    widget.set_mode('vectorscope')
    old_locale = get_locale()
    try:
        set_locale('zh-CN')
        assert widget.mode == 'vectorscope'
        assert '矢量' in widget.mode_choice.currentText()
        assert '8 位' in widget.source_label.text()
        assert '不是饱和度百分比' in widget.detail_label.toolTip()
        assert '预览转换器已完成' in widget.toolTip()
        set_locale('en')
        assert 'Vectorscope' in widget.mode_choice.currentText()
        assert '8-bit' in widget.source_label.text()
        assert 'RGB amplitude' in widget.detail_label.text()
        assert 'not saturation percentages' in widget.detail_label.toolTip()
        assert widget.data is data
        assert widget.mode == 'vectorscope'
    finally:
        set_locale(old_locale)
        widget.close()


def test_widget_stale_flag_is_explicit_and_invalid_frames_clear_measurements(app):
    widget = Scopes()
    image = qimage(np.full((2, 4, 3), 128, dtype=np.uint8))
    assert widget.data is None and not widget.stale
    widget.set_stale()
    assert widget.stale
    widget.set_image(image)
    assert widget.stale  # A late decoder frame cannot clear revision validity.
    assert 'out of date' in widget.canvas.accessibleDescription()
    assert widget.data is not None
    widget.set_stale(False)
    assert not widget.stale
    assert 'out of date' not in widget.canvas.accessibleDescription()
    widget.set_image(QImage())
    assert widget.data is None
    assert widget.histogram is widget.waveform is widget.parade is widget.vectorscope is None
    assert not widget._images
    assert not widget.stale
    widget.set_image(image)
    widget.clear()
    assert widget.data is None
    widget.close()


def test_vectorscope_geometry_is_square_for_wide_and_tall_widgets(app):
    widget = Scopes()
    widget.set_mode('vectorscope')
    for width, height in ((800, 240), (300, 550)):
        widget.resize(width, height)
        widget.show()
        app.processEvents()
        rect = widget.canvas.vector_rect()
        assert rect.width() == rect.height()
        assert rect.left() >= 0 and rect.top() >= 0
        assert rect.right() <= widget.canvas.width()
        assert rect.bottom() <= widget.canvas.height()
    widget.close()


def test_invalid_mode_is_rejected_without_mutating_selection(app):
    widget = Scopes()
    widget.set_mode('vectorscope')
    with pytest.raises(ValueError, match='Unknown scope'):
        widget.set_mode('hdr')
    assert widget.mode == 'vectorscope'
    widget.close()


@pytest.mark.parametrize('size', [(1, 1), (51, 67), (128, 128), (256, 256), (600, 600)])
def test_display_rebinning_never_loses_endpoint_or_single_bin_counts(size):
    counts = np.zeros((257, 257), dtype=np.uint32)
    counts[0, 0] = 1
    counts[0, -1] = 2
    counts[-1, 0] = 3
    counts[-1, -1] = 4
    counts[128, 128] = 65536
    reduced = _rebin_counts(counts, *size)
    assert reduced.sum() == 65546
    assert reduced.shape == (min(size[1], 257), min(size[0], 257))
    if min(size) > 1:
        assert reduced[0, 0] == 1 and reduced[0, -1] == 2
        assert reduced[-1, 0] == 3 and reduced[-1, -1] == 4


def test_rendered_parade_retains_saturated_endpoint_traces_after_downsizing(app):
    widget = Scopes()
    widget.resize(500, 220)
    widget.set_image(qimage(np.full((8, 256, 3), 255, dtype=np.uint8)))
    widget.show()
    app.processEvents()
    image = widget.canvas.grab().toImage()
    # The top code row is a full-brightness RGB trace, not just gray grid/text.
    # Qt nearest-neighbour raster shrinking alone used to discard this row.
    pixels = np.array([[image.pixelColor(x, y).getRgb()[:3] for x in range(image.width())]
                       for y in range(20, 25)], dtype=np.int16)
    assert np.count_nonzero((pixels[..., 0] > 180) & (pixels[..., 0] > pixels[..., 1] * 1.3)) > 20
    assert np.count_nonzero((pixels[..., 1] > 180) & (pixels[..., 1] > pixels[..., 0] * 1.3)) > 20
    assert np.count_nonzero((pixels[..., 2] > 180) & (pixels[..., 2] > pixels[..., 0] * 1.3)) > 20
    widget.close()


def test_rendered_vectorscope_keeps_neutral_spot_when_reduced(app):
    widget = Scopes()
    widget.resize(500, 260)
    widget.set_mode('vectorscope')
    widget.set_image(qimage(np.full((24, 48, 3), 128, dtype=np.uint8)))
    widget.show()
    app.processEvents()
    image = widget.canvas.grab().toImage()
    center = widget.canvas.vector_rect().center()
    pixels = np.array([image.pixelColor(x, y).getRgb()[:3]
                       for x in range(round(center.x()) - 2, round(center.x()) + 3)
                       for y in range(round(center.y()) - 2, round(center.y()) + 3)])
    assert np.count_nonzero(np.all(pixels > 150, axis=1)) > 0
    # Repeated resize/mode painting has a bounded cache as well as bounded math.
    for size in range(260, 290):
        widget.resize(500, size)
        app.processEvents()
        widget.grab()
    assert len(widget._images) <= 6
    widget.close()


def test_mainwindow_frame_callback_rejects_stale_revision_and_wrong_comparison(app):
    """Exercise the actual UI callback, with no decoder/media-device dependency."""
    from types import SimpleNamespace
    from lumen.ui import MainWindow
    widget = Scopes()
    old_image = qimage(np.full((3, 4, 3), 128, dtype=np.uint8))
    widget.set_image(old_image)
    original = widget.data
    calls = []
    frame = SimpleNamespace(isValid=lambda: True,
                            toImage=lambda: calls.append('decode') or old_image)
    owner = SimpleNamespace(scopes=widget, scope_counter=0, preview_revision=1,
                            revision=2, loaded_preview_mode='graded', preview_mode='graded')
    MainWindow.video_frame(owner, frame)
    assert widget.stale and widget.data is original and not calls
    owner.preview_revision = owner.revision
    owner.preview_mode = 'bypass'
    owner.scope_counter = 0
    MainWindow.video_frame(owner, frame)
    assert widget.stale and widget.data is original and not calls
    owner.loaded_preview_mode = 'bypass'
    owner.scope_counter = 0
    MainWindow.video_frame(owner, frame)
    assert not widget.stale and widget.data is not original and calls == ['decode']
    MainWindow.video_frame(owner, SimpleNamespace(isValid=lambda: False))
    assert widget.data is None
    widget.close()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('mode', ['parade', 'vectorscope', 'histogram_waveform'])
def test_compact_scope_keeps_source_visible_and_all_children_inside_101px(app, locale, mode):
    """Reproduce the real 1366x768 editor scope allocation, with native styling."""
    from lumen.ui_style import STYLE, apply_ui_layout_tokens
    set_locale(locale)
    widget = Scopes()
    widget.setStyleSheet(STYLE)
    apply_ui_layout_tokens(widget)
    widget.set_mode(mode)
    widget.set_image(qimage(np.full((24, 48, 3), 128, dtype=np.uint8)))
    widget.setFixedSize(691, 101)
    widget.show()
    for _ in range(4):
        app.processEvents()
    assert widget._compact
    assert not widget.hasHeightForWidth()
    assert widget.source_label.isVisible()
    assert ('8-bit' if locale == 'en' else '8 位') in widget.source_label.text()
    assert widget.detail_label.isHidden()
    for child in (widget.source_label, widget.mode_choice, widget.canvas):
        assert widget.rect().contains(child.geometry())
    assert not widget.canvas.geometry().intersects(widget.mode_choice.geometry())
    assert not widget.canvas.geometry().intersects(widget.source_label.geometry())
    assert widget.canvas.height() >= 35
    assert not widget.grab().isNull()
    # Increasing the available height restores the explanatory legend, without
    # replacing the measurements or selector state.
    data = widget.data
    widget.setFixedSize(691, 300)
    for _ in range(4):
        app.processEvents()
    assert not widget._compact
    assert widget.detail_label.isVisible()
    assert widget.data is data and widget.mode == mode
    assert widget.canvas.height() > 150
    widget.close()


@pytest.mark.parametrize('size', [(1000, 700), (1366, 768), (1600, 1120)])
@pytest.mark.parametrize('mode', ['parade', 'vectorscope'])
@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_mainwindow_allocates_and_paints_actual_scope_traces(app, tmp_path, size, mode, locale):
    """Layout acceptance alone cannot detect a zero-height custom-painted graph.

    Use the real MainWindow and its stretchy video, not a detached scope or a
    forced scope size. Seed the scope widget directly with a local RGB fixture;
    decoder provenance is independently tested by the reference-frame suites.
    """
    from types import SimpleNamespace
    from PySide6.QtCore import QSettings
    from lumen.model import Media
    from lumen.ui import MainWindow

    settings = QSettings(str(tmp_path / 'scopes.ini'), QSettings.Format.IniFormat)
    settings.setValue('mode', 'professional')
    settings.setValue('language', locale)
    window = MainWindow(workspace_settings=settings)
    image = qimage(np.full((144, 256, 3), 255, dtype=np.uint8))
    source = tmp_path / 'white.png'
    assert image.save(str(source))
    window.editor.add_media(Media('scope-fixture', str(source), 'White scope fixture',
                                  1, 256, 144, True, False))
    window.refresh()
    window.bin.setCurrentRow(0)
    window.add_selected_media()
    window.video.image = image
    window.preview_revision = window.revision
    window.loaded_preview_mode = window.preview_mode
    window.update_playback_controls()
    window.scope_counter = 0
    window.scopes.set_image(image)
    window.scopes.set_stale(False)
    window.scopes.set_mode(mode)
    # The shared seek row must also fit a long frame/time readout without
    # sacrificing slider width or overlapping its neighbor.
    window.position_label.setText('9999:59.967 · f17999999')
    window.resize(*size)
    window.show()
    try:
        for _ in range(8):
            app.processEvents()
        canvas = window.scopes.canvas
        evidence = os.environ.get('AUTO_FRAME_REVIEW_EVIDENCE')
        if evidence:
            import json
            from pathlib import Path
            from PySide6.QtGui import QFontInfo
            folder = Path(evidence) / 'scopes'; folder.mkdir(parents=True, exist_ok=True)
            name = f'{locale}-{mode}-{size[0]}x{size[1]}-dpr{window.devicePixelRatioF():g}'
            widgets = dict(window=window, viewer=window.video.parentWidget(), video=window.video,
                           scopes=window.scopes, selector=window.scopes.mode_choice,
                           source=window.scopes.source_label, canvas=canvas)
            record = dict(locale=locale, mode=mode, requested_size=size, dpr=window.devicePixelRatioF(),
                widgets={key: dict(rect=value.geometry().getRect(), minimum=value.minimumSize().toTuple(),
                    minimum_hint=value.minimumSizeHint().toTuple(),
                    visible=value.visibleRegion().boundingRect().getRect(),
                    font_family=QFontInfo(value.font()).family(), font_pixels=QFontInfo(value.font()).pixelSize())
                    for key, value in widgets.items()})
            (folder / (name+'.json')).write_text(json.dumps(record, indent=2), encoding='utf-8')
            assert window.grab().save(str(folder / (name+'.png')))
        assert window.size().toTuple() == size
        assert not window.scrub.geometry().intersects(window.position_label.geometry())
        assert window.scrub.width() >= 100
        assert window.video.parentWidget().rect().contains(window.position_label.geometry())
        assert canvas.isVisible() and window.scopes.data is not None
        assert canvas.height() >= (50 if size[1] <= 768 else 160)
        assert canvas.visibleRegion().boundingRect().height() == canvas.height()
        assert window.scopes.rect().contains(canvas.geometry())
        painted = canvas.grab().toImage()
        # QWidget geometry is logical; grab() stores physical image pixels.
        # Sample the same logical trace region at every actual device ratio.
        dpr = painted.devicePixelRatio()
        if mode == 'parade':
            top = 16 if canvas.height() < 100 else 22
            pixels = np.array([[painted.pixelColor(x, y).getRgb()[:3]
                                for x in range(painted.width())]
                               for y in range(round((top - 2) * dpr), round((top + 3) * dpr))], dtype=np.int16)
            for channel in range(3):
                assert np.count_nonzero((pixels[..., channel] > 180)
                                        & (pixels[..., channel] > pixels[..., (channel + 1) % 3] * 1.3)) > 20
        else:
            center = canvas.vector_rect().center()
            pixels = np.array([painted.pixelColor(x, y).getRgb()[:3]
                               for x in range(round((round(center.x()) - 2) * dpr), round((round(center.x()) + 3) * dpr))
                               for y in range(round((round(center.y()) - 2) * dpr), round((round(center.y()) + 3) * dpr))])
            assert np.any(np.all(pixels > 150, axis=1)), 'The neutral trace must be visibly painted.'
    finally:
        window.cancel_jobs()
        window.dirty = False
        window.closing = True
        window.close()
        app.processEvents()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('header_height', [39, 48, 60])
@pytest.mark.parametrize('font_points', [10, 13])
def test_compact_scope_reserves_graph_height_after_native_header_changes(app, locale, header_height, font_points):
    """Native selector metrics must not consume the graph's compact allocation."""
    from PySide6.QtWidgets import QWidget, QVBoxLayout
    set_locale(locale)
    host = QWidget()
    layout = QVBoxLayout(host); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
    from PySide6.QtCore import QSize
    class StretchVideo(QWidget):
        def sizeHint(self): return QSize(300, 300)
    video = StretchVideo(); video.setMinimumHeight(50)
    layout.addWidget(video, 1)
    widget = Scopes(); layout.addWidget(widget)
    widget.set_image(qimage(np.full((24, 48, 3), 128, dtype=np.uint8)))
    host.resize(360, 180); host.show()
    try:
        for height in (header_height, header_height + 8, header_height):
            from PySide6.QtGui import QFont
            font = QFont(widget.font()); font.setPointSize(font_points)
            widget.setFont(font)
            widget.mode_choice.setMinimumHeight(height)
            for _ in range(8): app.processEvents()
            margins = widget.layout().contentsMargins()
            header = widget.layout().itemAt(0).layout().minimumSize().height()
            needed = header + margins.top() + margins.bottom() + widget.layout().spacing() + 50
            assert widget.minimumSizeHint().height() >= needed
            assert widget.minimumHeight() >= needed
            assert host.size().toTuple() == (360, 180)
            assert video.height() >= 50
            assert widget.canvas.height() >= 50
            assert widget.canvas.visibleRegion().boundingRect() == widget.canvas.rect()
            for child in (widget.source_label, widget.mode_choice, widget.canvas):
                assert widget.rect().contains(child.geometry())
            assert not widget.canvas.geometry().intersects(widget.mode_choice.geometry())
            assert not widget.canvas.geometry().intersects(widget.source_label.geometry())
            assert widget.data is not None and not widget.canvas.grab().isNull()
    finally:
        host.close(); app.processEvents()

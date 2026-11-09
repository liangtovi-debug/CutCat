"""Exercise the exact bounded color acceptance shipped inside native packages."""
from copy import deepcopy
import json
import os

import numpy as np
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from lumen import color_pipeline_smoke as smoke
from lumen.caption_render import CaptionRenderError, font_identity
from lumen.media import ffmpeg_available
from lumen.model import CaptionStyle, Editor, Project, VERSION as PROJECT_VERSION
from lumen.render import RenderEngine
from lumen.ui import MainWindow


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def available_font():
    directory = os.environ.get('LUMEN_TEST_CJK_FONT_DIR', '')
    if directory:
        style = CaptionStyle(font_family='Noto Sans CJK SC', font_directory=directory)
        font_identity(style)  # An explicit fixture must work; never fall back or skip.
        return style
    choices = [CaptionStyle(font_family=name) for name in ('DejaVu Sans', 'Arial', 'Helvetica')]
    for style in choices:
        try:
            font_identity(style)
            return style
        except CaptionRenderError:
            pass
    pytest.skip('A local test font is required')


@pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
def test_packaged_color_smoke_real_pixels_dialogs_and_state_restoration(tmp_path, app, available_font):
    engine = RenderEngine(timeout=60)
    window = None
    try:
        project, result = smoke.run_color_pipeline_smoke(engine, tmp_path, available_font)
        assert result['synthetic_media_only'] and not result['downloads_or_user_media_used']
        animation = result['animation']
        assert animation['schema'] == PROJECT_VERSION and animation['rgb16_max_code_error'] <= 2
        assert animation['split_pixels_identical_and_undo_exact']
        assert animation['preview_cache_reused_and_key_edit_invalidated']
        apple = result['apple_log_experimental']
        assert apple['explicit_import_required'] and apple['normalization_survives_grade_bypass']
        assert not apple['camera_footage_validated'] and not apple['apple_lut_or_resolve_matching_claimed']
        chain = result['highbit_caption_sticker_chain']
        assert chain['distinct_ramp_luma_codes'] > 650
        assert chain['sub_eight_bit_patch_delta'] >= 1.75
        assert abs(chain['eight_bit_negative_control_patch_delta']) <= .5
        assert chain['inactive_frames_and_untouched_pixels_identical']
        assert chain['metadata']['pix_fmt'] == 'yuv422p10le'
        assert all(result[section]['source_unchanged'] for section in
                   ('animation', 'apple_log_experimental', 'highbit_caption_sticker_chain'))
        # The report remains JSON-safe and every retained render is real media.
        json.dumps(result)
        for section in ('apple_log_experimental', 'highbit_caption_sticker_chain'):
            assert (tmp_path / 'color-pipeline' / result[section]['rendered_file']).stat().st_size > 1000
        settings = QSettings(str(tmp_path / 'isolated-ui.ini'), QSettings.Format.IniFormat)
        window = MainWindow(workspace_settings=settings)
        window.editor = Editor(deepcopy(project))
        window.revision = 5
        window.dirty = True
        window.refresh(); window.select_clip(project.clips[0].id)
        window.resize(1920, 1080); window.show(); app.processEvents()
        original_editor = window.editor
        original = deepcopy((window.project.to_dict(), window.revision, window.dirty,
            window.selection, window.active_selection_type, window.timeline.playhead, window.ui_language))
        screenshots = []
        ui = smoke.capture_color_pipeline_ui(window, project, tmp_path, screenshots)
        assert window.editor is original_editor
        assert original == (window.project.to_dict(), window.revision, window.dirty,
            window.selection, window.active_selection_type, window.timeline.playhead, window.ui_language)
        assert ui['apply_once_undo_redo_and_persistence']
        assert ui['keyboard_timing_commit_and_escape_cancel'] and ui['numeric_collision_rejected']
        assert ui['stale_context_cannot_apply'] and ui['manual_refresh_only']
        assert not ui['physical_keyboard_tested']
        assert len(screenshots) == 4
        assert all(not QImage(str(tmp_path / name)).isNull() for name in screenshots)
        assert not window.jobs
    finally:
        if window is not None:
            window.dirty = False; window.close(); window.deleteLater(); app.processEvents()
        engine.close()


@pytest.mark.parametrize('kind', ['rgb16', 'yuv10'])
@pytest.mark.parametrize('size', [0, 1, 1000])
def test_decode_smoke_rejects_partial_missing_or_extra_frames(monkeypatch, kind, size):
    monkeypatch.setattr(smoke, '_ff', lambda *a, **k: bytes(size))
    decode = smoke._decode_rgb16 if kind == 'rgb16' else smoke._decode_yuv10
    with pytest.raises(RuntimeError, match='frame count/size mismatch'):
        decode('unused.mkv', 3, 16, 16)


@pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
def test_animation_smoke_detects_wrong_preview_pixels(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import lumen.frame_grade_preview as preview
    monkeypatch.setattr(preview, 'capture_grade_preview_snapshot',
                        lambda *a, **k: SimpleNamespace(frame=np.zeros((128, 192, 3))))
    with pytest.raises(RuntimeError, match='analytic oracle'):
        smoke._animation_checks(None, tmp_path)


@pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
def test_color_ui_failure_still_restores_editor_and_stops_jobs(tmp_path, app, monkeypatch):
    root = tmp_path / 'color-pipeline'; root.mkdir()
    project, _, _, _ = smoke._animated_fixture(root)
    window = MainWindow(workspace_settings=QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat))
    window.editor = Editor(deepcopy(project)); window.revision = 7; window.dirty = True
    window.refresh(); window.select_clip(project.clips[0].id); window.show(); app.processEvents()
    original = window.editor
    before = deepcopy((window.project.to_dict(), window.revision, window.dirty,
                       window.selection, window.timeline.playhead, window.ui_language))
    require = smoke._require
    def fail_capture(condition, message):
        if message == 'color dialog screenshot failed':
            raise RuntimeError('Injected screenshot failure')
        require(condition, message)
    monkeypatch.setattr(smoke, '_require', fail_capture)
    try:
        with pytest.raises(RuntimeError, match='Injected screenshot failure'):
            smoke.capture_color_pipeline_ui(window, project, tmp_path, [])
        assert window.editor is original
        assert before == (window.project.to_dict(), window.revision, window.dirty,
                          window.selection, window.timeline.playhead, window.ui_language)
        assert not window.jobs
    finally:
        window.dirty = False; window.close(); window.deleteLater(); app.processEvents()


def test_configured_smoke_caption_font_fails_closed(monkeypatch):
    import sys
    module = sys.modules[__name__]
    families = []
    def fail(style):
        families.append((style.font_family, style.font_directory))
        raise CaptionRenderError('Configured caption font failed')
    monkeypatch.setenv('LUMEN_TEST_CJK_FONT_DIR', '/synthetic-cjk-fixture')
    monkeypatch.setattr(module, 'font_identity', fail)
    with pytest.raises(CaptionRenderError, match='Configured caption font failed'):
        available_font.__wrapped__()
    assert families == [('Noto Sans CJK SC', '/synthetic-cjk-fixture')]

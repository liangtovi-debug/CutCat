"""The frozen restoration gate measures genuine local media processing."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import shutil

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from lumen.media import ffmpeg_binary, ffprobe_binary
from lumen.model import Project
from lumen.render import RenderEngine
from lumen.restoration_smoke import (
    FRAMES, HEIGHT, WIDTH, capture_restoration_ui, run_restoration_smoke,
    synthetic_restoration_frames, _quality_metrics,
)
from lumen.ui import MainWindow


@pytest.fixture(scope='module')
def actual_smoke(tmp_path_factory):
    if not shutil.which(ffmpeg_binary()) or not shutil.which(ffprobe_binary()):
        pytest.skip('External FFmpeg/ffprobe unavailable')
    folder = tmp_path_factory.mktemp('restoration-native-smoke')
    engine = RenderEngine(timeout=60)
    try:
        project, preview, report = run_restoration_smoke(engine, folder)
        yield folder, engine, project, preview, report
    finally:
        engine.close()


def test_original_fixture_is_deterministic_with_separate_measurable_degradations():
    clean, first = synthetic_restoration_frames()
    other_clean, second = synthetic_restoration_frames()
    np.testing.assert_array_equal(clean, other_clean)
    np.testing.assert_array_equal(first, second)
    assert clean.shape == (HEIGHT, WIDTH, 3) and clean.dtype == np.uint8
    assert first.shape == (FRAMES, HEIGHT, WIDTH, 3) and first.dtype == np.uint8
    metrics = _quality_metrics(first, clean)
    assert metrics['flat_rmse'] > 4 and metrics['flat_temporal_rms'] > 4
    assert metrics['block_boundary_rmse'] > 5
    assert metrics['soft_edge_rmse'] > 10
    assert metrics['hard_edge_contrast'] == 128
    perfect = _quality_metrics(np.repeat(clean[None], FRAMES, axis=0), clean)
    assert perfect['flat_rmse'] == perfect['block_boundary_rmse'] == perfect['soft_edge_rmse'] == 0


def test_real_stages_improve_known_degradation_and_preserve_edges(actual_smoke):
    folder, engine, project, preview, report = actual_smoke
    metrics = report['decoded_picture_metrics']; baseline = metrics['bypass']
    assert report['isolated_deblock_improved'] and report['isolated_denoise_improved']
    assert report['isolated_sharpen_improved'] and report['combined_noise_and_blocks_improved']
    assert metrics['denoise']['flat_rmse'] < baseline['flat_rmse'] * .8
    assert metrics['deblock']['block_boundary_rmse'] < baseline['block_boundary_rmse'] * .9
    assert metrics['sharpen']['soft_edge_rmse'] < baseline['soft_edge_rmse'] * .99
    assert report['strong_edge_and_halo_bounds_preserved']
    assert report['correction_mean_pixel_difference'] > .2
    assert 'synthetic' in report['quality_scope']


def test_real_preview_export_cache_audio_frame_count_and_source_integrity(actual_smoke):
    folder, engine, project, preview, report = actual_smoke
    assert report['source_unchanged'] and report['preview_cache_reused']
    assert report['restoration_bypass_invalidates_preview']
    assert report['source_frames'] == report['output_frames'] == report['preview_frames'] == FRAMES
    assert report['preview_export_mean_pixel_difference'] < 4
    assert report['audio_unchanged'] and report['audio_samples'] == 48000
    assert report['audio_rms'] > .03 and report['audio_max_sample_difference'] < 1e-6
    assert hashlib.sha256(Path(project.media[0].path).read_bytes()).hexdigest() == report['source_sha256']
    assert (folder / report['rendered_file']).is_file() and preview.is_file()
    assert Project.load(folder / report['project_file']) == project
    assert report['elapsed_seconds'] < 90


def test_existing_smoke_destination_is_never_overwritten(actual_smoke):
    folder, engine, project, preview, report = actual_smoke
    output = folder / report['rendered_file']
    before = hashlib.sha256(output.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        run_restoration_smoke(engine, folder)
    assert hashlib.sha256(output.read_bytes()).hexdigest() == before


def test_packaged_ui_edits_undoes_and_decodes_real_preview(actual_smoke, tmp_path):
    folder, engine, project, preview, report = actual_smoke
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / 'ui.ini'), QSettings.Format.IniFormat)
    window = MainWindow(workspace_settings=settings, ui_language='en')
    screenshots = []; before = deepcopy(project.to_dict())
    window.resize(1920, 1080); window.show()
    window.vertical_split.setSizes([720, 180])
    try:
        result = capture_restoration_ui(window, project, preview, tmp_path, screenshots)
        assert result == {'ui_apply_and_undo_verified': True, 'ui_bypass_preserves_strengths': True,
                          'ui_preview_decoded': True, 'ui_locales': ['zh-CN', 'en'],
                          'ui_locale_workspace_state_preserved': True, 'ui_complete_group_visible': True}
        assert screenshots == ['frozen-ui-zh-CN-restoration.png', 'frozen-ui-en-restoration.png']
        assert all(not QImage(str(tmp_path / name)).isNull() for name in screenshots)
        assert project.to_dict() == before and window.project.to_dict() == before
        assert window.restoration_enabled.isChecked()
        assert window.restoration_denoise.value() == .8
        assert 'No AI' in window.restoration_limits_note.text()
        assert not window.video.image.isNull() and not window.jobs
    finally:
        window.dirty = False; window.closing = True; window.close(); app.processEvents()

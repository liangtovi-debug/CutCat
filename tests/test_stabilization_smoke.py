"""The packaged acceptance helper uses actual external media processing."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from lumen.media import ffmpeg_binary, ffprobe_binary
from lumen.model import Project
from lumen.render import RenderEngine
from lumen.stabilization_smoke import (
    FRAMES, HEIGHT, WIDTH, capture_stabilization_ui, run_stabilization_smoke,
    synthetic_shake_frames, _translation_jitter,
)
from lumen.ui import MainWindow


@pytest.fixture(scope='module')
def actual_smoke(tmp_path_factory):
    if not shutil.which(ffmpeg_binary()) or not shutil.which(ffprobe_binary()):
        pytest.skip('External FFmpeg/ffprobe unavailable')
    filters = subprocess.run([ffmpeg_binary(), '-hide_banner', '-filters'],
                             capture_output=True, text=True, timeout=20)
    if not all(name in filters.stdout for name in ('vidstabdetect', 'vidstabtransform')):
        pytest.skip('External FFmpeg lacks libvidstab')
    folder = tmp_path_factory.mktemp('stabilization-native-smoke')
    engine = RenderEngine(timeout=60)
    try:
        project, preview, report = run_stabilization_smoke(engine, folder)
        yield folder, engine, project, preview, report
    finally:
        engine.close()


def test_original_fixture_is_deterministic_and_has_actual_known_shake():
    first, shifts = synthetic_shake_frames(); second, other = synthetic_shake_frames()
    np.testing.assert_array_equal(first, second)
    assert shifts == other and len(shifts) == FRAMES
    assert first.shape == (FRAMES, HEIGHT, WIDTH, 3) and first.dtype == np.uint8
    assert np.std(first[0]) > 20
    reference = np.roll(first[0], (-shifts[0][1], -shifts[0][0]), (0, 1))
    for frame, (x, y) in zip(first, shifts):
        np.testing.assert_array_equal(frame, np.roll(reference, (y, x), (0, 1)))
    assert _translation_jitter(first) > 3
    assert _translation_jitter(np.repeat(first[:1], FRAMES, axis=0)) == 0


def test_actual_analysis_render_preview_duration_integrity_and_jitter(actual_smoke):
    folder, engine, project, preview, report = actual_smoke
    assert report['source_unchanged'] and report['preview_cache_reused']
    assert report['source_frames'] == report['output_frames'] == report['preview_frames'] == 30
    assert report['dynamic_correction_std_px'] > .25
    assert report['correction_mean_pixel_difference'] > 1
    assert report['preview_export_mean_pixel_difference'] < 8
    assert report['known_translation_jitter_reduced']
    assert report['diagnostic_translation_jitter_after_px'] < report['diagnostic_translation_jitter_before_px'] * .85
    assert hashlib.sha256(Path(project.media[0].path).read_bytes()).hexdigest() == report['source_sha256']
    assert report['real_analysis']['analysis']['source_sha256'] == report['source_sha256']
    assert report['real_analysis']['statistics']['frame_count'] == 30
    assert (folder / report['rendered_file']).is_file() and preview.is_file()
    assert Project.load(folder / report['project_file']) == project
    assert report['elapsed_seconds'] < 60


def test_existing_smoke_destination_is_never_overwritten(actual_smoke):
    folder, engine, project, preview, report = actual_smoke
    before = hashlib.sha256((folder / report['rendered_file']).read_bytes()).hexdigest()
    with pytest.raises(FileExistsError): run_stabilization_smoke(engine, folder)
    assert hashlib.sha256((folder / report['rendered_file']).read_bytes()).hexdigest() == before


def test_packaged_ui_helper_edits_undoes_and_decodes_real_preview(actual_smoke, tmp_path):
    folder, engine, project, preview, report = actual_smoke
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / 'ui.ini'), QSettings.Format.IniFormat)
    window = MainWindow(workspace_settings=settings, ui_language='en')
    screenshots = []; before = deepcopy(project.to_dict())
    window.resize(1920, 1080); window.show()
    window.vertical_split.setSizes([720, 180])
    try:
        result = capture_stabilization_ui(window, project, preview, tmp_path, screenshots)
        assert result == {'ui_apply_and_undo_verified': True, 'ui_preview_decoded': True,
                          'ui_locales': ['zh-CN', 'en'], 'ui_locale_state_preserved': True}
        assert screenshots == ['frozen-ui-zh-CN-stabilization.png', 'frozen-ui-en-stabilization.png']
        assert all(not QImage(str(tmp_path / name)).isNull() for name in screenshots)
        assert project.to_dict() == before and window.project.to_dict() == before
        assert window.stabilization_enabled.isChecked()
        assert '600 seconds' in window.stabilization_processing_note.text()
        assert '18,000' in window.stabilization_processing_note.text()
        assert 'entire source' in window.stabilization_border_note.text()
        assert not window.video.image.isNull() and not window.jobs
    finally:
        window.dirty = False; window.closing = True; window.close(); app.processEvents()

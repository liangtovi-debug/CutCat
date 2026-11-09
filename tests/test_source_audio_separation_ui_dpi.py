"""Fresh-process actual separation dialog/inspector bilingual DPI screenshots."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('scale', [1, 1.5, 2])
def test_source_audio_separation_dpi_locales_workspaces_and_narrow_keyboard(tmp_path, scale):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / f'dpi-{scale:g}'
    result = subprocess.run([sys.executable, str(root / 'scripts/capture_source_audio_separation_ui.py'),
        '--output', str(output), '--scale', str(scale)], cwd=root,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen'), capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / 'report.json').read_text())
    assert report['status'] == 'passed' and not report['native_windows_macos_validation']
    assert report['identity_only_fixture'] and report['screenshot_bytes'] < 7 * 1024 * 1024
    assert len(report['records']) == 8
    for row in report['records']:
        assert row['device_pixel_ratio'] == scale and row['horizontal_scroll'] == 0
        assert row['footer_visible'] and row['keyboard_target_preview_apply_cancel']
        assert row['preview_project_unchanged'] and row['single_atomic_apply']
        assert row['stale_and_locked_apply_disabled'] and row['cancel_project_unchanged']
        assert row['source_unchanged'] and len(row['paths']) == (6 if row['geometry'] == 'narrow' else 4)
        if row['geometry'] == 'narrow':
            assert row['logical_size'][0] <= 388 and row['logical_size'][1] <= 536
        for path in row['paths']:
            assert (output / path).read_bytes().startswith(b'\x89PNG')

"""Separate-process bilingual master-gain geometry and screenshot evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('scale', [1, 1.5])
def test_master_dialog_narrow_fonts_and_workspace_dpi(tmp_path, scale):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / f'dpr-{scale:g}'
    process = subprocess.run([sys.executable, str(root / 'scripts/capture_master_gain_ui.py'),
        '--output', str(output), '--scale', str(scale)], cwd=root,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen'), capture_output=True, text=True, timeout=90)
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    assert report['status'] == 'passed' and report['project_unchanged']
    assert report['no_jobs_started'] and not report['native_windows_macos_validation']
    assert len(report['records']) == 8 and len(report['workspace_records']) == 4
    for row in report['records']:
        assert row['device_pixel_ratio'] == scale and row['horizontal_scroll'] == 0
        assert (output / row['screenshot']).read_bytes().startswith(b'\x89PNG')
        if row['bottom_screenshot']:
            assert (output / row['bottom_screenshot']).read_bytes().startswith(b'\x89PNG')
    for row in report['workspace_records']:
        assert row['master_action_enabled'] and row['timeline_viewport_height'] >= 120
        assert row['logical_size'] in [[1366, 768], [1000, 760]]
        assert (output / row['screenshot']).read_bytes().startswith(b'\x89PNG')
        assert (output / row['menu_screenshot']).read_bytes().startswith(b'\x89PNG')

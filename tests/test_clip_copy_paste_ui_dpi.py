"""Fresh Qt-process bilingual narrow-layout Copy/Paste screenshots."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('scale', [1, 1.5, 2])
def test_clip_paste_dpi_locales_workspaces_narrow_dialog(tmp_path, scale):
    root = Path(__file__).resolve().parents[1]
    output = Path(os.environ.get('CLIP_COPY_UI_EVIDENCE', tmp_path)) / f'dpi-{scale:g}'
    result = subprocess.run([sys.executable, str(root / 'scripts/capture_clip_copy_paste_ui.py'),
        '--output', str(output), '--scale', str(scale)], cwd=root,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen'), capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / 'report.json').read_text())
    assert report['status'] == 'passed' and not report['native_windows_macos_validation']
    assert len(report['records']) == 8
    if os.environ.get('LUMEN_TEST_CJK_FONT_DIR'):
        assert report['font_fixture'] is not None
    for row in report['records']:
        assert row['device_pixel_ratio'] == scale and row['horizontal_scroll'] == 0
        assert row['footer_visible'] and row['project_unchanged']
        assert row['logical_size'][0] <= 360 and row['logical_size'][1] <= 476
        for path in row['paths']:
            assert (output / path).read_bytes().startswith(b'\x89PNG')

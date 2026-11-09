"""Fresh-process bilingual reattach UI/screens at actual Qt scale factors."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('scale', [1, 1.5, 2])
def test_reattach_dpi_bilingual_workspaces_explicit_ids_keyboard(tmp_path, scale):
    root = Path(__file__).resolve().parents[1]; output = tmp_path / f'dpi-{scale:g}'
    result = subprocess.run([sys.executable, str(root / 'scripts/capture_source_audio_reattach_ui.py'),
        '--output', str(output), '--scale', str(scale)], cwd=root,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen'), capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / 'report.json').read_text())
    assert report['status'] == 'passed' and not report['native_windows_macos_validation']
    assert report['identity_only_fixture'] and report['screenshot_bytes'] < 7 * 1024 * 1024
    assert len(report['records']) == 8
    for row in report['records']:
        assert row['device_pixel_ratio'] == scale and row['horizontal_scroll'] == 0
        for key in ('footer_visible', 'keyboard_preview_apply_cancel', 'explicit_two_ids',
                    'other_candidate_retained', 'muted_transfer_preserved', 'preview_project_unchanged',
                    'single_atomic_apply_undo_redo', 'lock_stale_disabled', 'cancel_project_unchanged', 'source_unchanged'):
            assert row[key]
        if row['geometry'] == 'narrow': assert row['logical_size'][0] <= 388 and row['logical_size'][1] <= 536
        assert len(row['paths']) == (4 if row['geometry'] == 'narrow' else 3)
        for path in row['paths']: assert (output / path).read_bytes().startswith(b'\x89PNG')

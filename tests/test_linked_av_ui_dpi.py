"""Actual fresh-process EN/ZH pair-role and slip screens at three real DPRs."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('scale', [1, 1.5, 2])
def test_linked_av_bilingual_narrow_dpi_keyboard_apply_and_cancel(tmp_path, scale):
    root = Path(__file__).resolve().parents[1]; out = tmp_path / 'screens'
    result = subprocess.run([sys.executable, str(root / 'scripts/capture_linked_av_ui.py'),
        '--output', str(out), '--scale', str(scale)], cwd=root,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen'), capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((out / 'report.json').read_text())
    assert report['status'] == 'passed' and report['identity_only_fixture']
    assert not report['native_windows_macos_validation'] and report['screenshot_bytes'] < 2 * 1024 * 1024
    assert len(report['records']) == 4
    for row in report['records']:
        assert row['device_pixel_ratio'] == scale and row['horizontal_scroll'] == 0
        for key in ('explicit_role_parameter_edit', 'pair_selection', 'exact_rational_input',
                    'keyboard_preview_apply_undo_redo', 'draft_cancel_no_commit',
                    'link_unlink_metadata_only', 'footer_visible', 'accessible_controls', 'source_unchanged'):
            assert row[key]
        if row['geometry'] == 'narrow':
            assert row['logical_size'][0] <= 388 and row['logical_size'][1] <= 536
        for path in row['paths']:
            assert (out / path).read_bytes().startswith(b'\x89PNG')

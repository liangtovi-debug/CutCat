"""Unequal-edge EN/ZH capture at real DPRs; reuse the strict capture pipeline."""
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import pytest


@pytest.mark.parametrize('scale', [1, 1.5, 2])
def test_affine_paired_edge_trim_bilingual_narrow_dpi(tmp_path, scale):
    root = Path(__file__).resolve().parents[1]; out = tmp_path/'screens'
    result = subprocess.run([sys.executable, str(root/'scripts/capture_linked_av_trim_ui.py'),
        '--affine', '--output', str(out), '--scale', str(scale)], cwd=root,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen'), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr
    report = json.loads((out/'report.json').read_text())
    assert report['status'] == 'passed' and report['affine_fixture'] and report['identity_only_fixture']
    assert not report['native_windows_macos_validation'] and report['screenshot_bytes'] < 2*1024*1024
    assert len(report['records']) == 4
    for row in report['records']:
        assert row['device_pixel_ratio'] == scale
        assert row['canonical_durations'] == [27, 31] and row['common_delta'] == 3
        for name in ('both_outlines_visible','full_impact_accessible','full_rejection_accessible',
                     'one_commit_undo_redo','escape_reject_no_commit','source_unchanged',
                     'partner_collision_rejected','partner_lock_rejected','historical_order_rejected',
                     'label_clear_of_track_controls','canonical_roles_visible','offsets_preserved',
                     'both_active_roles_same_summary'):
            assert row[name]
        width = 390 if row['geometry'] == 'narrow' else 780
        assert row['requested_logical_size'] == [width,390]
        assert row['logical_size'][0] == width and row['logical_size'][1] >= 390
        assert len(row['paths']) == 6
        for name in row['paths']:
            raw = (out/name).read_bytes(); assert raw.startswith(b'\x89PNG')
            assert struct.unpack('>II', raw[16:24]) == tuple(round(size*scale) for size in row['logical_size'])

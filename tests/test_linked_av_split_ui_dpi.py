"""Fresh-process real-DPR joint-split layout and explicit mocked-worker UI QA."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.parametrize('scale', [1,1.5,2])
def test_joint_split_bilingual_narrow_dpi(tmp_path,scale):
    root=Path(__file__).resolve().parents[1]; out=tmp_path/'screens'
    result=subprocess.run([sys.executable,str(root/'scripts/capture_linked_av_split_ui.py'),
        '--output',str(out),'--scale',str(scale)],cwd=root,
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads((out/'report.json').read_text())
    assert report['status']=='passed' and report['identity_only_fixture'] and report['mocked_media_worker']
    assert not report['native_windows_macos_validation'] and not report['actual_media_proof_claimed']
    assert report['screenshot_bytes']<4*1024*1024 and len(report['records'])==4
    for row in report['records']:
        assert row['device_pixel_ratio']==scale
        assert row['available_width'] in (390,780)
        for flag in ('ready_verified_stale_failed_cancelled','same_plan_during_preview',
                     'one_shared_entry_qualification','localized_accessible_buttons',
                     'wrapped_full_text_scrollable','terminal_feedback_full_when_fitting_else_full_first_line',
                     'unchanged_state_preserves_user_scroll','late_cancel_result_ignored','no_project_or_source_changes'):
            assert row[flag]
        for name in row['paths']: assert (out/name).read_bytes().startswith(b'\x89PNG')


@pytest.mark.parametrize('scale', [1, 2])
def test_affine_joint_split_bilingual_narrow_dpi(tmp_path, scale):
    root = Path(__file__).resolve().parents[1]; out = tmp_path/'affine-screens'
    result = subprocess.run([sys.executable, str(root/'scripts/capture_linked_av_split_ui.py'),
        '--output', str(out), '--scale', str(scale), '--affine'], cwd=root,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen'), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr
    report = json.loads((out/'report.json').read_text())
    assert report['status'] == 'passed' and report['mocked_media_worker']
    assert not report['native_windows_macos_validation'] and not report['actual_media_proof_claimed']
    assert len(report['records']) == 4
    assert len(report['capture_geometry']) == 20
    for capture in report['capture_geometry'].values():
        assert capture['device_pixel_ratio'] == scale
        assert capture['physical_width'] == capture['logical_width'] * scale
        assert capture['physical_height'] == capture['logical_height'] * scale
        assert capture['logical_width'] <= capture['available_width'] - 32
    for row in report['records']:
        assert row['affine'] and row['device_pixel_ratio'] == scale
        assert row['ready_verified_stale_failed_cancelled'] and row['no_project_or_source_changes']
        for name in row['paths']: assert (out/name).read_bytes().startswith(b'\x89PNG')

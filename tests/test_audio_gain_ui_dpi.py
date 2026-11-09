"""Fresh-process actual bilingual gain editor screens and keyboard at real DPRs."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('scale',[1,1.25,1.5,2])
def test_audio_gain_bilingual_narrow_dpi_exact_sample_input_and_cancel(tmp_path,scale):
    root=Path(__file__).resolve().parents[1];out=tmp_path/'screens'
    result=subprocess.run([sys.executable,str(root/'scripts/capture_audio_gain_ui.py'),
        '--output',str(out),'--scale',str(scale)],cwd=root,
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads((out/'report.json').read_text())
    assert report['status']=='passed' and report['identity_only_fixture']
    assert not report['native_windows_macos_validation'] and report['screenshot_bytes']<2*1024*1024
    assert len(report['records'])==4
    for row in report['records']:
        assert row['device_pixel_ratio']==scale and row['horizontal_scroll']==0
        for key in ('exact_large_sample_input','keyboard_add_undo_redo','ghost_escape_no_commit',
                    'draft_close_no_commit','footer_visible','accessible_controls','source_unchanged'):assert row[key]
        if row['geometry']=='narrow':assert row['logical_size'][0]<=388 and row['logical_size'][1]<=536
        for path in row['paths']:assert (out/path).read_bytes().startswith(b'\x89PNG')

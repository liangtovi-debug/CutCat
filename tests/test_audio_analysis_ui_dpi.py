"""Real analysis dialog geometry and screenshots in independent Qt processes."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('scale', [1, 1.5])
def test_dialog_locales_fonts_narrow_layout_and_real_analysis(tmp_path, scale):
    root = Path(__file__).resolve().parents[1]
    output = Path(os.environ.get('AUTO_FRAME_REVIEW_EVIDENCE', str(tmp_path))) / 'audio-analysis' / f'dpr-{scale:g}'
    process = subprocess.run([sys.executable, str(root / 'scripts/capture_audio_analysis_ui.py'),
        '--output', str(output), '--scale', str(scale)], cwd=root,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen'), capture_output=True, text=True, timeout=90)
    (output / 'capture.log').write_text(process.stdout + process.stderr, encoding='utf-8')
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    assert report['status'] == 'passed' and report['real_tracked_analysis']
    assert report['project_unchanged'] and not report['native_windows_macos_validation']
    assert report['font_proof']['cjk']['glyph_count'] > 0
    if os.environ.get('LUMEN_TEST_CJK_FONT_DIR'):
        assert report['font_proof']['fixture']['font_sha256']
        assert report['font_proof']['fixture']['license_sha256']
    geometry = [json.loads(row) for row in (output / 'geometry.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(geometry) == 8 and all(row['horizontal_maximum'] == 0 for row in geometry)
    assert len(report['records']) == 8
    assert len(report['workspace_records']) == 4
    assert report['measurements']['overload_counts'][0] > 0
    assert report['measurements']['overload_counts'][1] == 0
    for row in report['records']:
        assert row['device_pixel_ratio'] == scale
        assert row['horizontal_scroll'] == 0
        assert (output / row['screenshot']).read_bytes().startswith(b'\x89PNG')
        if row['bottom_screenshot']:
            assert (output / row['bottom_screenshot']).read_bytes().startswith(b'\x89PNG')
    for row in report['workspace_records']:
        assert row['analyze_action_enabled']
        for key in ('screenshot', 'menu_screenshot'):
            assert (output / row[key]).read_bytes().startswith(b'\x89PNG')

"""Actual MainWindow geometry, fonts, scrollability, and separate-process DPI QA.

PNG evidence and detailed JSON remain in pytest's temporary output directory.
These checks do not replace human screenshot review or native release testing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/capture_ui_alignment.py'


def capture(tmp_path, scale, *extra):
    out = tmp_path / f'scale-{scale:g}'
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR=str(scale))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), '--output', str(out), '--scale', str(scale), *extra],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120,
    )
    report_path = out / 'report.json'
    assert report_path.is_file(), result.stdout + result.stderr
    return result, json.loads(report_path.read_text(encoding='utf-8')), out


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('scale', [1., 1.5, 2.])
def test_editor_geometry_all_sizes_modes_and_panels(tmp_path, scale, locale):
    result, report, out = capture(tmp_path, scale, '--locale', locale)
    assert report['locale'] == locale
    assert result.returncode == 0, json.dumps(report['failures'][:20], ensure_ascii=False, indent=2) + result.stderr
    assert report['status'] == 'passed'
    assert report['failures'] == []
    assert report['human_visual_review'] == 'required separately'
    assert report['fixture']['synthetic_media'] is True
    assert report['fixture']['rendered_preview'] is False
    assert report['fixture']['model_downloads'] is False
    assert report['record_count'] == 75
    sizes = {(1366, 768), (1440, 900), (1920, 1080)}
    assert {tuple(row['logical_size']) for row in report['records']} == sizes
    assert {row['mode'] for row in report['records']} == {'simple', 'professional'}
    assert {row['state'] for row in report['records']} >= {
        'clip', 'clip-edit', 'clip-restoration', 'clip-audio', 'clip-wheels', 'clip-curves', 'captions',
        'caption-appearance', 'tts', 'tts-audition', 'local-ai', 'local-ai-search',
        'stickers', 'clip-effects',
    }
    assert sum(row['checks']['baseline_pairs'] for row in report['records']) >= 30
    for row in report['records']:
        assert row['violations'] == []
        assert row['actual_size'] == row['logical_size']
        assert row['device_pixel_ratio'] == pytest.approx(scale)
        assert row['checks']['layout_items'] > 30
        assert row['checks']['labels'] > 10
        assert row['checks']['edit_rects'] >= 3
        assert row['checks']['form_rows'] >= 1
        assert row['checks']['scroll_reachability'] >= 1
        assert row['checks']['accessible_fields'] >= 1
        assert any(entry['wrapped'] for entry in row['labels'])
        assert all(edit['edit_rect'][2] > 0 and edit['edit_rect'][3] > 0 for edit in row['edit_controls'])
        png = (out / row['screenshot']).read_bytes()
        assert png[:8] == b'\x89PNG\r\n\x1a\n'
        width, height = struct.unpack('>II', png[16:24])
        assert [width, height] == row['physical_pixels']
        assert width == pytest.approx(row['logical_size'][0] * scale, abs=1)
        assert height == pytest.approx(row['logical_size'][1] * scale, abs=1)


def test_larger_cjk_ui_font_keeps_forms_readable(tmp_path):
    result, report, _ = capture(tmp_path, 1., '--ui-font', 'cjk', '--sizes', '1366x768,1920x1080',
                                '--states', 'clip,clip-restoration,clip-wheels,clip-curves,captions,tts,local-ai,stickers')
    assert report['font']['ui_font_mode'] == 'cjk'
    assert result.returncode == 0, json.dumps(report['failures'][:20], ensure_ascii=False, indent=2)
    assert report['status'] == 'passed'


def test_validator_rejects_actual_minimum_size_overflow(tmp_path):
    # Deliberately reintroduce a real shell defect in this isolated process.
    # The validator must fail, rather than merely report successful PNG writes.
    output = tmp_path / 'deliberate-overflow'
    code = '''
import importlib.util
from pathlib import Path
from lumen.ui import MainWindow
spec = importlib.util.spec_from_file_location('alignment_capture', %r)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
original = MainWindow._build_ui

def broken_shell(self):
    original(self)
    self.video.setMinimumHeight(1200)

MainWindow._build_ui = broken_shell
raise SystemExit(module.main(['--output', %r, '--scale', '1', '--sizes', '1366x768',
                              '--modes', 'simple', '--states', 'clip', '--no-screenshots']))
''' % (str(SCRIPT), str(output))
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                            env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR='1'),
                            text=True, capture_output=True, timeout=60)
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    assert result.returncode == 1, result.stdout + result.stderr
    assert report['status'] == 'failed'
    assert any(failure['code'] in {'requested_window_size_changed', 'layout_siblings_overlap',
                                   'layout_item_outside_parent'} for failure in report['failures'])
    assert report['records'][0]['screenshot'] is None

"""Help is read-only and complete, with screen-constrained accessible controls."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

from lumen.i18n import set_locale, tr, _load_catalogs, _CATALOGS
from lumen.ui_help import help_text

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_complete_existing_help_and_narrow_current_claims(locale):
    set_locale(locale)
    text = help_text()
    # Preserve the complete inherited warnings, not only a shortened summary.
    tree = ast.parse((ROOT / 'lumen/ui_help.py').read_text())
    keys = [n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == 'tr' and n.args
            and isinstance(n.args[0], ast.Constant)]
    _load_catalogs()
    for key in keys:
        assert key in _CATALOGS['zh-CN']
        assert _CATALOGS['zh-CN'][key]
    assert 'ProRes' in text and 'H.264' in text and '10' in text
    assert ('not dynamic normalization' if locale == 'en' else '不是动态响度归一化') in text
    assert ('not tracking' if locale == 'en' else '不是跟踪') in text
    assert ('does not claim paid-editor' if locale == 'en' else '不宣称与付费编辑器') in text
    assert ('Audio still cuts' if locale == 'en' else '音频') in text


@pytest.mark.parametrize('scale', [1, 1.5, 2])
@pytest.mark.parametrize('size', [(800,800), (360,480), (320,360)])
def test_actual_screen_geometry_events_and_large_fonts(tmp_path, scale, size):
    output=tmp_path/'capture'
    p=subprocess.run([sys.executable,str(ROOT/'scripts/capture_help_ui.py'),'--output',str(output),'--scale',str(scale),'--width',str(size[0]),'--height',str(size[1])],cwd=ROOT,env=dict(os.environ),capture_output=True,text=True,timeout=60)
    assert p.returncode == 0,p.stdout+p.stderr
    report=json.loads((output/'report.json').read_text())
    assert report['status']=='passed' and not report['native_validation']
    assert len(report['records'])==6
    for r in report['records']:
        assert r['frame_inside_screen'] and r['keyboard'] and r['selectable_readonly']
        assert r['horizontal_scroll']==0 and r['dpr']==scale
        for name in ('top','bottom'):
            assert (output/r[name]).read_bytes().startswith(b'\x89PNG')

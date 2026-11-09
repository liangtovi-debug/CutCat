"""Exact current full-graph/still integration on production FFmpeg (Windows 9).

Complementary to frozen-code FFmpeg 7 tests, not historical golden equivalence.
No runtime substitution, tolerance, font fallback or assertion weakening.
"""
from fractions import Fraction
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from lumen.media import ffmpeg_available
from scripts.benchmark_final_frame_preview import make_scene, make_source
from scripts.production_final_frame_oracle import compare_frames

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg required')
RATES = [Fraction(24), Fraction(25), Fraction(24000, 1001), Fraction(30000, 1001)]


@pytest.fixture(scope='module', params=RATES, ids=['24', '25', '23.976', '29.97'])
def production_source(request, tmp_path_factory):
    root = tmp_path_factory.mktemp('production-final-frame')
    return root, request.param, make_source(root, request.param)


@pytest.mark.parametrize('variant', ['legacy', 'static', 'complex', 'fractional', 'fractional_slow', 'fractional_fast'])
def test_production_full_graph_matches_every_still_frame(production_source, variant):
    root, rate, source = production_source
    project = make_scene(root/variant, rate, source=source, variant=variant)
    compare_frames(project, root/variant/'comparison')


@pytest.mark.parametrize('mode', ['legacy_blur', 'composite_default_vignette'])
def test_production_default_effect_precision_paths(production_source, mode):
    from lumen.model import ClipEffect
    root, rate, source = production_source
    project = make_scene(root/mode, rate, source=source,
                         variant='legacy' if mode == 'legacy_blur' else 'complex')
    if mode == 'legacy_blur':
        project.clips[0].effects.insert(0, ClipEffect('blur', 'blur', .8))
    else:
        project.clips[1].effects[-1].radius = project.clips[1].effects[-1].feather = 1.
    compare_frames(project, root/mode/'comparison', [5])


@pytest.mark.parametrize('transfer,encoding', [('bt709', 'rec709'),
    ('iec61966-2-1', 'srgb'), ('linear', 'linear_rec709')])
def test_production_restoration_spatial_grade_and_transfer(tmp_path, transfer, encoding):
    from lumen.restoration_settings import RestorationSettings
    rate = Fraction(24000, 1001)
    source = make_source(tmp_path/'source', rate, transfer=transfer)
    project = make_scene(tmp_path/'scene', rate, source=source, variant='fractional_slow')
    project.height = 64
    for clip in project.clips:
        clip.grade.input_encoding = encoding
    project.clips[1].restoration = RestorationSettings(enabled=True, deblock=.6, denoise=.7, sharpen=.4)
    project.clips[1].grade.qualifier.matte_smoothing = .7
    project.clips[1].grade.qualifier.matte_morphology = .45
    compare_frames(project, tmp_path/'comparison', [5, 7])


def test_production_pure_motion_uses_actual_full_graph_thread_selector(tmp_path):
    project = make_scene(tmp_path/'scene', Fraction(30000, 1001))
    project.overlays = []; project.captions = []
    for clip in project.clips:
        clip.effects = []; clip.grade.qualifier.window_keyframes = []
    project.validate()
    compare_frames(project, tmp_path/'comparison', [3, 5, 7, 9])
    receipt = json.loads((tmp_path/'comparison/full/runtime.json').read_text())
    assert receipt['filter_complex_threads'] == (4 if (os.cpu_count() or 1)>=4 else 2 if (os.cpu_count() or 1)>=2 else 1)


def test_production_still_comparison_detects_one_changed_byte(tmp_path, monkeypatch):
    from lumen import final_frame_preview as backend
    project = make_scene(tmp_path/'scene', variant='legacy')
    compare_frames(project, tmp_path/'unmodified', [1])
    original = backend.capture_final_frame_snapshot
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        raw = bytearray(result.yuv420p); raw[0] ^= 1
        return SimpleNamespace(width=result.width, height=result.height,
                               rgb_bytes=result.rgb_bytes, yuv420p=bytes(raw))
    monkeypatch.setattr(backend, 'capture_final_frame_snapshot', changed)
    with pytest.raises(AssertionError, match='production still YUV mismatch'):
        compare_frames(project, tmp_path/'changed', [1])

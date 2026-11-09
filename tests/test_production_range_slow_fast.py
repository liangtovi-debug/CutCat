"""Bounded whole-module shard: exact final range YUV/PCM, two scene variants.

The complete 12-case matrix is split across three modules, never reduced.
Each case compares all four windows against current production full-graph output; frozen-code tests remain separate.
"""
from fractions import Fraction

import pytest

from lumen.media import ffmpeg_available
from scripts.benchmark_range_preview import make_av_source, make_av_scene
from scripts.production_range_oracle import compare_ranges

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


@pytest.fixture(scope='module', params=[Fraction(24), Fraction(30000, 1001)], ids=['24', '29.97'])
def source(request, tmp_path_factory):
    root = tmp_path_factory.mktemp('range-final-oracle')
    return root, request.param, make_av_source(root / 'source', request.param)


@pytest.mark.parametrize('variant', ['fractional_slow', 'fractional_fast'])
def test_production_yuv_and_pcm_equal_full_slice(source, variant):
    root, rate, media = source
    project = make_av_scene(root / variant / 'scene', rate, source=media, variant=variant)
    compare_ranges(project, root / variant)

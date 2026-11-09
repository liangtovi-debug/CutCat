"""Bounded whole-module shard: exact final range YUV/PCM, two scene variants.

The complete 12-case matrix is split across three modules, never reduced.
Each case compares all four windows against isolated accepted-source output.
"""
from fractions import Fraction

import pytest

from lumen.media import ffmpeg_available
from scripts.benchmark_range_preview import compare_scene_ranges, make_av_source

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


@pytest.fixture(scope='module', params=[Fraction(24), Fraction(30000, 1001)], ids=['24', '29.97'])
def source(request, tmp_path_factory):
    root = tmp_path_factory.mktemp('range-final-oracle')
    return root, request.param, make_av_source(root / 'source', request.param)


@pytest.mark.parametrize('variant', ['legacy', 'static'])
def test_final_yuv_and_pcm_equal_frozen_full_slice(source, variant):
    root, rate, media = source
    compare_scene_ranges(root, rate, media, variant)

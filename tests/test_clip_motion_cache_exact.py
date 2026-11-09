"""Independent byte-for-byte old/new geq cache acceptance.

Run with candidate on PYTHONPATH. The baseline is loaded as a separate module
with package='lumen', sharing transform/motion types without importing its tree.
Default baseline is the portable frozen function beside this test. Override
CUT_CAT_BASELINE_COMPOSITE for an independent full original-source comparison.
Synthetic media only. No network, encoded-delivery, or timing assertions.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
import subprocess
import sys
import types

import numpy as np
import pytest

import lumen.clip_composite as candidate
from lumen.clip_motion import ClipMotionKeyframe as K
from lumen.clip_transform import ClipTransform
from lumen.media import ffmpeg_binary

BASELINE = Path(os.environ.get('CUT_CAT_BASELINE_COMPOSITE',
    str(Path(__file__).with_name('clip_composite_uncached_fixture.py'))))


def load_baseline():
    module = types.ModuleType('_cutcat_review_baseline_composite')
    module.__package__ = 'lumen'
    module.__file__ = str(BASELINE)
    sys.modules[module.__name__] = module
    exec(compile(BASELINE.read_text(), str(BASELINE), 'exec'), module.__dict__)
    original = module.clip_composite_filters

    def rgb_padding_reference(*args, **kwargs):
        # The frozen reference isolates geq expression caching. Apply only the
        # separately pixel-validated FFmpeg 9 padding/scale fix to its transport graph;
        # keep its uncached expressions and original source/hash untouched.
        graph = original(*args, **kwargs)
        if not graph:
            return graph
        under, layer, overlay = graph
        layer, count = re.subn(
            r'^format=gbrp16le,scale=flags=neighbor:in_range=full:out_range=full,'
            r'format=gbrap16le,(pad=\d+:\d+:0:0:color=black,)',
            r'format=gbrp16le,\1scale=flags=neighbor:in_range=full:out_range=full:threads=1,format=gbrap16le,',
            layer)
        assert count == 1, 'Frozen reference padding prefix changed unexpectedly'
        return under, layer, overlay

    module.clip_composite_filters = rgb_padding_reference
    return module


baseline = load_baseline()

# Nondefault N=0 is essential: a zero-initialized marker must not skip setup.
CASES = {
    'linear_nondefault_zero': (64, 48, [K(0, x=-7.125, y=3.75, scale=.3, opacity=.23),
        K(3, x=5.4, y=-2.6, scale=1.2, opacity=.87), K(7, x=-.5, scale=.7, opacity=.1)],
        ClipTransform(rotation=27, mirror_y=True)),
    'first_positive_key': (64, 48, [K(3, x=8.5, y=-2.5, scale=.4, opacity=.65),
        K(5, x=-6, y=5, scale=1, opacity=0)], ClipTransform(rotation=-90, mirror_x=True)),
    'negative_and_hold_boundaries': (64, 48, [K(-4, x=-12, scale=.6, opacity=.2, interpolation='hold'),
        K(2, x=16384, scale=.1, opacity=1, interpolation='hold'),
        K(4, x=0, scale=1, opacity=1), K(6, x=-16384, scale=4, opacity=0)],
        ClipTransform(rotation=180)),
    'single_constant_key': (64, 48, [K(-10, x=.25, y=-.75, scale=1.25, opacity=.53)],
        ClipTransform(rotation=-31, mirror_x=True, mirror_y=True)),
    'maximum_keys_extreme_frames': (64, 48,
        [K(-2**53+1, x=1)] + [K(i, x=(i%7)-3, y=(i%11)-5,
         scale=.1+(i%9)*.3, opacity=(i%5)/4,
         interpolation='hold' if i%3 == 0 else 'linear') for i in range(126)]
         + [K(2**53-1, x=4)], ClipTransform(rotation=90)),
    'narrow_and_short': (2, 2, [K(0, x=.25, y=-.1, scale=.9, opacity=.73),
        K(3, x=-.25, y=.1, scale=1.1, opacity=.13)], ClipTransform()),
    'slice_short_height': (70, 6, [K(0, x=-.2, scale=.95, opacity=.15),
        K(4, x=.2, scale=1.05, opacity=.85)], ClipTransform(rotation=0, mirror_y=True)),
    'odd_height_10bit': (66, 7, [K(0, x=.125, y=-.25, scale=.7, opacity=.3),
        K(3, x=-.125, y=.25, scale=1.2, opacity=.8)], ClipTransform(rotation=-19)),
}


def fixture_bytes(width, height, high, count):
    """Varying frames/all-code YUV and nonuniform full-precision RGB16."""
    rng = np.random.default_rng(183940 + width * 17 + height)
    main = []
    foreground = []
    for _ in range(count):
        main.extend(rng.integers(0, 1024 if high else 256,
            (height if plane == 0 or high else height//2,
             width if plane == 0 else width//2), dtype=np.uint16).astype('<u2' if high else 'u1').tobytes()
            for plane in range(3))
        foreground.append(rng.integers(0, 65536, (3, height, width), dtype=np.uint16).astype('<u2').tobytes())
    return b''.join(main), b''.join(foreground)


def render(module, tmp_path, case, high, transparent, threads, stage, *, count=8):
    width, height, keys, transform = CASES[case]
    main, foreground = fixture_bytes(width, height, high, count)
    picture_format = 'yuv422p10le' if high else 'yuv420p'
    under, layer, overlay = module.clip_composite_filters(
        module.ClipComposite(transparent, .39), transform, width, height,
        high_bit=high, motion_keys=keys)
    source = tmp_path / 'source.gbrp16le'
    source.write_bytes(foreground)
    command = [ffmpeg_binary(), '-hide_banner', '-v', 'error', '-nostdin',
        '-noauto_conversion_filters', '-filter_complex_threads', str(threads)]
    if stage == 'rgb16_alpha':
        # Inspect all 16-bit RGBA samples BEFORE zscale/YUV rounding can hide an error.
        graph = f'[0:v]setpts=PTS+113,{layer.split(",zscale=", 1)[0]}[out]'
        command += ['-f', 'rawvideo', '-pix_fmt', 'gbrp16le', '-s', f'{width}x{height}',
            '-r', '30000/1001', '-i', str(source)]
        output_format = 'gbrap16le'
        expected_size = count * width * height * 8
        input_bytes = None
    else:
        graph = f'[0:v]setpts=PTS+113,{under}[main];[1:v]setpts=PTS+113,{layer}[layer];[main][layer]{overlay}[out]'
        command += ['-f', 'rawvideo', '-pix_fmt', picture_format, '-s', f'{width}x{height}',
            '-r', '30000/1001', '-i', 'pipe:0', '-f', 'rawvideo', '-pix_fmt', 'gbrp16le',
            '-s', f'{width}x{height}', '-r', '30000/1001', '-i', str(source)]
        output_format = picture_format
        expected_size = len(main)
        input_bytes = main
    graph_path = tmp_path / 'exact.ffgraph'
    graph_path.write_text(graph)
    command += ['-/filter_complex', str(graph_path), '-map', '[out]', '-frames:v', str(count),
        '-an', '-c:v', 'rawvideo', '-threads', '1', '-pix_fmt', output_format,
        '-fps_mode', 'passthrough', '-f', 'rawvideo', 'pipe:1']
    result = subprocess.run(command, input=input_bytes, capture_output=True, timeout=40)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert len(result.stdout) == expected_size
    return result.stdout


@pytest.mark.parametrize('case,high', [
    (name, high) for name, (_, height, _, _) in CASES.items()
    for high in (False, True) if high or height % 2 == 0
])
@pytest.mark.parametrize('transparent', [False, True])
@pytest.mark.parametrize('threads', [1, 4])
@pytest.mark.parametrize('stage', ['composite', 'rgb16_alpha'])
def test_motion_cache_exact(tmp_path, case, high, transparent, threads, stage):
    before = render(baseline, tmp_path, case, high, transparent, threads, stage)
    after = render(candidate, tmp_path, case, high, transparent, threads, stage)
    assert before == after, {
        'case': case, 'high': high, 'transparent': transparent, 'threads': threads,
        'stage': stage, 'baseline_sha256': hashlib.sha256(before).hexdigest(),
        'candidate_sha256': hashlib.sha256(after).hexdigest(),
        'different_bytes': sum(a != b for a, b in zip(before, after)),
        'first_different_byte': next(i for i, (a, b) in enumerate(zip(before, after)) if a != b),
    }


@pytest.mark.parametrize('high', [False, True])
@pytest.mark.parametrize('transparent', [False, True])
@pytest.mark.parametrize('opacity', [0, .5, 1])
@pytest.mark.parametrize('keys', [None, []])
def test_static_graph_exact(high, transparent, opacity, keys):
    transform = ClipTransform(x=.12345, y=-2, scale=.83, rotation=-31, mirror_x=True)
    args = (transform, 64, 48)
    kwargs = dict(high_bit=high, motion_keys=keys)
    assert baseline.clip_composite_filters(baseline.ClipComposite(transparent, opacity), *args, **kwargs) == \
        candidate.clip_composite_filters(candidate.ClipComposite(transparent, opacity), *args, **kwargs)


def test_uncached_reference_body_is_frozen():
    """A reference edit must not silently redefine exact-output acceptance."""
    import ast
    path = Path(__file__).with_name('clip_composite_uncached_fixture.py')
    source = path.read_text()
    function = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == 'clip_composite_filters')
    body = ''.join(source.splitlines(keepends=True)[function.lineno-1:function.end_lineno])
    assert hashlib.sha256(body.encode()).hexdigest() == \
        '4ac5dc8db601ef5de5d38443524ad23d5a9a08349f993ebbc552e65431597c5a'

"""Portable CJK, escaped-cache, failure-publication and child-cleanup coverage.

The process tests bound the capability probe only. Existing local font discovery
and caption layout preparation do not have a cooperative cancellation deadline;
these tests make no whole-export cancellation-time guarantee.
"""
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
import pytest

from lumen import highbit_captions as high
from lumen.caption_render import CaptionRenderError, font_identity
from lumen.media import ffmpeg_available, ffmpeg_binary
from lumen.model import Caption, CaptionStyle, Project
from lumen.render import RenderCancelled, RenderEngine, RenderError


PROFILE = 'prores_422_hq_10bit'
requires_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


@pytest.fixture(scope='module')
def cjk_font():
    style = CaptionStyle()
    # A configured CI fixture must actually work. Do not hide corrupt or missing
    # provisioning behind a system-font fallback or skip.
    if 'LUMEN_TEST_CJK_FONT_DIR' in os.environ:
        from scripts.provision_test_font import FONT_FAMILY, ProvisionError, verify_fixture
        directory = os.environ['LUMEN_TEST_CJK_FONT_DIR']
        if not directory.strip():
            pytest.fail('LUMEN_TEST_CJK_FONT_DIR is configured but empty')
        try:
            verified = verify_fixture(directory)
            style.font_family = FONT_FAMILY
            style.font_directory = verified['font_directory']
            identity = font_identity(style)
        except (ProvisionError, CaptionRenderError, OSError) as error:
            pytest.fail(f'Configured CJK test fixture is invalid: {error}')
        assert Path(identity[0]) == Path(verified['font_file'])
        return style.font_family, style.font_directory
    for family in ('Noto Sans CJK SC', 'Microsoft YaHei', 'PingFang SC'):
        style.font_family = family
        try:
            font_identity(style)
        except CaptionRenderError:
            continue
        return family, ''
    pytest.skip('No local CJK test font: provision the explicit fixture or install a CJK font')


@pytest.fixture
def cjk_project(cjk_font):
    family, directory = cjk_font
    return Project(
        width=512, height=256,
        captions=[Caption('middle', 1, 1, '中文字幕\n颜色描边'),
                  Caption('end', 3, 1, '再见')],
        caption_style=CaptionStyle(font_family=family, font_directory=directory,
                                   font_size=40, margin_bottom=20, color='#FF0000',
                                   outline_color='#0000FF', outline_width=4))


@pytest.fixture
def engine():
    value = RenderEngine(timeout=60)
    try:
        yield value
    finally:
        value.close()


def compiled_planes(engine, project):
    """Read the real production graph before codec ringing affects edge colors."""
    inputs, graph, _ = engine._compile(project, export_profile=PROFILE)
    assert graph.endswith('[aout]')
    graph = graph[:-len('[aout]')] + ',anullsink'
    result = subprocess.run(
        [ffmpeg_binary(), '-hide_banner', '-v', 'error', '-nostdin',
         '-filter_complex_threads', '1', *inputs, '-filter_complex', graph,
         '-map', '[vout]', '-frames:v', str(project.duration_frames),
         '-c:v', 'rawvideo', '-threads', '1', '-pix_fmt', 'yuv422p10le',
         '-f', 'rawvideo', 'pipe:1'], capture_output=True, check=True, timeout=60)
    width, height = project.width, project.height
    pixels = width * height
    frames = np.frombuffer(result.stdout, '<u2').reshape(-1, pixels * 2)
    return (frames[:, :pixels].reshape(-1, height, width),
            frames[:, pixels:pixels * 3 // 2].reshape(-1, height, width // 2),
            frames[:, pixels * 3 // 2:].reshape(-1, height, width // 2))


@requires_ffmpeg
def test_real_cjk_colored_outline_and_special_cache_path(
        tmp_path, engine, cjk_project):
    special = tmp_path / ("cache's [a],;" + (' colon:' if os.name != 'nt' else ''))
    special.mkdir()
    engine._cache_dir.cleanup()
    engine._cache_dir = tempfile.TemporaryDirectory(dir=special)
    before = deepcopy(cjk_project.to_dict())
    y, u, v = compiled_planes(engine, cjk_project)
    u, v = np.repeat(u, 2, axis=2), np.repeat(v, 2, axis=2)
    signed = [plane.astype(np.int32) for plane in (y, u, v)]
    red = np.logical_and.reduce([abs(plane - target) <= 4
                                  for plane, target in zip(signed, (250, 409, 960))])
    blue = np.logical_and.reduce([abs(plane - target) <= 4
                                   for plane, target in zip(signed, (127, 960, 471))])
    assert len(y) == 4
    # Count solid interiors, not anti-aliased 4:2:2 edges or lossy-codec ringing.
    assert np.count_nonzero(red[1]) > 100
    assert np.count_nonzero(blue[1]) > 100
    assert np.count_nonzero(red[3]) > 50
    assert y[0].max() == y[2].max() == 64
    output = engine.render(cjk_project, tmp_path / 'cjk-outline.mov', export_profile=PROFILE)
    assert output.is_file() and output.stat().st_size > 0
    assert not list(tmp_path.glob('.lumen-render-*'))
    assert cjk_project.to_dict() == before


@requires_ffmpeg
@pytest.mark.parametrize('invalid', ['missing_font', 'missing_glyph'])
def test_unavailable_cjk_font_or_glyph_never_publishes(
        tmp_path, engine, cjk_project, invalid):
    if invalid == 'missing_font':
        cjk_project.caption_style.font_family = 'Not A Real Font Family 314159'
        expected = 'not found locally'
    else:
        cjk_project.captions[0].text = '\U0010ffff'
        expected = 'missing glyphs'
    before = deepcopy(cjk_project.to_dict())
    output = tmp_path / f'{invalid}.mov'
    with pytest.raises(RenderError, match=expected):
        engine.render(cjk_project, output, export_profile=PROFILE)
    assert not output.exists()
    assert not list(tmp_path.glob('.lumen-render-*'))
    assert cjk_project.to_dict() == before


@pytest.mark.parametrize('reason', ['timeout', 'cancel'])
def test_probe_kills_and_reaps_real_stalled_subprocess(tmp_path, monkeypatch, reason):
    project = Project(width=320, height=180, captions=[Caption('cue', 0, 1, 'hello')])
    monkeypatch.setattr(high, 'caption_filters', lambda *args: ['drawtext=text=hello'])
    monkeypatch.setattr(high, 'prepare_font', lambda *args: tmp_path / 'unused.ttf')
    actual_popen = subprocess.Popen
    processes = []
    cancel = threading.Event()

    def stall(command, **kwargs):
        # Use the running, trusted Python interpreter, not a platform-specific
        # shell/sleep utility. Pass through the real probe's open file handles.
        process = actual_popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        processes.append(process)
        if reason == 'cancel':
            cancel.set()
        return process

    monkeypatch.setattr(high.subprocess, 'Popen', stall)
    expected = RenderCancelled if reason == 'cancel' else RenderError
    message = 'cancelled' if reason == 'cancel' else 'timed out'
    began = time.monotonic()
    try:
        with pytest.raises(expected, match=message):
            high.highbit_caption_filters(project, tmp_path, .1 if reason == 'timeout' else 30,
                                         cancel, RenderError, RenderCancelled)
        assert len(processes) == 1
        assert processes[0].poll() is not None
        assert processes[0].wait(timeout=1) != 0
        assert time.monotonic() - began < 5
    finally:
        # A failing regression must not itself leave a child running in CI.
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

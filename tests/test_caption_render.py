"""Decoded-pixel integration tests for the shared caption preview/export graph."""
import subprocess
import os
from pathlib import Path
import numpy as np
import pytest
from lumen.media import ffmpeg_available, ffmpeg_binary, probe
from lumen.model import Project, Caption, CaptionStyle, Clip, Grade
from lumen.render import RenderEngine, RenderError
from lumen.caption_render import font_identity, CaptionRenderError

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg required')

@pytest.fixture
def engine():
    engine = RenderEngine(timeout=60)
    yield engine
    engine.close()


def pixels(path, width=320, height=180):
    result = subprocess.run([ffmpeg_binary(), '-v', 'error', '-protocol_whitelist', 'file,pipe',
                             '-i', str(path), '-an', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'],
                            capture_output=True, check=True)
    return np.frombuffer(result.stdout, np.uint8).reshape(-1, height, width, 3)


@pytest.fixture(scope='module')
def latin_font():
    for family in ('DejaVu Sans', 'Arial', 'Helvetica'):
        try:
            font_identity(CaptionStyle(font_family=family))
            return family
        except CaptionRenderError:
            continue
    pytest.skip('No local Latin test font: install DejaVu Sans, Arial, or Helvetica')


@pytest.fixture
def project(latin_font):
    def make(**kwargs):
        return Project(width=320, height=180,
                       captions=[Caption('a', 1, 1, 'HELLO'), Caption('b', 4, 1, 'END')],
                       caption_style=CaptionStyle(font_family=latin_font, font_size=30, margin_bottom=20),
                       **kwargs)
    return make


@pytest.mark.parametrize('num,den', [(30, 1), (30000, 1001), (60000, 1001)])
def test_exact_frame_gates_preview_export_reopen(tmp_path, engine, project, num, den):
    p = project(fps_num=num, fps_den=den)
    p.save(tmp_path / 'project.lumen')
    restored = Project.load(tmp_path / 'project.lumen')
    assert restored.duration_frames == 5
    for path in (engine.render(restored, tmp_path / 'out.mp4'), engine.preview(restored)):
        frames = pixels(path)
        assert len(frames) == 5
        for index in (0, 2, 3): assert frames[index].max() < 8
        for index in (1, 4): assert np.count_nonzero(frames[index] > 160) > 100
    assert engine.frame(restored, 1).startswith(b'\x89PNG')


def test_literal_multiline_and_injection_strings(tmp_path, engine, project):
    p = project()
    p.captions = [Caption('literal', 0, 2, "{\\an8}\\N 100% %{n}\n[foo]; 'a': \\ end")]
    p.caption_style.font_size = 16
    inputs, graph, _ = engine._compile(p)
    assert 'expansion=none' in graph
    assert '%{n}' not in graph and '\\an8' not in graph
    assert not inputs
    frames = pixels(engine.render(p, tmp_path / 'literal.mp4'))
    assert frames.max() > 240
    # Literal text is static; no expression expansion or karaoke-like changes.
    assert np.mean(np.abs(frames[0].astype(float) - frames[1])) < 1


def test_missing_glyph_and_missing_font_are_errors(tmp_path, engine, project):
    p = project()
    # U+10FFFF is a valid Unicode noncharacter absent from ordinary fonts.
    # This checks missing-glyph rejection without assuming Arial lacks Chinese.
    p.captions[0].text = '\U0010ffff'
    with pytest.raises(RenderError, match='missing glyphs'):
        engine.render(p, tmp_path / 'tofu.mp4')
    assert not (tmp_path / 'tofu.mp4').exists()
    p.caption_style.font_family = 'Not A Real Font Family 314159'
    with pytest.raises(RenderError, match='not found locally'):
        engine.preview(p)


def test_real_chinese_font_decoded_pixels(tmp_path, engine):
    # A CI-provided fixture must be genuine and usable. Never hide broken CI
    # provisioning with a skip or a system-font fallback, even on Windows.
    p = Project(width=320, height=180,
                captions=[Caption('a', 1, 1, '中文测试'), Caption('b', 4, 1, '再见')],
                caption_style=CaptionStyle(font_size=30, margin_bottom=20))
    if 'LUMEN_TEST_CJK_FONT_DIR' in os.environ:
        from scripts.provision_test_font import FONT_FAMILY, ProvisionError, verify_fixture
        directory = os.environ['LUMEN_TEST_CJK_FONT_DIR']
        if not directory.strip():
            pytest.fail('LUMEN_TEST_CJK_FONT_DIR is configured but empty')
        try:
            verified = verify_fixture(directory)
            p.caption_style.font_family = FONT_FAMILY
            p.caption_style.font_directory = verified['font_directory']
            identity = font_identity(p.caption_style)
        except (ProvisionError, CaptionRenderError, OSError) as error:
            pytest.fail(f'Configured CJK test fixture is invalid: {error}')
        assert Path(identity[0]) == Path(verified['font_file']), 'CI must render the pinned fixture'
    else:
        for family in ('Noto Sans CJK SC', 'Microsoft YaHei', 'PingFang SC'):
            p.caption_style.font_family = family
            try:
                font_identity(p.caption_style)
                break
            except CaptionRenderError:
                continue
        else:
            pytest.skip('No local CJK test font: provision the explicit test fixture or install a local CJK font')
    frames = pixels(engine.render(p, tmp_path / 'chinese.mp4'))
    assert len(frames) == 5
    for index in (0, 2, 3): assert frames[index].max() < 8
    assert np.count_nonzero(frames[1] > 160) > 100
    assert np.count_nonzero(frames[4] > 160) > 100


def test_grade_bypass_keeps_caption_and_caption_tail(tmp_path, engine, project):
    source = tmp_path / 'black.mp4'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-f', 'lavfi', '-i',
                    'color=black:s=320x180:r=30:d=0.1', '-c:v', 'libx264', str(source)],
                   capture_output=True, check=True)
    m = probe(source)
    p = project(media=[m], clips=[Clip('clip', m.id, 0, 0, 0, 3, grade=Grade(exposure=-3))])
    for enabled in (True, False):
        p.clips[0].grade.enabled = enabled
        frames = pixels(engine.render(p, tmp_path / f'grade-{enabled}.mp4'))
        assert len(frames) == 5
        assert frames[1].max() > 240  # Caption is not darkened by clip grade.
        assert frames[3].max() < 8  # Past media end is black.
        assert frames[4].max() > 240  # Caption-only tail survives.


def test_custom_font_directory_special_characters(tmp_path, engine, project):
    import shutil
    p = project()
    source = font_identity(p.caption_style)[0]
    directory = tmp_path / "fonts's [custom],; path"
    directory.mkdir()
    shutil.copy(source, directory / ("font's [a],;" + Path(source).suffix))
    p.caption_style.font_directory = str(directory)
    frames = pixels(engine.render(p, tmp_path / 'custom.mp4'))
    assert frames[1].max() > 240


def test_caption_cache_paths_with_apostrophes(tmp_path, engine, project):
    import tempfile
    special = tmp_path / ("cache's [a],;" + (" colon:" if os.name != "nt" else ""))
    special.mkdir()
    engine._cache_dir.cleanup()
    engine._cache_dir = tempfile.TemporaryDirectory(dir=special)
    frames = pixels(engine.render(project(), tmp_path / 'escaped.mp4'))
    assert frames[1].max() > 240


def test_font_replacement_invalidates_preview(tmp_path, engine, project):
    import os
    import shutil
    p = project()
    source = font_identity(p.caption_style)[0]
    directory = tmp_path / 'fonts'
    directory.mkdir()
    local = directory / ('local' + Path(source).suffix)
    shutil.copy(source, local)
    p.caption_style.font_directory = str(directory)
    before = engine.preview(p)
    stat = local.stat()
    os.utime(local, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    after = engine.preview(p)
    assert before != after
    assert pixels(after)[1].max() > 240


def test_caption_graph_file_uses_supported_literal_argv(tmp_path, engine, project, monkeypatch):
    """FFmpeg 7+ file-option syntax survives Windows and shell metacharacters."""
    import tempfile
    special = tmp_path / "graph's [a],; spaced"
    special.mkdir()
    engine._cache_dir.cleanup()
    engine._cache_dir = tempfile.TemporaryDirectory(dir=special)
    original = subprocess.Popen
    graphs = []

    def inspect(command, *args, **kwargs):
        assert '-filter_complex_script' not in command
        if '-/filter_complex' in command:
            assert '-filter_complex' not in command
            graph_path = Path(command[command.index('-/filter_complex') + 1])
            assert graph_path.parent == Path(engine._cache_dir.name).resolve()
            assert graph_path.is_file()
            graphs.append(graph_path.read_text(encoding='utf-8'))
            assert not kwargs.get('shell', False)
        return original(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', inspect)
    p = project()
    export = engine.render(p, tmp_path / 'graph.mp4')
    preview = engine.preview(p)
    assert len(graphs) == 2 and graphs[0] == graphs[1]
    for path in (export, preview):
        frames = pixels(path)
        assert frames[0].max() < 8
        assert frames[1].max() > 240

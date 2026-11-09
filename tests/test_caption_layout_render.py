"""Actual decoded-pixel tests for measured wrapping in the shared graph."""
from dataclasses import replace
import os
from pathlib import Path
import unicodedata

import numpy as np
import pytest

from lumen.caption_render import CaptionRenderError, font_identity
from lumen.media import ffmpeg_available
from lumen.model import CaptionStyle, Project
from lumen.render import RenderEngine, RenderError
from lumen.subtitles import format_srt, read_srt, write_srt
from scripts.verify_caption_layout import (CASES, capture_ui, compare_frames, decode_frames,
    make_project, project_layout, qimage_rgb, verify_bounds)

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg required')


@pytest.fixture(scope='module')
def local_font():
    """Respect the pinned CI fixture, never hide a broken configured font."""
    directory = os.environ.get('LUMEN_TEST_CJK_FONT_DIR')
    if directory is not None:
        from scripts.provision_test_font import FONT_FAMILY, verify_fixture
        assert directory.strip(), 'Configured CJK font directory must not be empty'
        verified = verify_fixture(directory)
        selected = font_identity(CaptionStyle(font_family=FONT_FAMILY,
                                              font_directory=verified['font_directory']))
        assert Path(selected[0]) == Path(verified['font_file'])
        return FONT_FAMILY, verified['font_directory']
    for family in ('Noto Sans CJK SC', 'Microsoft YaHei', 'PingFang SC'):
        try:
            font_identity(CaptionStyle(font_family=family))
            return family, ''
        except CaptionRenderError:
            continue
    pytest.skip('Install a local CJK font or configure LUMEN_TEST_CJK_FONT_DIR')


@pytest.fixture
def engine():
    value = RenderEngine(timeout=90)
    try:
        yield value
    finally:
        value.close()


@pytest.mark.parametrize('case', CASES, ids=[case.name for case in CASES])
def test_real_wrapped_preview_export_pixels_and_raw_persistence(tmp_path, engine, local_font, case):
    project = make_project(case, *local_font)
    path = tmp_path / 'original.lumen'
    project.save(path)
    restored = Project.load(path)
    before = restored.to_dict()
    srt_before = format_srt(restored.captions, restored.fps)
    layout = project_layout(restored, engine._cache_dir.name)
    assert 1 < len(layout.lines) <= restored.caption_style.max_lines
    assert layout.text.replace('\n', '') == case.text.replace('\n', '')
    if case.name == 'manual-break':
        assert layout.lines[0] == 'Manual break'
    if case.name in ('long-word-combining', 'accented-boundary', 'stacked-accents'):
        assert '\u0301' in layout.text
        assert all(not unicodedata.combining(line[0]) for line in layout.lines if line)
    exported = decode_frames(engine.render(restored, tmp_path / 'export.mp4'), case.width, case.height)
    graphfiles = list(Path(engine._cache_dir.name).glob('*.ffgraph'))
    assert graphfiles and all('textfile=' in path.read_text() and 'expansion=none' in path.read_text()
                              for path in graphfiles)
    textfiles = list(Path(engine._cache_dir.name).glob('*.txt'))
    assert any(path.read_text(encoding='utf-8') == layout.text for path in textfiles)
    preview = decode_frames(engine.preview(restored), case.width, case.height)
    assert len(preview) == len(exported) == 6
    for preview_frame, export_frame in zip(preview, exported):
        verify_bounds(preview_frame, restored)
        verify_bounds(export_frame, restored)
        compare_frames(preview_frame, export_frame)
    # Qt consumes a real frame extracted from that same preview at output size.
    from PySide6.QtGui import QImage
    qt_frame = qimage_rgb(QImage.fromData(engine.frame(restored, 0, width=case.width)))
    assert np.array_equal(qt_frame, preview[0])
    assert restored.to_dict() == before
    assert format_srt(restored.captions, restored.fps) == srt_before
    write_srt(tmp_path / 'original.srt', restored.captions, restored.fps)
    assert read_srt(tmp_path / 'original.srt', restored.fps)[0].text == case.text
    restored.save(tmp_path / 'reopened.lumen')
    assert Project.load(tmp_path / 'reopened.lumen').captions[0].text == case.text


@pytest.mark.parametrize('case,locale', [(CASES[0], 'en'), (CASES[2], 'zh-CN')],
                         ids=['english-editor', 'chinese-editor'])
def test_real_qt_editor_keeps_original_text_and_decodes_wrapped_preview(tmp_path, engine, local_font, case, locale):
    project = make_project(case, *local_font, duration=30)
    preview = engine.preview(project)
    result = capture_ui(project, preview, tmp_path / 'editor.png', locale)
    assert result['original_caption_text_unchanged']
    assert (tmp_path / result['screenshot']).is_file()
    from PySide6.QtGui import QImage
    qt_frame = qimage_rgb(QImage(str(tmp_path / result['decoded_frame'])))
    ffmpeg_frame = decode_frames(preview, case.width, case.height)[0]
    compare_frames(qt_frame, ffmpeg_frame)


def test_fractional_accent_advances_remain_inside_margin(tmp_path, engine):
    # This face has a concrete BASIC versus HarfBuzz advance mismatch for e +
    # combining acute. Real drawtext pixels independently guard the boundary.
    for family in ('DejaVu Sans Bold', 'Arial Bold', 'Helvetica Bold'):
        try:
            font_identity(CaptionStyle(font_family=family))
            break
        except CaptionRenderError:
            continue
    else:
        pytest.skip('No local bold Latin face for fractional-advance regression')
    case = replace(CASES[-1], width=320, height=360, font_size=40,
                   text='e\u0301' * 20)
    project = make_project(case, family)
    layout = project_layout(project, engine._cache_dir.name)
    assert all(not unicodedata.combining(line[0]) for line in layout.lines)
    exported = decode_frames(engine.render(project, tmp_path / 'accent.mp4'), case.width, case.height)
    for frame in exported:
        verify_bounds(frame, project)


@pytest.mark.parametrize('style_change', [{'max_lines': 1}, {'font_size': 1000},
                                         {'margin_bottom': 355}],
                         ids=['line-limit', 'font-too-tall', 'vertical-safe-area'])
def test_overflow_refuses_before_output_or_temporary_file(tmp_path, engine, local_font, style_change):
    project = make_project(CASES[0], *local_font)
    project.caption_style = replace(project.caption_style, **style_change)
    original = project.to_dict()
    target = tmp_path / 'must-not-exist.mp4'
    with pytest.raises(RenderError, match='cannot fit safely'):
        engine.render(project, target)
    assert not target.exists()
    assert not list(tmp_path.glob('.lumen-render-*'))
    assert project.to_dict() == original
    with pytest.raises(RenderError, match='cannot fit safely'):
        engine.preview(project)
    assert not list(Path(engine._cache_dir.name).glob('*.mp4'))


def test_style_changes_invalidate_preview_and_remeasure(tmp_path, engine, local_font):
    project = make_project(CASES[0], *local_font)
    before = engine.preview(project)
    before_layout = project_layout(project, engine._cache_dir.name)
    assert engine.preview(project) == before
    project.caption_style.safe_margin_percent = 20
    after = engine.preview(project)
    assert after != before
    after_layout = project_layout(project, engine._cache_dir.name)
    assert after_layout.text != before_layout.text
    verify_bounds(decode_frames(after, project.width, project.height)[0], project)
    project.caption_style.max_lines = 1
    with pytest.raises(RenderError, match='cannot fit safely'):
        engine.preview(project)
    # A formerly successful cached image must not mask an invalid current style.
    project.caption_style.max_lines = 6
    assert engine.preview(project) == after
    assert project.captions[0].text == CASES[0].text

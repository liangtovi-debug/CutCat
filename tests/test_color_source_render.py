"""Real high-bit-depth fixtures prove explicit ingress and shared delivery math."""
from copy import deepcopy
from dataclasses import replace
import json
import subprocess

import numpy as np
import pytest

from lumen import color
from lumen.color_media import read_source_color
from lumen.media import MediaError, ffmpeg_available, ffmpeg_binary, ffprobe_binary, probe
from lumen.model import Clip, Grade, Project
from lumen.render import RenderEngine, RenderError

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
WIDTH, HEIGHT = 64, 48


def rgb_source(tmp_path, rgb, transfer='unknown', range_tag='pc'):
    path = tmp_path / f'rgb-{transfer}-{range_tag}.mkv'
    tags = ['-colorspace', 'rgb', '-color_range', range_tag, '-color_primaries', 'bt709']
    if transfer != 'unknown':
        tags += ['-color_trc', transfer]
    command = [ffmpeg_binary(), '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'gbrp16le',
               '-s', f'{WIDTH}x{HEIGHT}', '-r', '30', *tags, '-i', 'pipe:0', '-frames:v', '3',
               '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'gbrp16le', *tags]
    planar = np.moveaxis(rgb[..., [1, 2, 0]], -1, 0).astype('<u2')
    subprocess.run(command + [str(path)], input=planar.tobytes() * 3,
                   capture_output=True, check=True)
    return probe(path)


def decoded(path):
    result = subprocess.run([ffmpeg_binary(), '-v', 'error', '-i', str(path), '-frames:v', '1',
                             '-pix_fmt', 'rgb48le', '-f', 'rawvideo', 'pipe:1'],
                            capture_output=True, check=True)
    return np.frombuffer(result.stdout, '<u2').reshape(HEIGHT, WIDTH, 3).astype(np.float32) / 65535


def project(media, encoding='rec709', enabled=True):
    clip = Clip('c', media.id, 0, 0, 0, 1, grade=Grade(input_encoding=encoding, enabled=enabled))
    return Project(width=WIDTH, height=HEIGHT, media=[media], clips=[clip])


@pytest.mark.parametrize('encoding,transfer', [('srgb', 'iec61966-2-1'), ('linear_rec709', 'linear')])
def test_tagged_input_normalized_once_in_shared_preview_export_and_before(tmp_path, encoding, transfer):
    rgb = np.broadcast_to(np.array([.2, .5, .8]) * 65535, (HEIGHT, WIDTH, 3)).astype(np.uint16)
    media = rgb_source(tmp_path, rgb, transfer)
    assert media.color_transfer == transfer and media.color_primaries == 'bt709' and media.color_range == 'pc'
    p = project(media, encoding)
    raw = rgb.astype(np.float32) / 65535
    expected = color.normalize_input(raw, encoding)
    twice = color.normalize_input(expected, encoding)
    assert np.max(np.abs(expected - twice)) > .03
    engine = RenderEngine(timeout=30)
    try:
        stage = engine._graded_segment(media, p.clips[0], p, None)
        np.testing.assert_allclose(decoded(stage), expected, atol=2 / 65535)
        preview, exported = engine.preview(p), engine.render(p, tmp_path / 'normalized.mp4')
        for output in (preview, exported):
            np.testing.assert_allclose(decoded(output)[24, 32], expected[24, 32], atol=.025)
        metadata = subprocess.run([ffprobe_binary(), '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=color_transfer,color_primaries,color_space,color_range', '-of', 'json',
            str(exported)], capture_output=True, text=True, check=True)
        tags = json.loads(metadata.stdout)['streams'][0]
        assert tags == dict(color_range='tv', color_space='bt709', color_transfer='bt709', color_primaries='bt709')
        p.clips[0].grade.enabled = False
        p.clips[0].grade.exposure = 2
        before = engine._graded_segment(media, p.clips[0], p, None)
        np.testing.assert_array_equal(decoded(before), decoded(stage))
        np.testing.assert_allclose(decoded(engine.preview(p))[24, 32], expected[24, 32], atol=.025)
    finally:
        engine.close()


def test_tagged_srgb_requires_matching_assignment_even_when_bypassed(tmp_path):
    rgb = np.full((HEIGHT, WIDTH, 3), 32768, np.uint16)
    media = rgb_source(tmp_path, rgb, 'iec61966-2-1')
    p = project(media, enabled=False)
    engine = RenderEngine(timeout=30)
    try:
        for operation in [lambda: engine.preview(p), lambda: engine.render(p, tmp_path / 'bad.mp4')]:
            with pytest.raises(RenderError, match='Select sRGB in Input encoding'):
                operation()
        assert not (tmp_path / 'bad.mp4').exists()
    finally:
        engine.close()


@pytest.mark.parametrize('range_tag', ['tv', 'pc'])
@pytest.mark.parametrize('matrix,kr,kb', [('bt709', .2126, .0722), ('smpte170m', .299, .114), ('bt470bg', .299, .114)])
def test_yuv_matrix_and_range_independently_match_reference(tmp_path, range_tag, matrix, kr, kb):
    values = (400, 600, 700)
    planes = b''.join(np.full((HEIGHT, WIDTH), value, '<u2').tobytes() for value in values)
    path = tmp_path / 'yuv.mkv'
    tags = ['-colorspace', matrix, '-color_range', range_tag, '-color_trc', 'bt709', '-color_primaries', 'bt709']
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'yuv444p10le',
        '-s', f'{WIDTH}x{HEIGHT}', '-r', '30', *tags, '-i', 'pipe:0', '-frames:v', '3', '-c:v', 'ffv1',
        '-level', '3', '-pix_fmt', 'yuv444p10le', *tags, str(path)],
        input=planes * 3, capture_output=True, check=True)
    y = (values[0] - 64) / 876 if range_tag == 'tv' else values[0] / 1023
    cb, cr = ((value - 512) / (896 if range_tag == 'tv' else 1023) for value in values[1:])
    r, b = y + (2 - 2 * kr) * cr, y + (2 - 2 * kb) * cb
    g = (y - kr * r - kb * b) / (1 - kr - kb)
    expected = np.array([r, g, b])
    media = probe(path)
    assert media.color_space == matrix and media.color_transfer == 'bt709'
    assert media.color_primaries == 'bt709' and media.color_range == range_tag
    p = project(media)
    engine = RenderEngine(timeout=30)
    try:
        stage = engine._graded_segment(media, p.clips[0], p, None)
        np.testing.assert_allclose(decoded(stage)[24, 32], expected, atol=3 / 65535)
    finally:
        engine.close()


def test_actual_limited_rgb_is_rejected_instead_of_silently_compressed(tmp_path):
    rgb = np.broadcast_to(np.array([4096, 32768, 60160]), (HEIGHT, WIDTH, 3)).astype(np.uint16)
    with pytest.raises(MediaError, match='Limited-range RGB input is unsupported'):
        rgb_source(tmp_path, rgb, 'linear', 'tv')
    with pytest.raises(RenderError, match='Limited-range RGB input is unsupported'):
        read_source_color(tmp_path / 'rgb-linear-tv.mkv', 30, RenderError)


def test_input_and_source_metadata_participate_in_cache_keys(tmp_path, monkeypatch):
    rgb = np.full((HEIGHT, WIDTH, 3), 32768, np.uint16)
    media = rgb_source(tmp_path, rgb)
    p = project(media)
    engine = RenderEngine(timeout=30)
    try:
        first = engine._graded_segment(media, p.clips[0], p, None)
        p.clips[0].grade.input_encoding = 'srgb'
        second = engine._graded_segment(media, p.clips[0], p, None)
        assert first != second
        assert abs(float(decoded(first)[24, 32, 0] - decoded(second)[24, 32, 0])) > .04
        source = read_source_color(media.path, 30, RenderError)
        monkeypatch.setattr('lumen.color_media.read_source_color', lambda *args:
                            replace(source, color_primaries='unknown'))
        third = engine._graded_segment(media, p.clips[0], p, None)
        assert second != third
        np.testing.assert_array_equal(decoded(second), decoded(third))
        # A preview-cache hit must still validate source tags and selection.
        cached = engine.preview(p)
        assert cached.exists()
        monkeypatch.setattr('lumen.color_media.read_source_color', lambda *args:
                            replace(source, color_transfer='bt709'))
        with pytest.raises(RenderError, match='Select Rec.709 in Input encoding'):
            engine.preview(p)
    finally:
        engine.close()

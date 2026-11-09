"""Synthetic Apple Log 1 ingress evidence, not camera or codec certification.

FFV1 preserves authored code values. The independent BT.2020 NCL scalar oracle
checks matrix/range decoding before the separately tested owned Log transform.
Subsampled fixtures vary luma with constant chroma: this does not qualify chroma siting.
"""
import json
import subprocess

import numpy as np
import pytest

from lumen import color
from lumen.color_media import read_source_color, source_rgb_filters
from lumen.color_picker_source import capture_picker_snapshot, PickerSourceError
from lumen.frame_grade_preview import capture_grade_preview_snapshot
from lumen.media import MediaError, ffmpeg_available, ffmpeg_binary, ffprobe_binary, probe
from lumen.model import Clip, Grade, Project
from lumen.render import RenderEngine, RenderError

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
WIDTH, HEIGHT = 64, 48
ENCODING = 'apple_log_bt2020'


def _fixture(tmp_path, pixel_format='gbrp16le', range_tag='pc', transfer=None, image=None):
    path = tmp_path / f'log-{pixel_format}-{range_tag}-{transfer}.mkv'
    if pixel_format == 'gbrp16le':
        # Non-8-bit-aligned channel codes expose accidental low-bit loss.
        rgb = np.broadcast_to(np.array([30011, 31007, 32003], '<u2'),
                              (HEIGHT, WIDTH, 3)).copy()
        rgb += np.arange(WIDTH, dtype=np.uint16)[None, :, None] * 13
        rgb += np.arange(HEIGHT, dtype=np.uint16)[:, None, None] * 7
        if image is not None:
            rgb = np.asarray(image, dtype='<u2')
        raw = np.moveaxis(rgb[..., [1, 2, 0]], -1, 0).copy().tobytes()
        expected = rgb.astype(np.float32) / np.float32(65535)
        matrix = 'rgb'
    else:
        shapes = [(HEIGHT, WIDTH)] + ([(HEIGHT, WIDTH)] * 2 if '444' in pixel_format
                 else [(HEIGHT, WIDTH // 2)] * 2 if '422' in pixel_format
                 else [(HEIGHT // 2, WIDTH // 2)] * 2)
        values = (400, 540, 570)
        y_codes = np.full((HEIGHT, WIDTH), values[0], '<u2')
        y_codes += np.arange(WIDTH, dtype=np.uint16)[None, :]
        y_codes += np.arange(HEIGHT, dtype=np.uint16)[:, None]
        raw = y_codes.tobytes() + b''.join(np.full(shape, value, '<u2').tobytes()
                       for shape, value in zip(shapes[1:], values[1:]))
        # BT.2020 NCL Kr/Kb, with digital offsets applied exactly once.
        y = (y_codes.astype(np.float64) - 64) / 876 if range_tag == 'tv' else y_codes / 1023
        cb, cr = ((v - 512) / (896 if range_tag == 'tv' else 1023) for v in values[1:])
        r, b = y + 2 * (1 - .2627) * cr, y + 2 * (1 - .0593) * cb
        g = (y - .2627 * r - .0593 * b) / .678
        expected = np.stack([r, g, b], axis=-1)
        matrix = 'bt2020nc'
    tags = ['-colorspace', matrix, '-color_range', range_tag, '-color_primaries', 'bt2020']
    if transfer:
        tags += ['-color_trc', transfer]
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-f', 'rawvideo', '-pix_fmt', pixel_format,
                    '-s', f'{WIDTH}x{HEIGHT}', '-r', '30', *tags, '-i', 'pipe:0',
                    '-frames:v', '3', '-c:v', 'ffv1', '-level', '3', '-pix_fmt', pixel_format,
                    '-threads', '1', *tags, str(path)],
                   input=raw * 3, capture_output=True, check=True)
    return path, expected


def _decode(path, filters=None):
    command = [ffmpeg_binary(), '-v', 'error', '-i', str(path)]
    if filters:
        command += ['-vf', ','.join(filters)]
    result = subprocess.run([*command, '-frames:v', '1', '-pix_fmt', 'rgb48le',
                             '-f', 'rawvideo', 'pipe:1'], capture_output=True, check=True)
    return np.frombuffer(result.stdout, '<u2').reshape(HEIGHT, WIDTH, 3).astype(np.float32) / np.float32(65535)


def _project(media):
    clip = Clip('log-clip', media.id, 0, 0, 0, 3, grade=Grade(input_encoding=ENCODING))
    return Project(width=WIDTH, height=HEIGHT, media=[media], clips=[clip])


@pytest.mark.parametrize('pixel_format', ['yuv444p10le', 'yuv422p10le', 'yuv420p10le'])
@pytest.mark.parametrize('range_tag', ['pc', 'tv'])
def test_real_yuv10_bt2020_matrix_range_and_normalization(tmp_path, pixel_format, range_tag):
    path, oracle = _fixture(tmp_path, pixel_format, range_tag)
    # Neither codec name nor BT.2020 alone identifies Apple Log.
    with pytest.raises(MediaError):
        probe(path)
    media = probe(path, input_encoding=ENCODING)
    assert (media.color_space, media.color_primaries, media.color_range) == ('bt2020nc', 'bt2020', range_tag)
    info = read_source_color(path, 30, RenderError, input_encoding=ENCODING)
    assert info.pixel_format == pixel_format
    raw = _decode(path, source_rgb_filters(path, 30, RenderError, info))
    np.testing.assert_allclose(raw, oracle, atol=1 / 65535, rtol=0)
    p = _project(media)
    engine = RenderEngine(timeout=30)
    try:
        stage = engine._graded_segment(media, p.clips[0], p, None)
        expected = color.normalize_input(raw, ENCODING)
        np.testing.assert_allclose(_decode(stage), np.clip(expected, 0, 1), atol=2 / 65535, rtol=0)
    finally:
        engine.close()


def test_real_rgb16_shared_picker_bypass_preview_export_and_tags(tmp_path):
    path, raw = _fixture(tmp_path)
    media = probe(path, input_encoding=ENCODING)
    info = read_source_color(path, 30, RenderError, input_encoding=ENCODING)
    np.testing.assert_array_equal(_decode(path, source_rgb_filters(path, 30, RenderError, info)), raw)
    p = _project(media)
    expected = color.normalize_input(raw, ENCODING)
    assert np.max(np.abs(expected - raw)) > .02
    # Sampling stages all see the same normalized source, not original Log.
    for stage in ('hue', 'qualifier', 'white_balance'):
        snapshot = capture_picker_snapshot(p, 'log-clip', 0, stage=stage)
        np.testing.assert_allclose(snapshot.frame, expected, atol=2e-6, rtol=0)
        assert snapshot.valid_mask.all()
    frame_preview = capture_grade_preview_snapshot(p, 'log-clip', 0)
    np.testing.assert_allclose(frame_preview.frame, expected, atol=2e-6, rtol=0)
    engine = RenderEngine(timeout=30)
    try:
        graded = engine._graded_segment(media, p.clips[0], p, None)
        np.testing.assert_allclose(_decode(graded), expected, atol=2 / 65535, rtol=0)
        p.clips[0].grade.enabled = False
        p.clips[0].grade.exposure = 2
        bypassed = engine._graded_segment(media, p.clips[0], p, None)
        np.testing.assert_array_equal(_decode(bypassed), _decode(graded))
        np.testing.assert_allclose(capture_picker_snapshot(p, 'log-clip', 0).frame,
                                   expected, atol=2e-6, rtol=0)
        preview = engine.preview(p)
        exported = engine.render(p, tmp_path / 'normalized.mp4')
        for output in (preview, exported):
            np.testing.assert_allclose(_decode(output)[24, 32], expected[24, 32], atol=.025, rtol=0)
        # Scopes consume the actual post-output frame, never raw Log codes.
        from lumen.color_scopes import analyze_rgb
        expected_codes = np.rint(np.clip(expected, 0, 1) * 255).astype(np.uint8)
        output_codes = np.rint(_decode(preview) * 255).astype(np.uint8)
        expected_scope = analyze_rgb(expected_codes)
        output_scope = analyze_rgb(output_codes)
        bins = np.arange(256)
        mean_expected = (expected_scope.histogram @ bins) / expected_scope.sample_count
        mean_output = (output_scope.histogram @ bins) / output_scope.sample_count
        np.testing.assert_allclose(mean_output, mean_expected, atol=5, rtol=0)
        assert output_scope.waveform.sum() == output_scope.sample_count == WIDTH * HEIGHT
        tags = json.loads(subprocess.check_output([ffprobe_binary(), '-v', 'error',
            '-select_streams', 'v:0', '-show_entries',
            'stream=color_transfer,color_primaries,color_space,color_range', '-of', 'json', str(exported)]))['streams'][0]
        assert tags == dict(color_range='tv', color_space='bt709', color_transfer='bt709', color_primaries='bt709')
        # Reinterpreting retained media cannot evade re-probing on cache hits.
        p.clips[0].grade.input_encoding = 'rec709'
        with pytest.raises(RenderError):
            engine.preview(p)
        with pytest.raises(PickerSourceError):
            capture_picker_snapshot(p, 'log-clip', 0)
    finally:
        engine.close()


@pytest.mark.parametrize('transfer', ['bt709', 'smpte2084', 'arib-std-b67'])
def test_actual_contradictory_transfer_cannot_be_overridden_as_log(tmp_path, transfer):
    path, _ = _fixture(tmp_path, transfer=transfer)
    with pytest.raises(MediaError):
        probe(path, input_encoding=ENCODING)
    with pytest.raises(RenderError):
        read_source_color(path, 30, RenderError, input_encoding=ENCODING)


@pytest.mark.parametrize('pixel_format', ['rgb48le', 'rgb48be', 'gbrp16le', 'gbrp16be'])
def test_all_allowed_rgb16_raw_decoder_formats_preserve_code_values(pixel_format):
    """Raw decoder channel/endian contract; not additional container qualification."""
    from lumen.color_source import source_color_from_stream
    rgb = np.arange(WIDTH * HEIGHT * 3, dtype=np.uint16).reshape(HEIGHT, WIDTH, 3) * 7
    samples = rgb if pixel_format.startswith('rgb') else np.moveaxis(rgb[..., [1, 2, 0]], -1, 0)
    dtype = '>u2' if pixel_format.endswith('be') else '<u2'
    info = source_color_from_stream(dict(pix_fmt=pixel_format, color_space='gbr',
        color_range='pc', color_primaries='bt2020', color_transfer='unknown'), input_encoding=ENCODING)
    filters = source_rgb_filters('unused', 30, RenderError, info)
    result = subprocess.run([ffmpeg_binary(), '-v', 'error', '-f', 'rawvideo',
        '-pix_fmt', pixel_format, '-s', f'{WIDTH}x{HEIGHT}', '-color_range', 'pc',
        '-colorspace', 'rgb', '-color_primaries', 'bt2020', '-i', 'pipe:0',
        '-vf', ','.join(filters), '-frames:v', '1', '-pix_fmt', 'rgb48le',
        '-f', 'rawvideo', 'pipe:1'], input=samples.astype(dtype).tobytes(),
        capture_output=True, check=True)
    actual = np.frombuffer(result.stdout, '<u2').reshape(HEIGHT, WIDTH, 3)
    np.testing.assert_array_equal(actual, rgb)


@pytest.mark.parametrize('transfer', ['bt709', 'smpte2084', 'arib-std-b67'])
def test_warm_caches_reprobe_replaced_source_even_with_stale_stat(tmp_path, monkeypatch, transfer):
    """Actual on-disk replacement; stale stat is explicitly injected, not an OS claim.

    Normal replacement changes ctime/inode and invalidates the fingerprint.
    Freezing only the source Path.stat also proves metadata validation precedes
    possible cache reuse when a filesystem/cache reports the old identity.
    """
    from pathlib import Path
    import os
    from lumen import color_media
    path, _ = _fixture(tmp_path)
    media = probe(path, input_encoding=ENCODING)
    p = _project(media)
    engine = RenderEngine(timeout=30)
    try:
        stage = engine._graded_segment(media, p.clips[0], p, None)
        preview = engine.preview(p)
        assert engine._graded_segment(media, p.clips[0], p, None) == stage
        assert engine.preview(p) == preview
        old_stat = path.stat()
        replacement, _ = _fixture(tmp_path, transfer=transfer)
        os.replace(replacement, path)
        actual_stat = path.stat()
        assert (actual_stat.st_ino, actual_stat.st_ctime_ns) != (old_stat.st_ino, old_stat.st_ctime_ns)
        assert stage.exists() and preview.exists()
        real_stat = Path.stat
        monkeypatch.setattr(Path, 'stat', lambda self, *a, **kw:
                            old_stat if self == path else real_stat(self, *a, **kw))
        reads = []
        real_read = color_media.read_source_color
        def tracked_read(*args, **kwargs):
            reads.append((str(args[0]), kwargs.get('input_encoding')))
            return real_read(*args, **kwargs)
        monkeypatch.setattr(color_media, 'read_source_color', tracked_read)
        for operation in (lambda: engine._graded_segment(media, p.clips[0], p, None),
                          lambda: engine.preview(p)):
            before = len(reads)
            with pytest.raises(RenderError):
                operation()
            assert len(reads) > before
            assert reads[-1] == (str(path), ENCODING)
        # Retained imported tags were intentionally unchanged; actual bytes won.
        assert media.color_transfer in ('unknown', 'unspecified', 'reserved')
    finally:
        engine.close()


def test_apple_log_prores_export_preserves_sub_8bit_input_precision(tmp_path):
    from test_color_highbit_reference import video_metadata, yuv422, rec709_yuv10
    image = np.full((HEIGHT, WIDTH, 3), 32010, dtype=np.uint16)
    image[:, WIDTH // 2:] = 32150
    coarse = (np.rint(image.astype(float) / 257) * 257).astype(np.uint16)
    assert np.unique(coarse).size == 1
    precise_dir, coarse_dir = tmp_path / 'fine', tmp_path / 'coarse'
    precise_dir.mkdir(); coarse_dir.mkdir()
    path, raw = _fixture(precise_dir, image=image)
    coarse_path, _ = _fixture(coarse_dir, image=coarse)
    media = probe(path, input_encoding=ENCODING)
    control_media = probe(coarse_path, input_encoding=ENCODING)
    engine = RenderEngine(timeout=60)
    try:
        # Same capability validation as production; no silent H.264 fallback.
        output = engine.render(_project(media), tmp_path / 'log-hq.mov',
                               export_profile='prores_422_hq_10bit')
        control = engine.render(_project(control_media), tmp_path / 'coarse-hq.mov',
                                export_profile='prores_422_hq_10bit')
        metadata = video_metadata(output)
        assert metadata.pop('color_range', 'tv') == 'tv'
        assert metadata == dict(codec_name='prores', profile='HQ', width=WIDTH, height=HEIGHT,
            pix_fmt='yuv422p10le', color_space='bt709', color_transfer='bt709', color_primaries='bt709')
        frame_tags = json.loads(subprocess.check_output([ffprobe_binary(), '-v', 'error',
            '-select_streams', 'v:0', '-read_intervals', '%+#1', '-show_frames',
            '-show_entries', 'frame=pix_fmt,color_range,color_space,color_transfer,color_primaries',
            '-of', 'json', str(output)]))['frames'][0]
        assert frame_tags['pix_fmt'] == 'yuv422p10le' and frame_tags['color_range'] == 'tv'
        assert all(frame_tags[key] == 'bt709' for key in
                   ('color_space', 'color_transfer', 'color_primaries'))
        y, cb, cr = yuv422(output, WIDTH, HEIGHT)
        coarse_y, _, _ = yuv422(control, WIDTH, HEIGHT)
        normalized = color.normalize_input(raw, ENCODING)
        expected_y = rec709_yuv10(normalized)[..., 0]
        # Flat interiors avoid 4:2:2 filtering and codec edge ringing.
        for sl in (slice(8, 24), slice(40, 56)):
            assert abs(y[0, 8:40, sl].mean() - expected_y[8:40, sl].mean()) < 1.1
        delta = y[0, 8:40, 40:56].mean() - y[0, 8:40, 8:24].mean()
        coarse_delta = coarse_y[0, 8:40, 40:56].mean() - coarse_y[0, 8:40, 8:24].mean()
        assert delta > 1.5, 'Earlier RGB8 quantization erased the Log source distinction'
        assert abs(coarse_delta) <= .5
        np.testing.assert_allclose(cb, 512, atol=1, rtol=0)
        np.testing.assert_allclose(cr, 512, atol=1, rtol=0)
    finally:
        engine.close()

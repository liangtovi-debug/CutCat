"""Decoded-pixel integration checks for the real preview/export float path."""
import subprocess
import threading
import os
from pathlib import Path
import numpy as np
import pytest
from lumen import color
from lumen.media import probe, ffmpeg_available, ffmpeg_binary
from lumen.model import Clip, Grade, Project
from lumen.render import RenderEngine, RenderCancelled, RenderError, _lut_fingerprint

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def source(tmp_path, rgb):
    height, width, _ = rgb.shape
    path = tmp_path / 'rgb16.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb48le',
                    '-s', f'{width}x{height}', '-r', '30', '-i', 'pipe:0', '-frames:v', '3',
                    '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', '-colorspace', 'bt709', path],
                   input=rgb.astype('<u2').tobytes() * 3, check=True, capture_output=True)
    return probe(path)


def decoded(path, width, height, fmt='rgb48le'):
    p = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-frames:v', '1',
                        '-pix_fmt', fmt, '-f', 'rawvideo', 'pipe:1'], capture_output=True, check=True)
    return np.frombuffer(p.stdout, dtype='<u2').reshape(height, width, 3).astype(np.float32) / 65535


def test_actual_16bit_decode_reaches_evaluator(tmp_path, monkeypatch):
    ramp = np.broadcast_to(np.arange(1024, dtype=np.uint16)[None, :, None] * 64,
                           (4, 1024, 3)).copy()
    m = source(tmp_path, ramp)
    p = Project(width=1024, height=4, media=[m], clips=[Clip('c', m.id, 0, 0, 0, 1)])
    seen = []
    original = color.apply_grade
    def inspect(rgb, grade, **kwargs):
        seen.append(rgb.copy())
        return original(rgb, grade, **kwargs)
    monkeypatch.setattr(color, 'apply_grade', inspect)
    e = RenderEngine(timeout=30)
    try:
        e.render(p, tmp_path / 'ramp.mp4')
        assert len(seen) == 1
        assert seen[0].dtype == np.float32
        assert np.unique(seen[0][..., 0]).size > 1000
        np.testing.assert_allclose(seen[0], ramp.astype(np.float32) / 65535, atol=2 / 65535)
    finally:
        e.close()


@pytest.mark.parametrize('grade', [
    Grade(linear_exposure=1), Grade(lift=[.1, 0, -.05]),
    Grade(gamma=[1.2, .8, 1]), Grade(gain=[1.4, .8, 1]),
    Grade(master_curve=[[0, 0], [.5, .7], [1, 1]]),
    Grade(red_curve=[[0, 0], [.5, .7], [1, 1]]),
])
def test_export_and_preview_use_float_math(tmp_path, grade):
    rgb = np.broadcast_to(np.array([.3, .4, .5]) * 65535, (48, 64, 3)).astype(np.uint16)
    m = source(tmp_path, rgb)
    c = Clip('c', m.id, 0, 0, 0, 1, grade=grade)
    p = Project(width=64, height=48, media=[m], clips=[c])
    expected = color.apply_grade(rgb.astype(np.float32) / 65535, grade)[24, 32]
    e = RenderEngine(timeout=30)
    try:
        out = e.render(p, tmp_path / 'graded.mp4')
        preview = e.preview(p)
        for path in (out, preview):
            actual = decoded(path, 64, 48)[24, 32]
            np.testing.assert_allclose(actual, np.clip(expected, 0, 1), atol=.025)
        # Cached lossless stage has much tighter precision than delivery H.264.
        stage = e._graded_segment(m, c, p, None)
        np.testing.assert_allclose(decoded(stage, 64, 48)[24, 32], expected, atol=2/65535)
    finally:
        e.close()


def test_new_controls_bypass_is_pixel_exact(tmp_path):
    rgb = np.broadcast_to(np.array([.3, .4, .5]) * 65535, (48, 64, 3)).astype(np.uint16)
    m = source(tmp_path, rgb)
    c = Clip('c', m.id, 0, 0, 0, 1)
    p = Project(width=64, height=48, media=[m], clips=[c])
    e = RenderEngine(timeout=30)
    try:
        original = decoded(e.render(p, tmp_path / 'identity.mp4'), 64, 48)
        c.grade = Grade(linear_exposure=2, lift=[.2, .1, 0], gamma=[2, 1, 1],
                        gain=[1, 2, 1], red_curve=[[0, .2], [1, .8]], enabled=False)
        bypass = decoded(e.render(p, tmp_path / 'bypass.mp4'), 64, 48)
        np.testing.assert_array_equal(original, bypass)
    finally:
        e.close()


def test_lut_content_edit_invalidates_preview_and_graded_stage(tmp_path, monkeypatch):
    rgb = np.broadcast_to(np.array([.25, .5, .75]) * 65535, (48, 64, 3)).astype(np.uint16)
    m = source(tmp_path, rgb)
    lut = (tmp_path / 'editable.cube').resolve()

    def contents(invert):
        return 'LUT_3D_SIZE 2\n' + '\n'.join(
            f'{1-r if invert else r} {g} {b}' for b in (0, 1) for g in (0, 1)
            for r in (0, 1)) + '\n'

    lut.write_text(contents(False), encoding='utf-8')
    before_stat = lut.stat()
    c = Clip('c', m.id, 0, 0, 0, 1, grade=Grade(lut_path=str(lut)))
    p = Project(width=64, height=48, media=[m], clips=[c])
    e = RenderEngine(timeout=30)
    try:
        before = e.preview(p)
        before_stage = e._graded_segment(m, c, p, None)
        np.testing.assert_allclose(decoded(before_stage, 64, 48), rgb / 65535, atol=2/65535)
        lut.write_text(contents(True), encoding='utf-8')
        os.utime(lut, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
        assert lut.stat().st_size == before_stat.st_size
        # Also model platforms where ctime stays unchanged for an in-place edit.
        original_stat = Path.stat
        monkeypatch.setattr(Path, 'stat', lambda path, *a, **kw:
                            before_stat if path == lut else original_stat(path, *a, **kw))
        after = e.preview(p)
        after_stage = e._graded_segment(m, c, p, None)
        assert after != before and after_stage != before_stage
        expected = rgb.astype(np.float32) / 65535
        expected[..., 0] = 1 - expected[..., 0]
        np.testing.assert_allclose(decoded(after_stage, 64, 48), expected, atol=2/65535)
        for path in (after, e.render(p, tmp_path / 'edited-lut.mp4')):
            np.testing.assert_allclose(decoded(path, 64, 48), expected, atol=.025)
    finally:
        e.close()


@pytest.mark.parametrize('grows_after_stat', [False, True])
def test_lut_fingerprint_read_is_bounded(tmp_path, monkeypatch, grows_after_stat):
    from types import SimpleNamespace
    lut = (tmp_path / 'too-large.cube').resolve()
    with lut.open('wb') as stream:
        stream.truncate(64 * 1024 * 1024 + 1)
    if grows_after_stat:
        stat = lut.stat()
        stale = SimpleNamespace(st_size=0, st_mtime_ns=stat.st_mtime_ns,
                                st_ctime_ns=stat.st_ctime_ns)
        original_stat = Path.stat
        monkeypatch.setattr(Path, 'stat', lambda path, *a, **kw:
                            stale if path == lut else original_stat(path, *a, **kw))
    with pytest.raises(RenderError, match='LUT exceeds 64 MiB'):
        _lut_fingerprint(lut)


def test_cancel_during_float_evaluator_cleans_stage(tmp_path, monkeypatch):
    rgb = np.full((48, 64, 3), 20000, dtype=np.uint16)
    m = source(tmp_path, rgb)
    p = Project(width=64, height=48, media=[m], clips=[Clip('c', m.id, 0, 0, 0, 1)])
    cancel = threading.Event()
    original = color.apply_grade
    def interrupt(rgb, grade):
        cancel.set()
        return original(rgb, grade)
    monkeypatch.setattr(color, 'apply_grade', interrupt)
    e = RenderEngine(timeout=30)
    try:
        with pytest.raises(RenderCancelled):
            e.render(p, tmp_path / 'cancel.mp4', cancel=cancel)
        assert not (tmp_path / 'cancel.mp4').exists()
        assert not list(__import__('pathlib').Path(e._cache_dir.name).glob('*.partial.mkv'))
    finally:
        e.close()


@pytest.mark.parametrize('rotation,sar', [(90, '1/1'), (0, '2/1')])
def test_rotation_and_sample_aspect_geometry(tmp_path, rotation, sar):
    base = tmp_path / 'base.mp4'
    # Distinct left/right halves let rotation expose a raw-frame reshape bug.
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'color=red:s=80x40:r=30:d=0.2', '-vf',
                    f'drawbox=x=40:y=0:w=40:h=40:c=blue:t=fill,setsar={sar}',
                    '-c:v', 'libx264', str(base)], check=True, capture_output=True)
    src = tmp_path / 'oriented.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-display_rotation', str(rotation), '-i', str(base), '-c', 'copy',
                    str(src)], check=True, capture_output=True)
    m = probe(src)
    p = Project(width=80, height=80, media=[m], clips=[Clip('c', m.id, 0, 0, 0, 3)])
    e = RenderEngine(timeout=30)
    try:
        out = decoded(e.render(p, tmp_path / 'out.mp4'), 80, 80)
        if rotation:
            # Rotated 40x80 image is centered horizontally; halves now vertical.
            assert out[40, 5].max() < .04
            assert out[10, 40, 2] > .8
            assert out[70, 40, 0] > .8
        else:
            # 2:1 SAR turns 80x40 into 4:1 display image, 80x20 in project.
            assert out[25, 20].max() < .04
            assert out[40, 20, 0] > .8
            assert out[40, 60, 2] > .8
    finally:
        e.close()


def test_full_range_yuv10_endpoints(tmp_path, monkeypatch):
    width, height = 1024, 4
    y = np.broadcast_to(np.arange(width, dtype='<u2'), (height, width)).copy()
    uv = np.full((height, width), 512, dtype='<u2')
    frame = y.tobytes() + uv.tobytes() * 2
    src = tmp_path / 'full-yuv10.mkv'
    # Tag the raw input as well as the output: FFmpeg 9 converts an unspecified
    # input matrix to requested BT.709, changing even neutral YUV code values.
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'yuv444p10le',
                    '-s', f'{width}x{height}', '-r', '30', '-color_range', 'pc',
                    '-colorspace', 'bt709', '-i', 'pipe:0', '-frames:v', '3',
                    '-c:v', 'ffv1', '-pix_fmt', 'yuv444p10le', '-colorspace', 'bt709',
                    '-color_range', 'pc', str(src)],
                   input=frame * 3, check=True, capture_output=True)
    fixture = subprocess.run([ffmpeg_binary(), '-v', 'error', '-i', str(src),
                              '-frames:v', '1', '-pix_fmt', 'yuv444p10le',
                              '-f', 'rawvideo', 'pipe:1'], check=True, capture_output=True)
    assert fixture.stdout == frame, 'The precision fixture must preserve every input Y/U/V code'
    m = probe(src)
    p = Project(width=width, height=height, media=[m], clips=[Clip('c', m.id, 0, 0, 0, 1)])
    seen = []
    original = color.apply_grade
    def capture(rgb, grade):
        seen.append(rgb.copy())
        return original(rgb, grade)
    monkeypatch.setattr(color, 'apply_grade', capture)
    e = RenderEngine(timeout=30)
    try:
        e.render(p, tmp_path / 'full.mp4')
        rgb = seen[0]
        assert np.unique(rgb[0, :, 0]).size == 1024
        np.testing.assert_allclose(rgb[:, 0], 0, atol=2/65535)
        np.testing.assert_allclose(rgb[:, -1], 1, atol=2/65535)
        np.testing.assert_allclose(rgb[0, 256], 256/1023, atol=2/65535)
        # All three channels, including every highlight and shadow code, must
        # reach the evaluator without an implicit matrix/range conversion.
        expected = np.broadcast_to(y[..., None].astype(np.float32) / 1023, rgb.shape)
        np.testing.assert_allclose(rgb, expected, atol=2/65535, rtol=0)
    finally:
        e.close()


def test_local_playlist_cannot_fetch_network(tmp_path):
    from lumen.media import MediaError
    playlist = tmp_path / 'remote.m3u8'
    playlist.write_text('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n'
                        '#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:1,\n'
                        'http://127.0.0.1:9/f.ts\n#EXT-X-ENDLIST\n')
    with pytest.raises(MediaError, match='(?i)not on whitelist'):
        probe(playlist)


def test_all_media_inputs_enforce_local_protocols(tmp_path, monkeypatch):
    rgb = np.full((48, 64, 3), 20000, dtype=np.uint16)
    m = source(tmp_path, rgb)
    p = Project(width=64, height=48, media=[m], clips=[Clip('c', m.id, 0, 0, 0, 1)])
    original = subprocess.Popen
    inspected = []
    def inspect(command, *args, **kwargs):
        command = list(map(str, command))
        if '-i' in command:
            for index, arg in enumerate(command):
                if arg == '-i':
                    assert command[index - 2:index] == ['-protocol_whitelist', 'file,pipe']
                    inspected.append(command[index + 1])
        elif '-show_entries' in command or '-show_streams' in command:
            assert '-protocol_whitelist' in command
            assert command[command.index('-protocol_whitelist') + 1] == 'file,pipe'
            inspected.append('probe')
        return original(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', inspect)
    e = RenderEngine(timeout=30)
    try:
        e.render(p, tmp_path / 'local.mp4')
        e.frame(p, 0, width=64)
        assert 'pipe:0' in inspected
        assert 'probe' in inspected
        assert sum(item.endswith('.mkv') for item in inspected) >= 3
        assert any(item.endswith('.mp4') for item in inspected)
    finally:
        e.close()

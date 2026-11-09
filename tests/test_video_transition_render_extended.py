"""Independent side-before-mix evidence and real delivery/cache failure tests."""
from dataclasses import replace
from fractions import Fraction
import json
import os
from pathlib import Path
import subprocess

import numpy as np
import pytest

from test_video_transition_render import fixture, command, decode_rgb
from lumen.clip_transform import ClipTransform
from lumen.media import ffprobe_binary, probe
from lumen.model import Grade, ClipEffect
from lumen.render import RenderEngine, RenderError
from lumen.restoration_settings import RestorationSettings
from lumen.video_transition import VideoTransition
from lumen.video_transition_render import prepare_video_transitions, verify_transition_support


def lut(path, swap=True):
    path.write_text('LUT_3D_SIZE 2\n' + ''.join(
        f'{b if swap else r} {g} {r if swap else b}\n'
        for b in (0, 1) for g in (0, 1) for r in (0, 1)))


def with_transition(tmp_path):
    project, frames = fixture(tmp_path)
    project.video_transitions = [VideoTransition('t', 'left', 'right', 5)]
    return project, frames


@pytest.mark.parametrize('high', [False, True])
@pytest.mark.parametrize('feature', ['grade', 'lut', 'restoration', 'transform', 'combined'])
def test_each_source_processed_before_analytic_mix(tmp_path, high, feature):
    project, _ = with_transition(tmp_path)
    cube = tmp_path / 'swap.cube'; lut(cube)
    for side, clip in enumerate(project.clips):
        if feature in ('grade', 'combined'):
            clip.grade = Grade(exposure=.17 if side == 0 else -.23, saturation=.72 + side * .15)
        if feature in ('lut', 'combined'):
            clip.grade.lut_path = str(cube)
            clip.grade.lut_strength = .35 + side * .5
        if feature in ('restoration', 'combined'):
            clip.restoration = RestorationSettings(enabled=True, denoise=.2 + side * .3, sharpen=.25)
        if feature in ('transform', 'combined'):
            clip.transform = ClipTransform(mirror_x=side == 0, mirror_y=side == 1)
    engine = RenderEngine()
    try:
        # Independently grade full sources, not the transition's trim windows;
        # independently spell out effects and mirror rather than its helper.
        sides = []
        for side, clip in enumerate(project.clips):
            full = replace(clip, source_in=0, duration=24, start=0, time_map=None)
            graded = engine._graded_segment(project.media[side], full, project, None, exact_video_frames=True)
            filters = ['format=gbrp16le']
            if feature in ('transform', 'combined'):
                filters += ['hflip' if side == 0 else 'vflip']
            raw = command(['-cpuflags', '0', '-filter_threads', '1', '-i', str(graded),
                           '-vf', ','.join(filters), '-pix_fmt', 'rgb48le', '-fps_mode', 'passthrough',
                           '-f', 'rawvideo', 'pipe:1'])
            sides.append(np.frombuffer(raw, dtype='<u2').reshape(24, 32, 64, 3))
        prepared, _ = prepare_video_transitions(engine, project, high)
        got = decode_rgb(prepared[0].path, 64, 32)
        weights = np.arange(5)[:, None, None, None] / 4
        expected = sides[0][10:15] * (1 - weights) + sides[1][6:11] * weights
        np.testing.assert_allclose(got, expected, atol=1.01, rtol=0)
        np.testing.assert_array_equal(got[0], sides[0][10])
        np.testing.assert_array_equal(got[-1], sides[1][10])
    finally:
        engine.close()


def test_genuine_1024_code_rgb16_transition_preserves_low_bits(tmp_path):
    project, _ = fixture(tmp_path, width=1024, height=2)
    ramp = np.arange(1024, dtype='<u2') * 64
    for side, media in enumerate(project.media):
        frames = np.broadcast_to((ramp + side)[None, None, :, None], (24, 2, 1024, 3)).copy()
        command(['-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-s', '1024x2', '-r', '30', '-i', 'pipe:0',
                 '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', '-threads', '1', '-color_range', 'pc',
                 '-colorspace', 'rgb', '-color_primaries', 'bt709', '-color_trc', 'bt709', media.path], frames.tobytes())
    project.video_transitions = [VideoTransition('t', 'left', 'right', 5)]
    engine = RenderEngine()
    try:
        prepared, _ = prepare_video_transitions(engine, project, True)
        got = decode_rgb(prepared[0].path, 1024, 2)
        assert len(np.unique(got[0, 0, :, 0])) == 1024
        np.testing.assert_array_equal(got[0, 0, :, 0], ramp)
        np.testing.assert_array_equal(got[-1, 0, :, 0], ramp + 1)
        expected = ramp[None, :] + np.arange(5)[:, None] / 4
        np.testing.assert_allclose(got[:, 0, :, 0], expected, atol=1, rtol=0)
        inputs, graph, _ = engine._compile(project, export_profile='prores_422_hq_10bit')
        raw = command(['-filter_complex_threads', '1', *inputs, '-filter_complex', graph,
                       '-map', '[vout]', '-map', '[aout]', '-c:v', 'rawvideo', '-c:a', 'pcm_s16le',
                       '-f', 'nut', 'pipe:1'])
        container = tmp_path / 'ramp.nut'; container.write_bytes(raw)
        yuv = command(['-i', str(container), '-an', '-pix_fmt', 'yuv422p10le',
                       '-fps_mode', 'passthrough', '-f', 'rawvideo', 'pipe:1'])
        planes = np.frombuffer(yuv, dtype='<u2').reshape(20, 2 * 2 * 1024)
        luma = planes[:, :2 * 1024].reshape(20, 2, 1024)
        assert len(np.unique(luma[0])) > 800  # Not an 8-bit ramp in a 10-bit file.
        assert 63 <= luma.min() <= 65 and 938 <= luma.max() <= 941
    finally:
        engine.close()


@pytest.mark.parametrize('profile,extension,pixel', [('h264_8bit', '.mp4', 'yuv420p'),
                                                    ('prores_422_hq_10bit', '.mov', 'yuv422p10le')])
def test_real_export_frame_count_and_tags(tmp_path, profile, extension, pixel):
    project, _ = with_transition(tmp_path)
    engine = RenderEngine()
    output = tmp_path / ('delivered' + extension)
    try:
        engine.render(project, output, export_profile=profile)
        assert len(decode_rgb(output, 64, 32)) == 20
        result = subprocess.run([ffprobe_binary(), '-v', 'error', '-select_streams', 'V:0',
                                 '-show_streams', '-of', 'json', str(output)], capture_output=True, check=True)
        stream = json.loads(result.stdout)['streams'][0]
        assert stream['pix_fmt'] == pixel
        assert stream['color_primaries'] == stream['color_transfer'] == stream['color_space'] == 'bt709'
        if profile == 'h264_8bit':
            assert stream['color_range'] == 'tv'
        else:
            decoded = subprocess.run([ffprobe_binary(), '-v', 'error', '-select_streams', 'V:0',
                                      '-read_intervals', '%+#1', '-show_frames', '-of', 'json', str(output)],
                                     capture_output=True, check=True)
            frame = json.loads(decoded.stdout)['frames'][0]
            assert frame['color_range'] == 'tv' and frame['pix_fmt'] == 'yuv422p10le'
    finally:
        engine.close()


@pytest.mark.parametrize('change', ['grade', 'transform', 'lut', 'source'])
def test_transition_cache_invalidation(tmp_path, change):
    project, _ = with_transition(tmp_path)
    cube = tmp_path / 'lut.cube'; lut(cube)
    if change == 'lut':
        project.clips[0].grade.lut_path = str(cube)
    engine = RenderEngine()
    try:
        first = prepare_video_transitions(engine, project)[0][0]
        before = decode_rgb(first.path, 64, 32).copy()
        if change == 'grade':
            project.clips[0].grade.exposure = .5
        elif change == 'transform':
            project.clips[0].transform = ClipTransform(mirror_x=True)
        elif change == 'lut':
            stat = cube.stat(); lut(cube, False); os.utime(cube, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        else:
            Path(project.media[0].path).write_bytes(Path(project.media[1].path).read_bytes())
        second = prepare_video_transitions(engine, project)[0][0]
        assert first.fingerprint != second.fingerprint and first.path != second.path
        assert not np.array_equal(before, decode_rgb(second.path, 64, 32))
    finally:
        engine.close()


@pytest.mark.parametrize('change', ['missing', 'corrupt', 'relink'])
def test_preview_cache_hit_revalidates_sources(tmp_path, change):
    project, _ = with_transition(tmp_path)
    engine = RenderEngine()
    try:
        cached = engine.preview(project)
        assert cached.exists()
        if change == 'missing':
            Path(project.media[0].path).unlink()
        elif change == 'corrupt':
            Path(project.media[0].path).write_bytes(b'invalid')
        else:
            project.media[0].path = str(tmp_path / 'unlinked.mkv')
        with pytest.raises(RenderError):
            engine.preview(project)
    finally:
        engine.close()


@pytest.mark.parametrize('failure', ['missing_filter', 'wrong_arithmetic'])
def test_runtime_blend_capability_failure_is_not_cached(tmp_path, monkeypatch, failure):
    import lumen.video_transition_render as vr
    engine = RenderEngine()
    def broken(command, timeout, cancel):
        if failure == 'missing_filter':
            raise RenderError('No such filter: blend')
        Path(command[-1]).write_bytes(bytes(3 * 3 * 2 * 1024 * 2))
    monkeypatch.setattr(vr, '_run', broken)
    try:
        with pytest.raises(RenderError):
            verify_transition_support(engine, ('fake', 'digest'))
        assert not getattr(engine, '_transition_support', set())
        assert not list(Path(engine._cache_dir.name).glob('cutcat-blend-probe-*'))
    finally:
        engine.close()


@pytest.mark.parametrize('high', [False, True])
def test_sticker_and_caption_stay_above_transition(tmp_path, high):
    from test_overlay_render import sticker
    from test_video_transition_render import compile_raw
    from lumen.model import Caption, CaptionStyle
    from lumen.caption_render import font_identity, CaptionRenderError
    project, _ = with_transition(tmp_path)
    overlay, _ = sticker(tmp_path, color=(255, 0, 0, 255), start=8, duration=5)
    project.overlays = [overlay]
    # Text lives in a larger canvas to fit the established layout constraints.
    project.width, project.height = 320, 180
    selected = None
    for family in ('DejaVu Sans', 'Arial', 'Helvetica'):
        try:
            style = CaptionStyle(font_family=family, font_size=24, margin_bottom=12)
            font_identity(style)
            selected = style
            break
        except CaptionRenderError:
            continue
    if selected is None:
        pytest.skip('No local Latin font available')
    project.caption_style = selected
    project.captions = [Caption('text', 8, 5, 'TOP')]
    engine = RenderEngine()
    try:
        raw, graph = compile_raw(engine, project, high)
        path = tmp_path / 'layers.nut'; path.write_bytes(raw)
        frames = decode_rgb(path, 320, 180)
        assert len(frames) == 20
        # Actual overlay pixels are red in the source-independent top-left.
        assert frames[10, 4, 4, 0] > 58000
        assert frames[10, 4, 4, 1:].max() < 5000
        assert graph.index('[transition0]') < graph.index('[stickers]')
        assert graph.index('[transition0]') < graph.index('[caption')
        project.video_transitions = []
        reference, _ = compile_raw(engine, project, high)
        plain = tmp_path / 'layers_without_transition.nut'; plain.write_bytes(reference)
        reference_frames = decode_rgb(plain, 320, 180)
        np.testing.assert_array_equal(frames[10, :12, :12], reference_frames[10, :12, :12])
        # The caption creates near-white pixels over the non-white source.
        assert np.count_nonzero((frames[10, 130:] > 60000).all(axis=-1)) > 30
    finally:
        engine.close()


@pytest.mark.parametrize('high', [False, True])
@pytest.mark.parametrize('effect', ['blur', 'vignette'])
def test_active_endpoint_effect_is_explicitly_rejected_without_mutation(tmp_path, high, effect):
    from dataclasses import asdict
    project, _ = with_transition(tmp_path)
    project.clips[0].effects = [ClipEffect('unsupported', effect, .4)]
    before = asdict(project)
    engine = RenderEngine()
    try:
        with pytest.raises((ValueError, RenderError), match='effect'):
            prepare_video_transitions(engine, project, high)
        assert asdict(project) == before
        assert not list(Path(engine._cache_dir.name).rglob('*.mkv'))
    finally:
        engine.close()


@pytest.mark.parametrize('high', [False, True])
@pytest.mark.parametrize('feature', ['grade', 'lut', 'restoration', 'transform', 'affine', 'combined'])
def test_transition_endpoints_equal_untransitioned_timeline(tmp_path, high, feature):
    from test_video_transition_render import compile_raw
    project, _ = with_transition(tmp_path)
    cube = tmp_path / 'endpoint.cube'; lut(cube)
    for side, clip in enumerate(project.clips):
        if feature in ('grade', 'combined'):
            clip.grade = Grade(exposure=.1 if side == 0 else -.1, saturation=.8)
        if feature in ('lut', 'combined'):
            clip.grade.lut_path = str(cube); clip.grade.lut_strength = .6
        if feature in ('restoration', 'combined'):
            clip.restoration = RestorationSettings(enabled=True, denoise=.2, sharpen=.3)
        if feature in ('transform', 'combined'):
            clip.transform = ClipTransform(mirror_x=side == 0, mirror_y=side == 1)
        if feature == 'affine':
            clip.transform = ClipTransform(x=3 - side * 5, y=-2 + side * 3, scale=.8 + side * .3, rotation=15 - side * 25)
    engine = RenderEngine()
    try:
        with_data, _ = compile_raw(engine, project, high)
        project.video_transitions = []
        without_data, _ = compile_raw(engine, project, high)
        a = tmp_path / 'with.nut'; a.write_bytes(with_data)
        b = tmp_path / 'without.nut'; b.write_bytes(without_data)
        got = decode_rgb(a, 64, 32); expected = decode_rgb(b, 64, 32)
        np.testing.assert_array_equal(got[8], expected[8])
        np.testing.assert_array_equal(got[12], expected[12])
        np.testing.assert_array_equal(got[:8], expected[:8])
        np.testing.assert_array_equal(got[13:], expected[13:])
    finally:
        engine.close()


@pytest.mark.parametrize('high', [False, True])
def test_multiple_nonoverlapping_transitions_preserve_duration_and_unaffected_frames(tmp_path, high):
    from test_video_transition_render import compile_raw
    from lumen.model import Clip
    project, _ = with_transition(tmp_path)
    project.clips.append(Clip('third', project.media[0].id, 0, 20, 8, 10))
    project.video_transitions.append(VideoTransition('second', 'right', 'third', 4))
    engine = RenderEngine()
    try:
        bridges, _ = prepare_video_transitions(engine, project, high)
        assert [len(decode_rgb(b.path, 64, 32)) for b in bridges] == [5, 4]
        with_data, graph = compile_raw(engine, project, high)
        project.video_transitions = []
        without_data, _ = compile_raw(engine, project, high)
        a = tmp_path / 'two.nut'; a.write_bytes(with_data)
        b = tmp_path / 'none.nut'; b.write_bytes(without_data)
        got = decode_rgb(a, 64, 32); expected = decode_rgb(b, 64, 32)
        assert len(got) == len(expected) == 30
        for first, last in ((0, 9), (12, 19), (21, 30)):
            np.testing.assert_array_equal(got[first:last], expected[first:last])
        assert not np.array_equal(got[9:12], expected[9:12])
        assert not np.array_equal(got[19:21], expected[19:21])
        assert '[transition0]' in graph and '[transition1]' in graph
    finally:
        engine.close()

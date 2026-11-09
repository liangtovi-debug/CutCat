"""Independent decoded acceptance for clip-relative qualifier-window animation.

The oracle below evaluates plain numeric key tuples and analytic pixel-center
geometry. It never calls the production animation, spatial-mask, color-grade,
or time-map sampling evaluators. The lossless fixtures keep identical colors
on both sides of each image; an isolated source-index stripe exposes temporal
phase errors independently of the moving window. No user media is involved.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

import numpy as np
import pytest

from lumen.color_picker_source import capture_picker_snapshot
from lumen.frame_grade_preview import capture_grade_preview_snapshot
from lumen.media import ffmpeg_available, probe
from lumen.model import Clip, Editor, Grade, Project
from lumen.qualifier import QualifierSettings
from lumen.render import RenderEngine
from lumen.timing import ClipTimeMap
from lumen.window_animation import WindowKeyframe
from test_color_highbit_reference import video_metadata
from test_color_picker_restoration_render import decode_yuv, subsample_reference, yuv_reference
from test_qualifier_render import ff

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
WIDTH, HEIGHT = 96, 64
# frame, center_x, center_y, width, height, outgoing interpolation
MOVING_KEYS = ((-2, .18, .38, .24, .40, 'linear'),
               (8, .80, .48, .40, .62, 'linear'))
HELD_KEYS = ((1, .25, .42, .30, .55, 'hold'),
             (3, .72, .42, .30, .55, 'linear'),
             (6, .40, .52, .42, .45, 'hold'))


def animated_settings(keys=MOVING_KEYS, **changes):
    args = dict(enabled=True, hue_width=.5, saturation_low=0.,
                saturation_high=1., luma_low=0., luma_high=1.,
                exposure=.6, saturation=1., hue_shift=0.,
                window_shape='ellipse', window_center_x=.87,
                window_center_y=.80, window_width=.20, window_height=.25,
                window_feather=.4,
                window_keyframes=[WindowKeyframe(*key) for key in keys])
    args.update(changes)
    return QualifierSettings(**args)


def geometry_reference(keys, local, settings):
    """Independent outgoing hold/linear interpolation; outside endpoints hold."""
    if not keys:
        return (settings.window_center_x, settings.window_center_y,
                settings.window_width, settings.window_height)
    if local <= keys[0][0]:
        return keys[0][1:5]
    for first, second in zip(keys, keys[1:]):
        if local < second[0]:
            if first[5] == 'hold':
                return first[1:5]
            amount = Fraction(local - first[0], second[0] - first[0])
            return tuple(float((1 - amount) * a + amount * b)
                         for a, b in zip(first[1:5], second[1:5]))
    return keys[-1][1:5]


def grade_reference(rgb16, local, keys, grade):
    """All fixture hues/saturations/lumas pass; only analytic spatial gating."""
    rgb = np.asarray(rgb16, dtype=float) / 65535
    settings = grade.qualifier
    if not grade.enabled or not settings.enabled:
        return rgb
    height, width, _ = rgb.shape
    center_x, center_y, window_width, window_height = geometry_reference(keys, local, settings)
    yy, xx = np.mgrid[:height, :width]
    dx = abs(((xx + .5) / width - center_x) * 2 / window_width)
    dy = abs(((yy + .5) / height - center_y) * 2 / window_height)
    if settings.window_shape == 'off':
        matte = np.ones((height, width))
    else:
        radius = np.hypot(dx, dy) if settings.window_shape == 'ellipse' else np.maximum(dx, dy)
        matte = (np.clip(1 - np.maximum(radius - 1, 0) / settings.window_feather, 0, 1)
                 if settings.window_feather else (radius <= 1).astype(float))
        if settings.window_invert:
            matte = 1 - matte
    return rgb * (1 + matte[..., None] * (2 ** settings.exposure - 1))


def make_source(tmp_path, settings, *, fps=Fraction(30), speed=Fraction(1),
                origin=Fraction(0), duration=8, start=0, source_stripe=True):
    """Exact RGB16 source at the same rational rate as the project grid."""
    count = 16
    frames = np.empty((count, HEIGHT, WIDTH, 3), dtype='<u2')
    frames[:] = [21007, 15011, 10009]
    if source_stripe:
        # Lower stripe has one known value per source frame, outside the usual
        # window core. The rest is identical across both space AND source time.
        for index in range(count):
            frames[index, -6:] = np.array([11003, 17011, 23003]) + index * 307
    source = tmp_path / 'window-animation-source.mkv'
    planar = np.moveaxis(frames[..., [1, 2, 0]], -1, 1).astype('<u2')
    tags = ['-colorspace', 'rgb', '-color_range', 'pc', '-color_trc', 'bt709',
            '-color_primaries', 'bt709']
    ff('-f', 'rawvideo', '-pix_fmt', 'gbrp16le', '-s', f'{WIDTH}x{HEIGHT}',
       '-r', f'{fps.numerator}/{fps.denominator}', *tags, '-i', 'pipe:0',
       '-frames:v', count, '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'gbrp16le',
       '-threads', '1', *tags, source, data=planar.tobytes())
    assert ff('-i', source, '-pix_fmt', 'gbrp16le', '-f', 'rawvideo', 'pipe:1') == planar.tobytes()
    media = probe(source)
    mapping = ClipTimeMap(origin, origin + duration * speed, speed)
    clip = Clip('moving-window', media.id, 0, start, origin, duration,
                grade=Grade(qualifier=settings), time_map=mapping)
    project = Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
                      fps_den=fps.denominator, media=[media], clips=[clip])
    project.validate()
    return project, frames


def decode_all_rgb16(path):
    raw = ff('-i', path, '-map', '0:V:0', '-an', '-pix_fmt', 'rgb48le', '-f', 'rawvideo', 'pipe:1')
    return np.frombuffer(raw, '<u2').reshape(-1, HEIGHT, WIDTH, 3)


def expected_frames(project, source, keys, indices):
    grade = project.clips[0].grade
    return np.asarray([grade_reference(source[index], local, keys, grade)
                       for local, index in enumerate(indices)])


@pytest.fixture
def renderer():
    engine = RenderEngine(timeout=90)
    try:
        yield engine
    finally:
        engine.close()


@pytest.mark.parametrize('fps', [Fraction(30), Fraction(30000, 1001)], ids=['30fps', 'ntsc'])
@pytest.mark.parametrize('speed,indices', [
    (Fraction(1, 2), (1, 2, 2, 3, 3, 4)),
    (Fraction(3, 2), (1, 3, 4, 6, 7, 9)),
], ids=['repeated-source', 'skipped-source'])
def test_output_frame_window_phase_matches_rgb16_preview_and_picker(
        tmp_path, renderer, fps, speed, indices):
    settings = animated_settings()
    project, source = make_source(tmp_path, settings, fps=fps, speed=speed,
                                  origin=Fraction(3, 2), duration=6, start=17)
    clip = project.clips[0]
    frozen = deepcopy(project)
    expected = expected_frames(project, source, MOVING_KEYS, indices)
    previews, pickers = [], []
    for local, index in enumerate(indices):
        timeline = clip.start + local
        picker = capture_picker_snapshot(project, clip.id, timeline, stage='qualifier')
        preview = capture_grade_preview_snapshot(project, clip.id, timeline)
        assert picker.source_frame == preview.source_frame == index
        assert picker.source_time == preview.source_time == Fraction(index) / fps
        assert picker.identity.timeline_frame == preview.identity.timeline_frame == timeline
        assert picker.is_current(project, clip.id, timeline)
        assert preview.is_current(project, clip.id, timeline)
        # Picking remains at the pre-qualifier stage, even for animated windows.
        np.testing.assert_allclose(picker.frame, source[index] / 65535, rtol=0, atol=4e-8)
        np.testing.assert_allclose(preview.frame, expected[local], rtol=0, atol=3e-7)
        assert not picker.frame.flags.writeable and not preview.frame.flags.writeable
        pickers.append(picker.frame)
        previews.append(preview.frame)
    stage = renderer._graded_segment(project.media[0], clip, project, None)
    decoded = decode_all_rgb16(stage)
    assert decoded.shape[0] == 6
    np.testing.assert_allclose(decoded, np.rint(expected * 65535), rtol=0, atol=2)
    if speed < 1:
        # These are literally the same decoded source, yet different output
        # frames. Grading once and repeating its pixels cannot satisfy this.
        np.testing.assert_array_equal(pickers[1], pickers[2])
        assert np.max(abs(previews[1] - previews[2])) > .04
        assert np.max(abs(decoded[1].astype(int) - decoded[2].astype(int))) > 2500
    # Same RGB at separated image positions must diverge only as the mask moves.
    np.testing.assert_array_equal(source[0, 26, 22], source[0, 26, 69])
    assert expected[0, 26, 22, 0] > expected[0, 26, 69, 0] + .10
    assert expected[-1, 26, 69, 0] > expected[-1, 26, 22, 0] + .10
    assert project == frozen


@pytest.mark.parametrize('shape,invert', [('ellipse', False), ('rectangle', True)])
def test_first_last_holds_and_outgoing_interpolation_match_real_delivery(
        tmp_path, renderer, shape, invert):
    settings = animated_settings(HELD_KEYS, window_shape=shape, window_invert=invert)
    project, source = make_source(tmp_path, settings, fps=Fraction(30000, 1001),
                                  speed=Fraction(1, 2), origin=Fraction(1, 2),
                                  source_stripe=False)
    expected = expected_frames(project, source, HELD_KEYS, (0, 1, 1, 2, 2, 3, 3, 4))
    np.testing.assert_array_equal(expected[0], expected[1])  # Before first key.
    np.testing.assert_array_equal(expected[1], expected[2])  # Outgoing hold.
    np.testing.assert_array_equal(expected[6], expected[7])  # After last key.
    assert np.max(abs(expected[2] - expected[3])) > .15  # Hold changes exactly at key.
    assert np.max(abs(expected[3] - expected[4])) > .04  # Following edge is linear.
    stage = renderer._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_allclose(decode_all_rgb16(stage), np.rint(expected * 65535), atol=2, rtol=0)
    for profile in ('preview', 'prores_422_hq_10bit'):
        ten = profile.startswith('prores')
        output = (renderer.render(project, tmp_path / f'{shape}.mov', export_profile=profile)
                  if ten else renderer.preview(project))
        metadata = video_metadata(output)
        assert metadata['codec_name'] == ('prores' if ten else 'h264')
        assert metadata['pix_fmt'] == ('yuv422p10le' if ten else 'yuv420p')
        reference = yuv_reference(expected, ten)
        for channel, plane in enumerate(decode_yuv(output, WIDTH, HEIGHT, ten)):
            assert plane.shape[0] == 8
            # The actual delivery converter uses left-sited 4:2:2 chroma.
            target = (reference[..., channel][..., ::2] if ten and channel
                      else subsample_reference(reference, channel, ten))
            error = abs(plane[:, 4:-4, 4:-4] - target[:, 4:-4, 4:-4])
            assert error.mean() < 1.3, (profile, channel, error.mean())
            assert np.quantile(error, .99) < (3.5 if ten else 4.5), (profile, channel, error.max())


@pytest.mark.parametrize('speed,indices', [
    (Fraction(1, 2), (1, 2, 2, 3, 3, 4, 4, 5)),
    (Fraction(3, 2), (1, 3, 4, 6, 7, 9, 10, 12)),
], ids=['slow-split', 'fast-split'])
def test_split_keeps_source_phase_and_animated_window_without_boundary_jump(
        tmp_path, renderer, speed, indices):
    project, source = make_source(tmp_path, animated_settings(),
                                  fps=Fraction(30000, 1001), speed=speed,
                                  origin=Fraction(3, 2), start=11)
    original = deepcopy(project)
    expected = expected_frames(original, source, MOVING_KEYS, indices)
    whole = decode_all_rgb16(renderer._graded_segment(
        project.media[0], project.clips[0], project, None))
    editor = Editor(project)
    left, right = editor.split_clip('moving-window', 13)  # Between, not on, keys.
    assert left.duration == 2 and right.duration == 6
    # Keys outside both new clip intervals remain present; dropping them would
    # alter the current interpolation slope even when no visible key is lost.
    assert [key.frame for key in left.grade.qualifier.window_keyframes] == [-2, 8]
    assert [key.frame for key in right.grade.qualifier.window_keyframes] == [-4, 6]
    parts = [decode_all_rgb16(renderer._graded_segment(project.media[0], clip, project, None))
             for clip in (left, right)]
    joined = np.concatenate(parts)
    np.testing.assert_array_equal(joined, whole)
    np.testing.assert_allclose(joined, np.rint(expected * 65535), atol=2, rtol=0)
    for clip, local, original_local in ((left, 1, 1), (right, 0, 2), (right, 1, 3)):
        preview = capture_grade_preview_snapshot(project, clip.id, clip.start + local)
        assert preview.source_frame == indices[original_local]
        np.testing.assert_allclose(preview.frame, expected[original_local], atol=3e-7, rtol=0)
    # Exercise the timeline compiler/concatenation too, not just two isolated
    # intermediates. ProRes is intra-frame, so splitting must preserve every
    # decoded delivery plane, including the nonzero-start black prefix.
    whole_output = renderer.render(original, tmp_path / 'whole.mov',
                                   export_profile='prores_422_hq_10bit')
    split_output = renderer.render(project, tmp_path / 'split.mov',
                                   export_profile='prores_422_hq_10bit')
    for whole_plane, split_plane in zip(decode_yuv(whole_output, WIDTH, HEIGHT, True),
                                        decode_yuv(split_output, WIDTH, HEIGHT, True)):
        assert split_plane.shape[0] == 19
        np.testing.assert_array_equal(split_plane, whole_plane)
    assert editor.undo()
    assert editor.project == original


@pytest.mark.parametrize('mode', ['static-window', 'grade-disabled', 'qualifier-disabled',
                                 'window-off', 'neutral-adjustment'])
def test_static_and_bypassed_grades_preserve_decoded_pixels(tmp_path, renderer, mode):
    settings = animated_settings(() if mode == 'static-window' else MOVING_KEYS)
    project, source = make_source(tmp_path, settings, speed=Fraction(1, 2),
                                  origin=Fraction(3, 2), duration=4)
    grade = project.clips[0].grade
    if mode == 'grade-disabled':
        grade.enabled = False
    elif mode == 'qualifier-disabled':
        settings.enabled = False
    elif mode == 'window-off':
        settings.window_shape = 'off'
    elif mode == 'neutral-adjustment':
        settings.exposure = 0.
    keys = () if mode == 'static-window' else MOVING_KEYS
    expected = expected_frames(project, source, keys, (1, 2, 2, 3))
    stage = renderer._graded_segment(project.media[0], project.clips[0], project, None)
    decoded = decode_all_rgb16(stage)
    np.testing.assert_allclose(decoded, np.rint(expected * 65535), atol=2, rtol=0)
    if mode in ('grade-disabled', 'qualifier-disabled', 'neutral-adjustment'):
        np.testing.assert_array_equal(decoded, source[[1, 2, 2, 3]])
    np.testing.assert_array_equal(decoded[1], decoded[2])


def test_key_replacement_invalidates_snapshots_and_render_caches(tmp_path, renderer):
    project, source = make_source(tmp_path, animated_settings(), duration=4)
    clip = project.clips[0]
    before = capture_grade_preview_snapshot(project, clip.id, 2)
    first_stage = renderer._graded_segment(project.media[0], clip, project, None)
    first_pixels = decode_all_rgb16(first_stage)
    first_preview = renderer.preview(project)
    keys = clip.grade.qualifier.window_keyframes
    keys[1] = replace(keys[1], center_x=.20, width=.20)
    assert not before.is_current(project, clip.id, 2)
    new_keys = (MOVING_KEYS[0], (8, .20, .48, .20, .62, 'linear'))
    expected = expected_frames(project, source, new_keys, (0, 1, 2, 3))
    second_stage = renderer._graded_segment(project.media[0], clip, project, None)
    second_pixels = decode_all_rgb16(second_stage)
    assert second_stage != first_stage
    assert renderer.preview(project) != first_preview
    assert np.max(abs(first_pixels.astype(int) - second_pixels.astype(int))) > 5000
    np.testing.assert_allclose(second_pixels, np.rint(expected * 65535), atol=2, rtol=0)
    current = capture_grade_preview_snapshot(project, clip.id, 2)
    assert current.is_current(project, clip.id, 2)
    np.testing.assert_allclose(current.frame, expected[2], atol=3e-7, rtol=0)

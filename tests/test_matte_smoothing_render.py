"""Spatial matte acceptance with an independent scalar RGB16/video oracle.

Synthetic edges, single-pixel islands and deterministic noisy keys exercise
the *matte* neighborhood, rather than an image blur. Expected masks use Python
float arithmetic and scalar clamped-edge convolution, never production matte
helpers. Delivery checks decode all frames of newly rendered H.264 and ProRes.
"""
import colorsys
from dataclasses import asdict, replace
import json
from pathlib import Path
from types import SimpleNamespace
import threading
from uuid import uuid4

import numpy as np
import pytest

from lumen.color_picker_source import capture_picker_snapshot
from lumen.grade_transfer import copy_grade
from lumen.media import ffmpeg_available
from lumen.model import VERSION, Grade, Overlay, OverlayKeyframe, Project
from lumen.project_recovery import RecoveryStore
from lumen.qualifier import apply_qualifier, qualifier_mask
from lumen.render import RenderCancelled, RenderError
from test_color_picker_restoration import primary_reference
from test_color_picker_restoration_render import yuv_reference
from test_qualifier_render import decode_rgb, engine, ff, selective_reference, source_project
from test_qualifier_sets_render import range_weight, settings_for_sets
from test_static_window_render import spatial_reference
from test_window_handles_render import fresh_delivery


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def scalar_blur(matte):
    """Independent radius-one [1,2,1] x [1,2,1] / 16, replicate edges."""
    height, width = matte.shape
    result = np.empty_like(matte, dtype=float)
    for y in range(height):
        for x in range(width):
            value = 0.
            for dy, wy in ((-1, 1), (0, 2), (1, 1)):
                for dx, wx in ((-1, 1), (0, 2), (1, 1)):
                    yy, xx = min(height-1, max(0, y+dy)), min(width-1, max(0, x+dx))
                    value += matte[yy, xx] * wy * wx / 16
            result[y, x] = value
    return result


def scalar_reference(rgb, settings):
    """Compose ranges, levels, smooth, gate, then adjust ORIGINAL RGB."""
    rgb = np.asarray(rgb, dtype=float)
    composed = np.empty(rgb.shape[:2], dtype=float)
    for (y, x), _ in np.ndenumerate(composed):
        sample = rgb[y, x]
        inclusion = max(range_weight(sample, key)
                        for key in (settings, *settings.inclusion_ranges))
        exclusion = max((0., *(range_weight(sample, key)
                              for key in settings.exclusion_ranges)))
        composed[y, x] = inclusion * (1.-exclusion)
    levels = np.clip((composed-settings.matte_black)
                     / (settings.matte_white-settings.matte_black), 0., 1.)
    smoothed = levels + settings.matte_smoothing * (scalar_blur(levels)-levels)
    window = spatial_reference(rgb.shape[1], rgb.shape[0], settings)
    matte = smoothed * window if settings.enabled else np.zeros_like(smoothed)
    output = np.empty_like(rgb)
    for (y, x), weight in np.ndenumerate(matte):
        sample = rgb[y, x]
        hue, saturation, value = colorsys.rgb_to_hsv(*sample)
        rotated = np.array(colorsys.hsv_to_rgb(
            (hue+settings.hue_shift) % 1., saturation, value))
        luma = sum(v*c for v, c in zip(rotated, (.2126, .7152, .0722)))
        adjusted = (luma + settings.saturation*(rotated-luma))*2**settings.exposure
        output[y, x] = sample + weight*(adjusted-sample)
    return matte, output, levels


def textured_frames():
    """Three distinct frames: edges, islands, holes, feather noise and sets."""
    height, width = 64, 128
    hues = np.full((height, width), .12)
    hues[:, :24] = 0.
    hues[0, 0], hues[-1, 0] = .12, .12  # Exercise clamping at picture edges.
    hues[16, 40] = hues[32, 40] = hues[48, 40] = 0.
    hues[8:56, 48:72] = 0.
    hues[16, 60] = hues[32, 60] = hues[48, 60] = .12
    rng = np.random.default_rng(37121)
    hues[8:56, 80:120] = rng.choice([0., .04, .06, .12], (48, 40))
    hues[4:12, 28:72] = 2/3
    hues[54:62, 28:72] = 2/3+.05
    image = np.array([colorsys.hsv_to_rgb(hue, .7, .36) for hue in hues.ravel()])
    image = image.reshape(height, width, 3)
    image[4:12, 50:72] *= .35  # Subtractive blue keys, not merely one hue.
    return np.rint(65535*np.stack([image, np.roll(image, 1, axis=1),
                                  np.roll(image, -1, axis=0)])).astype('<u2')


def textured_project(tmp_path, settings, grade=None, *, name='smoothing'):
    return source_project(tmp_path, textured_frames(),
                          grade=grade or Grade(qualifier=settings), name=name)


def decode_rgb_frames(path, width, height):
    raw = ff('-i', path, '-pix_fmt', 'rgb48le', '-f', 'rawvideo', 'pipe:1')
    return np.frombuffer(raw, '<u2').reshape(-1, height, width, 3).astype(float)/65535


def assert_stage(renderer, project, expected):
    stage = renderer._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_allclose(decode_rgb_frames(stage, project.width, project.height),
                               expected, atol=3/65535, rtol=0)
    return stage


def assert_oracle_deliveries(project, expected, tmp_path, stem, *, unsmoothed=None):
    # Encode the independently predicted RGB16 through a neutral pipeline.
    # Comparing decoded deliveries retains real codec filtering at EVERY edge,
    # unlike interior-patch tests that discard all interesting neighborhoods.
    oracle, _ = source_project(tmp_path, np.rint(expected*65535).astype('<u2'),
                               name=stem+'oracle')
    for profile in ('preview', 'prores_422_hq_10bit'):
        _, actual = fresh_delivery(project, tmp_path, stem+profile, profile)
        _, reference = fresh_delivery(oracle, tmp_path, stem+'oracle-'+profile, profile)
        ten = profile.startswith('prores')
        for channel, (plane, target) in enumerate(zip(actual, reference)):
            error = abs(plane-target)
            assert error.mean() < .6, (profile, channel, error.mean())
            assert np.quantile(error, .99) <= 3, (profile, channel, error.max())
        if unsmoothed is not None:
            desired_y = yuv_reference(expected, ten)[..., 0]
            wrong_y = yuv_reference(unsmoothed, ten)[..., 0]
            sensitive = abs(desired_y-wrong_y) > (8 if ten else 2)
            assert sensitive.sum() > 400, 'The fixture must expose omitted smoothing'
            correct_error = abs(actual[0]-desired_y)[sensitive].mean()
            omitted_error = abs(actual[0]-wrong_y)[sensitive].mean()
            assert correct_error < omitted_error*.45, (profile, correct_error, omitted_error)


@pytest.mark.parametrize('amount,shape,invert', [
    (.25, 'off', False), (1., 'off', False),
    (1., 'rectangle', False), (.75, 'ellipse', True),
])
def test_edges_islands_noise_match_rgb16_preview_and_prores(
        tmp_path, engine, amount, shape, invert):
    settings = settings_for_sets(matte_smoothing=amount, matte_black=.2, matte_white=.8,
        exposure=.75, saturation=.8, hue_shift=.04,
        window_shape=shape, window_invert=invert, window_width=.55,
        window_height=.6, window_feather=0. if shape == 'rectangle' else .6)
    project, frames = textured_project(tmp_path, settings)
    incoming = frames.astype(float)/65535
    expected, unsmoothed = [], []
    for frame in incoming:
        matte, output, levels = scalar_reference(frame, settings)
        _, unfiltered, _ = scalar_reference(frame, replace(settings, matte_smoothing=0.))
        untouched = frame.astype(np.float32)
        source = untouched.copy()
        np.testing.assert_allclose(qualifier_mask(source, settings), matte, atol=2e-6, rtol=0)
        np.testing.assert_allclose(apply_qualifier(source, settings), output, atol=2e-6, rtol=0)
        np.testing.assert_array_equal(source, untouched)
        if shape != 'off':
            spatial = spatial_reference(project.width, project.height, settings)
            np.testing.assert_array_equal(matte[spatial == 0], 0.)
            wrong_order = levels*spatial
            wrong_order += amount*(scalar_blur(wrong_order)-wrong_order)
            assert np.max(abs(matte-wrong_order)) > (.025 if shape == 'rectangle' else .01)
        expected.append(output); unsmoothed.append(unfiltered)
    expected, unsmoothed = np.asarray(expected), np.asarray(unsmoothed)
    assert np.max(abs(expected-unsmoothed)) > .025
    assert_stage(engine, project, expected)
    assert_oracle_deliveries(project, expected, tmp_path, 'textured-', unsmoothed=unsmoothed)


def test_levels_are_applied_before_smoothing_and_window_cannot_leak(tmp_path, engine):
    settings = settings_for_sets(matte_smoothing=1., matte_black=.3, matte_white=.65,
        window_shape='rectangle', window_center_x=.5, window_center_y=.5,
        window_width=.55, window_height=.55, window_feather=0.)
    project, frames = textured_project(tmp_path, settings)
    incoming = frames[0].astype(float)/65535
    matte, expected, _ = scalar_reference(incoming, settings)
    raw_settings = replace(settings, matte_black=0., matte_white=1., matte_smoothing=0.,
                           window_shape='off')
    raw, _, _ = scalar_reference(incoming, raw_settings)
    spatial = spatial_reference(project.width, project.height, settings)
    wrong = np.clip((scalar_blur(raw)-settings.matte_black)
                    / (settings.matte_white-settings.matte_black), 0., 1.)*spatial
    assert np.max(abs(matte-wrong)) > .2
    actual = apply_qualifier(incoming.astype(np.float32), settings)
    np.testing.assert_array_equal(actual[spatial == 0], incoming.astype(np.float32)[spatial == 0])
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None)
    decoded = decode_rgb(stage, project.width, project.height)
    np.testing.assert_allclose(decoded, expected, atol=3/65535, rtol=0)
    np.testing.assert_array_equal(np.rint(decoded[spatial == 0]*65535).astype('<u2'),
                                  frames[0][spatial == 0])


@pytest.mark.parametrize('stride', [1, 2, 3, 5])
def test_full_source_and_dense_halo_crops_match_independent_source_pixel_oracle(stride):
    settings = settings_for_sets(matte_smoothing=.8, matte_black=.2, matte_white=.8,
        window_shape='ellipse', window_width=.8, window_height=.7, window_feather=.6)
    source = textured_frames()[0].astype(np.float32)/np.float32(65535)
    matte, expected, levels = scalar_reference(source, settings)
    ys, xs = np.arange(9, 53, stride), np.arange(11, 119, stride)
    cropped = source[9:53:stride, 11:119:stride]
    assert not cropped.flags.c_contiguous
    context = dict(canvas_size=(source.shape[1], source.shape[0]), origin=(11, 9), stride=stride)
    wanted_matte, wanted_rgb = matte[ys[:, None], xs[None, :]], expected[ys[:, None], xs[None, :]]
    actual = qualifier_mask(cropped, settings, matte_source=source, **context)
    np.testing.assert_allclose(actual, wanted_matte, atol=2e-6, rtol=0)
    np.testing.assert_allclose(apply_qualifier(cropped, settings, matte_source=source, **context),
                               wanted_rgb, atol=2e-6, rtol=0)
    halo = source[ys[0]-1:ys[-1]+2, xs[0]-1:xs[-1]+2]
    halo_context = dict(matte_source=halo, matte_source_origin=(10, 8), **context)
    np.testing.assert_array_equal(qualifier_mask(cropped, settings, **halo_context), actual)
    np.testing.assert_allclose(apply_qualifier(cropped, settings, **halo_context),
                               wanted_rgb, atol=2e-6, rtol=0)
    if stride > 1:
        # These are source-pixel neighbors, not adjacent downsampled overview
        # samples. A superficially plausible blur of the display grid is wrong.
        sampled = levels[ys[:, None], xs[None, :]]
        source_filtered = levels + .8*(scalar_blur(levels)-levels)
        display_filtered = sampled + .8*(scalar_blur(sampled)-sampled)
        assert np.max(abs(source_filtered[ys[:, None], xs[None, :]]-display_filtered)) > .2


def test_save_copy_recovery_and_cache_edits_keep_upstream_source_unchanged(tmp_path, engine):
    settings = settings_for_sets(matte_smoothing=0., matte_black=.1, matte_white=.85)
    grade = Grade(exposure=-.1, hue_hue_curve=[[0., .01], [1., .01]], qualifier=settings)
    project, frames = textured_project(tmp_path, settings, grade)
    incoming = np.asarray([selective_reference(primary_reference(frame.astype(float)/65535,
                                                                 grade), grade)
                           for frame in frames])
    before = np.asarray([scalar_reference(frame, settings)[1] for frame in incoming])
    snapshot = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    np.testing.assert_allclose(snapshot.frame, incoming[0], atol=6e-7, rtol=0)
    before_stage = assert_stage(engine, project, before)
    before_preview = engine.preview(project)
    settings.matte_smoothing = .8
    assert not snapshot.is_current(project)
    changed = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    np.testing.assert_array_equal(changed.frame, snapshot.frame)
    expected = np.asarray([scalar_reference(frame, settings)[1] for frame in incoming])
    assert np.max(abs(expected-before)) > .02
    assert assert_stage(engine, project, expected) != before_stage
    assert engine.preview(project) != before_preview
    saved = tmp_path/'spatial-matte.lumen'; project.save(saved)
    encoded = json.loads(saved.read_text())
    assert encoded['version'] == VERSION
    assert encoded['clips'][0]['grade']['qualifier'] == asdict(settings)
    restored = Project.load(saved)
    assert restored == project
    copied = copy_grade(project, 'picture').grade_for_input('auto')
    assert copied.qualifier == settings
    copied.qualifier.matte_smoothing = .2
    copied.qualifier.inclusion_ranges.clear()
    assert settings.matte_smoothing == .8 and len(settings.inclusion_ranges) == 1
    with RecoveryStore(tmp_path/'recovery') as store:
        recovered = store.load(store.write(project, str(uuid4()), saved))
        assert recovered == project
        assert_stage(engine, recovered, expected)
        assert_oracle_deliveries(recovered, expected, tmp_path, 'recovered-')
    settings.matte_smoothing = 0.
    assert assert_stage(engine, project, before) == before_stage
    assert engine.preview(project) == before_preview


@pytest.mark.parametrize('bypass', ['zero', 'disabled', 'neutral', 'grade-disabled'])
def test_smoothing_zero_and_bypasses_have_exact_decoded_parity(tmp_path, engine, bypass):
    settings = settings_for_sets(matte_smoothing=0.)
    project, frames = textured_project(tmp_path, settings)
    if bypass in ('disabled', 'neutral', 'grade-disabled'):
        project.clips[0].grade.qualifier.enabled = False
    before_stage = decode_rgb_frames(
        engine._graded_segment(project.media[0], project.clips[0], project, None),
        project.width, project.height)
    before = {profile: fresh_delivery(project, tmp_path, 'before-'+profile, profile)[0]
              for profile in ('preview', 'prores_422_hq_10bit')}
    if bypass == 'zero':
        settings.matte_smoothing = 0.0
        source = frames[0].astype(np.float32)/np.float32(65535)
        np.testing.assert_array_equal(qualifier_mask(source, settings).reshape(-1),
                                      qualifier_mask(source.reshape(-1, 3), settings))
        np.testing.assert_array_equal(apply_qualifier(source, settings).reshape(-1, 3),
                                      apply_qualifier(source.reshape(-1, 3), settings))
    else:
        settings.matte_smoothing = 1.
        if bypass != 'disabled': settings.enabled = True
        if bypass == 'neutral':
            settings.exposure, settings.saturation, settings.hue_shift = 0., 1., 0.
        if bypass == 'grade-disabled': project.clips[0].grade.enabled = False
    np.testing.assert_array_equal(decode_rgb_frames(
        engine._graded_segment(project.media[0], project.clips[0], project, None),
        project.width, project.height), before_stage)
    for profile in before:
        after, _ = fresh_delivery(project, tmp_path, 'after-'+profile, profile)
        assert after == before[profile], (bypass, profile)


def test_smoothing_does_not_change_straight_alpha_overlay(tmp_path, engine):
    from PIL import Image
    from lumen.sticker_assets import load_sticker
    settings = settings_for_sets(matte_smoothing=1., exposure=.6)
    project, _ = textured_project(tmp_path, settings)
    baseline = decode_rgb(engine.preview(project), project.width, project.height)
    path = tmp_path/'transparent-overlay.png'
    Image.new('RGBA', (32, 32), (0, 255, 0, 128)).save(path)
    asset = load_sticker(path)
    project.overlays = [Overlay('green', str(path), asset.digest, 0, 3,
                                keyframes=[OverlayKeyframe(0, 8, 8, 1., .5)])]
    output = decode_rgb(engine.preview(project), project.width, project.height)
    expected = baseline[16:32, 12:20]*.75 + np.array([0., 1., 0.])*.25
    np.testing.assert_allclose(output[16:32, 12:20], expected, atol=7/255, rtol=0)
    stage = engine._overlay_segment(project, {'green': asset}, None, None)
    rgba = np.frombuffer(ff('-i', stage, '-frames:v', 1, '-pix_fmt', 'rgba',
                            '-f', 'rawvideo', 'pipe:1'), np.uint8)
    rgba = rgba.reshape(project.height, project.width, 4)
    assert np.all((rgba[16:32, 16:32, 3] >= 63) & (rgba[16:32, 16:32, 3] <= 64))
    assert not rgba[:, 48:, 3].any()


@pytest.mark.parametrize('stop', ['cancel', 'timeout'])
def test_stop_during_real_decoded_smoothing_cleans_children_and_can_retry(
        tmp_path, engine, monkeypatch, stop):
    from lumen import color_media, qualifier
    settings = settings_for_sets(matte_smoothing=1.)
    project, frames = textured_project(tmp_path, settings)
    event = threading.Event()
    original_mask, original_clock = qualifier._mask_block, color_media.time
    original_popen = color_media.subprocess.Popen
    children, classified = [], []

    def track_process(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        children.append(process)
        return process

    def stop_after_first_chunk(*args, **kwargs):
        result = original_mask(*args, **kwargs)
        classified.append(len(args[0]))
        if stop == 'cancel':
            event.set()
        else:
            # Move only this bridge's deadline clock after a real decoder has
            # produced RGB and one bounded classifier chunk has completed.
            monkeypatch.setattr(color_media, 'time', SimpleNamespace(
                monotonic=lambda: original_clock.monotonic()+engine.timeout+1))
        return result

    with monkeypatch.context() as patches:
        patches.setattr(color_media.subprocess, 'Popen', track_process)
        patches.setattr(qualifier, '_BLOCK_PIXELS', 64)
        patches.setattr(qualifier, '_mask_block', stop_after_first_chunk)
        error = RenderCancelled if stop == 'cancel' else RenderError
        with pytest.raises(error, match='cancelled' if stop == 'cancel' else 'timed out'):
            engine._graded_segment(project.media[0], project.clips[0], project, event)
        assert classified and len(classified) == 1
        assert len(children) >= 2 and all(child.poll() is not None for child in children)
        assert not list(Path(engine._cache_dir.name).glob('*.mkv'))
        assert not list(Path(engine._cache_dir.name).glob('*.partial*'))
    monkeypatch.setattr(color_media, 'time', original_clock)
    event.clear()
    expected = np.asarray([scalar_reference(frame.astype(float)/65535, settings)[1]
                           for frame in frames])
    assert_stage(engine, project, expected)


def test_spatial_smoothing_follows_float_stabilization_and_canvas_padding(tmp_path, engine):
    from lumen.stabilization_render import PreparedStabilization
    settings = settings_for_sets(matte_smoothing=.8, window_shape='ellipse',
        window_width=.85, window_height=.65, window_feather=.3)
    project, frames = textured_project(tmp_path, settings)
    width, height = project.width, project.height
    project.width, project.height = width+16, height+16
    # Supply known inverse translations to the real stabilization bridge. This
    # isolates grade/warp ordering without claiming to retest the estimator.
    matrices = np.repeat(np.eye(3)[None], len(frames), axis=0)
    matrices[:, 0, 2] = [.5, 1., 1.5]
    prepared = PreparedStabilization(Path(project.media[0].path),
        SimpleNamespace(active_width=width, active_height=height), matrices,
        'independent-smoothing-translation-fixture', {})
    expected = []
    for frame, shift in zip(frames.astype(float)/65535, (.5, 1., 1.5)):
        stable = np.zeros_like(frame)
        for x in range(width):
            source_x = x+shift
            left, fraction = int(source_x), source_x-int(source_x)
            if left < width: stable[:, x] += frame[:, left]*(1-fraction)
            if left+1 < width: stable[:, x] += frame[:, left+1]*fraction
        canvas = np.zeros((height+16, width+16, 3), dtype=float)
        canvas[8:height+8, 8:width+8] = stable
        expected.append(scalar_reference(canvas, settings)[1])
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None,
                                    prepared_stabilization=prepared)
    np.testing.assert_allclose(decode_rgb_frames(stage, project.width, project.height),
                               expected, atol=3/65535, rtol=0)

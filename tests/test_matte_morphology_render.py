"""Independent signed-morphology acceptance through actual decoded media.

Only synthetic lossless RGB16 sources are used. The oracle classifies every
pixel with scalar HSV arithmetic, uses Python min/max over clamped 3x3
neighborhoods, then independently smooths and gates the matte. No production
morphology, mask, color-adjustment or preview raster supplies expected pixels.
"""
import colorsys
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import threading
from uuid import uuid4

import numpy as np
import pytest

from lumen.color_picker_source import capture_picker_snapshot
from lumen.grade_transfer import copy_grade
from lumen.media import ffmpeg_available
from lumen.model import VERSION, Editor, Grade, Project
from lumen.qualifier import apply_qualifier, qualifier_mask
from lumen.render import RenderCancelled, RenderError
from lumen.project_recovery import RecoveryStore
from test_color_picker_restoration import primary_reference
from test_color_picker_restoration_render import yuv_reference
from test_matte_smoothing_render import assert_stage, decode_rgb_frames, textured_frames
from test_qualifier_detail_ui import zoom
from test_qualifier_render import engine, selective_reference, source_project
from test_qualifier_sets_render import range_weight, settings_for_sets
from test_qualifier_ui import app, loaded, window
from test_static_window_render import spatial_reference
from test_window_handles_render import fresh_delivery


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def scalar_morphology(matte, amount):
    """Independent min/max square radius-one operator with true-edge clamping."""
    height, width = matte.shape
    result = np.empty_like(matte, dtype=float)
    for y in range(height):
        for x in range(width):
            neighbors = [matte[min(height-1, max(0, y+dy)),
                               min(width-1, max(0, x+dx))]
                         for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
            extreme = min(neighbors) if amount < 0 else max(neighbors)
            result[y, x] = matte[y, x] + abs(amount)*(extreme-matte[y, x])
    return result


def scalar_smoothing(matte, amount):
    height, width = matte.shape
    result = np.empty_like(matte, dtype=float)
    for y in range(height):
        for x in range(width):
            filtered = sum(matte[min(height-1, max(0, y+dy)),
                                 min(width-1, max(0, x+dx))]*wy*wx/16
                           for dy, wy in ((-1, 1), (0, 2), (1, 1))
                           for dx, wx in ((-1, 1), (0, 2), (1, 1)))
            result[y, x] = matte[y, x] + amount*(filtered-matte[y, x])
    return result


def scalar_reference(rgb, settings):
    """Ranges -> levels -> signed morphology -> smoothing -> one window."""
    rgb = np.asarray(rgb, dtype=float)
    composed = np.empty(rgb.shape[:2], dtype=float)
    for (y, x), _ in np.ndenumerate(composed):
        sample = rgb[y, x]
        include = max(range_weight(sample, key)
                      for key in (settings, *settings.inclusion_ranges))
        exclude = max((0., *(range_weight(sample, key)
                             for key in settings.exclusion_ranges)))
        composed[y, x] = include*(1-exclude)
    levels = np.clip((composed-settings.matte_black)
                     / (settings.matte_white-settings.matte_black), 0., 1.)
    expanded = scalar_morphology(levels, settings.matte_morphology)
    filtered = scalar_smoothing(expanded, settings.matte_smoothing)
    spatial = spatial_reference(rgb.shape[1], rgb.shape[0], settings)
    matte = filtered*spatial if settings.enabled else np.zeros_like(filtered)
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


def morphology_frames():
    """Noisy feathers, island/hole diagonals, all corners and off-axis edges."""
    image = textured_frames()[0].copy()
    red = np.rint(np.array(colorsys.hsv_to_rgb(0., .7, .36))*65535).astype('<u2')
    yellow = np.rint(np.array(colorsys.hsv_to_rgb(.12, .7, .36))*65535).astype('<u2')
    image[:8, :8] = red
    image[0, 0] = yellow                 # A canvas-corner hole in selected pixels.
    image[:8, -8:] = yellow
    image[0, -1] = red                  # A selected canvas-corner island.
    image[-8:, :8] = yellow
    image[-1, 0] = red
    image[-8:, -8:] = red
    image[-1, -1] = yellow
    for i in range(8):
        image[16+i, 28+i] = red         # Diagonal islands require corner neighbors.
        image[36+i, 52+i] = yellow      # Diagonal holes inside selected region.
    return np.stack([image, np.roll(image, 1, axis=1),
                     np.roll(image, -1, axis=0)])


def morphology_project(tmp_path, settings, grade=None, *, name='morphology'):
    return source_project(tmp_path, morphology_frames(),
                          grade=grade or Grade(qualifier=settings), name=name)


def assert_deliveries(project, expected, tmp_path, stem, *, omitted=None):
    # Independently predicted RGB is encoded through a neutral real pipeline.
    # All decoded pixels (including edges) are compared, never only interiors.
    oracle, _ = source_project(tmp_path, np.rint(expected*65535).astype('<u2'),
                               name=stem+'oracle')
    metrics = {}
    for profile in ('preview', 'prores_422_hq_10bit'):
        _, actual = fresh_delivery(project, tmp_path, stem+profile, profile)
        _, reference = fresh_delivery(oracle, tmp_path, stem+'oracle-'+profile, profile)
        metrics[profile] = []
        ten = profile.startswith('prores')
        for channel, (plane, target) in enumerate(zip(actual, reference)):
            error = abs(plane-target)
            metrics[profile].append(dict(channel=channel, mean=float(error.mean()),
                p99=float(np.quantile(error, .99)), maximum=float(error.max())))
            assert error.mean() < .6, (profile, channel, error.mean())
            assert np.quantile(error, .99) <= 3, (profile, channel, error.max())
        if omitted is not None:
            wanted_y = yuv_reference(expected, ten)[..., 0]
            wrong_y = yuv_reference(omitted, ten)[..., 0]
            sensitive = abs(wanted_y-wrong_y) > (8 if ten else 2)
            assert sensitive.sum() > 400, 'Fixture must distinguish omitted morphology'
            correct_error = abs(actual[0]-wanted_y)[sensitive].mean()
            omitted_error = abs(actual[0]-wrong_y)[sensitive].mean()
            assert correct_error < omitted_error*.45, (profile, correct_error, omitted_error)
    if directory := os.environ.get('MORPHOLOGY_EVIDENCE_DIR'):
        path = Path(directory); path.mkdir(parents=True, exist_ok=True)
        (path/(stem+'decoded-metrics.json')).write_text(json.dumps(metrics, indent=2))


@pytest.mark.parametrize('amount,smoothing,shape,invert', [
    (-1., 0., 'off', False), (-.35, 0., 'off', False),
    (.35, 0., 'off', False), (1., 0., 'off', False),
    (-.75, .6, 'rectangle', False), (.75, .8, 'ellipse', True),
])
def test_signed_edges_diagonals_holes_rgb16_preview_and_prores(
        tmp_path, engine, amount, smoothing, shape, invert):
    settings = settings_for_sets(matte_morphology=amount, matte_smoothing=smoothing,
        matte_black=.2, matte_white=.8, exposure=.75, saturation=.8, hue_shift=.04,
        window_shape=shape, window_invert=invert, window_width=.55,
        window_height=.6, window_feather=0. if shape == 'rectangle' else .6)
    project, frames = morphology_project(tmp_path, settings)
    expected, omitted = [], []
    for frame in frames.astype(float)/65535:
        matte, output, levels = scalar_reference(frame, settings)
        no_morph = scalar_reference(frame, replace(settings, matte_morphology=0.))[1]
        source = frame.astype(np.float32); before = source.copy()
        np.testing.assert_allclose(qualifier_mask(source, settings), matte, atol=2e-6, rtol=0)
        np.testing.assert_allclose(apply_qualifier(source, settings), output, atol=2e-6, rtol=0)
        np.testing.assert_array_equal(source, before)
        if shape != 'off':
            gate = spatial_reference(project.width, project.height, settings)
            np.testing.assert_array_equal(matte[gate == 0], 0.)
            np.testing.assert_array_equal(apply_qualifier(source, settings)[gate == 0],
                                          source[gate == 0])
            wrong = scalar_smoothing(scalar_morphology(levels*gate, amount), smoothing)
            assert np.max(abs(matte-wrong)) > .01
        expected.append(output); omitted.append(no_morph)
    expected, omitted = np.asarray(expected), np.asarray(omitted)
    assert np.max(abs(expected-omitted)) > .025
    assert_stage(engine, project, expected)
    stem = f'signed-{amount:g}-smooth-{smoothing:g}-{shape}-'
    assert_deliveries(project, expected, tmp_path, stem, omitted=omitted)


@pytest.mark.parametrize('amount', [-.6, .6])
def test_operator_order_is_levels_then_morphology_then_smoothing_then_window(amount):
    settings = settings_for_sets(matte_morphology=amount, matte_smoothing=.8,
        matte_black=.3, matte_white=.65, window_shape='rectangle',
        window_width=.55, window_height=.55, window_feather=0.)
    source = morphology_frames()[0].astype(float)/65535
    matte, expected, levels = scalar_reference(source, settings)
    raw_settings = replace(settings, matte_black=0., matte_white=1.,
                           matte_morphology=0., matte_smoothing=0., window_shape='off')
    raw = scalar_reference(source, raw_settings)[0]
    gate = spatial_reference(source.shape[1], source.shape[0], settings)
    wrong_levels = np.clip((scalar_morphology(raw, amount)-settings.matte_black)
                           / (settings.matte_white-settings.matte_black), 0., 1.)
    wrong_levels = scalar_smoothing(wrong_levels, .8)*gate
    wrong_smoothing = scalar_morphology(scalar_smoothing(levels, .8), amount)*gate
    assert np.max(abs(matte-wrong_levels)) > .05
    assert np.max(abs(matte-wrong_smoothing)) > .1
    np.testing.assert_allclose(qualifier_mask(source.astype(np.float32), settings),
                               matte, atol=2e-6, rtol=0)
    np.testing.assert_allclose(apply_qualifier(source.astype(np.float32), settings),
                               expected, atol=2e-6, rtol=0)


@pytest.mark.parametrize('stride', [1, 2, 3, 5])
@pytest.mark.parametrize('amount', [-.8, .8])
def test_dense_full_source_and_two_pixel_halos_match_true_source_oracle(stride, amount):
    settings = settings_for_sets(matte_morphology=amount, matte_smoothing=.7,
        matte_black=.2, matte_white=.8, window_shape='ellipse',
        window_width=.8, window_height=.7, window_feather=.6)
    source = morphology_frames()[0].astype(np.float32)/np.float32(65535)
    matte, expected, levels = scalar_reference(source, settings)
    ys, xs = np.arange(9, 53, stride), np.arange(11, 119, stride)
    sample = source[9:53:stride, 11:119:stride]
    assert not sample.flags.c_contiguous
    context = dict(canvas_size=(source.shape[1], source.shape[0]), origin=(11, 9), stride=stride)
    wanted = matte[ys[:, None], xs[None, :]]
    actual = qualifier_mask(sample, settings, matte_source=source, **context)
    np.testing.assert_allclose(actual, wanted, atol=2e-6, rtol=0)
    halo = source[ys[0]-2:ys[-1]+3, xs[0]-2:xs[-1]+3]
    halo_context = dict(matte_source=halo, matte_source_origin=(9, 7), **context)
    np.testing.assert_array_equal(qualifier_mask(sample, settings, **halo_context), actual)
    np.testing.assert_allclose(apply_qualifier(sample, settings, **halo_context),
        expected[ys[:, None], xs[None, :]], atol=2e-6, rtol=0)
    # A radius-one crop is insufficient only when both operations are enabled.
    too_small = source[ys[0]-1:ys[-1]+2, xs[0]-1:xs[-1]+2]
    with pytest.raises(ValueError, match='halo'):
        qualifier_mask(sample, settings, matte_source=too_small,
                       matte_source_origin=(10, 8), **context)
    if stride > 1:
        sampled = levels[ys[:, None], xs[None, :]]
        wrong = scalar_smoothing(scalar_morphology(sampled, amount), .7)
        full = scalar_smoothing(scalar_morphology(levels, amount), .7)
        assert np.max(abs(full[ys[:, None], xs[None, :]]-wrong)) > .15


@pytest.mark.parametrize('corner', ['top-left', 'top-right', 'bottom-left', 'bottom-right'])
@pytest.mark.parametrize('amount', [-1., 1.])
def test_combined_halos_replicate_true_canvas_edges_not_crop_edges(corner, amount):
    source = morphology_frames()[0].astype(np.float32)/np.float32(65535)
    h, w = source.shape[:2]
    x, y = (0 if corner.endswith('left') else w-8), (0 if corner.startswith('top') else h-8)
    settings = settings_for_sets(matte_morphology=amount, matte_smoothing=.7)
    matte, expected, _ = scalar_reference(source, settings)
    sample = source[y:y+8, x:x+8]
    left, top = max(0, x-2), max(0, y-2)
    halo = source[top:min(h, y+10), left:min(w, x+10)]
    context = dict(canvas_size=(w, h), origin=(x, y), matte_source=halo,
                   matte_source_origin=(left, top))
    np.testing.assert_allclose(qualifier_mask(sample, settings, **context),
                               matte[y:y+8, x:x+8], atol=2e-6, rtol=0)
    np.testing.assert_allclose(apply_qualifier(sample, settings, **context),
                               expected[y:y+8, x:x+8], atol=2e-6, rtol=0)


def test_edit_save_recovery_copy_undo_and_cache_keep_upstream_source(tmp_path, engine):
    settings = settings_for_sets(matte_morphology=0., matte_smoothing=.65,
                                matte_black=.1, matte_white=.85)
    grade = Grade(exposure=-.1, hue_hue_curve=[[0., .01], [1., .01]], qualifier=settings)
    project, frames = morphology_project(tmp_path, settings, grade)
    editor = Editor(project)
    incoming = np.asarray([selective_reference(primary_reference(frame.astype(float)/65535,
                                                                 grade), grade)
                           for frame in frames])
    before = np.asarray([scalar_reference(frame, settings)[1] for frame in incoming])
    snapshot = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    np.testing.assert_allclose(snapshot.frame, incoming[0], atol=6e-7, rtol=0)
    before_stage = assert_stage(engine, project, before)
    before_preview = engine.preview(project)
    new_grade = asdict(grade); new_grade['qualifier']['matte_morphology'] = -.6
    editor.update_clip('picture', grade=new_grade)
    project = editor.project; changed_settings = project.clips[0].grade.qualifier
    assert len(editor._undo) == 1 and not snapshot.is_current(project)
    changed = capture_picker_snapshot(project, 'picture', 0, stage='qualifier')
    np.testing.assert_array_equal(changed.frame, snapshot.frame)
    expected = np.asarray([scalar_reference(frame, changed_settings)[1] for frame in incoming])
    assert np.max(abs(expected-before)) > .02
    changed_stage = assert_stage(engine, project, expected)
    changed_preview = engine.preview(project)
    assert changed_stage != before_stage and changed_preview != before_preview
    saved = tmp_path/'signed-matte.lumen'; project.save(saved)
    encoded = json.loads(saved.read_text())
    assert encoded['version'] == VERSION
    assert encoded['clips'][0]['grade']['qualifier'] == asdict(changed_settings)
    assert Project.load(saved) == project
    copied = copy_grade(project, 'picture').grade_for_input('auto')
    assert copied.qualifier == changed_settings
    copied.qualifier.matte_morphology = .2; copied.qualifier.inclusion_ranges.clear()
    assert changed_settings.matte_morphology == -.6 and len(changed_settings.inclusion_ranges) == 1
    with RecoveryStore(tmp_path/'recovery') as store:
        recovered = store.load(store.write(project, str(uuid4()), saved))
        assert recovered == project
        assert_stage(engine, recovered, expected)
        assert_deliveries(recovered, expected, tmp_path, 'recovered-')
    editor.undo()
    assert assert_stage(engine, editor.project, before) == before_stage
    assert engine.preview(editor.project) == before_preview
    editor.redo()
    assert assert_stage(engine, editor.project, expected) == changed_stage
    assert engine.preview(editor.project) == changed_preview


@pytest.mark.parametrize('bypass', ['zero', 'disabled', 'neutral', 'grade-disabled'])
def test_zero_and_disabled_paths_have_exact_decoded_parity(tmp_path, engine, bypass):
    settings = settings_for_sets(matte_morphology=0., matte_smoothing=.7)
    project, _ = morphology_project(tmp_path, settings)
    if bypass != 'zero': settings.enabled = False
    stage = engine._graded_segment(project.media[0], project.clips[0], project, None)
    before_stage = decode_rgb_frames(stage, project.width, project.height)
    before = {profile: fresh_delivery(project, tmp_path, 'before-'+profile, profile)[0]
              for profile in ('preview', 'prores_422_hq_10bit')}
    if bypass == 'zero':
        settings.matte_morphology = 0.0
    else:
        settings.matte_morphology = -1.
        if bypass != 'disabled': settings.enabled = True
        if bypass == 'neutral': settings.exposure, settings.saturation, settings.hue_shift = 0., 1., 0.
        if bypass == 'grade-disabled': project.clips[0].grade.enabled = False
    after = engine._graded_segment(project.media[0], project.clips[0], project, None)
    np.testing.assert_array_equal(decode_rgb_frames(after, project.width, project.height), before_stage)
    for profile in before:
        after, _ = fresh_delivery(project, tmp_path, 'after-'+profile, profile)
        assert after == before[profile], (bypass, profile)


@pytest.mark.parametrize('amount', [-.8, .8])
def test_full_resolution_matte_overlay_and_exports_use_one_unchanged_source(
        tmp_path, window, app, engine, monkeypatch, amount):
    settings = settings_for_sets(matte_morphology=amount, matte_smoothing=.65,
        matte_black=.2, matte_white=.8, window_shape='ellipse',
        window_width=.7, window_height=.8, window_feather=.3)
    project, frames = morphology_project(tmp_path, settings)
    window.editor = Editor(project); window.refresh(); window.select_clip('picture')
    before_project = deepcopy(project)
    expected = np.asarray([scalar_reference(frame.astype(float)/65535, settings)[1]
                           for frame in frames])
    assert_stage(engine, project, expected)
    baseline = {profile: fresh_delivery(project, tmp_path, 'before-overlay-'+profile, profile)[0]
                for profile in ('preview', 'prores_422_hq_10bit')}
    dialog = loaded(window)
    source = dialog.snapshot.frame
    frozen_hash = hashlib.sha256(source.tobytes()).hexdigest()
    np.testing.assert_allclose(source, frames[0].astype(float)/65535, atol=6e-7, rtol=0)
    matte, adjusted, _ = scalar_reference(source, settings)
    for level, center in ((0, (64, 32)), (8, (0, 0)), (8, (127, 63))):
        zoom(dialog, level)
        if level: dialog.set_detail_center(*center)
        for mode in ('matte', 'overlay', 'adjusted'):
            dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData(mode))
            dialog.render_preview()
            wanted = dialog.viewport.extract(matte)
            np.testing.assert_allclose(dialog.matte, wanted, atol=2e-6, rtol=0)
            original = dialog.viewport.extract(source)
            if mode == 'matte': pixels = np.repeat(wanted[..., None], 3, axis=-1)
            elif mode == 'overlay':
                pixels = original*(1-.5*wanted[..., None]) + np.array([1, 0, 1])*.5*wanted[..., None]
            else: pixels = dialog.viewport.extract(adjusted)
            np.testing.assert_allclose(dialog.canvas._pixels.astype(int),
                np.rint(np.clip(pixels, 0, 1)*255).astype(int), atol=1, rtol=0)
    assert source is dialog.snapshot.frame and not source.flags.writeable
    assert hashlib.sha256(source.tobytes()).hexdigest() == frozen_hash
    dialog.apply_settings()
    assert window.project == before_project and not window.editor.can_undo
    monkeypatch.setattr('lumen.qualifier_overlay.selection_overlay',
        lambda *a, **k: pytest.fail('Inspection-only tint reached an export'))
    for profile in baseline:
        actual, _ = fresh_delivery(window.project, tmp_path, 'after-overlay-'+profile, profile)
        assert actual == baseline[profile]


@pytest.mark.parametrize('stop', ['cancel', 'timeout'])
@pytest.mark.parametrize('smoothing', [0., .7])
def test_stopped_real_decode_cleans_children_and_can_retry(tmp_path, engine, monkeypatch, stop, smoothing):
    from lumen import color_media, qualifier
    settings = settings_for_sets(matte_morphology=-.8, matte_smoothing=smoothing)
    project, frames = morphology_project(tmp_path, settings)
    event = threading.Event()
    original_mask, original_clock = qualifier._mask_block, color_media.time
    original_popen = color_media.subprocess.Popen
    children, classified = [], []

    def track_process(*args, **kwargs):
        process = original_popen(*args, **kwargs); children.append(process)
        return process

    def stop_after_first_chunk(*args, **kwargs):
        result = original_mask(*args, **kwargs); classified.append(len(args[0]))
        if stop == 'cancel': event.set()
        else:
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
        assert classified and len(classified) == 1 and max(classified) <= 64
        assert len(children) >= 2 and all(child.poll() is not None for child in children)
        assert not list(Path(engine._cache_dir.name).glob('*.mkv'))
        assert not list(Path(engine._cache_dir.name).glob('*.partial*'))
    monkeypatch.setattr(color_media, 'time', original_clock); event.clear()
    expected = np.asarray([scalar_reference(frame.astype(float)/65535, settings)[1]
                           for frame in frames])
    assert_stage(engine, project, expected)


@pytest.mark.parametrize('stride', [1, 2, 5])
def test_combined_operator_classification_respects_small_tile_budget(monkeypatch, stride):
    from lumen import qualifier
    settings = settings_for_sets(matte_morphology=.7, matte_smoothing=.6)
    source = morphology_frames()[0].astype(np.float32)/np.float32(65535)
    wanted, expected, _ = scalar_reference(source, settings)
    original = qualifier._mask_block
    original_tile = qualifier._raw_image_mask
    chunks, tiles = [], []

    def classify_tile(rgb, *args, **kwargs):
        count = rgb.shape[0]*rgb.shape[1]
        tiles.append(count)
        assert count <= 64, 'Dense/sparse halo allocation exceeded its tile budget'
        return original_tile(rgb, *args, **kwargs)

    def classify(block, *args, **kwargs):
        chunks.append(len(block))
        assert len(block) <= 64, 'Neighborhood classification exceeded its tile budget'
        return original(block, *args, **kwargs)

    monkeypatch.setattr(qualifier, '_BLOCK_PIXELS', 64)
    monkeypatch.setattr(qualifier, '_mask_block', classify)
    monkeypatch.setattr(qualifier, '_raw_image_mask', classify_tile)
    sample = source[::stride, ::stride]
    context = dict(canvas_size=(source.shape[1], source.shape[0]), stride=stride,
                   matte_source=source)
    np.testing.assert_allclose(qualifier_mask(sample, settings, **context),
                               wanted[::stride, ::stride], atol=2e-6, rtol=0)
    np.testing.assert_allclose(apply_qualifier(sample, settings, **context),
                               expected[::stride, ::stride], atol=2e-6, rtol=0)
    assert chunks and len(chunks) > 10 and max(chunks) <= 64
    assert tiles and len(tiles) > 10 and max(tiles) <= 64


def test_combined_operator_follows_float_stabilization_and_canvas_padding(tmp_path, engine):
    from lumen.stabilization_render import PreparedStabilization
    settings = settings_for_sets(matte_morphology=-.7, matte_smoothing=.8,
        window_shape='ellipse', window_width=.85, window_height=.65, window_feather=.3)
    project, frames = morphology_project(tmp_path, settings)
    width, height = project.width, project.height
    project.width, project.height = width+16, height+16
    # Known inverse translations test pipeline ordering, not the motion estimator.
    matrices = np.repeat(np.eye(3)[None], len(frames), axis=0)
    matrices[:, 0, 2] = [.5, 1., 1.5]
    prepared = PreparedStabilization(Path(project.media[0].path),
        SimpleNamespace(active_width=width, active_height=height), matrices,
        'independent-morphology-translation-fixture', {})
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

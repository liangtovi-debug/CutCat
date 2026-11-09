"""Real two-source picture-in-picture workflow acceptance.

All pictures/audio are synthetic local fixtures. Geometry, source-frame selection,
source-over and sample budgets have independent NumPy/Fraction oracles. Delivery
codecs are lossy; only flat interiors are compared numerically, while untouched
production-graph YUV and pre-AAC PCM use exact identity checks. This is software
render evidence, not native desktop playback or packaged-app certification.
"""
from copy import deepcopy
from fractions import Fraction
import hashlib
import os
from pathlib import Path
import threading
import wave

import numpy as np
import pytest

from lumen.clip_composite import ClipComposite
from lumen.clip_transform import ClipTransform
from lumen.media import ffmpeg_available, probe
from lumen.model import Caption, CaptionStyle, Clip, ClipEffect, Editor, OverlayKeyframe, Project
from lumen.render import RenderCancelled, RenderEngine, RenderError
from lumen.timing import ClipTimeMap
from test_color_highbit_reference import ff, rgb_source, video_metadata
from test_highbit_caption_reference import latin_font
from test_overlay_render import sticker

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
WIDTH, HEIGHT, SOURCE_FRAMES = 96, 64, 24
FPS = Fraction(30000, 1001)
PROFILE = 'prores_422_hq_10bit'


def decode(path, *, width=WIDTH, height=HEIGHT):
    raw = ff('-i', path, '-map', '0:V:0', '-an', '-fps_mode', 'passthrough',
             '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1')
    return np.frombuffer(raw, np.uint8).reshape(-1, height, width, 3)


def independent_samples(frames):
    value = Fraction(frames * 48000, 1) / FPS
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def source_arrays():
    lower = np.empty((SOURCE_FRAMES, HEIGHT, WIDTH, 3), dtype=np.uint16)
    upper = np.empty_like(lower)
    for frame in range(SOURCE_FRAMES):
        lower[frame, :32, :48] = (30 + frame, 110, 186)
        lower[frame, :32, 48:] = (172, 58 + frame, 28)
        lower[frame, 32:, :48] = (40, 170, 64 + frame)
        lower[frame, 32:, 48:] = (130 + frame, 42, 162)
        # Actual black and white are opaque image content, not transparency.
        upper[frame, :32, :48] = (0, 0, 0)
        upper[frame, :32, 48:] = (255, 255, 255)
        upper[frame, 32:, :48] = 16 + frame * 9
        upper[frame, 32:, 48:] = (255, frame * 4, 0)
    return lower * 257, upper * 257


@pytest.fixture(scope='module')
def sources(tmp_path_factory):
    directory = tmp_path_factory.mktemp('pip-two-real-sources')
    lower, upper = source_arrays()
    media = []
    for index, pixels in enumerate((lower, upper)):
        picture = rgb_source(directory, pixels, name=f'picture-{index}', fps=FPS)
        count = independent_samples(SOURCE_FRAMES)
        # Distinct, bounded deterministic PCM: no mic, user media or network.
        phase = np.arange(count) / 48000
        pcm = np.rint(7000 * np.sin(2 * np.pi * (431 + index * 394) * phase)).astype('<i2')
        sound = directory / f'sound-{index}.wav'
        with wave.open(str(sound), 'wb') as stream:
            stream.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
            stream.writeframes(pcm.tobytes())
        target = directory / f'av-{index}.mkv'
        ff('-i', picture.path, '-i', sound, '-map', '0:V:0', '-map', '1:a:0',
           '-c', 'copy', target)
        media.append(probe(target))
    return media, (lower.astype(float) / 257, upper.astype(float) / 257)


@pytest.fixture
def engine():
    value = RenderEngine(timeout=60)
    try:
        yield value
    finally:
        value.close()


def project(sources, *, frames=3, transform=None, opacity=1, transparent=True):
    media, _ = sources
    return Project(width=WIDTH, height=HEIGHT, fps_num=FPS.numerator,
                   fps_den=FPS.denominator, media=deepcopy(media), clips=[
        Clip('background', media[0].id, 0, 0, 0, frames, muted=True),
        Clip('foreground', media[1].id, 1, 0, 0, frames, muted=True,
             transform=transform or ClipTransform(scale=.5),
             composite=ClipComposite(transparent, opacity)),
    ])


def affine(image, transform):
    """Independent matrix inversion and bilinear sampler, with geometric alpha."""
    h, w, _ = image.shape
    center = np.array([(w - 1) / 2, (h - 1) / 2])
    angle = np.deg2rad(transform.rotation)
    rotation = np.array([[np.cos(angle), -np.sin(angle)],
                         [np.sin(angle), np.cos(angle)]])
    scales = np.diag([(-1 if transform.mirror_x else 1) * transform.scale,
                      (-1 if transform.mirror_y else 1) * transform.scale])
    yy, xx = np.indices((h, w))
    coords = np.stack([xx.ravel(), yy.ravel()])
    xy = np.linalg.solve(rotation @ scales,
                         coords - center[:, None] - np.array([[transform.x], [transform.y]]))
    xy += center[:, None]
    near = np.abs(xy - np.rint(xy)) < 1e-9
    xy[near] = np.rint(xy[near])
    x, y = xy
    covered = (x >= 0) & (x <= w - 1) & (y >= 0) & (y <= h - 1)
    x, y = np.clip(x, 0, w - 1), np.clip(y, 0, h - 1)
    left, top = np.floor(x).astype(int), np.floor(y).astype(int)
    right, bottom = np.minimum(left + 1, w - 1), np.minimum(top + 1, h - 1)
    dx, dy = (x - left)[:, None], (y - top)[:, None]
    output = ((1 - dx) * (1 - dy) * image[top, left] +
              dx * (1 - dy) * image[top, right] +
              (1 - dx) * dy * image[bottom, left] + dx * dy * image[bottom, right])
    output[~covered] = 0
    return output.reshape(h, w, 3), covered.reshape(h, w)


def source_over(lower, upper, clip):
    transformed, covered = affine(upper, clip.transform)
    alpha = clip.composite.opacity * (covered if clip.composite.transparent_outside_transform
                                      else np.ones(covered.shape))
    return lower * (1 - alpha[..., None]) + transformed * alpha[..., None]


def flat_mask(expected, radius=3, threshold=8):
    low, high = expected.copy(), expected.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            other = np.roll(np.roll(expected, dy, axis=0), dx, axis=1)
            low, high = np.minimum(low, other), np.maximum(high, other)
    mask = np.max(high - low, axis=-1) < threshold
    mask[:radius] = mask[-radius:] = False
    mask[:, :radius] = mask[:, -radius:] = False
    return mask


def assert_picture(actual, expected, *, tolerance=7):
    assert actual.shape == expected.shape
    for got, want in zip(actual, expected):
        mask = flat_mask(want)
        assert mask.sum() > WIDTH * HEIGHT // 5, 'Fixture must inspect substantial interiors'
        error = np.abs(got.astype(float)[mask] - want[mask])
        assert np.quantile(error, .995) <= tolerance
        assert error.max() <= tolerance + 9


def deliveries(engine, p, directory, name):
    paths = [engine.preview(p), engine.render(p, directory / f'{name}.mp4'),
             engine.render(p, directory / f'{name}.mov', export_profile=PROFILE)]
    for path in paths:
        metadata = video_metadata(path)
        if path.suffix == '.mov':
            assert (metadata['codec_name'], metadata['profile'], metadata['pix_fmt']) == (
                'prores', 'HQ', 'yuv422p10le')
        else:
            assert metadata['pix_fmt'] == 'yuv420p'
        info = probe(path)
        assert Fraction(info.fps_num, info.fps_den) == FPS
        yield decode(path)


def compiled_video(engine, p, profile=PROFILE):
    """Run the unchanged production graph before the final lossy encoder."""
    inputs, graph, _ = engine._compile(p, export_profile=profile)
    fmt = 'yuv422p10le' if profile == PROFILE else 'yuv420p'
    raw = ff('-filter_complex_threads', '1', *inputs, '-filter_complex', graph,
             '-map', '[vout]', '-frames:v', p.duration_frames,
             '-r', str(FPS), '-fps_mode', 'cfr', '-c:v', 'rawvideo', '-threads', '1',
             '-pix_fmt', fmt, '-f', 'rawvideo', 'pipe:1',
             '-map', '[aout]', '-f', 'null', os.devnull)
    size = WIDTH * HEIGHT * (4 if profile == PROFILE else 3) // (1 if profile == PROFILE else 2)
    return np.frombuffer(raw, np.uint8).reshape(-1, size), graph


def compiled_pcm(engine, p, directory, name):
    inputs, graph, _ = engine._compile(p)
    target = directory / f'{name}.mkv'
    ff('-filter_complex_threads', '1', *inputs, '-filter_complex', graph,
       '-map', '[vout]', '-map', '[aout]', '-c:v', 'ffv1', '-c:a', 'pcm_f32le', target)
    raw = ff('-i', target, '-map', '0:a:0', '-ar', '48000', '-ac', '2',
             '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1')
    return np.frombuffer(raw, '<f4').reshape(-1, 2), graph


@pytest.mark.parametrize('transform,opacity,transparent', [
    (ClipTransform(scale=.5), 1, True),
    (ClipTransform(scale=.72, x=9, y=-5, rotation=27, mirror_x=True), .5, True),
    (ClipTransform(scale=.75, rotation=-90, mirror_y=True), .5, True),
    (ClipTransform(scale=.6, x=35, y=14), 1, True),
    (ClipTransform(scale=.5, rotation=31), 0, True),
    (ClipTransform(scale=.5), .5, False),
], ids=['shrink-opaque-black-white', 'rotate-mirror-half', 'quarter-turn-half',
        'partly-off-canvas', 'zero-opacity', 'opaque-outside-half'])
def test_real_two_source_preview_h264_and_prores_match_independent_source_over(
        tmp_path, sources, engine, transform, opacity, transparent):
    p = project(sources, transform=transform, opacity=opacity, transparent=transparent)
    before = deepcopy(p.to_dict())
    lower, upper = sources[1]
    expected = np.array([source_over(lower[n], upper[n], p.clips[1]) for n in range(3)])
    for actual in deliveries(engine, p, tmp_path, 'composited'):
        assert_picture(actual, expected)
    if opacity == 0:
        clean = deepcopy(p)
        clean.clips = [clean.clips[0]]
        for profile in ('h264_8bit', PROFILE):
            invisible, _ = compiled_video(engine, p, profile)
            background, _ = compiled_video(engine, clean, profile)
            np.testing.assert_array_equal(invisible, background)
    assert p.to_dict() == before


def timeline_project(sources):
    p = project(sources, frames=20, transform=ClipTransform(scale=.75, x=6, y=-3))
    foreground = p.clips[1]
    foreground.start, foreground.duration = 3, 7
    foreground.time_map = ClipTimeMap(Fraction(5, 4), Fraction(47, 4), Fraction(3, 2))
    foreground.source_in = Fraction(5, 4)
    tail = deepcopy(foreground)
    tail.id, tail.start, tail.duration = 'tail', 14, 3
    tail.time_map = ClipTimeMap(Fraction(31, 2), Fraction(17), Fraction(1, 2))
    tail.source_in = Fraction(31, 2)
    p.clips.append(tail)
    p.validate()
    return p


def timeline_reference(p, sources):
    lower, upper = sources[1]
    expected = lower[:p.duration_frames].copy()
    for clip in p.clips[1:]:
        for local in range(clip.duration):
            position = clip.timing.source_in + local * clip.timing.speed
            source_index = position.numerator // position.denominator
            frame = clip.start + local
            expected[frame] = source_over(expected[frame], upper[source_index], clip)
    return expected


def test_fractional_speed_cuts_gaps_and_end_frame_are_visible_in_all_deliveries(
        tmp_path, sources, engine):
    p = timeline_project(sources)
    expected = timeline_reference(p, sources)
    for actual in deliveries(engine, p, tmp_path, 'fractional-timeline'):
        assert len(actual) == 20
        assert_picture(actual, expected, tolerance=6)
        # Large neutral ID patch distinguishes every adjacent source frame;
        # no tolerance permits a duplicated/omitted retimed sample.
        for frame, source_index in zip(range(3, 10), [1, 2, 4, 5, 7, 8, 10]):
            level = np.median(actual[frame, 37:45, 27:38])
            assert abs(level - (16 + 9 * source_index)) < 4
        for frame in (0, 2, 10, 13, 17, 19):
            np.testing.assert_allclose(actual[frame, 8:20, 8:25],
                                       sources[1][0][frame, 8:20, 8:25], atol=6, rtol=0)


def test_fractional_split_and_trim_preserve_phase_geometry_and_exact_graph_pixels(
        sources, engine):
    p = timeline_project(sources)
    before, _ = compiled_video(engine, p)
    editor = Editor(deepcopy(p))
    left, right = editor.split_clip('foreground', 5)
    assert left.source_out == right.source_in == Fraction(17, 4)
    assert left.composite.to_dict() == right.composite.to_dict()
    assert left.transform.to_dict() == right.transform.to_dict()
    after, _ = compiled_video(engine, editor.project)
    np.testing.assert_array_equal(after, before)
    # Left trim removes two output frames without resetting fractional phase.
    editor = Editor(deepcopy(p))
    editor.trim_clip_left('foreground', 2)
    trimmed, _ = compiled_video(engine, editor.project)
    np.testing.assert_array_equal(trimmed[5:], before[5:])
    clean = deepcopy(p)
    clean.clips = [clean.clips[0]]
    background, _ = compiled_video(engine, clean)
    np.testing.assert_array_equal(trimmed[3:5], background[3:5])
    assert len(trimmed) == 20


@pytest.mark.parametrize('blur', [False, True], ids=['no-effects', 'scalar-safe-blur'])
def test_opacity_cannot_change_source_pcm_fades_graph_or_fractional_sample_budget(
        tmp_path, sources, engine, blur):
    p = timeline_project(sources)
    p.clips[1].muted = False
    p.clips[1].fade_in = p.clips[1].fade_out = 2
    p.clips[1].gain_db = -3
    p.clips[2].muted = False
    if blur:
        p.clips[1].effects = [ClipEffect('blur', 'blur', 2)]
    hashes = [hashlib.sha256(Path(m.path).read_bytes()).hexdigest() for m in p.media]
    visible, visible_graph = compiled_pcm(engine, p, tmp_path, 'visible-pcm')
    for clip in p.clips[1:]:
        clip.composite.opacity = 0
    hidden, hidden_graph = compiled_pcm(engine, p, tmp_path, 'invisible-pcm')
    assert len(hidden) == len(visible) == independent_samples(20)
    np.testing.assert_array_equal(hidden, visible)
    legacy = deepcopy(p)
    for clip in legacy.clips[1:]:
        clip.composite = ClipComposite()
    legacy_pcm, legacy_graph = compiled_pcm(engine, legacy, tmp_path, 'legacy-pcm')
    np.testing.assert_array_equal(legacy_pcm, visible)
    audio = lambda graph: [part for part in graph.split(';')
                           if ':a:0]' in part or '[silence]' in part or part.endswith('[aout]')]
    assert audio(hidden_graph) == audio(visible_graph) == audio(legacy_graph)
    assert 'rubberband=' in hidden_graph and 'afade=t=in:ss=0:ns=' in hidden_graph
    assert 'afade=t=out:ss=' in hidden_graph
    assert not hidden[:independent_samples(3)].any()
    assert not hidden[independent_samples(10):independent_samples(14)].any()
    assert not hidden[independent_samples(17):].any()
    assert np.max(np.abs(hidden[independent_samples(4):independent_samples(8)])) > .02
    assert hashes == [hashlib.sha256(Path(m.path).read_bytes()).hexdigest() for m in p.media]


@pytest.mark.parametrize('profile', ['h264_8bit', PROFILE], ids=['eight-bit', 'ten-bit'])
@pytest.mark.parametrize('chain', ['standard', 'shaped', 'vignette-only', 'mixed'])
def test_blur_vignette_effects_precede_geometry_and_do_not_touch_background(
        tmp_path, sources, engine, profile, chain):
    p = project(sources, frames=1, transform=ClipTransform(scale=.72, x=8, y=-5,
                                                         rotation=13, mirror_x=True), opacity=.5)
    shaped = chain == 'shaped'
    p.clips[1].effects = ([ClipEffect('blur', 'blur', 1.1)] if chain != 'vignette-only' else [])
    p.clips[1].effects.append(ClipEffect('vignette', 'vignette', .4,
                                       radius=.7 if shaped else 1,
                                       feather=1.3 if shaped else 1))
    if chain == 'mixed':
        p.clips[1].effects.append(ClipEffect('shaped', 'vignette', .2, radius=.8, feather=1.2))
    reference = deepcopy(p)
    reference.clips = [deepcopy(p.clips[1])]
    reference.clips[0].track = 0
    reference.clips[0].transform = ClipTransform()
    reference.clips[0].composite = ClipComposite()
    suffix = '.mov' if profile == PROFILE else '.mp4'
    effected = decode(engine.render(reference, tmp_path / ('effect-reference' + suffix),
                                    export_profile=profile))[0].astype(float)
    expected = source_over(sources[1][0][0], effected, p.clips[1])[None]
    output = engine.render(p, tmp_path / ('effect-composite' + suffix), export_profile=profile)
    actual = decode(output)
    assert_picture(actual, expected, tolerance=10)
    _, covered = affine(effected, p.clips[1].transform)
    foreground_mask = flat_mask(expected[0]) & covered
    assert foreground_mask.sum() > 50, 'Effects must inspect actual foreground, not only background'
    assert np.quantile(np.abs(actual[0].astype(float) - expected[0])[foreground_mask], .995) <= 10
    pixels, graph = compiled_video(engine, p, profile)
    assert len(pixels) == 1
    # Semicolons occur inside geq expressions too, so inspect the complete
    # chain rather than naively splitting the filter graph on semicolons.
    if chain != 'vignette-only':
        assert graph.index('gblur=') < graph.index('format=gbrap16le')
    assert graph.index('vignette=' if profile == 'h264_8bit' and not shaped else 'geq=') < graph.index('format=gbrap16le')
    # Compiling without the upper clip must leave outside-coverage samples exact.
    clean = deepcopy(p)
    clean.clips = [clean.clips[0]]
    background, _ = compiled_video(engine, clean, profile)
    dtype = '<u2' if profile == PROFILE else 'u1'
    luma = pixels.view(dtype).reshape(1, -1)[:, :WIDTH * HEIGHT].reshape(1, HEIGHT, WIDTH)
    clean_luma = background.view(dtype).reshape(1, -1)[:, :WIDTH * HEIGHT].reshape(1, HEIGHT, WIDTH)
    np.testing.assert_array_equal(luma[:, :4, :16], clean_luma[:, :4, :16])


def test_prores_picture_then_sticker_then_caption_order_with_actual_decoded_output(
        tmp_path, sources, engine, latin_font):
    p = project(sources, frames=1)
    overlay, _ = sticker(tmp_path, color=(0, 0, 255, 255), duration=1,
                         keyframes=[OverlayKeyframe(0, 0, 16, 6, 1)])
    p.overlays = [overlay]
    p.captions = [Caption('topmost', 0, 1, 'HH')]
    p.caption_style = CaptionStyle(font_family=latin_font, font_size=28,
                                  margin_bottom=4, outline_width=0)
    raw, graph = compiled_video(engine, p)
    assert graph.index('[over1]') < graph.index('[stickered]') < graph.index('drawtext=')
    luma = raw.view('<u2')[:, :WIDTH * HEIGHT].reshape(-1, HEIGHT, WIDTH)
    # Caption white is visible above the opaque blue sticker, not erased by it.
    assert np.count_nonzero(luma[0] > 930) > 40
    output = engine.render(p, tmp_path / 'pip-art-caption.mov', export_profile=PROFILE)
    actual = decode(output)
    assert actual.shape[0] == 1
    np.testing.assert_allclose(actual[0, 20:28, 4:12],
                               np.broadcast_to((0, 0, 255), (8, 8, 3)), atol=5, rtol=0)
    assert np.count_nonzero(np.all(actual[0] > 245, axis=-1)) > 40
    np.testing.assert_allclose(actual[0, 3:9, 5:18], sources[1][0][0, 3:9, 5:18], atol=5, rtol=0)


def test_composite_and_algorithm_invalidate_final_preview_but_reuse_rgb16_grade_cache(
        sources, engine, monkeypatch):
    import lumen.clip_composite as composite
    p = project(sources, frames=1)
    segment = engine._graded_segment(p.media[1], p.clips[1], p, None)
    stat = segment.stat()
    preview = engine.preview(p)
    assert engine.preview(p) == preview
    p.clips[1].composite.opacity = .5
    changed = engine.preview(p)
    assert changed != preview and changed.exists()
    assert not np.array_equal(decode(changed), decode(preview))
    assert engine._graded_segment(p.media[1], p.clips[1], p, None) == segment
    assert segment.stat().st_mtime_ns == stat.st_mtime_ns
    p.clips[1].composite.transparent_outside_transform = False
    opaque_outside = engine.preview(p)
    assert opaque_outside not in (preview, changed)
    assert not np.array_equal(decode(opaque_outside), decode(changed))
    monkeypatch.setattr(composite, 'COMPOSITE_ALGORITHM', composite.COMPOSITE_ALGORITHM + '-test')
    versioned = engine.preview(p)
    assert versioned not in (preview, changed, opaque_outside) and versioned.exists()
    np.testing.assert_array_equal(decode(versioned), decode(opaque_outside))
    assert engine._graded_segment(p.media[1], p.clips[1], p, None) == segment
    assert segment.stat().st_mtime_ns == stat.st_mtime_ns


@pytest.mark.parametrize('profile', ['h264_8bit', PROFILE], ids=['eight-bit', 'ten-bit'])
def test_unsupported_compositor_fails_explicitly_without_export_or_cache_fallback(
        tmp_path, sources, engine, monkeypatch, profile):
    import lumen.clip_composite as composite
    calls = []
    def unavailable(high_bit, timeout, cancel, error_type, cancelled_type, **kwargs):
        calls.append(high_bit)
        raise error_type('This FFmpeg build cannot preserve clip compositing precision.')
    monkeypatch.setattr(composite, 'verify_clip_composite_support', unavailable)
    p = project(sources, frames=1)
    output = tmp_path / ('unsupported.mov' if profile == PROFILE else 'unsupported.mp4')
    with pytest.raises(RenderError, match='cannot preserve clip compositing precision'):
        engine.render(p, output, export_profile=profile)
    assert calls == [profile == PROFILE]
    assert not output.exists()
    assert not list(tmp_path.glob('.lumen-render-*'))
    assert not list(Path(engine._cache_dir.name).iterdir())
    # The legacy opaque/default branch does not pretend to require new support.
    p.clips[1].composite = ClipComposite()
    engine.render(p, output, export_profile=profile)
    assert calls == [profile == PROFILE]


def test_cancellation_during_real_preparation_keeps_completed_cache_and_no_partial_output(
        tmp_path, sources, engine):
    p = project(sources, frames=3)
    segment = engine._graded_segment(p.media[0], p.clips[0], p, None)
    original = segment.read_bytes()
    cancelled = threading.Event()
    def progress(value):
        if value > 0:
            cancelled.set()
    output = tmp_path / 'cancelled.mp4'
    with pytest.raises(RenderCancelled):
        engine.render(p, output, progress=progress, cancel=cancelled)
    assert cancelled.is_set() and not output.exists()
    assert segment.read_bytes() == original
    assert not list(tmp_path.glob('.lumen-render-*'))
    assert not list(Path(engine._cache_dir.name).glob('*.partial*'))
    cancelled.clear()
    assert engine.render(p, output, cancel=cancelled).exists()
    assert not list(tmp_path.glob('.lumen-render-*'))


@pytest.fixture
def narrow_endpoint_evidence(tmp_path, monkeypatch):
    from media_endpoint_evidence import trace_synthetic_probes
    folder = Path(os.environ.get('AUTO_FRAME_REVIEW_EVIDENCE', str(tmp_path)))
    sources = {name: tmp_path / (name + '.mkv')
               for name in ('narrow-background', 'narrow-foreground')}
    with trace_synthetic_probes(monkeypatch, sources, folder / 'narrow-endpoint-probes.json'):
        yield


def test_narrow_strong_blur_is_safe_under_native_cpu_dispatch_and_actual_h264(
        tmp_path, engine, narrow_endpoint_evidence):
    """Regression: native Gaussian SIMD once turned constant gray100 to white.

    A graph-format assertion is insufficient: corruption reproduces in both
    RGB8 and RGB16 on affected FFmpeg builds. The production compiler injects
    scalar dispatch into both raw commands: this is production-scalar consistency,
    not a native SIMD comparison. Demand constant-picture preservation in preview
    and export; retain the historical node ID for existing failure receipts.
    """
    width, height = 2, 48
    media = [rgb_source(tmp_path, np.full((height, width, 3), value * 257, dtype='<u2'),
                        name=name, frames=2, fps=FPS)
             for value, name in ((40, 'narrow-background'), (100, 'narrow-foreground'))]
    p = Project(width=width, height=height, fps_num=FPS.numerator, fps_den=FPS.denominator,
                media=media, clips=[Clip('background', media[0].id, 0, 0, 0, 2),
                                   Clip('foreground', media[1].id, 1, 0, 0, 2,
                                        effects=[ClipEffect('strong-blur', 'blur', 30)],
                                        composite=ClipComposite(True))])
    inputs, graph, _ = engine._compile(p)
    def render_raw(extra):
        return ff(*extra, '-filter_complex_threads', '1', *inputs, '-filter_complex', graph,
                  '-map', '[vout]', '-frames:v', 2, '-c:v', 'rawvideo', '-threads', '1',
                  '-pix_fmt', 'yuv420p', '-f', 'rawvideo', 'pipe:1',
                  '-map', '[aout]', '-f', 'null', os.devnull)
    native = np.frombuffer(render_raw([]), np.uint8)
    scalar = np.frombuffer(render_raw(['-cpuflags', '0']), np.uint8)
    np.testing.assert_allclose(native.astype(int), scalar.astype(int), atol=1, rtol=0)
    frames = native.reshape(2, width * height * 3 // 2)
    np.testing.assert_allclose(frames[:, :width * height], 16 + 219 * 100 / 255,
                               atol=2, rtol=0)
    for output in (engine.preview(p), engine.render(p, tmp_path / 'narrow-blur.mp4')):
        np.testing.assert_allclose(decode(output, width=width, height=height),
                                   100, atol=3, rtol=0)


def test_fitted_portrait_bars_stay_opaque_inside_transformed_canvas(
        tmp_path, sources, engine):
    portrait = rgb_source(tmp_path, np.broadcast_to(np.array([65535, 0, 0], dtype='<u2'),
                                                    (HEIGHT, 24, 3)),
                          name='portrait', frames=1, fps=FPS)
    p = project(sources, frames=1)
    p.media.append(portrait)
    p.clips[1].media_id = portrait.id
    for actual in deliveries(engine, p, tmp_path, 'portrait-pip'):
        # Source fit/pad bars belong to the picture. Coverage is geometry,
        # never a brightness/chroma-key heuristic that reveals the underlay.
        assert actual[0, 23:40, 29:36].max() <= 5
        assert actual[0, 23:40, 60:67].max() <= 5
        np.testing.assert_allclose(actual[0, 23:40, 45:51],
                                   np.broadcast_to((255, 0, 0), (17, 6, 3)), atol=6, rtol=0)
        np.testing.assert_allclose(actual[0, 3:10, 5:20],
                                   sources[1][0][0, 3:10, 5:20], atol=6, rtol=0)


def test_unsupported_rgb8_blur_gate_cannot_silently_render_corrupted_video(
        tmp_path, sources, engine, monkeypatch):
    import lumen.clip_composite as composite
    calls = []
    def unsupported(timeout, cancel, error_type, cancelled_type):
        calls.append(True)
        raise error_type('This FFmpeg build cannot safely apply clip RGB8 blur.')
    monkeypatch.setattr(composite, 'ensure_composite_rgb8_blur_support', unsupported)
    p = project(sources, frames=1)
    p.clips[1].effects = [ClipEffect('blur', 'blur', 2)]
    output = tmp_path / 'unsupported-blur.mp4'
    with pytest.raises(RenderError, match='cannot safely apply clip RGB8 blur'):
        engine.render(p, output)
    assert calls == [True] and not output.exists()
    assert not list(tmp_path.glob('.lumen-render-*'))
    assert not list(Path(engine._cache_dir.name).iterdir())


def test_cancel_after_actual_final_ffmpeg_launch_reaps_process_and_removes_partial(
        tmp_path, sources, engine, monkeypatch):
    import lumen.render as render_module
    p = project(sources, frames=3)
    cancel = threading.Event()
    original_popen = render_module.subprocess.Popen
    started = []
    def launch(command, *args, **kwargs):
        process = original_popen(command, *args, **kwargs)
        if '-progress' in command:
            started.append(process)
            cancel.set()
        return process
    monkeypatch.setattr(render_module.subprocess, 'Popen', launch)
    output = tmp_path / 'cancel-after-launch.mp4'
    with pytest.raises(RenderCancelled):
        engine.render(p, output, cancel=cancel)
    assert len(started) == 1 and started[0].poll() is not None
    assert not output.exists()
    assert not list(tmp_path.glob('.lumen-render-*'))
    assert not list(Path(engine._cache_dir.name).glob('*.partial*'))

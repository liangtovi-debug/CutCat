"""Independent RGB16 and decoded ProRes oracles for native Gaussian blur.

The expected response is a three-fold convolution of the normalized discrete
exponential kernel, not production effect, color, or capability-probe math.
Precision controls deliberately destroy low bits: output tags alone cannot
pass these tests. All sources are synthetic; no user media/network is used.
"""
from copy import deepcopy
from pathlib import Path
import os
import struct
import threading

import numpy as np
import pytest

from lumen import highbit_effects as high
from lumen.clip_transform import ClipTransform
from lumen.color_output import validate_export_profile
from lumen.media import ffmpeg_available
from lumen.model import Caption, CaptionStyle, Clip, ClipEffect, Grade, Media, OverlayKeyframe, Project
from lumen.overlay_render import OverlayRenderError, effect_filters
from lumen.qualifier import QualifierSettings
from lumen.render import RenderCancelled, RenderEngine, RenderError
from test_color_highbit_reference import ff, project, rgb_source, video_metadata, yuv422
from test_highbit_caption_reference import latin_font, split422
from test_overlay_render import sticker


PROFILE = 'prores_422_hq_10bit'
needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


@pytest.fixture
def engine():
    renderer = RenderEngine(timeout=60)
    try:
        yield renderer
    finally:
        renderer.close()


def effects(*amounts):
    return [ClipEffect(f'blur-{i}', 'blur', amount) for i, amount in enumerate(amounts)]


def scene(*amounts):
    return Project(width=64, height=64,
                   media=[Media(id='media', path='/synthetic/unused.mkv', name='synthetic', duration=1, width=64, height=64, fps_num=30, fps_den=1, has_video=True, has_audio=False)],
                   clips=[Clip('picture', 'media', 0, 0, 0, 1, effects=effects(*amounts))])


def ensure(p, *, timeout=30, cancel=None):
    return high.ensure_highbit_effects(p, timeout, cancel, RenderError, RenderCancelled)


def filtered_rgb(image, filters, *, native=True):
    """Run the public effect graph on arbitrary native RGB16 samples."""
    image = np.asarray(image, dtype='<u2')
    height, width, _ = image.shape
    planar = np.moveaxis(image[..., [1, 2, 0]], -1, 0).astype('<u2')
    raw = ff(*(['-cpuflags', '0'] if any('gblur=' in f for f in filters) else []),
             *(['-noauto_conversion_filters'] if native else []),
             '-filter_threads', '1', '-f', 'rawvideo', '-pix_fmt', 'gbrp16le',
             '-s', f'{width}x{height}', '-i', 'pipe:0',
             '-vf', ','.join(filters) or 'null', '-frames:v', 1,
             '-c:v', 'rawvideo', '-threads', 1, '-pix_fmt', 'gbrp16le',
             '-f', 'rawvideo', 'pipe:1', data=planar.tobytes())
    planes = np.frombuffer(raw, '<u2').reshape(3, height, width)
    return np.moveaxis(planes[[2, 0, 1]], 0, -1)


def kernel(sigma, radius=256):
    """Independent normalized impulse response for three diffusion steps.

    Each causal/anticausal pair has response (1-nu)/(1+nu)*nu**abs(x).
    The three-step operator convolves that distribution three times. The
    large support here truncates less than 1e-9 even at sigma=30.
    """
    lam = sigma * sigma / 6
    nu = 2 * lam / (1 + 2 * lam + np.sqrt(1 + 4 * lam))
    step = (1 - nu) / (1 + nu) * nu ** np.abs(np.arange(-radius, radius + 1))
    return np.convolve(np.convolve(step, step), step)


def independent_blur(image, sigma):
    """Scalar separable convolution; compare away from recursive borders."""
    image = np.asarray(image, dtype=float)
    full = kernel(sigma, radius=max(32, int(sigma * 12)))
    radius = min((len(full) - 1) // 2, max(32, int(sigma * 24)))
    center = len(full) // 2
    weights = full[center - radius:center + radius + 1]
    result = image.copy()
    for axis in (1, 0):
        padding = [(0, 0)] * image.ndim
        padding[axis] = (radius, radius)
        padded = np.pad(result, padding, mode='edge')
        result = np.apply_along_axis(lambda row: np.convolve(row, weights, mode='valid'), axis, padded)
    return result


def compiled_pixels(engine, p):
    inputs, graph, _ = engine._compile(p, export_profile=PROFILE)
    raw = ff('-filter_complex_threads', 1, *inputs, '-filter_complex', graph,
             '-map', '[vout]', '-frames:v', p.duration_frames,
             '-r', f'{p.fps_num}/{p.fps_den}', '-fps_mode', 'cfr',
             '-c:v', 'rawvideo', '-threads', 1, '-pix_fmt', 'yuv422p10le',
             '-f', 'rawvideo', 'pipe:1', '-map', '[aout]', '-f', 'null', os.devnull)
    return split422(raw, p.width, p.height)


def test_verified_effects_are_enabled_and_legacy_filter_numbers_are_unchanged():
    p = scene(.125, 30)
    before = deepcopy(p.to_dict())
    assert validate_export_profile(p, PROFILE).high_bit
    assert high.highbit_effect_filters(p.clips[0].effects) == [
        'format=gbrp16le', 'gblur=sigma=0.125:steps=3',
        'gblur=sigma=30:steps=3', 'format=gbrp16le']
    assert effect_filters(p.clips[0].effects) == [
        'gblur=sigma=0.125:steps=3', 'gblur=sigma=30:steps=3']
    p.clips[0].effects.append(ClipEffect('v', 'vignette', .5))
    assert validate_export_profile(p, PROFILE).high_bit
    assert any(f.startswith('geq=') for f in high.highbit_effect_filters(p.clips[0].effects))
    assert not validate_export_profile(p).high_bit
    p.clips[0].effects.pop()
    assert p.to_dict() == before


def test_scalar_cpu_dispatch_is_required_only_for_effective_video_blur():
    assert high.highbit_effect_input_options(scene(2)) == ['-cpuflags', '0']
    for p in (scene(), scene(0)):
        assert high.highbit_effect_input_options(p) == []
    p = scene(30)
    p.clips[0].effects[0].enabled = False
    assert high.highbit_effect_input_options(p) == []
    p.clips[0].effects[0].enabled = True
    p.clips[0].track = 2
    assert high.highbit_effect_input_options(p) == []


@pytest.mark.parametrize('kind,amount', [('blur', -1), ('blur', 31), ('blur', True),
    ('blur', float('nan')), ('blur', float('inf')), ('vignette', 2), ('unknown', 0)])
def test_malformed_values_are_rejected_even_when_disabled(kind, amount):
    with pytest.raises((ValueError, OverlayRenderError), match='Invalid clip effect'):
        high.highbit_effect_filters([ClipEffect('bad', kind, amount, enabled=False)])


@needs_ffmpeg
@pytest.mark.parametrize('amount', [.25, 2.5, 8, 30])
def test_rgb16_impulse_matches_independent_discrete_gaussian(amount):
    width = height = 384
    image = np.full((height, width, 3), (10001, 20002, 30003), dtype='<u2')
    image[height // 2, width // 2] = (50001, 50002, 50003)
    actual = filtered_rgb(image, high.highbit_effect_filters(effects(amount)))
    response = kernel(amount)
    middle = len(response) // 2
    line = response[middle - width // 2:middle + width // 2]
    expected = np.array([10001., 20002., 30003.]) + np.outer(line, line)[..., None] * [40000, 30000, 20000]
    np.testing.assert_allclose(actual, expected, atol=1.1, rtol=0)
    assert actual[height // 2, width // 2, 0] < image[height // 2, width // 2, 0]
    assert actual[height // 2, width // 2 - 1, 0] > 10001
    # One-code source distinctions survive even at the maximum supported blur.
    np.testing.assert_array_equal(actual[0, :16], image[0, :16])


@needs_ffmpeg
@pytest.mark.parametrize('width,height', [(2, 2), (2, 48), (4, 96), (64, 2), (1024, 2)])
@pytest.mark.parametrize('amount', [1e-12, .0001, .1, 2, 30])
def test_tiny_narrow_and_wide_constant_planes_are_exact(width, height, amount):
    image = np.full((height, width, 3), (0, 10001, 65535), dtype='<u2')
    actual = filtered_rgb(image, high.highbit_effect_filters(effects(amount)))
    np.testing.assert_array_equal(actual, image)


@needs_ffmpeg
def test_native_blur_preserves_ramp_low_bits_and_sub_eight_bit_plateaus():
    width, height = 2048, 96
    image = np.broadcast_to(np.arange(width, dtype=np.uint16)[None, :, None] + 10001,
                            (height, width, 3)).copy()
    image[48:, :width // 2] = 32778
    image[48:, width // 2:] = 32958
    chain = high.highbit_effect_filters(effects(2))
    actual = filtered_rgb(image, chain)
    np.testing.assert_allclose(actual[16:32, 32:-32], image[16:32, 32:-32], atol=1, rtol=0)
    assert np.unique(actual[24, 32:-32, 0]).size > 1900
    assert actual[72, 1600, 0] - actual[72, 400, 0] == 180
    coarse = (np.rint(image.astype(float) / 257) * 257).astype('<u2')
    control = filtered_rgb(coarse, chain)
    assert control[72, 1600, 0] == control[72, 400, 0]
    assert np.mean(abs(control[24, 32:-32, 0].astype(float) - image[24, 32:-32, 0])) > 30


@needs_ffmpeg
@pytest.mark.parametrize('mode', ['zero', 'disabled', 'disabled-vignette'])
def test_zero_and_disabled_effects_are_bit_exact_for_every_rgb16_code(mode):
    samples = np.arange(65536, dtype='<u2').reshape(256, 256)
    image = np.stack([samples, np.flipud(samples), np.fliplr(samples)], -1)
    effect = {'zero': ClipEffect('zero', 'blur', 0),
              'disabled': ClipEffect('off', 'blur', 30, False),
              'disabled-vignette': ClipEffect('off', 'vignette', 1, False)}[mode]
    chain = high.highbit_effect_filters([effect])
    assert chain == []
    np.testing.assert_array_equal(filtered_rgb(image, chain), image)


@needs_ffmpeg
def test_two_blurs_keep_order_and_apply_both_real_operators():
    rng = np.random.default_rng(35019)
    image = rng.integers(7000, 53000, (96, 128, 3), dtype=np.uint16)
    chain = high.highbit_effect_filters(effects(.75, 3.25))
    assert chain.index('gblur=sigma=0.75:steps=3') < chain.index('gblur=sigma=3.25:steps=3')
    actual = filtered_rgb(image, chain)
    # Round at each integer RGB16 effect boundary, not only after both blurs.
    expected = np.rint(independent_blur(np.rint(independent_blur(image, .75)), 3.25))
    np.testing.assert_allclose(actual[24:-24, 24:-24], expected[24:-24, 24:-24], atol=2, rtol=0)
    once = filtered_rgb(image, high.highbit_effect_filters(effects(3.25)))
    assert np.max(abs(once[24:-24, 24:-24].astype(float) - actual[24:-24, 24:-24])) > 30


@needs_ffmpeg
def test_real_prores_blur_preserves_source_precision_and_native_decoded_planes(tmp_path, engine):
    width, height = 2048, 96
    image = np.broadcast_to(np.rint(np.linspace(0, 65535, width))[None, :, None],
                            (height, width, 3)).astype('<u2').copy()
    image[48:, :width // 2] = 32778
    image[48:, width // 2:] = 32958
    p = project(rgb_source(tmp_path, image))
    p.clips[0].effects = effects(2)
    before = deepcopy(p.to_dict())
    output = engine.render(p, tmp_path / 'blur.mov', export_profile=PROFILE)
    metadata = video_metadata(output)
    assert (metadata['codec_name'], metadata['profile'], metadata['pix_fmt']) == ('prores', 'HQ', 'yuv422p10le')
    assert metadata['color_space'] == metadata['color_transfer'] == metadata['color_primaries'] == 'bt709'
    y, u, v = yuv422(output, width, height)
    reference = 64 + 876 * image[24, :, 0].astype(float) / 65535
    ramp_error = np.mean(abs(y[0, 16:32, 32:-32].mean(0) - reference[32:-32]))
    assert ramp_error < .65 and np.unique(y[0, 24]).size > 700
    assert y[0, 64:80, 1152:1920].mean() - y[0, 64:80, 128:896].mean() >= 1.75
    np.testing.assert_allclose(u, 512, atol=1, rtol=0)
    np.testing.assert_allclose(v, 512, atol=1, rtol=0)
    coarse = (np.rint(image.astype(float) / 257) * 257).astype('<u2')
    q = project(rgb_source(tmp_path, coarse, name='coarse'))
    q.clips[0].effects = effects(2)
    control = engine.render(q, tmp_path / 'coarse.mov', export_profile=PROFILE)
    coarse_y, _, _ = yuv422(control, width, height)
    assert abs(coarse_y[0, 64:80, 1152:1920].mean() - coarse_y[0, 64:80, 128:896].mean()) <= .5
    assert p.to_dict() == before


def wb_reference(image, gains):
    encoded = np.asarray(image, dtype=float) / 65535
    linear = np.where(encoded < .081, encoded / 4.5, ((encoded + .099) / 1.099) ** (1 / .45))
    linear *= gains
    return np.where(linear < .018, linear * 4.5, 1.099 * linear ** .45 - .099)


def matte_reference(rgb):
    """Independent luma key, half-strength grow, full binomial smoothing."""
    y = rgb @ [.2126, .7152, .0722]
    distance = np.maximum(np.maximum(.23 - y, y - .43), 0)
    mask = np.maximum(0, 1 - distance / .04)
    padded = np.pad(mask, 1, mode='edge')
    neighbors = [padded[dy:dy + len(mask), dx:dx + mask.shape[1]]
                 for dy in range(3) for dx in range(3)]
    grown = mask + .5 * (np.maximum.reduce(neighbors) - mask)
    padded = np.pad(grown, 1, mode='edge')
    smooth = sum(padded[dy:dy + len(mask), dx:dx + mask.shape[1]] * wy * wx / 16
                 for dy, wy in enumerate((1, 2, 1)) for dx, wx in enumerate((1, 2, 1)))
    adjusted = (y[..., None] + .9 * (rgb - y[..., None])) * 2 ** .7
    return rgb + smooth[..., None] * (adjusted - rgb)


@needs_ffmpeg
def test_wb_matte_refinement_then_blur_before_geometry_matches_independent_pixels(tmp_path, engine):
    width, height = 192, 128
    row, col = np.indices((height, width))
    values = np.where((col // 16 + row // 16) % 2, 14003, 33017)
    image = np.stack([values, values + 1001, values - 997], -1).astype('<u2')
    gains = [1.2, .82, 1.07]
    q = QualifierSettings(enabled=True, hue_enabled=False, saturation_enabled=False,
        luma_low=.23, luma_high=.43, luma_softness=.04, exposure=.7,
        saturation=.9, matte_morphology=.5, matte_smoothing=1)
    grade = Grade(white_balance_enabled=True, white_balance_gains=gains, qualifier=q)
    p = project(rgb_source(tmp_path, image), grade=grade)
    p.clips[0].effects = effects(2)
    # Integer translation exposes ordering: blur must not contaminate the new
    # black geometry border, as would geometry-before-blur.
    p.clips[0].transform = ClipTransform(x=12)
    before = deepcopy(p.to_dict())
    actual = compiled_pixels(engine, p)
    graded = np.rint(np.clip(matte_reference(wb_reference(image, gains)), 0, 1) * 65535)
    expected = np.rint(independent_blur(graded, 2))
    desired_y = 64 + 876 * (expected / 65535 @ [.2126, .7152, .0722])
    np.testing.assert_allclose(actual[0][0, 24:-24, 36:-24], desired_y[24:-24, 24:-36], atol=1.5, rtol=0)
    np.testing.assert_array_equal(actual[0][0, :, :12], 64)
    # This nonlinear fixture must actually distinguish blur-before-WB/matte.
    wrong = matte_reference(wb_reference(np.rint(independent_blur(image, 2)), gains))
    wrong_y = 64 + 876 * (wrong @ [.2126, .7152, .0722])
    assert np.mean(abs(wrong_y[24:-24, 24:-36] - desired_y[24:-24, 24:-36])) > 10
    assert p.to_dict() == before


@needs_ffmpeg
def test_stickers_and_captions_stay_sharp_after_blurred_picture(tmp_path, engine, latin_font):
    width, height = 512, 160
    image = np.full((height, width, 3), 18001, dtype='<u2')
    image[:80, ::2] = 38009
    p = project(rgb_source(tmp_path, image), frames=3)
    p.clips[0].effects = effects(6)
    baseline = compiled_pixels(engine, p)
    art, _ = sticker(tmp_path, duration=1, start=1, color=(255, 0, 0, 255),
                     keyframes=[OverlayKeyframe(0, 32, 24, 2, 1)])
    p.overlays = [art]
    p.caption_style = CaptionStyle(font_family=latin_font, font_size=32, margin_bottom=12,
                                   outline_width=0, safe_margin_percent=5)
    p.captions = [Caption('caption', 1, 1, 'SHARP')]
    before = deepcopy(p.to_dict())
    actual = compiled_pixels(engine, p)
    for plain, decorated in zip(baseline, actual):
        np.testing.assert_array_equal(decorated[[0, 2]], plain[[0, 2]])
        np.testing.assert_array_equal(decorated[1, :16], plain[1, :16])
    np.testing.assert_allclose(actual[0][1, 24:56, 32:64], 64 + 876 * .2126, atol=3, rtol=0)
    np.testing.assert_array_equal(actual[0][1, 24:56, 31], baseline[0][1, 24:56, 31])
    np.testing.assert_array_equal(actual[0][1, 24:56, 64], baseline[0][1, 24:56, 64])
    assert np.count_nonzero(actual[0][1, 96:] != baseline[0][1, 96:]) > 150
    # Compare glyph coverage with the identical constant, unblurred picture;
    # the picture-effect branch may not soften either later art layer.
    unblurred = deepcopy(p)
    unblurred.clips[0].effects = []
    untouched = compiled_pixels(engine, unblurred)
    for decorated, sharp in zip(actual, untouched):
        np.testing.assert_array_equal(decorated[:, 104:], sharp[:, 104:])
    assert p.to_dict() == before


class ProbeProcess:
    def __init__(self, command, *, stdin, stdout, stderr, code=0, running=False,
                 output=None, log_size=0):
        self.command, self.streams = command, (stdin, stdout, stderr)
        self.returncode, self.running = code, running
        self.killed = self.waited = False
        source = stdin.read()
        size = command[command.index('-s') + 1]
        assert len(source) == (576 if size == '2x48' else 24576)
        self.source = source
        # A constant narrow image is independently an exact identity.
        if size == '2x48' and output == valid_probe_bytes():
            output = source
        stdout.write(source if output is None else output)
        stdout.flush()
        stderr.write(b'x' * log_size)
        stderr.flush()

    def poll(self):
        return None if self.running else self.returncode

    def kill(self):
        self.killed, self.running = True, False

    def wait(self):
        self.waited = True
        return self.returncode


def valid_probe_bytes():
    """Predict the production fixture with independent convolution."""
    image = np.full((64, 64, 3), 10001., dtype=float)
    image[:, 32:, 2] += 1
    image[32, 32, 0] += 40000
    result = np.rint(independent_blur(image, 2)).astype('<u2')
    return np.moveaxis(result[..., [1, 2, 0]], -1, 0).astype('<u2').tobytes()


def test_probe_accepts_independent_native_response_rechecks_and_closes_all_files(monkeypatch):
    processes = []
    payload = valid_probe_bytes()
    def start(command, **kwargs):
        processes.append(ProbeProcess(command, **kwargs, output=payload))
        return processes[-1]
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    p = scene(2)
    before = deepcopy(p.to_dict())
    ensure(p); ensure(p)
    assert len(processes) == 4
    assert [p.command[p.command.index('-s') + 1] for p in processes] == ['64x64', '2x48'] * 2
    for process in processes:
        assert process.waited and not process.killed
        assert all(stream.closed for stream in process.streams)
        assert '-noauto_conversion_filters' in process.command
        assert process.command.count('gbrp16le') == 2
        assert process.command[process.command.index('-cpuflags') + 1] == '0'
        assert process.command[process.command.index('-filter_threads') + 1] == '1'
        sigma = 30 if process.command[process.command.index('-s') + 1] == '2x48' else 2
        assert f'gblur=sigma={sigma}:steps=3,format=gbrp16le' in process.command
    assert p.to_dict() == before


@pytest.mark.parametrize('failure', ['returncode', 'empty', 'short', 'long', 'noop', 'eightbit', 'flat'])
def test_failed_or_false_precision_probe_is_rejected_and_reaped(monkeypatch, failure):
    payload = valid_probe_bytes()
    if failure == 'empty': payload = b''
    elif failure == 'short': payload = payload[:-2]
    elif failure == 'long': payload += b'\0\0'
    elif failure == 'noop': payload = None
    elif failure == 'eightbit':
        samples = np.frombuffer(payload, '<u2')
        payload = (np.rint(samples.astype(float) / 257) * 257).astype('<u2').tobytes()
    elif failure == 'flat': payload = struct.pack('<12288H', *([10001] * 12288))
    processes = []
    def start(command, **kwargs):
        processes.append(ProbeProcess(command, **kwargs, output=payload, code=int(failure == 'returncode')))
        return processes[-1]
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    with pytest.raises(RenderError, match='cannot apply Gaussian blur directly'):
        ensure(scene(2))
    assert processes[0].waited and not processes[0].killed
    assert all(stream.closed for stream in processes[0].streams)


@pytest.mark.parametrize('failure', ['cancel', 'timeout', 'output-budget', 'log-budget'])
def test_probe_interrupt_reaps_process_and_closes_all_files(monkeypatch, failure):
    cancel = threading.Event()
    processes = []
    def start(command, **kwargs):
        processes.append(ProbeProcess(command, **kwargs, running=True,
            output=b'x' * 65537 if failure == 'output-budget' else valid_probe_bytes(),
            log_size=65537 if failure == 'log-budget' else 0))
        if failure == 'cancel': cancel.set()
        return processes[-1]
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    with pytest.raises(RenderCancelled if failure == 'cancel' else RenderError):
        ensure(scene(2), cancel=cancel, timeout=0 if failure == 'timeout' else 30)
    assert processes[0].killed and processes[0].waited
    assert all(stream.closed for stream in processes[0].streams)


def test_missing_binary_closes_probe_files(monkeypatch):
    streams = []
    def missing(command, **kwargs):
        streams.extend(kwargs.values())
        raise FileNotFoundError('missing ffmpeg')
    monkeypatch.setattr(high.subprocess, 'Popen', missing)
    with pytest.raises(RenderError, match='compatible FFmpeg'):
        ensure(scene(2))
    assert len(streams) == 3 and all(stream.closed for stream in streams)


def test_bypass_audio_only_and_precancel_never_start_probe(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('No capability process should start')
    monkeypatch.setattr(high.subprocess, 'Popen', forbidden)
    ensure(scene())
    ensure(scene(0))
    p = scene(30); p.clips[0].effects[0].enabled = False
    ensure(p)
    p = scene(30); p.clips[0].track = 2
    ensure(p)
    cancel = threading.Event(); cancel.set()
    with pytest.raises(RenderCancelled):
        ensure(scene(2), cancel=cancel)


def test_cancel_during_fixture_preparation_prevents_process_start(monkeypatch):
    cancel = threading.Event()
    original = high._probe_frame
    def prepare():
        cancel.set()
        return original()
    def forbidden(*args, **kwargs):
        pytest.fail('Cancelled fixture may not start FFmpeg')
    monkeypatch.setattr(high, '_probe_frame', prepare)
    monkeypatch.setattr(high.subprocess, 'Popen', forbidden)
    with pytest.raises(RenderCancelled):
        ensure(scene(2), cancel=cancel)


@needs_ffmpeg
def test_real_capability_failure_precedes_grading_art_cache_and_publication(tmp_path, engine, monkeypatch, latin_font):
    p = project(rgb_source(tmp_path, np.full((64, 128, 3), 20001, dtype='<u2')))
    p.clips[0].effects = effects(2)
    p.overlays = [sticker(tmp_path, duration=1)[0]]
    p.captions = [Caption('c', 0, 1, 'TEXT')]
    p.caption_style.font_family = latin_font
    cache = Path(engine._cache_dir.name)
    before = set(cache.rglob('*'))
    calls = []
    def fail(*args, **kwargs):
        calls.append(True)
        raise RenderError(high.HIGHBIT_EFFECT_ERROR)
    def forbidden(*args, **kwargs):
        pytest.fail('Failed capability must precede cached picture and artwork generation')
    monkeypatch.setattr(high, 'ensure_highbit_effects', fail)
    monkeypatch.setattr(engine, '_ensure_highbit_encoder', lambda: None)
    monkeypatch.setattr(engine, '_graded_segment', forbidden)
    monkeypatch.setattr(engine, '_overlay_segment', forbidden)
    import lumen.highbit_captions as captions
    monkeypatch.setattr(captions, 'highbit_caption_filters', forbidden)
    with pytest.raises(RenderError, match='cannot apply Gaussian blur directly'):
        engine.render(p, tmp_path / 'failed.mov', export_profile=PROFILE)
    assert calls == [True]
    assert set(cache.rglob('*')) == before
    assert not (tmp_path / 'failed.mov').exists()
    assert not list(tmp_path.glob('.lumen-render-*'))


@needs_ffmpeg
def test_default_eight_bit_render_does_not_probe_highbit_effects(tmp_path, engine, monkeypatch):
    p = project(rgb_source(tmp_path, np.full((48, 64, 3), 20001, dtype='<u2')))
    p.clips[0].effects = [ClipEffect('b', 'blur', 2), ClipEffect('v', 'vignette', .2)]
    def forbidden(*args, **kwargs):
        pytest.fail('Legacy export must not require native high-bit effects')
    monkeypatch.setattr(high, 'ensure_highbit_effects', forbidden)
    monkeypatch.setattr(high, 'highbit_effect_filters', forbidden)
    _, graph, _ = engine._compile(p)
    assert 'gblur=sigma=2:steps=3,vignette=angle=' in graph
    assert 'overlay=format=yuv422p10' not in graph
    output = engine.render(p, tmp_path / 'legacy.mp4')
    assert video_metadata(output)['pix_fmt'] == 'yuv420p'


@pytest.mark.parametrize('failure', ['changed-lowbit', 'empty', 'eightbit', 'extra-byte'])
def test_narrow_regression_probe_is_mandatory_after_wide_probe_passes(monkeypatch, failure):
    processes = []
    def start(command, **kwargs):
        size = command[command.index('-s') + 1]
        if size == '64x64':
            payload = valid_probe_bytes()
        else:
            values = np.array([10001] * 96 + [10002] * 96 + [20003] * 96, dtype='<u2')
            if failure == 'changed-lowbit': values[0] += 1
            elif failure == 'eightbit': values = (np.rint(values.astype(float) / 257) * 257).astype('<u2')
            payload = values.tobytes()
            if failure == 'empty': payload = b''
            elif failure == 'extra-byte': payload += b'\0'
        processes.append(ProbeProcess(command, **kwargs, output=payload))
        return processes[-1]
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    with pytest.raises(RenderError, match='cannot apply Gaussian blur directly'):
        ensure(scene(30))
    assert len(processes) == 2
    assert all(process.waited and not process.killed for process in processes)
    assert all(stream.closed for process in processes for stream in process.streams)


@pytest.mark.parametrize('target', ['stdout', 'stderr'])
def test_finished_probe_cannot_evade_output_or_log_budget(monkeypatch, target):
    processes = []
    def start(command, **kwargs):
        processes.append(ProbeProcess(command, **kwargs, running=False,
            output=b'x' * 65537 if target == 'stdout' else valid_probe_bytes(),
            log_size=65537 if target == 'stderr' else 0))
        return processes[-1]
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    with pytest.raises(RenderError, match='output budget'):
        ensure(scene(2))
    assert processes[0].waited and not processes[0].killed
    assert all(stream.closed for stream in processes[0].streams)


@needs_ffmpeg
def test_blur_edits_reuse_rgb16_grade_cache_not_stale_effect_output(tmp_path, engine):
    row, col = np.indices((96, 192))
    samples = np.where((row // 4 + col // 4) % 2, 43017, 10003).astype('<u2')
    image = np.repeat(samples[..., None], 3, axis=2)
    p = project(rgb_source(tmp_path, image))
    original = compiled_pixels(engine, p)
    cache = Path(engine._cache_dir.name)
    files = sorted(cache.glob('*.mkv'))
    assert len(files) == 1
    baseline = [(path, path.stat().st_mtime_ns, path.read_bytes()) for path in files]
    p.clips[0].effects = effects(1)
    blurred = compiled_pixels(engine, p)
    p.clips[0].effects[0].amount = 5
    stronger = compiled_pixels(engine, p)
    assert np.mean(abs(blurred[0].astype(float) - original[0])) > 40
    assert np.mean(abs(stronger[0].astype(float) - blurred[0])) > 20
    p.clips[0].effects[0].amount = 0
    bypass = compiled_pixels(engine, p)
    for source, current in zip(original, bypass):
        np.testing.assert_array_equal(current, source)
    assert sorted(cache.glob('*.mkv')) == files
    for path, modified, contents in baseline:
        assert path.stat().st_mtime_ns == modified and path.read_bytes() == contents
    assert not list(cache.glob('*.partial*'))

"""Independent static source-over pixel proof, without model/UI or compiler."""
from dataclasses import asdict
from pathlib import Path
import subprocess
import tempfile

import numpy as np
import pytest

from lumen.clip_composite import ClipComposite, clip_composite_filters, validate_composite
from lumen.clip_transform import ClipTransform
from lumen.media import ffmpeg_available, ffmpeg_binary


def test_strict_default_roundtrip_and_legacy_bypass():
    value = ClipComposite()
    assert value.is_legacy()
    assert value.to_dict() == {'transparent_outside_transform': False, 'opacity': 1.0}
    assert ClipComposite.from_dict(value.to_dict()) == value
    for high in (False, True):
        assert clip_composite_filters(value, ClipTransform(scale=.1), 64, 48, high_bit=high) == ()
    assert not ClipComposite(True).is_legacy()
    assert not ClipComposite(opacity=.5).is_legacy()


@pytest.mark.parametrize('field,value', [
    ('transparent_outside_transform', 0), ('transparent_outside_transform', 1),
    ('transparent_outside_transform', None), ('transparent_outside_transform', 'true'),
    ('opacity', -0.01), ('opacity', 1.01), ('opacity', True), ('opacity', '0.5'),
    ('opacity', float('inf')), ('opacity', float('nan')), ('opacity', None)])
def test_strict_values_and_mutation_revalidation(field, value):
    with pytest.raises(ValueError):
        ClipComposite(**{field: value})
    settings = ClipComposite()
    setattr(settings, field, value)
    for action in (lambda: validate_composite(settings), settings.to_dict, settings.is_legacy,
                   lambda: clip_composite_filters(settings, ClipTransform(), 64, 48, high_bit=True)):
        with pytest.raises(ValueError):
            action()


@pytest.mark.parametrize('data', [None, [], {}, {'opacity': .5},
                                  {**asdict(ClipComposite()), 'extra': 0}])
def test_strict_schema(data):
    with pytest.raises(ValueError):
        ClipComposite.from_dict(data)


@pytest.mark.parametrize('width,height,high', [(0, 48, True), (64, 0, True), (64, True, True),
                                              (2.0, 48, True), (16386, 48, True),
                                              (3, 48, True), (64, 3, False), (64, 48, 1)])
def test_invalid_canvas_and_bit_depth(width, height, high):
    with pytest.raises(ValueError):
        clip_composite_filters(ClipComposite(True), ClipTransform(), width, height, high_bit=high)


def underlay(high, width=64, height=48):
    maximum = 1024 if high else 256
    cy = height if high else height // 2
    return (np.arange(width * height).reshape(height, width) % maximum,
            np.arange(width // 2 * cy).reshape(cy, width // 2) * 113 % maximum,
            np.arange(width // 2 * cy).reshape(cy, width // 2) * 173 % maximum)


def pack_planes(planes, high):
    return b''.join(np.asarray(p, '<u2' if high else 'u1').tobytes() for p in planes)


def unpack_planes(data, width, height, high):
    samples = np.frombuffer(data, '<u2' if high else 'u1')
    ysize = width * height
    csize = ysize // (2 if high else 4)
    assert samples.size == ysize + 2 * csize
    return (samples[:ysize].reshape(height, width).astype(float),
            samples[ysize:ysize + csize].reshape(-1, width // 2).astype(float),
            samples[ysize + csize:].reshape(-1, width // 2).astype(float))


def render_pixels(high, composite, transform, rgb, planes, *, encoder=None):
    if not ffmpeg_available():
        pytest.skip('FFmpeg required for static compositing decoded-pixel proof')
    height, width = rgb.shape[:2]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'source.gbrp16le'
        path.write_bytes(np.asarray(rgb[..., [1, 2, 0]].transpose(2, 0, 1), '<u2').tobytes())
        main, layer, overlay = clip_composite_filters(composite, transform, width, height, high_bit=high)
        graph = f'[0:v]{main}[main];[1:v]{layer}[layer];[main][layer]{overlay}[out]'
        pixel_format = 'yuv422p10le' if high else 'yuv420p'
        command = [ffmpeg_binary(), '-hide_banner', '-v', 'error', '-nostdin',
                   '-noauto_conversion_filters', '-filter_complex_threads', '1',
                   '-f', 'rawvideo', '-pix_fmt', pixel_format, '-s', f'{width}x{height}',
                   '-r', '1', '-i', 'pipe:0', '-f', 'rawvideo', '-pix_fmt', 'gbrp16le',
                   '-s', f'{width}x{height}', '-r', '1', '-i', str(path),
                   '-filter_complex', graph, '-map', '[out]', '-frames:v', '1',
                   '-an', '-threads', '1']
        if encoder:
            encoded = Path(directory) / ('encoded.mov' if high else 'encoded.mkv')
            codec_args = ['-c:v', 'prores_ks', '-profile:v', '3'] if high else ['-c:v', 'libx264', '-crf', '0']
            command += codec_args + ['-pix_fmt', pixel_format, str(encoded)]
        else:
            command += ['-c:v', 'rawvideo', '-pix_fmt', pixel_format, '-f', 'rawvideo', 'pipe:1']
        result = subprocess.run(command, input=pack_planes(planes, high), capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr.decode(errors='replace')
        data = result.stdout
        if encoder:
            result = subprocess.run([ffmpeg_binary(), '-v', 'error', '-threads', '1', '-i', str(encoded),
                                     '-frames:v', '1', '-pix_fmt', pixel_format, '-f', 'rawvideo', 'pipe:1'],
                                    capture_output=True, timeout=30)
            assert result.returncode == 0, result.stderr.decode(errors='replace')
            data = result.stdout
    return unpack_planes(data, width, height, high)


def independently_sample(rgb, transform):
    """Invert a forward homogeneous matrix, not production inverse expressions."""
    height, width = rgb.shape[:2]
    center = np.array([(width - 1) / 2, (height - 1) / 2])
    theta = np.deg2rad(transform.rotation)
    rot = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    rot[np.abs(rot) < 1e-12] = 0
    matrix = rot @ np.diag([(-1 if transform.mirror_x else 1) * transform.scale,
                            (-1 if transform.mirror_y else 1) * transform.scale])
    translation = center + [transform.x, transform.y] - matrix @ center
    forward = np.eye(3)
    forward[:2, :2], forward[:2, 2] = matrix, translation
    y, x = np.indices((height, width))
    source = np.linalg.inv(forward) @ np.stack([x.ravel(), y.ravel(), np.ones(x.size)])
    sx, sy = source[:2].reshape(2, height, width)
    valid = (sx >= 0) & (sx <= width - 1) & (sy >= 0) & (sy <= height - 1)
    sx, sy = np.clip(sx, 0, width - 1), np.clip(sy, 0, height - 1)
    x0, y0 = np.floor(sx).astype(int), np.floor(sy).astype(int)
    x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
    fx, fy = (sx - x0)[..., None], (sy - y0)[..., None]
    sampled = ((1 - fx) * (1 - fy) * rgb[y0, x0] + fx * (1 - fy) * rgb[y0, x1]
               + (1 - fx) * fy * rgb[y1, x0] + fx * fy * rgb[y1, x1])
    return np.where(valid[..., None], sampled, 0), valid


def independent_blend(rgb, planes, composite, transform, high):
    sampled, mask = independently_sample(rgb.astype(float), transform)
    red, green, blue = np.moveaxis(sampled / 65535., -1, 0)
    luma = .2126 * red + .7152 * green + .0722 * blue
    mult = 4 if high else 1
    fg = [mult * (16 + 219 * luma),
          mult * (128 + 224 * (blue - luma) / (2 * (1 - .0722))),
          mult * (128 + 224 * (red - luma) / (2 * (1 - .2126)))]
    alpha = composite.opacity * (mask if composite.transparent_outside_transform else np.ones_like(mask))
    expected, untouched = [], []
    for index, (bottom, top) in enumerate(zip(planes, fg)):
        expanded = bottom.astype(float)
        if index:
            expanded = np.repeat(expanded, 2, axis=1)
            if not high:
                expanded = np.repeat(expanded, 2, axis=0)
        mixed = expanded * (1 - alpha) + top * alpha
        clean = alpha == 0
        if index:
            mixed = (mixed[:, ::2] + mixed[:, 1::2]) / 2
            clean = clean[:, ::2] & clean[:, 1::2]
            if not high:
                mixed = (mixed[::2] + mixed[1::2]) / 2
                clean = clean[::2] & clean[1::2]
        expected.append(mixed)
        untouched.append(clean)
    return expected, untouched


def image(width=64, height=48):
    y, x = np.indices((height, width))
    rgb = np.stack([(x * 991 + y * 223) % 65536,
                    (x * 487 + y * 997) % 65536,
                    (x * 1511 + y * 461) % 65536], -1).astype(np.uint16)
    rgb[4:12, 4:20] = 0  # Real source black must never become transparent.
    rgb[20:28, 20:44] = 65535
    return rgb


@pytest.mark.parametrize('high', [False, True])
@pytest.mark.parametrize('settings', [ClipComposite(True, 0), ClipComposite(False, 0)])
def test_zero_opacity_exact_underlay_all_codes(high, settings):
    planes = underlay(high)
    actual = render_pixels(high, settings, ClipTransform(rotation=31, scale=.7), image(), planes)
    for received, original in zip(actual, planes):
        np.testing.assert_array_equal(received, original)
        assert np.unique(original).size == (1024 if high else 256)


@pytest.mark.parametrize('high', [False, True])
@pytest.mark.parametrize('transform', [ClipTransform(x=16384, y=-16384), ClipTransform(x=-16384)])
def test_fully_outside_exact_underlay(high, transform):
    planes = underlay(high)
    actual = render_pixels(high, ClipComposite(True), transform, image(), planes)
    for received, original in zip(actual, planes):
        np.testing.assert_array_equal(received, original)


@pytest.mark.parametrize('high', [False, True])
@pytest.mark.parametrize('opacity', [0.25, .5, 1])
@pytest.mark.parametrize('transform', [ClipTransform(), ClipTransform(scale=.5, x=5, y=-3),
    ClipTransform(x=.5, y=.25), ClipTransform(mirror_x=True), ClipTransform(mirror_y=True),
    ClipTransform(rotation=90), ClipTransform(rotation=-90), ClipTransform(rotation=180),
    ClipTransform(rotation=31, scale=.75, x=.25, mirror_x=True)])
def test_inverse_geometry_straight_alpha_matches_independent_reference(high, opacity, transform):
    rgb, planes, composite = image(), underlay(high), ClipComposite(True, opacity)
    reference, untouched = independent_blend(rgb, planes, composite, transform, high)
    actual = render_pixels(high, composite, transform, rgb, planes)
    for received, expected, clean, original in zip(actual, reference, untouched, planes):
        np.testing.assert_allclose(received, expected, atol=2, rtol=0)
        np.testing.assert_array_equal(received[clean], original[clean])


@pytest.mark.parametrize('high', [False, True])
def test_opaque_canvas_mode_fades_black_exterior_but_does_not_make_it_transparent(high):
    rgb, planes = image(), underlay(high)
    composite, transform = ClipComposite(False, .5), ClipTransform(scale=.5)
    reference, _ = independent_blend(rgb, planes, composite, transform, high)
    for actual, expected in zip(render_pixels(high, composite, transform, rgb, planes), reference):
        np.testing.assert_allclose(actual, expected, atol=2, rtol=0)


@pytest.mark.parametrize('high', [False, True])
@pytest.mark.parametrize('color', [(0, 0, 0), (65535, 65535, 65535), (65535, 0, 0), (0, 0, 65535)])
def test_true_black_white_saturation_and_opaque_fit_pad_pixels(high, color):
    rgb = np.full((48, 64, 3), color, dtype=np.uint16)
    planes, composite = underlay(high), ClipComposite(True)
    transform = ClipTransform(scale=.5)
    reference, _ = independent_blend(rgb, planes, composite, transform, high)
    actual = render_pixels(high, composite, transform, rgb, planes)
    for got, want in zip(actual, reference):
        np.testing.assert_allclose(got, want, atol=1, rtol=0)
    if color == (0, 0, 0):
        assert actual[0][24, 32] == (64 if high else 16)


@pytest.mark.parametrize('high', [False, True])
@pytest.mark.parametrize('color', [(65535, 0, 0), (0, 0, 65535)])
def test_saturated_single_pixel_even_odd_last_pair_symmetry(high, color):
    rgb = np.full((48, 64, 3), color, dtype=np.uint16)
    # Translating a full-width opaque canvas to either edge gives a 1px strip.
    # Mirrors/offsets must not introduce subsampled-alpha weighting heuristics.
    planes = tuple(np.full_like(p, 64 if i == 0 and high else 16 if i == 0 else 512 if high else 128)
                   for i, p in enumerate(underlay(high)))
    results = []
    for x in (-63, 63):
        composite, transform = ClipComposite(True), ClipTransform(x=x)
        reference, _ = independent_blend(rgb, planes, composite, transform, high)
        actual = render_pixels(high, composite, transform, rgb, planes)
        for got, want in zip(actual, reference):
            np.testing.assert_allclose(got, want, atol=1, rtol=0)
        results.append(actual)
    for plane in (1, 2):
        np.testing.assert_array_equal(results[0][plane][:, 0], results[1][plane][:, -1])


def test_genuine_foreground_1024_step_staircase_not_eightbit_then_dither():
    width, height = 2048, 32
    values = np.rint(np.repeat(np.linspace(0, 65535, 1024), 2)).astype(np.uint16)
    rgb = np.broadcast_to(values[None, :, None], (height, width, 3)).copy()
    # Both flat patches round to the same 8-bit value but differ by >2 Y10 codes.
    rgb[16:, :1024] = 32778
    rgb[16:, 1024:] = 32958
    coarse = (np.rint(rgb.astype(float) / 257) * 257).astype(np.uint16)
    params = (True, ClipComposite(True), ClipTransform())
    actual = render_pixels(*params, rgb, underlay(True, width, height))[0]
    control = render_pixels(*params, coarse, underlay(True, width, height))[0]
    reference = 64 + 876 * rgb.astype(float)[0, :, 0] / 65535
    assert np.unique(actual[0]).size >= 875
    assert np.unique(control[0]).size <= 256
    error = np.abs(actual[0] - reference).mean()
    control_error = np.abs(control[0] - reference).mean()
    assert error < .3 and error < control_error * .4
    assert actual[20, 1400] - actual[20, 400] >= 2
    assert control[20, 1400] == control[20, 400]


@pytest.mark.parametrize('high', [False, True])
def test_actual_h264_or_prores_encoding_retains_composition(high):
    rgb = np.full((48, 64, 3), (65535, 0, 0), dtype=np.uint16)
    planes = tuple(np.full_like(p, value) for p, value in zip(
        underlay(high), (288, 650, 400) if high else (72, 162, 100)))
    composite, transform = ClipComposite(True, .5), ClipTransform(scale=.5)
    raw = render_pixels(high, composite, transform, rgb, planes)
    encoded = render_pixels(high, composite, transform, rgb, planes, encoder=True)
    for before, after in zip(raw, encoded):
        # H.264 CRF0 is lossless. ProRes is lossy; legal flat picture planes
        # give a bounded codec check, separate from all-code raw identities.
        np.testing.assert_allclose(after, before, atol=8 if high else 0, rtol=0)


@pytest.mark.parametrize('high', [False, True])
@pytest.mark.parametrize('opacity', [.25, .5, 1])
def test_smallest_last_chroma_pair_odd_even_symmetry(high, opacity):
    rgb = np.full((4, 2, 3), (65535, 0, 0), dtype=np.uint16)
    planes = tuple(np.full_like(p, v) for p, v in zip(underlay(high, 2, 4),
                                                    (64, 512, 512) if high else (16, 128, 128)))
    outputs = []
    for shift in (-1, 1):
        composite, transform = ClipComposite(True, opacity), ClipTransform(x=shift)
        ref, _ = independent_blend(rgb, planes, composite, transform, high)
        got = render_pixels(high, composite, transform, rgb, planes)
        for actual, expected in zip(got, ref):
            np.testing.assert_allclose(actual, expected, atol=2, rtol=0)
        outputs.append(got)
    for plane in (1, 2):
        np.testing.assert_array_equal(outputs[0][plane], outputs[1][plane])


def test_highbit_odd_height_preserves_transparent_underlay():
    planes = underlay(True, 64, 3)
    got = render_pixels(True, ClipComposite(True, 0), ClipTransform(), image(64, 3), planes)
    for actual, source in zip(got, planes):
        np.testing.assert_array_equal(actual, source)


def test_typed_graph_never_contains_eightbit_foreground_and_documents_straight_alpha():
    main, layer, overlay = clip_composite_filters(ClipComposite(True, .5), ClipTransform(rotation=17),
                                                 1920, 1080, high_bit=True)
    assert 'format=yuv422p10le' in main and 'format=yuv444p10le' in main
    assert 'flags=neighbor' in main
    assert 'format=gbrp16le' in layer and 'format=gbrap16le' in layer
    assert 'zscale=matrixin=gbr:rangein=full:matrix=709:range=limited:dither=none' in layer
    assert 'format=yuva444p10le' in layer and 'rgba' not in layer
    assert 'pad=1921:1081' in layer and 'crop=1920:1080' in layer
    assert 'alpha=straight' in overlay and 'overlay=format=yuv444p10:' in overlay
    assert 'flags=area' in overlay and 'pad=iw+2:ih:0:0:color=black' in overlay


class ProbeError(RuntimeError):
    pass


class ProbeCancelled(RuntimeError):
    pass


def run_probe(high=True, timeout=30, cancel=None):
    from lumen.clip_composite import verify_clip_composite_support
    verify_clip_composite_support(high, timeout, cancel, ProbeError, ProbeCancelled)


def probe_oracle(source, layer, high):
    """Independent numpy oracle from actual synthetic process inputs."""
    planes = unpack_planes(source, 64, 64, high)
    raw_rgb = np.frombuffer(layer, '<u2').reshape(3, 64, 64)
    rgb = raw_rgb[[2, 0, 1]].transpose(1, 2, 0)
    cases = [(0, 1, 0, True), (0, .5, .5, True), (-63, 1, 1, True),
             (63, 1, 1, True), (0, 1, 1, True), (16384, 1, 1, True), (0, .5, .5, False)]
    frames = []
    for shift, scale, opacity, transparent in cases:
        expected, _ = independent_blend(rgb, planes, ClipComposite(transparent, opacity),
                                        ClipTransform(x=shift, scale=scale), high)
        frames.append(pack_planes([np.rint(p) for p in expected], high))
    return b''.join(frames)


class ProbeProcess:
    def __init__(self, command, *, stdin, stdout, stderr, running=False,
                 returncode=0, bad=None, log_size=0):
        self.command, self.running, self.returncode = command, running, returncode
        self.killed = self.waited = False
        self.path = Path(command[command.index('-filter_complex') - 1])
        source = stdin.read()
        self.high = command[command.index('-pix_fmt') + 1] == 'yuv422p10le'
        payload = probe_oracle(source, self.path.read_bytes(), self.high)
        if bad == 'short':
            payload = payload[:-1]
        elif bad == 'extra':
            payload += b'x'
        elif bad == 'empty':
            payload = b''
        elif bad == 'noop':
            payload = source * 7
        elif bad == 'eightbit':
            values = np.frombuffer(payload, '<u2')
            payload = ((values // 4) * 4).astype('<u2').tobytes()
        stdout.write(payload)
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


@pytest.mark.parametrize('high', [False, True])
def test_actual_bounded_capability_probe(high):
    if not ffmpeg_available():
        pytest.skip('FFmpeg required for behavior probe')
    run_probe(high)


@pytest.mark.parametrize('high', [False, True])
def test_probe_is_typed_repeated_and_cleans_files(high, monkeypatch):
    processes = []
    def spawn(command, **kwargs):
        processes.append(ProbeProcess(command, **kwargs))
        return processes[-1]
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    run_probe(high)
    run_probe(high)
    assert len(processes) == 2
    for process in processes:
        assert process.waited and not process.killed
        assert not process.path.exists() and not process.path.parent.exists()
        assert '-noauto_conversion_filters' in process.command
        assert len(subprocess.list2cmdline(process.command)) < 30000  # Windows argv budget.
        assert process.command[process.command.index('-frames:v') + 1] == '7'
        assert process.command[process.command.index('-pix_fmt', process.command.index('-pix_fmt') + 1) + 1] == 'gbrp16le'


@pytest.mark.parametrize('bad,code', [('short', 0), ('extra', 0), ('empty', 0),
                                      ('noop', 0), ('eightbit', 0), (None, 1)])
def test_probe_fails_closed_on_unsupported_or_false_precision(bad, code, monkeypatch):
    processes = []
    def spawn(command, **kwargs):
        processes.append(ProbeProcess(command, **kwargs, bad=bad, returncode=code))
        return processes[-1]
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    with pytest.raises(ProbeError):
        run_probe()
    assert processes[0].waited and not processes[0].path.exists()


@pytest.mark.parametrize('reason', ['cancel', 'timeout', 'output_budget', 'log_budget'])
def test_probe_reaps_process_and_removes_fixture_on_interrupt(reason, monkeypatch):
    import threading
    cancel = threading.Event()
    processes = []
    def spawn(command, **kwargs):
        processes.append(ProbeProcess(command, **kwargs, running=True,
                                      bad='extra' if reason == 'output_budget' else None,
                                      log_size=65537 if reason == 'log_budget' else 0))
        if reason == 'cancel':
            cancel.set()
        return processes[-1]
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    with pytest.raises(ProbeCancelled if reason == 'cancel' else ProbeError):
        run_probe(timeout=0 if reason == 'timeout' else 30, cancel=cancel)
    assert processes[0].waited and processes[0].killed
    assert not processes[0].path.exists() and not processes[0].path.parent.exists()


def test_probe_missing_binary_fails_explicit_and_removes_fixture(monkeypatch):
    paths = []
    def spawn(command, **kwargs):
        paths.append(Path(command[command.index('-filter_complex') - 1]))
        raise FileNotFoundError('Missing FFmpeg')
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    with pytest.raises(ProbeError, match='compatible FFmpeg'):
        run_probe()
    assert paths and not paths[0].exists()


@pytest.mark.parametrize('when', ['before', 'prepared'])
def test_precancel_never_starts_probe(when, monkeypatch):
    import threading
    from lumen import clip_composite as module
    cancel = threading.Event()
    if when == 'before':
        cancel.set()
    else:
        original = module._probe_inputs
        def prepare(high):
            result = original(high)
            cancel.set()
            return result
        monkeypatch.setattr(module, '_probe_inputs', prepare)
    def forbidden(*args, **kwargs):
        pytest.fail('Cancelled capability probe must not spawn')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(ProbeCancelled):
        run_probe(cancel=cancel)


def run_rgb8_blur_gate(timeout=30, cancel=None):
    from lumen.clip_composite import ensure_composite_rgb8_blur_support
    ensure_composite_rgb8_blur_support(timeout, cancel, ProbeError, ProbeCancelled)


class RGB8BlurProbeProcess:
    def __init__(self, command, *, stdin, stdout, stderr, running=False, code=0,
                 bad=None, log_size=0):
        self.command, self.running, self.returncode = command, running, code
        self.killed = self.waited = False
        self.handles = (stdin, stdout, stderr)
        self.original = stdin.read()
        payload = self.original
        if bad == 'short':
            payload = payload[:-1]
        elif bad == 'extra':
            payload += b'x'
        elif bad == 'empty':
            payload = b''
        elif bad == 'white':
            payload = bytes([255]) * len(payload)
        stdout.write(payload)
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


def test_actual_rgb8_scalar_blur_gate():
    if not ffmpeg_available():
        pytest.skip('FFmpeg required for RGB8 blur gate')
    run_rgb8_blur_gate()


@pytest.mark.parametrize('width,height,sigma', [(2, 48, 30), (64, 64, 2), (64, 64, 30)])
def test_real_rgb8_blur_pixels_and_rejection_of_noop_or_channel_corruption(width, height, sigma):
    if not ffmpeg_available():
        pytest.skip('FFmpeg required for RGB8 blur pixels')
    from lumen import clip_composite as module
    original = module._rgb8_blur_input(width, height, sigma)
    result = subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-cpuflags', '0',
                             '-filter_threads', '1', '-noauto_conversion_filters',
                             '-f', 'rawvideo', '-pix_fmt', 'gbrp', '-s', f'{width}x{height}',
                             '-i', 'pipe:0', '-vf', f'gblur=sigma={sigma}:steps=3,format=gbrp',
                             '-frames:v', '1', '-c:v', 'rawvideo', '-threads', '1',
                             '-pix_fmt', 'gbrp', '-f', 'rawvideo', 'pipe:1'],
                            input=original, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert module._valid_rgb8_blur(result.stdout, original, width, sigma)
    assert not module._valid_rgb8_blur(bytes(len(original)), original, width, sigma)
    assert not module._valid_rgb8_blur(bytes([255]) * len(original), original, width, sigma)
    if width == 2:
        assert result.stdout == bytes([100]) * 288
    else:
        assert not module._valid_rgb8_blur(original, original, width, sigma), 'A no-op is not verified blur'
        assert not module._valid_rgb8_blur(bytes([100]) * 4096 + bytes([73]) * 4096 + bytes([44]) * 4096,
                                           original, width, sigma), 'A flat fill is not verified blur'
        swapped = result.stdout[4096:8192] + result.stdout[:4096] + result.stdout[8192:]
        assert not module._valid_rgb8_blur(swapped, original, width, sigma)
        if sigma == 2:
            # This independent three-fold discrete exponential convolution is
            # a full-field oracle, not the production probe's loose intervals.
            from test_highbit_effects import independent_blur
            source = np.frombuffer(original, 'u1').reshape(3, 64, 64).transpose(1, 2, 0)
            actual = np.frombuffer(result.stdout, 'u1').reshape(3, 64, 64).transpose(1, 2, 0)
            np.testing.assert_allclose(actual, independent_blur(source, 2), atol=.51, rtol=0)


def test_rgb8_blur_probe_is_repeated_typed_scalar_and_closes_handles(monkeypatch):
    from lumen import clip_composite as module
    processes = []
    def spawn(command, **kwargs):
        processes.append(RGB8BlurProbeProcess(command, **kwargs))
        return processes[-1]
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    # This test checks lifecycle and command construction only. Actual validator
    # behavior and independent pixels are separately exercised above.
    monkeypatch.setattr(module, '_valid_rgb8_blur', lambda *args: True)
    run_rgb8_blur_gate()
    run_rgb8_blur_gate()
    assert len(processes) == 6
    for i, process in enumerate(processes):
        expected = [('2x48', 30, 288), ('64x64', 2, 12288), ('64x64', 30, 12288)][i % 3]
        command = process.command
        assert command[command.index('-cpuflags') + 1] == '0'
        assert '-noauto_conversion_filters' in command
        assert command[command.index('-pix_fmt') + 1] == 'gbrp'
        assert command[command.index('-s') + 1] == expected[0]
        assert command[command.index('-vf') + 1] == f'gblur=sigma={expected[1]}:steps=3,format=gbrp'
        assert len(process.original) == expected[2]
        assert process.waited and not process.killed
        assert all(handle.closed for handle in process.handles)


@pytest.mark.parametrize('bad,code', [('short', 0), ('extra', 0), ('empty', 0),
                                      ('white', 0), (None, 1), (None, 0)])
def test_rgb8_blur_gate_rejects_failed_corrupt_and_noop_results(bad, code, monkeypatch):
    processes = []
    def spawn(command, **kwargs):
        processes.append(RGB8BlurProbeProcess(command, **kwargs, bad=bad, code=code))
        return processes[-1]
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    with pytest.raises(ProbeError):
        run_rgb8_blur_gate()
    # The unchanged constant narrow fixture is valid; the next point impulse
    # must reject an unchanged output. All malformed narrow results stop first.
    assert len(processes) == (2 if bad is None and code == 0 else 1)
    assert all(process.waited and all(h.closed for h in process.handles) for process in processes)


@pytest.mark.parametrize('reason', ['cancel', 'timeout', 'output_budget', 'log_budget'])
def test_rgb8_blur_gate_interrupts_reaps_and_closes(reason, monkeypatch):
    import threading
    cancel = threading.Event()
    processes = []
    def spawn(command, **kwargs):
        processes.append(RGB8BlurProbeProcess(command, **kwargs, running=True,
                                              bad='extra' if reason == 'output_budget' else None,
                                              log_size=65537 if reason == 'log_budget' else 0))
        if reason == 'cancel':
            cancel.set()
        return processes[-1]
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    with pytest.raises(ProbeCancelled if reason == 'cancel' else ProbeError):
        run_rgb8_blur_gate(timeout=0 if reason == 'timeout' else 30, cancel=cancel)
    assert len(processes) == 1 and processes[0].waited and processes[0].killed
    assert all(h.closed for h in processes[0].handles)


def test_rgb8_blur_missing_binary_closes_handles(monkeypatch):
    handles = []
    def spawn(command, **kwargs):
        handles.extend(kwargs.values())
        raise FileNotFoundError('Missing FFmpeg')
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    with pytest.raises(ProbeError, match='compatible FFmpeg'):
        run_rgb8_blur_gate()
    assert handles and all(h.closed for h in handles)


@pytest.mark.parametrize('when', ['before', 'prepared'])
def test_rgb8_blur_precancel_does_not_spawn(when, monkeypatch):
    import threading
    from lumen import clip_composite as module
    cancel = threading.Event()
    if when == 'before':
        cancel.set()
    else:
        original = module._rgb8_blur_input
        def prepare(*args):
            result = original(*args)
            cancel.set()
            return result
        monkeypatch.setattr(module, '_rgb8_blur_input', prepare)
    def forbidden(*args, **kwargs):
        pytest.fail('Cancelled RGB8 blur probe must not spawn')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(ProbeCancelled):
        run_rgb8_blur_gate(cancel=cancel)


@pytest.mark.parametrize('timeout', [True, -1, float('nan'), float('inf'), '30'])
def test_rgb8_blur_gate_rejects_invalid_timeout(timeout):
    with pytest.raises(ValueError):
        run_rgb8_blur_gate(timeout=timeout)

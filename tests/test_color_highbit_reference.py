"""Independent decoded-pixel acceptance for the genuine 10-bit delivery path.

All fixtures are synthetic and local. Oracles deliberately do not call the
production color evaluator, source/output helpers, or timeline compiler. The
negative precision control is important: a 10-bit tag or >256 decoded values
alone cannot rule out an earlier 8-bit bottleneck followed by dithering.
"""
from copy import deepcopy
from fractions import Fraction
import json
import subprocess

import numpy as np
import pytest

from lumen.clip_transform import ClipTransform
from lumen.media import ffmpeg_available, ffmpeg_binary, ffprobe_binary, probe
from lumen.model import Caption, Clip, ClipEffect, Grade, Overlay, Project
from lumen.render import RenderEngine, RenderError
from lumen.restoration_settings import RestorationSettings
from lumen.stabilization_settings import StabilizationSettings
from lumen.timing import ClipTimeMap


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
PROFILE = 'prores_422_hq_10bit'


def ff(*args, data=None):
    return subprocess.run([ffmpeg_binary(), '-hide_banner', '-v', 'error',
                           '-nostdin', '-y', '-threads', '1', *map(str, args)],
                          input=data, capture_output=True, check=True, timeout=60).stdout


def rgb_source(tmp_path, rgb, *, name='rgb16', frames=3, fps=Fraction(30),
               transfer='bt709'):
    """Encode exact full-range RGB16 samples without an intermediate YUV codec."""
    rgb = np.asarray(rgb, dtype='<u2')
    if rgb.ndim == 3:
        rgb = np.repeat(rgb[None], frames, axis=0)
    count, height, width, _ = rgb.shape
    planar = np.moveaxis(rgb[..., [1, 2, 0]], -1, 1).astype('<u2')
    tags = ['-colorspace', 'rgb', '-color_range', 'pc', '-color_trc', transfer,
            '-color_primaries', 'bt709']
    path = tmp_path / f'{name}.mkv'
    ff('-f', 'rawvideo', '-pix_fmt', 'gbrp16le', '-s', f'{width}x{height}',
       '-r', f'{fps.numerator}/{fps.denominator}', *tags, '-i', 'pipe:0',
       '-frames:v', count, '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'gbrp16le',
       *tags, path, data=planar.tobytes())
    actual = ff('-i', path, '-map', '0:V:0', '-pix_fmt', 'gbrp16le',
                '-f', 'rawvideo', 'pipe:1')
    assert actual == planar.tobytes(), 'Precision fixtures must preserve every source code'
    return probe(path)


def project(media, *, frames=1, grade=None, fps=Fraction(30)):
    return Project(width=media.width, height=media.height,
                   fps_num=fps.numerator, fps_den=fps.denominator, media=[media],
                   clips=[Clip('picture', media.id, 0, 0, 0, frames,
                               grade=grade or Grade())])


def video_metadata(path):
    result = subprocess.run([ffprobe_binary(), '-v', 'error', '-select_streams', 'v:0',
                             '-show_entries',
                             'stream=codec_name,profile,pix_fmt,width,height,color_range,'
                             'color_space,color_transfer,color_primaries', '-of', 'json',
                             str(path)], capture_output=True, text=True, check=True,
                            timeout=30)
    return json.loads(result.stdout)['streams'][0]


def yuv422(path, width, height):
    """Read native decoded code values, not a second RGB conversion oracle."""
    metadata = video_metadata(path)
    assert metadata['pix_fmt'] == 'yuv422p10le'
    raw = ff('-i', path, '-map', '0:V:0', '-an', '-pix_fmt', 'yuv422p10le',
             '-f', 'rawvideo', 'pipe:1')
    pixels = width * height
    frames = np.frombuffer(raw, '<u2').reshape(-1, pixels * 2)
    assert frames.size and frames.max() <= 1023
    return (frames[:, :pixels].reshape(-1, height, width).astype(float),
            frames[:, pixels:pixels * 3 // 2].reshape(-1, height, width // 2).astype(float),
            frames[:, pixels * 3 // 2:].reshape(-1, height, width // 2).astype(float))


def rec709_yuv10(rgb):
    """BT.709 encoded RGB -> legal-range 10-bit Y'CbCr, before rounding."""
    red, green, blue = np.moveaxis(np.asarray(rgb, dtype=float), -1, 0)
    luma = .2126 * red + .7152 * green + .0722 * blue
    return np.stack([64 + 876 * luma,
                     512 + 896 * (blue - luma) / (2 * (1 - .0722)),
                     512 + 896 * (red - luma) / (2 * (1 - .2126))], axis=-1)


@pytest.fixture
def engine():
    renderer = RenderEngine(timeout=60)
    try:
        yield renderer
    finally:
        renderer.close()


def test_prores_retains_true_source_precision_not_only_ten_bit_tags(tmp_path, engine):
    width, height = 2048, 64
    image = np.repeat(np.rint(np.linspace(0, 65535, width))[None, :, None],
                      height, axis=0).repeat(3, axis=2).astype(np.uint16)
    # These two actual RGB16 values BOTH round to 128 in an 8-bit RGB image.
    # Broad flat interiors defeat both spatial dithering and codec ringing.
    image[32:, :width // 2] = 32778
    image[32:, width // 2:] = 32958
    coarse = (np.rint(image.astype(float) / 257) * 257).astype(np.uint16)
    assert np.unique(coarse[40:, :, 0]).tolist() == [128 * 257]
    source = rgb_source(tmp_path, image)
    control = rgb_source(tmp_path, coarse, name='deliberately-eight-bit')
    output = engine.render(project(source), tmp_path / 'precision.mov', export_profile=PROFILE)
    quantized = engine.render(project(control), tmp_path / 'quantized.mov', export_profile=PROFILE)
    metadata = video_metadata(output)
    # MOV nclc records primaries/transfer/matrix, not a stream range bit.
    # The ProRes decoder's actual frame range must nevertheless be limited.
    assert metadata.pop('color_range', 'tv') == 'tv'
    assert metadata == dict(codec_name='prores', profile='HQ', width=width, height=height,
                            pix_fmt='yuv422p10le', color_space='bt709',
                            color_transfer='bt709', color_primaries='bt709')
    frame_probe = subprocess.run([ffprobe_binary(), '-v', 'error', '-select_streams', 'v:0',
                                  '-read_intervals', '%+#1', '-show_frames', '-show_entries',
                                  'frame=pix_fmt,color_range,color_space,color_transfer,color_primaries',
                                  '-of', 'json', str(output)], capture_output=True, text=True,
                                 check=True, timeout=30)
    first_frame = json.loads(frame_probe.stdout)['frames'][0]
    assert first_frame['pix_fmt'] == 'yuv422p10le' and first_frame['color_range'] == 'tv'
    assert first_frame['color_space'] == first_frame['color_transfer'] == first_frame['color_primaries'] == 'bt709'
    y, cb, cr = yuv422(output, width, height)
    coarse_y, _, _ = yuv422(quantized, width, height)
    ramp = y[0, 8:24].mean(axis=0)
    reference = 64 + 876 * image[8, :, 0].astype(float) / 65535
    assert np.unique(y[0, 16]).size > 700
    assert abs(ramp[0] - 64) <= 2 and abs(ramp[-1] - 940) <= 2
    error = np.abs(ramp - reference).mean()
    control_error = np.abs(coarse_y[0, 8:24].mean(axis=0) - reference).mean()
    assert error < .65, f'Genuine 10-bit luma has excessive code error: {error}'
    assert error < control_error * .7, '10-bit output has retained only 8-bit source precision'
    low = y[0, 40:56, 128:896].mean()
    high = y[0, 40:56, 1152:1920].mean()
    assert high - low >= 1.75, 'Sub-8-bit source distinctions were lost before encoding'
    assert abs(coarse_y[0, 40:56, 128:896].mean()
               - coarse_y[0, 40:56, 1152:1920].mean()) <= .5
    np.testing.assert_allclose(cb, 512, atol=1, rtol=0)
    np.testing.assert_allclose(cr, 512, atol=1, rtol=0)


@pytest.mark.parametrize('range_tag', ['tv', 'pc'])
@pytest.mark.parametrize('matrix,kr,kb', [('bt709', .2126, .0722),
                                        ('smpte170m', .299, .114),
                                        ('bt470bg', .299, .114)])
def test_yuv_source_range_and_matrix_to_prores_match_independent_reference(
        tmp_path, engine, range_tag, matrix, kr, kb):
    width, height = 64, 48
    values = (480, 600, 640)
    frame = b''.join(np.full((height, width), value, '<u2').tobytes() for value in values)
    tags = ['-colorspace', matrix, '-color_range', range_tag,
            '-color_trc', 'bt709', '-color_primaries', 'bt709']
    path = tmp_path / 'tagged-yuv10.mkv'
    ff('-f', 'rawvideo', '-pix_fmt', 'yuv444p10le', '-s', f'{width}x{height}',
       '-r', '30', *tags, '-i', 'pipe:0', '-frames:v', '3', '-c:v', 'ffv1',
       '-level', '3', '-pix_fmt', 'yuv444p10le', *tags, path, data=frame * 3)
    assert ff('-i', path, '-frames:v', 1, '-pix_fmt', 'yuv444p10le',
              '-f', 'rawvideo', 'pipe:1') == frame
    y = (values[0] - 64) / 876 if range_tag == 'tv' else values[0] / 1023
    cb, cr = [(value - 512) / (896 if range_tag == 'tv' else 1023)
              for value in values[1:]]
    red, blue = y + 2 * (1 - kr) * cr, y + 2 * (1 - kb) * cb
    green = (y - kr * red - kb * blue) / (1 - kr - kb)
    expected = rec709_yuv10([red, green, blue])
    source = probe(path)
    assert (source.color_range, source.color_space) == (range_tag, matrix)
    output = engine.render(project(source), tmp_path / 'matrix.mov', export_profile=PROFILE)
    planes = yuv422(output, width, height)
    actual = [plane[0, 8:-8, 8:-8].mean() for plane in planes]
    np.testing.assert_allclose(actual, expected, atol=2, rtol=0)


@pytest.mark.parametrize('encoding,transfer', [('rec709', 'bt709'),
                                               ('srgb', 'iec61966-2-1'),
                                               ('linear_rec709', 'linear')])
def test_input_transfer_and_linear_grade_match_independent_equations(
        tmp_path, engine, encoding, transfer):
    samples = np.array([7000, 19000, 31000], dtype=np.uint16)
    source = rgb_source(tmp_path, np.broadcast_to(samples, (48, 64, 3)), transfer=transfer)
    grade = Grade(input_encoding=encoding, linear_exposure=.5)
    rgb = samples.astype(float) / 65535
    if encoding == 'rec709':
        linear = np.where(rgb < .081, rgb / 4.5, ((rgb + .099) / 1.099) ** (1 / .45))
    elif encoding == 'srgb':
        linear = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
    else:
        linear = rgb
    linear *= 2 ** .5
    normalized = np.where(linear < .018, 4.5 * linear, 1.099 * linear ** .45 - .099)
    expected = rec709_yuv10(normalized)
    output = engine.render(project(source, grade=grade), tmp_path / 'graded.mov',
                           export_profile=PROFILE)
    actual = [plane[0, 8:-8, 8:-8].mean() for plane in yuv422(output, 64, 48)]
    np.testing.assert_allclose(actual, expected, atol=2, rtol=0)


@pytest.mark.parametrize('fps', [Fraction(30), Fraction(30000, 1001)], ids=['30', '29.97'])
def test_late_upper_track_never_blocks_lower_track_or_extends_its_last_frame(tmp_path, engine, fps):
    bottom_rgb, top_rgb = np.array([12000, 21000, 30000]), np.array([45000, 12000, 19000])
    bottom = rgb_source(tmp_path, np.broadcast_to(bottom_rgb, (48, 64, 3)),
                        name='bottom', frames=12, fps=fps)
    top = rgb_source(tmp_path, np.broadcast_to(top_rgb, (48, 64, 3)),
                     name='upper', frames=3, fps=fps)
    p = Project(width=64, height=48, fps_num=fps.numerator, fps_den=fps.denominator,
                media=[bottom, top], clips=[Clip('bottom', bottom.id, 0, 2, 0, 8),
                                           Clip('upper', top.id, 1, 5, 0, 2)])
    output = engine.render(p, tmp_path / 'tracks.mov', export_profile=PROFILE)
    planes = yuv422(output, 64, 48)
    actual = np.stack([plane[:, 8:-8, 8:-8].mean(axis=(1, 2)) for plane in planes], axis=1)
    black = np.array([64., 512., 512.])
    low, high = rec709_yuv10(bottom_rgb / 65535), rec709_yuv10(top_rgb / 65535)
    expected = np.array([black, black, low, low, low, high, high, low, low, low])
    assert len(actual) == 10
    np.testing.assert_allclose(actual, expected, atol=2, rtol=0)


def test_retime_restoration_and_rgb16_geometry_keep_high_precision(tmp_path, engine):
    width, height = 2048, 32
    ramp = np.broadcast_to(np.rint(np.linspace(6000, 53000, width))[None, :, None],
                           (height, width, 3)).astype(np.uint16).copy()
    source_frames = np.stack([ramp + index * 1000 for index in range(6)])
    source = rgb_source(tmp_path, source_frames)
    p = project(source, frames=3)
    p.clips[0].time_map = ClipTimeMap(Fraction(0), Fraction(6), Fraction(2))
    p.clips[0].transform = ClipTransform(mirror_x=True)
    p.clips[0].restoration = RestorationSettings(enabled=True, denoise=.3, sharpen=.2)
    output = engine.render(p, tmp_path / 'restored-mirrored-retimed.mov', export_profile=PROFILE)
    y, _, _ = yuv422(output, width, height)
    assert len(y) == 3
    # 2x sampling picks source frames 0, 2, 4, independently of the compiler.
    expected = 64 + 876 * source_frames[[0, 2, 4], 16, ::-1, 0].astype(float) / 65535
    assert np.unique(y[0, 16]).size > 500
    np.testing.assert_allclose(y[:, 16, 8:-8], expected[:, 8:-8], atol=2, rtol=0)


def test_stabilization_never_delivers_the_eight_bit_analysis_proxy(tmp_path, engine):
    filters = ff('-filters').decode('utf-8', errors='replace')
    if not all(name in filters for name in ('vidstabdetect', 'vidstabtransform')):
        pytest.skip('External FFmpeg needs libvidstab for this stabilization acceptance')
    # Keep the 640-wide motion proxy at least 64 pixels tall, as required by
    # the existing stabilizer; the delivery canvas remains full resolution.
    width, height = 2048, 256
    image = np.broadcast_to(np.rint(np.linspace(0, 65535, width))[None, :, None],
                            (height, width, 3)).astype(np.uint16).copy()
    image[64:96, :width // 2] = 32778
    image[64:96, width // 2:] = 32958
    # Static high-contrast structure supplies valid motion-analysis features;
    # the independently checked ramp/flat patches retain actual RGB16 data.
    rng = np.random.default_rng(709)
    texture = rng.choice(np.array([0, 65535], np.uint16), size=((height - 96) // 8, width // 8))
    image[96:] = np.repeat(np.repeat(texture, 8, axis=0), 8, axis=1)[..., None]
    source = rgb_source(tmp_path, image, frames=8)
    p = project(source, frames=3)
    p.clips[0].stabilization = StabilizationSettings(enabled=True, smoothing=2, border='black')
    output = engine.render(p, tmp_path / 'stabilized.mov', export_profile=PROFILE)
    y, _, _ = yuv422(output, width, height)
    assert len(y) == 3 and np.unique(y[0, 24]).size > 700
    reference = 64 + 876 * image[24, :, 0].astype(float) / 65535
    np.testing.assert_allclose(y[:, 24, 16:-16], np.broadcast_to(reference[16:-16],
                               (3, width - 32)), atol=2, rtol=0)
    assert (y[:, 72:88, 1152:1920].mean()
            - y[:, 72:88, 128:896].mean()) >= 1.75


@pytest.mark.parametrize('feature', ['effect-capability', 'sticker'])
def test_unverified_eight_bit_features_are_rejected_before_grading(
        tmp_path, engine, monkeypatch, feature):
    source = rgb_source(tmp_path, np.full((48, 64, 3), 32000, dtype=np.uint16))
    p = project(source)
    if feature == 'effect-capability':
        p.clips[0].effects = [ClipEffect('vignette', 'vignette', 1)]
        import lumen.highbit_effects as high_effects
        def unsupported(*args, **kwargs):
            raise RenderError('ProRes RGB16 radial vignette capability unavailable.')
        monkeypatch.setattr(high_effects, 'ensure_highbit_effects', unsupported)
    else:
        # The output profile must reject this enabled path before trying to
        # resolve assets, rather than fail accidentally because the file lacks.
        p.overlays = [Overlay('sticker', str(tmp_path / 'unused.json'), '0' * 64, 0, 1)]
    def unexpected_grading(*args, **kwargs):
        pytest.fail('Unsupported export must fail before grading any clip')
    monkeypatch.setattr(engine, '_graded_segment', unexpected_grading)
    for operation in (
            lambda: engine._compile(p, export_profile=PROFILE),
            lambda: engine.render(p, tmp_path / 'unsupported.mov', export_profile=PROFILE)):
        with pytest.raises(RenderError, match='(?i)(10.bit|prores)'):
            operation()
    assert not (tmp_path / 'unsupported.mov').exists()
    assert not list(tmp_path.glob('.lumen-render-*'))


def test_inactive_effects_and_stickers_do_not_disable_highbit_export(tmp_path, engine):
    source = rgb_source(tmp_path, np.full((48, 64, 3), 32000, dtype=np.uint16))
    p = project(source)
    before = engine.render(p, tmp_path / 'clean.mov', export_profile=PROFILE)
    p.clips[0].effects = [ClipEffect('blur', 'blur', 2, enabled=False),
                         ClipEffect('zero-vignette', 'vignette', 0, enabled=True)]
    p.overlays = [Overlay('off', str(tmp_path / 'unused.png'), '0' * 64, 0, 1, enabled=False)]
    after = engine.render(p, tmp_path / 'disabled.mov', export_profile=PROFILE)
    for a, b in zip(yuv422(before, 64, 48), yuv422(after, 64, 48)):
        np.testing.assert_array_equal(a, b)


def test_profile_validation_rejects_unknown_profile_and_wrong_container(tmp_path, engine):
    source = rgb_source(tmp_path, np.full((48, 64, 3), 32000, dtype=np.uint16))
    p = project(source)
    with pytest.raises(RenderError):
        engine.render(p, tmp_path / 'wrong.mp4', export_profile=PROFILE)
    with pytest.raises(RenderError):
        engine.render(p, tmp_path / 'unknown.mov', export_profile='unknown_profile')
    assert not (tmp_path / 'wrong.mp4').exists()
    assert not (tmp_path / 'unknown.mov').exists()
    assert not list(tmp_path.glob('.lumen-render-*'))


def test_default_export_stays_h264_eight_bit_and_explicit_default_matches(tmp_path, engine):
    source = rgb_source(tmp_path, np.full((48, 64, 3), 32000, dtype=np.uint16))
    p = project(source)
    default = engine.render(p, tmp_path / 'default.mp4')
    explicit = engine.render(deepcopy(p), tmp_path / 'explicit.mp4', export_profile='h264_8bit')
    for path in (default, explicit):
        metadata = video_metadata(path)
        assert metadata['codec_name'] == 'h264' and metadata['pix_fmt'] == 'yuv420p'
        assert metadata['color_range'] == 'tv' and metadata['color_space'] == 'bt709'
    assert ff('-i', default, '-an', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1') == ff(
        '-i', explicit, '-an', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1')

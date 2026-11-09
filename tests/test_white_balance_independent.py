"""Independent scalar transfer/XYZ chart oracle and real decoded WB delivery.

Expected pixels never call production transfer, grade, or output helpers.
This verifies diagonal neutral correction, not chromatic adaptation or a
calibrated monitor. Qt coverage is the actual metadata-corrected decode path.
"""
import math
import os
from fractions import Fraction

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pytest

from lumen.color import apply_grade
from lumen.model import Grade


def encode(v):
    a = abs(float(v))
    return math.copysign(4.5*a if a < .018 else 1.099*a**.45-.099, v)


def decode(v):
    a = abs(float(v))
    return math.copysign(a/4.5 if a < .081 else ((a+.099)/1.099)**(1/.45), v)


def neutral_gains(encoded):
    linear = [decode(v) for v in encoded]
    y = sum(a*b for a, b in zip(linear, (.2126, .7152, .0722)))
    return np.asarray([y/v for v in linear])


def oracle(rgb, gains, encoding='rec709', exposure=0., lift=(0, 0, 0)):
    rows = []
    for pixel in np.asarray(rgb).reshape(-1, 3):
        row = []
        for c, v in enumerate(pixel):
            v = float(v)
            if encoding == 'srgb':
                a = abs(v)
                v = math.copysign(a/12.92 if a <= .04045 else ((a+.055)/1.055)**2.4, v)
            elif encoding != 'linear_rec709':
                v = decode(v)
            row.append(encode(v*float(gains[c])*2**exposure + lift[c]))
        rows.append(row)
    return np.asarray(rows).reshape(np.shape(rgb))


def chart():
    # Independently specified CIE XYZ stimuli transformed with the published
    # D65 XYZ-to-linear-sRGB matrix; not measured/claimed ColorChecker patches.
    xyz = np.array([[.171,.180,.196], [.12,.09,.04], [.1,.18,.07],
                    [.08,.05,.25], [.28,.24,.08], [.11,.12,.21]])
    matrix = np.array([[3.2404542,-1.5371385,-.4985314],
                       [-.9692660,1.8760108,.0415560],
                       [.0556434,-.2040259,1.0572252]])
    linear = xyz @ matrix.T
    assert linear.min() > 0
    return np.asarray([[encode(v) for v in row] for row in linear])


@pytest.mark.parametrize('sample', [[.6,.5,.4], [.35,.43,.51], [.08,.075,.07]])
def test_sample_matches_independent_luminance_neutralization(sample):
    from lumen.white_balance import sample_white_balance
    expected = neutral_gains(sample)
    actual = sample_white_balance(sample)
    np.testing.assert_allclose(actual, expected, rtol=2e-6, atol=2e-7)
    corrected = apply_grade(np.array([sample], np.float32),
                            Grade(white_balance_enabled=True, white_balance_gains=list(actual)))
    target = encode(sum(decode(v)*w for v,w in zip(sample,(.2126,.7152,.0722))))
    np.testing.assert_allclose(corrected, target, atol=3e-7, rtol=0)


@pytest.mark.parametrize('sample', [[0,.4,.4],[.02,.4,.4],[.98,.4,.4],[1,.4,.4],
                                    [-.1,.4,.4],[1.1,.4,.4],[float('nan'),.4,.4],
                                    [float('inf'),.4,.4],[.025,.9,.9],[.9,.025,.025]])
def test_picker_refuses_unsafe_samples(sample):
    from lumen.white_balance import sample_white_balance
    assert sample_white_balance(sample) is None


@pytest.mark.parametrize('value', [.0201,.08,.18,.5,.9799])
def test_neutral_is_exact_identity_and_invalid_mask_rejects(value):
    from lumen.white_balance import sample_white_balance
    assert tuple(sample_white_balance([value]*3)) == (1.,1.,1.)
    assert sample_white_balance([value]*3, valid=False) is None


@pytest.mark.parametrize('encoding', ['rec709','srgb','linear_rec709'])
def test_independent_xyz_chart_input_transfer_extended_range_and_lift_order(encoding):
    rgb = np.vstack([chart(), [[-.2,.03,1.3]]]).astype(np.float32)
    gains = [1.3,.87,1.17]
    lift = [.03,-.01,.02]
    expected = oracle(rgb, gains, encoding, exposure=.2, lift=lift)
    grade = Grade(input_encoding=encoding, white_balance_enabled=True,
                  white_balance_gains=gains, linear_exposure=.2, lift=lift)
    actual = apply_grade(rgb, grade)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=8e-7)
    assert actual[-1,0] < 0 and actual[-1,2] > 1


@pytest.mark.parametrize('mode', ['disabled','neutral','bypass','grade-disabled'])
def test_inactive_wb_bit_exact_with_full_16bit_ramp_and_signed_zero(mode):
    r = np.concatenate([np.arange(65536,dtype=np.float32)/65535,
                        np.array([-.0,-.3,1.8],np.float32)])
    rgb = np.stack([r,np.roll(r,9),r[::-1]],-1)
    grade = Grade(white_balance_enabled=mode!='disabled',
                  white_balance_gains=[1.,1.,1.] if mode=='neutral' else [1.2,.8,1.1],
                  enabled=mode!='grade-disabled')
    actual = apply_grade(rgb, grade, bypass=mode=='bypass')
    assert actual.tobytes() == rgb.tobytes()


def test_wb_after_normalized_restoration():
    rgb = chart().astype(np.float32)
    restored = rgb*.6+.1
    grade = Grade(white_balance_enabled=True,white_balance_gains=[1.3,.9,1.1])
    actual = apply_grade(rgb,grade,normalized_transform=lambda x: x*.6+.1)
    np.testing.assert_allclose(actual,oracle(restored,grade.white_balance_gains),rtol=0,atol=4e-7)


def make_source(tmp_path, grade):
    from lumen.media import probe
    from lumen.model import Project, Clip
    from test_contrast_pivot_render import ff
    codes = np.rint(chart()*65535).astype('<u2')
    pixels = np.repeat(np.repeat(codes[None],32,axis=0),32,axis=1)
    width = pixels.shape[1]
    planar = np.moveaxis(pixels[..., [1,2,0]],-1,0).astype('<u2').tobytes()
    path = tmp_path/'wb-xyz-chart.mkv'
    tags = ['-colorspace','rgb','-color_range','pc','-color_trc','bt709','-color_primaries','bt709']
    ff('-f','rawvideo','-pix_fmt','gbrp16le','-s',f'{width}x32','-r','6',*tags,
       '-i','pipe:0','-frames:v','3','-c:v','ffv1','-level','3','-pix_fmt','gbrp16le',
       *tags,path,data=planar*3)
    assert ff('-i',path,'-pix_fmt','gbrp16le','-f','rawvideo','pipe:1') == planar*3
    media = probe(path)
    project = Project(width=width,height=32,fps_num=6,media=[media],
                      clips=[Clip('wb-picture',media.id,0,0,0,3,grade=grade)])
    return project, codes.astype(float)/65535


def means(frame):
    return np.array([frame[8:24,i*32+8:i*32+24].mean((0,1)) for i in range(6)])


def test_actual_rgb16_prores_and_corrected_qt_preview(tmp_path):
    from lumen.media import ffmpeg_available
    if not ffmpeg_available():
        pytest.skip('FFmpeg/ffprobe required')
    from lumen.render import RenderEngine
    from lumen.preview_color import preview_frame_image
    from PySide6.QtWidgets import QApplication
    from test_contrast_pivot_render import ff
    from test_preview_color_render import decode_actual, full_rgb, independent_rgb
    grade = Grade(white_balance_enabled=True,white_balance_gains=[1.2,.92,1.15])
    project, source = make_source(tmp_path, grade)
    expected = np.clip(oracle(source,grade.white_balance_gains),0,1)
    app = QApplication.instance() or QApplication([])
    engine = RenderEngine(timeout=90)
    try:
        stage = engine._graded_segment(project.media[0],project.clips[0],project,None)
        raw = ff('-i',stage,'-pix_fmt','rgb48le','-f','rawvideo','pipe:1')
        frames = np.frombuffer(raw,'<u2').reshape(-1,32,project.width,3)/65535
        assert len(frames)==3
        for frame in frames:
            np.testing.assert_allclose(means(frame),expected,atol=2/65535,rtol=0)
        prores = engine.render(project,tmp_path/'white-balance.mov',export_profile='prores_422_hq_10bit')
        raw = ff('-i',prores,'-pix_fmt','yuv422p10le','-f','rawvideo','pipe:1')
        planes = np.frombuffer(raw,'<u2').reshape(3,-1)
        r,g,b = expected.T
        y = .2126*r+.7152*g+.0722*b
        expected_yuv = np.stack([64+876*y,512+896*(b-y)/(2*(1-.0722)),
                                512+896*(r-y)/(2*(1-.2126))],-1)
        n=project.width*32
        for frame in planes:
            yuv = [frame[:n].reshape(32,project.width),
                   frame[n:n+n//2].reshape(32,project.width//2),
                   frame[n+n//2:].reshape(32,project.width//2)]
            measured = np.array([[plane[8:24,(i*32+8)//(1 if c==0 else 2):
                                             (i*32+24)//(1 if c==0 else 2)].mean()
                                  for c,plane in enumerate(yuv)] for i in range(6)])
            np.testing.assert_allclose(measured,expected_yuv,atol=2,rtol=0)
        preview = engine.preview(project)
        decoded = decode_actual(app,preview,Fraction(6),3)
        ff_reference = independent_rgb(preview,project.width,32,matrix='bt709',full=False)
        for i,frame in decoded.items():
            corrected = full_rgb(preview_frame_image(frame))
            np.testing.assert_allclose(corrected,ff_reference[i],atol=2,rtol=0)
            np.testing.assert_allclose(means(corrected)/255,expected,atol=4/255,rtol=0)
    finally:
        engine.close()


def test_snapshot_wb_samples_precreative_and_hue_includes_wb(tmp_path):
    from lumen.media import ffmpeg_available
    if not ffmpeg_available():
        pytest.skip('FFmpeg/ffprobe required')
    from lumen.color_picker_source import capture_picker_snapshot
    grade = Grade(white_balance_enabled=True,white_balance_gains=[1.2,.9,1.1],
                  exposure=.1,contrast=1.1,contrast_pivot=.18)
    project,source = make_source(tmp_path,grade)
    sample = capture_picker_snapshot(project,project.clips[0].id,0,stage='white_balance')
    np.testing.assert_allclose(means(sample.frame),source,atol=2/65535,rtol=0)
    assert sample.stage=='white_balance'
    hue = capture_picker_snapshot(project,project.clips[0].id,0,stage='hue')
    expected = (oracle(source,grade.white_balance_gains)*2**.1-.18)*1.1+.18
    np.testing.assert_allclose(means(hue.frame),expected,atol=2/65535,rtol=0)


@pytest.mark.parametrize('sign', [-1,1])
def test_near_gray_roundoff_is_exact_but_real_small_cast_is_not(sign):
    from lumen.white_balance import sample_white_balance
    near = [.5+sign*1e-8,.5,.5]
    assert sample_white_balance(near)==(1.,1.,1.)
    cast = [.5+sign*1e-4,.5,.5]
    actual = sample_white_balance(cast)
    assert actual is not None and actual!=(1.,1.,1.)
    np.testing.assert_allclose(actual,neutral_gains(cast),rtol=0,atol=2e-14)


@pytest.mark.parametrize('boundary', [.25,4.])
def test_gain_safety_limits_from_independent_linear_luminance_equation(boundary):
    from lumen.white_balance import sample_white_balance
    # Set R=.1 and solve equal G/B so Y/R is just inside/outside the limit.
    # Near .25, green/blue gains exceed 4, so instead use B as limiting
    # channel: its low .0722 luma coefficient keeps all other gains legal.
    coefficient = .0722 if boundary==.25 else .2126
    channel = 2 if boundary==.25 else 0
    for offset, safe in [(-1e-5,boundary==4.),(1e-5,boundary==.25)]:
        target = boundary+offset
        others = .1*(target-coefficient)/(1-coefficient)
        linear = [others]*3
        linear[channel]=.1
        sample = [encode(v) for v in linear]
        assert min(sample)>.02 and max(sample)<.98
        gains = neutral_gains(sample)
        assert abs(gains[channel]-target)<1e-12
        actual = sample_white_balance(sample)
        if safe:
            np.testing.assert_allclose(actual,gains,rtol=0,atol=1e-12)
        else:
            assert actual is None


@pytest.mark.parametrize('sample', [[],[.4,.4],[[.4,.4,.4]],['.4','.4','.4'],
                                    [True,False,True],None])
def test_malformed_reference_fails_closed(sample):
    from lumen.white_balance import sample_white_balance
    assert sample_white_balance(sample) is None

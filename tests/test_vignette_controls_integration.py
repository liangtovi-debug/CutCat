"""Synthetic independent acceptance for radius/feather in real delivery graphs."""
from copy import deepcopy
import math
from pathlib import Path
import numpy as np
import pytest
from lumen.model import ClipEffect, Grade, Caption, CaptionStyle, OverlayKeyframe
from lumen.render import RenderEngine
from lumen.clip_transform import ClipTransform
from lumen.media import ffmpeg_available
from test_color_highbit_reference import project, rgb_source, yuv422, video_metadata, rec709_yuv10, ff
from test_highbit_effects import compiled_pixels
from test_highbit_vignette_integration import linear_exposure
from test_highbit_caption_reference import latin_font
from test_overlay_render import sticker

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def oracle(image, amount, radius, feather):
    h, w, _ = image.shape
    out = np.empty_like(image)
    for y in range(h):
        for x in range(w):
            distance = math.sqrt(int(x-w/2)**2 + int(y-h/2)**2) / (math.sqrt(w*w+h*h)/2)
            q = min(1, (distance/radius)**(1/feather))
            gain = math.cos(amount*math.pi/2*q)**4
            for c in range(3):
                out[y,x,c] = int(float(image[y,x,c])*gain)
    return out


@pytest.fixture
def engine():
    e = RenderEngine(timeout=90)
    try: yield e
    finally: e.close()


def fx(radius=.75, feather=1.5, amount=.65):
    return ClipEffect('shape', 'vignette', amount, radius=radius, feather=feather)


@pytest.mark.parametrize('radius,feather', [(.25,.25), (.75,1.5), (2,2)])
def test_real_prores_matches_independent_shaped_vignette(tmp_path, engine, radius, feather):
    h,w=64,256
    image=np.broadcast_to((14001+np.arange(w,dtype=np.uint16)*120)[None,:,None],(h,w,3)).copy()
    p=project(rgb_source(tmp_path,image));p.clips[0].effects=[fx(radius,feather)]
    before=deepcopy(p.to_dict())
    output=engine.render(p,tmp_path/'shaped.mov',export_profile='prores_422_hq_10bit')
    info=video_metadata(output)
    assert (info['codec_name'],info['pix_fmt'])==('prores','yuv422p10le')
    y,cb,cr=yuv422(output,w,h)
    expected=64+876*oracle(image,.65,radius,feather)[...,0].astype(float)/65535
    # The smallest radius/feather has a sharp clamp shoulder; lossy ProRes
    # can ring by up to three luma codes there. Raw RGB oracle tests retain
    # the separate one-code precision bound, and mean error stays sub-code.
    np.testing.assert_allclose(y[0,4:-4,4:-4],expected[4:-4,4:-4],atol=3,rtol=0)
    assert np.mean(abs(y[0,4:-4,4:-4]-expected[4:-4,4:-4])) < .65
    np.testing.assert_allclose(cb,512,atol=1,rtol=0)
    np.testing.assert_allclose(cr,512,atol=1,rtol=0)
    assert p.to_dict()==before


def test_nondefault_shape_native_precision_survives_real_prores(tmp_path,engine):
    h,w=64,2048
    image=np.broadcast_to(np.rint(np.linspace(0,65535,w))[None,:,None],(h,w,3)).astype('<u2').copy()
    p=project(rgb_source(tmp_path,image));p.clips[0].effects=[fx(1.5,.8,.2)]
    output=engine.render(p,tmp_path/'precision.mov',export_profile='prores_422_hq_10bit')
    y,_,_=yuv422(output,w,h)
    expected=64+876*oracle(image,.2,1.5,.8)[...,0].astype(float)/65535
    error=np.mean(abs(y[0,8:24]-expected[8:24]))
    coarse=(np.rint(image.astype(float)/257)*257).astype('<u2')
    q=project(rgb_source(tmp_path,coarse,name='coarse'));q.clips[0].effects=[fx(1.5,.8,.2)]
    control=engine.render(q,tmp_path/'coarse.mov',export_profile='prores_422_hq_10bit')
    cy,_,_=yuv422(control,w,h)
    coarse_error=np.mean(abs(cy[0,8:24]-expected[8:24]))
    assert error < .65
    assert error < coarse_error*.8
    assert np.unique(y[0,16]).size>650


def test_shape_is_after_grade_before_transform_and_preserves_grade_cache(tmp_path,engine):
    h,w=64,128
    row,col=np.indices((h,w));image=np.stack([11001+col*90,15003+row*80,21007+col*30],-1).astype('<u2')
    p=project(rgb_source(tmp_path,image),grade=Grade(linear_exposure=.5))
    p.clips[0].effects=[fx()];p.clips[0].transform=ClipTransform(x=12)
    actual=compiled_pixels(engine,p)
    expected=rec709_yuv10(oracle(linear_exposure(image,.5),.65,.75,1.5).astype(float)/65535)[...,0]
    np.testing.assert_allclose(actual[0][0,8:-8,20:-8],expected[8:-8,8:-20],atol=2,rtol=0)
    cache=Path(engine._cache_dir.name);files=sorted(cache.glob('*.mkv'))
    snapshot=[(f,f.stat().st_mtime_ns,f.read_bytes()) for f in files]
    p.clips[0].effects[0].radius=1.7
    changed=compiled_pixels(engine,p)
    assert np.mean(abs(actual[0].astype(float)-changed[0]))>20
    p.clips[0].effects[0].feather=.3
    changed2=compiled_pixels(engine,p)
    assert np.mean(abs(changed2[0].astype(float)-changed[0]))>20
    assert sorted(cache.glob('*.mkv'))==files
    for f,t,b in snapshot: assert f.stat().st_mtime_ns==t and f.read_bytes()==b


def decode8(path,w,h):
    data=ff('-i',path,'-an','-pix_fmt','rgb24','-f','rawvideo','pipe:1')
    return np.frombuffer(data,np.uint8).reshape(-1,h,w,3)


@pytest.mark.parametrize('preview',[False,True])
def test_h264_and_rendered_preview_apply_shape_and_bypass_exactly(tmp_path,engine,preview):
    h,w=64,128
    image=np.full((h,w,3),35000,dtype='<u2')
    p=project(rgb_source(tmp_path,image));baseline=engine.render(p,tmp_path/'base.mp4',preview=preview)
    baseline=decode8(baseline,w,h)
    p.clips[0].effects=[fx()]
    active=decode8(engine.render(p,tmp_path/'active.mp4',preview=preview),w,h)
    assert active[0,0,0,0]+20<baseline[0,0,0,0]
    assert active[0,h//2,w//2,0]>active[0,0,0,0]+20
    p.clips[0].effects[0].radius=2
    large=decode8(engine.render(p,tmp_path/'large.mp4',preview=preview),w,h)
    assert np.mean(large.astype(float)-active)>10
    p.clips[0].effects[0].amount=0
    bypass=decode8(engine.render(p,tmp_path/'bypass.mp4',preview=preview),w,h)
    np.testing.assert_array_equal(baseline,bypass)


def test_caption_and_alpha_sticker_remain_unaffected_by_radius_feather(tmp_path, engine, latin_font):
    width, height = 384, 160
    image = np.full((height, width, 3), 28001, dtype='<u2')
    p = project(rgb_source(tmp_path, image), frames=3)
    p.clips[0].effects = [fx(.65, 1.7, .8)]
    baseline = compiled_pixels(engine, p)
    art, _ = sticker(tmp_path, start=1, duration=1, color=(255, 0, 0, 128),
                     keyframes=[OverlayKeyframe(0, 16, 24, 2, 1)])
    p.overlays = [art]
    p.caption_style = CaptionStyle(font_family=latin_font, font_size=32, margin_bottom=12,
                                   outline_width=0, safe_margin_percent=5)
    p.captions = [Caption('caption', 1, 1, 'SHARP')]
    before = deepcopy(p.to_dict())
    actual = compiled_pixels(engine, p)
    for plain, decorated in zip(baseline, actual):
        np.testing.assert_array_equal(decorated[[0, 2]], plain[[0, 2]])
        np.testing.assert_array_equal(decorated[1, :20], plain[1, :20])
    expected_red = rec709_yuv10([1, 0, 0])
    for index, (plain, decorated) in enumerate(zip(baseline, actual)):
        left, right = (16, 48) if index == 0 else (8, 24)
        expected = plain[1, 24:56, left:right] * (1 - 128 / 255) + expected_red[index] * 128 / 255
        np.testing.assert_allclose(decorated[1, 24:56, left:right], expected, atol=4, rtol=0)
    np.testing.assert_array_equal(actual[0][1, 24:56, 15], baseline[0][1, 24:56, 15])
    np.testing.assert_array_equal(actual[0][1, 24:56, 48], baseline[0][1, 24:56, 48])
    assert np.count_nonzero(actual[0][1, 96:] != baseline[0][1, 96:]) > 150
    # Independently bake just the picture. Identical later artwork proves
    # vignette never darkens the caption or the half-alpha red sticker itself.
    baked = project(rgb_source(tmp_path, oracle(image, .8, .65, 1.7), name='baked'), frames=3)
    baked.overlays, baked.captions = deepcopy(p.overlays), deepcopy(p.captions)
    baked.caption_style = deepcopy(p.caption_style)
    reference = compiled_pixels(engine, baked)
    for decorated, independent in zip(actual, reference):
        np.testing.assert_allclose(decorated, independent, atol=1, rtol=0)
    assert p.to_dict() == before


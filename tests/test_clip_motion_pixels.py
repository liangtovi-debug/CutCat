"""Independent moving geometric-alpha oracle and actual frame-count feasibility."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
import subprocess

import numpy as np
import pytest
from lumen.clip_motion import ClipMotionKeyframe as K
from lumen.clip_motion_render import motion_expression
from lumen.clip_composite import ClipComposite,clip_composite_filters,verify_clip_motion_support
from lumen.clip_transform import ClipTransform
from lumen.media import ffmpeg_available,ffmpeg_binary
from test_clip_composite_pixels import image,underlay,pack_planes,unpack_planes,independent_blend


def dynamic_pixels(tmp_path,keys,high,*,transform=None,transparent=True,count=9):
    w,h=64,48;rgb=image();planes=underlay(high)
    path=tmp_path/'motion.gbrp16le';path.write_bytes(np.asarray(rgb[:,:,[1,2,0]].transpose(2,0,1),'<u2').tobytes()*count)
    main,layer,over=clip_composite_filters(ClipComposite(transparent),transform or ClipTransform(),w,h,high_bit=high,motion_keys=keys)
    graph=f'[0:v]{main}[main];[1:v]{layer}[layer];[main][layer]{over}[out]'
    # 128-key graphs may exceed one OS argv limit; actual renderer also uses file loading.
    graph_path=tmp_path/'motion.ffgraph';graph_path.write_text(graph)
    fmt='yuv422p10le' if high else 'yuv420p';packed=pack_planes(planes,high)
    command=[ffmpeg_binary(),'-v','error','-nostdin','-noauto_conversion_filters','-filter_complex_threads','1',
        '-f','rawvideo','-pix_fmt',fmt,'-s','64x48','-r','30000/1001','-i','pipe:0',
        '-f','rawvideo','-pix_fmt','gbrp16le','-s','64x48','-r','30000/1001','-i',str(path),
        '-/filter_complex',str(graph_path),'-map','[out]','-frames:v',str(count),'-threads','1','-pix_fmt',fmt,'-f','rawvideo','pipe:1']
    result=subprocess.run(command,input=packed*count,capture_output=True,timeout=60)
    assert result.returncode==0,result.stderr.decode()
    assert len(result.stdout)==len(packed)*count
    return [unpack_planes(result.stdout[i*len(packed):(i+1)*len(packed)],w,h,high) for i in range(count)],rgb,planes


@pytest.mark.skipif(not ffmpeg_available(),reason='FFmpeg required')
@pytest.mark.parametrize('high',[False,True])
@pytest.mark.parametrize('transparent',[False,True])
@pytest.mark.parametrize('transform',[ClipTransform(),ClipTransform(rotation=90,mirror_x=True),ClipTransform(rotation=27,mirror_y=True),ClipTransform(rotation=-90,mirror_x=True),ClipTransform(rotation=-31),ClipTransform(rotation=180)])
def test_dynamic_geometry_and_alpha_same_local_frame(tmp_path,high,transparent,transform):
    keys=[K(0,x=-8,y=4,scale=.5,opacity=0),K(8,x=8,y=-4,scale=.75,opacity=1)]
    outputs,rgb,planes=dynamic_pixels(tmp_path,keys,high,transform=transform,transparent=transparent)
    for k,actual in enumerate(outputs):
        settings=ClipComposite(transparent,k/8)
        geometry=replace(transform,x=2*k-8,y=4-k,scale=.5+k/32)
        expected,clean=independent_blend(rgb,planes,settings,geometry,high)
        for a,b,c,p in zip(actual,expected,clean,planes):
            np.testing.assert_allclose(a,b,atol=3,rtol=0)
            np.testing.assert_array_equal(a[c],p[c])


@pytest.mark.parametrize('high',[False,True])
def test_hold_exact_endpoint_hidden_and_offscreen(tmp_path,high):
    keys=[K(-3,x=-10,scale=.5,opacity=.25,interpolation='hold'),K(3,x=16384,opacity=1,interpolation='hold'),K(6,scale=1,opacity=1),K(20,x=0,scale=1,opacity=1)]
    outputs,rgb,planes=dynamic_pixels(tmp_path,keys,high)
    for k,actual in enumerate(outputs):
        transform=ClipTransform(x=-10,scale=.5) if k<3 else ClipTransform(x=16384) if k<6 else ClipTransform()
        composite=ClipComposite(True,.25 if k<3 else 1)
        expected,clean=independent_blend(rgb,planes,composite,transform,high)
        for a,b,c,p in zip(actual,expected,clean,planes):
            np.testing.assert_allclose(a,b,atol=3,rtol=0);np.testing.assert_array_equal(a[c],p[c])


@pytest.mark.parametrize('high',[False,True])
def test_max_keys_balanced_parser_and_extreme_signed_frames(tmp_path,high):
    keys=[K(-2**53+1,x=1),*[K(i,x=(i%7)-3,scale=.5+(i%3)/10,opacity=(i%5)/4) for i in range(126)],K(2**53-1,x=4)]
    actual,rgb,planes=dynamic_pixels(tmp_path,keys,high,count=2)
    for k,a in enumerate(actual):
        expected,clean=independent_blend(rgb,planes,ClipComposite(True,(k%5)/4),ClipTransform(x=(k%7)-3,scale=.5+(k%3)/10),high)
        for got,want,c,p in zip(a,expected,clean,planes):
            np.testing.assert_allclose(got,want,atol=3,rtol=0);np.testing.assert_array_equal(got[c],p[c])


@pytest.mark.parametrize('high',[False,True])
def test_varying_runtime_capability_probe(high): verify_clip_motion_support(high,30)


def test_empty_keys_exact_existing_graph_and_trusted_values_only():
    for high in (False,True):
        for c in (ClipComposite(),ClipComposite(True,.5)):
            t=ClipTransform(x=.5,rotation=31)
            assert clip_composite_filters(c,t,64,48,high_bit=high)==clip_composite_filters(c,t,64,48,high_bit=high,motion_keys=[])
    with pytest.raises(ValueError):motion_expression([K()],"x);system('evil')")
    with pytest.raises(ValueError):motion_expression([], 'x')


@pytest.mark.parametrize('high',[False,True])
def test_dynamic_probe_rejects_first_frame_only_regression(high,monkeypatch):
    import lumen.clip_motion_render as motion
    from lumen.clip_composite import CLIP_MOTION_ERROR
    monkeypatch.setattr(motion,'motion_expression',lambda keys,field: str({'x':-4,'y':0,'scale':.5,'opacity':0}[field]))
    with pytest.raises(RuntimeError,match='cannot animate pictures'):
        verify_clip_motion_support(high,30)


def test_dynamic_probe_cancel_and_deadline_are_bounded():
    import threading
    class Cancelled(RuntimeError):pass
    event=threading.Event();event.set()
    with pytest.raises(Cancelled,match='cancelled'):
        verify_clip_motion_support(False,30,event,cancelled_type=Cancelled)
    with pytest.raises(RuntimeError,match='timed out'):
        verify_clip_motion_support(False,0)

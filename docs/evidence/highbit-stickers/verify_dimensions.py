"""Read-only independent review: actual BGRA cache and exact production filters."""
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import numpy as np
from lumen import highbit_stickers as high
from lumen.model import Project, OverlayKeyframe
from lumen.render import RenderEngine
from lumen.overlay_render import compose_frame
from test_highbit_sticker_reference import array_sticker, direct_sticker_pixels, compiled_pixels
from test_color_highbit_reference import video_metadata, yuv422
from test_overlay_render import rgba

assert high._OVERLAY_FILTER.count('pad=iw+2:ih:0:0:color=black') == 1
assert high._OVERLAY_FILTER.count('crop=iw-2:ih:0:0') == 1
rng = np.random.default_rng(999)
records = []
widths = [2,4,6,8,10,14,16,18,30,32,34,62,64,66,98,130,638,1922,16384]
sizes = [(w, 2 if w == 2 else 4 if w < 64 else 12) for w in widths] + [(2,16),(2,16384)]
with TemporaryDirectory(prefix='sticker-final-review-') as tmp:
    for w,h in sizes:
        d = Path(tmp) / f'{w}x{h}'; d.mkdir()
        aw,ah = min(w,2048),min(h,2048)
        a = rng.integers(0,256,(ah,aw,4),dtype=np.uint8)
        a[...,3] = rng.choice([0,64,128,192,255],(ah,aw))
        a[:,-2:] = 0
        a[::2,-2] = [255,0,0,255]
        a[1::2,-1] = [255,0,0,255]
        o,asset = array_sticker(d,a,duration=2,keyframes=[
            OverlayKeyframe(0,w-aw,h-ah,1,0), OverlayKeyframe(1,w-aw,h-ah,1,1)])
        p = Project(width=w,height=h,overlays=[o]);p.validate()
        s = [rng.integers(0,1024,(h,w),dtype=np.uint16),
             rng.integers(0,1024,(h,w//2),dtype=np.uint16),
             rng.integers(0,1024,(h,w//2),dtype=np.uint16)]
        art = rgba(compose_frame(p,1,{o.id:asset})).astype(float)
        rgb,alpha = art[...,:3]/255,art[...,3]/255
        yy = rgb @ np.array([.2126,.7152,.0722])
        fg = [64+876*yy,512+896*(rgb[...,2]-yy)/(2*(1-.0722)),
              512+896*(rgb[...,0]-yy)/(2*(1-.2126))]
        expected = []
        for i,(bg,top) in enumerate(zip(s,fg)):
            z = (bg if i == 0 else np.repeat(bg,2,axis=1))*(1-alpha)+top*alpha
            expected.append(z if i == 0 else (z[:,::2]+z[:,1::2])/2)
        e = RenderEngine(timeout=60)
        try:
            actual = direct_sticker_pixels(e,p,s)
            errs = [float(np.max(np.abs(x[1].astype(float)-y))) for x,y in zip(actual,expected)]
            for x,y in zip(actual,s):
                np.testing.assert_array_equal(x[0],y)
            for i,(x,y) in enumerate(zip(actual,s)):
                untouched = alpha == 0 if i == 0 else (alpha[:,::2]+alpha[:,1::2]) == 0
                np.testing.assert_array_equal(x[1][untouched],y[untouched])
            assert max(errs) < 3,(w,h,errs)
            record = {'width':w,'height':h,'max_error_yuv':errs,
                      'transparent_frame_exact':True,'outside_alpha_exact':True}
            if w == 2 and h in (2,16):
                raw = compiled_pixels(e,p)
                np.testing.assert_allclose(raw[1][1],462,atol=2,rtol=0)
                np.testing.assert_allclose(raw[2][1],737,atol=2,rtol=0)
                output = e.render(p,d/'tiny.mov',export_profile='prores_422_hq_10bit')
                meta = video_metadata(output)
                dec = yuv422(output,w,h)
                assert meta['width'] == w and meta['height'] == h
                assert meta['pix_fmt'] == 'yuv422p10le' and meta['profile'] == 'HQ'
                for got,ref in zip(dec,raw):
                    np.testing.assert_allclose(got,ref,atol=4,rtol=0)
                record['real_prores_verified'] = True
            records.append(record)
            print(json.dumps(record),flush=True)
        finally:
            e.close()
result = {'status':'pass','source_sha256':hashlib.sha256(Path(high.__file__).read_bytes()).hexdigest(),
          'composition_filter':high._OVERLAY_FILTER,'cases':records,
          'max_error':max(max(r['max_error_yuv']) for r in records),
          'limitations':'Synthetic local fixtures; no native Windows/macOS validation; no publication.'}
Path(__file__).with_name('dimension_review.json').write_text(json.dumps(result,indent=2)+'\n')
print('ALL PASS',len(records),'dimensions, worst_error',result['max_error'])

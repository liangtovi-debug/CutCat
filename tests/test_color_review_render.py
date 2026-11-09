"""Independent high-bit-depth YUV ingress check (in addition to RGB16 fixtures)."""
import subprocess
import numpy as np
import pytest
from lumen import color
from lumen.media import probe, ffmpeg_binary, ffmpeg_available
from lumen.model import Project, Clip, Grade
from lumen.render import RenderEngine

pytestmark = pytest.mark.skipif(not ffmpeg_available(),reason='FFmpeg required')


def test_tagged_limited_yuv10_preserves_precision_before_grade(tmp_path,monkeypatch):
    width,height=1024,4
    y=np.broadcast_to(np.rint(np.linspace(64,940,width))[None,:],(height,width)).astype('<u2')
    uv=np.full((height,width),512,dtype='<u2')
    source=tmp_path/'rec709-yuv10.mkv'
    subprocess.run([ffmpeg_binary(),'-v','error','-f','rawvideo','-pixel_format','yuv444p10le','-video_size',f'{width}x{height}','-framerate','30','-i','pipe:0','-frames:v','3','-c:v','ffv1','-pix_fmt','yuv444p10le','-colorspace','bt709','-color_trc','bt709','-color_primaries','bt709','-color_range','tv',str(source)],input=(y.tobytes()+uv.tobytes()+uv.tobytes())*3,capture_output=True,check=True)
    media=probe(source)
    project=Project(width=width,height=height,media=[media],clips=[Clip('c',media.id,0,0,0,1,grade=Grade(linear_exposure=.1))])
    captured=[];evaluate=color.apply_grade
    def capture(rgb,grade):
        captured.append(rgb.copy());return evaluate(rgb,grade)
    monkeypatch.setattr(color,'apply_grade',capture)
    engine=RenderEngine()
    try:engine.render(project,tmp_path/'delivery.mp4')
    finally:engine.close()
    assert len(captured)==1
    rgb=captured[0]
    assert rgb.dtype==np.float32
    assert np.unique(rgb[0,:,0]).size>850
    assert np.max(np.abs(rgb[:,0,:]))<.002
    assert np.min(rgb[:,-1,:])>.998

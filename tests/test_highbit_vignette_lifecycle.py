"""Actual FFmpeg cancellation plus unsupported export geometry guards."""
import threading
import time
from pathlib import Path
import numpy as np
import pytest
from lumen import highbit_effects as high
from lumen.model import ClipEffect
from lumen.render import RenderEngine, RenderError, RenderCancelled
from test_color_highbit_reference import rgb_source, project


@pytest.mark.parametrize('width,height', [(63,48),(64,47),(63,47),(1,48),(64,1)])
def test_odd_export_canvas_rejected_before_probe_or_cache(tmp_path, monkeypatch, width, height):
    p = project(rgb_source(tmp_path,np.full((48,64,3),24001,dtype='<u2')))
    p.width, p.height = width, height
    p.clips[0].effects = [ClipEffect('v','vignette',.65)]
    engine = RenderEngine(timeout=30)
    def forbidden(*a, **k): pytest.fail('unsupported export geometry must fail before effects/cache')
    monkeypatch.setattr(high,'ensure_highbit_effects',forbidden)
    monkeypatch.setattr(engine,'_graded_segment',forbidden)
    try:
        with pytest.raises(RenderError,match='even width and height'):
            engine.render(p,tmp_path/'bad.mov',export_profile='prores_422_hq_10bit')
        assert not (tmp_path/'bad.mov').exists()
        assert not list(tmp_path.glob('.lumen-render-*'))
        assert not list(Path(engine._cache_dir.name).glob('*.mkv'))
    finally: engine.close()


def test_real_running_vignette_encode_cancels_promptly_and_reaps(tmp_path,monkeypatch):
    import lumen.render as render
    p = project(rgb_source(tmp_path,np.full((128,192,3),24001,dtype='<u2'),frames=90),frames=90)
    p.clips[0].effects = [ClipEffect('v','vignette',.65)]
    engine = RenderEngine(timeout=30); cancel=threading.Event(); processes=[]; timers=[]; moments=[]
    original=render.subprocess.Popen
    def launch(command,*args,**kwargs):
        process=original(command,*args,**kwargs)
        if 'prores_ks' in command and '-progress' in command:
            processes.append(process)
            def interrupt(): moments.append(time.monotonic()); cancel.set()
            timer=threading.Timer(.1,interrupt);timers.append(timer);timer.start()
        return process
    monkeypatch.setattr(render.subprocess,'Popen',launch)
    try:
        with pytest.raises(RenderCancelled):
            engine.render(p,tmp_path/'cancel.mov',cancel=cancel,export_profile='prores_422_hq_10bit')
        assert len(processes)==1 and processes[0].poll() is not None
        assert moments and time.monotonic()-moments[0]<4
        assert not (tmp_path/'cancel.mov').exists()
        assert not list(tmp_path.glob('.lumen-render-*'))
        assert not list(Path(engine._cache_dir.name).glob('*.partial*'))
    finally:
        for timer in timers: timer.cancel();timer.join()
        engine.close()

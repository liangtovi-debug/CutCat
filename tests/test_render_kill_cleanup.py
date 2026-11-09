import subprocess, sys
from pathlib import Path
import pytest
from test_clip_motion_render import sources, engine, project
from lumen.render import RenderError


def test_forced_kill_before_progress_delete(sources, engine, tmp_path, monkeypatch):
    original = subprocess.Popen
    unlink = Path.unlink
    calls = []
    children = []
    progress_paths = []
    class Process:
        def __init__(self, child): self.child = child
        def poll(self): return self.child.poll()
        def terminate(self): calls.append('terminate')
        def wait(self, timeout=None):
            calls.append(('wait',timeout))
            if timeout is not None:
                raise subprocess.TimeoutExpired('review-child', timeout)
            return self.child.wait()
        def kill(self):
            calls.append('kill')
            return self.child.kill()
    def start(command,*args,**kwargs):
        if '-progress' not in command:
            return original(command,*args,**kwargs)
        progress = command[command.index('-progress')+1]
        progress_paths.append(Path(progress))
        child = original([sys.executable,'-c','import time; time.sleep(30)'],*args,**kwargs)
        children.append(child)
        engine.timeout=0
        return Process(child)
    def checked_unlink(path,*args,**kwargs):
        if path in progress_paths:
            assert children and all(child.poll() is not None for child in children)
            calls.append('unlink-progress')
        return unlink(path,*args,**kwargs)
    monkeypatch.setattr(subprocess,'Popen',start)
    monkeypatch.setattr(Path,'unlink',checked_unlink)
    target=tmp_path/'review-timeout.mp4'
    try:
        with pytest.raises(RenderError,match='Render timed out'):
            engine.render(project(sources),target)
        assert calls == ['terminate',('wait',3),'kill',('wait',None),'unlink-progress']
        assert all(child.poll() is not None for child in children)
        assert not target.exists() and not list(tmp_path.glob('.lumen-render-*'))
        assert all(not p.exists() for p in progress_paths)
    finally:
        for child in children:
            if child.poll() is None: child.kill()
            child.wait()

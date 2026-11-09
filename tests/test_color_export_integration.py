"""MainWindow routing keeps chooser, file naming and render snapshot consistent."""
import threading
from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog

from test_ui import app, window, populate


@pytest.mark.parametrize('profile,suffix', [('h264_8bit','.mp4'), ('prores_422_hq_10bit','.mov')])
def test_export_routes_profile_suffix_and_immutable_snapshot(window, tmp_path, monkeypatch, profile, suffix):
    w = window; populate(w, tmp_path)
    expected_grade = w.project.clips[0].grade.exposure
    monkeypatch.setattr('lumen.ui.choose_export_profile', lambda *_: profile)
    dialogs=[]
    def filename(*args):
        dialogs.append(args)
        return str(tmp_path/'new-export'), ''
    monkeypatch.setattr(QFileDialog,'getSaveFileName',filename)
    jobs=[]
    monkeypatch.setattr(w,'start_job',lambda *args: jobs.append(args))
    received=[]
    class Engine:
        def render(self, project, path, **kwargs):
            received.append((project,path,kwargs))
            return Path(path)
        def close(self): pass
    monkeypatch.setattr('lumen.ui.RenderEngine',Engine)
    w.export_dialog()
    assert len(dialogs)==len(jobs)==1
    assert dialogs[0][2].endswith(suffix)
    assert '*'+suffix in dialogs[0][3]
    w.project.clips[0].grade.exposure = expected_grade + 1
    result = jobs[0][1](threading.Event(),lambda _:None)
    assert result.suffix == suffix
    assert received[0][2]['export_profile']==profile
    assert received[0][0] is not w.project
    assert received[0][0].clips[0].grade.exposure == expected_grade


def test_cancel_profile_never_opens_filename_or_starts_job(window,tmp_path,monkeypatch):
    w=window;populate(w,tmp_path)
    revision=w.revision
    monkeypatch.setattr('lumen.ui.choose_export_profile',lambda *_:None)
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *_:pytest.fail('Cancelled profile cannot choose filename'))
    monkeypatch.setattr(w,'start_job',lambda *_:pytest.fail('Cancelled profile cannot start export'))
    w.export_dialog()
    assert w.revision==revision


def test_cancel_filename_never_starts_job(window,tmp_path,monkeypatch):
    w=window;populate(w,tmp_path)
    monkeypatch.setattr('lumen.ui.choose_export_profile',lambda *_:'prores_422_hq_10bit')
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *_:('',''))
    monkeypatch.setattr(w,'start_job',lambda *_:pytest.fail('Cancelled filename cannot start export'))
    w.export_dialog()

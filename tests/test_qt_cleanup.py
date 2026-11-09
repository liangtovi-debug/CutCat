"""Bounded clean-process-exit regression for test-only Qt ownership cleanup."""
import os
from pathlib import Path
import subprocess
import sys


def test_repeated_media_windows_release_and_process_exits():
    root = Path(__file__).resolve().parents[1]
    code = r'''
import tempfile
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QSettings, QThread
from shiboken6 import isValid
from lumen.ui import MainWindow
from scripts.qt_test_cleanup import cleanup_test_windows
app=QApplication([])
shared=QWidget()
baseline=list(app.topLevelWidgets())
with tempfile.TemporaryDirectory() as folder:
    for i in range(12):
        w=MainWindow(workspace_settings=QSettings(folder+f'/{i}.ini',QSettings.IniFormat))
        w.dirty=False; w.closing=True
        assert w.close()
        app.processEvents()
        assert isValid(w)  # close alone deliberately supports reopening.
        cleanup_test_windows(app,baseline)
        assert not isValid(w)
        assert isValid(shared)
        assert len(app.topLevelWidgets()) == len(baseline)
shared.deleteLater()
cleanup_test_windows(app)
print('CLEAN_EXIT',flush=True)
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=root,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
        capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'CLEAN_EXIT' in result.stdout


def test_worker_leak_fails_but_completed_queued_job_cleans_cache():
    root = Path(__file__).resolve().parents[1]
    code = r'''
import tempfile,threading
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings
from shiboken6 import isValid
from lumen.ui import MainWindow
from scripts.qt_test_cleanup import cleanup_test_windows
app=QApplication([])
with tempfile.TemporaryDirectory() as folder:
    w=MainWindow(workspace_settings=QSettings(folder+'/one.ini',QSettings.IniFormat))
    entered=threading.Event()
    def work(cancel,progress):
        entered.set();cancel.wait(5)
    job=w.start_job('test',work,lambda _:None,'preview')
    assert entered.wait(2)
    cache=Path(w.cache)
    try:
        cleanup_test_windows(app)
    except AssertionError as exc:
        assert 'unexpected live worker' in str(exc), str(exc)
    else:
        raise AssertionError('Live worker leak was masked')
    assert not isValid(w) and not cache.exists()
    w=MainWindow(workspace_settings=QSettings(folder+'/two.ini',QSettings.IniFormat))
    job=w.start_job('done',lambda cancel,progress:None,lambda _:None,'preview')
    assert job.wait(2000) and job in w.jobs
    cache=Path(w.cache)
    cleanup_test_windows(app)
    assert not isValid(w) and not cache.exists()
    assert not app.topLevelWidgets()
print('CLEAN_WORKERS',flush=True)
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=root,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'CLEAN_WORKERS' in result.stdout


def test_noncooperative_worker_owner_is_retained_until_explicit_release():
    root = Path(__file__).resolve().parents[1]
    code = r'''
import threading,gc,time
from PySide6.QtWidgets import QApplication,QWidget
from PySide6.QtCore import QThread
from shiboken6 import isValid
from scripts.qt_test_cleanup import cleanup_test_windows,_retained_unsafe_roots
app=QApplication([])
release=threading.Event();entered=threading.Event()
class Worker(QThread):
    def run(self):
        entered.set();release.wait(5)
owner=QWidget();thread=Worker(owner);thread.start();assert entered.wait(2)
start=time.monotonic()
try:
    cleanup_test_windows(app,timeout_ms=10)
except AssertionError as exc:
    assert 'owner retained' in str(exc)
else:
    raise AssertionError('Noncooperative worker was ignored')
assert time.monotonic()-start < 2
assert owner in _retained_unsafe_roots and isValid(owner) and thread.isRunning()
del owner;gc.collect()
assert isValid(_retained_unsafe_roots[0])
release.set();assert thread.wait(2000)
cleanup_test_windows(app)
assert not _retained_unsafe_roots and not app.topLevelWidgets()
print('RETAINED_AND_RELEASED',flush=True)
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=root,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'RETAINED_AND_RELEASED' in result.stdout

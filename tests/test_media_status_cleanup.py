"""Strict test cleanup distinguishes only a requested background scan shutdown."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('case', ['uncanceled_scanner', 'canceled_foreground', 'blocked_scanner', 'drained_scanner'])
def test_status_shutdown_cleanup_classification(case):
    code = r'''
import sys, threading
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QThread
from shiboken6 import isValid
from scripts.qt_test_cleanup import cleanup_test_windows, _retained_unsafe_roots
case=sys.argv[1]
app=QApplication([]); release=threading.Event(); entered=threading.Event()
class Worker(QThread):
    def __init__(self,parent):
        super().__init__(parent); self.cancel=threading.Event()
    def run(self):
        entered.set()
        if case == 'uncanceled_scanner': self.cancel.wait(3)
        else: release.wait(3)
owner=QWidget(); worker=Worker(owner)
owner._media_status_job = None if case == 'canceled_foreground' else worker
owner.closing = case != 'uncanceled_scanner'
owner._media_status_close_pending = case != 'uncanceled_scanner'
owner.cancel_jobs = lambda: worker.cancel.set()
if case != 'uncanceled_scanner': worker.cancel.set()
worker.start(); assert entered.wait(2)
if case in ('drained_scanner','canceled_foreground'):
    threading.Timer(.05,release.set).start()
try:
    cleanup_test_windows(app,timeout_ms=10 if case == 'blocked_scanner' else 1000)
except AssertionError as error:
    if case == 'blocked_scanner':
        assert 'owner retained' in str(error)
        assert isValid(owner) and worker.isRunning() and owner in _retained_unsafe_roots
        release.set(); assert worker.wait(2000)
        cleanup_test_windows(app)
    else:
        assert case in ('uncanceled_scanner','canceled_foreground')
        assert 'unexpected live worker' in str(error)
else:
    assert case == 'drained_scanner'
assert not isValid(owner)
print('CLEANUP_CASE_PASSED')
'''
    result = subprocess.run([sys.executable, '-c', code, case],
        cwd=Path(__file__).resolve().parents[1], env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
        text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'CLEANUP_CASE_PASSED' in result.stdout


def test_explicit_disposal_retains_blocked_scan_owner_until_release():
    code = r'''
import tempfile,threading,time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings,QCoreApplication,QEvent
from shiboken6 import isValid
from lumen.ui import MainWindow
from lumen.model import Media
from scripts.qt_test_cleanup import cleanup_test_windows,_retained_unsafe_roots
app=QApplication([]); release=threading.Event(); entered=threading.Event()
with tempfile.TemporaryDirectory() as folder:
    w=MainWindow(workspace_settings=QSettings(folder+'/test.ini',QSettings.IniFormat))
    w.editor.add_media(Media('m',folder+'/m.mov','m',2,640,360,True,True))
    original=Path.exists
    def blocked(path): entered.set(); release.wait(5); return False
    Path.exists=blocked
    w.refresh_media_browser(); assert entered.wait(2)
    worker=w._media_status_job; cache=Path(w.cache)
    w.deleteLater(); w.deleteLater()
    w.show(); assert w._media_status_job is worker and w._media_status_closed
    QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
    assert isValid(w) and worker.isRunning()
    assert w._media_status_delete_pending and worker.cancel.is_set()
    try: cleanup_test_windows(app,timeout_ms=10)
    except AssertionError as error: assert 'owner retained' in str(error)
    else: raise AssertionError('Blocked worker must fail bounded cleanup')
    assert isValid(w) and w in _retained_unsafe_roots
    release.set(); assert worker.wait(2000)
    Path.exists=original
    cleanup_test_windows(app)
    assert not isValid(w) and not cache.exists()
    assert not _retained_unsafe_roots and not app.topLevelWidgets()
print('EXPLICIT_DISPOSAL_SAFE')
'''
    result = subprocess.run([sys.executable, '-c', code],
        cwd=Path(__file__).resolve().parents[1], env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
        text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'EXPLICIT_DISPOSAL_SAFE' in result.stdout

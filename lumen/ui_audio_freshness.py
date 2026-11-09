"""One bounded, non-Qt metadata scanner shared by all audio dialogs in a window.

Filesystem metadata may block on removable/network-backed local paths. The GUI
never checks it or joins a live worker. Closing/canceling only invalidates tokens
and sets an event. A reopened dialog replaces the one pending request rather
than creating another worker while an obsolete scan is still blocked.
"""
from __future__ import annotations

import queue
import threading

from PySide6.QtCore import QObject, QTimer, Signal


def _scan(token, project, report, checker, cancel, results):
    """Pure Python worker: copied project/scalars only, never Qt or callbacks."""
    if cancel.is_set():
        return
    try:
        current = bool(checker(project, report))
    except Exception:
        current = False
    if not cancel.is_set():
        results.put((token, report, current))


class AudioFreshnessController(QObject):
    checked = Signal(object, object, bool)

    def __init__(self, owner):
        super().__init__(owner)
        self.worker = None
        self.token = None
        self._pending = None
        self.results = queue.SimpleQueue()
        self._lifetime = {'cancel': threading.Event()}
        lifetime = self._lifetime
        owner.destroyed.connect(lambda *_: lifetime['cancel'].set())
        self.timer = QTimer(self)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self.poll)

    def request(self, project, report, checker):
        self._lifetime['cancel'].set()
        self.token = object()
        self._pending = (self.token, project, report, checker)
        self.poll()
        self.timer.start()
        return self.token

    def cancel(self, token):
        if token is not self.token:
            return
        self.token = None
        self._pending = None
        self._lifetime['cancel'].set()
        # Do not wait for a live thread, even with a nominally short timeout.

    def poll(self):
        while not self.results.empty():
            token, report, current = self.results.get_nowait()
            if token is self.token:
                self.checked.emit(token, report, current)
        if self.worker is not None and not self.worker.is_alive():
            self.worker.join(0)
            self.worker = None
        if self.worker is None and self._pending is not None:
            token, project, report, checker = self._pending
            self._pending = None
            cancel = threading.Event()
            self._lifetime['cancel'] = cancel
            self.worker = threading.Thread(target=_scan,
                args=(token, project, report, checker, cancel, self.results),
                name='CutCat audio source freshness', daemon=True)
            self.worker.start()
        if self.worker is None and self._pending is None:
            self.timer.stop()

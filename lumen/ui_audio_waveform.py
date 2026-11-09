"""Disposable display-only work, independent of editor jobs/history.

Neither filesystem identity checks nor decoding run on the GUI thread. One
non-Qt daemon worker scans a bounded visible batch; a timer consumes its results.
Cancellation only sets an event, so New/Open/Close never wait on filesystem I/O.
"""
from __future__ import annotations

import queue
import threading
from PySide6.QtCore import QObject, QTimer
from .audio_waveform import PeakCache, extract_peaks, identity

MAX_VISIBLE_SOURCES = 16


def _scan(paths, generation, cancel, results, extractor, cached, failed):
    for path in paths:
        if cancel.is_set():
            break
        key = None
        try:
            key = identity(path)
            if cancel.is_set():
                break
            if key in cached:
                status, peaks = 'ready', None
            elif key in failed:
                status, peaks = 'unavailable', None
            else:
                results.put((generation, path, key, 'loading', None))
                peaks = extractor(path, cancel)
                status = 'ready' if peaks.identity == key else 'unavailable'
                if status != 'ready':
                    peaks = None
        except Exception:
            status, peaks = 'unavailable', None
        if not cancel.is_set():
            if status == 'ready' and key is not None:
                cached.add(key)
            elif status == 'unavailable' and key is not None:
                failed.add(key)
            results.put((generation, path, key, status, peaks))


class WaveformController(QObject):
    def __init__(self, owner, changed, extractor=extract_peaks):
        super().__init__(owner)
        self.cache = PeakCache()
        self.changed = changed
        self.extractor = extractor
        self.generation = 0
        self.context = None
        self.paths = ()
        self.desired = {}
        self.states = {}
        self.failed = set()
        self.results = queue.SimpleQueue()
        self.worker = None
        self._lifetime = {'cancel': threading.Event()}
        lifetime = self._lifetime
        owner.destroyed.connect(lambda *_: lifetime['cancel'].set())
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.poll)
        self.closed = False

    def request(self, paths, context):
        if self.closed:
            return
        paths = tuple(dict.fromkeys(paths))[:MAX_VISIBLE_SOURCES]
        if context is not self.context or paths != self.paths:
            self.generation += 1
            self._lifetime['cancel'].set()
            if context is not self.context:
                self.failed.clear()
                self.desired.clear()
                self.states.clear()
            self.context = context
            self.paths = paths
            self.desired = {p: k for p, k in self.desired.items() if p in paths}
            self.states = {p: s for p, s in self.states.items() if p in paths}
            self.failed.intersection_update(self.desired.values())
            self.changed()
        self.poll()
        if self.worker is None and paths:
            cached = set(self.cache.keys())
            cancel = threading.Event()
            self._lifetime['cancel'] = cancel
            self.worker = threading.Thread(
                target=_scan,
                args=(paths, self.generation, cancel, self.results, self.extractor,
                      cached, self.failed.copy()),
                name='CutCat source waveform', daemon=True)
            self.worker.start()
        if self.worker is not None:
            self.timer.start()

    def get(self, path):
        key = self.desired.get(path)
        return self.cache.get(key) if key is not None else None

    def status(self, path):
        if path not in self.paths:
            return 'limited'
        if self.states.get(path) == 'ready' and self.get(path) is None:
            return 'limited'
        return self.states.get(path, 'loading')

    def poll(self):
        if self.closed:
            return
        changed = False
        while not self.results.empty():
            generation, path, key, status, peaks = self.results.get_nowait()
            if generation != self.generation or path not in self.paths:
                continue
            # Invalidate painted old data on identity loss/change, even on error.
            previous = (self.desired.get(path), self.states.get(path))
            if key is None:
                self.desired.pop(path, None)
            else:
                self.desired[path] = key
            self.states[path] = status
            if peaks is not None:
                self.cache.put(key, peaks)
            elif status == 'ready' and key is not None:
                self.cache.get(key)  # Keep visible hits ahead of historical sources.
            elif status == 'unavailable' and key is not None:
                self.failed.add(key)
            changed |= previous != (key, status) or peaks is not None
        if self.worker is not None and not self.worker.is_alive():
            self.worker.join(0)
            self.worker = None
        # Bound bookkeeping to currently visible identities as files mutate.
        self.failed.intersection_update(self.desired.values())
        if changed:
            self.changed()
        if self.worker is None:
            self.timer.stop()

    def suspend(self):
        self.generation += 1
        self.paths = ()
        self.desired.clear()
        self.states.clear()
        self._lifetime['cancel'].set()

    def close(self):
        self.suspend()
        self.closed = True
        self.timer.stop()
        self.cache.clear()

"""Deterministic ownership cleanup for Qt tests, never production shutdown policy."""
from __future__ import annotations


# Keep unsafe native owners alive even if the caller discards the exception.
_retained_unsafe_roots = []

def cleanup_test_windows(app, existing=(), timeout_ms=5000):
    """Destroy only test-created root widgets after cooperative worker shutdown.

    Normal fixture teardown runs first. No forced thread termination is allowed:
    a worker that fails to stop makes cleanup fail, retaining its owning window.
    DeferredDelete must be explicitly dispatched without a running app event loop.
    """
    import time
    from PySide6.QtCore import QCoreApplication, QEvent, QThread
    from shiboken6 import isValid
    _retained_unsafe_roots[:] = [w for w in _retained_unsafe_roots if isValid(w)]
    preserved = {id(w) for w in existing}
    roots = [w for w in app.topLevelWidgets()
             if id(w) not in preserved and w.parent() is None]
    failures = []
    deadline = time.monotonic() + timeout_ms / 1000
    unsafe_owner = False
    for window in roots:
        if not isValid(window):
            continue
        threads = list(window.findChildren(QThread))
        scanner = getattr(window, '_media_status_job', None)
        expected_scanner = (scanner is not None and getattr(window, 'closing', False)
                            and getattr(window, '_media_status_close_pending', False)
                            and scanner.cancel.is_set())
        leaked = [t for t in threads if isValid(t) and t.isRunning()
                  and not (expected_scanner and t is scanner)]
        if leaked:
            failures.append(f'{type(window).__name__}: unexpected live worker at fixture teardown')
        if hasattr(window, 'closing'):
            window.closing = True
        cancel = getattr(window, 'cancel_jobs', None)
        if cancel:
            cancel()
        threads = list(window.findChildren(QThread))
        for thread in threads:
            if isValid(thread) and thread.isRunning() and not thread.wait(max(0, int((deadline - time.monotonic()) * 1000))):
                failures.append(f'{type(window).__name__}: {thread.objectName() or type(thread).__name__}')
        if any(isValid(t) and t.isRunning() for t in threads):
            unsafe_owner = True
            if window not in _retained_unsafe_roots:
                _retained_unsafe_roots.append(window)
            continue  # Never destroy a running QThread's owner.
        # Deliver finished callbacks before closeEvent inspects window.jobs.
        app.processEvents()
        if not isValid(window):
            continue
        if any(t.isRunning() for t in window.findChildren(QThread)):
            failures.append(f'{type(window).__name__}: worker restarted during cleanup')
            unsafe_owner = True
            if window not in _retained_unsafe_roots:
                _retained_unsafe_roots.append(window)
            continue
        # MainWindow fixtures already resolve dirty prompts; repeat only cleanup.
        if hasattr(window, 'closing'):
            window.closing = True
        window.close()
        window.deleteLater()
    if unsafe_owner:
        raise AssertionError('Qt test worker lifecycle violation; owner retained: ' + ', '.join(failures))
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    if failures:
        raise AssertionError('Qt test worker lifecycle violation: ' + ', '.join(failures))
    _retained_unsafe_roots[:] = [w for w in _retained_unsafe_roots if isValid(w)]
    survivors = [w for w in roots if isValid(w)]
    if survivors:
        raise AssertionError('Qt test roots survived DeferredDelete: ' + ', '.join(type(w).__name__ for w in survivors))

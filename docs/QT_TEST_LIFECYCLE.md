# Qt test lifecycle diagnosis (Linux, 2026-10-18)

## Finding and bounded fix
The previous aggregate run printed its terminal test summary but did not exit during subsequent polling; it was interrupted after the summary. Its exact stalled native stack was not captured, so the original hang is not conclusively attributed to one component.

A separate 60-test TTS subset exited normally but retained 1,500 top-level Qt widgets. Twelve sequential MainWindows retained 25 additional widgets each after close(), reaching300; explicit deleteLater plus DeferredDelete dispatch returned to zero on every iteration. close() intentionally supports reopening and is not destruction. Many test fixtures previously relied on interpreter shutdown to release hidden widgets and native multimedia owners.

The fix is test infrastructure only: an autouse fixture snapshots pre-existing roots, cleans only new roots after ordinary fixture teardown, cooperatively stops owned workers, drains finished callbacks, and explicitly dispatches deferred deletion. Unexpected live workers still fail teardown even if rescue cancellation succeeds. A worker that misses the bounded deadline retains its owner; no forced thread termination or os._exit is used. Shared roots and production close/reopen semantics remain intact.

## Final frozen validation
351 tests passed in145.02s,79 existing Qt deprecation warnings; process exit0. Coverage includes TTS UI and harness, consecutive-take safety, shortcut dispatch/reopen, standard editor UI, retime, stabilization, restoration, component setup, caption layout and workspace modes. Final diagnostics: zero top-level widgets, zero active global thread-pool tasks, no remaining Python worker threads. Pre/post helper and test hashes match.

Three subprocess regressions verify repeated media-window disposal and clean exit, rejection of leaked cooperative workers plus queued-finished cache cleanup, and retention/release of a noncooperative worker owner. No production lumen/ files changed in this diagnosis.

This establishes the resource-accumulation fix and clean bounded integration exit. It is not a new full-suite pass or proof of the original aggregate stall's precise cause. Previous aggregate2754pass/13stale-harness-fail/2skip history remains preserved. No native Windows/macOS validation, GitHub push, CI or payment occurred.

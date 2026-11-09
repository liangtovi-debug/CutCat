# Automatic paused selected-frame grade preview

## Workflow

Open Current-frame grade preview with a video clip selected and the playhead inside it. Opening performs the existing manual capture. Automatically refresh while paused is off for every new dialog and is not saved in the project. Enable it to update the selected-clip still after changes. The ordinary viewer continues to play the rendered timeline cache.

Changes are detected by a 200 ms GUI timer and playback-state notifications, then debounced by 300 ms. Every edit resets the debounce; one CPU capture may run while one newest request waits. Superseded work is canceled, never run concurrently with a replacement. Unchanged errors are not retried in a loop. Other shared jobs block automatic capture until they finish.

Cancel stops debounce, queued intent and current capture; it will not automatically resubmit the same editing context. A later edit, selection/playhead/project/source/LUT change, or explicit refresh/mode re-enable can rearm it. Turning the mode off cancels pending/current refresh. Closing stops timers and cancels work; reopening returns to manual mode. Automatic mode never pauses playback on its own. Manual Refresh retains its existing explicit pause behavior.

## Fidelity and stale results

Manual and automatic work call the identical capture_grade_preview_snapshot function: RGB16 decode, normalized/restored full fitted canvas, float32 complete grade/qualifier/LUT. No pre-matte downsampling or cached simplified proxy is introduced. Only display is clipped/quantized to RGB8. Existing strict source validation, alpha and stabilization exclusions are unchanged. This is before effects, clip transforms, fades, captions, overlays and compositing. Scopes and references still use the full timeline preview.

Revision, project identity, clip selection, playhead, selected clip/media signature, source and active LUT file-stat identity invalidate stale work. LUT contents are fingerprinted inside the capture worker, not polled on the GUI thread. As in the existing manual path, a filesystem which secretly rewrites bytes while preserving every identity/stat field cannot be detected by a stat-only display guard; explicit recapture validates the worker input again. Generation guards reject out-of-order completion and errors. Source data is never uploaded.

## Boundaries and comparison

This reduces the repeated Refresh step. It does not provide live playback grading, final-composition preview, GPU acceleration, calibrated/HDR monitoring, RAW decoding, Apple Log input conversion, animated/tracked color windows or Resolve-equivalent performance. Full-resolution CPU restoration, spatial keys and large canvases can be slow; cancellation is cooperative at the existing bounded processing boundaries.

The official DaVinci Resolve Color overview describes integrated grading, scopes and animated/tracked Power Windows: https://www.blackmagicdesign.com/products/davinciresolve/color . This round closes one adjustment-feedback workflow gap, not those other feature categories. Static qualifier windows already exist; the About text now names the missing animated/tracked windows precisely.

Schema28 and all dependencies are unchanged. Linux offscreen Qt/FFmpeg verification is separate from native Windows/macOS lifecycle/build and signing validation. Local regression receipts, measurements and archive hashes are recorded outside this source tree in the round evidence; no current native package, upload or remote-main success is implied.

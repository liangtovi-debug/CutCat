# Selected clip current frame grade review

A deliberate single-frame review reduces the work needed to judge a stored color
adjustment. It is **not** a final timeline frame or a realtime GPU viewer. The
full timeline preview remains the only playback/composite review path, and export
still uses the unchanged renderer. No project schema change or new dependency.

## Why this round

[Blackmagic Design's Color page](https://www.blackmagicdesign.com/products/davinciresolve/color)
(reviewed 2026-10-23) describes directly adjusted primary wheels, custom curves
with a live histogram and shot matching. CutCat's previous color round made CPU
hue processing faster, but a new playable preview still required the complete
timeline. This change addresses that feedback delay with a bounded explicit
single-frame operation, not a claim of equivalent Resolve features or speed.
No same-machine Resolve benchmark was run.

## Image contract

- Selected clip and current integer timeline frame, at project canvas resolution.
- The existing RGB16 local decoder and exact legacy/retime source selection are
  reused. Input normalization and supported spatial restoration precede grading.
- The complete stored grade runs on the full HWC float32 canvas: white balance,
  primary controls, RGB curves, hue curves, qualifier including spatial windows
  and matte operations, then the LUT. It is never graded as disconnected flat
  chunks that would destroy qualifier geometry.
- The returned image is before clip effects, static transform, other video
  tracks, stickers, captions and the final composite. Those omissions are
  labelled, not silently presented as final output. Audio is not evaluated.
- Display conversion clips/quantizes to 8-bit for the Qt image only. Processing
  retains RGB16 decoding and float32 arithmetic; this is not HDR/Log/RAW support,
  a calibrated monitor, or an unclipped high-bit-depth display.
- The selected-clip image is not installed into the main playback cache and
  cannot replace the final-preview scopes or captured reference still.

## Work and limits

Refresh is explicit, not automatically scheduled on slider motion. Jobs run off
of the GUI thread. A new request cancels the old request and only the latest
context may be displayed. Project/revision/selection/playhead and source/LUT
identity changes invalidate old results. Closing the dialog cancels pending
work and releases the large frame; the app's normal jobs/settings/shutdown gates
continue to apply. A full-timeline handoff uses the regular preview workflow.

The source pipeline is bounded to 8,388,608 canvas pixels and a total requested
timeout capped at 120 seconds. It can still decode from source origin to preserve
existing timestamp phase. A late frame in long or difficult material can take
longer than an early frame or time out; this is not a random-access speed promise.
Cancellation during CPU operations is cooperative, not a hard realtime deadline.
No user media or API service is involved.

Active stabilization, transparent input formats and changing source geometry
with active restoration remain explicitly unsupported by this source-frame
path. Unsupported input metadata and missing frames are errors, not guessed
color or timing. Use the complete timeline renderer for these cases.

## Verification and measurement

The accompanying tests compare real RGB16 input and the decoded existing graded
clip boundary, with an explicit quantization tolerance, as well as independent
pixel expectations where applicable. They cover full spatial qualification,
LUT, restoration, timing and stale/cancel paths. Existing export tests remain in
the complete regression suite.

`FRAME_GRADE_PREVIEW_RESULTS.json` records actual cold process timings and their
scope. The single-frame job and complete playable timeline cache are different
deliverables. The difference measures waiting for a color check, not a faster
export or equivalent output. Each process and render cache is fresh; OS file
cache is uncontrolled. Peak memory is the Python process only, not the decoder's
separate process. No native Windows/macOS packages or remote-main update are
claimed by this source milestone.

### Recorded measurements

On the recorded Linux CPU environment, three fresh-process runs per mode gave:

| Synthetic source and timeline | Complete timeline proxy median | Selected grade-frame median |
| --- | ---: | ---: |
| 1920×1080, 30 frames, selected frame 15 | 21.42 s | 4.12 s |
| 3840×2160, 8 frames, selected frame 4 | 29.31 s | 8.64 s |

These deliberately short fixtures measure work avoided, not long-film or live
interaction guarantees. Background focused tests were allowed during the run;
this is not an isolated performance lab. Raw observations and memory figures are
retained in the results JSON. Reproduce with `python scripts/benchmark_frame_grade_preview.py`.
The script generates its own deterministic RGB16 fixtures in a child process,
then alternates the single-frame and whole-preview modes with fresh render caches.

The working thread fingerprints an active LUT before and after evaluation.
The GUI only checks stat identity, keeping timer validation lightweight. A
filesystem adversary that changes contents while preserving every identity/stat
field cannot be detected by that GUI poll; an explicit refresh reads and hashes
again. Neither this nor the existing source-file guard is a filesystem lock.

LUT evaluation is pinned to the captured resolved file target; retargeting a
symlink during evaluation cannot substitute different pixels under the original
identity. Queued refreshes and delayed timeline handoffs are also canceled by
global Cancel and accepted main-window close, and reopening the frame dialog
supersedes an older hidden dialog's handoff.

A separate one-shot synthetic 4K presentation check recorded about 65.6 ms for
worker RGB8 conversion and 8.3 ms for the GUI's QImage copy (24.9 MB). It is not a
latency guarantee or a repeated statistical benchmark; the GUI performs bounded
image ownership/copy work, not zero work. See the presentation-cost JSON.

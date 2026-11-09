# Bounded rendered A/V range preview (schema43 unchanged)

This local candidate adds **Preview around playhead** to the viewer and Playback
menu in both workspaces. Open it, choose seconds before/after the playhead
(default 2 seconds each side), then **Build range preview** and **Play**.
**Pause**, **Loop range**, **Cancel**, and **Close** affect only this session.
Opening or rebuilding never starts playback automatically. Moving the main
playhead or changing the project invalidates the range. The range's position
readout maps its local playback clock back to global timeline frames without
moving the editor playhead or adding Undo history.

The original canvas and final composition are rendered to H.264 8-bit Rec.709
SDR plus AAC from the final 48 kHz stereo mix. This is **rendered playback**, not
live trimming, a calibrated/HDR reference, lossless encoded media, or native
Windows/macOS acceptance. The full timeline preview, its cache, comparison
mode, scopes, reference stills, and export remain separate and unchanged.

## Session In/Out selection

The viewer's **Range preview** menu and Timeline menu expose **Set preview In**
(`I`), **Set preview Out (inclusive)** (`O`), **Build and loop selection**
(`Shift+Space`) and **Clear preview selection**. Seek to each desired frame, set
both marks, then build/loop. A teal ruler strip with I/O boundary lines shows the
selection. The status bar and selection dialog show numeric inclusive marks;
rendering still receives the exact half-open `[In, Out + 1)` frame window. Setting
both marks on the same frame previews one frame. Either endpoint may be set first;
reversed, missing, outside-project, >10-second or >600-frame windows are rejected,
never swapped or truncated. The full-preview action remains available.

This explicit build/loop command starts playback only after rendering and source
verification; ordinary **Build range preview** still waits for **Play**. Changing
marks, moving the main playhead, editing, Undo/Redo, Cancel, closing and changing
projects invalidate pending playback through the existing generation guards.
Repeated requests retain only the latest range, and looping rechecks sources.

Marks belong only to this editor session. They do not modify persistent timeline
markers, project JSON, dirty state, Undo, cache/export or clip timing. Same-session
edits and Undo retain fixed marks; shortening past them produces an explicit
out-of-bounds error until corrected or undone. Replacing the editor/project clears
them. Keyboard dispatch uses the existing text, numeric input, IME, dialog, popup,
media-list and unrelated-focus guards. Previously saved shortcut profiles gain
these commands **unbound**, preserving custom I/O bindings; resetting the preset
or assigning them in Keyboard shortcuts enables the defaults.

## Non-destructive clock contract

No shortened Project or rebased Clip is saved or used as an edited timeline.
The compositor receives an explicit half-open integer frame window and selects
intersecting original records for work. Existing linked J/L roles and historical
mix anchors remain independent and intact.

- The legacy seconds-trim-before-fps and affine normalized-source-floor sampling
  branches are preserved; there is no input seek or reset of source FPS phase.
- Only intersecting output frames are graded. Qualifier window animation uses
  the original clip-local output frame, as does picture motion/opacity.
- Stickers evaluate the original global timeline frame and their own original
  animation/keyframe clock. Captions retain original global frame gates.
- Audio keeps each original decode/resample/retime, gain/key, fade and source
  endpoint chain. The original local signal is sliced using global half-up
  48 kHz boundaries and placed in the range before the same anchored sum and
  master gain. Duration is `B(end)-B(start)`, not a rounded relative duration.
- Full-project scalar effect selection is retained even when its triggering
  effect is outside the range. Unrelated timeline media are not opened or
  graded, and the range never loops a complete single-frame renderer.

Source decoding can still traverse preceding source frames/samples to retain
codec/resampler state. Source metadata verification retains the existing bounded
source-tail probe; reduced output work is **not** constant-time random access.

## Bounded first version and explicit fallback

A range is at most 10 seconds / 600 frames, with at most 32 intersecting clips,
an even original SDR canvas up to 1920×1080, a 1 GiB temporary working-cache
budget and a 120-second rendering deadline. Metadata, source-color and retime-capability probes use the same cancellable
deadline adapter; owned subprocesses are killed and reaped on cancellation.
Caption/font checks raise on cancellation instead of ignoring a boolean result.
The default radius is adjustable from 0.5 to 5 seconds and clipped to the project;
fractional-rate radius conversion rounds down to whole frames.

Unsupported active cross dissolves, stabilization, Apple Log, indirect-source
manifests/external references, more than 300 seconds of required source history,
and audible clip-local history over 30 seconds explicitly require **Build full
timeline preview**. Audio's 300-second check conservatively includes the original
selected source endpoint, even when demand stops before that endpoint. Missing
required filters/fonts, invalid source data and cache/time limits also fail
visibly, never silently omit a picture/audio layer or restart its timing state.

The range uses original stored corrections, regardless of the main viewer's
Before/After comparison selection. No persistent In/Out marks, render-bar scheduling, proxy manager, or
seamless/gapless loop is added.
Loop restart performs another background source check, so a short gap may occur.

## Worker, freshness and ownership

Deep copy, file identity checks, source/container probes, LUT/sticker/generated
media verification, rendering and cache deletion run off the GUI thread. One
registered worker and only the latest pending request survive repeated clicks.
Project/editor/session/revision/range guards reject late results. Source checks
run again before publication, each explicit Play and loop, and periodically while playing (not while idle/paused);
these are asynchronous checks, not an OS lock against instantaneous external
writes. Cancel, Close, project replacement and competing playback stop the
independent range player and drain owned work without activating late results.

## Independent evidence and remaining gates

The range oracle runs all 217 Python modules from accepted private-main commit
`f91f80ac101ea8fed59fa44497a019faa375f50f` in an isolated hash-pinned subprocess.
It renders the **complete** final graph to pre-encoder YUV420 and stereo f32 PCM;
only finished reference bytes are sliced. Tests cover J/L ownership, frame/sample
edges, fractional FPS, gain/fades/master/anchors, retiming, different-rate/VFR
legacy source phases, final effects/geometry/keys/captions/stickers, and excluded
tails. A separate actual H.264/AAC flash/burst test checks decoded event alignment
after encoder priming/padding; a real Qt player smoke checks frame delivery and
end-of-media, not audible hardware output. A bundled source-only frozen archive makes this oracle reproducible from
a fresh source extraction without importing the candidate range compiler.

Existing final-still oracle copies remain hash-pinned to their historical full
compiler; default-argument extensions never redefine expected pixels. Focused
tests, complete frozen-source regression, independent review, fresh extraction,
private integration and native packages are separate gates. Counts belong only
to their exact source-hashed receipts; exploratory passes are not summed into an
acceptance total. No remote publication or hosted build is part of this round.

## Paid-editor comparison (official pages checked 2026-10-31)

Premiere documents explicit In/Out rendering for sequence sections and a
separate audio rendering option ([Adobe guide](https://helpx.adobe.com/premiere/desktop/render-and-export/render-sequences-for-playback/render-a-section-of-a-sequence.html)).
Final Cut Pro can loop a selected portion of a project or clip ([Apple guide](https://support.apple.com/en-sg/guide/final-cut-pro/ver90ba4ef0/mac)).
Resolve describes dynamic JKL trimming during looping playback ([Blackmagic Design](https://www.blackmagicdesign.com/products/davinciresolve/edit)).

This candidate addresses the narrower CutCat gap of hearing a final short J/L
edit without grading/rendering the entire project. It does not claim those
products' realtime trim interaction, background cache coverage, complete format
support, professional monitoring or feature parity.

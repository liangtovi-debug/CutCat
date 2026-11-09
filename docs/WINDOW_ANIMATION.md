# Manual animated color windows

This local source milestone adds explicit position and size keyframes to the
existing single ellipse/rectangle qualifier window. It does not add an automatic
tracker, polygon/freehand windows, rotation animation, GPU processing, or a
real-time performance guarantee. Linux CPU/offscreen verification is not a new
Windows/macOS application build. No remote repository update is claimed.

## Why this step

Blackmagic's official Colorist Guide describes static and dynamic color keyframes,
and its reference manual exposes separate window keyframe tracks. This milestone
addresses the smaller practical gap between a static mask and automatic tracking:
manually keep a regional correction aligned as a subject moves. It is not a claim
of matching Resolve's tracker or dynamic interpolation implementation.

- [Blackmagic Colorist Guide, static/dynamic keyframes](https://documents.blackmagicdesign.com/mx/UserManuals/DaVinci-Resolve-17-Colorist-Guide.pdf?_v=1623808249000)
- [Blackmagic reference manual, window tracks](https://documents.blackmagicdesign.com/UserManuals/DaVinci_Resolve_10_Reference_Manual.pdf)
- [Current Blackmagic product capabilities](https://www.blackmagicdesign.com/products/davinciresolve/whatsnew)

## Coordinates and interpolation

Keys use integer clip-relative **timeline output frames**, not source frame indices
or seconds. A frame of 0 is the first output picture of this clip. Source sampling
continues to use exact rational project-frame units, floor(source_in + k × speed).
The window is evaluated independently at output k, including every duplicate
source picture during slow motion. At 30000/1001 fps, no floating-point conversion
to seconds changes the key coordinate or source sampling phase.

Each key records center X/Y, full width/height in normalized fitted/padded canvas
coordinates, and an outgoing `hold` or `linear` interpolation mode. Hold keeps the
left key's geometry until the next exact key; linear interpolates the four
geometry values. The first/last values hold outside their key range. Shape,
feather, invert, qualifier membership, and correction remain static. The window
multiplies the same refined color matte as before.

At most 128 strictly ordered unique keys are accepted. Geometry bounds match the
static window. Signed key frames are bounded to ±(2^53−1); hidden keys outside the
visible clip are deliberate, not malformed state. They retain interpolation
context after trims and split and can become visible again after extension.

`evaluate_window` and `evaluate_grade` resolve geometry without mutating stored
settings. Evaluated animated settings have no keys; static settings take the
unchanged no-animation path. Low-level evaluation of active unresolved animated
settings is rejected instead of silently applying a static base geometry.

## Editing semantics

- Split retains every key in both pieces and rebases the right piece by the split
  offset. Visible pixels and interpolation at the cut do not jump.
- A left timeline trim rebases every key by removed output frames; retained
  timeline pictures keep their original geometry.
- Rolling a shared cut preserves the left clip's local animation and rebases the
  right clip by the cut delta, retaining hidden keys. No animation is transferred
  between unrelated clips.
- Source slip changes source selection, not local window timing. Explicit source
  endpoint or duration edits retain local key positions. This is a timeline-
  anchored animation, not object tracking tied to source content.
- Speed changes keep local key times, with no automatic scaling or deletion.
  Shorter clips retain keys beyond the visible end; longer clips hold the last
  value. Source subjects may therefore need manual key adjustments after retime.
- Moving the clip moves the animation with it. Copy/paste carries the complete
  creative grade and the same local key times, without scaling to target duration.
  Shorter targets retain hidden keys; longer targets hold the final geometry.
- Transactions, undo/redo, saved project restore and recovery preserve all keys.
  Project schema 30 declares the addition. Older schemas migrate only their
  original documented fields; injecting keys into a legacy version is rejected.

## Review workflow and limits

Open Window animation from the selected picture clip's color controls. Work on
private clip-local frames, explicitly add/replace or delete a key, choose outgoing
hold/linear, and navigate previous/next visible keys. Changing frames clears the
old picture; click Refresh frame to decode the chosen position. Geometry drafts
must be added/replaced before they can be previewed or applied. Preview is selected-clip CPU work,
not continuous tracking or the final composited timeline. Apply commits the staged
settings once; Cancel leaves the project unchanged. Context changes and stale
async results must not replace a newer preview or commit onto a different clip.
The static qualifier editor explains animated geometry and does not silently edit
base geometry that the keys override. The animation dialog shows a labeled sparse
overview; small details can be missed there, while export evaluates every pixel.
The existing qualifier detail view remains available at its captured timeline
frame. The dialog accepts clip durations up to 2,147,483,648 output frames; longer
clips are rejected with a clear message and their stored keys remain unchanged.

The full-grade still-preview/automatic-preview and render paths use the same
frame evaluator; picker sources stay before their own correction so they do not
sample a grade back into itself. Precise matte preview is based on float source
samples, never display RGB8. The RGB16 intermediate clamps only at its existing
SDR boundary, and ProRes remains a separately encoded 10-bit deliverable.

Apple Log 1 remains explicit and experimental under the preceding input rules.
No Apple Log 2 support, camera certification, Resolve/LUT match, HDR mastering,
ICC display calibration or automatic tracking is introduced by this feature.

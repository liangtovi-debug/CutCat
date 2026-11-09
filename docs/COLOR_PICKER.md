# Static source hue picking

## Use

Professional → Color → Curves → Hue vs Hue / Hue vs Saturation → Pick from clip frame.
Place the timeline playhead inside the selected picture clip. The dedicated window
loads that clip only, not the composited After preview. Click a colored pixel,
then Add sampled hue points. Picking alone changes no grade; adding controls
subdivides the existing line without resetting its values. Adjust the selected
center point next. One insertion is one undo; repeat at the same hue adds no
history. Cancel/Escape never adds points. At 1280×800 the inspector uses normal scrolling:
the picker button and exact fields, or the lower range note, may need separate views.

## Stage, time and coordinate contract

The decoder uses the same RGB16 source matrix/range, SAR/rotation, fit/pad and
legacy/nonlegacy project-rate frame mapping as the renderer. The display shows
its timeline frame, normalized project-rate source index and nominal source time.
Samples are float32 after input normalization, active-picture spatial restoration,
and enabled primary/master/RGB curves, before both selective curves and LUT.
Restoration uses the same per-frame deblock → denoise → restrained sharpen
transform as rendering, with synthetic padding excluded from every kernel.
Disabled creative grading still permits restoration; disabled/zero-strength
restoration is bypassed and both states are labelled explicitly. No RGB8 or
graded-segment codec round trip occurs in the sampled float values.
The panel is intentionally before post-grade transforms, transitions, effects,
opacity, overlays and compositing; those are not represented or sampled here.
Display pixels are clipped/quantized to 8-bit solely for presentation. Mouse
coordinates map into the immutable float frame, never into these display bytes.
It is not a display calibration, HDR, raw-source-bit-depth or real-time claim.

## Boundaries and stale state

The fitted active-picture rectangle comes from the actual decoder spatial chain.
Padding, outside-image coordinates, transparent source formats, nonfinite,
out-of-[0,1], and gray/near-gray RGB samples (spread ≤ 1/255) are rejected. Black
content is rejected as achromatic, not used to infer geometric padding. Active
stabilization still blocks capture; disabling it is not done automatically.
For active restoration, the requested-frame active geometry must equal the
renderer's source-origin geometry. A mismatch fails closed rather than sampling
a different crop. This supports only existing per-frame spatial restoration,
not temporal processing or motion-compensated restoration.
Decode size/time/output budgets and cancellation bound work, including checks
during each spatial restoration tile and each grading block.

Changing the project revision, selected clip, curve, playhead or source identity
invalidates the frame. Current state is checked again before applying. A late
result after dismissal cannot apply points; opening twice reuses the active
window. Source identity includes canonical path, device/inode, size and nanosecond
modification/change timestamps. This is local-file change detection, not a
content-addressed immutable filesystem guarantee.

## Hue curves, not a spatial mask

The picked hue and ±30° anchors are inserted on the existing curve. Existing
nearby points are reused; anchors crossing red wrap around 0°/360°. Existing
points are retained and capacity beyond 32 points is rejected atomically.
The shaded curve spans/text show the closure of actual non-neutral segments,
not the picker tolerance, not pixel coverage, not an HSL qualifier or tracking.
Moving an existing point may affect a smaller neighbor-bounded range, and existing
adjustments elsewhere remain active. Neutral crossings may lie inside shown spans.
Schema remains 17. No production render/color arithmetic or default pixels change.

## Competitor comparison, 2026-10-22

Blackmagic's official Resolve 20 Colorist Guide describes sampled Hue-vs-Hue
center points with limiting anchors. The previous round added that practical selection workflow. This round makes
that source-stage workflow faithful with existing spatial restoration enabled;
it does not add live/composited viewer picking.
Resolve still has broader curve families, qualifier/matte views, tracked spatial
isolation and GPU-oriented grading. CutCat does not claim these features.

Sources:
- https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-20-Colorist-Guide.pdf
- https://www.blackmagicdesign.com/products/davinciresolve/color

Next bounded candidate: Hue-vs-Luma with explicit encoded-domain semantics,
using the same pre-selective sampled hue. HSL qualification with a matte preview
is a separate, larger milestone. Do not substitute a final-preview picker.

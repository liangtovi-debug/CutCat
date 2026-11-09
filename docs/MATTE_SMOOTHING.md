# Bounded spatial matte smoothing

This independently specified feature smooths the **selection weights**, not RGB,
alpha, motion, or the video image. It does not reproduce Resolve's Blur Radius,
Denoise, Clean Black/White, morphology, or In/Out Ratio algorithms.

## Contract

`matte_smoothing` is a finite scalar blend amount in [0, 1]. The radius is fixed
at **one fitted project-canvas pixel**, not a screen pixel or original-camera
pixel. The kernel is the separable binomial outer product `[1, 2, 1] / 4` in each
axis (3×3 weights `[[1,2,1],[2,4,2],[1,2,1]] / 16`). At actual canvas edges,
neighbor coordinates clamp to the nearest canvas pixel (replication). A crop
edge is not a canvas edge.

Pipeline:

1. Compute the base/include union and exclusion subtraction from incoming
   encoded Rec.709 RGB after primary and selective-color adjustments.
2. Apply existing pointwise matte black/white levels.
3. Blend that weight with its binomial neighborhood average using the amount.
4. Keep destinations outside finite [0,1] RGB unselected, even if their neighbors
   have positive weights. Out-of-range neighbors contribute zero key weight.
5. Multiply by the static spatial window, including its feather/inversion.
6. Apply selected hue/saturation/code-value exposure once using the final mask.

The window remains last, so smoothing cannot introduce selection outside its
zero-weight area. Like any blur, this filter can extend a color key by one pixel
inside the permitted window. It does not remove connected components or fill
holes selectively. An isolated impulse is softened, not eliminated. Avoid
excessive adjustment where that spread creates a visible color fringe.

Amount zero bypasses the new neighborhood path exactly. Existing non-image,
strided and legacy outputs retain their prior behavior. Disabled and neutral
adjustments remain exact copies. Strict setting validation still applies.

## Sampling and memory

The full export frame provides its own dense neighborhoods. Overview and detail
inspection use the frozen incoming float32 source frame, never the displayed
RGB8 image or a sparse grid treated as adjacent pixels. Sampled API calls provide
`canvas_size`, `origin`, `stride`, and `matte_source` with
`matte_source_origin`. A cropped source must contain the full one-pixel halo,
except where the actual canvas edge supplies replicated neighbors. Incomplete
or inconsistent context is rejected rather than silently filtering a crop edge.

Processing uses bounded neighborhoods/tiles with cancellation checkpoints; it
never needs to materialize all full-frame neighbor RGB arrays. Output storage
still scales with image size. This remains offline CPU grading, not a promise of
real-time playback. Renderer timeout/cancellation checks remain active during
spatial processing, not only after an entire frame completes.

## Local performance observation

With 17 H/S/Y ranges and active selected adjustments, final-source Linux
tracemalloc runs measured 4.98 s at 1080p and 19.31 s at 4K for smoothing amount
0.75 (legacy amount-zero runs: 4.84 s and 17.83 s). Tracked scratch allocation
excluding input and returned RGB was 12.76 MB at both sizes; required output
storage was 24.88 MB and 99.53 MB. Maximum observed smoothing cancellation
checkpoint gaps were 176.5 ms and 195.1 ms. These are one-run observations with
allocation tracing enabled, not hard timing limits, total RSS, real-time claims
or Windows/macOS native results.

## UI and project format

The qualifier's matte controls expose smoothing percentage alongside black and
white points. Reset restores neutral matte settings. Inspection, overlay, crop
navigation and window handles do not commit a change; Apply creates one undoable
edit, and Cancel discards it. Source sampling still reads the unmodified frozen
incoming pixels. Existing stale-source, unsupported alpha and active-stabilization
sampling protections remain in place.

Schema 22 stores the new field. Exact older qualifier schemas migrate to zero
smoothing; fields from future schemas cannot be smuggled into an older version.
Save/load, recovery, grade copy and cache identity include smoothing.

## Comparison and boundaries

The [current Resolve color page](https://www.blackmagicdesign.com/products/davinciresolve/color)
separates secondary qualifiers, spatial windows and tracking. The official
[Resolve 17 features guide](https://documents.blackmagicdesign.com/SupportNotes/DaVinci_Resolve_17_New_Features_Guide.pdf)
provides historical documentation of matte blur and directional refinement,
including a warning that blur can produce edge halos. This is evidence of the
capability gap, not evidence that CutCat uses the same mathematics.

Still absent: variable-radius blur, directional spread, spatial morphology,
stroke/softness sampling, tracking/keyframes, multiple/freeform windows and
HDR/OCIO workflows. See this round's validation report for exact measured tests.
Local Linux/offscreen source validation does not establish native Windows/macOS
build success or a remote main update. Upload-pending files are not delivered.

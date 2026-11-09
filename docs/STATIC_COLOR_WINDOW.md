> Current update: schema20 now supports bounded independent additive/subtractive color ranges. See [QUALIFIER_SETS.md](QUALIFIER_SETS.md). Earlier single-range/schema statements below describe the original milestone; unchanged coordinate, source, window and output restrictions still apply.

# Static spatial window × color qualifier

This is a bounded local-source milestone, not a native Windows/macOS release or a remote-main publication claim.

## Purpose and scope

A color key alone cannot distinguish a foreground object from a similarly colored background. The qualifier now supports one static ellipse or axis-aligned rectangle, combined by multiplication with the existing hue / HSV-saturation / encoded Rec.709 Y′ key. Numeric center, full width/height, exterior feather and optional spatial-only inversion define the window. Off preserves previous output exactly.

This is not tracking, keyframing, multiple windows, a freeform polygon, gradient, rotation, a node graph, semantic masking or matte cleanup. Moving subjects can leave the window. Do not describe this as Resolve-equivalent behavior.

## Coordinate and arithmetic contract

Coordinates refer to the fitted project canvas, including its letterbox/pillarbox padding. They do not refer to native camera pixels or the final composited viewer. The order is RGB16 decode and fit/pad → input normalization → enabled spatial restoration on active picture → primary / RGB curves / selective hue curves → combined qualifier → LUT → clip position/scale/rotation/mirror → composition/output conversion. The static window consequently moves with the clip's later transform. Source snapshots still reject active stabilization and unsupported alpha inputs.

Pixel centers are evaluated at ((x + 0.5) / canvas_width, (y + 0.5) / canvas_height). Stored width/height are full normalized canvas dimensions, not radii. A width of 50% and height of 50% produce a canvas-aspect ellipse, not necessarily a pixel-space circle; for a circle choose dimensions with equal pixel extents. Center coordinates are normalized on each axis. Copying a grade preserves these normalized values on the destination canvas; it does not promise object tracking or match a subject's position.

For normalized offsets dx = 2 × (x_normalized − center_x) / width and dy = 2 × (y_normalized − center_y) / height, radius is sqrt(dx² + dy²) for an ellipse or max(abs(dx), abs(dy)) for a rectangle. Radius ≤ 1 is the full-strength core. Exterior feather of f gives weight max(0, 1 − (radius − 1) / f) outside the core. At f = 0 the edge is hard. This is a simple shape-relative linear ramp, not Euclidean-distance feathering or asymmetric edge feather. Window inversion uses 1 − spatial_weight, then multiplies by the color weight; it never inverts color membership. Off remains spatial weight 1, even when the dormant inversion value is saved.

The combined weight blends the existing hue/saturation/code-value exposure adjustment against the original pre-qualifier float source. It does not modify alpha. Domain exclusions from the color key remain: out-of-[0,1] finite values are not selected, and narrow hue selections exclude achromatic samples. Padding is not silently hidden from the matte: if its incoming color matches, the combined key can include it. Source picking still rejects padding.

## Inspection and editing

Open Professional → Color → Hue / saturation / luma qualifier. Set the window shape and numeric position/size, or enable the explicit [window-handle editor](WINDOW_HANDLES.md) to move/resize it directly; inspect the matte, source, adjusted or magenta-overlay view. All local views evaluate the same full-canvas coordinates. Detail cropping, overview sampling, pan, zoom and display DPI do not recenter or resize the mask. Overview is sparse and may miss small detail; Detail samples every fitted-canvas pixel within the current crop.

Display pixels are clipped RGB8. Selection and adjustment operate on the immutable RGB16-derived float32 source, never feedback from the adjusted display or magenta tint. Clicking the source replaces only color ranges and leaves the window geometry unchanged. Pending edits remain local until Apply; Cancel or stale source/session invalidation cannot commit them. A successful Apply is one undoable grade edit. Saved/recovery projects retain the window, and grade copy preserves the destination input interpretation while copying creative window settings.

## Compatibility and verification limits

New saves use schema19 so older readers cannot silently drop the spatial qualifier. Legacy schema18 qualifier settings migrate with the window off; older projects continue their existing migration path. Unknown/partial/new fields in old schemas must reject rather than be silently discarded.

Actual validation results are recorded in the round's acceptance report, not implied by this design contract. Linux/offscreen Qt tests and decoded FFmpeg evidence do not establish native Windows/macOS playback, installer behavior, monitor calibration or HDR accuracy. Preview remains 8-bit; supported ProRes 422 HQ delivery uses the existing 10-bit pipeline and restrictions.

## Competitor comparison and next bounded gap

Checked 2026-10-22 against [Blackmagic Design's official Resolve Color page](https://www.blackmagicdesign.com/products/davinciresolve/color): Power Windows add geometric object selection, adjustable softness and inside/outside correction, while tracking and nodes are separate tools. The original static-window milestone addressed the same-color-background limitation with numeric controls. The subsequent [direct-handle milestone](WINDOW_HANDLES.md) adds on-screen positioning and symmetric resizing while preserving those coordinates. Resolve additionally offers more shape types and broader controls; these are not implemented here.

Direct window positioning/resizing is now covered by the subsequent handle milestone, with numeric parity, cancellation and source-coordinate tests. Additive/subtractive color selection, tracking, matte cleanup and HDR/OCIO remain separate larger gaps. Competitor capability alone is not evidence of algorithm equivalence or native-platform readiness.

# Bounded matte shrink/grow

This is independent fixed-radius matte morphology, not a recreation of Resolve's algorithm or a temporal cleanup feature.

## Mathematical contract

The qualifier first forms the maximum of the base and inclusion ranges, multiplies by one minus the maximum exclusion range, and applies the black/white matte levels. Let this matte be M.

For signed strength s in [-1, 1], take the minimum (s < 0, shrink) or maximum (s > 0, grow) over the full 3×3 square centered on each project-canvas pixel, including diagonals. Call this E. The new matte is M + abs(s) × (E − M). At magnitude 1 the result is exactly E; at zero morphology is bypassed. Strength is a blend, not a fractional geometric radius. The support radius is always one project-canvas pixel for any nonzero strength.

Optional pre-existing binomial smoothing follows morphology. Static window multiplication is last, so it cannot acquire weight beyond its zero-weight boundary. Finite incoming RGB outside [0,1] remains excluded. No RGB image blur is performed.

## Coordinate and boundary contract

Only true project-canvas edges are replicated. Crops read unchanged dense source pixels beyond crop and tile boundaries. Morphology alone requires one pixel of halo; morphology plus smoothing requires two. A sparse overview samples real full-resolution neighboring source pixels rather than treating adjacent sparse samples as adjacent canvas pixels. Display zoom and DPI never change the kernel radius.

Processing remains tiled with bounded scratch buffers and cancellation checks. The output itself remains frame-sized. These resource guarantees are not a real-time performance promise.

## Compatibility

Schema 23 adds matte_morphology; older projects default to zero and cannot silently activate the feature. Zero morphology preserves the smoothing-only and legacy pointwise paths. The value participates in copy/paste, recovery, undo and cache identity through the grade data model. Numeric changes remain pending until Apply.

## Deliberate limits

No variable radius, opening/closing operator, connected-component analysis, tracked/keyframed windows, temporal noise reduction, AI segmentation, HDR/OCIO or claim of native Windows/macOS validation. Synthetic acceptance fixtures never contain user media.

## Competitor reference

The official Blackmagic Resolve 17 New Features Guide describes separate Shrink/Grow matte operations. This is historical capability evidence, freshly rechecked through official search-index text; it is not a statement about undocumented Resolve internals.

https://documents.blackmagicdesign.com/SupportNotes/DaVinci_Resolve_17_New_Features_Guide.pdf

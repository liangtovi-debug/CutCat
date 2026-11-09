> Current update: schema20 now supports bounded independent additive/subtractive color ranges. See [QUALIFIER_SETS.md](QUALIFIER_SETS.md). Earlier single-range/schema statements below describe the original milestone; unchanged coordinate, source, window and output restrictions still apply.

# Contextual selection overlay

Choose **Selection overlay · magenta 50%** in the static qualifier preview.
It displays the frozen pre-qualifier source, with selected pixels tinted magenta.
Fully selected pixels use 50% tint, excluded pixels retain clipped source color,
and fractional weights smoothly scale the tint. The exact display formula is
`clip(source, 0, 1) * (1 - 0.5 * weight) + (1, 0, 1) * 0.5 * weight`.
This is a display annotation, not a color adjustment or alpha channel. Existing
black/white matte, source, and adjusted-before-LUT views remain available.

## Picking through the overlay

Arm **Replace ranges from source click** while overlay is selected. The overlay
stays visible, and the instruction explicitly says that clicks read underlying
float32 source, never tinted RGB8 display bytes. A valid click still replaces
H ±10°, S/Y′ ±10 percentage points, preserving feathers, adjustments and enable
state. The result shows exact source coordinates and RGB/H/S/Y′. After a click,
overlay remains visible. Other preview modes keep their prior source→matte flow.

The same bounded overview and continuous 512/256/128/64-pixel local detail
viewports are used. Use Detail to inspect tiny targets omitted by sparse overview.
Double-click, wheel, middle-pan and X/Y navigation do not mutate saved grades.
Display clipping occurs only for annotation; the range weight is calculated from
unclipped, pre-qualifier float source using the same qualifier math as export.
Correction-disabled or grading-disabled states can still inspect range membership.

## Safety and limits

No model/schema, pipeline, render, undo or recovery format changes. Overlay mode
is never persisted. Only Apply commits the pending qualifier, as one transaction.
Cancel closes and releases the snapshot and display arrays; stale revisions,
source changes and unsupported input guards remain. Display work is bounded to
512×512 and does not keep an additional full-frame source or overlay copy.
Gray/near-gray, out-of-range RGB and canvas padding cannot initialize ranges.
Transparent input, active stabilization and changing restoration geometry remain
unsupported. Fitted-canvas padding can qualify, and overlay intentionally shows
that membership just as matte/export do; it is not a spatial selection mask.

This is inspection support, not Resolve feature parity. Official Resolve Color
materials describe viewing a key, multiple qualifier approaches, windows and
tracking: https://www.blackmagicdesign.com/products/davinciresolve/color
We still lack multi-key additive/subtractive selections, spatial windows/tracking,
matte cleanup, asymmetric feather and HDR/OCIO. A useful next larger step is a
bounded static spatial window combined with the color range, not more cosmetic
inspection controls; it requires matching math, project migration, render,
copy/paste and undo coverage before shipping. Tracking remains a separate scope.

> Current update: schema20 now supports bounded independent additive/subtractive color ranges. See [QUALIFIER_SETS.md](QUALIFIER_SETS.md). Earlier single-range/schema statements below describe the original milestone; unchanged coordinate, source, window and output restrictions still apply.

# Direct manipulation of a static color window

This local-source milestone improves the existing single ellipse/rectangle qualifier window. It is not tracking, keyframing, a freeform mask or a native-platform release.

## Coordinate contract

The window belongs to the fitted project canvas, before the clip's position, zoom, rotation and mirror transform. A later clip transform moves the result with the clip. It does not select in final composition coordinates. Padding remains part of the canvas; existing source-picking exclusions are unchanged.

Display navigation is not a grade edit. The overview is a sparse source grid and detail is a contiguous source crop. On-screen geometry must follow the exact sample-center phase, source origin, stride and rounded device-pixel image rectangle. The display is not treated as an independent normalized canvas. Source sampling reads the immutable pre-qualifier float data, not painted controls or selection tint.

The numerical parameter ranges remain center X/Y in [0, 1], full width/height in [0.001, 2]. Feather and spatial-only inversion retain their previous meaning. Width/height are normalized separately by the canvas axes; equal percentages do not necessarily describe a pixel-space circle. Schema19 is unchanged.

## Editing workflow

Choose an ellipse or rectangle, then enable the explicit window-handle edit mode. Drag the center marker or shape interior to move. Drag the left/right or top/bottom marker to resize symmetrically around the fixed center. This is not an opposite-edge anchored crop gesture. Crossing the center stops at the 0.1% minimum; it does not flip the axes. Centers clamp to the canvas and full dimensions clamp at 200%, so a large window may extend beyond the canvas.

Very small windows retain their exact outline; their handle targets are spread apart on screen for usability. Dragging uses displacement from the press point, so touching an offset marker does not enlarge or jump the window. Numeric controls remain available for precise positioning, including a window whose controls are outside the current detail crop.

Press Escape during a drag to restore the geometry at the start of that drag without closing the dialog. Source picking and window editing are separate modes. Feather and inversion are edited numerically, not by these move/resize handles.

## Safety and transaction contract

Controls are display-only. They must not enter source pixels, the mathematical range matte, saved project fields or preview/export rendering. Pending geometry changes remain in the qualifier dialog. Only Apply changes the project through its existing single undoable transaction; a view-only or no-op Apply creates no edit. Cancel discards pending changes. Stale clip/frame/source state must never apply to a new target.

## Competitor comparison

Rechecked 2026-10-22 against [Blackmagic Design's official Resolve Color page](https://www.blackmagicdesign.com/products/davinciresolve/color). Resolve's geometric Power Windows support direct placement, sizing and softness in the viewer, alongside numeric controls. Tracking, nodes and additional shape types are distinct capabilities.

This round addresses direct placement and sizing only. It does not claim Resolve algorithm parity, rotation/freeform/gradient shapes, multiple windows, tracking, keyframes, additive/subtractive color selection, matte cleanup, HDR or OCIO. After this round is validated, additive/subtractive qualifier selection is a bounded candidate for further competitor comparison, rather than silently extending this implementation.

## Verification limits

Exact acceptance results belong to the accompanying report. Linux/offscreen Qt screenshots and decoded FFmpeg tests do not establish native Windows/macOS behavior, installer correctness, display calibration or HDR accuracy. The offscreen plugin cannot validate the operating system's native mouse capture routing; explicit capture-loss, hide, disable and interruption events are covered locally, with physical desktop checks still outstanding. No remote-main update or native package is implied. Existing unsupported alpha and active-stabilization snapshot restrictions remain.

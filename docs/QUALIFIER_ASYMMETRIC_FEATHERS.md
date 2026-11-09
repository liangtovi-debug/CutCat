# Independent low/high qualifier feathers

The selected base, inclusion or exclusion range has separate low/high exterior feathers for HSV saturation and encoded Rec.709 Y′. Hue remains circular and symmetric. This is a color-range transition, not a spatial blur or HSL lightness. No linked-edit toggle is provided: changing a side never rewrites its opposite.

## Numerical contract

For a channel value v and inclusive hard core [low, high], the weight is 1 inside the core. Below low it is max(0, 1 − (low − v)/f_low); above high it is max(0, 1 − (v − high)/f_high). A zero feather has no exterior ramp. Calculations retain the historical float64 ramp evaluation and float32 weight storage, including subnormal feather handling. Each feather must be finite in [0, 1]. Invalid values are rejected rather than silently clamped.

Sampling outside the core updates only that exterior edge to max(existing effective feather, 2 × distance). This reaches at least 50% of that channel's weight, not necessarily the combined matte. An inside-core sample or already sufficient feather changes nothing, including its inherited representation. Impossible required values reject. The selected range's core, other side, other channels and unrelated ranges stay unchanged. Increasing an exclusion feather expands the removal transition.

Hue sampling retains the circular shortest-distance calculation and its 0.5-turn maximum. Achromatic hue is undefined and rejected; saturation and Y′ can be measured for gray, although an unchanged narrow hue gate still excludes it.

## Compatibility and persistence

Schema24 retains saturation_softness and luma_softness unchanged and adds nullable saturation_softness_low, saturation_softness_high, luma_softness_low and luma_softness_high. Null explicitly inherits the matching legacy symmetric value. Old schemas migrate to null overrides, so their original feather values and numerical outputs are preserved without copying rounded UI values. Explicit side edits write only that override. Opening a dialog, editing an unrelated field or applying an unchanged dialog must not materialize or round inherited values.

The legacy symmetric constructor/assignment fields remain supported for programmatic compatibility; an explicit override takes precedence on its own edge. The UI exposes effective low/high values, not an ambiguous combined legacy control. Every primary and extra range carries the same contract. Older readers reject schema24 instead of silently dropping the new behavior. New readers reject new side fields mislabeled as older schema data.

## Processing, preview and history

The pipeline order remains: frozen float source before qualifier → H/S/Y′ per-range weights → maximum inclusions and exclusion complement → matte levels → morphology → smoothing → static window → selected color adjustment. Source sampling reads the frozen full-precision pixel, never the overlay or display RGB8. Overview and full-resolution detail preserve existing geometry and real-neighbor processing.

All edits remain draft-only until Apply; Cancel or stale source rejection preserves the project. Apply submits one undo transaction only if the exact settings changed. Copy, recovery, save/reopen and render-cache keys include the side values. Neutral legacy settings retain the old render path numerically.

## Competition and boundaries

The current official [Resolve color page](https://www.blackmagicdesign.com/products/davinciresolve/color) describes H/S/luminance qualifiers, drag sampling, windows and tracking. Fresh official search-index evidence from historical [Resolve11 Reference Manual](https://documents.blackmagicdesign.com/UserManuals/DaVinci_Resolve_11_Reference_Manual.pdf), page598, shows separate Sat/Lum Low Soft and High Soft controls while Hue uses Soft/Symmetry. Historical evidence establishes the comparison, not today's identical UI or algorithm.

CutCat's exterior ramps and half-channel sample rule are independently defined. This iteration does not add subtract-softness sampling, drag strokes, separate channel enable switches, tracking/keyframes, multiple/freeform windows, HDR/OCIO, or native Windows/macOS build verification. A bounded next improvement is explicit component enable/disable for each range, allowing saturation-only or luminance-only selections without changing saved bounds. That needs neutral legacy migration, clear disabled-channel UI and complete math/render/history tests before implementation.

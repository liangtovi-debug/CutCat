# Selected-range source-pixel softness

Choose an existing base/include/exclude range and one channel (H, HSV saturation, or encoded Rec.709 Y′), then sample one source pixel. Only that range's selected feather changes. This is not an all-channel range replacement, spatial blur, drag stroke, or a claim of Resolve algorithm equivalence.

## Defined math

Let d be distance outside the selected hard core. For H, d=max(0, min(|h−c|, 1−|h−c|)−w), with canonical circular hue and half-width w. For S/Y′, d=max(low−v, v−high, 0). Set feather f′=max(f, 2d). All core bounds remain unchanged. Existing feather sufficient to give at least one-half channel weight is unchanged. Sampling inside the core is a no-op.

The channel's exterior ramp is 1−d/f′, so a newly extended edge has 50% CHANNEL weight. This does not promise 50% combined matte: other channels, range composition, matte refinement and the window still contribute. Increasing an exclusion feather expands its removal transition. For S/Y′, only the sampled outside edge changes; its opposite effective feather is preserved. Hue remains symmetric. See [independent edge compatibility](QUALIFIER_ASYMMETRIC_FEATHERS.md).

Hue feather is capped by its existing 0.5-turn schema bound; S/Y′ by 1.0. A required value beyond these bounds is rejected, not clamped, and the core is never widened as a fallback. Achromatic or near-gray hue is undefined and rejected. S/Y′ softness can be sampled from gray, but the unchanged hue gate may still exclude achromatic pixels (full 0.5-turn half-width allows them). The UI must explain this distinction.

## Source and persistence

Read the frozen full-resolution float32 source before the qualifier, never the tinted overlay or RGB8 display. Reject invalid padding, nonfinite/out-of-domain RGB, stale snapshots and ambiguous targets. Overview/detail mapping and DPI use the existing source coordinate contract. Source/target/mode changes disarm the one-shot picker.

All changes remain in the dialog draft. Cancel preserves the project. Apply commits one undo entry only if settings actually changed. Schema24 persists independent S/Y′ edge overrides while retaining old symmetric values through explicit inheritance, including copy/recovery/cache behavior.

## Competitor evidence and limits

Current official [DaVinci Resolve Color page](https://www.blackmagicdesign.com/products/davinciresolve/color), freshly read 2026-10-23, describes hue/saturation/luminance qualification and broader tracking/HDR capabilities. Historical official [Resolve 11 Reference Manual](https://documents.blackmagicdesign.com/UserManuals/DaVinci_Resolve_11_Reference_Manual.pdf), fresh search-index evidence, distinguishes range and Add/Subtract Softness pickers. The large PDF could not be directly parsed by the web tool; historical picker text is not evidence of a current identical UI or algorithm.

This implementation independently defines increase-only action (the sampled S/Y′ exterior edge, or circular symmetric hue) for one selected channel/range. It does not implement subtract-softness picking, drag sampling, tracking/keyframes, multiple/freeform windows, HDR/OCIO, or native Windows/macOS build verification.

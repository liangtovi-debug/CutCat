# Independent H / HSV-S / Y′ qualifier conditions

Every base, inclusion and exclusion range has separate strict boolean hue_enabled, saturation_enabled and luma_enabled switches, defaulting to true. This is a condition bypass, not a range mute: a disabled component contributes weight 1 and retains every stored core and feather value. Toggle back on to restore that condition exactly. The controls remain visible but disabled while their condition is off.

All three off means all finite RGB pixels within [0,1] qualify, including gray and black. Extended RGB still never qualifies. Therefore an all-disabled inclusion selects all valid colors; an all-disabled exclusion clears the entire composed color key. The dialog explains this and asks for confirmation before applying correction-enabled settings containing an all-disabled exclusion. No means remain in the dialog without a project edit. Global correction bypass and static preview behavior are unchanged.

A narrower enabled hue condition still excludes near-gray chroma <= 1e-6; a disabled hue condition does not. The existing hue half-width 0.5 also retains its historical bypass. HSV saturation and encoded Rec.709 Y′ are not HSL lightness or linear luminance.

## Sampling and transaction semantics

Replace, Add and Subtract sample only the enabled channels of the BASE range. Disabled channels retain their stored core and feather values and remain disabled. New added/excluded ranges inherit the base switches and feather settings. Gray can be sampled when base hue is off. Replace still explicitly clears extra ranges; it is not a selected-range replacement. All-off Replace may therefore clear extras without changing the base core. All-off Add/Subtract create an all-colors range, with the exclusion consequence described above. Soften operates on the selected range and explicitly rejects a disabled selected channel without changing settings. No picker silently re-enables a channel.

The dialog deep-copy transaction still commits only on Apply. Cancel, repeat cancel, invalid/stale source and rejected confirmation cannot commit. Copy/paste, recovery, undo, saved projects, render fingerprints and stage cache keys carry all switches via canonical serialization.

## Math and compatibility

Weight = H × S × Y; disabled factors are 1. Existing maximum inclusion union, inverse maximum exclusion, levels, morphology, smoothing and one static window keep the same order and arithmetic. The all-enabled path is byte-identical to archived v24 actual mask and corrected-RGB captures, including spatial stages. The float32 stage remains the source for matte and correction; preview display remains 8-bit and ProRes export remains the existing restricted SDR10-bit path.

Schema25 requires all three switches in every range. Schema18–24 accept exactly their historical fields and migrate switches to true; schema24 preserves independent nullable edge overrides. New fields under old version tags are rejected, not ignored. A version24 reader rejects version25 before decoding clips.

## Competitor comparison and next bounded gap

Checked 2026-10-23: the [current official Resolve color page](https://www.blackmagicdesign.com/products/davinciresolve/color) describes component-based qualification. The [historical official Resolve10 reference manual, printed page477](https://documents.blackmagicdesign.com/UserManuals/DaVinci_Resolve_10_Reference_Manual.pdf) explicitly documents independent Hue/Sat/Lum checkboxes and selecting saturation/luminance while ignoring hue. This supports the workflow comparison, not a claim about identical present-day UI or proprietary math.

This round closes independent component enablement. Still absent: reducing a feather by sampling, drag sampling, tracking/keyframes, multiple/freeform windows, node grading and HDR/OCIO. Next bounded proposal: explicitly selected-channel feather reduction sampling with no core changes, respecting independent low/high edges, disabled channels, no-op history and source precision. Tracking is a larger separate engineering task.

## Delivery boundary

Linux source and offscreen test evidence only. No new Windows/macOS native installers, remote-main push, live API call or private-media upload is claimed. Final acceptance counts belong to the matching report and frozen manifest, not this implementation description.

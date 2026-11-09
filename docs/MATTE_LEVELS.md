# Pointwise qualifier matte levels (schema 21)

## Contract

`matte_black` defaults to 0 and `matte_white` to 1. Both must be finite Python
numbers, excluding booleans, with 0 <= black < white <= 1. Equal, reversed,
nonfinite and out-of-bounds points are rejected even when correction is disabled.

For color weight c = max(base, inclusions) * (1 - max(exclusions)), define:
- r = 0 when c <= black;
- r = 1 when c >= white;
- r = (c - black) / (white - black) otherwise.

The final selection is r * window, not a remap of c * window. Thus a zero
window remains zero and a feathered window retains its proportional weight.
Default 0/1 bypasses remapping entirely, preserving prior float32 output bits.
The nonneutral ramp uses float64 intermediates and stores float32 results.
Division occurs only inside the open ramp, including for subnormal valid gaps.
Out-of-range finite RGB is excluded before evaluation. No full-frame temporary
is needed: the existing bounded block traversal is retained.

This is scalar levels, NOT spatial noise removal, blur, morphology or hole
filling. Identical color weights receive identical results regardless of their
neighbors. It does not reproduce Resolve's Clean Black/White algorithms.
No gamma or hidden temporal state is introduced.

## UI and stages

In the qualifier dialog, "Matte levels · pointwise" offers black/white points
in percent and a reset to 0/100. Both endpoints may temporarily be invalid
while editing; Apply and sampling are disabled, and the stale preview is cleared.
Reset repairs only the two levels. All edits remain pending until one Apply
transaction; Cancel, stale frame and no-op Apply preserve the project/history.

Live matte and magenta overlay inspect the refined key even when correction
is disabled. Source sampling still reads frozen float32 pixels after input
normalization, restoration, primary, RGB and hue curves, before qualifier/LUT.
A detail crop uses its original full-canvas origin and stride. Neither UI
zoom nor display quantization changes matte evaluation or export coordinates.
Replacement/additive/subtractive sampling preserves the matte levels.

## Persistence and compatibility

Schema 21 requires both fields. Exact schema 18/19/20 records migrate to
neutral levels; their known field sets are checked before defaults are added.
They cannot smuggle future matte fields. Earlier qualifier-free schemas retain
disabled defaults. Prior readers reject schema21 at the version gate.
Save/load, copy/paste, undo/redo, recovery and grade cache keys carry the levels.

## Competitor evidence and limits

The current [Resolve color page](https://www.blackmagicdesign.com/products/davinciresolve/color)
distinguishes key inspection/refinement, spatial windows and tracking. The
[official Resolve17 beginner guide](https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-17-Beginners-Guide.pdf)
(printed pages319–320) describes matte finesse after selecting color. These
motivate a separate post-key control, not an algorithm-equivalence claim.
Spatial denoise/cleanup, stroke/softness sampling, tracking/keyframes,
multiple/freeform windows and HDR/OCIO remain gaps. No native Windows/macOS
package or remote main update is established by this local source milestone.

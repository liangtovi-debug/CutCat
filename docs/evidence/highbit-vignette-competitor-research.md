# CutCat vignette workflow: primary-source comparison

Verified: 2026-10-23 UTC. Research only; no source files changed. Competitor facts below come from official Adobe and Blackmagic documentation. CutCat status is the implementing worker's supplied scope, not an independent build or visual acceptance test.

## Useful conclusion

An independent radial vignette is a sensible bounded improvement, but another amount-only control does not close the most actionable competitor gap: separately controlling the affected area and edge softness. Prioritize midpoint/radius and feather next. Keep this iteration described as high-bit SDR processing plus an independent radial vignette, not Lumetri/Resolve parity, HDR support, or an ICC-managed preview pipeline.

## Verified competitor behavior

### Premiere / Lumetri

Adobe documents four independent controls: Amount can darken or lighten; Midpoint controls how far the affected region reaches inward; Roundness changes geometry; Feather changes edge softness. Adobe specifies that the vignette follows other corrections within that Lumetri instance. Its separate mask workflow provides shape and position control beyond the frame-centered effect. This establishes a useful UI and sequencing reference, not an identical mathematical transfer function or an export-quality benchmark. [Adobe Lumetri vignette documentation](https://helpx.adobe.com/premiere/desktop/add-video-effects/commonly-used-effects/create-vignettes-using-lumetri-color.html), updated 2026-08-18.

Adobe's dedicated mask page describes custom shapes, off-center subjects, mask tracking, and inverted selection. It is a distinct workflow, not an implied capability of the four Lumetri vignette sliders. [Adobe mask-based vignette documentation](https://helpx.adobe.com/premiere/desktop/add-video-effects/commonly-used-effects/create-vignettes-using-masks.html), updated 2026-08-18.

### DaVinci Resolve

The official Colorist Guide teaches a serial-node vignette using a circular Power Window, resizing/repositioning, wide soft edges, inverted selection, and a Gamma adjustment. Its rationale is to avoid making bright areas such as sky too obviously dark. The window can be saved as a preset, and the guide separately identifies Vignette in Resolve FX Stylize as a quick customizable alternative (printed pp. 81–82). A later example preserves the window's position relative to the clip when changing 1080p to UHD at the same aspect ratio (pp. 344–346). This does not establish behavior for arbitrary crops or aspect-ratio changes. [Blackmagic official Colorist Guide to Resolve 20](https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-20-Colorist-Guide.pdf).

Blackmagic's current Color page separately confirms Power Window geometry, position, rotation, softness, inside/outside grading and motion tracking. It advertises 32-bit image processing, wide-gamut/HDR support, nodes and keyframeable Resolve FX. Those are broader pipeline features and are not evidence that any CutCat RGB16 implementation achieves the same color-management behavior. [Blackmagic Resolve Color](https://www.blackmagicdesign.com/products/davinciresolve/color).

The currently linked official training book is Resolve 20 even though the product page displays Resolve 21. These references are intentionally version-qualified; no claim was made that every control or behavior was exhaustively tested in Resolve 21. [Blackmagic training index](https://www.blackmagicdesign.com/products/davinciresolve/training).

## CutCat scope and remaining gaps

Supplied current scope: existing ordered blur/vignette effects; a legacy vignette slider from 0 to 1 mapped into an angle up to pi/2; high-bit SDR RGB16 processing in grade → effects → transform order, then 10-bit ProRes export. An additional independent radial vignette is being implemented. Midpoint, roundness and feather controls are not yet exposed. HDR and ICC support are not claimed.

1. **This iteration closes a narrow separation/precision gap.** An independently implemented radial effect can avoid coupling its behavior to the legacy effect and preserve high-bit intermediate precision. Completion still needs pixel tests and exported-file evidence; 10-bit encoding alone is not proof that intermediate processing stayed high-bit.
2. **Next: radius/midpoint and feather.** These let users protect the subject and soften transitions without changing darkness. Add roundness/aspect and center X/Y afterward for portrait, landscape and off-center compositions. Keep signed lightening as a separate explicit decision if only darkening is currently supported.
3. **Later: tonal protection and local grading.** Resolve's Gamma/window workflow suggests testing whether uniform darkening unnecessarily suppresses highlights. Treat a future highlight-protection or tonal-response option as a deliberate feature, not as parity implied by a radial mask.
4. **Define geometry and ordering.** Grade-before-vignette resembles the documented Lumetri finishing order, but an ordered effect list is not Resolve's node graph. With vignette before transform, scaling/cropping can move or remove the edge darkening. Specify source-relative versus output-frame-relative geometry; do not silently change existing project behavior.
5. **Keep both implementations unambiguous.** A legacy vignette and a new radial control may accumulate. Label their roles, verify enable/bypass behavior and project serialization, and do not migrate saved numeric values as though the algorithms were equivalent.

## Recommended acceptance evidence for this round

- Neutral/zero setting and disabled effect are exact bypasses; toggling the independent effect does not rewrite the legacy one.
- Neutral-gray and RGB ramps retain smooth gradients after grade → ordered effects → transform; inspect clipping, channel ratios and corner symmetry. Measure actual unique levels before encoding and inspect encoded pixel format/bit depth separately.
- Effect order visibly and reproducibly changes non-commuting combinations such as blur/vignette. Preview and export share the intended formula and operation order.
- Landscape, portrait and square frames; odd dimensions; resize/crop/rotation; reduced preview versus full-resolution export. Define tolerances before judging matches.
- Save/reopen, undo/redo, reset and zero/max values preserve behavior. Compare both effects enabled versus each alone to catch accidental double application.

These are proposed engineering checks, not claims that they have passed. The source research makes no direct quality, performance or numeric-output equivalence claim against commercial applications.

## Retrieval note

The web reader could not ingest the 24,358,573-byte official Resolve PDF due to its content-length limit. The same official URL was successfully downloaded and text-extracted locally; printed page references above were checked against that text. No competitor executable, nonofficial tutorial or user media was used.

# Beauty reference and acceptance plan

Planning only, 2026-10-15. The user identified Meitu Wink as a quality reference. No CutCat beauty implementation or measured equivalence is claimed.

## Evidence limits

Official pages read by the research worker:
- https://wink.cn/ai-skin-retouching describes adjustable strength, texture-aware skin smoothing, manual regions and body skin consistency. Its FAQ acknowledges lost detail and flattened lighting at excessive strength.
- https://wink.cn/ai-face-editor describes individual controls, left/right adjustments and saved recipes. Its FAQ acknowledges that reshaping may pull hair or backgrounds.

These are vendor text claims. No continuous demonstration video was evaluated; temporal stability and proprietary algorithms were not verified. Reference media/assets must not be copied into the product.

## Proposed CutCat acceptance, not vendor specifications

1. Disabled/zero-strength processing is pixel-identical to the original shared renderer.
2. Licensed, annotated skin/protected-region fixtures report skin high-frequency retention and mean/maximum changes in eyes, eyebrows, lips and hair. Preserve face lighting and texture, not merely whitening or global blur.
3. Motion-compensated frame analysis records skin luminance/chroma variation and mask overlap. Inspect peak temporal changes manually.
4. Test turns, profiles, hands, glasses, facial hair, entry/exit, occlusion and scene cuts. Low-confidence segmentation should return smoothly to the original; no background stretching or accidental non-skin smoothing.
5. The same timestamp uses the same evaluator in preview/export. Strength sweeps must be continuous. No default face reshaping.
6. Report p95 preview latency, export throughput and peak memory separately for tested hardware.

Numeric acceptance thresholds must follow measured baselines and visual review, not invented targets. Initial candidate controls: smoothing strength, detail protection, restrained skin-tone adjustment, original comparison, reset and saved recipes. Strong sharpening cannot recover authentic missing texture. Model and runtime licenses, offline operation, two-platform compatibility and asset provenance are independent release gates.

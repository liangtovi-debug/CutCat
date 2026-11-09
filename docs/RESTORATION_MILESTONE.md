# Local quality restoration v1

Verified 2026-10-16: main `cb47ef721b8d6cc4358aa016c59a67096b338b94`, [native run 38837683338](https://github.com/liangtovi-debug/LumenEdit/actions/runs/38837683338) succeeded on both platforms. Windows: 2100 passed / 7 POSIX-only skips; macOS: 2106 passed / 1 Windows-only skip. Local Linux: 2106 passed / 1 Windows-only skip. Each native platform passed 450 real Qt layout states and re-extracted frozen-runtime smoke with 18 bilingual screenshots. These are CI runner tests, not clean consumer-desktop certification.

## Scope

Optional, local spatial processing of a selected picture clip. Three independent strengths control weak compression-discontinuity suppression, edge-aware spatial denoise, and restrained sharpening. Defaults are disabled and zero strength. This is conservative signal processing, not AI super-resolution, recovery of missing detail, temporal restoration, face beauty, or motion deblur.

The implemented shared render order is declared source decode and source normalization, restoration of the active picture in normalized Rec.709-encoded float32 RGB, then existing creative grading/LUT. Stabilization and exact source-time selection retain their established placement. No intermediate 8-bit conversion is introduced. The existing SDR RGB48 intermediate and final delivery codec boundaries still apply. Letterbox padding must not participate in restoration neighborhoods.

Settings belong to each picture clip and persist in schema 9. Earlier schemas load with restoration disabled. Apply/reset/enable changes are undoable, split copies independent settings, and simple/professional or language switching preserves parameters. Independent audio, subtitle, speech and sticker timing must remain unchanged.

## Required acceptance before delivery

- Exact disabled/all-zero identity, finite float/out-of-range preservation, constant/ramp/edge behavior and bounded memory/cancellation.
- Known synthetic noise and compression degradation measured against clean references, with separate edge/detail preservation checks and sharpening halo limits.
- Shared actual preview/export path, source integrity, exact retime/split phase, unchanged audio, cache invalidation and cancellation recovery.
- Migration/roundtrip/undo, both languages and workspaces, narrow inspector, real Qt UI snapshots and geometry checks.
- Both native regression suites and re-extracted frozen-program acceptance, including true local restoration and bilingual screenshots.

Quality and performance measurements will be reported with the exact fixtures, dimensions and machine context. More smoothing can remove genuine detail; repeated low-contrast structures may resemble compression artifacts. No general quality guarantee or real-time HD/4K performance is implied.

## Implemented algorithm and practical guidance

The owned NumPy evaluator works in bounded 64-row strips. Deblock identifies repeated weak, flat-sided discontinuities without assuming a fixed codec grid; strong or isolated edges are protected, but repeated low-contrast graphic patterns remain ambiguous. Denoise performs one 3×3 edge-aware spatial pass. Sharpen applies a capped local residual constrained to the neighboring sample envelope, preventing new local extrema; it can still amplify noise already present. All passes are deterministic for one input frame and retain finite signed floating-point samples rather than clipping to 0–1. No temporal history is used, so temporal consistency is not guaranteed.

Start with denoise at a low or moderate value, inspect fine texture, then add only modest sharpening if needed. Use deblock when weak repetitive compression boundaries are visible; inspect text, line art and patterned fabric carefully. Review moving footage rather than only a still image. Zero strengths or disabled restoration preserve the prior picture path exactly. Before comparison bypasses restoration and creative color corrections, while retaining required source normalization and existing stabilization; it does not replace explicit per-feature bypass when isolating one effect.

Final local numerical/media fixture results: seeded moderate-noise RMS improves ~39%; real H.264 fixture flat-region RMSE 3.294→1.623 with denoise, weak-boundary RMSE 5.690→2.878 with deblock, softened-edge RMSE 16.261→15.916 with sharpen. Sharpen alone worsens that fixture’s flat-noise RMSE to ~3.66; combined moderate denoise/sharpen is ~1.77. These separate measurements show the tradeoff rather than asserting every setting improves every source. Screenshots use original procedural QA media, not customer material or an AI-enhanced demonstration.

## Native acceptance details

Original 320×240, 24-frame / 24 fps procedural fixture, with a 440 Hz audio tone. Windows decoded denoise flat RMSE: 3.294→1.625; deblock boundary RMSE: 5.690→2.878; sharpen soft-edge RMSE: 16.261→15.916. Mac: 3.722→2.230, 5.722→2.931, and 16.391→16.016 respectively. Platform codec builds produce different quantization; these are separate per-platform measurements. Sharpen alone increases noise, as reported in the complete JSON. Both retain source bytes and exactly 48,000 decoded audio samples with zero sample difference; preview/export mean pixel differences are 0.569 (Windows) and 0.613 (Mac) on a 0–255 scale after lossy delivery encoding. No perceptual, temporal or real-time HD/4K guarantee follows.

The test suite now rejects collected node IDs over 1,024 UTF-16 code units before fixtures run, preventing huge parameter strings from overflowing Windows environment blocks. Explicit IDs preserve all original large payloads and assertions. CI runs this collection gate before external runtime provisioning.

Delivery source includes this post-validation documentation update; runtime code matches the tested main commit. Historical local delivery documents retained from earlier archives are not evidence of new remote commits. Optional inference models/runtimes, FFmpeg binaries and CI-only CJK fonts are excluded from native packages.

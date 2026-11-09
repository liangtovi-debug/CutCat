# Explicit Apple Log 1 / BT.2020 input (experimental, schema29)

This is a bounded, synthetic-validated input interpretation, not automatic camera detection, arbitrary iPhone-file qualification, Apple Log 2, an Apple display LUT, or Resolve matching. The original Apple Log uses BT.2020 primaries; Apple Log 2 uses Apple Gamut and is deliberately excluded. References and pinned transform provenance are in [APPLE_LOG_MATH.md](APPLE_LOG_MATH.md).

## Import and interpretation

Use **Import Apple Log 1 / BT.2020 (experimental)**, separately from normal import. Filename, codec brand, image appearance, and BT.2020 tags never select Log automatically. The explicit action records `Media.input_encoding = apple_log_bt2020`; each new picture clip inherits that interpretation in `Grade.input_encoding`. Ordinary import keeps its previous strict SDR contract and byte-identical neutral evaluator path.

The supported experimental media profile requires all of:

- Transfer tag unknown/unspecified/reserved, **explicit BT.2020 primaries**, no mastering/content-light/Dolby Vision/HDR dynamic side metadata.
- Full-range (`pc`) RGB16: `rgb48le`, `rgb48be`, `gbrp16le`, or `gbrp16be`, and RGB/GBR or missing matrix metadata. Missing matrix on an RGB pixel format does not select a YUV matrix.
- Or 10-bit planar YUV `yuv420p10le`, `yuv422p10le`, or `yuv444p10le`, explicitly `bt2020nc` matrix, and explicitly `pc` or `tv` code range. Matrix/range are never inferred on the Log path.

Known SDR transfer tags, PQ/HLG, other primaries, unknown YUV matrix/range, lower/higher precision formats, alpha, and Apple-specific transfer identifiers not exposed through the tested FFmpeg profile are rejected. This is intentional, including files that a camera may actually have recorded in Log but mislabeled. There is no silent override or promised automatic repair. Use a verified external conversion for unsupported sources.

Changing a default-imported clip to Apple Log in the inspector is rejected: import it explicitly instead. Likewise, the explicit Log marker cannot silently become SDR through its dropdown. Relinking preserves interpretation and checks every referencing clip before mutation; mismatched sources leave the project unchanged. Creative copy/paste/reset preserve the target's input interpretation. Undo/redo and save/reopen retain it. Versions below29 cannot carry the new Apple Log interpretation; old SDR projects migrate with a default marker and no pixel change.

## Exact order and SDR look

1. FFmpeg/zscale interprets only the declared YUV matrix and code range, producing full-range RGB16 **still encoded in Apple Log**. RGB16 sources retain raw code values.
2. Convert RGB16 codes to normalized floating values. Inverse original Apple Log gives scene-linear BT.2020.
3. Convert linear BT.2020 directly to linear Rec.709 with a matrix derived from chromaticities and shared D65. No intermediate ACES gamut is needed.
4. Apply **CutCat SDR shoulder v1**, fixed exposure1. Luminance through18% remains unchanged. Above18%, a smooth exponential shoulder approaches1. A neutral-axis gamut compression then reduces saturation just enough to fit the SDR cube while preserving the mapped luminance. Nonpositive luminance becomes black. This is a defined rendering policy, not simple channel clipping or recovery of clipped camera information.
5. Encode with the BT.709 OETF, then use the existing float32 Rec.709 creative grading stack and final RGB16 quantization. The normalization calculations use float64 for numerical stability before returning float32; this does not create more source precision.

The shoulder is fixed, not adjustable exposure matching or a scene-referred grading environment. It can desaturate extreme colors substantially. Creative exposure and white balance currently operate **after** this SDR rendering, not before tone/gamut mapping. Existing geometric resizing precedes normalization in encoded Log and is not scene-linear resampling. RGB16 ingress clips negative/super-white code values; it is not a floating codec interface.

Before/After and disabled correction preserve normalization. Paused-frame grade preview, hue/qualifier/white-balance sampling, render caches and final export share the same normalization; no technical LUT is automatically added. Do not add a Log-to-SDR technical LUT afterward, as that would apply a second conversion. The paused-frame view is selected-clip before effects/compositing, not final timeline pixels. H.264 preview/scopes remain8-bit SDR; ProRes delivery follows the existing verified10-bit SDR output profile. Neither is calibrated HDR monitoring.

Active stabilization is explicitly unsupported on experimental Log until its immutable snapshot metadata pipeline is qualified. The option is not silently ignored. Restoration follows normalization; unrelated existing effect/compositing limitations remain in force.

## Verification boundaries

Tests generate synthetic FFV1 media, including actual RGB16 and10-bit YUV with full/limited range. Independent scalar BT.2020 YUV decoding is compared with raw decoded RGB16, separately from the Log math. Varying luma and RGB patterns check channel order/range. Chroma subsampling/siting is not a claim of identical reconstructed sharp chroma edges across codecs. The math suite independently checks curve segments, round trips, derived matrices, luminance shoulder and gamut extremes. Integration compares lossless graded RGB16 caches, sampling stages, decoded preview/export and scope inputs within their actual output quantization limits.

No private footage, camera reference captures, new paid services or LUT downloads were used. Synthetic FFV1 testing does not qualify HEVC/ProRes/iPhone container conventions. Native Windows/macOS packages, remote main and publication are not established by these local tests. Final counts and source/archive hashes belong to the frozen delivery record, not this implementation description.

## Competitor comparison and remaining gap

[DaVinci Resolve20 Colorist Guide, pp170–176](https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-20-Colorist-Guide.pdf) separates input interpretation, working space, output space and display rendering, supports per-clip input overrides and tone/gamut mapping. This round closes one narrow gap: an explicit, separately persisted original Apple Log interpretation feeding a specified SDR rendering. It does **not** close Resolve's broad camera-space support, scene-referred color management, advanced gamut mapping, calibrated monitoring, adjustable DRT or camera-qualified transforms. Next meaningful work is qualified real-camera decoder fixtures and pre-render scene-linear exposure/WB, rather than adding unverified camera names to a preset list.

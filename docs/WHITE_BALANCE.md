# Neutral-reference white balance (schema 27)

## Competitor comparison and chosen scope

Blackmagic's Resolve training documents separate white-balance temperature/tint adjustments from other primary controls. Its Fusion tool reference explicitly warns that a custom neutral sample must come from the source, not the white-balance output. CutCat previously provided only artistic encoded-RGB warm/tint offsets, which are not a neutral-reference white-balance correction. This milestone adds the missing bounded reference workflow, not a claim to reproduce Resolve's proprietary processing.

Choose an object you know was neutral gray or white under the intended illumination. Do not select skin or an arbitrary colored object. There is no automatic detection of neutrality, mixed-light correction, tracking, spectral reconstruction, or skin protection. Unknown/clipped camera data cannot be recovered by channel gains.

Primary references:
- ITU-R BT.709-6, §1: [transfer and Rec.709/D65 primaries](https://www.itu.int/rec/R-REC-BT.709/en).
- Blackmagic Design: [DaVinci Resolve 15 Definitive Guide, Making White Balance Adjustments](https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-15-Definitive-Guide.pdf).
- Blackmagic Design: [Fusion 9 Tool Reference, White Balance](https://documents.blackmagicdesign.com/UserManuals/Fusion9_Tool_Reference.pdf).
- [W3C CSS Color 4 conversion code](https://www.w3.org/TR/css-color/#color-conversion-code) describes Bradford adaptation. That is a different model and is deliberately not used or claimed here.

## Exact contract

Input is the selected clip's normalized, spatially restored Rec.709-encoded RGB16-derived source frame. The picker does not sample the final preview, LUT output, exposure-adjusted image, qualifier output, or its own white-balance result. Geometry is the fitted pre-grade canvas. Padding is invalid; unsupported alpha/stabilization retains the existing picker fail-closed behavior.

The known-neutral reference is decoded with the existing inverse BT.709 OETF. With linear reference channels `r,g,b`, compute `Y=.2126*r+.7152*g+.0722*b`, then diagonal gains `[Y/r,Y/g,Y/b]`. These are the project's explicit rounded Rec.709 luminance weights. The sampled neutral retains its linear luminance and becomes neutral. Other colors generally change luminance. This is global per-clip linear-RGB correction, not XYZ/Bradford chromatic adaptation, camera calibration, an absolute illuminant measurement, Kelvin temperature, or a guarantee of perceptual appearance matching.

Each encoded sample channel must be strictly between 0.02 and 0.98. Non-finite, malformed, padding, out-of-range, dark and near-clipped references are rejected. Each computed gain must be in [0.25,4], otherwise the entire sample is rejected; gains are never silently clamped. These conservative thresholds are product safety limits, not universal colorimetric constants. A true or numerically near-neutral reference (all gains within 1e-6 of one) yields exact identity gains, avoiding an unnecessary rounded-transfer roundtrip.

Processing order:
1. Existing source metadata validation, RGB16 decoding and geometry.
2. Explicit input normalization (Rec.709, sRGB or linear Rec.709).
3. Independently enabled spatial restoration on active picture only.
4. Decode Rec.709, apply enabled neutral-reference gains, then linear exposure, then lift/gain/gamma, then re-encode.
5. Existing encoded exposure, contrast/pivot, saturation, artistic warm/tint, tonal offsets, RGB curves, hue curves, qualifier and LUT.
6. Existing RGB16 cache clipping/quantization and selected delivery format.

WB shares the existing linear block, so it adds no intermediate clipping or extra transfer roundtrip. Disabled WB and enabled [1,1,1] preserve old results bit-for-bit. Creative bypass disables WB but not source normalization or independently enabled restoration. The original warm/tint controls keep their historical meaning and are not renamed as calibrated white balance.

## Persistence and UI

`Grade.white_balance_enabled` and `Grade.white_balance_gains` are appended fields. Schema 27 requires both; schema 1–26 rejects smuggled new fields and migrates absent values to disabled identity. Future versions continue to fail closed. Settings participate in normal grade copy/paste, project serialization, cache identity, undo/redo and recovery without copying the target's input encoding.

Opening or clicking the picker does not modify the grade. Applying a valid still-current sample makes one ordinary undoable edit. Selection/project/revision/playhead/source changes invalidate the snapshot. Cancel, close and late callbacks do not commit. The source image is shown as clipped 8-bit presentation only; sampling uses the retained float32 frame. Reset and enable are explicit controls.

## Boundaries

SDR Rec.709 primaries only. No new support for RAW, camera Log, HDR, wide gamut, ICC display calibration or unbounded float codecs. Source RGB16 and final delivery still have their documented clipping/quantization boundaries. Gains may push other colors out of gamut; that headroom survives the evaluator but is clipped at the existing RGB16 boundary. Source noise, mixed lighting, glare and subject color make some references unsuitable even when the numeric safety checks pass.

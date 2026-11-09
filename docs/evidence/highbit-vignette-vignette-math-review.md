# Independent RGB16 vignette review

Environment: Debian FFmpeg 7.1.5, single filter thread, synthetic local pixels only. No production code was edited by this reviewer.

## Primary-source behavior

FFmpeg's [native vignette implementation](https://raw.githubusercontent.com/FFmpeg/FFmpeg/n7.1/libavfilter/vf_vignette.c) accepts only 8-bit formats. With default center and square pixels, signed offsets are truncated toward zero from x-width/2 and y-height/2. Their Euclidean length is divided by hypot(width/2,height/2); gain is cos(angle*distance)^4. Gain is zero outside normalized radius one. The old CutCat angle is amount*pi/2. Legacy angle clipping and its stored gain map use float32; multiplication and output rounding therefore need not exactly match higher precision. Native vignette uses source SAR and the aspect option to scale x or y; CutCat sets SAR to one before effects, so the tested RGB16 formula is valid for its canvas, not arbitrary non-square-pixel inputs.

FFmpeg's [geq implementation](https://raw.githubusercontent.com/FFmpeg/FFmpeg/n7.1/libavfilter/vf_geq.c) advertises native GBRP16 and evaluates expressions in double precision, then assigns to uint16. It does not saturate arbitrary expression results, so keeping this multiplier in [0,1] matters. Nearest sampling at integer X,Y is sufficient and avoids bilinear edge behavior varying across FFmpeg versions. It computes the expression for each pixel, each plane, each frame, unlike native vignette's reusable gain map.

## Independent experiments

- gbrp16le -> native vignette with -noauto_conversion_filters fails as expected with no common pixel format.
- gbrp16le -> geq -> gbrp16le succeeds with conversion disabled. An initial 14x10 scalar experiment had zero error and 214 distinct low-byte values.
- A 15x11 RGB8 native vignette frame matches the independent signed-trunc/float32 scalar formula exactly. Omitting trunc produces errors of up to20 8-bit codes, affecting every sample in this fixture.
- tests/test_highbit_vignette_math.py exercises 1x1,1x9,9x1,2x9,9x2,15x11,16x12, amounts .001/.125/.5/1, every final edge, channel separation, exact centers, multiple sequential vignettes, and disabled/zero bypass.
- Fine inputs that collapse to one value after explicit 8-bit quantization remain distinguishable. Both input and output precision-destruction controls exceed50 RGB16 codes of oracle error. Actual RGB16 output differs from the independent ideal scalar by at most one code; sequential applications permit two accumulated rounding codes.
- One-code channel steps 10001/10002/10003 are retained exactly at the unity center.

## Practical performance

See math-benchmarks.txt and multiframe-benchmarks.txt for measured timings. Single-frame numbers include process startup and pipe transfer, so they are not pure kernel timings. On this CPU, 1080p geq required about1.2s for a frame, versus about0.14s native8 and0.11s no-op. The native8 figure is not a precision-compatible substitute. The independent expressions using pow versus a local register plus repeated multiplications showed no advantage for the latter. Multiple-frame tests confirm geq is an export-cost feature, not real-time playback. No 4K throughput or macOS/Windows timing claim is made.

## Limits and recommendations

- Preserve the high-bit double-precision contract instead of emulating 8-bit/YUV loss; document that legacy output is not bit-identical.
- Keep square-pixel normalization before the effect and native runtime capability probes with conversion disabled.
- Preserve zero/disabled graph bypass and nearest sampling; both have explicit tests.
- An eventual cached high-bit gain mask could improve speed, but would require its own precision, frame-lifecycle, ordering, and platform validation. It is not part of this implementation or acceptance claim.

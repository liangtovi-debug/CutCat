# Full-resolution selective-color CPU optimization

## What changes for editing

Hue vs Hue / Hue vs Saturation processing now spends less time on temporary
arrays and three-channel reductions. Existing source picking, offline preview
rendering and exports use the same float32 evaluator, at the same resolution.
There is no new control, schema migration, dependency, GPU path or proxy quality
switch. Schema27 and the existing neutral-reference white balance remain intact.

The application still renders the **whole timeline preview before playback**.
This improves one measured CPU bottleneck; it does not make adjustment dragging
or 4K playback real-time. Cached preview hits and reference-still painting do not
re-evaluate the selective curves and should not be credited with this speedup.

## Exact implementation contract

Only `lumen/selective_color.py` changes production code:

- Read curve x/y coordinates separately rather than gathering two N×2 matrices.
  Keep float64 control coordinates, searchsorted behavior, and expression order.
- Use R→G→B elementwise minimum/maximum chains for fixed three-channel pixels.
- Gather hue-channel data from one-dimensional views, with the same sector ties.
- Replace a dense all-true rotation selection with a full slice; sparse masks
  still use their exact selection. No sampled-neutral pixel is processed.
- Compute three-channel luma in the original left-to-right order, including the
  reduction's initial positive zero. This is necessary for signed-zero and
  subnormal parity; no reassociation, fused operation or lower precision is used.
- Reuse the already gathered saturation pixels instead of gathering the block
  a second time. Preserve explicit factor-zero gray handling and nonmutation.

The 65,536-pixel temporary bound, validation, finite-value failure behavior,
extended signed/above-one range, stage order and output ownership stay unchanged.
No export is downsampled, no enabled stage is skipped, and no precision is lost.
No whole-frame or unbounded cross-frame cache is added.

The frozen v20 evaluator in the test fixtures is an independent *compatibility*
oracle. Existing scalar colorsys, RGB16 codec and published Rec.709 matrix tests
remain independent *mathematical* oracles. New uint32 comparisons cover dense and
sparse selections, narrow double control coordinates, signed zeros, subnormals,
noncontiguous/read-only arrays, block boundaries and exceptional arithmetic.
Local bit equality is established for the recorded Linux CPU / NumPy 2.2.4 build.
NumPy's cross-platform signed-zero tie behavior is not a portable API promise;
the same parity tests must also pass on Windows/macOS before claiming native
cross-platform bit equality. No native build is claimed in this milestone.

## Reproducible evidence and limitations

See `COLOR_PREVIEW_PERFORMANCE_RESULTS.json` for final timing, memory and parity
results. Run the bundled benchmark script with the pinned test environment:

    python scripts/benchmark_color_render.py
    python scripts/benchmark_color_viewer.py

They generate synthetic local fixtures only. No user media or private uploads
are needed. Output goes to `.color-performance-benchmark` in the working directory
(or `LUMEN_COLOR_BENCHMARK_OUTPUT`). The render benchmark is intentionally slow:
five cold independent runs per mode/resolution/output, alternating legacy and
optimized order, plus cancellation. It measures 1920×1080 eight-frame and
3840×2160 four-frame timelines through actual RGB16 FFV1 ingress, the shared
grade stage, H.264 preview and ProRes 422 HQ 10-bit SDR output. Cold process peak
RSS is separate from fixture generation and records the Python process through
render completion, before validation decodes; FFmpeg subprocess memory is not
included. Decoder/encoder costs are included in end-to-end time. Median and p95 from only five render runs are descriptive, not
population latency guarantees. Individual grade-frame distributions are also
recorded. Cancellation is triggered deterministically on entering the first
color evaluation; latency includes the remaining frame evaluation and cleanup,
not a promise of immediate per-block interruption.

An unchanged-path diagnostic measures 60 real Qt-allocated YUV420P frame
conversions, reference captures, sampled scopes and offscreen reference painting
at both source resolutions. Painting uses a 1280×720 UI viewport. Scopes retain
their pre-existing, disclosed 256-pixel maximum sampling dimension; this is **not**
a render-quality change. These timings are not a native GPU/display benchmark,
not a decoder throughput guarantee and not calibrated colorimetry.

Correct Rec.709 matrix/range metadata, fail-closed unsupported formats and queued
seek/revision guards are untouched and re-tested. Reference images remain
session-only decoded 8-bit pixels, never a 10-bit/HDR source comparison.

## Competitor comparison and the next useful gap

Blackmagic's official [Resolve Color workflow](https://www.blackmagicdesign.com/products/davinciresolve/color)
describes direct wheel adjustment, selective curves with a live histogram, and
comparison tools for matching shots. Its [collaboration workflow](https://www.blackmagicdesign.com/products/davinciresolve/collaboration)
also documents per-user cache and monitoring preferences. Those pages establish
workflow features, **not** a fair CPU speed comparison with this prototype.
No Resolve performance numbers, equivalent algorithms or superiority are claimed.

CutCat now reduces full-resolution CPU work without compromising its existing
SDR arithmetic, but the material remaining gap is the adjustment feedback loop:
changing a curve still waits for complete preview rendering. The next bounded
improvement should be an explicitly labelled **selected-frame draft preview**
with cancellation, generation/revision guards and full-resolution sample/export
separation. It should make a real adjustment visibly reviewable sooner, rather
than merely adding another benchmark or claiming real-time GPU parity.

## Delivery scope

Source/offscreen Linux verification only. No newly built Windows/macOS installer,
remote-main publication, paid API, camera Log/RAW/HDR pipeline or automatic
matching is included. Earlier test packages do not contain this change.

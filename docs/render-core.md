# Render core and precision contract

Preview playback, still previews and H.264 export use one `RenderEngine`. Every
video clip passes through `lumen.color.apply_grade`, the owned NumPy float32
Rec.709 evaluator. No FFmpeg grade filters or independent UI-only grade path
remain. FFmpeg handles decoding, frame selection, image geometry, lossless
transport, compositing and delivery encoding.

## Actual pipeline

1. Re-probe the original sources and validate the project before rendering.
2. Decode the first non-cover-art video stream. Normalize timestamps, trim by
   source-in and duration in project-frame units, normalize again, and use
   FFmpeg's `fps` filter at the rational project rate. This is exactly the
   existing timeline's frame selection order. VFR and mixed-rate media are
   duplicated/dropped by FFmpeg at this step; this is not a source-frame-index
   editor and does not interpolate temporal frames.
3. Normalize sample aspect ratio to square pixels, then fit/pad to the project
   dimensions (including FFmpeg's normal autorotation),
   interpreting accepted YUV metadata as Rec.709. Before geometry, zscale
   converts YUV to planar RGB16 with explicit Rec.709 matrix and source range.
   Tagged full range (or legacy yuvj formats) stays full; untagged YUV is
   interpreted as limited range. RGB sources use an identity matrix. Convert source range to
   full-range RGB48, then normalize uint16 samples to float32 RGB code values.
   Source precision is never first reduced to eight bits. Unknown source
   primaries/transfer are assumed SDR Rec.709; HDR and explicitly unsupported
   primaries/transfer remain rejected by media inspection.
4. Run the same float evaluator for preview and export: linear-light exposure,
   RGB lift/gamma/gain in linear light, legacy code-value gain,
   contrast/saturation, temperature/tint and tonal offsets, master/channel
   curves and optional 3D LUT.
   Padding precedes grading, preserving the previous compiler's behavior:
   lift/curves can tint the letterbox bars. See `color.py` for precise numerical
   definitions. Intermediate
   negative and above-one values remain floating point inside this evaluator.
5. At the SDR transport boundary only, clamp to [0,1], round to 16-bit and encode
   losslessly as planar RGB16 FFV1. This is a deliberate bounded-memory CPU
   implementation, not a claimed end-to-end floating-point codec pipeline.
6. Composite the graded segments with existing timeline gates; source audio is
   always read from the originals, with its original offset/trim/gain/fades.
   The timeline output is limited-range Rec.709 8-bit YUV 4:2:0 H.264 plus AAC.
   Preview uses a faster H.264 preset/CRF, so preview and export need not be
   byte-identical even though grading and frame mapping are shared.

## Cache, bounds and safety

Only used clip segments are processed. RAM holds one decoded frame plus the
float evaluator's temporary arrays, independent of clip duration. FFV1 segments
are cached per engine by source stat, source trim, project dimensions/rate,
complete grade and LUT stat. The cache is private temporary storage, deleted
when the engine closes; it may consume significant disk space for long edits.
There is no real-time/GPU-performance claim.

The grading pipe's watchdog kills decoder/encoder on cancellation or timeout;
partial intermediates are removed. The delivery process has the same controls.
Timeout applies to each segment stage and final encode rather than a total
project deadline. Export cannot overwrite any existing output or input. Final
output is linked atomically from a same-folder temporary file only after success.

## Regression evidence

`tests/test_color_render.py` instruments the actual decode-to-evaluator path
with a 1024-level 16-bit ramp, asserts float32 input and over 1000 distinct values,
and checks linear exposure, all three wheel operations, master/channel curves
against decoded real exports and previews. Lossless intermediate checks use a
2/65535 tolerance; delivery checks allow codec/matrix/chroma quantization. Bypass
is pixel-exact against the identity path. Cancellation inside grading checks
partial-output cleanup. Existing render tests cover cuts/gaps/overlays, audio
offsets, rational rates, source-in, cover art, source changes, LUT files, output
protection and cancellation.

## Runtime conversion requirement and frame bounds

FFmpeg must include the `zscale` filter (libzimg) for YUV ingestion. This avoids
swscale's high-bit-depth limited-range endpoint approximation: nominal 10-bit
black 64 and white 940 become RGB 0 and 65535 exactly with zscale. Missing filter
support produces a render error, rather than silently falling back to a less
accurate transform. FFmpeg is still installed separately by the user.

The existing model conservatively floors reported source duration into complete
project frames. Containers with coarse millisecond duration metadata can report
a single 30-fps frame as 0.033 seconds, making that extremely short source fail
clip bounds validation. This change does not revise that existing policy.

## Local-only media processing

Every FFmpeg/ffprobe media input is restricted to `file,pipe` protocols. This
includes source inspection, color metadata inspection, grading decode/encode,
all final-compositor inputs, and preview frame extraction. A local playlist
cannot cause the engine to fetch HTTP/HTTPS media; nested network URLs are
rejected by FFmpeg before connection. Rendering, grading and caches run on the
machine running the app and use no cloud APIs or telemetry. External FFmpeg and
Python/Qt dependencies must already be installed; the app does not download them
at render time.

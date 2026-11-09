# Sticker and effect render contract

Lumen uses one RenderEngine compiler for playback previews, extracted preview
stills, and exported MP4/MOV files. Stickers are actual image composition and
clip effects are actual FFmpeg filters. There are no placeholder image boxes or
UI-only effect simulations.

## Coordinate and timing model

- Timeline positions, durations, keyframes, and animated frame durations are
  integer **project frames**, including at rational rates such as 30000/1001.
- An enabled sticker is visible on `[start, start + duration)`. Disabled stickers
  remain on the timeline and preserve its extent, but do not draw or require an
  asset to render. Stickers can extend the timeline past all media and captions.
- Keyframe frame numbers are relative to the sticker's start. `x` and `y` place
  its **top-left corner** in output-canvas pixels. `scale` uniformly multiplies
  the original PNG dimensions. `opacity` multiplies its existing alpha.
- X, Y, scale, and opacity are independently linearly interpolated. The first
  and last transforms are held outside their keyframe range. A single keyframe
  gives a static transform. No easing, rotation, perspective, or audio attachment
  is implied.
- Frame-pack selection is `relative_frame // frame_duration`. Looping packs wrap
  that index; non-looping packs hold their final PNG. Animation timing is not
  wall-clock based and does not drift with a rational project frame rate.
- Larger z-order draws above smaller z-order. Equal values preserve the order of
  the overlays in the project. Every sticker is above video tracks and below
  captions. Off-canvas pixels are clipped. Large scales use QPainter transforms,
  rather than allocating a huge resized source image.

The reusable `evaluate_overlay()` returns the exact frame pose or None. The
`compose_frame()` function uses this evaluator to return a project-sized RGBA
QImage. It requires the caller's mapping of overlay IDs to verified assets.

## Actual processing order and precision

1. Decode, conform, and grade each video clip through the existing owned
   floating-point grading path and its 16-bit lossless intermediate.
2. Apply that clip's enabled effects, in stored order, before video-track
   composition. Blur uses `gblur` with sigma in project pixels and three passes.
   Vignette uses the FFmpeg forward vignette with angle `amount * pi / 2`,
   centered in the image, with dithering disabled. Both are deterministic.
3. Composite video tracks using the existing frame-exact track gates.
4. Composite the sticker layer.
5. Burn captions on top, then encode SDR Rec.709 H.264 output.

A zero effect amount and a disabled effect omit the filter entirely. They are
exact graph bypasses, so FFmpeg's otherwise non-identity defaults cannot alter
the picture. Blur and vignette are video-only; the existing audio trim/mix,
gain, mute, fades, channel layout, and AAC encoding graph remains unchanged.

Stickers are explicitly **8-bit display-referred SDR**. PNG RGB is assumed sRGB;
the asset loader accepts only the documented SDR 8-bit RGB/RGBA subset. QPainter
uses 8-bit premultiplied-alpha source-over with smooth image transforms. It then
converts to straight RGBA. This is code-value compositing, not linear-light,
wide-gamut, or HDR compositing. Quantization from premultiplication/unpremultiplication
is intentional. Original pixel alpha and keyframed opacity are both applied.

The full-canvas transparent image stream is encoded as FFV1/BGRA, retaining
alpha losslessly. FFmpeg reads the CFR stream at the exact project rate, converts
it explicitly to limited-range Rec.709 YUVA420, and uses straight alpha to place
it over the video result. Chroma is subsampled at this final composition/output
boundary. Thin saturated edges may show 4:2:0 color bleed. Preview and export
use the same images, evaluator, transforms, effect order, and graph, but different
H.264 presets/CRF values; decoded pictures are therefore close, not guaranteed
bit-identical.

## Asset identity, cache, and cancellation

- Imported assets are explicit local PNG files or strict JSON frame packs. No
  asset path, label, or user text is interpolated into an FFmpeg expression.
  Paths travel as separate subprocess arguments; filters contain only validated
  numeric values and fixed filter names.
- Before every render and every preview request, including a cached preview,
  the selected manifest and every PNG are revalidated and hashed against the
  project's imported identity. Stat size/mtime is never considered sufficient.
- Decoding reads bounded, validated PNG bytes with an explicit PNG decoder,
  rather than allowing file content to select arbitrary image plugins. Each
  immutable byte buffer must also match its exact per-frame SHA-256 recorded
  during verification before its decoded pixels are used. This catches even a
  changed file that is restored before the final overall verification. Asset
  dimensions are checked again at the composition boundary.
- Asset identity is checked again after composing the intermediate and before
  atomic cache publication. A changed asset fails with an actionable error;
  existing imported identity is never silently updated. The final FFmpeg render
  then reads the owned immutable intermediate instead of the original PNGs.
- The compositor cache key includes resolution, exact rate, timeline length,
  all overlay settings/keyframes, and an implementation version. It is private
  to one RenderEngine and removed on close. The decoded-image LRU is capped at
  64 MiB; only one output frame is streamed at a time. Sticker composition is
  limited to 16,777,216 canvas pixels and rejects larger projects before
  allocating a canvas or starting an encoder. UHD 3840 x 2160 is supported;
  full 8K is outside this compositor limit. The error asks for a smaller project
  resolution. This does not silently resize the project.
- A watcher interrupts a blocked encoder on cancellation or timeout. Every
  started encoder is reaped; incomplete temporary files are deleted. Publication
  is atomic. No partial file is returned as a valid preview.
- Export refuses existing files and symlinks, media/LUT inputs, and sticker
  manifests/frame dependencies. The final export is atomically created without
  overwriting a file that appears during rendering.

## Verification

`tests/test_overlay_render.py` checks the evaluator, endpoint holds, PNG alpha,
z-order ties, negative/off-canvas geometry, scale, bounded image cache, effect
whitelist, zero/disabled bypass, real blur and vignette order, grade/effect/sticker/
caption order, animation loop and final-frame hold, preview/export decoded pixel
tolerance, rational-rate start/end frames, FFV1 alpha preservation, unchanged
decoded audio, tampering despite preserved file metadata, mid-render changes,
source/output protection, cancellation, timeout, and temporary cleanup.

# Selective hue curves

CutCat provides two per-clip periodic curves: **Hue vs Hue** and **Hue vs Sat**.
They select colors by the hue of the image after the existing master/RGB curves.
They are evaluated by the same owned float-color pipeline used for preview and
export. There is no separate lightness curve, tracking mask, spatial selection, or
skin detector in these curves. The separate schema-18 joint qualifier is
documented in QUALIFIER.md and follows the hue curves before the LUT.

## Persisted settings and migration

Project schema **17** appends two fields to `Grade`:

| Field | X axis | Y axis | Identity |
| --- | --- | --- | --- |
| `hue_hue_curve` | Hue in turns, 0 through 1 | Hue offset in turns, −0.5 through +0.5 | `[[0,0],[1,0]]` |
| `hue_sat_curve` | Hue in turns, 0 through 1 | Encoded-luma saturation factor, 0 through 2 | `[[0,1],[1,1]]` |

One turn is 360°. Thus an offset of +1/6 rotates red toward yellow, +1/3 rotates
red to green, and −1/6 rotates red toward magenta. Hue positions are red = 0/1,
yellow = 1/6, green = 1/3, cyan = 1/2, blue = 2/3, magenta = 5/6.

Each curve is a JSON list of 2–32 `[x,y]` lists. Coordinates must be finite real
numbers, not booleans; X strictly increases, with first X exactly 0 and last X
exactly 1. The first and last Y values must match exactly. The duplicated red
endpoint closes the curve and makes piecewise-linear interpolation continuous
across the red seam. Derivatives need not match at the seam or control points.
Arbitrary non-monotonic Y values inside the stated bounds are allowed.

Schema-17 clip grades must contain both fields. Versions 1–16 without them
migrate to independent neutral curves and retain their prior pixel results.
Putting either new field into a pre-17 project is an error, even if neutral:
version downgrades must not silently discard an active look. A malformed,
missing, out-of-range, non-finite, unclosed, or unordered schema-17 curve is an
error, not a request to reset it. Unknown fields are rejected. Earlier readers
reject schema 17 rather than falsely rendering an unsupported grade.

Both fields participate in the existing deep-copy project serialization and
editor transactions. Undo/redo, clip split, grade transfer, and render cache
identity retain the full control-point lists. Mutating a caller's list after an
editor transaction does not mutate the saved clip.

## Defined image arithmetic

The working image is **Rec.709-encoded float32 RGB**, with Rec.709/sRGB primaries
and D65. These are creative encoded-value operations, not linear-light or HDR
processing. They run after input normalization, restoration, the existing
creative controls, and master/RGB curves, but **before the optional 3D LUT and
its strength blend**. The LUT therefore sees the selectively graded result.
Grade disable and Before/bypass skip both curves along with the other creative
controls; source normalization remains active.

Both curves sample the **same original hue entering this stage**. Hue-vs-Sat
does not select the new hue produced by Hue-vs-Hue. A red-range saturation edit
therefore continues to address those red-range input pixels while a hue edit
rotates them to green.

For each RGB triplet, let `m = min(R,G,B)`, `M = max(R,G,B)`, and `C = M-m`.
If `C=0`, the pixel is achromatic and is returned bit-for-bit unchanged,
including negative gray, super-white gray, and signed zeros. No near-gray
threshold is introduced: a small but nonzero chroma remains selectable.

For nonzero chroma, hue is the standard six-sector RGB hexcone angle:

- If red is maximum, use `(G-B)/C`.
- Otherwise, if green is maximum, use `2+(B-R)/C`.
- Otherwise, use `4+(R-G)/C`.
- Divide by six and wrap modulo one. Ties use red, then green, then blue.

This definition does not divide by `M` and is therefore defined for negative
RGB, zero maximum, and values above one. For ordinary nonnegative RGB it is the
usual HSV hue. For signed RGB it is an explicitly defined extension, not a
claim that negative encoded RGB has a standardized perceptual hue.

1. Interpolate hue offset `d` and saturation factor `s` at that incoming hue.
2. If `d` is nonzero, wrap `h+d` modulo one and reconstruct the six-sector RGB
   hue with the original `m` and `M`. At the resulting unit-hue channel value
   `u`, the reconstructed channel is `m*(1-u)+M*u`. This preserves min/max and
   chroma, not encoded luma or perceived brightness.
3. If `s` differs from one, compute the **rotated** encoded luma
   `Y = .2126 R + .7152 G + .0722 B` and output
   `Y + s*(RGB-Y)`. Factor zero explicitly outputs gray `Y`; one is exact
   identity; two doubles deviations from that luma. This step preserves the
   rotated encoded luma to float32 rounding precision. It is not HSV or HSL
   saturation and does not promise perceptually constant brightness.

The offset ordinates are interpolated as ordinary signed turns, not by the
shortest route around the offset circle. In particular, a segment from −0.5
to +0.5 passes through zero even though the endpoint rotations are equivalent.

## Precision, headroom, and resource bounds

- There is no RGB clipping, 8-bit conversion, LUT quantization, gamut mapping,
  or hidden saturation cap in this stage. Hue wrap is a coordinate operation,
  not an image-range clamp. Negative and above-one results are preserved.
- All-neutral curves skip color arithmetic. Sampled-neutral pixels also skip
  hue reconstruction and saturation mixing, preserving their incoming bits.
  The evaluator never mutates its input and returns independent float32 output.
- Curve-control interpolation uses float64 coordinates and bounded fractions,
  then rounds the sampled control to float32. This avoids collapsing distinct
  valid JSON X coordinates or overflowing a slope for a very narrow segment.
  Working hue/RGB arrays and output remain float32. There is no integer image
  intermediate.
- A float32 modulo can round a tiny negative angle to exactly one; it is
  explicitly canonicalized to zero to avoid an invalid seventh hue sector.
- Finite input does not guarantee every requested operation is representable
  in float32. For example, subtracting a near-maximum negative channel from a
  near-maximum positive channel can overflow chroma; saturation expansion can
  exceed float32 output range. Such active operations fail with a non-finite
  grade error, without mutating the input. They never silently clip, return
  NaNs, or change precision contracts. Entirely neutral curves remain exact
  even at the full finite float32 limits.
- Temporary arrays operate in blocks of at most **65,536 pixels**, independent
  of frame dimensions, plus the returned full RGB frame. Each curve has at most
  32 points. No unbounded lookup table, per-frame history, or global image cache
  is introduced.

The existing media boundary still clips/quantizes to RGB16 after the complete
owned grade. Delivery then uses the chosen 8-bit H.264 or 10-bit ProRes path.
Selective curves do not turn that path into float-through-composition output,
and an enabled LUT still has its explicitly bounded sampling domain.

## Verification

`tests/test_selective_color.py` checks the following:

- Independent Python scalar interpolation and stdlib `colorsys` references,
  using normalized RGB for the signed/overrange extension.
- Analytic primary rotations; signed headroom; rotated-luma invariance;
  original-hue selection when both curves are active.
- Exact neutral, sampled-neutral, achromatic, disabled, and bypass results;
  mutable-default isolation and no input mutation.
- Red-wrap continuity, tiny negative modulo angles, narrow/subnormal control
  intervals, and an 8,192-step gradient with no 8-bit intermediate.
- Arbitrary shapes, strided and empty arrays, small forced block boundaries,
  output type, and explicit overflow errors.
- All point/count/range/shape/finite/type constraints; schema-17 persistence,
  missing-field rejection, legacy migration and field-smuggling rejection;
  atomic invalid edits, undo/redo, split, and independent snapshots.
- Explicit input-normalization → RGB curves → selective curves → LUT ordering,
  including zero-strength LUT bypass and preservation of extended headroom.

The independent integration tests additionally exercise real decoded preview,
H.264 and ProRes files and shared cache/grade-transfer behavior. Those tests are
separate from a claim of broad real-media or calibrated-monitor validation.

### Recorded CPU measurement, not a playback promise

A local run on 2026-10-22 used Linux x86_64, Python 3.12.14 and NumPy 2.2.4,
with a 1920×1080 float32 RGB image uniformly sampled over `[-0.1,1.1]`. After one
warm-up, five full `apply_grade` runs had these wall times:

| Grade | Median | Observed range |
| --- | --- | --- |
| Neutral default | 0.028 s/frame | 0.025–0.030 s/frame |
| Both shaped five-point selective curves | 1.003 s/frame | 0.918–1.076 s/frame |

These measurements include the existing neutral creative stack but exclude
decode, scaling, output encoding, and UI playback. They are not a cross-device
benchmark or real-time guarantee. Preview rendering remains asynchronous and
cached; an active full-resolution selective grade is materially more expensive
than the neutral path in this CPU implementation.

## Deliberate scope

The curve names follow familiar grading terminology, but this documented
algorithm is CutCat's own. No pixel identity with DaVinci Resolve, Premiere Pro,
CapCut, or any other application's proprietary algorithm is claimed. There is
no imported proprietary code, copied LUT, learned model, public media upload,
or external API call in these controls. This is not a lightness curve, film
emulation, an ACES/OCIO color-management system, or a production color-science
certification.

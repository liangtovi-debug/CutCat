> Current update: schema20 now supports bounded independent additive/subtractive color ranges. See [QUALIFIER_SETS.md](QUALIFIER_SETS.md). Earlier single-range/schema statements below describe the original milestone; unchanged coordinate, source, window and output restrictions still apply.

# Joint hue / saturation / luma qualifier (offline)

This is one per-clip secondary correction layer, not a node graph, an HSL
lightness editor, a skin detector, a spatial mask, or an object tracker.
All media remains local. The same float path supplies preview and export.

## Defined channels and stage

The qualifier sees encoded Rec.709 float32 RGB after input normalization,
active-picture spatial restoration, primary corrections, master/RGB curves,
and both selective hue curves. Its mask is measured once, before its own
adjustments and before the LUT. Changing its selected hue cannot feed back
into selection. It does not sample the final proxy or composited viewer.

For selection only, any RGB triplet outside [0,1] is excluded, not clipped.
Finite negative or superwhite image values remain untouched. Channels are:

- Hue: standard hexcone circular hue in turns; zero/one is red.
- Saturation: HSV saturation, (max-min)/max, zero at black. This is not the
  adjustment's luma-deviation saturation factor.
- Luma Y′: .2126 R′ + .7152 G′ + .0722 B′. This is encoded luma, not linear
  luminance, HSL lightness (max+min)/2, or HSV value (max).

Hue distance is the shortest circular distance to center. Hue width is a
half-width in turns. Saturation and Y′ have inclusive low/high bounds.
Each weight is one in its interval and falls linearly to zero over the
specified outward feather; zero feather is a hard edge. The final selection
is the product of hue, saturation, and Y′ weights. Hue half-width .5 bypasses
hue selection, including achromatic pixels. Otherwise chroma <= 1e-6 is
excluded because it has no stable hue. These deliberate rules need not
numerically match Resolve's proprietary qualification implementation.

## Selected correction

The selected candidate rotates hue while preserving min/max, scales its
encoded-luma deviations by the selected saturation factor, then multiplies
encoded RGB by 2^exposure. The last control is code-value gain in log2 units,
NOT linear-light exposure. Original and candidate are mixed using the single
pre-adjustment mask. Output is not silently clipped by this stage. Default,
disabled, and neutral-adjustment paths remain bit exact, even with a narrow
nonneutral mask. Grade bypass skips the qualifier with other creative work.

## Matte and UI

The dialog decodes a real selected-clip RGB16 frame and performs matching
float normalization/restoration/grade stages. The image is a frozen static
snapshot; 8-bit clipping is for screen presentation only. To keep controls
responsive, the initial overview evaluates an exact sparse source-pixel grid
with a maximum 512-pixel long dimension. This overview can miss sub-grid detail.
The current Detail modes instead evaluate every pixel in a contiguous local
512/256/128/64-pixel crop of the same frozen fitted project canvas. The displayed
crop origin, source/sample dimensions and sampling mode are explicit. See
[detail navigation and sampling](QUALIFIER_PICKER.md). This is not camera-native
resolution before project scaling. Export evaluates every project-canvas pixel. Range matte is
shown even when the correction layer is disabled.
The fitted canvas includes padding, which is graded and can qualify just as
in the renderer; it is not a spatial exclusion mask. Settings are local until
Apply creates one undoable edit. Cancel, stale clip,
source, playhead or project revisions cannot commit an obsolete snapshot.

The snapshot path currently rejects alpha-bearing sources, active stabilization,
and changing restoration geometry rather than display a misleading mask.
It is not the final composite: transforms, later effects, overlays, and other
tracks are not included. Transparent overlays still composite after grading.

## Persistence and compatibility

Schema 18 requires the complete qualifier settings. Schemas 1–17 without
qualifier data migrate to a disabled independent default; inserting the new
field into an older schema is rejected. Unknown and invalid parameters are
errors, not silent resets. Settings participate in undo/redo, recovery, grade
copy, clip splitting and render cache fingerprints. Older applications reject
new project files rather than silently render them without this correction.

## Competitor comparison and remaining gaps

Blackmagic's official Resolve Color page describes hue/saturation/luminance
qualification, selection preview, node-based correction and spatial tools:
https://www.blackmagicdesign.com/products/davinciresolve/color
The official reference manual documents range and softness controls:
https://documents.blackmagicdesign.com/UserManuals/DaVinci_Resolve_11_Reference_Manual.pdf

This bounded implementation closes joint color-range selection and visible
matte inspection, not feature parity. Missing: multiple independent qualifier
nodes, add/subtract sample strokes, spatial windows, tracking, matte denoise /
clean black-white / blur, per-channel feather asymmetry, HDR/OCIO management,
and validated native macOS/Windows builds. Synthetic skin-like patches are
only color tests, not evidence of skin-detection accuracy.

Next bounded candidate: sampled joint-range initialization from this exact
pre-qualifier snapshot, with explicit add/replace semantics and analytic
range coverage tests. It should not precede freezing and verifying this round.

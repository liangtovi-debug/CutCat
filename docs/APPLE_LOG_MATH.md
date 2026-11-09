# Original Apple Log input mathematics (experimental)

## Scope and reference

This opt-in input is **original Apple Log / BT.2020**, not Apple Log 2, another
camera log curve, PQ, or HLG. Selection is explicit; metadata alone cannot
reliably identify original Apple Log. Input RGB must already have correct YUV
matrix and video/full-range decoding applied. Do not apply a second Log LUT.

Transfer equations are adapted from the current official ACES open-source
reference, pinned on 2026-10-23 at commit
`29b722bccd529460696a8382394504cae2e88419`:

- [Decode](https://github.com/aces-aswf/aces-input-and-colorspaces/blob/29b722bccd529460696a8382394504cae2e88419/apple/CSC.Apple.AppleLog_to_ACES.ctl)
- [Encode](https://github.com/aces-aswf/aces-input-and-colorspaces/blob/29b722bccd529460696a8382394504cae2e88419/apple/CSC.Apple.ACES_to_AppleLog.ctl)
- [LICENSE](https://github.com/aces-aswf/aces-input-and-colorspaces/blob/29b722bccd529460696a8382394504cae2e88419/LICENSE)

Both CTL files say `SPDX-License-Identifier: Apache-2.0` and `Copyright
Contributors to the ACES Project.` The pinned complete recursive tree has no
NOTICE file (root NOTICE also returned 404). The unmodified LICENSE and local
modification/attribution notice are in `lumen/assets/licenses/aces-input-and-colorspaces/`;
include both in distributed source and binaries. No proprietary Apple LUT was
downloaded, embedded, or redistributed. This is not a certified Apple or ACES
rendering, and does not claim to match Apple's LUT or DaVinci Resolve.

## Transfer and matrix

Constants: R0 = -0.05641088, Rt = 0.01, c = 47.28711236, b = 0.00964052,
g = 0.08550479, d = 0.69336945, Pt = c*(Rt-R0)^2 = 0.20855531595464202.
Decode P >= Pt with 2^((P-d)/g)-b; decode 0 <= P < Pt with sqrt(P/c)+R0;
negative P maps to R0. The forward helper uses g*log2(R+b)+d for R >= Rt,
c*(R-R0)^2 for R0 <= R < Rt, and zero otherwise. Official rounded constants
produce a roughly 2.75e-9 encoded junction mismatch, not a meaningful tone step.

Direct linear BT.2020 to BT.709 uses normalized primary matrices, without an
ACES detour or chromatic adaptation because both white points are D65:

- BT.2020 xy: R (.708,.292), G (.170,.797), B (.131,.046).
- BT.709 xy: R (.640,.330), G (.300,.600), B (.150,.060).
- D65 xy: (.3127,.3290).

For each primary form XYZ = (x/y, 1, (1-x-y)/y), solve the three scale factors
against D65 XYZ, then calculate inverse(M709) * M2020. For column RGB vectors:

```
[ 1.66049100210843 -0.58764113878855 -0.07284986331988 ]
[-0.12455047452159  1.13289989712596 -0.00834942260437 ]
[-0.01815076335491 -0.10057889800801  1.11872966136291 ]
```

Scene-linear values above one and negative matrix components are retained
through this stage. Premature per-channel clipping is not used.

## Fixed CutCat SDR rendering policy

This display rendering is a deliberate CutCat look, not part of the reference
Apple Log inverse. Exposure is fixed at 1. Compute linear BT.709 luminance from
the normalized-primary Y row (.21263900587151, .71516867876776, .07219231536073).
Nonpositive luminance maps to black. Preserve positive luminance L <= .18.
Above .18 use Y = .18 + .82*(1-exp(-(L-.18)/.82)). The shoulder is continuous
with slope one at .18, monotonically increasing and asymptotic to one. Scale
all RGB components by Y/L, preserving ratios before gamut compression.

For out-of-gamut RGB, take the largest t in [0,1] such that
Y + t*(RGB-Y) lies inside the unit RGB cube. This neutral-axis desaturation
preserves mapped linear luminance and a straight RGB chroma direction, unlike
channel clipping. It does **not** guarantee perceptual hue preservation, may
wash out saturated highlights, and is not a perceptual/ACES gamut mapper.
Only a final floating-point fence clips tiny rounding errors at cube faces.

Encode output with the existing pipeline's standard rounded BT.709 OETF:
4.5*L below .018, otherwise 1.099*L^.45-.099. Those rounded standard coefficients
have an approximately .000248 code jump at the toe boundary; this is not a
perfectly continuous custom OETF. Output is float32 encoded Rec.709 [0,1], not
scene-linear data, sRGB, PQ, HLG, or an HDR deliverable.

## Numerical contract and validation limits

The public function accepts finite real arrays of shape (...,3), preserves
shape, does not mutate input, and rejects nonfinite data/wrong channel counts.
Transfer helpers return float64. To avoid overflow for absurd finite input,
decode caps the exponent at 512 (log code 44.47182193). This is far outside the
normalized camera interval [0,1], which is not capped. The cap is a robustness
policy, not a meaningful additional camera exposure range. Float32 output and
the shoulder inevitably lose extreme-highlight separation near white.

Tests use independent scalar curve evaluation, fixed numerical anchors,
normalized-primary matrix coefficients, junction/roundtrip tolerances, linear
values above one, shoulder slope/continuity, saturated RGB cube extremes,
neutral-axis and luminance invariants, float64 extreme finite values, input
immutability, shape checks, and NaN/infinity rejection. These are mathematical
and synthetic-image checks, not verified real iPhone footage or visual matching
to Apple's LUT/Resolve. Camera footage and native platform inspection remain
separate acceptance work.

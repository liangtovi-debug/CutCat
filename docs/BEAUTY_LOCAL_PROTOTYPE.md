# Face-aware beauty: local experimental groundwork

Status (2026-10-16): local research/prototype only. The shipping CutCat UI and render graph have no automatic beauty feature. The last native-tested application remains restoration commit `cb47ef721b8d6cc4358aa016c59a67096b338b94`. This document is not a Wink-equivalence claim.

## Bounded implementation

The prototype separates three contracts:

1. A caller supplies conservative face/skin-region polygons and exclusion polygons. Geometry can protect named eye/lip/brow regions, but it cannot establish that the polygon contains skin or detect a hand, hair, glasses, beard or another occluder.
2. A canonical sequence gate consumes explicitly supplied validity, confidence, track identity, timestamps and cut/reset signals. Unsafe frames return to the original immediately. Reacquisition may ramp in, but stale masks must never be carried onto a missing face. Random preview seeks must consult the same precomputed per-frame results used by export.
3. An owned float evaluator modifies only allowed samples and retains protected pixels exactly. Numerical detail/lighting tests document smoothing tradeoffs; they do not establish natural-face perceptual quality.

These pieces are experimental and deliberately not wired into project serialization, user-facing menus or production render/cache evaluation until an actual detector and occlusion policy pass the gates below. No hidden model download or network runtime is added.

## Dependency/privacy gate

Official MediaPipe package metadata advertises on-device input processing but also performance/utilization metric transmission. The official project discussion says there is no official API to disable this collection; building the SDK from source or blocking host access are suggested alternatives. We have not established a reproducible offline source build or verified effective runtime egress isolation. Consequently no MediaPipe wheel is executed as part of this prototype.

- Current package metadata: https://pypi.org/pypi/mediapipe/1.1.1/json
- Official maintainer discussion: https://github.com/google-ai-edge/mediapipe/issues/6291
- Older candidate metadata: https://pypi.org/pypi/mediapipe/0.10.21/json
- Python setup: https://developers.google.com/edge/mediapipe/solutions/setup_python

The older 0.10.21 package requires NumPy <2, conflicting with CutCat's NumPy 2.2.4. An isolated old test environment would not establish compatibility with the application, security maintenance, binary provenance or absence of telemetry. We do not downgrade the application to conceal these gaps. Model-card licensing is separate from binary runtime behavior and the exact downloaded model bundle's provenance.

## Release gates still open

- Audited, hash-pinned local model and runtime, including licenses for every component and effective no-egress behavior.
- Actual inference on properly licensed test material; no user media or identifiers sent externally.
- Conservative uncertainty handling for profile turns, hands, hair, glasses, beards, entry/exit, multiple faces and scene cuts. Landmark geometry alone is insufficient.
- Motion-compensated mask/appearance assessment and human review of temporal changes.
- Shared production preview/export integration with canonical source-time analysis, content/version/config cache identity, cancellation and resource limits.
- Save/undo/migration, EN/ZH UI, before/after, complete regression and native frozen-program validation.

GitHub Actions has exhausted the account's included minutes and the verified zero-dollar budget blocks further usage. No remote push or native build is attempted for this local prototype. Previous successful native packages remain the tested release baseline. Source-only prototype results must never be described as a newly validated Windows/macOS application package.

## Test media policy

Prefer original synthetic fixtures for deterministic region, detail and safety tests. NASA's astronaut portrait is documented as public-domain by scikit-image, but identifiable-person endorsement/publicity restrictions still require care; no NASA portrait is included in an installer, public demonstration or marketing output here. No natural-face inference has been performed at this stage.

- https://scikit-image.org/docs/0.25.x/api/skimage.data.html#skimage.data.astronaut
- https://www.nasa.gov/nasa-brand-center/images-and-media/

## Initial measured numerical tradeoff

A 192×144 original synthetic fixture contains low-contrast cheek texture, localized spots, a lighting gradient and explicit protected regions. At smoothing strength 0.8 / detail protection 0.75, the tested spot-center error decreased 24.90%, selected cheek-texture attenuation measured 0%, and allowed-region RMS correction was 0.00008877 in encoded RGB. Protected/background samples and the lighting-only fixture were bit-identical. These are fixture-specific figures, not evidence that pores or real blemishes are understood.

At detail protection 0, spot-center reduction increased to 34.63%, while the same weak texture attenuated 53.37%. The lighting-only maximum boundary shift was 0.0003836. This counterexample is retained: greater smoothing can remove genuine texture. Strong/broad blemishes and missing detail are not reconstructed.

A single 1080p evaluator run took approximately 2.35 seconds in the cloud Python process with memory tracing enabled. Traced total peak was 29.59 MiB including a 23.73 MiB output. This is a bounded allocation sanity check, not a consumer-device benchmark, p95 latency measurement or realtime playback promise.

Sequence handling intentionally favors avoiding an incorrect effect over a visually smooth exit: rejected geometry/detection returns immediately to original pixels and reacquisition ramps in. It may still produce visible strength changes. It neither tracks mask motion nor proves flicker-free video. Supplied confidence is not calibrated detector confidence.

## Final local validation

228 new prototype tests passed. The unchanged application baseline passed 2,106 tests with one Windows-only skip (2,107 collected). Combined coverage is 2,335 cases with one platform skip. All 214 prior source-file hashes remain identical. Independent review rechecked protected pixels, read-only/strided inputs, subnormal arithmetic, cancellation, geometry bounds, huge numeric rejection, cap-overflow prevention and canonical seek parity. No existing production module imports the experiment. No native build, repository update, model download or model inference was performed.

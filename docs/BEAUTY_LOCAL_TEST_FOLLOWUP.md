# Beauty prototype: targeted local failure-case tests

This continues the isolated supplied-mask experiment. It is not automatic face detection, skin segmentation, a new app UI or native Windows/macOS acceptance. No model is installed or executed; no repository push or CI is used.

## Detail-scale counterexamples

The earlier synthetic fixture's zero measured texture loss is not a universal property. A new sweep covers three spatial frequencies, three amplitudes and three detail-protection settings in normalized encoded RGB, with explicit excluded regions.

At spatial frequency 1.4 radians/pixel and amplitude 0.025, strength 0.8:
- Detail protection 0.75 reduces texture RMS from 0.0125583 to 0.00769021, approximately 38.76% attenuation.
- Even detail protection 1.0 reduces it to 0.00907037, approximately 27.77% attenuation.

The same numeric image receives the same processing whether its pattern is called unwanted noise or desired texture. No semantic understanding of pores or blemishes exists. Excluded/protected samples remain exactly unchanged.

The fixed 5×5 neighborhood is resolution-dependent. With detail protection 0.75, a Gaussian spot's center error improves about 28.58% at radius 1 pixel, 8.65% at radius 2, and 0% at radii 4 or 8. These are measured counterexamples to broad blemish-removal claims, not a recommendation to remove all broad features.

Reproduce the numeric sweep with `python scripts/probe_beauty_detail_limits.py --output detail-probe.json`. Test cases are in `tests/test_beauty_detail_limits.py`. No natural face is used.

## Temporal boundary investigation

An exact integer translation of synthetic image and explicitly supplied geometry exposed a zero-feather boundary roundoff defect. A protected rectangle corner reconstructed as 29.0000000000000036 rather than 29 caused a corner-adjacent pixel's footprint clearance to become +2.44e-15 instead of zero. The binary mask threshold switched that pixel on for one frame, causing a measured motion-compensated output jump of approximately 0.02107346 despite zero source change.

The fix uses a conservative coordinate-scaled float64 roundoff margin before both clearance branches. This rejects numerically ambiguous boundary samples rather than granting effect coverage. Regression must preserve the original exact-translation assertion; no quality threshold is relaxed or skipped. Positive feathering remains advisable, but it does not establish flicker-free tracking or occlusion safety.

True geometry changes and uncertain-frame rejection can still create visible strength changes. Immediate fallback avoids applying a stale mask; it is not temporal denoising or motion compensation.

## Offline runtime feasibility

OpenCV Zoo YuNet supplies a credible MIT-licensed local face-detection candidate, but its five points do not outline eyes, lips, hair or occlusions. It cannot justify a safe automatic skin-only mask. Exact runtime and model provenance still require verification before execution/packaging.

ONNX Runtime privacy behavior is version-specific. Current 1DS documentation must not be projected onto the application's pinned optional 1.22.1 runtime. The latter uses no-op POSIX telemetry and Windows ETW/TraceLogging with API disable support in the audited sources; the existing search worker calls that disable API. This does not certify Windows import-time behavior or OS-managed event handling. See the accompanying official-source audit. No model/runtime execution is performed in this follow-up.

## Verified outcome

After the fix, exact integer translation at feather 0, 2 and 5 produces zero motion-compensated mask/output change on the supplied synthetic sequence. True ±1.25-pixel boundary jitter still produces maximum encoded-RGB changes of 0.02500001 at feather 0/2 and 0.01519188 at feather 5. Immediate uncertainty fallback can produce comparable strength jumps. This evidence supports deterministic safe rejection, not flicker-free beauty.

269 targeted tests passed: 265 beauty tests (37 new cases beyond the prior 228) plus four collection-guard cases. Complete test collection succeeded with 2,372 cases. The unchanged application's full suite was not rerun without new production-code evidence. Its earlier 2,106-pass/one-skip result remains historical evidence, not a new full-suite run. All 214 original application/source-document hashes were rechecked unchanged. Only the isolated prototype mask module, new tests, diagnostic script and planning documents changed.

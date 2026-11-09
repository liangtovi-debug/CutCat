# Simple / Professional workspace and SDR input milestone

**840 tests passed in 121.62 seconds** on the final Linux source checkout. The extracted source passed all **840 tests again in 122.11 seconds**. The relocated render/save/reopen/Qt-decoding self-test passed, and the relocated sRGB project produced the same decoded RGB hash as the included example. Subsequent changes were documentation/report files only. Evidence is in `artifacts/workspace-color-validation/` and `artifacts/workspace-color-milestone/`.

Actual Qt buttons switched one shared project 22 times without changing its data or revision. Independent exports made after Simple and Professional selection have exactly matching decoded RGB frame hashes. Focused tests preserve undo/redo, staged precision/keyframes, active render work and existing preview state. Simple explicitly reports hidden active settings; hidden-effect Delete actions and audio-only color controls are guarded.

Real 16-bit RGB fixtures verify explicit sRGB and linear-Rec.709 normalization exactly once, including creative bypass/Before. Source matrix/range conversion is separately exercised with 10-bit YUV, full/limited range and BT.709/BT.601 matrix tags with explicit Rec.709 primaries. Known transfer mismatch, unsupported HDR/gamut and limited-range RGB are rejected. Limited RGB was rejected after an independent real-frame check showed the available zscale path did not expand it correctly. Input and output tags are asserted, not merely assumed from encoder arguments.

The restored 755-test baseline initially had one sticker regeneration failure: 71 semitransparent pixels differed by one straight-RGB channel code, while all premultiplied pixels, alpha and opaque pixels were exact. The test now compares exact premultiplied artwork and separately pins all shipped asset bytes and project asset digests. No artwork or integrity checks were weakened. See `STICKER_REPRODUCIBILITY.md`.

This is a bounded transfer/matrix/range foundation for SDR Rec.709 primaries. Camera Log/gamut transforms, local correction masks, ICC/OCIO-managed monitoring, HDR/RAW and temporal denoise remain unimplemented. Stickers retain the existing 8-bit code-value compositor. New renders re-probe source color; merely activating an already-built GUI preview cache is not a fresh source validation. Native Windows/macOS, physical audio and offline cold-start acceptance remain pending. Previous AI inference evidence is retained; optional AI models/runtimes were not restored or re-run in this workspace-mode milestone.

## Previous local AI milestone

# Local subtitle AI, semantic search and voice catalog milestone

**755 tests passed in 89.42 seconds** on the final Linux source checkout. The extracted source passed all **755 tests again in 91.97 seconds**. Fresh Qwen and MiniLM inference from the extracted directory passed, with imported source path recorded; the relocated render/save/reopen/Qt-decoding self-test also passed. Evidence is in `artifacts/local-ai-validation/`. Subsequent edits changed only reports/documentation. The restored effects baseline first passed all 561 tests in 100.42 seconds. New tests cover bounded model registration, one-heavy-task concurrency, caption/model revision guards, reviewed text-only undo, exact tokenizer/pooling, subprocess cancellation, installed-version voice validation and real audition cleanup.

Actual local inference evidence:

- Qwen3.5-0.8B Q4_0 + pinned llama.cpp CPU build corrected synthetic English and Chinese typos. Date reformatting and collapsed bilingual line breaks were rejected by conservative guards. Suggestions still require human semantic review; translation, title generation and batch rewrites are not implemented. See `artifacts/subtitle-ai-validation/`.
- Multilingual MiniLM INT8 ONNX placed the intended caption first for 10/10 fixed bilingual/cross-language probes among 16 synthetic captions. This is a small fixture result, not general retrieval accuracy or a no-answer guarantee. Actual mean pooling, unit vector norms, truncation rejection and cancellation were checked. See `artifacts/search-validation/`.
- All 103 v1.1 and 53 retained v1.0 speaker entries generated finite, non-silent bilingual samples. Different PCM hashes do not establish perceptually distinct personas. v1.1 has 55 Chinese female, 45 Chinese male, 2 US female and 1 British female catalog entries. One legacy `hm_omega` output had six over-range float samples (peak 1.079); conversion would clip those samples. No v1.1 sweep output exceeded full scale. Six final-adapter WAVs are included in `artifacts/voice-browser-validation/`; no human listening or physical audio verification is claimed.
- Actual asynchronous Qt verification, Qwen proposal, MiniLM result and frame-150 jump, and installed v1.1 Chinese voice audition were exercised. Captures in `artifacts/local-ai-demo/` show the running application, not mockups. The reviewed example export uses the same render graph and retains subtitle IDs/times.

The application reads explicitly selected local model files. It has no downloader, cloud inference fallback or media upload path; search disables ONNX Runtime telemetry. This is not OS-enforced network isolation or a packet-trace test. The per-process inference gate allows one heavy AI job, with CPU thread limits and bounded subprocess outputs/timeouts. Separate editor processes are not globally coordinated. Native Windows/macOS builds, ARM search compatibility, physical audio and disconnected cold-start acceptance remain pending. Model/runtime binaries are excluded; full Kokoro component redistribution remains uncleared.

## Earlier consolidated milestone

# Consolidated local effects/stickers milestone

**561 tests passed in 82.80 seconds** on the Linux source checkout; the extracted source passed all561 again in82.95seconds. The relocated combined project and real Qt decoding self-test also passed. Final documentation edits did not change tested code/resources. This retains all303 TTS-and-earlier tests and adds90 overlay/effect model tests,119 safe asset tests,25 shared compositor/render tests and24 Qt control tests.

Actual demo: `artifacts/effects-demo/project.lumen` and `effects-and-stickers.mp4`, with original static/animated PNG artwork, linear position/scale/opacity keyframes, ordered blur/vignette, bilingual local TTS and captions. Output has402 frames at30fps, exactly matching the project. Decoded audio is byte-identical to the previous TTS demonstration; final imported asset hashes were reverified. Evidence: `artifacts/effects-demo/validation.json`.

Three actual Qt captures show stickers, advanced keys and the effect stack in that rendered project. They are not UI mockups. The initial full run identified GUI-dependent PNG DPI metadata in the generator; explicit metadata now stays stable, and cross-platform reproduction compares actual RGBA artwork while separate tamper tests retain strict byte hashes.

The runtime self-test passed from Linux source: real grade/sticker rendering, save/reopen, preview extraction and actual Qt Multimedia decoding. A Python wheel was built and inspected: all33 packaged source/resource files match,10 original PNGs are included, and no model or native runtime is bundled. These are not frozen Windows/macOS results. See `artifacts/native-source-smoke/` and `docs/NATIVE_VALIDATION_GATE.md`.

The compositor is bounded to16,777,216 output pixels; UHD4K fits, full8K does not. It is8-bit SDR code-value composition, not linear-light/HDR/GPU real-time processing. Native builds, physical audio, disconnected cold-start inference and component-license release clearance remain unverified.

## Previous milestones

# Local TTS milestone validation

**303 tests passed in 67.71 seconds** in the TTS source checkout; extracted TTS ZIP passed all303 again in71.43seconds. Actual synthesis from the relocated source also succeeded (2.649-second test WAV). Previous 204-test subtitle/ASR baseline remains part of the suite.

Actual inference: sherpa-onnx1.12.26/Kokoro multilingual v1.0, CPU, explicit existing test pack. English5.729s, Chinese6.683s;1.5speed English3.483s. Real cancellation reaped worker; corrupt model failed safely. Exact sources, hashes, component licensing and local ASR cross-checks: `artifacts/tts-validation/`.

Actual lossy AAC export preserves both full voice intervals: sample correlation with original WAV is 0.999966 (English) and 0.999988 (Chinese), with 137,500 and 160,388 source samples respectively. Evidence: `artifacts/tts-demo/export-audio-check.json`.

Actual complete graph: `artifacts/tts-demo/local-bilingual-narration.mp4`, project, SRT and separate immutable generated voice assets. Round-up retains final partial-frame audio samples; source digest verified even on preview-cache hits. Native Windows/macOS, human listening and OS-enforced disconnected cold-start remain unverified.

## Earlier subtitle/ASR validation

# Validation record — 2026-10-14

This record describes the local checkout, not a Windows/macOS release certification.

## Current subtitle / local ASR milestone

**204 tests passed** in 64.60 seconds. Counts: ASR 34, real caption render 10, color numerical 24, color render/precision 15, model 23, legacy render 18, subtitle model/SRT 48, Qt UI 31, persisted workflow 1. All previous color/audio/timeline tests remain.

The real whisper.cpp v1.8.2 multilingual-small CPU model ran on bundled synthetic English/Chinese fixtures. Exact source/license/hash and reference/hypothesis records are under `artifacts/asr-validation/`; short English matched, Chinese contains documented errors. Actual cancellation and invalid-model handling were exercised. Audio-origin normalization is tested with real positive/negative timestamp-shifted media.

An additional actual video → ASR → editable caption/SRT → shared-graph export run is in `artifacts/asr-caption-demo/`. AAC-video recognition misrecognized the first two Chinese sentences; raw output is intentionally preserved. This is workflow evidence, not a quality benchmark.

Caption tests inspect decoded pixels at exact start/end frames for 30, 30000/1001 and 60000/1001 fps, real Chinese TTC glyphs, plain multiline escaping, color bypass, black tails, project reopening and font-cache invalidation. Local fonts must exist and cover the text; no silent tofu substitution. Captions do not auto-wrap and are independent of video edits.

The environment denies network namespaces and ptrace. Hard-disconnected cold-start and observed-no-network tracing were **not verified**, and restrictions were not bypassed. Successful inference used local runtime/model/fixtures; the adapter has no downloader/network API and restricts media inputs to file/pipe. Native desktop offline acceptance remains a release gate.

The older sections below record the color milestone's prior evidence; they are retained for continuity.

## Environment

- Debian 13 Linux cloud execution environment, Python 3.12
- PySide6 / Qt 6.8.3, NumPy 2.2.4
- FFmpeg 7.1.5, software H.264/AAC encoding
- Qt offscreen platform with a CPU-painted video sink

## Previous color milestone

`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`

**98 tests passed** in the combined run (32.28 seconds).

- 23 model tests: all prior timeline/persistence protections plus version 2 curves/vectors, version 1 migration, finite/bounded parameters, and deep undo/split snapshots.
- 18 existing real render regressions: cuts/gaps/layers, mixed/rational frame rates, positive/negative audio offsets, gain/mute/mixing/fades, source changes, cover artwork, LUT errors and safe cancellation/output.
- 24 numerical color checks: transfer reference values, rounded-toe behavior, neutral and bypass identity, signed/out-of-range values, real linear-light ratios, LGG, curve extrapolation/order, LUT parsing/interpolation, legacy creative controls and highlight recovery after values exceed 1.
- 15 additional actual render/precision checks: RGB16 ramp retains 1024 levels before float evaluation; limited/full-range YUV10 preserve levels and nominal endpoints; actual preview/export pixels match new operations; 16-bit intermediate tolerances; bypass; cancellation; rotated and non-square-pixel sources; local-only protocols and rejection of a network-referencing local playlist.
- 17 Qt integration tests: previous editor operations plus wheel/curve staging and single-step undo, numeric controls, before/after snapshots/history/cache, stale async results and point-delete shortcut protection.
- 1 persisted-project workflow: mixed-rate source → split → non-neutral linear exposure/gamma/curve plus creative/audio changes → save → reopen → export. Reopened exports have identical tested decoded frames and exactly 60 output frames at 30000/1001 fps. Lossy preview and delivery are compared with tolerance.

## Color precision finding and correction

An independent limited-range 10-bit YUV ramp exposed swscale mapping nominal white to 0.9961547. The source bridge now uses format/range-aware zscale for YUV→RGB16, with no silent fallback. Tests verify limited-range 64→0 and 940→1, full-range 0→0 and 1023→1, and retained distinct levels. RGB sources retain their direct 16-bit path. The FFmpeg build must include zscale/libzimg.

The owned evaluator remains float32 until its explicit 16-bit SDR transport boundary; compositing/delivery remain 8-bit H.264. No end-to-end float codec or HDR claim is made.

## Long synthetic synchronization reference

Run `python scripts/verify_long_reference.py` to reproduce.

A 1,800-second synthetic source was rendered at 30000/1001 fps. The requested timeline was 53,946 frames. Result:

- Output: exactly 53,946 frames
- Video duration: 1799.998200 seconds
- Source final-tone onset relative to 1798 seconds: 1.5000416667 seconds
- Export final-tone onset at the same reference: 1.5000416667 seconds
- Measured onset difference: 0.0 seconds at 48 kHz sample resolution

This verifies one generated reference case. It does not establish that every codec, VFR source, device, long real project or playback system stays synchronized. JSON evidence: `artifacts/long-reference-result.json`.

## Demonstration artifacts

- `artifacts/demo-verified/first-cut.lumen`: portable project with generated media alongside it
- `artifacts/demo-verified/first-cut-v2.mp4`: actual seven-second, five-clip edited export regenerated through the new color engine
- `artifacts/color-milestone/float-color.lumen`, `graded.mp4`, `before.mp4`: actual 16-bit-source color project and two rendered states
- `artifacts/color-editor-wheels.png`, `color-editor-curves.png`, `color-editor-before.png`: actual Qt UI captures decoding these renders
- `artifacts/editor-screenshot.png`: actual offscreen Qt editor displaying decoded rendered video, timeline and scopes; not a visual mockup
- `artifacts/rendered-preview-frame.png`: actual exported preview frame

Reproduce a fresh demo with `python scripts/create_demo.py my-new-demo --render`. Existing directories are refused to protect files.

## Not established

- Windows/macOS native package builds, clean-machine installation, keyboard/accessibility behavior, Apple Silicon/Intel coverage and OS-specific playback
- Physical audio output (no device available in the cloud environment)
- Interactive playback/seek performance, high-resolution or large-project throughput
- Calibrated display management, OCIO/ACES, HDR or 10-bit delivery mastering; the implemented float32 Rec.709 evaluator does not establish these
- Production stability, completeness, competitor usability superiority or licensing clearance

The UI scopes use the decoded 8-bit SDR preview. Legacy RGB gain is encoded-value multiplication; the new Linear exposure applies a linear-light ratio after the explicitly assumed Rec.709 inverse OETF. It does not recover unknown original scene radiance. See `render-core.md` and `ROADMAP.md`.

## Remaining media boundary caveat

Ordinary imported source duration is conservatively floored to complete project frames. Coarsely rounded container duration metadata can discard a partial final frame or reject a one-frame asset. Accurate packet-level duration accounting and still-image duration controls are future work; do not infer their support from successful longer-video tests.

Generated speech is the explicit exception: rounded up to preserve final PCM samples, with less than one project frame of silence padding.

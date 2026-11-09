# Next gate: Windows and macOS validation

Current local candidate (2026-10-26): **schema39 project timeline markers**. This candidate has no native Windows/macOS package acceptance. Exact current source, remote/CI status, packaged executable evidence, signing and physical-device checks must remain separately identified; earlier source/native receipts do not certify schema39. See [current capability inventory](CURRENT_EDITING_CAPABILITIES.zh-CN.md).

## Historical gate context (2026-10-24)

At that review, cumulative source was schema32 and its native verification was pending. The repository/run details in this historical section describe that dated review, not the current predecessor-publication task. Preserve accepted local source ZIPs and evidence independently of native artifacts.

The private repository is https://github.com/liangtovi-debug/LumenEdit. The most recently verified historical native run is [38837683338](https://github.com/liangtovi-debug/LumenEdit/actions/runs/38837683338), successful at cb47ef721b8d6cc4358aa016c59a67096b338b94 (schema9). Its artifact listing was empty at this review, and no repository Releases existed. Historical success does not establish present package availability or current-source native correctness.

Private source integration and native tests are authorized; paid CI, public publication and signing identity use are not. Confirm available free quota before triggering hosted jobs. The integration commit uses `[skip ci]`; triggers remain unchanged for future authorized runs. The exact remote commit and CI outcome must be recorded separately after publication/execution, never inferred from Linux source tests.

## Prepared workflow

`.github/workflows/desktop-ci.yml` targets
Windows 2022 x64 and macOS 15 ARM with Python3.12, pinned Python dependencies, source tests,
unsigned native PyInstaller draft builds, and an explicit packaged-executable
`--lumen-self-test NEW_DIRECTORY` run. The self-test:

1. Resolves the bundled original sticker assets from the installed package.
2. Creates synthetic picture/audio locally with external FFmpeg.
3. Grades and composites an alpha sticker through the shared render graph.
4. Saves/reopens the project, exports, builds preview and extracts a real frame.
5. Decodes the preview with Qt Multimedia and writes a JSON result.

It does not claim physical sound output, ASR/TTS model inference, accessibility,
real-time performance or clean-machine installer verification. Its source path
has historical Linux and native evidence for earlier source; the expanded current-source frozen Windows/macOS paths remain unrun.

FFmpeg must include zscale, FFV1, libx264, AAC, drawtext, gblur, vignette, rubberband, vidstabdetect, vidstabtransform and prores_ks, plus pass actual RGB16 high-bit composition checks.
The explicit CI provisioning script verifies the Windows Gyan archive checksum or
macOS ffmpeg@7 version/revision and compatible bottle metadata before install,
then checks capabilities through real media. No automatic source-build fallback
or in-app installation is intended. The detailed report records selected versions
and environment; Homebrew transitive dependencies are not completely locked.

macOS 15 ARM replaces the initial macOS 14 runner proposal because current
Homebrew dependency bottles require newer macOS. This does not establish support
for earlier macOS, Intel Macs or Windows ARM. macOS packages are tar.gz archives
preserving executable modes/symlinks, and the packaged smoke test runs from a fresh
extraction. Failure diagnostics are retained separately from successful packages.

## Distribution exclusions

The default draft build explicitly excludes sherpa_onnx, sherpa_onnx_core, onnxruntime and tokenizers.
It does not bundle Kokoro/Whisper models, eSpeak data, dictionaries, FFmpeg,
third-party fonts or external whisper.cpp executables. Consequently the frozen
draft does not yet offer TTS or semantic-search inference; current AI evidence is source-run with
explicit separately installed dependencies. This restriction remains until the
component-license and native-inference packaging review is complete.

Original sticker assets, 24 procedural sounds, 53 individually documented CC0 sounds and their provenance/license notices are included.
Their distribution license is an owner decision; no public-domain dedication is
made. The private repository name LumenEdit is authorized. Public licensing, releases,
signing and notarization remain separate owner decisions.

## Acceptance before calling either platform supported

- Run all tests, retain failure/skip details and dependency manifests.
- Pass packaged runtime self-test on each target architecture; expand beyond the
  initial runner architecture to Apple Silicon/Intel and relevant Windows targets.
- Verify physical preview/audio, seek, file dialogs, non-ASCII paths, system fonts,
  cancellation and source protection on actual target machines.
- Test local ASR and licensed TTS with complete packs from a disconnected cold
  start, without a cached online session or any silent download/cloud fallback.
- Complete Qt/FFmpeg/ASR/TTS/font/component notices and redistribution audit.
- Complete signing/notarization/installer/upgrade procedures before release.
  Do not disable OS security or bypass warnings to make an unsigned draft run.

Historical native runs exposed provisioning, file-identity, path and FFmpeg-version differences. Current-source successful distribution still requires full tests and extracted-package smoke on each exact target commit. No current native success is asserted here.

## Additional dual-workspace / SDR gate

On each native platform, verify a fresh launch defaults to Simple, one-click mode switching preserves an edited v6 project and undo/redo, and mode preference survives restart without entering the project file. Exercise keyboard focus, hidden-control Delete protection, high-DPI layout and the full caption/voice panel in both modes. Compare decoded exports from the same project across modes.

Run the tagged RGB16 sRGB/linear and YUV10 matrix/range fixtures with the exact bundled/selected FFmpeg build; assert actual source and output tags. Limited-range RGB remains unsupported and must fail clearly. Verify original v1–v5 projects migrate to Rec.709 assignment without resets. Check actual monitor behavior separately; Linux tests and screenshots do not establish calibrated or native color accuracy.

## Native Chinese caption fixture

CI explicitly downloads the commit-pinned Noto Sans CJK SC 2.004 font and its
SIL OFL-1.1 license into runner temporary storage. Both SHA256 values, font
metadata, and required Chinese glyph coverage are verified before rendering.
A configured fixture failure is an error, not a skipped Chinese-caption test.
The workflow retains `build/test-font-provenance.json`; no test font binary is
shipped in the application or source ZIP. Customer fonts remain local runtime
dependencies. The extracted-package smoke also renders a Chinese caption when
this external CI fixture is configured. This does not establish font availability
on a customer's clean machine.

## Historical appendix: completed initial runner gate — 2026-10-15

[Run 38774145073](https://github.com/liangtovi-debug/LumenEdit/actions/runs/38774145073)
succeeded at bbda84a97ca5862c272069b6dad83223c1406fe6: Windows x64 983 passed /
7 POSIX-only skips, macOS 15 arm64 989 passed / 1 Windows-drive-only skip. Both
re-extracted frozen packages passed genuine render/export and Qt video decoding,
with original stickers and Chinese captions. No Chinese-font coverage was skipped.
This completes the initial CI-runner gate, not the physical desktop, additional
architecture, offline model inference, licensing, signing or release gates above.
See [native quickstart](NATIVE_TEST_QUICKSTART.zh-CN.md).

## Project compatibility and current frozen coverage

Current projects use schema32. Preserve originals and test with copies: the historical schema9 application cannot open schema32 and downgrade-save is not supported. Expanded smoke must exercise current qualifier/window animation and time editing, experimental Apple Log 1 synthetic input, and high-bit caption/static-sticker output from the extracted frozen executable. Source imports alone do not prove frozen module/resource inclusion. This is not real camera certification, live GPU preview, HDR or automatic tracking.

## Additional schema31 static-composite native checks

The existing native smoke alone is not a claim of packaged PIP acceptance. On each actual Windows/macOS package, additionally verify two synthetic video sources in preview and H.264/ProRes export: transformed exterior reveals the lower track, true black and fitted black bars stay picture content, opacity0 preserves audio/timing, and unsupported compositor or scalar blur capability fails explicitly. Exercise percent controls and disabled multi-selection in both workspaces/languages, save/reopen, undo/redo and cancel. Include narrow 2-pixel RGB8 blur at maximum sigma, because optimized FFmpeg CPU paths can corrupt this case; the new opt-in path probes and uses scalar blur. Record exact package/source identities and screenshots; do not reuse historical results.


## Additional schema32 animated-picture native checks

The schema31 static checks above remain required. On each extracted Windows/macOS package, verify manual X/Y/scale/opacity keys with outgoing Hold/Linear interpolation, exact keyed endpoints, first/last holds, hidden signed keys, split/left trim/roll continuity, save/reopen, recovery, undo/redo and one-undo conversion to static. No-key clips must retain the static path. Exercise nonzero clip start, fractional source origin/tail and non-unit speed at fractional project rates in preview, H.264 and ProRes output. Record actual package/source identities, output precision and frame alignment; Linux source tests and historical package results do not establish current native acceptance.

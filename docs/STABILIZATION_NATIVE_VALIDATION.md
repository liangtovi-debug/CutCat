# Stabilization native validation — 2026-10-15

Code commit: `d3c77edef4974649bda99a1a1f784a943066f592`. [Private CI run](https://github.com/liangtovi-debug/LumenEdit/actions/runs/38824635143). Both jobs completed successfully. This record and updated milestone documentation are added after the tested code; no runtime code or tests changed afterwards.

## Results

- Windows Server 2022 x64: 1,922 passed, seven POSIX-only skips, zero failures/errors; 738.430 seconds.
- macOS 15 arm64: 1,928 passed, one Windows-drive-only skip, zero failures/errors; 465.940 seconds.
- Local Linux: 1,928 passed, one Windows-drive-only skip; 592.15 seconds.
- Each native platform: 414 Qt layout states at 1×/1.5×/2×, English/Chinese, zero recorded violations; 16 screenshots from the re-extracted frozen app.
- No Chinese-font test skipped. CI uses an explicitly provisioned, hash-verified Noto font, not bundled with the app. Customer system font fallback and physical IME remain unverified.
- Real frozen executable: local source snapshot/hash integrity, analysis, stabilization, project save/reopen, preview, export, Qt frame decoding, UI apply/undo and language state invariance passed.
- Existing caption, LUT 0.4, sticker/effect, constant speed and shortcut runtime checks retained. No actual optional model inference or physical speaker/keyboard acceptance is claimed.

## Measured fixture, not a general performance promise

Original procedural 320×240, 30-frame, 30fps known translation. Strength 0.8, smoothing radius 8, maximum zoom 1.2. On Mac/Windows respectively, diagnostic inter-frame jitter changed from 4.817 px to 2.716 / 2.729 px; preview/export encoded-video mean pixel difference was 4.154 / 4.179 out of 255. All 30 frames and source hash were preserved. Smoke step elapsed 5.259 / 7.532 seconds, including analysis/render/checks; this is not a steady-state FPS benchmark or HD/4K guarantee. Native screenshots deliberately show the procedural QA chart.

The broader regression includes true combined rotation/translation, fractional-rate split and source-origin phase invariance, enabled/disabled encoded audio and decoded PCM comparison, cancellation/process reaping, cache/source replacement, crop limits and float interpolation tests. Independent audio, captions, stickers and TTS timeline positions are preserved.

## Dependency and distribution scope

Windows CI: external Gyan FFmpeg 9.0.2 essentials. Mac CI: external Homebrew ffmpeg@7 7.1.5 revision 3. Both verified libvidstab and Rubber Band capabilities. FFmpeg, models, third-party fonts, sherpa-onnx, ONNX Runtime/tokenizers and speech frontends are not redistributed here. Whole-source limits are 600 seconds, 18,000 project-grid frames, 16 GiB and active picture at least 64×64. Processing is CPU-based; analysis precedes cached preview. Optional inference in the source development setup does not imply frozen-package inference support.

Unsigned/unnotarized internal test archives. Native runner acceptance is not clean-machine installation, consumer Windows 10/11, Intel Mac, Windows ARM, minimum OS, hardware audio, monitor calibration, or commercial release certification. Do not disable OS security to run a blocked archive. Internal executable basename remains LumenEdit-Draft for compatibility; visible English name is CutCat and Chinese name is 剪辑猫.

Known limits include source-encoded RGB bilinear warp, fixed source-wide crop, conservative motion rejection, external runtime variation, and an existing final-frame duration-rounding limitation. Rolling-shutter repair, parallax correction, deblur, optical flow, beauty and AI restoration are not implemented by this milestone. See [implementation and usage](STABILIZATION_MILESTONE.md).

## Package identity

- [CutCat-macOS-arm64-画面稳定.tar.gz](https://github.com/liangtovi-debug/LumenEdit/actions/runs/38824635143/artifacts/12014957254): 56,120,487 bytes; SHA256 `bcaa276c66356d8f5aa198d9329cd1f435b6a51763867964eadc50348409386a`.
- [CutCat-Windows-x64-画面稳定.zip](https://github.com/liangtovi-debug/LumenEdit/actions/runs/38824635143/artifacts/12014929192): 73,670,956 bytes; SHA256 `071f9713739b503eb057ec35b2fc0cfce738ae0341aba39d60dc3595edf6d5b6`.

GitHub artifact retention is seven days from 2026-10-15; durable unchanged package copies are also retained in the owner’s Library. No archive was split/repacked to evade an attachment-size restriction.

# Bilingual UI and LUT native validation — 2026-10-15

Code: `2b7db2dd260b58b1280480cbb0aeabdd46f668c7`.
Both jobs succeeded in [run 38799830277](https://github.com/liangtovi-debug/LumenEdit/actions/runs/38799830277).

- Windows Server 2022 x64: 1093 passed, 7 POSIX-only skips; zero failures/errors.
- macOS 15 arm64: 1099 passed, 1 Windows-drive-only skip; zero failures/errors.
- Each platform: 414 Qt states, Chinese/English × three scales × 69 size/workspace/panel states; zero geometry violations.
- Each platform: rebuilt archive re-extracted and frozen executable run; real preview/export, project save/reopen, Chinese caption, original local 3D .cube at strength0.4, decoded Qt viewer and six Chinese/English Simple/Professional/LUT screenshots.
- Both frozen runs verified real nonzero UI glyphs, official Qt Chinese dialog translation and locale-switch editor-state invariance. Screenshots were visually inspected. A suspected Windows leading-character loss in the downscaled viewer was disproved by original-pixel crops; source labels are complete. No speculative repaint modification was made.
- Linux final source: 1099 passed, 1 platform skip. Independent extracted-source check: 71 language/LUT tests passed.

## Packages

| Package | Bytes | SHA256 |
|---|---:|---|
| CutCat-Windows-x64-中文英文-LUT.zip | 71765120 | cd00e4440adcd97c735cc2a684b3ed07eaf965d96dff42bb58a3da4bf7783f43 |
| CutCat-macOS-arm64-中文英文-LUT.tar.gz | 52898784 | e5c928a2bcdc94d9d8b72ea5879626c72b0dc16674e1807f07ca9842a0102722 |

Archive members retain legacy LumenEdit-Draft executable/app names. New app preferences default to Simplified Chinese; saved English preference persists. UI locale changes brand (剪辑猫 / CutCat), not source/project text or rendering. Unknown external diagnostics remain verbatim beneath a translated explanation.

## LUT evidence

34 focused acceptance cases cover 0/.5/1 strength, default1 for prior project versions, bounded .cube parsing, unsupported/malformed input, chooser/undo/redo/remove, save/reopen, active-content cache changes, zero-strength missing-file bypass, and cleanup. Independent preview/export engines both called the shared float32 color evaluator. Lossless RGB48 stage comparison tolerance was 2/65535; decoded H.264 comparison tolerance .025. These are test tolerances, not measured universal fidelity guarantees. See [LUT workflow](LUT_WORKFLOW.md) for exact domain, interpolation and encoded-Rec.709 blend order.

## Boundaries

Native OS build/runtime validation uses Qt offscreen and a separately verified external Noto fixture, not full physical desktop interaction. No font is bundled or downloaded by the app. System-font fallback on clean desktops, IME, all display/DPI configurations and physical speakers remain unverified. Minimum OS is not established; Windows10/11 user desktops, Intel Mac, Windows ARM and older macOS are not certified by these runs.

Unsigned/unnotarized test packages; do not bypass OS warnings. FFmpeg/ffprobe and caption fonts remain external prerequisites. Optional AI runtimes/models are excluded. No OFX, cloud API or MCP implementation was added. No public release, full competitor parity, HDR/RAW/OCIO or arbitrary LUT-format support is claimed. [Current usage instructions](NATIVE_TEST_QUICKSTART.zh-CN.md).

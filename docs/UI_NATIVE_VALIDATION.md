# Historical English-only UI native validation — 2026-10-15

Superseded by [bilingual UI and LUT validation](BILINGUAL_LUT_NATIVE_VALIDATION.md). This record describes the preserved earlier package, not the current interface.

Source build: `9dbedc469204d0aa0156525a3171782da0f92873`.
Both jobs completed successfully in [run 38790860486](https://github.com/liangtovi-debug/LumenEdit/actions/runs/38790860486).

- Windows Server 2022 x64: 1019 passed, 7 POSIX-only skipped, zero errors/failures.
- macOS 15 arm64: 1025 passed, 1 Windows-drive-only skipped, zero errors/failures.
- Each platform: 207 real Qt states at three logical sizes and three scale factors; zero geometry violations. Simple/Professional, edit/audio/color/wheels/curves, captions/appearance/TTS/auditions, local AI/search, stickers/effects covered.
- Both rebuilt archives were re-extracted and their frozen executable ran the smoke test: original local media generation, import/probe, project save/reopen, graded preview/export, original sticker, Chinese caption, actual Qt video sink decoding, real MainWindow Simple/Professional screenshots.
- Frozen smoke reported real nonzero UI glyph indices and visible glyph rasters, `native_ui_preview_decoded=true`; Windows decoded 1 sink frame, Mac 10. These are decoding smoke counts, not performance measurements.
- Both platforms' 1920×1080 frozen Simple and Professional screenshots were visually inspected. The English UI shows `CutCat` alone. Landscape preview is an original 640×360 synthetic test source, not an image-quality benchmark. Professional screenshots expose a working lift wheel and RGB controls. Vertical scrolling is intentional; complete content remains reachable.

## Packages

| Package | Bytes | SHA256 |
|---|---:|---|
| CutCat-Windows-x64-UI.zip | 71646810 | 3c180791c363ac8de461d604662259ad6f012b04375965a6816d008a9d05d016 |
| CutCat-macOS-arm64-UI.tar.gz | 52782773 | 8073564e6281acc80245b81375e5ac4f6e534f61912132ac5468c563d566f4a6 |

Archive members keep legacy `LumenEdit-Draft` executable/app names. Internal module, settings and project IDs remain compatible. No repository rename, public release, public license or new runtime/model redistribution occurred.

## Important boundaries

Native OS builds were executed, but automated UI runs use Qt `offscreen`, not physical desktop plugins. The Windows offscreen backend had no OS font database. The test process and frozen smoke explicitly registered the separately downloaded/hash-verified Noto fixture; no font is included in the shipped packages. This establishes real glyph rendering with that local fixture, not clean-machine Windows/macOS system-font fallback or all monitors/IME interactions.

External FFmpeg/ffprobe and local caption fonts remain prerequisites. Packages are unsigned/unnotarized test drafts. Do not bypass OS security warnings. Physical speakers, Windows 10/11 desktops, Intel Mac, Windows ARM and older macOS remain unverified. Minimum supported OS is not established. Optional native AI runtimes/models are excluded; source inference evidence is not frozen-package inference validation. No full competitor parity claim is made.

English interface branding is CutCat regardless of OS language. The Chinese display-name mapping is 剪辑猫 for a future actual Chinese interface; complete localization and a language switch are not implemented.

## Diagnosis retained

Earlier native passes could not be accepted solely from test counts: manual screenshot review found missing Windows UI glyphs in a separate frozen process. The smoke now verifies glyph raster content, and the final screenshot was reinspected. Empty-panel horizontal-overflow assertions were never relaxed. Hidden forms now use actual viewport widths and field minimums; failed geometry retains complete JSON plus PNG. See [UI alignment acceptance](UI_ALIGNMENT.md) and [quickstart](NATIVE_TEST_QUICKSTART.zh-CN.md).

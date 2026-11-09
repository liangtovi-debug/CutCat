# Independent Effects / Stickers native validation

Validated 2026-10-15, commit `cef94b3f3c911fbc7970df74f93b2ea94811847c`.
[Native CI run](https://github.com/liangtovi-debug/LumenEdit/actions/runs/38803835084) completed successfully on Windows x64 and macOS arm64.

## Delivered change

Effects and Stickers are independent top-level inspector pages with independent lists, selection and controls. Professional mode exposes the ordered clip-effect stack (Gaussian blur / vignette); Simple mode retains Stickers and preserves professional effects without disabling them. Returning to Professional restores the Effects page when appropriate. Language and workspace switches preserve project data, staged controls, undo history and renderer identity. No model schema or processing algorithm changed.

## Evidence

- Linux: 1110 passed, 1 Windows-drive-semantics skip.
- Windows: 1104 passed, 7 POSIX-specific skips; zero failures/errors.
- macOS: 1110 passed, 1 Windows-drive-semantics skip; zero failures/errors.
- Each native platform: 414 real Qt layout states across two languages, Simple/Professional and three scales; zero geometry violations.
- Each re-extracted frozen package: 10 actual UI screenshots, verified Chinese glyphs, decoded preview, caption burn-in and original .cube LUT at strength 0.4; locale/project state invariant.
- Both native Chinese Effects and Stickers screenshots manually inspected for readable text, distinct pages/lists and aligned controls.
- Extracted source: 158 manifest entries verified; 49 panel/LUT/localization tests passed.

Native runs use Qt offscreen and an external, hash-verified Noto test font. This is not a physical desktop/IME/font-fallback acceptance test. Physical audio output and optional AI inference were not tested in this run. Existing runtime/model and external FFmpeg requirements apply. Unsigned test packages remain unsigned; Mac package is Apple Silicon, not Intel/universal.

## Package hashes

- `CutCat-Windows-x64-独立特效贴纸.zip`, 71766026 bytes, SHA256 `f949fd1431384be7419abd820fa5dff74d10267feccf8cd5632f82e9c64f3458`.
- `CutCat-macOS-arm64-独立特效贴纸.tar.gz`, 52898657 bytes, SHA256 `dc824fa56da033b77adf9082ad2b67ea20448b1f1c338bb94104eecee94265c0`.

The repository commit contains only 13 code/test/capture-script changes. Later local planning/validation documents are included in the source delivery but are not represented as remotely committed. The earlier canceled documentation-only request was not retried or bundled.

Speed mapping/stabilization and Wink-inspired beauty acceptance remain design work. Restoration, beauty, speed, stabilization and the proposed new icon are not implemented by this package.

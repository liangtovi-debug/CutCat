# Local component setup validation — 2026-10-16

## Scope and environment

Linux 6.18.44 x86_64, glibc2.41; Python3.12.14; PySide6/Qt6.8.3; NumPy2.2.4; system FFmpeg7.1.5. Qt used the offscreen platform with a separately downloaded, pinned Noto Sans CJK SC2.004/OFL-1.1 test font. The font is not bundled in the source deliverable. This is not Windows/macOS native, physical-display, microphone/speaker, packaged-app, or new AI inference acceptance.

No GitHub update or CI was performed. No optional native AI package was installed or executed. Actual empty-environment diagnostics report all unconfigured assets and four unavailable optional distributions accurately; `inference_tested` remains false.

## Functional checks

New backend tests cover no native import/subprocess/download calls; missing, malformed, symlink and special-file inputs; size/header checks; optional pinned hashes; cancel; source replacement; dependency version/build guidance; bounded atomic preferences, including deeply nested malformed JSON.

Real Qt tests cover the existing MainWindow job lifecycle, cancellation/retry, stale results, close/reopen while work exits, active-job/stale-setting Apply guards, local choosers, persisted paths, corrupted-preference startup, both menu and panel entrypoints, EN/ZH changes preserving user paths, and unchanged project/revision/model-verification state.

Independent source review found and corrected missing-preferences clobbering, unmanaged thread ownership, URL/UNC acceptance, stale Apply behavior, inconsistent preference bounds and deeply nested JSON startup failure. Review is not runtime or native-platform certification.

## Layout evidence

- Existing Local AI panels:72 real Qt measured states: two languages × three scales × three logical window sizes × two workspace modes × two AI panels; zero geometry violations. These72 states have JSON measurements only, not screenshots.
- Setup dialog: English/Chinese × scales1/1.5/2 × two simulated screen sizes (1920×1600 and1366×768 physical); five captured states per combination. Screenshots are actual Qt widgets; synthetic diagnostic reports are explicitly geometry fixtures.
- Separate user-facing Chinese screenshots use the real diagnostics on an empty configuration, showing unconfigured components rather than fictional working models.
- Fixed initial high-DPI screen overflow, height-for-width note overlap and late CJK button-height minima. Layout tests also simulate native frame margins; this is not a substitute for actual Windows/macOS window-manager testing.
- At1366×768/200%, the path-form viewport is cramped (about68 logical pixels) and depends on vertical scrolling. Each of its seven fields is fully reachable, and actions/results remain accessible; not all fields are simultaneously visible. Extremely smaller logical displays may remain below the dialog's usable minimum.

## Source preservation

Compared with the226-file restored beauty-source manifest, only README.md, DELIVERY.md, lumen/ui.py and lumen/ui_ai.py changed among baseline files. All222 other original files, including every beauty prototype/test and media/render module, retain their original hashes. New setup modules/tests/docs are additive. Source archives exclude virtual environments, models, optional runtime binaries, caches and downloaded fonts.

## Results and reproducibility

Exact terminal regression counts are recorded below after completion. Logs, final captured geometry and selected screenshots are included in the accompanying evidence archive.

Run from an environment prepared using the existing project quickstart:

```sh
QT_QPA_PLATFORM=offscreen LUMEN_TEST_CJK_FONT_DIR=/path/to/verified/test-font \
  python -m pytest tests/test_component_setup.py tests/test_component_setup_ui.py \
  tests/test_component_setup_entrypoints.py tests/test_component_setup_layout.py \
  tests/test_i18n_coverage.py -q
```

The font provisioning script requires an explicit `--download`; app startup never downloads fonts or models. The separate capture script in the evidence archive documents simulated screen/scaling arguments.

### Terminal results

- Initial full regression: **2392 passed, 1 failed, 1 skipped**, 1128.04s; process exited1. The sole failure was the missing Chinese `Close` catalog entry, discovered while layout work was still in progress. The skip requires native Windows drive semantics.79 warnings were existing Qt `setActiveWindow` deprecations.
- Fixed `Close` to `关闭`; final post-fix/setup-layout/localization run: **46 passed**, 20.35s, process exited0. This run includes all18 tests added after the initial full collection and rechecks the failing catalog test. The later source also normalizes deeply nested preference JSON and contains the final small-screen layout fixes.
- Final collection: **2412 cases**. Every collected case has a passing result or the one explicit platform skip across the full run and final targeted rerun. This is combined coverage, **not** a claim that one fresh2412-case full run was performed on the final snapshot.
- The existing AI/backend subset separately passed225 cases during implementation. Counts overlap and must not be added.
-22 layout/dialog tests independently passed20.87s; these overlap the final46.

The retained full-regression log deliberately includes the original failure. No native CI was started, and no optional model inference was used as a substitute for these tests.

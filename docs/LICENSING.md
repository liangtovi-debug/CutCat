# Licensing and distribution gates

The original project material in this source snapshot is covered by the [CutCat Use, Modification and No-Sale License](../LICENSE). Anyone may use, modify and redistribute it free of charge, but may not sell the software, copies or modified versions. This is not a general commercial-use ban or a restriction on selling works created with CutCat. It is a source-available licence, not an OSI-defined open-source licence. Separately licensed material retains its own terms, including the CC0 sounds/catalogue and Apache-2.0 Apple Log code (including frozen copies). This checklist is not legal advice or a determination that native application packages are ready to distribute. Retained LumenEdit executable/package identifiers do not grant trademark rights.

本次源码发布的项目原创材料适用根目录 [LICENSE](../LICENSE)：允许使用、修改和免费分发，禁止售卖软件、其副本或修改版本；不等于全面禁止商业用途，也不禁止销售使用本软件创作的作品。CC0 音效和目录、Apache-2.0 代码等继续适用各自许可。这是源码可用许可，不是 OSI 定义的开源许可；本说明不表示原生安装包已经完成合规审查。

## Dependency inventory to audit

| Component | Current use | Required review before distribution |
| --- | --- | --- |
| Python 3.12 | Interpreter/runtime | Include the applicable PSF and bundled component notices. |
| PySide6 6.8.3 / Qt / Shiboken | Native UI and associated Qt modules | Inventory the precise shipped libraries/plugins and their licenses. Select a compliant open-source or commercial licensing route. Preserve notices and satisfy the obligations of that route. |
| NumPy 2.2.4 | Preview scopes and numerical image processing | Preserve NumPy and bundled numerical-library notices; inventory binary wheel dependencies. |
| fontTools 4.61.1 | Caption font inspection and face extraction | Installed metadata declares MIT; retain `LICENSE` and review `LICENSE.external` and the actual collected code. Fonts remain separately licensed assets. |
| Pillow 13.0.0 | Caption font metrics through FreeType and RAQM | Installed metadata declares MIT-CMU; retain its license and inventory the exact wheel's bundled libraries, including text-shaping dependencies. Check native RAQM availability, not only the Python version. |
| regex 2026.9.29 | Extended-grapheme caption wrapping | Installed metadata declares `Apache-2.0 AND CNRI-Python`; retain its complete license text. |
| PyInstaller 6.12.0 / pyinstaller-hooks-contrib 2025.1 | Native packaging | Review the license and bootloader exception, collected dependencies and notices. The packaging exception does not relicense collected software. |
| setuptools 75.8.0 / wheel 0.45.1 / pytest 8.3.5 | Build-system and test tools | These are declared build/test dependencies, not intended application features. Record resolved versions and investigate unexpected inclusion in the frozen package. |
| FFmpeg / ffprobe | External subprocesses for probing and rendering | Record exact package, version, source, build configuration and enabled libraries. Review LGPL/GPL/nonfree implications, redistribution obligations and codec patents for target jurisdictions. |
| openai-agents 0.24.0 (`agents` extra) | Optional offline fake-model orchestration | Installed metadata declares MIT. Audit its resolved transitive dependencies if included; the default CI installation does not request this extra. |
| onnxruntime 1.22.1 / tokenizers 0.21.4 (`search` extra) | Optional local semantic-search worker | Deliberately excluded from the default native draft. Runtime/model license and architecture review remain separate before packaging or redistribution. |
| sherpa-onnx 1.12.26 / sherpa-onnx-core 1.12.26 (`tts` extra) | Optional local voice worker | Deliberately excluded from the default native draft. Runtime/model/voice-pack rights and native integration require separate review. |
| External ASR, subtitle-AI and voice executables/model packs | Explicitly selected local optional components | No model weights or these executables are bundled in this source snapshot. See the component-specific documentation; registration/hash validation is not license clearance. |
| User LUTs, fonts and media | User-selected assets | Verify rights when redistributing sample projects, presets, exports or bundled assets. |

The pins above come from `pyproject.toml`. The Pillow, regex, fontTools and
openai-agents license labels were read from the installed distributions' metadata
and accompanying license-file inventory on 2026-10-24, not inferred from a
project name. They do not describe every transitive library or prove compliance
of a Windows/macOS binary. `scripts/build_app.py` checks the base runtime and
PyInstaller/hook versions, but the package still needs native loader and caption
render validation. There is no complete hash-locked transitive dependency graph
or per-platform SBOM here; `pip freeze` alone is not either of those artifacts.

Qt for Python contains third-party components with their own terms; a single label is insufficient. Start with [Qt for Python 6.8 licenses](https://doc.qt.io/qtforpython-6.8/licenses.html), [Qt licensing](https://doc.qt.io/qt-6.10/licensing.html) and [Qt's explanation of LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations). An onedir package is a practical packaging choice, not proof of LGPL compliance. Review library replacement/relinking, notices, source availability and installation restrictions as applicable. Commercial licensing must be evaluated against the actual development and distribution circumstances.

FFmpeg's license depends on its build and linked components. Consult the [FFmpeg legal page](https://ffmpeg.org/legal.html) and capture `ffmpeg -version` plus `ffmpeg -buildconf` for every proposed bundled build. Review codecs separately; an open-source software license is not a blanket patent license. The current application expects separately installed executables and the build script does not copy them into the application. This reduces bundling scope but does not replace legal review of the overall product or any future installer that supplies them.

## Release checklist

- [x] Record the owner-selected no-sale licence and approved CutCat branding for this source snapshot; preserve independently licensed exceptions.
- [ ] Freeze and audit a complete dependency/asset inventory and SBOM for each OS/architecture.
- [ ] Audit exact Qt libraries/plugins collected by PyInstaller, including transitive dependencies.
- [ ] Select and document the Qt licensing route and fulfill its requirements.
- [ ] Select supported FFmpeg builds; review configurations, codecs and geographic distribution.
- [ ] Prepare third-party license texts, acknowledgments, source offers/source materials and replacement/relinking instructions where applicable.
- [ ] Review packaged artifacts, not only dependency declarations; remove unexpected components.
- [ ] Validate any new installer, updater, download mechanism or terms before enabling it.
- [ ] Obtain release approval and platform signing/notarization through an authorized process.

CI artifacts are unsigned engineering drafts. Their availability is not permission to publish them broadly or a completed license audit. No FFmpeg binary download mirror, bundled codec license, commercial Qt entitlement or signing credential is provided by this project.

The CI-only FFmpeg provisioner currently pins the Windows Gyan 9.0.2 archive and
macOS Homebrew ffmpeg@7 7.1.5 revision 3 top-level bottles. It does not bundle them
with the app. Homebrew transitive versions are resolved and recorded rather than
fully locked. The test-font provisioner verifies Noto Sans CJK SC 2.004 and its
OFL-1.1 text against pinned hashes in an external CI cache; the font is not copied
into the application. A passing test that uses this external font does not prove
the clean target machine has a suitable licensed CJK font installed.

## Bundled asset inventory

There are **77 packaged WAVs**, not 77 CutCat originals:

- 24 original procedural sounds and `catalog.json` have the asset-specific CC0
  notice in `lumen/assets/sounds/LICENSE.txt`. Their mathematical synthesis source
  is `scripts/create_original_sounds.py`.
- 53 third-party sounds are separately identified in `third_party_catalog.json`
  and `THIRD_PARTY_PROVENANCE.json`: Kenney Interface Sounds (18), Kenney Impact
  Sounds (19), Kenney RPG Audio (7), Iwan Gabovitch's bamboo swishes (4), muted.io
  Birds of Hawaii (4), and Ove Melaa's rain (1). Their recorded asset-specific
  license is CC0-1.0. The original CutCat notice does not speak for these authors.
  `lumen/assets/sounds/THIRD_PARTY_README.md`, `licenses/source-declarations.json`,
  the four pack-license notices and `licenses/CC0-1.0.txt` preserve source
  URLs, short declaration excerpts, retrieval dates and hashes. This is recorded
  provenance, not a warranty covering every possible right or an independent
  legal determination. No full website HTML or original download archives ship.

All 77 WAV identities are covered by the catalogs and asset tests. The third-party
provenance records no human listening acceptance pass; hash/numeric checks are not
a substitute for one. User-imported sounds are not
license-verified and must not be redistributed without appropriate rights.
Neither sound-pack notice changes the licence of other application material;
conversely, the project no-sale licence does not restrict the CC0 rights already
granted for these sounds and the original catalogue.

Other shipped resources and source fixtures:

- `lumen/assets/app/provenance.json` identifies the approved original icon master
  by hash and records the PNG/ICO/ICNS conversion outputs and their hashes. The
  source master is not duplicated in the bundle. The metadata is provenance,
  not a new application/artwork redistribution license.
- Ten geometric sticker PNGs and the animation manifest have an included
  procedural generator (`scripts/create_original_stickers.py`). Their
  `lumen/assets/stickers/LICENSE.txt` references the root project licence for
  these original assets. The two UI chevron SVGs are simple inline geometric
  paths, with no external references; they also fall under the root licence.
- `lumen/assets/licenses/aces-input-and-colorspaces/ATTRIBUTION.txt` and `LICENSE`
  retain the pinned ACES Apple Log equation reference's Apache-2.0 attribution,
  license text and CutCat modification description. These notices are included
  by the asset packaging rules. No Apple LUT is bundled.
- The three `tests/fixtures/*.npz` files are small synthetic numeric regression
  oracles, not user footage or model weights. They contain float32 RGB/mask/output
  arrays plus plain Unicode settings/manifest data, and load with
  `allow_pickle=False`. Their companion provenance records describe seeded
  historic captures; the contrast fixture also includes its capture script.
  They are test-only files outside the packaged `lumen` resources. This is not
  permission to substitute future user-media fixtures into a distributed build.

This source publication does not establish native-binary release clearance.
Exact packaged binary contents, notices, optional components, signing and
native behaviour still require the applicable gates above; the source audit
does not claim that they passed.

## Optional controlled-assistant orchestration (2026-10-21)

The optional `agents` extra pins `openai-agents==0.24.0`, distributed under MIT.
It is not vendored in this source archive or required by the default offline app.
The implemented adapter uses only a built-in local fake Model and disables remote
tracing; it does not grant access to hosted models or replace provider terms.
Upstream license: https://github.com/openai/openai-agents-python/blob/v0.24.0/LICENSE
See `CONTROLLED_ASSISTANT.md` for integration and privacy limits.

## Public-copy contact redaction

Only the bamboo-swish author contact email has been omitted from its pack notice. Author name, website, source and the full licence statements are retained. The provenance record preserves the original notice hash and records the updated distributed-notice hash and the reason for the change. No sound or application code was changed for this redaction.

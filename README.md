# CutCat · 剪辑猫

**桌面视频剪辑原型 / Desktop video editor prototype**

## 中文

剪辑猫是以 Python 3.12、PySide6 和本地 FFmpeg 为基础的桌面视频剪辑原型。它提供时间线剪辑、调色、字幕、音频处理及本地预览和导出，并包含可选的本地语音和 AI 组件接口。功能范围和限制以各功能文档为准，不代表专业剪辑软件的完整替代。

- **使用与许可：**任何人可以使用和修改，也可以免费分发；禁止售卖 CutCat 软件、其副本或修改版本。这不是全面禁止商业用途，也不禁止销售使用 CutCat 创作的作品。CC0 音效、Apache-2.0 代码等独立许可材料仍按各自许可使用。完整条款见 [LICENSE](LICENSE) 和 [第三方许可说明](docs/LICENSING.md)。本项目是带禁止售卖条件的源码公开项目，不是 OSI 定义的开源软件。
- **发布范围：**此仓库只发布独立的源码快照，不包含原私有 Git 历史、个人邮箱、用户媒体、模型权重或 Windows/macOS 安装包。它仍是实验性原型，不是已完成原生验证的正式发行版。
- **验证限制：**公开仓库的 GitHub Actions 已禁用。部分完整回归测试依赖未公开的历史基线，不能仅凭此仓库复现；已有历史测试记录不代表当前版本全部通过。详见 [发布说明](docs/PUBLICATION.md)。
- **项目状态：**此次源码发布后，项目开发和测试停止；没有启用自动同步或持续构建。

从源码运行：使用 Python 3.12 创建虚拟环境，运行 `python -m pip install -e .`，单独安装适用的 FFmpeg 和 ffprobe，再运行 `python -m lumen`。依赖和可选组件需要分别遵守其许可；请先用备份素材试用。

[中文快速上手](docs/START_HERE.zh-CN.md) · [功能清单](docs/CURRENT_EDITING_CAPABILITIES.zh-CN.md)

## English

CutCat is a native desktop video-editing prototype built with Python 3.12, PySide6 and local FFmpeg. It includes timeline editing, colour controls, captions, audio processing, local previews and export, with optional local speech and AI component interfaces. Individual feature documents define the supported scope and limitations; this is not a complete replacement for a production editing suite.

- **Use and licence:** Anyone may use, modify and redistribute it free of charge. Selling the CutCat software, copies or modified versions is prohibited. This is not a general ban on commercial use and does not prohibit selling works created with CutCat. Separately licensed material, including CC0 sounds and Apache-2.0 code, retains its own terms. See [LICENSE](LICENSE) and [third-party licensing notes](docs/LICENSING.md). This is source-available software with a no-sale condition, not OSI-defined open-source software.
- **Publication scope:** This independent source snapshot contains no private Git history, personal account email, user media, model weights or Windows/macOS installers. It remains an experimental prototype, not a fully validated native release.
- **Validation limits:** GitHub Actions is disabled in this public repository. Some full-regression tests require an unpublished historical baseline and cannot be reproduced from this repository alone. Historical test receipts are not a claim that this version passes every check. See [publication notes](docs/PUBLICATION.md).
- **Project status:** Development and testing are stopped after this source publication. No automatic synchronization or ongoing builds are enabled.

To run from source, create a Python 3.12 virtual environment, run `python -m pip install -e .`, install suitable FFmpeg and ffprobe executables separately, then run `python -m lumen`. Dependencies and optional components have their own licences. Start with copied media, not irreplaceable originals.

## 历史开发记录 / Historical development notes

以下内容保留了源码中的历史开发记录。旧的“当前候选”、许可待定、继续开发、CI 和交付描述只反映当时状态；本页上方和本仓库 LICENSE、docs/PUBLICATION.md 说明此次发布状态。

The material below preserves the source's historical development notes. Earlier “current candidate”, pending-licence, development, CI and delivery statements describe their original rounds. The introduction above, LICENSE and docs/PUBLICATION.md define this publication's status.

---


# Local candidate: session In/Out range preview

Set preview In/Out with `I` / `O` on the timeline, then `Shift+Space` to build and
loop the final selected picture and mix. The viewer **Range preview** menu exposes
all commands. Out includes its displayed frame; the ruler highlights the selected
window. Marks are session-only, separate from persistent annotations and Undo.
Existing 10-second / 600-frame limits, source checks, cancellation and explicit
full-preview fallback remain. [Workflow and limits](docs/RANGE_PREVIEW.md#session-inout-selection).

Based on private main `c709771a3c587152a46facfeced5303e817fa0ec` (the four upstream CI files are retained exactly).
Local review candidate only; no main update, Actions run or native package claim.

# Current local candidate: final A/V preview around the playhead

Build and play a bounded final SDR range (default ±2 seconds), with loop,
original clip/global clocks and 48 kHz stereo mixing. No timeline/schema/history
mutation; unsupported combinations explicitly fall back to the full preview.
[Workflow, bounds, clock contract and evidence limits](docs/RANGE_PREVIEW.md).

Based on accepted private main `f91f80ac101ea8fed59fa44497a019faa375f50f`.
Local development only: matching frozen full/fresh acceptance and native
Windows/macOS packages are separate gates. No remote publication or hosted CI
is part of this round.

## Earlier rounds (historical descriptions below)

> New reconstructed candidate: [exact affine-linked A/V through-split](docs/AFFINE_LINKED_PAIR_SPLIT.md) preserves each role's J/L outer edges and residual tail. No historical validation is transferred to this candidate.

# Current candidate: paired-edge trim for affine-linked J/L pairs (schema43 unchanged)

Select exactly one complete linked V/A pair and drag either member's corresponding edge. One common integer timeline delta moves both own edges while preserving J/L offsets, independent rational tails and source-audio ownership. The preview shows separate canonical V/A durations; left gain keys rebase by each role's own global sample-boundary difference. Fades stay edge-anchored and audio may change. Affine through-split now has a separate bounded candidate; joint roll remains strict-v1-only. [Workflow, exact contract and evidence limits](docs/AFFINE_LINKED_EDGE_TRIM.md).

Baseline: private main `43022004f40fb6d8fede527121a6f16c52fd2b9f`, 1,054 files, identity `733a21df7198d071103c74f94565d249291c1bfcf357a537ddfa1b5cb04e1c2e`. Schema43, native version, renderer/audio compilers and dependencies are unchanged. Focused results belong to their individual frozen sources; current full/fresh, private-remote and native Windows/macOS package gates are not asserted here.

## Previous bounded rounds (historical descriptions below)

> Development candidate: [schema43 affine A/V relationship foundation](docs/LINKED_AV_AFFINE_FOUNDATION.md).
> The separate [staged audio-cut roll/reset candidate](docs/LINKED_AUDIO_CUT_ROLL.md) is under UI/media validation; it is not yet a release.
> Release gates remain pending; accepted strict-v1 editing behavior is retained.

# Current candidate: joint roll of two adjacent linked A/V pairs (schema42 unchanged)

Select exactly two complete adjacent strict linked A/V pairs. The existing roll inspector previews all four roles at one shared absolute timeline cut, preserving both outer endpoints and the active role, then commits once. No new IDs or links, ripple, transition, ownership change or silent extra-selection reduction. Exact rational tails and all hidden right-side keys are retained/rebased; non-fitting fades, source/key bounds, locks and changed historical audio order reject atomically. Audio can intentionally change. [Workflow, exact contract, limits and official comparison](docs/LINKED_AV_PAIR_ROLL.md).

Based on accepted R5 linked-pair split source: schema42, 1,028 files, identity `dd2501d3462f7b21c6ac6f90932e7d4a41f1e08185d94e883fb77e46945f5bda`. Schema, renderer/audio compilers and dependencies are unchanged. Source/offscreen tests, frozen aggregate/fresh acceptance, private integration and Windows/macOS packages remain distinct gates; no new pass or delivery is inferred from this candidate description. Independent A/V timing/J/L, linked speed/ripple, multi-partner and external sync remain absent.

## Previous bounded rounds (historical descriptions below)

# Current candidate: qualified joint linked A/V through-split (schema42 unchanged)

Pause playback, select one complete same-source linked pair and choose Split at playhead. Preview and Apply each verify actual CFR timestamps and complete native-resolution RGB16 legacy/global timing sequences before one atomic edit. Left IDs/link remain; the new right pair is selected with the active V/A role preserved. All hidden keys rebase exactly; crossing fades, changed potential mix order and incompatible PCM sample boundaries reject. [Workflow, exact contract, limits and official comparison](docs/LINKED_AV_PAIR_SPLIT.md).

First version: integer source In/Out, whole-frame 1×, exact source/project FPS, selected source union ≤ 30 seconds, fresh qualified media. Retimed/fractional/partial-tail/mixed-rate/VFR and active effects/stabilization/restoration reject; this is not J/L or arbitrary multi-component split. Based on accepted 1018-file edge-trim source identity `3bcb8e73ab42242e6dad3e5bf8b637b974597d405a8b2ad48510ff065f3f8d8c`. Schema, render/audio compilers and dependencies are unchanged. Matching frozen tests/independent/full/fresh acceptance, private remote/CI and native Windows/macOS packages are separate gates, not asserted here.

## Previous bounded rounds (historical descriptions below)

# Current candidate: joint non-ripple linked A/V edge trim (schema42 unchanged)

Select exactly one complete same-source linked pair and drag either edge of either member. Both roles preview together and commit in one Undo, preserving exact source-tail residue, rebasing hidden keys correctly and keeping other objects fixed. Fades retain their frame lengths and follow their edges; newly selected audio is not promised to match retained PCM. Partner collisions, locks, key overflow and changed historical mixing order reject atomically. [Workflow, exactness, safeguards and official comparison](docs/LINKED_AV_EDGE_TRIM.md).

Based on accepted linked-A/V schema42 source (1011 files, tree identity `5f81be5a75af37d3e4eb99123b71145eab1db46a23ef0025ebf5acd8029eec43`). Schema, render/audio compilers and runtime dependencies remain unchanged. Linked split/roll/speed/ripple, independent A/V/J/L timing and external sync remain unsupported. Matching focused/oracle/UI, independent review, frozen aggregate and fresh-extraction gates remain separate; remote/main/CI and Windows/macOS packages are not asserted here.

## Previous bounded rounds (historical descriptions below)

# Current candidate: persistent same-source linked A/V (schema42)

Explicitly link one suppressed V clip and one enabled A clip with identical source/timeline maps. Select/move/delete/copy the complete pair, choose an active V/A role for parameters, and preview/apply exact joint source slip in one Undo. Link/Unlink is metadata only and never restores video audio. This earlier round did not include linked edge trim; the current candidate above adds it. Linked split/ripple/J/L/external sync remain unsupported. [Workflow, safeguards, schema and official comparison](docs/LINKED_AV_EDITING.md).

Based on the accepted 994-file final-frame schema41 source. Render/audio compilers and runtime dependencies remain unchanged. Frozen focused, actual RGB/PCM, independent review, EN/ZH DPI, aggregate and fresh-extraction receipts remain separate gates. No remote/main/CI or Windows/macOS package result is asserted here.

## Previous bounded round (accepted local source; historical text below)

# Current candidate: final composite SDR still preview

Pause and explicitly refresh one complete final timeline picture without first encoding the whole timeline. Supported original SDR canvases up to 1920×1080 retain two-track grade/effect/geometry/alpha, caption and sticker order; unsupported active stabilization/transitions and other unverified inputs explicitly require the existing full preview. No audio, playback-cache replacement, proxy workflow or realtime claim. [Contract, limitations and official comparison](docs/FINAL_FRAME_PREVIEW.md).

Based on **locally accepted schema41** source `2c70ebb1e31733ee4d95fe20a43152ae0d38f5df`, including [clip volume keyframes](docs/AUDIO_GAIN_KEYFRAMES.md). This new candidate changes neither schema41 nor the existing renderer/audio/export/dependencies. Matching frozen-source focused, exact-pixel/performance, independent review, UI, aggregate and fresh-extraction gates remain separate. Remote/main/CI and Windows/macOS packages are not established by local-source evidence.

## Historical bounded-round descriptions below

Older “current candidate” headings and schema numbers below describe their original rounds, not the current source/schema or a fresh validation claim. The [current inventory](docs/CURRENT_EDITING_CAPABILITIES.zh-CN.md) distinguishes accepted foundations from this candidate.

# Historical round: strict explicit source-audio reattachment

Select exactly one source-audio-suppressed V0/V1 clip and one enabled A0/A1 clip. Preview the **two explicit IDs and the deletion of only that audio clip**, then Apply after a second source check. Both must share the same Media record, timeline start/duration and full exact rational source time map. The video retains every visual/source field and receives the selected audio's gain, mute, fades and effective historical mix slot. User-muted audio remains muted. One Undo restores both original records.

Schema **40**, render/audio compilers and dependencies remain unchanged. No automatic candidate matching, timing rebase/clamp, extraction, source writes, extra cleanup or persistent linked A/V relationship. Locks, groups, transition endpoints and ambiguous mix slots reject. Existing separated-component split/source-cut and copy policies remain. [Workflow, limits, exact PCM and official-editor comparison](docs/SOURCE_AUDIO_REATTACH.md).

This is a local candidate until matching frozen focused/UI, independent review, aggregate and fresh-extraction receipts are accepted. Private remote/CI and native Windows/macOS package results are separate gates; no paid-editor parity is claimed.

## Previous bounded round (historical scope)

# Current bounded round: Separate source audio — independent clips

Choose one committed video clip, explicitly choose free A0/A1, verify a staged preview, then Apply once. The new audio shares the original Media and complete timing/gain/mute/fades; the video's picture is unchanged and its source-audio component is structurally disabled. No extraction, clone-and-mute shortcut, automatic link or group. Delete/move/trim/mute are independent; Undo reverses the separation.

Schema **40** adds strict emission state and a historical summation anchor. Only final audio mixing-label order changes; legacy picture/input traversal and old-project output remain unchanged. [Workflow, exact PCM contract, edit restrictions and official-editor comparison](docs/SOURCE_AUDIO_SEPARATION.md). Split and source cuts of separated components conservatively reject in this first version. Dependencies unchanged. Matching frozen focused/UI, independent review, aggregate and extraction receipts are required; remote/CI and native Windows/macOS packages remain separate gates.

## Previous bounded round (historical scope)

# Current bounded round: private whole-clip Copy / Paste

Copy committed clips and complete groups, then paste at the captured playhead or an explicitly entered project frame on original tracks with relative gaps. Whole timing/grade/effect/animation/audio parameters are frozen independently; source files are shared, freshly checked and never copied. Collision, locks, transition endpoints and incompatible sample phases reject atomically. One Undo restores the original project.

Schema **39**, render/audio compilers and dependencies remain unchanged. [Workflow, dependency/resource limits and official-editor comparison](docs/CLIP_COPY_PASTE.md). This is window-private, same-session reuse, not system/cross-project clipboard, Cut, overwrite/insert Paste or paid-editor parity. Matching frozen-source focused/UI, independent review, aggregate and fresh-extraction receipts are required; source acceptance, remote/CI state and native Windows/macOS packages remain separate gates.

## Previous bounded round (historical scope)

# Current bounded round: persistent project timeline markers

Schema **39** adds named point/range annotations, comments, named colors, searchable list, an independent timeline lane and guarded navigation. Fixed project-frame metadata does not follow clips/ripple, extend project duration or modify rendered output. Shortened projects retain out-of-range annotations; navigation never silently clamps them.

[Workflow, strict persistence, preview lifecycle and official-editor comparison](docs/TIMELINE_MARKERS.md). No clip-attached markers, chapters, range export or paid-editor parity. This is a candidate until matching frozen-source focused/UI, independent review, aggregate and extraction receipts are accepted; native Windows/macOS packages remain a separate gate.

## Previous bounded round (historical scope)

# Current bounded round: close one captured interior gap

Choose an explicit target track and Capture playhead, preview its real-neighbor gap, then Apply once. **No real clip is deleted.** Optional tracks/captions/stickers translate as whole records; intersections reject and unchecked items remain fixed. Exact local audio and one common sample shift are required even for target-only gap closure.

Schema **38**, renderers/audio compilers, dependencies and old ripple policies unchanged. [Workflow, interval identity, proof limits and official-editor comparison](docs/SINGLE_GAP_CLOSE.md). Not Delete All Gaps, persistent Sync Lock, general ripple trim or paid-editor parity. Acceptance requires matching frozen-source focused/UI, independent review, aggregate and extraction receipts; no native or remote publication claim.

## Previous bounded round (historical scope)

# Current bounded round: safe ripple delete with explicit participants

Fixes fractional-frame downstream sample/fade corruption in the existing same-track action. A separate per-operation preview can include other Clip tracks, captions and stickers, deleting only the selected committed clip. Any intersecting follower rejects; unchecked items remain fixed and may lose synchronization. Exact local audio and common negative sample displacement are required.

Schema **38**, renderer/audio compilers and dependencies unchanged. [Workflow, exact boundaries, compatibility tightening and official-editor comparison](docs/RIPPLE_DELETE_PARTICIPATION.md). Not persistent Sync Lock, arbitrary range Extract, multiselect ripple, linked A/V or paid-editor parity. Acceptance requires matching frozen-source focused/UI, independent review, aggregate and extraction receipts; no native or remote publication claim is made here.

## Previous bounded round (historical scope)

# Current bounded round: explicit Source Insert participation

Choose additional Clip tracks and independently move downstream captions/stickers for one Insert. Destination-only remains the unchanged default. Crossing followers reject rather than split/stretch; nonparticipants stay fixed. All changed collections commit together, with one Undo. Cross-participant audio additionally requires one common integer sample displacement, not just local compiler equality.

Schema **38**, renderer/audio compilers and dependencies remain unchanged. [Workflow, exact boundaries, proof limits and official-editor comparison](docs/SOURCE_INSERT_PARTICIPATION.md). No persistent Sync Lock, linked A/V/source patching, native-package delivery or paid-editor parity is claimed. Acceptance requires the matching frozen-source focused/UI, independent review, aggregate and fresh-extraction receipts; no remote/CI actions are part of this round.

## Previous bounded round (historical scope)

# Current bounded round: named reusable source ranges

Save, recall, rename and delete exact named source selections in schema **38**. Reopened ranges are unverified until a fresh worker checks portable content/timing identity; exact current-grid mapping only, with no rounding, clamping or automatic rebind. Rename/Delete are single Undo steps; Recall changes source marks only and existing clips remain independent.

Saved ranges protect media removal while Used/Unused still means timeline usage. Long metadata selections do not bypass the existing Insert/Overwrite 30-second proof and compatibility/resource limits. [Workflow, safety contract and official-editor comparison](docs/SAVED_SOURCE_RANGES.md).

Candidate acceptance requires matching focused, independent review, aggregate and extraction receipts. No native package, remote publication or paid-editor parity is claimed.

## Previous bounded round (historical scope)

# Current bounded round: source marks → single-track Insert v1

The source viewer now offers explicit **Insert / Overwrite**. Insert preserves eligible existing source content, splits at an interior destination and shifts only the chosen track's downstream clips by the selection length. **Other tracks, captions and stickers remain fixed: this is not cross-track synchronization.**

Whole moved clips must retain their complete local audio compilation (including fades); only the final timeline delay may differ. Non-equivalent fractional-frame sample phases and unsupported clip relationships/settings reject the entire transaction. Schema37, legacy rendering/audio compilers and dependencies remain unchanged. [Workflow, compatibility and resource limits](docs/SOURCE_MARKS_INSERT.md) · [Current source-evidenced capability inventory](docs/CURRENT_EDITING_CAPABILITIES.zh-CN.md).

Local candidate only. Matching frozen-source focused/UI, independent review, aggregate and fresh-extraction receipts are required before acceptance. This round claims no remote/CI publication, Windows/macOS package or paid-editor parity.

## Previous bounded rounds (historical scope)

# Current bounded round: exact source-audio windows

Late AAC/MP3/FLAC and embedded-audio source windows use sparse session-only exact f32 preparation, without input seeking or changing schema37/rendering. The 30-second selected/proof limit remains. [Behavior, resource limits, measured cold/warm costs and paid-editor gaps](docs/EXACT_SOURCE_AUDIO_CACHE.md).

Local candidate only: focused tests are not final aggregate acceptance, and no remote/native-package delivery is claimed.

## Previous bounded rounds

# CutCat cumulative integration · schema35 · non-destructive media bins

Latest bounded round: four persistent track edit locks protect complete clip records, related groups/transitions and referenced source records atomically. Locked tracks remain in playback/export; this is not mute, sync-lock or final-output freeze. Schema 37. [Scope, history, limitations and paid-editor comparison](docs/TRACK_EDIT_LOCKS.zh-CN.md).

Organize project media into nested bins without moving or deleting source files. Search names/paths inside the selected scope, filter by type and timeline usage, move selected entries explicitly, and remove only unused project entries. Imports are staged off the GUI thread and accepted as one undoable batch. [Workflow, safety boundaries and comparison](docs/MEDIA_BINS.md).

Schema35 stores bin IDs and membership separately from media metadata. Older projects migrate to empty organization; older readers reject the new version. This is basic manual organization, not Smart Bins, semantic media search, proxies or collaborative locking. Native Windows/macOS packages and paid-editor parity are not claimed. Acceptance requires this round’s matching frozen source, full-suite, independent review, UI and fresh-extraction evidence.

## Previous bounded rounds (historical scope and receipts)

# CutCat cumulative integration · schema34 · accessible build help

Build support and limitations now use selectable, wrapped, scrollable text with a persistent keyboard-accessible Close action, bounded to the available screen without shrinking fonts. Existing safety warnings remain intact. [Scope and local verification](docs/HELP_ACCESSIBILITY.md).

No model, renderer, audio, dependency or schema changes. Native Windows/macOS packages, desktop scaling and assistive technology remain separate validation gates; this is not paid-editor parity.

## Previous bounded rounds

# CutCat cumulative integration · schema34 · measured loudness gain proposal

Optional current analysis → choose target/estimated-peak ceiling → measure detached master-gain draft → explicit Apply. Actual remeasurement, not predicted LUFS, determines eligibility; peak-limited and target-miss cases are shown. No project changes before Apply, one Undo after. [Workflow and limitations](docs/MEASURED_LOUDNESS_PROPOSAL.md).

This is a conservative user-confirmed gain proposal, not normalization, a limiter, compressor or encoded-file compliance. Targets are session-only; schema34 and the existing rendering path remain unchanged. Native packages and remote publication remain unverified.

## Previous bounded rounds

# CutCat cumulative integration · schema34 · manual master output gain

Latest bounded round: Timeline → Master output gain stages one project-wide −96 to +24 dB output adjustment after the stereo mix and before encoding. Apply is one undo step; Cancel is a no-op. Default 0 dB preserves the previous mix, and old projects migrate to 0 dB. Clip settings stay unchanged. [Bilingual workflow, scope and UI validation](docs/MASTER_OUTPUT_GAIN.md).

Apply gain → Build preview and listen → Analyze timeline audio again. The shared preview/export/analysis path includes master gain; old analysis results clear after a gain change. This is not automatic normalization, a limiter, compressor, master mute, live meter or clipping-prevention guarantee. Native Windows/macOS packages remain a separate verification gate.

## Previous bounded rounds (historical scope and receipts)

The analysis and true-peak entries below describe their original schema33 rounds. Their historical “no new project fields” and “unchanged export” statements do not describe this schema34 master-gain addition.

# CutCat cumulative integration · schema33 · pre-encoding final mix audio analysis

Latest bounded round: explicit cancellable analysis of the shared final stereo timeline mix before AAC encoding. Integrated loudness, per-channel sample/estimated true peaks (dBTP) and overload inspection support the existing manual gain-adjust/reanalyze workflow. Session-only, no automatic normalization or project changes. [Scope, validation and paid-editor comparison](docs/ESTIMATED_TRUE_PEAK.zh-CN.md).

Not live track/master metering, true-peak certification or paid-editor parity. Local plain mono/stereo sources only, bounded immutable snapshots and streaming PCM. Schema33 and previous export semantics remain unchanged; native Windows/macOS packages and remote publication are not claimed. Final acceptance requires the matching frozen-source receipts.

## Previous bounded rounds

# CutCat cumulative integration · schema33 · real-handle video cross dissolve

Latest bounded round: centered two-source Cross Dissolve on adjacent same-track 1x CFR clips. Actual decoded source handles are required; timeline length, original clip ranges and all audio remain unchanged. Separate transition markers and cancellable staged editing use one undo step. [Exact frame contract, supported settings and competitor comparison](docs/VIDEO_CROSS_DISSOLVE.zh-CN.md).

This is intentionally not general paid-editor transition parity. Active effects, stabilization, picture/window animation, transparent compositing, retiming and VFR endpoints are rejected without changing their settings. Grade/LUT/restoration and static opaque geometry remain supported. No new dependencies, remote publication or Windows/macOS package claim is included. The accepted local test receipt is required before calling this source verified.

## Previous bounded rounds

# CutCat cumulative integration · schema32 · exact-pixel animation speedup

Latest bounded round: frame-only motion-expression reuse in the existing RGB16/full-chroma compositor, with no geometry, alpha, color-range or precision downgrade. No new controls, project fields, dependencies or native-package claim. [Profiling, exact-byte regression and competitor comparison](docs/CLIP_MOTION_PERFORMANCE.zh-CN.md).

This remains offline composition, not interactive playback parity. Same-fixture repeated 1080p/4K measurements and verified local-source delivery status are described in that report. Windows/macOS packages remain unverified; no remote/CI/publication is included.

## Previous round

# CutCat cumulative integration · schema32

Latest bounded round: manual clip-local position, uniform scale and whole-picture opacity keyframes, with outgoing Hold/Linear interpolation and preserved hidden handles through left trims, splits and rolls. Static rotation, mirrors and transparent exterior remain independent; no-key clips keep their previous path. [Model, timeline semantics and compatibility](docs/CLIP_MOTION.zh-CN.md). Current Windows/macOS packages remain unverified.

This is correct **offline animation support**, not interactive paid-editor usability parity. Apply changes, then build the existing preview or export. A bounded synthetic three-frame timing on this host took about 22 seconds at 1080p and 86–87 seconds at 4K; no realtime claim, native package certification, remote publication or new dependency is included. Performance optimization remains a priority.

## Previous round

# CutCat cumulative integration · schema31

Latest bounded round: opt-in static picture-in-picture outside the transformed fitted canvas, plus constant clip opacity. Real black and existing fit/pad black bars remain picture content; audio is unchanged. Old defaults keep the exact opaque legacy path. [Workflow, pixel contract and official competitor comparison](docs/CLIP_COMPOSITE.zh-CN.md). Current Windows/macOS packages remain unverified.

## Previous round

# CutCat cumulative integration · schema30

Latest bounded round: Shift-click timeline clip multi-selection, atomic same-delta movement and non-ripple batch deletion. Single-target tools are disabled for multi-selection; source waveforms and schema30 remain unchanged. [Workflow, safety semantics and paid-editor gap comparison](docs/CLIP_MULTI_SELECTION.zh-CN.md). Native Windows/macOS packages remain unverified.

Current source includes the visual window-keyframe time editor, qualifier/window animation, experimental Apple Log 1 input and bounded high-bit SDR composition. Current native Windows/macOS packages remain unverified; historical package results do not cover this tree. Preserve project/media copies, because schema30 cannot be opened by the old schema9 build.

[Current native status and first-start prerequisites](docs/NATIVE_TEST_QUICKSTART.zh-CN.md) · [Native acceptance gate](docs/NATIVE_VALIDATION_GATE.md) · [Window keyframe editor](docs/WINDOW_KEYFRAME_EDITOR.md). The sections below are historical implementation notes, not current delivery claims.

## Historical implementation notes

# Explicit Apple Log 1 input · experimental

A separate explicit Apple Log 1 / BT.2020 import path now uses strict verified RGB16 / tagged10-bit YUV decoding, the pinned original Apple Log inverse, linear gamut conversion and a fixed CutCat SDR shoulder/gamut policy. It is synthetic-validated only: no arbitrary iPhone compatibility, Apple Log 2, Apple LUT/Resolve matching, HDR mastering or active stabilization claim. Existing SDR defaults remain unchanged. Schema29 persists the input interpretation separately from copied creative grades.

[Supported formats, workflow, competitor comparison and limits](docs/APPLE_LOG_INPUT.md) · [Equations and license provenance](docs/APPLE_LOG_MATH.md). Native Windows/macOS builds and remote main have not been verified in this local source round. No upload is part of this checkpoint.

## Historical implementation notes (state at that iteration)

# Automatic paused color feedback

The modeless current-frame grade preview now offers an opt-in “Automatically refresh while paused” checkbox. Changes are debounced for 300 ms after detection (200 ms polling), with at most one running capture and one latest pending request. Manual refresh remains the default. Cancel suppresses automatic retry until the editing context changes or the user explicitly refreshes/toggles the mode.

It reuses the same full-canvas RGB16 → float32 grade capture, before effects and compositing. This is selected-clip still feedback, not live playback, final timeline compositing, HDR/RAW/Apple Log support or GPU acceleration. [Workflow and safety semantics](docs/AUTO_FRAME_GRADE_PREVIEW.md). Schema28 unchanged. Native Windows/macOS packages and remote main are not verified by this local source round.

## Earlier implementation notes

# Bounded vignette radius and feather

The existing vignette now has radius and feather controls (0.25–2.00, defaults 1.00), saved in schema28. Exact default branches preserve prior H.264 and RGB16 output. Nondefault shape uses explicitly defined radial RGB8 processing for preview/H.264 and native RGB16 for ProRes. This changes the H.264 color-processing path, so even small departures from default can alter color as well as shape.

CPU expressions can slow rendering; this is not realtime GPU, HDR, tracking, arbitrary windows or competitor algorithm parity. [Formula, migration, workflow, limitations and official competitor comparison](docs/VIGNETTE_CONTROLS.zh-CN.md). No new dependencies, native package or remote-main claim.

## Historical implementation notes (state at that iteration)

# Native RGB16 radial vignette for high-bit SDR export

The existing vignette now works in ProRes 422 HQ 10-bit SDR using capability-checked RGB16 radial multiplication, not an 8-bit detour. Zero/disabled bypass and effect order are preserved; blur, basic captions and verified static PNG stickers remain supported. Unknown effects and animated packs remain outside the verified profile. Preview/H.264 remain legacy and are not pixel-identical to this high-bit path.

This is encoded SDR code-value math, not HDR/ICC/linear light or realtime GPU finishing. The generic expression is measurably slower than legacy vignette. [Formula, geometry, performance, limitations and competitor comparison](docs/HIGH_BIT_VIGNETTE.zh-CN.md). Schema27 unchanged; no native package or remote-main claim.

## Historical implementation notes (state at that iteration)

# High-bit Gaussian blur without an 8-bit detour

ProRes 422 HQ 10-bit SDR now accepts the existing Gaussian blur through capability-checked native RGB16 processing. Sigma remains 0–30 project pixels; zero/disabled effects are exact bypasses. The current effect catalog contains blur and vignette only; active vignette remains blocked, as do animated sticker packs. Preview and H.264 retain their legacy path.

This is encoded Rec.709 code-value blur, not linear-light/HDR processing, an effect-keyframe system or Resolve parity. Runtime precision/behavior checks and no-auto-conversion rendering fail closed. [Exact stages, supported combinations, competitor comparison and validation](docs/HIGH_BIT_EFFECTS.zh-CN.md). Schema27 unchanged. Native package and remote-main status require separate verified evidence.

## Earlier implementation notes

# High-bit static PNG sticker composition

Verified static PNG stickers now work with ProRes 422 HQ 10-bit SDR export without reducing the background to 8-bit. Existing position, scale, opacity and transform keyframes remain available; basic high-bit captions can be used together. Runtime pixel probes fail closed on incompatible FFmpeg builds. Animation packs and active vignette remain outside this profile; Gaussian blur support is described above.

Artwork is still the existing Qt 8-bit RGBA layer with legacy gamma-coded SDR numeric interpretation, not linear-light, sRGB transfer conversion, ICC or HDR color management. [Precise scope, edge handling, competitor comparison and validation](docs/HIGH_BIT_STICKERS.zh-CN.md). Schema27 unchanged. This is cumulative source/offscreen validation, not a native Windows/macOS release or remote-main update.

## Earlier implementation notes

# Selected clip current frame grade review

Review the current selected-clip frame after all stored color corrections without rebuilding the complete timeline. Explicit refresh, full-resolution RGB16/float32 processing and stale-result guards; this image is before effects, transforms and compositing, not final output. [Workflow, timing, stages and limits](docs/FRAME_GRADE_PREVIEW.md). No schema change, native package or remote-main claim.

## Earlier implementation notes

# Full-resolution color processing, less waiting

Selective hue curves now use lower-allocation CPU evaluation while preserving the existing float32 math and full-resolution render path. No GPU/realtime claim, UI change or schema migration. [Measured scope, exact arithmetic, reproduction and competitor gap](docs/COLOR_PREVIEW_PERFORMANCE.md). Schema27 white balance, corrected YUV preview and queued-seek guards are retained.

## Earlier implementation notes

# Neutral-reference SDR white balance

Pick a known neutral gray/white object from the normalized/restored source before creative grading. Bounded linear Rec.709 RGB gains neutralize that reference while preserving its luminance, not every pixel’s brightness. Artistic warm/tint remain separate. Schema27, disabled identity for old projects. [Math, workflow, compatibility, competitor comparison and limits](docs/WHITE_BALANCE.md). The prior corrected Qt YUV preview and queued-seek guards are retained. Source/offscreen validation only; no native package or remote-main claim.

## Earlier implementation notes

# Session-only cross-clip reference comparison

Capture one immutable decoded preview still and compare it with another shot using side-by-side or wipe. Viewer-only, no automatic matching, no grade changes or project schema change. [Workflow, provenance, display limitations and competitor comparison](docs/COLOR_REFERENCE_COMPARISON.md). Final verification results are in the matching acceptance report.

## Earlier implementation notes

# Adjustable primary contrast pivot

Basic color now pairs contrast with an explicitly encoded Rec.709 RGB pivot (0–1, default 0.5). Default arithmetic is bit-exact with the prior pipeline; contrast 1 bypasses pivot arithmetic. Schema26 preserves old projects at 0.5 and rejects silent downgrade. [Exact math, compatibility, workflow comparison and remaining gaps](docs/PRIMARY_CONTRAST_PIVOT.md). Source/offscreen verification only, not native packages or remote-main publication.

## Earlier implementation notes

# Independent qualifier channel switches

Each base/add/exclude range now independently enables H, HSV-S and Y′ conditions while retaining stored core and feather values. Disabled means condition bypass, not muted range. All-off exclusion clears the key and prompts before applying active correction. Schema25 preserves legacy all-on output. [Exact semantics, sampling and limits](docs/QUALIFIER_CHANNEL_SWITCHES.md). Source/offscreen validation only, not native packages or remote-main publication.

## Earlier implementation notes

# Independent low/high qualifier feathers

HSV-S and Y′ now expose separate low/high exterior feather controls for every selected base/add/exclude range. Source-pixel softness sampling changes only the sampled outside edge; Hue stays circular and symmetric. Schema24 preserves old symmetric values via explicit inheritance and prevents silent downgrade. [Exact compatibility and math](docs/QUALIFIER_ASYMMETRIC_FEATHERS.md). Frozen validation counts are recorded in the matching acceptance report; no native builds or remote-main update are claimed.

## Earlier implementation notes

# Selected-range source-pixel softness (local source)

Select an existing base/include/exclude range and explicit H, HSV-S or Y′ channel, choose Soften, then pick frozen pre-qualifier float source. Only that feather extends; hard cores and unrelated settings remain unchanged. The sampled edge reaches at least 50% channel weight, not necessarily combined matte. Impossible bounds reject; gray hue rejects while S/Y measurement remains available. No-op picks do not add project history; Apply is one undo transaction. [Exact math and limitations](docs/QUALIFIER_SOFTNESS_SAMPLING.md). Schema23 and rendering are unchanged. No native builds or remote-main update are claimed.

## Earlier source milestones

# Current milestone: additive/subtractive qualifier ranges

Professional → Color → Hue / saturation / luma qualifier now provides explicit Replace, Add and Subtract source-sample modes, a separately editable range list, removal and clear-extras controls. Independent ranges do not silently expand disjoint colors into one interval. One primary range plus up to eight additions and eight exclusions compose by maximum-union then inverse-exclusion multiplication, before the existing single static window. All clicks sample frozen pre-qualifier float32 source, never display tint. Only Apply commits one undo transaction.

Schema20 persists the bounded sets; schema18/19 migrate with empty extras, preserving their exact old output. [Exact math, limits and compatibility](docs/QUALIFIER_SETS.md). This is Linux source/offscreen verification, not a new Windows/macOS package or remote-main update. Current final counts are in the matching acceptance report; older entries below record historical milestones.

## Historical milestones (superseded where noted above)

# Static spatial color window (local source)

One ellipse or rectangle now constrains the H/S/Y color key by fitted-canvas position, size and exterior feather, with optional window-only inversion. The same combined float key drives matte/overlay inspection and render corrections; Apply remains one undoable transaction. Schema19 protects the new geometry from silent downgrade. [Exact coordinate, arithmetic, comparison and limits](docs/STATIC_COLOR_WINDOW.md). No tracking, keyframes, native package or remote-main update is claimed.

# Click-to-replace qualifier sampling

The joint qualifier now initializes all three ranges from one deliberate source click, then shows the pending matte. It preserves feather, adjustments and enable state, reads exact pre-qualifier float32 grid samples, and commits only on Apply. This is replacement, not additive sampling. [Coordinates, tolerances, competitor comparison and limits](docs/QUALIFIER_PICKER.md). Local source only; no new native packages or remote-main publication.

# Joint color-range qualification

Professional → Color → Hue / saturation / luma qualifier opens a real RGB16-derived static range matte. One feathered hue/HSV-saturation/encoded-Y′ selection drives bounded hue, luma-relative saturation, and code-value gain adjustments in the shared float pipeline. Schema18 persists it; old projects migrate disabled. See [exact channels, arithmetic and limits](docs/QUALIFIER.md). Offline source verification only: no new native Windows/macOS build or remote-main publication is claimed.

## Selective hue curves (local source milestone, 2026-10-22)

## Static source hue picker (2026-10-22, local source)

Pick from a dedicated selected-clip RGB16/float32 frame before selective curves and LUT, not the composited 8-bit viewer. Insert circular, line-preserving anchors in one undo, then edit the selected point. Gray/padding/out-of-range and stale source/project samples are rejected. Spatial restoration now runs on normalized float32 active-picture pixels before primary grading, with an explicit applied/bypassed label. Active stabilization, changing restoration geometry and transparent sources remain unsupported for picking. Actual non-neutral curve spans are shown, not an HSL/spatial mask. [Stage, usage, limits and competitor comparison](docs/COLOR_PICKER.md). No new native installers or remote-main publication is claimed.

Professional → Curves now includes Hue vs Hue and Hue vs Saturation with periodic red endpoints, exact numeric point edits, keyboard controls and one-step undo. Both address the same original post-RGB-curve hue in the shared float32 SDR pipeline before LUT; default curves preserve old output exactly. Project schema17 rejects silent downgrade. These curves are separate from the new joint qualifier; neither is a spatial mask, HDR workflow or Resolve-equivalent algorithm. See [precise contract](docs/SELECTIVE_COLOR.md) and [comparison, limits and next bounded improvement](docs/SELECTIVE_COLOR_COMPARISON.zh-CN.md). Existing 8-bit preview and restricted ProRes10-bit delivery boundaries remain. No new Windows/macOS package or remote-main publication is claimed.

## 色彩基础完善（2026-10-22，本地源码）

新增 RGB Parade、Rec.709 矢量示波器、保留目标输入解释的调色复制/粘贴，以及明确区分的 H.264 8 位与 ProRes 422 HQ 10 位 SDR 导出。高位深路径保留 RGB16 调色结果并使用 10 位轨道合成，不是把旧 8 位结果重新封装。该档位支持现有基础字幕和已验证的静态 PNG 贴纸，分别要求实际通过 10 位 drawtext／overlay 能力检查的 FFmpeg；不兼容时明确拒绝，不将背景视频悄悄降为 8 位。动画贴纸包和生效中的暗角／未经验证特效仍不支持；高斯模糊可经原生 RGB16 能力检查后使用。贴纸图像仍由 Qt 生成 8 位 RGBA 图层，保留旧版伽马编码 SDR 数值外观，不新增线性光混合、sRGB 至 Rec.709 传递转换或 ICC/HDR 管理。预览仍是 8 位代理，不代表 10 位监看或显示器校色。详见[高位深贴纸与竞品边界](docs/HIGH_BIT_STICKERS.zh-CN.md)及[高位深字幕说明](docs/HIGH_BIT_CAPTIONS.zh-CN.md)。详见[色彩流程、验证与限制](docs/COLOR_FOUNDATION.zh-CN.md)。本轮不含新原生安装包或远程 main 更新声明。

## 基础剪辑补齐（2026-10-22，源码里程碑）

新增明确限定当前轨的波纹删除，以及可撤销/保存的主视频静态位置、缩放、旋转和镜像。普通删除保留空隙；变换仍是不透明黑画布，不是透明画中画或视频关键帧。新保存格式为 schema16。详见[基础功能差距、使用说明与交付限制](docs/BASIC_EDITING_COMPARISON.zh-CN.md)。这不是新版 Windows/macOS 安装包，也不代表已更新远程 main。

## Experimental controlled edits (local source, 2026-10-21)

The AI panel now previews and confirms exact subtitle-text and selected-clip gain
changes as one undoable batch. This deterministic offline form is not an autonomous
agent. Existing local proofreading uses the same strict transactional gate. A
separate optional OpenAI Agents SDK 0.24.0 adapter is tested with a local fake model
and remote tracing disabled, not a live API. See [scope and limits](docs/CONTROLLED_ASSISTANT.md).
A new [three-step API settings dialog](docs/API_SETTINGS.md) prepares configuration
in memory only, with local format checks and an honestly disabled connection test.
No new native packages or remote publication are included in this source milestone.

## Offline sound-effects library (local source, 2026-10-21)

The independent **音效库 / Sounds** tab includes **77 offline sounds: 24 original procedural effects plus 53 third-party CC0 effects**. Bilingual category/search, author/source/license details, cancellable auditions, exact-frame/track insertion, atomic undo/redo and safe local import are included. Saved projects retain immutable provenance in the `.lumen` file and carry inserted WAVs in a neighboring `.assets` folder. No online catalog or paid API is required. [Usage, provenance, portability and verification limits](docs/SOUND_LIBRARY.md). This source milestone is not in the older Windows/macOS test packages; native playback/build validation remains separate.

## Natural Mandarin ASR diagnostic (local source only, 2026-10-17)

Three official Apache-2.0 AISHELL-3 human recordings were compared with base/small and auto/zh. Raw outputs, spelling-normalized supplementary scores and CPU runtimes are retained; this tiny test is not general accuracy certification. New local comparison tooling has135 targeted tests passing; production recognition behavior was not changed. See [results and reproducibility limits](docs/NATURAL_MANDARIN_ASR_VALIDATION.md).

# CutCat — independent desktop prototype

**CutCat** is the English display name of this independent native desktop editor project; its Chinese name is **剪辑猫**. Display branding follows the actual interface language. The interface supports Simplified Chinese and English with a persistent live language switch. Fresh settings default to Chinese; the actual chosen UI language, not the OS language, determines the displayed name. The repository remains `LumenEdit`; internal `lumen` identifiers, `.lumen` projects, settings locations and `LumenEdit-Draft` executable names stay unchanged for compatibility. It does not modify or replace StableFlowPro. It is an early engineering slice, not a finished professional editor, and makes no claim of superiority to another editor.

The Chinese interface uses the separate `剪辑猫` name. Its font helper uses locally available Chinese fonts and falls back to `CutCat` with an explanatory tooltip if glyphs are missing. Fonts are never bundled or downloaded automatically.

## Local component setup (source-only milestone)

Settings and the Local AI panel now open a bilingual local-component setup dialog for ASR, TTS, semantic search and subtitle assistance. It saves explicit local paths and provides cancellable metadata/optional hash diagnostics without launching models or downloading anything. This change is Linux-source tested only; it is not included in the previously delivered Windows/macOS packages. See [setup guide and limitations](docs/LOCAL_COMPONENT_SETUP.md). Isolated beauty prototypes remain experimental.

## Local ASR integration revalidation (2026-10-17, source only)

Real official whisper.cpp CPU inference now has end-to-end setup, editable caption,
undo/save/SRT, decoded preview and export evidence under a Linux development
network-denial harness. Runtime subprocesses use a private working directory and
scrub backend override variables. The small model matched one synthetic English
fixture; Mandarin recognition remained substantially inaccurate and requires
review. No model downloads happen in the app. This does not update the previously
delivered native packages. See [exact results, pins and limits](docs/LOCAL_ASR_SETUP_VALIDATION.md).

## Safe caption wrapping (local source only)

Long captions now wrap within configurable safe margins in the shared preview/export graph. Original editable text, manual line breaks and SRT contents are preserved; overflow produces a clear error instead of truncation. New projects save as schema version 10, with older project migration. This Linux-source milestone is not yet in native packages. See [layout behavior and validation limits](docs/CAPTION_LAYOUT.md).

## Local voice workflow revalidation (source only)

Actual Kokoro v1.1 auditions now have reproducible network-denied Linux acceptance for four documented voice IDs in English/Chinese, saved full takes, cancellation, undo/save/relocation and decoded export. This validates existing optional functionality; no runtime/model binaries are bundled and native packages are unchanged. See [evidence scope and privacy limits](docs/LOCAL_TTS_SETUP_VALIDATION.md).

## Consecutive subtitle takes (local source milestone)

If a saved narration take overlaps an earlier clip, you can explicitly trim/move that blocker and retry the same full WAV without regenerating it. Reuse verifies the original caption, frame rate, edit session and audio hash; stale assumptions still reject. English/Chinese guidance and extended local voice/long-text acceptance are documented in [consecutive takes](docs/CONSECUTIVE_TTS_TAKES.md).

## One-click Simple / Professional

A fresh app starts in Simple. The top-bar buttons switch instantly to Professional without changing the project, render output, undo history or active jobs. Simple keeps core editing, basic color, captions, local voices/AI and sticker placement; Professional exposes implemented wheels/curves/LUTs, scopes, effects and sticker keyframes. Hidden active settings are explicitly reported and preserved. See [workspace modes](docs/workspace-modes.md).

Input color now has explicit Rec.709, sRGB and linear Rec.709 assignments, separate from creative correction. Unsupported source gamut/HDR and known tag conflicts are rejected. This is an SDR transfer foundation, not automatic camera Log conversion, OCIO/HDR or calibrated display management.

## UI alignment

Current packages include live Simplified Chinese/English UI (剪辑猫 / CutCat) and local 3D .cube strength controls. Windows/macOS native build and re-extracted runtime validation passed at `2b7db2d`. See [package instructions](docs/NATIVE_TEST_QUICKSTART.zh-CN.md), [native validation](docs/BILINGUAL_LUT_NATIVE_VALIDATION.md), [localization](docs/LOCALIZATION.md) and [LUT workflow](docs/LUT_WORKFLOW.md) for exact scope and limits.

Shared point-sized typography, aligned form labels/inputs, consistent controls and visible disclosure arrows now cover both workspaces. The viewer/timeline splitter adapts to smaller windows without overlapping playback/progress controls. Long labels wrap and long combo choices remain available in tooltips. See [UI acceptance](docs/UI_ALIGNMENT.md) for the real-Qt geometry/screenshot matrix and native validation boundaries.

## Configurable PR-reference shortcuts

Settings → Keyboard shortcuts… provides English/Chinese search, independent Windows/macOS profiles, local persistence, reset and conflict checks. Only implemented commands are included: Ctrl/Cmd+K splits, S toggles real snapping, and text/numeric/IME/dialog input keeps its own keys. See [shortcut behavior and native-validation limits](docs/KEYBOARD_SHORTCUTS.md).

## Customer-local processing

All current media processing is local. There are no cloud inference, media upload or telemetry APIs. FFmpeg source access is restricted to local file/pipe protocols. Dependencies must be installed first; actual editing/rendering does not need a network connection. Speech-to-text now has a tested local whisper.cpp adapter with explicit runtime/model selection and no downloader. Subtitle reading now has an experimental local Kokoro/sherpa-onnx adapter with actual English/Chinese synthesis, editable audio clips and immutable provenance. Optional runtime/model setup is explicit; see docs/LOCAL_TTS.md for component licensing limits.

## Architecture and scope — dual workspace / SDR input milestone

- Python 3.12 and PySide6 6.8.3 provide the native Qt desktop UI.
- External FFmpeg and ffprobe inspect media and render the timeline. The supported FFmpeg build must include zscale, FFV1, libx264, AAC and drawtext with local font support.
- An owned NumPy float32 SDR color evaluator implements linear-light exposure, RGB lift/gamma/gain, master/RGB curves, creative controls and 3D LUTs. Actual decoder input and intermediate transport are 16-bit RGB; delivery remains 8-bit H.264/AAC.
- Preview-cache generation and final export use the same timeline render compiler. Preview must finish rendering before cached playback; this is not a real-time GPU effects engine.
- The first slice is SDR Rec.709 only. Unsupported declared color spaces/HDR require conversion outside the app. Untagged media is interpreted as Rec.709, which may be wrong for some sources. Monitor calibration and display color management are not supplied.
- There is no implemented C++ engine, GPU rendering pipeline, OpenColorIO integration, HDR mastering, calibrated display management or end-to-end float codec pipeline.

The intended first-slice workflow includes bin import; four integer-frame tracks (two video tracks with embedded audio and two audio tracks); clip move, trim, split and delete; undo/redo; linear exposure, lift/gamma/gain wheels, master/RGB curves, legacy code gain, contrast, saturation, temperature/tint, shadows/highlights and LUT controls; before/after viewer snapshots; gain, mute and fades; rendered timeline preview and scopes; JSON project save/load with relative media paths and relinking; and export progress/cancellation with source-file protection. This workflow is implemented and exercised by the recorded local automated tests; see [recorded validation](docs/VALIDATION.md) for exact evidence and limitations. It is still a first-slice prototype, not full production readiness.

[中文快速上手](docs/START_HERE.zh-CN.md) · [Validation record](docs/VALIDATION.md) · [Full feature roadmap](docs/ROADMAP.md)

## Run from source

Use Python **3.12**, then create an isolated environment:

```sh
python -m venv .venv
# macOS
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[test,build]"
python -m lumen
```

Install FFmpeg and ffprobe separately from a trusted package source:

```sh
# macOS with an existing Homebrew installation
brew install ffmpeg@7
export LUMEN_FFMPEG="$(brew --prefix ffmpeg@7)/bin/ffmpeg"
export LUMEN_FFPROBE="$(brew --prefix ffmpeg@7)/bin/ffprobe"
# Windows with an existing Windows Package Manager installation
winget install --id Gyan.FFmpeg --exact --source winget
```

Review package prompts and applicable terms. The project does not download or bundle FFmpeg automatically. FFmpeg publishes source; Windows binaries are supplied by third parties linked from its [official download page](https://ffmpeg.org/download.html). The package definitions are available from [Homebrew](https://formulae.brew.sh/formula/ffmpeg@7) and [Microsoft's winget repository](https://github.com/microsoft/winget-pkgs/tree/master/manifests/g/Gyan/FFmpeg).

The app discovers executables on PATH or through the explicit LUMEN_FFMPEG and LUMEN_FFPROBE environment variables. Set each override to the full path of its executable, not a directory. Verify both executables:

```sh
ffmpeg -version
ffprobe -version
```

A Finder-launched macOS application may not inherit your shell PATH. Set up a launch environment containing the required paths, or launch the app executable from a configured terminal. A distributable installation flow is still pending.

## Basic workflow and acceptance checks

1. Import short SDR Rec.709 video and audio fixtures; confirm metadata and unsupported-input errors.
2. Add clips to the timeline. Check video stacking, embedded-audio mixing, audio-only tracks and gaps.
3. Move, trim, split and delete clips; undo and redo each operation. Check frame boundaries.
4. Apply grade and audio adjustments, render the preview cache, and compare the cached output with an export of the same range. Changes must invalidate stale preview results.
5. Save a project, close it, reopen it, and test relinking after moving source media. Keep a backup; the prototype project format may change.
6. Export to a new destination. Verify progress, cancellation, video/audio duration and source-file protection. Never test destructive cases against irreplaceable media.

Use small copied fixtures first. Long timelines can consume substantial render time and disk space. Codec availability depends on your FFmpeg build. Scopes describe the preview image; they are not a substitute for a calibrated monitoring path or certified measurement.

## Tests and native draft builds

```sh
python -m pytest
python scripts/build_app.py
# Retain a diagnostic console:
python scripts/build_app.py --console
```

Build on the target OS; [PyInstaller is not a cross-compiler](https://pyinstaller.org/en/v6.12.0/). The build script checks pinned dependency versions and creates an onedir draft in dist/. Windows and macOS must be built and validated separately. The macOS architecture follows the build machine; an arm64 build is not automatically universal or Intel-compatible. No installers, release signing, notarization or automatic updates are included. PyInstaller may apply its default local ad-hoc macOS signatures; these do not establish a trusted developer identity. Do not bypass operating-system security warnings.

The CI definition targets Windows 2022 x64 and macOS 15 ARM runners. Its explicit developer provisioning step verifies a pinned Gyan Windows ZIP or a compatible Homebrew ffmpeg@7 bottle, then checks required filters/encoders with actual media before tests. FFmpeg remains external to the application package. Homebrew transitive dependencies are recorded but not fully locked. There is no in-app downloader. Native CI succeeded on 2026-10-15: Windows x64 983 passed / 7 POSIX-only skips; macOS 15 arm64 989 passed / 1 Windows-only skip. Both re-extracted frozen packages passed real media/Qt decoding smoke, including Chinese captions with an external verified test font. See [native package quickstart and limits](docs/NATIVE_TEST_QUICKSTART.zh-CN.md) and [the verified run](https://github.com/liangtovi-debug/LumenEdit/actions/runs/38774145073).

Packages are archived before their smoke test; macOS uses tar.gz to retain executable bits and framework symlinks. CI tests the re-extracted package and retains failure diagnostics. Build success does not establish physical playback, clean-machine setup, accessibility, minimum-OS compatibility, or accurate monitor color. Intel macOS and Windows ARM are not covered by this initial matrix. Generated demonstrations in artifacts/ are delivered separately and excluded from the source-only repository upload.

### Release gates

Do not distribute a public release until native UI/playback tests, clean-machine installation tests, security/dependency review, codec licensing review, third-party notices, project ownership/license decision, approved branding, and platform signing/notarization have been completed. See [licensing](docs/LICENSING.md) and [roadmap](docs/ROADMAP.md).

## Reproducible demo and headless tools

```sh
python scripts/create_demo.py my-new-demo --render
python -m lumen.cli validate my-new-demo/first-cut.lumen
python -m lumen.cli render my-new-demo/first-cut.lumen my-second-export.mp4
python scripts/verify_long_reference.py
```

The demo uses generated test patterns and tones, not downloaded footage. Existing demo folders and output files are protected. A ready-made demo is included in `artifacts/demo-verified/`. The long reference is a 30-minute, low-resolution synthetic synchronization check; it is not a performance benchmark or production-media certification.

## Color milestone

The correction stack is visible and defined in [COLOR_PIPELINE.md](docs/COLOR_PIPELINE.md). Wheels/curve gestures commit one undo operation, and the Before viewer mode bypasses corrections in a snapshot without editing the project. Export always uses stored corrections.

Generate a real high-bit-depth chart demonstration:

```sh
python scripts/create_color_demo.py my-new-color-demo --render
```

Open `artifacts/color-milestone/float-color.lumen`; `graded.mp4` and `before.mp4` are actual renders from its 16-bit generated source. Version2 projects retain all parameters; version1 loads with neutral new fields. The newly defined float creative controls can differ from old FFmpeg grades, so older looks may need adjustment.

## Editable subtitles and local speech-to-text

The Captions tab edits text, language and integer-frame timing on an independent subtitle track. It imports/exports UTF-8 SRT and supports undo. Frame quantization is nearest/half-up; actual overlaps or zero-frame cues are reported instead of silently rewriting them. Video edits do not automatically move subtitles.

Caption burn-in uses the same preview/export graph and exact frame gates. Select an installed font with glyph coverage: the tested Linux bilingual demo uses Noto Sans CJK SC. Windows/macOS users may need a locally installed font such as Microsoft YaHei or PingFang SC; these native choices have not yet been tested. Fonts and models are not bundled. Long lines currently require explicit line breaks.

For speech recognition, select an existing trusted whisper.cpp executable and multilingual model file in the Captions tab. The selected source clip's trim and position are applied to generated captions. Existing overlapping captions require explicit replacement approval, and recognition is one undoable operation. See [LOCAL_ASR.md](docs/LOCAL_ASR.md) for exact runtime/model provenance, setup, timing, errors and real inference results. Chinese recognition made a documented error: always review text and timing.

Ready-made caption project/export: `artifacts/subtitle-milestone/`. [Full local-only product requirements](docs/FULL_OFFLINE_REQUIREMENTS.md) are a backlog, not a claim that every feature is implemented.

## Local subtitle narration (experimental)

Install the explicit optional runtime with `python -m pip install -e ".[tts]"`, then select and verify an existing complete Kokoro multilingual v1.0 or v1.1 pack in Captions. The installed version determines the actual voice catalog; filter by origin/text language, save favorites and audition before insertion. Review the subtitle text, choose a verified voice/speed/audio lane, and save a NEW WAV. Longer speech requires explicit acceptance; overlaps prevent insertion. No generated speech is silently truncated. Source text and generation hashes are preserved in project schema v6 (with v1–v5 migration). See `docs/LOCAL_TTS.md`, `docs/GENERATED_SPEECH.md`, and actual `artifacts/tts-demo/`.

The default draft build excludes the optional TTS runtime pending component-license and native-packaging review. No model/runtime binary is included in this source delivery.

## Offline effects and stickers

Effects & stickers now provides original local static/animated PNG assets, safe explicit PNG/JSON-pack import, independent timed layers, position/scale/opacity linear keyframes, and ordered Gaussian blur/vignette with bypass and undo. Rendering is shared with preview/export, after clip color/effects and before captions. See `docs/STICKER_ASSETS.md` and `docs/OVERLAY_RENDER.md` for limits and exact semantics. This is an 8-bit SDR code-value compositor; it is not a linear-light/HDR or real-time GPU compositor.

Actual combined project and export: `artifacts/effects-demo/`. The procedural original scene/stickers require no third-party art pack. Their provenance is included; an owner-selected distribution license remains pending. Native Windows/macOS builds remain unverified.

## Local subtitle AI and voice browsing

The Local AI tab registers explicit local files, stages Qwen proofreading as a reviewed diff, and builds semantic search over actual saved subtitles with precise frame jumps. One shared heavy AI task runs at a time. Source setup and boundaries: [Local AI models](docs/LOCAL_AI_MODELS.md).

Captions now supports versioned Kokoro v1.0/v1.1 selection, verified installed voices only, source-origin/text-language filters, favorites and actual temporary auditions. Models/native runtimes remain unbundled; native Windows/macOS validation and component licensing are still pending.

## Exact non-ripple source slip (local source, 2026-10-20)

The Editing inspector now has a separate absolute source-in slip control. It shifts the selected source interval without changing timeline start, duration, speed, fades or its partial final source interval. Attached source audio follows the selection; timeline captions, stickers and separate audio clips do not. Undo/redo and bounds rejection are transactional. See [usage and validation limits](docs/EXACT_SOURCE_SLIP.md). No new native Windows/macOS package is included in this local milestone.

## Adjacent cut roll (local source, 2026-10-20)

The Editing inspector can now roll the shared boundary of two immediately adjacent clips on the same track. Their combined timeline span stays fixed, each side retains its exact speed/partial tail, and source/fade conflicts reject atomically. Separate captions, stickers and audio do not follow. Undo/redo, saved projects, real preview/export and decoded PCM are covered by local acceptance. See [usage, contract and limits](docs/ROLL_EDIT.md). This adds no new native Windows/macOS package.

## Fractional-rate source tails

New imports can retain the final decodable frame of fractional-rate media using bounded decoded-tail evidence rather than truncating rounded container duration. Project format 15 retains this optional evidence; legacy clips are not resized. This does not certify whole-file CFR or fix arbitrary VFR/shifted mixed-A/V duration metadata. See [scope and validation](docs/FRACTIONAL_FRAME_TAIL.zh-CN.md).

## Local project recovery

Unsaved content is checkpointed locally every 30 seconds, without copying media or writing API settings. File → Recover unsaved projects lists inactive sessions; restoring opens an unsaved copy and requires an explicit save destination. Closing the manager retains snapshots. Successful save or accepted abandonment of the current session clears its snapshots. This is not a backup of source media, and edits since the last checkpoint can still be lost. See [Chinese recovery guide](docs/PROJECT_RECOVERY.zh-CN.md).

### Qualifier detail sampling

The joint qualifier now offers exact local 512/256/128/64-pixel detail crops, scroll zoom, middle-drag pan and X/Y positioning. It reuses the immutable pre-qualifier RGB16-to-float32 snapshot, not a proxy or enlarged sparse grid. Resolution labels distinguish the sparse overview from full-resolution local matte evaluation. This means fitted project-canvas pixels, not pre-resize camera-native pixels. See [workflow and limits](docs/QUALIFIER_PICKER.md). Linux-source/offscreen validation only; no new native installers or remote main update is claimed.


## Metadata-aware preview conversion (2026-10-23)

Supported mapped YUV preview frames now use explicit matrix/range conversion instead of Qt 6.8.3 CPU fixed-BT.601 conversion. Unsupported or unknown YUV color metadata fails visibly, clears current scopes and disables capture rather than showing guessed colors. Reference pixels remain immutable and export/schema26 are unchanged. Already-RGB backend frames are passed through, not independently color-verified. See [conversion, evidence and limitations](docs/PREVIEW_COLOR.md). This change is not ICC/HDR monitoring or Windows/macOS native validation.

## Manual animated color windows (local source)

The color panel now supports explicitly keyed center and size for one qualifier
window, with hold/linear interpolation on the clip-relative output timeline.
Slow-motion duplicate source pictures still evaluate their own window geometry.
Split/left-trim/roll preserve hidden interpolation keys; retiming and copy/paste
retain local key timing without duration scaling. See [workflow and exact
semantics](docs/WINDOW_ANIMATION.md). Schema 30; no automatic tracking, new native
Windows/macOS builds, GPU/realtime claim, HDR output, or remote main update.

### 时间线直接修边（本地源码轮）

支持现有视频／音频轨道单片段左右边缘拖动，以工程整帧修剪，另一边和其他对象不动；候选轮廓只预览，松开一次提交／撤销。可延长到可用源素材范围，拒绝邻片段冲突和会缩短淡入淡出的候选。保留变速源尾段与隐藏颜色窗口关键帧。不是波纹或多轨联动编辑。详见 [直接修边的操作、验证与边界](docs/TIMELINE_EDGE_TRIMMING.md)。本轮未更新 Windows／macOS 安装包。

## 有界画面动画合成线程

普通画面动画支持经过精确像素验证的最多 4 个滤镜线程，复杂组合和静态路径保留标量调度；不是实时播放或 GPU 加速。见 [性能范围、复现与验证](docs/CLIP_MOTION_THREADS.zh-CN.md)。

## 持久片段编组（本地源码）

时间线片段可保存编组关系，按成员选中整组、同位移水平移动及非波纹整组删除；单个成员的修边等时间编辑要求先取消编组。不是轨道锁定、同步锁或音画链接。工程格式 36，详见[操作、竞品差距与验证边界](docs/PERSISTENT_CLIP_GROUPS.zh-CN.md)。本轮没有新的原生安装包或 main 更新声明。

### Session source marks and atomic overwrite

Select one bin item and use **Open source viewer…** to inspect source frames, set session-only In/Out marks, capture a destination track/playhead, preview the affected ranges and explicitly apply one atomic overwrite. Outside ranges and other tracks do not ripple. Source audition is a separate bounded source-only window, not the final timeline mix.

The initial picture-edit subset requires matched-rate, verified CFR whole-frame sources and an aggregate 30-second selected/affected source proof budget. Mixed-rate inspection remains available but picture overwrite rejects it. Ambiguous historical trim phase, unsupported affected settings and non-preserving audio sample boundaries reject the whole operation. Schema 37 and established rendering semantics remain unchanged. See [Source marks and overwrite](docs/SOURCE_MARKS_OVERWRITE.md) for exact behavior, resource limits and deferred features.

The next bounded round replaces the former 30-second compressed-audio origin-endpoint limit with session-only exact f32 PCM windows. Cold preparation scans from origin; warm windows can be reused after source/hash checks. Selected/affected proof remains 30 seconds combined, with a 120-second whole-operation deadline, 30-minute cumulative normalized origin-scan budget and 48 MiB shared retained/pending storage. Embedded picture-track audio now receives PCM completeness proof. [Exact contract, measurements and comparison](docs/EXACT_SOURCE_AUDIO_CACHE.md).


## 片段音量关键帧（schema 41，本地源码）

音频检查器新增最多 128 个手动音量关键帧，以 48 kHz 输出片段本地采样点定位，支持 dB 线性／保持插值。原有基础增益与包络偏移相加，不自动限幅；隐藏关键点保留，修剪／分割按全局采样边界重新定位。准确样本输入、毫秒显示及可取消的候选拖动均不按视频帧吸附。预览、导出和音频分析共用逐样本混音实现，空包络保持旧滤镜图与 PCM；源波形仍为处理前波形。见[时钟、编辑语义、竞品边界和验证](docs/AUDIO_GAIN_KEYFRAMES.md)。这不是轨道总线、自动 ducking、实时播放保证或新的 Windows／macOS 测试包声明。

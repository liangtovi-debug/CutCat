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

# Current candidate: source slip for existing affine-linked J/L pairs (schema43 unchanged)

Use the existing Joint source slip action with the explicitly active V/A role as the requested source-In anchor. One exact common source delta translates both independent source maps while preserving their J/L timeline edges, unequal rational tails, local keys, ownership and historical mix anchors. Preview/Apply retain worker freshness, one Undo and direct/late-result IME/modal/playback/job guards. [Workflow and exact limits](docs/AFFINE_LINKED_SOURCE_SLIP.md).

Baseline: private J/L source `ef7fa394e3f23b2b8276365f66283b24aa0dba81`, 1,049 files, identity `5c36cc7bd03ac11cdaffbad9571d4e5e8a204910c7dbd357ed19d2d6ccaf2939`. Schema43, native version, renderer/audio compilers and dependencies are unchanged. V2 trim/split/joint roll remain unsupported. New UI/media, whole/fresh, remote and Windows/macOS package gates are not asserted by this candidate description.

## Previous bounded rounds (historical descriptions below)

# Current candidate: joint roll of two adjacent linked A/V pairs (schema42 unchanged)

Select exactly two complete adjacent strict linked A/V pairs. The existing roll inspector previews all four roles at one shared absolute timeline cut, preserving both outer endpoints and the active role, then commits once. No new IDs or links, ripple, transition, ownership change or silent extra-selection reduction. Exact rational tails and all hidden right-side keys are retained/rebased; non-fitting fades, source/key bounds, locks and changed historical audio order reject atomically. Audio can intentionally change. [Workflow, exact contract, limits and official comparison](docs/LINKED_AV_PAIR_ROLL.md).

Based on accepted R5 linked-pair split source: schema42, 1,028 files, identity `dd2501d3462f7b21c6ac6f90932e7d4a41f1e08185d94e883fb77e46945f5bda`. Schema, renderer/audio compilers and dependencies are unchanged. Source/offscreen tests, frozen aggregate/fresh acceptance, private integration and Windows/macOS packages remain distinct gates; no new pass or delivery is inferred from this candidate description. Independent A/V timing/J/L, linked speed/ripple, multi-partner and external sync remain absent.

## Previous bounded rounds (historical descriptions below)

# Current candidate: qualified joint linked A/V through-split (schema42 unchanged)

Pause playback, select one complete same-source linked pair and choose Split at playhead. Preview and Apply each verify actual CFR timestamps and complete native-resolution RGB16 legacy/global timing sequences before one atomic edit. Left IDs/link remain; the new right pair is selected with the active V/A role preserved. All hidden keys rebase exactly; crossing fades, changed potential mix order and incompatible PCM sample boundaries reject. [Workflow, exact contract, limits and official comparison](docs/LINKED_AV_PAIR_SPLIT.md).

First version: integer source In/Out, whole-frame 1×, exact source/project FPS, selected source union ≤ 30 seconds, fresh qualified media. Retimed/fractional/partial-tail/mixed-rate/VFR and active effects/stabilization/restoration reject; this is not J/L or arbitrary multi-component split. Based on accepted 1018-file edge-trim source identity `3bcb8e73ab42242e6dad3e5bf8b637b974597d405a8b2ad48510ff065f3f8d8c`. Schema, render/audio compilers and dependencies are unchanged. Matching frozen tests/independent/full/fresh acceptance, private remote/CI and native Windows/macOS packages are separate gates, not asserted here.

The candidate also scopes queued form reflow when a hidden top-level controller root owns visible child windows, preserving their hidden-tab layout while deferring hidden main-window geometry. Non-window roots, construction, explicit refresh and showing the parent retain full reflow. [Exact layout behavior and validation limits](docs/UI_ALIGNMENT.md#queued-reflow-while-a-parent-window-is-hidden).

## Previous bounded rounds (historical descriptions below)

# Current candidate: joint non-ripple linked A/V edge trim (schema42 unchanged)

Select exactly one complete same-source linked pair and drag either edge of either member. Both roles preview together and commit in one Undo, preserving exact source-tail residue, rebasing hidden keys correctly and keeping other objects fixed. Fades retain their frame lengths and follow their edges; newly selected audio is not promised to match retained PCM. Partner collisions, locks, key overflow and changed historical mixing order reject atomically. [Workflow, exactness, safeguards and official comparison](docs/LINKED_AV_EDGE_TRIM.md).

Based on accepted linked-A/V schema42 source (1011 files, tree identity `5f81be5a75af37d3e4eb99123b71145eab1db46a23ef0025ebf5acd8029eec43`). Schema, render/audio compilers and runtime dependencies remain unchanged. Linked split/roll/speed/ripple, independent A/V/J/L timing and external sync remain unsupported. Matching focused/oracle/UI, independent review, frozen aggregate and fresh-extraction gates remain separate; remote/main/CI and Windows/macOS packages are not asserted here.

## Previous bounded rounds (historical descriptions below)

# Current candidate: persistent same-source linked A/V (schema42)

Explicitly link one suppressed V clip and one enabled A clip with identical source/timeline maps. Select/move/delete/copy the complete pair, choose an active V/A role for parameters, and preview/apply exact joint source slip in one Undo. Link/Unlink is metadata only and never restores video audio. This earlier round did not include linked edge trim; the current candidate above adds it. Linked split/ripple/J/L/external sync remain unsupported. [Workflow, safeguards, schema and official comparison](docs/LINKED_AV_EDITING.md).

Based on the accepted 994-file final-frame schema41 source. Render/audio compilers and runtime dependencies remain unchanged. Frozen focused, actual RGB/PCM, independent review, EN/ZH DPI, aggregate and fresh-extraction receipts remain separate gates. No remote/main/CI or Windows/macOS package result is asserted here.

## Previous bounded round (accepted local source; historical text below)

# Current candidate: final composite still preview (schema41 unchanged)

Accepted baseline: `2c70ebb1e31733ee4d95fe20a43152ae0d38f5df`, including clip volume keyframes. This candidate adds only bounded final SDR still feedback. [Scope and gates](docs/FINAL_FRAME_PREVIEW.md). Its focused/pixel/UI/performance evidence does not replace matching frozen aggregate/extraction acceptance. No remote/main/CI or Windows/macOS package result is asserted for this candidate. The older delivery/candidate headings below are historical records, not current status.



# Current bounded round: strict explicit source-audio reattachment

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

# Current bounded candidate: persistent project timeline markers

Schema **39** adds fixed project-frame point/range annotations, names/comments, named colors, searchable list and guarded navigation. Metadata never follows clips/ripple, extends duration or alters rendering. Out-of-range annotations remain saved rather than silently clamped/deleted.

[Workflow, strict persistence and official-editor comparison](docs/TIMELINE_MARKERS.md). This candidate requires matching frozen-source focused/UI, independent review, aggregate and fresh-extraction receipts. Source acceptance, remote/CI status, and Windows/macOS native packages remain separate gates. This round makes no native-package or paid-editor-parity claim.

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

# Named source ranges — schema38 candidate

Exact named source selections now persist as metadata and can be freshly verified and recalled before the accepted Insert/Overwrite flow. See [SAVED_SOURCE_RANGES](docs/SAVED_SOURCE_RANGES.md). Source acceptance, remote/CI state and native package delivery remain separate; historical counts below are not this candidate’s verification.

## Prior delivery records

# Current bounded round: source marks → single-track Insert v1

The source viewer now offers explicit **Insert / Overwrite**. Insert preserves eligible existing source content, splits at an interior destination and shifts only the chosen track's downstream clips by the selection length. **Other tracks, captions and stickers remain fixed: this is not cross-track synchronization.**

Whole moved clips must retain their complete local audio compilation (including fades); only the final timeline delay may differ. Non-equivalent fractional-frame sample phases and unsupported clip relationships/settings reject the entire transaction. Schema37, legacy rendering/audio compilers and dependencies remain unchanged. [Workflow, compatibility and resource limits](docs/SOURCE_MARKS_INSERT.md) · [Current source-evidenced capability inventory](docs/CURRENT_EDITING_CAPABILITIES.zh-CN.md).

Local candidate only. Matching frozen-source focused/UI, independent review, aggregate and fresh-extraction receipts are required before acceptance. This round claims no remote/CI publication, Windows/macOS package or paid-editor parity.

## Previous bounded rounds (historical scope)

# Current bounded round: exact source-audio windows

Late AAC/MP3/FLAC and embedded-audio source windows use sparse session-only exact f32 preparation, without input seeking or changing schema37/rendering. The 30-second selected/proof limit remains. [Behavior, resource limits, measured cold/warm costs and paid-editor gaps](docs/EXACT_SOURCE_AUDIO_CACHE.md).

Local candidate only: focused tests are not final aggregate acceptance, and no remote/native-package delivery is claimed.

## Previous bounded rounds

# Current bounded round: non-destructive media bins

Local-only schema35 source. Nested manual bins, scoped text/type/usage filters, explicit media moves, empty-bin deletion and unused project-entry removal. No original media files are moved or deleted. Asynchronous imports validate the captured project/destination and commit accepted files in one undo step. [Scope and limitations](docs/MEDIA_BINS.md).

Acceptance requires matching frozen-source full-suite, independent review, UI and fresh-extraction receipts. No remote writes, hosted CI, Library publication, new dependencies or Windows/macOS packages in this round. The private schema34 review branch and historical native links do not establish delivery of schema35.

## Previous bounded rounds

# Current bounded round: accessible build help

Local source only. Screen-bounded, selectable and scrollable About/help with persistent Close, Tab/Shift-Tab, Enter and Escape access. English/Chinese and large-font high-DPI offscreen checks do not establish native desktop or screen-reader validation. [Scope](docs/HELP_ACCESSIBILITY.md). Matching frozen full-suite, independent review and fresh-extraction receipts are required for acceptance. No remote writes, hosted CI, new native packages, Library publication or dependency changes.

## Previous bounded rounds

# Current bounded round: measured loudness master-gain proposal

Local source only, schema34 unchanged. An optional target/estimated-peak ceiling produces a conservative starting gain, followed by actual cancellable detached draft measurement and explicit Apply with fresh source/context verification. Manual gain remains available. No normalization, limiter, compressor or encoded-output guarantee. [Scope and limitations](docs/MEASURED_LOUDNESS_PROPOSAL.md).

Acceptance requires the matching frozen full-suite, independent review, UI and fresh-extraction receipts. No new Windows/macOS package, remote write, hosted CI, Library publication or dependency change. Historical results below do not establish this round’s acceptance.

## Previous bounded rounds

# Current bounded round: final mix audio analysis

Local source only. Explicit cancellable pre-encoding stereo analysis uses the export audio builder; adds bounded four-times FIR estimated true peak (dBTP), with no automatic gain changes, live meters or compliance claim. Schema33 unchanged. See [scope and limits](docs/ESTIMATED_TRUE_PEAK.zh-CN.md). Acceptance is conditional on matching frozen full-suite, independent review and extracted archive receipts. No new native Windows/macOS package, remote write, hosted CI or Library publication is included. Historical results below do not establish this round's acceptance.

# 当前本地迭代：真实素材余量视频交叉溶解（schema33）

同轨相邻1倍速CFR片段间的居中双源转场，保持原切点、长度与音频不变。要求可实际解码的前后余量；不重复末帧、不暗中缩短。独立标记、异步可取消检查、单步撤销与失效上下文拒绝已实现。活动片段效果等首版限制明确拒绝，保留用户设置。详见[完整语义、边界与竞品对比](docs/VIDEO_CROSS_DISSOLVE.zh-CN.md)。本地最终全量／解压／独立复核以本轮对应回执为准，不能推断远程main或双平台原生包已更新。

## 先前迭代记录

# 本轮增量：画面动画有界线程

在上一轮表达式缓存之上，仅为已验证的普通画面动画选择最多 4 个滤镜线程，能力探测与实际导出使用相同线程数；普通／静态与复杂效果、字幕、贴纸、窗口动画仍保持 1。数学、RGB16／10 位精度、schema32 和依赖不变。详见 [测量、精度约束与竞品对照](docs/CLIP_MOTION_THREADS.zh-CN.md)。本地最终全量／解压验收以对应回执为准；不代表远程 main 或原生包已更新。

# 当前轮：画面动画全精度合成提速

仅复用同一局部帧的关键帧表达式结果，保留 RGB16、10 位全色度、几何边界和 straight alpha，不新增控件或依赖，schema32 不变。独立旧函数逐字节对照覆盖颜色转换前的 RGB16+alpha 与最终 YUV。详见[性能测量、验证与竞品对照](docs/CLIP_MOTION_PERFORMANCE.zh-CN.md)。

仍是离线合成提速，不是实时查看器、双平台原生包或付费编辑器体验对等。本轮只验证本地源码，不更新远程 main、不运行托管 CI、不上传 Library 或公开私有源码。最终数量与哈希以本轮冻结验收记录为准。

## 前轮状态（保留当轮测量，非本轮性能）

# 当前交付状态：累计源码与原生验证准备

本轮在累计源码上增加 schema32 画面位置、缩放与不透明度关键帧，支持保持／线性插值及隐藏关键帧的裁切／分割／滚动连续性。无关键帧时保留原有静态路径。详见 [画面动画语义与验证口径](docs/CLIP_MOTION.zh-CN.md)。远程提交与原生运行状态须以对应提交的独立验收记录为准，不能从本文件或历史测试数推断。

本轮定位为正确的离线动画能力，不是成熟付费编辑器的实时交互体验对等。应用后需要构建预览／导出；本机合成 3 帧的有界测量约为 1080p 22 秒、4K 86–87 秒，性能仍需优先优化。详细测量边界和限制见功能文档。

尚无本次源码的 Windows/macOS 原生包；不复用历史下载链接或旧包哈希。运行 CI 前先确认免费配额，不启用付费服务。源码整合提交使用 `[skip ci]`，后续工作流触发配置保持原样。请参阅 [当前原生说明](docs/NATIVE_TEST_QUICKSTART.zh-CN.md)。

## 历史功能交付记录

# 本轮：可视窗口关键帧时间编辑

为已有窗口关键帧增加可视时间条与精确帧号修改，明确拒绝重复帧移动，保留并标明片段外的关键帧。仍为整帧、保持／线性插值；不新增渲染算法、跟踪或缓动，schema30 不变。

详细边界和官方竞品对照见 [窗口关键帧时间编辑](docs/WINDOW_KEYFRAME_EDITOR.md)。本地验证不代表原生 Windows/macOS 包、远程 main 更新或已上传交付。最终数量与哈希以本轮冻结验收记录为准。

## 历史交付记录

# Apple Log 1 显式输入（实验性）

新增独立的“导入 Apple Log 1 / BT.2020（实验性）”入口；严格检查原色、矩阵、码值范围与传递函数标签，使用逆 Log、线性色域矩阵和固定的 CutCat SDR 高光／色域映射。默认 SDR 路径不放宽。schema29 保存素材解释；复制调色不复制输入解释。

目前只用合成 RGB16／10 位 YUV 文件验证，不代表任意 iPhone、HEVC／ProRes 摄像机文件已兼容。拒绝 Apple Log 2、冲突 SDR／PQ／HLG 标签、未知 YUV 矩阵／范围和活动防抖；不是 Apple LUT、Resolve 匹配或 HDR 母版。白平衡与创意曝光仍在固定 SDR 映射后处理。完整范围见 [输入说明](docs/APPLE_LOG_INPUT.md) 与 [数学／授权依据](docs/APPLE_LOG_MATH.md)。

本地源码验证不等于原生 Windows/macOS 安装包、远程 main 更新或已上传交付；最终测试和哈希以本轮冻结验收记录为准。

## 历史实现说明（保留当轮边界，不代表当前所有功能）

# 本轮：暂停时自动更新调色静帧

当前帧调色预览新增可选“暂停时自动刷新”，默认仍手动。检测到修改后等待 300 毫秒停顿，再更新当前所选片段；只保留一个运行任务和最新请求，旧任务取消、旧结果丢弃。取消后不自行重试，下一次编辑或手动刷新／重新开启才恢复。

仍是 RGB16 解码与整幅 float32 调色，包含 LUT，不含特效、变换、字幕和最终合成。不是实时播放、GPU、HDR、RAW 或 Apple Log 支持。schema28 不变；本轮只交付本地源码验收，不代表原生双平台安装包或远程 main 已更新。

# 本轮：暗角半径与柔边

在既有暗角上增加 0.25–2.00 半径与柔边，默认 1.00 保留历史路径；非默认明确采用 8 位/RGB16 径向数学。schema28，旧工程默认迁移。CPU 渲染，不是实时 GPU、HDR 或达芬奇算法对等。

详见[数学、迁移、竞品对照和边界](docs/VIGNETTE_CONTROLS.zh-CN.md)。本轮不声明已生成 Windows/macOS 原生包或更新远程 main。最终验证与交付以本轮终结证据为准。

## 历史记录（仅反映当时）

# 本轮：原生 RGB16 径向暗角

既有暗角可进入 ProRes 422 HQ 10 位 SDR，使用 RGB16 径向乘法，不先降 8 位。保留强度／开关／排序、0 值精确旁路，以及高斯模糊、基础字幕和已验证静态 PNG 贴纸。预览／H.264 保留旧路径，不保证与高位深暗角逐像素一致。

这是编码 SDR 数值处理，不是线性光、HDR/ICC、GPU 实时特效或竞品对等。通用表达式比旧暗角慢，详见[数学、实测性能与验证范围](docs/HIGH_BIT_VIGNETTE.zh-CN.md)。schema27 不变。最终通过数量以同轮验收说明为准；未生成双平台原生安装包，未声明远程 main 已更新。

## 历史记录（以下状态仅反映当时）

# 本轮 高位深高斯模糊

ProRes 422 HQ 10 位 SDR 现在接受已有高斯模糊，通过实际能力检查直接处理 RGB16，不借助 8 位中间链路。Sigma 保持 0–30 个项目像素，禁用／零强度精确旁路；现有特效目录只有高斯模糊与暗角，生效中的暗角仍阻止高位深导出。可与基础字幕、已验证静态 PNG 共用，动画贴纸包仍不支持。

这是编码 Rec.709 码值模糊，不是线性光、HDR、特效关键帧或达芬奇等效。详见[精确范围、竞品对照与验证方式](docs/HIGH_BIT_EFFECTS.zh-CN.md)。schema27 不变，预览和 H.264 旧路径保留。实际测试数量、双平台原生包和远程 main 状态以对应已验证的交付记录为准，不从源码或 offscreen 截图推断。

## 历史记录 以下状态仅反映当时

# 本轮 高位深静态 PNG 贴纸合成

ProRes 422 HQ 10 位 SDR 现在可使用经过验证的静态 PNG 贴纸，不把背景视频降为 8 位。位置、缩放、不透明度与已有变换关键帧保持可用，也可同时烧录基础字幕。实际像素能力检查失败会明确阻止导出，不静默降级；动画帧包与生效中的暗角仍不支持此档位；高斯模糊支持见本轮说明。

贴纸自身仍由 Qt 生成 8 位 RGBA 图层，保留旧版伽马编码 SDR 数值解释，并非线性光、sRGB 传递函数转换、ICC 或 HDR 色彩管理。详见[支持范围、透明边缘与竞品差距](docs/HIGH_BIT_STICKERS.zh-CN.md)。schema27 不变；最终通过数量与交付状态以同轮验收说明为准。本轮为累计源码，未生成 Windows/macOS 原生安装包、未更新远程 main。

## 历史记录 以下状态仅反映当时

# 本轮：当前帧调色检查

调整并应用调色后，可单独检查所选片段的当前帧，不必先重建整条时间线。完整调色和 LUT 之后、特效／变换／字幕／叠层合成之前的静帧，明确标注不是最终画面；正式预览和导出仍用原流程。

全分辨率 RGB16 解码、float32 计算，手动刷新、取消和过期保护；详见[使用方式、阶段边界和验证](docs/FRAME_GRADE_PREVIEW.md)。schema27 不变。本轮为累计源码，不代表已生成 Windows/macOS 安装包或已更新远程 main。

## 历史记录（以下状态仅反映当时）

# 本轮：全分辨率色彩处理提速

优化色相曲线的 CPU 归约、索引与临时内存，不降低预览／导出分辨率，不跳过调色阶段，也不改变 float32 数学。原控制和 schema27 保持不变，白平衡、Rec.709 矩阵／范围及排队定位保护保留。

[可复现实测、逐位一致性、限制与竞品差距](docs/COLOR_PREVIEW_PERFORMANCE.md)。预览仍须完整渲染后播放，不是 GPU 实时调色。完整回归与重新解压验收以本轮验收说明为准；本轮为累计源码，未生成 Windows/macOS 原生安装包、未更新远程 main。

## 历史记录（以下状态仅反映当时）

# 本轮：中性参考白平衡

从已归一化、已完成空间修复，但尚未白平衡及创意调色的源画面中，选择已知中性灰／白表面。使用有界的线性 Rec.709 RGB 增益修正色偏，保留所选参考的线性亮度，而非全画面亮度。黑暗、接近剪切、非有限及需要过大增益的参考会被拒绝；原暖冷／绿洋红艺术偏移保留独立。

schema27；旧项目默认关闭且恒等。详见 [数学、取样阶段与限制](docs/WHITE_BALANCE.md)。保留上一轮 Qt YUV 颜色修正及排队定位保护。完整回归、解压验收与交付状态以本轮验收说明为准。本轮仍为累计源码，未生成 Windows/macOS 原生安装包，未更新远程 main，不声称 Kelvin、Bradford、RAW／Log／HDR 或自动肤色保护。

## 历史记录（以下状态仅反映当时）

# 本轮：跨镜头参考静帧对照

捕获一张当前预览静帧，切换到另一镜头后并排或划像对照，辅助人工统一色彩。参考只在本次会话内保存，不随后续调色改变；对照仅用于显示，不参与导出、工程保存或撤销历史。不是自动匹配，也不是原始高位深参考。

详见 [操作、数据边界与竞品差距](docs/COLOR_REFERENCE_COMPARISON.md)。schema26 保持不变，最终回归和解压验收以本轮验收说明为准；未生成 Windows/macOS 原生安装包，未更新远程 main。

## 历史记录（以下状态仅反映当时）

# 本轮：基础对比度中心

对比度不再只能围绕固定 0.5 拉伸，可在基础调色中调整明确标注的编码 Rec.709 RGB 中心。默认值和原运算次序保留旧画面；对比度为 1 时中心不影响画面。schema26 保存此参数，复制、撤销、恢复、缓存和取样阶段保持一致。

详见[数学、操作、兼容与竞品差距](docs/PRIMARY_CONTRAST_PIVOT.md)。最终完整回归、解压验收和 Library 版本以确认结果为准。本轮为累计源码，未生成 Windows/macOS 原生包、未更新远程 main；不是 HDR、真实白平衡或 Resolve 算法等效。

## 历史记录（以下状态仅反映当时）

# 本轮：颜色条件独立开关

每个基础、添加与排除范围都可独立开关 H、HSV 饱和度、Y′ 亮度条件，关闭保留全部硬范围与羽化原值。关闭条件权重为 1，三项全关会选中全部有效颜色；排除范围全关则清空颜色蒙版，已启用校正时应用前明确确认。取样只修改基础范围已启用的通道；所选通道关闭时拒绝柔边取样。

schema25；详见[条件数学、取样及兼容边界](docs/QUALIFIER_CHANNEL_SWITCHES.md)。最终完整回归、重新解压验收及 Library 版本以成功验证结果为准。本轮为累计源码，未生成 Windows/macOS 原生包，未更新远程 main。

## 历史记录（以下状态仅反映当时）

# 本轮：独立低端与高端颜色柔边

基础、添加与排除范围的 HSV 饱和度和 Y′ 亮度现在分别调整低端/高端羽化。柔边取样只改变像素所在的外侧边缘，不再同时影响另一端；色相仍按圆周对称处理。旧工程通过明确继承保留原值与原输出，不在打开或无变化应用时隐式改写。

schema24；数学、迁移与边界见 [独立双侧柔边](docs/QUALIFIER_ASYMMETRIC_FEATHERS.md)。最终完整回归、解压验收和 Library 版本以成功验证结果为准。本轮未生成 Windows/macOS 原生包，未更新远程 main。

## 历史记录（以下状态仅反映当时）

# 本轮：指定范围与通道的柔边取样

先选基础/添加/排除范围，再明确选择 H、HSV 饱和度或 Y′ 亮度通道，点击冻结源像素调整该通道羽化。硬范围及其他通道不变；新边缘达到至少 50% 的单通道权重，并非总体蒙版权重。无法在现有羽化上限内实现时明确拒绝，不自动扩大范围。详见 [数学与边界](docs/QUALIFIER_SOFTNESS_SAMPLING.md)。

修改留在草稿，只有应用提交一步撤销，取消不改项目。schema23 与渲染数学不变，保留此前所有功能。本轮最终测试数量与 Library 交付版本以验收报告和确认结果为准；未生成 Windows/macOS 原生包，未更新远程 main。

## 历史记录（以下状态仅反映当时，不代表当前交付状态）

# 本轮：有界蒙版收缩与扩张

限定器新增带正负方向的蒙版边缘强度：负值收缩、正值扩张，固定 1 个项目画布像素的 3×3 方形邻域，包含对角线。绝对值是与原蒙版的混合强度，不是可变半径。0% 精确绕过新路径，保留此前结果。

先合并颜色范围、应用黑白点，再收缩/扩张、平滑，最后乘以静态窗口。叠加平滑时需要两像素真实邻域；稀疏概览与连续细节均读取同一冻结全精度源，不把显示网格当邻接像素。详见 [数学与边界](docs/MATTE_MORPHOLOGY.md)。

这是独立定义的有界形态运算，不宣称复制 Resolve 算法，不是时域去噪、跟踪或智能孔洞清理。最终冻结报告记录实际验收结果；未生成 Windows/macOS 原生安装包，未更新远程 main。Library 必需上传流程仍阻塞，本地文件不代表已交付。

## 前轮记录（不是本轮验收计数）

# 本轮：有界蒙版空间平滑

限定器新增蒙版平滑强度：固定半径 1 个项目画布像素，使用 3×3 二项式核，平滑颜色范围与黑白点处理后的选区，再乘以静态窗口。零强度完整保留此前输出。不是画面模糊、时域去噪、孔洞识别或 Resolve 算法复刻。

全图概览与局部细节都读取同一冻结浮点源的真实相邻像素，不能把稀疏网格当作连续像素；裁切边界需带一像素邻域，只有画布边界使用复制延拓。窗口外的零权重不会被平滑抬高。schema22 保存平滑强度；旧工程精确迁移为零，保留复制、撤销、恢复与缓存失效。详见 [数学与边界](docs/MATTE_SMOOTHING.md)。

验收计数以本轮最终冻结报告为准。仍为 Linux 离屏本地源码验证；未生成 Windows/macOS 原生包，未更新远程 main。Library 必需上传流程未恢复前，本地文件不代表已交付。

## 前轮记录（不是本轮验收计数）

# 本轮：蒙版黑白点细化

限定器新增逐像素蒙版黑点、白点和重置。颜色范围合并与扣除之后，先重映射蒙版权重，再乘以静态窗口，避免在窗口外产生选择。默认 0% / 100% 完整保留上一轮计算路径。

这是数值黑白点，不是空间去噪、模糊或孔洞填补，也不宣称复制 Resolve Clean Black/White 算法。黑点必须严格小于白点；临时无效输入会禁用应用并清除预览，可直接重置修复。蒙版与叠色显示实际细化结果，取样仍来自限定器之前的冻结浮点源画面。

schema21 保存两个黑白点，旧 schema18/19/20 精确迁移为默认值。复制、撤销、恢复与窗口行为保留。详见 [数学与边界](docs/MATTE_LEVELS.md)。冻结完整回归和源码包解压验收以本轮验收 PDF 为准。

仍为 Linux 离屏本地源码验证；未生成 Windows/macOS 原生包，未更新远程 main。Library 上传阻塞未解除，不能将本地文件视为已交付。

## 前轮记录（不是本轮验收计数）

# 本轮：颜色范围添加与减除

限定器新增明确的“替换 / 添加 / 减去”取样模式，以及可分别编辑、移除、清空附加范围的列表。一个基础范围加最多 8 个添加范围、8 个减除范围，不会把红色和蓝色悄悄扩大成覆盖中间颜色的单一区间。颜色包含权重取最大值，再乘以排除权重的补值，最后乘以现有的单个静态窗口。

取样来自限定器之前的冻结 float32 源像素，不读取叠色、控制柄或调整后的 RGB8 显示。只在点击应用时产生一步撤销；取消与过期画面不改项目。schema20 保存独立范围，旧 schema18/19 迁移为空附加列表，保留旧输出。详见 [精确数学与边界](docs/QUALIFIER_SETS.md)。

最终冻结完整回归、重新解压验收与截图结果以本轮验收说明为准。本轮未生成 Windows/macOS 原生包、未更新远程 main，Library 上传仍待支持流程可用，不能把本地文件视为已交付。

## 前轮记录（不是本轮验收计数）

# 本轮：静态调色窗口直接拖动与缩放

在限定器的源视图直接放置和缩放单个椭圆或矩形窗口，保持数值坐标、羽化与空间反转语义。控制柄只是界面辅助，不进入取样、蒙版或导出。仍为静态窗口，不提供跟踪或关键帧。

只有应用才提交一个可撤销的调色修改；显示导航与无变化应用不创建修改。实际验证结果以本轮验收报告为准。详见 [窗口控制柄](docs/WINDOW_HANDLES.md)。

仍为 Linux 本地源码验收，未生成新 Windows/macOS 原生包，未更新远程 main；Library 更新须以实际确认版本为准。

## 前轮记录（不是本轮验收计数）

# 本轮：原图选区叠色检查

限定器新增“选区叠色 · 品红 50%”：保留源画面上下文，按实际选区权重叠加品红，羽化边缘强度连续变化。叠色下仍可直接取样，明确读取底层 float32 源像素，而非显示颜色。保留原有全图、局部细节、蒙版与调整后视图。

叠色仅显示、不写入工程、不进入导出；点击仍替换范围，只有应用才提交且可一步撤销。处理阶段、灰阶/黑边/过期与不支持源格式保护不变。详见 [选区叠色检查](docs/QUALIFIER_OVERLAY.md)。

本轮完整冻结回归、重新解压验收与截图数字见随附验收 PDF。仍为 Linux 离线源码验收；没有新 Windows/macOS 原生包，也未更新远程 main。

## 前轮记录（不是本轮验收计数）

# 本轮：局部全精度细节取样

限定器新增 512 / 256 / 128 / 64 像素连续局部裁切、滚轮缩放、中键平移与 X/Y 定位。双击全图进入细节，能找回稀疏网格遗漏的小色点；始终读取同一份限定器之前的 float32 快照，不放大代理网格冒充原始细节。

“全精度”指已经适配项目画布的像素，不是项目缩放前的相机原始像素。全图明确标为稀疏取样，局部蒙版逐像素计算，导出覆盖整帧。取样仍替换范围并保留羽化、校正与启用状态；平移和缩放不改调色，应用可一步撤销。schema18 不变。详见 [操作、处理阶段与边界](docs/QUALIFIER_PICKER.md)。

本轮完整回归、交付包解压验收和截图数字见随附验收 PDF。仍为 Linux 离线源码验收；没有新 Windows/macOS 原生包，也未更新远程 main。

## 前轮记录（不是本轮验收计数）

# 本轮：单击源画面初始化联合颜色范围

限定器新增明确的“单击源画面替换范围”：从限定器之前的真实 float32 源取样，替换为 H ±10°、HSV S / 编码 Y′ ±10 个百分点，并立即显示待应用蒙版。保留羽化、校正参数和启用状态；取消不改项目，应用只产生一步撤销。不提供会悄悄扩大联合区间的“追加”按钮。

显示为最长边 512 的精确源取样网格，点击对应显示网格原像素并显示源坐标和 RGB/H/S/Y′；不是全分辨率放大取色，不读取屏幕 RGB8 或调整后颜色。灰阶、域外 RGB、填充边缘与过期源均拒绝。schema18 不变。详见 [阶段、操作和竞品边界](docs/QUALIFIER_PICKER.md)。

本轮完整冻结回归、交付包解压验证和真实 Qt 截图计数以随附验收 PDF 为准。仍是 Linux 离线源码交付，不声称已更新远程 main 或生成新的 Windows/macOS 安装包。

## 前轮记录（不是本轮验收计数）

# 本轮：色相 / HSV 饱和度 / Y′ 亮度联合限定器

专业工作区 → 颜色 → 色相 / 饱和度 / 亮度限定器。真实片段静态画面和黑白选区预览，可调整色相范围、饱和度与编码亮度上下限、各自羽化，并仅对选区调整色相、饱和度和编码增益。不是 HSL 明度、线性曝光、肤色识别或跟踪。

RGB16 解码 → 输入转换与空间修复 → 基础/RGB/色相曲线 → 限定器取样和调整 → LUT。schema18；默认关闭、选区调整中性时输出逐位不变。详情与限制见 [限定器说明](docs/QUALIFIER.md)。透明源和生效防抖仍不支持静态限定器取样；本轮为离线源码验收，不声称已推送 main 或生成 Windows/macOS 安装包。

## 前轮记录（不是本轮验证计数）

# 本轮修复阶段一致取色源码（2026-10-22）

新增独立的片段静态取色窗口：读取 RGB16 源画面，输入归一化后先在有效画面执行空间画质修复，再经基础调色与 RGB 曲线，最后在色相曲线和 LUT 之前以 float32 取样，不误用最终合成的 8 位预览色相。取色只选择颜色，确认后添加保留当前曲线形状的中心点及范围锚点；随后调整选中点。支持红色环绕、一步撤销、重复不新增、灰阶/黑边/过期画面拒绝，以及真实非中性曲线范围反馈。

专业工作区 → 颜色 → 曲线 → 色相对色相 / 色相对饱和度 → 从片段画面取色。先将播放头放在选中片段内。详细处理阶段、精度和限制见 [取色说明](docs/COLOR_PICKER.md)。现支持生效的逐帧空间画质修复，并标示已应用或旁路；仍拒绝生效防抖、修复区域中途变化及透明源格式取色，绝不自动关闭原功能；旧项目、schema17 和导出行为保持不变。

最终冻结源码回归、交付包解压验收及实际截图数字见随附本轮 PDF。仍是 Linux 源码交付，没有新的 Windows/macOS 安装包或已确认的远程 main 更新；下方记录仅为历史。

# 本轮选择性色相曲线源码（2026-10-22）

新增 Hue vs Hue / Hue vs Saturation 循环曲线，具备精确输入、键盘操作、撤销/重做、保存重开和调色复制。schema17；旧工程缺失字段迁移为中性默认，默认输出不变。两条曲线接入同一 float32 SDR 处理链，在主/RGB 曲线之后、LUT 之前运行，适用于原预览和受限 ProRes10 位导出。

详细数值定义见 [选择性色相曲线](docs/SELECTIVE_COLOR.md)，竞品复核、边界及建议下一轮见 [本轮对比](docs/SELECTIVE_COLOR_COMPARISON.zh-CN.md)。完整冻结源码、解压回归和实际 Qt 截图的最终验收数字见随附 PDF。Linux CPU 离线评估不代表实时性能或原生平台验证；本轮没有新的 Windows/macOS 安装包，也未确认远程 main 更新。以下为历史记录，不能视作本轮构建结果。

# 本轮基础剪辑源码（2026-10-22）

最终完整回归：3832 通过、2 跳过、0 失败，1290.03 秒。跳过是显式字体夹具与原生 Windows 盘符语义；只代表 Linux 源码验收。

新增本轨波纹删除、主视频静态位置/缩放/旋转/镜像。普通删除和旧工程默认渲染保持原行为。schema16，支持撤销/重做与保存重开。变换是离线、不透明黑画布，不是透明PIP或关键帧。完整对比、入口、边界与实测见 [基础剪辑报告](docs/BASIC_EDITING_COMPARISON.zh-CN.md)。本轮未验证新的 Windows/macOS 构建，也未确认远程 main 已更新。下方历史原生包记录不包含本轮功能。

# 最新本地源码：受控编辑预览（2026-10-21，实验性离线功能）

在 AI 面板选择已有字幕及音频片段，输入具体文字或 dB，预览差异后确认，整批只占一次撤销。
此界面是确定性的离线编辑表单，不是自动理解自然语言的完整 agent；原有本地字幕校对接入同一安全层。
可选 OpenAI Agents SDK 0.24.0 已通过真实 Agent/Runner 的本地假模型测试，未配置真实模型接口或保存密钥，远程追踪关闭。
详见 [功能范围](docs/CONTROLLED_ASSISTANT.md) 与 [验证记录](docs/CONTROLLED_ASSISTANT_VALIDATION.zh-CN.md)。
本轮无远端推送、CI 或新版 Windows/macOS 安装包，原生平台效果尚未验证。

# 最新本地源码：精确素材滑移（2026-10-20，尚无新版原生包）

新增独立的精确素材滑移：改变选用的素材内容，保留时间线位置、时长、速度、淡入淡出及变速末帧的精确源范围。附带音频随源区间变化；独立音频、字幕和贴纸不自动跟随。支持撤销重做及保存重开。详情、实测和限制见 [精确素材滑移](docs/EXACT_SOURCE_SLIP.md)。本轮不修改线上配音、不触发付费构建、不推送远端；保留已有 MeloTTS/Kokoro/在线音色目录。源码包不含运行库、模型或字体。

# 本地自然中文语音诊断（2026-10-17）

已用官方许可的3条真人普通话录音完成12组模型/语言配置对比。small在这些样例优于base，仍有错词；简繁差异另列评分，不修改原始字幕。135项相关测试通过，没有新版原生包。详见 [完整结果及限制](docs/NATURAL_MANDARIN_ASR_VALIDATION.md)。

# 最新本地源码：真实 ASR 到字幕/导出验证（2026-10-17）

官方 whisper.cpp CPU 运行库和明确选择的本地 small 模型，已完成真实识别、字幕编辑/撤销、工程保存重开、SRT、预览与导出流程；相关回归 243 项通过，另有 7 项重叠的防护测试通过。Linux 测试子进程禁止指定 socket/io_uring 系统调用；已修正测试防护中 sendto 错误码导致的 Qt/PulseAudio 管道唤醒卡死，所有原有系统调用仍被禁止，中英文完整流程重新通过。英文合成样例识别准确；中文样例仍有明显错词，必须人工校对，不能据此宣称中文识别质量达标。模型、运行库及字体未捆绑；没有新增 Windows/Mac 包或付费构建。详见 [本轮结果与复现方法](docs/LOCAL_ASR_SETUP_VALIDATION.md)。

# 最新本地源码：AI 组件设置（尚无新版原生包）

2026-10-16：新增本地 AI 组件路径设置、依赖元数据诊断、可选哈希检查、取消/重试及中英文界面。仅在 Linux 本地源码环境验证；没有重新构建 Windows/macOS 包，没有新增模型推理验证。美颜实验仍未接入自动人脸推理。详见 [组件设置说明](docs/LOCAL_COMPONENT_SETUP.md)。以下原生构建记录为历史交付，请勿套用于本次源码修改。

# 当前交付：中英文界面＋LUT 原生测试包

2026-10-15：Windows x64 与 macOS arm64 包均已构建、重新解压运行并验证。Windows1093通过/7平台跳过，Mac1099通过/1平台跳过，各414个中英文界面状态无布局违规。中文「剪辑猫」/英文CutCat实时切换；3D .cube 支持0–1强度、移除、撤销及保存。源码与包验证提交2b7db2d。

使用步骤、外部FFmpeg/字体要求及未签名限制见 [当前说明](docs/NATIVE_TEST_QUICKSTART.zh-CN.md)，精确证据见 [本轮原生验收](docs/BILINGUAL_LUT_NATIVE_VALIDATION.md)。自动截图为offscreen加独立测试字体，不等同于完整物理桌面验收；可选AI运行库/模型未捆绑。

以下保留先前源码功能里程碑说明与历史测试数量，不替代上方当前原生交付记录。

# 简易 / 专业双模式与 SDR 输入基础

本阶段新增：

- 顶栏一键切换 Simple / Professional。共享同一工程和渲染引擎，切换不修改参数、撤销历史、预览或进行中的任务。
- 简易模式保留剪辑、基础调色、字幕、朗读与本地 AI、贴纸放置；专业模式显示已实现的色轮、曲线、LUT、示波器、效果和贴纸关键帧。
- 被隐藏的高级设置仍保留并参与渲染，界面明确提示。简易“重置基础调色”不清除高级设置；调色重置不改变输入解释。
- 显式 Rec.709 / sRGB / 线性 Rec.709 输入归一化；传递函数、色域、YUV 矩阵与范围分别处理。已知标签冲突、未支持的 HDR/广色域输入会报错。
- 新工程格式 v6，读取 v1–v5 并保留既有 Rec.709 解释。Before 与调色旁路仍保留输入归一化。

Actual one-click workspace switching and explicit SDR source-transfer normalization. The modes preserve one project and render pipeline. This is not a camera Log preset library, local masking, OCIO/HDR or calibrated monitoring implementation.

双工作区里程碑本地回归：840 项测试全部通过。实际运行截图、同工程双模式导出和 sRGB 示例：`artifacts/workspace-color-milestone/`。测试与限制见 `docs/VALIDATION.md`；两种模式说明见 `docs/workspace-modes.md`。此句描述当时状态：彼时尚未进行原生验收，界面为英文；当前状态见本文顶部。

## 保留的上一阶段功能

# 本地 AI 字幕与音色阶段 / Local AI captions and voices

临时工作名为 Lumen Edit。独立工程，未修改 StableFlowPro，未发布仓库。

## 本阶段实现 / Implemented

- 本地 Qwen 字幕校对：显示原文、建议和差异；明确确认后只改文字，保留字幕 ID/语言/时间，可撤销。
- 本地 MiniLM 语义搜索：中英文描述检索真实字幕，跳转到对应帧；字幕或模型变化会使旧索引失效。
- Kokoro v1.1 的103个音色条目（中文女声55、男声45；英语女声3：美式2、英式1），以及v1.0原有53个条目；156是版本与说话人条目的总数，不代表156种经听评确认的独特声线。仅已验证安装的版本可选，不混用ID。
- 音色名称/来源与文字语言筛选、本地收藏、真实生成试听；试听不自动插入时间线。
- 共享本地模型登记、哈希检查和AI任务互斥；无静默下载或云端回退。

Actual local proofreading, semantic caption retrieval and versioned voice audition are implemented. Existing editing, grading, captions, ASR, narration, effects and stickers remain covered by regression tests.

## 实测与限制 / Evidence and limits

最终完整测试数量见 `docs/VALIDATION.md`。旧版561项回归已保留。

Qwen实际纠正了英文和中文错字；日期改写与双语换行变化被保守规则拒绝。它仍可能出错，不能自动接受建议。MiniLM在16条合成字幕的10个中英文/跨语言测试查询中，预期字幕均排第一；这不是通用准确率或可靠“无答案”识别证明。

音色扫测验证各条目可以生成有限、非静音音频；它不等于逐个音色的人类听感评测。云环境没有扬声器，因此未验证物理播放。

- 真实项目/导出与界面：`artifacts/local-ai-demo/`
- 校对来源、哈希、输出及取消测试：`artifacts/subtitle-ai-validation/`
- 搜索完整分数、来源及资源测量：`artifacts/search-validation/`
- 音色来源、全条目指标及代表性音频：`artifacts/voice-browser-validation/`

## 启动 / Run from source

Python3.12，外部 FFmpeg/ffprobe（含zscale、FFV1、libx264、AAC、drawtext、gblur、vignette），以及所用语言字体。

```sh
python -m venv .venv
```

Windows PowerShell:
```powershell
.venv\Scripts\python.exe -m pip install -e ".[test,search,tts]"
.venv\Scripts\python.exe -m lumen
```

macOS/Linux:
```sh
.venv/bin/python -m pip install -e ".[test,search,tts]"
.venv/bin/python -m lumen
```

依赖安装是显式步骤；运行中的应用不下载模型。Local AI分页选择现有模型和可信运行程序；Captions分页选择并验证完整音色包。具体已验证版本、架构和大小见 `docs/LOCAL_AI_MODELS.md`、`docs/LOCAL_SUBTITLE_AI.md`、`docs/LOCAL_SEARCH.md`。

## 验证 / Validation

上一阶段源码回归：755项测试全部通过；解压运行、真实本地推理及音色采样详见 `docs/VALIDATION.md`。示例与实际界面截图在 `artifacts/local-ai-demo/`，6份音色样本在 `artifacts/voice-browser-validation/`。

## 尚未完成 / Still incomplete

Windows/macOS原生构建、物理声音、断网冷启动推理和完整发行许可审核尚未完成。当前新增AI证据来自Linux x86_64；不能当作已经验证的双平台安装包。默认原生草稿打包排除可选TTS/搜索运行库，模型与外部运行程序也不分发。

Kokoro顶层Apache声明不覆盖全部eSpeak NG GPL组件与词典/FST来源；非营利不免除这些义务。项目/原创素材没有被擅自授予开源或公共领域许可。仓库名称、可见性和发行许可仍待决定。

完整商业素材库、更多特效/转场、视频片段与效果的通用高级关键帧、代理、多机位、跟踪、OCIO/HDR及商用品质仍是后续目标。贴纸线性关键帧已实现，但不等于完整通用关键帧系统。该历史交付时界面已支持简体中文/英文，最终输出为 8-bit H.264/AAC。后续本地源码另有受限 ProRes 422 HQ 10 位 SDR 档位，以及基础字幕／静态 PNG 贴纸支持，见 [当前高位深边界](docs/HIGH_BIT_STICKERS.zh-CN.md)；不据此声明新的原生平台安装包已交付。

下一道验收仍是原生Windows/macOS与离线模型包验证，详见 `docs/NATIVE_VALIDATION_GATE.md`。这份交付不是剪映或Final Cut Pro全功能替代品。

## Native validation fixes (2026-10-15)

Initial real Windows/macOS runs reached the full test suite and exposed native
path aliases, Windows stat/fstat timestamp differences, line-ending changes,
and FFmpeg 9 option/fixture behavior. These are addressed without lowering
caption-frame, source-integrity or 10-bit precision assertions. Latest integrated
Linux regression: 986 passed, one Windows-drive-only test skipped; additional
focused render regression results are retained with the native validation evidence.
Native package acceptance still depends on a subsequent successful workflow and
its extracted-archive runtime smoke test. Chinese caption CI now uses a verified
external Noto font fixture and does not silently skip missing configured glyphs.
The source ZIP contains no font binaries, model weights, FFmpeg or AI runtimes.

## Verified native test packages — 2026-10-15

Run 38774145073 at commit bbda84a97ca5862c272069b6dad83223c1406fe6 succeeded
on both platforms. Windows x64: 983 passed, 7 POSIX-only skips. macOS 15 arm64:
989 passed, 1 Windows-drive-only skip. Both archived/re-extracted frozen apps
rendered media and decoded Qt video frames, including original stickers and
Chinese captions using a separately verified local CI font. Final committed-tree
Linux suite: 989 passed, 1 Windows-only skip. See
[Chinese package quickstart](docs/NATIVE_TEST_QUICKSTART.zh-CN.md) for actual
external FFmpeg/font requirements and unsigned-package limitations. Physical
audio, clean-machine setup, minimum OS, additional architectures and native
optional AI inference remain unverified. These results supersede earlier
statements that native CI had not run; they do not establish release readiness.

## Safe long-caption layout (local source only, 2026-10-17)

字幕烧录按画面宽度自动换行，原始字幕、SRT 文本和时间不变。横向安全边距与最大行数支持中英文界面、保存和撤销。无法完整容纳时明确报错，不自动缩字或截断。项目写入版本 10，旧版本 1–9 可迁移。验证范围和实际画面见 [字幕布局说明](docs/CAPTION_LAYOUT.md)。本轮未更新 Windows/macOS 安装包。

## 本地多音色朗读复验 / Local TTS revalidation

对现有 Kokoro v1.1 的 4 个真实音色执行中英文试听与实际合成，验证完整音频插入、取消/重试、撤销、保存和项目移动后的导出。运行于 Linux 开发网络禁止测试环境；没有实体扬声器验收或新 Windows/macOS 包。模型、运行库与数据不分发，许可证及跨平台遥测仍需发行前审查。详见 [本地朗读验证](docs/LOCAL_TTS_SETUP_VALIDATION.md)。

## 连续字幕配音 / Consecutive subtitle takes

配音与前一片段冲突时，可手动修剪或移动阻挡片段，再插入同一份已保存配音，无需重新合成。重试核对原字幕、帧率、工程会话及 WAV 哈希；完整音频、溢出确认和撤销保留。新增本地验证覆盖更多真实音色及长段落，具体结果见独立证据包和 [连续配音说明](docs/CONSECUTIVE_TTS_TAKES.md)。Windows/macOS 包未更新。

### Local TTS pause-preservation follow-up (2026-10-18)
Added20–100% native pause preservation, immutable provenance and schema11 migration. Same-word/same-voice samples demonstrate a pacing difference; user listening preference and pronunciation quality remain open. See docs/TTS_PAUSE_PRESERVATION.md. This local source milestone has no new native desktop package or full-suite-pass claim.

## Local TTS text-path audit (2026-10-19)

Known native text-loss diagnostics now prevent incomplete audio publication;
quoted punctuation/mixed identifiers receive non-destructive EN/ZH review.
New speech uses exact PCM sample counts (schema12), preserving legacy timing.
This does not solve Kokoro accent or clause prosody. Local-only validation and
actual model findings: [LOCAL_TTS_TEXT_AUDIT.md](docs/LOCAL_TTS_TEXT_AUDIT.md).
No new Windows/macOS package is included in this source milestone.


## 2026-10-22 fractional-rate source-tail milestone

The source includes bounded decoded CFR-compatible tail evidence, project schema 15, and relink persistence. Final frozen-source regression: **3718 passed, 2 skipped, 0 failed** in 1172.96 seconds. New tail-specific tests: 33 passed. See [Chinese scope, evidence, and remaining VFR/shifted mixed-A/V limitations](docs/FRACTIONAL_FRAME_TAIL.zh-CN.md). This delivery is source only; no new native package, remote push, or live model API verification is claimed.


## Metadata-aware preview conversion (2026-10-23)

Supported mapped YUV preview frames now use explicit matrix/range conversion instead of Qt 6.8.3 CPU fixed-BT.601 conversion. Unsupported or unknown YUV color metadata fails visibly, clears current scopes and disables capture rather than showing guessed colors. Reference pixels remain immutable and export/schema26 are unchanged. Already-RGB backend frames are passed through, not independently color-verified. See [conversion, evidence and limitations](docs/PREVIEW_COLOR.md). This change is not ICC/HDR monitoring or Windows/macOS native validation.

## Manual color-window animation (local source, 2026-10-24)

One ellipse/rectangle window can use explicit center X/Y and width/height keys
with hold/linear interpolation on output timeline frames. Repeated source frames
are not reused after grading: each animated output frame evaluates its own mask.
Hidden keys survive split/left trim/roll; speed/copy retain timing without scaling.
See [workflow, comparison and limitations](docs/WINDOW_ANIMATION.md). Schema 30.
This local milestone does not update native packages, remote main, automatic
tracking, GPU/realtime processing, or the experimental Apple Log input scope.

## Direct timeline edge trimming (local source round)

Single-clip non-ripple left/right edge dragging complements the source waveform view. It stages a frame-aligned candidate and commits one undoable edit on release, preserving the opposite edge, independent tracks and exact fractional source tails. Existing numerical trim, slip and roll controls remain. See [scope and limitations](docs/TIMELINE_EDGE_TRIMMING.md). Schema 30 and rendering are unchanged. This local source milestone does not claim remote publication, hosted CI or new Windows/macOS packages.

## Source marks / overwrite source candidate (schema 37)

Added a dedicated session-only source viewer and an explicit atomic single-track overwrite workflow. Inspect exact source frames, mark In/Out, capture destination track/playhead, preview affected ranges and Apply once; one undo restores the edit. Other tracks/captions/overlays and downstream positions stay fixed. Bounded audio/video audition is source-only and marking is disabled while it plays.

Compatibility is deliberately conservative: matched actual FPS/CFR whole-frame picture sources only, aggregate 30-second selected/affected source verification, and rejection of ambiguous legacy timestamp phase or retained-PCM sample-grid changes. Mixed-rate source inspection does not imply mixed-rate overwrite support. The accepted renderer and historical tests are unchanged; a shared rendering-contract change was investigated and explicitly not shipped. No new project schema is introduced.

Detailed contract and limits: `docs/SOURCE_MARKS_OVERWRITE.md`. Local validation receipts and fresh source-archive hashes are delivered separately. This remains source, not a native installable Windows/macOS package, and does not claim paid-editor parity, general source patching, insert/ripple, linked A/V, VFR overwrite or proxies. No remote/CI/dependency/publication action is part of this local candidate.

The next bounded round replaces the former 30-second compressed-audio origin-endpoint limit with session-only exact f32 PCM windows. Cold preparation scans from origin; warm windows can be reused after source/hash checks. Selected/affected proof remains 30 seconds combined, with a 120-second whole-operation deadline, 30-minute cumulative normalized origin-scan budget and 48 MiB shared retained/pending storage. Embedded picture-track audio now receives PCM completeness proof. [Exact contract, measurements and comparison](docs/EXACT_SOURCE_AUDIO_CACHE.md).


## Sample-local manual clip gain envelopes (schema 41 source candidate)

Adds strict bounded `audio_gain_keyframes`, exact 48 kHz output-local hand editing, outgoing linear-dB/hold interpolation, hidden-handle edit rebasing and one shared per-sample preview/export/analysis path. Existing scalar gain remains unchanged; no automatic limiting. Empty envelopes add no filter and preserve accepted schema40 graph/PCM. Separation copies the envelope; strict reattachment adopts the audio clip's current envelope. Source cuts through envelope clips are rejected atomically in this initial version.

See `docs/AUDIO_GAIN_KEYFRAMES.md` and `docs/AUDIO_GAIN_SCHEMA41_FIXTURE_LEDGER.md` for signal order, bounds, compatibility, intentionally narrow fixture updates, verification and limitations. Final validation receipts are separate; this local source candidate does not claim remote publication, aggregate acceptance, hosted CI, real-time playback or new native Windows/macOS packages.

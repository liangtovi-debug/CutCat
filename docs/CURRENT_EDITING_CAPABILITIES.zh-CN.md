# 当前剪辑能力清单：已验证基础与未完成边界

核对日期：2026-10-28 · 已接受基线：1028 文件 R5 合格单对音画联合分割 schema42 · 新候选：两对相邻关联音画联合滚动剪辑，schema42 不变。

这不是全产品完成率或付费版本等价表。旧 schema30 付费能力规划仅属历史，本 checkout 不包含其原文件；本页不补造引用或据此推算完成率。旧 `BASIC_EDITING_COMPARISON.zh-CN.md` 和 `ROADMAP.md` 的历史段落也不能覆盖较新的具体实现。

## 状态含义

- **verified-local（有界项）**：已接受的前序本地源码中存在具体实现、测试与里程碑证据；不是本次重新运行的结果，也不代表所属能力族全部完成。
- **partial（能力族）**：有可用子集，但关键工作流仍缺失，或本轮新增候选尚待绑定冻结源码的验收。
- **absent（具体缺口）**：本轮未实现；不是把相邻已有能力一并抹掉。
- **native-unverified**：此候选无 Windows/macOS 原生包验收；不能从 Linux 离屏、旧包或源码存在推断可交付。

## 本轮候选：两对相邻关联音画联合滚动剪辑

准确选择两对完整且相邻的同源关联音画，原滚动检查器显示四个角色的 ID、源区间与时长，输入一个新的绝对时间线接点，原子提交并一次撤销。两对可引用不同素材／速度，组内映射仍须一致；保留各自不足一帧的尾部，右侧全部隐藏画面键按帧、音量键按全局采样边界差重定位。淡化原长度跟随边缘，无法容纳即拒绝；历史混音顺序改变、锁定、越界或额外选区也整次拒绝。不更改外端点、ID、link、音频所有权和其他工程对象；不承诺 PCM 保持。见 [契约、限制与官方对照](LINKED_AV_PAIR_ROLL.md)。冻结／媒体／独立／全量／解压验收仍是另列证据，不是原生包交付。

## 前序已接受有界项：经验证的单对关联音画联合分割

暂停播放，只选择一对同源关联音画，在内部播放头处用原有分割按钮／菜单／快捷键打开捕获切点预览。后台验证实际 CFR 时间戳及原段／左右两段完整原尺寸 RGB16 帧序列；应用时重验，左侧保留原 ID/link，右侧形成新 pair 并保持当前 V/A 参数角色，一次撤销。所有隐藏键正确重定位，外淡化保持，跨切点淡化、混音排序或采样网格变化整次拒绝；其他工程记录固定。首版仅整数源入出点、1 倍速、同帧率、源并集 ≤30 秒；拒绝小数源边界／不足一帧尾部、变速、VFR／混合帧率、活动效果／防抖／修复。见 [契约、限制与官方对照](LINKED_AV_PAIR_SPLIT.md)。该 R5 前序源码已完成本地冻结／全量／解压验收；不是本轮重新执行或已交付原生包。

前序同源 1:1 关联、整对选择／移动／复制／删除、明确 V/A 参数角色、后台验证联合源滑移及单对非波纹联合边缘修剪已获本地有界源码验收。边缘修剪仍按原契约保留精确源尾与边缘淡化，不承诺 PCM 保持。联合分割不是独立音画边界／J/L；联合滚动仅新增上述待验收子集；联合变速／波纹、多伙伴、外录同步与 source patching 仍缺失。schema42、编译／渲染和依赖不变，不等于付费版本全部功能。

## 前序已接受有界项：最终合成静帧

暂停后明确刷新目标帧，包含两视频轨、调色/LUT/窗口、效果、静态/动画几何与透明度、字幕、静态/动画贴纸和空隙。原尺寸偶数 SDR 画布最高 1920×1080；活动防抖、活动转场、Apple Log/透明源及未验证组合明确改用已有整段预览。不处理音频，不改变有效播放缓存、示波器、参考静帧或旧导出。可选自动暂停刷新只有一个运行任务与最新待处理请求，并拒绝过期成功/错误。见 [FINAL_FRAME_PREVIEW.md](FINAL_FRAME_PREVIEW.md)。前序有界源码已接受；不是实时播放器或代理工作流。

## 已接受 schema41 有界项：片段音量关键帧

`audio_gain.py` / `ui_audio_gain.py`：48kHz 输出片段局部采样时钟、Hold/Linear dB 包络、隐藏关键点、精确编辑/撤销/保存；分割与左裁切保持规定局部坐标，源插入/覆盖限制仍在。前序冻结源码已通过本地验收；源波形仍为处理前波形，不等于总线自动化、ducking、EQ、limiter 或实时处理后仪表。见 [AUDIO_GAIN_KEYFRAMES.md](AUDIO_GAIN_KEYFRAMES.md)。当前关联候选不改这些音频编译语义。

## 前序已接受有界项：严格、明确选择的源音频重新附加

准确选择一个已关闭源音频的 V0/V1 片段和一个启用源音频的 A0/A1 片段。预览显示两个具体 ID、保留的视频与仅删除的音频；应用时再次后台检查原文件。同一 Media、起点、时长和完整有理数源时序必须完全匹配，不猜配对、不重定位、不夹短。视频保留全部画面／源字段，接收选定音频的增益、用户静音、淡化与有效历史混音位置；音频即使用户静音也可重新附加并保持静音。一次撤销恢复两个原 ID 与设置。组、锁、转场和歧义混音位置拒绝；原有分割／源切割限制不放宽。见 [SOURCE_AUDIO_REATTACH.md](SOURCE_AUDIO_REATTACH.md)。该历史轮保留 schema40、渲染器、音频编译与依赖；现已包含在 schema41 已接受基线，不等于完整 A/V Link/Unlink、复合片段或付费版本等价。

## 前序已接受有界项：源音频分离为独立片段

明确选择 V0/V1 的一个已提交片段和空闲 A0/A1，先验证预览，再重新检查并一次应用。新音频引用同一 Media／原文件，保留完整有理时序、增益／静音／淡化；视频只关闭独立于用户静音的源音频组件。历史混音位置保持浮点累加次序，旧项目图像／PCM 不静默改变。两者不联动、不自动编组，删除音频或取消视频静音不会恢复原声；一次撤销恢复。首版明确拒绝分割和源插入／覆盖切割已分离组件，仍可独立移动／裁切／变速。见 [SOURCE_AUDIO_SEPARATION.md](SOURCE_AUDIO_SEPARATION.md)。前轮已接受本地源码；不代表完整 A/V Link/Unlink、源 patching 或付费编辑器等价。严格重新附加已包含在当前已接受基线。

## 前序已接受有界项：持久工程时间线标记

schema39 增加命名点/范围标记、备注、具名颜色、名称/备注搜索与颜色筛选；独立标记带和列表导航不改变已有片段命中区域。坐标固定在工程帧，不跟随片段、插入或波纹；不延长输出。缩短时间线后保留越界记录并拒绝定位，不隐式夹到末帧。标记纯元数据编辑/撤销保留已完成当前预览，但旧编辑计划和进行中任务照常过期。见 [TIMELINE_MARKERS.md](TIMELINE_MARKERS.md)。不是片段附着标记、章节、范围播放/导出或付费版本等价；现已包含在当前已接受基线。

## 前序已接受有界项：捕获目标轨的单个内部空隙

明确选择目标轨、捕获播放头后，只关闭相邻真实片段间的 `[L.end,R.start)`；不删除任何真实片段，不自动关闭所有空隙。可选其他轨/字幕/贴纸整项平移，所有相交参与项拒绝，未参与项完全固定。计划绑定左右身份及项目上下文，单步撤销/重开；新入口即使仅本轨也要求共同样本位移，拒绝局部相同却整体错一采样的移动。见 [SINGLE_GAP_CLOSE.md](SINGLE_GAP_CLOSE.md)。这不是持久 Sync Lock、磁性时间线、通用波纹修剪或付费编辑器等价。

## 前序已接受有界项：波纹删除音频保护与显式参与预览

旧本轨波纹删除现在验证完整局部音频编译，拒绝会改变样本数或淡化边界的移动，同时保留可证明等价的变速/复杂整片段和平移转场对。新增单次参与预览只删除选中已提交片段，其他勾选轨道/字幕/贴纸仅整项跟随；任何与删除区间相交的参与跟随项整笔拒绝。共同负样本位移必须为 `S(A)−S(B)`，不是 `−S(D)`。默认即时本轨入口仍保留；新入口保守拒绝触及转场。见 [RIPPLE_DELETE_PARTICIPATION.md](RIPPLE_DELETE_PARTICIPATION.md)，历史轮验收已包含在 schema41 已接受基线；不是本轮重新执行的结果。

## 前序已接受有界项：源插入的显式参与项

本轨仍为默认；单次 Insert 可显式勾选额外片段轨道，并分别选字幕/贴纸跟随。跟随项跨切点即整笔拒绝；未勾选项原样固定。额外验证共同整数样本位移，三类集合一次提交、单步撤销。不等于持久同步锁、音画链接或独立 A/V patching。见 [SOURCE_INSERT_PARTICIPATION.md](SOURCE_INSERT_PARTICIPATION.md)，历史轮验收已包含在 schema41 已接受基线；不是本轮重新执行的结果。

## 前序已接受有界项：命名源选区

保存/调用/重命名/删除源选区；使用有理数秒和捕获帧率，重开后重新验证内容与时序绑定。精确帧映射，不取整、不夹短、不自动换源。记录是元数据，不是独立子片段或代理；既有 Insert/Overwrite 验证不变。完整契约与官方对照见 [SAVED_SOURCE_RANGES.md](SAVED_SOURCE_RANGES.md)。前序已完成本地源码验收；不代表该能力族或原生包完整。

## 本轮纠偏矩阵

| ID / 能力族 | 当前状态 | 源码可证实的已有子集 | 未完成 / 不可混淆 |
|---|---|---|---|
| **T01 基础剪辑与修边** | **partial**；既有基础项 verified-local | 分割、数值修剪、非波纹直接边缘修剪、撤销/重做；`Editor.split_clip`、`trim_edge`，`tests/test_trim_edge_model.py` / `test_trim_edge_media.py` | 不是任意关系联动修剪；原生鼠标/触控板与当前包仍待验收 |
| **T02 slip / roll** | **partial**；既有单轨项 verified-local，本轮两对关联滚动候选 | 精确源滑移与同轨相邻切点滚动；新候选在严格两对音画上联合移动同一接点；`EXACT_SOURCE_SLIP.md` / `ROLL_EDIT.md` / `LINKED_AV_PAIR_ROLL.md` | 不是任意多轨关系修剪、slide 或成熟直接手势集；新增联合滚动仍需本轮冻结验收 |
| **T03 波纹 / Insert / Overwrite / slide** | **partial，不再是 absent** | `Editor.ripple_delete_clip` 已做本轨波纹删除；`source_overwrite.py` 已做有界源选区覆盖；`source_insert.py` 已有保留旧内容的本轨插入；Insert 与波纹删除已接受显式单次跨轨/字幕/贴纸参与；已接受捕获目标轨的单个内部空隙关闭 | 默认其他项固定，可显式选择整个下游项跟随；没有持久同步锁/通用锚点策略、通用 slide、四点/fit-to-fill 或完整 ripple 工具组；单空隙关闭有独立冻结本地验收，不能推导本轮关联或原生验收 |
| **T04 轨道、选择、组、链接、锁、solo** | **partial**；多选/持久组/编辑锁子集 verified-local | `Editor.move_clips` / `delete_clips` 原子多选操作；`group_clips` / `ungroup_clips` 与 schema36 组；`set_track_edit_lock` 与 schema37 四轨编辑锁 | 固定两视频两音频轨；组不等于音画链接，编辑锁不等于 sync-lock/mute/输出冻结；无可变轨数、轨道 solo/总线管理或字幕/贴纸组 |
| **T04b 独立音频组件分离／严格重新附加** | **partial；分离及严格重新附加 verified-local（schema41 基线）** | 分离引用同一源并关闭视频原声；已有严格重新附加可显式选择精确同源同时序 V+A，将所选音频参数／历史混音位置交回 V 并仅删除 A；单步撤销 | 不等于链接／取消链接、自动编组、复合片段或源 patching；时序不匹配不重定位；特殊状态分割／源切割限制保留 |
| **T04c 同源 1:1 音画关联／联合源滑移／边缘修剪／分割／两对滚动** | **partial；关联／源滑移／边缘修剪／R5 分割已接受，本轮两对滚动候选** | 持久 link/unlink、整对选择与移动／复制／删除、明确 V/A 参数角色；精确联合源滑移；单对非波纹边缘修剪；有界媒体验证的单对1倍速分割；新候选只移动两对四个成员的共享接点 | 分割仍拒绝未证明媒体／小数尾部／变速等；不支持联合变速／波纹、J/L、多个音频伙伴、外录同步或源 patching；取消关联不恢复视频声；新滚动候选需独立冻结验收 |
| **E01 视频转场与音频交叉淡化** | **partial，不再是“无双素材转场”** | `video_transition.py` / `video_transition_sources.py` / `video_transition_render.py` 已有真实 handles、同轨相邻匹配-CFR 1×居中视频 Cross Dissolve；历史本地项 verified-local | 音频保持原混音，不是音频交叉淡化；无转场库、跨轨/变速/VFR/动画-PIP 通用转场；不足余量拒绝 |
| **E02 画面/窗口关键帧** | **partial**；既有有界项 verified-local | `clip_motion.py`、窗口关键帧与时间编辑器；Hold/Linear 位置、缩放、不透明度及窗口动画；静态透明 PIP 已有 | 不是统一效果动画、通用曲线/缓动、跟踪或实时合成；独立音量包络已接受，不再写作全部音频关键帧缺失 |
| **T07 素材组织与重连** | **partial**；手动组织与命名源选区子集 verified-local | 素材箱、范围搜索、类型/时间线使用筛选；schema38 新增精确命名源选区，保存/重开/重新验证/调用、重命名和删除；相同内容重连可重新验证 | 不等于 Smart Bins、语义内容搜索、团队锁、评级/关键词管理或完整独立子片段；未保存的工作 marks 仍为会话状态 |
| **T07b 工程标记与批注** | **partial；schema39 前轮 verified-local** | 固定工程帧点/范围、名称/备注/具名颜色、搜索筛选、稳定 ID 编辑/撤销/保存/恢复；独立标记带；越界保留并拒绝导航 | 不是片段/源附着标记、波纹跟随、章节/待办、范围播放/导出或标记交换 |
| **T07c 完整片段复制／粘贴** | **partial；schema39 有界项已包含在 schema41 已接受基线** | `clip_copy_paste.py` + `ui_clip_copy_paste.py`：窗口私有已提交参数快照、完整组新 ID、原轨与相对间距、明确目标、依赖后台检查、局部／共同 PCM 位移守卫、一次撤销 | 不含系统／跨项目／跨窗口、Cut、属性粘贴、拖拽复制、换轨／覆盖／插入粘贴或转场复制；同轨冲突／锁／不等价相位拒绝；冻结验收回执另计 |
| **T08 精确时间与源尾** | **partial** | 有理数帧/采样边界、完整解码视频尾、源查看器实际 CFR/帧 hash、精确源音频窗口与实际 EOF 证明；`timing.py`、`source_viewer.py`、`audio_timing.py` | 混合帧率/VFR 源插入/覆盖仍保守拒绝；不能把全部媒体格式和相位都称兼容；旧渲染不在此轮暗改 |
| **P01 最终画面反馈** | **partial；静帧已接受基线** | 已有整段 H.264 播放缓存、选定片段完整调色静帧和帧导航；本轮增加有界最终 SDR 合成静帧 | 不是实时播放器；无新音频处理或完整代理契约；候选精度/性能/生命周期需独立验收 |
| **T09 代理/优化媒体** | **absent（完整工作流）** | 已有离线预览缓存和 session-only 精确 PCM 窗口；这是不同用途的缓存 | 无生成/外接/重连/切换代理与原片的完整契约；源窗口缓存不是代理导出源 |
| **A01 音频定位、分析与输出控制** | **partial**；既有有界项 verified-local | `audio_waveform.py` 源音频包络；`audio_analysis.py` 最终混音离线分析；手动 master gain、测量增益提案及 schema41 精确片段音量包络 | 源波形不是处理后波形；离线响度/估算 true peak 不是实时仪表、认证 true peak、自动规范化或 limiter；无完整路由/总线/EQ/动态处理 |
| **R01 工程恢复与可靠性** | **partial**；既有恢复子集 verified-local | `project_recovery.py` 本地周期快照、显式恢复副本、原子保存；`PROJECT_RECOVERY.zh-CN.md` | 不是零丢失、长期备份、媒体备份、团队版本控制或原生跨平台恢复保证 |
| **N01 当前原生发布** | **native-unverified** | 有源码、测试与构建/验收说明；`NATIVE_VALIDATION_GATE.md` | 此候选未生成并验收 Windows/macOS 包；远程提交、CI、打包、签名与实机结果不可互相替代 |

表中路径均相对仓库根目录的 `lumen/`、`tests/` 或 `docs/`；精确函数名比会随实现增减漂移的行号更稳定。当前候选自己的独立复核、全量和解压结果以冻结验收回执为准，不把前序测试存在当作本轮通过。

## 三项重要源码追踪

### T03：已有“本轨”不等于关系安全跨轨

- [模型](../lumen/model.py)：`ripple_delete_clip` 删除后只移动本轨后续 clips；中央提交、组、锁、转场守卫仍生效。[既有波纹测试](../tests/test_basics_ripple.py)、[实际媒体测试](../tests/test_basics_ripple_media.py)。
- [源覆盖](../lumen/source_overwrite.py)：不可变方案、保留片段、项目签名、过期拒绝、一次提交；[模型测试](../tests/test_source_overwrite_model.py)、[真实媒体测试](../tests/test_source_overwrite_media.py)。
- [已接受源插入](../lumen/source_insert.py)：同轨平移/内部拆分，含显式单次参与项；局部编译等价之外再守共同整数样本位移；[使用与资源边界](SOURCE_INSERT_PARTICIPATION.md)。该历史轮证据不替代当前候选回归。
- 音视频的实际处理来自 [audio_timing.py](../lumen/audio_timing.py) 和 [audio_mix.py](../lumen/audio_mix.py)。视频片段已有嵌入声音，不能把“没有显式音画链接”写成“没有源音频”。

### T04：多选、组与编辑锁三者分开

- [多选](CLIP_MULTI_SELECTION.zh-CN.md)：同一整数位移、原子非波纹删除，不是自动链接；[多选媒体测试](../tests/test_clip_multiselection_media.py)。
- [持久组](PERSISTENT_CLIP_GROUPS.zh-CN.md)：独立 ID、保存/重开/恢复、整组移动/删除；单成员修剪等需先显式解组；[组持久化测试](../tests/test_clip_groups_persistence.py)。
- [编辑锁](TRACK_EDIT_LOCKS.zh-CN.md)：四轨持久编辑保护，引用素材与关系同受保护；播放/导出不静音；[锁模型测试](../tests/test_track_edit_locks_model.py)、[锁媒体测试](../tests/test_track_edit_locks_media.py)。

### E01：已有真正视频交叉溶解，不冒充音频/全转场库

- [精确合同](VIDEO_CROSS_DISSOLVE.zh-CN.md)：保留切点和长度，要求真实素材余量，RGB16 双源混合，音频不变。
- [转场模型测试](../tests/test_video_transition_model.py)、[源证明测试](../tests/test_video_transition_sources.py)、[实际渲染测试](../tests/test_video_transition_render.py) 分别覆盖结构、handles 和输出；这些历史证据不能替代当前冻结版本回归。

## 本轮对照与下轮选择

本轮 2026-10-27 已复核 [Adobe/Apple 关联音画工作流](LINKED_AV_EDITING.md#official-workflow-comparison-checked-2026-10-27)。此前 2026-10-26 补充核对预览/代理与色彩工作流（[本轮对照](FINAL_FRAME_PREVIEW.md#official-comparison-and-remaining-gaps--官方对照与后续缺口)）；以下剪辑页面保留原 2026-10-25 核对；不据此声称某项是 Studio/Pro 独占权益，也未做竞品实机性能排名。

- [Adobe 源 patching / Insert / Overwrite](https://helpx.adobe.com/nz/premiere/desktop/edit-projects/intro-to-editing/add-media-to-the-timeline-using-source-patching.html)：CutCat 已有本轨插入及已接受的显式单次参与策略；独立 A/V 分别 patching 仍缺。
- [Adobe Sync Lock](https://helpx.adobe.com/premiere/desktop/edit-projects/change-clip-sequence/sync-lock-to-prevent-changes.html)：既有参与预览借鉴参与轨道的明确选择，不冒充持久同步锁或完整 ripple 体系。
- [Resolve Edit](https://www.blackmagicdesign.com/products/davinciresolve/edit)：其文档区分 Insert、Overwrite、Replace、Fit to Fill、Ripple Overwrite 及更多修剪操作。不能因本轨 Insert 存在就把这组全部打勾。
- [Final Cut Pro Insert](https://support.apple.com/en-euro/guide/final-cut-pro/ver4e2eff6/mac)：storyline 中后移和内部拆分提供工作流参照；连接关系与 CutCat 四条独立轨道不同。

本轮候选补齐单对关联音画联合边缘修剪；后续优先比较 **关联分割与独立 A/V/J/L 时间、源 patching**、**连续交互播放与完整代理契约**、**专业色彩管理/监看**。持久同步与混合帧率/VFR 仍属独立有界工程。先定义关系归属、固定对象策略和失败原子性，再增加入口；不通过自动解组、解锁、清设置或改旧渲染来伪造兼容。原生包、代理、实时性能与更宽格式支持继续作为独立门槛，不顺带宣称完成。

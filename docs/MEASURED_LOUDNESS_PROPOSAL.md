# 测量后确认主增益 / Measured master-gain proposal

## 中文

这是可选择的“测量草稿 → 明确应用”工作流，不是自动响度归一化、限幅器或压缩器。原有“时间线 → 主输出增益”手动调整方式保留。仅保存已应用的主增益；目标和草稿不写入工程，schema34 不变。

1. 在“分析时间线音频”中显式分析当前项目，获得源文件已验证的当前结果。
2. 从当前报告打开响度增益建议。输入自己的目标 LUFS 和真峰值估算上限；初始 −18 LUFS、−2 dBTP 仅为可编辑起点，不是任何平台标准或音质保证。
3. 显式测量建议草稿。先计算保守起点，再在独立项目副本上实际分析该主增益。过程中不修改项目、片段参数、历史记录或已构建预览；可以取消。
4. 查看实测 LUFS、每声道采样峰值与真峰值估算，以及与目标的差异。峰值约束可能使目标无法达到；绝对响度门限也可能使实测结果不同于简单预测。不会自动迭代或添加处理器。
5. 只有明确点击应用，且源文件及项目上下文复验成功，才通过已有编辑器提交一次主增益修改。一次撤销还原；取消、关闭、过期回调不提交。应用后旧分析及预览失效，应自行构建预览、试听，再分析或导出。

### 建议计算与拒绝条件

设当前主增益 M、当前实测响度 L、两声道最高真峰值估算 P、用户目标 T、用户上限 C：

- 增量取 min(T−L, C−P)，建议主增益取 M 加该增量，再向下量化到 0.1 dB。不是把增量当作绝对主增益。
- 计算结果超出 −96…+24 dB 时拒绝，而不是截断到范围边界或悄悄放宽上限。
- 预测 L+增量仅作提示。必须对建议主增益实际重新测量；实测超出所选估算上限则拒绝应用，不静默修正。
- 静音、少于 400 ms、低于响度门限、非有限数值、不完整或不匹配报告、过期或不支持的源文件均不给出可应用建议。用户仍可用原有手动主增益。
- 项目/编辑会话/修订/音频设置、源文件身份和哈希、解码器及算法均绑定；目标变更或相关编辑使旧草稿失效。所有源文件访问在后台执行，不在 GUI 线程检查文件。

### 精度和使用边界

分析对象是各片段增益、静音、时序、淡变和求和，再经主增益后的最终 48 kHz 立体声浮点混音，位于编码之前。真峰值是有限四倍 FIR 重建与采样峰值下限保护得到的估算，不是理想连续信号真峰值或认证。AAC 等编码可能改变峰值；编码前通过所选估算上限不保证成片达标。

保留现有本地普通单/双声道素材、有限快照、时长和资源约束；不是实时电平表、多总线混音台、响度范围整形、自动匹配片段、实时监听或付费编辑器功能对等。后台复验会重新读取素材，长素材可能需要等待，可取消。

## English

This optional workflow measures a detached master-gain draft and requires explicit Apply. It is not normalization, limiting, compression, loudness-range shaping or an encoded-file compliance check. Manual master output gain remains available. Targets and drafts are session-only; only the existing applied master gain is persisted in schema34.

Start from a current source-verified timeline audio report. Choose a target LUFS and estimated true-peak ceiling; −18 LUFS and −2 dBTP are editable starting points, not platform standards. Measure the draft, inspect its actual integrated loudness and both channels' sample/estimated true peaks, then choose Apply or Cancel. The entire draft stage leaves the project, clips, history and preview untouched. Apply rechecks source and project identity in a cancellable background operation, then commits exactly one existing editor master-gain edit. Build preview and listen, and analyze again after applying.

The starting delta is min(target−measured LUFS, ceiling−maximum estimated true peak). Add it to the current master gain, round the resulting master gain downward to 0.1 dB, and reject values outside −96…+24 dB. Never clamp to silently relax the ceiling. Absolute loudness gating can change active blocks, so predicted LUFS is not authority. Actual detached remeasurement is mandatory; a measured estimated-peak ceiling violation rejects the draft, with no iterative automatic correction. Target misses remain visible and may be accepted by the user only when the measured draft satisfies its peak constraint.

Silence, too-short/below-gate material, nonfinite or inconsistent measurements, stale/mismatched reports and unsupported sources are not eligible. Requests bind editor/session/revisions, audio settings, original master gain, target/ceiling, source identities/hashes, decoder and algorithm. Background work never enables an obsolete request. Cancel and close discard pending work; one Undo restores an applied gain.

The finite four-times FIR estimate includes a sample-peak lower bound. It is not ideal reconstruction or compliance certification, and subsequent AAC/other encoding may change peaks. Existing local-source, bounded snapshot, duration and resource restrictions remain. No realtime/multi-bus mixer or paid-editor parity claim. Local/offscreen/source tests do not establish native Windows/macOS package support; the exact frozen acceptance receipts are required for this round.

## 本轮竞品对照 / Official workflow comparison

Official sources checked on 2026-10-25; product features below are not claimed to be paid-tier exclusive.

- [Premiere Loudness Meter settings](https://helpx.adobe.com/premiere/desktop/add-audio-effects/apply-audio-effects/edit-loudness-meter-settings.html): target loudness and maximum true peak configure meter/display thresholds. These meter settings alone do not normalize or limit audio.
- [Adobe's Premiere/Audition broadcast-loudness tutorial](https://www.adobe.com/nz/learn/audition/web/broadcast-loudness-standards), published 2022-07-23: the Premiere section describes export loudness normalization, an optional true-peak limiter and a loudness report. This is an older official workflow, not a guarantee of the latest UI location.
- [Adobe Media Encoder automatic loudness correction](https://helpx.adobe.com/media-encoder/desktop/encoding-and-exporting/automatic-loudness-correction.html): export correction includes a true-peak limiter with lookahead/release and an XML report. This describes Media Encoder specifically, not CutCat or a re-labeled Premiere feature.
- [DaVinci Resolve Fairlight](https://www.blackmagicdesign.com/products/davinciresolve/fairlight): clip normalization, selectable-standard loudness meters, live bus loudness graph and offline audio-level analysis belong to a broader audio-post workflow. These are Resolve-family capabilities, not asserted Studio-only features.
- [FFmpeg volume](https://www.ffmpeg.org/ffmpeg-filters.html#volume) is multiplicative gain. [FFmpeg loudnorm](https://www.ffmpeg.org/ffmpeg-filters.html#loudnorm) has linear and dynamic normalization modes, loudness-range/true-peak targets and fallback conditions. CutCat does not invoke loudnorm in this workflow.

本轮补齐“从当前测量得到保守主增益草稿、实际复测、用户确认”这一个闭环。没有复刻 Premiere/Resolve 的归一化引擎、实时总线电平、限幅动态处理、自动化或编码成片认证。下一轮应继续按这些独立能力与真实工作流验收，不把本轮的静态增益建议称为付费版本对等。

> Subsequent candidate: [joint roll of exactly two adjacent linked A/V pairs](LINKED_AV_PAIR_ROLL.md) adds a separate bounded shared-cut workflow. Older unsupported-roll statements below describe their original round; no later acceptance is inferred.

# 相邻片段滚动剪辑 / Adjacent cut roll (2026-10-20)

## 使用

在时间线上选择接点左侧片段，打开检查器「剪辑」页的「滚动相邻接点」。输入新的绝对时间线帧，检查右侧片段名称、两段时长和源区间，然后点「应用滚动剪辑」。它不是偏移量，也不是素材的原始帧编号。

- 只支持同一轨道上恰好相接的两个片段。间隙、不同轨道或没有右侧片段时不可用。
- 两段共享接点移动；左侧外起点、右侧外终点和合计时间范围保持不变。不会推动后续片段，也不创建重叠或转场。
- 两侧可使用不同的恒定速度；保留各自精确源区间的末帧不足部分。支持视频和音频轨道上的片段。
- 片段自带音频随其源入出点变化。独立音频、字幕和贴纸仍固定在原时间线位置，需要自行校对；不是关联组选区编辑。
- 速度、调色、特效、稳定、修复、增益、静音和淡入淡出数值不变。淡入淡出仍相对于各自片段边缘定位，不是相对于整段固定时间。如果缩短后无法容纳已有淡入淡出，整个操作被拒绝，必须先明确调整淡入淡出。
- 每段必须保留至少一帧，且有足够素材余量。超界或有理数精度超限时拒绝整个操作，不裁剪素材、不污染撤销历史。
- 一个操作可完整撤销/重做；保存重开保持结果。未改接点不会新增撤销记录或清空重做。
- 应用滚动剪辑不会同时提交尚未应用的普通时间、素材滑移或速度输入。

## Exact contract

For adjacent clips A and B with cut C0, requested integer timeline cut C and delta = C-C0:

- A retains its source origin; its exact source endpoint becomes Ea + delta*Ra.
- B retains its exact source endpoint; its source origin becomes Ob + delta*Rb.
- A.start and B.end remain fixed; A.end = B.start = C.
- Each clip retains its speed and the exact fractional deficit D*R-(E-O). Thus ceil((E-O)/R) changes by +delta / -delta, including partial tails.
- Preview calculations use copied clips. Application re-evaluates the current pair and commits one whole-project-validated transaction.
- Fades are checked against candidate durations before invoking the existing timing helper. Overlapping in/out fades remain legal if each individually fits, matching the existing model.
- No project schema change is required. Cache fingerprints already include both exact time maps; undo can reuse the prior verified cache.

## Acceptance and scope

Tests are in tests/test_roll_model.py, tests/test_roll_ui.py and tests/test_roll_media.py. scripts/verify_roll_properties.py adds a reproducible 240-case pure-model/audio-plan matrix; those cases are not additional pytest tests and do not represent real audio decoding.

Real generated media covers 24000/1001 and 30000/1001, independent source frame IDs, mixed speeds and partial tails, exact lossless picture stages, actual preview/export, PCM source-switch boundaries, sample-varying source trim and exclusive-tail silence, fixed outer anchors, serialization and cache/undo behavior. Lossless PCM verification maps both video and audio to match production graph scheduling. Discarding the generated video into a nullsink was found to prematurely end one audio-only test harness; production dual-stream output did not reproduce that artifact, so the renderer was not changed.

UI evidence uses actual offscreen Qt widgets in Chinese/English, Simple/Professional, 1366×768 and 1920×1080, at scales 1 and 1.5. It proves geometry and local click/state behavior, not native platform UI or physical speaker playback. The roll control supports cuts through Qt's signed-integer maximum and explicitly disables larger cuts. This does not expand existing whole-editor extreme-timeline limits.

This is local Linux source validation, not a new Windows/macOS build, installer, repository push, remote CI run or production release. No dependencies, paid services, credentials, model downloads or media uploads are added. Existing offline behavior is preserved.

The known conservative fractional-FPS import/end-bound limitation is unchanged. Roll uses current source bounds; simply rounding container duration upward remains unsafe. A general fix still requires decoded timestamp/index evidence, especially for VFR and different audio/video endpoints.

See the accompanying validation report for exact final executed counts and checks. Counts from different runs overlap and must not be added as unique coverage.
